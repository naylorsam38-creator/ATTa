#!/usr/bin/env python3
"""
proxy_launch.py — v116: the one way an app's skin proxy (its .ui-capability/run-ui.sh) is started or stopped.

Before v116 the runner and the self-healer each ran `bash <library>/<app>/.ui-capability/run-ui.sh` themselves:
as root, with the whole service environment (session secret, API keys, Coolify token), on whatever file was
there, uploaded or LLM-written included. Now every start:

  1. verifies the overlay's code is byte-for-byte what ATTa's installer wrote (overlay_integrity.verify);
  2. copies the overlay to <ROOT>/proxy/<app>/ (the proxy never sees the library, which holds customers' .env);
  3. runs run-proxy.sh as the unprivileged proxy user (atta-proxy): through setpriv when this process is root,
     through `sudo -u atta-proxy` (one sudoers rule, one command) when it runs as its own service user;
     with an empty environment plus TARGET_URL, UI_PORT, APP_BUILDER_ROOT;
  4. copies the registration run-ui.sh wrote back into state/apps/, pointing at the library overlay as before.

On a laptop (not root, not under systemd, no proxy user) the proxy runs as the person themselves, still from
a verified copy with an empty environment. On a server that cannot drop to the proxy user it is refused.
"""
from __future__ import annotations
import grp, json, os, pwd, re, shutil, socket, stat, subprocess, time
from pathlib import Path

import overlay_integrity

# ===================== CONFIG — edit here, nothing below needs reading =====================
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
PROXY_BASE = ROOT / "proxy"
TARGETS = ROOT / "state" / "apps"
# The user every skin proxy runs as on a server (created by bootstrap.sh; no shell, no Docker, no library).
PROXY_USER = "atta-proxy"
# The root-owned copy of run-proxy.sh the sudoers rule names (installed by bootstrap.sh).
WRAPPER_INSTALLED = Path("/usr/local/lib/atta/run-proxy")
WRAPPER_LOCAL = Path(__file__).resolve().parent / "run-proxy.sh"
# Seconds a proxy gets to start listening and register itself.
START_WAIT = 20
# ==========================================================================================

APP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PROCS: dict[str, subprocess.Popen] = {}   # proxies this process started, reaped when stopped (no zombies)


class LaunchError(RuntimeError):
    pass


