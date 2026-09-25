"""The deploy itself: stage -> check -> back up -> activate -> health -> DEPLOYED,
or on any failure: ROLLING_BACK -> re-run the previous release (or the backup) -> health -> ROLLED_BACK.
Used by both deployd (queue) and deployctl (direct). One deploy at a time, enforced by a file lock."""
from contextlib import contextmanager
from pathlib import Path
import fcntl, hashlib, os, shutil, sys, traceback
from . import config, journal, queue, staging, backup, activation, health, authz


@contextmanager
def exclusive():
    config.ensure_dirs()
    with open(config.LOCK, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("another deploy is running (deployd or deployctl holds the lock)")
        f.write(str(os.getpid())); f.flush()
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _rollback(job, log, failed_release):
    journal.record(job, "ROLLING_BACK", reason="deploy failed; putting the previous version back")
    target = activation.previous() if failed_release else activation.current()
    kind = "release"
    if target is None or not (target / "run").is_file():
        target = backup.latest(); kind = "backup"
    if target is None:
        journal.record(job, "ROLLBACK_FAILED", reason="nothing to roll back to (no previous release, no backup)")
        log.write("FAIL rollback: nothing to roll back to\n"); return False
    log.write(f"=== rollback from {kind} {target}\n"); log.flush()
    try:
        if kind == "release":
            activation.run_bootstrap(target, log)
            activation.stamp(target, _version_of(target), job)
        else:
            # A backup is the live 04-deployment folder as it was. bootstrap.sh copies its own folder to APP.
            env_release = target
            activation.run_bootstrap_dir(env_release, log)
    except Exception as e:
        journal.record(job, "ROLLBACK_FAILED", reason=str(e), log=str(log.name))
        log.write(f"FAIL rollback: {e}\n"); return False
    fails = health.check()
    for line in fails:
        log.write(line + "\n")
    if fails:
        journal.record(job, "ROLLBACK_FAILED", reason="; ".join(fails), restored_from=str(target))
        return False
    journal.record(job, "ROLLED_BACK", restored_from=str(target), kind=kind)
    return True


def _version_of(release):
    try:
        import json
        return str(json.loads((Path(release) / "release.json").read_text()).get("version", "?"))
    except (OSError, ValueError):
        return "?"


def process(meta):
    """Run one queued job to a verdict. Returns the verdict string."""
    job = meta["job_id"]
    config.ensure_dirs()
    logp = config.LOGS / f"{job}.log"
    with open(logp, "a") as log, exclusive():
        journal.record(job, "STAGING", log=str(logp))
        release = None
        try:
            ok, why = authz.allowed(meta)
            if not ok:
                raise staging.BundleRejected(f"not authorised: {why}")
            staged = staging.extract(meta["archive"], job)
            root = staging.find_root(staged)
            version = staging.check(root)
            journal.record(job, "CHECKED", version=version, root=str(root))
            log.write(f"checked bundle version {version}\n")
            # v116: its own tests must pass before anything live is touched.
            if config.RUN_TESTS:
                ran = staging.run_tests(root, log)
                journal.record(job, "TESTED", result=ran)
            else:
                journal.record(job, "TESTS_SKIPPED", reason="ATTA_ADM_RUN_TESTS=0")
            b = backup.snapshot(job)
            journal.record(job, "BACKED_UP", backup=str(b) if b else None)
            release = activation.promote(root, job, version)
            journal.record(job, "ACTIVATING", release=str(release))
            activation.activate(release, version, job, log)
            fails = health.check()
            for line in fails:
                log.write(line + "\n")
            if fails:
                raise activation.ActivationFailed("; ".join(fails))
            journal.record(job, "HEALTHY")
            removed = backup.prune()
            journal.record(job, "DEPLOYED", version=version, release=str(release), pruned=removed)
            log.write(f"DEPLOYED {version}\n")
            verdict = "DEPLOYED"
        except staging.BundleRejected as e:
            log.write(f"FAIL bundle rejected: {e}\n")
            journal.record(job, "FAILED", reason=f"bundle rejected: {e}", touched_live=False)
            log.write("FAILED (nothing live was touched)\n")
            verdict = "FAILED"
        except Exception as e:
            log.write(f"FAIL {e}\n{traceback.format_exc()}")
            journal.record(job, "FAILED", reason=str(e), touched_live=release is not None)
            if release is not None:
                ok = _rollback(job, log, failed_release=release)
                log.write("ROLLED_BACK\n" if ok else "ROLLBACK_FAILED\n")
                verdict = "ROLLED_BACK" if ok else "ROLLBACK_FAILED"
            else:
                log.write("FAILED (nothing live was touched)\n")
                verdict = "FAILED"
        finally:
            staging.discard(job)
            queue.finish(meta)
            log.flush()
    return verdict


def self_hash():
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")) + [Path(__file__).parent.parent / "deployd.py"]:
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()
