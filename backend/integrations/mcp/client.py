"""Host-side MCP client: builds supervised connections from an APPROVED manifest and runs bounded operations.

Used for capability negotiation and discovery at install verification, and for live health checks. Tool calls on
behalf of a task never come through here: they run in the Stage 01A supervised runtime via the generated bridge,
so they are subject to the task contract, drift guard, policy, budgets and the gateway."""
from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from ..network import NetworkPolicy
from . import wire
from .wire import McpError, McpSession, McpTransportError


def stdio_argv(command: list[str], package_dir: Path | None) -> list[str]:
    argv = list(command)
    if argv and argv[0] == "{python}":
        argv[0] = sys.executable
        argv.insert(1, "-I")
    return argv


@contextmanager
def open_session(manifest_raw: dict, package_dir: Path | None, policy: NetworkPolicy, *, request_timeout_s: float,
                 startup_timeout_s: float):
    mcp = manifest_raw["mcp"]
    if mcp["transport"] == "stdio":
        conn = wire.StdioConnection(stdio_argv(mcp["command"], package_dir), cwd=str(package_dir) if package_dir
                                    else None, startup_timeout_s=startup_timeout_s)
    else:
        host = urlparse(mcp["url"]).hostname or ""
        ok, why = policy.host_allowed(host, tuple(manifest_raw["network_requirements"]["hosts"]))
        if not ok:
            raise McpTransportError(f"network policy: {why}")
        conn = wire.HttpConnection(mcp["url"], timeout_s=request_timeout_s)
    s = McpSession(conn, request_timeout_s=mcp.get("request_timeout_s", request_timeout_s))
    try:
        yield s
    finally:
        s.close()


def negotiate_and_discover(manifest_raw: dict, package_dir: Path | None, policy: NetworkPolicy, *,
                           request_timeout_s: float, startup_timeout_s: float,
                           cancel: threading.Event | None = None) -> dict:
    """initialize -> tools/list -> resources/list -> prompts/list. Raises McpError subclasses on failure."""
    with open_session(manifest_raw, package_dir, policy, request_timeout_s=request_timeout_s,
                      startup_timeout_s=startup_timeout_s) as s:
        if cancel is not None:
            threading.Thread(target=lambda: (cancel.wait(), s.cancel()), daemon=True).start()
        versions = tuple(manifest_raw["mcp"].get("protocol_versions") or wire.PROTOCOL_VERSIONS)
        versions = tuple(v for v in versions if v in wire.PROTOCOL_VERSIONS) or wire.PROTOCOL_VERSIONS
        s.initialize(versions, timeout=startup_timeout_s)
        tools = s.list_tools()
        resources = s.list_resources()
        prompts = s.list_prompts()
        return {"protocol_version": s.protocol_version, "server_info": s.server_info,
                "server_capabilities": s.server_capabilities, "instructions_ignored": s.instructions_ignored,
                "tools": tools, "resources": resources, "prompts": prompts}


def ping(manifest_raw: dict, package_dir: Path | None, policy: NetworkPolicy, *, request_timeout_s: float,
         startup_timeout_s: float) -> dict:
    with open_session(manifest_raw, package_dir, policy, request_timeout_s=request_timeout_s,
                      startup_timeout_s=startup_timeout_s) as s:
        s.initialize(timeout=startup_timeout_s)
        s.ping(timeout=request_timeout_s)
        tools = s.list_tools()
        return {"ok": True, "protocol_version": s.protocol_version, "tools": tools}


__all__ = ["open_session", "negotiate_and_discover", "ping", "McpError"]
