"""v117 (checklists B and HTTPS): ATTa renders its whole nginx site — HTTPS included — and checks go THROUGH it.

    cd ATTa && python3 -m unittest tests.test_v117_nginx -v

Rendering and the stock-site clean-up are tested everywhere; the rest runs a REAL nginx (skipped without nginx or
root) in front of a REAL gateway: every mode, a real TLS certificate from a throwaway CA checked through SNI, the
http->https redirect, the Ubuntu stock site that made v114.2 say VERIFIED over "Welcome to nginx!", and the Amazon
Linux / Fedora stock server block inside nginx.conf."""
import json, os, shutil, subprocess, sys, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atta_testlib import DEP, Gateway, free_port, nginx_available, tmpdir, wait_until, _port_open  # noqa: E402
import atta_health, nginx_site  # noqa: E402

# The stock nginx.conf shipped by Fedora/RHEL-family nginx packages (Amazon Linux 2023 uses this layout): a server
# block inside http{} with its own `listen 80`, next to `include conf.d/*.conf`. Reproduced here as the fixture.
FEDORA_NGINX_CONF = r"""# For more information on configuration, see:
#   * Official English Documentation: http://nginx.org/en/docs/
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log notice;
pid /run/nginx.pid;
include /usr/share/nginx/modules/*.conf;
events {
    worker_connections 1024;
}
http {
    log_format  main  '$remote_addr - $remote_user [$time_local] "$request" '
                      '$status $body_bytes_sent "$http_referer" '
                      '"$http_user_agent" "$http_x_forwarded_for"';
    access_log  /var/log/nginx/access.log  main;
    sendfile            on;
    keepalive_timeout   65;
    types_hash_max_size 4096;
    include             /etc/nginx/mime.types;
    default_type        application/octet-stream;
    include /etc/nginx/conf.d/*.conf;
    server {
        listen       80;
        listen       [::]:80;
        server_name  _;
        root         /usr/share/nginx/html;
        # Load configuration files for the default server block.
        include /etc/nginx/default.d/*.conf;
        error_page 404 /404.html;
        location = /404.html {
        }
        error_page 500 502 503 504 /50x.html;
        location = /50x.html {
        }
    }
# Settings for a TLS enabled server.
#
#    server {
#        listen       443 ssl;
#        server_name  _;
#    }
}
"""


