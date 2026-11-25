"""Typed content extraction (Stage 11 §12) — standard library only, isolated handler process.

Retrieved content is DATA. Extraction never executes it, never follows its instructions and never invents metadata:
a field the document does not state is null, and every stated field records where it came from
(`metadata_provenance`). Text is split into blocks with exact character offsets (content boundaries) so evidence
spans and citations can point back into the stored text."""
from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"
EXTRACTION_FAILED = "EXTRACTION_FAILED"
BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"

MAX_TEXT_CHARS = 400_000
MAX_LINKS = 500
_SKIP = {"script", "style", "noscript", "template", "svg", "math", "iframe", "object", "embed", "canvas", "form",
         "button", "select", "textarea", "head"}
_BOILERPLATE = {"nav", "footer", "aside"}
_BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "td", "th", "dd", "dt", "figcaption",
          "caption", "summary", "div", "section", "article", "main", "header", "tr", "br", "hr", "table", "ul", "ol"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_WS = re.compile(r"\s+")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][0-9:.]+(Z|[+-]\d{2}:?\d{2})?)?$")

AUTHOR_META = ("author", "article:author", "dc.creator", "dcterms.creator", "citation_author", "parsely-author",
               "sailthru.author")
PUBLISHED_META = ("article:published_time", "citation_publication_date", "citation_date", "dc.date",
                  "dcterms.created", "dcterms.issued", "date", "pubdate", "publish-date", "parsely-pub-date",
                  "sailthru.date", "og:published_time")
MODIFIED_META = ("article:modified_time", "og:updated_time", "dcterms.modified", "last-modified")
PUBLISHER_META = ("og:site_name", "citation_publisher", "dc.publisher", "dcterms.publisher", "application-name")
TITLE_META = ("og:title", "twitter:title", "citation_title", "dc.title")


def _norm_date(value: str | None) -> str | None:
    """Keep the stated date verbatim when it is ISO-8601-like; anything else is kept only as raw text."""
    if not value:
        return None
    v = value.strip()
    return v if _ISO_DATE.match(v) else None


class _Html(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.stack: list[str] = []
        self.skip_depth = 0
        self.hidden_depth = 0
        self.boiler_depth = 0
        self.title_parts: list[str] = []
        self.in_title = False
        self.meta: dict[str, str] = {}
        self.canonical: str | None = None
        self.lang: str | None = None
        self.links: list[dict] = []
        self.blocks: list[dict] = []
        self.cur: list[str] = []
        self.cur_kind = "p"
        self.jsonld: list[str] = []
        self.in_jsonld = False
        self.jsonld_buf: list[str] = []
        self.times: list[str] = []
        self.anchor: dict | None = None
        self.hidden_text_chars = 0

    def _flush(self):
        text = _WS.sub(" ", "".join(self.cur)).strip()
        if text:
            self.blocks.append({"kind": self.cur_kind, "text": text})
        self.cur = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag not in _VOID:
            self.stack.append(tag)
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"][:20]
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self.in_jsonld, self.jsonld_buf = True, []
        if tag in _SKIP:
            if tag not in _VOID:
                self.skip_depth += 1
            return
        hidden = ("hidden" in a or a.get("aria-hidden") == "true" or
                  re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", a.get("style", ""), re.I))
        if hidden and tag not in _VOID:
            self.hidden_depth += 1
            self.stack[-1] = tag + "#hidden"
        if tag in _BOILERPLATE and tag not in _VOID:
            self.boiler_depth += 1
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"][:1000]
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split() and a.get("href"):
            self.canonical = urljoin(self.base, a["href"])
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"][:64])
        elif tag == "a" and a.get("href"):
            href = urljoin(self.base, a["href"].strip())
            if urlsplit(href).scheme in ("http", "https") and len(self.links) < MAX_LINKS:
                self.anchor = {"url": href.split("#", 1)[0], "text": ""}
        if tag in _BLOCK and not self.skip_depth:
            self._flush()
            self.cur_kind = tag if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "td",
                                           "th") else "p"

    def handle_endtag(self, tag):
        if tag == "script" and self.in_jsonld:
            self.jsonld.append("".join(self.jsonld_buf)[:200_000])
            self.in_jsonld = False
        if tag in _VOID:
            return
        # pop to the matching tag (tolerates unclosed children)
        idx = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].split("#")[0] == tag:
                idx = i
                break
        if idx is None:
            return
        for t in self.stack[idx:]:
            base = t.split("#")[0]
            if base in _SKIP:
                self.skip_depth = max(0, self.skip_depth - 1)
            if t.endswith("#hidden"):
                self.hidden_depth = max(0, self.hidden_depth - 1)
            if base in _BOILERPLATE:
                self.boiler_depth = max(0, self.boiler_depth - 1)
        del self.stack[idx:]
        if tag == "title":
            self.in_title = False
        if tag == "a" and self.anchor is not None:
            self.anchor["text"] = _WS.sub(" ", self.anchor["text"]).strip()[:300]
            self.links.append(self.anchor)
            self.anchor = None
        if tag in _BLOCK and not self.skip_depth:
            self._flush()

    def handle_data(self, data):
        if self.in_jsonld:
            self.jsonld_buf.append(data)
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.skip_depth:
            return
        if self.hidden_depth:
            self.hidden_text_chars += len(data)
            return
        if self.anchor is not None:
            self.anchor["text"] += data
        if self.boiler_depth:
            return
        self.cur.append(data)

    def close(self):
        super().close()
        self._flush()


