#!/usr/bin/env python3
"""
app_runner.py — starts each library app so the six-stage watcher has something real to check.

Before this, nothing started the apps, so every build ended NOT_QUALIFIED / NO_TARGETS.

For each app, one at a time:
  1. FIND A WAY TO RUN IT (the "parts"), cheapest and most faithful first:
       a. a recipe that already worked for this app (state/runner/recipes/<app>.json) — it sticks
       b. the app's own compose file, when it only uses published images
       c. the app's own published image, named in its own README/compose/CI files and confirmed
          to exist in the registry
       d. the app's own compose file that builds from source
       e. the app's own Dockerfile (build from source)
  2. START IT in Docker (an app that needs a newer Go/Node than the server has still runs:
     the container carries its own toolchain).
  3. ADAPT on failure, without swapping the part: the start error and the container's own logs
     are read against RULES below. A simple, understood problem (missing .env, a required
     variable with no value, a port clash, a slow first boot, a full disk) is fixed and the SAME
     part is retried. Only when the part itself can't work (image doesn't exist, needs a database
     it doesn't bring, wrong CPU type, killed for memory) does it move to the next part.
  4. When it answers HTTP: start its skin proxy (.ui-capability/run-ui.sh), register the target,
     run the watcher (stages 1–6, stage 6 = real browser), record the result.
  5. Stop it again (unless KEEP_RUNNING), so the next app has the machine.

A recipe that worked is saved, so next time step 1a runs it straight away. Nothing is guessed:
every image is checked against the registry, every port is probed, every result comes from the
real watcher. If every part is spent, the app's result says exactly what was tried and why each
failed; the self-healer (known fixes -> adapter -> LLM -> human) takes it from there, and a recipe
the LLM finds is saved here like any other.

Command line (for a person):
  python3 app_runner.py check <app>     start it, check it, stop it (prints the watcher result)
  python3 app_runner.py up <app>        start it and leave it running
  python3 app_runner.py down <app>      stop it
  python3 app_runner.py all             check every app in the library
  python3 app_runner.py recipe <app>    show the saved recipe
"""
from __future__ import annotations
import json, os, re, secrets, shutil, signal, socket, subprocess, sys, time
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
import app_env

# ============================ RULES / CONFIG — edit here, nothing below needs reading ============================
# Seconds an app gets to answer HTTP after it starts. Raise for slow machines; lower = faster give-up.
BOOT_TIMEOUT = int(os.environ.get("APP_BUILDER_BOOT_TIMEOUT", "240"))
# If the app is still visibly starting when BOOT_TIMEOUT runs out (its logs are still moving), it gets
# more time, up to this many seconds in total. Heavy apps (Appsmith, Supabase) need this on first boot.
BOOT_TIMEOUT_MAX = int(os.environ.get("APP_BUILDER_BOOT_TIMEOUT_MAX", "900"))
# Most start attempts for one app, across all its parts and fixes. Stops a fix loop from running forever.
MAX_ATTEMPTS = int(os.environ.get("APP_BUILDER_RUN_ATTEMPTS", "14"))
# How many different images to try from the app's own files before moving on to building from source.
MAX_IMAGE_CANDIDATES = int(os.environ.get("APP_BUILDER_RUN_IMAGE_CANDIDATES", "4"))
# true = apps stay running after their check (a server with room to spare). false = stopped after the
# check so the next app has the memory. Qualified apps are run for real by Coolify either way.
KEEP_RUNNING = os.environ.get("APP_BUILDER_KEEP_RUNNING", "false").lower() in {"1", "true", "yes"}
# true = delete an app's downloaded images after its check (small disks). false = keep them (faster reruns).
PRUNE_IMAGES = os.environ.get("APP_BUILDER_PRUNE_IMAGES", "false").lower() in {"1", "true", "yes"}
# true = allow building from source (compose `build:` / Dockerfile) when no published image works.
# Slow and heavy; false = those apps stop at "no published image" and go to the self-healer.
ALLOW_SOURCE_BUILD = os.environ.get("APP_BUILDER_ALLOW_SOURCE_BUILD", "true").lower() in {"1", "true", "yes"}
# Seconds allowed for downloading images / building from source / `compose up`.
PULL_TIMEOUT = int(os.environ.get("APP_BUILDER_PULL_TIMEOUT", "1500"))
BUILD_TIMEOUT = int(os.environ.get("APP_BUILDER_BUILD_TIMEOUT", "2400"))
# Host ports handed to apps come from this range (never 80/443/8787, never the skin proxies' 8100-9099).
HOST_PORTS = range(int(os.environ.get("APP_BUILDER_HOST_PORT_FROM", "20000")), int(os.environ.get("APP_BUILDER_HOST_PORT_TO", "29999")))
# Compose files whose path matches this are never used to run the app (tests, dev setups, examples, add-ons).
COMPOSE_SKIP = re.compile(r"(^|[/._-])(test|tests|e2e|example|examples|sample|samples|ci|debug|monitoring|observability|"
                          r"windows|devcontainer|benchmark|otel|nightly)([/._-]|$)", re.I)
