#!/usr/bin/env python3
"""deployd — ATTa Deploy Manager daemon (runs as the atta-deployd systemd service).

Watches the deploy queue. A bundle gets there two ways:
  1. someone uploads an ATTa bundle on the web Add page -> pipeline.py queues it (build record links to the job)
  2. `deployctl deploy <zip>` (runs it itself) or a zip dropped into <ADM>/incoming/
Each job: validate -> build (off to the side) -> switch + verify -> live, or failed/timed_out/interrupted and a
rollback to exactly the release that was live before (see adm/manager.py for the state machine).

v117:
  - the server-wide deploy lock is taken BEFORE a job is claimed; a job is claimed by an atomic rename, so it is
    never picked up twice, and deployctl's own jobs never enter the shared queue at all;
  - at start (and before every job) deploys that were killed are recovered: stragglers stopped, the record marked
    `interrupted`, the previous release put back if anything was switched. They are never re-run;
  - SIGTERM (systemctl stop/restart) stops the running deploy's whole process tree and marks it interrupted; its
    rollback runs when deployd starts again.

Read the outcome:  deployctl status     (last job + verdict)
                   deployctl journal <job>
                   last line of <ADM>/logs/<job>.log, plus any FAIL lines
"""
import os, signal, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adm import config, queue, manager, journal  # noqa: E402
from adm.lock import DeployLock, Busy  # noqa: E402

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Everything tunable lives in adm/config.py (poll interval, timeouts, keep counts, paths).
# ==========================================================================================

_stopping = threading.Event()


def _on_term(signum, frame):
    print(f"deployd: signal {signum}: stopping (a running deploy is interrupted; its rollback runs on next start)",
          flush=True)
    _stopping.set()


def _say(msg):
    print(msg, flush=True)


def one_pass(started_hash):
    """Sweep the drop folders, then (under the lock) recover and run at most one job. Returns 'restart' when this
    daemon's own code changed with a deploy."""
    queue.sweep_incoming()
    queue.sweep_requests()   # v116: web-uploaded bundles handed over by the non-root pipeline
    probe = journal.new_job_id() + "-deployd"
    try:
        lk = DeployLock(config.LOCK, probe).acquire()
    except Busy as e:
        # v117: a lock held by a KILLED deployment's leftovers is freed (see manager.reclaim_abandoned).
        if manager.reclaim_abandoned(log_to=_say) is None and queue.pending():
            _say(f"deployd: waiting — {e}")
        return None
    try:
        manager.recover(lk, log_to=_say)
        if _stopping.is_set():
            return None
        meta = queue.claim_next()
        if meta is None:
            return None
        lk.deployment_id = meta["job_id"]
        lk._write_owner()                      # the owner record names the job actually running
        _say(f"job {meta['job_id']}: {meta.get('original_name')} (build {meta.get('build_id')}, "
             f"origin {meta.get('origin')}, by {meta.get('requested_by')})")
        verdict = manager.process(meta, lk=lk, cancel=_stopping.is_set, shutting_down=_stopping.is_set)
        _say(f"job {meta['job_id']}: {verdict}")
        if verdict == "DEPLOYED" and manager.self_hash() != started_hash:
            return "restart"
    finally:
        lk.release()
    return None


def main():
    config.ensure_dirs()
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    started_hash = manager.self_hash()
    _say(f"deployd up: queue={config.QUEUE} incoming={config.INCOMING} live={config.APP}")
    while not _stopping.is_set():
        try:
            if one_pass(started_hash) == "restart":
                _say("deployd code changed by this deploy; restarting on the live version")
                os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            _say(f"deployd error: {e}")
        _stopping.wait(config.POLL_SECONDS)
    _say("deployd stopped")


if __name__ == "__main__":
    main()
