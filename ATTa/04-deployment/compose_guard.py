#!/usr/bin/env python3
"""
compose_guard.py — v115: keep ATTa's own secrets out of customer apps (review item #1).

Two kinds of secret meet in a compose file:

  ATTa secrets      the session secret, ATTa's API keys, Docker Hub and Coolify credentials, webhook
                    URLs, cloud credentials. They belong to the server. No app may ever receive one.
  customer secrets  Stripe, PayPal, OpenAI, Anthropic, email-provider tokens... supplied FOR one app (today in
                    the app's own .env; later from the client form). That app receives them, by design.

The protection is about the first kind only, so it never blocks the second:

  1. Compose runs with a CLEAN environment (clean_env): PATH, HOME, Docker's own settings, plus the app's
     approved values. ATTa's secrets are simply not there to interpolate: ${APP_BUILDER_SESSION_SECRET}
     renders empty. The app's own .env is still read by compose from the app's folder, as before.
  2. validate_paths: every file compose would read on the app's behalf must be inside the app's folder:
     env_file, secrets/configs `file:`, build context and additional_contexts, the project .env, and
     (unless an admin trusted the app) bind-style named volumes. Symlinks are resolved. Service bind
     mounts outside the folder are dropped by app_runner.harden_service, as before.
  3. scan_rendered: the finished configuration must not contain the VALUE of any ATTa secret, under any
     name, raw, base64 or URL-encoded. A customer's key with the same NAME as one of ATTa's (both called
     ANTHROPIC_API_KEY) passes: only ATTa's value is protected.

Every refusal raises ComposeSecurityError whose message starts with REFUSED_MARK. Messages name the setting
and the kind of secret, never a value.
"""
from __future__ import annotations
import base64, json, os, re
from pathlib import Path
from urllib.parse import quote, quote_plus

import envfile, redact

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Where ATTa keeps its own settings; every secret-looking value in it is protected.
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
ATTA_ENV_FILE = ROOT / ".env"
# The only server variables compose (and every docker command) is given. Docker needs these to find its
# daemon, its config and its plugins; nothing here is a secret.
COMPOSE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "USER",
                    "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
                    "DOCKER_BUILDKIT", "BUILDKIT_PROGRESS", "COMPOSE_BAKE",
                    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
# ATTa's own secrets by name, protected whatever their value looks like. Names that merely LOOK secret
# (redact.py's name patterns: *PASSWORD*, *TOKEN*, *API_KEY*, ...) are protected too.
ATTA_SECRET_NAMES = frozenset({
    "APP_BUILDER_SESSION_SECRET", "APP_BUILDER_PASSWORD", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "COOLIFY_TOKEN", "DOCKERHUB_TOKEN", "DOCKERHUB_PASSWORD", "ALERT_WEBHOOK_URL",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "GITHUB_TOKEN", "GH_TOKEN",
})
# Shorter values are not matched (too likely to appear by chance: "true", "8787", "admin").
MIN_SECRET_LEN = 8
# ==========================================================================================

REFUSED_MARK = "ATTA SECURITY REFUSED:"
_SECRET_NAME = re.compile(redact._NAME, re.I)


class ComposeSecurityError(RuntimeError):
    def __init__(self, msg: str):
        super().__init__(f"{REFUSED_MARK} {msg}")


# ------------------------------------------------------------------ 1. environment

def clean_env(extra: dict | None = None) -> dict:
    """The environment for compose and docker: Docker's own settings plus the app's approved values."""
    base = {k: os.environ[k] for k in COMPOSE_ENV_KEYS if k in os.environ}
    base.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    return {**base, **{str(k): str(v) for k, v in (extra or {}).items()}}


# ------------------------------------------------------------------ 2. paths

def is_inside(path, root) -> bool:
    """True when path, with every symlink resolved, is root or inside it."""
    try:
        Path(path).resolve(strict=False).relative_to(Path(root).resolve(strict=False))
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def _roots(app_root) -> list[Path]:
    return [Path(r) for r in (app_root if isinstance(app_root, (list, tuple)) else [app_root])]


def inside_any(path, roots) -> bool:
    return any(is_inside(path, r) for r in _roots(roots))


def _require_inside(value, base: Path, app_root, label: str) -> None:
    if not isinstance(value, str) or not value:
        return
    p = Path(value) if os.path.isabs(value) else Path(base) / value
    if not inside_any(p, app_root):
        raise ComposeSecurityError(f"{label} points outside the app's folder ({value}); "
                                   "a compose file may only use files that ship with the app")


