"""Stage 12 — transactional plug-in installer: dry-run, conflicts, checksums, rollback, crash recovery, repeat
installation, upgrade, uninstall, migration numbering, CRLF (Windows) checkouts and preservation of other workers'
additions to shared files. Targets are real exports of the Stage 01A baseline."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from stage12_harness import ROOT, record

INST = ROOT / "installer" / "stage12"
BASE = "21104c413c30a3b2714f877892970e7db19a1d19"
PY = sys.executable
sys.path.insert(0, str(INST))
import install as installer  # noqa: E402


@pytest.fixture(scope="session")
def payload(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("payload") / "payload"
    subprocess.run([PY, str(INST / "build_payload.py"), "--out", str(out)], check=True, capture_output=True)
    return out


@pytest.fixture(scope="session")
def base_tree(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("base") / "atta"
    d.mkdir()
    a = subprocess.run(["git", "-C", str(ROOT), "archive", BASE], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(d)], input=a, check=True)
    return d


@pytest.fixture
def tree(base_tree, tmp_path) -> Path:
    d = tmp_path / "atta"
    shutil.copytree(base_tree, d, symlinks=True)
    return d


def run(target: Path, payload: Path, *args, env=None) -> tuple[int, dict]:
    p = subprocess.run([PY, str(INST / "install.py"), "--target", str(target), "--payload", str(payload), "--json",
                        "--no-verify-import", *args], capture_output=True, text=True, timeout=600,
                       env={**os.environ, **(env or {})})
    try:
        return p.returncode, json.loads(p.stdout)
    except json.JSONDecodeError:
        return p.returncode, {"raw": p.stdout + p.stderr}


def snapshot(d: Path) -> dict:
    out = {}
    for p in d.rglob("*"):
        rel = p.relative_to(d).as_posix()
        if p.is_file() and not rel.startswith(".atta-stage12") and "__pycache__" not in rel:
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_S12_T80_dry_run_changes_nothing_and_reports_the_plan(tree, payload):
    before = snapshot(tree)
    code, out = run(tree, payload, "--dry-run")
    assert code == 0 and out["status"] == "DRY_RUN_OK", out
    assert out["plan"]["hunk"] == sum(len(v) for v in json.loads((payload / "manifest.json").read_text())["edits"]
                                      .values())
    assert snapshot(tree) == before and not (tree / ".atta-stage12" / "installed.json").exists()
    record("S12-T80", {"plan": out["plan"], "migration": out["migration"]})


def test_S12_T81_install_repeat_uninstall_reinstall(tree, payload):
    before = snapshot(tree)
    code, out = run(tree, payload)
    assert code == 0 and out["status"] == "INSTALLED", out
    m = json.loads((payload / "manifest.json").read_text())
    for f in m["files"]:
        assert hashlib.sha256((tree / f["path"]).read_bytes()).hexdigest() == f["sha256"]
    after = snapshot(tree)
    for rel in ("backend/orchestration/gateway.py", "backend/orchestration/catalogue.py", "backend/api/app.py"):
        assert (tree / rel).read_bytes() == (ROOT / rel).read_bytes(), f"{rel} differs from the sidecar"
    code2, out2 = run(tree, payload)
    assert code2 == 0 and out2["status"] == "ALREADY_INSTALLED" and snapshot(tree) == after, "repeat = no-op"
    code3, out3 = run(tree, payload, "--uninstall")
    assert code3 == 0 and out3["status"] == "UNINSTALLED", out3
    now = snapshot(tree)
    diff = sorted(k for k in set(now) | set(before) if now.get(k) != before.get(k))
    assert diff == [], f"uninstall did not restore the baseline: {diff[:10]}"
    code4, out4 = run(tree, payload)
    assert code4 == 0 and out4["status"] == "INSTALLED" and snapshot(tree) == after, "reinstall reproduces install"
    record("S12-T81", {"install": out["plan"], "repeat": out2["status"], "uninstall": out3["status"],
                       "reinstall": out4["status"], "files": len(after) - len(before)})


def test_S12_T82_other_workers_additions_are_preserved(tree, payload):
    g = tree / "backend" / "orchestration" / "gateway.py"
    t = g.read_text()
    t = t.replace('"""The controlled execution gateway', '# WORKER-A: added header line\n"""The controlled execution gateway', 1)
    t = t.rstrip("\n") + "\n\n\ndef worker_a_helper():\n    return 'kept'\n"
    g.write_text(t)
    a = tree / "backend" / "api" / "app.py"
    a.write_text(a.read_text().replace("SERVICE_NAME = \"atta-api\"", "SERVICE_NAME = \"atta-api\"\nWORKER_2_FLAG = True"))
    d = tree / "DECISIONS.md"
    d.write_text(d.read_text() + "\n## Stage 04 decisions (Worker 2)\n\n| D-S04-01 | kept |\n")
    code, out = run(tree, payload)
    assert code == 0 and out["status"] == "INSTALLED", out
    gt = g.read_text()
    assert "WORKER-A: added header line" in gt and "def worker_a_helper" in gt and "self.guards" in gt
    assert "WORKER_2_FLAG = True" in a.read_text() and "mount_integrations" in a.read_text()
    dt = d.read_text()
    assert "D-S04-01 | kept" in dt and "ATTA-STAGE12:BEGIN DECISIONS.md" in dt
    code, out = run(tree, payload, "--uninstall")
    assert code == 0, out
    assert "def worker_a_helper" in g.read_text() and "self.guards" not in g.read_text()
    assert "D-S04-01 | kept" in d.read_text() and "ATTA-STAGE12" not in d.read_text()
    record("S12-T82", {"preserved": ["gateway.py header + helper", "app.py flag", "DECISIONS.md section"]})


