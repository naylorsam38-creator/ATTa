"""research.web — CAP-11 web search and controlled source retrieval (Stage 11).

Runs ONLY inside the supervised, isolated handler process, reached ONLY through the Stage 01A execution gateway
(task contract -> numbered tool pool -> drift guard -> policy -> gateway). Arguments are worker-controlled and are
therefore validated again here; the retrieval policy comes from the trusted package (retrieval_policy.py).

Workspace layout written by these tools (task workspace = the objective workspace):
  research/snapshots/<sha256>.raw        exact bytes retrieved (content-addressed, immutable)
  research/snapshots/<sha256>.meta.json  retrieval facts (URLs, redirects, status, headers, time)
  research/extracted/<sha256>.json       typed extraction of that snapshot (text, blocks, metadata, links)
Outcomes are returned as data: {"ok": false, "error_code": ...} is a successful execution that observed a failure
(the research service records it); a raised exception is a tool failure."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import extract as ex
import netguard as ng
import providers as pv
from retrieval_policy import POLICY

SNAP_DIR = Path("research/snapshots")
EXTRACT_DIR = Path("research/extracted")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fail(code: str, detail: str, **extra) -> dict:
    return {"ok": False, "error_code": code, "detail": detail[:2000], **extra}


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _store_snapshot(r: dict, retrieved_at: str) -> dict:
    body = r["body"]
    sha = hashlib.sha256(body).hexdigest()
    raw = SNAP_DIR / f"{sha}.raw"
    if not raw.exists():
        _write_atomic(raw, body)
    meta = {"requested_url": r["requested_url"], "final_url": r["final_url"], "status": r["status"],
            "content_type": r["content_type"], "charset": r["charset"], "bytes": len(body), "sha256": sha,
            "redirects": r["redirects"], "resolved_ip": r["resolved_ip"], "headers": r["headers"],
            "retrieved_at": retrieved_at}
    _write_atomic(SNAP_DIR / f"{sha}.meta.json", json.dumps(meta, indent=1).encode())
    return {**meta, "snapshot_path": raw.as_posix()}


def _retrieve(url: str) -> dict:
    retrieved_at = _now()
    try:
        r = ng.fetch(url, POLICY, accept="text/html,application/xhtml+xml,text/plain;q=0.9,application/json;q=0.8,"
                                         "application/pdf;q=0.5,*/*;q=0.1")
    except ng.RetrievalError as exc:
        return _fail(exc.code, exc.detail, requested_url=url, attempted_at=retrieved_at,
                     redirects=exc.extra.get("redirects", []), redirect_target=exc.extra.get("redirect_target"),
                     timeout=bool(exc.extra.get("timeout")), http_status=exc.extra.get("http_status"))
    return {"ok": True, "error_code": None, **_store_snapshot(r, retrieved_at)}


# --------------------------------------------------------------------------------------------- tools
def search(payload: dict, context) -> dict:
    """011.001 — query ONE configured provider. The endpoint host was authorised by the task contract."""
    kind, endpoint, query = payload["provider_kind"], payload["endpoint"], payload["query"].strip()
    n = int(payload.get("max_results", 10))
    retrieved_at = _now()
    if not query:
        return _fail("PROVIDER_UNAVAILABLE", "empty query", query=query, results=[])
    try:
        url, accept = pv.build_request(kind, endpoint, query, n, payload.get("options") or {})
        r = ng.fetch(url, POLICY, accept=accept)
        if r["content_type"] not in ("application/json", "application/ld+json"):
            return _fail("PROVIDER_UNAVAILABLE", f"provider returned {r['content_type']}, not JSON", query=query,
                         results=[])
        parsed = pv.parse_response(kind, r["body"], query, n, retrieved_at)
    except pv.ProviderError as exc:
        return _fail(exc.code, exc.detail, query=query, provider_kind=kind, results=[])
    except ng.RetrievalError as exc:
        code = exc.code if exc.code == "NETWORK_DENIED" else "PROVIDER_UNAVAILABLE"
        return _fail(code, exc.detail, query=query, provider_kind=kind, results=[])
    return {"ok": True, "error_code": None, "query": query, "provider_kind": kind,
            "endpoint_host": urlsplit(endpoint).hostname, "retrieved_at": retrieved_at,
            "response_sha256": hashlib.sha256(r["body"]).hexdigest(), "results": parsed["results"],
            "provider_metadata": parsed["provider_metadata"]}


def fetch_source(payload: dict, context) -> dict:
    """011.002 — controlled retrieval of one URL into an immutable, content-addressed snapshot."""
    return _retrieve(payload["url"])


def _load_snapshot(path: str) -> tuple[bytes, dict]:
    p = Path(path)
    name = p.name
    if not name.endswith(".raw") or not _SHA.match(name[:-4]):
        raise ValueError("snapshot_path must name a content-addressed snapshot (<sha256>.raw)")
    body = p.read_bytes()
    if hashlib.sha256(body).hexdigest() != name[:-4]:
        raise ValueError("snapshot bytes do not match their content address (tampered or corrupted)")
    meta_p = p.with_name(name[:-4] + ".meta.json")
    meta = json.loads(meta_p.read_text()) if meta_p.is_file() else {}
    return body, meta


def extract_content(payload: dict, context) -> dict:
    """011.003 — typed extraction of a stored snapshot (web) or an authorised local file (local research)."""
    path = payload["snapshot_path"]
    try:
        if payload.get("local_source"):
            body = Path(path).read_bytes()
            sha = hashlib.sha256(body).hexdigest()
            meta = {"final_url": None, "requested_url": None, "retrieved_at": _now(), "sha256": sha,
                    "content_type": payload.get("content_type") or "text/plain", "charset": "utf-8"}
            local = SNAP_DIR / f"{sha}.raw"
            if not local.exists():
                _write_atomic(local, body)
            _write_atomic(SNAP_DIR / f"{sha}.meta.json", json.dumps({**meta, "local_source": True,
                                                                     "bytes": len(body)}).encode())
        else:
            body, meta = _load_snapshot(path)
            sha = meta.get("sha256") or hashlib.sha256(body).hexdigest()
    except (OSError, ValueError) as exc:
        return _fail("EXTRACTION_FAILED", str(exc), snapshot_path=path)
    ctype = meta.get("content_type") or ""
    base = meta.get("final_url") or "http://local.invalid/"
    try:
        doc = ex.extract(body, ctype, meta.get("charset"), base)
    except ValueError as exc:
        code, detail = (exc.args + ("",))[:2] if len(exc.args) >= 2 else ("EXTRACTION_FAILED", str(exc))
        return _fail(code, detail, content_sha256=sha, content_type=ctype)
    doc.update({"content_sha256": sha, "content_type": ctype, "source_url": meta.get("requested_url"),
                "final_url": meta.get("final_url"), "retrieved_at": meta.get("retrieved_at"),
                "local_source": bool(payload.get("local_source"))})
    out = EXTRACT_DIR / f"{sha}.json"
    _write_atomic(out, json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    return {"ok": True, "error_code": None, "extracted_path": out.as_posix(), "content_sha256": sha,
            "text_sha256": doc["text_sha256"], "text_chars": doc["text_chars"], "blocks": len(doc["blocks"]),
            "links": len(doc["links"]), "metadata": doc["metadata"],
            "metadata_provenance": doc["metadata_provenance"], "kind": doc["kind"]}


def _load_extracted(path: str) -> dict:
    p = Path(path)
    if not p.name.endswith(".json") or not _SHA.match(p.name[:-5]):
        raise ValueError("extracted_path must name a content-addressed extraction (<sha256>.json)")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if hashlib.sha256(doc["text"].encode("utf-8")).hexdigest() != doc["text_sha256"]:
        raise ValueError("extracted text does not match its recorded hash")
    return doc


def inspect_metadata(payload: dict, context) -> dict:
    """011.004 — metadata of an extracted source (stated fields only, with provenance)."""
    try:
        doc = _load_extracted(payload["extracted_path"])
    except (OSError, ValueError, KeyError) as exc:
        return _fail("EXTRACTION_FAILED", str(exc))
    return {"ok": True, "error_code": None, "metadata": doc["metadata"],
            "metadata_provenance": doc["metadata_provenance"], "content_sha256": doc["content_sha256"],
            "text_sha256": doc["text_sha256"], "final_url": doc.get("final_url"), "kind": doc["kind"],
            "retrieved_at": doc.get("retrieved_at"), "links": len(doc["links"])}


def follow_link(payload: dict, context) -> dict:
    """011.005 — fetch a link that actually appears in an already extracted source (no invented URLs)."""
    try:
        doc = _load_extracted(payload["extracted_path"])
    except (OSError, ValueError, KeyError) as exc:
        return _fail("EXTRACTION_FAILED", str(exc))
    url = payload["url"]
    if url not in {lk["url"] for lk in doc["links"]}:
        return _fail("NETWORK_DENIED", "the URL is not a link of the given source", requested_url=url)
    r = _retrieve(url)
    r["followed_from"] = doc["content_sha256"]
    return r


def search_within_source(payload: dict, context) -> dict:
    """011.006 — find passages containing the given terms; returns exact spans (offsets into the stored text)."""
    try:
        doc = _load_extracted(payload["extracted_path"])
    except (OSError, ValueError, KeyError) as exc:
        return _fail("EXTRACTION_FAILED", str(exc))
    text, low = doc["text"], doc["text"].lower()
    terms = [t.lower() for t in payload["terms"] if t.strip()]
    hits = []
    for b in doc["blocks"]:
        seg = low[b["start"]:b["end"]]
        matched = [t for t in terms if t in seg]
        if matched:
            hits.append({"block": b["index"], "start": b["start"], "end": b["end"], "matched_terms": matched,
                         "score": len(matched), "excerpt": text[b["start"]:b["end"]][:600]})
    hits.sort(key=lambda h: (-h["score"], h["start"]))
    return {"ok": True, "error_code": None, "text_sha256": doc["text_sha256"],
            "content_sha256": doc["content_sha256"], "hits": hits[:int(payload.get("max_hits", 10))]}


# --------------------------------------------------------------------------------------------- health / verify
def healthcheck() -> dict:
    ok = bool(POLICY["allowed_schemes"]) and POLICY["max_response_bytes"] > 0
    return {"healthy": ok, "production_policy": not POLICY["loopback_ports_allowed_for_tests"]}


def verify(payload: dict, output: dict) -> dict:
    """Independent re-check used by the controller's tool_verify criterion: stored bytes match what was reported."""
    problems = []
    if not isinstance(output, dict):
        return {"verified": False, "problems": ["no output"]}
    if output.get("ok") and output.get("snapshot_path"):
        p = Path(output["snapshot_path"])
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != output.get("sha256"):
            problems.append("snapshot missing or its bytes do not match the reported sha256")
    if output.get("ok") and output.get("extracted_path"):
        try:
            doc = _load_extracted(output["extracted_path"])
            if doc["text_sha256"] != output.get("text_sha256"):
                problems.append("extraction text hash differs from the reported one")
        except (OSError, ValueError, KeyError) as exc:
            problems.append(f"extraction unreadable: {exc}")
    if not output.get("ok") and not output.get("error_code"):
        problems.append("failed outcome without an error code")
    return {"verified": not problems, "problems": problems}