def _jsonld_fields(scripts: list[str]) -> dict:
    out: dict = {}

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
            return
        if not isinstance(node, dict):
            return
        if "@graph" in node:
            walk(node["@graph"])
        for key, field in (("datePublished", "published_at"), ("dateModified", "modified_at"),
                           ("headline", "title")):
            if isinstance(node.get(key), str) and field not in out:
                out[field] = node[key][:500]
        for key, field in (("author", "author"), ("publisher", "publisher")):
            v = node.get(key)
            if field in out or v is None:
                continue
            if isinstance(v, list) and v:
                v = v[0]
            if isinstance(v, dict) and isinstance(v.get("name"), str):
                out[field] = v["name"][:300]
            elif isinstance(v, str):
                out[field] = v[:300]
    for s in scripts:
        try:
            walk(json.loads(s))
        except (ValueError, RecursionError):
            continue
    return out


def _decode(body: bytes, charset: str | None) -> str:
    for enc in [c for c in (charset, "utf-8") if c]:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _assemble(blocks: list[dict]) -> tuple[str, list[dict]]:
    """Join blocks with blank lines; record each block's exact [start, end) offsets in the joined text."""
    text_parts, out, pos = [], [], 0
    for i, b in enumerate(blocks):
        t = b["text"]
        if pos + len(t) > MAX_TEXT_CHARS:
            break
        if text_parts:
            text_parts.append("\n\n")
            pos += 2
        out.append({"index": len(out), "kind": b["kind"], "start": pos, "end": pos + len(t)})
        text_parts.append(t)
        pos += len(t)
    return "".join(text_parts), out


