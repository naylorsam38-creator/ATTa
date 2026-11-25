"""Stage 12 trial installs onto OTHER WORKERS' REAL TREES (never merged, never pushed).

    .venv/bin/python scripts/stage12_trial.py --trial stage01b|stage11|base [--phases pre,post]

For each trial a git worktree is checked out at the other worker's published commit ("pre") and a second one gets
Stage 12 installed by installer/stage12/install.py ("post"). The same suites run in both, with the same environment;
the Stage 12 suite additionally runs in "post". Regressions = tests failing post that pass pre. Evidence:
evidence/stage_12/trials/<trial>/... The worktrees are removed afterwards."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "bin" / "python")
TRIALS = {
    "base": {"ref": "21104c413c30a3b2714f877892970e7db19a1d19", "worker": "Stage 01A baseline", "extra": []},
    "stage01b": {"ref": "origin/claude/ai-app-stage-01-foundation-82jy3a", "worker": "Worker A — Stage 01B",
                 "extra": [("S01B", ["tests/acceptance/test_s01b_acceptance.py"])]},
    "stage11": {"ref": "origin/claude/focused-cori-ajjjn0", "worker": "Worker 3 — Stage 11",
                "extra": [("S11", ["tests/research"])]},
}
COMMON = [("UNIT", ["tests/unit", "tests/security", "tests/integration"]),
          ("S01A-A", ["tests/acceptance/test_s01a_acceptance.py"]),
          ("S01A-B", ["tests/acceptance/test_s01a_acceptance_b.py"]),
          ("S01", ["tests/acceptance/test_s01_acceptance.py"])]
S12 = ("S12", ["tests/integrations", "--ignore=tests/integrations/test_stage12_installer.py"])


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sh(cmd, cwd, env=None, timeout=3600):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def suite(name, args, tree: Path, env, out_dir: Path) -> dict:
    t0, started = time.monotonic(), now()
    try:
        p = sh([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs", *args], tree, env, 3000)
        code, text = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as exc:
        code, text = -1, f"TIMEOUT {exc}"
    clean = "\n".join(ln for ln in text.splitlines() if not ln.startswith('{"ts"'))
    last = clean.strip().splitlines()[-1] if clean.strip() else ""
    rec = {"suite": name, "args": args, "exit_code": code, "started": started, "duration_s": round(time.monotonic() - t0, 1),
           "counts": {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|errors?)", last)},
           "failed": sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", clean, re.M))),
           "skipped": re.findall(r"^SKIPPED \[\d+\] (.+)$", clean, re.M),
           "output_sha256": hashlib.sha256(text.encode()).hexdigest(), "output_tail": clean[-2500:]}
    (out_dir / f"{name}.json").write_text(json.dumps(rec, indent=1))
    print(f"  {name:7} exit={code} {rec['counts']} failed={len(rec['failed'])} {rec['duration_s']}s", flush=True)
    return rec


def prepare(ref: str, path: Path) -> str:
    if path.exists():
        sh(["git", "worktree", "remove", "--force", str(path)], ROOT)
        shutil.rmtree(path, ignore_errors=True)
    r = sh(["git", "worktree", "add", "--detach", str(path), ref], ROOT)
    if r.returncode != 0:
        raise SystemExit(r.stderr)
    shutil.copy2(ROOT / "configuration" / "local.env", path / "configuration" / "local.env")
    (path / ".venv").symlink_to(ROOT / ".venv")
    return sh(["git", "rev-parse", "HEAD"], path).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", required=True, choices=sorted(TRIALS))
    ap.add_argument("--phases", default="pre,post")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    t = TRIALS[a.trial]
    base_dir = Path(os.environ.get("ATTA_TRIAL_DIR", "/tmp/atta-stage12-trials"))
    ev = ROOT / "evidence" / "stage_12" / "trials" / a.trial
    ev.mkdir(parents=True, exist_ok=True)
    payload = base_dir / "payload"
    sh([PY, str(ROOT / "installer/stage12/build_payload.py"), "--out", str(payload)], ROOT)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")}
    if "ATTA_CHROMIUM" not in env:
        env["ATTA_CHROMIUM"] = sh([PY, "-c", "from playwright.sync_api import sync_playwright\nwith sync_playwright() as p: "
                                   "print(p.chromium.executable_path)"], ROOT, env).stdout.strip()
    summary = {"trial": a.trial, "worker": t["worker"], "ref": t["ref"], "sidecar_commit":
               sh(["git", "rev-parse", "HEAD"], ROOT).stdout.strip(), "recorded": now(), "phases": {}}
    suites = COMMON + t["extra"]
    for phase in a.phases.split(","):
        tree = base_dir / f"{a.trial}-{phase}"
        commit = prepare(t["ref"], tree)
        pdir = ev / phase
        pdir.mkdir(exist_ok=True)
        info = {"tree_commit": commit, "suites": {}}
        print(f"[{a.trial}/{phase}] {commit}", flush=True)
        if phase == "post":
            r = sh([sys.executable, str(ROOT / "installer/stage12/install.py"), "--target", str(tree), "--payload",
                    str(payload), "--python", PY, "--json"], ROOT, env)
            info["install"] = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {"raw": r.stdout + r.stderr}
            info["install"].pop("changes", None)
            print(f"  install: {info['install'].get('status')}", flush=True)
            if r.returncode != 0:
                summary["phases"][phase] = info
                break
        m = sh([PY, "-m", "backend.database", "migrate"], tree, env)
        info["migrate"] = (m.stdout + m.stderr).strip().splitlines()[-1:] if (m.stdout + m.stderr).strip() else []
        for name, args in suites + ([S12] if phase == "post" else []):
            info["suites"][name] = {k: v for k, v in suite(name, args, tree, env, pdir).items()
                                    if k not in ("output_tail",)}
        sh(["git", "checkout", "--", "evidence"], tree)
        summary["phases"][phase] = info
        if not a.keep:
            sh(["git", "worktree", "remove", "--force", str(tree)], ROOT)
    pre, post = summary["phases"].get("pre"), summary["phases"].get("post")
    if pre and post:
        summary["regressions"] = {n: sorted(set(post["suites"][n]["failed"]) - set(pre["suites"][n]["failed"]))
                                  for n in pre["suites"] if n in post["suites"]}
        summary["fixed_or_changed"] = {n: sorted(set(pre["suites"][n]["failed"]) - set(post["suites"][n]["failed"]))
                                       for n in pre["suites"] if n in post["suites"]}
    (ev / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({"regressions": summary.get("regressions")}, indent=1))
    return 0 if not any((summary.get("regressions") or {}).values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
