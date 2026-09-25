# ATTa v114.2 — Build Test Report

**Tested:** `ATTa-deploy114_2_updated.zip`, 2026-09-25
**Method:** ran the real build repeatedly, broke it on purpose, and checked whether the scripts caught each break. **No bundle code was changed.** Any patch mentioned below was made only in a throwaway copy so I could reach the next failure.

---

## Where things stand

| Area | Status |
|---|---|
| Shipped tests (`tests/`) | 44 passed, 1 skipped |
| Local build (`bash run` on a laptop-style box) | Works clean. **7 breaks tested, 6 missed or mishandled** |
| Full server build (Ubuntu 24.04 + systemd, fresh box) | **Fails on every fresh install** (bug #1). After test-only patches it reports **VERIFIED while the site is unreachable** (bug #2) |
| Upgrades with `deployctl deploy` | Good upgrades work, including your original zip. **Rollback goes to the wrong version. Hung or killed deploys run twice or keep running** |
| App build pipeline (upload an app) | **Unfinished.** 3 test apps were uploaded and queued; stopped before reading verdicts |
| Amazon Linux 2023 (main target) | **Not tested.** My sandbox's network blocked the image download |

---

## Failures proven by running the build (most serious first)

### 1. CRITICAL: a fresh server install always breaks itself
- **What happened:** `bootstrap.sh` writes `.env` from an unquoted heredoc. One comment in it says ``Re-run `bash run` after setting``. Bash treats the backticks as a command, so writing `.env` **runs the whole deploy again, nested inside itself, as root**, and pastes that output into `.env`. The outer run then rejects its own file: `envfile: line 14: not NAME=value → DEPLOYMENT FAILED`. Re-running doesn't help because the bad `.env` stays. The nested run also starts with an empty `.env`, so it has no session secret.
- **Proof:** reproduced on 2 clean machines (`srv1`, `srv2`). The `.env` contained the text `FULL SERVER DEPLOY (bootstrap.sh)`.
- **Why the tests missed it:** the tests use a fake `.env` and never run this heredoc.
- **Fix:** remove the backticks from that comment (`04-deployment/bootstrap.sh`, `.env` heredoc). Better: write fixed text with a quoted heredoc (`<<'EOF'`) and add the variable lines with `printf`. Add a test that fails if any unquoted heredoc in bootstrap contains a backtick or `$(`. Add `shellcheck` to the build.

### 2. CRITICAL: says "DEPLOYMENT VERIFIED" while the site is unreachable
- **What happened:** on Ubuntu, nginx's stock default site already owns port 80. ATTa's site uses `server_name _`, and nginx warned `conflicting server name "_" ... ignored`. The build still printed **DEPLOYMENT VERIFIED**. Opening the server showed **"Welcome to nginx!"**, and `/login` returned **404**.
- **Why it passed:** the nginx check (`curl -fsSI http://127.0.0.1/`) and ADM's `FRONT_URL` check only ask whether *anything* answered.
- **Fix:** remove `/etc/nginx/sites-enabled/default` on apt systems. On Amazon Linux 2023, disable the default `server {}` in `nginx.conf`, or make ATTa's block `listen 80 default_server`. Change **both** gates (bootstrap and `adm/health.py`) to request `http://127.0.0.1/health` **through nginx** and require the body `OK`. Treat any nginx warning from `nginx -t` as a failure.

### 3. HIGH: rollback restores the wrong version, a silent downgrade
- **What happened:** deployed A, then B (live = B). Deployed C, which crashes on start. ADM detected the failure and rolled back to **A**, not B. It happened again with the hang test (H). The result line also names the broken version: `RESULT: ROLLED_BACK v114.2-C-broken`.
- **Cause:** `manager._rollback()` uses `previous()`. But when `bash run` fails, `stamp()` has not run yet, so `current()` still holds the last good version.
- **Fix:** record the last good release *before* activating and roll back to exactly that. Make the RESULT line print the version that is live now. Add a test that runs A → B → broken C and expects B.

### 4. HIGH: Ubuntu installs can never pass the browser check
- **What happened:** `Host system is missing dependencies to run browsers`. The installer only adds Chromium's system libraries on the dnf (Amazon Linux) path. The failure appears as a raw Python traceback with no `DEPLOYMENT FAILED` line.
- **Fix:** on apt systems use `python3 -m playwright install --with-deps chromium`. Wrap the browser check so it prints `DEPLOYMENT FAILED: browser cannot launch` plus the reason.

### 5. HIGH: a failed build leaves unverified code live, and repeat runs corrupt the rollback target
- **What happened:** after a failure early in the install, `/opt/app-builder` already pointed at the new, unverified code. Running it again twice (still failing) left `.previous`, the rollback target, pointing at a build that **never passed**. A code folder is added on every attempt and nothing is cleaned up.
- **Cause:** code is switched live at the start. Only the final health gate triggers `gate_failed`; a `set -e` exit anywhere earlier skips it.
- **Fix:** switch code live **after** packages, Docker and the browser check pass. Add `trap gate_failed ERR`. Only record a release as a rollback target once it has printed DEPLOYMENT VERIFIED (a `.verified` marker). Clean up failed release folders.

### 6. HIGH: a deploy that times out keeps running in the background
- **What happened:** with a 20s timeout, ADM gave up and rolled back, but the hung install step **was still running**. A real hung `dnf` or `pip` would keep changing the server after the rollback, or block the next deploy.
- **Fix:** in `adm/activation.py`, start `bash run` with `start_new_session=True`. On timeout, kill the whole process group (`os.killpg`) and wait for it to exit before rolling back.

### 7. HIGH: the same deploy can run twice at the same time
- **What happened:** while `deployctl deploy` was running, `deployd` saw the same job in the queue and kept retrying it. After `deployctl` was killed, deployd **re-ran the job while the killed one's installer was still running**: two `bootstrap.sh` processes on one job, both writing to the same log.
- **Cause:** jobs aren't claimed, and the lock belongs to the Python process, not the installer it starts.
- **Fix:** claim a job atomically (rename `<job>.json` to `<job>.running`) before starting it. Take a lock inside `bootstrap.sh` itself (`exec 9>/run/atta-deploy.lock; flock -n 9 || exit`) so no two installs can overlap, whoever starts them. When starting up, deployd should mark leftover `.running` jobs as FAILED rather than re-run them.

### 8. HIGH: the local `run` still executes `.env` as shell code
- **What happened:** added `APP_BUILDER_WATCHER_INTERVAL=$(touch /tmp/PWNED_A)` to the local `.env`, ran `bash run`, and `/tmp/PWNED_A` was created. v114.1 fixed this for the server path but not the local one (`set -a; . "$root/.env"` in `run`).
- **Fix:** have the local path load `.env` through `envfile.py` the way the server path does.

### 9. MEDIUM: local build says RUNNING while the pipeline is dead
- **What happened:** with a syntax error in `pipeline.py`, `bash run` printed **RUNNING** and `bash run status` printed **UP**, although the pipeline had crashed on start. Only the gateway is checked.
- **Fix:** after start, check that both processes are alive, and make `status` report each one. Add a pipeline heartbeat file that `status` reads.

### 10. MEDIUM: any other program on port 8787 counts as ATTa
- **What happened:** a plain web server answering `/health` with `OK` on 8787 made `bash run` report **"Already running"** and status **UP**.
- **Fix:** make `/health` return something ATTa-specific (for example an instance ID stored in `.env`) and check it, together with the pid files.

### 11. MEDIUM: port already in use: slow detection and an orphan left behind
- **What happened:** caught correctly (exit 1), but only after the full 30s wait, and the **pipeline was left running** after the build said it failed.
- **Fix:** check the port is free before starting anything. On failure, stop whatever was started.

### 12. MEDIUM: `bash run stop` can kill an unrelated process
- **What happened:** a stale `pipeline.pid` pointing at an unrelated process got that **process killed**.
- **Fix:** before killing, check `/proc/<pid>/cmdline` contains `04-deployment/pipeline.py` or `gateway.py`.

### 13. MEDIUM: running twice gives duplicate pipelines, and stop can't stop them
- **What happened:** two `bash run` at once, or re-running after the gateway died, gave **2 pipelines**. `bash run stop` then answered `NOT STOPPED` and left processes running.
- **Fix:** put a `flock` around local start and stop. Add a single-instance lock inside `pipeline.py` itself.

### 14. LOW: problems in the `run` file
- Two lines (`detect_aws`, `first_boot`) sit **above the shebang**, and calls to them are appended at the end. Each run calls the AWS metadata service and creates `.initialized` inside the bundle folder. `./run` gets no shebang.
- **Fix:** remove those lines; the shebang must be line 1.

### 15. LOW: build hygiene
- **Nothing in the zip is executable** (every file has `-rw` permissions). It works only because everything is started with `bash …`.
- **Playwright and anthropic aren't pinned**, so each install gets whatever is newest that day (the run pulled a Chromium 153 build).
- **All services run as root**, including the web-facing gateway.
- **ADM keeps ~25 MB per release**, because the 19 MB Coolify zip rides along; after 7 test deploys ADM used 200 MB.

---

## What worked (the scripts caught these correctly)
- Clean local build: up in seconds, logins created, `status` correct.
- Docker unable to run a container: `DEPLOYMENT FAILED: docker cannot run a container`.
- Bundle with a Python syntax error: refused by ADM before anything live was touched.
- Good upgrades A and B, and **your original zip**, deployed with `deployctl`: `DEPLOYED`.
- Bundle that crashes on start: detected (`app-builder-gateway.service is not active`) and rolled back, although to the wrong version (#3).
- After a successful deploy, all 6 services stayed up with 0 restarts. The block on cloud-metadata access is in place.

---

## Found by reading, not yet proven in a run
These need a run to confirm or rule out:
- **Extra files at the top of the zip** (`bootstrap.sh`, `aws-cloud-init.yaml`, `deployd/atta.service`, `scripts/install-docker.sh`, `04-deployment/*.py` stubs) aren't part of ATTa. `atta.service` runs `run start` with `Restart=always`, which would redeploy in a loop. `aws-cloud-init.yaml` runs bootstrap from `/opt/atta`, but nothing copies files there. Recommend removing them.
- **Each redeploy may drop HTTPS:** bootstrap rewrites `/etc/nginx/conf.d/app-builder.conf` from the plain HTTP template every run, overwriting what certbot added.
- **Certificate renewal** isn't set up on Amazon Linux 2023 (no `certbot-renew.timer` is enabled). Also check that `python3-certbot-nginx` exists in the AL2023 repos.
- **Ubuntu 22.04** ships Node 12, so the `node >= 18` check would always fail there.
- **Health URL hardcoded to port 8787** in bootstrap and ADM, while the gateway reads `APP_BUILDER_PORT` from `.env`.
- **Risky copy in `atta_stage_code`:** it runs inside `$(…)`, where `set -e` is off. If `mktemp` failed, `$new` would be empty and files would be copied into `/`.
- **Amazon Linux 2023's stock `nginx.conf`** probably has the same default-server problem as #2.

---

## Not tested yet (next steps)
1. **App pipeline verdicts.** `goodapp` (should pass), `crashapp` (should fail) and `junk` (should be refused) were uploaded and queued; I stopped before reading the results.
2. **Amazon Linux 2023** full build, on a real EC2 instance or with an image registry my sandbox can reach.
3. TLS/certbot with a real domain, the Coolify installer, and uploading the ATTa bundle as admin through the web Add page.

---

## Test-environment changes, all undone
| Change (for testing only) | Restored |
|---|---|
| Started a Docker daemon on the test host | Stopped |
| Pulled Ubuntu images; built test image `atta-u24`; containers `srv1`–`srv3` and their volumes | All removed |
| Test server: HTTPS apt mirrors + proxy, pre-installed Playwright 1.56.0, mounted local Chromium, removed IPv6 listen from nginx's default site (my container has no IPv6) | Deleted with the container |
| Test-only patch: removed the backticks (#1) in a **scratch copy** so later stages could run | Scratch copy only; your bundle is untouched |
| Installed `pytest` on the host | Uninstalled |
| Local test instance, `/tmp/PWNED_A`, `/tmp/imp`, `/tmp/r1.log`, `/tmp/r2.log`, `/tmp/pipe.bak` | Stopped and deleted |
