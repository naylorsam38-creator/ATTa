#!/usr/bin/env python3
"""Minimal smart-HTTP git server (read-only): hands each request to `git http-backend` (CGI)."""
import os, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT = os.environ.get("GIT_PROJECT_ROOT", "/srv/git")
class H(BaseHTTPRequestHandler):
    def _cgi(self):
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if self.command == "POST" else b""
        env = {"GIT_PROJECT_ROOT": ROOT, "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": path, "QUERY_STRING": query,
               "REQUEST_METHOD": self.command, "CONTENT_TYPE": self.headers.get("Content-Type", ""),
               "CONTENT_LENGTH": str(len(body)), "REMOTE_ADDR": self.client_address[0], "PATH": "/usr/bin:/bin"}
        if self.headers.get("Git-Protocol"): env["GIT_PROTOCOL"] = self.headers["Git-Protocol"]
        out = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        status = 200; hdrs = []
        for line in head.decode(errors="replace").split("\r\n"):
            if not line: continue
            k, _, v = line.partition(":")
            if k.lower() == "status": status = int(v.strip().split()[0])
            else: hdrs.append((k, v.strip()))
        self.send_response(status)
        for k, v in hdrs: self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
    do_GET = do_POST = _cgi
ThreadingHTTPServer(("0.0.0.0", 80), H).serve_forever()
