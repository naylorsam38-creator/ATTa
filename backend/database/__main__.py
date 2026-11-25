import argparse
import json
import sys

from backend.settings import load_settings

from .migrate import MigrationError, migrate, status


def main() -> int:
    ap = argparse.ArgumentParser(prog="backend.database")
    ap.add_argument("command", choices=["migrate", "status"])
    ap.add_argument("--env", default=None)
    a = ap.parse_args()
    s = load_settings(a.env)
    try:
        if a.command == "migrate":
            applied = migrate(s)
            print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
        else:
            print(json.dumps(status(s), indent=2))
    except MigrationError as exc:
        print(f"MIGRATION ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"DATABASE ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
