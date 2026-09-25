"""Helpers shared by the v117 tests: free ports, a REAL gateway process on a throwaway data folder, impostor web
servers, and a way to wait for things. Nothing here touches the machine outside temporary folders."""
import http.server, json, os, secrets, shutil, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE.parent
DEP = BUNDLE / "04-deployment"
for p in (str(DEP), str(DEP / "deployd")):
    if p not in sys.path:
        sys.path.insert(0, p)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until(fn, timeout=20.0, interval=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return fn()


def write_env(path, **vals):
    Path(path).write_text("".join(f"{k}={v}\n" for k, v in vals.items()))
    os.chmod(path, 0o600)
    return Path(path)


class Gateway:
    """A real gateway.py on a free loopback port, with its own data folder, secret and accounts."""

    def __init__(self, tmp, code_dir=DEP, secret=None, port=None):
        self.tmp = Path(tmp)
        self.root = self.tmp / "root"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "front-door.html").write_text("<html><body>front door</body></html>")
        self.port = port or free_port()
        self.secret = secret or secrets.token_urlsafe(48)
        self.code_dir = Path(code_dir)
        self.env_file = write_env(self.tmp / ".env", APP_BUILDER_ROOT=self.root, APP_BUILDER_HOST="127.0.0.1",
                                  APP_BUILDER_PORT=self.port, APP_BUILDER_SESSION_SECRET=self.secret)
        self.proc = None

    def env(self):
        e = {k: v for k, v in os.environ.items() if not k.startswith(("APP_BUILDER_", "ATTA_"))}
        e.update(APP_BUILDER_ROOT=str(self.root), APP_BUILDER_HOST="127.0.0.1", APP_BUILDER_PORT=str(self.port),
                 APP_BUILDER_SESSION_SECRET=self.secret, APP_BUILDER_TEST_ACCOUNTS="1", PYTHONDONTWRITEBYTECODE="1")
        e.pop("INVOCATION_ID", None)
        return e

    def start(self):
        subprocess.run([sys.executable, str(self.code_dir / "accounts.py"), "init"], env=self.env(),
                       capture_output=True, check=True, timeout=60)
        self.log = open(self.tmp / "gateway.log", "ab")
        self.proc = subprocess.Popen([sys.executable, str(self.code_dir / "gateway.py")], env=self.env(),
                                     stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        ok = wait_until(lambda: _port_open(self.port) or self.proc.poll() is not None, 30)
        if self.proc.poll() is not None or not ok:
            raise RuntimeError("gateway did not start: " + (self.tmp / "gateway.log").read_text()[-2000:])
        return self

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.wait(5)
        if getattr(self, "log", None):
            self.log.close()


def _port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


class Impostor:
    """A web server on `port` that answers every path with fixed text (nginx welcome, bare OK, fake JSON...)."""

    def __init__(self, body, status=200, ctype="text/html", headers=None, port=None):
        self.port = port or free_port()
        body_b = body.encode() if isinstance(body, str) else body
        extra = headers or {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                for k, v in extra.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body_b)))
                self.end_headers()
                self.wfile.write(body_b)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown(); self.server.server_close()


NGINX_WELCOME = ("<!DOCTYPE html><html><head><title>Welcome to nginx!</title></head><body><h1>Welcome to nginx!</h1>"
                 "</body></html>")


def nginx_available():
    return shutil.which("nginx") is not None and os.geteuid() == 0


def tmpdir(prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    os.chmod(d, 0o755)
    return d
