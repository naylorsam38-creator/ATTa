#!/usr/bin/env python3
"""
repair_actions.py — the fixed set of repair actions the self-healing layer may take.

Every tier uses these and nothing else: known fixes (tier 1), the capability adapter
(tier 2) and the LLM (tier 3, which calls them as tools). That keeps every repair
bounded, logged and replayable. A fix the LLM finds is stored as a list of these
actions and replayed by tier 1 next time.

Hard limits, whoever calls:
  - Upstream app source is never touched. Writes go only to an app's .ui-capability overlay,
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


def action(description: str, properties: dict, mutating: bool = True, learnable: bool = True):
    """Register an action. `properties` is the JSON-schema of its arguments.
    learnable=False: the action's arguments are specific to one file's contents, so a fix that
    used it is never replayed on other builds as a known fix."""
    def wrap(fn):
        ACTIONS[fn.__name__] = {"fn": fn, "description": description, "mutating": mutating, "learnable": learnable,
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
        {"app": {"type": "string"}, "path": {"type": "string"}, "content": {"type": "string"}}, learnable=False)
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


def _json_mechanical_fix(text: str) -> str:
    """Safe, mechanical JSON repairs only: strip a BOM, // and /* */ comments, and trailing
    commas before } or ]. String contents are never altered. Anything needing a guess is left alone."""
    if text.startswith("\ufeff"):
        text = text[1:]
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if c == '"':
            in_str = True; out.append(c); i += 1; continue
        if text.startswith("//", i):
            j = text.find("\n", i); i = n if j < 0 else j; continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2); i = n if j < 0 else j + 2; continue
        if c == ",":
            k = i + 1
            while k < n and text[k] in " \t\r\n":
                k += 1
            if k < n and text[k] in "}]":
                i += 1; continue
        out.append(c); i += 1
    return "".join(out)


@action("Mechanically repair a JSON overlay file that won't parse (BOM, comments, trailing commas). "
        "Writes only if the result parses; the old file is backed up. Never guesses at content.",
        {"app": {"type": "string"}, "path": {"type": "string"}})
def repair_overlay_json(app: str, path: str) -> str:
    ui = ui_dir(app)
    dest = _inside(ui, path)
    rel = dest.relative_to(ui.resolve()).as_posix()
    if not rel.endswith(".json"):
        raise ActionError(f"{rel} is not a .json file")
    if not dest.is_file():
        raise ActionError(f"{rel} missing")
    raw = dest.read_text(errors="replace")
    try:
        json.loads(raw)
        return f"{rel} already parses; nothing changed"
    except json.JSONDecodeError:
        pass
    fixed = _json_mechanical_fix(raw)
    try:
        json.loads(fixed)
    except json.JSONDecodeError as e:
        raise ActionError(f"{rel} is not mechanically fixable ({e.msg} line {e.lineno}); escalating")
    return write_overlay_file(app, rel, fixed) + " (mechanical JSON repair)"


@action("Syntax-check every file in an app's .ui-capability overlay (Python, JSON, YAML, JavaScript, shell). "
        "Returns 'path: error' lines, or 'no syntax errors'. Use it to tell a simple slip from a real breakage.",
        {"app": {"type": "string"}}, mutating=False)
def syntax_check(app: str) -> str:
    import syntax_triage
    errs = syntax_triage.scan(ui_dir(app))
    return "\n".join(f"{e['path']}: {e['error']}" for e in errs) or "no syntax errors"


@action("Fix a small slip in one overlay file by replacing old_text (must occur exactly once) with new_text. "
        "The file is syntax-checked afterwards; if it still fails, the original is put back and nothing changes. "
        "Never capability-port/. The old version is backed up.",
        {"app": {"type": "string"}, "path": {"type": "string"},
         "old_text": {"type": "string"}, "new_text": {"type": "string"}}, learnable=False)
def patch_overlay_file(app: str, path: str, old_text: str, new_text: str) -> str:
    import syntax_triage
    ui = ui_dir(app)
    dest = _inside(ui, path)
    rel = dest.relative_to(ui.resolve()).as_posix()
    if rel.startswith("capability-port/") or rel.startswith(BACKUP_DIRNAME):
        raise ActionError("capability-port/ is never modified; use reinstall_app_overlay to restore it")
    if not dest.is_file():
        raise ActionError(f"{rel} missing")
    if not syntax_triage.has_checker(dest):
        raise ActionError(f"no syntax checker for {dest.suffix or 'this file type'}; can't verify a patch, so refusing")
    original = dest.read_text(errors="replace")
    count = original.count(old_text) if old_text else 0
    if count != 1:
        raise ActionError(f"old_text found {count} times in {rel}; it must match exactly once")
    patched = original.replace(old_text, new_text, 1)
    if len(patched.encode()) > MAX_WRITE:
        raise ActionError(f"result over {MAX_WRITE} bytes")
    before = syntax_triage.check_file(dest)
    bak = ui / BACKUP_DIRNAME / time.strftime("%Y%m%d-%H%M%S") / rel
    bak.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dest, bak)
    dest.write_text(patched)
    after = syntax_triage.check_file(dest)
    if after:
        shutil.copy2(bak, dest)  # put the original back: a bad patch never makes things worse
        raise ActionError(f"patch did not fix {rel} (still: {after}); original restored")
    return f"patched {rel}; syntax check now clean" + (f" (was: {before})" if before else "")


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


# ---------------------------------------------------------------- app runner (starting apps)
@action("Show how the app runner tried to start an app: every attempt, the rule it matched, and the evidence "
        "lines from the start error / container logs. Read this before changing anything for a 2 APP_UP failure.",
        {"app": {"type": "string"}}, mutating=False)
def runner_attempts(app: str) -> str:
    p = ROOT / "state" / "runner" / "results" / f"{safe_id(app)}.json"
    if not p.is_file():
        raise ActionError(f"no runner result for {app!r} yet")
    r = json.loads(p.read_text()).get("runner") or {}
    return json.dumps(r, indent=1, default=str)[:MAX_READ]


@action("List the ways the app runner found to run an app (its compose files, published images named in its own "
        "files, Dockerfiles), in the order it tries them.", {"app": {"type": "string"}}, mutating=False)
def runner_parts(app: str) -> str:
    import app_runner as ar
    return json.dumps(ar.parts(app, ar.app_dir(app)), indent=1)[:MAX_READ]


@action("Propose a run recipe for an app: how to start it. It is tried FIRST on the next qualification and kept only if the app passes all six stages with it. recipe_json is a JSON object: "
        '{"kind":"image","image":"owner/name:tag","port":8080,"env":{"KEY":"value"},"command":["serve"]} or '
        '{"kind":"compose","file":"<path inside the app>","env":{...}} or {"kind":"dockerfile","dockerfile":"<path>","context":"<dir>","port":80}. '
        "Images must exist in their registry; files must be inside the app. The next qualification run tests it.",
        {"app": {"type": "string"}, "recipe_json": {"type": "string"}}, learnable=False)
def set_run_recipe(app: str, recipe_json: str) -> str:
    import app_runner as ar
    try:
        r = json.loads(recipe_json)
    except json.JSONDecodeError as e:
        raise ActionError(f"recipe_json is not JSON: {e}")
    if not isinstance(r, dict) or r.get("kind") not in {"image", "compose", "dockerfile"}:
        raise ActionError("kind must be image, compose or dockerfile (reusable runtimes are found by the runner itself)")
    d = ar.app_dir(app)
    env = r.get("env") or {}
    if not isinstance(env, dict) or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)) for k in env):
        raise ActionError("env must be an object of VARIABLE_NAME: value")
    r["env"] = {str(k): str(v) for k, v in env.items()}
    if r["kind"] == "image":
        rc = subprocess.run(["docker", "manifest", "inspect", str(r.get("image", ""))], capture_output=True, timeout=60).returncode
        if rc != 0:
            raise ActionError(f"image {r.get('image')!r} does not exist in its registry")
    for key in ("file", "dockerfile"):
        if r["kind"] in ("compose", "dockerfile") and key in r:
            _inside(d, r[key])
            if not (d / r[key]).is_file():
                raise ActionError(f"{r[key]} is not a file in {app}")
    if r.get("command") is not None and not (isinstance(r["command"], list) and all(isinstance(c, str) for c in r["command"])):
        raise ActionError("command must be a list of strings")
    # Not saved as the app's recipe: stored as a CANDIDATE. The next qualification tries it first and
    # it becomes the recipe only if the app passes all six stages with it; otherwise it is discarded.
    keep = {k: v for k, v in r.items() if not str(k).startswith("_")}
    (ar.RECIPES / f"{ar.safe_id(app)}.candidate.json").write_text(json.dumps({**keep, "proposed_at": time.time(),
                                                                            "proposed_by": "self-healer"}, indent=2) + "\n")
    return (f"candidate recipe stored for {app}: {r['kind']} " + str(r.get("image") or r.get("file") or r.get("dockerfile"))
            + " (becomes the recipe only if the app passes the full check with it)")


@action("Teach the app runner a new failure rule for ALL apps: a regex matched against the start error and container logs, "
        "and the fix to apply: set_env (the regex must capture the variable as (?P<var>...)), copy_env, new_ports, prune, "
        "wait_longer, pick_command or next_part. Use when you recognise a general failure pattern the runner doesn't know.",
        {"rule_id": {"type": "string"}, "pattern": {"type": "string"}, "fix": {"type": "string"}})
def add_runner_rule(rule_id: str, pattern: str, fix: str) -> str:
    import app_runner as ar
    if fix not in {"set_env", "copy_env", "new_ports", "prune", "wait_longer", "pick_command", "next_part"}:
        raise ActionError(f"unknown fix {fix!r}")
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ActionError(f"pattern does not compile: {e}")
    if fix == "set_env" and "var" not in rx.groupindex:
        raise ActionError("a set_env rule must capture the variable name as (?P<var>...)")
    import rule_lifecycle
    with rule_lifecycle._Locked():
        rows = [x for x in ar.learned_rules() if x.get("id") != safe_id(rule_id)]
        rows.append({"id": safe_id(rule_id), "pattern": pattern, "fix": fix, **rule_lifecycle.new_fields()})
        rule_lifecycle.save(ar.LEARNED_RULES, rows)
    return f"runner rule {rule_id} saved ({fix})"


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
