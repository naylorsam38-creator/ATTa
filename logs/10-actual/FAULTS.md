# 10. Actual — commit 8d59f30cfb36 (2026-10-17), image actualbudget/actual-server:latest
Docs (README → actualbudget.org/docs/install/docker): "use the docker-compose.yml file included in the actual repository … `docker compose up --detach`" (= packages/sync-server/docker-compose.yml; root docker-compose.yml is the dev container), or alternatively the documented `docker run --pull=always --restart=unless-stopped -d -p 5006:5006 -v <dir>:/data --name my_actual_budget actualbudget/actual-server:latest`.

Steps: A ok · B ok · C **FAIL** (compose) · D ok (pull via docs' docker run) · E **FAIL** · F–J NOT REACHED - blocked by step E

## App faults
1. **C — Shipped docker-compose.yml is rejected by Docker Compose as-is.** `docker compose up --detach` → `validating .../packages/sync-server/docker-compose.yml: services.actual_server.environment must be a mapping`. Every entry under `environment:` is commented out, leaving a null value. The file itself says "!! If you are not using any of these options, remove the 'environment:' tag entirely", but the docs page tells users to just run `docker compose up --detach`.
   Points to: `packages/sync-server/docker-compose.yml:8-17`.
2. **E — Server never listens; logs success, then exits 0 in a restart loop.** With the documented `docker run`, the container logs `Listening on :::5006...` and immediately exits with code 0 (restart count 11 after ~2 min). Port 5006 never answers (`curl` → no response for 120 s). Cause: default bind host is `::` (`packages/sync-server/src/load-config.js:93-97`, `default: '::'`) and this sandbox kernel has no IPv6, so `listen` fails with EAFNOSUPPORT. Express 5 `app.listen` passes the bind error to the callback (`server.once('error', done)`, express/lib/application.js:603), but Actual's callback ignores it and prints the success message.
   Points to: `packages/sync-server/src/app.ts:234-236` (`app.listen(port, hostname, () => { sendServerStartedMessage(); })`) and `:229-231` (https variant). Environment-triggered (no IPv6), but the error is swallowed and reported as success.

- F. HTTP — NOT REACHED - blocked by step E
- G. Playwright pages/signup/login — NOT REACHED - blocked by step E
- H. Forms — NOT REACHED - blocked by step E
- I. Browser console — NOT REACHED - blocked by step E
- J. Container logs — captured (startup loop above); runtime NOT REACHED - blocked by step E

Fault count: 2 · first failing step: C
