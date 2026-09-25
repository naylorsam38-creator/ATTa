"""Who is answering on ATTa's port (v117). Shared by the gateway (answers) and atta_health.py (asks).

A plain `GET /health` answering "OK" proves only that *something* is listening: any web server, nginx's
welcome page behind a catch-all, another program that took the port. So the health check sends a random
challenge and the gateway answers with a proof only THIS installation can make: an HMAC keyed from its own
session secret (which lives in .env, readable by root and by the local launcher's user only).

The proof key is derived, never the session secret itself: session cookies are HMAC(secret, "name:ver:ts:nonce")
and a derived key HMAC(secret, KEY_LABEL) can't be turned into one (the label has no ':' so it can never be a
session value), so answering challenges gives nothing away that signs a cookie.
"""
from __future__ import annotations
import hashlib, hmac, json, re
from pathlib import Path

SERVICE = "app-builder-gateway"
PROTOCOL = "atta-health-v1"
# release.json "features" entry that says a release's gateway answers challenges (older ones don't).
FEATURE = "health_identity_v1"
KEY_LABEL = b"atta-health-proof-key-v1"
CHALLENGE_RE = re.compile(r"[A-Za-z0-9]{32,128}")
# Marker every login page carries, so a check can tell ATTa's login from any other page with a form.
LOGIN_MARKER = '<meta name="atta-page" content="login">'


def _key(secret: str) -> bytes:
    return hmac.new(secret.encode(), KEY_LABEL, hashlib.sha256).digest()


def proof(secret: str, challenge: str) -> str:
    """The answer to one challenge. Raises ValueError on a malformed challenge or a missing secret."""
    if not secret:
        raise ValueError("no session secret: this instance cannot prove who it is")
    if not CHALLENGE_RE.fullmatch(challenge or ""):
        raise ValueError("challenge must be 32-128 letters or digits")
    msg = f"{PROTOCOL}:{SERVICE}:{challenge}".encode()
    return hmac.new(_key(secret), msg, hashlib.sha256).hexdigest()


def instance_id(secret: str) -> str:
    """A public, stable name for this installation (shown in logs and status; reveals nothing)."""
    return hmac.new(_key(secret), b"instance-id", hashlib.sha256).hexdigest()[:16] if secret else ""


def verify(secret: str, challenge: str, answer: str) -> bool:
    try:
        return hmac.compare_digest(proof(secret, challenge), str(answer or ""))
    except ValueError:
        return False


def release_info(code_dir: Path) -> dict:
    """{"version", "release", "features"} of the code in code_dir (from the release.json copied beside it)."""
    code_dir = Path(code_dir)
    rj = {}
    # An installed release has release.json beside its code; a bundle run in place (laptop) has it one level up.
    for f in (code_dir / "release.json", code_dir.parent / "release.json"):
        try:
            rj = json.loads(f.read_text())
            break
        except (OSError, ValueError):
            continue
    try:
        name = code_dir.resolve().name
    except OSError:
        name = code_dir.name
    return {"version": str(rj.get("version") or "unknown"), "release": name,
            "features": [str(f) for f in rj.get("features") or []]}


def has_identity(code_dir: Path) -> bool:
    """True when the release in code_dir answers health challenges (so its checks must demand a proof)."""
    return FEATURE in release_info(code_dir)["features"]
