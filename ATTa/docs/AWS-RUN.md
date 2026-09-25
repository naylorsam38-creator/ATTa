# Running v109 on AWS (airexploit.com) — and what to check

## Server
- Instance: **m7i-flex.2xlarge** (8 vCPU, 32 GB) or **m7i-flex.4xlarge** (16 vCPU, 64 GB). Intel/AMD, not Graviton.
  v114.2: every app is checked at the same time. Leave `APP_BUILDER_RUN_PARALLEL` unset (0 = all at once).
- Disk: your 500 GB gp3. Keep `APP_BUILDER_PRUNE_IMAGES=false` (images stay, re-checks are faster).
- Security group: ports 80 and 443 open.
- DNS: A records for `airexploit.com` and `www` → the server's Elastic IP.

## v116 security notes
- Security group: 80 and 443 for everyone, **22 from your own address only**. Nothing else (Coolify's 8000 only
  from the ATTa server, on Coolify's own server).
- Until step 3 has HTTPS, ATTa answers on 127.0.0.1 only: reach it with `ssh -L 8080:127.0.0.1:80 <server>`.
- Try every new bundle on a staging instance first: `sudo bash tests/staging/ec2_acceptance.sh` (see
  `docs/SECURITY-V116.md`).

## Steps (each one's purpose)
1. **Unzip v109 and run `sudo bash run`.** Installs Docker + compose (with the Docker Hub mirror), Python,
   Playwright/Chromium, nginx, the three ATTa services, and creates `/srv/app-builder/.env`.
2. **Edit `/srv/app-builder/.env`:**
   - `APP_BUILDER_DOMAIN=airexploit.com,www.airexploit.com`
   - `APP_BUILDER_LETSENCRYPT_EMAIL=<your email>`
   - leave `ANTHROPIC_API_KEY=` blank: with no key, tier 3 is the script probe (maintenance.py PROBE), not the AI
   - optional: `DOCKERHUB_USERNAME=` / `DOCKERHUB_TOKEN=` (higher Docker Hub download limit)
   - `COOLIFY_URL=` / `COOLIFY_TOKEN=` once Coolify is installed on its own server
3. **Run `sudo bash run` again.** Gets the HTTPS certificate. Ends with `TLS ISSUED` and `DEPLOYMENT VERIFIED`.
4. **Log in at https://www.airexploit.com** as admin (password in `/srv/app-builder/TEST_ACCOUNTS.txt`).
5. **Add app → upload this same zip.** The system runs the whole catalogue (all apps, every run).

## What to read afterwards
- The build page: numbered checklist, every app against steps S01–S12, where it stopped, how long it
  took, and **regressions** (apps that passed last run and fail now) plus **newly passing** apps.
- `/srv/app-builder/state/checklists/<build>.md` — the same checklist as a file.
- `/srv/app-builder/state/runner/evidence.jsonl` — every failed start with the real log lines. Send this
  file back: it's what the next round of script rules is made from.

## Things this run must prove (not proven on the test machine)
| What | Where to look |
|---|---|
| Source builds: **botpress, tidb, open-design** — do their builds actually complete on AWS's network? | each app's S05 STARTED and its attempts in evidence.jsonl (`build.failed` = no) |
| Reusable **PHP runtime** (flarum, krayin-crm if its official image doesn't pass first) | S05 note says "reusable PHP runtime" |
| Reusable **Rust + WebAssembly runtime** (graphite) | S05 note says "reusable Rust+WebAssembly web runtime" |
| **Upstream search** — krayin-crm via its official image `webkul/krayin` from its own docs | S05 note says "named in the app's own docs" |
| Apps needing IPv6 (clearflask, coolify) | should get past S05 on AWS |

Nothing counts as fixed until a full run shows it. Every run re-checks the whole catalogue.
