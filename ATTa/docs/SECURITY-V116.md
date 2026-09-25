# ATTa v116 — security hardening

The same ATTa: upload an app or paste a git address, it joins the library, gets its skin, is started and
checked in a real browser, and the qualified apps go to Coolify. Customers' own service tokens still reach
their apps. What changed is underneath: the ways an uploaded app, a tester account or an AI repair could take
over the server are closed.

## What was wrong, and what v116 does instead

| # | Weakness in v114.2 | v116 |
|---|---|---|
| 1 | Compose ran with the server's whole environment: an uploaded app received the session secret (forge an admin login) and ATTa's API key, which even **replaced** a customer's own key | Docker gets a clean environment; the app's own `.env` still supplies its tokens; files outside the app can't be read; the final config is scanned for ATTa's secret **values** (`docs/SECRETS.md`) |
| 2 | An app's evidence page ran its scripts inside an admin's session | Evidence is data: text download, sandboxed, owner-only; site-wide CSP |
| 3 | Accounts named `system`/`deployctl`/`incoming` were trusted to deploy as root | Authority comes from where a job came from, never a name (`docs/ADM.md`) |
| 4 | Uploads or the LLM could supply the skin launcher; it ran as root with every secret | Overlay code is hash-checked; the LLM can't write code and has its own off switch; proxies run as `atta-proxy` with an empty environment |
| 5 | Every service ran as root | `atta-web` (gateway), `atta-run` (pipeline/watcher), `atta-proxy`; systemd sandboxing (gateway 9.4 → 1.7) |
| 6 | Stray files and dead AWS lines shipped in the bundle | Removed; bundles with anything extra are refused |
| 7 | Plain HTTP login on the internet by default | nginx answers on 127.0.0.1 until HTTPS works |
| 8 | Git addresses and README links could make the server fetch internal addresses | Allowed git hosts only; public addresses only; redirects re-checked |
| 9 | 10 test accounts always created; short legacy password reused | Test accounts only on request; legacy password must be ≥16 characters |
| 10 | Containers could reach the VPC, internal services and the server itself | Internet only (or nothing); never metadata, private ranges or the host |
| 11 | Login throttling per address only, spoofable | Per address and per account, persistent, address only from nginx |
| 12 | "Log out" left the session valid for 24h | Logout ends the token; "everywhere"; 12h sessions |
| 13 | No per-user upload limits | Per-user active builds and daily bytes, disk floor, nginx rate limits |
| 14 | Front Door model calls: per-user caps only, reset on restart | Server-wide daily cap, persistent counts |
| 15 | Unpinned installs as root | Exact versions (`requirements-server.txt`), Coolify zip checksum |
| 16 | Dangerous switches honoured on servers | Refused or ignored on a server |
| + | (found during the work) README-link fetches, Coolify token over redirects/plain HTTP, macvlan networks around the firewall, zip-size lies | All closed; see `docs/history/CHANGES-2026-09-25-V116-HARDENING.md` |

Deploying is also safer: **a bundle is only activated after its own test suite passes**, run by ADM as
`nobody` with no secrets, before anything live is touched (backup and rollback as before).

## Upgrading a running server (v114.x / v115 → v116)

Do it on a **staging** server first (below). On the real one:

1. Before: `sudo deployctl status` (note the live version) and make sure `/srv/app-builder/adm/backups` has room.
2. `sudo deployctl deploy ATTa-deploy116.zip` (or upload it as admin). ADM stages it, runs its tests, backs
   up, installs, health-checks, and rolls back by itself on failure. The first run also creates the service
   users and moves file ownership over to them.
3. Read the result: `sudo deployctl status`; the job's journal shows `TESTED` and `DEPLOYED`.

What you may notice afterwards, and what to do:

