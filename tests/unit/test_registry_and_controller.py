"""Unit tests: capability execution pipeline (with a test policy double) and build-controller path rules."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "acceptance"))

from s01_helpers import write_capability  # noqa: E402

from backend.capability_registry import CapabilityRegistry  # noqa: E402
from backend.contracts import ApiError, ErrorCode, Principal, RequestContext  # noqa: E402
from backend.verification.build_controller import glob_to_regex, matches_any  # noqa: E402


CTX = RequestContext(request_id="req_x", correlation_id="cor_unit_test", principal=Principal.anonymous())


def trusted_registry(tmp_path, dirs, **kw):
    """Registry whose trust store (operator-signed with a per-test key) trusts every package in `dirs`."""
    from backend.capability_registry import TrustStore
    from backend.capability_registry.trust import generate_keypair, trust_package
    generate_keypair(tmp_path / "k.pem", tmp_path / "k.pub")
    for d in dirs:
        trust_package(d, tmp_path / "trust.json", tmp_path / "k.pem", "unit-test")
    from backend.policy_engine import Catalog
    catalog = Catalog(permissions={"test.echo": "unit-test permission"}, controller_granted=frozenset(),
                      approval_required_risks=frozenset({"medium", "high", "critical"}))
    return CapabilityRegistry([dirs[0].parent], trust=TrustStore.load(tmp_path / "trust.json", tmp_path / "k.pub"),
                              catalog=catalog, **kw)


def test_direct_execution_is_disabled(tmp_path):
    d = write_capability(tmp_path / "caps", "echo", "test.echo")
    reg = trusted_registry(tmp_path, [d])
    reg.discover()
    assert reg.get("test.echo").executable
    with pytest.raises(ApiError) as e:
        reg.execute("test.echo", {"text": "hi"}, CTX)
    assert e.value.code is ErrorCode.CAPABILITY_UNAVAILABLE and "gateway" in e.value.message


def test_healthcheck_timeout_marks_unhealthy(tmp_path):
    d = write_capability(tmp_path / "caps", "hang", "test.hang", handler="import time\ndef execute(p, c):\n    return {}\n"
                         "def healthcheck():\n    time.sleep(5)\n    return True\ndef verify(p, o):\n    return True\n")
    reg = trusted_registry(tmp_path, [d], healthcheck_timeout_s=0.5)
    (a,) = reg.discover()
    assert a.state.value == "unhealthy" and "timeout" in a.problems[0]


def test_untrusted_and_tampered_packages_are_never_loaded(tmp_path):
    d = write_capability(tmp_path / "caps", "echo", "test.echo")
    reg = trusted_registry(tmp_path, [d])
    reg.discover()
    assert reg.get("test.echo").executable
    (d / "handler.py").write_text((d / "handler.py").read_text() + "\n# tampered\n")
    (a,) = reg.discover()
    assert a.state.value == "integrity_failed"
    write_capability(tmp_path / "caps", "other", "test.other")
    states = {x.id: x.state.value for x in reg.discover()}
    assert states["test.other"] == "untrusted"


def test_package_digest_matches_isolated_child(tmp_path):
    import importlib.util
    from backend.capability_registry.package import package_digest
    d = write_capability(tmp_path / "caps", "echo", "test.echo")
    (d / "sub").mkdir()
    (d / "sub" / "data.txt").write_text("x")
    spec = importlib.util.spec_from_file_location("child", ROOT / "backend/capability_runtime/child.py")
    child = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(child)
    assert child.snapshot(str(d))[1] == package_digest(d)


def test_dependency_cycle_is_invalid(tmp_path):
    write_capability(tmp_path, "a", "test.a", deps="{capabilities: [test.b]}")
    write_capability(tmp_path, "b", "test.b", deps="{capabilities: [test.a]}")
    by = {x.id: x for x in CapabilityRegistry([tmp_path]).discover()}
    assert by["test.a"].state.value == "invalid" and "cycle" in by["test.a"].problems[0]


@pytest.mark.parametrize("pattern,path,ok", [
    ("backend/**", "backend/api/app.py", True), ("backend/**", "backendx/a.py", False),
    ("**/*.md", "README.md", True), ("**/*.md", "docs/x/y.md", True), ("tests/test_*.py", "tests/test_a.py", True),
    ("tests/test_*.py", "tests/unit/test_a.py", False), ("START.bat", "START.bat", True), ("*.bat", "a/b.bat", False),
])
def test_glob_rules(pattern, path, ok):
    assert bool(glob_to_regex(pattern).match(path)) is ok
    assert matches_any(path, [pattern]) is ok
