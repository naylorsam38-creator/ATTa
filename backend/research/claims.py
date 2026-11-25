"""Claims, citation binding and contradiction analysis (§16, §17, §19).

Research output is stored as structured claims, not prose. A claim builder (the Stage 03 Hub model, or an explicit
test fixture until then) PROPOSES ClaimInputs; this module decides what is stored:

  * every support/contradiction reference must resolve to evidence of THIS research: an evidence id the service
    issued, or an exact quote that occurs in a stored snapshot of a source of this research — anything else (an
    invented citation id, another research's evidence, a quote not in the source) is rejected;
  * citation identifiers are minted here (cit_<ULID>); a builder can never choose one;
  * support status is derived from the bound evidence, not from the builder's say-so;
  * contradictions are recorded — explicit counter-evidence, and structured claims (same subject + attribute,
    different value) — with supporting/contradicting sources, date and scope differences; nothing is hidden."""
from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from .contracts import ClaimInput, ResearchContract
from .errors import ResearchError, ResearchErrorCode
from .ids import is_rid
from .hub import HubClaimBuilder  # noqa: F401 — Stage 03 claim builder (re-exported)
from .store import ResearchStore


@runtime_checkable
class ClaimBuilder(Protocol):
    name: str

    def available(self) -> bool: ...
    def build(self, contract: ResearchContract, evidence_packet: dict) -> list[ClaimInput]: ...


class ExplicitClaims:
    """TEST FIXTURE — NOT A MODEL. Replays claims written explicitly by a test (labelled builder='explicit-test')."""
    name = "explicit-test"

    def __init__(self, claims: list[dict] | callable):
        self._claims = claims
        self.seen_packets: list[dict] = []

    def available(self) -> bool:
        return True

    def build(self, contract, evidence_packet):
        self.seen_packets.append(evidence_packet)
        items = self._claims(evidence_packet) if callable(self._claims) else self._claims
        return [ClaimInput.model_validate({**c, "builder": "explicit-test"}) for c in items]


