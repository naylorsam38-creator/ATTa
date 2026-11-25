"""Search provider adapters (Stage 11 §8) — request building and response parsing, standard library only.

The research architecture is provider-independent: every adapter maps a provider's response onto the same
normalised result (query, rank, title, url, snippet, source, published_at, retrieved_at, provider metadata).
Nothing here fabricates results: an adapter that cannot run returns a structured error code.

Adapters:
  searxng      self-hostable meta-search (JSON API, no credential) — usable offline-of-commercial-providers
  brave        Brave Search API — needs an API key, which must be injected by Stage 02 credential protection;
               until then it returns BLOCKED_DEPENDENCY (handlers get a clean environment and never see secrets)
"""
from __future__ import annotations

import json
from urllib.parse import urlencode, urlsplit

PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"

KINDS = ("searxng", "brave")


class ProviderError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def build_request(kind: str, endpoint: str, query: str, max_results: int, options: dict) -> tuple[str, str]:
    """Return (url, accept header)."""
    if kind == "searxng":
        params = {"q": query, "format": "json", "pageno": 1, "safesearch": 1}
        if options.get("language"):
            params["language"] = options["language"]
        if options.get("time_range") in ("day", "week", "month", "year"):
            params["time_range"] = options["time_range"]
        base = endpoint.rstrip("/")
        if not base.endswith("/search"):
            base += "/search"
        return base + "?" + urlencode(params), "application/json"
    if kind == "brave":
        raise ProviderError(BLOCKED_DEPENDENCY, "the Brave Search API needs an API key; credentials reach handlers "
                            "only through Stage 02 credential protection, which is not integrated")
    raise ProviderError(PROVIDER_UNAVAILABLE, f"unknown provider kind '{kind}'")


def _s(v, n: int) -> str | None:
    return v.strip()[:n] if isinstance(v, str) and v.strip() else None


def parse_response(kind: str, body: bytes, query: str, max_results: int, retrieved_at: str) -> dict:
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProviderError(PROVIDER_UNAVAILABLE, f"provider returned invalid JSON: {exc}") from exc
    if kind != "searxng":
        raise ProviderError(PROVIDER_UNAVAILABLE, f"unknown provider kind '{kind}'")
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        raise ProviderError(PROVIDER_UNAVAILABLE, "provider response has no 'results' list")
    results, seen = [], set()
    for item in doc["results"]:
        if not isinstance(item, dict):
            continue
        url = _s(item.get("url"), 2048)
        if not url or urlsplit(url).scheme not in ("http", "https") or url in seen:
            continue
        seen.add(url)
        engines = item.get("engines") if isinstance(item.get("engines"), list) else []
        results.append({
            "rank": len(results) + 1, "title": _s(item.get("title"), 500), "url": url,
            "snippet": _s(item.get("content"), 2000),
            "source": _s(item.get("engine"), 100) or (engines[0] if engines and isinstance(engines[0], str) else None),
            "published_at": _s(item.get("publishedDate") or item.get("pubdate"), 64),
            "retrieved_at": retrieved_at,
            "provider_metadata": {"engines": [e for e in engines if isinstance(e, str)][:10],
                                  "score": item.get("score") if isinstance(item.get("score"), (int, float)) else None,
                                  "category": _s(item.get("category"), 50)},
        })
        if len(results) >= max_results:
            break
    meta = {"number_of_results": doc.get("number_of_results") if isinstance(doc.get("number_of_results"),
                                                                          (int, float)) else None,
            "unresponsive_engines": [str(e)[:100] for e in (doc.get("unresponsive_engines") or [])][:20]}
    return {"results": results, "provider_metadata": meta}
