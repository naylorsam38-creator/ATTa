# v115 — security: secret separation, evidence isolation, origin-based deploy authority

Three fixes from the v114.2 hardening review. The client workflow is unchanged: fill the form / supply the
app's own service tokens → build → the app receives its approved tokens → qualify → deploy.

## #1 ATTa's own secrets never reach an app (customer tokens still do)
`compose_guard.py` (new), `trusted_apps.py` (new), `app_runner.py`, `repair_actions.py`. See `docs/SECRETS.md`.
- Every docker/compose process runs with a clean environment + the app's approved values. v114.2 passed the
  whole server environment, so `${APP_BUILDER_SESSION_SECRET}` in any uploaded compose file received the
  session secret (forge an admin cookie → root deploy), and `${ANTHROPIC_API_KEY}` received ATTa's key —
  even replacing a key the customer had put in the app's own `.env`.
- `_fill_blank_secrets` no longer treats the server environment as a source.
- YAML check (include:/extends: followed) + two-pass render: paths first (`--no-env-resolution`, so env files
  are visible), then the real config. env_file, include/extends files, secrets/configs files, build
  contexts/dockerfile/additional_contexts and the project `.env` must stay inside the app's folder (symlinks
  resolved). Older Compose without that flag: the YAML check covers env files; no PyYAML either = refused.
- The rendered compose file (it holds the app's customer tokens) is written 0600. Bind-style named volumes, `build.ssh`
  and secrets read from the runner's environment are refused.
- The rendered config and any `docker run -e` list are scanned for ATTa secret VALUES (raw/base64/URL).
  Customer values pass, including under the same names as ATTa's variables.
- Refusals are final for that start method (`security.refused` → next part; never self-healed).
- `APP_BUILDER_ALLOW_HOST_ACCESS` (host access for every app) is ignored; admins trust one app at a time
  with `trusted_apps.py` (root-only file, reason + approver recorded in the build notes).

## #2 Evidence is data, never ATTa UI
`gateway.py`. `page.html` → text/plain download; screenshot inline; JSON as JSON; anything else a download;
every evidence response sandboxed + nosniff. Only admins and users with a build naming the app may fetch it
(others get 404). Symlinks out of the evidence folder are refused. Site-wide CSP, X-Frame-Options DENY,
nosniff, Referrer-Policy and COOP on every gateway response.

## #3 Deploy authority comes from origin, not names
`accounts.py`, `pipeline.py`, `gateway.py`, `alerts.py`, `deployd/adm/{authz,queue,config,manager,journal}.py`,
`deployd/deployctl`. See "Who may deploy (v115)" in `docs/ADM.md`.

## Upgrading
- ADM jobs queued by an older version (no `origin`) are refused: queue them again.
- Accounts named system / incoming / deployctl / local / root are disabled at gateway start (alert raised).
- If `APP_BUILDER_ALLOW_HOST_ACCESS=true` was set for Portainer/Coolify-type apps, trust those apps by name.
- A customer token placed in `/srv/app-builder/.env` no longer reaches apps: move it to the app's own `.env`.

## What proved it
`tests/test_v115_security.py` (85 tests; the real-compose ones need only the `docker compose` CLI, no daemon).
Each fix's tests fail on v114.2 and pass now. Before/after probes against v114.2's own code:
evidence `<script>` served as text/html to an unrelated tester → 404; four compose attacks leaking the session
secret / ATTa's key → nothing leaks, the env_file attack is refused, the customer's key is delivered.
Real Chromium: every gateway page loads with zero CSP violations. Full suite: 130 tests OK (2 skipped).
