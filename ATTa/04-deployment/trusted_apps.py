#!/usr/bin/env python3
"""
trusted_apps.py — v115: the apps an admin has allowed to reach the host (Docker socket, host bind mounts,
host network/pid). Replaces the old server-wide APP_BUILDER_ALLOW_HOST_ACCESS switch, which handed that
access to EVERY uploaded app at once.

Docker managers (Portainer, Coolify, ...) cannot work without the Docker socket, and the socket is root on
this server, so trusting an app is trusting whoever wrote it. The list is a root-only file written only
from the command line on the server; nothing an app or a web user sends can add to it. Trust never lets an
app read ATTa's own secrets: compose still runs with a clean environment, env_file/secrets/configs/build
contexts must still stay inside the app's folder, and the rendered configuration is still scanned.

Command line (run on the server as root):

  python3 trusted_apps.py list
  python3 trusted_apps.py trust APP --reason "Portainer needs the Docker socket"
  python3 trusted_apps.py untrust APP
"""
from __future__ import annotations
import argparse, getpass, json, os, re, stat, sys, tempfile, time
from pathlib import Path

# ===================== CONFIG — edit here, nothing below needs reading =====================
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
# The list itself. Only honoured while it is a regular file owned by TRUSTED_UID and writable by nobody else.
TRUSTED_FILE = ROOT / "state" / "trusted_apps.json"
# Root on a server. Not read from the environment: whoever sets the runner's environment is root already.
TRUSTED_UID = 0
# ==========================================================================================

APP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _safe(app: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(app).lower()).strip("-")


def load() -> dict:
    """{app: record}, or {} when the file is missing or cannot be vouched for (fails closed)."""
    try:
        st = os.lstat(TRUSTED_FILE)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != TRUSTED_UID or st.st_mode & 0o022 or st.st_nlink != 1:
            return {}
        d = json.loads(TRUSTED_FILE.read_text())
    except (OSError, ValueError):
        return {}
    apps = d.get("apps") if isinstance(d, dict) else None
    return {k: v for k, v in (apps or {}).items() if isinstance(v, dict) and APP_RE.match(k)}


def is_trusted(app: str) -> bool:
    return _safe(app) in load()


def _save(apps: dict) -> None:
    TRUSTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=TRUSTED_FILE.parent, prefix=".trusted.")
    with os.fdopen(fd, "w") as f:
        json.dump({"schema": "ATTA_TRUSTED_APPS.v1", "apps": apps}, f, indent=2, sort_keys=True); f.write("\n")
        f.flush(); os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, TRUSTED_FILE)


def trust(app: str, reason: str, by: str | None = None) -> dict:
    a = _safe(app)
    if not APP_RE.match(a):
        raise ValueError(f"bad app id {app!r}")
    if not (reason or "").strip():
        raise ValueError("say why this app needs the host (it is recorded)")
    apps = load()
    apps[a] = {"approved_by": by or os.environ.get("SUDO_USER") or getpass.getuser(),
               "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "reason": reason.strip()}
    _save(apps)
    return apps[a]


def untrust(app: str) -> bool:
    apps = load()
    gone = apps.pop(_safe(app), None) is not None
    _save(apps)
    return gone


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="apps an admin allows to reach the host")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    t = sub.add_parser("trust"); t.add_argument("app"); t.add_argument("--reason", required=True)
    u = sub.add_parser("untrust"); u.add_argument("app")
    a = ap.parse_args(argv)
    if a.cmd != "list" and os.geteuid() != TRUSTED_UID:
        print("error: run as root (sudo)", file=sys.stderr); return 1
    try:
        if a.cmd == "list":
            for k, v in sorted(load().items()):
                print(f"{k:<24} by {v.get('approved_by')} at {v.get('approved_at')}: {v.get('reason')}")
        elif a.cmd == "trust":
            r = trust(a.app, a.reason); print(f"trusted {_safe(a.app)} (by {r['approved_by']})")
        else:
            print(f"untrusted {_safe(a.app)}" if untrust(a.app) else f"{_safe(a.app)} was not trusted")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
