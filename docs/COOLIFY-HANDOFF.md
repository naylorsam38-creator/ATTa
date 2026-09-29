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

## Checked against Coolify's source

The supplied Coolify source (`05-coolify/coolify-main.zip`, version 4.3.23) was read to confirm:

- `routes/api.php`: `POST /api/v1/deploy` needs the `deploy` permission. A `GET` answers 405
  "This endpoint has changed to a POST request". `GET /api/v1/applications` needs `read`.
- `DeployController::deploy`: an unknown UUID answers 404 "No resources found", which the
  self-healer's lookup fix handles. Success is 200 with `deployments[]`, and each entry carries
  a `deployment_uuid` when a deploy was actually queued.
- `ApiAllowed` middleware: if API access is off in Coolify's settings, every call answers 403.

This has not yet been run against a live Coolify. That needs a server where Coolify is installed.
