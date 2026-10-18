# v121 — journey_author.py: the half that feeds the gate (2026-09-28)

v120 had the judge (stage 6 refuses to qualify a web app without a proven user journey) and nothing
to judge: 0 catalogue apps carried a journey, nothing wrote one, nothing made a test account. v121 adds
the machine that produces journeys, by class of app, for whatever is in the catalogue — no app names,
no app count anywhere in the code.

## New: `04-deployment/journey_author.py`
Called by the runner for every web app that starts and has no journey yet, inside the existing
parallel pool (so the whole catalogue is worked at once, `APP_BUILDER_RUN_PARALLEL` as before).

1. **Provision a test account, by class** (first one that works wins; the class is recorded):
   - `stored` — an account this module made on an earlier run (`state/accounts/<id>.json`, mode 600).
     If the app no longer accepts it (its data was reset), the store entry is dropped and a new one made.
   - `supplied` — a contract a person or the self-healer put on the shelf (`recipes/<id>.journey.json`).
   - `env_admin` — a first/admin USER+PASSWORD pair found in the env the runner already started the app
     with (recipe env, the app's `.env`, the rendered compose). Matched by variable-name shape
     (`*ADMIN*USER`, `*DEFAULTUSER_NAME`, `*SETUP_EMAIL`, `*_PASS(WORD)`), never by app.
   - `exec` — the recipe carries `account: {kind: exec, command: [...]}` to run inside the container
     (the app's own CLI). Shelf slot for Tier 3; the self-healer is told how to fill it.
   - `signup` — a sign-up / register / first-run setup form, filled in a real browser: collapsed
     sections opened, an embedded database (SQLite / built-in) chosen where a wizard offers one (native
     `<select>` or custom dropdown), only consent checkboxes ticked (never feature switches), pre-filled
     values kept, a "name already taken" answer retried once with a unique suffix, and the app's restart
     after a wizard waited out (answers twice, 3 s apart, within `APP_BUILDER_JOURNEY_RESTART_WAIT`).
2. **Author the contract from what was observed.** Login: `username`/`password` are `store:<id>`
   references — no secret ever enters a target, recipe, result or evidence file. The post-login
   expectation is derived from the real post-login page, strongest first: a "log out" line that
   appeared → a known marker present after and absent before → the URL path changed → any stable new
   line of text. No login: a `smoke` contract from a real control — an internal link whose URL changes,
   else a button/tab whose click makes stable new text appear.
3. **Dry-run once with the watcher's own `run_user_journey`, from a fresh session.** A contract that
   fails here is not handed on; the code is recorded (`AUTHORED_BUT_FAILED:<watcher code>`).
4. **The watcher still decides.** The runner copies the contract to the watcher target; the watcher
   executes it itself on the proxied URL. An authoring failure becomes stage-6 code
   `JOURNEY_NOT_AUTHORED:<cause>` so the verdict says why.

Config block at the top (identity used for new accounts, timeouts, the word lists and variable-name
patterns, the marker list) — every setting says what changes if it is edited.

## Fleet report: `state/checklists/journeys.md`
Written after every full run (pipeline and `app_runner.py all`): every app grouped by outcome code,
passed first, then by size of group. One fix per group clears every app under it. Codes seen so far:
`PASSED:login`, `PASSED:smoke`, `ACCOUNT_NO_STRATEGY`, `SMOKE_NO_CONTROL`, `AUTHORED_BUT_FAILED:*`,
`APP_UNREACHABLE`, `BROWSER_UNAVAILABLE`, `NOT_STARTED`, `NOT_REACHED:<stage>`.

## Watcher changes (`system_watcher.py`)
- `store:<id>` credential references (reads `state/accounts/<id>.json`).
- `{"error": CODE}` contracts fail stage 6 with `JOURNEY_NOT_AUTHORED:CODE`.
- **Bug fixed, found by the dry-run on a real app:** the login submit fallback was "the first
  `button` on the page" — on Gotify that is the menu icon, so every SPA login would have failed. Now
  `_login_submit`: a submit inside the form that holds the password field, else a button in that form,
  else a submit anywhere, else a button that says log in / sign in.
- **Bug fixed, same way:** single-page apps render the signed-in view after the network goes quiet;
  the watcher judged too early. After submit it now waits (bounded by `wait_ms`) for the password
  field to go and for the page text to stop changing. Same for the smoke click.
- More username-field fallbacks (`name*=login`, `id*=user`, `id*=email`, any non-password input).

## Runner / pipeline / self-healer
- `app_runner.qualify_app`: authors before the watcher check; `journey_author` block on the result;
  `account` kept in saved recipes; S10 note carries the journey verdict or the authoring code.
- `pipeline.qualify` and `app_runner.py all` write the fleet report.
- `repair_actions.set_journey_contract(app, contract_json)` (validated: type, expectation, credentials)
  and self-healer prompt text for `JOURNEY_NOT_AUTHORED:*` failures. The self-healer can supply env, an
  `account.exec` command, or a contract — it can never mark a pass.

## Proven here — real products, real processes, real browser (no Docker in this workspace)
| App (real) | Class exercised | Outcome |
|---|---|---|
| Gotify 2.6.3 (binary, `GOTIFY_DEFAULTUSER_NAME/PASS`) | `env_admin`, then `stored` on the second run | login contract, `expected_text: LOGOUT`, dry-run PASS; store file mode 600; secret absent from the contract |
| Gitea 1.24.5 (binary) at first run | `signup` via the install wizard: SQLite chosen in a custom dropdown, admin section opened, restart waited out | login contract, `expected_selector: a[href*="/settings" i]`, dry-run PASS |
| Gitea 1.24.5 installed, admin already present, store lost | `signup` via Register, name taken → retried with suffix | login contract, dry-run PASS |
| Glances 4 web (display-only: no links, no buttons) | none possible | `SMOKE_NO_CONTROL`, reported, not faked |

Every one of those outcomes was reached by fixing a real failure the run exposed (variable-name
shape, SPA render timing, wrong submit button, hidden wizard section, custom dropdown, checkbox
mis-fill, app restart, name taken, `:text-matches` not matching in this Playwright — replaced by
`has_text=re.compile`).

Tests: `tests/test_v121_journey_author.py` — 8 logic tests (real code, scratch root) + 3 live tests
gated on `ATTA_LIVE_GOTIFY_URL` / `ATTA_LIVE_GITEA_URL` / `ATTA_LIVE_NOLOGIN_URL` (run here: 11/11).
Whole suite: 117 pass, 4 skipped (the 3 live tests without their URLs + 1 pre-existing), one process,
real Chromium.

## Open — for Sam to rule on
- **Display-only apps** (Glances class: nothing to click) cannot pass a `login` or `smoke` contract, so
  they stay NOT_QUALIFIED under `SMOKE_NO_CONTROL`. Either that is correct (an app with no interaction
  has no journey to prove) or a third contract type is wanted (e.g. `render`: a required app-specific
  selector present with live content). Not built; the group will be visible in `journeys.md` after the
  first server run.
- Correction to what was said by voice: Playwright + Chromium were already baked into the image since
  v117 (`Dockerfile`); nothing was added for v121.

## Not proven here
The catalogue run itself. First server run: `docker build`, `bash run`, then read
`state/checklists/journeys.md` and fix by code group.
