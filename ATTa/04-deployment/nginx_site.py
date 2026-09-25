#!/usr/bin/env python3
"""ATTa's nginx site, rendered whole by bootstrap.sh on every run (v117).

Up to v116 bootstrap.sh wrote an HTTP-only site from a template and certbot --nginx then edited it in place to add
HTTPS. The next `bash run` overwrote the file with the template again, so every redeploy dropped HTTPS until certbot
happened to re-add it. Now ATTa renders the WHOLE site itself — HTTPS included, whenever the certificate files exist —
and certbot only issues/renews certificates (webroot), never edits nginx. A redeploy, a reboot or a rollback renders
the same HTTPS site from the same inputs (.env + the certificate on disk).

Modes (decided from .env and the certificate on disk):
  tls      a certificate exists for the first domain: :443 serves ATTa, :80 serves ACME challenges and redirects
           everything else to https. proxy.json -> https://DOMAIN (checked with the real certificate).
  pending  domain + email set, no certificate yet: public :80 serves ONLY ACME challenges (so certbot can prove the
           domain) and a 503 "HTTPS is being set up" page; ATTa itself answers on 127.0.0.1:80 only.
  public   no domain, APP_BUILDER_ALLOW_PUBLIC_HTTP=true: plain HTTP on :80 (explicit opt-out; logins in clear text).
  local    no domain: ATTa on 127.0.0.1:80 only (reach it with an SSH tunnel). The default.

Every server ATTa renders is `default_server` for its address, and the distribution's stock sites are taken out of
the way (neutralize), so nginx's own welcome page can never answer in ATTa's place (v114.2 said VERIFIED while the
server showed "Welcome to nginx!").

    nginx_site.py render --port P --domains "a b" --email E --allow-public-http BOOL --letsencrypt DIR
                         --acme-root DIR --out SITE.conf --proxy-json FILE
    nginx_site.py check --port P --domains "a b" --email E --allow-public-http BOOL      validate only, writes nothing
    nginx_site.py neutralize --nginx-conf /etc/nginx/nginx.conf --sites-enabled /etc/nginx/sites-enabled
    nginx_site.py check-output FILE     exit 1 if `nginx -t` output (in FILE) has any [warn] or [emerg]
"""
from __future__ import annotations
import argparse, json, os, re, sys
from pathlib import Path

DOMAIN_RE = re.compile(r"(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

PROXY_LOCATIONS = """\
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_set_header X-Authenticated-Customer-Id "";
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "upgrade";

  location = /login {
    limit_req zone=atta_login burst=5 nodelay;
    proxy_pass http://127.0.0.1:__PORT__;
  }
  location = /upload {
    limit_req zone=atta_upload burst=3 nodelay;
    proxy_request_buffering off;
    proxy_pass http://127.0.0.1:__PORT__;
  }
  location / {
    proxy_pass http://127.0.0.1:__PORT__;
  }
"""

COMMON = """\
  server_tokens off;
  client_max_body_size 10G;
  client_body_timeout 120s;
  limit_conn atta_conn 20;
  limit_req_status 429;
  limit_conn_status 429;
"""

ACME = """\
  location ^~ /.well-known/acme-challenge/ {
    root __ACME__;
    default_type text/plain;
    try_files $uri =404;
  }
"""

HEAD = """\
# Written by ATTa's bootstrap.sh (nginx_site.py) on every deploy — edits here are overwritten.
# Mode: __MODE__. Change the domain / email / public-HTTP settings in /srv/app-builder/.env and run `sudo bash run`.
# Per-address limits on logins, uploads and connections. This server's own checks (bootstrap's gate, ADM, the
# browser check) come from loopback and are not counted: an empty key is never limited.
geo $atta_limit_key {
  default    $binary_remote_addr;
  127.0.0.1  "";
  ::1        "";
}
limit_req_zone  $atta_limit_key zone=atta_login:10m  rate=10r/m;
limit_req_zone  $atta_limit_key zone=atta_upload:10m rate=6r/m;
limit_conn_zone $atta_limit_key zone=atta_conn:10m;
"""


class SiteError(ValueError):
    pass


def domains_of(text):
    names = [d for d in re.split(r"[,\s]+", (text or "").strip()) if d]
    for d in names:
        if not DOMAIN_RE.fullmatch(d):
            raise SiteError(f"not a domain name: {d[:80]!r}")
    if len(set(names)) != len(names):
        raise SiteError("a domain is listed twice")
    return names


def mode_of(domains, email, allow_public_http, cert_dir):
    if domains and email:
        if (Path(cert_dir) / "fullchain.pem").is_file() and (Path(cert_dir) / "privkey.pem").is_file():
            return "tls"
        return "pending"
    return "public" if allow_public_http else "local"


def ipv6_available():
    """nginx refuses to start on a `listen [::]:80` where the kernel has IPv6 switched off (seen in containers)."""
    import socket
    if not socket.has_ipv6 or not Path("/proc/net/if_inet6").exists():
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM):
            return True
    except OSError:
        return False


