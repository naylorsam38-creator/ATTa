"""Operator CLI for Stage 12 (metadata only — never runs integration code).

    python -m backend.integrations validate <dir-or-integration.yaml> [--trust TRUSTED]
    python -m backend.integrations approval <dir-or-integration.yaml> [--trust TRUSTED]   # payload to sign
    python -m backend.integrations sign --key <operator.pem> --action A --subject S --payload P --operator NAME
    python -m backend.integrations diff <installed integration.yaml> <candidate integration.yaml>

The operator private key never belongs in the repository; ATTa holds only the public key."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.policy_engine.catalog import load_catalog
from backend.settings import REPO_ROOT

from .manager import approval_binding
from .manifest import IntegrationManifestError, load_integration_manifest
from .types import TrustClass
from .upgrade import compare


def _path(p: str) -> Path:
    q = Path(p)
    return q / "integration.yaml" if q.is_dir() else q


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.integrations")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "approval"):
        s = sub.add_parser(name)
        s.add_argument("path")
        s.add_argument("--trust", default="TRUSTED", choices=[t.value for t in TrustClass])
    s = sub.add_parser("sign")
    for a in ("--key", "--action", "--subject", "--payload", "--operator"):
        s.add_argument(a, required=True)
    s.add_argument("--ttl", type=float, default=600)
    s = sub.add_parser("diff")
    s.add_argument("installed")
    s.add_argument("candidate")
    a = ap.parse_args(argv)
    catalog = load_catalog(REPO_ROOT / "configuration" / "permissions.yaml")
    known = frozenset(catalog.permissions)
    try:
        if a.cmd in ("validate", "approval"):
            m = load_integration_manifest(_path(a.path), known_permissions=known)
            out = {"valid": True, "integration_id": m.integration_id, "version": m.version, "type": m.type.value,
                   "manifest_digest": m.manifest_digest, "package_digest": m.package_digest,
                   "tools": [t["name"] for t in m.tools]}
            if a.cmd == "approval":
                out.update(action="integration.install", subject=f"{m.integration_id}@{m.version}",
                           payload=approval_binding(m, TrustClass(a.trust), None),
                           note="for an upgrade use the binding returned by IntegrationManager.validate (it covers "
                                "the upgrade diff)")
            print(json.dumps(out, indent=2))
            return 0
        if a.cmd == "sign":
            from backend.orchestration.authorization import sign_authorization
            auth = sign_authorization(Path(a.key), a.action, a.subject, a.payload, a.operator, a.ttl)
            print(json.dumps(auth.__dict__, indent=2))
            return 0
        if a.cmd == "diff":
            old = load_integration_manifest(_path(a.installed), known_permissions=known)
            new = load_integration_manifest(_path(a.candidate), known_permissions=known)
            print(json.dumps(compare(old.raw, new.raw), indent=2))
            return 0
    except IntegrationManifestError as exc:
        print(json.dumps({"valid": False, "source": exc.source, "problems": exc.problems}, indent=2))
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
