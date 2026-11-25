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
from backend.policy_engine import Decision, PolicyEngine  # noqa: E402
from backend.verification.build_controller import glob_to_regex, matches_any  # noqa: E402


class Allow(PolicyEngine):
    def evaluate(self, req):
        return self._decide(Decision.ALLOW, "unit-test double", req)


CTX = RequestContext(request_id="req_x", correlation_id="cor_unit_test", principal=Principal.anonymous())


def test_execution_pipeline_validates_input_output_and_verifies(tmp_path):
    write_capability(tmp_path, "echo", "test.echo")
    write_capability(tmp_path, "liar", "test.liar", handler=textwrap.dedent("""\
        def execute(p, c):
            return {"echo": "something else"}
        def healthcheck():
            return True
        def verify(p, o):
            return {"verified": o["echo"] == p["text"]}
        """))
    write_capability(tmp_path, "badout", "test.badout", handler="def execute(p, c):\n    return {'nope': 1}\n"
                     "def healthcheck():\n    return True\ndef verify(p, o):\n    return True\n")
    write_capability(tmp_path, "slow", "test.slow", timeout_s=0.3, handler="import time\ndef execute(p, c):\n"
                     "    time.sleep(2)\n    return {'echo': ''}\ndef healthcheck():\n    return True\n"
                     "def verify(p, o):\n    return True\n")
    reg = CapabilityRegistry([tmp_path], policy=Allow())
    reg.discover()
    r = reg.execute("test.echo", {"text": "hi"}, CTX)
    assert r.output == {"echo": "hi"} and r.verified
    assert reg.execute("test.liar", {"text": "hi"}, CTX).verified is False
    with pytest.raises(ApiError) as e:
        reg.execute("test.echo", {"wrong": 1}, CTX)
    assert e.value.code is ErrorCode.VALIDATION_FAILED
    with pytest.raises(ApiError) as e:
        reg.execute("test.badout", {"text": "x"}, CTX)
    assert e.value.code is ErrorCode.INTERNAL
    with pytest.raises(ApiError) as e:
        reg.execute("test.slow", {"text": "x"}, CTX)
    assert e.value.code is ErrorCode.TIMEOUT


def test_healthcheck_timeout_marks_unhealthy(tmp_path):
    write_capability(tmp_path, "hang", "test.hang", handler="import time\ndef execute(p, c):\n    return {}\n"
                     "def healthcheck():\n    time.sleep(5)\n    return True\ndef verify(p, o):\n    return True\n")
    reg = CapabilityRegistry([tmp_path], healthcheck_timeout_s=0.3)
    (a,) = reg.discover()
    assert a.state.value == "unhealthy" and "timed out" in a.problems[0]


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
