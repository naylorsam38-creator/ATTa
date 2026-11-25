"""Requirement traceability (Stage 01A §20):
ORIGINAL REQUIREMENT -> OUTCOME CONTRACT -> TASK -> TOOL POOL -> PROPOSAL -> POLICY DECISION -> EXECUTION ->
RESULTING STATE -> INDEPENDENT VERIFICATION -> ACCEPTANCE EVIDENCE -> DELIVERABLE. A missing link = not verified."""
from __future__ import annotations

from .store import ControlStore


def trace(store: ControlStore, objective_id: str) -> dict:
    objective, _ = store.objective(objective_id)
    tasks = {t["task_id"]: t for t in store.tasks(objective_id)}
    contracts = {tid: store.contract(objective_id, tid) for tid in tasks}
    executions = store.executions(objective_id)
    verifs = store.verifications(objective_id)
    with store.db.tx() as x:
        proposals = x.execute("SELECT proposal_id, task_id, accepted FROM control.proposals WHERE objective_id = %s",
                              (objective_id,)).fetchall()
    chains, gaps = {}, []
    for req in objective.functional_requirements + objective.non_functional_requirements:
        links = []
        for tid, c in contracts.items():
            if req.id not in c.requirement_ids:
                continue
            t = tasks[tid]
            ex = [e for e in executions if e["task_id"] == tid and e["state"] == "succeeded"
                  and e["attempt_id"] == t["attempt_id"]]
            accepted = {p["proposal_id"] for p in proposals if p["task_id"] == tid and p["accepted"]}
            latest = {}
            for v in verifs:
                if v["task_id"] == tid and v["attempt_id"] == t["attempt_id"]:
                    latest[v["criterion_id"]] = v
            crit = [k for k in c.acceptance_criteria if req.id in k.requirement_ids]
            link = {
                "task_id": tid, "tool_pool": c.permitted_tool_ids, "contract_hash": c.contract_hash,
                "proposals": sorted(p for p in accepted if any(e["proposal_id"] == p for e in ex)),
                "policy_decisions": [e["policy"] for e in ex], "executions": [e["execution_id"] for e in ex],
                "resulting_state": t["state"],
                "verifications": [{"criterion_id": k.id, "passed": bool(latest.get(k.id, {}).get("passed")),
                                   "evidence_id": latest.get(k.id, {}).get("evidence_id")} for k in crit],
            }
            missing = []
            if not link["executions"]:
                missing.append("no successful execution")
            if not link["proposals"]:
                missing.append("no accepted proposal")
            if not crit:
                missing.append("no acceptance criterion for this requirement")
            if any(not v["passed"] or not v["evidence_id"] for v in link["verifications"]):
                missing.append("verification missing or failed")
            if t["state"] != "COMPLETED":
                missing.append(f"task is {t['state']}")
            link["missing"] = missing
            links.append(link)
        if not links:
            gaps.append(f"{req.id} has no assigned task")
        elif not any(not lk["missing"] for lk in links):
            gaps.append(f"{req.id} is not fully verified: " + "; ".join(m for lk in links for m in lk["missing"]))
        chains[req.id] = links
    for tid, c in contracts.items():
        unknown = set(c.requirement_ids) - objective.requirement_ids()
        if unknown:
            gaps.append(f"{tid} serves requirements not in the approved objective: {sorted(unknown)}")
    deliverables = {d.id: {"requirement_ids": d.requirement_ids,
                           "verified": all(r not in " ".join(gaps) for r in d.requirement_ids)}
                    for d in objective.required_deliverables}
    return {"objective_id": objective_id, "objective_version": objective.contract_version,
            "original_instructions_sha256": objective.original_source_sha256, "requirements": chains,
            "deliverables": deliverables, "gaps": gaps, "complete": not gaps}
