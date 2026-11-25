"""Source deduplication (§14): same URL, identical content, near-duplicate and syndicated material.

A duplicate is still recorded (provenance is preserved) but references the source it duplicates, and it shares
that source's independence group, so the same story syndicated on three sites never counts as three independent
corroborations. The 5-word-shingle Jaccard here is the same algorithm as capabilities/research_evidence (kept
equal by tests/research/test_dedup.py)."""
from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = re.compile(r"[a-z0-9]+")
SHINGLE = 5
NEAR_DUPLICATE = 0.8
CONTAINMENT = 0.9


def shingles(text: str, k: int = SHINGLE) -> set:
    words = _WORD.findall(text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def containment(a: set, b: set) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


@dataclass(frozen=True)
class DuplicateMatch:
    source_id: str
    relation: str          # same_url | identical_content | near_duplicate | syndicated
    score: float


def find_duplicate(*, canonical_url: str | None, content_hash: str | None, text: str | None, domain: str | None,
                   existing: list[dict], texts: dict[str, str]) -> DuplicateMatch | None:
    """existing: prior source rows of the same research (originals only); texts: snapshot text per source_id."""
    for s in existing:
        if s["duplicate_of"]:
            continue
        if canonical_url and s["canonical_url"] == canonical_url:
            return DuplicateMatch(s["source_id"], "same_url", 1.0)
    for s in existing:
        if s["duplicate_of"]:
            continue
        if content_hash and s["content_hash"] == content_hash:
            return DuplicateMatch(s["source_id"], "identical_content", 1.0)
    if not text:
        return None
    mine = shingles(text)
    best: DuplicateMatch | None = None
    for s in existing:
        other = texts.get(s["source_id"])
        if s["duplicate_of"] or not other:
            continue
        theirs = shingles(other)
        j, c = jaccard(mine, theirs), containment(mine, theirs)
        if j >= NEAR_DUPLICATE or c >= CONTAINMENT:
            rel = "near_duplicate" if (domain and s["domain"] and domain == s["domain"]) else "syndicated"
            score = round(max(j, c), 4)
            if best is None or score > best.score:
                best = DuplicateMatch(s["source_id"], rel, score)
    return best
