"""Stage 11 final qualification: migration lifecycle on real databases, security denials, new budgets (evidence
storage, model requests), authority revocation, credential references, stored-data hygiene, the Stage 04
additive app.py patch, and offline handling of already retrieved evidence."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from pydantic import ValidationError

from backend.research.claims import ExplicitClaims
from backend.research.contracts import NetworkMode
from backend.research.errors import ResearchError
from conftest import requires_db
from harness import ROOT, build, record

BASELINE = "21104c413c30a3b2714f877892970e7db19a1d19"
PY = sys.executable
Q = "When did the Rivertown bridge reopen and what did the repairs cost?"
SUBS = ["Rivertown bridge reopen date", "Rivertown bridge repair cost"]


def sid_for(packet, suffix):
    return next(s["source_id"] for s in packet["sources"] if (s["canonical_url"] or "").endswith(suffix)
                and not s["duplicate_of"])


def one_claim(p):
    return [{"text": "The bridge reopened on Monday.", "support": [{"source_id": sid_for(p, "/page/a.html"),
                                                                     "quote": "The Rivertown bridge reopened on Monday"}]}]


# ------------------------------------------------------------------------------------------------ migration 0011
def _su(sql: str, db: str = "postgres") -> subprocess.CompletedProcess:
    return subprocess.run(["su", "postgres", "-c", f"psql -X -q -v ON_ERROR_STOP=1 -d {db} -c \"{sql}\""],
                          capture_output=True, text=True)


SUPERUSER = shutil.which("su") is not None and _su("SELECT 1").returncode == 0
needs_superuser = pytest.mark.skipif(not SUPERUSER, reason="needs local PostgreSQL superuser access to create "
                                                           "throw-away databases")


def _new_db() -> str:
    name = f"atta_s11q_{uuid.uuid4().hex[:10]}"
    assert _su(f"CREATE DATABASE {name} OWNER atta").returncode == 0
    assert _su("CREATE EXTENSION IF NOT EXISTS vector", name).returncode == 0
    _su(f"REVOKE CREATE ON SCHEMA public FROM PUBLIC; GRANT CONNECT ON DATABASE {name} TO atta_app", name)
    return name


def _migrate(db: str, root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "ATTA_ENV": "test", "ATTA__DATABASE__NAME": db}
    local = ROOT / "configuration" / "local.env"
    if root != ROOT and local.is_file():
        shutil.copyfile(local, root / "configuration" / "local.env")
    return subprocess.run([PY, "-m", "backend.database", "migrate"], cwd=root, capture_output=True, text=True, env=env)


def _query(db: str, sql: str) -> str:
    cp = subprocess.run(["su", "postgres", "-c", f"psql -X -At -d {db} -c \"{sql}\""], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    return cp.stdout.strip()


@pytest.fixture
def baseline_tree(tmp_path) -> Path:
    t = tmp_path / "baseline"
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(ROOT), str(t)], check=True)
    subprocess.run(["git", "-C", str(t), "checkout", "-q", BASELINE], check=True)
    return t


@needs_superuser
def test_migration_fresh_upgrade_repeat_and_failure_recovery(baseline_tree):
    dbs = []
    try:
        # fresh installation
        fresh = _new_db(); dbs.append(fresh)
        cp = _migrate(fresh, ROOT)
        assert cp.returncode == 0 and "0011_stage11_research.sql" in cp.stdout, cp.stdout + cp.stderr
        tables = _query(fresh, "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'research'")
        assert tables == "14"
        # repeated execution is a no-op with intact checksums
        cp = _migrate(fresh, ROOT)
        assert cp.returncode == 0 and "0011" not in cp.stdout, cp.stdout
        # upgrade from the Stage 01A baseline with existing data preserved
        up = _new_db(); dbs.append(up)
        cp = _migrate(up, baseline_tree)
        assert cp.returncode == 0 and "0011" not in cp.stdout, cp.stdout + cp.stderr
        _query(up, "INSERT INTO control.tool_identifiers (namespace, number, tool_string) VALUES "
                   "('upgrade-test', '040.001', 'ops.system_status:report')")
        before = _query(up, "SELECT md5(string_agg(table_schema || '.' || table_name, ',' ORDER BY 1)) FROM "
                            "information_schema.tables WHERE table_schema IN ('control', 'platform')")
        cp = _migrate(up, ROOT)
        assert cp.returncode == 0 and "0011_stage11_research.sql" in cp.stdout, cp.stdout + cp.stderr
        assert _query(up, "SELECT tool_string FROM control.tool_identifiers WHERE namespace = 'upgrade-test'") == \
            "ops.system_status:report"
        assert _query(up, "SELECT md5(string_agg(table_schema || '.' || table_name, ',' ORDER BY 1)) FROM "
                          "information_schema.tables WHERE table_schema IN ('control', 'platform')") == before
        # failure recovery: an unrelated existing `research` schema is never overwritten; the failed migration
        # leaves no partial state; after the conflict is removed the migration succeeds
        bad = _new_db(); dbs.append(bad)
        assert _migrate(bad, baseline_tree).returncode == 0
        _query(bad, "CREATE SCHEMA research AUTHORIZATION atta; CREATE TABLE research.foreign_data (x int)")
        cp = _migrate(bad, ROOT)
        assert cp.returncode != 0
        assert _query(bad, "SELECT count(*) FROM public.atta_schema_migrations WHERE name LIKE '%0011%'") == "0"
        assert _query(bad, "SELECT string_agg(table_name, ',') FROM information_schema.tables WHERE "
                           "table_schema = 'research'") == "foreign_data"
        _query(bad, "DROP SCHEMA research CASCADE")
        assert _migrate(bad, ROOT).returncode == 0
        record("S11-MIGRATION", {"fresh": fresh, "upgrade": up, "failure_recovery": bad, "tables": tables})
    finally:
        for d in dbs:
            _su(f"DROP DATABASE IF EXISTS {d} WITH (FORCE)")


# ------------------------------------------------------------------------------------------------ security
@requires_db
def test_handlers_cannot_access_credentials(tmp_path, fx, monkeypatch):
    monkeypatch.setenv("S11_CANARY_DB_PASSWORD", "s11-canary-db")
    monkeypatch.setenv("BRAVE_API_KEY", "s11-canary-key")
    probe = ROOT / "tests" / "research" / "fixtures" / "envprobe"
    h = build(tmp_path, port=fx.port, extra_capabilities={str(probe): ("040.950", "test.envprobe:probe")})
    c = h.service.create("bridge", "QUICK_SEARCH")
    h.approve(c.research_id)
    from backend.orchestration.controller import TaskSpec
    from backend.orchestration.contracts import ToolProposal
    oid = c.objective_id
    h.controller.plan(oid, [TaskSpec(task_id="TASK-0001", requirement_ids=["FR-01"], task_type="test.envprobe",
                                     description="probe env", required_outcome="probed", operations=["inspect"],
                                     acceptance_criteria=[{"id": "TC-01", "description": "ran", "requirement_ids":
                                                           ["FR-01"], "verifier": "execution_succeeded"}])])
    assert "TASK-0001" in h.controller.release(oid)
    t = h.controller.store.transition(oid, "TASK-0001", "RUNNING", "t", h.controller.controller_id, expect=["QUEUED"])
    con = h.controller.store.contract(oid, "TASK-0001")
    ctx = h.controller.gateway.issue_context(oid, "TASK-0001", t["attempt_id"], con.contract_hash, con.assigned_worker)
    r = h.controller.gateway.execute(ctx, ToolProposal(proposal_id="probe-1", task_id="TASK-0001", contract_version=1,
                                     tool_id="040.950", operation="inspect", arguments={}, expected_result="x",
                                     referenced_requirement_ids=["FR-01"]))
    assert r.status == "executed" and r.output["leaked"] == [], r.output
    assert set(r.output["keys"]) <= {"PATH", "LANG", "PYTHONDONTWRITEBYTECODE", "PWD", "LC_CTYPE", "HOME", "TMPDIR"}
    record("S11-CREDENTIAL-ISOLATION", r.output)


@requires_db
def test_cross_task_and_unassigned_capability_denied(h, fx):
    from backend.orchestration.controller import TaskSpec
    from backend.orchestration.contracts import ToolProposal
    c = h.service.create("bridge", "QUICK_SEARCH")
    h.approve(c.research_id)
    oid = c.objective_id
    crit = [{"id": "TC-01", "description": "ran", "requirement_ids": ["FR-01"], "verifier": "execution_succeeded"}]
    h.controller.plan(oid, [
        TaskSpec(task_id="TASK-0001", requirement_ids=["FR-01"], task_type="research.cite", description="cite",
                 required_outcome="cited", operations=["cite"], acceptance_criteria=crit,
                 permitted_filesystem_paths=["research"]),
        TaskSpec(task_id="TASK-0002", requirement_ids=["FR-01"], task_type="research.retrieve", description="get",
                 required_outcome="stored", operations=["fetch", "extract"], acceptance_criteria=crit,
                 permitted_filesystem_paths=["research"], permitted_network_destinations=["127.0.0.1"])])
    ctx = {}
    for tid in h.controller.release(oid):
        t = h.controller.store.transition(oid, tid, "RUNNING", "t", h.controller.controller_id, expect=["QUEUED"])
        con = h.controller.store.contract(oid, tid)
        ctx[tid] = h.controller.gateway.issue_context(oid, tid, t["attempt_id"], con.contract_hash, con.assigned_worker)

    def P(tid, tool, op, args):
        return ToolProposal(proposal_id=f"x-{uuid.uuid4().hex[:8]}", task_id=tid, contract_version=1, tool_id=tool,
                            operation=op, arguments=args, expected_result="x", referenced_requirement_ids=["FR-01"])
    # the cite task's context cannot act for the retrieve task, nor use the retrieve task's network tool
    r1 = h.controller.gateway.execute(ctx["TASK-0001"], P("TASK-0002", "011.002", "fetch", {"url": f"{fx.base}/page/a.html"}))
    r2 = h.controller.gateway.execute(ctx["TASK-0001"], P("TASK-0001", "011.002", "fetch", {"url": f"{fx.base}/page/a.html"}))
    forged = ctx["TASK-0002"].__class__(**{**ctx["TASK-0002"].__dict__, "task_id": "TASK-0001"})
    r3 = h.controller.gateway.execute(forged, P("TASK-0001", "011.002", "fetch", {"url": f"{fx.base}/page/a.html"}))
    assert r1.status == r2.status == r3.status == "rejected"
    assert "tool_not_in_pool" in [v.rule for v in r2.violations]
    assert [v.rule for v in r3.violations] == ["unauthenticated_context"]
    assert not any("/page/a.html" in p for p in fx.hits)


@requires_db
def test_verification_tampering_is_undone_or_refused(h, fx):
    c = h.service.create(Q, "STANDARD_RESEARCH", subquestions=SUBS)
    h.approve(c.research_id)
    h.service.run(c.research_id, claim_builder=ExplicitClaims(lambda p: one_claim(p) + [
        {"text": "An uncited material claim.", "support": []}]))
    rid = c.research_id
    bad = next(x for x in h.store.claims(rid) if x["support_status"] == "unsupported")
    with h.store.db.tx() as x:      # an attacker with runtime DB access marks the unsupported claim verified
        x.execute("UPDATE research.claims SET verification_status = 'VERIFIED' WHERE claim_id = %s", (bad["claim_id"],))
        x.execute("UPDATE research.citations SET verified = false WHERE research_id = %s", (rid,))
    from backend.research.verification import ResearchVerifier
    rep = ResearchVerifier(h.store, h.controller.gateway.workspace(c.objective_id)).verify(h.store.contract(rid))
    assert not rep["passed"]
    assert {x["claim_id"]: x["verification_status"] for x in h.store.claims(rid)}[bad["claim_id"]] == "VERIFICATION_FAILED"
    assert all(x["verified"] for x in h.store.citations(rid))          # recomputed from evidence, not trusted
    for sql in ("UPDATE research.results SET result = '{}'::jsonb WHERE research_id = %s",
                "DELETE FROM research.evidence WHERE research_id = %s",
                "UPDATE research.events SET kind = 'x' WHERE research_id = %s"):
        with pytest.raises(psycopg.Error):
            with h.store.db.tx() as x:
                x.execute(sql, (rid,))
    with pytest.raises(psycopg.Error):
        with h.store.db.tx() as x:
            x.execute("UPDATE control.verifications SET passed = true WHERE objective_id = %s", (c.objective_id,))


@requires_db
def test_authority_revocation_stops_research(h, fx):
    c = h.service.create("slow bridge sources and Rivertown bridge", "STANDARD_RESEARCH",
                         subquestions=["Rivertown bridge reopen date", "slow bridge source"])
    h.approve(c.research_id)
    out = {}
    th = threading.Thread(target=lambda: out.update(r=h.service.run(c.research_id)))
    th.start()
    deadline = time.time() + 60
    while time.time() < deadline and not any(p.startswith("/slow") for p in fx.hits):
        time.sleep(0.2)
    from backend.orchestration.authorization import AuthorizationError
    with pytest.raises(AuthorizationError):
        h.service.revoke(c.research_id, None)
    h.service.revoke(c.research_id, h.auth("revoke_research", c.research_id, h.store.contract(c.research_id).hash()))
    th.join(60)
    assert not th.is_alive() and out["r"]["research_status"] == "CANCELLED"
    assert any(e["kind"] == "authority.revoked" for e in h.store.events(c.research_id))
    n = len(h.store.events(c.research_id, ("task.planned",)))
    h.service.run(c.research_id)
    assert len(h.store.events(c.research_id, ("task.planned",))) == n     # never continues after revocation


@requires_db
def test_no_plaintext_credentials_in_research_records(h, fx):
    c = h.service.create("unsafe bridge results", "QUICK_SEARCH")
    h.approve(c.research_id)
    h.service.run(c.research_id)
    rid = c.research_id
    blobs = json.dumps([h.store.events(rid), h.store.sources(rid)], default=str)
    assert "hunter2" not in blobs and "[redacted]@example.org" in blobs
    assert not any("hunter2" in p for p in fx.hits)


# ------------------------------------------------------------------------------------------------ budgets
@requires_db
def test_evidence_storage_budget(h, fx):
    c = h.service.create(Q, "STANDARD_RESEARCH", subquestions=SUBS, budget={"max_evidence_kb": 3})
    h.approve(c.research_id)
    r = h.service.run(c.research_id, claim_builder=ExplicitClaims(one_claim))
    srcs = h.store.sources(c.research_id)
    assert any(s["error_code"] == "BUDGET_EXHAUSTED" for s in srcs) and any(s["snapshot_id"] for s in srcs)
    b = h.store.budgets(c.research_id)["evidence_bytes"]
    assert b["consumed"] <= b["limit"] == 3 * 1024
    assert any(l["code"] == "BUDGET_EXHAUSTED" and "evidence_bytes" in l["metrics"] for l in r["limitations"])


@requires_db
def test_model_request_budget(tmp_path, fx):
    from test_s11_integration_kits import FakeHub
    hub = FakeHub()
    h = build(tmp_path, port=fx.port, hub=hub)
    c = h.service.create(Q, "STANDARD_RESEARCH", budget={"max_model_requests": 1})
    h.approve(c.research_id)
    r = h.service.run(c.research_id)
    assert hub.calls == ["plan"]                       # no model call after the budget is spent
    assert h.store.budgets(c.research_id)["model_requests"]["consumed"] == 1
    assert r["research_status"] in ("BLOCKED", "COMPLETED_WITH_LIMITATIONS")
    assert h.store.queries(c.research_id) == []


# ------------------------------------------------------------------------------------------------ provider credentials
def test_provider_credentials_are_references_only():
    from backend.research.config import ProviderConfig
    with pytest.raises(ValidationError):
        ProviderConfig(id="brave", kind="brave", endpoint="https://api.search.brave.com/x")          # no reference
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate({"id": "brave", "kind": "brave", "endpoint": "https://x.example.org",
                                       "credential_ref": "cred://stage02/x", "api_key": "sk-123"})   # no key values
    with pytest.raises(ValidationError):
        ProviderConfig(id="brave", kind="brave", endpoint="https://x.example.org", credential_ref="sk-plaintext")
    ok = ProviderConfig(id="brave", kind="brave", endpoint="https://x.example.org", credential_ref="cred://stage02/x")
    assert ok.credential_ref.startswith("cred://")
    from backend.research.security import UnavailableCredentialResolver
    with pytest.raises(ResearchError):
        UnavailableCredentialResolver().resolve("cred://stage02/x", principal_id=None, tenant_id=None, purpose="search")


# ------------------------------------------------------------------------------------------------ Stage 04 app patch
def test_stage04_app_patch_applies_and_wires_the_api(tmp_path):
    patch = ROOT / "documentation" / "stage11" / "integration" / "stage04_app_wiring.patch"
    work = tmp_path / "w"
    (work / "backend" / "api").mkdir(parents=True)
    shutil.copyfile(ROOT / "backend" / "api" / "app.py", work / "backend" / "api" / "app.py")
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    cp = subprocess.run(["git", "-C", str(work), "apply", str(patch)], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    spec = importlib.util.spec_from_file_location("backend.api.app_s11_patched", work / "backend" / "api" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from backend.settings import load_settings
    from fastapi.testclient import TestClient
    app = mod.create_app(load_settings("test"), dependency_probe=lambda: [], registry=object())
    assert "/v1/research" in app.openapi()["paths"]
    with TestClient(app) as client:
        assert client.get("/v1/research").status_code == 403            # default deny preserved
        assert client.get("/v1/health/live").status_code == 200         # existing routes unchanged
    base = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASELINE}:backend/api/app.py"], capture_output=True,
                          check=True).stdout
    assert base == (ROOT / "backend" / "api" / "app.py").read_bytes()   # the sidecar never edited app.py


# ------------------------------------------------------------------------------------------------ offline evidence
@requires_db
def test_previously_retrieved_evidence_usable_offline(h, fx):
    c = h.service.create(Q, "STANDARD_RESEARCH", subquestions=SUBS)
    h.approve(c.research_id)
    r = h.service.run(c.research_id, claim_builder=ExplicitClaims(one_claim))
    assert r["verification_status"] == "VERIFIED"
    h.network.mode = NetworkMode.AIR_GAPPED
    hits = len(fx.hits)
    from backend.research.verification import ResearchVerifier
    rep = ResearchVerifier(h.store, h.controller.gateway.workspace(c.objective_id)).verify(h.store.contract(c.research_id))
    assert rep["passed"] and len(fx.hits) == hits                        # stored evidence re-verified offline
    assert h.service.result(c.research_id)["research_id"] == c.research_id
    assert h.service.progress(c.research_id)["counts"]["sources"] >= 1
    web = h.service.create(Q, "QUICK_SEARCH")                             # new external research: honest refusal
    h.approve(web.research_id)
    rr = h.service.run(web.research_id)
    assert rr["research_status"] == "BLOCKED" and rr["error"]["code"] == "OFFLINE_UNAVAILABLE"
    assert len(fx.hits) == hits


@requires_db
def test_provider_rate_limit(tmp_path, fx):
    h = build(tmp_path, port=fx.port, providers=[{"id": "fixture-searx", "kind": "searxng", "max_results": 10,
                                                   "endpoint": f"http://127.0.0.1:{fx.port}/searx",
                                                   "max_requests": 1, "rate_window_s": 3}])
    c = h.service.create(Q, "STANDARD_RESEARCH", subquestions=SUBS)
    h.approve(c.research_id)
    h.service.run(c.research_id, claim_builder=ExplicitClaims(one_claim))
    q = sorted(h.store.queries(c.research_id), key=lambda r: r["executed_at"])
    assert len(q) >= 2
    gap = (q[1]["executed_at"] - q[0]["executed_at"]).total_seconds()
    assert gap >= 2.0, gap                                       # second request waited for the window
    assert any(e["kind"] == "provider.rate_limited" for e in h.store.events(c.research_id))


@requires_db
def test_interactive_page_reported_as_browser_dependency(h, fx):
    c = h.service.create("javascript app bridge page", "QUICK_SEARCH")
    h.approve(c.research_id)
    r = h.service.run(c.research_id)
    lim = [l for l in r["limitations"] if "interactive-browser" in str(l.get("dependency"))]
    assert lim and lim[0]["code"] == "BLOCKED_DEPENDENCY" and lim[0]["source_ids"]


@requires_db
def test_research_time_limit_is_enforced(h, fx):
    """The research deadline bounds running tasks too: a source that never answers is cut off at the deadline."""
    c = h.service.create("slow bridge source", "STANDARD_RESEARCH", subquestions=["slow bridge source"],
                         budget={"time_limit_s": 4})
    h.approve(c.research_id)
    t0 = time.time()
    r = h.service.run(c.research_id, claim_builder=ExplicitClaims(lambda p: []))
    assert time.time() - t0 < 20, "the slow fetch (40 s) was not bounded by the 4 s research deadline"
    ev = [(e["kind"], e["detail"]) for e in h.store.events(c.research_id)]
    assert any(k == "budget.exhausted" and d.get("metric") == "time_limit_s" for k, d in ev), ev
    assert any(l["code"] == "BUDGET_EXHAUSTED" and "time_limit_s" in l["metrics"] for l in r["limitations"])
    slow = [s for s in h.store.sources(c.research_id) if (s["original_url"] or "").endswith("/slow")]
    assert slow and slow[0]["extraction_status"] == "not_retrieved"


@requires_db
def test_evidence_gap_triggers_one_bounded_reformulated_query(h, fx):
    c = h.service.create("bridge facts", "STANDARD_RESEARCH",
                         subquestions=["nothing about bridges here at all", "Rivertown bridge reopen date"])
    h.approve(c.research_id)
    h.service.run(c.research_id, claim_builder=ExplicitClaims(one_claim))
    gaps = [e["detail"] for e in h.store.events(c.research_id, ("gap.identified",))]
    assert [g["subquestion_index"] for g in gaps] == [0]
    q0 = [q for q in h.store.queries(c.research_id) if q["subquestion_index"] == 0]
    assert len(q0) == 2 and q0[1]["query_text"] != q0[0]["query_text"]          # one reformulation, no more
