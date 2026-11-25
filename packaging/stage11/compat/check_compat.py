#!/usr/bin/env python3
"""Stage 11 compatibility check against the ACTUAL Stage 01B and Stage 04 packages (standard library only).

    python compat/check_compat.py --baseline <Stage 01A tree: git repo, directory or zip> \
        --stage 01B=<package> --stage 04=<package> [--run-tests] [--out report.json]

A <package> may be a full application tree (zip or directory containing backend/orchestration/controller.py,
like ATTa-stage11-sidecar.zip) or an additions package with an overlay/ directory. Nothing is modified in place:
everything happens in a temporary Git repository that is deleted afterwards.

Steps
  1. materialise the baseline into a temporary Git repository and commit it;
  2. apply each other stage (in the order given) as one commit, recording what it added/changed/deleted;
  3. static compatibility checks between those stages and Stage 11:
       overlapping paths · migration file names and numeric prefixes · SQL touching the `research` schema ·
       capability ids · tool numbers 011/012 and research.* tool names · permission names · identifier prefixes ·
       /v1/research routes · configuration/research.yaml · the Stage 01B network-mode and Stage 04 wiring hooks;
  4. run this package's transactional installer (--check, then a real install) on the combined tree;
  5. optionally (--run-tests) run Stage 11 + Stage 01/01A + legacy + the other stages' own tests in the combined
     tree (needs the ATTa Python environment and the test PostgreSQL; applies the combined migrations).
Exit 0 = COMPATIBLE, 1 = INCOMPATIBLE (conflicts or failures), 2 = usage/input error.
Report: JSON (stdout or --out) with every finding and the verdict."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
S11 = json.loads((PKG / "PACKAGE.json").read_text(encoding="utf-8"))
OUR_TOOLS = re.compile(r"^(011|012)\.\d{3}$")
OUR_PREFIXES = {"rsr", "rqy", "src", "snp", "rev", "clm", "cit", "cdn", "rck", "ret"}
OUR_PERMISSIONS = {"research.search.query", "network.web.read"}
OUR_CAPABILITIES = {"research.web", "research.evidence"}
# Fail on the UNMODIFIED Stage 01A baseline in the reference environment (STAGE_11_VERIFICATION.md §1): reported,
# never counted as a Stage 11 incompatibility. Any other failure is a new failure.
KNOWN_PREEXISTING = {
    "test_S01_T06_discover_valid_capability_manifest", "test_S01_T07_reject_invalid_capability_manifest",
    "test_S01_T08_reject_duplicate_capability_identifiers", "test_S01_T09_new_capability_registers_without_changing_start",
    "test_S01_T13_protected_operations_default_to_deny",
    "test_S01A_T51_worker_cannot_alter_protected_verification_criteria",
    "test_journey_result_is_recorded_on_stage_6", "test_login_requires_a_real_test_account",
    "test_login_with_wrong_expectation_fails_even_though_login_worked",
    "test_login_without_a_post_login_expectation_is_refused", "test_page_load_without_journey_is_not_qualified",
    "test_real_login_journey_passes", "test_wrong_password_fails_the_journey",
    "test_login_submit_is_inside_the_password_form"}
EXPECTED_SHARED = {"BLOCKERS.md", "DECISIONS.md", "IMPLEMENTATION_PROGRESS.md", "CHANGE_MANIFEST.md",
                   "MASTER_IMPLEMENTATION_REGISTER.md", "documentation/requirements/requirement_register.yaml"}


def git(cwd: Path, *a: str, check: bool = True) -> str:
    cp = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)
    if check and cp.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {cp.stderr.strip()[:400]}")
    return cp.stdout


def unpack(src: Path, into: Path) -> Path:
    """Return the directory holding the package content (zip extracted into `into`)."""
    if src.is_file() and zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            for n in z.namelist():
                if n.startswith("/") or ".." in Path(n).parts:
                    raise ValueError(f"unsafe path in {src}: {n}")
            z.extractall(into)
        kids = [p for p in into.iterdir() if p.name != "__MACOSX"]
        return kids[0] if len(kids) == 1 and kids[0].is_dir() else into
    if src.is_dir():
        return src
    raise ValueError(f"not a zip or directory: {src}")


def is_full_tree(d: Path) -> bool:
    return (d / "backend" / "orchestration" / "controller.py").is_file()


def copy_tree(src: Path, dst: Path) -> None:
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if ".git" in rel.parts or "__pycache__" in rel.parts or ".venv" in rel.parts or "node_modules" in rel.parts:
            continue
        t = dst / rel
        if p.is_dir():
            t.mkdir(parents=True, exist_ok=True)
        elif p.is_file():
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, t)


def materialise_baseline(src: Path, repo: Path, tmp: Path, ref: str | None = None) -> None:
    if (src / ".git").exists():
        git(tmp, "clone", "-q", str(src), str(repo))
        if ref:
            git(repo, "checkout", "-q", ref)
        return
    d = unpack(src, tmp / "baseline-src")
    repo.mkdir(parents=True)
    copy_tree(d, repo)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=compat", "-c", "user.email=compat@local", "commit", "-qm", "baseline")


def apply_stage(repo: Path, label: str, src: Path, tmp: Path) -> dict:
    d = unpack(src, tmp / f"stage-{label}")
    if is_full_tree(d):
        tracked = set(git(repo, "ls-files").splitlines())
        incoming = {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and not
                    ({".git", "__pycache__", ".venv", "node_modules"} & set(p.relative_to(d).parts))}
        for rel in sorted(tracked - incoming):
            (repo / rel).unlink()
        copy_tree(d, repo)
    elif (d / "overlay").is_dir():
        copy_tree(d / "overlay", repo)
        for side in ("ours",):
            if (d / side).is_dir():
                copy_tree(d / side, repo)
    else:
        raise ValueError(f"{label}: neither a full application tree nor an additions package with overlay/")
    git(repo, "add", "-A")
    st = git(repo, "status", "--porcelain").splitlines()
    git(repo, "-c", "user.name=compat", "-c", "user.email=compat@local", "commit", "-qm", f"stage {label}",
        "--allow-empty")
    out = {"added": [], "modified": [], "deleted": []}
    for line in git(repo, "show", "--name-status", "--format=", "HEAD").splitlines():
        code, _, path = line.partition("\t")
        out[{"A": "added", "M": "modified", "D": "deleted"}.get(code[:1], "modified")].append(path)
    return out


def scan_text(repo: Path, paths: list[str], pattern: str) -> list[str]:
    rx = re.compile(pattern)
    hits = []
    for rel in paths:
        p = repo / rel
        if p.is_file() and p.stat().st_size < 2_000_000:
            try:
                if rx.search(p.read_text(encoding="utf-8", errors="ignore")):
                    hits.append(rel)
            except OSError:
                pass
    return hits


def static_checks(repo: Path, label: str, ch: dict) -> list[dict]:
    f = []
    ours = set(S11["added"]) | set(S11["modified"])
    touched = set(ch["added"]) | set(ch["modified"]) | set(ch["deleted"])

    def add(severity, kind, detail):
        f.append({"stage": label, "severity": severity, "check": kind, "detail": detail})
    for rel in sorted(touched & ours):
        if rel in EXPECTED_SHARED:
            add("info", "shared_document", f"{rel} also changed by {label}: installer merges it (union/3-way/regenerated)")
        else:
            add("error", "path_overlap", f"{rel} is also added/changed by {label}")
    for rel in ch["deleted"]:
        if rel in ("backend/orchestration/controller.py", "backend/orchestration/gateway.py",
                   "backend/capability_registry/registry.py", "backend/policy_engine/engine.py",
                   "backend/orchestration/models.py", "backend/orchestration/context.py"):
            add("error", "dependency_removed", f"{label} deletes {rel}, which Stage 11 builds on")
    migs = sorted(p.name for p in (repo / "database" / "migrations").glob("*.sql"))
    ours_mig = [Path(r).name for r in S11["added"] if r.startswith("database/migrations/")]
    for m in ours_mig:
        if m in migs and f"database/migrations/{m}" in touched:
            add("error", "migration_name", f"{label} ships a migration named {m}")
        same_prefix = [x for x in migs if x[:4] == m[:4] and x != m]
        for x in same_prefix:
            add("error", "migration_number", f"{x} ({label}) uses the same number as {m}")
    sql = [r for r in touched if r.endswith(".sql")]
    for rel in scan_text(repo, sql, r"(?i)\bresearch\s*\.\s*\w+|create\s+schema\s+(if\s+not\s+exists\s+)?research\b"):
        add("error", "schema_research", f"{rel} ({label}) touches the `research` schema")
    for rel in [r for r in touched if r.endswith("capability.yaml")]:
        m = re.search(r"(?m)^id:\s*([\w.]+)", (repo / rel).read_text(errors="ignore")) if (repo / rel).is_file() else None
        if m and m.group(1) in OUR_CAPABILITIES:
            add("error", "capability_id", f"{rel} ({label}) declares capability id {m.group(1)}")
    tn = repo / "configuration" / "tool_numbers.yaml"
    if tn.is_file() and "configuration/tool_numbers.yaml" in touched:
        for num, tool in re.findall(r'"(\d{3}\.\d{3})":\s*\{tool:\s*"?([^"}\s]+)', tn.read_text()):
            if OUR_TOOLS.match(num) or tool.startswith(("research.web:", "research.evidence:")):
                add("error", "tool_number", f"{label} assigns {num} -> {tool} (reserved for Stage 11)")
    perm = repo / "configuration" / "permissions.yaml"
    if perm.is_file() and "configuration/permissions.yaml" in touched:
        for p in OUR_PERMISSIONS:
            if re.search(rf"(?m)^\s+{re.escape(p)}:", perm.read_text()):
                add("warning", "permission", f"{label} already defines permission {p}: confirm the meaning matches")
    ident = repo / "backend" / "contracts" / "identifiers.py"
    if ident.is_file() and "backend/contracts/identifiers.py" in touched:
        clash = OUR_PREFIXES & set(re.findall(r'"([a-z]{3})"', ident.read_text()))
        if clash:
            add("error", "id_prefix", f"{label} adds identifier prefixes used by Stage 11: {sorted(clash)}")
    py = [r for r in touched if r.endswith((".py", ".ts", ".tsx"))]
    for rel in scan_text(repo, py, r"""["']/(v1/)?research(/|["'])"""):
        add("warning", "api_route", f"{rel} ({label}) references a /research route: check it uses backend/research/api.py")
    if "configuration/research.yaml" in touched:
        add("error", "config", f"{label} changes configuration/research.yaml")
    hooks = {"01B": (r"AIR_GAPPED|current_mode|NetworkMode", "network-mode provider for backend/research/network.py"),
             "04": (r"wire_research_api|build_research_router|research_service", "Stage 04 research API wiring"),
             "12": (r"(?i)mcp|connector|skill|plugin", "Stage 12 connector/MCP/skill/plugin registration (must use the "
                    "one capability registry and tool catalogue)")}
    for key, (pat, what) in hooks.items():
        if label.upper().startswith(key):
            hits = scan_text(repo, py, pat)
            add("info", "integration_hook", f"{what}: {'found in ' + ', '.join(hits[:5]) if hits else 'not found yet'}")
    return f


def run(cmd: list[str], cwd: Path, env: dict, timeout: int, junit: Path | None = None) -> dict:
    if junit is not None:
        cmd = cmd + [f"--junitxml={junit}"]
    try:
        cp = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"cmd": " ".join(cmd), "exit": None, "summary": "TIMEOUT", "new_failures": ["TIMEOUT"]}
    tail = (cp.stdout + cp.stderr)[-3000:]
    summary = next((ln for ln in reversed(tail.splitlines()) if re.search(r"passed|failed|error", ln)), "")
    out = {"cmd": " ".join(cmd), "exit": cp.returncode, "summary": summary.strip(), "tail": tail[-1500:]}
    if junit is not None:
        import xml.etree.ElementTree as ET
        failed = []
        try:
            for tc in ET.parse(junit).iter("testcase"):
                if any(ch.tag in ("failure", "error") for ch in tc):
                    failed.append(tc.get("name"))
        except (ET.ParseError, OSError):
            failed = ["junit report missing (collection error?)"] if cp.returncode else []
        out["known_preexisting"] = sorted(n for n in failed if n in KNOWN_PREEXISTING)
        out["new_failures"] = sorted(n for n in failed if n not in KNOWN_PREEXISTING)
    else:
        out["new_failures"] = [] if cp.returncode == 0 else [f"exit {cp.returncode}"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--baseline-ref", help="commit to check out when --baseline is a Git repository "
                                           "(e.g. 21104c4 for Stage 01A)")
    ap.add_argument("--stage", action="append", default=[], help="LABEL=PATH (zip or directory); repeatable, in order")
    ap.add_argument("--run-tests", action="store_true")
    ap.add_argument("--python", default=sys.executable, help="ATTa Python environment for --run-tests")
    ap.add_argument("--keep", action="store_true", help="keep the temporary combined repository")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    report = {"stage11_package": S11.get("sidecar_commit"), "stages": [], "findings": [], "installer": {},
              "tests": [], "verdict": None}
    tmp = Path(tempfile.mkdtemp(prefix="atta-s11-compat-"))
    repo = tmp / "combined"
    try:
        materialise_baseline(Path(a.baseline).resolve(), repo, tmp, a.baseline_ref)
        for spec in a.stage:
            label, _, path = spec.partition("=")
            if not path:
                raise ValueError(f"--stage needs LABEL=PATH: {spec}")
            ch = apply_stage(repo, label, Path(path).resolve(), tmp)
            report["stages"].append({"label": label, "source": path, **{k: len(v) for k, v in ch.items()}})
            report["findings"] += static_checks(repo, label, ch)
        inst = [sys.executable, str(PKG / "install.py"), str(repo)]
        chk = subprocess.run(inst + ["--check"], capture_output=True, text=True)
        report["installer"]["check"] = {"exit": chk.returncode, "output": chk.stdout[-3000:]}
        if chk.returncode == 0:
            real = subprocess.run(inst, capture_output=True, text=True)
            report["installer"]["install"] = {"exit": real.returncode, "output": real.stdout[-3000:]}
        if a.run_tests and report["installer"].get("install", {}).get("exit") == 0:
            prefix = subprocess.run([a.python, "-c", "import sys; print(sys.prefix)"], capture_output=True,
                                    text=True).stdout.strip()
            # equivalent conditions: fresh trees have no .venv of their own; start.sh/tests use ATTA_VENV
            env = dict(os.environ, ATTA_ENV="test", ATTA_REQUIRE_DB="1", ATTA_VENV=prefix)
            local_env = Path(a.baseline) / "configuration" / "local.env"
            if local_env.is_file():
                shutil.copyfile(local_env, repo / "configuration" / "local.env")
            report["tests"].append(run([a.python, "-m", "backend.database", "migrate"], repo, env, 600))
            suites = [["tests/research"], ["tests/unit", "tests/security", "tests/integration", "tests/acceptance"]]
            legacy = sorted(str(p.relative_to(repo)) for p in (repo / "tests").glob("test_*.py"))
            if legacy:
                suites.append(legacy)
            others = sorted({str(Path(r).parent) for s in report["stages"] for r in
                             git(repo, "ls-files", "tests").splitlines()
                             if r.endswith(".py") and Path(r).name.startswith("test_")} -
                            {"tests", "tests/research", "tests/unit", "tests/security", "tests/integration",
                             "tests/acceptance"})
            for o in others:
                suites.append([o])
            for i, s in enumerate(suites):
                report["tests"].append(run([a.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *s], repo, env,
                                           3600, junit=tmp / f"suite{i}.xml"))
    except Exception as exc:  # noqa: BLE001
        report["verdict"] = "ERROR"
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if a.keep:
            report["combined_repository"] = str(repo)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    if report["verdict"] != "ERROR":
        errors = [x for x in report["findings"] if x["severity"] == "error"]
        inst_ok = report["installer"].get("check", {}).get("exit") == 0 and \
            report["installer"].get("install", {}).get("exit") == 0
        tests_ok = all(not t.get("new_failures") for t in report["tests"])
        report["verdict"] = "COMPATIBLE" if not errors and inst_ok and tests_ok else "INCOMPATIBLE"
        report["note"] = "pre-existing failures documented in STAGE_11_VERIFICATION.md (S01-T06..T09/T13, S01A-T51, " \
                         "8 legacy Playwright tests) also fail on the unmodified baseline; compare suites before judging"
    text = json.dumps(report, indent=2)
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    return {"COMPATIBLE": 0, "INCOMPATIBLE": 1}.get(report["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
