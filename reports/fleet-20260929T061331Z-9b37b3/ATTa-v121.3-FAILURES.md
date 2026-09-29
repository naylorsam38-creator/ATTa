# ATTa v121.3: fleet failures (fleet-20260929T061331Z-9b37b3)

The fleet ran on EC2 `i-0f13b8644b215931d`. It finished at 2026-09-29 07:11 UTC.

**Source.** Everything here was read, read-only, from ATTa's own `/srv/app-builder/state/runner/RUN-REPORT.md` and `RUN-REPORT.json`, and from each app's failure record in `/srv/app-builder/state/runner/failures/<app>/`.

**Nothing was changed.** No rerun, no fix and no deploy was made for this report.

**Totals.**

| Item | Count |
|---|---|
| Apps requested and reported | 29 |
| QUALIFIED | 7 |
| FAILED | 11 |
| START_FAILED | 8 |
| TIMEOUT | 3 |
| INCONCLUSIVE, UNVERIFIED, NOT_STARTED | 0 each |

The peak was 29 apps running at once. The deployment revision `code:e46676963529e1afb54f957b` stayed the same for the whole run. Every app ran once (attempt 1).

**Repairs.** Every failed app has repair status `NO_MATCHING_REPAIR`: ATTa had no evidence-based fix for it, so no repair or retest was done.

**Qualified (7).** agno, docker-moby, frp, grafana, graphite, stride, tidb. agno, docker-moby and stride are system or package projects, so ATTa checked whether they build and never ran them as apps.

**Where the evidence is.**
- Each app's failure record is `/srv/app-builder/state/runner/failures/<app>/<run>-a1-f1.json`. It sits next to a `.container.log` when the app got as far as starting.
- Stage 6 (browser) evidence is in `/srv/app-builder/state/runner/evidence/<app>/<run>-a1/`. The files are `screenshot.png`, `page.html` and `browser.json`.

## The 22 failures, classified by the evidence

### Proven ATTa-side (2)

| App | Stage | Exact failure | Evidence |
|---|---|---|---|
| memos | 6 CLEAN | `CONSOLE_ERRORS:19`. All 38 console and request errors are `net::ERR_NETWORK_CHANGED`, on memos's own `127.0.0.1:8185` assets. | `evidence/memos/run-1790662411421-memos-e2b8ce1d-a1/browser.json`. The container log shows memos answering normally at the same time. |
| super-productivity | 6 CLEAN | `CONSOLE_ERRORS:151`. All 100 errors counted are `net::ERR_NETWORK_CHANGED`, on its own `127.0.0.1:8815` assets. | `evidence/super-productivity/run-1790662411454-super-productivity-6adfb2c6-a1/browser.json`. The container log shows the same files served with HTTP 200. |

Chromium throws `net::ERR_NETWORK_CHANGED` when the host's network changes during a page load. Here that was Docker creating networks for the other apps running in parallel. Neither app was at fault.

### ATTa-side from the evidence (5)

In each of these, the app itself answered. The problem was the part ATTa chose to run, the port ATTa checked, its proxy origin, or its login strategy.

| App | Stage | Exact failure | Evidence |
|---|---|---|---|
| traefik | 6 CLEAN | `JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL` | ATTa ended up running `traefik/traefikee-webapp-demo`, a demo site, after the CLI images needed a command. The page title was "Traefik Labs: Makes Networking Boring". |
| coolify | 3 PROXY_UP | `NOT_HTML` | `coolify-helper` never answered, at 240 s and again at 900 s. ATTa then ran `coolify-realtime`, the Soketi websocket server, which is not the web UI. |
| supabase | 6 CLEAN | `JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL` | The page title was "400 The plain HTTP request was sent to HTTPS port", so ATTa sent plain HTTP to an HTTPS port. The log also shows the database host `docker.for.mac.localhost` could not be resolved. |
| flarum | 6 CLEAN | `CONSOLE_ERRORS:4` (CORS) | Fonts load from `127.0.0.1:20000`, the app's own configured URL. The page is served through ATTa's proxy at `127.0.0.1:8426`, so the fonts are cross-origin and blocked. |
| apache-airflow | 6 CLEAN | `JOURNEY_NOT_AUTHORED:ACCOUNT_NO_STRATEGY`: "no sign-up or first-run form found" | The app is up on its login page. The log shows `POST /auth/token` answered 401 twice, so ATTa had no admin credential to log in with. |

