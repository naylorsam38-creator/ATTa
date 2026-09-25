# ATTa: Coolify deploy kit

This sets up the [Coolify](https://coolify.io) server that **ATTa** (the APP Builder) hands its qualified builds to.
Coolify is a self-hosted platform, similar to Heroku or Vercel, that runs your apps on your own server.
ATTa's own code is on the `claude/atta-hardening-v116` branch.

The kit does not contain Coolify's source code. Coolify's official installer downloads the prebuilt, signed images, so you don't build Coolify yourself.
This repo wraps that installer so the install is:

- **Pinned** to a tested version (Coolify **4.3.23**).
- **Locked down from the first second.** The admin account is created during install and public sign-up is disabled. Stock Coolify leaves sign-up open until someone registers, so whoever reaches port 8000 first becomes admin.
- **Checked before it runs.** Preflight looks at OS, CPU, RAM, disk, kernel IPv6, ports and network.
- **Ready to deploy.** Coolify validates its own host during install and the reverse proxy is started. The first thing you see is a working platform, not a setup wizard with loose ends.
- **Proven after it runs.** Verification checks container health, the dashboard, the realtime server, the proxy, secrets, the admin account, that sign-up is closed, and that Coolify can actually run commands on the host over SSH.
- **Connected to ATTa.** `connect-atta.sh` turns on the API, creates a least-privilege (`deploy`+`read`) token, and writes ATTa's `.env` lines and app map. ATTa's real hand-off code was run against Coolify installed by this kit and reached `DISPATCHED`.
- **Recoverable.** It includes backup, restore to a new server, and upgrade with an automatic backup first.
- **Tested.** 67 unit tests, plus a 51-assertion end-to-end test against real Coolify. It installs Coolify, deploys a real app through the API and loads it through the proxy, upgrades, wipes the server, reinstalls, restores, and proves the original login, data, API token and app came back.

## Quick start

On a fresh **Ubuntu 24.04** server with at least 2 vCPU, 4 GB RAM and 40 GB disk, logged in as root:

```bash
git clone https://github.com/naylorsam38-creator/ATTa.git /opt/atta
cd /opt/atta
cp config/coolify.env.example config/coolify.env
nano config/coolify.env          # set ROOT_USERNAME and ROOT_USER_EMAIL, leave the password empty
                                 # optional: COOLIFY_SOURCE_ZIP=/path/to/ATTa's coolify-main.zip
./scripts/install.sh --dry-run   # checks everything, changes nothing
./scripts/install.sh             # installs Coolify 4.3.23 and verifies it
cat /root/coolify-admin-credentials.txt
```

Then open `http://<server-ip>:8000` and log in.
**Follow [docs/RUNBOOK.md](docs/RUNBOOK.md) from step 5** to add a domain and HTTPS, close the temporary ports and set up backups.
It's about 15 minutes of clicking and they matter.

## What's in here

| Path | What it does |
|---|---|
| `scripts/install.sh` | Validates config, runs preflight, runs Coolify's official installer for the pinned version, then verifies. Safe to re-run. |
| `scripts/preflight.sh` | Read-only readiness checks. Exits non-zero on any blocker. |
| `scripts/verify.sh` | Read-only health and security checks. Run it any time. |
| `scripts/backup.sh` | Backs up Coolify's database, `.env` (APP_KEY), SSH keys and proxy/TLS config into one verified archive. |
| `scripts/restore.sh` | Restores that archive onto a fresh install, on the same server or a new one. |
| `scripts/connect-atta.sh` | Creates ATTa's Coolify API token (`deploy`+`read` only) and writes `COOLIFY_URL`/`COOLIFY_TOKEN` and `coolify_resources.json` for ATTa. |
| `scripts/upgrade.sh` | `upgrade.sh 4.3.24`: backs up, upgrades, then verifies. |
| `config/coolify.env.example` | The only file you edit. Copy it to `config/coolify.env`, which git ignores. |
| `docs/RUNBOOK.md` | Step by step from buying a server to running ATT. |
| `docs/DEPLOY-ATT.md` | How ATTa and Coolify connect, the live hand-off test results, and the status → fix table. |
| `docs/TROUBLESHOOTING.md` | Every failure we hit or anticipate, with the fix. |
| `docs/FIXES.md` | Every problem found and fixed, and what is still open. |
| `tests/` | Unit tests (bats) and the destructive end-to-end test. |

## Development

```bash
make lint        # shellcheck on every script
make test        # 67 unit tests, no Docker or network needed
make e2e         # DESTRUCTIVE: real install/upgrade/backup/wipe/restore; throwaway VM only
```

CI (`.github/workflows/ci.yml`) runs lint and unit tests on every push.
`.github/workflows/e2e.yml` runs the full end-to-end test on a fresh GitHub runner, against the live Coolify CDN, whenever `scripts/` or `tests/e2e/` change, or on demand.