def render(port, domains, email, allow_public_http, letsencrypt, acme_root, *, http_port=80, https_port=443,
           ipv6=None, cafile=None):
    """(site config text, proxy description dict). http_port/https_port/cafile exist for tests only."""
    if not (0 < int(port) < 65536):
        raise SiteError("port out of range")
    if email and not EMAIL_RE.fullmatch(email):
        raise SiteError(f"not an email address: {email[:80]!r}")
    if domains and not email:
        raise SiteError("APP_BUILDER_DOMAIN is set but APP_BUILDER_LETSENCRYPT_EMAIL is empty: HTTPS needs both")
    cert_dir = Path(letsencrypt) / "live" / domains[0] if domains else None
    mode = mode_of(domains, email, allow_public_http, cert_dir or "/nonexistent")
    names = " ".join(domains) if domains else "_"
    ipv6 = ipv6_available() if ipv6 is None else ipv6
    H, S = int(http_port), int(https_port)
    v6h = f"  listen [::]:{H} default_server;\n" if ipv6 else ""
    v6s = f"  listen [::]:{S} ssl default_server;\n" if ipv6 else ""
    proxy = PROXY_LOCATIONS.replace("__PORT__", str(int(port)))
    acme = ACME.replace("__ACME__", str(acme_root))
    out = [HEAD.replace("__MODE__", mode)]
    if mode == "tls":
        redirect = "https://$host$request_uri" if S == 443 else f"https://$host:{S}$request_uri"
        out.append(f"server {{\n  listen {H} default_server;\n{v6h}  server_name {names};\n"
                   f"{COMMON}{acme}  location / {{\n    return 308 {redirect};\n  }}\n}}\n")
        out.append(f"server {{\n  listen {S} ssl default_server;\n{v6s}"
                   f"  server_name {names};\n{COMMON}"
                   f"  ssl_certificate {cert_dir}/fullchain.pem;\n  ssl_certificate_key {cert_dir}/privkey.pem;\n"
                   "  ssl_protocols TLSv1.2 TLSv1.3;\n  ssl_prefer_server_ciphers off;\n"
                   "  ssl_session_cache shared:atta_tls:10m;\n  ssl_session_timeout 1d;\n"
                   '  add_header Strict-Transport-Security "max-age=31536000" always;\n'
                   f"{acme}{proxy}}}\n")
        desc = {"scheme": "https", "host": domains[0], "port": S, "connect": "127.0.0.1", "redirect_http": True,
                "http_port": H}
    elif mode == "pending":
        out.append(f"server {{\n  listen {H} default_server;\n{v6h}  server_name {names};\n"
                   f"{COMMON}{acme}  location / {{\n    default_type text/plain;\n"
                   "    return 503 \"HTTPS is being set up for this site. Try again in a few minutes.\\n\";\n  }\n}\n")
        out.append(f"server {{\n  listen 127.0.0.1:{H} default_server;\n  server_name _;\n{COMMON}{acme}{proxy}}}\n")
        desc = {"scheme": "http", "host": "127.0.0.1", "port": H, "connect": "127.0.0.1", "redirect_http": False}
    elif mode == "public":
        out.append(f"server {{\n  listen {H} default_server;\n{v6h}  server_name _;\n"
                   f"{COMMON}{acme}{proxy}}}\n")
        desc = {"scheme": "http", "host": "127.0.0.1", "port": H, "connect": "127.0.0.1", "redirect_http": False}
    else:
        out.append(f"server {{\n  listen 127.0.0.1:{H} default_server;\n  server_name _;\n{COMMON}{acme}{proxy}}}\n")
        desc = {"scheme": "http", "host": "127.0.0.1", "port": H, "connect": "127.0.0.1", "redirect_http": False}
    desc["mode"] = mode
    if cafile:
        desc["cafile"] = str(cafile)
    return "\n".join(out), desc


def _skip_quoted(text, i):
    """Index just past the quoted string starting at text[i]."""
    q, j, n = text[i], i + 1, len(text)
    while j < n and text[j] != q:
        j += 2 if text[j] == "\\" else 1
    return min(j + 1, n)


def _block_end(text, i):
    """text[i] is '{': index just past its matching '}', or -1. Comments and quoted strings are skipped."""
    d, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if c == "#":
            j = text.find("\n", j)
            if j < 0:
                return -1
            continue
        if c in "\"'":
            j = _skip_quoted(text, j); continue
        if c == "{":
            d += 1
        elif c == "}":
            d -= 1
            if d == 0:
                return j + 1
        j += 1
    return -1


