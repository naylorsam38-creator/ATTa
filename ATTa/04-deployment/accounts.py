#!/usr/bin/env python3
"""
accounts.py — user accounts for the APP Builder gateway.

Real accounts: username + salted PBKDF2-SHA256 password hash + role.
Roles: "admin" sees every user's builds; "user" sees only their own.

Command line (run on the server, as the service user or root):

  python3 accounts.py init                 create the admin + 10 test accounts (idempotent)
  python3 accounts.py list                 list accounts (no passwords)
  python3 accounts.py add NAME [--admin]   create one account, prints its password once
  python3 accounts.py reset NAME           new password for NAME, prints it once
  python3 accounts.py disable NAME         block logins for NAME (existing sessions stop working)
  python3 accounts.py enable NAME          undo disable

Passwords are only ever printed (and, for `init`, written to TEST_ACCOUNTS.txt with 0600
permissions). The accounts file itself holds hashes only.
"""
from __future__ import annotations
import argparse, hashlib, hmac, json, os, re, secrets, sys, tempfile, threading, time
from pathlib import Path
from reserved import RESERVED_NAMES, is_reserved

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Where the app keeps its data. Same variable the gateway and pipeline use.
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
# The accounts file (hashes only). Change only if the state folder moves.
USERS_FILE = ROOT / "state" / "users.json"
# Where `init` writes the generated test-account passwords for handing out. 0600.
TEST_ACCOUNTS_FILE = ROOT / "TEST_ACCOUNTS.txt"
# How many pre-made test accounts `init` creates.
TEST_ACCOUNT_COUNT = 10
# Test account names are this prefix + a two-digit number: tester01 .. tester10.
TEST_ACCOUNT_PREFIX = "tester"
# Name of the admin account `init` creates.
ADMIN_NAME = os.environ.get("APP_BUILDER_USER", "admin")
# PBKDF2 iterations. Higher = slower to guess, slower to log in. 600k is the OWASP 2023 figure for SHA-256.
PBKDF2_ITERATIONS = 600_000
# Length of generated passwords, in random bytes (url-safe encoded, so ~1.33 chars per byte).
PASSWORD_BYTES = 12
# ==========================================================================================

ROLES = ("admin", "user")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")
_LOCK = threading.Lock()


def _hash(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations).hex()


def make_hash(password: str) -> dict:
    salt = secrets.token_bytes(16)
    return {"algo": "pbkdf2_sha256", "iterations": PBKDF2_ITERATIONS, "salt": salt.hex(),
            "hash": _hash(password, salt, PBKDF2_ITERATIONS)}


def new_password() -> str:
    return secrets.token_urlsafe(PASSWORD_BYTES)


def load() -> dict:
    try:
        d = json.loads(USERS_FILE.read_text())
        if isinstance(d, dict) and isinstance(d.get("users"), dict):
            return d
    except FileNotFoundError:
        pass
    return {"schema": "APP_BUILDER_USERS.v1", "users": {}}


def save(d: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=USERS_FILE.parent, prefix=".users.")
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=2); f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, USERS_FILE)


def get(name: str) -> dict | None:
    """The account, or None. v115: a reserved name (reserved.py) is never an account, even if the file
    has one (from before v115): it can't log in, its sessions stop, and nothing trusts it."""
    if is_reserved(name):
        return None
    return load()["users"].get(name)


def verify(name: str, password: str) -> dict | None:
    """Return the account if name+password are right and the account is enabled, else None.
    Always does one hash, even for unknown names, so response time doesn't reveal which names exist."""
    u = get(name)
    p = (u or {}).get("password") or {"salt": "00" * 16, "iterations": PBKDF2_ITERATIONS, "hash": ""}
    got = _hash(password, bytes.fromhex(p["salt"]), int(p["iterations"]))
    if u and not u.get("disabled") and hmac.compare_digest(got, p["hash"]):
        return u
    return None


def create(name: str, role: str, password: str | None = None, note: str = "") -> str:
    if not NAME_RE.match(name):
        raise ValueError(f"bad username {name!r}: 2-32 chars, lowercase letters, digits, _ . -")
    if is_reserved(name):
        raise ValueError(f"{name!r} is reserved for ATTa's own processes and can't be an account")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    password = password or new_password()
    with _LOCK:
        d = load()
        if name in d["users"]:
            raise ValueError(f"account {name!r} already exists")
        d["users"][name] = {"name": name, "role": role, "password": make_hash(password),
                            "created": time.time(), "session_version": 1, "note": note}
        save(d)
    return password


