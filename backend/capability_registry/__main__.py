"""Operator CLI for capability trust and numbering.

  python -m backend.capability_registry keygen --private /etc/atta-operator/capability_trust_ed25519.pem \\
         --public configuration/capability_trust_ed25519.pub
  python -m backend.capability_registry trust capabilities/<dir> --key <private.pem> --operator NAME
  python -m backend.capability_registry list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.settings import load_settings
from backend.settings.loader import resolve_path

from .trust import TrustStore, generate_keypair, trust_package


def main() -> int:
    ap = argparse.ArgumentParser(prog="backend.capability_registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--private", type=Path, required=True)
    k.add_argument("--public", type=Path, required=True)
    t = sub.add_parser("trust")
    t.add_argument("directory", type=Path)
    t.add_argument("--key", type=Path, required=True)
    t.add_argument("--operator", required=True)
    t.add_argument("--store", type=Path)
    sub.add_parser("list")
    a = ap.parse_args()
    s = load_settings()
    store = resolve_path(s.capabilities.trust_store)
    if a.cmd == "keygen":
        if a.private.exists():
            print("refusing to overwrite an existing private key", file=sys.stderr)
            return 2
        generate_keypair(a.private, a.public)
        print(f"private key: {a.private} (0600, keep outside the repository)\npublic key:  {a.public}")
        return 0
    if a.cmd == "trust":
        print(json.dumps(trust_package(a.directory, a.store or store, a.key, a.operator), indent=2))
        return 0
    ts = TrustStore.load(store, resolve_path(s.capabilities.trust_public_key))
    print(json.dumps({"problems": ts.problems, "entries": {k: v.__dict__ | {"permissions": sorted(v.permissions)}
                                                            for k, v in ts.entries.items()}}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
