"""app_env.py — what an uploaded app may receive from ATTa, and the check on its finished Compose config.

v115. ATTa runs every library app with Docker/Compose. Before v115 those commands inherited ATTa's whole
service environment (.env: session secret, model API key, Docker Hub and Coolify tokens), and Compose
reads its own environment for `${NAME}` in the app's compose file and for bare `environment: [NAME]`
entries. So an app's compose file could simply ask for ATTa's secrets.

Now:
  1. runner_env()     every docker / docker compose command gets a minimal environment: the few names the
                      Docker CLI itself needs (BASE_ENV), plus what the runner chose for this app (its own
                      generated passwords, fixes it applied). Nothing else from ATTa's environment.
  2. check_sources()  before rendering: an env_file, include, extends or label_file the compose file names,
                      and the `.env` Compose reads for interpolation, must resolve (links followed) inside
                      the app's own folders. A symlinked `.env -> /srv/app-builder/.env` is refused.
  3. check_rendered() after `docker compose config`: no string anywhere in the finished config contains one
                      of ATTa's protected values, and file sources (secrets, configs, build contexts,
                      Dockerfiles, bind-type named volumes) stay inside the app's folders.

Customer integration tokens (Stripe, OpenAI, email service...) never pass through here: they go straight to
Coolify for the deployed app (customer_secrets.py). A local check run gets a clearly fake placeholder for
each customer variable name on record, never the real value.
"""
from __future__ import annotations
import os, re
from pathlib import Path

# The only names from ATTa's environment a docker/compose command gets. The Docker CLI needs these to find
# the daemon and its own config (registry logins stay in DOCKER_CONFIG's file, which containers never see).
# Proxy variables are deliberately absent: they can carry credentials and the pulls happen in the daemon.
BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "USER", "LOGNAME",
            "XDG_RUNTIME_DIR", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_CERT_PATH",
            "DOCKER_TLS_VERIFY", "DOCKER_BUILDKIT", "BUILDKIT_PROGRESS")