# Compose files whose path matches this are tried AFTER the others (dev setups still beat nothing).
COMPOSE_LATE = re.compile(r"(^|[/._-])(dev|devenv|development|local|full-stack|build)([/._-]|$)", re.I)
# When an image stops and prints its help, the first of these words its help lists is the command it's given.
SERVER_COMMANDS = ["standalone", "server", "serve", "webserver", "web", "start", "run", "up", "daemon"]
# v114 container isolation. Uploaded apps are untrusted code on the same server as ATTa, so every
# container starts with: no privilege gain (setuid binaries can't raise it), a process cap, a CPU cap, and a
# reduced set of kernel capabilities. An app that fails with "Operation not permitted" is retried once with
# Docker's default capability set (rule container.caps); the other limits stay. HARDEN=false turns all of it off.
HARDEN = os.environ.get("APP_BUILDER_HARDEN", "true").lower() in {"1", "true", "yes"}
PIDS_LIMIT = int(os.environ.get("APP_BUILDER_PIDS_LIMIT", "1024"))
CPUS = os.environ.get("APP_BUILDER_CPUS", "2.0")
# Kept after dropping ALL: what ordinary images need to start (entrypoints that chown data folders and then
# switch user, web servers binding port 80). Left out vs Docker's default: NET_RAW, MKNOD, SYS_CHROOT,
# SETFCAP, AUDIT_WRITE.
SAFE_CAPS = ["CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "SETGID", "SETUID", "SETPCAP", "NET_BIND_SERVICE"]
# A compose file may not hand its containers the host. These service keys are removed, and host bind mounts
# outside the app's own folder are dropped (the Docker socket above all: it is root on this server), except the
# read-only time-zone files below. ALLOW_HOST_ACCESS=true restores them for apps an admin trusts (Portainer,
# Coolify and other Docker managers need the socket and will not pass without it).
ALLOW_HOST_ACCESS = os.environ.get("APP_BUILDER_ALLOW_HOST_ACCESS", "false").lower() in {"1", "true", "yes"}
HOST_KEYS = ("privileged", "devices", "device_cgroup_rules", "cgroup_parent", "userns_mode")
HOST_MODES = ("network_mode", "pid", "ipc", "uts")
HOST_BIND_OK = ("/etc/localtime", "/etc/timezone", "/usr/share/zoneinfo")
# Largest share of this machine's memory one app's containers may each use (0.6 = 60%). A heavy app is
# then stopped by Docker inside its own box instead of taking the whole server down with it.
APP_MEMORY_SHARE = float(os.environ.get("APP_BUILDER_APP_MEMORY_SHARE", "0.6"))
# If the machine's free memory drops below this share while an app starts, the app is stopped (HOST_MEMORY).
HOST_MEMORY_FLOOR = float(os.environ.get("APP_BUILDER_HOST_MEMORY_FLOOR", "0.08"))
# Seconds an app must keep answering (no 5xx) after it first answers, before it is handed to the watcher.
# Apps like Appsmith answer the front page before their backend is ready.
SETTLE_SECONDS = int(os.environ.get("APP_BUILDER_SETTLE_SECONDS", "30"))
# When the watcher sees a 5xx (the app still warming up), wait this long and check again, this many times.
WARMUP_RECHECK_WAIT = int(os.environ.get("APP_BUILDER_WARMUP_RECHECK_WAIT", "60"))
WARMUP_RECHECKS = int(os.environ.get("APP_BUILDER_WARMUP_RECHECKS", "3"))
# Docker Hub limits anonymous downloads. First fix: send Docker Hub downloads through this mirror
# (Google's public Docker Hub cache). Blank = don't. Second fix: log in with DOCKERHUB_USERNAME /
# DOCKERHUB_TOKEN from .env (a free account has a much higher limit). Last: wait this many seconds.
REGISTRY_MIRROR = os.environ.get("APP_BUILDER_REGISTRY_MIRROR", "https://mirror.gcr.io")
RATE_LIMIT_WAIT = int(os.environ.get("APP_BUILDER_RATE_LIMIT_WAIT", "600"))
# How many apps are started and checked at the same time. 0 (the default) = EVERY app at once, with
# nothing held back: no memory gate, no disk gate, no download queue. Set a number only to cap it.
PARALLEL = int(os.environ.get("APP_BUILDER_RUN_PARALLEL", "0"))
# An app that isn't healthy and prints nothing new for this many seconds has stalled: stop waiting.
STALL_SECONDS = int(os.environ.get("APP_BUILDER_STALL_SECONDS", "240"))
# true = when the app's own folder has no way to run it that works, look at what its upstream publishes
# (its docs, its owner's deployment repos, its owner's Docker Hub images) before building from source or
# asking the AI. false = skip straight to building from source.
UPSTREAM_SEARCH = os.environ.get("APP_BUILDER_UPSTREAM_SEARCH", "true").lower() in {"1", "true", "yes"}
# Most seconds the runner spends trying to start ONE app, across all its parts and fixes, before it
# stops and hands the app to the self-healer / a person. The per-app limit that stops endless looping.
APP_TIME_BUDGET = int(os.environ.get("APP_BUILDER_APP_TIME_BUDGET", "3600"))
# Ports tried when an image doesn't say which port it listens on.
COMMON_PORTS = [80, 8080, 3000, 8000, 5000, 5173, 4000, 9000, 8081, 3001]
# Registries checked for an image named only by owner/repo in the app's files.
REGISTRY_PREFIXES = ["", "ghcr.io/"]
# ==============================================================================================================

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
LIB = ROOT / "library"
RUNNER = ROOT / "state" / "runner"
RECIPES = RUNNER / "recipes"
RESULTS = RUNNER / "results"
WORK = RUNNER / "work"
LEARNED_RULES = RUNNER / "learned_rules.json"
TARGETS = ROOT / "state" / "apps"
for _p in (RECIPES, RESULTS, WORK, TARGETS):
    _p.mkdir(parents=True, exist_ok=True)
_HTTP = build_opener(ProxyHandler({}))   # local probes never go through an outbound proxy


# ------------------------------------------------------------------ what a failure means, and what to do
# Each rule: a pattern searched in the start error + container logs, and what to do:
#   set_env        the app says a variable has no value -> give it a generated one, retry the SAME part
#   copy_env       the compose file wants a .env that isn't there -> copy the app's own example, retry
#   new_ports      a port clash -> hand out fresh host ports, retry
#   prune          disk full -> clear unused Docker data, retry
#   wait_longer    it's still coming up -> more time (up to BOOT_TIMEOUT_MAX)
#   next_part      this part can't work here -> move to the next way of running the app
# Rules are tried in order; the first match wins. learned_rules.json (same shape) is checked first.
RULES = [
    {"id": "compose.required_var", "pattern": r"required variable (?P<var>[A-Za-z_][A-Za-z0-9_]*) is missing a value", "fix": "set_env"},
    {"id": "compose.env_file_missing", "pattern": r"env file (?P<path>\S+?) not found|Couldn't find env file|failed to read .*\.env", "fix": "copy_env"},
    {"id": "port.clash", "pattern": r"port is already allocated|address already in use|bind: address already in use", "fix": "new_ports"},
    # The compose file asks for higher process limits than this server allows (rootless / locked-down Docker).
    {"id": "server.rlimit", "pattern": r"error setting rlimits?|setrlimit.*operation not permitted", "fix": "drop_ulimits"},
    # The server's kernel has no IPv6 and the app listens on "::" (MariaDB, Coolify's nginx). Nothing is
    # wrong with the app; the server needs IPv6 enabled (normal on AWS). Reported as its own reason.
    {"id": "host.no_ipv6", "pattern": r"Address family not supported by protocol|errno: 97|\(97: Address family", "fix": "next_part"},
    {"id": "compose.invalid", "pattern": r"invalid compose project|yaml: (line|unmarshal)|services\.\S+ (Additional property|must be)", "fix": "next_part"},
    {"id": "disk.full", "pattern": r"no space left on device", "fix": "prune"},
    # Docker Hub's anonymous pull limit. Not a missing image: nothing is wrong with the part.
    {"id": "registry.rate_limit", "pattern": r"429 Too Many Requests|toomanyrequests|You have reached your (unauthenticated )?pull rate limit",
     "fix": "rate_limit"},
    # Started before its own database was ready and gave up (Appsmith + embedded MongoDB on a slow
    # machine). Nothing is wrong with the part: restart it now the database is up.
    {"id": "app.dependency_not_ready", "pattern": r"NotPrimaryOrSecondary|node is not in primary or recovering state|"
                                                  r"the database system is starting up|Error starting ApplicationContext|"
                                                  r"MongoServerSelectionError|Server selection timed out",
     "fix": "restart"},
    {"id": "image.missing", "pattern": r"pull access denied|manifest unknown|repository does not exist|not found: manifest|"
                                       r"requested access to the resource is denied|failed to resolve reference", "fix": "next_part"},
    {"id": "image.arch", "pattern": r"exec format error|no matching manifest for linux", "fix": "next_part"},
    {"id": "build.failed", "pattern": r"failed to solve|failed to compute cache key|executor failed running|ERROR \[.*\] RUN", "fix": "next_part"},
    {"id": "app.oom", "pattern": r"OOMKilled|exit(?:ed)? \(?137\)?|Killed\s*$", "fix": "next_part"},
    # v112: a web app that refuses the Host header it is probed with (Homepage's HOMEPAGE_ALLOWED_HOSTS,
    # Django's ALLOWED_HOSTS, Rails host authorization) answers 4xx JSON/text and names the allow-list
    # variable in its own log. That is not a service and not "up": set the variable to * and start again.
    {"id": "app.host_rejected", "pattern": r"(?i:host validation failed|DisallowedHost|Invalid HTTP_HOST header|Blocked hosts?|"
                                           r"host(?:name)? [^\n]{0,40}?not (?:allowed|permitted)|not in ALLOWED_HOSTS)"
                                           r"[^\n]{0,300}?(?P<var>(?:[A-Z][A-Z0-9_]*_)?(?:ALLOWED|TRUSTED)_HOSTS?[A-Z0-9_]*)", "fix": "set_env"},
    # The app itself names a variable it needs. Only UPPER_CASE names, so prose never matches.
    {"id": "app.env_required", "pattern": r"\b(?P<var>[A-Z][A-Z0-9_]{2,})\b[\"'`]?\s+(?:environment variable\s+)?"
                                          r"(?:must be (?:set|provided|defined)|is (?:required|not set|missing|undefined|not defined))", "fix": "set_env"},
    {"id": "app.env_specify", "pattern": r"(?:must|You must)\s+specify\s+(?P<var>[A-Z][A-Z0-9_]{2,})", "fix": "set_env"},
    {"id": "app.env_required2", "pattern": r"(?:missing|required|please set|set the)\s+(?:the\s+)?(?:env(?:ironment)?\s+var(?:iable)?s?\s*:?\s*)"
                                           r"[\"'`]?(?P<var>[A-Z][A-Z0-9_]{2,})", "fix": "set_env"},
    # Needs a database this part doesn't bring: a part that brings one (the app's compose) is the fix.
    {"id": "app.needs_database", "pattern": r"(ECONNREFUSED|[Cc]onnection refused|could not connect|Can't connect|getaddrinfo|"
                                            r"ENOTFOUND|Name or service not known)[^\n]{0,120}(5432|3306|6379|27017|postgres|mysql|"
                                            r"mariadb|redis|mongo|db\b)", "fix": "next_part"},
    # The image is a command-line tool that printed its usage and stopped: it needs to be told what to run.
    # The fix reads the tool's OWN help text and picks the subcommand that starts a server.
    {"id": "cli.needs_command", "pattern": r"arguments are required|[Uu]sage:\s+\S+.*(COMMAND|<command>|\[command\])|"
                                            r"Error: [Mm]issing command|no command specified", "fix": "pick_command"},
    # v112: the app fails on a folder/file that does not exist inside its container (ENOENT on /app/config/...):
    # a folder it expects mounted. Give it an anonymous volume there and start again (image/dockerfile parts).
    {"id": "container.missing_path", "pattern": r"ENOENT[^\n]{0,120}?['\"](?P<path>/[^'\"\s]+)['\"]|"
                                                r"ENOENT[\s\S]{0,200}?\bpath:\s*['\"](?P<path3>/[^'\"\s]+)['\"]|"
                                                r"[Nn]o such file or directory[^\n]{0,40}?['\"](?P<path2>/[^'\"\s]+)['\"]", "fix": "add_volume"},
    # v114 (after the specific rules, so they win): the app needs a kernel capability the hardened start leaves out. Retried once with Docker's defaults.
    {"id": "container.caps", "pattern": r"[Oo]peration not permitted|\bEPERM\b|(?:setgroups|setgid|setuid|chown|capset)[^\n]{0,60}(?:[Pp]ermission denied|not permitted)", "fix": "relax_caps"},
    {"id": "host.memory", "pattern": r"__HOST_MEMORY__", "fix": "next_part"},
    {"id": "app.no_page", "pattern": r"__NO_APP_PAGE__", "fix": "next_part"},
    {"id": "container.exited", "pattern": r"__EXITED__", "fix": "next_part"},
    {"id": "app.stalled", "pattern": r"__STALLED__", "fix": "next_part"},
    {"id": "app.still_starting", "pattern": r"__STILL_STARTING__", "fix": "wait_longer"},
]


def learned_rules() -> list[dict]:
    """Live learned runner rules, read under the lifecycle lock."""
    import rule_lifecycle
    return rule_lifecycle.load(LEARNED_RULES)


def diagnose(text: str, skip: tuple = ()) -> dict | None:
    # v111a: a learned rule is PENDING until a full-catalogue run completes after it was learned
    # (rule_lifecycle.py). Pending rules are not consulted.
    learned = [r for r in learned_rules() if r.get("state", "candidate") != "pending"]
    for r in learned + RULES:
        if r.get("id") in skip:
            continue
        try:
            m = re.search(r["pattern"], text or "", re.M)
        except re.error:
            continue
        if m:
            if r in learned:
                import rule_lifecycle
                rule_lifecycle.record(LEARNED_RULES, r["id"], "fired")
                return {"rule": r["id"], "fix": r["fix"], "learned": True, **{k: v for k, v in m.groupdict().items() if v}}
            return {"rule": r["id"], "fix": r["fix"], **{k: v for k, v in m.groupdict().items() if v}}
    return None


# ------------------------------------------------------------------ small helpers
def safe_id(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-") or "app"


def sh(cmd, timeout=120, env=None, cwd=None) -> tuple[int, str]:
    # v115: a docker/compose command never inherits ATTa's service environment (its secrets). Compose
    # reads its environment for ${NAME} and bare `environment: [NAME]`; the CLI needs only BASE_ENV.
    if env is None and cmd and cmd[0] == "docker":
        env = app_env.runner_env()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out + f"\n[timed out after {timeout}s]"
    except FileNotFoundError as e:
        return 127, str(e)


def docker_ok() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "Docker is not installed on this server"
    rc, out = sh(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
    if rc == 0:
        return True, ""
    # Known fix: the Docker service is installed but not running. Start it, then check again.
    if shutil.which("systemctl") and Path("/run/systemd/system").is_dir():
        sh(["systemctl", "start", "docker"], timeout=90)
    elif os.geteuid() == 0 and shutil.which("dockerd"):
        subprocess.Popen(["dockerd"], stdout=(RUNNER / "dockerd.log").open("a"), stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(30):
        rc, out2 = sh(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
        if rc == 0:
            return True, "docker was not running; started it"
        time.sleep(2)
    return False, out.strip()


def fix_rate_limit(notes: list[str]) -> bool:
    """Docker Hub said "too many requests". Try, in order: add the registry mirror to Docker's
    config and restart Docker; log in to Docker Hub; wait. True if something was done."""
    cfg = Path("/etc/docker/daemon.json")
    try:
        cur = json.loads(cfg.read_text()) if cfg.is_file() else {}
    except (OSError, ValueError):
        cur = None   # a daemon.json we can't read is never overwritten
    others_running = len(_RUNNING) > 1   # restarting Docker would stop the other apps mid-check
    if REGISTRY_MIRROR and cur is not None and REGISTRY_MIRROR not in (cur.get("registry-mirrors") or []) and os.geteuid() == 0 \
            and not others_running:
        cur["registry-mirrors"] = (cur.get("registry-mirrors") or []) + [REGISTRY_MIRROR]
        cfg.parent.mkdir(parents=True, exist_ok=True)
        if cfg.is_file():
            shutil.copy2(cfg, cfg.with_suffix(".json.atta-backup"))
        cfg.write_text(json.dumps(cur, indent=2) + "\n")
        if shutil.which("systemctl") and Path("/run/systemd/system").is_dir():
            sh(["systemctl", "restart", "docker"], timeout=120)
        else:
            sh(["pkill", "-x", "dockerd"], timeout=20); time.sleep(3)
        ok, _ = docker_ok()
        notes.append(f"Docker Hub rate limit: added registry mirror {REGISTRY_MIRROR} to {cfg} and restarted Docker"
                     + ("" if ok else " (Docker did not come back!)"))
        return ok
    user, token = os.environ.get("DOCKERHUB_USERNAME"), os.environ.get("DOCKERHUB_TOKEN")
    if user and token and not getattr(fix_rate_limit, "_logged_in", False):
        r = subprocess.run(["docker", "login", "-u", user, "--password-stdin"], input=token, text=True, capture_output=True, timeout=60)
        fix_rate_limit._logged_in = r.returncode == 0
        notes.append("Docker Hub rate limit: logged in as " + user + ("" if r.returncode == 0 else " (login FAILED)"))
        if r.returncode == 0:
            return True
    notes.append(f"Docker Hub rate limit: waiting {RATE_LIMIT_WAIT}s for it to reset")
    time.sleep(RATE_LIMIT_WAIT)
    return True


import threading
_PORT_LOCK = threading.Lock()
_HANDED_OUT: set[int] = set()   # ports given to an app in this process (parallel apps never share one)


def free_port(taken: set[int]) -> int:
    with _PORT_LOCK:
        return _free_port(taken)


def _free_port(taken: set[int]) -> int:
    for p in HOST_PORTS:
        if p in taken or p in _HANDED_OUT:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p)); taken.add(p); _HANDED_OUT.add(p); return p
            except OSError:
                continue
    raise RuntimeError("no free host port in HOST_PORTS")


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_probe(port: int, path: str = "/") -> tuple[int | None, str]:
    try:
        with _HTTP.open(Request(f"http://127.0.0.1:{port}{path}", headers={"Accept": "text/html,*/*"}), timeout=6) as r:
            return r.status, r.headers.get("Content-Type", "")
    except HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else ""
    except (URLError, OSError, ValueError):
        return None, ""


def _meminfo() -> tuple[int, int]:
    """(total, available) bytes of this machine's memory."""
    try:
        t = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)
        kb = lambda k: int(t[k].strip().split()[0]) * 1024
        return kb("MemTotal"), kb("MemAvailable")
    except (OSError, KeyError, ValueError):
        return 0, 0


def mem_limit() -> str | None:
    total, _ = _meminfo()
    return f"{int(total * APP_MEMORY_SHARE / 1024 / 1024)}m" if total and APP_MEMORY_SHARE > 0 else None


def host_memory_low() -> bool:
    total, avail = _meminfo()
    return bool(total) and avail < total * HOST_MEMORY_FLOOR


def app_dir(app: str) -> Path:
    d = LIB / app
    if d.is_dir():
        return d
    for x in LIB.iterdir():
        if x.is_dir() and safe_id(x.name) == safe_id(app):
            return x
    raise FileNotFoundError(f"{app} is not in the library")


def intake_ledger(d: Path) -> dict:
    try:
        return json.loads((d / ".atta-intake.json").read_text())
    except (OSError, ValueError):
        return {}


def profile(d: Path) -> str:
    p = intake_ledger(d).get("qualification")
    return p if p in {"web", "service", "system", "package"} else "web"


def exclude_from_git(d: Path, rel: str) -> None:
    ex = d / ".git" / "info" / "exclude"
    try:
        if not (d / ".git").is_dir():
            return
        ex.parent.mkdir(parents=True, exist_ok=True)
        t = ex.read_text() if ex.exists() else ""
        if rel not in t.split():
            ex.write_text(t + ("" if t.endswith("\n") or not t else "\n") + rel + "\n")
    except OSError:
        pass


def gen_value(var: str) -> str:
    v = var.upper()
    if "ALLOWED_HOST" in v or "TRUSTED_HOST" in v:
        return "*"                       # every host: the probe port changes on every attempt
    if any(k in v for k in ("PORT",)):
        return "8080"
    if any(k in v for k in ("EMAIL", "MAIL_FROM")):
        return "admin@example.com"
    if v.endswith(("_URL", "_HOST", "_ORIGIN", "_DOMAIN")) or "URL" in v:
        return "http://localhost"
    if any(k in v for k in ("USER", "USERNAME", "NAME")):
        return "atta"
    return secrets.token_hex(24)


# ------------------------------------------------------------------ finding the parts
IMG = r"((?:[a-z0-9.-]+\.[a-z]{2,}(?::\d+)?/)?[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*){0,2})(?::([A-Za-z0-9._-]+))?"
IMG_PATTERNS = [re.compile(r"docker\s+(?:container\s+)?run\b[^\n`]*?\s" + IMG + r"\s*(?:$|\n|`|\\)", re.M),
                re.compile(r"^\s*image:\s*[\"']?" + IMG, re.M),
                re.compile(r"docker\s+pull\s+" + IMG)]
NOT_APP_IMAGES = re.compile(r"^(?:docker\.io/)?(?:library/)?(postgres|mysql|mariadb|redis|valkey/valkey|mongo|memcached|nginx|"
                            r"traefik|caddy|busybox|alpine|ubuntu|debian|node|python|golang|php|minio/minio|rabbitmq|"
                            r"adminer|dpage/pgadmin4|prom/prometheus|grafana/grafana|localstack/localstack|registry|"
                            r"bitnami\w*/\S+|elasticsearch|kibana|quay\.io/\S+|cgr\.dev/\S+|pgvector/pgvector)(:|$)")


# Image names that are an app's helper, not the app (demo servers, test data, its database image, SDKs).
IMAGE_HELPER = re.compile(r"(whoami|fake|test|demo|example|mock|loki|mongo|postgres|redis|mysql|mariadb|sdk|cli$|"
                          r"-base$|builder|dev$|devcontainer|e2e|playground|enterprise|-ee$)", re.I)


# Image names that are the app's main part when they sit under the app's own name (billionmail/core).
APP_ROLE_WORDS = {"core", "server", "app", "web", "ui", "frontend", "backend", "api", "studio", "platform", "community"}


def repo_slug(d: Path) -> tuple[str, str]:
    rc, url = sh(["git", "-C", str(d), "remote", "get-url", "origin"], timeout=10)
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?\s*$", url.strip()) if rc == 0 else None
    return (m.group(1).lower(), m.group(2).lower()) if m else ("", d.name.lower())


def _text_files(d: Path):
    globs = ["README*", "*.md", "docs/*.md", "docs/**/*.md", "**/docker-compose*.y*ml", "**/compose*.y*ml",
             ".github/workflows/*.y*ml", "deploy/**/*", "docker/**/*", "hosting/**/*", "self-host*/**/*"]
    seen = set()
    for g in globs:
        try:
            for f in d.glob(g):
                if f in seen or not f.is_file() or "node_modules" in f.parts or ".git" in f.parts:
                    continue
                seen.add(f)
                if f.stat().st_size < 400_000:
                    yield f
        except OSError:
            continue


def image_candidates(d: Path, extra_text: str = "", exclude: set[str] | None = None) -> list[str]:
    """Images the app's own files name, most likely first: the ones that carry the app's or
    owner's name, then how often they're mentioned. Databases, proxies and base images are not
    the app. Each is confirmed in the registry before it's offered."""
    owner, repo = repo_slug(d)
    keys = {k for k in {owner, repo, safe_id(d.name).split("-")[0], repo.split("-")[0]} if len(k) >= 3}
    counts: dict[str, int] = {}
    texts = [extra_text] if extra_text else [f.read_text(errors="replace") for f in _text_files(d)]
    for t in texts:
        for p in IMG_PATTERNS:
            for m in p.finditer(t):
                name, tag = m.group(1), m.group(2)
                if "$" in name or "{" in name or len(name) < 3 or name.isdigit() or (NOT_APP_IMAGES.match(name) and not any(k in name for k in keys)):
                    continue
                name = re.sub(r"^(?:index\.)?docker\.io/(?:library/)?", "", name)
                ref = name + (":" + tag if tag and "$" not in tag and not tag.startswith("{") else "")
                counts[ref] = counts.get(ref, 0) + 1
    # The upstream's own conventional names too (owner/repo on Docker Hub and GHCR).
    if owner and repo and not extra_text:
        for pre in REGISTRY_PREFIXES:
            counts.setdefault(f"{pre}{owner}/{repo}", 0)
    def rank(r: str) -> tuple:
        last = r.split(":")[0].rsplit("/", 1)[-1].lower()
        exact = last in keys                                        # traefik, grafana/grafana, memos
        starts = any(last.startswith(k) for k in keys)              # appsmith-ce, langflow-backend
        helper = bool(IMAGE_HELPER.search(last))                     # whoami, fake-data-gen, mongodb
        return (helper, not exact, not starts, -counts[r], ":" in r)
    def is_app(r: str) -> bool:
        # The app itself, not one of its helpers: named after the app (memos, appsmith-ce) or a main role
        # under the app's own name (billionmail/core). billionmail/rspamd, billionmail/dovecot are helpers.
        parts_ = r.split(":")[0].lower().split("/")
        last = parts_[-1]
        return any(last == k or last.startswith(k) for k in keys) or (
            any(k in parts_[:-1] for k in keys) and last in APP_ROLE_WORDS)
    ranked = sorted((r for r in counts if is_app(r)), key=rank)
    out = []
    for r in ranked:
        base = r.split(":")[0]
        if exclude and base in exclude:
            continue
        if any(o.split(":")[0] == base for o in out):
            continue
        ref = r if ":" in r.rsplit("/", 1)[-1] else r + ":latest"
        if _image_exists(ref):
            out.append(ref)
        if len(out) >= MAX_IMAGE_CANDIDATES:
            break
    return out


MOUNT_RE = re.compile(r"(?:^|\s)(?:-v|--volume)[\s=]+(?:[^\s:]+:)?(?P<target>/[^\s:,]+)(?::[a-zA-Z,]+)?", re.M)


def documented_mounts(d: Path, image: str) -> list[str]:
    """v112: container paths the app's OWN `docker run` examples mount (-v host:/app/config). An image that
    doesn't declare a VOLUME for them starts with the folder missing and fails with ENOENT (Homepage's
    /app/config). The runner gives each an anonymous volume: the app's documented run shape, no host paths."""
    base = image.split(":")[0].split("/")[-1]
    found: list[str] = []
    for f in _text_files(d):
        try:
            txt = f.read_text(errors="replace")
        except OSError:
            continue
        for block in re.finditer(r"docker\s+(?:container\s+)?run\b(?:\\\r?\n|[^\n])*", txt):
            b = block.group(0)
            if base not in b:
                continue
            for m in MOUNT_RE.finditer(b):
                t = m.group("target").rstrip("/")
                if t and t not in ("/", "/var/run/docker.sock") and not t.startswith("/var/run/") and t not in found:
                    found.append(t)
    return found[:6]


def _image_exists(ref: str) -> bool:
    """Is this image in its registry? Answers are remembered for a day, so re-checks don't use up
    Docker Hub's request limit. A rate-limited answer is never remembered as "missing"."""
    cache_f = RUNNER / "image_exists_cache.json"
    try:
        cache = json.loads(cache_f.read_text())
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(ref)
    if hit and time.time() - hit["at"] < 86400:
        return hit["exists"]
    rc, out = sh(["docker", "manifest", "inspect", ref], timeout=30)
    if rc != 0 and re.search(r"429|toomanyrequests|rate limit", out, re.I):
        return False
    cache[ref] = {"exists": rc == 0, "at": time.time()}
    cache_f.write_text(json.dumps(cache, indent=1))
    return rc == 0


def compose_files(d: Path) -> list[Path]:
    """The app's compose files, most likely to be "run this app" first. Any depth: Airflow keeps its
    official one five folders down. Ranked by evidence: a file that runs the app's OWN image (its
    name is in an image: line) beats one for a sub-component; deploy/docker/hosting folders beat
    others; dev/local setups come late; shallower beats deeper."""
    owner, repo = repo_slug(d)
    keys = {k for k in {owner, repo, safe_id(d.name).split("-")[0], repo.split("-")[0]} if len(k) >= 3}
    found = []
    for pat in ("docker-compose*.y*ml", "compose*.y*ml"):
        for f in d.rglob(pat):
            rel = f.relative_to(d).as_posix()
            if "node_modules" in rel or "/.git/" in "/" + rel or COMPOSE_SKIP.search(rel) or len(f.relative_to(d).parts) > 7:
                continue
            svcs = _compose_services(f)
            # An overlay (docker-compose.pg15.yml, ...caddy.yml) only tweaks services the base file
            # defines: some service has neither image nor build. It can't run on its own.
            if not svcs or any(isinstance(v, dict) and not v.get("image") and not v.get("build") and not v.get("extends")
                               for v in svcs.values()):
                continue
            found.append(f)
    def score(f: Path) -> tuple:
        rel = f.relative_to(d).as_posix()
        imgs = " ".join(str(s.get("image", "")) for s in _compose_services(f).values() if isinstance(s, dict)).lower()
        own = any(k in imgs for k in keys)
        place = bool(re.search(r"(^|/)(deploy|docker|hosting|self-?host\w*|production|prod|install)(/|$)", rel, re.I))
        late = bool(COMPOSE_LATE.search(rel))
        base = f.name.lower() in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
        return (not own, late, not place and len(f.relative_to(d).parts) > 1, not base, len(f.relative_to(d).parts), f.name)
    return sorted(set(found), key=score)


def _compose_services(f: Path) -> dict:
    try:
        import yaml
        doc = yaml.safe_load(f.read_text(errors="replace")) or {}
        s = doc.get("services") or {}
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def dockerfiles(d: Path) -> list[Path]:
    out = []
    for depth in ("", "*/", "*/*/"):
        for f in d.glob(depth + "Dockerfile"):
            rel = f.relative_to(d).as_posix()
            if any(x in rel.lower() for x in ("test", "example", "devcontainer", "e2e", "ci/")):
                continue
            out.append(f)
    return sorted(out, key=lambda f: len(f.relative_to(d).parts))


def parts(app: str, d: Path) -> list[dict]:
    """Every way to run this app, in the order they're tried."""
    out = []
    cand = load_candidate(app)
    if cand:
        out.append({**cand, "why": "self-healer's proposed recipe (saved only if it passes)"})
    saved = load_recipe(app)
    if saved:
        out.append({**saved, "why": "recipe that worked before"})
    built, late = [], []
    for f in compose_files(d):
        svcs = _compose_services(f)
        if not svcs:
            continue
        rel = f.relative_to(d).as_posix()
        needs_build = any(isinstance(s, dict) and s.get("build") and not s.get("image") for s in svcs.values())
        entry = {"kind": "compose", "file": rel, "env": {},
                 "why": f"the app's own compose file {rel}" + (" (builds from source)" if needs_build else "")}
        # A compose file for a development setup (dev/devenv/local folders or names) is tried only after the
        # app's own published image: it's meant for working on the app, not running it.
        (built if needs_build else late if COMPOSE_LATE.search(rel) else out).append(entry)
    for ref in image_candidates(d):
        part = {"kind": "image", "image": ref, "env": {}, "why": f"published image {ref}, named in the app's own files"}
        vols = documented_mounts(d, ref)
        if vols:
            part["volumes"] = vols     # v112: the mounts its own `docker run` line asks for, as anonymous volumes
        out.append(part)
    out += late
    # Nothing in the app's own folder worked? What its upstream publishes comes next (its docs' docker
    # commands and compose files, its owner's deployment repos, its owner's Docker Hub images), before
    # building from source and before the AI. Each is still only a candidate until it passes.
    if UPSTREAM_SEARCH:
        out += upstream_parts(app, d, {x.get("image", "").split(":")[0] for x in out if x.get("image")})
    if ALLOW_SOURCE_BUILD:
        out += built
        for f in dockerfiles(d):
            rel = f.relative_to(d).as_posix()
            out.append({"kind": "dockerfile", "dockerfile": rel, "context": f.parent.relative_to(d).as_posix() or ".",
                        "env": {}, "why": f"the app's own Dockerfile {rel} (builds from source)"})
    # Last: a reusable runtime for the app's TYPE (PHP app, Rust+wasm web app) when it ships no way to start it.
    if ALLOW_SOURCE_BUILD:
        import runtimes
        rt = runtimes.detect(d)
        if rt:
            out.append({**rt, "env": {}})
    # de-duplicate (the saved recipe is usually also one of the found parts)
    seen, uniq = set(), []
    for p in out:
        k = json.dumps({x: p.get(x) for x in ("kind", "file", "image", "dockerfile", "runtime")}, sort_keys=True)
        if k not in seen:
            seen.add(k); uniq.append(p)
    return uniq


# ------------------------------------------------------------------ recipes (what worked)
RECIPE_KINDS = {"compose", "image", "dockerfile", "runtime"}


def upstream_parts(app: str, d: Path, have: set[str]) -> list[dict]:
    import upstream
    owner, repo = repo_slug(d)
    keys = {k for k in {owner, repo, safe_id(d.name).split("-")[0], repo.split("-")[0]} if len(k) >= 3}
    try:
        f = upstream.discover(safe_id(app), d, owner, repo, keys, WORK / safe_id(app), RUNNER / "upstream")
    except Exception as e:
        return []
    out = []
    for ref in image_candidates(d, extra_text=f.get("doc_text", ""), exclude=have):
        out.append({"kind": "image", "image": ref, "env": {}, "why": f"image {ref} named in the app's own docs ({', '.join(f['doc_pages'][:2])})"})
    for i, b in enumerate(f.get("compose_blocks") or []):
        dest = WORK / safe_id(app) / "upstream" / f"docs-compose-{i + 1}" / "docker-compose.yml"
        dest.parent.mkdir(parents=True, exist_ok=True); dest.write_text(b["yaml"])
        out.append({"kind": "compose", "file": str(dest), "env": {}, "why": f"compose file from the app's docs ({b['from']})"})
    for r in f.get("deploy_repos") or []:
        for cf in compose_files(Path(r["path"]))[:3]:
            out.append({"kind": "compose", "file": str(cf), "env": {}, "why": f"compose file from the owner's deployment repo {r['url']}"})
    for img in f.get("hub_images") or []:
        base = img.split(":")[0]
        if base not in have and _image_exists(img + ":latest"):
            out.append({"kind": "image", "image": img + ":latest", "env": {}, "why": f"image {img} from the owner's Docker Hub namespace"})
    return out


def load_recipe(app: str) -> dict | None:
    try:
        r = json.loads((RECIPES / f"{safe_id(app)}.json").read_text())
        return r if r.get("kind") in RECIPE_KINDS else None
    except (OSError, ValueError):
        return None


def load_candidate(app: str) -> dict | None:
    """A recipe the self-healer (AI) proposed. Tried first, but only becomes the app's recipe if the
    app then passes the full six-stage check with it; otherwise it is thrown away."""
    try:
        r = json.loads((RECIPES / f"{safe_id(app)}.candidate.json").read_text())
        return r if r.get("kind") in RECIPE_KINDS else None
    except (OSError, ValueError):
        return None


def forget_recipe(app: str) -> None:
    (RECIPES / f"{safe_id(app)}.json").unlink(missing_ok=True)


def save_recipe(app: str, part: dict, how: str) -> None:
    part = {k: v for k, v in part.items() if not k.startswith("_")}
    keep = {k: part[k] for k in ("kind", "file", "image", "dockerfile", "context", "env", "port", "service", "path", "command",
                                  "drop_ulimits", "relaxed_caps", "volumes", "runtime", "php", "docroot", "laravel", "env_example", "database", "frontend",
                                  "wasm_bindgen", "binaryen", "cargo_about", "build") if k in part}
    keep.update(saved_at=time.time(), saved_by=how)
    (RECIPES / f"{safe_id(app)}.json").write_text(json.dumps(keep, indent=2) + "\n")


# ------------------------------------------------------------------ running one part
def project(app: str) -> str:
    return "atta-" + safe_id(app)


def _logs(app: str, part: dict, tail: int = 200) -> str:
    if part["kind"] == "compose":
        rendered = WORK / safe_id(app) / "compose.rendered.json"
        rc, out = sh(["docker", "compose", "-p", project(app), "-f", str(rendered), "logs", "--no-color", "--tail", str(tail)], timeout=60)
        return out
    rc, out = sh(["docker", "logs", "--tail", str(tail), project(app)], timeout=30)
    return out


def _containers(app: str) -> list[dict]:
    rc, out = sh(["docker", "ps", "-a", "--filter", f"label=atta.app={safe_id(app)}", "--format", "{{json .}}"], timeout=30)
    rows = [json.loads(l) for l in out.splitlines() if l.strip().startswith("{")] if rc == 0 else []
    rc, out = sh(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project(app)}", "--format", "{{json .}}"], timeout=30)
    rows += [json.loads(l) for l in out.splitlines() if l.strip().startswith("{")] if rc == 0 else []
    uniq = {r.get("ID"): r for r in rows}
    return list(uniq.values())


def _published(rows: list[dict]) -> list[tuple[int, str, str]]:
    """(host port, container name, image) for every published tcp port."""
    out = []
    for r in rows:
        for m in re.finditer(r"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]|::):(\d+)->(\d+)/tcp", r.get("Ports", "")):
            out.append((int(m.group(1)), r.get("Names", ""), r.get("Image", "")))
    return sorted(set(out))


def down(app: str, prune: bool | None = None) -> str:
    stop_proxy(app)
    used_images = {r.get("Image") for r in _containers(app) if r.get("Image")}
    rendered = WORK / safe_id(app) / "compose.rendered.json"
    msgs = []
    if rendered.is_file():
        rc, out = sh(["docker", "compose", "-p", project(app), "-f", str(rendered), "down", "-v", "--remove-orphans"], timeout=300)
        msgs.append(f"compose down rc={rc}")
    for r in _containers(app):
        sh(["docker", "rm", "-f", "-v", r["ID"]], timeout=60)
    # Runtime data from this run goes too: the next start is a clean first boot. Containers often
    # write it as another user, so a helper container clears what this process may not be allowed to.
    data = WORK / safe_id(app) / "data"
    if data.exists():
        shutil.rmtree(data, ignore_errors=True)
        if data.exists():
            sh(["docker", "run", "--rm", "-v", f"{data}:/d", "alpine:3", "sh", "-c", "rm -rf /d/* /d/.[!.]*"], timeout=120)
            shutil.rmtree(data, ignore_errors=True)
    sh(["docker", "network", "rm", project(app)], timeout=30)
    if prune if prune is not None else PRUNE_IMAGES:
        # Only this app's images: with apps running in parallel, a global prune could delete an image
        # another app has just downloaded. An image still in use elsewhere is refused by Docker and kept.
        for img in used_images:
            sh(["docker", "rmi", img], timeout=300)
        sh(["docker", "rmi", f"atta-local/{safe_id(app)}:latest"], timeout=120)
        msgs.append(f"{len(used_images)} image(s) removed")
    tp = TARGETS / f"{safe_id(app)}.json"
    if tp.is_file():
        try:
            t = json.loads(tp.read_text()); t["parked"] = True; t["parked_at"] = time.time()
            tp.write_text(json.dumps(t, indent=2) + "\n")
        except (OSError, ValueError):
            pass
    return "; ".join(msgs) or "stopped"


def _env_for(part: dict, app: str | None = None) -> dict:
    """v115: what Compose sees. Only the Docker CLI's own names plus what the runner chose for this app,
    plus a fake placeholder for each customer variable on record (the real one is in Coolify only)."""
    own = {k: str(v) for k, v in (part.get("env") or {}).items()}
    return app_env.runner_env({**(app_env.placeholders_for(app, own) if app else {}), **own})


def _ensure_env_file(d: Path, compose_file: Path, notes: list[str]) -> None:
    """The compose file's folder needs the .env it refers to. Use the app's OWN example file."""
    folder = compose_file.parent
    wanted = [folder / ".env"]; named: set[Path] = set()
    for s in _compose_services(compose_file).values():
        ef = s.get("env_file") if isinstance(s, dict) else None
        for e in ([ef] if isinstance(ef, (str, dict)) else ef or []):
            path = e.get("path") if isinstance(e, dict) else e
            if isinstance(path, str):
                path = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}]*)\}", r"\1", path)   # ${ENV_FILE_PATH:-.env} -> .env
                if "$" not in path:
                    named.add((folder / path).resolve())
                    wanted.append((folder / path).resolve())
    def rel(w: Path) -> str:
        try:
            return w.relative_to(d.resolve()).as_posix()
        except ValueError:
            return str(w)   # outside the library (an upstream compose file in the runner's work folder)
    def hide(w: Path) -> None:
        try:
            exclude_from_git(d, w.relative_to(d.resolve()).as_posix())
        except ValueError:
            pass
    for w in dict.fromkeys(wanted):
        if w.is_dir() and not any(w.iterdir()) and not (str(w).startswith(str(d.resolve())) and _tracked(d, rel(w))):
            # Docker creates an empty FOLDER when a missing file is mounted; a folder named .env breaks compose.
            w.rmdir(); notes.append(f"removed an empty folder {w.name} left where the .env file belongs")
        if w.exists() or not (str(w).startswith(str(d.resolve())) or str(w).startswith(str(WORK.resolve()))):
            continue
        for ex in ENV_EXAMPLES:
            src = w.parent / ex
            if src.is_file() and _own_file(src, d):
                shutil.copy2(src, w); hide(w)
                notes.append(f"created {rel(w)} from its own {ex}")
                break
        else:
            if w not in named:
                continue   # a bare .env nobody names is optional to compose
            w.parent.mkdir(parents=True, exist_ok=True); w.write_text("")
            hide(w)
            notes.append(f"created empty {rel(w)} (named by the compose file, no example shipped)")