def _is_bind_volume(driver_opts: dict) -> bool:
    """A `local` named volume that is really a bind of a host folder: type none/bind, or o=bind/rbind.
    tmpfs and network (nfs, cifs) volumes are not host folders and stay allowed."""
    opts = {x.strip().split("=", 1)[0].lower() for x in str(driver_opts.get("o", "")).split(",") if x.strip()}
    return str(driver_opts.get("type", "")).lower() in ("none", "bind") or bool(opts & {"bind", "rbind"})


def check_project_env(compose_file: Path, app_root) -> None:
    """The .env compose reads for interpolation must be the app's own file, not a link to ATTa's."""
    env = Path(compose_file).parent / ".env"
    if env.is_symlink() or env.exists():
        if not inside_any(env, app_root):
            raise ComposeSecurityError("the app's .env is a link to a file outside the app's folder")


def validate_paths(cfg: dict, app_root, *, host_access: bool = False) -> None:
    """Refuse a rendered compose configuration that reads or mounts anything outside app_root (one folder,
    or a list: the app's library folder and its own work folder under the runner).
    Service bind mounts are not refused here: app_runner.harden_service DROPS any that leave the app's
    folder (as since v114) so the app can still start without them.
    host_access (an admin-trusted app only) relaxes bind-style named volumes; it never relaxes env_file,
    secrets, configs or build contexts, which are how ATTa's own files would be read."""
    if not isinstance(cfg, dict):
        raise ComposeSecurityError("compose configuration is not an object")
    base = _roots(app_root)[0]
    services = cfg.get("services") or {}
    if not isinstance(services, dict):
        raise ComposeSecurityError("compose `services` is not a map")
    for name, s in services.items():
        if not isinstance(s, dict):
            continue
        for e in (s.get("env_file") or []) if isinstance(s.get("env_file"), list) else [s.get("env_file")]:
            _require_inside(e.get("path") if isinstance(e, dict) else e, base, app_root, f"services.{name}.env_file")
        b = s.get("build")
        if isinstance(b, str):
            _require_inside(b, base, app_root, f"services.{name}.build")
        elif isinstance(b, dict):
            ctx = b.get("context") or "."
            if not re.match(r"^[a-z][a-z0-9+.-]*://", str(ctx)):   # a git/https context is fetched, not read from disk
                _require_inside(ctx, base, app_root, f"services.{name}.build.context")
                ctx_dir = Path(ctx) if os.path.isabs(str(ctx)) else base / str(ctx)
                _require_inside(b.get("dockerfile"), ctx_dir, app_root, f"services.{name}.build.dockerfile")
            for k, v in (b.get("additional_contexts") or {}).items():
                v = str(v)
                if v.startswith(("service:", "docker-image://", "oci-layout://")) or re.match(r"^[a-z][a-z0-9+.-]*://", v):
                    continue
                _require_inside(v, base, app_root, f"services.{name}.build.additional_contexts.{k}")
            if b.get("ssh"):
                raise ComposeSecurityError(f"services.{name}.build.ssh would hand the build this server's SSH agent")
    for kind in ("secrets", "configs"):
        for k, item in (cfg.get(kind) or {}).items():
            if isinstance(item, dict):
                _require_inside(item.get("file"), base, app_root, f"{kind}.{k}.file")
                if item.get("environment"):
                    # Sourced from compose's own environment. Under clean_env that holds no ATTa secret, but
                    # there is no legitimate reason for an app to read the runner's environment at all.
                    raise ComposeSecurityError(f"{kind}.{k} reads `{item['environment']}` from the server's environment")
    if not host_access:
        for k, vol in (cfg.get("volumes") or {}).items():
            opts = ((vol or {}).get("driver_opts") or {}) if isinstance(vol, dict) else {}
            if opts and _is_bind_volume(opts):
                dev = str(opts.get("device") or "")
                if not dev or not os.path.isabs(dev) or not inside_any(dev, app_root):
                    raise ComposeSecurityError(f"named volume {k} is a bind of the host path {dev or '(unset)'}")


