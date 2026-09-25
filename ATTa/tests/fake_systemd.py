#!/usr/bin/env python3
"""A stand-in for systemd in the ADM tests: owns the gateway process the way systemd owns
app-builder-gateway.service (the deploy's `restart` asks it; the gateway is NOT a child of the deploy).

    fake_systemd.py CTL_DIR APP_LINK ENV_FILE

Protocol (files in CTL_DIR): a deploy writes `restart.req` (any content); this restarts the gateway from APP_LINK
(the symlink, resolved at start, like ExecStart=/opt/app-builder/gateway.py) and writes `restart.ack` with the same
content once the new process has been started. `stop.req` stops it. The current gateway pid is in `gateway.pid`."""
import os, signal, subprocess, sys, time
from pathlib import Path

ctl, app, env_file = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "04-deployment"))
import envfile  # noqa: E402

child = None
running = True


def stop_child():
    global child
    if child and child.poll() is None:
        child.terminate()
        try:
            child.wait(10)
        except subprocess.TimeoutExpired:
            child.kill(); child.wait()
    child = None


def start_child():
    global child
    vals, _ = envfile.load(str(env_file))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("APP_BUILDER_", "ATTA_"))}
    env.update(vals)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    code = app.resolve()
    log = open(ctl / "gateway.log", "ab")
    child = subprocess.Popen([sys.executable, str(code / "gateway.py")], env=env, stdout=log, stderr=subprocess.STDOUT,
                             cwd=str(vals.get("APP_BUILDER_ROOT", "/")))
    (ctl / "gateway.pid").write_text(f"{child.pid} {code.name}\n")


def on_term(*_):
    global running
    running = False


signal.signal(signal.SIGTERM, on_term)
(ctl / "ready").write_text(str(os.getpid()))
while running:
    for req in ("restart", "stop"):
        r = ctl / f"{req}.req"
        if r.exists():
            content = r.read_text()
            r.unlink()
            stop_child()
            if req == "restart" and app.exists():
                start_child()
            (ctl / f"{req}.ack").write_text(content)
    time.sleep(0.05)
stop_child()