class Render(unittest.TestCase):
    def setUp(self):
        self.t = tmpdir("atta-nginx-r-")

    def r(self, **kw):
        args = dict(port=8787, domains=[], email="", allow_public_http=False, letsencrypt=self.t / "le",
                    acme_root=self.t / "acme", ipv6=False)
        args.update(kw)
        return nginx_site.render(**args)

    def cert(self, domain):
        d = self.t / "le" / "live" / domain
        d.mkdir(parents=True)
        (d / "fullchain.pem").write_text("x"); (d / "privkey.pem").write_text("x")

    def test_modes(self):
        self.assertEqual(self.r()[1]["mode"], "local")
        self.assertIn("listen 127.0.0.1:80 default_server", self.r()[0])
        self.assertEqual(self.r(allow_public_http=True)[1]["mode"], "public")
        site, d = self.r(domains=["a.example.com"], email="o@example.com")
        self.assertEqual(d["mode"], "pending")
        self.assertIn("return 503", site)                         # public :80 never shows the login before TLS
        self.assertIn("listen 127.0.0.1:80 default_server", site)
        self.cert("a.example.com")
        site, d = self.r(domains=["a.example.com", "www.a.example.com"], email="o@example.com")
        self.assertEqual((d["mode"], d["scheme"], d["host"], d["redirect_http"]), ("tls", "https", "a.example.com", True))
        self.assertIn("ssl_certificate " + str(self.t / "le/live/a.example.com/fullchain.pem"), site)
        self.assertIn("return 308 https://$host$request_uri", site)
        self.assertIn("/.well-known/acme-challenge/", site)       # renewals keep working over :80

    def test_same_inputs_same_site_every_time(self):
        # What makes HTTPS survive redeploys, reboots and rollbacks: nothing but .env + the cert decides the site.
        self.cert("a.example.com")
        a = self.r(domains=["a.example.com"], email="o@example.com")
        b = self.r(domains=["a.example.com"], email="o@example.com")
        self.assertEqual(a, b)

    def test_port_comes_from_env_value(self):
        self.assertIn("proxy_pass http://127.0.0.1:9123;", self.r(port=9123)[0])
        self.assertNotIn("8787", self.r(port=9123)[0])

    def test_ipv6_only_when_available(self):
        self.assertNotIn("[::]", self.r(allow_public_http=True, ipv6=False)[0])
        self.assertIn("listen [::]:80 default_server", self.r(allow_public_http=True, ipv6=True)[0])

    def test_bad_inputs(self):
        for bad in ("a b;c", "exa mple", "-x.com", "x..com", "a" * 64 + ".com", "$(id).com", "x.com;"):
            with self.subTest(bad=bad), self.assertRaises(nginx_site.SiteError):
                nginx_site.domains_of(bad)
        with self.assertRaises(nginx_site.SiteError):
            nginx_site.domains_of("a.com a.com")
        with self.assertRaises(nginx_site.SiteError):
            self.r(domains=["a.example.com"], email="")                # a domain needs the certificate email
        with self.assertRaises(nginx_site.SiteError):
            self.r(domains=["a.example.com"], email="not an email")

    def test_neutralize_fedora_style_server_block(self):
        conf = self.t / "nginx.conf"
        conf.write_text(FEDORA_NGINX_CONF)
        se = self.t / "sites-enabled"; se.mkdir()
        changed = nginx_site.neutralize(conf, se)
        self.assertEqual(len(changed), 1, changed)
        new = conf.read_text()
        self.assertNotIn("root         /usr/share/nginx/html;", new)
        self.assertIn("include /etc/nginx/conf.d/*.conf;", new)      # everything else kept
        self.assertIn("log_format  main", new)
        self.assertIn("#    server {", new)                             # commented example untouched
        self.assertEqual((self.t / "nginx.conf.atta-orig").read_text(), FEDORA_NGINX_CONF)
        self.assertEqual(nginx_site.neutralize(conf, se), [])          # idempotent

    def test_neutralize_ubuntu_style(self):
        conf = self.t / "nginx.conf"
        conf.write_text("events {}\nhttp {\n  include /etc/nginx/sites-enabled/*;\n}\n")
        se = self.t / "sites-enabled"; se.mkdir()
        (self.t / "default").write_text("server { listen 80 default_server; }")
        (se / "default").symlink_to(self.t / "default")
        changed = nginx_site.neutralize(conf, se)
        self.assertFalse((se / "default").exists())
        self.assertTrue((self.t / "default").exists())                 # sites-available copy stays
        self.assertEqual(conf.read_text(), "events {}\nhttp {\n  include /etc/nginx/sites-enabled/*;\n}\n")
        self.assertEqual(len(changed), 1)

    def test_unparseable_conf_left_alone(self):
        conf = self.t / "nginx.conf"
        conf.write_text("http { server { listen 80; ")
        self.assertEqual(nginx_site.neutralize(conf, self.t / "none"), [])
        self.assertEqual(conf.read_text(), "http { server { listen 80; ")

    def test_check_output_flags_warnings(self):
        self.assertEqual(nginx_site.check_output("nginx: the configuration file ... syntax is ok\n"), [])
        w = 'nginx: [warn] conflicting server name "_" on 0.0.0.0:80, ignored'
        self.assertEqual(nginx_site.check_output(w + "\n"), [w])


def _openssl(*args, cwd):
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


