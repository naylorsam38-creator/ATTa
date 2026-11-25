"""Security boundary tests: no leaked internals, no secrets in tracked config, handler confinement, CORS."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.api.app import create_app  # noqa: E402
from backend.capability_registry import CapabilityRegistry, ManifestError, load_manifest_file  # noqa: E402
from backend.settings import load_settings  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    s = load_settings("test", env={"ATTA__CAPABILITIES__DIRECTORIES": f'["{tmp_path}"]'})
    app = create_app(s, dependency_probe=lambda: [], registry=CapabilityRegistry([tmp_path]))

    @app.get("/v1/_boom_for_test")
    def boom():
        raise RuntimeError("secret internal detail /etc/shadow")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_unhandled_errors_do_not_leak_internals(client):
    r = client.get("/v1/_boom_for_test")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "shadow" not in r.text and "Traceback" not in r.text


def test_validation_errors_use_standard_envelope(client):
    r = client.request("POST", "/v1/capabilities/x/executions", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code in (403, 422)
    assert set(r.json()["error"]) == {"code", "message", "correlation_id", "details"}


def test_cors_rejects_unknown_origins(client):
    r = client.get("/v1/health/live", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    r = client.get("/v1/health/live", headers={"Origin": "http://127.0.0.1:3000"})
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"


def test_security_headers_present(client):
    r = client.get("/v1/health/live")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"


def test_no_credentials_in_tracked_files():
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "configuration",
                            "infrastructure", "backend", "capabilities"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    pat = re.compile(r"(password|secret|api[_-]?key|token)\s*[:=]\s*['\"]?(?!change-me|\$\{|\$|<|None|null|\s*$)[A-Za-z0-9+/_-]{8,}",
                     re.I)
    hits = []
    for f in files:
        p = ROOT / f
        if p.suffix in (".yaml", ".yml", ".env", ".example", ".tf", ".json", ".toml", ".sh") and p.is_file():
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if pat.search(line) and "lock" not in f:
                    hits.append(f"{f}:{i}: {line.strip()}")
    assert not hits, "possible credentials committed:\n" + "\n".join(hits)
    assert not (ROOT / "configuration/local.env").exists() or "configuration/local.env" not in files


def test_handler_cannot_escape_capability_directory(tmp_path):
    outside = tmp_path / "evil.py"
    outside.write_text("def execute(p, c):\n    return {}\ndef healthcheck():\n    return True\ndef verify(p, o):\n    return True\n")
    capdir = tmp_path / "caps" / "x"
    capdir.mkdir(parents=True)
    (capdir / "link.py").symlink_to(outside)
    (capdir / "capability.yaml").write_text(
        "manifest_version: 1\nid: test.escape\nversion: 1.0.0\ndescription: tries to load code outside its folder\n"
        "handlers: {execute: link:execute, healthcheck: link:healthcheck, verify: link:verify}\n"
        "input_schema: {type: object}\noutput_schema: {type: object}\npermissions: []\nrisk: low\n"
        "dependencies: {}\ntimeout_s: 5\nenabled: true\n")
    with pytest.raises(ManifestError, match="outside the capability directory"):
        load_manifest_file(capdir / "capability.yaml")
