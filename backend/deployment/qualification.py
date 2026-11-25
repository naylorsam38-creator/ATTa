"""The single qualification runner (Stage 01B §20). Every result records: test id, environment id, profile, UTC
start/end, command, expected and actual result, exit code, evidence file + SHA-256, and PASS / FAIL / BLOCKED.
BLOCKED is never converted to PASS. Tests run against isolated resources; AIR_GAPPED suites run inside a new kernel
network namespace (or are BLOCKED if that isolation is unavailable — never a simulated air-gap)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

from backend.contracts import iso_utc, new_id
from backend.settings.loader import REPO_ROOT

from . import isolation

SUITES = REPO_ROOT / "configuration" / "qualification" / "suites.yaml"


def load_suites() -> dict:
    return yaml.safe_load(SUITES.read_text())


def expand(suite: str, data: dict) -> tuple[list[str], str]:
    s = data["suites"][suite]
    if "includes" in s:
        raise ValueError("composite suite: run its members")
    return list(s["tests"]), s.get("network", "host")


def condition_met(cond: str, manifest: dict, ctx: dict) -> tuple[bool, str]:
    gpu = manifest.get("gpu") or []
    osname = str(manifest.get("operating_system", {}).get("system", "")).lower()
    if cond == "os:linux":
        return osname == "linux", f"requires Linux (this is {osname})"
    if cond == "os:windows":
        return osname == "windows", f"requires Windows (this is {osname}); Windows-specific behaviour is only " \
                                    "certified on Windows"
    if cond == "netns":
        return bool(manifest.get("isolation_capabilities", {}).get("network_namespace")), \
            "no OS-level network namespace available: air-gap cannot be enforced, so it is not claimed"
    if cond == "postgres_binaries":
        return bool(isolation.pg_bin("initdb")), "PostgreSQL server binaries not found (disposable cluster impossible)"
    if cond == "node":
        return bool(shutil.which("node")) and (REPO_ROOT / "node_modules").is_dir(), "Node.js workspaces not installed"
    if cond == "gpu:nvidia":
        return bool(gpu), "no NVIDIA GPU detected"
    if cond == "gpu:h100":
        ok = any("H100" in g["name"].upper() and g["vram_mb"] >= 79 * 1024 for g in gpu)
        return ok, "no NVIDIA H100 80 GB detected on this machine (H100 qualification requires the real hardware)"
    if cond == "vllm":
        return "vllm" in (manifest.get("available_model_runtimes") or []), "vLLM not installed"
    if cond == "bundle":
        return bool(ctx.get("bundle")), "no offline dependency bundle supplied (--bundle DIR)"
    if cond.startswith("stage:"):
        return False, f"requires {cond[6:]} (model inference) which is not implemented yet"
    return False, f"unknown condition {cond}"


def _builtin(name: str, ctx: dict) -> tuple[int, str]:
    if name == "network_denied":
        reached = isolation.external_reachable()
        return (0 if not reached else 1), json.dumps({"external_reached": reached})
    if name == "offline_install":
        venv = Path(ctx["workspace"]) / "offline-venv"
        r = subprocess.run([sys.executable, "-m", "backend.deployment", "bundle", "install-offline", "--bundle",
                            ctx["bundle"], "--venv", str(venv)], cwd=REPO_ROOT, capture_output=True, text=True,
                           timeout=1800, env=ctx["env"])
        return r.returncode, r.stdout[-4000:] + r.stderr[-4000:]
    if name == "h100_identity":
        from .discovery import nvidia_gpus
        g = nvidia_gpus()
        h100 = [x for x in g["gpus"] if "H100" in x["name"].upper() and x["vram_mb"] >= 79 * 1024]
        ok = bool(h100) and bool(g.get("driver")) and bool(g.get("cuda"))
        return (0 if ok else 1), json.dumps({"gpus": g["gpus"], "driver": g.get("driver"), "cuda": g.get("cuda"),
                                             "h100_count": len(h100)})
    if name == "h100_worker_gpu":
        # The CUDA driver API honours CUDA_VISIBLE_DEVICES: the model worker sees exactly its GPU, tool workers none.
        probe = ("import ctypes,json;c=ctypes.CDLL('libcuda.so.1');assert c.cuInit(0)==0;n=ctypes.c_int();"
                 "c.cuDeviceGetCount(ctypes.byref(n));print(json.dumps(n.value))")
        seen = {}
        for label, vis in (("model_worker", "0"), ("tool_worker", "")):
            r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60,
                               env={**ctx["env"], "CUDA_VISIBLE_DEVICES": vis})
            seen[label] = r.stdout.strip() if r.returncode == 0 else f"error: {r.stderr[-200:]}"
        ok = seen.get("model_worker") == "1" and seen.get("tool_worker") in ("0", ) or \
            str(seen.get("tool_worker", "")).startswith("error")
        return (0 if ok and seen.get("model_worker") == "1" else 1), json.dumps(seen)
    if name == "h100_vllm":
        r = subprocess.run([sys.executable, "-c", "import torch, vllm; assert torch.cuda.is_available(); "
                            "print(vllm.__version__, torch.version.cuda, torch.cuda.get_device_name(0))"],
                           capture_output=True, text=True, timeout=300, env=ctx["env"])
        return r.returncode, r.stdout + r.stderr[-2000:]
    return 2, f"builtin {name} is not implemented"


def run_suite(suite: str, *, manifest: dict, profile: str, out_root: Path, bundle: str | None = None,
              inside_netns: bool = False, qid: str | None = None) -> dict:
    data = load_suites()
    test_ids, network = expand(suite, data)
    qid = qid or new_id("run").replace("run_", "qual_")
    ws = out_root / qid
    ws.mkdir(parents=True, exist_ok=True)
    os.chmod(ws, 0o755)
    meta = {"qualification_id": qid, "suite": suite, "environment_id": manifest.get("environment_id"),
            "profile": profile, "network": network, "started_at": iso_utc()}
    if network == "none" and not inside_netns:
        if not isolation.netns_available():
            results = [_blocked(t, data["tests"][t], meta, "no OS-level network namespace available: an air-gapped "
                                "PASS cannot be established on this machine") for t in test_ids]
            return _finish(ws, meta, results, None, None)
        cmd = isolation.in_new_netns([sys.executable, "-m", "backend.deployment", "qualify", "--suite", suite,
                                      "--inside-netns", "--qid", qid, "--profile", profile] +
                                     (["--bundle", bundle] if bundle else []))
        r = subprocess.run(cmd, cwd=REPO_ROOT, env={**os.environ, "ATTA_QUAL_OUT": str(out_root)})
        return json.loads((ws / "results.json").read_text())
    ctx = {"bundle": bundle, "workspace": str(ws)}
    pg = None
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ATTA__", "ATTA_BUILD_"))}
    netaudit = ws / "network_audit.jsonl"
    env.update({"ATTA_ENV": "test", "ATTA_PROFILE": "TEST_ISOLATED", "ATTA_DEPLOYMENT_DIR": str(ws / "deployment"),
                "ATTA_NET_AUDIT_LOG": str(netaudit), "NEXT_TELEMETRY_DISABLED": "1",
                "PYTHONPATH": str(Path(__file__).parent / "netaudit") + os.pathsep + str(REPO_ROOT),
                # Isolated per-qualification state: own runtime/log directories, own API and Redis ports (so a
                # managed Redis started by a test is a separate process with separate data), own object-storage
                # bucket and catalogue namespace. Credentials come from the disposable cluster below.
                "ATTA__RUNTIME__DIRECTORY": str(ws / "runtime"), "ATTA__LOGGING__DIRECTORY": str(ws / "runtime" / "logs"),
                "ATTA__API__PORT": str(isolation.free_port()), "ATTA__REDIS__PORT": str(isolation.free_port()),
                "ATTA__OBJECT_STORAGE__BUCKET": "atta-" + qid.lower().replace("_", "-"),
                "ATTA__CAPABILITIES__CATALOGUE_NAMESPACE": qid})
    try:
        if not isolation.pg_bin("initdb"):
            raise isolation.IsolationUnavailable("PostgreSQL server binaries not found")
        pg = isolation.DisposablePostgres(ws)
        pg.start()
        pg.migrate(env)
        env.update(pg.env())
        isolation_detail = {"database": f"disposable cluster on 127.0.0.1:{pg.port}", "workspace": str(ws),
                            "network": "new kernel network namespace (loopback only)" if inside_netns else "host"}
    except isolation.IsolationUnavailable as exc:
        isolation_detail = {"error": str(exc)}
    ctx["env"] = env
    meta["isolation"] = isolation_detail
    results = []
    for tid in test_ids:
        spec = data["tests"][tid]
        unmet = [why for c in spec.get("requires", []) for ok, why in [condition_met(c, manifest, ctx)] if not ok]
        if "error" in isolation_detail and not spec.get("builtin"):
            unmet.append("isolated resources unavailable: " + isolation_detail["error"])
        if unmet:
            results.append(_blocked(tid, spec, meta, "; ".join(unmet)))
            continue
        results.append(_execute(tid, spec, meta, ws, env, ctx))
    if pg is not None:
        pg.stop(destroy=all(r["status"] != "FAIL" for r in results))
    attempts = [json.loads(x) for x in netaudit.read_text().splitlines()] if netaudit.is_file() else []
    return _finish(ws, meta, results, attempts, isolation_detail)


def _blocked(tid: str, spec: dict, meta: dict, reason: str) -> dict:
    now = iso_utc()
    return {"test_id": tid, "title": spec.get("title"), "environment_id": meta["environment_id"], "profile": meta["profile"],
            "started_at": now, "finished_at": now, "command": spec.get("command") or f"builtin:{spec.get('builtin')}",
            "expected": "exit 0", "actual": "not executed", "exit_code": None, "evidence": None,
            "evidence_sha256": None, "status": "BLOCKED", "reason": reason,
            "environment_specific": bool(spec.get("environment_specific"))}


def _execute(tid: str, spec: dict, meta: dict, ws: Path, env: dict, ctx: dict) -> dict:
    started, t0 = iso_utc(), time.monotonic()
    if spec.get("builtin"):
        code, out = _builtin(spec["builtin"], ctx)
        cmd = f"builtin:{spec['builtin']}"
    else:
        cmd = [c.replace("{python}", sys.executable) for c in spec["command"]]
        try:
            cp = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                                timeout=float(spec.get("timeout_s", 900)))
            code, out = cp.returncode, (cp.stdout or "")[-200000:] + "\n--- stderr ---\n" + (cp.stderr or "")[-100000:]
        except subprocess.TimeoutExpired:
            code, out = None, f"TIMEOUT after {spec.get('timeout_s', 900)}s"
    ev = ws / "evidence" / f"{tid}.log"
    ev.parent.mkdir(exist_ok=True)
    ev.write_text(out)
    tail = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith('{"ts"')][-3:]
    return {"test_id": tid, "title": spec.get("title"), "environment_id": meta["environment_id"], "profile": meta["profile"],
            "started_at": started, "finished_at": iso_utc(), "duration_s": round(time.monotonic() - t0, 2),
            "command": cmd, "expected": "exit 0", "actual": " | ".join(tail)[-400:], "exit_code": code,
            "evidence": str(ev.relative_to(REPO_ROOT)) if REPO_ROOT in ev.parents else str(ev),
            "evidence_sha256": hashlib.sha256(ev.read_bytes()).hexdigest(),
            "status": "PASS" if code == 0 else "FAIL", "environment_specific": bool(spec.get("environment_specific"))}


def _finish(ws: Path, meta: dict, results: list[dict], attempts, isolation_detail) -> dict:
    mandatory = [r for r in results if not r["environment_specific"]]
    if any(r["status"] == "FAIL" for r in results):
        status = "FAIL"
    elif any(r["status"] == "BLOCKED" for r in mandatory) or not any(r["status"] == "PASS" for r in results):
        status = "BLOCKED"            # nothing executed is never a PASS (e.g. the H100 suite on non-H100 hardware)
    else:
        status = "PASS"
    doc = {**meta, "finished_at": iso_utc(), "status": status, "isolation": isolation_detail,
           "network_attempts": attempts, "results": results,
           "counts": {s: sum(1 for r in results if r["status"] == s) for s in ("PASS", "FAIL", "BLOCKED")}}
    (ws / "results.json").write_text(json.dumps(doc, indent=2, default=str))
    return doc