@unittest.skipUnless(nginx_available() and shutil.which("openssl"), "needs nginx and openssl, as root")
class RealNginx(unittest.TestCase):
    """nginx -p <tmp>: its own prefix, pid and logs; high ports; a real gateway behind it."""

    @classmethod
    def setUpClass(cls):
        cls.t = tmpdir("atta-nginx-")
        cls.gw = Gateway(cls.t / "gw").start()
        cls.nginx = None

    @classmethod
    def tearDownClass(cls):
        cls.stop_nginx()
        cls.gw.stop()

    @classmethod
    def stop_nginx(cls):
        if cls.nginx and cls.nginx.poll() is None:
            cls.nginx.terminate(); cls.nginx.wait(10)
        cls.nginx = None

    def start(self, site, main_extra=""):
        self.stop_nginx()
        p = self.t / "ngx"
        shutil.rmtree(p, ignore_errors=True)
        (p / "logs").mkdir(parents=True)
        (p / "site.conf").write_text(site)
        (p / "nginx.conf").write_text(f"pid {p}/nginx.pid;\nerror_log {p}/logs/error.log;\nevents {{}}\n"
                                      f"http {{\n access_log off;\n include {p}/site.conf;\n{main_extra}}}\n")
        t = subprocess.run(["nginx", "-t", "-p", str(p), "-c", str(p / "nginx.conf")], capture_output=True, text=True)
        bad = nginx_site.check_output(t.stderr)
        if t.returncode or bad:
            return t.stderr
        type(self).nginx = subprocess.Popen(["nginx", "-p", str(p), "-c", str(p / "nginx.conf"), "-g", "daemon off;"],
                                            stderr=subprocess.DEVNULL)
        return None

    def render(self, **kw):
        self.hp, self.sp = free_port(), free_port()
        args = dict(port=self.gw.port, domains=[], email="", allow_public_http=False, letsencrypt=self.t / "le",
                    acme_root=self.t / "acme", http_port=self.hp, https_port=self.sp, ipv6=False)
        args.update(kw)
        site, desc = nginx_site.render(**args)
        pj = self.t / "proxy.json"
        pj.write_text(json.dumps(desc))
        return site, desc, pj

    def up(self, desc):
        ports = [desc["port"]] + ([desc["http_port"]] if desc.get("http_port") else [])
        self.assertTrue(wait_until(lambda: all(_port_open(p) for p in ports), 10), "nginx did not come up")

    def test_local_mode_checks_through_nginx(self):
        site, desc, pj = self.render()
        self.assertIsNone(self.start(site))
        self.up(desc)
        ok, lines = atta_health.run_checks(self.gw.env_file, proxy_file=pj, direct=True, proxy=True, timeout=10)
        self.assertTrue(ok, lines)

    def test_tls_mode_with_real_certificate(self):
        le = self.t / "le" / "live" / "atta.test"
        le.mkdir(parents=True, exist_ok=True)
        ca = self.t / "ca"; ca.mkdir(exist_ok=True)
        _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "2",
                 "-subj", "/CN=ATTa test CA", cwd=ca)
        _openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(le / "privkey.pem"), "-out", "site.csr",
                 "-subj", "/CN=atta.test", cwd=ca)
        (ca / "ext").write_text("subjectAltName=DNS:atta.test\n")
        _openssl("x509", "-req", "-in", "site.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
                 "-out", str(le / "fullchain.pem"), "-days", "2", "-extfile", "ext", cwd=ca)
        site, desc, pj = self.render(domains=["atta.test"], email="ops@atta.test", cafile=ca / "ca.pem")
        self.assertEqual(desc["mode"], "tls")
        self.assertIsNone(self.start(site))
        self.up(desc)
        ok, lines = atta_health.run_checks(self.gw.env_file, proxy_file=pj, direct=True, proxy=True, timeout=10)
        self.assertTrue(ok, lines)
        self.assertIn("https://atta.test", lines[-1])
        self.assertIn("redirect", lines[-1])
        # A certificate for another name is refused (SNI + hostname check are real).
        with self.assertRaisesRegex(atta_health.CheckFailed, "certificate"):
            atta_health.check_identity("https", "127.0.0.1", desc["port"], "other.test", self.gw.secret,
                                       cafile=ca / "ca.pem")

    def test_pending_mode_hides_login_from_the_public_address(self):
        site, desc, pj = self.render(domains=["atta.test"], email="ops@atta.test")
        self.assertEqual(desc["mode"], "pending")
        self.assertIsNone(self.start(site))
        self.up(desc)
        ok, lines = atta_health.run_checks(self.gw.env_file, proxy_file=pj, direct=False, proxy=True, timeout=10)
        self.assertTrue(ok, lines)                                       # 127.0.0.1 serves ATTa
        import socket
        ip = next((a for a in socket.gethostbyname_ex(socket.gethostname())[2] if not a.startswith("127.")), None)
        if ip:                                                           # the public address does not
            st, _, body = atta_health._get("http", ip, self.hp, "/login", "atta.test", 5)
            self.assertEqual(st, 503)
        (self.t / "acme" / ".well-known" / "acme-challenge").mkdir(parents=True, exist_ok=True)
        (self.t / "acme" / ".well-known" / "acme-challenge" / "tok").write_text("proof")
        st, _, body = atta_health._get("http", "127.0.0.1", self.hp, "/.well-known/acme-challenge/tok", "atta.test", 5)
        self.assertEqual((st, body), (200, "proof"))

    def test_stock_default_site_beside_ours_is_refused_until_neutralized(self):
        # PR #5 bug 2: a stock 'listen 80 default_server' site beside ATTa's. nginx must refuse it (duplicate default
        # server) rather than start and show its welcome page; after neutralize, ATTa answers.
        site, desc, pj = self.render(allow_public_http=True)
        stock = f"server {{ listen {self.hp} default_server; server_name _; return 200 'Welcome to nginx!'; }}\n"
        err = self.start(site + stock)
        self.assertIsNotNone(err)
        self.assertRegex(err, "duplicate default server|conflicting")
        self.assertIsNone(self.start(site))
        self.up(desc)
        ok, lines = atta_health.run_checks(self.gw.env_file, proxy_file=pj, direct=False, proxy=True, timeout=10)
        self.assertTrue(ok, lines)

    def test_welcome_page_in_atta_place_fails_the_check(self):
        site, desc, pj = self.render(allow_public_http=True)
        welcome = site.replace(f"proxy_pass http://127.0.0.1:{self.gw.port};",
                               "default_type text/html; return 200 '<h1>Welcome to nginx!</h1>';")
        self.assertIsNone(self.start(welcome))
        self.up(desc)
        ok, lines = atta_health.run_checks(self.gw.env_file, proxy_file=pj, direct=False, proxy=True, timeout=2,
                                           interval=0.5)
        self.assertFalse(ok)
        self.assertIn("Welcome to nginx", lines[-1])


if __name__ == "__main__":
    unittest.main()
