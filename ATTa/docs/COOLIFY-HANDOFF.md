# Coolify hand-off — the boundary

This is the contract between the APP Builder and Coolify. The APP Builder side is built
(`04-deployment/coolify_handoff.py`). The owner sets up the Coolify side.

## When

A build is handed off **only** when its state is `QUALIFIED`, and **every** qualified build is
handed off. There is no manual pick. `hand_off()` refuses any build that is not `QUALIFIED`.

A build becomes `QUALIFIED` only when the pipeline's gate (`pipeline.py → qualify()`) runs the
real six-stage watcher (`system_watcher.check`) against every registered app target, and every
target:

- passes stages 1–5 (INSTALLED, APP_UP, PROXY_UP, SKIN, HOOK), **and**
- passes stage 6 CLEAN, the real Playwright/Chromium browser check, with status `OK`.

A browser stage that didn't run (`PASS (CLEAN not run)`) does **not** qualify. Having no targets
doesn't qualify either. Either case gives `NOT_QUALIFIED`, and nothing goes to Coolify.

```
QUEUED → VALIDATING → FETCHING_LIBRARY → INSTALLING_UI_CAPABILITY
       → READY_FOR_WATCHER → QUALIFYING → QUALIFIED ──► Coolify
                                        ↘ NOT_QUALIFIED   (stops here)
   any step ↘ FAILED                                       (stops here)
```

## What Coolify receives

1. **A hand-off manifest file**: `<ROOT>/state/coolify/outbox/<build_id>.json`

   ```json
   {
     "schema": "APP_BUILDER_COOLIFY_HANDOFF.v1",
     "build_id": "b-20260923-050837-ef11b993",
     "owner": "tester01",
     "qualified_at": 1790140117.2,
     "apps": [
       { "app": "Grafana", "verdict": "PASS",
         "ui_dir": "/srv/app-builder/library/grafana/.ui-capability",
         "target_url": "http://127.0.0.1:3000/", "proxy_url": "http://127.0.0.1:9100/" }
     ]
   }
   ```

2. **A deploy call per app** to the Coolify API, for each app that has a resource UUID:

   ```
   POST {COOLIFY_URL}/api/v1/deploy?uuid=<resource uuid>&force=false
   Authorization: Bearer {COOLIFY_TOKEN}
   ```

   An app counts as accepted only when Coolify's reply lists a `deployment_uuid` for that
   resource. An app that's already accepted is never deployed a second time for the same build.

## What the owner sets up (the Coolify side)

| Where | What |
|---|---|
| Coolify | Install it with `sudo bash 05-coolify/install-coolify.sh`, ideally on its own server. |
| Coolify → Settings → Advanced | Turn **API Access** on. Otherwise every call gets 403 "API is disabled". |
| Coolify → Keys & Tokens | Create an API token with the **deploy** and **read** permissions. |
| `<ROOT>/.env` | `COOLIFY_URL=http://your-coolify-host:8000` and `COOLIFY_TOKEN=<that token>` |
| `<ROOT>/coolify_resources.json` | Maps each app name (the `app` field above) to the Coolify resource UUID that runs it: `{"apps": {"Grafana": "<uuid>"}}` |

For customer tokens (v115, below) the same API token also needs **write**. Do **not** give it
`read:sensitive`: then Coolify never returns a variable's value to ATTa, even by accident.
An entry in `coolify_resources.json` may also be `{"uuid": "<uuid>", "kind": "service"}` for a Coolify service.

Restart the pipeline service after you edit `.env`: `systemctl restart app-builder-pipeline`.
Changes to `coolify_resources.json` are picked up on the next retry and don't need a restart.

## Status on the build

Each qualified build gets a `coolify` block, shown on `/builds/<id>`:

| status | meaning |
|---|---|
| `DISPATCHED` | Coolify accepted every app. Done. |
| `RETRYING` | A deploy call failed (non-2xx or unreachable). Retried automatically. |
| `BLOCKED_NOT_CONFIGURED` | `COOLIFY_URL` or `COOLIFY_TOKEN` isn't set. Retried until it is. |
| `BLOCKED_UNMAPPED` | An app has no UUID in `coolify_resources.json`. Retried until it has one. |

Retries run from the pipeline loop, starting after 30 s and doubling up to every 15 min
(`COOLIFY_RETRY_BASE` / `COOLIFY_RETRY_MAX`). A qualified build is never dropped.

## Customer tokens (v115)

A customer's own integration tokens reach their app through Coolify, never through ATTa's disk:

```
PATCH {COOLIFY_URL}/api/v1/applications/<uuid>/envs/bulk      (services/<uuid>/... for a service)
{"data": [{"key": "STRIPE_SECRET_KEY", "value": "...", "is_literal": true, "is_shown_once": true,
           "is_runtime": true, "is_buildtime": false, "is_preview": false, "is_multiline": false}]}
then POST {COOLIFY_URL}/api/v1/deploy?uuid=<uuid>&force=false   so the running app picks them up
```

Entered on `/apps/<app>/secrets` (the account that added the app, or an admin) or
`POST /api/apps/<app>/secrets` `{"secrets": {"NAME": "value"}}`. ATTa writes only a receipt,
`state/customer_secrets/<app>.json`: name, a 16-hex keyed fingerprint, time, build, who, and the Coolify
resource. `POST /api/apps/<app>/secrets/check` `{"name", "value"}` says whether a token matches what was
delivered, from the fingerprint alone. ATTa never reads a value back from Coolify. If Coolify isn't
configured or the app has no resource yet, nothing is sent and nothing is kept: enter the tokens again later.
Checked against the bundled 4.3.23 source: `routes/api.php` (`write` ability), `create_bulk_envs`
(accepted fields, 201), `removeSensitiveData` (values hidden without `read:sensitive`).

ATTa's own local check runs never see a customer's value: each recorded variable gets the placeholder
`atta-local-check-placeholder-not-a-real-secret`. An app that refuses to start without a REAL key can't
be proved locally. Its deployed copy in Coolify is the one that runs with it.

## Checked against Coolify's source

The supplied Coolify source (`05-coolify/coolify-main.zip`, version 4.3.23) was read to confirm:

- `routes/api.php`: `POST /api/v1/deploy` needs the `deploy` permission. A `GET` answers 405
  "This endpoint has changed to a POST request". `GET /api/v1/applications` needs `read`.
- `DeployController::deploy`: an unknown UUID answers 404 "No resources found", which the
  self-healer's lookup fix handles. Success is 200 with `deployments[]`, and each entry carries
  a `deployment_uuid` when a deploy was actually queued.
- `ApiAllowed` middleware: if API access is off in Coolify's settings, every call answers 403.

This has not yet been run against a live Coolify. That needs a server where Coolify is installed.
