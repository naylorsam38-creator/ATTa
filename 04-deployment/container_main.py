#!/usr/bin/env python3
"""
container_main.py — v117: ATTa in one container, the way Coolify runs it.

Does what bootstrap.sh + systemd do on a plain server, then keeps the three services running:
  gateway.py (the site you log into), pipeline.py (fetch, build, qualify, hand off),
  system_watcher.py --loop (the six-stage check, stage 6 = real Chromium via Playwright).
If any of them stops, all stop and the container exits, so Docker/Coolify restarts it cleanly.

First start only: drops <id>.library.json in the inbox, so the pipeline fetches every app on the
app list, checks them all and hands the ones that pass to Coolify. Nobody has to upload anything.

Needs (docker-compose.coolify.yml sets these up):
  /var/run/docker.sock   ATTa builds and test-runs apps on the host's Docker
  /srv/app-builder       ATTa's data, mounted at the SAME path on the host, because the apps it starts
                         are started by the host's Docker and must find their folders there too
  network_mode: host     test runs publish on the host's 127.0.0.1; the checks must reach them
"""
from __future__ import annotations
import json, os, secrets, shutil, signal, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
FIRST_RUN_MARK = ROOT / "state" / "container-first-library-run"
SERVICES = [("gateway", ["gateway.py"]), ("pipeline", ["pipeline.py"]), ("watcher", ["system_watcher.py", "--loop"])]

DEFAULT_ENV = """APP_BUILDER_ROOT={root}
APP_BUILDER_HOST={host}
APP_BUILDER_PORT={port}
APP_BUILDER_SESSION_SECRET={secret}
APP_BUILDER_BROWSER_CHECK=true
APP_BUILDER_WATCHER_INTERVAL=300
# Coolify hand-off. deploy-atta.sh fills these in; qualified apps wait in the outbox until both are set.
COOLIFY_URL=
COOLIFY_TOKEN=
# v117: a qualified app with no Coolify resource gets its own Coolify service, made by ATTa.
COOLIFY_AUTOCREATE=true
# Self-healing tier 3 (LLM). Blank = that tier is skipped.
ANTHROPIC_API_KEY=
APP_BUILDER_KEEP_RUNNING=false
APP_BUILDER_PRUNE_IMAGES=false
APP_BUILDER_BOOT_TIMEOUT=240
APP_BUILDER_BOOT_TIMEOUT_MAX=900
ALERT_WEBHOOK_URL=
"""


def say(*a):
    print("[atta]", *a, flush=True)


def seed(root: Path = ROOT, here: Path = HERE) -> None:
    """Folders, app list, front door, .env and accounts. Safe to run on every start."""
    for sub in ("inbox", "work", "library", "package", "state", "state/apps"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    # The app list shipped with this image is authoritative: a newer image brings a newer list.
    if (here / "upstream_apps.json").is_file():
        shutil.copyfile(here / "upstream_apps.json", root / "upstream_apps.json")
    front = here.parent / "02-front-door" / "front-door.html"
    if front.is_file():
        shutil.copyfile(front, root / "front-door.html")
    env = root / ".env"
    if not env.is_file():
        old = os.umask(0o077)
        try:
            env.write_text(DEFAULT_ENV.format(root=root, host=os.environ.get("APP_BUILDER_HOST", "0.0.0.0"),
                                              port=os.environ.get("APP_BUILDER_PORT", "8787"),
                                              secret=secrets.token_urlsafe(48)))
        finally:
            os.umask(old)
    os.chmod(env, 0o600)
    res = root / "coolify_resources.json"
    if not res.is_file():
        res.write_text('{\n  "apps": {}\n}\n')
    os.chmod(res, 0o600)


def load_env(root: Path = ROOT) -> dict:
    sys.path.insert(0, str(HERE))
    import envfile
    values, warnings = envfile.load(str(root / ".env"))
    for w in warnings:
        say(".env:", w)
    # deploy-atta.sh writes the Coolify address and token here (root-only); they win over .env.
    conn = root / ".coolify-connection"
    if conn.is_file():
        cvals, _ = envfile.load(str(conn))
        values.update({k: v for k, v in cvals.items() if k in ("COOLIFY_URL", "COOLIFY_TOKEN")})
    # Settings given to the container (Coolify's environment tab) win over the file.
    for k, v in os.environ.items():
        if k.startswith(envfile.KNOWN_PREFIXES) or k in envfile.KNOWN_NAMES:
            values[k] = v
    return values


def queue_first_library_run(root: Path = ROOT) -> bool:
    """Once per data folder: ask the pipeline to fetch and check everything on the app list."""
    mark = root / "state" / "container-first-library-run"
    if mark.exists():
        return False
    bid = time.strftime("lib%Y%m%d%H%M%S")
    (root / "inbox" / f"{bid}.library.json").write_text("{}\n")
    mark.write_text(json.dumps({"queued": bid, "at": time.time()}) + "\n")
    return True


def docker_ready(timeout: int = 300) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                              timeout=20).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(3)
    return False


def main() -> int:
    seed()
    env = {**os.environ, **load_env()}
    subprocess.run([sys.executable, str(HERE / "accounts.py"), "init"], env=env, check=False)
    creds = ROOT / "TEST_ACCOUNTS.txt"
    if creds.is_file():
        os.chmod(creds, 0o600)
        say(f"logins (admin + testers) are in {creds} on the host. Hand them out, then delete the file.")
    if not docker_ready():
        say("Docker is not reachable through /var/run/docker.sock; nothing can be built. Stopping.")
        return 1
    if queue_first_library_run():
        say("first start: queued a run over the whole app list")
    procs = {}
    for name, args in SERVICES:
        procs[name] = subprocess.Popen([sys.executable, str(HERE / args[0]), *args[1:]], cwd=str(ROOT), env=env)
        say(f"started {name} (pid {procs[name].pid})")

    def stop(*_):
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
        deadline = time.time() + 20
        for p in procs.values():
            try:
                p.wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                p.kill()
    signal.signal(signal.SIGTERM, lambda *_: (stop(), sys.exit(0)))
    signal.signal(signal.SIGINT, lambda *_: (stop(), sys.exit(0)))
    while True:
        for name, p in procs.items():
            if p.poll() is not None:
                say(f"{name} stopped (exit {p.returncode}); stopping the rest so the container restarts cleanly")
                stop()
                return p.returncode or 1
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
