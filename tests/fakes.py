"""Stand-ins for what the runner and watcher talk to: ports that aren't HTTP, and a skin proxy
serving pages shaped like the run-2 failures. Real sockets, real HTTP, real browser; no Docker."""
import http.server, socket, threading, time

def banner_server(banner: bytes) -> int:
    """A port that answers in some other protocol (a mail server's greeting, a database handshake)."""
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0)); s.listen(16)
    def loop():
        while True:
            c, _ = s.accept()
            try:
                c.sendall(banner); time.sleep(0.1)
            except OSError:
                pass
            finally:
                c.close()
    threading.Thread(target=loop, daemon=True).start()
    return s.getsockname()[1]

SKIN = '<link rel="stylesheet" href="/_cs/app/skin.css">'
HOOK = '<script src="/_cp/port.js" data-capability-hook="1" defer></script>'
def _doc(head, body): return f"<!doctype html><html><head><title>t</title>{head}</head><body>{body}</body></html>"

PAGES = {
    "/": _doc(SKIN + HOOK, "<p>ready</p>"),
    "/ok": _doc(SKIN + HOOK, "<p>ready</p>"),
    # colanode: the page is drawn by its own JavaScript a few seconds after "load"
    "/late": _doc(SKIN + HOOK, '<div id="root"></div><script>setTimeout(()=>{root.textContent="workspace"},{DELAY})</script>'),
    "/blank": _doc(SKIN + HOOK, ""),
    "/spinner": _doc(SKIN + HOOK, '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="40" height="40">'),
    "/canvas": _doc(SKIN + HOOK, '<canvas width="400" height="300"></canvas>'),
    # plane, three ways the browser can end up without the link the server-side check saw
    "/spa": _doc(SKIN + HOOK, '<p>app</p><script>addEventListener("load",()=>setTimeout(()=>document.querySelectorAll(\'link[href^="/_cs/"]\').forEach(l=>l.remove()),200))</script>'),
    "/redir": _doc(SKIN + HOOK, '<p>x</p><script>addEventListener("load",()=>{location.replace("/login")})</script>'),
    "/login": _doc(HOOK, "<p>login</p>"),
}

class _H(http.server.BaseHTTPRequestHandler):
    delay_ms = 2000
    def log_message(self, *a): pass
    def do_GET(self):
        p = self.path.split("?")[0]
        if p.startswith("/_cs/"): body, ct = b"body{}", "text/css"
        elif p == "/_cp/port.js": body, ct = b"/*port*/", "application/javascript"
        elif p in PAGES: body, ct = PAGES[p].replace("{DELAY}", str(self.delay_ms)).encode(), "text/html; charset=utf-8"
        else:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

def skin_proxy(delay_ms: int = 2000) -> str:
    _H.delay_ms = delay_ms
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{s.server_address[1]}"
