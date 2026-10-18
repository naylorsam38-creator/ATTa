"""Snapshot of the LIVE code folder (config.APP) before each activation, and pruning of old ones.
A backup is a complete copy of 04-deployment as it runs now; bootstrap.sh can be run straight
from it, which is how rollback works when there is no earlier release to go back to."""
import shutil
from datetime import datetime, timezone
from . import config


def snapshot(job):
    if not config.APP.is_dir():
        return None  # first ever deploy on this box: nothing live to back up
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = config.BACKUPS / f"{ts}-{job}"
    shutil.copytree(config.APP, d, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    return d


def latest():
    dirs = sorted(p for p in config.BACKUPS.iterdir() if p.is_dir()) if config.BACKUPS.is_dir() else []
    return dirs[-1] if dirs else None


def prune():
    """Keep the newest KEEP_BACKUPS backups and KEEP_RELEASES releases; never the live or previous release."""
    keep = set()
    for link in (config.CURRENT, config.PREVIOUS):
        if link.is_symlink():
            try:
                keep.add(link.resolve())
            except OSError:
                pass
    removed = []
    if config.BACKUPS.is_dir():
        dirs = sorted(p for p in config.BACKUPS.iterdir() if p.is_dir())
        for p in dirs[:-config.KEEP_BACKUPS] if config.KEEP_BACKUPS > 0 else []:
            shutil.rmtree(p, ignore_errors=True); removed.append(str(p))
    if config.RELEASES.is_dir():
        dirs = sorted(p for p in config.RELEASES.iterdir() if p.is_dir())
        old = dirs[:-config.KEEP_RELEASES] if config.KEEP_RELEASES > 0 else []
        for p in old:
            if p.resolve() in keep:
                continue
            shutil.rmtree(p, ignore_errors=True); removed.append(str(p))
    return removed
