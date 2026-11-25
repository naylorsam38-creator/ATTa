"""Independent research verification (§18, §20) — run by the research service on PERSISTED records, never on the
worker's (or claim builder's) completion claim.

Checks: every citation resolves to a stored source of this research; the evidence row exists, belongs to the same
research and source, and is linked to the claim; the stored snapshot text still hashes to the recorded text hash;
the quoted span still hashes to the cited quote hash; the raw snapshot bytes on disk (when present) still hash to
the source's content hash; every material claim has the required citations; freshness requirements are evaluated
from stated publication dates; meaningful contradictions are exposed."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .contracts import ResearchContract, VerificationStatus
from .quality import freshness_of, parse_time
from .store import ResearchStore

NAME = "research-verifier/1"


class ResearchVerifier:
    def __init__(self, store: ResearchStore, workspace: Path | None = None):
        self.store, self.workspace = store, workspace

    def verify_citation(self, research_id: str, citation: dict, claim_ids: set[str], links: set[tuple]) -> dict:
        problems = []
        src = self.store.source(citation["source_id"])
        if src is None or src["research_id"] != research_id:
            problems.append("cited source does not exist in this research")
        ev = self.store.evidence(citation["evidence_id"])
        if ev is None or ev["research_id"] != research_id:
            problems.append("cited evidence does not exist in this research")
        elif ev["source_id"] != citation["source_id"]:
            problems.append("cited evidence belongs to a different source")
        if citation["claim_id"] not in claim_ids:
            problems.append("citation is bound to a claim outside this research")
        if (citation["claim_id"], citation["source_id"], citation["evidence_id"]) not in links:
            problems.append("no claim/source linkage records this citation")
        if src is not None and src["snapshot_id"]:
            snap = self.store.snapshot(src["snapshot_id"])
            text = snap["text"] if snap else None
            if snap is None:
                problems.append("stored snapshot missing")
            else:
                if hashlib.sha256(text.encode("utf-8")).hexdigest() != snap["text_sha256"]:
                    problems.append("stored text no longer matches its hash")
                if snap["text_sha256"] != citation["text_sha256"]:
                    problems.append("citation refers to a different version of the source text")
                s, e = citation["span_start"], citation["span_end"]
                if not (0 <= s < e <= len(text)):
                    problems.append("cited span is outside the stored text")
                elif hashlib.sha256(text[s:e].encode("utf-8")).hexdigest() != citation["quote_sha256"]:
                    problems.append("text at the cited span differs from the cited quote")
                if self.workspace and snap["raw_path"]:
                    raw = (self.workspace / snap["raw_path"]).resolve()
                    if self.workspace.resolve() in raw.parents and raw.is_file():
                        if hashlib.sha256(raw.read_bytes()).hexdigest() != snap["content_sha256"]:
                            problems.append("raw snapshot bytes no longer match the content hash")
                    else:
                        problems.append("raw snapshot file is missing")
        elif src is not None:
            problems.append("cited source has no stored content")
        return {"citation_id": citation["citation_id"], "verified": not problems, "problems": problems}

    def verify(self, contract: ResearchContract, *, reference_time: datetime | None = None,
               require_claims: bool = True) -> dict:
        rid = contract.research_id
        claims = self.store.claims(rid)
        claim_ids = {c["claim_id"] for c in claims}
        links_rows = self.store.claim_sources(rid)
        links = {(r["claim_id"], r["source_id"], r["evidence_id"]) for r in links_rows if r["relation"] == "supports"}
        cits = self.store.citations(rid)
        results = []
        for cit in cits:
            r = self.verify_citation(rid, cit, claim_ids, links)
            self.store.set_citation_verification(cit["citation_id"], r["verified"], {"verifier": NAME, **r})
            results.append(r)
        by_claim: dict[str, list[dict]] = {}
        for cit, r in zip(cits, results):
            by_claim.setdefault(cit["claim_id"], []).append(r)
        need = contract.citation_requirements.min_citations_per_claim if \
            contract.citation_requirements.require_citations else 0
        uncited, claim_results = [], []
        for c in claims:
            good = [r for r in by_claim.get(c["claim_id"], []) if r["verified"]]
            ok = len(good) >= need
            if c["material"] and not ok:
                uncited.append({"claim_id": c["claim_id"], "verified_citations": len(good), "required": need})
            status = VerificationStatus.VERIFIED if ok and all(r["verified"] for r in by_claim.get(c["claim_id"], [])) \
                else VerificationStatus.VERIFICATION_FAILED
            if not c["material"] and ok:
                status = VerificationStatus.VERIFIED
            self.store.update_claim(c["claim_id"], verification_status=status.value)
            claim_results.append({"claim_id": c["claim_id"], "status": status.value})
        # freshness
        ref = reference_time or parse_time(contract.required_freshness.reference_time) or datetime.now(timezone.utc)
        fresh = {}
        for s in self.store.sources(rid):
            if s["duplicate_of"] or s["extraction_status"] != "extracted":
                continue
            fresh[s["source_id"]] = freshness_of(s, contract.required_freshness, ref)
        freshness_required = contract.required_freshness.requires_current or \
            contract.required_freshness.max_age_days is not None
        cited_sources = {c["source_id"] for c in cits}
        relevant = {k: v for k, v in fresh.items() if not cited_sources or k in cited_sources}
        freshness_met = (not freshness_required) or any(v["fresh"] for v in relevant.values())
        contradictions = self.store.contradictions(rid)
        problems = []
        failed_cits = [r for r in results if not r["verified"]]
        if failed_cits:
            problems.append(f"{len(failed_cits)} citation(s) failed verification")
        if uncited:
            problems.append(f"{len(uncited)} material claim(s) lack the required verified citations")
        if require_claims and not claims:
            problems.append("no claims were established")
        passed = not problems
        return {"verifier": NAME, "passed": passed,
                "status": (VerificationStatus.VERIFIED if passed else VerificationStatus.VERIFICATION_FAILED).value,
                "citations": results, "claims": claim_results, "uncited_material_claims": uncited,
                "freshness": {"required": freshness_required, "met": freshness_met,
                              "reference_time": ref.isoformat(), "sources": fresh},
                "contradictions_exposed": len(contradictions), "problems": problems}