def _create_missing_env(d: Path, part: dict, path: str | None, notes: list[str]) -> bool:
    """The error names an env file that isn't there: create it from the app's own example next to
    it, else empty. True only if something was actually created (no change = no retry)."""
    if not path or part.get("kind") != "compose":
        return False
    p = Path(path.strip("\"'")) if Path(path.strip("\"'")).is_absolute() else (d / part["file"]).parent / path.strip("\"'")
    p = p.resolve()
    if p.exists() or not str(p).startswith(str(d.resolve()) + os.sep):
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    for ex in ENV_EXAMPLES:
        if (p.parent / ex).is_file() and _own_file(p.parent / ex, d):
            shutil.copy2(p.parent / ex, p); break
    else:
        p.write_text("")
    exclude_from_git(d, p.relative_to(d.resolve()).as_posix())
    notes.append(f"created {p.relative_to(d.resolve())} (the compose file needs it)")
    return True


def _own_file(src: Path, d: Path) -> bool:
    """v115: an example file is copied only if it really is the app's (a link to ATTa's .env is not)."""
    try:
        r = src.resolve()
    except (OSError, RuntimeError):
        return False
    return r.is_relative_to(d.resolve()) or r.is_relative_to(WORK.resolve())


def _tracked(d: Path, rel: str) -> bool:
    """True if `rel` is (or contains) a file the app ships in its own git history."""
    rc, out = sh(["git", "-C", str(d), "ls-files", "--", rel], timeout=30)
    return rc == 0 and bool(out.strip())


