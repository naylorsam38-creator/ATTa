#!/usr/bin/env python3
"""
coolify_provision.py — v117: give every QUALIFIED app its own home in Coolify, automatically.

Before v117 the hand-off only deployed apps someone had already created in Coolify by hand
(coolify_resources.json). Now, when a qualified app has no Coolify resource yet, ATTa makes one.

What runs in Coolify is exactly what passed the six-stage check (browser stage included):
  * the recipe that qualified (state/runner/recipes/<app>.json) decides how it runs,
  * a compose (docker-compose) file is written from that recipe, with the same fence the
    test run had (no-new-privileges, pid limit, dropped capabilities, memory/cpu limits),
  * it is created in Coolify as ONE service per app, so each app lives in its own
    containers on its own, and is deployed straight away.

The images used are the ones ATTa already pulled or built while qualifying. ATTa runs on the
same Docker host as Coolify (it is deployed BY Coolify, see 05-coolify/kit/scripts/deploy-atta.sh),
so Coolify starts them without pulling. A locally built image is re-tagged per qualified build
(atta-qualified/<app>:<build>) so a later test rebuild never changes what is live.

Coolify side (checked against the supplied Coolify 4.3.23 source):
  GET  /api/v1/servers, /api/v1/projects, /api/v1/services     read
  POST /api/v1/projects                                         {name}                -> {uuid}
  POST /api/v1/services   {project_uuid, server_uuid, environment_name, name,
                           docker_compose_raw (base64), instant_deploy: true}         -> {uuid, domains}
  PATCH /api/v1/services/<uuid> {docker_compose_raw (base64)}   a re-qualified app gets its new compose
The app runs with its skin + capability adapter proxy in front (service atta-skin), exactly as it
was checked; only that proxy is published, through Coolify's own proxy with SERVICE_URL_ATTA_SKIN_8100,
which gives the app its own address (your wildcard domain, or sslip.io if none is set).

LIVE ENVIRONMENT: images ATTa built and the skin files are on THIS server's disk, so Coolify must
deploy to the same server ATTa runs on (the default: Coolify's "localhost" server). Set
COOLIFY_SERVER_UUID only to another server that shares /srv/app-builder and the images.
"""
from __future__ import annotations
import base64, json, os, re, subprocess
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ===================== CONFIG — edit here, nothing below needs reading =====================
# true = a qualified app with no Coolify resource gets one made for it. false = pre-v117 behaviour.
AUTOCREATE = os.environ.get("COOLIFY_AUTOCREATE", "true").lower() in ("1", "true", "yes", "on")
# Coolify project the apps go into (made if missing), and its environment.
PROJECT_NAME = os.environ.get("COOLIFY_PROJECT_NAME", "ATTa Apps")
PROJECT_UUID = os.environ.get("COOLIFY_PROJECT_UUID", "")
ENVIRONMENT = os.environ.get("COOLIFY_ENVIRONMENT", "production")
# Server the apps run on. Blank = the first server Coolify lists (on a one-box install: localhost).
SERVER_UUID = os.environ.get("COOLIFY_SERVER_UUID", "")
# Every Coolify resource ATTa makes is named <prefix><app id>, so it can always find its own again.
NAME_PREFIX = os.environ.get("COOLIFY_NAME_PREFIX", "atta-")
# Per-app limits in Coolify (same idea as the test run's fence).
LIMIT_MEMORY = os.environ.get("COOLIFY_APP_MEMORY", "2g")
LIMIT_CPUS = os.environ.get("COOLIFY_APP_CPUS", "2")
# The skin + capability adapter proxy goes in front of every app live, exactly as in its check
# (watcher stages 3-5 test the app THROUGH it). false = ship the bare app (not what qualified).
SKIN = os.environ.get("COOLIFY_SKIN", "true").lower() in ("1", "true", "yes", "on")
# Image the skin proxy runs in. Inside Coolify this is ATTa's own image (same Node the check used,
# already on the server, nothing to pull). Elsewhere a stock Node image.
SKIN_IMAGE = os.environ.get("ATTA_IMAGE") or os.environ.get("COOLIFY_SKIN_IMAGE", "node:20-alpine")
SKIN_PORT = 8100
SKIN_SERVICE = "atta-skin"
# Settings the skin proxy reads; passed on to the live copy when ATTa has them, as in the check.
SKIN_ENV = ("APP_BUILDER_CUSTOMER_ID", "APP_BUILDER_NO_INJECT_PATHS", "APP_BUILDER_GATEWAY_SECRET")
# ==========================================================================================

_TARGET: dict = {}


class ProvisionError(RuntimeError):
    """This app can't be given a Coolify home (yet). The message says why, in plain words."""


