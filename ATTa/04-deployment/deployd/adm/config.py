"""ADM (ATTa Deploy Manager) — settings. Every other adm module reads from here."""
from pathlib import Path
import os

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Where the LIVE system's data lives (same value the rest of ATTa uses). Change only if bootstrap.sh's ROOT changes.
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
# Where the LIVE code runs from — the folder bootstrap.sh copies 04-deployment into and systemd starts from.
# Must match APP= in bootstrap.sh, or ADM backs up and health-checks the wrong folder.
APP = Path(os.environ.get("APP_BUILDER_APP", "/opt/app-builder"))
# ADM's own folder. Releases, staging, backups, journals all live under here. Move it with ATTA_ADM_ROOT.
ADM = Path(os.environ.get("ATTA_ADM_ROOT", str(ROOT / "adm")))
INCOMING = ADM / "incoming"     # drop a bundle zip here (or use deployctl deploy) to queue a deploy
# v116: the (non-root) pipeline hands web-uploaded bundles over here. Everything taken from it is a WEB job:
# the uploading account must be an enabled admin, checked again by authz. Owned by the runner user.
REQUESTS = ADM / "requests"
QUEUE    = ADM / "state" / "queue"    # one <job>.zip + <job>.json per queued deploy, oldest first
JOURNAL  = ADM / "state" / "journal"  # one <job>.json per deploy: every event, the verdict, the rollback if any
STAGING  = ADM / "staging"      # bundle extracted and checked here before anything live is touched
RELEASES = ADM / "releases"     # every bundle that was activated, kept whole so it can be re-run for rollback
BACKUPS  = ADM / "backups"      # copy of the live code folder taken before each activation
LOGS     = ADM / "logs"         # full `bash run` output per job; read the last line + FAIL lines
CURRENT  = ADM / "current"      # symlink -> the release that is live now
PREVIOUS = ADM / "previous"     # symlink -> the release that was live before it (rollback target)
RUNNING  = ADM / "state" / "running"  # v117: a job is CLAIMED by an atomic rename from QUEUE into here
# v117: the server-wide deploy lock (adm/lock.py). deployd, deployctl AND a hand-typed `bash run` (bootstrap.sh)
# all take this one; its fd is passed to the installer so the whole deploy tree holds it. Owner beside it (.owner.json).
LOCK     = ADM / "state" / "deployd.lock"
# v117: ADM's own known-good record (the BUNDLE to re-run if a fast restore is impossible); code-level known-good
# lives with the code releases (CODE_RELEASES/.known-good.json, adm/releases.py).
KNOWN_GOOD = ADM / "state" / "known_good.json"
# The live pipeline's lock. ADM waits until it is gone before restarting services, so a running build isn't killed.
PIPELINE_LOCK = ROOT / "state" / "pipeline.lock"
# Longest ADM waits for a running build to finish before deploying anyway (seconds). 0 = never wait.
PIPELINE_WAIT = int(os.environ.get("ATTA_ADM_PIPELINE_WAIT", "3600"))
# Longest a full `bash run` may take (seconds). First deploy on a fresh box installs Docker/Playwright: keep this generous.
DEPLOY_TIMEOUT = int(os.environ.get("ATTA_ADM_DEPLOY_TIMEOUT", "5400"))
# v117: on timeout or cancel the whole deploy tree gets SIGTERM, this long to finish, then SIGKILL (adm/proc.py).
KILL_GRACE = float(os.environ.get("ATTA_ADM_KILL_GRACE", "30"))
# v117: longest a rollback (fast restore, or re-running the known-good bundle) may take.
ROLLBACK_TIMEOUT = int(os.environ.get("ATTA_ADM_ROLLBACK_TIMEOUT", "3600"))
# Files a bundle MUST contain (relative to its root) or it is refused before anything live is touched.
REQUIRED_FILES = ["run", "release.json", "04-deployment/bootstrap.sh", "04-deployment/gateway.py",
                  "04-deployment/pipeline.py", "04-deployment/deployd/deployd.py"]
