"""MCP wire protocol client — STANDARD LIBRARY ONLY (Stage 12 §14, §16).

The same file is used (a) host-side by backend.integrations.mcp.client for negotiation, discovery and health, and
(b) copied verbatim into each generated MCP capability package as `_atta_mcp_wire.py`, where the ATTa bridge uses it
inside the Stage 01A supervised runtime to perform tools/call. One implementation, two supervised contexts.

Implements JSON-RPC 2.0 over:
  * stdio  — newline-delimited JSON with a local process started WITHOUT a shell (argv only), in its own session,
             with a minimal environment; killed as a process group on close/timeout;
  * Streamable HTTP — POST per message, JSON or SSE responses, Mcp-Session-Id and MCP-Protocol-Version headers.
The client advertises NO client capabilities (no roots, sampling or elicitation): a server can never ask ATTa for
model output, files or secrets. Server-initiated requests are answered with 'method not found'."""
import json
import os
import queue
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
CLIENT_INFO = {"name": "atta-hub", "version": "12.0"}


class McpError(Exception):
    kind = "error"

    def __init__(self, message, code=None, data=None):
        super().__init__(message)
        self.code, self.data = code, data


class McpTimeout(McpError):
    kind = "timeout"


class McpCancelled(McpError):
    kind = "cancelled"


class McpProtocolError(McpError):
    kind = "protocol"


class McpTransportError(McpError):
    kind = "transport"


