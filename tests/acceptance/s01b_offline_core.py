"""S01B-T26 helper, executed INSIDE a new kernel network namespace (loopback only): creates a disposable PostgreSQL
cluster, starts the complete ATTa core through the service manager, probes the API and stops everything.
Writes a JSON result; nothing here can reach the Internet."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.deployment import isolation  # noqa: E402

work, out = Path(sys.argv[1]), Path(sys.argv[2])
res = {"external_reachable": isolation.external_reachable()}
pg = isolation.DisposablePostgres(work)
try:
    pg.start()
    base = {k: v for k, v in os.environ.items() if not k.startswith("ATTA__")}
    env = {**base, "ATTA_ENV": "test", "PYTHONPATH": str(ROOT), "NEXT_TELEMETRY_DISABLED": "1",
           "ATTA__RUNTIME__DIRECTORY": str(work / "rt"), "ATTA__LOGGING__DIRECTORY": str(work / "rt" / "logs"),
           "ATTA__API__PORT": str(isolation.free_port()), "ATTA__REDIS__PORT": str(isolation.free_port()), **pg.env()}
    pg.migrate(env)
    sm = lambda c: subprocess.run([sys.executable, "-m", "backend.service_manager", c, "--json"], cwd=ROOT, env=env,  # noqa: E731
                                  capture_output=True, text=True, timeout=300)
    r = sm("start")
    res["start_exit"], res["start_tail"] = r.returncode, r.stdout[-1500:]
    import httpx
    port = env["ATTA__API__PORT"]
    for path, key in (("/v1/health/live", "live"), ("/v1/health/ready", "ready"), ("/v1/health/controller", "controller")):
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}{path}", timeout=10)
            res[key + "_status"], res[key] = resp.status_code, resp.json()
        except Exception as exc:  # noqa: BLE001
            res[key + "_status"], res[key] = None, repr(exc)
    r = sm("stop")
    res["stop_exit"] = r.returncode
finally:
    pg.stop(destroy=True)
    out.write_text(json.dumps(res, indent=2, default=str))
