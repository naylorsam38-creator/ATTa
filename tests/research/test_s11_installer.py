"""Stage 11 package and transactional installer: integrity, target rules, conflicts, dry-run exit codes, rehearsal,
rollback on failure and on hard kill, CRLF (Windows) targets, protected configuration, idempotence, and the
compatibility harness against synthetic Stage 01B / Stage 04 packages (the actual packages were not supplied)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "21104c413c30a3b2714f877892970e7db19a1d19"
sys.path.insert(0, str(ROOT / "scripts"))
import build_stage11_package as builder  # noqa: E402


@pytest.fixture(scope="module")
def pkg(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("pkg")
    return builder.build(out)


def git(cwd, *a, check=True):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, check=check).stdout


def commit(repo, msg="x"):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg)


@pytest.fixture
def target(tmp_path) -> Path:
    t = tmp_path / "atta"
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(ROOT), str(t)], check=True)
    git(t, "checkout", "-q", BASELINE)
    return t


def install(pkg_dir: Path, target: Path, *args, env=None):
    cp = subprocess.run([sys.executable, str(pkg_dir / "install.py"), str(target), *args], capture_output=True,
                        text=True, env={**os.environ, **(env or {})})
    return cp.returncode, cp.stdout + cp.stderr


def clean(t: Path) -> bool:
    return git(t, "status", "--porcelain", "--untracked-files=all").strip() == ""


def snapshot(t: Path) -> str:
    h = hashlib.sha256()
    for line in sorted(git(t, "ls-files", "-s").splitlines()):
        h.update(line.encode())
    for p in sorted(t.rglob("*")):
        if ".git" not in p.relative_to(t).parts and p.is_file():
            h.update(p.relative_to(t).as_posix().encode() + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------ package
def test_package_layout_and_windows_wrappers(pkg):
    meta = json.loads((pkg / "PACKAGE.json").read_text())
    assert meta["baseline_commit"] == BASELINE and not meta["deleted"]
    assert len(meta["modified"]) == 6 and all((pkg / "base" / m).is_file() and (pkg / "ours" / m).is_file()
                                               for m in meta["modified"])
    assert all((pkg / "overlay" / a).is_file() for a in meta["added"])
    for p in meta["added"] + meta["modified"]:
        assert not any(__import__("fnmatch").fnmatchcase(p, g.replace("**", "*")) for g in meta["protected_paths"]), p
    bat = (pkg / "install.bat").read_bytes()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"")      # CRLF for cmd.exe
    assert b"py -3" in bat and b"install.py" in bat
    assert "install.py" in (pkg / "install.ps1").read_text()
    src = (pkg / "install.py").read_text()
    for posix_only in ("os.fork", "os.getuid", "fcntl", "termios", "/bin/sh", "shell=True"):
        assert posix_only not in src, posix_only
    zf = pkg.parent / "ATTa-stage11-additions.zip"
    with zipfile.ZipFile(zf) as z:
        assert all(n.startswith("ATTa-stage11-additions/") for n in z.namelist())


def test_package_build_is_deterministic(pkg, tmp_path):
    again = builder.build(tmp_path)
    a = (pkg.parent / "ATTa-stage11-additions.zip").read_bytes()
    b = (again.parent / "ATTa-stage11-additions.zip").read_bytes()
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()


@pytest.mark.parametrize("tamper", ["modify", "extra", "remove"])
def test_tampered_package_refused_before_anything(pkg, tmp_path, target, tamper):
    bad = tmp_path / "bad"
    shutil.copytree(pkg, bad)
    f = bad / "overlay" / "backend" / "research" / "service.py"
    if tamper == "modify":
        f.write_text(f.read_text() + "\n# tampered\n")
    elif tamper == "extra":
        (bad / "overlay" / "backend" / "research" / "evil.py").write_text("print('x')")
    else:
        f.unlink()
    before = snapshot(target)
    code, out = install(bad, target)
    assert code == 7 and "REFUSED" in out and snapshot(target) == before


# ------------------------------------------------------------------------------------------------ target rules
def test_dirty_tree_refused(pkg, target):
    (target / "README.md").write_text("changed")
    code, out = install(pkg, target, "--check")
    assert code == 2 and "not clean" in out
    git(target, "checkout", "--", "README.md")
    (target / "untracked.txt").write_text("x")
    code, out = install(pkg, target)
    assert code == 2 and "not clean" in out and not (target / "backend" / "research").exists()


def test_subdirectory_nested_and_non_git_targets_refused(pkg, target, tmp_path):
    code, out = install(pkg, target / "backend", "--check")
    assert code == 2 and "top level" in out
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q")
    nested = outer / "atta"
    shutil.move(str(target), str(nested))
    code, out = install(pkg, nested, "--check")
    assert code == 2 and "nested inside another Git repository" in out
    plain = tmp_path / "plain"
    shutil.copytree(nested, plain, ignore=shutil.ignore_patterns(".git"))
    code, out = install(pkg, plain, "--check")
    assert code == 2 and "not a Git working tree" in out


def test_nested_repository_on_a_written_path_refused(pkg, target):
    sub = target / "capabilities"
    git(sub, "init", "-q")          # a repository nested where Stage 11 would write capabilities/research_*
    (target / ".gitignore").write_text((target / ".gitignore").read_text() + "\ncapabilities/.git\n")
    commit(target, "ignore")
    code, out = install(pkg, target, "--check")
    assert code in (2, 3) and "nested" in out


def test_merge_in_progress_and_non_atta_refused(pkg, target, tmp_path):
    (Path(git(target, "rev-parse", "--absolute-git-dir").strip()) / "MERGE_HEAD").write_text(BASELINE)
    code, out = install(pkg, target, "--check")
    assert code == 2 and "in progress" in out
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    (other / "a").write_text("a")
    commit(other)
    code, out = install(pkg, other, "--check")
    assert code == 2 and "not an ATTa Stage 01A tree" in out


# ------------------------------------------------------------------------------------------------ dry run / conflicts
def test_check_succeeds_changes_nothing_and_leaves_no_worktree(pkg, target):
    before = snapshot(target)
    code, out = install(pkg, target, "--check")
    assert code == 0 and "rehearsal passed" in out and "check passed" in out
    assert snapshot(target) == before and clean(target)
    assert len(git(target, "worktree", "list").splitlines()) == 1


def test_conflicting_new_file_fails_check_and_install(pkg, target):
    (target / "backend" / "research").mkdir()
    (target / "backend" / "research" / "hub.py").write_text("# someone else's file\n")
    commit(target, "other hub")
    before = snapshot(target)
    for args in (["--check"], []):
        code, out = install(pkg, target, *args)
        assert code == 3 and "UNRESOLVED CONFLICTS" in out and "backend/research/hub.py" in out
        assert snapshot(target) == before


def test_conflicting_register_yaml_fails_check(pkg, target):
    reg = target / "documentation" / "requirements" / "requirement_register.yaml"
    text = reg.read_text()
    i = text.index("- id: CAP-11")
    reg.write_text(text[:i] + text[i:].replace("status: NOT_STARTED", "status: BLOCKED", 1))
    commit(target, "other stage edits CAP-11")
    before = snapshot(target)
    code, out = install(pkg, target, "--check")
    assert code == 3 and "requirement_register.yaml" in out and snapshot(target) == before


def test_shared_logs_merge_with_other_stage_sections(pkg, target):
    for name in ("DECISIONS.md", "BLOCKERS.md"):
        p = target / name
        p.write_text(p.read_text() + "\n### Stage 01B section\n\n| D-01B-01 | other stage | reason |\n")
    p = target / "IMPLEMENTATION_PROGRESS.md"
    p.write_text("<!-- edited by Stage 04 -->\n" + p.read_text())
    commit(target, "other stage appended")
    code, out = install(pkg, target)
    assert code == 0, out
    for name in ("DECISIONS.md", "BLOCKERS.md"):
        t = (target / name).read_text()
        assert "Stage 01B section" in t and "Stage 11 sidecar" in t and "<<<<<<<" not in t
    t = (target / "IMPLEMENTATION_PROGRESS.md").read_text()
    assert t.startswith("<!-- edited by Stage 04 -->") and "Stage 11 sidecar" in t
    assert "union" in out


# ------------------------------------------------------------------------------------------------ install / rollback
def test_install_reproduces_the_sidecar_and_is_idempotent(pkg, target):
    meta = json.loads((pkg / "PACKAGE.json").read_text())
    code, out = install(pkg, target)
    assert code == 0, out
    for rel in meta["added"]:
        assert (target / rel).read_bytes() == (pkg / "overlay" / rel).read_bytes(), rel
    for rel in meta["modified"]:
        assert (target / rel).read_bytes() == (pkg / "ours" / rel).read_bytes(), rel
    changed = {ln[3:] for ln in git(target, "status", "--porcelain", "--untracked-files=all").splitlines()}
    assert changed == set(meta["added"]) | set(meta["modified"])
    assert not (Path(git(target, "rev-parse", "--absolute-git-dir").strip()) / "atta-stage11-install").exists()
    commit(target, "stage 11")
    code, out = install(pkg, target)
    assert code == 0 and "already installed" in out


def test_protected_configuration_untouched(pkg, target):
    meta = json.loads((pkg / "PACKAGE.json").read_text())
    import fnmatch
    files = [f for f in git(target, "ls-files").splitlines()
             if any(fnmatch.fnmatchcase(f, g.replace("**", "*")) for g in meta["protected_paths"])]
    assert len(files) > 50
    before = {f: hashlib.sha256((target / f).read_bytes()).hexdigest() for f in files}
    assert install(pkg, target)[0] == 0
    assert before == {f: hashlib.sha256((target / f).read_bytes()).hexdigest() for f in files}


@pytest.mark.parametrize("after", [1, 40, 85])
def test_failure_midway_rolls_back_completely(pkg, target, after):
    before = snapshot(target)
    code, out = install(pkg, target, env={"ATTA_S11_INSTALL_FAIL_AFTER": str(after)})
    assert code == 5 and "rolled back" in out, out
    assert snapshot(target) == before and clean(target)
    assert not (Path(git(target, "rev-parse", "--absolute-git-dir").strip()) / "atta-stage11-install").exists()


def test_hard_kill_is_recoverable(pkg, target):
    before = snapshot(target)
    code, _ = install(pkg, target, env={"ATTA_S11_INSTALL_CRASH_AFTER": "50"})
    assert code == 9 and not clean(target)
    code, out = install(pkg, target)
    assert code == 2 and "interrupted" in out
    code, out = install(pkg, target, "--rollback-interrupted")
    assert code == 0, out
    assert snapshot(target) == before and clean(target)
    assert install(pkg, target, "--check")[0] == 0


def test_crlf_target_windows_checkout(pkg, target):
    meta = json.loads((pkg / "PACKAGE.json").read_text())
    git(target, "config", "core.autocrlf", "false")
    for rel in meta["modified"]:
        p = target / rel
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    commit(target, "CRLF checkout")
    code, out = install(pkg, target)
    assert code == 0, out
    for rel in meta["modified"]:
        data = (target / rel).read_bytes()
        assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), rel     # line endings preserved
    assert "Stage 11 sidecar" in (target / "DECISIONS.md").read_text()


def test_installed_tree_imports_and_unit_tests_pass(pkg, target):
    assert install(pkg, target)[0] == 0
    cp = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                         "tests/research/test_s11_contracts.py", "tests/research/test_s11_extraction_dedup.py"],
                        cwd=target, capture_output=True, text=True, env={**os.environ, "ATTA_ENV": "test"})
    assert cp.returncode == 0, cp.stdout[-2000:]


# ------------------------------------------------------------------------------------------------ compatibility harness
def _full_tree(tmp: Path, name: str) -> Path:
    d = tmp / name
    d.mkdir()
    data = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", BASELINE], capture_output=True,
                          check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(d)], input=data, check=True)
    return d


def compat(pkg, *args):
    cp = subprocess.run([sys.executable, str(pkg / "compat" / "check_compat.py"), *args], capture_output=True,
                        text=True)
    return cp.returncode, json.loads(cp.stdout)


def test_compat_harness_accepts_a_compatible_synthetic_01b_and_04(pkg, tmp_path):
    s01b = _full_tree(tmp_path, "s01b")
    (s01b / "backend" / "deployment").mkdir(parents=True)
    (s01b / "backend" / "deployment" / "network_mode.py").write_text(
        "class Mode:\n    AIR_GAPPED = 'AIR_GAPPED'\n\ndef current_mode():\n    return Mode.AIR_GAPPED\n")
    (s01b / "database" / "migrations" / "0003_offline_qualification.sql").write_text("SELECT 1;\n")
    (s01b / "DECISIONS.md").write_text((s01b / "DECISIONS.md").read_text() + "\n### Stage 01B\n\n| D-01B-01 | x | y |\n")
    s04 = _full_tree(tmp_path, "s04")
    shutil.copytree(s01b, s04, dirs_exist_ok=True)
    (s04 / "apps" / "web" / "research.tsx").write_text("export const x = 1;\n")
    (s04 / "BLOCKERS.md").write_text((s04 / "BLOCKERS.md").read_text() + "\n### Stage 04\n")
    code, rep = compat(pkg, "--baseline", str(ROOT), "--baseline-ref", BASELINE, "--stage", f"01B={s01b}",
                       "--stage", f"04={s04}")
    assert code == 0 and rep["verdict"] == "COMPATIBLE", json.dumps(rep, indent=1)[:3000]
    assert rep["installer"]["install"]["exit"] == 0
    assert any(f["check"] == "integration_hook" and "found in" in f["detail"] for f in rep["findings"])


def test_compat_harness_reports_real_conflicts(pkg, tmp_path):
    bad = _full_tree(tmp_path, "s04bad")
    (bad / "backend" / "research").mkdir(parents=True)
    (bad / "backend" / "research" / "api.py").write_text("# Stage 04's own research API\n")
    (bad / "database" / "migrations" / "0011_ui_state.sql").write_text("CREATE SCHEMA research;\n")
    tn = bad / "configuration" / "tool_numbers.yaml"
    tn.write_text(tn.read_text() + '  "011.001": {tool: "ui.search:query"}\n')
    code, rep = compat(pkg, "--baseline", str(_full_tree(tmp_path, "base")), "--stage", f"04={bad}")
    kinds = {f["check"] for f in rep["findings"] if f["severity"] == "error"}
    assert code == 1 and rep["verdict"] == "INCOMPATIBLE"
    assert {"path_overlap", "migration_number", "schema_research", "tool_number"} <= kinds, kinds
    assert rep["installer"]["check"]["exit"] == 3
