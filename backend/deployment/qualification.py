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
        # Complete offline installation from the bundle with no network: bundled CPython, every Python lock, both npm
        # projects, system packages resolvable from the bundle alone, and the bundled browser actually launching.
        venv = Path(ctx["workspace"]) / "offline-venv"
        steps, log = [
            ("install-offline", [sys.executable, "-m", "backend.deployment", "bundle", "install-offline", "--full",
                                 "--bundle", ctx["bundle"], "--venv", str(venv)]),
            ("install-system --simulate", [sys.executable, "-m", "backend.deployment", "bundle", "install-system",
                                           "--simulate", "--bundle", ctx["bundle"]]),
            ("bundled browser", [str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")), "-c",
                                 "from playwright.sync_api import sync_playwright\n"
                                 "with sync_playwright() as p:\n b=p.chromium.launch(); pg=b.new_page();"
                                 " pg.set_content('<p>offline</p>'); print('chromium', b.version, pg.inner_text('p'))"]),
            ("bundle unchanged", [sys.executable, "-m", "backend.deployment", "bundle", "verify", "--bundle",
                                  ctx["bundle"]]),
        ], ""
        for label, cmd in steps:
            env = {**ctx["env"], "PLAYWRIGHT_BROWSERS_PATH": str(Path(ctx["bundle"]).resolve() / "browsers")}
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=3600, env=env)
            log += f"== {label}: exit {r.returncode}\n{r.stdout[-1500:]}{r.stderr[-1500:]}\n"
            if r.returncode != 0:
                return r.returncode, log
        return 0, log
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


def _whoami() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    except (ImportError, KeyError):
        return os.environ.get("USERNAME", "unknown")


def share_ca_bundles(env: dict[str, str], uid: int, gid: int, dest: str = "/var/lib/atta-tester/ca") -> dict[str, str]:
    """Environment variables that point to a CA certificate bundle the test identity cannot read (e.g. a proxy CA under
    the verifier's home) are re-pointed to a world-readable copy. Only public certificate bundles qualify: a file
    holding any private key is never copied."""
    import hashlib
    import stat
    out: dict[str, str] = {}
    for k, v in env.items():
        if not v or not v.startswith("/") or not os.path.isfile(v):
            continue
        try:
            st = os.stat(v)
            readable = (st.st_uid == uid and st.st_mode & stat.S_IRUSR) or (st.st_gid == gid and st.st_mode & stat.S_IRGRP) \
                or st.st_mode & stat.S_IROTH
            parents_ok = all(os.stat(p).st_mode & stat.S_IXOTH for p in _parents(v))
            if readable and parents_ok:
                continue
            data = open(v, "rb").read(16 << 20)
        except OSError:
            continue
        if b"BEGIN CERTIFICATE" not in data or b"PRIVATE KEY" in data:
            continue
        os.makedirs(dest, mode=0o755, exist_ok=True)
        os.chmod(dest, 0o755)
        target = os.path.join(dest, hashlib.sha256(data).hexdigest()[:16] + ".crt")
        if not os.path.isfile(target):
            with open(target + ".tmp", "wb") as f:
                f.write(data)
            os.chmod(target + ".tmp", 0o644)
            os.replace(target + ".tmp", target)
        out[k] = target
    return out


def _parents(path: str) -> list[str]:
    out, p = [], os.path.dirname(path)
    while p and p != "/":
        out.append(p)
        p = os.path.dirname(p)
    return out


def _test_identity(data: dict) -> tuple[list[str], dict, "object | None"]:
    """(runuser prefix, env overrides, pwd entry) for the unprivileged test identity, when this runner is root on a
    POSIX host and the account exists; otherwise ([], {}, None) and tests run as the current account (recorded)."""
    user = data.get("run_as")
    if not user or os.name != "posix" or os.geteuid() != 0 or not shutil.which("runuser"):
        return [], {}, None
    import pwd
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return [], {}, None
    return ["runuser", "-u", user, "--"], {"HOME": pw.pw_dir, "USER": user, "LOGNAME": user,
                                           **share_ca_bundles(dict(os.environ), pw.pw_uid, pw.pw_gid)}, pw


def _hand_over(path: Path, pw) -> None:
    """Give the test identity ownership of a qualification-owned path (never the disposable cluster's data)."""
    if pw is None or not path.exists():
        return
    for p in [path, *([] if path.is_file() else path.rglob("*"))]:
        if "postgres" in p.relative_to(path).parts[:1] if p != path else False:
            continue
        try:
            os.lchown(p, pw.pw_uid, pw.pw_gid)
        except OSError:
            pass


