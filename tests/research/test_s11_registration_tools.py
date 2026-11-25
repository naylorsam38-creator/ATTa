"""Stage 11 registration through the EXISTING capability registry (§4) and the evidence tools run through the
Stage 01A execution gateway (§5, §17)."""
from __future__ import annotations

import uuid
from pathlib import Path

import yaml

from backend.capability_registry import CapabilityRegistry, TrustStore
from backend.capability_registry.numbering import load_numbers
from backend.capability_runtime import CapabilityRuntime
from backend.orchestration.contracts import ToolProposal
from backend.orchestration.controller import TaskSpec
from backend.policy_engine import PolicyEngine
from conftest import requires_db
from harness import ROOT, STAGE11_NUMBERS, record

PROD_NUMBERS = ROOT / "configuration" / "tool_numbers.yaml"


def test_production_registry_discovers_stage11_packages_but_does_not_execute_them():
    """Registered automatically, reported 'untrusted' until the operator signs them (fail closed)."""
    reg = CapabilityRegistry([ROOT / "capabilities"], policy=PolicyEngine(),
                             trust=TrustStore.load(ROOT / "configuration" / "capability_trust.json",
                                                   ROOT / "configuration" / "capability_trust_ed25519.pub"),
                             runtime=CapabilityRuntime(), service_available=lambda n: True)
    reg.discover()
    states = {a.id: a.state.value for a in reg.availability()}
    assert states["research.web"] == "untrusted" and states["research.evidence"] == "untrusted"
    assert states["ops.system_status"] == "available"          # existing capability unaffected
    assert "research.web" not in reg.executable_ids()


def test_proposed_numbers_reconcile_with_existing_numbering():
    current = load_numbers(PROD_NUMBERS)
    merged = {k: {"tool": v.tool} for k, v in current.items()} | {k: v for k, v in STAGE11_NUMBERS.items()}
    tmp = Path("/tmp") / f"s11-numbers-{uuid.uuid4().hex}.yaml"
    tmp.write_text(yaml.safe_dump({"tools": merged}))
    try:
        entries = load_numbers(tmp)           # raises on duplicate numbers or a tool with two numbers
    finally:
        tmp.unlink()
    assert set(current) <= set(entries) and len(entries) == len(current) + 10
    assert all(n.startswith("011.") for n, e in entries.items() if e.tool.startswith("research.web:"))
    assert all(n.startswith("012.") for n, e in entries.items() if e.tool.startswith("research.evidence:"))
    assert not set(current) & set(STAGE11_NUMBERS)


def P(tid, tool, op, args):
    return ToolProposal(proposal_id=f"t-{uuid.uuid4().hex[:8]}", task_id=tid, contract_version=1, tool_id=tool,
                        operation=op, arguments=args, target_resources=[], expected_result="x",
                        referenced_requirement_ids=["FR-01"])


