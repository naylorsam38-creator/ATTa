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
   GET {COOLIFY_URL}/api/v1/deploy?uuid=<resource uuid>&force=false
   Authorization: Bearer {COOLIFY_TOKEN}
   ```

   Any 2xx response counts as accepted. An app that's already accepted is never deployed a second
   time for the same build.

## What the owner sets up (the Coolify side)

| Where | What |
|---|---|
| `<ROOT>/.env` | `COOLIFY_URL=https://your-coolify-host` and `COOLIFY_TOKEN=<API token with deploy permission>` |
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

## Not yet verified

- The deploy endpoint path and method come from the Coolify v4 API as I remember it. I
  couldn't check the Coolify docs from this environment. If your Coolify version differs, set
  `COOLIFY_DEPLOY_PATH` in `.env`.
- It has not been run against a real Coolify instance. The client was tested against a local HTTP
  stand-in that checked the path, the bearer token, the retry after a 500, and that nothing was
  deployed twice.
