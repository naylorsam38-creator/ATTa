# v109: the app runner (2026-09-24)

v108 never started any app, so every build ended NOT_QUALIFIED / NO_TARGETS and went straight to "human".

New: `04-deployment/app_runner.py`. The pipeline's QUALIFIED gate now starts each app in Docker, puts its
skin proxy in front, runs the six-stage watcher (stage 6 = real Chromium), then stops it. Apps run in
parallel (APP_BUILDER_RUN_PARALLEL), with a memory guard so one heavy app can't take the server down.

How it finds a way to run each app, in order: a saved recipe -> the app's own compose file -> the app's
own published image (named in its own files, confirmed in the registry) -> compose/Dockerfile build.
Whatever works is saved as a recipe and used first next time.

Fixes the scripts now make on their own (each one found on a real app in the library):
- Docker not running -> starts it
- missing .env -> made from the app's own example (incl. env_init); empty .env folder left by Docker -> removed
- blank passwords/secrets in the example .env -> generated for the run (BillionMail)
- compose "required variable X is missing" / app says "X must be set" -> X generated, same part retried (ClearFlask)
- docs placeholder instead of an image tag (`apache/airflow:|version|`) -> :latest (Airflow)
- image prints its help and exits -> reads the help, starts it with `standalone`/`server`/`serve`
- server refuses compose `ulimits` -> started without them (BillionMail)
- app started before its own database was ready -> containers restarted, same part (Appsmith)
- slow boot -> waits for the app's own healthcheck; settles; re-checks 5xx warm-ups
- container that died / app that stalled -> detected early instead of waiting out the limit
- Docker Hub rate limit -> registry mirror (bootstrap sets mirror.gcr.io), docker login, then wait
- server kernel has no IPv6 -> reported as a server problem (RUNNER_HOST_NO_IPV6), not an app fault
- runtime data and config files are mounted from copies: the library copy of an app is never written to
- stage 6: a logged-out 401/403 is noise; an error the bare app throws too (no proxy) is the app's own
  and recorded, not failed; errors only the skin/proxy causes still fail

Every failed start is kept in state/runner/evidence.jsonl (what was tried, rule matched, real log lines).
"unrecognised" rows there are where the next rule comes from. The self-healer's LLM tier can now read
runner attempts, save a recipe (set_run_recipe) and teach the runner a new rule for all apps (add_runner_rule).

bootstrap.sh now installs Docker + compose, sets the Docker Hub mirror, and adds the runner settings to .env.
Library page shows each app's last real check and how it was started.

Real results so far (this sandbox, real Chromium): memos, apache-airflow, colanode, billionmail PASS;
agno PASS (package check). Full-library run continues; results in state/runner/results/.
Limits of the test box, not of the apps: no IPv6 in its kernel, and a TLS-inspecting proxy that source
builds inside containers don't trust, so compose/Dockerfile source builds could not be proven here.

## Added later the same day (all class rules, none tied to an app's name)
- Numbered apps (A-001…) and a numbered test checklist: every app through the same steps S01–S12;
  adapter hooked = pass, dormant/active recorded. Missing result = NOT CHECKED = run fails.
- Per-app clock: start/finish, queued, starting, checking, per-attempt download/boot.
- Every run re-checks the WHOLE catalogue (healing runs too); regressions vs the last full run and newly
  passing apps are named on the checklist.
- Recipes saved only after all six stages pass. AI proposals are candidates under the same rule.
- Tiers: own files -> upstream (docs, owner's deploy repos, owner's Docker Hub) -> source build ->
  reusable runtimes (PHP, Rust+WebAssembly web) -> AI draft -> person. Per-app time budget.
- Compose-aware memory, disk check before every download, download slots, second pass for apps that
  failed only because the machine ran short, broken-symlink-safe copies, non-web apps checked as services,
  empty 403/404 server pages not counted as running, trait-based subdomain note (replaces a name check).
See docs/AWS-RUN.md for running it and what the AWS run must prove.
