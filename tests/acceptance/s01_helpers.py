"""Shared helpers for the Stage 01 acceptance tests. Everything here drives the REAL system: the service manager
CLI, real processes, real ports, real PostgreSQL/Redis. Evidence for each test is written to evidence/stage_01/."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
EVIDENCE = ROOT / "evidence" / "stage_01" / "acceptance"
sys.path.insert(0, str(ROOT))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def platform_env(name: str, **overrides: str) -> dict[str, str]:
    """Environment for an isolated platform instance: own runtime dir, own free ports, ATTA_ENV=test."""
    rt = f"runtime/test/{name}-{os.getpid()}"
    env = {k: v for k, v in os.environ.items() if not k.startswith("ATTA__")}
    env.update({"ATTA_ENV": "test", "ATTA__RUNTIME__DIRECTORY": rt, "ATTA__LOGGING__DIRECTORY": f"{rt}/logs",
                "ATTA__API__PORT": str(free_port()), "ATTA__REDIS__PORT": str(free_port()),
                "ATTA__RUNTIME__MONITOR_INTERVAL_S": "0.5", "PYTHONPATH": str(ROOT)})
    env.update(overrides)
    return env


def sm(cmd: str, env: dict[str, str], *extra: str, timeout: float = 180) -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-m", "backend.service_manager", cmd, *extra], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=timeout)


def sm_json(cmd: str, env: dict[str, str], timeout: float = 180) -> tuple[int, dict]:
    cp = sm(cmd, env, "--json", timeout=timeout)
    try:
        return cp.returncode, json.loads(cp.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"{cmd} produced non-JSON output (exit {cp.returncode}):\n{cp.stdout}\n{cp.stderr}")


def state(env: dict[str, str]) -> dict:
    p = ROOT / env["ATTA__RUNTIME__DIRECTORY"] / "state" / "manager.json"
    return json.loads(p.read_text())


def get(port: int, path: str) -> httpx.Response:
    return httpx.get(f"http://127.0.0.1:{port}{path}", timeout=5, trust_env=False)


def wait_until(predicate, timeout: float = 30, interval: float = 0.25, what: str = "condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {what} (last={last!r})")


def record(test_id: str, data: dict) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"{test_id}.json").write_text(json.dumps(data, indent=2, default=str))


class Platform:
    """Start the real platform through the service manager CLI; always stop it afterwards."""

    def __init__(self, name: str, **overrides: str):
        self.env = platform_env(name, **overrides)
        self.api_port = int(self.env["ATTA__API__PORT"])
        self.redis_port = int(self.env["ATTA__REDIS__PORT"])
        self.start_result: subprocess.CompletedProcess | None = None

    def __enter__(self) -> "Platform":
        self.start_result = sm("start", self.env)
        return self

    def __exit__(self, *exc) -> None:
        sm("stop", self.env)

    @property
    def started(self) -> bool:
        return self.start_result is not None and self.start_result.returncode == 0


def write_capability(base: Path, dirname: str, cap_id: str, *, handler: str | None = None,
                     manifest_overrides: str = "", enabled: bool = True, deps: str = "{capabilities: [], services: [], python: []}",
                     timeout_s: float = 10, raw_manifest: str | None = None) -> Path:
    d = base / dirname
    d.mkdir(parents=True, exist_ok=True)
    manifest = raw_manifest if raw_manifest is not None else textwrap.dedent(f"""\
        manifest_version: 1
        id: {cap_id}
        version: 1.0.0
        description: Acceptance-test capability {cap_id} that echoes its input text.
        requirement: CAP-40
        handlers: {{execute: handler:execute, healthcheck: handler:healthcheck, verify: handler:verify}}
        input_schema: {{type: object, required: [text], properties: {{text: {{type: string}}}}}}
        output_schema: {{type: object, required: [echo], properties: {{echo: {{type: string}}}}}}
        permissions: [test.echo]
        risk: low
        dependencies: {deps}
        timeout_s: {timeout_s}
        enabled: {str(enabled).lower()}
        """) + manifest_overrides
    (d / "capability.yaml").write_text(manifest)
    (d / "handler.py").write_text(handler if handler is not None else textwrap.dedent("""\
        def execute(payload, context):
            return {"echo": payload["text"]}

        def healthcheck():
            return {"healthy": True}

        def verify(payload, output):
            return {"verified": output["echo"] == payload["text"]}
        """))
    return d
