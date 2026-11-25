"""Source-quality metadata (§15) and freshness evaluation (§20).

Quality is a set of explicit, inspectable factors. There is deliberately NO universal truth score: the factors
help planning and verification, they never decide what is true. Unknown stays unknown (None / False with a note).

Freshness: retrieval time is always known; publication time only when the source states it. For a current-
information request, a source without a stated date, or older than the allowed age, cannot satisfy freshness —
it is never silently treated as fresh."""
from __future__ import annotations

from datetime import datetime, timezone

from .contracts import Freshness, SourceQuality
from .urls import domain_matches, registrable_group


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    if len(v) == 10:
        v += "T00:00:00+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def assess_quality(*, domain: str | None, extraction: dict | None, official_suffixes: list[str],
                   independence_group: str | None, retrieval_complete: bool, primary: bool | None = None) -> SourceQuality:
    meta = (extraction or {}).get("metadata") or {}
    notes = []
    official = None
    if domain:
        official = any(domain.endswith(s) or domain_matches(domain, s.lstrip(".")) for s in official_suffixes)
    if primary is None:
        notes.append("primary/secondary not determined (needs claim-level judgement: Stage 03 or reviewer)")
    if not meta.get("published_at"):
        notes.append("publication date not stated by the source")
    return SourceQuality(
        primary=primary, official=official, publication_identified=bool(meta.get("publisher")),
        author_identified=bool(meta.get("author")), publication_date_known=bool(meta.get("published_at")),
        has_citations=bool((extraction or {}).get("links")),
        independence_group=independence_group or (registrable_group(domain) if domain else "local"),
        retrieval_complete=retrieval_complete, notes=notes)


def freshness_of(source: dict, policy: Freshness, reference: datetime) -> dict:
    """Return {'fresh': bool|None, 'reason': str, 'age_days': float|None} for one source record."""
    published = parse_time(source.get("published_at")) or parse_time(source.get("modified_at"))
    if not policy.requires_current and policy.max_age_days is None:
        return {"fresh": None, "reason": "no freshness requirement", "age_days": None}
    if published is None:
        return {"fresh": False, "age_days": None,
                "reason": "publication date unknown: retrieval time alone cannot prove the content is current"}
    age = (reference - published).total_seconds() / 86400
    limit = policy.max_age_days if policy.max_age_days is not None else 365
    if age < -1:
        return {"fresh": False, "age_days": round(age, 2), "reason": "stated publication date is in the future"}
    if age > limit:
        return {"fresh": False, "age_days": round(age, 2), "reason": f"published {age:.0f} days before the reference "
                f"time; the limit is {limit} days"}
    return {"fresh": True, "age_days": round(age, 2), "reason": f"published within {limit} days"}
