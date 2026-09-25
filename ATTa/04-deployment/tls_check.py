#!/usr/bin/env python3
"""Daily HTTPS check (v117), run by atta-tls-check.timer. Makes certificate trouble VISIBLE before it bites:

  - the certificate on disk for the first APP_BUILDER_DOMAIN: present, and how many days until it expires;
  - what nginx actually SERVES on 127.0.0.1:443 for that name: the same certificate (a renewal that nginx never
    picked up would otherwise expire in front of users while the file on disk looks fine);
  - the renewal timer is enabled and active.

Any problem: one line on stderr (in the journal), an ATTa alert (the /alerts page, and ALERT_WEBHOOK_URL if set,
at most once a day per problem), <ROOT>/state/tls-status.json, and exit 1 so the unit shows in `systemctl --failed`.

    tls_check.py [--env /srv/app-builder/.env] [--renewal-timer UNIT] [--warn-days 21] [--letsencrypt DIR]
"""
from __future__ import annotations
import argparse, hashlib, json, os, socket, ssl, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envfile  # noqa: E402


def _pem_der(path):
    """The first certificate in a PEM file, as DER bytes."""
    text = Path(path).read_text()
    start = text.index("-----BEGIN CERTIFICATE-----")
    end = text.index("-----END CERTIFICATE-----", start) + len("-----END CERTIFICATE-----")
    return ssl.PEM_cert_to_DER_cert(text[start:end])


def not_after(path):
    out = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", str(path)], capture_output=True, text=True,
                         check=True).stdout.strip()
    return datetime.strptime(out.split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)


def served_der(host, port=443, connect="127.0.0.1", timeout=10):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # we compare fingerprints with the file; trust is not the question here
    with socket.create_connection((connect, port), timeout) as s:
        with ctx.wrap_socket(s, server_hostname=host) as t:
            return t.getpeercert(binary_form=True)


def timer_ok(unit):
    if not unit:
        return True
    a = subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0
    e = subprocess.run(["systemctl", "is-enabled", "--quiet", unit]).returncode == 0
    return a and e


def check(env_file, letsencrypt, renewal_timer, warn_days, now=None):
    """(status dict, [problems])."""
    vals, _ = envfile.load(str(env_file))
    domains = [d for d in (vals.get("APP_BUILDER_DOMAIN") or "").replace(",", " ").split() if d]
    status = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "domain": None, "problems": []}
    if not domains or not vals.get("APP_BUILDER_LETSENCRYPT_EMAIL"):
        status["configured"] = False
        return status, []
    d = domains[0]
    status.update(domain=d, configured=True)
    cert = Path(letsencrypt) / "live" / d / "fullchain.pem"
    problems = []
    if not cert.is_file():
        problems.append(f"no certificate on disk for {d} ({cert}); HTTPS is not set up — run `sudo bash run`")
    else:
        exp = not_after(cert)
        days = (exp - (now or datetime.now(timezone.utc))).total_seconds() / 86400
        status.update(not_after=exp.isoformat(), days_left=round(days, 1))
        if days < 0:
            problems.append(f"the certificate for {d} EXPIRED on {exp:%Y-%m-%d}")
        elif days < warn_days:
            problems.append(f"the certificate for {d} expires in {days:.0f} days ({exp:%Y-%m-%d}) and has not been "
                            "renewed: renewals are failing (see `journalctl -u certbot*` / `certbot renew --dry-run`)")
        try:
            same = hashlib.sha256(served_der(d)).hexdigest() == hashlib.sha256(_pem_der(cert)).hexdigest()
            status["served_matches_disk"] = same
            if not same:
                problems.append(f"nginx serves a different certificate for {d} than the one on disk (a renewal was "
                                "not picked up): `sudo nginx -t && sudo systemctl reload nginx`")
        except (OSError, ssl.SSLError, ValueError) as e:
            status["served_matches_disk"] = None
            problems.append(f"could not read the certificate nginx serves for {d} on 127.0.0.1:443 ({e})")
    if renewal_timer and not timer_ok(renewal_timer):
        problems.append(f"the certificate renewal timer {renewal_timer} is not enabled and active")
    status["renewal_timer"] = renewal_timer
    status["problems"] = problems
    return status, problems


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=os.environ.get("ATTA_ENV_FILE", "/srv/app-builder/.env"))
    ap.add_argument("--letsencrypt", default="/etc/letsencrypt")
    ap.add_argument("--renewal-timer", default="")
    ap.add_argument("--warn-days", type=float, default=21)
    a = ap.parse_args(argv)
    try:
        status, problems = check(a.env, a.letsencrypt, a.renewal_timer, a.warn_days)
    except (OSError, envfile.EnvFileError, subprocess.CalledProcessError, ValueError) as e:
        status, problems = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, [f"check failed: {e}"]
    root = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
    try:
        (root / "state").mkdir(parents=True, exist_ok=True)
        tmp = root / "state" / ".tls-status.json.tmp"
        tmp.write_text(json.dumps(status, indent=2) + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, root / "state" / "tls-status.json")
    except OSError:
        pass
    if not problems:
        print("TLS OK" + (f": {status['domain']} valid for {status.get('days_left')} more days"
                          if status.get("domain") else " (HTTPS not configured)"))
        return 0
    for p in problems:
        print(f"TLS PROBLEM: {p}", file=sys.stderr)
    # One alert per problem per day (the timer is daily; a manual re-run the same day doesn't repeat it).
    stamp = root / "state" / ".tls-alerted"
    key = hashlib.sha256((time.strftime("%Y-%m-%d") + "|".join(problems)).encode()).hexdigest()
    try:
        if not stamp.exists() or stamp.read_text() != key:
            import alerts
            alerts.system_alert("tls", "HTTPS certificate: " + " / ".join(problems), domain=status.get("domain"),
                                days_left=status.get("days_left"))
            stamp.write_text(key)
    except Exception as e:  # the journal line and the failed unit still say it
        print(f"TLS PROBLEM: could not raise the ATTa alert ({e})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
