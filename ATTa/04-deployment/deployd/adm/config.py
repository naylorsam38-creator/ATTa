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
QUEUE    = ADM / "state" / "queue"    # one <job>.zip + <job>.json per queued deploy, oldest first
JOURNAL  = ADM / "state" / "journal"  # one <job>.json per deploy: every event, the verdict, the rollback if any
STAGING  = ADM / "staging"      # bundle extracted and checked here before anything live is touched
RELEASES = ADM / "releases"     # every bundle that was activated, kept whole so it can be re-run for rollback
BACKUPS  = ADM / "backups"      # copy of the live code folder taken before each activation
LOGS     = ADM / "logs"         # full `bash run` output per job; read the last line + FAIL lines
CURRENT  = ADM / "current"      # symlink -> the release that is live now
PREVIOUS = ADM / "previous"     # symlink -> the release that was live before it (rollback target)
LOCK     = ADM / "state" / "deployd.lock"  # flock: deployd and deployctl never deploy at the same time
# The live pipeline's lock. ADM waits until it is gone before restarting services, so a running build isn't killed.
PIPELINE_LOCK = ROOT / "state" / "pipeline.lock"
# Longest ADM waits for a running build to finish before deploying anyway (seconds). 0 = never wait.
PIPELINE_WAIT = int(os.environ.get("ATTA_ADM_PIPELINE_WAIT", "3600"))
# Longest a full `bash run` may take (seconds). First deploy on a fresh box installs Docker/Playwright: keep this generous.
DEPLOY_TIMEOUT = int(os.environ.get("ATTA_ADM_DEPLOY_TIMEOUT", "5400"))
# Files a bundle MUST contain (relative to its root) or it is refused before anything live is touched.
REQUIRED_FILES = ["run", "release.json", "04-deployment/bootstrap.sh", "04-deployment/gateway.py",
                  "04-deployment/pipeline.py", "04-deployment/deployd/deployd.py"]
# Gateway health URL (bootstrap.sh's own gate uses the same one). Must answer with body "OK".
HEALTH_URL = os.environ.get("ATTA_ADM_HEALTH_URL", "http://127.0.0.1:8787/health")
# Nginx front URL — must answer at all (any status) for the deploy to count.
FRONT_URL = os.environ.get("ATTA_ADM_FRONT_URL", "http://127.0.0.1/")
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
# How often deployd looks at the queue (seconds).
POLL_SECONDS = 5
# ==========================================================================================

DIRS = (INCOMING, QUEUE, JOURNAL, STAGING, RELEASES, BACKUPS, LOGS)


def ensure_dirs():
    for d in DIRS:
        d.mkdir(parents=True, exist_ok=True)
