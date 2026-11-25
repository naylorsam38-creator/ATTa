"""Operator-signed trust store for capability packages (correction 4).

configuration/capability_trust.json lists, per capability id: version, package digest, approved permissions and
risk, signed with the operator's Ed25519 private key (kept outside the repository, readable only by the operator).
The API holds only the public key, so it can verify trust but never create it. A package whose bytes, version,
permissions or risk differ from its trusted record is never loaded."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from backend.contracts import canonical_json


@dataclass(frozen=True)
class TrustEntry:
    id: str
    version: str
    package_digest: str
    permissions: frozenset[str]
    risk: str
    trusted_by: str
    trusted_at: str


class TrustStore:
    def __init__(self, entries: dict[str, TrustEntry], problems: list[str], source: str):
        self.entries, self.problems, self.source = entries, problems, source

    @classmethod
    def empty(cls, reason: str = "no trust store configured") -> "TrustStore":
        return cls({}, [reason], "<none>")

    @classmethod
    def load(cls, store_path: Path, public_key_path: Path) -> "TrustStore":
        if not store_path.is_file():
            return cls({}, [f"trust store {store_path} not found: every capability is untrusted"], str(store_path))
        if not public_key_path.is_file():
            return cls({}, [f"trust public key {public_key_path} not found: every capability is untrusted"],
                       str(store_path))
        try:
            doc = json.loads(store_path.read_text(encoding="utf-8"))
            pub = serialization.load_pem_public_key(public_key_path.read_bytes())
            if not isinstance(pub, Ed25519PublicKey):
                raise ValueError("trust public key is not Ed25519")
            pub.verify(base64.b64decode(doc["signature_ed25519"]), canonical_json(doc["entries"]).encode())
        except (InvalidSignature, KeyError, ValueError, json.JSONDecodeError) as exc:
            return cls({}, [f"trust store signature invalid ({type(exc).__name__}): every capability is untrusted"],
                       str(store_path))
        entries = {k: TrustEntry(id=k, version=v["version"], package_digest=v["package_digest"],
                                 permissions=frozenset(v["permissions"]), risk=v["risk"], trusted_by=v["trusted_by"],
                                 trusted_at=v["trusted_at"]) for k, v in doc["entries"].items()}
        return cls(entries, [], str(store_path))

    def check(self, manifest, digest: str) -> tuple[str | None, list[str]]:
        """Return (state, problems): None if trusted, else 'untrusted' or 'integrity_failed'."""
        if self.problems and not self.entries:
            return "untrusted", list(self.problems)
        e = self.entries.get(manifest.id)
        if e is None:
            return "untrusted", [f"capability '{manifest.id}' is not in the operator trust store"]
        problems = []
        if e.version != manifest.version:
            problems.append(f"version {manifest.version} differs from trusted version {e.version}")
        if e.package_digest != digest:
            problems.append("package contents differ from the trusted digest (modified after trust)")
        if problems:
            return "integrity_failed", problems
        extra = sorted(set(manifest.permissions) - e.permissions)
        if extra:
            return "untrusted", [f"permissions not approved by the operator: {extra}"]
        if e.risk != manifest.risk.value:
            return "untrusted", [f"risk {manifest.risk.value} differs from the approved risk {e.risk}"]
        return None, []


def generate_keypair(private_path: Path, public_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    private_path.chmod(0o600)
    public_path.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                          serialization.PublicFormat.SubjectPublicKeyInfo))


def trust_package(cap_dir: Path, store_path: Path, private_key_path: Path, operator: str,
                  approved_permissions: list[str] | None = None) -> dict:
    """Operator action: record a package as trusted (re-signs the whole store)."""
    from .manifest import MANIFEST_FILENAME, load_manifest_file
    from .package import package_digest
    m = load_manifest_file(cap_dir / MANIFEST_FILENAME)
    key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("operator key is not Ed25519")
    doc = json.loads(store_path.read_text()) if store_path.is_file() else {"entries": {}}
    perms = sorted(approved_permissions if approved_permissions is not None else m.permissions)
    entry = {"version": m.version, "package_digest": package_digest(cap_dir), "permissions": perms,
             "risk": m.risk.value, "trusted_by": operator,
             "trusted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    doc["entries"][m.id] = entry
    doc["signature_ed25519"] = base64.b64encode(key.sign(canonical_json(doc["entries"]).encode())).decode()
    doc["format"] = "atta-capability-trust/1"
    store_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return {"id": m.id, **entry}
