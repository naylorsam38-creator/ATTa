"""Environment manifest (Stage 01B §4): what this machine is, what it can run, and which profile was chosen.

Integrity: every manifest carries a SHA-256 over its canonical content and, when the operator provides a
deployment signing key (ATTA_DEPLOY_SIGNING_KEY_FILE, outside the repository), an Ed25519 signature verified with
configuration/deployment_ed25519.pub. Plaintext credentials are never written (see `redact`)."""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

from backend.contracts import canonical_hash, iso_utc, new_id

MANIFEST_VERSION = 1
SECRET_KEY = re.compile(r"(pass(word)?|secret|token|api[_-]?key|credential|private)", re.I)


class ManifestError(ValueError):
    pass


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if SECRET_KEY.search(str(k)) and v not in (None, "", False) else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def build(env: dict, decision, models: list[dict], settings, *, configuration_source: dict,
          qualification_status: str = "NOT_QUALIFIED") -> dict:
    svc = env.get("services", {})
    required = [n for n, s in settings.services.items() if s.essential and s.mode != "disabled"]
    gpus = env.get("gpu", {}).get("gpus", [])
    rt = env.get("runtimes", {})
    available_rt = [k for k in ("llamacpp", "ollama") if (rt.get(k) or {}).get("binary")] + \
        (["vllm"] if (rt.get("vllm") or {}).get("python_package") else []) + \
        sorted(n for n, e in (rt.get("endpoints") or {}).items() if e.get("reachable"))
    body = {
        "manifest_version": MANIFEST_VERSION,
        "environment_id": new_id("svc").replace("svc_", "env_"),
        "profile": decision.selected,
        "profile_requested": decision.requested,
        "profile_compatible": decision.compatible,
        "profile_problems": decision.reasons,
        "operating_system": env["operating_system"],
        "architecture": env["architecture"],
        "host_id": env["host_id"],
        "cpu": env["cpu"],
        "ram": env["ram"],
        "storage": env["storage"],
        "gpu": [{k: g[k] for k in ("name", "vram_mb", "id")} for g in gpus],
        "gpu_vram": sum(g["vram_mb"] for g in gpus),
        "nvidia_driver": env.get("gpu", {}).get("driver"),
        "cuda_status": env.get("gpu", {}).get("cuda") or ("unavailable: " + (env.get("gpu", {}).get("reason") or "")),
        "available_model_runtimes": available_rt,
        "available_models": [{k: m[k] for k in ("model_id", "format", "size_bytes", "fingerprint", "compatible_runtimes",
                                                "status")} for m in models],
        "required_services": required,
        "available_services": sorted(n for n, s in svc.items() if isinstance(s, dict) and s.get("healthy")),
        "unavailable_services": sorted(n for n, s in svc.items() if isinstance(s, dict) and "healthy" in s
                                       and not s["healthy"]),
        "ports": env["ports"],
        "isolation_capabilities": env["isolation"],
        "offline_capability": offline_capability(env),
        "resources": decision.resources,
        "tools": env["tools"],
        "docker": env["docker"],
        "configuration_source": configuration_source,
        "discovery_timestamp": iso_utc(),
        "qualification_status": qualification_status,
    }
    body = redact(body)
    body["content_sha256"] = canonical_hash({k: v for k, v in body.items() if k != "content_sha256"})
    return body


def offline_capability(env: dict) -> dict:
    """What can run with no Internet: core services local, no external inference."""
    iso = env.get("isolation", {})
    return {"core_local_services": True, "external_inference_required": False,
            "network_isolation_for_qualification": bool(iso.get("network_namespace")),
            "notes": "core platform is offline-capable once dependencies are provisioned; features needing the "
                     "Internet report OFFLINE_UNAVAILABLE"}


def _sign(body: dict) -> str | None:
    kp = os.environ.get("ATTA_DEPLOY_SIGNING_KEY_FILE")
    if not kp:
        return None
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(Path(kp).read_bytes(), password=None)
    return base64.b64encode(key.sign(body["content_sha256"].encode())).decode()


def write(body: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    sig = _sign(body)
    doc = {**body, "signature_ed25519": sig}
    path = directory / "environment_manifest.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True))
    os.replace(tmp, path)
    hist = directory / "manifests"
    hist.mkdir(exist_ok=True)
    (hist / f"{body['environment_id']}.json").write_text(json.dumps(doc, indent=2, sort_keys=True))
    return path


def verify(path: Path, public_key: Path | None = None) -> dict:
    """Load and verify a manifest. Raises ManifestError if content or signature does not match."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ManifestError(f"manifest unreadable: {exc}") from exc
    sig = doc.pop("signature_ed25519", None)
    claimed = doc.get("content_sha256")
    actual = canonical_hash({k: v for k, v in doc.items() if k != "content_sha256"})
    if claimed != actual:
        raise ManifestError("environment manifest content does not match its SHA-256 (modified)")
    if public_key is not None and public_key.is_file():
        if not sig:
            raise ManifestError("environment manifest is not signed but a deployment public key is installed")
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        try:
            serialization.load_pem_public_key(public_key.read_bytes()).verify(base64.b64decode(sig), claimed.encode())
        except (InvalidSignature, ValueError) as exc:
            raise ManifestError("environment manifest signature invalid") from exc
    doc["signature_ed25519"] = sig
    return doc
