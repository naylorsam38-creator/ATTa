#!/usr/bin/env python3
"""
repair_actions.py — the fixed set of repair actions the self-healing layer may take.

Every tier uses these and nothing else: known fixes (tier 1), the capability adapter
(tier 2) and the LLM (tier 3, which calls them as tools). That keeps every repair
bounded, logged and replayable. A fix the LLM finds is stored as a list of these
actions and replayed by tier 1 next time.

Hard limits, whoever calls:
  - App source is never touched. Writes go only to an app's .ui-capability overlay,
    the target registrations, coolify_resources.json, and the build inbox.
  - capability-port/port.js is never modified (the dormant-hook rule). It can only be
    restored from the delivered package, byte for byte.
  - Secrets (.env, users.json, TEST_ACCOUNTS.txt) can't be read.
"""
from __future__ import annotations
import importlib.util, json, os, re, shutil, socket, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import builds

# ===================== CONFIG — edit here, nothing below needs reading =====================
ROOT = builds.ROOT
LIB = ROOT / "library"
PKG = ROOT / "package"
INBOX = ROOT / "inbox"
TARGET_DIR = ROOT / "state" / "apps"
# Non-git folders found where a library checkout should be are moved here, never deleted.
QUARANTINE = ROOT / "quarantine"
# Old versions of any overlay file the self-healer rewrites are kept here, inside the overlay.
BACKUP_DIRNAME = ".self-heal-backups"
# Largest file the LLM may read (bytes) and write (bytes).
MAX_READ = 20_000
MAX_WRITE = 200_000
# Seconds to wait for a restarted UI proxy to start listening.
PROXY_START_WAIT = 15
# Seconds allowed for installing the Playwright browser.
BROWSER_INSTALL_TIMEOUT = 900
# ==========================================================================================

SECRET_NAMES = {".env", "users.json", "TEST_ACCOUNTS.txt", "INITIAL_LOGIN.txt"}


class ActionError(RuntimeError):
    pass


ACTIONS: dict[str, dict] = {}


def action(description: str, properties: dict, mutating: bool = True):
    """Register an action. `properties` is the JSON-schema of its arguments."""
    def wrap(fn):
        ACTIONS[fn.__name__] = {"fn": fn, "description": description, "mutating": mutating,
                                "schema": {"type": "object", "properties": properties,
                                           "required": list(properties), "additionalProperties": False}}
        return fn
    return wrap


def run(name: str, args: dict) -> str:
    a = ACTIONS.get(name)
    if not a:
        raise ActionError(f"unknown action {name!r}")
    return str(a["fn"](**args))


