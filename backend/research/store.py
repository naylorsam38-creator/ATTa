"""Persistent Stage 11 research state (schema `research`), runtime credentials only (§24, §32, §34).

Same conventions as backend/orchestration/store.py: one transaction per safety-relevant mutation, conditional
updates for budgets (a budget can be consumed only while consumed + amount <= limit), integrity hashes on stored
contracts, checkpoints and results, and an append-only event trail carrying the execution identifiers."""
from __future__ import annotations

import json
from typing import Any

from backend.contracts import canonical_hash
from backend.database.session import Database

from .contracts import ResearchContract, ResearchStatus, TERMINAL_STATUSES
from .ids import rid


def _j(v: Any) -> str:
    return json.dumps(v, default=str)


class ResearchIntegrityError(RuntimeError):
    pass


class ResearchStore:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ runs + contracts
    def create_run(self, c: ResearchContract, budgets: dict[str, float], *, principal_id: str | None,
                   tenant_id: str | None, reason: str = "created") -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO research.runs (research_id, tenant_id, principal_id, objective_id, parent_task_id, "
                      "mode, question, status, current_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (c.research_id, tenant_id, principal_id, c.objective_id, c.task_id, c.research_type.value,
                       c.question, c.status.value, c.contract_version))
            x.execute("INSERT INTO research.contract_versions (research_id, version, contract, contract_hash, reason) "
                      "VALUES (%s,%s,%s,%s,%s)", (c.research_id, c.contract_version, _j(c.body()), c.hash(), reason))
            for metric, limit in budgets.items():
                x.execute("INSERT INTO research.budgets (research_id, metric, limit_value) VALUES (%s,%s,%s)",
                          (c.research_id, metric, limit))
            self._event(x, c.research_id, "contract.created", {"version": c.contract_version, "hash": c.hash(),
                                                               "budgets": budgets})

    def add_contract_version(self, c: ResearchContract, reason: str, authorised_by: str | None,
                             new_limits: dict[str, float] | None = None) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO research.contract_versions (research_id, version, contract, contract_hash, reason, "
                      "authorised_by) VALUES (%s,%s,%s,%s,%s,%s)",
                      (c.research_id, c.contract_version, _j(c.body()), c.hash(), reason, authorised_by))
            x.execute("UPDATE research.runs SET current_version = %s, objective_id = COALESCE(%s, objective_id), "
                      "updated_at = now() WHERE research_id = %s", (c.contract_version, c.objective_id, c.research_id))
            for metric, limit in (new_limits or {}).items():   # lowering only: the DB trigger refuses raises
                x.execute("UPDATE research.budgets SET limit_value = GREATEST(%s, consumed) WHERE research_id = %s "
                          "AND metric = %s", (limit, c.research_id, metric))
            self._event(x, c.research_id, "contract.versioned", {"version": c.contract_version, "hash": c.hash(),
                                                                 "reason": reason, "authorised_by": authorised_by})

    def contract(self, research_id: str, version: int | None = None) -> ResearchContract:
        with self.db.tx() as x:
            if version is None:
                row = x.execute("SELECT * FROM research.contract_versions WHERE research_id = %s ORDER BY version DESC "
                                "LIMIT 1", (research_id,)).fetchone()
            else:
                row = x.execute("SELECT * FROM research.contract_versions WHERE research_id = %s AND version = %s",
                                (research_id, version)).fetchone()
        if row is None:
            raise KeyError(research_id)
        c = ResearchContract.model_validate(row["contract"])
        if c.hash() != row["contract_hash"]:
            raise ResearchIntegrityError(f"research contract {research_id} v{row['version']} fails its integrity hash")
        return c

    def contract_versions(self, research_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT version, contract_hash, reason, authorised_by, created_at FROM "
                             "research.contract_versions WHERE research_id = %s ORDER BY version",
                             (research_id,)).fetchall()

    def run(self, research_id: str) -> dict:
        with self.db.tx() as x:
            row = x.execute("SELECT * FROM research.runs WHERE research_id = %s", (research_id,)).fetchone()
        if row is None:
            raise KeyError(research_id)
        return row

    def list_runs(self, limit: int = 50, after: str | None = None, tenant_id: str | None = None) -> list[dict]:
        with self.db.tx() as x:
            q = "SELECT research_id, mode, question, status, verification_status, created_at, updated_at FROM " \
                "research.runs WHERE (%s::text IS NULL OR research_id > %s) AND (%s::text IS NULL OR tenant_id = %s) " \
                "ORDER BY research_id LIMIT %s"
            return x.execute(q, (after, after, tenant_id, tenant_id, limit)).fetchall()

    def set_status(self, research_id: str, status: ResearchStatus, *, reason: str, error: dict | None = None,
                   phase: str | None = None, verification_status: str | None = None, batch: int | None = None,
                   started: bool = False, deadline_s: int | None = None) -> dict:
        with self.db.tx() as x:
            cur = x.execute("SELECT status FROM research.runs WHERE research_id = %s FOR UPDATE",
                            (research_id,)).fetchone()
            if ResearchStatus(cur["status"]) in TERMINAL_STATUSES - {ResearchStatus.BLOCKED} and \
                    ResearchStatus(cur["status"]) is not status:
                raise ResearchIntegrityError(f"research {research_id} is final ({cur['status']})")
            row = x.execute(
                "UPDATE research.runs SET status = %s, error = COALESCE(%s, error), phase = COALESCE(%s, phase), "
                "verification_status = COALESCE(%s, verification_status), batch = COALESCE(%s, batch), "
                "started_at = CASE WHEN %s AND started_at IS NULL THEN now() ELSE started_at END, "
                "deadline_at = CASE WHEN %s::int IS NOT NULL AND deadline_at IS NULL THEN now() + make_interval(secs => %s::int) "
                "ELSE deadline_at END, updated_at = now() WHERE research_id = %s RETURNING *",
                (status.value, _j(error) if error is not None else None, phase, verification_status, batch, started,
                 deadline_s, deadline_s, research_id)).fetchone()
            self._event(x, research_id, "status", {"from": cur["status"], "to": status.value, "reason": reason,
                                                   "phase": phase, "error": error})
        return row

    def request_cancel(self, research_id: str, reason: str) -> dict:
        with self.db.tx() as x:
            row = x.execute("UPDATE research.runs SET cancel_requested = true, status = CASE WHEN status IN "
                            "('CREATED','RUNNING','PAUSED','BLOCKED') THEN 'CANCELLING' ELSE status END, "
                            "updated_at = now() WHERE research_id = %s RETURNING *", (research_id,)).fetchone()
            self._event(x, research_id, "cancel.requested", {"reason": reason})
        return row

    def cancel_requested(self, research_id: str) -> bool:
        with self.db.tx() as x:
            return bool(x.execute("SELECT cancel_requested FROM research.runs WHERE research_id = %s",
                                  (research_id,)).fetchone()["cancel_requested"])

    def deadline_passed(self, research_id: str) -> bool:
        with self.db.tx() as x:
            r = x.execute("SELECT deadline_at IS NOT NULL AND now() > deadline_at AS late FROM research.runs WHERE "
                          "research_id = %s", (research_id,)).fetchone()
        return bool(r["late"])

    # ------------------------------------------------------------------ budgets
    def consume(self, research_id: str, metric: str, amount: float = 1) -> bool:
        """Atomic: succeeds only while consumed + amount <= limit. The worker never calls this; the service does."""
        with self.db.tx() as x:
            row = x.execute("UPDATE research.budgets SET consumed = consumed + %s WHERE research_id = %s AND "
                            "metric = %s AND consumed + %s <= limit_value RETURNING consumed, limit_value",
                            (amount, research_id, metric, amount)).fetchone()
            if row is None:
                self._event(x, research_id, "budget.exhausted", {"metric": metric, "requested": amount})
        return row is not None

    def remaining(self, research_id: str, metric: str) -> float:
        b = self.budgets(research_id)[metric]
        return b["remaining"]

    def budgets(self, research_id: str) -> dict[str, dict]:
        with self.db.tx() as x:
            rows = x.execute("SELECT metric, limit_value, consumed FROM research.budgets WHERE research_id = %s",
                             (research_id,)).fetchall()
        return {r["metric"]: {"limit": float(r["limit_value"]), "consumed": float(r["consumed"]),
                              "remaining": float(r["limit_value"] - r["consumed"])} for r in rows}

    # ------------------------------------------------------------------ queries
    def add_query(self, research_id: str, **f) -> str:
        qid = rid("rqy")
        with self.db.tx() as x:
            x.execute("INSERT INTO research.queries (query_id, research_id, subquestion_index, query_text, provider_id, "
                      "provider_kind, objective_id, task_id, execution_id, status, error_code, result_count, "
                      "response_sha256, results) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (qid, research_id, f.get("subquestion_index"), f["query_text"], f.get("provider_id"),
                       f.get("provider_kind"), f.get("objective_id"), f.get("task_id"), f.get("execution_id"),
                       f["status"], f.get("error_code"), len(f.get("results") or []), f.get("response_sha256"),
                       _j(f.get("results") or [])))
            self._event(x, research_id, "query." + f["status"], {"query": f["query_text"], "provider": f.get("provider_id"),
                        "error_code": f.get("error_code"), "results": len(f.get("results") or [])},
                        objective_id=f.get("objective_id"), task_id=f.get("task_id"),
                        execution_id=f.get("execution_id"), query_id=qid)
        return qid

    def queries(self, research_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM research.queries WHERE research_id = %s ORDER BY executed_at, query_id",
                             (research_id,)).fetchall()

    # ------------------------------------------------------------------ snapshots / sources / evidence
    def add_snapshot(self, research_id: str, *, content_sha256: str, text_sha256: str, content_type: str, nbytes: int,
                     text: str, blocks: list, links: list, raw_path: str | None) -> str:
        with self.db.tx() as x:
            row = x.execute("SELECT snapshot_id FROM research.snapshots WHERE research_id = %s AND content_sha256 = %s",
                            (research_id, content_sha256)).fetchone()
            if row:
                return row["snapshot_id"]
            sid = rid("snp")
            x.execute("INSERT INTO research.snapshots (snapshot_id, research_id, content_sha256, text_sha256, "
                      "content_type, bytes, text, blocks, links, raw_path) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (sid, research_id, content_sha256, text_sha256, content_type, nbytes, text, _j(blocks),
                       _j(links), raw_path))
        return sid

    def snapshot(self, snapshot_id: str) -> dict | None:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM research.snapshots WHERE snapshot_id = %s", (snapshot_id,)).fetchone()

    def add_source(self, research_id: str, **f) -> str:
        sid = rid("src")
        cols = ["kind", "original_url", "canonical_url", "final_url", "domain", "title", "publisher", "author",
                "published_at", "modified_at", "retrieved_at", "content_type", "content_hash", "text_hash",
                "extraction_status", "error_code", "quality", "snapshot_id", "citation_metadata",
                "metadata_provenance", "duplicate_of", "duplicate_relation", "independence_group", "depth",
                "discovered_by", "objective_id", "task_id", "execution_id"]
        vals = [(_j(f.get(c) or {}) if c in ("quality", "citation_metadata", "metadata_provenance") else f.get(c))
                for c in cols]
        vals[cols.index("depth")] = f.get("depth") or 0
        with self.db.tx() as x:
            x.execute(f"INSERT INTO research.sources (source_id, research_id, {', '.join(cols)}) VALUES "
                      f"({', '.join(['%s'] * (len(cols) + 2))})", [sid, research_id, *vals])
            self._event(x, research_id, "source." + f["extraction_status"],
                        {"url": f.get("canonical_url"), "content_hash": f.get("content_hash"),
                         "error_code": f.get("error_code"), "duplicate_of": f.get("duplicate_of"),
                         "relation": f.get("duplicate_relation")},
                        objective_id=f.get("objective_id"), task_id=f.get("task_id"),
                        execution_id=f.get("execution_id"), source_id=sid)
        return sid

    def sources(self, research_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM research.sources WHERE research_id = %s ORDER BY created_at, source_id",
                             (research_id,)).fetchall()

    def source(self, source_id: str) -> dict | None:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM research.sources WHERE source_id = %s", (source_id,)).fetchone()

    def add_evidence(self, research_id: str, **f) -> str:
        eid = rid("rev")
        with self.db.tx() as x:
            x.execute("INSERT INTO research.evidence (evidence_id, research_id, source_id, snapshot_id, span_start, "
                      "span_end, quote, quote_sha256, text_sha256, purpose, subquestion_index, task_id, execution_id) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (eid, research_id, f["source_id"], f["snapshot_id"], f["start"], f["end"], f["quote"],
                       f["quote_sha256"], f["text_sha256"], f["purpose"], f.get("subquestion_index"),
                       f.get("task_id"), f.get("execution_id")))
            self._event(x, research_id, "evidence.captured", {"purpose": f["purpose"], "span": [f["start"], f["end"]]},
                        task_id=f.get("task_id"), execution_id=f.get("execution_id"), source_id=f["source_id"])
        return eid

    def evidence(self, evidence_id: str) -> dict | None:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM research.evidence WHERE evidence_id = %s", (evidence_id,)).fetchone()

    def evidence_for(self, research_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM research.evidence WHERE research_id = %s ORDER BY created_at, evidence_id",
                             (research_id,)).fetchall()

    # ------------------------------------------------------------------ claims / citations / contradictions
    def add_claim(self, research_id: str, **f) -> str:
        cid = rid("clm")
        with self.db.tx() as x:
            x.execute("INSERT INTO research.claims (claim_id, research_id, claim_text, material, subquestion_index, "
                      "structured, support_status, contradiction_status, confidence, builder) VALUES "
                      "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (cid, research_id, f["claim_text"], f["material"], f.get("subquestion_index"),
                       _j(f.get("structured") or {}), f["support_status"], f.get("contradiction_status", "none"),
                       _j(f.get("confidence") or {}), f["builder"]))
            self._event(x, research_id, "claim.created", {"builder": f["builder"], "support": f["support_status"]},
                        claim_id=cid)
        return cid

    def update_claim(self, claim_id: str, **f) -> None:
        sets = [f"{k} = %s" for k in ("support_status", "contradiction_status", "verification_status") if k in f]
        with self.db.tx() as x:
            row = x.execute(f"UPDATE research.claims SET {', '.join(sets)} WHERE claim_id = %s RETURNING research_id",
                            [f[k] for k in ("support_status", "contradiction_status", "verification_status") if k in f]
                            + [claim_id]).fetchone()
            self._event(x, row["research_id"], "claim.updated", f, claim_id=claim_id)

    def claims(self, research_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM research.claims WHERE research_id = %s ORDER BY created_at, claim_id",
                             (research_id,)).fetchall()

    def add_claim_source(self, claim_id: str, source_id: str, evidence_id: str, relation: str) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO research.claim_sources (claim_id, source_id, evidence_id, relation) VALUES "
                      "(%s,%s,%s,%s) ON CONFLICT DO NOTHING", (claim_id, source_id, evidence_id, relation))

    def claim_sources(self, research_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT cs.* FROM research.claim_sources cs JOIN research.claims c USING (claim_id) "
                             "WHERE c.research_id = %s ORDER BY cs.created_at", (research_id,)).fetchall()

    def add_citation(self, research_id: str, **f) -> str:
        cid = rid("cit")
        with self.db.tx() as x:
            x.execute("INSERT INTO research.citations (citation_id, research_id, claim_id, source_id, evidence_id, "
                      "span_start, span_end, quote_sha256, text_sha256, locator, metadata) VALUES "
                      "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (cid, research_id, f["claim_id"], f["source_id"], f["evidence_id"], f["start"], f["end"],
                       f["quote_sha256"], f["text_sha256"], f["locator"], _j(f.get("metadata") or {})))
            self._event(x, research_id, "citation.bound", {"evidence_id": f["evidence_id"]}, claim_id=f["claim_id"],
                        source_id=f["source_id"], citation_id=cid)
        return cid

    def set_citation_verification(self, citation_id: str, verified: bool, detail: dict) -> None:
        with self.db.tx() as x:
            row = x.execute("UPDATE research.citations SET verified = %s, verification = %s WHERE citation_id = %s "
                            "RETURNING research_id", (verified, _j(detail), citation_id)).fetchone()
            self._event(x, row["research_id"], "citation.verified" if verified else "citation.rejected", detail,
                        citation_id=citation_id)

    def citations(self, research_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM research.citations WHERE research_id = %s ORDER BY created_at, citation_id",
                             (research_id,)).fetchall()

    def citation(self, citation_id: str) -> dict | None:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM research.citations WHERE citation_id = %s", (citation_id,)).fetchone()

    def add_contradiction(self, research_id: str, **f) -> str:
        cid = rid("cdn")
        with self.db.tx() as x:
            x.execute("INSERT INTO research.contradictions (contradiction_id, research_id, claim_id, other_claim_id, "
                      "supporting_source_ids, contradicting_source_ids, date_differences, scope_differences, status, "
                      "detail, detected_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (cid, research_id, f["claim_id"], f.get("other_claim_id"), _j(f.get("supporting_source_ids", [])),
                       _j(f.get("contradicting_source_ids", [])), _j(f.get("date_differences", {})),
                       _j(f.get("scope_differences", {})), f["status"], f["detail"], f["detected_by"]))
            self._event(x, research_id, "contradiction.recorded", {"status": f["status"], "detail": f["detail"]},
                        claim_id=f["claim_id"])
        return cid

    def contradictions(self, research_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM research.contradictions WHERE research_id = %s ORDER BY created_at",
                             (research_id,)).fetchall()

    # ------------------------------------------------------------------ checkpoints / events / results
    def add_checkpoint(self, research_id: str, data: dict) -> dict:
        with self.db.tx() as x:
            seq = x.execute("SELECT COALESCE(max(sequence), 0) + 1 AS n FROM research.checkpoints WHERE research_id = %s",
                            (research_id,)).fetchone()["n"]
            cid = rid("rck")
            h = canonical_hash(data)
            x.execute("INSERT INTO research.checkpoints (checkpoint_id, research_id, sequence, data, data_hash) VALUES "
                      "(%s,%s,%s,%s,%s)", (cid, research_id, seq, _j(data), h))
            self._event(x, research_id, "checkpoint", {"checkpoint_id": cid, "sequence": seq, "hash": h})
        return {"checkpoint_id": cid, "sequence": seq, "data_hash": h}

    def latest_checkpoint(self, research_id: str) -> dict | None:
        with self.db.tx() as x:
            row = x.execute("SELECT * FROM research.checkpoints WHERE research_id = %s ORDER BY sequence DESC LIMIT 1",
                            (research_id,)).fetchone()
        if row and canonical_hash(row["data"]) != row["data_hash"]:
            raise ResearchIntegrityError(f"research checkpoint {row['checkpoint_id']} fails its hash")
        return row

    def event(self, research_id: str, kind: str, detail: dict, **refs) -> None:
        with self.db.tx() as x:
            self._event(x, research_id, kind, detail, **refs)

    @staticmethod
    def _event(x, research_id: str, kind: str, detail: dict, *, objective_id=None, task_id=None, execution_id=None,
               query_id=None, source_id=None, claim_id=None, citation_id=None) -> None:
        x.execute("INSERT INTO research.events (event_id, research_id, kind, detail, objective_id, task_id, "
                  "execution_id, query_id, source_id, claim_id, citation_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (rid("ret"), research_id, kind, _j(detail), objective_id, task_id, execution_id, query_id, source_id,
                   claim_id, citation_id))

    def events(self, research_id: str, kinds: tuple[str, ...] | None = None) -> list[dict]:
        with self.db.tx() as x:
            rows = x.execute("SELECT * FROM research.events WHERE research_id = %s ORDER BY at, event_id",
                             (research_id,)).fetchall()
        return [r for r in rows if not kinds or r["kind"].startswith(kinds)]

    def add_result(self, research_id: str, result: dict) -> dict:
        with self.db.tx() as x:
            seq = x.execute("SELECT COALESCE(max(sequence), 0) + 1 AS n FROM research.results WHERE research_id = %s",
                            (research_id,)).fetchone()["n"]
            h = canonical_hash(result)
            x.execute("INSERT INTO research.results (research_id, sequence, result, result_hash) VALUES (%s,%s,%s,%s)",
                      (research_id, seq, _j(result), h))
            self._event(x, research_id, "result.stored", {"sequence": seq, "hash": h,
                                                          "status": result.get("research_status")})
        return {"sequence": seq, "result_hash": h}

    def latest_result(self, research_id: str) -> dict | None:
        with self.db.tx() as x:
            row = x.execute("SELECT * FROM research.results WHERE research_id = %s ORDER BY sequence DESC LIMIT 1",
                            (research_id,)).fetchone()
        if row and canonical_hash(row["result"]) != row["result_hash"]:
            raise ResearchIntegrityError(f"research result {research_id}#{row['sequence']} fails its hash")
        return row
