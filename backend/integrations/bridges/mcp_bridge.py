"""ATTa MCP bridge — copied into generated MCP capability packages as `_atta_mcp_bridge.py`. STANDARD LIBRARY ONLY.

Runs inside the Stage 01A supervised runtime (separate process, rlimits, process-tree kill on timeout/cancel), only
after the gateway has authorised the exact call. Starts the approved MCP server (argv only, no shell), negotiates,
calls exactly one approved tool and returns a normalised, size-checked envelope. Binary content is never passed
through (only its type, length and SHA-256)."""
import base64
import hashlib
import os
import sys

from _atta_mcp_tools import CONFIG
import _atta_mcp_wire as wire

PKG = os.path.dirname(os.path.abspath(__file__))


def _argv(command):
    argv = list(command)
    if argv and argv[0] == "{python}":
        argv = [sys.executable, "-I"] + argv[1:]
        if len(argv) > 2 and not os.path.isabs(argv[2]):
            argv[2] = os.path.join(PKG, argv[2])
    elif argv and not os.path.isabs(argv[0]):
        argv[0] = os.path.join(PKG, argv[0])
    return argv


def _normalise(item):
    if not isinstance(item, dict):
        return {"type": "invalid"}
    t = item.get("type")
    if t == "text":
        return {"type": "text", "text": str(item.get("text", ""))}
    if t in ("image", "audio"):
        raw = str(item.get("data", ""))
        try:
            data = base64.b64decode(raw, validate=False)
        except Exception:  # noqa: BLE001
            data = raw.encode()
        return {"type": t, "mimeType": str(item.get("mimeType", ""))[:100], "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}
    if t == "resource_link":
        return {"type": t, "uri": str(item.get("uri", ""))[:500], "name": str(item.get("name", ""))[:200]}
    if t == "resource":
        r = item.get("resource") if isinstance(item.get("resource"), dict) else {}
        out = {"type": t, "uri": str(r.get("uri", ""))[:500]}
        if "text" in r:
            out["text"] = str(r["text"])
        return out
    return {"type": "unsupported", "original_type": str(t)[:40]}


def call(tool, payload, ctx):
    t = CONFIG["tools"][tool]
    mcp = CONFIG["mcp"]
    timeout = float(mcp.get("request_timeout_s") or CONFIG["request_timeout_s"])
    if mcp["transport"] == "stdio":
        conn = wire.StdioConnection(_argv(mcp["command"]), cwd=PKG)
    else:
        conn = wire.HttpConnection(mcp["url"], timeout_s=timeout)
    s = wire.McpSession(conn, request_timeout_s=timeout)
    try:
        versions = tuple(v for v in (mcp.get("protocol_versions") or wire.PROTOCOL_VERSIONS)
                         if v in wire.PROTOCOL_VERSIONS) or wire.PROTOCOL_VERSIONS
        s.initialize(versions, timeout=float(mcp.get("startup_timeout_s") or CONFIG["startup_timeout_s"]))
        res = s.call_tool(t["mcp_name"], payload, timeout=timeout)
    finally:
        s.close()
    content = [_normalise(c) for c in res.get("content") or []]
    if res.get("isError") is True:
        first = next((c.get("text", "") for c in content if c.get("type") == "text"), "")
        raise RuntimeError("MCP tool reported an error: %s" % first[:300])
    sc = res.get("structuredContent")
    return {"content": content, "structured_content": sc if isinstance(sc, dict) else None, "is_error": False}


def healthcheck():
    mcp = CONFIG["mcp"]
    timeout = float(CONFIG["startup_timeout_s"])
    try:
        if mcp["transport"] == "stdio":
            conn = wire.StdioConnection(_argv(mcp["command"]), cwd=PKG)
        else:
            conn = wire.HttpConnection(mcp["url"], timeout_s=timeout)
        s = wire.McpSession(conn, request_timeout_s=timeout)
        try:
            s.initialize(timeout=timeout)
            s.ping(timeout=timeout)
            names = sorted(x.get("name") for x in s.list_tools() if isinstance(x, dict))
        finally:
            s.close()
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "detail": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
    declared = sorted(v["mcp_name"] for v in CONFIG["tools"].values())
    missing = [n for n in declared if n not in names]
    return {"healthy": not missing, "detail": "missing tools %s" % missing if missing else "ok",
            "undeclared_offered": [n for n in names if n not in declared][:20]}