# ---------------------------------------------------------------- helpers
def safe_id(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-")


def target(app: str) -> tuple[Path, dict]:
    """The registration file and contents for an app (by file name or its `app` field)."""
    for p in sorted(TARGET_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            d = {}
        if p.stem == safe_id(app) or d.get("app") == app or d.get("app_id") == app:
            return p, d
    raise ActionError(f"no target registration for app {app!r}")


def ui_dir(app: str) -> Path:
    try:
        _, t = target(app)
        if t.get("ui_dir"):
            return Path(t["ui_dir"])
    except ActionError:
        pass
    for d in LIB.glob("*/.ui-capability"):
        if safe_id(d.parent.name) == safe_id(app):
            return d
    raise ActionError(f"no .ui-capability overlay found for app {app!r}")


def _inside(base: Path, rel: str) -> Path:
    p = (base / rel).resolve()
    if p != base.resolve() and not str(p).startswith(str(base.resolve()) + os.sep):
        raise ActionError(f"path {rel!r} is outside {base}")
    return p


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _installer():
    """The delivered package's own installer, imported as a module (the capability adapter's logic)."""
    path = PKG / "out" / "install_all.py"
    if not path.is_file():
        raise ActionError(f"package installer missing at {path}; the build must be re-run first")
    spec = importlib.util.spec_from_file_location("app_builder_install_all", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- read-only (LLM context)
@action("Read a text file under the app-builder data folder (secrets excluded). path is relative to the data folder.",
        {"path": {"type": "string"}}, mutating=False)
def read_file(path: str) -> str:
    p = _inside(ROOT, path)
    if p.name in SECRET_NAMES or "secret" in p.name.lower():
        raise ActionError("that file holds secrets and can't be read")
    if not p.is_file():
        raise ActionError(f"{path} is not a file")
    b = p.read_bytes()
    return b[:MAX_READ].decode("utf-8", "replace") + ("\n…[truncated]" if len(b) > MAX_READ else "")


@action("List a folder under the app-builder data folder. path is relative to it ('.' for the top).",
        {"path": {"type": "string"}}, mutating=False)
def list_dir(path: str) -> str:
    p = _inside(ROOT, path)
    if not p.is_dir():
        raise ActionError(f"{path} is not a folder")
    rows = [f"{'d' if c.is_dir() else 'f'} {c.name}" for c in sorted(p.iterdir())[:200]]
    return "\n".join(rows) or "(empty)"


@action("Run the six-stage watcher check for one app right now and return its result.",
        {"app": {"type": "string"}}, mutating=False)
def watcher_check(app: str) -> str:
    import system_watcher
    _, t = target(app)
    return json.dumps(system_watcher.check(t), indent=1)


# ---------------------------------------------------------------- build layer
@action("Put a failed build's bundle back in the inbox so the pipeline runs it again.",
        {"build_id": {"type": "string"}})
def requeue_build(build_id: str) -> str:
    builds.path(build_id)  # validates the id
    failed, live = INBOX / f"{build_id}.failed.zip", INBOX / f"{build_id}.zip"
    if live.exists():
        return "already queued"
    if not failed.exists():
        raise ActionError(f"no failed bundle for {build_id} in the inbox")
    failed.rename(live)
    return "requeued"


@action("Move a non-git folder that blocks a library checkout into the quarantine folder (never deletes).",
        {"name": {"type": "string"}})
def quarantine_library_dir(name: str) -> str:
    if "/" in name or name in ("", ".", ".."):
        raise ActionError("name must be a single library folder name")
    src = LIB / name
    if not src.is_dir():
        raise ActionError(f"library/{name} does not exist")
    if (src / ".git").exists():
        raise ActionError(f"library/{name} is a git checkout; refusing to move it")
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    dest = QUARANTINE / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.move(str(src), str(dest))
    return f"moved to {dest}"


# ---------------------------------------------------------------- capability overlay (tier 2 + LLM)
@action("Reinstall one app's .ui-capability overlay from the delivered package (skin, UI bridge, capability port).",
        {"app": {"type": "string"}})
def reinstall_app_overlay(app: str) -> str:
    ui = ui_dir(app)
    appdir = ui.parent
    inst = _installer()
    category, _ = inst.resolve_category(appdir.name)
    variant = inst.choose_variant(category)
    inst.install_one(appdir, appdir.name, category, variant, "self-heal-reinstall")
    return f"reinstalled {appdir.name} overlay ({category}/{variant})"


@action("Make an app's run-ui.sh launcher executable again.", {"app": {"type": "string"}})
def chmod_launcher(app: str) -> str:
    f = ui_dir(app) / "run-ui.sh"
    if not f.is_file():
        raise ActionError(f"{f} missing")
    f.chmod(0o755)
    return "run-ui.sh is executable"


@action("Point an app's target registration at its current overlay folder when the registered one has moved or vanished.",
        {"app": {"type": "string"}})
def reregister_target(app: str) -> str:
    p, t = target(app)
    current = Path(t.get("ui_dir", ""))
    if current.is_dir() and (current / "deployment.json").is_file():
        return "registration already points at a live overlay"
    candidates = [Path(t["app_dir"]) / ".ui-capability"] if t.get("app_dir") else []
    candidates += [d for d in LIB.glob("*/.ui-capability") if safe_id(d.parent.name) == safe_id(app)]
    for c in candidates:
        if (c / "deployment.json").is_file():
            t["ui_dir"] = str(c); t["app_dir"] = str(c.parent)
            p.write_text(json.dumps(t, indent=2) + "\n")
            return f"ui_dir now {c}"
    raise ActionError(f"no installed overlay found for {app!r}")


@action("Start an app's UI proxy (run-ui.sh) when nothing is listening on its registered proxy port.",
        {"app": {"type": "string"}})
def start_proxy(app: str) -> str:
    _, t = target(app)
    ui = Path(t["ui_dir"]); port = urlparse(t["proxy_url"]).port
    if not port:
        raise ActionError("registered proxy_url has no port")
    if _port_open(port):
        return f"something is already listening on {port}"
    log = (ROOT / "state" / f"proxy-{safe_id(app)}.log").open("a")
    env = {**os.environ, "TARGET_URL": t["target_url"], "UI_PORT": str(port), "APP_BUILDER_ROOT": str(ROOT)}
    subprocess.Popen(["bash", str(ui / "run-ui.sh")], env=env, stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(PROXY_START_WAIT * 2):
        if _port_open(port):
            return f"proxy listening on {port}"
        time.sleep(0.5)
    raise ActionError(f"proxy did not start listening on {port}; see state/proxy-{safe_id(app)}.log")


@action("Write one file inside an app's .ui-capability overlay (never capability-port/). The old version is backed up.",
        {"app": {"type": "string"}, "path": {"type": "string"}, "content": {"type": "string"}})
def write_overlay_file(app: str, path: str, content: str) -> str:
    ui = ui_dir(app)
    dest = _inside(ui, path)
    rel = dest.relative_to(ui.resolve()).as_posix()
    if rel.startswith("capability-port/") or rel.startswith(BACKUP_DIRNAME):
        raise ActionError("capability-port/ is never modified; use reinstall_app_overlay to restore it")
    if len(content.encode()) > MAX_WRITE:
        raise ActionError(f"content over {MAX_WRITE} bytes")
    if dest.exists():
        bak = ui / BACKUP_DIRNAME / time.strftime("%Y%m%d-%H%M%S") / rel
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, bak)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    return f"wrote {rel} ({len(content)} chars)"


# ---------------------------------------------------------------- QUALIFIED gate
@action("Install the Playwright Chromium browser the watcher's stage 6 needs.", {})
def install_browser() -> str:
    try:
        import playwright  # noqa: F401
    except ImportError:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--break-system-packages", "playwright"],
                           capture_output=True, text=True, timeout=BROWSER_INSTALL_TIMEOUT)
        if r.returncode:
            raise ActionError("pip install playwright failed: " + r.stderr[-500:])
    r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                       capture_output=True, text=True, timeout=BROWSER_INSTALL_TIMEOUT)
    if r.returncode:
        raise ActionError("playwright install chromium failed: " + r.stderr[-500:])
    return "chromium installed"


# ---------------------------------------------------------------- Coolify hand-off
@action("Ask Coolify for an application whose name matches the app and, if exactly one matches, record its UUID.",
        {"app": {"type": "string"}})
def lookup_coolify_uuid(app: str) -> str:
    import coolify_handoff as ch
    if not ch.COOLIFY_URL or not ch.COOLIFY_TOKEN:
        raise ActionError("Coolify is not configured")
    req = Request(f"{ch.COOLIFY_URL}/api/v1/applications",
                  headers={"Authorization": f"Bearer {ch.COOLIFY_TOKEN}", "Accept": "application/json"})
    with urlopen(req, timeout=ch.TIMEOUT) as r:
        items = json.loads(r.read())
    items = items.get("data", items) if isinstance(items, dict) else items
    hits = [i for i in items if isinstance(i, dict) and str(i.get("name", "")).strip().lower() == app.strip().lower()]
    if len(hits) != 1:
        raise ActionError(f"{len(hits)} Coolify applications named {app!r}; need exactly one")
    res = {}
    try:
        res = json.loads(ch.RESOURCES_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    res.setdefault("apps", {})[app] = hits[0]["uuid"]
    ch.RESOURCES_FILE.write_text(json.dumps(res, indent=2) + "\n")
    return f"{app} -> {hits[0]['uuid']}"


@action("Retry the Coolify hand-off for a qualified build now, instead of waiting for the next scheduled retry.",
        {"build_id": {"type": "string"}})
def redispatch(build_id: str) -> str:
    import coolify_handoff as ch
    c = ch.dispatch(build_id)
    return f"coolify status {c.get('status')}"
