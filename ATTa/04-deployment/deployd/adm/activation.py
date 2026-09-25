"""Activation = run the bundle's own `bash run` (bootstrap.sh). ADM does not re-implement the deploy; it wraps the
one that already exists, keeps the bundle whole so it can be re-run for a slow rollback, and (v117):

  - runs it as a process TREE (adm/proc.py): on timeout or cancel every descendant is stopped and that is
    confirmed before anything else happens;
  - passes the server-wide deploy lock down to it (ATTA_DEPLOY_LOCK_FD), so the installer and everything it
    starts hold the lock, and bootstrap.sh does not try to take it a second time;
  - tells it the deployment id (ATTA_DEPLOYMENT_ID) so bootstrap.sh records its steps (built, health_checking,
    verified, or failed + its own restore) in the SAME deployment record ADM opened.

No more stamp(): ADM's current/previous links and the known-good record move only when a deploy reaches `live`
(manager.py + releases.py); a failed release can never land in `previous`."""
from pathlib import Path
import json, os, shutil, time
from . import config, proc


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


def adm_known_good():
    """ADM's known-good BUNDLE record ({release, version, deployment_id, code_release}), if its folder still exists."""
    try:
        d = json.loads(config.KNOWN_GOOD.read_text())
    except (OSError, ValueError):
        return None
    return d if d.get("release") and (Path(d["release"]) / "run").is_file() else None


def record_live(bundle_release, version, job, code_release):
    """The deploy reached `live`: ADM's known-good bundle, and current/previous links, follow it."""
    old = current()
    new = Path(bundle_release).resolve()
    if old and old != new:
        _link(config.PREVIOUS, old)
    _link(config.CURRENT, new)
    doc = {"release": str(new), "version": version, "deployment_id": job, "code_release": str(code_release),
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tmp = config.KNOWN_GOOD.with_name(config.KNOWN_GOOD.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, config.KNOWN_GOOD)
    # Nothing is written INTO the release folder: once verified it is immutable (its content hash is the proof
    # that a rollback or reinstall brings back exactly the code that was verified).


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


def wait_for_pipeline(log, cancel=None):
    waited = 0
    while pipeline_busy():
        if config.PIPELINE_WAIT and waited >= config.PIPELINE_WAIT:
            log.write(f"WARN pipeline still busy after {waited}s; deploying anyway\n"); log.flush()
            return False
        if cancel is not None and cancel():
            raise proc.Cancelled("stopped while waiting for a build to finish")
        if waited == 0:
            log.write("waiting: a build is running; services will be restarted once it finishes\n"); log.flush()
        time.sleep(config.POLL_SECONDS); waited += config.POLL_SECONDS
    return True


def _env(job, lock_fd, extra=None):
    env = {**os.environ, "APP_BUILDER_ROOT": str(config.ROOT), "ATTA_ADM_ROOT": str(config.ADM),
           "ATTA_ADM_ACTIVE": "1", "ATTA_DEPLOYMENT_ID": str(job)}
    if lock_fd is not None:
        env["ATTA_DEPLOY_LOCK_FD"] = str(lock_fd)
        env["ATTA_DEPLOY_LOCK_PATH"] = str(config.LOCK)
    env.pop("SUDO_USER", None)
    env.update(extra or {})
    return env


def run_tree_logged(cmd, cwd, log, *, job, lock_fd, timeout, cancel=None, on_start=None, on_stop=None,
                    extra_env=None):
    """Run cmd as a stoppable tree with its output in the job log. Returns proc.TreeResult (never raises for it)."""
    log.write(f"=== {' '.join(cmd)}  (cwd {cwd}, timeout {timeout}s)\n"); log.flush()
    res = proc.run_tree(cmd, cwd=str(cwd), env=_env(job, lock_fd, extra_env), stdout=log, timeout=timeout,
                        grace=config.KILL_GRACE, pass_fds=(lock_fd,) if lock_fd is not None else (),
                        cancel=cancel, on_start=on_start, on_stop=on_stop)
    log.flush()
    what = ("TIMED OUT" if res.timed_out else "INTERRUPTED" if res.interrupted else f"exit {res.returncode}")
    log.write(f"=== {cmd[-1]}: {what} after {res.seconds}s\n")
    if res.signalled:
        log.write(f"    stopped {len(res.signalled)} process(es){' (SIGKILL needed)' if res.escalated else ''}: "
                  + "; ".join(f"{p} {c[:80]}" for p, c in res.signalled[:20]) + "\n")
    if res.leftovers:
        log.write(f"FAIL it left {len(res.leftovers)} process(es) running after it exited (now stopped): "
                  + "; ".join(f"{p} {c[:80]}" for p, c in res.leftovers[:20]) + "\n")
    if res.survivors:
        log.write(f"FAIL {len(res.survivors)} process(es) could not be stopped: "
                  + "; ".join(f"{p} {c[:80]}" for p, c in res.survivors) + "\n")
    log.flush()
    return res


def run_bootstrap(release, log, *, job, lock_fd, cancel=None, on_start=None, on_stop=None, timeout=None,
                  extra_env=None):
    """`bash run` from a bundle root (a deploy, or a slow rollback re-running the known-good bundle)."""
    return run_tree_logged(["bash", "run"], release, log, job=job, lock_fd=lock_fd,
                           timeout=timeout or config.DEPLOY_TIMEOUT, cancel=cancel, on_start=on_start,
                           on_stop=on_stop, extra_env=extra_env)


def run_bootstrap_dir(code_dir, log, *, job, lock_fd, cancel=None, timeout=None, extra_env=None):
    """bootstrap.sh straight from a code folder (an ADM backup): the last-resort rollback."""
    return run_tree_logged(["bash", "bootstrap.sh"], code_dir, log, job=job, lock_fd=lock_fd,
                           timeout=timeout or config.ROLLBACK_TIMEOUT, cancel=cancel, extra_env=extra_env)


def run_restore(txn_dir, log, *, job, lock_fd, cancel=None):
    """The fast, exact rollback bootstrap.sh prepared before it switched anything (restore.sh in its snapshot)."""
    return run_tree_logged(["bash", "restore.sh"], txn_dir, log, job=job, lock_fd=lock_fd,
                           timeout=config.ROLLBACK_TIMEOUT, cancel=cancel)
