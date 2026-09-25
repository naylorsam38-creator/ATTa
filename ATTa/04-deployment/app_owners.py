"""app_owners.py — who added each library app, so per-app data is shown only to them (and admins). v117 (from PR #3).

Why: every build re-checks the WHOLE catalogue (v114.2), so every build record names every app. v116 let a user see
an app's evidence when one of their builds named it — which, with the whole catalogue in every build, was everyone's.
Now per-app data (stage-6 evidence, checklist rows, discovered apps, customer tokens) belongs to the account that
added the app to the library, and admins.

Written by the pipeline when an upload or a git address adds a NEW app (state/app_owners.json; the pipeline's user,
group-readable so the gateway can read it). An app with no record — in the library before this version, a seed repo,
or one placed on the server itself — is admin-only: nobody is guessed as its owner. An admin assigns one:

    python3 app_owners.py list
    python3 app_owners.py set APP ACCOUNT      (the account must exist; replaces any earlier owner)
"""
from __future__ import annotations
import json, os, re, tempfile, threading, time
from pathlib import Path

ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
FILE = ROOT / "state" / "app_owners.json"
_LOCK = threading.Lock()


def app_id(name) -> str:
    """Same normalisation the library, runner and evidence folders use."""
    return re.sub(r"[^a-z0-9-]+", "-", str(name or "").lower()).strip("-")


def _load() -> dict:
    try:
        d = json.loads(FILE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record(app: str, owner: str, build_id: str | None, replace: bool = False) -> None:
    """First owner wins (who added it to the library) unless replace=True."""
    aid = app_id(app)
    if not aid or not isinstance(owner, str) or not owner:
        return
    with _LOCK:
        d = _load()
        if aid in d and not replace:
            return
        d[aid] = {"owner": owner, "build_id": build_id, "at": time.time()}
        FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=FILE.parent, prefix=".app_owners.")
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, indent=2); f.write("\n")
        os.chmod(tmp, 0o640)            # the gateway (same group) reads it; nobody else
        os.replace(tmp, FILE)


def get(app: str) -> dict | None:
    return _load().get(app_id(app))


def can_access(account: dict | None, app: str) -> bool:
    """Admins: every app. Anyone else: only apps they added. Unknown owner: admins only."""
    if not account:
        return False
    if account.get("role") == "admin":
        return True
    rec = get(app)
    return bool(rec) and rec.get("owner") == account.get("name")


def main(argv=None) -> int:
    import argparse, sys
    import accounts
    ap = argparse.ArgumentParser(description="who owns each library app")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    st = sub.add_parser("set"); st.add_argument("app"); st.add_argument("account")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for k, v in sorted(_load().items()):
            print(f"{k:<30} {v.get('owner'):<14} {v.get('build_id') or '-'}")
        return 0
    if not accounts.get(a.account):
        print(f"error: no account {a.account!r} (reserved names are never accounts)", file=sys.stderr); return 1
    if not (ROOT / "library" / app_id(a.app)).is_dir():
        print(f"error: no app {app_id(a.app)!r} in {ROOT / 'library'}", file=sys.stderr); return 1
    record(a.app, a.account, None, replace=True)
    print(f"{app_id(a.app)} now owned by {a.account}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
