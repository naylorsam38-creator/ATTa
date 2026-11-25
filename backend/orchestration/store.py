"""Persistent controller state in PostgreSQL (schema `control`), runtime credentials only.

Every mutation that matters for safety is a single transaction with row locks or conditional updates:
  * task transitions lock the task row and are checked against the state machine before being logged;
  * Focus-4 slot reservation locks the objective row, so concurrent controllers cannot exceed the limit;
  * budgets reserve with a conditional UPDATE guarded by CHECK (consumed + reserved <= limit);
  * executions are unique per (objective, task, proposal) — the idempotent reservation of a step."""
from __future__ import annotations

import json
from typing import Any, Iterable

from backend.contracts import canonical_hash, new_id
from backend.database.session import Database

from .contracts import OutcomeContract, TaskContract
from .states import ACTIVE_STATES, InvalidTaskTransition, check_transition


def _j(v: Any) -> str:
    return json.dumps(v, default=str)


class StaleState(RuntimeError):
    pass


class ControlStore:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ objectives
    def insert_objective(self, c: OutcomeContract, *, approved_by: str | None = None, amendment_reason: str | None = None,
                         authorised_by: str | None = None, max_active: int = 4) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO control.objectives (objective_id, version, original_instructions, "
                      "original_source_sha256, contract, contract_hash, approval_status, approved_by, amendment_reason, "
                      "authorised_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (c.objective_id, c.contract_version, c.original_instructions, c.original_source_sha256,
                       _j(c.model_dump(mode="json")), c.hash(), c.approval_status, approved_by, amendment_reason,
                       authorised_by))
            x.execute("INSERT INTO control.objective_runtime (objective_id, current_version, status, max_active) "
                      "VALUES (%s,%s,'open',%s) ON CONFLICT (objective_id) DO UPDATE SET current_version = "
                      "EXCLUDED.current_version, updated_at = now()", (c.objective_id, c.contract_version, max_active))

    def objective(self, objective_id: str, version: int | None = None) -> tuple[OutcomeContract, dict] | None:
        with self.db.tx() as x:
            if version is None:
                row = x.execute("SELECT * FROM control.objectives WHERE objective_id = %s ORDER BY version DESC LIMIT 1",
                                (objective_id,)).fetchone()
            else:
                row = x.execute("SELECT * FROM control.objectives WHERE objective_id = %s AND version = %s",
                                (objective_id, version)).fetchone()
        if row is None:
            return None
        c = OutcomeContract.model_validate(row["contract"])
        if c.hash() != row["contract_hash"]:
            raise StaleState(f"objective {objective_id} v{row['version']} fails its integrity hash")
        return c, row

    def objective_runtime(self, objective_id: str) -> dict:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM control.objective_runtime WHERE objective_id = %s", (objective_id,)).fetchone()

    def set_objective_status(self, objective_id: str, status: str, batch: int | None = None) -> None:
        with self.db.tx() as x:
            x.execute("UPDATE control.objective_runtime SET status = %s, batch = COALESCE(%s, batch), updated_at = now() "
                      "WHERE objective_id = %s", (status, batch, objective_id))

    # ------------------------------------------------------------------ task contracts + state
    def insert_task(self, c: TaskContract, reason: str, controller_id: str) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO control.task_contracts (task_id, objective_id, version, contract, contract_hash, reason) "
                      "VALUES (%s,%s,%s,%s,%s,%s)", (c.task_id, c.parent_objective_id, c.contract_version,
                                                     _j(c.model_dump(mode="json")), c.contract_hash, reason))
            if c.contract_version == 1:
                x.execute("INSERT INTO control.tasks (objective_id, task_id, current_version, state, repair_of) "
                          "VALUES (%s,%s,1,'CREATED',%s)", (c.parent_objective_id, c.task_id, c.parent_task_id))
                x.execute("INSERT INTO control.task_transitions (objective_id, task_id, from_state, to_state, reason, "
                          "controller_id) VALUES (%s,%s,NULL,'CREATED',%s,%s)",
                          (c.parent_objective_id, c.task_id, reason, controller_id))
            else:
                x.execute("UPDATE control.tasks SET current_version = %s, row_version = row_version + 1, updated_at = now() "
                          "WHERE objective_id = %s AND task_id = %s", (c.contract_version, c.parent_objective_id, c.task_id))

    def contract(self, objective_id: str, task_id: str, version: int | None = None) -> TaskContract:
        with self.db.tx() as x:
            if version is None:
                version = x.execute("SELECT current_version FROM control.tasks WHERE objective_id = %s AND task_id = %s",
                                    (objective_id, task_id)).fetchone()["current_version"]
            row = x.execute("SELECT contract, contract_hash FROM control.task_contracts WHERE objective_id = %s AND "
                            "task_id = %s AND version = %s", (objective_id, task_id, version)).fetchone()
        if row is None:
            raise KeyError(f"{task_id} v{version} not found")
        c = TaskContract.model_validate(row["contract"])
        if c.compute_hash() != row["contract_hash"] or c.contract_hash != row["contract_hash"]:
            raise StaleState(f"{task_id} v{version} fails its integrity hash")
        return c

    def contract_versions(self, objective_id: str, task_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT version, contract_hash, reason, created_at FROM control.task_contracts WHERE "
                             "objective_id = %s AND task_id = %s ORDER BY version", (objective_id, task_id)).fetchall()

    def task(self, objective_id: str, task_id: str) -> dict:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM control.tasks WHERE objective_id = %s AND task_id = %s",
                             (objective_id, task_id)).fetchone()

    def tasks(self, objective_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM control.tasks WHERE objective_id = %s ORDER BY task_id", (objective_id,)).fetchall()

    def transition(self, objective_id: str, task_id: str, target: str, reason: str, controller_id: str, *,
                   expect: Iterable[str] | None = None, attempt_id: str | None = None, new_attempt: bool = False,
                   evidence: dict | None = None, conn=None) -> dict:
        def run(x):
            row = x.execute("SELECT * FROM control.tasks WHERE objective_id = %s AND task_id = %s FOR UPDATE",
                            (objective_id, task_id)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if expect is not None and row["state"] not in set(expect):
                raise StaleState(f"{task_id} is {row['state']}, expected one of {sorted(expect)}")
            check_transition(row["state"], target)
            if row["state"] == "CANCELLED" and not new_attempt:
                raise InvalidTaskTransition("a cancelled task resumes only through a new authorised attempt")
            if target == "COMPLETED":
                self._assert_verified(x, objective_id, task_id, row["attempt_id"])
            att, no = row["attempt_id"], row["attempt_no"]
            if new_attempt:
                att, no = new_id("run"), no + 1
            x.execute("UPDATE control.tasks SET state = %s, attempt_id = %s, attempt_no = %s, row_version = row_version + 1, "
                      "updated_at = now() WHERE objective_id = %s AND task_id = %s", (target, att, no, objective_id, task_id))
            x.execute("INSERT INTO control.task_transitions (objective_id, task_id, from_state, to_state, reason, "
                      "controller_id, attempt_id, evidence) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                      (objective_id, task_id, row["state"], target, reason, controller_id, att, _j(evidence or {})))
            return {**row, "state": target, "attempt_id": att, "attempt_no": no, "previous_state": row["state"]}
        if conn is not None:
            return run(conn)
        with self.db.tx() as x:
            return run(x)

    def _assert_verified(self, x, objective_id: str, task_id: str, attempt_id: str | None) -> None:
        c = self.contract(objective_id, task_id)
        rows = x.execute("SELECT DISTINCT ON (criterion_id) criterion_id, passed FROM control.verifications WHERE "
                         "objective_id = %s AND task_id = %s AND attempt_id IS NOT DISTINCT FROM %s ORDER BY criterion_id, id DESC",
                         (objective_id, task_id, attempt_id)).fetchall()
        results = {r["criterion_id"]: r["passed"] for r in rows}
        missing = [k.id for k in c.acceptance_criteria if results.get(k.id) is not True]
        if missing:
            raise InvalidTaskTransition(f"{task_id} cannot complete: criteria not independently verified: {missing}")

    def transitions(self, objective_id: str, task_id: str | None = None) -> list[dict]:
        with self.db.tx() as x:
            if task_id:
                return x.execute("SELECT * FROM control.task_transitions WHERE objective_id = %s AND task_id = %s ORDER BY id",
                                 (objective_id, task_id)).fetchall()
            return x.execute("SELECT * FROM control.task_transitions WHERE objective_id = %s ORDER BY id",
                             (objective_id,)).fetchall()

    # ------------------------------------------------------------------ Focus-4 slots
    def reserve_slots(self, objective_id: str, candidates: list[str], controller_id: str) -> list[str]:
        """Atomically move up to (max_active - active) READY candidates into active slots (QUEUED)."""
        granted = []
        with self.db.tx() as x:
            rt = x.execute("SELECT max_active, status FROM control.objective_runtime WHERE objective_id = %s FOR UPDATE",
                           (objective_id,)).fetchone()
            if rt is None or rt["status"] != "open":
                return []
            active = x.execute("SELECT count(*) AS n FROM control.active_slots WHERE objective_id = %s",
                               (objective_id,)).fetchone()["n"]
            for task_id in candidates:
                if active >= rt["max_active"]:
                    break
                row = x.execute("SELECT state FROM control.tasks WHERE objective_id = %s AND task_id = %s FOR UPDATE",
                                (objective_id, task_id)).fetchone()
                if row is None or row["state"] != "READY":
                    continue
                x.execute("INSERT INTO control.active_slots (objective_id, task_id, controller_id) VALUES (%s,%s,%s)",
                          (objective_id, task_id, controller_id))
                self.transition(objective_id, task_id, "QUEUED", "released by Focus-4 controller", controller_id,
                                expect=["READY"], new_attempt=True, conn=x)
                active += 1
                granted.append(task_id)
        return granted

    def requeue_with_slot(self, objective_id: str, task_id: str, controller_id: str, reason: str) -> bool:
        """CANCELLED -> QUEUED for an authorised new attempt, only if a Focus-4 slot is free (same lock as release)."""
        with self.db.tx() as x:
            rt = x.execute("SELECT max_active FROM control.objective_runtime WHERE objective_id = %s FOR UPDATE",
                           (objective_id,)).fetchone()
            active = x.execute("SELECT count(*) AS n FROM control.active_slots WHERE objective_id = %s",
                               (objective_id,)).fetchone()["n"]
            if active >= rt["max_active"]:
                return False
            x.execute("INSERT INTO control.active_slots (objective_id, task_id, controller_id) VALUES (%s,%s,%s)",
                      (objective_id, task_id, controller_id))
            self.transition(objective_id, task_id, "QUEUED", reason, controller_id, expect=["CANCELLED"],
                            new_attempt=True, conn=x)
        return True

    def release_slot(self, objective_id: str, task_id: str) -> None:
        with self.db.tx() as x:
            x.execute("DELETE FROM control.active_slots WHERE objective_id = %s AND task_id = %s", (objective_id, task_id))

    def active_count(self, objective_id: str) -> int:
        with self.db.tx() as x:
            return x.execute("SELECT count(*) AS n FROM control.active_slots WHERE objective_id = %s",
                             (objective_id,)).fetchone()["n"]

    # ------------------------------------------------------------------ budgets
    def init_budgets(self, objective_id: str, task_id: str, limits: dict[str, float]) -> None:
        with self.db.tx() as x:
            for metric, limit in limits.items():
                x.execute("INSERT INTO control.budgets (objective_id, task_id, metric, limit_value) VALUES (%s,%s,%s,%s) "
                          "ON CONFLICT DO NOTHING", (objective_id, task_id, metric, limit))

    def reserve(self, objective_id: str, task_id: str, metric: str, amount: float, conn=None) -> bool:
        sql = ("UPDATE control.budgets SET reserved = reserved + %s WHERE objective_id = %s AND task_id = %s AND "
               "metric = %s AND consumed + reserved + %s <= limit_value RETURNING metric")
        args = (amount, objective_id, task_id, metric, amount)
        if conn is not None:
            return conn.execute(sql, args).fetchone() is not None
        with self.db.tx() as x:
            return x.execute(sql, args).fetchone() is not None

    def settle(self, objective_id: str, task_id: str, metric: str, reserved: float, consumed: float) -> None:
        """Release a reservation and record actual consumption (capped at the limit; overflow is recorded as exhausted)."""
        with self.db.tx() as x:
            x.execute("UPDATE control.budgets SET reserved = GREATEST(reserved - %s, 0), consumed = LEAST(limit_value - "
                      "GREATEST(reserved - %s, 0), consumed + %s) WHERE objective_id = %s AND task_id = %s AND metric = %s",
                      (reserved, reserved, consumed, objective_id, task_id, metric))

    def consume(self, objective_id: str, task_id: str, metric: str, amount: float) -> bool:
        with self.db.tx() as x:
            return x.execute("UPDATE control.budgets SET consumed = consumed + %s WHERE objective_id = %s AND task_id = %s "
                             "AND metric = %s AND consumed + reserved + %s <= limit_value RETURNING metric",
                             (amount, objective_id, task_id, metric, amount)).fetchone() is not None

    def budgets(self, objective_id: str, task_id: str) -> dict[str, dict]:
        with self.db.tx() as x:
            rows = x.execute("SELECT metric, limit_value, consumed, reserved FROM control.budgets WHERE objective_id = %s "
                             "AND task_id = %s", (objective_id, task_id)).fetchall()
        return {r["metric"]: {"limit": float(r["limit_value"]), "consumed": float(r["consumed"]),
                              "reserved": float(r["reserved"]),
                              "remaining": float(r["limit_value"] - r["consumed"] - r["reserved"])} for r in rows}

    def step_failure(self, objective_id: str, task_id: str, step_key: str) -> int:
        with self.db.tx() as x:
            return x.execute("INSERT INTO control.step_failures (objective_id, task_id, step_key, failures) VALUES "
                             "(%s,%s,%s,1) ON CONFLICT (objective_id, task_id, step_key) DO UPDATE SET failures = "
                             "control.step_failures.failures + 1 RETURNING failures",
                             (objective_id, task_id, step_key)).fetchone()["failures"]

    def step_failures(self, objective_id: str, task_id: str, step_key: str) -> int:
        with self.db.tx() as x:
            r = x.execute("SELECT failures FROM control.step_failures WHERE objective_id = %s AND task_id = %s AND "
                          "step_key = %s", (objective_id, task_id, step_key)).fetchone()
        return r["failures"] if r else 0

    # ------------------------------------------------------------------ resource locks
    @staticmethod
    def lock_key(objective_id: str, resource: str) -> str:
        """Workspace resources are scoped to their objective; only 'global:' resources are shared across objectives."""
        return resource if resource.startswith("global:") else f"{objective_id}/{resource}"

    def lock_resources(self, objective_id: str, task_id: str, resources: list[str],
                       family: set[str] | None = None) -> list[str]:
        """Return resources held by OTHER tasks (empty = all locks held by this task). `family` = this task and the
        task it repairs: a repair works on its parent's locked resources, nobody else does."""
        family = (family or set()) | {task_id}
        conflicts = []
        with self.db.tx() as x:
            for r in sorted({self.lock_key(objective_id, r) for r in resources}):
                x.execute("INSERT INTO control.resource_locks (resource_id, objective_id, task_id) VALUES (%s,%s,%s) "
                          "ON CONFLICT DO NOTHING", (r, objective_id, task_id))
                holder = x.execute("SELECT objective_id, task_id FROM control.resource_locks WHERE resource_id = %s",
                                   (r,)).fetchone()
                if holder["objective_id"] != objective_id or holder["task_id"] not in family:
                    conflicts.append(f"{r.split('/', 1)[-1]} (held by {holder['task_id']})")
        return conflicts

    def unlock_resources(self, objective_id: str, task_id: str, resources: list[str] | None = None) -> None:
        with self.db.tx() as x:
            if resources is None:
                x.execute("DELETE FROM control.resource_locks WHERE objective_id = %s AND task_id = %s",
                          (objective_id, task_id))
            else:
                x.execute("DELETE FROM control.resource_locks WHERE objective_id = %s AND task_id = %s AND "
                          "resource_id = ANY(%s)", (objective_id, task_id,
                                                    [self.lock_key(objective_id, r) for r in resources]))

    # ------------------------------------------------------------------ proposals / executions / drift
    def record_proposal(self, objective_id: str, task_id: str, attempt_id: str | None, proposal_id: str, body: Any,
                        accepted: bool, errors: list, source: str) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO control.proposals (proposal_id, objective_id, task_id, attempt_id, body, body_hash, "
                      "accepted, errors, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                      (proposal_id, objective_id, task_id, attempt_id, _j(body), canonical_hash(body), accepted,
                       _j(errors), source))

    def record_drift(self, objective_id: str, task_id: str, proposal_id: str | None, rule: str, detail: str) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO control.drift_violations (objective_id, task_id, proposal_id, rule, detail) "
                      "VALUES (%s,%s,%s,%s,%s)", (objective_id, task_id, proposal_id, rule, detail))

    def drift(self, objective_id: str, task_id: str | None = None) -> list[dict]:
        with self.db.tx() as x:
            q = "SELECT * FROM control.drift_violations WHERE objective_id = %s" + (" AND task_id = %s" if task_id else "")
            return x.execute(q + " ORDER BY id", (objective_id, task_id) if task_id else (objective_id,)).fetchall()

    def reserve_execution(self, row: dict) -> tuple[dict, bool]:
        """Insert the execution reservation; on a duplicate (same task + proposal) return the existing row."""
        with self.db.tx() as x:
            ins = x.execute(
                "INSERT INTO control.executions (execution_id, objective_id, task_id, attempt_id, proposal_id, tool_number, "
                "tool_string, operation, request_hash, arguments, idempotency, state, policy, lease_expires_at) VALUES "
                "(%(execution_id)s,%(objective_id)s,%(task_id)s,%(attempt_id)s,%(proposal_id)s,%(tool_number)s,"
                "%(tool_string)s,%(operation)s,%(request_hash)s,%(arguments)s,%(idempotency)s,'reserved',%(policy)s,"
                "now() + make_interval(secs => %(lease_s)s)) ON CONFLICT (objective_id, task_id, proposal_id) DO NOTHING "
                "RETURNING *", {**row, "arguments": _j(row["arguments"]), "policy": _j(row["policy"])}).fetchone()
            if ins is not None:
                return ins, True
            return x.execute("SELECT * FROM control.executions WHERE objective_id = %s AND task_id = %s AND proposal_id = %s",
                             (row["objective_id"], row["task_id"], row["proposal_id"])).fetchone(), False

    def claim_execution(self, execution_id: str) -> bool:
        with self.db.tx() as x:
            return x.execute("UPDATE control.executions SET state = 'running', started_at = now() WHERE execution_id = %s "
                             "AND state = 'reserved' RETURNING execution_id", (execution_id,)).fetchone() is not None

    def finish_execution(self, execution_id: str, state: str, result: Any = None, error: Any = None,
                         output_hash: str | None = None) -> None:
        with self.db.tx() as x:
            x.execute("UPDATE control.executions SET state = %s, result = %s, error = %s, output_hash = %s, "
                      "finished_at = now() WHERE execution_id = %s AND state IN ('reserved', 'running')",
                      (state, _j(result) if result is not None else None, _j(error) if error is not None else None,
                       output_hash, execution_id))

    def executions(self, objective_id: str, task_id: str | None = None) -> list[dict]:
        with self.db.tx() as x:
            q = "SELECT * FROM control.executions WHERE objective_id = %s" + (" AND task_id = %s" if task_id else "")
            return x.execute(q + " ORDER BY started_at NULLS LAST, execution_id",
                             (objective_id, task_id) if task_id else (objective_id,)).fetchall()

    # ------------------------------------------------------------------ evidence / verification / checkpoints
    def add_evidence(self, objective_id: str, task_id: str | None, kind: str, content: dict) -> str:
        eid = new_id("evd")
        with self.db.tx() as x:
            x.execute("INSERT INTO control.evidence (evidence_id, objective_id, task_id, kind, sha256, content) VALUES "
                      "(%s,%s,%s,%s,%s,%s)", (eid, objective_id, task_id, kind, canonical_hash(content), _j(content)))
        return eid

    def evidence(self, evidence_id: str) -> dict | None:
        with self.db.tx() as x:
            row = x.execute("SELECT * FROM control.evidence WHERE evidence_id = %s", (evidence_id,)).fetchone()
        if row and canonical_hash(row["content"]) != row["sha256"]:
            raise StaleState(f"evidence {evidence_id} fails its hash")
        return row

    def evidence_for(self, objective_id: str, task_id: str | None = None) -> list[dict]:
        with self.db.tx() as x:
            q = "SELECT * FROM control.evidence WHERE objective_id = %s" + (" AND task_id = %s" if task_id else "")
            return x.execute(q + " ORDER BY at", (objective_id, task_id) if task_id else (objective_id,)).fetchall()

    def add_verification(self, objective_id: str, task_id: str, attempt_id: str | None, criterion_id: str, passed: bool,
                         detail: dict, evidence_id: str | None, verifier: str) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO control.verifications (objective_id, task_id, attempt_id, criterion_id, passed, detail, "
                      "evidence_id, verifier) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                      (objective_id, task_id, attempt_id, criterion_id, passed, _j(detail), evidence_id, verifier))

    def verifications(self, objective_id: str, task_id: str | None = None) -> list[dict]:
        with self.db.tx() as x:
            q = "SELECT * FROM control.verifications WHERE objective_id = %s" + (" AND task_id = %s" if task_id else "")
            return x.execute(q + " ORDER BY id", (objective_id, task_id) if task_id else (objective_id,)).fetchall()

    def add_checkpoint(self, objective_id: str, verified: bool, data: dict) -> dict:
        with self.db.tx() as x:
            seq = x.execute("SELECT COALESCE(max(sequence), 0) + 1 AS n FROM control.checkpoints WHERE objective_id = %s",
                            (objective_id,)).fetchone()["n"]
            cid = new_id("chk")
            x.execute("INSERT INTO control.checkpoints (checkpoint_id, objective_id, sequence, verified, data, data_hash) "
                      "VALUES (%s,%s,%s,%s,%s,%s)", (cid, objective_id, seq, verified, _j(data), canonical_hash(data)))
        return {"checkpoint_id": cid, "sequence": seq, "verified": verified, "data_hash": canonical_hash(data)}

    def latest_checkpoint(self, objective_id: str, verified_only: bool = True) -> dict | None:
        with self.db.tx() as x:
            row = x.execute("SELECT * FROM control.checkpoints WHERE objective_id = %s" +
                            (" AND verified" if verified_only else "") + " ORDER BY sequence DESC LIMIT 1",
                            (objective_id,)).fetchone()
        if row and canonical_hash(row["data"]) != row["data_hash"]:
            raise StaleState(f"checkpoint {row['checkpoint_id']} fails its hash")
        return row

    def add_recovery(self, objective_id: str, action: str, detail: dict) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO control.recovery_records (objective_id, action, detail) VALUES (%s,%s,%s)",
                      (objective_id, action, _j(detail)))

    def counts(self) -> dict:
        with self.db.tx() as x:
            rows = x.execute("SELECT state, count(*) AS n FROM control.tasks GROUP BY state").fetchall()
        by = {r["state"]: r["n"] for r in rows}
        return {"active": sum(v for k, v in by.items() if k in ACTIVE_STATES), "blocked": by.get("BLOCKED", 0), "by_state": by}