# ATTa's own credentials by name. Their VALUES may never appear in an app's finished config.
PROTECTED_NAMES = ("APP_BUILDER_SESSION_SECRET", "APP_BUILDER_PASSWORD", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                   "DOCKERHUB_TOKEN", "COOLIFY_TOKEN", "GITHUB_TOKEN", "GH_TOKEN",
                   "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
# ...and any other variable in ATTa's environment whose name looks like a credential.
SECRET_LOOKING = re.compile(r"(PASS|PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|CREDENTIAL|_AUTH$|SALT)", re.I)
# Shorter values are too likely to match by accident ("admin", "8787").
MIN_PROTECTED_LEN = 8

ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
PLACEHOLDER = "atta-local-check-placeholder-not-a-real-secret"


def runner_env(extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in BASE_ENV if k in os.environ}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    for k, v in (extra or {}).items():
        env[str(k)] = str(v)
    return env


def _env_file_values(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[k] = v
    except OSError:
        pass
    return out


def protected_values() -> dict:
    """{value: NAME} for ATTa's own credentials: from its environment and its .env file, plus the
    customer-secret fingerprint key. Never logged; used only to look for them."""
    found = {}
    sources = [dict(os.environ), _env_file_values(ROOT / ".env")]
    for src in sources:
        for k, v in src.items():
            if not isinstance(v, str) or len(v) < MIN_PROTECTED_LEN or k in BASE_ENV:
                continue
            if k in PROTECTED_NAMES or SECRET_LOOKING.search(k):
                found.setdefault(v, k)
    try:
        key = (ROOT / "state" / "secret-fingerprint.key").read_text().strip()
        if len(key) >= MIN_PROTECTED_LEN:
            found.setdefault(key, "customer-secret fingerprint key")
    except OSError:
        pass
    return found


def find_protected(obj, values: dict | None = None, where: str = "") -> list[str]:
    """Paths inside obj (dict/list/str) whose text contains a protected value. Names only, never values."""
    values = protected_values() if values is None else values
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits += find_protected(k, values, f"{where}.{k}(key)")
            hits += find_protected(v, values, f"{where}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hits += find_protected(v, values, f"{where}[{i}]")
    elif isinstance(obj, str):
        for val, name in values.items():
            if val in obj:
                hits.append(f"{where.lstrip('.') or '(top)'} holds ATTa's {name}")
    return hits


def _inside(p: Path, allowed: list[Path]) -> bool:
    try:
        r = p.resolve()
    except (OSError, RuntimeError):
        return False
    return any(r == a or r.is_relative_to(a) for a in allowed)


def allowed_dirs(app_dir: Path, work_dir: Path) -> list[Path]:
    """The app's own library folder and its own runner work folder. Nothing else."""
    return [app_dir.resolve(), work_dir.resolve()]


def _paths_from(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        p = v.get("path") or v.get("file")
        return [p] if isinstance(p, str) else []
    if isinstance(v, list):
        out = []
        for x in v:
            out += _paths_from(x)
        return out
    return []


def check_sources(compose_file: Path, doc: dict, allowed: list[Path]) -> list[str]:
    """Before rendering. Files Compose would READ into the config from outside the app's folders."""
    base = compose_file.parent
    bad = []
    if not _inside(compose_file, allowed):
        bad.append(f"the compose file itself resolves outside the app's folder ({compose_file.resolve()})")
    envf = base / ".env"
    if envf.exists() or envf.is_symlink():
        if not _inside(envf, allowed):
            bad.append(f".env next to the compose file points outside the app's folder ({envf.resolve()})")
    def check(label, raw):
        for s in _paths_from(raw):
            if "$" in s:
                s = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}]*)\}", r"\1", s)
            if "$" in s:
                bad.append(f"{label} {s!r} uses a variable in its path"); continue
            p = Path(os.path.expanduser(s)) if s.startswith("~") else (Path(s) if os.path.isabs(s) else base / s)
            if not _inside(p, allowed):
                bad.append(f"{label} {s!r} is outside the app's folder")
    if not isinstance(doc, dict):
        return bad
    for inc in doc.get("include") or []:
        check("include", inc if isinstance(inc, (str, dict)) else [])
        if isinstance(inc, dict):
            check("include env_file", inc.get("env_file"))
            check("include project_directory", inc.get("project_directory"))
    for name, s in (doc.get("services") or {}).items():
        if not isinstance(s, dict):
            continue
        check(f"{name}: env_file", s.get("env_file"))
        check(f"{name}: label_file", s.get("label_file"))
        ext = s.get("extends")
        if isinstance(ext, dict) and ext.get("file"):
            check(f"{name}: extends file", ext.get("file"))
    return bad


def check_rendered(cfg: dict, allowed: list[Path], values: dict | None = None) -> list[str]:
    """After `docker compose config`: protected values anywhere, and file sources outside the app."""
    bad = find_protected(cfg, values)
    def check(label, s):
        if isinstance(s, str) and s and not _inside(Path(s), allowed):
            bad.append(f"{label} {s!r} is outside the app's folder")
    for kind in ("secrets", "configs"):
        for name, x in (cfg.get(kind) or {}).items():
            if isinstance(x, dict) and x.get("file"):
                check(f"{kind}.{name} file", x["file"])
    for name, v in (cfg.get("volumes") or {}).items():
        opts = (v or {}).get("driver_opts") or {} if isinstance(v, dict) else {}
        dev = opts.get("device")
        if dev and ("bind" in str(opts.get("o", "")) or str(opts.get("type", "")) in ("none", "bind")):
            check(f"volume {name} device", dev)
    for name, s in (cfg.get("services") or {}).items():
        b = s.get("build") if isinstance(s, dict) else None
        if isinstance(b, dict):
            ctx = b.get("context")
            if isinstance(ctx, str) and not re.match(r"^[a-z]+://|^git@", ctx):
                check(f"{name}: build context", ctx)
                df = b.get("dockerfile")
                if isinstance(df, str) and os.path.isabs(df):
                    check(f"{name}: dockerfile", df)
            for extra in (b.get("additional_contexts") or {}).values():
                if isinstance(extra, str) and not re.match(r"^[a-z-]+://|^service:", extra):
                    check(f"{name}: build additional context", extra)
    return bad


def placeholders_for(app: str, part_env: dict) -> dict:
    """Customer variable names on record for this app (customer_secrets.py) get an obviously fake value in
    local check runs. The real value lives only in Coolify."""
    try:
        import customer_secrets
        names = customer_secrets.names(app)
    except Exception:
        names = []
    return {n: PLACEHOLDER for n in names if n not in (part_env or {})}
