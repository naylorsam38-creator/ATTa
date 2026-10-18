# v115 — three security fixes, customer tokens via Coolify (2026-09-25)

Built on v114.2. The way a client uses ATTa doesn't change: fill the form, give your own service
tokens, submit. The build, the six-stage gate, ADM's deploy steps and the heal chain stay as they were.
The fixes sit underneath.

## The holes (all present in v114.2, shown before fixing)
1. **Uploaded apps got ATTa's own secrets.** Every `docker` / `docker compose` command inherited the
   service environment (`.env`), and Compose reads that for `${NAME}` and bare `environment: [NAME]`.
   Run for real on v114.2 with Docker: an uploaded compose file printed the session secret, the model API key
   and the Coolify token inside its container. With the session secret, anyone can sign an admin cookie.
2. **Stage-6 evidence ran inside ATTa.** `/evidence/<app>/page.html` (the page an uploaded app
   produced) was served as `text/html` on ATTa's own origin, to any logged-in account, with no CSP. Its
   script runs with the viewer's session, and there was no cross-site check on POST: an admin viewing it
   could be made to upload a bundle, which ADM then deploys as root.
3. **Trust by name.** `system` (and in ADM `deployctl`, `incoming`) were trusted by name. An inbox item
   with no build record silently became a `system` build, `owner` defaulted to `system`, and those names
   could be created as ordinary accounts.

## What was built
**#1 ATTa's secrets never reach an app; customer tokens go to Coolify only**
- `app_env.py` (new). Docker/Compose commands get only the names the Docker CLI needs (`BASE_ENV`), plus
  what the runner chose for this app (`sh()` does this for every `docker` call).
- Before rendering: `env_file`, `include`, `extends`, `label_file`, the compose file itself and the `.env`
  Compose interpolates from must resolve (links followed) inside the app's own folders.
- After rendering: no string in the finished config may contain one of ATTa's protected values, and
  secrets/configs files, build contexts, Dockerfiles and bind-type named volumes must stay inside.
  A failure is `__UNSAFE_CONFIG__ refused this compose file: ...`, naming the variable, never the value.
- Bind mounts may come from the app's own library folder and its own work folder only (no longer the shared
  work folder of every app). ATTa's data and code folders (and `/`) are never mounted, even with
  `APP_BUILDER_ALLOW_HOST_ACCESS=true`, which still allows the Docker socket for Docker managers.
- An app's example env file is copied only if it really is the app's (a symlink to ATTa's `.env` is not).
- `customer_secrets.py` (new): the customer's tokens → `PATCH /api/v1/{applications|services}/<uuid>/envs/bulk`
  (`is_literal`, `is_shown_once`, runtime only, never build-time) → redeploy → a receipt:
  name, keyed 16-hex fingerprint, time, build, who, which Coolify resource. The value is never written to
  disk, logged, or put in a build record, and ATTa never reads it back from Coolify. A token can later be checked
  against the fingerprint. Refused: names that configure ATTa or Coolify (`APP_BUILDER_`, `ATTA_`,
  `COOLIFY_`, `SERVICE_FQDN_`…), and any value that is one of ATTa's own credentials. Coolify not
  configured or app unmapped = nothing sent, nothing kept.
- Web: Library → **tokens** (`/apps/<app>/secrets`), `POST /api/apps/<app>/secrets`,
  `POST /api/apps/<app>/secrets/check`. Only the account that added the app, or an admin.

**#2 Evidence is data, not ATTa UI**
- `page.html` downloads as `text/plain` (attachment). A screenshot is shown only if it is really a PNG.
  JSON is served as JSON. Anything else downloads as `application/octet-stream`. Links inside the evidence
  folder are refused.
- Only the app's owner (`app_owners.py`, new: recorded when an upload or git address adds the app) or an
  admin can see it; anyone else gets the same 404 as "missing". Apps from before v115 are admin-only until
  an admin runs `python3 app_owners.py set APP ACCOUNT`.
- Every response: `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, a CSP. ATTa's own pages
  allow no script at all and can't be framed. The Front Door keeps its inline script but can't be framed
  by another site.
- **Cross-site POSTs refused** (`same_origin_post`): `Sec-Fetch-Site` must be same-origin, and
  `Origin`/`Referer` must be this host and port. Closes the "script elsewhere acts as the admin" half of
  the chain, including apps deployed on a sibling subdomain.
- **Build results filtered by ownership.** A non-admin user's build page shows only the test results (checklist
  rows and discovered apps) for applications they own. The full build record JSON is also filtered to omit
  apps, checklists, and qualification data. Admins see everything (needed for system oversight). This closes
  information leakage where a user could learn about all library apps from viewing their own builds.

**#3 Trust from origin, not names**
- `reserved.py` (new, one list for the pipeline and ADM): `system`, `deployctl`, `incoming`, `local`,
  `root`, … can't be created, and existing ones can't log in (`accounts.get()` hides them).
  `accounts.py init` disables them, and `APP_BUILDER_USER` can't be one.
- `builds.create(owner, origin=...)` has no default. The gateway writes `origin="web"`.
- Pipeline: a bundle placed on the server goes in **`local-inbox/`** (root-only 0700). It becomes an
  `origin="local"` build only after checking the file and folder are root's, not links, and not group/other-writable.
  Anything in `inbox/` must have the gateway's record with `origin="web"`; no record, no origin, or a
  reserved owner = refused. `rec.get('owner','system')` is gone.
- ADM: every job carries `origin`. `local` = deployctl / `incoming/` / pipeline local inbox, and the queue
  folder, job file and `incoming/` must be root's and not loosened (a loose zip goes to `incoming/rejected/`).
  `web` = an enabled admin right now, never a reserved name. No/unknown origin = refused, before backup.
- `APP_BUILDER_AUTH_DISABLED` (laptop testing) now acts as `local-admin`. It can add apps but not update the
  system, because it isn't a real account.

## What proved it
`tests/test_v115_security.py`, 54 tests; 99 in the suite, all passing (1 network test skipped).
- **Every fix was broken on purpose** (16 mutations: env passthrough, each compose check, each gateway
  check, ADM/pipeline name trust, unkeyed fingerprint, …). Each one turned a test red.
- Compose tests run the real `docker compose config`. The gateway tests run a real `gateway.py` process.
  The Coolify tests use a fake Coolify that records every request (and would answer a GET for a value:
  none is ever made).
- **Real containers** (Docker 29 here): the same hostile compose app printed all three ATTa secrets on
  v114.2 and none on v115. An ordinary nginx app with an example `.env` still started, answered HTTP with
  its page, and got its generated password.

## Limits, said plainly
- **Local re-checks run with placeholders, not the customer's real token.** Point 7 of the design
  ("re-qualification uses the deployed app's Coolify secret") can't happen locally without ATTa reading the
  secret back, which it must not. So the local check gets `atta-local-check-placeholder-not-a-real-secret`.
  An app that refuses to boot without a *valid* key will fail the local check. Its Coolify deployment is
  where it runs for real. The next step, if wanted, is a stage that checks the deployed Coolify URL.
- **No token field on the Front Door yet.** Tokens are entered on the app's **tokens** page (or the API).
- Not run against a live Coolify or the AWS box. Pipeline and gateway still run as root (as in v114).
- Builds queued before this upgrade have no origin and are refused: upload them again.
