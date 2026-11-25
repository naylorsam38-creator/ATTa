"""Stage 01 acceptance tests S01-T01 … S01-T16 (PROTECTED: sealed by the build controller).

Each test exercises the real implementation. Tests needing infrastructure use a local PostgreSQL 16 + pgvector
(configured through configuration/local.env) and the platform's own managed Redis.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import psutil
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from s01_helpers import (PY, ROOT, Platform, free_port, get, platform_env, port_open, record, sm, sm_json,  # noqa: E402
                         state, wait_until, write_capability)

from backend.capability_registry import CapabilityRegistry  # noqa: E402
from backend.contracts import (ApiError, AvailabilityState, ErrorCode, Principal, PrincipalKind,  # noqa: E402
                               RequestContext)
from backend.policy_engine import Decision, PolicyEngine, PolicyRequest, SelfGrantError  # noqa: E402
from backend.settings import ConfigurationError, load_settings  # noqa: E402

BASE_COMMIT = "cef732913714fdcc5e58ae35ce4c484032289ec0"


class AllowAllForTest(PolicyEngine):
    """Test double used ONLY to prove that invalid capabilities stay non-executable even if policy allowed."""

    def evaluate(self, req):  # noqa: D401
        return self._decide(Decision.ALLOW, "test double", req)


def ctx() -> RequestContext:
    return RequestContext(request_id="req_test", correlation_id="cor_acceptance_test", principal=Principal.anonymous())


# --------------------------------------------------------------------------------------------- S01-T01
REQUIRED_DIRS = """apps/web apps/desktop apps/mobile backend/api backend/authentication backend/orchestration
backend/model_gateway backend/policy_engine backend/task_manager backend/capability_registry backend/verification
capabilities workers packages/shared_types packages/api_client packages/design_system packages/capability_sdk
database/migrations database/policies infrastructure/docker infrastructure/terraform configuration scripts
tests/unit tests/integration tests/end_to_end tests/security tests/regression tests/acceptance documentation
evidence""".split()
REQUIRED_FILES = """INSTALL.bat START.bat STOP.bat HEALTHCHECK.bat RECOVERY.bat install.sh start.sh stop.sh healthcheck.sh
recovery.sh MASTER_IMPLEMENTATION_REGISTER.md IMPLEMENTATION_PROGRESS.md DECISIONS.md BLOCKERS.md CHANGE_MANIFEST.md
STAGE_01_VERIFICATION.md pyproject.toml uv.lock package.json package-lock.json apps/mobile/package-lock.json
configuration/base.yaml configuration/local.env.example configuration/locks/backend-core.lock.txt""".split()


def test_S01_T01_repository_structure():
    tracked = set(subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout.splitlines())
    missing_dirs = [d for d in REQUIRED_DIRS if not any(f.startswith(d + "/") for f in tracked)]
    missing_files = [f for f in REQUIRED_FILES if f not in tracked]
    duplicates = [d for d in ("backend/capabilities", "backend/registry", "backend/policy", "src")
                  if (ROOT / d).exists()]
    record("S01-T01", {"required_dirs": REQUIRED_DIRS, "missing_dirs": missing_dirs, "missing_files": missing_files})
    assert not missing_dirs, f"directories missing or empty (untracked): {missing_dirs}"
    assert not missing_files, f"required files missing: {missing_files}"
    assert not duplicates, f"duplicate implementation directories present: {duplicates}"
    for legacy in ("04-deployment/gateway.py", "02-front-door/front-door.html", "run", "docs/ADM.md"):
        assert (ROOT / legacy).is_file(), f"existing ATTa file removed: {legacy}"


# --------------------------------------------------------------------------------------------- S01-T02
def test_S01_T02_clean_environment_install(tmp_path):
    venv = tmp_path / "venv"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VIRTUAL_ENV", "ATTA__"))}
    env["ATTA_VENV"] = str(venv)
    t0 = time.time()
    cp = subprocess.run(["bash", str(ROOT / "install.sh"), "--no-node"], cwd=ROOT, env=env, capture_output=True,
                        text=True, timeout=900)
    assert cp.returncode == 0, f"install.sh failed:\n{cp.stdout[-4000:]}\n{cp.stderr[-4000:]}"
    assert "INSTALL COMPLETE" in cp.stdout
    vpy = venv / "bin" / "python"
    probe = subprocess.run([str(vpy), "-I", "-c", textwrap.dedent("""\
        import json, importlib.metadata as md
        import backend.api.app, backend.service_manager.manager, backend.capability_registry, backend.verification.build_controller
        print(json.dumps({p: md.version(p) for p in ("fastapi", "pydantic", "uvicorn", "psycopg", "redis", "jsonschema")}))
        """)], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert probe.returncode == 0, probe.stderr
    versions = json.loads(probe.stdout)
    lock = (ROOT / "configuration/locks/backend-core.lock.txt").read_text()
    for pkg, ver in versions.items():
        assert re.search(rf"^{pkg}=={re.escape(ver)}\b", lock, re.M | re.I), f"{pkg} {ver} not the locked version"

    # Node workspaces from the lockfile into a clean copy.
    js = tmp_path / "js"
    for rel in ["package.json", "package-lock.json", ".npmrc"] + [
            f"{w}/package.json" for w in json.loads((ROOT / "package.json").read_text())["workspaces"]]:
        (js / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, js / rel)
    npm = shutil.which("npm")
    assert npm, "npm not found"
    cp2 = subprocess.run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"], cwd=js,
                         env={**env, "ELECTRON_SKIP_BINARY_DOWNLOAD": "1"}, capture_output=True, text=True, timeout=900)
    assert cp2.returncode == 0, cp2.stderr[-4000:]
    next_ver = json.loads((js / "node_modules/next/package.json").read_text())["version"]
    lock_js = json.loads((ROOT / "package-lock.json").read_text())
    assert next_ver == lock_js["packages"]["node_modules/next"]["version"]
    record("S01-T02", {"python_versions": versions, "next": next_ver, "duration_s": round(time.time() - t0, 1),
                       "install_stdout_tail": cp.stdout[-2000:]})


# --------------------------------------------------------------------------------------------- S01-T03/T04
def test_S01_T03_start_service_manager():
    with Platform("t03") as p:
        assert p.started, f"start failed:\n{p.start_result.stdout}\n{p.start_result.stderr}"
        st = state(p.env)
        assert st["ready"] is True and st["state"] in ("ready", "degraded")
        assert psutil.pid_exists(st["manager_pid"])
        for name in ("postgres", "redis", "api"):
            assert st["services"][name]["status"] == "healthy", st["services"][name]
        for name in ("redis", "api"):
            assert psutil.pid_exists(st["services"][name]["pid"])
        assert st["startup_order"].index("redis") < st["startup_order"].index("api")
        assert st["startup_order"].index("postgres") < st["startup_order"].index("api")
        caps = {c["id"]: c for c in st["capabilities"]}
        assert caps["ops.system_status"]["state"] == "available"
        code, status = sm_json("status", p.env)
        assert code == 0 and status["running"] is True
        record("S01-T03", {"start_stdout": p.start_result.stdout, "state": st})


def test_S01_T04_backend_liveness():
    with Platform("t04") as p:
        assert p.started, p.start_result.stdout
        r = get(p.api_port, "/v1/health/live")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "healthy" and body["service"] == "atta-api"
        assert re.match(r"^cor_[0-9A-Z]{26}$", r.headers["X-Correlation-ID"])
        r2 = get(p.api_port, "/v1/health/live")
        assert r2.headers["X-Correlation-ID"] != r.headers["X-Correlation-ID"]
        api_pid = state(p.env)["services"]["api"]["pid"]
        listeners = {c.laddr.port for c in psutil.Process(api_pid).net_connections(kind="tcp")
                     if c.status == psutil.CONN_LISTEN}
        assert p.api_port in listeners, "liveness must be served by the managed API process"
        record("S01-T04", {"response": body, "headers": dict(r.headers), "api_pid": api_pid})
    assert not port_open(p.api_port), "API still answering after stop"


# --------------------------------------------------------------------------------------------- S01-T05
def test_S01_T05_readiness_reflects_dependency_health():
    evidence = {}
    with Platform("t05") as p:
        assert p.started, p.start_result.stdout
        r = get(p.api_port, "/v1/health/ready")
        rep = r.json()
        deps = {d["name"]: d for d in rep["dependencies"]}
        assert r.status_code == 200 and rep["ready"] is True
        assert deps["postgres"]["status"] == "healthy" and "pgvector" in deps["postgres"]["detail"]
        assert deps["redis"]["status"] == "healthy" and str(p.redis_port) in deps["redis"]["detail"]
        if deps["object_storage"]["status"] != "healthy":  # optional dependency down => degraded, still ready
            assert rep["status"] == "degraded"
        evidence["healthy"] = rep

    # Same API code, Redis pointed at a port nothing listens on: readiness must go false with the reason.
    env = platform_env("t05b", ATTA__REDIS__PORT=str(free_port()))
    port = int(env["ATTA__API__PORT"])
    proc = subprocess.Popen([PY, "-m", "uvicorn", "backend.api.app:create_app", "--factory", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        wait_until(lambda: port_open(port), 30, what="standalone API")
        r = get(port, "/v1/health/ready")
        rep = r.json()
        deps = {d["name"]: d for d in rep["dependencies"]}
        assert r.status_code == 503 and rep["ready"] is False
        assert deps["redis"]["status"] == "unhealthy"
        assert deps["postgres"]["status"] == "healthy"
        assert any("redis" in reason for reason in rep["reasons"])
        assert get(port, "/v1/health/live").status_code == 200, "liveness is independent of dependencies"
        evidence["redis_down"] = rep
    finally:
        proc.terminate()
        proc.wait(10)
    record("S01-T05", evidence)


# --------------------------------------------------------------------------------------------- S01-T06/T07/T08
def test_S01_T06_discover_valid_capability_manifest(tmp_path):
    write_capability(tmp_path, "echo", "test.echo")
    reg = CapabilityRegistry([ROOT / "capabilities", tmp_path])
    report = {a.id: a for a in reg.discover()}
    assert report["test.echo"].state is AvailabilityState.AVAILABLE and report["test.echo"].executable
    assert report["ops.system_status"].state is AvailabilityState.AVAILABLE
    m = reg.get("test.echo").manifest
    assert set(m.handlers) == {"execute", "healthcheck", "verify"} and all(callable(h) for h in m.handlers.values())
    assert reg.get("test.echo").health_detail == {"healthy": True}
    assert "test.echo" in reg.executable_ids()
    record("S01-T06", {"availability": [a.model_dump(mode="json") for a in report.values()]})


INVALID_CASES = {
    "missing_fields": ("test.missing", None, "manifest_version: 1\nid: test.missing\nversion: 1.0.0\n"),
    "bad_id": ("BadId", None, None),
    "bad_version": ("test.badver", None, None),
    "missing_module": ("test.nomodule", None, None),
    "wrong_arity": ("test.arity", "def execute(payload):\n    return {}\ndef healthcheck():\n    return True\n"
                                  "def verify(p, o):\n    return True\n", None),
    "import_error": ("test.importerr", "raise RuntimeError('boom at import')\n", None),
    "bad_input_schema": ("test.badschema", None, None),
    "excessive_timeout": ("test.timeout", None, None),
    "not_callable": ("test.notcallable", "execute = 42\ndef healthcheck():\n    return True\n"
                                         "def verify(p, o):\n    return True\n", None),
}


def test_S01_T07_reject_invalid_capability_manifest(tmp_path):
    for name, (cid, handler, raw) in INVALID_CASES.items():
        d = write_capability(tmp_path, name, cid, handler=handler, raw_manifest=raw,
                             timeout_s=99999 if name == "excessive_timeout" else 10)
        text = (d / "capability.yaml").read_text()
        if name == "bad_version":
            text = text.replace("version: 1.0.0", "version: one")
        if name == "missing_module":
            text = text.replace("handler:execute", "nowhere:execute")
        if name == "bad_input_schema":
            text = text.replace("input_schema: {type: object", "input_schema: {type: notatype")
        (d / "capability.yaml").write_text(text)
    (tmp_path / "unhealthy").mkdir()
    write_capability(tmp_path, "unhealthy", "test.unhealthy", handler="def execute(p, c):\n    return {'echo': ''}\n"
                     "def healthcheck():\n    return {'healthy': False, 'why': 'disk'}\ndef verify(p, o):\n    return True\n")
    write_capability(tmp_path, "good", "test.good")
    reg = CapabilityRegistry([tmp_path], policy=AllowAllForTest(), max_timeout_s=900)
    report = reg.discover()
    by_src = {Path(a.source).parent.name: a for a in report}
    for name in INVALID_CASES:
        a = by_src[name]
        assert a.state is AvailabilityState.INVALID, (name, a)
        assert not a.executable and a.problems, (name, a)
    assert by_src["unhealthy"].state is AvailabilityState.UNHEALTHY and not by_src["unhealthy"].executable
    assert by_src["good"].executable
    assert reg.executable_ids() == ["test.good"]
    # Even with a policy that allows everything, invalid/unhealthy capabilities cannot execute.
    for cid in ["test.arity", "test.importerr", "test.unhealthy", "test.notcallable"]:
        with pytest.raises(ApiError) as e:
            reg.execute(cid, {"text": "x"}, ctx())
        assert e.value.code in (ErrorCode.NOT_FOUND, ErrorCode.CAPABILITY_UNAVAILABLE)
    assert reg.execute("test.good", {"text": "hi"}, ctx()).output == {"echo": "hi"}
    record("S01-T07", {"report": [a.model_dump(mode="json") for a in report]})


def test_S01_T08_reject_duplicate_capability_identifiers(tmp_path):
    a, b = tmp_path / "dirA", tmp_path / "dirB"
    write_capability(a, "echo_one", "test.dup")
    write_capability(b, "echo_two", "test.dup")
    write_capability(a, "other", "test.unique")
    reg = CapabilityRegistry([a, b], policy=AllowAllForTest())
    report = reg.discover()
    dups = [x for x in report if x.id == "test.dup"]
    assert len(dups) == 2 and all(x.state is AvailabilityState.REJECTED_DUPLICATE for x in dups)
    assert all(not x.executable and "duplicate" in x.problems[0] for x in dups)
    assert reg.get("test.dup") is None and reg.executable_ids() == ["test.unique"]
    with pytest.raises(ApiError) as e:
        reg.execute("test.dup", {"text": "x"}, ctx())
    assert e.value.code is ErrorCode.NOT_FOUND
    record("S01-T08", {"report": [x.model_dump(mode="json") for x in report]})


# --------------------------------------------------------------------------------------------- S01-T09
def test_S01_T09_new_capability_registers_without_changing_start(tmp_path):
    start_files = ["START.bat", "start.sh"]
    before = {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in start_files}
    capdir = tmp_path / "installed"
    cp = subprocess.run([PY, str(ROOT / "scripts/new_capability.py"), "text.word_count", "--requirement", "CAP-40",
                         "--dir", str(capdir)], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    d = capdir / "text_word_count"
    (d / "handler.py").write_text(textwrap.dedent('''\
        def execute(payload, context):
            return {"result": str(len(payload.get("text", "").split()))}

        def healthcheck():
            return {"healthy": True}

        def verify(payload, output):
            return {"verified": output["result"].isdigit()}
        '''))
    m = (d / "capability.yaml").read_text().replace("enabled: false", "enabled: true").replace(
        "properties: {}}", "properties: {text: {type: string}}}")
    (d / "capability.yaml").write_text(m)
    env = platform_env("t09", ATTA__CAPABILITIES__DIRECTORIES=json.dumps(["capabilities", str(capdir)]))
    start = subprocess.run(["bash", str(ROOT / "start.sh"), "--json"], cwd=ROOT, env=env, capture_output=True,
                           text=True, timeout=180)
    try:
        assert start.returncode == 0, start.stdout + start.stderr
        caps = {c["id"]: c for c in json.loads(start.stdout)["capabilities"]}
        assert caps["text.word_count"]["state"] == "available" and caps["text.word_count"]["executable"]
        assert caps["ops.system_status"]["state"] == "available"
    finally:
        sm("stop", env)
    after = {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in start_files}
    assert before == after, "START scripts changed"
    diff = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *start_files], cwd=ROOT)
    assert diff.returncode == 0, "START scripts differ from the committed version"
    record("S01-T09", {"start_hashes": after, "registered": caps["text.word_count"]})


# --------------------------------------------------------------------------------------------- S01-T10
def test_S01_T10_detect_unavailable_dependency(tmp_path):
    from backend.service_manager import ServiceManager, StartupFailed
    closed = free_port()
    s = load_settings("test", env={**platform_env("t10"), "ATTA__REDIS__PORT": str(closed),
                                   "ATTA__SERVICES__REDIS__REQUIRES_BINARY": "atta-binary-that-does-not-exist"})
    mgr = ServiceManager(s)
    problems = mgr.validate_dependencies()
    assert any("atta-binary-that-does-not-exist" in p for p in problems)
    with pytest.raises(StartupFailed):
        mgr.start()
    detected = mgr.detect_services()
    assert detected["redis"].status.value == "unhealthy" and str(closed) in detected["redis"].detail
    assert detected["postgres"].status.value == "healthy"
    assert all(h.status.value != "healthy" for h in mgr.model_health), "unreachable model endpoints must be reported"

    # Capability dependencies: missing python module, unavailable service, and a capability depending on those.
    write_capability(tmp_path, "needs_mod", "test.needs_mod",
                     deps="{capabilities: [], services: [], python: [atta_module_that_does_not_exist]}")
    write_capability(tmp_path, "needs_svc", "test.needs_svc", deps="{capabilities: [], services: [redis], python: []}")
    write_capability(tmp_path, "chained", "test.chained", deps="{capabilities: [test.needs_mod], services: [], python: []}")
    write_capability(tmp_path, "missing_cap", "test.missing_cap", deps="{capabilities: [test.absent], services: [], python: []}")
    reg = CapabilityRegistry([tmp_path], service_available=lambda n: False)
    by = {a.id: a for a in reg.discover()}
    for cid in ("test.needs_mod", "test.needs_svc", "test.chained", "test.missing_cap"):
        assert by[cid].state is AvailabilityState.DEPENDENCY_MISSING and not by[cid].executable, by[cid]
    assert "atta_module_that_does_not_exist" in by["test.needs_mod"].problems[0]
    assert "redis" in by["test.needs_svc"].problems[0]
    assert "test.absent" in by["test.missing_cap"].problems[0]
    record("S01-T10", {"validate_problems": problems, "detected": {k: v.model_dump(mode="json") for k, v in detected.items()},
                       "capabilities": [a.model_dump(mode="json") for a in by.values()]})


# --------------------------------------------------------------------------------------------- S01-T11
def test_S01_T11_no_false_readiness_after_essential_failure():
    evidence = {}
    # (a) essential external dependency down at startup -> start fails, never ready, nothing left running.
    env = platform_env("t11a", ATTA__DATABASE__PORT=str(free_port()), ATTA__SERVICES__POSTGRES__STARTUP_TIMEOUT_S="2")
    code, st = sm_json("start", env)
    assert code != 0 and st["ready"] is False and st["state"] == "failed"
    assert any("postgres" in r for r in st["reasons"])
    assert not port_open(int(env["ATTA__API__PORT"])) and not port_open(int(env["ATTA__REDIS__PORT"]))
    evidence["startup_with_postgres_down"] = st
    sm("stop", env)

    # (b) essential managed service crashes at runtime: one bounded restart, then permanent failure.
    with Platform("t11b", ATTA__SERVICES__REDIS__MAX_RESTARTS="1", ATTA__SERVICES__REDIS__RESTART_BACKOFF_S="0.2") as p:
        assert p.started, p.start_result.stdout
        pid1 = state(p.env)["services"]["redis"]["pid"]
        psutil.Process(pid1).kill()
        wait_until(lambda: (s := state(p.env))["services"]["redis"]["restarts"] == 1
                   and s["services"]["redis"]["status"] == "healthy" and s["ready"], 30, what="redis restart")
        pid2 = state(p.env)["services"]["redis"]["pid"]
        assert pid2 != pid1
        evidence["after_first_crash"] = state(p.env)
        psutil.Process(pid2).kill()
        st = wait_until(lambda: (s := state(p.env))["state"] == "failed" and s, 30, what="failed state")
        assert st["ready"] is False and st["services"]["redis"]["status"] == "failed"
        assert "restart budget exhausted" in st["services"]["redis"]["detail"]
        r = get(p.api_port, "/v1/health/ready")
        assert r.status_code == 503 and r.json()["ready"] is False
        assert sm("status", p.env).returncode != 0
        assert sm("healthcheck", p.env).returncode != 0
        time.sleep(2)
        assert state(p.env)["services"]["redis"]["restarts"] == 1, "restarts must stay bounded"
        evidence["after_second_crash"] = st
        evidence["api_ready_response"] = r.json()
    record("S01-T11", evidence)


# --------------------------------------------------------------------------------------------- S01-T12
def _bc(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-m", "backend.verification", "--root", str(root), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=300, env={**os.environ, "PYTHONPATH": str(ROOT)})


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def _controller_repo(tmp: Path, a_exit: int) -> Path:
    root = tmp / "repo"
    (root / "configuration/build/stages").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "configuration/build/roadmap.yaml").write_text(textwrap.dedent("""\
        checkpoint_every: 4
        protected_criteria: ["configuration/build/**"]
        stages: [{id: S01, title: one}, {id: S02, title: two}]
        """))
    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (root / "README").write_text("x\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    base = _git(root, "rev-parse", "HEAD").strip()
    stage = textwrap.dedent(f"""\
        stage: S01
        title: controller test
        base_commit: {base}
        protected_paths: ["README"]
        tasks:
          - id: TASK-A
            objective: a
            depends_on: []
            permitted_paths: ["src/**", "configuration/build/**"]
            required_changes: [x]
            rollback: git revert
            acceptance_tests:
              - {{id: T-A, command: ["{{python}}", "-c", "import sys; sys.exit({a_exit})"], expected_exit_code: 0}}
          - id: TASK-B
            objective: b
            depends_on: [TASK-A]
            permitted_paths: ["src/**"]
            required_changes: [x]
            rollback: git revert
            acceptance_tests:
              - {{id: T-B, command: ["{{python}}", "-c", "open('ran_b','w').write('1')"], expected_exit_code: 0}}
        """)
    (root / "configuration/build/stages/S01.yaml").write_text(stage)
    (root / "configuration/build/stages/S02.yaml").write_text(stage.replace("stage: S01", "stage: S02"))
    (root / "src/code.py").write_text("print('hello')\n")
    return root


def test_S01_T12_build_controller_rejects_progression_after_failed_test(tmp_path):
    ev = {}
    root = _controller_repo(tmp_path / "fail", a_exit=1)
    assert _bc(root, "seal", "--operator", "acceptance").returncode == 0
    r = _bc(root, "verify-stage", "S01")
    ev["failing_stage"] = r.stdout
    assert r.returncode == 1 and "STAGE S01 GATE: FAILED" in r.stdout
    assert re.search(r"TASK-A\s+FAILED", r.stdout) and re.search(r"TASK-B\s+BLOCKED", r.stdout)
    assert not (root / "ran_b").exists(), "a task with a failed dependency must not run"
    ledger = [json.loads(x) for x in (root / "evidence/build_controller/ledger.jsonl").read_text().splitlines()]
    test_rec = next(x for x in ledger if x["type"] == "test_result" and x["test"] == "T-A")
    assert test_rec["exit_code"] == 1 and test_rec["passed"] is False
    assert (root / test_rec["evidence_file"]).is_file()
    assert not (root / "evidence/build_controller/checkpoints.json").exists()
    # No way to progress: dependent task cannot begin, next stage refuses, no "approve" command exists.
    b = _bc(root, "begin", "S01", "TASK-B")
    assert b.returncode == 2 and "dependency TASK-A is FAILED" in b.stderr
    s2 = _bc(root, "verify-stage", "S02")
    assert s2.returncode == 2 and "previous stage S01 has not passed its gate" in s2.stderr
    assert _bc(root, "approve", "S01", "TASK-A").returncode != 0
    # Tampering with protected acceptance criteria is detected and blocks the controller.
    stage_file = root / "configuration/build/stages/S01.yaml"
    stage_file.write_text(stage_file.read_text().replace("sys.exit(1)", "sys.exit(0)"))
    t = _bc(root, "verify-stage", "S01")
    assert t.returncode == 2 and "protected file modified: configuration/build/stages/S01.yaml" in t.stderr
    ev["tamper_refusal"] = t.stderr
    # Editing the ledger to fake a pass is detected.
    root2 = _controller_repo(tmp_path / "ledger", a_exit=1)
    _bc(root2, "seal")
    _bc(root2, "verify-stage", "S01")
    lf = root2 / "evidence/build_controller/ledger.jsonl"
    lf.write_text(lf.read_text().replace('"status": "FAILED"', '"status": "PASSED"'))
    i = _bc(root2, "integrity")
    assert i.returncode == 1 and "hash mismatch" in i.stdout
    # Unauthorised file change fails the gate even when every test passes.
    root3 = _controller_repo(tmp_path / "files", a_exit=0)
    _bc(root3, "seal")
    (root3 / "README").write_text("changed\n")
    (root3 / "rogue.txt").write_text("x\n")
    u = _bc(root3, "verify-stage", "S01")
    assert u.returncode == 1 and "protected path changed (M): README" in u.stdout
    assert "unauthorised change (A?): rogue.txt" in u.stdout
    # Positive control: a clean, passing stage passes its gate (the controller is not trivially failing).
    root4 = _controller_repo(tmp_path / "pass", a_exit=0)
    _bc(root4, "seal")
    ok = _bc(root4, "verify-stage", "S01")
    assert ok.returncode == 0 and "STAGE S01 GATE: PASSED" in ok.stdout
    ev.update({"ledger_tamper": i.stdout, "unauthorised": u.stdout, "passing_control": ok.stdout})
    record("S01-T12", ev)


# --------------------------------------------------------------------------------------------- S01-T13
def test_S01_T13_protected_operations_default_to_deny(tmp_path):
    from fastapi.testclient import TestClient
    from backend.api.app import create_app
    from backend.contracts import DependencyHealth, HealthStatus
    marker = tmp_path / "executed.txt"
    write_capability(tmp_path, "sidefx", "test.sidefx", handler=textwrap.dedent(f"""\
        def execute(payload, context):
            open({str(marker)!r}, "w").write("ran")
            return {{"echo": "x"}}
        def healthcheck():
            return True
        def verify(p, o):
            return True
        """))
    engine = PolicyEngine()
    reg = CapabilityRegistry([ROOT / "capabilities", tmp_path], policy=engine)
    reg.discover()
    assert reg.get("test.sidefx").executable
    settings = load_settings("test", env=platform_env("t13"))
    app = create_app(settings, policy=engine, registry=reg, dependency_probe=lambda: [
        DependencyHealth(name="postgres", essential=True, status=HealthStatus.HEALTHY)])
    results = {}
    with TestClient(app) as c:
        public = {p: c.get(p).status_code for p in ("/v1/health/live", "/v1/health/ready", "/v1/system/version")}
        assert public == {"/v1/health/live": 200, "/v1/health/ready": 200, "/v1/system/version": 200}
        for hdrs in ({}, {"Authorization": "Bearer forged-admin-token"}, {"X-Atta-Permissions": "*"}):
            for method, path, body in (("GET", "/v1/capabilities", None),
                                       ("POST", "/v1/capabilities/test.sidefx/executions", {"text": "x"}),
                                       ("POST", "/v1/capabilities/ops.system_status/executions", {})):
                r = c.request(method, path, json=body, headers=hdrs)
                assert r.status_code == 403, (method, path, hdrs, r.text)
                assert r.json()["error"]["code"] == "permission_denied"
                results[f"{method} {path} {list(hdrs)}"] = r.status_code
    assert not marker.exists(), "a denied capability must never run"
    # Direct registry execution is policy-gated as well.
    with pytest.raises(ApiError) as e:
        reg.execute("test.sidefx", {"text": "x"}, ctx())
    assert e.value.code is ErrorCode.PERMISSION_DENIED and not marker.exists()
    # Every non-public operation, including unknown ones and any principal kind, is denied.
    for kind in PrincipalKind:
        p = Principal(id=f"{kind.value}-1", kind=kind, permissions=frozenset({"*"}), authenticated=True)
        for op in ("capability.execute", "capability.list", "admin.anything", "unknown.op", "policy.grant"):
            assert engine.evaluate(PolicyRequest(principal=p, operation=op)).decision is Decision.DENY
    # No agent, capability or worker can grant permissions — to itself or anyone; at Stage 01 nobody can.
    for kind in (PrincipalKind.AGENT, PrincipalKind.CAPABILITY, PrincipalKind.WORKER, PrincipalKind.USER):
        me = Principal(id="p1", kind=kind)
        with pytest.raises(SelfGrantError):
            engine.grant(me, me, "capability.execute")
    with pytest.raises(SelfGrantError):
        engine.grant(Principal(id="admin", kind=PrincipalKind.USER), Principal(id="u2", kind=PrincipalKind.USER), "x")
    with pytest.raises(Exception):
        Principal.anonymous().permissions.add("x")  # frozenset: cannot self-escalate in place
    # Unfinished endpoints cannot be exposed publicly by configuration.
    with pytest.raises(ConfigurationError):
        load_settings("test", env={**platform_env("t13b"), "ATTA__API__HOST": "0.0.0.0"})
    audit = [d.decision.value for d in engine.audit_log()]
    assert "deny" in audit
    record("S01-T13", {"public": public, "protected": results, "audit_tail": [d.__dict__ for d in engine.audit_log()[-5:]]})


# --------------------------------------------------------------------------------------------- S01-T14
def test_S01_T14_stop_and_restart_cleanly():
    env = platform_env("t14")
    api, redis = int(env["ATTA__API__PORT"]), int(env["ATTA__REDIS__PORT"])
    cycles = []
    for cycle in range(2):
        code, st = sm_json("start", env)
        assert code == 0 and st["ready"], st
        pids = [st["manager_pid"], st["services"]["redis"]["pid"], st["services"]["api"]["pid"]]
        assert get(api, "/v1/health/live").status_code == 200
        stop = sm("stop", env)
        assert stop.returncode == 0 and "stopped cleanly" in stop.stdout, stop.stdout + stop.stderr
        wait_until(lambda: not any(psutil.pid_exists(p) and psutil.Process(p).status() != psutil.STATUS_ZOMBIE
                                   for p in pids), 15, what="processes to exit")
        assert not port_open(api) and not port_open(redis)
        final = state(env)
        assert final["state"] == "stopped" and final["ready"] is False
        assert all(s["pid"] is None for s in final["services"].values())
        cycles.append({"start_state": st["state"], "pids": pids, "stop": stop.stdout.strip()})
    again = sm("stop", env)
    assert again.returncode == 0 and "not running" in again.stdout
    record("S01-T14", {"cycles": cycles})


# --------------------------------------------------------------------------------------------- S01-T15
MANDATED = ["AI conversation", "Model selection", "Extended thinking", "Chat management", "Memory", "Personalisation",
            "Projects", "Knowledge base", "File uploads", "Vision", "Web search", "Deep research", "Artifacts",
            "Document creation", "Spreadsheet creation", "Presentation creation", "Design workspace",
            "Application creation", "Code execution", "Coding agent", "Connectors", "MCP", "Skills", "Plugins",
            "Cowork", "Parallel agents", "Browser control", "Computer control", "Local file access", "Scheduling",
            "Notifications", "Voice", "Collaboration", "Cross-device synchronisation", "Account management",
            "Subscription management", "Administration", "Data management", "Security and approvals",
            "Operational management"]


def test_S01_T15_requirement_register_complete():
    cp = subprocess.run([PY, str(ROOT / "scripts/requirement_register.py"), "check"], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    data = yaml.safe_load((ROOT / "documentation/requirements/requirement_register.yaml").read_text())
    reqs = data["requirements"]
    assert [r["id"] for r in reqs] == [f"CAP-{i:02d}" for i in range(1, 41)]
    assert [r["title"] for r in reqs] == MANDATED
    fields = ["implementation_stage", "dependencies", "source_files", "interface_controls", "api_endpoints",
              "security_controls", "acceptance_tests", "status", "evidence"]
    for r in reqs:
        assert all(f in r for f in fields), r["id"]
        assert r["status"] not in ("IMPLEMENTED", "VERIFIED"), f"{r['id']} cannot be complete at Stage 01"
    master = (ROOT / "MASTER_IMPLEMENTATION_REGISTER.md").read_text()
    for r in reqs:
        assert f"### {r['id']} — {r['title']}" in master
    record("S01-T15", {"statuses": {r["id"]: r["status"] for r in reqs}})


# --------------------------------------------------------------------------------------------- S01-T16
LEGACY_PATHS = ["01-specs", "02-front-door", "03-ui-skins-capability-package", "04-deployment", "05-coolify", "docs",
                "tests/fixtures", "run", "Dockerfile", "docker-compose.coolify.yml", "release.json", "START-HERE.txt",
                ".dockerignore"]


def _legacy_tests() -> list[str]:
    return sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob("test_*.py"))


def test_S01_T16_existing_functionality_not_removed_or_downgraded(tmp_path):
    changed = subprocess.run(["git", "diff", "--name-status", BASE_COMMIT, "--", *LEGACY_PATHS, *_legacy_tests()],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    assert changed == "", f"existing ATTa files changed:\n{changed}"
    for f in ("README.md", ".gitignore"):  # shared files may only gain lines
        diff = subprocess.run(["git", "diff", "--unified=0", BASE_COMMIT, "--", f], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout
        removed = [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
        assert not removed, f"{f}: lines removed: {removed}"
    baseline = json.loads((ROOT / "tests/regression/legacy_baseline.json").read_text())
    env = dict(os.environ)
    if "ATTA_CHROMIUM" not in env:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            env["ATTA_CHROMIUM"] = p.chromium.executable_path
    junit = tmp_path / "legacy.xml"
    cp = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}", *_legacy_tests()],
                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=1500)
    now = {}
    for tc in ET.parse(junit).iter("testcase"):
        st = "passed"
        for ch in tc:
            st = {"failure": "failed", "error": "failed", "skipped": "skipped"}.get(ch.tag, st)
        now[f"{tc.get('classname')}::{tc.get('name')}"] = st
    regressions = [t for t, s in baseline["tests"].items() if s == "passed" and now.get(t) != "passed"]
    counts = {k: sum(1 for v in now.values() if v == k) for k in ("passed", "failed", "skipped")}
    record("S01-T16", {"baseline_counts": baseline["counts"], "current_counts": counts, "regressions": regressions,
                       "pytest_tail": cp.stdout[-1500:]})
    assert not regressions, f"previously passing tests no longer pass: {regressions}"
    assert counts["passed"] >= baseline["counts"]["passed"]
