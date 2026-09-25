"""Independent post-deploy check (v117). bootstrap.sh has its own gate; this one runs AFTER it, from outside, so a
deploy is only DEPLOYED when both agree. Returns a list of FAIL lines (empty = healthy).

It no longer asks "does anything answer on 8787 / on port 80?" (v114.2's nginx welcome page passed that). It uses
atta_health.py: the gateway must prove it is THIS installation, on the port .env names, answering as the release
that is supposed to be live — directly, through nginx with the scheme nginx should serve, and in a real browser."""
import os, subprocess, sys
from pathlib import Path
from . import config

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # 04-deployment (atta_health, atta_identity)
import atta_health  # noqa: E402


def _active(unit):
    r = subprocess.run(["systemctl", "is-active", "--quiet", unit])
    return r.returncode == 0


def check(expect_release=None, legacy=False, browser=None):
    """expect_release: the code release folder name that must be answering (None = any release of this install).
    legacy: the release predates identity proofs (v116 and older): body OK + ATTa's old login page instead."""
    fails = []
    for unit in config.SERVICES:
        if not _active(unit):
            fails.append(f"FAIL service not active: {unit}")
    proxy_file = config.PROXY_FILE if Path(config.PROXY_FILE).is_file() else None
    # A release from before v117 never recorded what nginx should serve (proxy.json): its checks are direct only.
    if proxy_file is None and config.REQUIRE_PROXY and not legacy:
        fails.append(f"FAIL {config.PROXY_FILE} missing: bootstrap.sh did not record what nginx should serve")
    browser = config.BROWSER_CHECK if browser is None else browser
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", config.PLAYWRIGHT_BROWSERS)   # v116: Chromium's shared folder
    ok, lines = atta_health.run_checks(config.ENV_FILE, proxy_file=proxy_file, direct=True, proxy=bool(proxy_file),
                                       browser=bool(proxy_file) and browser, expect_release=expect_release,
                                       legacy=legacy, timeout=config.HEALTH_RETRIES * config.HEALTH_RETRY_SECONDS,
                                       interval=config.HEALTH_RETRY_SECONDS)
    if not ok:
        fails.append(lines[-1] if lines[-1].startswith("FAIL") else f"FAIL {lines[-1]}")
    return fails