def test_S12_T83_conflicting_changes_are_refused_without_any_change(tree, payload):
    g = tree / "backend" / "orchestration" / "gateway.py"
    g.write_text(g.read_text().replace("self._gateway_key = Attestor()", "self._gateway_key = Attestor()  # W-X edit"))
    (tree / "backend" / "integrations").mkdir(parents=True)
    (tree / "backend" / "integrations" / "types.py").write_text("# another worker's file\n")
    before = snapshot(tree)
    code, out = run(tree, payload)
    assert code == 3 and out["status"] == "CONFLICTS", out
    assert any("gateway.py: hunk" in c for c in out["conflicts"]), out["conflicts"]
    assert any("backend/integrations/types.py" in c and "not overwritten" in c for c in out["conflicts"])
    assert snapshot(tree) == before, "nothing may change when there are conflicts"
    record("S12-T83", {"conflicts": out["conflicts"]})


def test_S12_T84_migration_numbering_conflicts(tree, payload):
    mig = tree / "database" / "migrations" / "0012_other_worker.sql"
    mig.write_text("-- another worker\n")
    before = snapshot(tree)
    code, out = run(tree, payload)
    assert code == 3 and any("migration number 0012 already used" in c for c in out["conflicts"]), out
    assert snapshot(tree) == before
    code, out = run(tree, payload, "--migration-number", "0012")
    assert code == 3
    code, out = run(tree, payload, "--migration-number", "auto")
    assert code == 0 and out["migration"] == "database/migrations/0013_stage12_integrations.sql", out
    assert (tree / out["migration"]).is_file() and mig.read_text() == "-- another worker\n"
    assert not (tree / "database/migrations/0012_stage12_integrations.sql").exists()
    code, out = run(tree, payload, "--uninstall")
    assert code == 0 and not (tree / "database/migrations/0013_stage12_integrations.sql").exists()
    assert installer.compare_ledger(["migration:0012_stage12_integrations.sql"],
                                    "database/migrations/0012_stage12_integrations.sql") == []
    assert installer.compare_ledger(["migration:0013_other.sql"], "database/migrations/0013_stage12_integrations.sql")
    assert installer.compare_ledger(["migration:0012_stage12_integrations.sql"],
                                    "database/migrations/0013_stage12_integrations.sql"), \
        "already applied under another number: must keep that number"
    record("S12-T84", {"auto": "0013_stage12_integrations.sql"})


def test_S12_T85_checksum_failure_and_rollback_and_crash_recovery(tree, payload, tmp_path):
    bad = tmp_path / "bad-payload"
    shutil.copytree(payload, bad)
    f = bad / "files" / "backend" / "integrations" / "types.py"
    f.write_text(f.read_text() + "\n# tampered\n")
    before = snapshot(tree)
    code, out = run(tree, bad)
    assert code == 2 and "checksum" in out["error"], out
    assert snapshot(tree) == before
    for step in (1, 60, 140, 155):                    # failures during file creation, hunks, sections, derived
        code, out = run(tree, payload, env={"ATTA_STAGE12_INSTALLER_FAIL_AT": str(step)})
        assert code == 4 and out["status"] == "ROLLED_BACK", (step, out)
        assert snapshot(tree) == before, f"rollback incomplete at step {step}"
    code, out = run(tree, payload, env={"ATTA_STAGE12_INSTALLER_CRASH_AT": "100"})
    assert code == 9, "the installer process was killed mid-transaction"
    assert snapshot(tree) != before
    code, out = run(tree, payload, "--recover")
    assert code == 0 and any("recovered interrupted transaction" in r for r in out["recovered"]), out
    assert snapshot(tree) == before
    code, out = run(tree, payload, "--python", "/nonexistent/python", "--no-verify-import")
    assert code in (0, 4)
    if code == 0:
        run(tree, payload, "--uninstall")
    py_without_deps = shutil.which("python3") or PY
    p = subprocess.run([py_without_deps, "-c", "import fastapi"], capture_output=True)
    if p.returncode != 0:                              # post-install verification failure -> full rollback
        r = subprocess.run([PY, str(INST / "install.py"), "--target", str(tree), "--payload", str(payload), "--json",
                            "--python", py_without_deps], capture_output=True, text=True, timeout=600)
        o = json.loads(r.stdout)
        assert r.returncode == 4 and "import check failed" in o["error"], o
        assert snapshot(tree) == before
    record("S12-T85", {"checksum": "refused", "rollback_steps": [1, 60, 140, 155], "crash_recovered": True,
                       "verification_rollback": p.returncode != 0})