def update(name: str, **fields) -> None:
    if is_reserved(name) and fields.get("disabled") is False:
        raise ValueError(f"{name!r} is reserved for ATTa's own processes and can't be enabled")
    with _LOCK:
        d = load()
        u = d["users"].get(name)
        if not u:
            raise ValueError(f"no account {name!r}")
        u.update(fields)
        # Any change to password or enabled state invalidates that user's existing sessions.
        u["session_version"] = int(u.get("session_version", 1)) + 1
        save(d)


def reset_password(name: str) -> str:
    pw = new_password()
    update(name, password=make_hash(pw))
    return pw


def init() -> list[tuple[str, str, str]]:
    """Create the admin and the test accounts that don't exist yet. Returns (name, role, password)
    for accounts created in this run only. Existing accounts are never touched."""
    made = []
    if is_reserved(ADMIN_NAME):
        raise ValueError(f"APP_BUILDER_USER={ADMIN_NAME!r} is reserved for ATTa's own processes; choose another admin name")
    disable_reserved()
    d = load()
    if ADMIN_NAME not in d["users"]:
        # Carry over the old single shared password if the server already had one, so the
        # existing admin login keeps working after the upgrade.
        legacy = os.environ.get("APP_BUILDER_PASSWORD") or None
        made.append((ADMIN_NAME, "admin", create(ADMIN_NAME, "admin", legacy, "initial admin")))
    for i in range(1, TEST_ACCOUNT_COUNT + 1):
        name = f"{TEST_ACCOUNT_PREFIX}{i:02d}"
        if name not in load()["users"]:
            made.append((name, "user", create(name, "user", note="pre-made test account")))
    if made:
        _append_credentials(made)
    return made


def disable_reserved() -> list[str]:
    """v115: an account created before names were reserved is disabled (sessions ended, note kept).
    Returns the names disabled in this call."""
    done = []
    with _LOCK:
        d = load()
        for name, u in d["users"].items():
            if is_reserved(name) and not u.get("disabled"):
                u["disabled"] = True
                u["note"] = (u.get("note", "") + " | disabled: name reserved for ATTa's own processes (v115)").strip(" |")
                u["session_version"] = int(u.get("session_version", 1)) + 1
                done.append(name)
        if done:
            save(d)
    return done


def _append_credentials(rows) -> None:
    TEST_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    new = not TEST_ACCOUNTS_FILE.exists()
    fd = os.open(TEST_ACCOUNTS_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        if new:
            f.write("APP Builder accounts. Hand each tester ONE line. Delete this file once handed out.\n")
            f.write("Passwords are not stored anywhere else; lost ones are reset with: accounts.py reset NAME\n\n")
        f.write(f"# created {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        for name, role, pw in rows:
            f.write(f"{name:<12} {role:<6} {pw}\n")
    os.chmod(TEST_ACCOUNTS_FILE, 0o600)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="APP Builder accounts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init"); sub.add_parser("list")
    a = sub.add_parser("add"); a.add_argument("name"); a.add_argument("--admin", action="store_true")
    for c in ("reset", "disable", "enable"):
        sub.add_parser(c).add_argument("name")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "init":
            for name in disable_reserved():
                print(f"disabled {name}: the name is reserved for ATTa's own processes", file=sys.stderr)
            made = init()
            for name, role, _ in made:
                print(f"created {name} ({role})")
            print(f"{len(made)} account(s) created. Passwords: {TEST_ACCOUNTS_FILE}" if made else "all accounts already exist")
        elif args.cmd == "list":
            for u in sorted(load()["users"].values(), key=lambda u: (u["role"] != "admin", u["name"])):
                state = "RESERVED (never active)" if is_reserved(u["name"]) else "DISABLED" if u.get("disabled") else "active"
                print(f"{u['name']:<14} {u['role']:<6} {state}")
        elif args.cmd == "add":
            print(create(args.name, "admin" if args.admin else "user"))
        elif args.cmd == "reset":
            print(reset_password(args.name))
        elif args.cmd == "disable":
            update(args.name, disabled=True); print(f"{args.name} disabled")
        elif args.cmd == "enable":
            update(args.name, disabled=False); print(f"{args.name} enabled")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