### Infrastructure or dependency: the app needs a companion service, secret or config (9)

| App | Stage | Exact failure | Evidence |
|---|---|---|---|
| umami | 2 APP_UP | `container.exited` on all 4 ways of running it | `check-db.js: TypeError: Invalid URL, input: 'undefined'`. There is no `DATABASE_URL` or Postgres. |
| colanode | 6 CLEAN | `SMOKE_NO_CONTROL` | The server needs Redis: `REDIS_URL is not set`, then the Redis client crashed. Only the web UI came up, with no backend behind it. |
| medplum | 6 CLEAN | `SMOKE_NO_CONTROL` | The server exited. The UI came up, but its API calls got `ERR_CONNECTION_REFUSED` (3 times). |
| ghost | 2 APP_UP | `RUNNER_EXHAUSTED` after 8 attempts | The published image failed with `app.needs_database` (it needs MySQL). The other 6 attempts were dev-tooling Dockerfiles, which failed on missing COPY sources. |
| billionmail | 2 APP_UP | TIMEOUT (`app.still_starting`) | The postfix start script reads `/etc/postfix/sql/pgsql_*.cf` files that don't exist, and the dovecot sieve scripts fail to compile. It is a multi-service mail stack. |
| plane | 2 APP_UP | `container.exited` on all 8 images | The plane-proxy nginx config is invalid: `client_max_body_size` has no value because an env var is unset. |
| opencloud | 2 APP_UP | `container.exited` | "The jwt_secret has not been set … use `opencloud init`". It needs initialised config or a secret. |
| polar | 2 APP_UP | TIMEOUT | The server build failed. pgbouncer needed env vars. Tailscale waited for an interactive login, which needs an auth key. |
| clearflask | 2 APP_UP | TIMEOUT (`app.stalled`) | The connect image needs `/opt/clearflask/connect.config.json`. Mounting a volume did not fix it: the same failure came back and was recorded as FIX_DID_NOT_RESOLVE. The server image took 184 s to start in Tomcat and then never answered. |

### Cause not yet proven (6)

| App | Stage | Exact failure | Evidence |
|---|---|---|---|
| open-design | 2 APP_UP | `build.failed`: COPY source `/plugins/_official` not found (Dockerfile line 79) | Build log in the failure record |
| opencart | 2 APP_UP | The compose file's images are missing. Its Dockerfiles then failed on missing COPY sources: `docker/php/docker-opencart-entrypoint`, `docker/nginx/conf/default.conf`, `docker/apache/conf/opencart.conf`. | Build log in the failure record |
| botpress | 2 APP_UP | The main Dockerfile ran but answered `Missing bot header`. `integrations/chat/Dockerfile` failed because COPY source `server.js` was not found. | Failure record |
| langflow | 2 APP_UP | The main images exited. The frontend needs `BACKEND_URL`. The compose file ATTa took from the docs failed to parse (`go-yaml … mapping values are not allowed`). | Failure record |
| appsmith | 6 CLEAN | `BROWSER_HTTP 502` through the proxy, while the app's own log shows requests handled with 200 | `browser.json` and the container log |
| krayin-crm | 4 SKIN | `NO_LINK`: the skin `<link>` is not in the page, and no detail was recorded | Failure record and container log (supervisord shows apache and mysql running) |

**One pattern to investigate.** open-design, opencart and botpress, plus 6 of ghost's attempts, all fail the same way: a COPY source is "not found" during the Docker build. It happens across unrelated apps. That points at either the build context ATTa uses or ATTa's library checkout. It is not proven yet, so it is not counted as ATTa-side.