def _payload_v2(payload: Path, tmp: Path, version: str, file_change: str, hunk_tag: str) -> Path:
    d = tmp / f"payload-{version}"
    shutil.copytree(payload, d)
    m = json.loads((d / "manifest.json").read_text())
    m["version"] = version
    f = d / "files" / "backend" / "integrations" / "types.py"
    f.write_text(f.read_text() + f"\n# {file_change}\n")
    for e in m["files"]:
        if e["path"] == "backend/integrations/types.py":
            e["sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
    h = m["edits"]["backend/orchestration/gateway.py"][0]
    h["new"] = h["new"].replace("# Stage 12 extension points", f"# Stage 12 extension points ({hunk_tag})")
    mt = json.dumps(m, indent=1, sort_keys=True)
    (d / "manifest.json").write_text(mt)
    sums = [f"{hashlib.sha256(mt.encode()).hexdigest()}  manifest.json"] + \
        [f"{e['sha256']}  files/{e['path']}" for e in m["files"]]
    (d / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n")
    return d


def test_S12_T86_upgrade_replaces_only_untouched_installed_content(tree, payload, tmp_path):
    assert run(tree, payload)[0] == 0
    v2 = _payload_v2(payload, tmp_path, "12.2.0", "v2 change", "v2")
    code, out = run(tree, v2)
    assert code == 0 and out["status"] == "INSTALLED" and out["mode"].startswith("upgrade from"), out
    assert "# v2 change" in (tree / "backend/integrations/types.py").read_text()
    gt = (tree / "backend/orchestration/gateway.py").read_text()
    assert "extension points (v2)" in gt and gt.count("self.guards: list") == 1
    (tree / "backend/integrations/types.py").write_text("# locally modified after install\n")
    v3 = _payload_v2(v2, tmp_path, "12.3.0", "v3 change", "v3")
    before = snapshot(tree)
    code, out = run(tree, v3)
    assert code == 3 and any("modified after the previous Stage 12 install" in c for c in out["conflicts"]), out
    assert snapshot(tree) == before
    record("S12-T86", {"upgrade": "12.1 -> 12.2 ok", "local_modification": "12.3 refused"})


def test_S12_T87_crlf_windows_checkout(tree, payload):
    targets = ["backend/orchestration/gateway.py", "DECISIONS.md", "documentation/requirements/requirement_register.yaml",
               ".gitignore"]
    for rel in targets:
        p = tree / rel
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    before = snapshot(tree)
    code, out = run(tree, payload)
    assert code == 0 and out["status"] == "INSTALLED", out
    for rel in targets:
        b = (tree / rel).read_bytes()
        assert b.count(b"\r\n") == b.count(b"\n"), f"{rel} lost its CRLF line endings"
    code, out = run(tree, payload, "--uninstall")
    assert code == 0 and snapshot(tree) == before
    bat = (INST / "install.bat").read_bytes()
    assert bat.count(b"\r\n") == bat.count(b"\n") and b"exit /b %ERRORLEVEL%" in bat and b"goto trypython" in bat
    record("S12-T87", {"crlf_files": targets})


def test_S12_T88_installed_tree_imports_and_verifies(tree, payload):
    p = subprocess.run([PY, str(INST / "install.py"), "--target", str(tree), "--payload", str(payload), "--json",
                        "--python", PY], capture_output=True, text=True, timeout=600)
    out = json.loads(p.stdout)
    assert p.returncode == 0 and out["status"] == "INSTALLED", out
    r = subprocess.run([PY, "scripts/requirement_register.py", "check"], cwd=tree, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    sh = subprocess.run(["bash", str(INST / "install.sh"), "--target", str(tree), "--payload", str(payload),
                         "--json", "--no-verify-import"], capture_output=True, text=True, timeout=600)
    assert sh.returncode == 0 and json.loads(sh.stdout)["status"] == "ALREADY_INSTALLED"
    record("S12-T88", {"install": out["status"], "register_check": r.stdout.strip(), "install_sh": "ok"})