| You see | Why | Do |
|---|---|---|
| The site answers only on 127.0.0.1 | No HTTPS yet (v116 won't serve logins in clear text) | Set `APP_BUILDER_DOMAIN` + `APP_BUILDER_LETSENCRYPT_EMAIL` and re-run `bash run`; meanwhile `ssh -L 8080:127.0.0.1:80 server` and open http://127.0.0.1:8080/ |
| A Portainer/Coolify-type app lost the Docker socket | Host access is now per app | `sudo python3 /opt/app-builder/trusted_apps.py trust <app> --reason "..."` |
| A customer token placed in `/srv/app-builder/.env` no longer reaches its app | That file is ATTa's; its secrets are protected | Put it in the app's own `.env` |
| The AI repair tier stopped | It needs its own switch now | `APP_BUILDER_HEAL_LLM=true` (it can only edit CSS/JSON) |
| Coolify hand-off says `REFUSED: ... plain http://` | The token would cross the internet readable | Use `https://`, Coolify's private address, or `COOLIFY_ALLOW_HTTP=true` |
| A git address is refused | Host not allowed | `APP_BUILDER_GIT_HOSTS=github.com,gitlab.com,<your host>` |
| An ADM job from before the upgrade was refused | Jobs now record where they came from | Queue it again |
| New testers aren't created | Test accounts are opt-in | `APP_BUILDER_TEST_ACCOUNTS=10` in `.env`, re-run `bash run` |

Rolling back: `sudo deployctl rollback` restores the previous release (the v115-era root services still work
with the v116 file ownership, since root can read everything).

## New settings (`/srv/app-builder/.env`)

| Setting | Default | Meaning |
|---|---|---|
| `APP_BUILDER_ALLOW_PUBLIC_HTTP` | `false` | `true` = serve plain HTTP publicly without a certificate |
| `APP_BUILDER_TEST_ACCOUNTS` | `0` | how many tester accounts `accounts.py init` creates |
| `APP_BUILDER_APP_EGRESS` | `public` | what app containers may reach: `public` internet, or `deny` |
| `APP_BUILDER_HEAL_LLM` | `false` | allow the LLM repair tier (also needs `ANTHROPIC_API_KEY`) |
| `APP_BUILDER_GIT_HOSTS` | github.com, gitlab.com, codeberg.org, bitbucket.org | git hosts apps may be added from |
| `APP_BUILDER_MAX_ACTIVE_BUILDS` / `_MAX_DAILY_UPLOAD_GB` / `_DISK_RESERVE_GB` | 3 / 20 / 10 | per-user upload limits, disk floor |
| `APP_BUILDER_LOGIN_MAX_ACCOUNT_FAILURES` | 30 | failed logins per account per 15 minutes |
| `APP_BUILDER_SESSION_TTL` | 43200 | session lifetime in seconds (12h) |
| `APP_BUILDER_FRONTDOOR_TOTAL_PER_DAY` | 1500 | Front Door model calls per day, whole server |
| `COOLIFY_ALLOW_HTTP` | unset | `true` = send the Coolify token over plain HTTP to a public address |
| `ATTA_ADM_RUN_TESTS` | `1` | `0` skips the deploy test gate (emergency only; journaled) |

Removed or changed: `APP_BUILDER_ALLOW_HOST_ACCESS` (ignored; use `trusted_apps.py`),
`APP_BUILDER_AUTH_DISABLED` (refused on a server), `APP_BUILDER_HARDEN=false` (ignored on a server).

## Staging before production

1. A throwaway EC2 instance like production. Security group: 22 from your address only; 80/443 if testing
   HTTPS.
2. Unzip the bundle, `sudo bash run`.
3. `sudo bash tests/staging/ec2_acceptance.sh --deploy-drill ATTa-deploy116.zip`
   It checks, on the real server: service users and sandbox scores, file permissions, what nginx exposes,
   real containers failing to reach the metadata service / this host's SSH / docker0 while reaching the
   internet, a hostile compose app through the real pipeline (no container ends up holding the session
   secret or the Docker socket), skin proxies as `atta-proxy`, security headers, logout, and a deploy →
   rollback → redeploy through ADM with the test gate. It must end `0 failed`.
4. Only then deploy to production, with `deployctl` (it keeps the backup for rollback).

On AWS, also require IMDSv2 on the instance (bootstrap does it when the instance role may; the acceptance
script tells you if not):
`aws ec2 modify-instance-metadata-options --instance-id <id> --http-tokens required --http-put-response-hop-limit 1`.

## How it was checked (before this bundle was handed over)

- `python3 -m unittest discover -s tests`: 215 tests, on Python 3.11 and on Python 3.9 (Amazon Linux 2023's).
  As root they use real users, the real node skin proxy, real network namespaces with real packets, real
  nginx 1.24 and the real `docker compose` CLI.
- Each security test was shown to fail on v114.2 before it passed on v116; before/after probes against
  v114.2's own code are recorded in the commit messages.
- `tests/staging/multiuser_smoke.py` (as root, throwaway machine): the real gateway as `atta-web` and the real
  pipeline as `atta-run`, driven over HTTP; 21 checks.
- The bundle through its own deploy gate as `nobody`: 215 tests, OK.
- Not checkable without a real server (that is what `ec2_acceptance.sh` is for): systemd actually applying the
  sandbox, Let's Encrypt issuance, the Docker daemon enforcing the egress rules end to end, a full deploy drill.

## Changing a pinned version

Edit `04-deployment/requirements-server.txt`, then prove it installs everywhere before shipping:
`pip download --only-binary=:all: --python-version 3.9 --platform manylinux2014_x86_64 -r requirements-server.txt`
(repeat for 3.12/3.13 and `aarch64`), and run the test suite on Python 3.9.
