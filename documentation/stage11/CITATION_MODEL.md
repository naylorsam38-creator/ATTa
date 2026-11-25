# Stage 11 — Claim, evidence and citation model

```
research.runs ─┬─ research.sources ── research.snapshots (content-addressed text of the retrieved bytes)
               │        │
               │        └── research.evidence (exact span [start,end) of a snapshot, quote + hashes)
               │                   │
               ├─ research.claims ─┼── research.claim_sources (claim, source, evidence, supports|contradicts)
               │                   └── research.citations (minted cit_<ULID>, bound to claim + source + evidence)
               └─ research.contradictions (claim vs claim / counter-evidence, sources, dates, scope, status)
```

## Source record (`research.sources`)

`source_id, research_id, kind, original_url, canonical_url, final_url, domain, title, publisher, author,
published_at (as stated, else NULL), modified_at, retrieved_at, content_type, content_hash (SHA-256 of bytes),
text_hash, extraction_status, error_code, quality (factors), snapshot_id (stored evidence), citation_metadata,
metadata_provenance, duplicate_of, duplicate_relation, independence_group, depth, discovered_by, task_id,
execution_id`. A URL is never evidence: the content hash and stored snapshot are.

## Claims

A claim builder proposes `ClaimInput` (text, material flag, optional structured form subject/attribute/value/as_of/
scope, `support` and `contradicts` references, builder label). Each reference is one of:

* `{"evidence_id": "rev_…"}` — an evidence id issued by the service for **this** research; or
* `{"source_id": "src_…", "quote": "…"}` — the quote must occur verbatim in the stored snapshot text of a source of
  this research; the service creates the evidence span.

Anything else is rejected and recorded (`claim.rejected`): invented ids, ids of another research, quotes not in the
source, unknown fields such as a self-chosen `citation_id`. Support status is derived from bound evidence
(`supported`, `partially_supported`, `unsupported`, `contested`), not from the builder.

## Citations

Created only by `bind_claims`, one per supporting evidence row: `citation_id` (minted), `claim_id`, `source_id`,
`evidence_id`, `span_start/end`, `quote_sha256`, `text_sha256`, `locator` ("chars s-e"), metadata (URL, title,
author, publisher, published/retrieved times, content hash). The binding columns are immutable (trigger).

## Independent citation verification (`ResearchVerifier`)

For every citation: source exists and belongs to the research; evidence exists, belongs to the research and the
same source; a claim/source link records it; the stored snapshot text still hashes to its `text_sha256`; the
citation refers to that text version; the text at the span hashes to `quote_sha256`; the raw snapshot bytes on disk
still hash to the content hash. For every material claim: at least `min_citations_per_claim` verified citations
when the contract requires citations. Results are written back (`citations.verified`, `claims.verification_status`)
and the research fails with `VERIFICATION_FAILED` otherwise. This runs on persisted records after the worker
finished, independently of any completion claim.

## Contradictions

* Explicit counter-evidence on a claim → contradiction with supporting vs contradicting sources.
* Structured claims with the same subject + attribute and different values → contradiction between the claims;
  if their `as_of` or `scope` differ the status is `possibly_scope_or_time` (both may hold), otherwise
  `unresolved`. Date differences record each source's stated publication and retrieval time.
* Every contradiction appears in the result (`contradictions`, `unresolved_questions`, limitation
  `UNRESOLVED_CONTRADICTION`); none is resolved silently.

## Duplicates and independence

`same_url`, `identical_content`, `near_duplicate` (same domain) and `syndicated` (different domain, ≥ 0.8 shingle
Jaccard or ≥ 0.9 containment). A duplicate keeps its own record and provenance but shares the original's
`independence_group`, so it never counts as independent corroboration.