@requires_db
def test_evidence_tools_through_the_gateway(h, fx):
    c = h.service.create("bridge", "QUICK_SEARCH")
    h.approve(c.research_id)
    oid = c.objective_id
    ctl = h.controller
    crit = [{"id": "TC-01", "description": "ran", "requirement_ids": ["FR-01"], "verifier": "execution_succeeded"}]
    ctl.plan(oid, [
        TaskSpec(task_id="TASK-0001", requirement_ids=["FR-01"], task_type="research.retrieve", description="get a",
                 required_outcome="stored", operations=["fetch", "extract", "find"], acceptance_criteria=crit,
                 permitted_filesystem_paths=["research"], permitted_network_destinations=["127.0.0.1"]),
        TaskSpec(task_id="TASK-0002", requirement_ids=["FR-01"], task_type="research.cite", description="cite",
                 required_outcome="cited", operations=["cite", "verify"], acceptance_criteria=crit,
                 permitted_filesystem_paths=["research"]),
        TaskSpec(task_id="TASK-0003", requirement_ids=["FR-01"], task_type="research.analyse", description="capture",
                 required_outcome="captured", operations=["capture", "inspect"], acceptance_criteria=crit,
                 permitted_filesystem_paths=["research"])])
    pools = {t: ctl.store.contract(oid, t).permitted_tool_ids for t in ("TASK-0001", "TASK-0002", "TASK-0003")}
    assert pools == {"TASK-0001": ["011.002", "011.003", "011.006"], "TASK-0002": ["012.003", "012.004"],
                     "TASK-0003": ["011.004", "012.001"]}
    ctx = {}
    for tid in ctl.release(oid):
        t = ctl.store.transition(oid, tid, "RUNNING", "test", ctl.controller_id, expect=["QUEUED"])
        con = ctl.store.contract(oid, tid)
        ctx[tid] = ctl.gateway.issue_context(oid, tid, t["attempt_id"], con.contract_hash, con.assigned_worker)
    g = ctl.gateway
    f = g.execute(ctx["TASK-0001"], P("TASK-0001", "011.002", "fetch", {"url": f"{fx.base}/page/c.html"}))
    e = g.execute(ctx["TASK-0001"], P("TASK-0001", "011.003", "extract", {"snapshot_path": f.output["snapshot_path"]}))
    path = e.output["extracted_path"]
    hit = g.execute(ctx["TASK-0001"], P("TASK-0001", "011.006", "find", {"extracted_path": path,
                                                                        "terms": ["15 million"]})).output["hits"][0]
    cap = g.execute(ctx["TASK-0003"], P("TASK-0003", "012.001", "capture", {"extracted_path": path,
                    "start": hit["start"], "end": hit["end"]}))
    assert cap.status == "executed" and "15 million" in cap.output["evidence"]["quote"]
    meta = g.execute(ctx["TASK-0003"], P("TASK-0003", "011.004", "inspect", {"extracted_path": path}))
    assert meta.output["metadata"]["author"] == "Lee Ortiz"
    draft = g.execute(ctx["TASK-0002"], P("TASK-0002", "012.003", "cite", {"extracted_path": path,
                      "start": hit["start"], "end": hit["end"]})).output["citation_draft"]
    assert "citation_id" not in draft and draft["locator"].startswith("chars ")
    ok = g.execute(ctx["TASK-0002"], P("TASK-0002", "012.004", "verify", {
        "extracted_path": path, "start": draft["start"], "end": draft["end"],
        "quote_sha256": draft["quote_sha256"], "text_sha256": draft["text_sha256"]}))
    assert ok.output["verified"] is True
    bad = g.execute(ctx["TASK-0002"], P("TASK-0002", "012.004", "verify", {
        "extracted_path": path, "start": draft["start"] + 1, "end": draft["end"],
        "quote_sha256": draft["quote_sha256"], "text_sha256": draft["text_sha256"]}))
    assert bad.output["verified"] is False
    # a cite task cannot fetch: the network tool is not in its pool
    r = g.execute(ctx["TASK-0002"], P("TASK-0002", "011.002", "fetch", {"url": f"{fx.base}/page/a.html"}))
    assert r.status == "rejected" and r.violations[0].rule == "tool_not_in_pool"
    # path escape attempts are refused by the drift guard
    r = g.execute(ctx["TASK-0003"], P("TASK-0003", "011.004", "inspect", {"extracted_path": "../../etc/passwd"}))
    assert r.status == "rejected" and r.violations[0].rule == "path_not_permitted"
    record("S11-EVIDENCE-TOOLS", {"pools": pools, "draft": draft})


@requires_db
def test_policy_denies_stage11_tools_without_operator_grant(tmp_path, fx):
    """Global installation is not authorisation: without the operator's permission grant, policy denies."""
    from harness import build
    h = build(tmp_path, port=fx.port, grant=False)
    states = {a.id: a.state.value for a in h.registry.availability()}
    assert states["research.web"] == "invalid"           # permissions not in the production catalog
    web = [t for t in h.catalogue.all() if t.spec.capability_id == "research.web"]
    assert web and not any(t.available for t in web)      # network tools need the operator grant
    c = h.service.create("Rivertown bridge", "QUICK_SEARCH")
    h.approve(c.research_id)
    r = h.service.run(c.research_id)
    assert r["research_status"] == "BLOCKED" and r["error"]["code"] == "BLOCKED_DEPENDENCY"
    assert not fx.hits                                    # nothing executed, nothing contacted