def safe_id(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-") or "app"


def _on_server() -> bool:
    return os.geteuid() == 0 or bool(os.environ.get("INVOCATION_ID"))


def _proxy_user():
    try:
        return pwd.getpwnam(PROXY_USER)
    except KeyError:
        return None


def _runner(args: list[str]) -> list[str]:
    """The command that runs run-proxy.sh <args> as the proxy user (or as ourselves on a laptop)."""
    u = _proxy_user()
    if u is None:
        if _on_server():
            raise LaunchError(f"user {PROXY_USER} does not exist; re-run `bash run` so the server creates it "
                              "(skin proxies never run as a service user)")
        return ["bash", str(WRAPPER_LOCAL)] + args
    if os.geteuid() == 0:
        return ["setpriv", f"--reuid={u.pw_uid}", f"--regid={u.pw_gid}", "--init-groups", "--no-new-privs",
                "bash", str(WRAPPER_INSTALLED if WRAPPER_INSTALLED.is_file() else WRAPPER_LOCAL)] + args
    if os.geteuid() == u.pw_uid:
        return ["bash", str(WRAPPER_LOCAL)] + args
    return ["sudo", "-n", "-u", PROXY_USER, str(WRAPPER_INSTALLED)] + args


def _clean_env() -> dict:
    return {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def proxy_dir(app: str) -> Path:
    return PROXY_BASE / safe_id(app)


def _prepare(app: str, ui_dir: Path) -> Path:
    """A fresh copy of the verified overlay, readable by the proxy user; run/ and state/apps/ writable by it."""
    dest = proxy_dir(app)
    PROXY_BASE.mkdir(parents=True, exist_ok=True)
    os.chmod(PROXY_BASE, 0o755)
    if _proxy_user() is not None:
        # The proxy user must be able to pass through every folder above its copy (and nothing more: the data
        # folder is 3771, traverse without listing). Say so plainly instead of letting the proxy fail.
        for d in [PROXY_BASE, *PROXY_BASE.parents]:
            if not os.stat(d).st_mode & 0o001:
                raise LaunchError(f"{d} is not passable by {PROXY_USER} (needs o+x; the data folder is 3771); "
                                  "bootstrap.sh sets this on a server")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(ui_dir, dest / ".ui-capability", symlinks=True,
                    ignore=shutil.ignore_patterns(overlay_integrity.BACKUP_DIRNAME))
    for dirpath, dirnames, filenames in os.walk(dest):
        os.chmod(dirpath, 0o755)
        for n in filenames:
            p = Path(dirpath) / n
            os.chmod(p, 0o755 if p.name == "run-ui.sh" else 0o644)
    u = _proxy_user()
    for sub in ("run", "state", "state/apps"):
        d = dest / sub
        d.mkdir(parents=True, exist_ok=True)
        if u is not None and os.geteuid() != u.pw_uid:
            gid = u.pw_gid
            try:
                os.chown(d, -1, gid)   # root, or a service user in the proxy user's group
            except PermissionError:
                raise LaunchError(f"cannot hand {d} to group {grp.getgrgid(gid).gr_name}; "
                                  f"the service user must be in that group (bootstrap.sh sets this)")
            os.chmod(d, 0o2770)
    return dest


def stop(app: str) -> None:
    d = proxy_dir(app)
    if not (d / ".ui-capability").is_dir():
        return
    try:
        subprocess.run(_runner(["stop", str(d)]), env=_clean_env(), capture_output=True, timeout=30)
    except (LaunchError, OSError, subprocess.TimeoutExpired):
        pass
    p = _PROCS.pop(safe_id(app), None)
    if p is not None:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def launch(app: str, ui_dir, target_url: str, port: int, log) -> dict:
    """Start app's proxy on `port` in front of target_url. Returns its registration. Raises LaunchError."""
    app = safe_id(app)
    ui = Path(ui_dir)
    if not APP_RE.match(app):
        raise LaunchError(f"bad app id {app!r}")
    if not re.fullmatch(r"http://127\.0\.0\.1:\d{1,5}/?", str(target_url)):
        raise LaunchError(f"target {target_url!r} is not a local app address")
    try:
        overlay_integrity.verify(app, ui)
    except overlay_integrity.IntegrityError as e:
        raise LaunchError(str(e))
    stop(app)
    if _port_open(port):
        raise LaunchError(f"the skin proxy port {port} is already taken by something else")
    d = _prepare(app, ui)
    p = subprocess.Popen(_runner(["start", str(d), app, str(target_url), str(port)]), env=_clean_env(),
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    _PROCS[app] = p
    reg = d / "state" / "apps" / f"{app}.json"
    deadline = time.time() + START_WAIT
    while time.time() < deadline:
        if _port_open(port) and reg.is_file():
            break
        if p.poll() not in (None, 0):
            raise LaunchError(f"skin proxy exited with code {p.returncode}")
        time.sleep(0.3)
    else:
        stop(app)
        raise LaunchError(f"skin proxy did not listen on {port} and register within {START_WAIT}s")
    try:
        t = json.loads(reg.read_text())
    except (OSError, ValueError):
        raise LaunchError("run-ui.sh did not register the app")
    # The registration names the library overlay (as before v116): the watcher and repairs work on that.
    t.update(app=app, app_id=app, ui_dir=str(ui), app_dir=str(ui.parent),
             proxy_url=f"http://127.0.0.1:{port}/", target_url=str(target_url))
    TARGETS.mkdir(parents=True, exist_ok=True)
    tmp = TARGETS / f".{app}.json.tmp"
    tmp.write_text(json.dumps(t, indent=2) + "\n"); os.replace(tmp, TARGETS / f"{app}.json")
    return t
