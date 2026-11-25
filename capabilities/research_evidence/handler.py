"""research.evidence — CAP-12 evidence capture, source comparison and citation drafting/checking (Stage 11).

Isolated handler process; reached only through the Stage 01A execution gateway. Works on the content-addressed
extractions written by research.web. These tools let a worker DRAFT evidence and citations; the authoritative
binding of citations to persisted source records and the final citation verification are done by the research
service and its independent verifier (backend/research), never by the worker's own claim."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{64}$")
_WORD = re.compile(r"[a-z0-9]+")
SHINGLE = 5            # identical to backend/research/dedup.py (tests/research/test_dedup.py keeps them equal)
NEAR_DUPLICATE = 0.8


def _load(path: str) -> dict:
    p = Path(path)
    if not p.name.endswith(".json") or not _SHA.match(p.name[:-5]):
        raise ValueError("extracted_path must name a content-addressed extraction (<sha256>.json)")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if hashlib.sha256(doc["text"].encode("utf-8")).hexdigest() != doc["text_sha256"]:
        raise ValueError("extracted text does not match its recorded hash")
    return doc


def _fail(code: str, detail: str) -> dict:
    return {"ok": False, "error_code": code, "detail": detail[:2000]}


def _span(doc: dict, start: int, end: int) -> dict | None:
    if not (0 <= start < end <= len(doc["text"])) or end - start > 5000:
        return None
    quote = doc["text"][start:end]
    block = next((b["index"] for b in doc["blocks"] if b["start"] <= start < b["end"]), None)
    return {"start": start, "end": end, "quote": quote,
            "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(), "block": block,
            "text_sha256": doc["text_sha256"], "content_sha256": doc["content_sha256"]}


def shingles(text: str, k: int = SHINGLE) -> set:
    words = _WORD.findall(text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def capture_evidence(payload: dict, context) -> dict:
    """012.001 — capture an exact evidence span [start, end) of an extracted source, with hashes and context."""
    try:
        doc = _load(payload["extracted_path"])
    except (OSError, ValueError, KeyError) as exc:
        return _fail("EXTRACTION_FAILED", str(exc))
    s = _span(doc, int(payload["start"]), int(payload["end"]))
    if s is None:
        return _fail("VERIFICATION_FAILED", "span is outside the source text or longer than 5000 characters")
    text = doc["text"]
    s["context_before"] = text[max(0, s["start"] - 200):s["start"]]
    s["context_after"] = text[s["end"]:s["end"] + 200]
    return {"ok": True, "error_code": None, "evidence": s}


def compare_sources(payload: dict, context) -> dict:
    """012.002 — pairwise comparison: near-duplicate / syndication (5-word shingle Jaccard), titles, dates."""
    docs = []
    for path in payload["extracted_paths"]:
        try:
            docs.append(_load(path))
        except (OSError, ValueError, KeyError) as exc:
            return _fail("EXTRACTION_FAILED", f"{path}: {exc}")
    sh = [shingles(d["text"]) for d in docs]
    pairs = []
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            a, b = docs[i], docs[j]
            sim = round(jaccard(sh[i], sh[j]), 4)
            contain = round(len(sh[i] & sh[j]) / min(len(sh[i]), len(sh[j])), 4) if sh[i] and sh[j] else 0.0
            ma, mb = a["metadata"], b["metadata"]
            pairs.append({"a": a["content_sha256"], "b": b["content_sha256"], "jaccard": sim, "containment": contain,
                          "identical_text": a["text_sha256"] == b["text_sha256"],
                          "near_duplicate": sim >= NEAR_DUPLICATE or contain >= 0.9,
                          "same_title": bool(ma.get("title")) and ma.get("title") == mb.get("title"),
                          "published": [ma.get("published_at"), mb.get("published_at")],
                          "publishers": [ma.get("publisher"), mb.get("publisher")]})
    return {"ok": True, "error_code": None, "pairs": pairs}


def construct_citation(payload: dict, context) -> dict:
    """012.003 — draft a citation for a span. Its identifier is minted later by the research service, which binds
    it to a persisted source record; a draft cannot name or invent a citation id."""
    try:
        doc = _load(payload["extracted_path"])
    except (OSError, ValueError, KeyError) as exc:
        return _fail("EXTRACTION_FAILED", str(exc))
    s = _span(doc, int(payload["start"]), int(payload["end"]))
    if s is None:
        return _fail("VERIFICATION_FAILED", "span is outside the source text")
    m = doc["metadata"]
    return {"ok": True, "error_code": None,
            "citation_draft": {**s, "locator": f"chars {s['start']}-{s['end']}" +
                               (f", block {s['block']}" if s["block"] is not None else ""),
                               "url": doc.get("final_url") or doc.get("source_url"), "title": m.get("title"),
                               "author": m.get("author"), "published_at": m.get("published_at"),
                               "retrieved_at": doc.get("retrieved_at")}}


def verify_citation(payload: dict, context) -> dict:
    """012.004 — re-check a citation draft against the stored extraction (worker-side self-check only)."""
    problems = []
    try:
        doc = _load(payload["extracted_path"])
    except (OSError, ValueError, KeyError) as exc:
        return {"ok": True, "error_code": None, "verified": False, "problems": [f"source unreadable: {exc}"]}
    if doc["text_sha256"] != payload["text_sha256"]:
        problems.append("source text changed since the citation was drafted")
    s = _span(doc, int(payload["start"]), int(payload["end"]))
    if s is None:
        problems.append("span is outside the source text")
    elif s["quote_sha256"] != payload["quote_sha256"]:
        problems.append("quoted text at the span differs from the cited quote")
    return {"ok": True, "error_code": None, "verified": not problems, "problems": problems}


def healthcheck() -> dict:
    return {"healthy": jaccard({"a"}, {"a"}) == 1.0}


def verify(payload: dict, output: dict) -> dict:
    if not isinstance(output, dict) or "ok" not in output:
        return {"verified": False, "problems": ["malformed output"]}
    if output["ok"] is False and not output.get("error_code"):
        return {"verified": False, "problems": ["failure without an error code"]}
    return {"verified": True, "problems": []}