# ------------------------------------------------------------------ Coolify API
def _api(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    import coolify_handoff as h
    data = json.dumps(body).encode() if body is not None else None
    req = Request(f"{h.COOLIFY_URL}/api/v1{path}", data=data, method=method,
                  headers={"Authorization": f"Bearer {h.COOLIFY_TOKEN}", "Accept": "application/json",
                           "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=h.TIMEOUT) as r:
            raw = r.read(2_000_000).decode("utf-8", "replace")
            status = r.status
    except HTTPError as e:
        raw, status = e.read(20000).decode("utf-8", "replace"), e.code
    except (URLError, OSError) as e:
        raise ProvisionError(f"UNREACHABLE: {e}")
    try:
        return status, json.loads(raw) if raw.strip() else {}
    except ValueError:
        return status, raw


def _as_list(x) -> list:
    """Coolify list endpoints answer a plain JSON array. Accept {data: [...]} too, never crash."""
    if isinstance(x, list):
        return x
    if isinstance(x, dict) and isinstance(x.get("data"), list):
        return x["data"]
    return []


def target() -> dict:
    """{project_uuid, server_uuid, environment_name}; found once, then remembered."""
    if _TARGET:
        return _TARGET
    server = SERVER_UUID
    if not server:
        st, body = _api("GET", "/servers")
        servers = _as_list(body) if st == 200 else []
        if not servers:
            raise ProvisionError(f"Coolify listed no servers (HTTP {st})")
        local = [s for s in servers if str(s.get("name", "")).lower() == "localhost"]
        server = (local or servers)[0].get("uuid", "")
    project = PROJECT_UUID
    if not project:
        st, body = _api("GET", "/projects")
        found = [p for p in _as_list(body) if p.get("name") == PROJECT_NAME] if st == 200 else []
        if found:
            project = found[0].get("uuid", "")
        else:
            st, body = _api("POST", "/projects", {"name": PROJECT_NAME,
                                                  "description": "Apps qualified by ATTa. Made and updated by ATTa."})
            if st not in (200, 201) or not isinstance(body, dict) or not body.get("uuid"):
                raise ProvisionError(f"could not create Coolify project {PROJECT_NAME!r} (HTTP {st}: {str(body)[:300]}). "
                                     "The ATTa token needs read, write and deploy (05-coolify/kit/scripts/connect-atta.sh).")
            project = body["uuid"]
    if not server or not project:
        raise ProvisionError("could not work out the Coolify project and server to use")
    _TARGET.update(project_uuid=project, server_uuid=server, environment_name=ENVIRONMENT)
    return _TARGET


def resource_name(app: str) -> str:
    import app_runner
    return NAME_PREFIX + app_runner.safe_id(app)


def find_existing(app: str) -> str | None:
    """UUID of a Coolify service ATTa made earlier for this app (e.g. before a crash lost the record)."""
    st, body = _api("GET", "/services")
    if st != 200:
        return None
    want = resource_name(app)
    hit = [s for s in _as_list(body) if s.get("name") == want]
    return hit[0].get("uuid") if hit else None


# ------------------------------------------------------------------ skin + adapter in front
def _skin_front(app: str, host: str, port: int, build_id: str) -> dict:
    """The app's skin/capability proxy as a compose service, pointed at the app inside its own
    Coolify network. Its files are copied per qualified build, so a later repair or re-check of
    the library copy never changes what is live."""
    import shutil, app_runner
    aid = app_runner.safe_id(app)
    try:
        ui = app_runner.app_dir(app) / ".ui-capability"
    except FileNotFoundError:
        ui = app_runner.LIB / app / ".ui-capability"
    if not (ui / "ui-bridge" / "proxy.js").is_file():
        raise ProvisionError(f"its skin/adapter overlay is missing ({ui}); it must be installed as in the check (S03)")
    snap = app_runner.ROOT / "state" / "coolify" / "live" / aid / re.sub(r"[^A-Za-z0-9_.-]", "-", build_id) / ".ui-capability"
    if not snap.is_dir():
        tmp = snap.with_name(".ui-capability.tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(ui, tmp, symlinks=False)
        tmp.rename(snap)
    env = {k: os.environ[k] for k in SKIN_ENV if os.environ.get(k)}
    env[f"SERVICE_URL_{_svc_key(SKIN_SERVICE)}_{SKIN_PORT}"] = ""
    svc = {"image": SKIN_IMAGE, "restart": "unless-stopped", "depends_on": [host],
           "command": ["node", f"{snap}/ui-bridge/proxy.js", "--app-id", aid, "--target", f"http://{host}:{int(port)}",
                       "--port", str(SKIN_PORT), "--host", "0.0.0.0",
                       "--customer-id", os.environ.get("APP_BUILDER_CUSTOMER_ID", "default")],
           # Host path == container path: ATTa's data folder is mounted at the same path on the host.
           "volumes": [f"{snap}:{snap}:ro"], "environment": env,
           "security_opt": ["no-new-privileges:true"], "cap_drop": ["ALL"], "pids_limit": 256, "mem_limit": "256m"}
    if SKIN_IMAGE == os.environ.get("ATTA_IMAGE"):
        svc["pull_policy"] = "never"
    return svc


def _put_skin_in_front(out: dict, web: str | None, port: int | None, app: str, build_id: str, skin) -> dict:
    if not SKIN or skin is None:
        return out
    if not web or not port:
        raise ProvisionError("no web port known, so its skin/adapter proxy can't be put in front of it")
    env = out["services"][web].get("environment") or {}
    for k in [k for k in env if k.startswith("SERVICE_URL_")]:
        env.pop(k)                  # the address belongs to the skin proxy; the app itself stays inside
    if env:
        out["services"][web]["environment"] = env
    else:
        out["services"][web].pop("environment", None)
    if SKIN_SERVICE in out["services"]:
        raise ProvisionError(f"the app already has a service called {SKIN_SERVICE}")
    out["services"][SKIN_SERVICE] = skin(app, web, port, build_id)
    return out


# ------------------------------------------------------------------ recipe -> compose
def _svc_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_") or "APP"


def _fence(part: dict) -> dict:
    """The same fence the test run used (app_runner.harden_args), as compose keys."""
    import app_runner as r
    s: dict = {"restart": "unless-stopped"}
    if r.HARDEN:
        s["security_opt"] = ["no-new-privileges:true"]
        s["pids_limit"] = int(r.PIDS_LIMIT)
        if not part.get("relaxed_caps"):
            s["cap_drop"] = ["ALL"]
            s["cap_add"] = list(r.SAFE_CAPS)
    if LIMIT_CPUS and LIMIT_CPUS != "0":
        s["cpus"] = LIMIT_CPUS
    if LIMIT_MEMORY:
        s["mem_limit"] = LIMIT_MEMORY
    return s


def _image_ports(image: str) -> list[int]:
    try:
        out = subprocess.run(["docker", "image", "inspect", image, "--format", "{{json .Config.ExposedPorts}}"],
                             capture_output=True, text=True, timeout=30)
        d = json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() not in ("", "null") else {}
        return sorted({int(k.split("/")[0]) for k in (d or {}) if k.endswith("/tcp")})
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def _pin_local_image(image: str, app: str, build_id: str) -> str:
    """A locally built image gets a per-build tag, so later test rebuilds never change the live app."""
    import app_runner
    tag = f"atta-qualified/{app_runner.safe_id(app)}:{re.sub(r'[^A-Za-z0-9_.-]', '-', build_id)[:120] or 'latest'}"
    r = subprocess.run(["docker", "tag", image, tag], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise ProvisionError(f"could not tag the qualified image {image} ({(r.stderr or r.stdout).strip()[:300]})")
    return tag


def compose_for(app: str, recipe: dict, build_id: str, qualified_port: int | None = None,
                rendered: dict | None = None, image_ports=_image_ports, pin=_pin_local_image,
                skin=_skin_front) -> tuple[dict, str | None]:
    """The compose file Coolify runs for one app, and the service that serves its pages.
    Pure apart from the two docker helpers passed in (tests replace them)."""
    kind = recipe.get("kind")
    if kind == "compose":
        if not rendered or not isinstance(rendered.get("services"), dict):
            raise ProvisionError("the qualified compose file is missing (state/runner/work/<app>/compose.rendered.json); "
                                 "it is written when the app is started, so re-run its check")
        services, web, web_port = {}, recipe.get("service"), None
        for name, s in rendered["services"].items():
            s = {k: v for k, v in s.items() if k not in ("container_name", "networks", "ports")}
            for p in (rendered["services"][name].get("ports") or []):
                t = p.get("target") if isinstance(p, dict) else None
                if t and (web in (None, name)) and web_port is None:
                    web, web_port = name, int(t)
            services[name] = s
        if web and web not in services:
            web = None
        port = int(recipe.get("port") or web_port) if web and (recipe.get("port") or web_port) else None
        if port:
            env = services[web].get("environment") or {}
            if isinstance(env, list):
                env = dict(e.split("=", 1) if "=" in e else (e, "") for e in env)
            env[f"SERVICE_URL_{_svc_key(web)}_{port}"] = ""
            services[web]["environment"] = env
        out = {"services": services}
        vols = {k: ({kk: vv for kk, vv in (v or {}).items() if kk != "name"} or None)
                for k, v in (rendered.get("volumes") or {}).items()}
        if vols:
            out["volumes"] = vols
        out = _put_skin_in_front(out, web, port, app, build_id, skin)
        return out, (SKIN_SERVICE if SKIN and skin else web)
    if kind in ("image", "dockerfile"):
        import app_runner
        if kind == "dockerfile":
            image = pin(f"atta-local/{app_runner.safe_id(app)}:latest", app, build_id)
        else:
            image = recipe.get("image")
            if not image:
                raise ProvisionError("its recipe names no image")
        s = {"image": image, **_fence(recipe)}
        if kind == "dockerfile":
            s["pull_policy"] = "never"     # built here while qualifying; there is nothing to pull
        env = dict(recipe.get("env") or {})
        port = recipe.get("port") or qualified_port or (image_ports(image) or [None])[0]
        if port:
            env[f"SERVICE_URL_APP_{int(port)}"] = ""
        if env:
            s["environment"] = env
        vols = [v for v in (recipe.get("volumes") or []) if isinstance(v, str) and v.startswith("/") and ":" not in v]
        if vols:
            s["volumes"] = vols
        if recipe.get("command"):
            s["command"] = [str(c) for c in recipe["command"]]
        out = _put_skin_in_front({"services": {"app": s}}, "app" if port else None, port, app, build_id, skin)
        return out, (SKIN_SERVICE if SKIN and skin else ("app" if port else None))
    raise ProvisionError(f"recipe kind {kind!r} has no Coolify form yet")


# ------------------------------------------------------------------ tiny YAML writer
def _yscalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    return json.dumps(str(v))          # a JSON string is a valid double-quoted YAML scalar


def to_yaml(x, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(x, dict):
        if not x:
            return pad + "{}\n"
        out = []
        for k, v in x.items():
            key = json.dumps(str(k))
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{key}:\n{to_yaml(v, indent + 1)}")
            else:
                out.append(f"{pad}{key}: {_yscalar(v) if not isinstance(v, (dict, list)) else ('{}' if isinstance(v, dict) else '[]')}\n")
        return "".join(out)
    if isinstance(x, list):
        out = []
        for v in x:
            if isinstance(v, (dict, list)) and v:
                body = to_yaml(v, indent + 1).lstrip()
                out.append(f"{pad}- {body}")
            else:
                out.append(f"{pad}- {_yscalar(v) if not isinstance(v, (dict, list)) else '{}'}\n")
        return "".join(out)
    return pad + _yscalar(x) + "\n"


# ------------------------------------------------------------------ the one call the hand-off makes
def provision(app: str, build_id: str, qualified: dict, existing_uuid: str | None = None) -> dict:
    """Make (or update) this app's own Coolify service and start it.
    Returns {uuid, url, created}. Raises ProvisionError with a plain reason when it can't."""
    import app_runner
    recipe = app_runner.load_recipe(app)
    if not recipe:
        raise ProvisionError("no saved recipe: it only exists once the app has passed its check (S12)")
    rendered = None
    if recipe.get("kind") == "compose":
        f = app_runner.WORK / app_runner.safe_id(app) / "compose.rendered.json"
        try:
            rendered = json.loads(f.read_text())
        except (OSError, ValueError):
            rendered = None
    compose, web = compose_for(app, recipe, build_id, rendered=rendered)
    raw = base64.b64encode(to_yaml(compose).encode()).decode()
    uuid = existing_uuid or find_existing(app)
    if uuid:
        st, body = _api("PATCH", f"/services/{uuid}", {"docker_compose_raw": raw})
        if st not in (200, 201):
            raise ProvisionError(f"Coolify refused the updated compose for {resource_name(app)} (HTTP {st}: {str(body)[:400]})")
        return {"uuid": uuid, "url": None, "created": False, "web_service": web}
    t = target()
    st, body = _api("POST", "/services", {**t, "name": resource_name(app), "instant_deploy": True,
                                          "description": f"{app}: qualified by ATTa build {build_id}",
                                          "docker_compose_raw": raw})
    if st not in (200, 201) or not isinstance(body, dict) or not body.get("uuid"):
        raise ProvisionError(f"Coolify refused to create {resource_name(app)} (HTTP {st}: {str(body)[:400]})")
    doms = body.get("domains") or []
    return {"uuid": body["uuid"], "url": (doms[0] if isinstance(doms, list) and doms else None) or None,
            "created": True, "web_service": web}