def extract(body: bytes, content_type: str, charset: str | None, base_url: str) -> dict:
    """Return the typed extraction document. Raises ValueError(code, detail) for unsupported/failed extraction."""
    provenance: dict[str, str] = {}
    meta: dict = {"title": None, "author": None, "published_at": None, "modified_at": None, "publisher": None,
                  "canonical_url": None, "language": None, "description": None}
    links: list[dict] = []
    hidden_chars = 0
    if content_type in ("text/html", "application/xhtml+xml"):
        p = _Html(base_url)
        try:
            p.feed(_decode(body, charset))
            p.close()
        except Exception as exc:  # noqa: BLE001 — malformed markup is an extraction failure, not a crash
            raise ValueError(EXTRACTION_FAILED, f"HTML parsing failed: {type(exc).__name__}: {exc}") from exc
        ld = _jsonld_fields(p.jsonld)
        title = _WS.sub(" ", "".join(p.title_parts)).strip()
        if title:
            meta["title"], provenance["title"] = title[:500], "html:title"
        else:
            for k in TITLE_META:
                if p.meta.get(k):
                    meta["title"], provenance["title"] = p.meta[k][:500], f"meta:{k}"
                    break
            else:
                if ld.get("title"):
                    meta["title"], provenance["title"] = ld["title"], "json-ld:headline"
        for field, keys, ldk in (("author", AUTHOR_META, "author"), ("publisher", PUBLISHER_META, "publisher")):
            for k in keys:
                if p.meta.get(k):
                    meta[field], provenance[field] = p.meta[k][:300], f"meta:{k}"
                    break
            else:
                if ld.get(ldk):
                    meta[field], provenance[field] = ld[ldk], f"json-ld:{ldk}"
        for field, keys, ldk in (("published_at", PUBLISHED_META, "published_at"),
                                 ("modified_at", MODIFIED_META, "modified_at")):
            for k in keys:
                d = _norm_date(p.meta.get(k))
                if d:
                    meta[field], provenance[field] = d, f"meta:{k}"
                    break
            else:
                d = _norm_date(ld.get(ldk))
                if d:
                    meta[field], provenance[field] = d, f"json-ld:{'datePublished' if ldk == 'published_at' else 'dateModified'}"
                elif field == "published_at":
                    d = next((x for x in (_norm_date(t) for t in p.times) if x), None)
                    if d:
                        meta[field], provenance[field] = d, "html:time[datetime]"
        if p.meta.get("description") or p.meta.get("og:description"):
            meta["description"] = (p.meta.get("description") or p.meta.get("og:description"))[:1000]
            provenance["description"] = "meta:description" if p.meta.get("description") else "meta:og:description"
        if p.canonical and urlsplit(p.canonical).scheme in ("http", "https"):
            meta["canonical_url"], provenance["canonical_url"] = p.canonical, "html:link[rel=canonical]"
        if p.lang:
            meta["language"], provenance["language"] = p.lang, "html:lang"
        text, blocks = _assemble(p.blocks)
        seen = set()
        for lk in p.links:
            if lk["url"] not in seen:
                seen.add(lk["url"])
                links.append(lk)
        hidden_chars = p.hidden_text_chars
        kind = "html"
    elif content_type in ("text/plain", "text/markdown", "text/csv"):
        raw = _decode(body, charset).replace("\r\n", "\n")
        paras = [{"kind": "p", "text": _WS.sub(" ", x).strip()} for x in re.split(r"\n\s*\n", raw)]
        text, blocks = _assemble([x for x in paras if x["text"]])
        kind = "text"
    elif content_type in ("application/json", "application/ld+json"):
        try:
            doc = json.loads(_decode(body, charset))
        except ValueError as exc:
            raise ValueError(EXTRACTION_FAILED, f"invalid JSON: {exc}") from exc
        if content_type == "application/ld+json":
            ld = _jsonld_fields([json.dumps(doc)])
            for f in ("title", "author", "publisher", "published_at", "modified_at"):
                if ld.get(f) and (f not in ("published_at", "modified_at") or _norm_date(ld[f])):
                    meta[f], provenance[f] = ld[f], f"json-ld:{f}"
        text, blocks = _assemble([{"kind": "pre", "text": json.dumps(doc, indent=1, ensure_ascii=False)[:MAX_TEXT_CHARS]}])
        kind = "json"
    elif content_type in ("application/xml", "text/xml", "application/rss+xml", "application/atom+xml"):
        raw = re.sub(r"<[^>]{0,2000}>", " ", _decode(body, charset))
        text, blocks = _assemble([{"kind": "p", "text": _WS.sub(" ", raw).strip()}])
        kind = "xml"
    elif content_type == "application/pdf":
        raise ValueError(BLOCKED_DEPENDENCY, "PDF text extraction needs an approved PDF parser; none is part of the "
                         "locked dependency set (Stage 11 records the snapshot, extraction is BLOCKED)")
    else:
        raise ValueError(UNSUPPORTED_CONTENT, f"no extractor for content type '{content_type}'")
    return {"extractor": "atta-research-extract/1", "kind": kind, "metadata": meta,
            "metadata_provenance": provenance, "text": text, "blocks": blocks, "links": links[:MAX_LINKS],
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "text_chars": len(text),
            "hidden_text_chars_excluded": hidden_chars}
