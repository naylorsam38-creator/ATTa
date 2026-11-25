"""TEST FIXTURE ONLY: a Streamable-HTTP MCP endpoint on 127.0.0.1 serving the same deterministic tools as the stdio
fixture server. Started/stopped by tests; never used outside tests."""
from __future__ import annotations

import json
import runpy
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"


def _load(mode: str) -> dict:
    old = sys.argv
    sys.argv = ["mcp_test_server.py", mode]
    try:
        return runpy.run_path(str(FIX / "mcp_test_server.py"), run_name="fixture")
    finally:
        sys.argv = old


class HttpMcp:
    def __init__(self, mode: str = "normal"):
        ns = _load(mode)
        self.tools, self.call = ns["TOOLS"], ns["call"]
        self.requests: list[dict] = []
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                srv.requests.append({"headers": dict(self.headers), "body": body})
                if "id" not in body:
                    self.send_response(202)
                    self.end_headers()
                    return
                m, rid = body.get("method"), body["id"]
                if m == "initialize":
                    res = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "atta-http-test", "version": "1"},
                           "capabilities": {"tools": {}}}
                elif m == "ping":
                    res = {}
                elif m == "tools/list":
                    res = {"tools": srv.tools}
                elif m == "tools/call":
                    p = body.get("params") or {}
                    res = srv.call(p["name"], p.get("arguments") or {})
                else:
                    res = None
                out = {"jsonrpc": "2.0", "id": rid}
                if res is None:
                    out["error"] = {"code": -32601, "message": "method not found"}
                else:
                    out["result"] = res
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if m == "initialize":
                    self.send_header("Mcp-Session-Id", "test-session-1")
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def http_manifest(port: int, iid: str = "test.http_mcp", host: str = "127.0.0.1") -> dict:
    sys.path.insert(0, str(Path(__file__).parent))
    from fixture_builder import base, mcp_tools
    tools = mcp_tools()
    return base(iid, "MCP_SERVER", "HTTP MCP (fixture)", "1.0.0", "Streamable HTTP MCP endpoint for tests.",
                "MCP_HTTP", tools, ["workspace.read"], network_requirements={"hosts": [host]},
                mcp={"transport": "streamable_http", "url": f"http://{host}:{port}/mcp", "request_timeout_s": 10})