class StdioConnection:
    def __init__(self, argv, cwd=None, env=None, startup_timeout_s=10.0):
        if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) for a in argv):
            raise McpTransportError("stdio command must be a non-empty argv list")
        base_env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1"}
        base_env.update(env or {})
        try:
            self.proc = subprocess.Popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, cwd=cwd, env=base_env, shell=False,
                                         start_new_session=True, bufsize=0)
        except OSError as exc:
            raise McpTransportError("could not start MCP server: %s" % exc)
        self.inbox = queue.Queue()
        self.stderr_tail = []
        self.closed = False
        self._wlock = threading.Lock()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        buf = b""
        try:
            while True:
                try:
                    chunk = os.read(self.proc.stdout.fileno(), 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_MESSAGE_BYTES and b"\n" not in buf:
                    self.inbox.put(McpProtocolError("server message exceeds %d bytes" % MAX_MESSAGE_BYTES))
                    return
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        try:
                            self.inbox.put(json.loads(line.decode("utf-8")))
                        except (ValueError, UnicodeDecodeError):
                            self.inbox.put(McpProtocolError("server wrote a non-JSON line to stdout"))
        finally:
            self.inbox.put(None)

    def _read_stderr(self):
        for line in iter(self.proc.stderr.readline, b""):
            self.stderr_tail.append(line.decode("utf-8", "replace")[:500])
            del self.stderr_tail[:-20]

    def send(self, msg):
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
        with self._wlock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise McpTransportError("MCP server stdin closed: %s" % exc)

    def receive(self, timeout):
        try:
            item = self.inbox.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return "timeout"
        if item is None:
            self.inbox.put(None)
            raise McpTransportError("MCP server exited (code %s); stderr: %s"
                                    % (self.proc.poll(), "".join(self.stderr_tail)[-300:]))
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if self.proc.poll() is not None:
                break
            try:
                os.killpg(self.proc.pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                self.proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        try:   # anything left in the server's process group (grandchildren) goes too
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


class HttpConnection:
    """Streamable HTTP. The URL is fixed by the approved manifest; host policy is enforced by the caller."""

    def __init__(self, url, headers=None, timeout_s=30.0):
        self.url, self.timeout_s = url, timeout_s
        self.headers = dict(headers or {})
        self.session_id = None
        self.protocol_version = None
        self.pending = []
        self.closed = False

    def send(self, msg):
        body = json.dumps(msg).encode("utf-8")
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        h.update(self.headers)
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            h["MCP-Protocol-Version"] = self.protocol_version
        req = urllib.request.Request(self.url, data=body, headers=h, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            resp = opener.open(req, timeout=self.timeout_s)
        except urllib.error.HTTPError as exc:
            if exc.code == 202:
                return
            raise McpTransportError("HTTP %s from MCP server" % exc.code)
        except (urllib.error.URLError, OSError) as exc:
            raise McpTransportError("MCP server unreachable: %s" % exc)
        with resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid and len(sid) < 200:
                self.session_id = sid
            if resp.status == 202 or "id" not in msg:
                return
            raw = resp.read(MAX_MESSAGE_BYTES + 1)
            if len(raw) > MAX_MESSAGE_BYTES:
                raise McpProtocolError("server response exceeds %d bytes" % MAX_MESSAGE_BYTES)
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            text = raw.decode("utf-8", "replace")
            if ctype == "text/event-stream":
                data = []
                for line in text.splitlines() + [""]:
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        try:
                            self.pending.append(json.loads("\n".join(data)))
                        except ValueError:
                            raise McpProtocolError("invalid SSE data from MCP server")
                        data = []
            else:
                try:
                    obj = json.loads(text)
                except ValueError:
                    raise McpProtocolError("invalid JSON from MCP server")
                self.pending.extend(obj if isinstance(obj, list) else [obj])

    def receive(self, timeout):
        if self.pending:
            return self.pending.pop(0)
        return "timeout"

    def close(self):
        self.closed = True


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):   # a redirect could move the call to an unapproved host
        raise McpTransportError("MCP server attempted a redirect; refused")


class McpSession:
    def __init__(self, conn, request_timeout_s=30.0):
        self.conn, self.timeout = conn, request_timeout_s
        self._next = 0
        self._cancel = threading.Event()
        self.server_info = None
        self.server_capabilities = None
        self.protocol_version = None
        self.instructions_ignored = False

    def cancel(self):
        self._cancel.set()

    def _request(self, method, params=None, timeout=None):
        self._next += 1
        rid = self._next
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.conn.send(msg)
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            if self._cancel.is_set():
                self._notify_cancel(rid, "cancelled by ATTa")
                raise McpCancelled("request %s cancelled" % method)
            left = deadline - time.monotonic()
            if left <= 0:
                self._notify_cancel(rid, "timeout")
                raise McpTimeout("%s timed out after %.1fs" % (method, timeout or self.timeout))
            item = self.conn.receive(min(left, 0.1))
            if item == "timeout":
                continue
            if not isinstance(item, dict) or item.get("jsonrpc") != "2.0":
                raise McpProtocolError("malformed JSON-RPC message from server")
            if "method" in item:                       # server -> client traffic
                if "id" in item:                       # a server REQUEST: ATTa grants nothing
                    try:
                        self.conn.send({"jsonrpc": "2.0", "id": item["id"],
                                        "error": {"code": -32601, "message": "method not supported by ATTa client"}})
                    except McpError:
                        pass
                continue                               # notifications are ignored
            if item.get("id") != rid:
                continue
            if "error" in item:
                e = item["error"] if isinstance(item["error"], dict) else {}
                raise McpError(str(e.get("message", "server error"))[:500], e.get("code"))
            if "result" not in item:
                raise McpProtocolError("response without result")
            return item["result"]

    def _notify_cancel(self, rid, reason):
        try:
            self.conn.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                            "params": {"requestId": rid, "reason": reason}})
        except McpError:
            pass

    def initialize(self, versions=PROTOCOL_VERSIONS, timeout=None):
        res = self._request("initialize", {"protocolVersion": versions[0], "capabilities": {},
                                           "clientInfo": CLIENT_INFO}, timeout)
        if not isinstance(res, dict):
            raise McpProtocolError("initialize result must be an object")
        pv = res.get("protocolVersion")
        if pv not in versions:
            raise McpProtocolError("unsupported protocol version %r (supported: %s)" % (pv, ", ".join(versions)))
        self.protocol_version = pv
        if isinstance(self.conn, HttpConnection):
            self.conn.protocol_version = pv
        caps = res.get("capabilities")
        self.server_capabilities = caps if isinstance(caps, dict) else {}
        info = res.get("serverInfo")
        self.server_info = info if isinstance(info, dict) else {}
        # Server 'instructions' are never forwarded to the model: they would bypass the trusted/untrusted boundary.
        self.instructions_ignored = "instructions" in res
        self.conn.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res

    def _paged(self, method, key, limit):
        items, cursor = [], None
        for _ in range(20):
            res = self._request(method, {"cursor": cursor} if cursor else {})
            page = res.get(key) if isinstance(res, dict) else None
            if not isinstance(page, list):
                raise McpProtocolError("%s result has no '%s' list" % (method, key))
            items.extend(page)
            if len(items) > limit:
                raise McpProtocolError("server lists more than %d %s" % (limit, key))
            cursor = res.get("nextCursor")
            if not cursor:
                return items
        raise McpProtocolError("%s pagination did not terminate" % method)

    def list_tools(self, limit=200):
        return self._paged("tools/list", "tools", limit)

    def list_resources(self, limit=500):
        if "resources" not in (self.server_capabilities or {}):
            return []
        return self._paged("resources/list", "resources", limit)

    def list_prompts(self, limit=200):
        if "prompts" not in (self.server_capabilities or {}):
            return []
        return self._paged("prompts/list", "prompts", limit)

    def call_tool(self, name, arguments, timeout=None):
        res = self._request("tools/call", {"name": name, "arguments": arguments}, timeout)
        if not isinstance(res, dict) or not isinstance(res.get("content", []), list):
            raise McpProtocolError("tools/call result must contain a content list")
        return res

    def ping(self, timeout=None):
        self._request("ping", {}, timeout)
        return True

    def close(self):
        self.conn.close()
