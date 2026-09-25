"""customer_secrets.py — a customer's integration tokens go to Coolify for their app; ATTa keeps a receipt.
v117 (from PR #3's v115, on the v116 base).

The customer (the account that added the app, or an admin) enters their own tokens — Stripe, PayPal, OpenAI/
Anthropic, their email service, whatever their app integrates with. This module:

  1. checks each NAME/value: a real variable name; not one of ATTa's settings prefixes or Coolify's own names;
     not one of ATTa's OWN credential values pasted by mistake (compose_guard.protected_values, the same list that
     keeps ATTa's secrets out of apps);
  2. hands the values straight to Coolify for that app's resource, over a route v116 allows for the Coolify token
     (HTTPS, or a private address; never through a redirect, which would carry the Bearer token elsewhere):
        PATCH {COOLIFY_URL}/api/v1/{applications|services}/{uuid}/envs/bulk
        {"data": [{"key", "value", "is_literal": true, "is_shown_once": true,
                   "is_runtime": true, "is_buildtime": false, "is_multiline": ...}]}
     (Coolify 4.3.23 source: ApplicationsController/ServicesController::create_bulk_envs, `write` ability, 201.)
     is_literal: Coolify never expands `$` in the value. is_buildtime false: never baked into an image layer;
  3. asks Coolify to redeploy the app so the running app picks the values up;
  4. keeps a RECEIPT only: variable name, a short keyed fingerprint, when, which build, who, where it went. The value
     is never written to disk, logged, put in a build record, a URL or a process argument, or sent anywhere else.

ATTa never reads a value back from Coolify: nothing here GETs envs, and the reply to the bulk call is reduced to the
key names at once. Give ATTa's Coolify token the `write` ability WITHOUT `read:sensitive` (Coolify then hides values
in replies anyway).

Fingerprint = first 16 hex of HMAC-SHA256(key, NAME + "\\0" + value), key = a random 32-byte file made once
(state/gateway/secret-fingerprint.key, 0600, the gateway's own). Keyed, so a copied receipt can't test guesses
offline; the NAME is in it, so one token under two names gives two fingerprints. check() recomputes it for a token
the customer supplies — ATTa never needs the old token.
"""
from __future__ import annotations
import hashlib, hmac, json, os, re, secrets, tempfile, threading, time
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError

import app_owners, compose_guard, coolify_handoff

ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
# The gateway (its own user since v116) writes only under state/gateway: receipts and the key live there.
GW_DIR = ROOT / "state" / "gateway"
RECEIPTS = GW_DIR / "customer_secrets"
KEY_FILE = GW_DIR / "secret-fingerprint.key"
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
# Names that configure ATTa itself or that Coolify fills in on its own. A customer variable can't use them.
REFUSED_PREFIXES = ("APP_BUILDER_", "ATTA_", "COOLIFY_", "SERVICE_FQDN_", "SERVICE_URL_", "SERVICE_USER_",
                    "SERVICE_PASSWORD_", "SERVICE_BASE64_", "SERVICE_REALBASE64_", "SERVICE_HEX_", "LD_", "PYTHON")
REFUSED_NAMES = ("PATH", "HOME", "HOSTNAME", "SOURCE_COMMIT", "PORT", "HOST", "NODE_OPTIONS", "BASH_ENV")
MAX_VALUE = 8192
MAX_PER_CALL = 50
FINGERPRINT_HEX = 16
_LOCK = threading.Lock()


class SecretError(ValueError):
    """A refusal to show the customer. Never contains a value."""


def _key() -> bytes:
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32) + "\n")
    except FileExistsError:
        pass
    k = KEY_FILE.read_text().strip()
    if len(k) < 32:
        raise SecretError("fingerprint key file is damaged; an admin must look at it")
    return k.encode()


def fingerprint(name: str, value: str) -> str:
    return hmac.new(_key(), f"{name}\0{value}".encode(), hashlib.sha256).hexdigest()[:FINGERPRINT_HEX]


def validate(values: dict) -> dict:
    """{NAME: value} checked. Raises SecretError naming the variable, never the value."""
    if not isinstance(values, dict) or not values:
        raise SecretError("no variables given")
    if len(values) > MAX_PER_CALL:
        raise SecretError(f"at most {MAX_PER_CALL} variables at once")
    own = compose_guard.protected_values()
    out = {}
    for name, value in values.items():
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise SecretError(f"{str(name)[:60]!r} is not a variable name (letters, digits, _; not starting with a digit)")
        up = name.upper()
        if up.startswith(REFUSED_PREFIXES) or up in REFUSED_NAMES:
            raise SecretError(f"{name} is reserved for ATTa, Coolify or the system and can't be a customer variable")
        if not isinstance(value, str) or not value:
            raise SecretError(f"{name}: empty value")
        if len(value) > MAX_VALUE:
            raise SecretError(f"{name}: value longer than {MAX_VALUE} characters")
        if "\0" in value:
            raise SecretError(f"{name}: value contains a NUL character")
        if any(v in value for v in own):
            raise SecretError(f"{name}: that value is one of this server's own credentials, not yours; nothing was sent")
        out[name] = value
    return out


