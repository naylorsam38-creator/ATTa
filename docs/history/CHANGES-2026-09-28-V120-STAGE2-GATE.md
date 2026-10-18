# v120 — v118 base + the v119 runtime-journey gate (2026-09-28)

## Why this version exists
v119 was built on v117, not v118. It added the right thing (stage 6 must prove a real user journey)
but silently dropped every v118 fix: verified-repos-only cloning, memory/disk start gates, live
images protected from `docker image prune -a`, the HTTPS route under Coolify's Traefik, the
"qualified AND live" user catalogue, the fixed heal-model id. v120 is v118 with the gate ported on.

## Ported from v119 (exact hunks, nothing else)
- `04-deployment/system_watcher.py`: `_journey_spec`, `_credential`, `_locator`, `_login_detected`,
  `run_user_journey`, and stage 6 now runs the journey and FAILS on any journey code. A page load is
  never a pass. Journey verdict is written into `stages["6 CLEAN"].journey`.
- `04-deployment/app_runner.py`: a recipe may carry `journey`; it is kept by `save_recipe` and copied
  to the watcher target before the check.
- `tests/test_v119_login_proof.py` (see "Tests" for what it does and does not prove).

## Changed while porting
1. **Login journey must state what logged-in looks like.** v119 passed a login on "the password box
   went away". v120 refuses a login contract with none of `expected_url_contains` /
   `expected_selector` / `expected_text` (`LOGIN_EXPECTATION_MISSING`), before touching the page —
   the v119 change log already claimed this rule; now the code does it.
2. **Coolify hand-off: QUALIFIED only.** v119's log said a PARTIALLY_QUALIFIED build is not handed
   off; its `coolify_handoff.HANDOFF_STATES` still included it. Now `(QUALIFIED,)`, and the pipeline
   calls `hand_off` only on QUALIFIED.
3. **One source of truth for what users see.** v119 wrote a static `state/qualified_catalogue.json`
   snapshot (no live check) that the gateway injected into the page. Dropped. v118's
   `user_catalogue.py` — latest check passed all six stages (which now includes the journey) AND live
   in Coolify AND has a category, checked fresh per request — is the only source.
4. **The system chooses the foundation; the person never does.** v118's Front Door listed the
   qualified apps in a category by name and asked the person to pick. The Locked Goal says the person
   receives an application, not a library entry, and is never asked to choose. `user_catalogue.for_user`
   now returns ONE foundation per category as `{id, url}` — no name, no family, no alternatives —
   chosen deterministically (`choose()`: lowest id among qualified+live; one function to change if a
   different preference is wanted). The Front Door opens it directly; `PICK_LINE` and the pick
   buttons are gone; app names never reach the page. Admin `/library` still shows every candidate
   with names and every exclusion with its reason.
5. Version strings → v120 (`release.json`, `Dockerfile`, `docker-compose.coolify.yml`,
   `deploy-atta.sh`, v117 suite).

## Tests — 106/106 pass, in one process, in a real Chromium (no stall)
- `test_v120_stage2.py` (16): one foundation per category, no names, deterministic choice, unqualified
  lowest-id never wins, empty category stays empty, page has no pick/no names, hand-off states,
  pipeline hand-off gate, snapshot gone, watcher gate wired, fail codes without a page, env creds,
  runner carries journey, release lineage.
- `test_v119_login_proof.py` (7): real Chromium against a login page the test serves. Proves the
  watcher's logic — page load refused, missing account refused, missing expectation refused, wrong
  password fails, wrong expectation fails even when login worked, real login with real expectation
  passes. **It is not proof that any catalogue app passes**; that only exists in
  `state/runner/results/*.json` after a real server run.
- v118 (9), v117 (31), v114.1 (25, 1 skipped), v114 (20): unchanged and passing. Note for the record:
  the v117 Coolify suite talks to a stand-in Coolify HTTP server (`FakeCoolify`, pre-existing).
- Live check, real gateway (`APP_BUILDER_AUTH_DISABLED=1`) + real Chromium on scratch data: two
  qualified apps in Monitoring, one failed in Analytics. `/api/front-door/library` returned one
  `{id,url}` for Monitoring only; the page opened straight into it (0 pick buttons, no names in the
  page text, iframe loaded the live address); Analytics got the "nothing has passed yet" line.
- Front Door script: `node --check` clean. All Python: `py_compile` clean.
- The test files read the Chromium path from `ATTA_CHROMIUM` (default `/usr/bin/chromium`).

## What v120 does NOT do (Stage 2 is not done yet)
- **0 of 34 catalogue apps carry a journey contract**, and nothing in v120 writes one. Nothing
  provisions a test account inside a freshly started app. On the server, every web app will fail
  stage 6 with `JOURNEY_NOT_CONFIGURED` until the next two pieces exist:
  1. test-account provisioning, per app class (first-run signup form / env-seeded admin / app CLI);
  2. journey authoring into the candidate recipe, verdict still owned by the watcher.
- Docker/AWS: no catalogue run here. First thing on the server: `docker build`, then `bash run`.
