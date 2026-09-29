#!/usr/bin/env python3
"""deployd — ATTa Deploy Manager daemon (runs as the atta-deployd systemd service).

Watches the deploy queue. A bundle gets there two ways:
  1. someone uploads an ATTa bundle on the web Add page -> pipeline.py queues it (build record links to the job)
  2. `deployctl deploy <zip>` or a zip dropped into <ADM>/incoming/
Each job: stage -> check -> back up live code -> `bash run` from the release -> health -> DEPLOYED,
and on any failure after activation: previous release (or backup) is re-run -> ROLLED_BACK.
After a successful deploy the daemon restarts itself if its own code changed, so it always runs the live version.

Read the outcome:  deployctl status     (last job + verdict)
                   deployctl journal <job>
                   last line of <ADM>/logs/<job>.log, plus any FAIL lines
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adm import config, queue, manager

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Everything tunable lives in adm/config.py (poll interval, timeouts, keep counts, paths).
# ==========================================================================================


def main():
    config.ensure_dirs()
    started_hash = manager.self_hash()
    print(f"deployd up: queue={config.QUEUE} incoming={config.INCOMING} live={config.APP}", flush=True)
    while True:
        try:
            queue.sweep_incoming()
            jobs = queue.pending()
            if jobs:
                meta = jobs[0]
                print(f"job {meta['job_id']}: {meta.get('original_name')} (build {meta.get('build_id')})", flush=True)
                try:
                    verdict = manager.process(meta)
                except RuntimeError as e:  # lock held by deployctl: leave the job for the next pass
                    print(f"job {meta['job_id']}: waiting — {e}", flush=True)
                    time.sleep(config.POLL_SECONDS); continue
                print(f"job {meta['job_id']}: {verdict}", flush=True)
                if verdict == "DEPLOYED" and manager.self_hash() != started_hash:
                    print("deployd code changed by this deploy; restarting on the live version", flush=True)
                    os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            print(f"deployd error: {e}", flush=True)
        time.sleep(config.POLL_SECONDS)


if __name__ == "__main__":
    main()