# The app's own example settings files, in the order they're used to create a missing .env.
# BillionMail ships `env_init`.
ENV_EXAMPLES = (".env.example", ".env.sample", "example.env", ".env.template", "env.example", ".env.dist",
                "variables.env", ".env.defaults", ".env.default", "env_init", "env.init", ".env.init", "env.sample",
                "env.template", "default.env", "sample.env", ".env.local.example", ".env.production.example")


SECRET_NAME = re.compile(r"(PASS|PASSWORD|PASSWD|SECRET|TOKEN|_KEY$|^KEY$|SALT)", re.I)


def _fill_blank_secrets(f: Path, part: dict, notes: list[str]) -> None:
    """Passwords and secrets the compose file uses but the app's example .env leaves blank
    (BillionMail: DBPASS, REDISPASS -> Postgres and Redis refuse to start). Each gets a generated
    value for this run. Only secret-looking names, only when blank, never overriding a real value."""
    text = f.read_text(errors="replace")
    names = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?[-?][^}]*)?\}", text)) | set(re.findall(r"\$([A-Z_][A-Z0-9_]*)", text))
    envf = f.parent / ".env"
    have = {}
    if envf.is_file():
        for line in envf.read_text(errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                have[k.strip()] = v.strip().strip("\"'")
    for n in sorted(names):
        if SECRET_NAME.search(n) and not have.get(n) and n not in part["env"]:
            part["env"][n] = secrets.token_hex(16)
            notes.append(f"{n} was blank: generated a value for this run")


def harden_args(part: dict) -> list[str]:
    """v114: `docker run` flags that fence an untrusted container in."""
    if not HARDEN:
        return []
    a = ["--security-opt", "no-new-privileges", "--pids-limit", str(PIDS_LIMIT)]
    if CPUS and CPUS != "0":
        a += ["--cpus", CPUS]
    if not part.get("relaxed_caps"):
        a += ["--cap-drop", "ALL"]
        for c in SAFE_CAPS:
            a += ["--cap-add", c]
    return a


def _exposes_atta(src: str) -> bool:
    """v115: a host path that would hand a container ATTa itself: its data folder (.env, accounts, other
    apps), its live code, or a parent of either (/, /srv). Refused even when ALLOW_HOST_ACCESS is on."""
    try:
        r = Path(src).resolve()
    except (OSError, RuntimeError):
        return True
    for atta in (ROOT.resolve(), HERE, Path("/opt/app-builder").resolve(), Path("/opt/app-builder-releases")):
        if r == atta or atta.is_relative_to(r) or r.is_relative_to(atta):
            return True
    return False


def harden_service(name: str, s: dict, part: dict, notes: list[str], d: Path, work: Path | None = None) -> None:
    """v114: the same fence for one compose service, plus removal of anything that hands it the host.
    v115: bind mounts may only come from the app's OWN folders (not the shared work folder), and never
    expose ATTa's own data or code, whatever HARDEN / ALLOW_HOST_ACCESS say."""
    own = [d.resolve(), (work or WORK).resolve()]
    kept = []
    for v in s.get("volumes") or []:
        src = v.get("source") if isinstance(v, dict) else (str(v).split(":", 1)[0] if ":" in str(v) else None)
        bind = (isinstance(v, dict) and v.get("type") == "bind") or (isinstance(v, str) and src and src.startswith(("/", ".", "~")))
        if bind and src and _exposes_atta(src) and not any(Path(src).resolve().is_relative_to(a) for a in own):
            notes.append(f"{name}: dropped host mount {src} (it would expose ATTa's own data or code)")
            continue
        kept.append(v)
    if "volumes" in s:
        s["volumes"] = kept
    if not HARDEN:
        return
    if not ALLOW_HOST_ACCESS:
        for k in HOST_KEYS:
            if s.pop(k, None) not in (None, False, [], {}):
                notes.append(f"{name}: removed `{k}` (a container may not reach the host)")
        for k in HOST_MODES:
            v = s.get(k)
            if isinstance(v, str) and v.strip().lower() == "host":
                s.pop(k); notes.append(f"{name}: removed `{k}: host` (a container may not share the host's {k})")
        so = s.get("security_opt")
        if isinstance(so, list):
            kept = [o for o in so if "unconfined" not in str(o).lower() and "no-new-privileges=false" not in str(o).lower().replace(":", "=")]
            if len(kept) != len(so):
                notes.append(f"{name}: removed security_opt entries that switch confinement off")
            s["security_opt"] = kept
        vols = []
        for v in s.get("volumes") or []:
            src = v.get("source") if isinstance(v, dict) else (str(v).split(":", 1)[0] if ":" in str(v) else None)
            bind = (isinstance(v, dict) and v.get("type") == "bind") or (isinstance(v, str) and src and src.startswith(("/", ".", "~")))
            if bind and src:
                try:
                    inside = any(Path(src).resolve().is_relative_to(a) for a in own)
                except (OSError, ValueError):
                    inside = False
                ro = (isinstance(v, dict) and v.get("read_only")) or (isinstance(v, str) and v.endswith(":ro"))
                if not inside and not (ro and src.rstrip("/") in HOST_BIND_OK):
                    notes.append(f"{name}: dropped host mount {src} (outside the app's folder; a container may not reach the host)")
                    continue
            vols.append(v)
        if "volumes" in s:
            s["volumes"] = vols
        if not part.get("relaxed_caps"):
            s["cap_add"] = [c for c in (s.get("cap_add") or []) if str(c).upper().removeprefix("CAP_") in SAFE_CAPS]
    so = [o for o in (s.get("security_opt") or []) if not str(o).startswith("no-new-privileges")]
    s["security_opt"] = so + ["no-new-privileges:true"]
    if not s.get("pids_limit"):
        s["pids_limit"] = PIDS_LIMIT
    if CPUS and CPUS != "0" and not s.get("cpus"):
        s["cpus"] = CPUS
    if part.get("relaxed_caps"):
        s.pop("cap_drop", None)
    else:
        s["cap_drop"] = ["ALL"]
        s["cap_add"] = sorted(set(s.get("cap_add") or []) | set(SAFE_CAPS))


def _render_compose(app: str, d: Path, part: dict, notes: list[str]) -> tuple[bool, str]:
    f = d / part["file"]
    _ensure_env_file(d, f, notes)
    _fill_blank_secrets(f, part, notes)
    # v115: files Compose would read INTO the config (env_file, include, extends, the .env it interpolates
    # from) must be the app's own, links followed. Checked before Compose reads them.
    try:
        import yaml
        raw = yaml.safe_load(f.read_text(errors="replace")) or {}
    except Exception:
        raw = {}
    bad = app_env.check_sources(f, raw, app_env.allowed_dirs(d, WORK / safe_id(app)))
    if bad:
        return False, "__UNSAFE_CONFIG__ refused this compose file: " + "; ".join(bad[:8])
    rc, out = sh(["docker", "compose", "-f", str(f), "--project-directory", str(f.parent), "config", "--format", "json"],
                 timeout=120, env=_env_for(part, app), cwd=str(f.parent))
    if rc != 0:
        return False, out
    try:
        # stdout (the JSON) and stderr (warnings) come back together: decode just the JSON object.
        cfg, _ = json.JSONDecoder().raw_decode(out[out.index("{"):])
    except ValueError:
        return False, "compose config did not return JSON:\n" + out[-2000:]
    taken: set[int] = set()
    for name, s in (cfg.get("services") or {}).items():
        s.pop("container_name", None)
        img = s.get("image")
        if isinstance(img, str) and ":" in img.rsplit("/", 1)[-1]:
            repo_part, tag = img.rsplit(":", 1)
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
                # Docs templates leave placeholders (apache/airflow:|version|). Use the image's latest release.
                s["image"] = repo_part + ":latest"
                notes.append(f"{name}: image tag {tag!r} is a docs placeholder, not a tag -> {repo_part}:latest")
        s.setdefault("labels", {})
        if isinstance(s["labels"], dict):
            s["labels"]["atta.app"] = safe_id(app)
        new = []
        for p in s.get("ports") or []:
            if isinstance(p, dict) and p.get("target"):
                if p.get("published") in (None, ""):
                    continue   # unpublished ports stay internal
                p = {**p, "published": str(free_port(taken)), "host_ip": "127.0.0.1"}
                new.append(p)
        if new or "ports" in s:
            s["ports"] = new
        if s.get("restart") in ("always", "unless-stopped"):
            s["restart"] = "on-failure"
        if part.get("drop_ulimits") and s.pop("ulimits", None) is not None:
            notes.append(f"{name}: this server won't let containers raise their limits; started without `ulimits`")
        own_limit = (((s.get("deploy") or {}).get("resources") or {}).get("limits") or {}).get("memory")
        if mem_limit() and not s.get("mem_limit") and not own_limit:
            # The app's own memory limit (deploy.resources.limits.memory) wins; adding a second one makes
            # compose refuse the whole file (OpenCart: "can't set distinct values on mem_limit").
            s["mem_limit"] = mem_limit()
        # Data folders the app writes at run time (./stacks, ./data, ./logs) would otherwise be created
        # INSIDE the library copy: that changes the app's folder and, worse, a half-finished first boot
        # leaves data behind that breaks every later start (Appsmith: MongoDB never initialised).
        # Any bind mount whose source is inside the app but not part of its own files is moved to the
        # runner's data folder for this app, which starts empty on every fresh attempt.
        for v in s.get("volumes") or []:
            if not (isinstance(v, dict) and v.get("type") == "bind" and v.get("source")):
                continue
            src = Path(v["source"])
            try:
                rel = src.resolve().relative_to(d.resolve())
            except ValueError:
                continue
            new_src = WORK / safe_id(app) / "data" / rel
            if _tracked(d, rel.as_posix()) or src.is_file() or (src.is_dir() and any(src.iterdir())):
                # The app's own file (a config it ships, or its .env). Containers write into their config
                # (BillionMail's postfix edits main.cf), so they get a fresh copy; the original is never written.
                if not new_src.exists():
                    new_src.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        if src.is_dir():
                            # symlinks copied as links: source trees carry dangling ones (Polar's .prettierignore)
                            shutil.copytree(src, new_src, symlinks=True)
                        else:
                            shutil.copy2(src, new_src)
                    except (OSError, shutil.Error) as e:
                        # Can't copy it: mount the app's own copy READ-ONLY instead, so it still can't be written.
                        shutil.rmtree(new_src, ignore_errors=True)
                        v["read_only"] = True
                        notes.append(f"{name}: {rel} could not be copied ({type(e).__name__}); mounted read-only from the library")
                        continue
                v["source"] = str(new_src)
                continue
            new_src.mkdir(parents=True, exist_ok=True)
            v["source"] = str(new_src)
            notes.append(f"{name}: runtime data {rel} kept outside the library ({new_src})")
        harden_service(name, s, part, notes, d, WORK / safe_id(app))
    # v115: the finished config may not carry ATTa's secrets or read files from outside the app.
    bad = app_env.check_rendered(cfg, app_env.allowed_dirs(d, WORK / safe_id(app)))
    if bad:
        return False, "__UNSAFE_CONFIG__ refused this compose file: " + "; ".join(bad[:8])
    cfg["name"] = project(app)
    wd = WORK / safe_id(app); wd.mkdir(parents=True, exist_ok=True)
    (wd / "compose.rendered.json").write_text(json.dumps(cfg, indent=1))
    return True, ""


def _start(app: str, d: Path, part: dict, notes: list[str]) -> tuple[bool, str]:
    """Bring the part up straight away. No download queue, no disk gate: every app starts at once.
    If the disk does fill, the disk.full rule and the second pass in qualify_all() deal with it."""
    return _start_inner(app, d, part, notes)


def _start_inner(app: str, d: Path, part: dict, notes: list[str]) -> tuple[bool, str]:
    """Bring the part up. (started?, error text)."""
    wd = WORK / safe_id(app); wd.mkdir(parents=True, exist_ok=True)
    if part["kind"] == "runtime":
        import runtimes
        gen = runtimes.write(part, d, wd / "runtime")
        part.setdefault("env", {}).setdefault("ATTA_DB_PASSWORD", secrets.token_hex(16))
        part = {**part, "kind": "compose", "file": str(gen)}   # from here on, the same path as any compose file
        notes.append(f"generated {part.get('runtime')} runtime files in {gen.parent} (the library is not touched)")
    env = _env_for(part, app)
    if part["kind"] == "compose":
        ok, err = _render_compose(app, d, part, notes)
        if not ok:
            return False, err
        up = ["docker", "compose", "-p", project(app), "-f", str(wd / "compose.rendered.json"), "up", "-d", "--remove-orphans"]
        if not ALLOW_SOURCE_BUILD:
            up.append("--no-build")
        rc, out = sh(up, timeout=PULL_TIMEOUT + (BUILD_TIMEOUT if ALLOW_SOURCE_BUILD else 0), env=env, cwd=str((d / part["file"]).parent))
        return rc == 0, out
    image = part.get("image")
    if part["kind"] == "dockerfile":
        image = f"atta-local/{safe_id(app)}:latest"
        ctx = d / part.get("context", ".")
        rc, out = sh(["docker", "build", "-t", image, "-f", str(d / part["dockerfile"]), str(ctx)], timeout=BUILD_TIMEOUT)
        if rc != 0:
            return False, out[-6000:]
    else:
        rc, out = sh(["docker", "pull", image], timeout=PULL_TIMEOUT)
        if rc != 0:
            return False, out[-3000:]
    rc, out = sh(["docker", "image", "inspect", image, "--format", "{{json .Config.ExposedPorts}}"], timeout=30)
    exposed = sorted({int(k.split("/")[0]) for k in (json.loads(out) or {}) if k.endswith("/tcp")}) if rc == 0 and out.strip() not in ("", "null") else []
    ports = [int(part["port"])] if part.get("port") else (exposed or COMMON_PORTS)
    taken: set[int] = set()
    cmd = ["docker", "run", "-d", "--name", project(app), "--label", f"atta.app={safe_id(app)}"] + harden_args(part)
    if mem_limit():
        cmd += ["--memory", mem_limit()]
    for p in ports:
        cmd += ["-p", f"127.0.0.1:{free_port(taken)}:{p}"]
    for k, v in {**app_env.placeholders_for(app, part.get("env") or {}), **(part.get("env") or {})}.items():
        cmd += ["-e", f"{k}={v}"]
    for vol in (part.get("volumes") or []):
        if not isinstance(vol, str) or not vol.startswith("/") or ":" in vol:
            notes.append(f"ignored volume {vol!r}: only anonymous volumes (a container path) are mounted"); continue
        cmd += ["-v", vol]                # anonymous volume: the app gets its writable folder, no host path is chosen
        notes.append(f"anonymous volume at {vol} (the app's own run instructions mount it)")
    rc, out = sh(cmd + [image] + [str(c) for c in (part.get("command") or [])], timeout=120)
    return rc == 0, out


def _wait_http(app: str, part: dict) -> tuple[int | None, str]:
    """Wait for any published port to answer HTTP. (port, reason when none)."""
    started = time.time(); deadline = started + int(part.get("boot_timeout") or BOOT_TIMEOUT)
    last_logs, extended = "", False
    stall_sig, stall_since = None, time.time()
    owner_keys = {safe_id(app).split("-")[0], "web", "app", "frontend", "ui", "proxy", "nginx", "server", "studio", "caddy"}
    while True:
        if host_memory_low():
            return None, "__HOST_MEMORY__ the machine ran low on memory while this app started; stopped to protect it\n" + _logs(app, part, 40)
        rows = _containers(app)
        pubs = _published(rows)
        # The app's own healthcheck says it isn't ready (Appsmith: web server up, backend still starting).
        # Trust it: keep waiting, up to BOOT_TIMEOUT_MAX, before any answer counts.
        # A container that stopped with an error (a database that died) won't come back by waiting.
        # One-shot setup containers that finish cleanly (exit 0) are normal and ignored.
        died = [r for r in rows if r.get("State") == "exited" and not re.search(r"Exited \(0\)", r.get("Status", ""))]
        if died and time.time() - started > 15:
            txt = "\n".join(f"== {r.get('Names')} {r.get('Status')}\n" + sh(["docker", "logs", "--tail", "80", r["ID"]], timeout=30)[1]
                            for r in died[:3])
            return None, f"__EXITED__ {len(died)} container(s) stopped with an error\n" + txt
        unready = [r.get("Names", "") for r in rows if re.search(r"health: starting|\(unhealthy\)|^Restarting", r.get("Status", ""))]
        if unready and time.time() - started < BOOT_TIMEOUT_MAX:
            # While it's not healthy, read its logs every ~30s: a startup crash the runner knows how to
            # fix (restart, missing variable) is acted on now instead of waiting out the whole boot limit.
            if time.time() - started > 60 and int(time.time() - started) % 30 < 5:
                logs = _logs(app, part, 400)
                dx = diagnose(logs)
                if dx and dx["fix"] in ("restart", "set_env") and not part.get("_restarted_for_" + dx["rule"]):
                    return None, logs
                # Stalled: not healthy and nothing new in its logs for STALL_SECONDS. Waiting longer won't help.
                sig = hash(logs[-3000:])
                if sig != stall_sig:
                    stall_sig, stall_since = sig, time.time()
                elif time.time() - stall_since > STALL_SECONDS:
                    return None, f"__STALLED__ not healthy and no new log output for {STALL_SECONDS}s\n" + logs[-6000:]
            time.sleep(5)
            continue
        # Most likely web container first, then html answers first.
        pubs.sort(key=lambda x: (not any(k in (x[1] + x[2]).lower() for k in owner_keys), x[0]))
        best = None
        empty_server = None
        for hp, name, img in pubs:
            code, ctype = http_probe(hp)
            if code is not None and code < 500:
                if code in (403, 404) and "html" in ctype.lower():
                    # A bare "Forbidden"/"Not Found" page: a web server is up but the app isn't in it
                    # (OpenCart's tools image). Not counted as up; if nothing better appears, try the next part.
                    empty_server = empty_server or (hp, code)
                    continue
                if "html" in ctype.lower():
                    return _settle(hp, part, app)
                if 400 <= code < 500:
                    # v112: a refusal, not an answer. If the app's own log names why (a host allow-list
                    # variable), that is a known failure class and its fix comes before any "service" verdict.
                    logs = _logs(app, part, 120)
                    dx = diagnose(logs)
                    if dx and dx["fix"] == "set_env":
                        return None, logs
                best = best or hp
        if empty_server and not best and time.time() - started > 60:
            return None, f"__NO_APP_PAGE__ port {empty_server[0]} answers {empty_server[1]} with an empty web server page, not the app\n" + _logs(app, part, 60)
        if best and time.time() - started > 20:
            return _settle(best, part, app)
        running = [r for r in rows if r.get("State") in ("running", "restarting", "created")]
        if not running:
            return None, "__EXITED__ every container stopped\n" + _logs(app, part)
        if part["kind"] == "compose" and pubs == []:
            # nothing published: a compose app that only exposes internally can't be reached
            if time.time() - started > 30:
                return None, "__EXITED__ the compose file publishes no port for the app\n" + _logs(app, part, 60)
        if time.time() > deadline:
            logs = _logs(app, part, 80)
            if not extended and logs != last_logs and time.time() - started < BOOT_TIMEOUT_MAX:
                deadline = started + BOOT_TIMEOUT_MAX; extended = True
                continue
            return None, "__STILL_STARTING__ no answer after %ds\n" % int(time.time() - started) + logs
        if int(time.time() - started) % 30 < 3:
            last_logs = _logs(app, part, 80)
        time.sleep(3)


def _settle(port: int, part: dict, app: str) -> tuple[int | None, str]:
    """The app answered once. It must keep answering without a 5xx for SETTLE_SECONDS: some apps
    serve their front page before their backend is ready. A 5xx restarts the settle clock (within
    the boot limit); still 5xx at the limit = __STILL_STARTING__."""
    limit = time.time() + int(part.get("boot_timeout") or BOOT_TIMEOUT)
    good_since = time.time()
    while time.time() - good_since < SETTLE_SECONDS:
        code, _ = http_probe(port)
        if code is None or code >= 500:
            good_since = time.time()
            if time.time() > limit:
                return None, f"__STILL_STARTING__ answers {code} and never settles\n" + _logs(app, part, 80)
        time.sleep(3)
    return port, ""


def run_app(app: str, log=print) -> dict:
    """Start the app by the first part that works, adapting each part before giving it up.
    Returns {"ok", "url", "part", "attempts": [...]}; the app is left running when ok."""
    d = app_dir(app)
    ok, why = docker_ok()
    if not ok:
        return {"ok": False, "code": "RUNNER_NO_DOCKER", "detail": why or "docker is not running", "attempts": []}
    todo = parts(app, d)
    attempts: list[dict] = []
    if not todo:
        return {"ok": False, "code": "RUNNER_NO_RECIPE",
                "detail": "no compose file, no published image named in the app's own files, no Dockerfile", "attempts": []}
    n = 0; t_begin = time.time()
    for part in todo:
        if time.time() - t_begin > APP_TIME_BUDGET:
            attempts.append({"n": n, "part": part.get("why"), "outcome": "NOT_TRIED", "rule": "budget.spent",
                             "notes": [f"per-app time budget ({APP_TIME_BUDGET}s) spent before this way could be tried"]})
            break
        part = json.loads(json.dumps(part))   # own copy: fixes add to it
        fixes_here = 0
        pending_rule = None   # a LEARNED rule whose fix was applied on the last attempt; judged by this attempt
        while n < MAX_ATTEMPTS:
            n += 1; notes: list[str] = []
            down(app, prune=False)
            log(f"  [{app}] attempt {n}: {part['why']}" + (f" env={sorted(part['env'])}" if part.get("env") else ""))
            t_a = time.time()
            started, err = _start(app, d, part, notes)
            t_b = time.time()
            port, reason = (None, err) if not started else _wait_http(app, part)
            if pending_rule:
                # The previous attempt applied a learned rule's fix and retried the same part. Up now = HELD;
                # still down = FAILED. Only retry-fixes are judged here (next_part rules only classify).
                import rule_lifecycle
                rule_lifecycle.record(LEARNED_RULES, pending_rule, "held" if port else "failed",
                                      None if port else {"app": app, "part": part.get("why"), "detail": _evidence(reason)})
                pending_rule = None
            timing = {"download_start_s": round(t_b - t_a, 1), "boot_wait_s": round(time.time() - t_b, 1)}
            if port:
                url = f"http://127.0.0.1:{port}/"
                part["port_seen"] = port
                attempts.append({"n": n, "part": part["why"], "outcome": "UP", "url": url, "notes": notes, **timing})
                # Not saved yet: "answered on a port" is not "works". qualify_app saves it only if the app
                # then passes the full six-stage check.
                return {"ok": True, "url": url, "part": part, "attempts": attempts}
            dx = diagnose(reason, skip=("container.caps",) if part.get("relaxed_caps") or not HARDEN else ()) \
                or {"rule": "unrecognised", "fix": "next_part"}
            attempts.append({"n": n, "part": part["why"], "outcome": "FAILED", "rule": dx["rule"], "fix": dx["fix"],
                             "notes": notes, "evidence": _evidence(reason), **timing})
            _record_evidence(app, part, dx, reason)
            log(f"  [{app}]   -> {dx['rule']} -> {dx['fix']}")
            fixes_here += 1
            pending_rule = dx["rule"] if dx.get("learned") and dx["fix"] != "next_part" else None
            if dx["fix"] == "set_env" and dx.get("var") and dx["var"] not in part["env"] and fixes_here <= 8:
                part["env"][dx["var"]] = gen_value(dx["var"]); continue
            if dx["fix"] == "copy_env" and fixes_here <= 3 and _create_missing_env(d, part, dx.get("path"), notes):
                attempts[-1]["notes"] = notes; continue
            if dx["fix"] == "new_ports" and fixes_here <= 2:
                continue      # fresh ports are handed out on every attempt
            if dx["fix"] == "pick_command" and part["kind"] != "compose" and not part.get("command"):
                cmd = _pick_command(reason)
                if cmd:
                    part["command"] = [cmd]; attempts[-1]["notes"] = notes + [f"its help lists `{cmd}`: starting it with that"]
                    continue
            if dx["fix"] == "add_volume" and part["kind"] != "compose" and fixes_here <= 3:
                vol = _volume_for(dx.get("path") or dx.get("path2") or dx.get("path3") or "")
                if vol and vol not in (part.get("volumes") or []):
                    part.setdefault("volumes", []).append(vol)
                    attempts[-1]["notes"] = notes + [f"the app needs {dx.get('path') or dx.get('path2') or dx.get('path3')}: mounting an anonymous volume at {vol}"]
                    continue
            if dx["fix"] == "relax_caps" and HARDEN and not part.get("relaxed_caps"):
                part["relaxed_caps"] = True
                attempts[-1]["notes"] = notes + ["the app needs a kernel capability the hardened start leaves out: "
                                                 "retrying with Docker's default set (other limits kept)"]
                continue
            if dx["fix"] == "drop_ulimits" and not part.get("drop_ulimits"):
                part["drop_ulimits"] = True; continue
            if dx["fix"] == "rate_limit" and fixes_here <= 3:
                fix_rate_limit(notes); attempts[-1]["notes"] = notes; continue
            if dx["fix"] == "restart" and not part.get("_restarted_for_" + dx["rule"]):
                # Restart the SAME containers (data kept), rather than tearing down and starting over.
                part["_restarted_for_" + dx["rule"]] = True
                notes.append(f"{dx['rule']}: restarting the app's containers now its dependencies are up")
                attempts[-1]["notes"] = notes
                for c in _containers(app):
                    sh(["docker", "restart", c["ID"]], timeout=180)
                port, reason = _wait_http(app, part)
                if port:
                    url = f"http://127.0.0.1:{port}/"
                    attempts.append({"n": n, "part": part["why"], "outcome": "UP after restart", "url": url, "notes": notes})
                    return {"ok": True, "url": url, "part": part, "attempts": attempts}
                dx = diagnose(reason) or {"rule": "unrecognised", "fix": "next_part"}
                attempts.append({"n": n, "part": part["why"], "outcome": "FAILED after restart", "rule": dx["rule"],
                                 "fix": dx["fix"], "notes": [], "evidence": _evidence(reason)})
                break
            if dx["fix"] == "prune" and fixes_here <= 1:
                sh(["docker", "builder", "prune", "-af"], timeout=900)
                sh(["docker", "image", "prune", "-f" if len(_RUNNING) > 1 else "-af"], timeout=900); continue
            if dx["fix"] == "wait_longer" and fixes_here <= 1 and BOOT_TIMEOUT < BOOT_TIMEOUT_MAX:
                part["boot_timeout"] = BOOT_TIMEOUT_MAX; continue
            if part.get("why") == "recipe that worked before":
                # A saved recipe that no longer works is forgotten, so it can't keep going first.
                forget_recipe(app)
                attempts[-1]["notes"] = notes + ["the saved recipe no longer works: forgotten"]
            if part.get("why", "").startswith("self-healer's proposed"):
                (RECIPES / f"{safe_id(app)}.candidate.json").unlink(missing_ok=True)
                attempts[-1]["notes"] = notes + ["the self-healer's recipe did not start the app: discarded"]
            break             # next_part, or this part's fixes are spent
        if n >= MAX_ATTEMPTS:
            break
    down(app, prune=False)
    last = attempts[-1] if attempts else {}
    if any(a.get("rule") == "host.no_ipv6" for a in attempts):
        return {"ok": False, "code": "RUNNER_HOST_NO_IPV6",
                "detail": "the app listens on IPv6 and this server's kernel has IPv6 switched off. Not an app fault: "
                          "enable IPv6 on the server (it is on by default on AWS)", "attempts": attempts}
    return {"ok": False, "code": "RUNNER_EXHAUSTED",
            "detail": f"{len(attempts)} attempts over {len({a['part'] for a in attempts})} ways of running it; last: "
                      f"{last.get('part')} -> {last.get('rule')}", "attempts": attempts}


def _volume_for(path: str) -> str | None:
    """The folder to mount for a missing container path: the path itself when it looks like a folder,
    its parent when it looks like a file (has an extension). Never / or a one-level system folder."""
    p = path.rstrip("/")
    if not p.startswith("/"):
        return None
    if re.search(r"\.[A-Za-z0-9]{1,8}$", p.rsplit("/", 1)[-1]):
        p = p.rsplit("/", 1)[0]
    if p.count("/") < 2 or p.split("/")[1] in ("bin", "etc", "lib", "lib64", "proc", "sys", "dev", "usr", "run", "tmp"):
        return None
    return p


def _pick_command(help_text: str) -> str | None:
    """The server-starting subcommand the tool's own help lists (a line starting with it)."""
    listed = set(re.findall(r"^\s{1,12}([a-z][a-z0-9-]+)\s{2,}\S", help_text or "", re.M))
    listed |= set(re.findall(r"\{([a-z0-9,-]+)\}", help_text or "")) and {w for grp in re.findall(r"\{([a-z0-9,-]+)\}", help_text or "") for w in grp.split(",")}
    return next((c for c in SERVER_COMMANDS if c in listed), None)


def _write_result(app: str, r: dict) -> None:
    """Write-then-rename: a full disk leaves the old result, never an empty file."""
    dest = RESULTS / f"{safe_id(app)}.json"
    tmp = dest.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(r, indent=2, default=str) + "\n"); os.replace(tmp, dest)
    except OSError:
        tmp.unlink(missing_ok=True)