def _strip_server_blocks(text):
    """Remove `server { ... }` blocks that sit directly inside `http { ... }` (brace-aware; comments and quoted
    strings respected). Returns (new text, number removed). Anything it cannot parse is returned unchanged."""
    out, stmt, stmt_start, stack, removed = [], "", 0, [], 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "#":                                   # a comment: kept, never part of a statement
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(text[i:j]); i = j; continue
        if c in "\"'":
            j = _skip_quoted(text, i)
            out.append(text[i:j]); stmt += text[i:j]; i = j; continue
        if c == "{":
            words = stmt.split()
            if words == ["server"] and stack == ["http"]:
                j = _block_end(text, i)
                if j < 0:
                    return text, 0
                del out[stmt_start:]
                out.append("\n    # (stock server block removed by ATTa: nginx_site.py neutralize)")
                removed += 1
                i, stmt, stmt_start = j, "", len(out) + 0
                continue
            stack.append(words[0] if words else "")
            out.append(c); i += 1; stmt, stmt_start = "", len(out); continue
        if c == "}":
            if not stack:
                return text, 0
            stack.pop()
            out.append(c); i += 1; stmt, stmt_start = "", len(out); continue
        if c == ";":
            out.append(c); i += 1; stmt, stmt_start = "", len(out); continue
        out.append(c); stmt += c; i += 1
    if stack:
        return text, 0
    return "".join(out), removed


def neutralize(nginx_conf, sites_enabled):
    """Take the distribution's stock sites out of the way. Returns a list of what was changed (for the log)."""
    changed = []
    se = Path(sites_enabled)
    d = se / "default"
    if d.is_symlink() or d.exists():
        d.unlink()
        changed.append(f"disabled {d} (Debian/Ubuntu stock site)")
    conf = Path(nginx_conf)
    if conf.is_file():
        text = conf.read_text()
        new, removed = _strip_server_blocks(text)
        if removed:
            backup = conf.with_name(conf.name + ".atta-orig")
            if not backup.exists():
                backup.write_text(text)
            tmp = conf.with_name(conf.name + ".atta-tmp")
            tmp.write_text(new)
            os.chmod(tmp, conf.stat().st_mode & 0o777)
            os.replace(tmp, conf)
            changed.append(f"removed {removed} stock server block(s) from {conf} (original kept as {backup.name})")
    return changed


def write_file(path, text):
    """Atomic write (temp + rename) — but ONLY over a regular file or nothing. Renaming onto a device, FIFO or folder
    would replace it: as root, `--out /dev/null` once turned the server's /dev/null into a plain file."""
    import stat
    p = Path(path)
    try:
        st = os.lstat(p)
        if not stat.S_ISREG(st.st_mode):
            raise SiteError(f"refusing to replace {p}: it is not a regular file")
    except FileNotFoundError:
        pass
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)


def check_output(text):
    """nginx -t output: any warning (e.g. 'conflicting server name') or error means the site may not be served."""
    bad = [l.strip() for l in text.splitlines() if "[warn]" in l or "[emerg]" in l or "[alert]" in l or "[crit]" in l]
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--port", required=True, type=int)
    r.add_argument("--domains", default="")
    r.add_argument("--email", default="")
    r.add_argument("--allow-public-http", default="false")
    r.add_argument("--letsencrypt", default="/etc/letsencrypt")
    r.add_argument("--acme-root", default="/var/lib/atta-acme")
    r.add_argument("--out", required=True)
    r.add_argument("--proxy-json", required=True)
    r.add_argument("--http-port", type=int, default=80, help=argparse.SUPPRESS)     # tests
    r.add_argument("--https-port", type=int, default=443, help=argparse.SUPPRESS)   # tests
    r.add_argument("--ipv6", choices=("auto", "yes", "no"), default="auto")
    n = sub.add_parser("neutralize")
    n.add_argument("--nginx-conf", default="/etc/nginx/nginx.conf")
    n.add_argument("--sites-enabled", default="/etc/nginx/sites-enabled")
    k = sub.add_parser("check", help="validate the settings only; writes nothing")
    k.add_argument("--port", required=True, type=int)
    k.add_argument("--domains", default="")
    k.add_argument("--email", default="")
    k.add_argument("--allow-public-http", default="false")
    c = sub.add_parser("check-output")
    c.add_argument("file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            render(a.port, domains_of(a.domains), a.email, a.allow_public_http == "true", "/nonexistent",
                   "/var/lib/atta-acme", ipv6=False)
            print("ok")
            return 0
        if a.cmd == "render":
            site, desc = render(a.port, domains_of(a.domains), a.email, a.allow_public_http == "true",
                                a.letsencrypt, a.acme_root, http_port=a.http_port, https_port=a.https_port,
                                ipv6=None if a.ipv6 == "auto" else a.ipv6 == "yes")
            for path, text in ((a.out, site), (a.proxy_json, json.dumps(desc, indent=2) + "\n")):
                write_file(path, text)
            print(desc["mode"])
            return 0
        if a.cmd == "neutralize":
            for line in neutralize(a.nginx_conf, a.sites_enabled):
                print(line)
            return 0
        if a.cmd == "check-output":
            bad = check_output(Path(a.file).read_text(errors="replace"))
            for l in bad:
                print(l)
            return 1 if bad else 0
    except (SiteError, OSError) as e:
        print(f"nginx_site: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
