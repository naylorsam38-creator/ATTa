import json, os, socket, tempfile, threading, time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "04-deployment"))
from resilience import safe_http_probe, safe_tcp_probe, deployment_revision, revision_stable


def test_probe_refused_is_classified():
    # Ask the OS for a currently unused loopback port without fabricating a server.
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    r = safe_tcp_probe("127.0.0.1", port, timeout=1)
    assert r["classification"] == "ENDPOINT_NOT_LISTENING"
    assert r["tcp_connected"] is False


def test_real_http_response_is_observation_not_verdict():
    import http.server
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(b"ok")
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    try:
        r = safe_http_probe(f"http://127.0.0.1:{srv.server_port}/", timeout=2)
        assert r["classification"] == "HTTP_RESPONSE_RECEIVED"
        assert r["http_status"] == 200
        assert r["tcp_connected"] is True
    finally:
        srv.shutdown(); srv.server_close(); th.join(timeout=2)


def test_revision_changes_invalidate_stability(tmp_path):
    (tmp_path / "adm" / "current").mkdir(parents=True)
    p = tmp_path / "adm" / "current" / "release.json"
    p.write_text(json.dumps({"version":"a"}))
    a = deployment_revision(tmp_path)
    p.write_text(json.dumps({"version":"b"}))
    b = deployment_revision(tmp_path)
    assert a != b
    assert revision_stable(a, b) is False