def run_suite(suite: str, *, manifest: dict, profile: str, out_root: Path, bundle: str | None = None,
              inside_netns: bool = False, qid: str | None = None) -> dict:
    data = load_suites()
    out_root = Path(out_root).resolve()   # children run in other working directories: every path must be absolute
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
        # the re-executed run must evaluate conditions against THIS manifest, not whatever a test may have written
        # into a shared deployment directory meanwhile
        (ws / "qualification_manifest.json").write_text(json.dumps(manifest, default=str))
        cmd = isolation.in_new_netns([sys.executable, "-m", "backend.deployment", "qualify", "--suite", suite,
                                      "--inside-netns", "--qid", qid, "--profile", profile] +
                                     (["--bundle", bundle] if bundle else []))
        r = subprocess.run(cmd, cwd=REPO_ROOT, env={**os.environ, "ATTA_QUAL_OUT": str(out_root)})
        return json.loads((ws / "results.json").read_text())
    ctx = {"bundle": bundle, "workspace": str(ws)}
    pg = None
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ATTA__", "ATTA_BUILD_", "ATTA_ISOLATION_FILE"))}
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
                "ATTA__CAPABILITIES__CATALOGUE_NAMESPACE": qid,
                # Hermetic tooling: tests that create throw-away git repositories must not inherit the operator's
                # global/system git configuration (signing programs, hooks, credential helpers, network services).
                "GIT_CONFIG_GLOBAL": str(ws / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"})
    (ws / "gitconfig").write_text("[user]\n\tname = ATTa qualification\n\temail = qualification@localhost\n"
                                  "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n"
                                  "[safe]\n\tdirectory = *\n")        # this run's own copies, whichever identity
    try:
        if not isolation.pg_bin("initdb"):
            raise isolation.IsolationUnavailable("PostgreSQL server binaries not found")
        pg = isolation.DisposablePostgres(ws)
        pg.start()
        pg.migrate(env)
        env.update(pg.env())
        # The isolated endpoints are also written to an isolation overlay (ATTA_ISOLATION_FILE, highest configuration
        # precedence, restricted to endpoint keys). Existing test helpers deliberately reset ATTA__ overrides for the
        # platforms they start, and installers rewrite discovered.yaml; with the overlay every child still resolves
        # to the disposable cluster, never to the production database.
        overlay = ws / "isolation.yaml"
        overlay.write_text("# GENERATED for one qualification run: isolated resources only\n" + yaml.safe_dump({
            "database": {"host": "127.0.0.1", "port": pg.port, "name": "atta", "sslmode": "disable"},
            "object_storage": {"bucket": env["ATTA__OBJECT_STORAGE__BUCKET"]},
            "capabilities": {"catalogue_namespace": env["ATTA__CAPABILITIES__CATALOGUE_NAMESPACE"]}}, sort_keys=True))
        env["ATTA_ISOLATION_FILE"] = str(overlay)
        isolation_detail = {"database": f"disposable cluster on 127.0.0.1:{pg.port}", "workspace": str(ws),
                            "network": "new kernel network namespace (loopback only)" if inside_netns else "host"}
    except isolation.IsolationUnavailable as exc:
        isolation_detail = {"error": str(exc)}
    prefix, overrides, pw = _test_identity(data)
    ctx.update({"env": env, "run_prefix": prefix, "run_env": overrides, "run_pw": pw})
    if pw is not None:
        for d in ("runtime", "deployment"):
            (ws / d).mkdir(exist_ok=True)
            _hand_over(ws / d, pw)
        netaudit.touch()
        _hand_over(netaudit, pw)
        os.lchown(ws, pw.pw_uid, pw.pw_gid)
    isolation_detail["test_identity"] = pw.pw_name if pw is not None else _whoami()
    meta["isolation"] = isolation_detail
    results = []
    try:
        for tid in test_ids:
            spec = data["tests"][tid]
            unmet = [why for c in spec.get("requires", []) for ok, why in [condition_met(c, manifest, ctx)] if not ok]
            if "error" in isolation_detail and not spec.get("builtin"):
                unmet.append("isolated resources unavailable: " + isolation_detail["error"])
            if unmet:
                results.append(_blocked(tid, spec, meta, "; ".join(unmet)))
                continue
            results.append(_execute(tid, spec, meta, ws, env, ctx))
    finally:
        if pg is not None:      # always stopped; data kept only for diagnosis when something FAILED
            pg.stop(destroy=all(r["status"] != "FAIL" for r in results) and len(results) == len(test_ids))
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
    run_as = _whoami()
    if spec.get("builtin"):
        code, out = _builtin(spec["builtin"], ctx)       # builtins run in the (privileged) runner itself
        cmd = f"builtin:{spec['builtin']}"
    else:
        cmd = [c.replace("{python}", sys.executable) for c in spec["command"]]
        try:
            run_env = {**env, **{k: str(v) for k, v in (spec.get("env") or {}).items()}}   # declared per-test pins
            cwd = REPO_ROOT
            if spec.get("source_copy"):
                # earlier stages' unchanged tests run in a private source copy: their evidence writes land there
                if not ctx.get("source_copy"):
                    ctx["source_copy"] = isolation.source_copy(ws / "src")
                    _hand_over(ctx["source_copy"], ctx.get("run_pw"))
                cwd = ctx["source_copy"]
                run_env["PYTHONPATH"] = run_env["PYTHONPATH"].replace(str(REPO_ROOT), str(cwd))
            if not spec.get("privileged") and ctx.get("run_prefix"):
                # unprivileged test identity: cannot read the verifier keys or alter protected criteria
                cmd = ctx["run_prefix"] + cmd
                run_env.update(ctx["run_env"])
                run_as = ctx["run_pw"].pw_name
            cp = subprocess.run(cmd, cwd=cwd, env=run_env, capture_output=True, text=True,
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
            "status": "PASS" if code == 0 else "FAIL", "environment_specific": bool(spec.get("environment_specific")),
            "run_as": run_as, "privileged": bool(spec.get("privileged") or spec.get("builtin"))}


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
