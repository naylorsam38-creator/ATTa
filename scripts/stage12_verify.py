"""Stage 12 sidecar verification runner: executes every Stage 12 test and every regression suite and records
command, exit code, UTC timestamps, duration, pass/fail counts and a SHA-256 of the output as evidence.

    .venv/bin/python scripts/stage12_verify.py [--only S12|REG]

Writes evidence/stage_12/runs/<run>/<id>.json and evidence/stage_12/run_summary.json. Stage 01/01A acceptance tests
rewrite their own evidence files when executed; this runner restores them from git afterwards so the sidecar never
changes Stage 01/01A evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
OUT = ROOT / "evidence" / "stage_12"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def stage12_tests() -> list[tuple[str, str, list[str]]]:
    items = []
    for f in sorted((ROOT / "tests" / "integrations").glob("test_stage12_*.py")):
        for m in re.finditer(r"^def (test_(S12_T\d+)_\w+)", f.read_text(), re.M):
            tid = m.group(2).replace("_", "-")
            items.append((tid, m.group(1), [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                            f"{f.relative_to(ROOT)}::{m.group(1)}"]))
    return items


REGRESSION = [
    ("REG-S01-UNIT", "Stage 01 unit, security and integration suites",
     ["-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/unit", "tests/security", "tests/integration"]),
    ("REG-S01A-A", "Stage 01A acceptance (packages 01-04, checkpoint A)",
     ["-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/acceptance/test_s01a_acceptance.py"]),
    ("REG-S01A-B", "Stage 01A acceptance B (packages 05-08, checkpoint B; T48 re-runs Stage 01, T49 legacy ATTa)",
     ["-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/acceptance/test_s01a_acceptance_b.py"]),
    ("REG-S01", "Stage 01 acceptance (5 tests superseded by Stage 01A by design — proven by S01A-T48)",
     ["-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/acceptance/test_s01_acceptance.py"]),
    ("REG-LEGACY", "Existing ATTa v121.5 application suite (tests/test_*.py) vs tests/regression/legacy_baseline.json",
     ["-m", "pytest", "-q", "-p", "no:cacheprovider"] + sorted(str(p.relative_to(ROOT)) for p in
                                                             (ROOT / "tests").glob("test_*.py"))),
]


def run(tid: str, desc: str, cmd: list[str], run_dir: Path, env: dict, timeout: int = 2400) -> dict:
    started, t0 = now(), time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        code, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code, out = -1, f"TIMEOUT after {timeout}s\n{exc.stdout or ''}"
    out_clean = "\n".join(ln for ln in out.splitlines() if not ln.startswith('{"ts"'))
    summary = re.findall(r"(\d+) (passed|failed|skipped|error|errors|deselected)", out_clean.splitlines()[-1]
                         if out_clean.strip() else "")
    rec = {"id": tid, "description": desc, "command": [c if c != PY else "{python}" for c in cmd],
           "exit_code": code, "result": "PASS" if code == 0 else "FAIL", "started": started, "finished": now(),
           "duration_s": round(time.monotonic() - t0, 2), "counts": {k: int(v) for v, k in summary},
           "failed_tests": sorted(set(re.findall(r"^FAILED (\S+)", out_clean, re.M))),
           "output_sha256": hashlib.sha256(out.encode()).hexdigest(), "output_tail": out_clean[-3000:]}
    (run_dir / f"{tid}.json").write_text(json.dumps(rec, indent=2))
    print(f"{tid:14} {rec['result']} exit={code} {rec['counts']} {rec['duration_s']}s", flush=True)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["S12", "REG"])
    a = ap.parse_args()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    run_id = "s12run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = OUT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    if "ATTA_CHROMIUM" not in env:
        try:
            env["ATTA_CHROMIUM"] = subprocess.run(
                [PY, "-c", "from playwright.sync_api import sync_playwright\nwith sync_playwright() as p: "
                           "print(p.chromium.executable_path)"], capture_output=True, text=True, env=env,
                timeout=60).stdout.strip()
        except Exception:  # noqa: BLE001
            pass
    results = []
    if a.only in (None, "S12"):
        for tid, name, cmd in stage12_tests():
            results.append(run(tid, name, cmd, run_dir, env, 900))
    if a.only in (None, "REG"):
        for tid, desc, args in REGRESSION:
            results.append(run(tid, desc, [PY, *args], run_dir, env))
        subprocess.run(["git", "checkout", "--", "evidence/stage_01", "evidence/stage_01a"], cwd=ROOT)
    summary = {"run": run_id, "code_under_test": head, "working_tree_dirty": bool(subprocess.run(
        ["git", "status", "--porcelain", "--", "backend", "database", "tests", "configuration"], cwd=ROOT,
        capture_output=True, text=True).stdout.strip()), "recorded": now(), "python": sys.version.split()[0],
        "results": [{k: r[k] for k in ("id", "result", "exit_code", "counts", "failed_tests", "started", "finished",
                                       "duration_s", "output_sha256")} for r in results]}
    prev = OUT / "run_summary.json"
    merged = json.loads(prev.read_text()) if prev.is_file() else {}
    merged.setdefault("runs", {})[run_id] = summary
    merged["latest"] = run_id
    prev.write_text(json.dumps(merged, indent=2))
    return 0 if all(r["result"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