def raw_env_files(compose_file: Path, _depth: int = 0) -> dict:
    """{"services": {name: {"env_file": [absolute paths]}}} read straight from the YAML, following `include:`
    and `extends: file:` (whose own paths are listed too, so they must be the app's files). Checked before
    compose reads anything, on every compose version (the only env_file check on a compose too old for
    `config --no-env-resolution`). Unreadable or too deep = refused, not skipped."""
    import yaml
    if _depth > 8:
        raise ComposeSecurityError("compose `include:`/`extends:` nest too deep to check")
    f = Path(compose_file)
    try:
        doc = yaml.safe_load(f.read_text(errors="replace")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ComposeSecurityError(f"cannot read {f.name} to check its env files ({type(e).__name__})")
    out: dict = {"services": {}}
    if not isinstance(doc, dict):
        return out
    def add(name, paths):
        out["services"].setdefault(name, {"env_file": []})["env_file"] += paths
    for name, svc in (doc.get("services") or {}).items() if isinstance(doc.get("services"), dict) else []:
        if not isinstance(svc, dict):
            continue
        ef = svc.get("env_file")
        items = ef if isinstance(ef, list) else [ef] if ef else []
        add(f"{f.name}:{name}", [str((f.parent / (e.get("path") if isinstance(e, dict) else e)))
                                 for e in items if isinstance(e, (str, dict)) and (e.get("path") if isinstance(e, dict) else e)])
        ext = svc.get("extends")
        if isinstance(ext, dict) and ext.get("file"):
            add(f"{f.name}:{name}:extends", [str(f.parent / str(ext["file"]))])
            for k, v in raw_env_files(f.parent / str(ext["file"]), _depth + 1)["services"].items():
                add(k, v["env_file"])
    inc = doc.get("include") or []
    for item in inc if isinstance(inc, list) else [inc]:
        paths = item.get("path") if isinstance(item, dict) else item
        for pth in (paths if isinstance(paths, list) else [paths]):
            if isinstance(pth, str) and pth and not re.match(r"^[a-z][a-z0-9+.-]*://", pth):
                inc_file = f.parent / pth
                # the included file itself must be the app's own, or it could hide anything
                out["services"].setdefault(f"{f.name}:include", {"env_file": []})["env_file"].append(str(inc_file))
                for k, v in raw_env_files(inc_file, _depth + 1)["services"].items():
                    add(k, v["env_file"])
    return out


# ------------------------------------------------------------------ 3. values

def _docker_config_secrets() -> list[str]:
    cfg = Path(os.environ.get("DOCKER_CONFIG") or Path.home() / ".docker") / "config.json"
    out = []
    try:
        d = json.loads(cfg.read_text())
    except (OSError, ValueError):
        return out
    for a in (d.get("auths") or {}).values():
        for k in ("auth", "identitytoken", "registrytoken", "password"):
            if isinstance(a, dict) and a.get(k):
                out.append(str(a[k]))
        try:
            user_pw = base64.b64decode(str(a.get("auth", ""))).decode()
            if ":" in user_pw:
                out.append(user_pw.split(":", 1)[1])
        except Exception:
            pass
    return out


def is_atta_secret_name(name: str) -> bool:
    return name in ATTA_SECRET_NAMES or bool(_SECRET_NAME.fullmatch(name or ""))


def protected_values(env_file: Path | None = None, environ: dict | None = None) -> dict[str, str]:
    """{value: where it comes from} for every ATTa secret this process can see: secret-looking entries of
    ATTa's .env, of this process's own environment (systemd loads .env into it), and Docker's stored logins.
    The label is a NAME, safe to show; the value never leaves this module except as a dict key."""
    found: dict[str, str] = {}
    env_file = ATTA_ENV_FILE if env_file is None else env_file
    try:
        vals, _ = envfile.load(str(env_file))
    except (OSError, ValueError):
        vals = {}
    for src, d in (("ATTa .env", vals), ("ATTa environment", os.environ if environ is None else environ)):
        for k, v in d.items():
            if is_atta_secret_name(k) and isinstance(v, str) and len(v.strip()) >= MIN_SECRET_LEN:
                found.setdefault(v.strip(), f"{src}: {k}")
    for v in _docker_config_secrets():
        if len(v) >= MIN_SECRET_LEN:
            found.setdefault(v, "Docker login")
    return found


def _forms(v: str) -> set[str]:
    b = v.encode()
    forms = {v, quote(v, safe=""), quote_plus(v, safe=""),
             base64.b64encode(b).decode(), base64.urlsafe_b64encode(b).decode()}
    forms |= {f.rstrip("=") for f in list(forms)}
    return {f for f in forms if len(f) >= MIN_SECRET_LEN}


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _strings(v)


def scan_rendered(cfg: dict, protected: dict[str, str]) -> None:
    """Refuse a rendered configuration that carries the value of an ATTa secret anywhere (environment,
    command, labels, names...). Customer values are not in `protected`, so they pass by construction."""
    text = "\n".join(_strings(cfg))
    for value, label in sorted(protected.items(), key=lambda kv: -len(kv[0])):
        if any(f in text for f in _forms(value)):
            raise ComposeSecurityError(f"the app's configuration contains an ATTa secret ({label.split(': ')[-1]}); "
                                       "ATTa's own credentials are never given to an app")


def scan_env(env: dict, protected: dict[str, str]) -> None:
    """The same check for a `docker run -e NAME=value` start (image and Dockerfile apps)."""
    scan_rendered({"environment": dict(env or {})}, protected)