class CitationRejected(ResearchError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(ResearchErrorCode.VERIFICATION_FAILED, message, details)


def _locate(store: ResearchStore, research_id: str, ref: dict, purpose: str, subq: int | None) -> dict:
    """Resolve one reference to a stored evidence row of this research. Raises CitationRejected."""
    if set(ref) - {"evidence_id", "source_id", "quote"}:
        raise CitationRejected(f"unknown citation reference fields {sorted(set(ref) - {'evidence_id', 'source_id', 'quote'})}")
    if "evidence_id" in ref:
        eid = ref["evidence_id"]
        if not isinstance(eid, str) or not is_rid(eid, "rev"):
            raise CitationRejected(f"'{eid}' is not an evidence identifier issued by the research service")
        ev = store.evidence(eid)
        if ev is None or ev["research_id"] != research_id:
            raise CitationRejected(f"evidence {eid} does not exist in this research")
        return ev
    sid, quote = ref.get("source_id"), ref.get("quote")
    if not isinstance(sid, str) or not is_rid(sid, "src") or not isinstance(quote, str) or len(quote.strip()) < 8:
        raise CitationRejected("a quote citation needs a stored source_id and a quote of at least 8 characters")
    src = store.source(sid)
    if src is None or src["research_id"] != research_id:
        raise CitationRejected(f"source {sid} does not belong to this research")
    if not src["snapshot_id"]:
        raise CitationRejected(f"source {sid} has no stored content to cite")
    snap = store.snapshot(src["snapshot_id"])
    start = snap["text"].find(quote)
    if start < 0:
        raise CitationRejected(f"the quoted text does not occur in the stored content of {sid}")
    end = start + len(quote)
    eid = store.add_evidence(research_id, source_id=sid, snapshot_id=snap["snapshot_id"], start=start, end=end,
                             quote=quote, quote_sha256=hashlib.sha256(quote.encode()).hexdigest(),
                             text_sha256=snap["text_sha256"], purpose=purpose, subquestion_index=subq)
    return store.evidence(eid)


def bind_claims(store: ResearchStore, contract: ResearchContract, inputs: list[ClaimInput]) -> dict:
    """Validate and persist claims with their citations. Returns {'claims': [...ids], 'rejected': [...]}."""
    rid = contract.research_id
    need = contract.citation_requirements.min_citations_per_claim if contract.citation_requirements.require_citations else 0
    stored, rejected = [], []
    for ci in inputs:
        try:
            support = [_locate(store, rid, r, "support", ci.subquestion_index) for r in ci.support]
            against = [_locate(store, rid, r, "contradiction", ci.subquestion_index) for r in ci.contradicts]
        except CitationRejected as exc:
            rejected.append({"claim": ci.text[:200], "builder": ci.builder, "reason": exc.message})
            store.event(rid, "claim.rejected", {"claim": ci.text[:200], "builder": ci.builder, "reason": exc.message})
            continue
        support_sources = {e["source_id"] for e in support}
        if against and support:
            status = "contested"
        elif len(support) >= max(need, 1):
            status = "supported"
        elif support:
            status = "partially_supported"
        else:
            status = "unsupported"
        structured = {k: getattr(ci, k) for k in ("subject", "attribute", "value", "as_of", "scope")
                      if getattr(ci, k) is not None}
        cid = store.add_claim(rid, claim_text=ci.text, material=ci.material, subquestion_index=ci.subquestion_index,
                              structured=structured, support_status=status,
                              contradiction_status="contradicted" if against else "none",
                              confidence={"stated_by_builder": ci.confidence,
                                          "independent_supporting_sources": len(support_sources)},
                              builder=ci.builder)
        for ev in support:
            store.add_claim_source(cid, ev["source_id"], ev["evidence_id"], "supports")
            src = store.source(ev["source_id"])
            store.add_citation(rid, claim_id=cid, source_id=ev["source_id"], evidence_id=ev["evidence_id"],
                               start=ev["span_start"], end=ev["span_end"], quote_sha256=ev["quote_sha256"],
                               text_sha256=ev["text_sha256"],
                               locator=f"chars {ev['span_start']}-{ev['span_end']}",
                               metadata={"url": src["final_url"] or src["original_url"], "title": src["title"],
                                         "author": src["author"], "publisher": src["publisher"],
                                         "published_at": src["published_at"],
                                         "retrieved_at": str(src["retrieved_at"]) if src["retrieved_at"] else None,
                                         "content_hash": src["content_hash"]})
        for ev in against:
            store.add_claim_source(cid, ev["source_id"], ev["evidence_id"], "contradicts")
        if against:
            _record(store, rid, cid, None, sorted(support_sources), sorted({e["source_id"] for e in against}),
                    "sources disagree with this claim (explicit counter-evidence)", "claim-builder:" + ci.builder)
        stored.append(cid)
    return {"claims": stored, "rejected": rejected}


def _dates(store: ResearchStore, source_ids: list[str]) -> dict:
    out = {}
    for s in source_ids:
        row = store.source(s)
        out[s] = {"published_at": row["published_at"], "retrieved_at": str(row["retrieved_at"])}
    return out


def _record(store, rid, claim_id, other, supporting, contradicting, detail, by, scope=None, status="unresolved"):
    return store.add_contradiction(rid, claim_id=claim_id, other_claim_id=other, supporting_source_ids=supporting,
                                   contradicting_source_ids=contradicting,
                                   date_differences=_dates(store, supporting + contradicting),
                                   scope_differences=scope or {}, status=status, detail=detail, detected_by=by)


def analyse_contradictions(store: ResearchStore, research_id: str) -> list[str]:
    """Deterministic structural check: claims about the same subject+attribute with different values."""
    claims = store.claims(research_id)
    links = store.claim_sources(research_id)
    support = {}
    for lk in links:
        if lk["relation"] == "supports":
            support.setdefault(lk["claim_id"], set()).add(lk["source_id"])
    groups: dict[tuple, list[dict]] = {}
    for c in claims:
        s = c["structured"] or {}
        if s.get("subject") and s.get("attribute") and s.get("value") is not None:
            groups.setdefault((s["subject"].strip().lower(), s["attribute"].strip().lower()), []).append(c)
    recorded = []
    for (subject, attribute), cs in groups.items():
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                a, b = cs[i], cs[j]
                va, vb = str(a["structured"]["value"]).strip().lower(), str(b["structured"]["value"]).strip().lower()
                if va == vb:
                    continue
                scope = {"a": {"as_of": a["structured"].get("as_of"), "scope": a["structured"].get("scope")},
                         "b": {"as_of": b["structured"].get("as_of"), "scope": b["structured"].get("scope")}}
                differs = scope["a"] != scope["b"]
                status = "possibly_scope_or_time" if differs else "unresolved"
                detail = (f"'{subject}' {attribute}: '{a['structured']['value']}' vs '{b['structured']['value']}'" +
                          (" — the claims differ in date or scope, so they may both hold; not resolved automatically"
                           if differs else " — unresolved disagreement between sources"))
                recorded.append(_record(store, research_id, a["claim_id"], b["claim_id"],
                                        sorted(support.get(a["claim_id"], set())),
                                        sorted(support.get(b["claim_id"], set())), detail,
                                        "structured-claim-comparison/1", scope, status))
                for c in (a, b):
                    store.update_claim(c["claim_id"], contradiction_status="unresolved",
                                       support_status="contested" if c["support_status"] == "supported" else
                                       c["support_status"])
    return recorded
