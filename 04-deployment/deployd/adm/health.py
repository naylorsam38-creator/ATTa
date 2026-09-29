"""Independent post-deploy check. bootstrap.sh has its own gate; this one runs AFTER it from
outside, so a deploy is only DEPLOYED when both agree. Returns a list of FAIL lines (empty = healthy)."""
import subprocess, time, urllib.error, urllib.request
from . import config


def _active(unit):
    r = subprocess.run(["systemctl", "is-active", "--quiet", unit])
    return r.returncode == 0


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read(200)


def check():
    fails = []
    for unit in config.SERVICES:
        if not _active(unit):
            fails.append(f"FAIL service not active: {unit}")
    ok = False
    for _ in range(config.HEALTH_RETRIES):
        try:
            status, body = _get(config.HEALTH_URL)
            if status == 200 and body.strip() == b"OK":
                ok = True; break
        except Exception:
            pass
        time.sleep(config.HEALTH_RETRY_SECONDS)
    if not ok:
        fails.append(f"FAIL gateway health did not answer OK at {config.HEALTH_URL}")
    try:
        urllib.request.urlopen(config.FRONT_URL, timeout=5).read(10)
    except urllib.error.HTTPError:
        pass  # any HTTP answer means nginx is up
    except Exception as e:
        fails.append(f"FAIL nginx did not answer at {config.FRONT_URL}: {e}")
    return fails