def _host_starved(r: dict) -> bool:
    """Did this app fail because the MACHINE ran out (disk, memory, Docker Hub limit), not because of the app?"""
    run = r.get("runner") or {}
    rules = {a.get("rule") for a in run.get("attempts") or []}
    text = json.dumps(r, default=str)
    return bool(rules & {"disk.full", "host.memory", "registry.rate_limit"}) or "No space left on device" in text \
        or "no space left on device" in text


def _record_evidence(app: str, part: dict, dx: dict, reason: str) -> None:
    """Every failed start, kept forever (state/runner/evidence.jsonl): what was tried, which rule it
    matched, and the real error/log lines. "unrecognised" rows are where the next rule comes from;
    tests/run_tests.py replays the rules against these real failures."""
    row = {"at": time.time(), "app": safe_id(app), "part": part.get("why"), "rule": dx.get("rule"), "fix": dx.get("fix"),
           "evidence": _evidence(reason), "raw_tail": (reason or "")[-6000:]}
    try:
        with _EVIDENCE_LOCK, (RUNNER / "evidence.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass   # a full disk must never turn an app's failure into a runner crash


_EVIDENCE_LOCK = threading.Lock()


def _evidence(text: str) -> str:
    """The lines of a failure worth a person's (or the self-healer's) eyes."""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    hot = [l for l in lines if re.search(r"error|fail|fatal|panic|exception|required|missing|denied|refused|not found|killed|__", l, re.I)]
    pick = (hot[-15:] if hot else []) + lines[-10:]
    return "\n".join(dict.fromkeys(pick))[-3000:]


# ------------------------------------------------------------------ skin proxy + registration
def _proxy_pidfile(app: str) -> Path:
    return RUNNER / f"proxy-{safe_id(app)}.pid"


def stop_proxy(app: str) -> None:
    pf = _proxy_pidfile(app)
    try:
        pid = int(pf.read_text().strip())
        os.killpg(pid, signal.SIGTERM)
        for _ in range(20):
            os.killpg(pid, 0); time.sleep(0.2)
        os.killpg(pid, signal.SIGKILL)
    except (OSError, ValueError, ProcessLookupError):
        pass
    pf.unlink(missing_ok=True)


def start_proxy(app: str, target_url: str) -> tuple[bool, str, dict | None]:
    d = app_dir(app); ui = d / ".ui-capability"
    launcher = ui / "run-ui.sh"
    if not launcher.is_file():
        return False, f"{launcher} missing (installer did not run for this app)", None
    stop_proxy(app)
    m = re.search(r'DEFAULT_UI_PORT="(\d+)"', launcher.read_text(errors="replace"))
    port = int(m.group(1)) if m else 8100
    if port_open(port):
        return False, f"the skin proxy port {port} is already taken by something else", None
    logf = (RUNNER / f"proxy-{safe_id(app)}.log").open("a")
    env = {**os.environ, "TARGET_URL": target_url, "UI_PORT": str(port), "APP_BUILDER_ROOT": str(ROOT)}
    p = subprocess.Popen(["bash", str(launcher)], env=env, stdout=logf, stderr=logf, stdin=subprocess.DEVNULL, start_new_session=True)
    _proxy_pidfile(app).write_text(str(p.pid))
    for _ in range(40):
        if port_open(port):
            break
        if p.poll() is not None:
            return False, "skin proxy exited: " + (RUNNER / f"proxy-{safe_id(app)}.log").read_text(errors="replace")[-1500:], None
        time.sleep(0.5)
    else:
        return False, f"skin proxy did not listen on {port}", None
    try:
        t = json.loads((TARGETS / f"{safe_id(app)}.json").read_text())
    except (OSError, ValueError):
        return False, "run-ui.sh did not register the app", None
    return True, "", t


def register_static(app: str, prof: str) -> dict:
    """System projects and packages aren't started; the watcher checks their build readiness."""
    d = app_dir(app)
    t = {"app": safe_id(app), "app_id": safe_id(app), "app_dir": str(d), "ui_dir": str(d / ".ui-capability"),
         "target_url": "http://127.0.0.1:9/", "proxy_url": "http://127.0.0.1:9/", "profile": prof}
    (TARGETS / f"{safe_id(app)}.json").write_text(json.dumps(t, indent=2) + "\n")
    return t


# ------------------------------------------------------------------ the whole thing for one app
def _watcher():
    sys.path.insert(0, str(HERE))
    import system_watcher
    return system_watcher


def _synth(app: str, code: str, detail: str) -> dict:
    """A watcher-shaped result for an app that never got as far as the watcher."""
    w = _watcher()
    ui = app_dir(app) / ".ui-capability"
    st = {"1 INSTALLED": w.stage()} if (ui / "run-ui.sh").is_file() else {}
    if not st:
        return w.fail(safe_id(app), {}, "1 INSTALLED", "FILE_MISSING:run-ui.sh", detail)
    return w.fail(safe_id(app), st, "2 APP_UP", code, detail)


def qualify_app(app: str, keep: bool | None = None, log=print) -> dict:
    """Start (if it runs), skin-proxy, register, watch, stop. Returns the watcher result, plus
    a `runner` block saying how it was started. Written to state/runner/results/<app>.json."""
    keep = KEEP_RUNNING if keep is None else keep
    d = app_dir(app); prof = profile(d)
    t0 = time.time()
    if prof in {"system", "package"}:
        t = register_static(app, prof)
        r = _watcher().check(t)
        r["runner"] = {"profile": prof, "started": False, "why": f"{prof} project: nothing to run, stage 6 checks build readiness"}
    else:
        run = run_app(app, log=log)
        t_up = time.time()
        if not run["ok"]:
            r = _synth(app, run["code"], run["detail"] + "\n" + "\n".join(
                f"#{a['n']} {a['part']}: {a.get('rule')} ({a.get('fix')})" for a in run["attempts"]))
            r["runner"] = {k: run.get(k) for k in ("code", "detail", "attempts")}
        else:
            ok, why, t = start_proxy(app, run["url"])
            if not ok:
                r = _synth(app, "PROXY_NOT_STARTED", why)
            else:
                # It runs and answers, but with no web page (frp answers 401 JSON): it's a service, not a
                # website. Check it as one, and say so, instead of failing it for not being HTML.
                code, ctype = http_probe(int(run["url"].rsplit(":", 1)[1].strip("/")))
                if prof == "web" and code is not None and (code < 400 or code == 401) and ctype and "html" not in ctype.lower():
                    prof = "service"
                    log(f"  [{app}] answers {code} {ctype.split(';')[0]}, no web page: checking it as a service")
                    run.setdefault("attempts", []).append({"n": 0, "part": "profile", "outcome": "RECLASSIFIED",
                                                           "notes": [f"answers {code} {ctype}; checked as a service"]})
                t["profile"] = prof
                (TARGETS / f"{safe_id(app)}.json").write_text(json.dumps(t, indent=2) + "\n")
                r = _watcher().check(t)
                # Known fix: a 5xx means the app is still warming up, not broken. Wait, check again.
                for i in range(WARMUP_RECHECKS):
                    if not _warming(r):
                        break
                    log(f"  [{app}] {r['verdict']}: still warming up, re-checking in {WARMUP_RECHECK_WAIT}s ({i + 1}/{WARMUP_RECHECKS})")
                    time.sleep(WARMUP_RECHECK_WAIT)
                    r = _watcher().check(t)
            r["runner"] = {"started": True, "url": run["url"], "part": run["part"].get("why"),
                           "part_detail": {k: v for k, v in run["part"].items() if not k.startswith("_")}, "attempts": run["attempts"]}
        if not keep:
            down(app)
    # The recipe rule: a way of starting an app is saved ONLY when the app passed the full check with it
    # (every stage, including the real browser). Self-healer proposals follow the same rule.
    run_ = r.get("runner") or {}
    passed = r.get("broken_at") is None and (r.get("stages") or {}).get("6 CLEAN", {}).get("status") == "OK"
    used = (run_.get("part_detail") or None)
    if used and run_.get("started"):
        if passed:
            save_recipe(app, used, "qualified: passed all six stages")
            (RECIPES / f"{safe_id(app)}.candidate.json").unlink(missing_ok=True)
        else:
            if used.get("why", "").startswith("self-healer's proposed"):
                (RECIPES / f"{safe_id(app)}.candidate.json").unlink(missing_ok=True)
            if load_recipe(app) and used.get("why") == "recipe that worked before":
                forget_recipe(app)
    r["number"] = app_number(app)
    t_end = time.time()
    r["runner"]["seconds"] = int(t_end - t0)
    # Every app's clock, kept on its result: when it started and finished, how long it queued for room,
    # and how its own time split between starting it and checking it.
    r["timing"] = {"queued_s": round(_QUEUED.get(safe_id(app), 0), 1),
                   "started_at": time.strftime("%H:%M:%S", time.localtime(t0)),
                   "finished_at": time.strftime("%H:%M:%S", time.localtime(t_end)),
                   "total_s": int(t_end - t0),
                   "start_s": int((locals().get("t_up") or t_end) - t0),
                   "check_s": int(t_end - (locals().get("t_up") or t_end)),
                   "attempts": [{k: a.get(k) for k in ("n", "outcome", "download_start_s", "boot_wait_s")}
                                for a in (r["runner"].get("attempts") or [])]}
    r["runner"]["kept_running"] = bool(keep and r["runner"].get("started"))
    _write_result(app, r)
    return r


def _warming(r: dict) -> bool:
    st = (r.get("stages") or {}).get(r.get("broken_at") or "", {})
    return (r.get("code") in ("UPSTREAM_5XX", "UPSTREAM_502", "BROWSER_HTTP")
            and (r.get("code") != "BROWSER_HTTP" or str(st.get("detail", "")).startswith("5")))


# ------------------------------------------------------------------ app numbers + the test checklist
NUMBERS = ROOT / "state" / "app_numbers.json"
_NUMBER_LOCK = threading.Lock()


def app_number(app: str) -> str:
    """Every app's permanent number (A-001, A-002, ...). Assigned by this script the first time the
    app is seen, never by hand, never reused, never changed: the number is the app's address in every
    test checklist. state/app_numbers.json is the register."""
    key = safe_id(app)
    with _NUMBER_LOCK:
        try:
            reg = json.loads(NUMBERS.read_text())
        except (OSError, ValueError):
            reg = {"next": 1, "apps": {}}
        if key not in reg["apps"]:
            reg["apps"][key] = {"no": f"A-{reg['next']:03d}", "assigned_at": time.time()}
            reg["next"] += 1
            NUMBERS.parent.mkdir(parents=True, exist_ok=True)
            tmp = NUMBERS.with_suffix(".tmp"); tmp.write_text(json.dumps(reg, indent=2) + "\n"); os.replace(tmp, NUMBERS)
        return reg["apps"][key]["no"]


def number_all(names: list[str]) -> None:
    """First sight of a batch: numbers go out in name order, so the same library always numbers the same way."""
    for n in sorted(names, key=safe_id):
        app_number(n)


# The build test loop's numbered steps. EVERY app goes through the SAME numbers, in this order, on every
# build. The numbers are permanent: a new step gets the next number at the end, a step is never renumbered.
#   PASS = did what it should   FAIL = didn't   SKIP = not reached (an earlier step failed)   N/A = doesn't
#   apply to this kind of app (system projects and packages aren't started)   INFO = recorded, never failed
STEPS = [
    ("S01", "IN_LIBRARY",      "the app's folder is in the library, as a git copy"),
    ("S02", "CATALOGUED",      "it has a catalogue entry with a skin category"),
    ("S03", "SKIN_INSTALLED",  "its .ui-capability overlay is installed and complete (watcher stage 1)"),
    ("S04", "INTAKE_CHECKED",  "intake checked it and wrote its ledger"),
    ("S05", "STARTED",         "the runner found a way to run it and started it"),
    ("S06", "APP_UP",          "the app answers (watcher stage 2)"),
    ("S07", "PROXY_UP",        "the skin proxy in front of it answers (watcher stage 3)"),
    ("S08", "SKIN_SERVED",     "its skin stylesheet is served and correct (watcher stage 4)"),
    ("S09", "ADAPTER_HOOKED",  "the capability adapter is hooked into the page (watcher stage 5)"),
    ("S10", "BROWSER_CLEAN",   "a real browser opens it cleanly (watcher stage 6)"),
    ("S11", "ADAPTER_STATE",   "dormant or active, and which capabilities (recorded, never failed)"),
    ("S12", "RECIPE_SAVED",    "how it was started is saved, so next time it starts first try"),
]


def app_steps(app: str, r: dict | None) -> list[dict]:
    """One app's ticks against STEPS, from real evidence only (files on disk + the watcher result)."""
    d = LIB / app
    try:
        d = app_dir(app)
    except FileNotFoundError:
        pass
    stages = (r or {}).get("stages") or {}
    run = (r or {}).get("runner") or {}
    prof = profile(d) if d.is_dir() else "web"
    static = prof in ("system", "package")
    def st(name):
        x = stages.get(name, {}).get("status")
        return {"OK": "PASS", "FAIL": "FAIL", "SKIPPED": "SKIP", "NOT_APPLICABLE": "N/A", "NOT_RUN": "FAIL"}.get(x, "SKIP")
    try:
        cat = json.loads((ROOT / "app_catalogue.json").read_text()).get("apps", {})
        centry = next((e for k, e in cat.items() if safe_id(e.get("app", k)) == safe_id(app)), None)
    except (OSError, ValueError):
        centry = None
    ad = (r or {}).get("adapter") or {}
    started = "N/A" if static else ("PASS" if run.get("started") else "FAIL" if r else "SKIP")
    ticks = {
        "S01": "PASS" if (d / ".git").is_dir() else "FAIL",
        "S02": "PASS" if centry and centry.get("skin_category") else "FAIL",
        "S03": st("1 INSTALLED"),
        "S04": "PASS" if (d / ".atta-intake.json").is_file() else "FAIL",
        "S05": started,
        "S06": st("2 APP_UP") if not static else "N/A",
        "S07": st("3 PROXY_UP") if not static else "N/A",
        "S08": st("4 SKIN") if not static else "N/A",
        "S09": st("5 HOOK") if not static else "N/A",
        "S10": st("6 CLEAN"),
        "S11": "INFO",
        "S12": "N/A" if static else ("PASS" if load_recipe(app) else "FAIL" if run.get("started") else "SKIP"),
    }
    if started == "FAIL":   # never started: the web stages weren't reached, they didn't "fail"
        for k in ("S06", "S07", "S08", "S09", "S10"):
            ticks[k] = "SKIP"
    info = {"S11": (ad.get("state") or "not seen") + (": " + ", ".join(ad.get("capabilities")) if ad.get("capabilities") else ""),
            "S05": run.get("part") or run.get("why") or (run.get("code") or "")}
    return [{"no": no, "name": name, "tick": ticks[no], "note": info.get(no, "")} for no, name, _ in STEPS]


def checklist(names: list[str], results: list[dict]) -> dict:
    """The test checklist for one run: every app in the library, by number, ticked PASS or FAIL.
    An app on the list with no result is NOT_CHECKED, and that fails the run: no app can quietly
    drop out of a test. complete = every number has a result."""
    by_app = {r.get("app"): r for r in results}
    rows = []
    for n in sorted(names, key=app_number):
        r = by_app.get(safe_id(n))
        if r is None:
            rows.append({"no": app_number(n), "app": safe_id(n), "tick": "NOT_CHECKED", "verdict": "no result for this app",
                         "steps": app_steps(safe_id(n), None)})
            continue
        steps = app_steps(safe_id(n), r)
        ok = all(x["tick"] in ("PASS", "N/A", "INFO") for x in steps)
        first_bad = next((x for x in steps if x["tick"] in ("FAIL", "SKIP")), None)
        rows.append({"no": app_number(n), "app": safe_id(n), "tick": "PASS" if ok else "FAIL", "verdict": r.get("verdict"),
                     "stopped_at": f"{first_bad['no']} {first_bad['name']}" if first_bad else None,
                     "seconds": (r.get("runner") or {}).get("seconds"), "timing": r.get("timing"), "steps": steps})
    count = {k: sum(1 for x in rows if x["tick"] == k) for k in ("PASS", "FAIL", "NOT_CHECKED")}
    return {"expected": len(names), "ticked": count["PASS"] + count["FAIL"], **count,
            "complete": count["NOT_CHECKED"] == 0 and len(rows) == len(names), "rows": rows}


def checklist_text(cl: dict, title: str) -> str:
    mark = {"PASS": "[x] PASS", "FAIL": "[ ] FAIL", "NOT_CHECKED": "[ ] NOT CHECKED"}
    lines = [f"# Test checklist: {title}", "",
             f"Expected {cl['expected']} apps, ticked {cl['ticked']}: {cl['PASS']} PASS, {cl['FAIL']} FAIL, "
             f"{cl['NOT_CHECKED']} NOT CHECKED. " + ("COMPLETE" if cl["complete"] else "INCOMPLETE (apps missing a result)"), ""]
    if "regressions" in cl:
        lines += [f"Compared with the last full run: REGRESSIONS {len(cl['regressions'])}"
                  + (f" ({', '.join(cl['regressions'])})" if cl["regressions"] else "")
                  + f"; newly passing {len(cl.get('newly_passing') or [])}"
                  + (f" ({', '.join(cl['newly_passing'])})" if cl.get("newly_passing") else ""), ""]
    if "rule_suspects" in cl:
        lines += ["Learned rules promoted to CONFIRMED this run: " + (", ".join(cl.get("rules_promoted") or []) or "none"),
                  "Regression suspects (rules learned/promoted since the last clean run): " + (", ".join(cl["rule_suspects"]) or "none"),
                  "Full rule health: state/checklists/rule_health.md", ""]
    sym = {"PASS": "✓", "FAIL": "✗", "SKIP": "·", "N/A": "–", "INFO": "i"}
    lines += ["Steps (same numbers for every app): " + "  ".join(f"{no} {name}" for no, name, _ in STEPS), "",
              "| No. | App | Result | " + " | ".join(no for no, _, _ in STEPS) + " | Stopped at | Adapter | Secs | Start→Finish | Queued | Starting | Checking |",
              "|---|---|---|" + "---|" * len(STEPS) + "---|---|---|---|---|---|---|"]
    for x in cl["rows"]:
        steps = {s_["no"]: s_ for s_ in x.get("steps") or []}
        lines.append(f"| {x['no']} | {x['app']} | {mark[x['tick']]} | "
                     + " | ".join(sym.get(steps.get(no, {}).get('tick'), "?") for no, _, _ in STEPS)
                     + f" | {x.get('stopped_at') or ''} | {steps.get('S11', {}).get('note', '')} | {x.get('seconds') if x.get('seconds') is not None else ''} | "
                     + (lambda t: f"{t.get('started_at','')}→{t.get('finished_at','')} | {t.get('queued_s','')}s | {t.get('start_s','')}s | {t.get('check_s','')}s |" if t else " |  |  |  |")(x.get("timing")))
    lines += ["", "✓ pass  ✗ fail  · not reached  – not applicable  i recorded"]
    return "\n".join(lines) + "\n"


def library_apps() -> list[str]:
    try:
        m = json.loads((LIB / "UI_CAPABILITY_DEPLOYMENT_MANIFEST.json").read_text())
        return [Path(a["path"]).name for a in m.get("apps", []) if a.get("status") == "READY"]
    except (OSError, ValueError):
        return sorted(p.name for p in LIB.iterdir() if (p / ".ui-capability").is_dir())


def _free_disk_gb() -> float:
    try:
        st = os.statvfs(str(ROOT))
        return st.f_bavail * st.f_frsize / 2**30
    except OSError:
        return 1e9


_QUEUED: dict[str, float] = {}   # kept for the timing record; always 0 now that nothing waits


_RUNNING: set[str] = set()
_RUNNING_LOCK = threading.Lock()


def qualify_all(apps: list[str] | None = None, log=print, parallel: int | None = None) -> list[dict]:
    """Every app at the same time (or PARALLEL at a time if that is set). Results come back in library order."""
    names = list(apps if apps is not None else library_apps())
    number_all(library_apps() + names)
    n = max(1, parallel or PARALLEL or len(names))
    if n == 1 or len(names) < 2:
        return [_qualify_one(a, log) for a in names]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=n) as pool:
        out = list(pool.map(lambda a: _qualify_one(a, log, gate=True), names))
    # Second pass: apps that failed only because the machine ran short while everything ran at once
    # (disk full, memory, Docker Hub limit) are checked again one at a time, with room cleared first.
    starved = [i for i, r in enumerate(out) if r.get("broken_at") is not None and _host_starved(r)]
    if starved:
        log(f"second pass: {len(starved)} app(s) failed for lack of machine room; re-checking one at a time")
    for i in starved:
        sh(["docker", "builder", "prune", "-af"], timeout=600)
        sh(["docker", "image", "prune", "-af"], timeout=900)   # nothing else is running now
        out[i] = _qualify_one(names[i], log)
        out[i].setdefault("runner", {})["second_pass"] = True
    return out