def _receipt_path(app: str) -> Path:
    aid = app_owners.app_id(app)
    if not aid:
        raise SecretError("no app named")
    return RECEIPTS / f"{aid}.json"


def receipts(app: str) -> dict:
    try:
        d = json.loads(_receipt_path(app).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def names(app: str) -> list[str]:
    return sorted((receipts(app).get("vars") or {}).keys())


def _save(app: str, d: dict) -> None:
    p = _receipt_path(app)
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".receipt.")
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=2); f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def _scrub(text: str, values: dict) -> str:
    for v in values.values():
        if v:
            text = text.replace(v, "[customer value hidden]")
    return text


def _push(resource: dict, values: dict) -> tuple[bool, str]:
    """One bulk call. (ok, detail). detail never contains a value."""
    ok, why = coolify_handoff.token_route_ok()
    if not ok:
        return False, f"REFUSED: {why}"
    kind = "services" if resource.get("kind") == "service" else "applications"
    url = f"{coolify_handoff.COOLIFY_URL}/api/v1/{kind}/{resource['uuid']}/envs/bulk"
    body = json.dumps({"data": [{"key": k, "value": v, "is_literal": True, "is_shown_once": True,
                                 "is_runtime": True, "is_buildtime": False, "is_preview": False,
                                 "is_multiline": "\n" in v} for k, v in values.items()]}).encode()
    req = Request(url, data=body, method="PATCH",
                  headers={"Authorization": f"Bearer {coolify_handoff.COOLIFY_TOKEN}",
                           "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with build_opener(coolify_handoff._NoRedirect).open(req, timeout=coolify_handoff.TIMEOUT) as r:
            status = r.status
            # Only the key names are taken from the reply (it may echo values if ATTa's Coolify token was given
            # `read:sensitive`): the parsed reply is dropped right here, never stored or logged.
            try:
                got = json.loads(r.read(200000))
                keys = {e.get("key") for e in got if isinstance(e, dict)} if isinstance(got, list) else set()
            except ValueError:
                keys = set()
            got = None
            missing = [k for k in values if k not in keys]
            if status not in (200, 201) or missing:
                return False, f"HTTP {status}: Coolify did not confirm {', '.join(missing) or 'the variables'}"
            return True, f"HTTP {status}: {len(values)} variable(s) stored in Coolify"
    except HTTPError as e:
        if 300 <= e.code < 400:
            return False, f"HTTP {e.code}: Coolify answered with a redirect; not followed (the token stays here)"
        return False, _scrub(f"HTTP {e.code}: {e.read(2000).decode('utf-8', 'replace')}", values)[:600]
    except (URLError, OSError) as e:
        return False, _scrub(f"UNREACHABLE: {e}", values)[:600]


def deliver(app: str, values: dict, actor: str, build_id: str | None = None, redeploy: bool = True) -> dict:
    """Validate, hand to Coolify, redeploy, write the receipt. Returns a summary without values.
    Raises SecretError (nothing sent, nothing stored) when it can't be delivered."""
    values = validate(values)
    if not coolify_handoff.COOLIFY_URL or not coolify_handoff.COOLIFY_TOKEN:
        raise SecretError("Coolify is not configured (COOLIFY_URL / COOLIFY_TOKEN); nothing was sent or kept. "
                          "Enter the tokens again once it is.")
    aid = app_owners.app_id(app)
    resource = coolify_handoff.resource_for(aid)
    if not resource:
        raise SecretError(f"{aid} has no Coolify resource yet ({coolify_handoff.RESOURCES_FILE.name}); "
                          "nothing was sent or kept. Enter the tokens again once it has one.")
    ok, detail = _push(resource, values)
    if not ok:
        raise SecretError(f"Coolify did not take the variables ({detail}); nothing was kept")
    now = time.time()
    where = f"coolify:{resource.get('kind', 'application')}:{resource['uuid']}"
    with _LOCK:
        d = receipts(aid) or {"app": aid, "vars": {}, "history": []}
        for name, value in values.items():
            entry = {"fingerprint": fingerprint(name, value), "at": now, "build_id": build_id,
                     "by": actor, "delivered_to": where}
            d["vars"][name] = entry
            d["history"] = (d.get("history") or [])[-199:] + [{"name": name, **entry}]
        _save(aid, d)
    out = {"app": aid, "delivered": sorted(values), "to": where, "detail": detail,
           "fingerprints": {n: d["vars"][n]["fingerprint"] for n in values}}
    if redeploy:
        rok, rdetail = coolify_handoff._deploy(resource["uuid"])
        out["redeploy"] = {"ok": rok, "detail": rdetail}
    return out


def check(app: str, name: str, value: str) -> dict:
    """Does this token match what was delivered for NAME? Compares fingerprints only."""
    v = (receipts(app).get("vars") or {}).get(name)
    if not v:
        return {"recorded": False, "matches": False}
    same = isinstance(value, str) and hmac.compare_digest(fingerprint(name, value), v.get("fingerprint", ""))
    return {"recorded": True, "matches": same, "at": v.get("at"), "build_id": v.get("build_id"),
            "delivered_to": v.get("delivered_to")}