# v117: where bootstrap.sh installs code releases (APP is a symlink to one of them). Must match RELS= in bootstrap.sh.
CODE_RELEASES = Path(os.environ.get("ATTA_CODE_RELEASES", "/opt/app-builder-releases"))
# v117 health (atta_health.py): the gateway must PROVE it is this installation (key from .env), on the port .env
# names, directly and through nginx as bootstrap.sh last rendered it (PROXY_FILE: scheme/host/port), plus a real
# browser through the proxy. Nothing here names a port: it comes from .env.
ENV_FILE = ROOT / ".env"
PROXY_FILE = ROOT / "state" / "proxy.json"
REQUIRE_PROXY = True          # tests without nginx turn this off in-process; a server always checks the proxy
BROWSER_CHECK = os.environ.get("ATTA_ADM_BROWSER_CHECK", "1") != "0"
PLAYWRIGHT_BROWSERS = os.environ.get("ATTA_BROWSERS", "/opt/ms-playwright")
# systemd units that must be active after a deploy. Add here if bootstrap.sh gains a service.
SERVICES = ["app-builder-gateway.service", "app-builder-pipeline.service", "app-builder-watcher.service", "nginx.service"]
# How many times / how long the health check retries after a restart (seconds between tries, total tries).
HEALTH_RETRY_SECONDS = 2
HEALTH_RETRIES = 30
# How many releases and backups to keep. Older ones are deleted after a successful deploy; the live one is never deleted.
KEEP_RELEASES = 5
KEEP_BACKUPS = 5
# v114: limits on a bundle while it is staged, so a hostile or broken zip can't fill the disk.
# Total uncompressed size (bytes), number of files, and the largest compression ratio allowed for one member.
MAX_BUNDLE_BYTES = int(os.environ.get("ATTA_ADM_MAX_BUNDLE_BYTES", str(2 * 1024**3)))
MAX_BUNDLE_FILES = int(os.environ.get("ATTA_ADM_MAX_BUNDLE_FILES", "20000"))
MAX_COMPRESSION_RATIO = int(os.environ.get("ATTA_ADM_MAX_COMPRESSION_RATIO", "200"))
# v116: what an ATTa bundle's root folder may hold. Anything else (stray scripts, units, files beside the
# bundle's own folder in the zip) refuses the bundle before anything live is touched.
ALLOWED_TOP = {"run", "release.json", "START-HERE.txt", "README.md", ".gitignore", "01-specs", "02-front-door",
               "03-ui-skins-capability-package", "04-deployment", "05-coolify", "docs", "tests"}
# v116: a bundle's own test suite runs (as an unprivileged user, no secrets) before it can be activated.
# 0 = skip (only for an emergency; the job's journal says it was skipped).
RUN_TESTS = os.environ.get("ATTA_ADM_RUN_TESTS", "1") != "0"
TEST_TIMEOUT = int(os.environ.get("ATTA_ADM_TEST_TIMEOUT", "1200"))
TEST_USER = os.environ.get("ATTA_ADM_TEST_USER", "nobody")
# How often deployd looks at the queue (seconds).
POLL_SECONDS = 5
# v115: the only user whose files ADM trusts in its queue and incoming folders. Root on a server. Not read from
# the environment on purpose: whoever sets deployd's environment is root already. Tests override it in-process.
TRUSTED_UID = 0
# ==========================================================================================

DIRS = (INCOMING, QUEUE, RUNNING, JOURNAL, STAGING, RELEASES, BACKUPS, LOGS)
# v115: nobody but TRUSTED_UID may read or write these. A job's authority depends on them (see authz.py).
PRIVATE_DIRS = (INCOMING, QUEUE, RUNNING)


def ensure_dirs():
    for d in DIRS:
        d.mkdir(parents=True, exist_ok=True)
    for d in PRIVATE_DIRS:
        try:
            if d.stat().st_uid == os.geteuid():
                d.chmod(0o700)
        except OSError:
            pass   # authz refuses jobs from a folder it cannot vouch for, so a failure here fails closed
