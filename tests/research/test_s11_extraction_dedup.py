"""Stage 11 content extraction (§12), source fingerprint, deduplication (§14), source quality (§15), freshness (§20)."""
from __future__ import annotations

import hashlib
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.research import dedup
from backend.research.contracts import Freshness
from backend.research.quality import assess_quality, freshness_of

CAPS = Path(__file__).resolve().parents[2] / "capabilities"


def _load(pkg, name):
    spec = importlib.util.spec_from_file_location(f"s11x_{pkg}_{name}", CAPS / pkg / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ex = _load("research_web", "extract")
evh = _load("research_evidence", "handler")

from fixture_server import ARTICLE_A, ARTICLE_C, INJECTION, NODATE  # noqa: E402

BASE = "https://news.example.org"


def page(t):
    return t.replace("{recent}", "2026-11-23T10:00:00Z").replace("{base}", BASE).replace("{{", "{").replace("}}", "}")


def test_html_extraction_preserves_metadata_with_provenance():
    doc = ex.extract(page(ARTICLE_A).encode(), "text/html", "utf-8", f"{BASE}/page/a.html")
    m, prov = doc["metadata"], doc["metadata_provenance"]
    assert m["title"] == "Rivertown bridge reopens after repairs" and prov["title"] == "html:title"
    assert m["author"] == "Dana Field" and prov["author"] == "meta:author"
    assert m["publisher"] == "Rivertown Gazette" and prov["publisher"] == "meta:og:site_name"
    assert m["published_at"] == "2026-11-23T10:00:00Z" and prov["published_at"] == "meta:article:published_time"
    assert m["canonical_url"] == f"{BASE}/page/a.html" and m["language"] == "en"
    assert "Home | News" not in doc["text"] and "Copyright" not in doc["text"]          # boilerplate excluded
    assert any(lk["url"] == f"{BASE}/page/council.html" for lk in doc["links"])


def test_json_ld_metadata_and_content_boundaries():
    doc = ex.extract(page(ARTICLE_C).encode(), "text/html", None, f"{BASE}/page/c.html")
    assert doc["metadata"]["author"] == "Lee Ortiz" and doc["metadata_provenance"]["author"] == "json-ld:author"
    assert doc["metadata"]["publisher"] == "Coastal Times"
    assert doc["metadata"]["published_at"] == "2026-11-23T10:00:00Z"
    for b in doc["blocks"]:                       # every block boundary indexes the stored text exactly
        assert doc["text"][b["start"]:b["end"]].strip() == doc["text"][b["start"]:b["end"]]
        assert b["end"] > b["start"]
    assert doc["text_sha256"] == hashlib.sha256(doc["text"].encode()).hexdigest()


def test_missing_metadata_is_never_invented():
    doc = ex.extract(NODATE.encode(), "text/html", "utf-8", f"{BASE}/x")
    m = doc["metadata"]
    assert m["author"] is None and m["published_at"] is None and m["publisher"] is None
    assert set(doc["metadata_provenance"]) == {"title"}
    bad = '<html><head><meta property="article:published_time" content="last Tuesday"></head><body>x</body></html>'
    assert ex.extract(bad.encode(), "text/html", None, BASE)["metadata"]["published_at"] is None


def test_hidden_injection_text_excluded_and_visible_text_is_just_data():
    doc = ex.extract(INJECTION.encode(), "text/html", None, f"{BASE}/inject")
    assert "hidden instruction" not in doc["text"] and doc["hidden_text_chars_excluded"] > 0
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in doc["text"]      # kept verbatim as DATA, never executed
    assert "atta-research-budget" not in str(doc["metadata"])


def test_plain_text_json_and_pdf_handling():
    doc = ex.extract(b"Para one.\r\n\r\nPara two.", "text/plain", None, BASE)
    assert [doc["text"][b["start"]:b["end"]] for b in doc["blocks"]] == ["Para one.", "Para two."]
    assert ex.extract(b'{"a": 1}', "application/json", None, BASE)["kind"] == "json"
    with pytest.raises(ValueError) as e:
        ex.extract(b"%PDF-1.4", "application/pdf", None, BASE)
    assert e.value.args[0] == "BLOCKED_DEPENDENCY"                 # no approved PDF parser: honest dependency
    with pytest.raises(ValueError) as e:
        ex.extract(b"{bad", "application/json", None, BASE)
    assert e.value.args[0] == "EXTRACTION_FAILED"
    with pytest.raises(ValueError) as e:
        ex.extract(b"x", "image/png", None, BASE)
    assert e.value.args[0] == "UNSUPPORTED_CONTENT"


def test_source_fingerprint_is_content_addressed():
    a1 = page(ARTICLE_A).encode()
    a2 = page(ARTICLE_A).replace("12 million", "13 million").encode()
    assert hashlib.sha256(a1).hexdigest() != hashlib.sha256(a2).hexdigest()
    d1 = ex.extract(a1, "text/html", None, BASE)
    d2 = ex.extract(a2, "text/html", None, BASE)
    assert d1["text_sha256"] != d2["text_sha256"]


def test_dedup_same_algorithm_in_service_and_tool():
    text = page(ARTICLE_A)
    assert dedup.shingles(text) == evh.shingles(text)
    assert dedup.SHINGLE == evh.SHINGLE and dedup.NEAR_DUPLICATE == evh.NEAR_DUPLICATE


def _src(sid, url, chash, domain, dup=None, group=None):
    return {"source_id": sid, "canonical_url": url, "content_hash": chash, "domain": domain, "duplicate_of": dup,
            "independence_group": group or domain}


def test_duplicate_detection_relations():
    a = ex.extract(page(ARTICLE_A).encode(), "text/html", None, BASE)["text"]
    b = ex.extract(page(ARTICLE_A).replace("Rivertown Gazette", "Valley Wire").encode(), "text/html", None, BASE)["text"]
    c = ex.extract(page(ARTICLE_C).encode(), "text/html", None, BASE)["text"]
    existing = [_src("src_A", "https://gazette.example/a", "h1", "gazette.example")]
    texts = {"src_A": a}
    assert dedup.find_duplicate(canonical_url="https://gazette.example/a", content_hash="zz", text=None, domain=None,
                                existing=existing, texts=texts).relation == "same_url"
    assert dedup.find_duplicate(canonical_url="https://x.example/", content_hash="h1", text=None, domain=None,
                                existing=existing, texts=texts).relation == "identical_content"
    syn = dedup.find_duplicate(canonical_url="https://wire.example/b", content_hash="h2", text=b,
                               domain="wire.example", existing=existing, texts=texts)
    assert syn and syn.relation == "syndicated" and syn.source_id == "src_A"
    assert dedup.find_duplicate(canonical_url="https://coastal.example/c", content_hash="h3", text=c,
                                domain="coastal.example", existing=existing, texts=texts) is None


def test_quality_is_factors_not_a_truth_score():
    doc = ex.extract(page(ARTICLE_A).encode(), "text/html", None, BASE)
    q = assess_quality(domain="transport.gov.uk", extraction=doc, official_suffixes=[".gov.uk"],
                       independence_group=None, retrieval_complete=True)
    d = q.model_dump()
    assert q.official is True and q.publication_identified and q.author_identified and q.publication_date_known
    assert q.primary is None and any("primary/secondary" in n for n in q.notes)    # unknown stays unknown
    assert q.independence_group == "transport.gov.uk"
    assert not any(k in d for k in ("score", "truth", "trust_score", "credibility"))
    nodate = ex.extract(NODATE.encode(), "text/html", None, BASE)
    q2 = assess_quality(domain="blog.example.com", extraction=nodate, official_suffixes=[".gov"],
                        independence_group=None, retrieval_complete=True)
    assert q2.official is False and not q2.publication_date_known and not q2.author_identified


def test_freshness_never_silently_satisfied():
    ref = datetime(2026, 11, 25, tzinfo=timezone.utc)
    cur = Freshness(requires_current=True, max_age_days=30)
    assert freshness_of({"published_at": "2026-11-20T00:00:00Z"}, cur, ref)["fresh"] is True
    assert freshness_of({"published_at": "2019-03-01T08:00:00Z"}, cur, ref)["fresh"] is False
    undated = freshness_of({"published_at": None, "retrieved_at": ref.isoformat()}, cur, ref)
    assert undated["fresh"] is False and "unknown" in undated["reason"]       # retrieval time is not publication time
    assert freshness_of({"published_at": "2030-01-01"}, cur, ref)["fresh"] is False
    assert freshness_of({"published_at": None}, Freshness(), ref)["fresh"] is None
