# v116 — full security hardening (review items #1–#16, plus findings made along the way)

Operator guide, upgrade notes and settings: `docs/SECURITY-V116.md`. Secret separation: `docs/SECRETS.md`.
Deploy authority, request hand-off and the test gate: `docs/ADM.md`. Every change's reasoning, before/after
probe and test counts are in its commit message on the branch.

## Built on v115 (#1–#3)
Secret separation for compose; evidence served as data; deploy authority from a job's origin, not names.

## v116
- **#4** Overlay code integrity (`overlay_integrity.py`); self-healing never writes code; overlays shipped with
  uploads/clones removed; skin proxies through `proxy_launch.py` + `run-proxy.sh` as `atta-proxy`, empty
  environment, verified copy; `APP_BUILDER_HEAL_LLM` switch (default off).
- **#5** `atta-web` / `atta-run` / `atta-proxy`; systemd sandboxing (gateway exposure 9.4 → 1.7, pipeline and
  watcher 9.4 → 6.8); data-folder layout 3771 with migration; Chromium in `/opt/ms-playwright`; one sudoers
  rule; web bundles handed to deployd via `adm/requests/`; owner-preserving writes.
- **#6** Stray files outside `ATTa/` and the dead AWS lines in `run` removed.
- **#7** nginx on 127.0.0.1 until HTTPS; certbot failure falls back to local; `APP_BUILDER_ALLOW_PUBLIC_HTTP`.
- **#8** `netguard.py`: git hosts allow-list, no internal addresses, https-only git without hooks/submodules.
  Found while fixing it: README-link fetches (upstream.py) could reach internal addresses — closed, redirects
  re-checked.
- **#9** Test accounts opt-in (`APP_BUILDER_TEST_ACCOUNTS`), short legacy password not reused, `accounts.py delete`.
- **#10** `container-egress.sh`: containers reach the internet (or nothing), never metadata, private ranges or
  the host; non-bridge compose networks refused.
- **#11/#12** Per-account + per-address throttling, persistent, X-Real-IP only from nginx; logout revokes the
  token; logout everywhere; 12h sessions.
- **#13/#14** Upload limits (active builds, daily bytes, disk floor), nginx rate limits; Front Door server-wide cap.
- **#15** `requirements-server.txt` (versions proven on Python 3.9/3.12/3.13, x86_64/aarch64); Coolify zip
  checksum; Coolify token only over HTTPS/private addresses and never through a redirect.
- **#16** `APP_BUILDER_AUTH_DISABLED` refused on servers; `APP_BUILDER_HARDEN=false` ignored on servers.
- **Deploy validation**: strict bundle contents; the bundle's own tests must pass (as `nobody`) before
  activation; app-zip limits count real bytes, cap entries, refuse zip bombs.
- PyYAML guaranteed (the compose YAML checks need it on older Docker Compose).

## Proven here / on staging
Here: 215 tests (Python 3.11 and 3.9), real users, real node proxy, real namespaces and packets, real nginx,
real compose CLI; multi-user staging smoke 21/21; the bundle's own deploy gate as `nobody`.
On a staging server: `tests/staging/ec2_acceptance.sh` (systemd sandbox, TLS, Docker egress, deploy drill).
