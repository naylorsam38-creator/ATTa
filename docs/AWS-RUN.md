# Running v109 on AWS (airexploit.com) — and what to check

## Server
- Instance: **m7i-flex.2xlarge** (8 vCPU, 32 GB) → `APP_BUILDER_RUN_PARALLEL=3`
  or **m7i-flex.4xlarge** (16 vCPU, 64 GB) → `APP_BUILDER_RUN_PARALLEL=6`. Intel/AMD, not Graviton.
- Disk: your 500 GB gp3. Keep `APP_BUILDER_PRUNE_IMAGES=false` (images stay, re-checks are faster).
- Security group: ports 80 and 443 open.
- DNS: A records for `airexploit.com` and `www` → the server's Elastic IP.

## Steps (each one's purpose)
1. **Unzip v109 and run `sudo bash run`.** Installs Docker + compose (with the Docker Hub mirror), Python,
   Playwright/Chromium, nginx, the three ATTa services, and creates `/srv/app-builder/.env`.
2. **Edit `/srv/app-builder/.env`:**
   - `APP_BUILDER_DOMAIN=airexploit.com,www.airexploit.com`
   - `APP_BUILDER_LETSENCRYPT_EMAIL=<your email>`
   - `APP_BUILDER_RUN_PARALLEL=3` (or 6, see above)
   - optional: `ANTHROPIC_API_KEY=` (switches on the AI step, last resort only)
   - optional: `DOCKERHUB_USERNAME=` / `DOCKERHUB_TOKEN=` (higher Docker Hub download limit)
   - `COOLIFY_URL=` / `COOLIFY_TOKEN=` once Coolify is installed on its own server
3. **Run `sudo bash run` again.** Gets the HTTPS certificate. Ends with `TLS ISSUED` and `DEPLOYMENT VERIFIED`.
4. **Log in at https://www.airexploit.com** as admin (password in `/srv/app-builder/TEST_ACCOUNTS.txt`).
5. **Add app → upload this same zip.** The system runs the whole catalogue (all apps, every run).

## What to read afterwards
- The build page: numbered checklist, every app against steps S01–S12, where it stopped, how long it
  took, and **regressions** (apps that passed last run and fail now) plus **newly passing** apps.
- `/srv/app-builder/state/checklists/<build>.md` — the same checklist as a file.
- `/srv/app-builder/state/runner/evidence.jsonl`: every failed start with the real log lines (`"kind": "start"`),
  and every started app that then failed a check stage with that stage's full detail (`"kind": "check"`:
  for a missing skin link that's where the browser ended up, the page it was served there, and its
  `<head>`). Send this file back: it's what the next round of script rules is made from.
- `sudo APP_BUILDER_ROOT=/srv/app-builder python3 /opt/app-builder/app_runner.py replay` (or, on a copy of the file,
  `APP_BUILDER_ROOT=/tmp/atta python3 04-deployment/app_runner.py replay evidence.jsonl`):
  re-diagnoses every recorded failed start under the current rules. `changed` = failures a new rule now
  handles differently; `unrecognised` = failures no rule knows yet, grouped by their most telling line.

## Things this run must prove (not proven on the test machine)
| What | Where to look |
|---|---|
| Source builds: **botpress, tidb, open-design** — do their builds actually complete on AWS's network? | each app's S05 STARTED and its attempts in evidence.jsonl (`build.failed` = no) |
| Reusable **PHP runtime** (flarum, krayin-crm if its official image doesn't pass first) | S05 note says "reusable PHP runtime" |
| Reusable **Rust + WebAssembly runtime** (graphite) | S05 note says "reusable Rust+WebAssembly web runtime" |
| **Upstream search** — krayin-crm via its official image `webkul/krayin` from its own docs | S05 note says "named in the app's own docs" |
| Apps needing IPv6 (clearflask, coolify) | should get past S05 on AWS |
| Run-2 rules: billionmail, tidb (non-web ports), krayin-crm, flarum (disk), graphite (download) | no RUNNER_EXCEPTION; attempts show `disk.full` / `net.transient` retrying the **same** part |
| colanode (page drawn late) | S10 passes; its 6 CLEAN detail shows `drawn_after_s` |
| plane (skin link missing in the browser) | if it still fails, the `kind: check` row's `observed` says which: `SERVED_WITHOUT_SKIN`, `REMOVED_BY_PAGE_SCRIPT` or `NAVIGATED_TO:<path>` |

Nothing counts as fixed until a full run shows it. Every run re-checks the whole catalogue.
