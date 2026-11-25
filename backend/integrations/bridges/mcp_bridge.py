"""ATTa MCP bridge — copied into generated MCP capability packages as `_atta_mcp_bridge.py`. STANDARD LIBRARY ONLY.

Runs inside the Stage 01A supervised runtime (separate process, rlimits, process-tree kill on timeout/cancel), only
after the gateway has authorised the exact call. Starts the approved MCP server (argv only, no shell), negotiates,
calls exactly one approved tool and returns a normalised, size-checked envelope. Binary content is never passed
through (only its type, length and SHA-256)."""
import base64
import hashlib
import os
import shutil
import sys
import tempfile

from _atta_mcp_tools import CONFIG
import _atta_mcp_wire as wire

PKG = os.path.dirname(os.path.abspath(__file__))


def _snapshot_files():
    """The package bytes the Stage 01A child verified against the approved digest before loading any code."""
    for f in sys.meta_path:
        files = getattr(f, "files", None)
        if isinstance(files, dict) and str(getattr(f, "label", "")).startswith("capability:"):
            return files
    return None


def _materialise():
    """Write the VERIFIED snapshot of the server's own files into a private temporary directory and run it from
    there: no time-of-check/time-of-use gap, and it works when Stage 01B drops the worker to an account that cannot
    read the installed package directory. Falls back to the package directory when no snapshot is present."""
    files = _snapshot_files()
    if files is None:
        return PKG, None
    root = tempfile.mkdtemp(prefix=".atta-mcp-", dir=os.getcwd())
    for rel, data in files.items():
        if rel.startswith("_atta_") or rel == "capability.yaml" or rel.startswith("/") or ".." in rel.split("/"):
            continue
        dst = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(data)
    return root, root


def _argv(command, base):
    argv = list(command)
    if argv and argv[0] == "{python}":
        argv = [sys.executable, "-I"] + argv[1:]
        if len(argv) > 2 and not os.path.isabs(argv[2]):
            argv[2] = os.path.join(base, argv[2])
    elif argv and not os.path.isabs(argv[0]):
        argv[0] = os.path.join(base, argv[0])
    return argv


def _connect(mcp, timeout):
    if mcp["transport"] == "stdio":
        base, tmp = _materialise()
        try:
            return wire.StdioConnection(_argv(mcp["command"], base), cwd=base), tmp
        except Exception:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            raise
    return wire.HttpConnection(mcp["url"], timeout_s=timeout), None


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
    conn, tmp = _connect(mcp, timeout)
    s = wire.McpSession(conn, request_timeout_s=timeout)
    try:
        versions = tuple(v for v in (mcp.get("protocol_versions") or wire.PROTOCOL_VERSIONS)
                         if v in wire.PROTOCOL_VERSIONS) or wire.PROTOCOL_VERSIONS
        s.initialize(versions, timeout=float(mcp.get("startup_timeout_s") or CONFIG["startup_timeout_s"]))
        res = s.call_tool(t["mcp_name"], payload, timeout=timeout)
    finally:
        s.close()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
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
        conn, tmp = _connect(mcp, timeout)
        s = wire.McpSession(conn, request_timeout_s=timeout)
        try:
            s.initialize(timeout=timeout)
            s.ping(timeout=timeout)
            names = sorted(x.get("name") for x in s.list_tools() if isinstance(x, dict))
        finally:
            s.close()
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "detail": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
    declared = sorted(v["mcp_name"] for v in CONFIG["tools"].values())
    missing = [n for n in declared if n not in names]
    return {"healthy": not missing, "detail": "missing tools %s" % missing if missing else "ok",
            "undeclared_offered": [n for n in names if n not in declared][:20]}
