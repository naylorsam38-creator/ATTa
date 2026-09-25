"""Activation = run the bundle's own `bash run` (which runs bootstrap.sh: copies code to the live
folder, rewrites the systemd units, restarts services, and refuses unless its own gate passes).
ADM does not re-implement the deploy; it wraps the one that already exists, then keeps the release
whole so the same command can be run again for rollback."""
from pathlib import Path
import json, os, shutil, subprocess, time
from . import config


class ActivationFailed(RuntimeError):
    pass


def promote(root, job, version):
    """Move the checked bundle root out of staging into releases/, whole."""
    safe = "".join(c if c.isalnum() or c in "._-+" else "_" for c in version)
    dest = config.RELEASES / f"{job}-{safe}"
    shutil.rmtree(dest, ignore_errors=True)
    shutil.move(str(root), str(dest))
    return dest


def _link(link, target):
    tmp = link.with_name(link.name + ".new")
    tmp.unlink(missing_ok=True)
    os.symlink(str(Path(target).resolve()), tmp)
    os.replace(tmp, link)


def current():
    try:
        return config.CURRENT.resolve(strict=True) if config.CURRENT.is_symlink() else None
    except OSError:
        return None


def previous():
    try:
        return config.PREVIOUS.resolve(strict=True) if config.PREVIOUS.is_symlink() else None
    except OSError:
        return None


def pipeline_busy():
    """True while the live pipeline is inside a build (its lock names a live pid)."""
    p = config.PIPELINE_LOCK
    if not p.exists():
        return False
    try:
        pid = int(json.loads(p.read_text())["pid"])
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, KeyError):
        return False


def wait_for_pipeline(log):
    waited = 0
    while pipeline_busy():
        if config.PIPELINE_WAIT and waited >= config.PIPELINE_WAIT:
            log.write(f"WARN pipeline still busy after {waited}s; deploying anyway\n"); log.flush()
            return False
        if waited == 0:
            log.write("waiting: a build is running; services will be restarted once it finishes\n"); log.flush()
        time.sleep(config.POLL_SECONDS); waited += config.POLL_SECONDS
    return True


def run_bootstrap(release, log):
    """`bash run` from the release root. Full output goes to the job log. Raises on non-zero exit."""
    env = {**os.environ, "APP_BUILDER_ROOT": str(config.ROOT), "ATTA_ADM_ACTIVE": "1"}  # v114.1: ADM owns rollback
    env.pop("SUDO_USER", None)
    log.write(f"=== bash run  (cwd {release})\n"); log.flush()
    try:
        r = subprocess.run(["bash", "run"], cwd=str(release), stdout=log, stderr=subprocess.STDOUT,
                           env=env, timeout=config.DEPLOY_TIMEOUT)
    except subprocess.TimeoutExpired:
        log.write(f"FAIL bash run timed out after {config.DEPLOY_TIMEOUT}s\n"); log.flush()
        raise ActivationFailed(f"bash run timed out after {config.DEPLOY_TIMEOUT}s")
    log.write(f"=== bash run exit {r.returncode}\n"); log.flush()
    if r.returncode:
        raise ActivationFailed(f"bash run exited {r.returncode} (see log)")


def stamp(release, version, job):
    """Record what is live: release.json inside the live code folder + the current/previous links."""
    if config.APP.is_dir():
        (config.APP / "release.json").write_text(json.dumps({"version": version, "adm_job": job,
                                                              "release": str(release)}, indent=2) + "\n")
    old = current()
    if old and old != Path(release).resolve():
        _link(config.PREVIOUS, old)
    _link(config.CURRENT, release)


def activate(release, version, job, log):
    wait_for_pipeline(log)
    run_bootstrap(release, log)
    stamp(release, version, job)


def run_bootstrap_dir(code_dir, log):
    """Run bootstrap.sh straight from a backup of the live code folder (rollback path when no
    earlier release exists). bootstrap.sh copies its own folder into the live folder and restarts."""
    env = {**os.environ, "APP_BUILDER_ROOT": str(config.ROOT), "ATTA_ADM_ACTIVE": "1"}  # v114.1: ADM owns rollback
    log.write(f"=== bash bootstrap.sh  (cwd {code_dir})\n"); log.flush()
    try:
        r = subprocess.run(["bash", "bootstrap.sh"], cwd=str(code_dir), stdout=log, stderr=subprocess.STDOUT,
                           env=env, timeout=config.DEPLOY_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ActivationFailed(f"bootstrap.sh timed out after {config.DEPLOY_TIMEOUT}s")
    log.write(f"=== bootstrap.sh exit {r.returncode}\n"); log.flush()
    if r.returncode:
        raise ActivationFailed(f"bootstrap.sh exited {r.returncode} (see log)")