def _qualify_one(a: str, log, gate: bool = False) -> dict:
    with _RUNNING_LOCK:
        _RUNNING.add(a)   # no gate: registered as running at once (other code reads how many are running)
    try:
        return _qualify_one_inner(a, log)
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(a)


def _qualify_one_inner(a: str, log) -> dict:
    log(f"[{a}] qualifying")
    if True:
        try:
            r = qualify_app(a, log=log)
        except Exception as e:   # one app's crash never stops the others
            import traceback
            tb = traceback.format_exc()
            r = _synth(a, "RUNNER_EXCEPTION", f"{type(e).__name__}: {e}\n{tb[-2000:]}")
            r["runner"] = {"exception": str(e), "traceback": tb[-4000:]}
            try:
                down(a, prune=False)
            except Exception:
                pass
            _write_result(a, r)
    log(f"[{a}] {r['verdict']}")
    return r


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"check", "up", "down", "all", "recipe"}:
        print(__doc__.split("Command line (for a person):", 1)[1]); return 2
    cmd, app = argv[1], (argv[2] if len(argv) > 2 else None)
    if cmd == "all":
        # Resumes by default: apps checked since the last library change keep their result (a restart of
        # the machine mid-run doesn't start the whole library over). `all --fresh` checks every app again.
        names = library_apps()
        if "--fresh" not in argv:
            done = {p.stem for p in RESULTS.glob("*.json")}
            names = [n for n in names if safe_id(n) not in done]
        qualify_all(names)
        rs = [json.loads(p.read_text()) for p in sorted(RESULTS.glob("*.json"))]
        cl = checklist(library_apps(), rs)
        (RUNNER / "CHECKLIST.md").write_text(checklist_text(cl, "app_runner.py all"))
        print(checklist_text(cl, "app_runner.py all"))
        return 0 if cl["complete"] and cl["FAIL"] == 0 else 1
        for r in rs:
            print(f"{r['app']:<24} {r['verdict']}")
        return 0 if all(r.get("broken_at") is None for r in rs) else 1
    if not app:
        print("which app?"); return 2
    if cmd == "recipe":
        print(json.dumps(load_recipe(app), indent=2)); return 0
    if cmd == "down":
        print(down(app)); return 0
    if cmd == "up":
        r = run_app(app)
        if r["ok"]:
            ok, why, _ = start_proxy(app, r["url"])
            print(f"UP  app {r['url']}  skin proxy {'started' if ok else 'NOT started: ' + why}")
        else:
            print(f"NOT STARTED: {r['code']}: {r['detail']}")
        return 0 if r["ok"] else 1
    r = qualify_app(app)
    _watcher().print_result(r)
    print(f"  HOW: {r['runner'].get('part') or r['runner'].get('why') or r['runner'].get('detail')}")
    return 0 if r.get("broken_at") is None else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
