# Docker + Playwright Execution Test Report — 24 GitHub Apps

Run date: 2026-10-17. Every result below was observed during execution in this session; nothing is predicted or researched.

## Environment (verified before testing)

| Capability | Status |
|---|---|
| Git | 2.43.0 |
| Docker | Engine 29.8.2 + Compose v5.6.0. The daemon was not running and was started manually with `dockerd`. |
| Playwright | 1.56.1 (Node), Chromium 1194 |
| Host | 4 vCPU, 15 GB RAM, ~31 GB free disk, Linux 6.18, cgroup v1 |

### Environmental limitations (these are NOT app faults)

- **ENV-1: Docker Hub rate limit.** Anonymous pulls from `registry-1.docker.io` returned `429 Too Many Requests` (`ratelimit-remaining: 0;w=3600`, shared egress IP 160.79.106.139). Mitigation: the daemon-level `/etc/docker/daemon.json` `{"registry-mirrors":["https://mirror.gcr.io"]}`. Google's official Docker Hub mirror serves the same images. No app file or image reference was changed.
- **ENV-2: no outbound HTTPS from inside containers.** All session egress goes through a TLS-intercepting proxy whose CA is not trusted inside containers. Every HTTPS fetch from a container fails with `certificate verify failed` / `Connection reset by peer`. Plain HTTP works (e.g. `http://deb.debian.org`). Any image *build* that downloads packages over HTTPS (npm, pip, go mod, apk, cargo, …) therefore cannot succeed here without editing Dockerfiles, which the test rules forbid. Such failures are labelled **ENVIRONMENT (ENV-2)** and are **not counted as app faults**. Runtime calls to external services (e.g. update checks) also fail for the same reason.
- **ENV-3:** single host, no public DNS/TLS, no real SMTP, no GPU, no real OAuth providers. No external integrations were configured.
- **ENV-4: no IPv6 in the host kernel.** `/proc/sys/net/ipv6` does not exist on the host or in containers. Software that binds `::` fails with `EAFNOSUPPORT` (errno 97, "Code: -97"). Found while testing Ghost (#9).
- **ENV-5: low open-files hard limit.** The host and containers have `ulimit -Hn` = 20000; common Docker defaults are 1048576. Software that requires a higher `RLIMIT_NOFILE` cannot raise it ("Operation not permitted"). Found while testing Supabase (#12).

### Method

- Each repo was shallow-cloned into `/home/user/apps-test/<app>`. Compose projects are named `t-<app>`, so cleanup touches only that app's resources. The Docker baseline before testing was empty (no pre-existing containers, volumes, or networks).
- Step G/H/I uses `harness/crawl.js`, a generic Playwright script:
  - breadth-first crawl of same-origin pages (≤25);
  - up to 12 clicks of non-destructive buttons per page;
  - each distinct form is filled once with fictional data (`testuser` / `tester@example.test` / `Test-Passw0rd!2026xyz`) and submitted;
  - captures console errors, page errors, failed requests and HTTP ≥ 400 responses.
- Links, buttons and submits matching delete/remove/reset/wipe/logout/send/publish/pay/buy/import/upload/etc. are skipped for safety.
- `net::ERR_ABORTED` on requests cancelled by the harness's own navigation is recorded in evidence but is **not** counted as a fault. Errors on third-party sites the crawler followed into (e.g. github.com) are also excluded.
- Evidence is kept in `evidence/<app>/`:
  - `commit.txt`
  - `pull.log` / `build.log` / `up.log`
  - `http.txt`
  - `playwright.json`
  - screenshots
  - `container.log`

---

## 1. ArchiveBox

- **A.** `ArchiveBox/ArchiveBox` at `eec1d7db5fc0`, committed 2026-10-16 ("Bump release version to 0.9.74rc283").
- **B.** Setup from README "Option A: Docker Compose" plus the repo's `docker-compose.yml`. Image: `archivebox/archivebox:latest`.
- **C.** Created `./data`. No env changes; compose defaults used (`PUBLIC_ADD_VIEW=False`, `ARCHIVEDOTORG_ENABLED=True`).
- **D.** `docker compose -p t-archivebox pull` exited 0 in 57 s. This is a prebuilt image; there is no source build in the documented path.
- **E.** `docker compose -p t-archivebox up -d --wait` exited 0. Container was `Up (healthy)`; the app reported version 0.9.73.
- **F.** `http://admin.archivebox.localhost:5797/admin/` returned 302 to `/admin/login/?next=/admin/`. The login page returned 200.
- **G.** The first-run "Create admin and continue" form was submitted with a fictional admin, which logged in successfully.
  - 22 same-origin pages returned 200: admin dashboard, `/add/`, crawls, snapshots, archive results, tags, personas, users, config/binaries/plugins/workers/logs, machine, API docs.
  - 128 safe clicks.
- **H.** 17 forms submitted:
  - The machine change form correctly rejected test data ("Enter a valid JSON").
  - Search forms worked. Admin bulk-action selects showed native "Please select an item" validation, which is expected.
- **I.** No app console errors and no HTTP ≥ 400 on the app origin.
  - Excluded from the count: 11 `ERR_ABORTED` requests (setup probes and `/progress.json` polls cancelled by navigation), plus 404 and clipboard errors on github.com after following the external Docs link.
- **J.** `container.log` and `app-logs/*.log` have no errors or tracebacks on startup or shutdown (`docker compose stop`).
- **K.** `down -v --rmi all`; data dir emptied. Verified 0 containers, volumes, networks and images remaining.
- **Safety skips:** "Log out" submits, "🗑 Delete" links, "import from Chrome".
- **Limitation:** No URL was actually archived. Archiving makes external requests and submits to archive.org (ENV-2/ENV-3).

```
ArchiveBox
No faults observed in attempted steps
```

---

## 2. Authelia

- **A.** `authelia/authelia` at `e8f58adc603d`, committed 2026-10-17 ("build(deps): update module github.com/knadh/koanf/v2 to v2.4.0 (#13509)").
- **B.** README "docker compose" section names the **Local** bundle for testing: `examples/compose/local/` (`compose.yml`, `setup.sh`). Images: `authelia/authelia`, `traefik:v3.6.13`, `traefik/whoami`.
- **C.** Ran the documented `./setup.sh` with piped answers: domain default `example.com`, user `testuser`, display name `Testy McTestface`, fictional password. The script itself edits `/etc/hosts` and `users_database.yml` and generates certs. Nothing was edited by hand.
- **D.** Prebuilt images; `setup.sh` pulls them (exit 0, ~13 s). No source build in the documented path.
- **E.** The script's `docker compose up -d` exited 0. Containers `authelia`, `traefik`, `secure` and `public` were Up.
- **F.**
  - `https://authelia.example.com/api/health` returned 200 and `https://public.example.com/` returned 200.
  - `https://traefik.example.com/` and `https://secure.example.com/` returned 302 to the Authelia portal, as expected.
- **G.** Crawled the portal, reset-password step 1, the 2FA page and settings.
  - 1FA login reached the protected Traefik dashboard (`/dashboard/`, title "Traefik Proxy").
  - 2FA: "Register device" → ADD OTP → identity-verification code read from `authelia/notification.txt` (the documented filesystem notifier) → TOTP registered → logged in with a fresh TOTP → reached `https://secure.example.com/` (whoami output).
- **H.** The login form submitted successfully.
  - `GET /api/secondfactor/totp` returned 404 before registration; the server log says "no TOTP configuration for user", which is expected API behavior.
  - A first OTP attempt in the same 30 s window returned 403 "already used this code recently". That is correct replay protection, not a fault.
- **I.** No console errors other than the expected 404 and 403 above. `ERR_ABORTED` locale and `api/state` fetches were cancelled by navigation.
- **J.** Logs saved to `container.log` and `container-run1.log`.
- **K.** `docker compose -p local down -v --rmi all` (sudo inside `setup.sh` drops `COMPOSE_PROJECT_NAME`, so the project is `local`). Also removed the leftover containers, restored `/etc/hosts`, and ran `git clean` on the bundle. Verified no containers, volumes, networks or app images remain.
- **Excluded (tester-induced):** `attempt to write a readonly database` was caused by my deleting `db.sqlite3` under the still-running first stack. A clean re-run was done.
- **Safety skips:** RESET (password reset), LOGOUT.

```
Authelia
- Step E: Traefik fails to load the TLS certificate generated by setup.sh:
  `ERR tlsmanager.go:137 > Unable to parse certificate /etc/traefik/certs/cert.pem error="unable to generate TLS certificate: tls: failed to find any PEM data in certificate input"`.
  setup.sh line 74 (`authelia crypto certificate rsa generate --directory=/tmp/certs/`) writes `public.crt` + `private.pem`.
  `traefik/certificates.yml` expects `cert.pem` + `key.pem`.
  Result: Traefik serves `CN = TRAEFIK DEFAULT CERT` instead of the generated `CN=*.example.com`.
  Points to examples/compose/local/setup.sh:74, examples/compose/local/traefik/certificates.yml:8, container `traefik`; evidence/authelia/served-cert.txt.
- Step J: `level=error msg="Request timeout occurred while handling request from client." error="read tcp 172.18.0.3:9091->172.18.0.4:44946: i/o timeout" ... status_code=408` (4 times, on connections from traefik, path `/`). No user-facing failure was observed alongside these.
  Points to container `authelia`, evidence/authelia/container-run1.log.
- Step K: setup.sh's helper `docker run` calls (lines 74 and 112, no `--rm`) leave 2 exited `authelia/authelia` containers per run (e.g. `eager_carver`, `inspiring_jackson`). They are not removed by `docker compose down`.
  Points to examples/compose/local/setup.sh; evidence/authelia/containers-before-cleanup.txt.
```

---

## 3. Mixpost

- **A.** `inovector/mixpost` at `df57648b8663`, committed 2026-03-16 ("Update CHANGELOG").
- **B.** The repo is a Laravel package with no Dockerfile or compose file. The README "Installation" section links to the official docs, https://docs.mixpost.app/lite/installation/docker/, which name the official image `inovector/mixpost:latest`. The page is saved as `evidence/mixpost/docs-docker.html`.
- **C.** Used the docs' "Without SSL Support" `docker-compose.yml` verbatim (`docs-block-4.txt`, exposing port 9000) and the docs' `.env` template, changed "accordingly" as the docs instruct:
  - generated `APP_KEY` (`base64:` 32 random bytes)
  - `APP_DOMAIN=localhost`
  - `APP_URL=http://localhost:9000`
  - `DB_PASSWORD=FakeDbPass2026`
  
  The file used is in `env-used.txt`, with the key omitted.
- **D.** `docker compose -p t-mixpost pull` exited 0. Prebuilt images, no source build.
- **E.** `docker compose -p t-mixpost up -d` exited 0. `mixpost` was Up; `mysql` and `redis` were Up (healthy).
- **F.** `http://localhost:9000/` returned 302 to `/mixpost`, which returned 302 to `/mixpost/login` (200).
- **G.** Logged in with the documented default credentials `admin@example.com` / `changeme`.
  - 23 pages returned 200: accounts, posts, posts/create, calendar (two dates), media, services, settings, system status/logs, and Horizon (dashboard, monitoring, metrics, batches, jobs pending/completed/silenced, failed).
  - 77 safe clicks.
- **H.**
  - Services form submitted with fictional values; no error.
  - A draft post was typed into the editor. It auto-saved (`/mixpost/posts/<uuid>`) and is listed under Posts with status "Draft".
  - "POST NOW" and "ADD TO QUEUE" were not clicked (publishing).
- **I.** 0 console errors, 0 page errors, 0 failed requests, 0 HTTP ≥ 400.
- **J.** Container logs have only routine MySQL/Redis warnings: deprecated `--skip-host-cache`, a self-signed CA, Redis' no-auth and overcommit notices, and IPv6 bind `errno: 97` (no IPv6 in the container). The Laravel `storage/logs` volume was empty, with no errors.
- **K.** `down -v --rmi all`. Verified 0 containers, volumes and networks, and the images were removed.
- **Safety skips:** "Upload" (media), POST NOW, ADD TO QUEUE.
- **Limitation:** No social accounts were connected (external OAuth, ENV-3).
- **Harness note:** The first crawl showed a 405 that came from the session proxy, not Mixpost (Playwright's `proxy` option forces loopback through the proxy). The harness was fixed and re-run; the 405 is not counted.

```
Mixpost
No faults observed in attempted steps
```

---

## 4. Alexandrie

- **A.** `Smaug6739/Alexandrie` at `ccf5603ea067`, committed 2026-09-21 ("docs: update curl commands and link formatting in README.md (#780)").
- **B.** README "Quick Start": download `.env.example` + `docker-compose.yml`, `cp .env.example .env`, `docker compose up -d`. Images: `ghcr.io/smaug6739/alexandrie-backend:latest`, `-frontend:latest`, `mysql:8.0`, `rustfs/rustfs:latest`.
- **C.** `cp .env.example .env`, unmodified, exactly as documented.
- **D.** `docker compose -p t-alexandrie pull` exited 0. Prebuilt images.
- **E.** `docker compose -p t-alexandrie up -d` exited 0. All four containers Up; mysql and rustfs healthy.
- **F.** The README's documented URL `http://localhost:8200` returned 200. The README documents no API health endpoint, so `:8201/` returning 404 was not judged.
- **G.**
  - Signup with fictional `testuser` / `tester@example.test` redirected to `/login`; login reached `/dashboard/home`.
  - 17 pages returned 200: landing, signup, login, dashboard home/categories/docs/docs-new/cdn/cdn-diagram/teams/settings, documentation, about, privacy.
  - 58 safe clicks.
- **H.**
  - Profile Update form: "Profile updated".
  - Join-workspace with a fictional invite code: "Not found" (404). That is correct validation.
  - Targeted upload of a generated 8×8 test PNG: `POST /api/resources` returned 200.
- **I.** Console and network errors are listed below.
  - Excluded: `403 https://api.github.com/repos/.../contributors` "rate limit exceeded" (shared egress IP, ENV); `ERR_ABORTED` chunk/API fetches (navigation).
- **J.** Logs had only routine MySQL warnings. The backend logged no errors.
- **K.** `down -v --rmi all`; removed `.env`. Verified 0 containers, volumes, networks and images.
- **Safety skips:** Import, "Publish & Share".

```
Alexandrie
- Step H: Uploaded media cannot be displayed. The frontend requests
  `http://localhost:9005/alexandrie/766684384823738369/766685157687164933.png` → `net::ERR_CONNECTION_REFUSED`.
  The same object returns 200 image/png at `http://localhost:9000/...`.
  Cause: `.env.example:21 CDN_URL=http://localhost:9005` while `.env.example:64 RUSTFS_EXTERNAL_PORT=9000`, so the documented `cp .env.example .env` yields a CDN URL with no listener.
  Points to .env.example, service `frontend`/`backend` (NUXT_PUBLIC_BASE_CDN / MINIO_PUBLIC_URL).
- Step I: Every visit to `/login` logs a console error: `GET http://localhost:8201/api/auth/oidc/providers` → 404 (Not Found) with the default (no-OIDC) configuration.
  Points to service `backend`, URL /api/auth/oidc/providers.
- Step I: Console error on `/dashboard/home`: "Hydration completed but contains mismatches." (Nuxt SSR/client mismatch).
  Points to service `frontend`, URL http://localhost:8200/dashboard/home.
- Step G: The diagram editor at `/dashboard/cdn/diagram` fails to load its embedded editor: `403 GET https://embed.diagrams.net/?embed=1&ui=min&spin=1&proto=json` (external service; reproduced with curl using an iframe/localhost Referer → 403, while a direct request → 200).
  Points to external dependency embed.diagrams.net, URL /dashboard/cdn/diagram.
```

---

## 5. memos

- **A.** `usememos/memos` at `1a51572406e1`, committed 2026-10-17 ("feat(web): add source mode to the memo composer").
- **B.** README "Quick Start" documents `docker run -d --name memos -p 5230:5230 -v ~/.memos:/var/opt/memos neosmemo/memos:stable`.
- **C.** Used as documented, with two deviations for isolation only: container name `t-memos`, and the host data dir `/home/user/apps-test/memos-data` instead of `~/.memos`. No app configuration set.
- **D.** `docker pull neosmemo/memos:stable` exited 0. Prebuilt image.
- **E.** `docker run` exited 0. Container Up; log shows "Server running on port 5230", sqlite driver.
- **F.** `http://localhost:5230/` returned 200 and `/healthz` returned 200.
- **G.**
  - `/` redirected to `/auth/signup`. "Create admin account" with fictional `testuser` succeeded.
  - Sign-in with the same credentials worked.
  - Clean visits all loaded: `/`, `/explore`, `/views`, `/calendar` (→ `/calendar/2026/10`), `/map`, `/attachments`, `/inbox`, `/archived`, `/setting`, `/u/testuser`.
  - The crawl visited 25 pages with 85 safe clicks.
- **H.** Created a memo ("Fictional test memo #testing") via the composer's Save button; it appears in the timeline.
- **I.** No app faults.
  - Excluded: 401 `AuthService/RefreshToken` on unauthenticated page loads (server log: "refresh token not found", correct behavior). 
  - Excluded: "Failed to initialize auth/instance …: Failed to fetch" console errors. Every instance coincides with an `ERR_ABORTED` request cancelled by harness navigation, and clean visits do not reproduce them.
  - `/api/v1/sse` aborts happen on each navigation away from the page.
- **J.** No startup or shutdown errors.
  - Not counted: the server logs client-cancelled requests at `level=ERROR msg="unknown error" ... error="context canceled"`, triggered by the harness's aborted requests above.
- **K.** Removed the container and image and emptied/removed the data dir. Verified 0 containers and volumes.

```
memos
No faults observed in attempted steps
```

---

## 6. OpenCut

- **A.** `OpenCut-app/OpenCut` at `e66801077856`, committed 2026-09-24 ("ci(media): add the workflow that builds FFmpeg for every platform").
- **B.** No Docker setup exists at this commit:
  - no `Dockerfile`, `docker-compose*`, `compose.y*ml` or `.dockerignore` anywhere in the tree;
  - the only file mentioning "docker" is `Cargo.lock` (a crate name);
  - the README "Development" section documents only `proto use` + `moon run web:dev` / `api:dev` / `desktop:dev`.
  
  The README says the repo is a Rust-core rewrite and points to `opencut-app/opencut-classic` as "the one to reach for today". That is a different repository and was not substituted.
- **K.** No Docker resources were created; nothing to clean. The clone is kept at `/home/user/apps-test/opencut`.

```
OpenCut
- Step B: No supported Docker setup or documented official image: repo tree has no Dockerfile/compose file; README documents only proto/moon dev commands (README.md "Development"); evidence/opencut/docker-grep.txt.
- Step C: NOT REACHED - blocked by step B: no Docker setup to configure.
- Step D: NOT REACHED - blocked by step B.
- Step E: NOT REACHED - blocked by step B.
- Step F: NOT REACHED - blocked by step B.
- Step G: NOT REACHED - blocked by step B.
- Step H: NOT REACHED - blocked by step B.
- Step I: NOT REACHED - blocked by step B.
- Step J: NOT REACHED - blocked by step B.
```

---

## 7. open-notebook

- **A.** `lfnovo/open-notebook` at `a0f6f3081ae0`, committed 2026-10-04 ("docs: defaults table matches the code; CHANGELOG entry for the docs overhaul (#1462)").
- **B.** `docs/1-INSTALLATION/docker-compose.md` ("Recommended") with the root `docker-compose.yml`. Images: `lfnovo/open_notebook:v1-latest`, `surrealdb/surrealdb:v2`.
- **C.** Copied `docker-compose.yml` into its own folder (docs Step 1) and made only the documented edits (`evidence/open-notebook/config-diff.txt`):
  - Step 2: `OPEN_NOTEBOOK_ENCRYPTION_KEY` = `openssl rand -hex 32`.
  - Step 3: ports bound to `127.0.0.1`.
- **D.** `docker compose -p t-open-notebook pull` exited 0. Prebuilt images.
- **E.** `up -d` exited 0. Both services Up.
- **F.** Documented check `curl http://localhost:5055/health` returned `{"status":"healthy"}` (200). UI `http://localhost:8502/` returned 307 to `/notebooks` (200).
- **G.** Auth is off by default, so there was no signup or login to test. 8 pages returned 200: notebooks, sources, search, podcasts, settings/models, transformations, settings, advanced. 48 safe clicks.
- **H.**
  - Settings Save: no error.
  - Created notebook "Fictional Test Notebook" (`POST /api/notebooks` 200); it is listed.
  - Added a text source (`POST /api/sources` 200). The UI then shows "Source processing failed", because no AI provider or models are configured. Server log: "No model configured for default for type=transformation" / "No embedding model configured". The docs list an AI provider as a prerequisite, so this is a **limitation (ENV-3: no external AI keys)**, not counted.
- **I.** 0 console errors, 0 HTTP ≥ 400. One `ERR_ABORTED` (navigation).
- **J.**
  - `WARNING Version check failed: [SSL: CERTIFICATE_VERIFY_FAILED] ... self-signed certificate in certificate chain` is ENV-2.
  - `WARNING CORS_ORIGINS is not set` is informational.
  - The only ERRORs are the three model-not-configured lines above. No startup or shutdown errors.
- **K.** `down -v --rmi all`; removed the data dirs and run folder. Verified 0 containers, volumes, networks and images.
- **Safety skips:** Sign Out, Archive, Delete.
- **Limitations:** Chat, podcasts and transformations were not exercised (they need an external AI provider).

```
open-notebook
No faults observed in attempted steps
(limitation: source processing/chat require an AI provider, which is unavailable here.)
```

---

## 8. Sendportal

- **A.** `mettle/sendportal` at `c76a9cffa11f`, committed 2026-10-12 ("Fix sp:install skipping migrations on a fresh install (#344)").
- **B.** No Docker setup exists:
  - no `Dockerfile` or compose file in the repo;
  - the README "Installation" section links to https://sendportal.io/docs/v4/getting-started/installation, which documents only `git clone` + `composer install` on PHP ≥ 8.3 with Apache or nginx;
  - "docker" appears 0 times on that page (saved as `evidence/sendportal/docs-installation.html`) and on Configuration & Setup / Quickstart;
  - `/docs/v4/getting-started/docker` is a catch-all that serves the Installation page;
  - no official image is named anywhere.
- **K.** No Docker resources were created; nothing to clean.

```
Sendportal
- Step B: No supported Docker setup or documented official image: repo has no Dockerfile/compose; official installation docs (sendportal.io/docs/v4/getting-started/installation) describe only composer + PHP webserver install; evidence/sendportal/docs-installation.html.
- Step C: NOT REACHED - blocked by step B.
- Step D: NOT REACHED - blocked by step B.
- Step E: NOT REACHED - blocked by step B.
- Step F: NOT REACHED - blocked by step B.
- Step G: NOT REACHED - blocked by step B.
- Step H: NOT REACHED - blocked by step B.
- Step I: NOT REACHED - blocked by step B.
- Step J: NOT REACHED - blocked by step B.
```

---

## 9. Ghost

- **A.** `TryGhost/Ghost` at `d4952f1dec90`, committed 2026-10-17 ("Update Docker image digests").
  - The documented Docker tooling repo `TryGhost/ghost-docker` is at `f859922bd147`, committed 2026-10-16.
- **B.** The README documents only ghost-cli. The repo's Docker files are dev/e2e tooling (`compose.dev*.yaml`, `docker/*`, `Dockerfile.production`).
  - Ghost's official docs, https://docs.ghost.org/install/docker ("preview"), name the Docker path: clone `TryGhost/ghost-docker`, use the official `ghost:6-alpine` image plus Caddy and MySQL. The page is saved as `docs-docker.html`.
- **C.** As documented: `cp .env.example .env`, `cp caddy/Caddyfile.example caddy/Caddyfile`. Changed `DOMAIN=localhost` (no public DNS, ENV-3) and generated both DB passwords with `openssl rand -hex 32` (`config-diff.txt`). The example SMTP values were left fictional.
- **D.** `docker compose -p t-ghost pull` exited 0 in 32 s. Prebuilt images.
- **E.** `docker compose -p t-ghost up -d` exited 0, but `ghost` is `Restarting (2)` in a loop: 40 occurrences of `Code: -97`, the server failing to listen. `db` was healthy and `caddy` Up.
  - Cause: the official image's `/var/lib/ghost/config.production.json` sets `"server": {"host": "::"}`, and this host has no IPv6 (ENV-4).
  - Changing `server__host` would be a configuration workaround, which the test rules forbid, so it was not applied.
- **F.** `https://localhost/` and `https://localhost/ghost/` returned **502** (Caddy, no upstream). `http://localhost/` returned 308.
- **K.** `down -v --rmi all`; removed `data/`, `.env` and `Caddyfile`. Verified 0 containers, volumes, networks and images.

```
Ghost
- Step E: ENVIRONMENT (ENV-4), not counted as an app fault: container `ghost` (ghost:6-alpine, Ghost 6.71.0) crash-loops:
  `ERROR (Code: -97) "There was an error starting your server." InternalServerError: (Code: -97) at Server.<anonymous> (/var/lib/ghost/versions/6.71.0/core/server/ghost-server.js:91:34)`.
  The image default binds `"host": "::"` (config.production.json) and the host kernel has no IPv6.
  Points to container t-ghost-ghost-1, evidence/ghost/container.log.
- Step F: https://localhost/ → 502 from caddy, as a consequence of step E; evidence/ghost/http.txt.
- Step G: NOT REACHED - blocked by step E: Ghost never listened.
- Step H: NOT REACHED - blocked by step E.
- Step I: NOT REACHED - blocked by step E.
- Step J: done (logs captured); only the step E error.
```

---

## 10. Actual

- **A.** `actualbudget/actual` at `8d59f30cfb36`, committed 2026-10-17 (":chart_with_upwards_trend: Monte carlo investment phases (#9233)").
- **B.** README → https://actualbudget.org/docs/install/docker (saved as `docs-docker.html`).
  - Image: `actualbudget/actual-server:latest`.
  - The docs offer two procedures: "the docker-compose.yml file included in the actual repository" (`packages/sync-server/docker-compose.yml`; the root compose is a dev build), or the documented `docker run` command.
- **C.** No configuration; defaults.
- **D.** `docker compose -p t-actual pull` **failed** (exit 1). See fault below.
- **E.** Used the second documented procedure, unmodified apart from the name and data path: `docker run --pull=always --restart=unless-stopped -d -p 5006:5006 -v <testdir>:/data --name t-actual actualbudget/actual-server:latest`. It exited 0, but the container restart-loops: `Restarting (0)`, 8 restarts, exit code 0.
  - Cause: binding `::` fails with `EAFNOSUPPORT -97` on this IPv6-less host (ENV-4). Verified inside the image with a one-line node listen test; IPv4 `0.0.0.0` works.
- **F.** `http://localhost:5006/` and `/health` gave connection refused (`000`).
- **K.** Removed the container, image and data dir. Verified 0 containers, volumes and images.

```
Actual
- Step D: The repository's documented compose file is rejected by Docker Compose v5.6.0:
  `validating .../packages/sync-server/docker-compose.yml: services.actual_server.environment must be a mapping`.
  `environment:` is present but every entry under it is commented out, so it is null.
  Points to packages/sync-server/docker-compose.yml (environment: block); evidence/actual/pull.log, compose-version.txt.
- Step E: Server start failure is swallowed. The container logs `Listening on :::5006...` then exits with code 0 (9 times, no error text), although the listen failed (`EAFNOSUPPORT -97`, verified in-image).
  Points to container t-actual, evidence/actual/container.log.
  (The listen failure itself is ENV-4; the false "Listening" message and exit status 0 are app behavior.)
- Step F: http://localhost:5006/health → connection refused, consequence of step E; evidence/actual/http.txt.
- Step G: NOT REACHED - blocked by step E: server never stays up.
- Step H: NOT REACHED - blocked by step E.
- Step I: NOT REACHED - blocked by step E.
```

---

## 11. Gitea

- **A.** `go-gitea/gitea` at `31626de2a6cd`, committed 2026-10-17 ("enhance(repo): prefill new issue and pull request sidebars from query parameters (#39804)").
- **B.** The README names the "official image" (`gitea/gitea`). The official docs page https://docs.gitea.com/installation/install-with-docker (`docs-docker.html`) gives the basic SQLite compose with `docker.gitea.com/gitea:28.1.0`.
- **C.** Used the docs' basic compose verbatim (`evidence/gitea/docker-compose.used.yml`). Settings were made in the web installer with defaults (SQLite) plus a fictional admin `testuser` / `tester@example.test`.
- **D.** `docker compose -p t-gitea pull` exited 0. Prebuilt image.
- **E.** `up -d` exited 0. Container `gitea` Up.
- **F.** `http://localhost:3000/` returned 200 (installer). `/api/healthz` returned 200 `{"status":"pass"}`.
- **G.**
  - The install wizard completed and landed on the admin dashboard.
  - Login as `testuser` worked.
  - 30 pages returned 200: dashboard, explore repos/users/orgs, repo/create, repo/migrate, org/create, issues, pulls, milestones, notifications, profile, settings, site admin + config, swagger, licenses.
- **H.**
  - With generic placeholder data, Create Repository and Create Organization correctly showed validation errors.
  - With valid fictional data: created repo `testuser/fictional-test-repo` (auto-init README) and issue #1 "Fictional test issue". Both are confirmed via `/api/v1`.
  - `git clone http://localhost:3000/testuser/fictional-test-repo.git` succeeded (README.md present).
  - Update Profile submitted without error.
- **I.** 0 console errors, 0 HTTP ≥ 400 in the crawl.
  - Excluded: during the install wizard, the page's own polling of `/user/login` returned 404 and `ERR_CONNECTION_RESET` while Gitea restarted after install (by design, transient).
- **J.** No `[E]` or `[F]` log lines. Only `[W]`: "no mail service configured" (forgot-password with no SMTP), "issueReposQueryPattern not match", and "Unable to remove temporary authorized keys file ... no such file" (benign). Clean SIGTERM shutdown.
- **K.** `down -v --rmi all`; removed the data dir. Verified 0 containers, volumes, networks and images.
- **Safety skips:** Sign Out, "Delete Current Avatar".

```
Gitea
No faults observed in attempted steps
```

---

## 12. Supabase

- **A.** `supabase/supabase` at `7425d4930528`, committed 2026-10-17 ("Update humans.txt (#50972)").
- **B.** `docker/README.md` ("official Docker Compose setup") → https://supabase.com/docs/guides/self-hosting/docker "Manual installation" (`docs-docker.html`).
- **C.** As documented:
  - copied `docker/.` into a project dir (`t-supabase`) and ran `cp .env.example .env`;
  - `sh utils/generate-keys.sh --update-env` and `sh utils/add-new-auth-keys.sh --update-env`. `--update-env` is the scripts' documented equivalent of answering "yes" when not on a TTY. The second script itself enables the JWKS lines in `docker-compose.yml`, as the docs state;
  - set `DASHBOARD_PASSWORD=TestDashPass2026` (letters and digits, as required);
  - kept the default URLs (`http://localhost:8000`).
  
  Changed key names are in `env-changed-keys.txt`.
- **D.** `docker compose pull` exited 0 (1 min 13 s). Prebuilt images.
- **E.** `sh run.sh start` (= `up -d --wait`) **exit 1**: "container supabase-pooler is unhealthy".
  - `db`, `auth`, `rest`, `realtime`, `storage`, `imgproxy`, `meta`, `studio` and `envoy` were healthy.
  - `supabase-pooler` and `supabase-edge-functions` restart-looped, both for environment reasons (see below).
- **F.**
  - `http://localhost:8000/` returned 401 without auth and 307 with basic auth (supabase / password).
  - `/auth/v1/health` returned 200 (GoTrue v2.197.0) and `/storage/v1/status` returned 200.
  - `/rest/v1/` returned 403 "RBAC: access denied" with public keys and 200 with service_role. That is gateway policy, not a fault.
- **G.** Studio was crawled with basic auth: 25 pages returned 200 (table/SQL editors, schemas, auth users, storage, functions, realtime, advisors, observability, integrations, settings, account).
  - The crawl logged 114 "TypeError: Failed to fetch", but every one coincides with requests aborted by harness navigation (0 non-aborted failures, 0 HTTP ≥ 400).
  - Clean, no-click visits to 11 pages plus a standalone load of `/project/default` produced **zero** errors (`flow.txt`).
- **H.**
  - SQL editor: `select 1 as ok;` returned 1 row (`sql-result.png`).
  - Auth signup API with a fictional email returned **500** (see fault below).
- **I.** No app console or network errors on clean loads.
- **J.** Logs saved to `container.log` (13.6k lines). Not counted:
  - Studio's `AI_APICallError: Cannot connect to API: self-signed certificate in certificate chain` (Assistant; ENV-2).
  - Edge-functions jsr.io failure (ENV-2).
  - Realtime `[warning] MFA {Realtime.PromEx.Plugins.Tenant, :execute_tenant_metrics, []} encountered an error ... the table identifier does not refer to an existing ETS table` at startup (warning level only).
  - Realtime logs `[error] ... :sigterm received` on normal stop.
- **K.** `docker compose down -v --rmi all` (25 resources removed); removed the project dir and clone. Verified 0 containers, volumes and project networks, and the images were removed.

```
Supabase
- Step H: Email signup fails with the documented default configuration:
  `POST /auth/v1/signup` → HTTP 500 `{"code":500,"error_code":"unexpected_failure","msg":"Error sending confirmation email"}`.
  Auth log: `"error":"dial tcp: lookup supabase-mail on 127.0.0.11:53: no such host"`.
  `.env.example:187 SMTP_HOST=supabase-mail` refers to a service defined only in `dev/docker-compose.dev.yml`, not in `docker-compose.yml`.
  Points to docker/.env.example, container supabase-auth; evidence/supabase/auth-signup.txt.
- Step J: Shutdown error in container supabase-storage:
  `{"level":50,...,"type":"tus","error":{"message":"Connection terminated","stack":"Error: Connection terminated at /app/node_modules/pg/lib/client.js:694:17 ... at async PostgresPubSub.unsubscribe (/app/dist/internal/pubsub/postgres.js:104:7) at async Object.<anonymous> (/app/dist/http/routes/tus/index.js:182:5)"},"msg":"Failed to stop TUS lock notifier"}`.
  Points to evidence/supabase/container.log:321.
- Step E: ENVIRONMENT (ENV-5), not counted: supabase-pooler (supavisor:2.9.13) restart-loops with `/app/limits.sh: line 6: ulimit: open files: cannot modify limit: Operation not permitted` (it requests 100000; the host hard limit is 20000). This makes `sh run.sh start` exit 1 ("container supabase-pooler is unhealthy").
- Step E: ENVIRONMENT (ENV-2), not counted: supabase-edge-functions restart-loops with `worker boot error ... Import 'https://jsr.io/@panva/jose/meta.json' failed: ... invalid peer certificate: UnknownIssuer`.
```

---

## 13. Appsmith

- **A.** `appsmithorg/appsmith` at `fd78f12c57ca`, committed 2026-10-16 ("test(cypress): retry the CommunityIssues row read after Save (#42407)").
- **B.** README → https://docs.appsmith.com/getting-started/setup/installation-guides/docker ("Recommended"; `docs-docker.html`).
  - The docs' compose uses `appsmith-ee:<version>` and says to use `appsmith-ce` for the Community edition; omitting the tag means `latest`, which the docs call acceptable for local trials.
  - `deploy/docker/docker-compose.yml` is labelled "during development".
- **C.** The docs' compose verbatim with `index.docker.io/appsmith/appsmith-ce:latest` (`docker-compose.used.yml`). No env.
- **D.** `docker compose -p t-appsmith pull` exited 0. Prebuilt image. Compose warned that the docs' `version: "3"` attribute is obsolete.
- **E.** `up -d` exited 0. Container `appsmith` Up.
- **F.** `http://localhost/api/v1/health` returned 200 after ~70 s; the docs say "up to 5 minutes". `http://localhost/` returned 200 → `/setup/welcome`.
- **G.**
  - Admin setup with `tester@example.test` failed silently (fault below).
  - Admin setup with fictional `tester@example.com` succeeded and landed on `/applications`.
  - Login worked.
  - 20 pages returned 200: applications, and settings general/profile/email/branding/audit-logs/ai/user-settings/authentication/google-auth/provisioning/access-control/instance-settings/configuration/mcp-server/version.
- **H.** "Create new → Application" created "Untitled application 1" and the editor loaded (`editor.png`). The branding Submit and the email/AI settings forms submitted without app errors.
- **I.** Console and network errors are listed below.
  - Excluded as ENV-2: `GET /api/v1/app-templates` 400, `/app-templates/filters` 500, `/datasources/mocks` 500, and "Received error from cloud services (certificate_unknown) PKIX path building failed". The backend calls `https://cs.appsmith.com/...`, which fails TLS here.
  - Third-party `ERR_ABORTED` (pkg.betterbugs.io, t.appsmith.com telemetry) are also excluded.
- **J.** No non-ENV ERRORs except the email validation exception below. Clean supervisor shutdown (backend exit 143 on SIGTERM).
- **K.** `down -v --rmi all`; removed the stacks dir and clone. Verified 0 containers, volumes, networks and images.

```
Appsmith
- Step G: Admin setup fails silently.
  With email `tester@example.test`, the client accepts the form, but `POST /api/v1/users/super` fails server-side:
  `com.appsmith.server.exceptions.AppsmithException: Please enter a valid parameter email.` / "Super user creation failed after claiming slot. Releasing sentinel."
  The server then redirects to `/setup/welcome?error=Please%20enter%20a%20valid%20parameter%20email.`, but the page does not display the error; it just shows an empty step-1 form again (verified: error text not present; screenshot setup-error-not-shown.png).
  Points to backend UserSignupCEImpl.createAndSetupSuperUser / signupAndLogin, URL /setup/welcome; evidence/appsmith/container.log.
- Step I: On a fresh instance, `/settings/profile` requests `GET /api/v1/git/profile/default` → 404 (Not Found), logged as a console `Error ... {type: API...}`.
  Points to http://localhost/settings/profile; evidence/appsmith/playwright.json.
```

---

## 14. Payload

- **A.** `payloadcms/payload` at `d5b01b40d81c`, committed 2026-10-17 ("fix(tanstack-start): prevent subtree remounts on router refresh (#18806)").
- **B.** Payload is a framework monorepo. Its Docker setup is in the project templates: `templates/blank/README.md` "### Docker" says to copy `.env.example` → `.env`, then run `docker-compose up`.
  - The template's `docker-compose.yml` runs `node:20-alpine` with `corepack enable && corepack prepare pnpm@latest --activate && pnpm install && pnpm dev`, plus `mongo:latest`.
  - The other templates and examples follow the same pattern.
- **C.** `cp .env.example .env`. Set `DATABASE_URL=mongodb://mongo/payload-test`, as the compose file's comment instructs ("use 'mongo' as the hostname"), and a random `PAYLOAD_SECRET` (`env-used.txt`).
- **D.** `docker compose -p t-payload pull` exited 0. There is no image build; dependencies are installed at container start.
- **E.** `up -d` exited 0. `mongo` was Up; `payload` **Exited (1)**: `Internal Error: Error when performing the request to https://registry.npmjs.org/pnpm`. This is ENV-2: no HTTPS from inside containers.
- **F.** `http://localhost:3000/` returned connection refused (`000`).
- **K.** `down -v --rmi all`; removed `.env` and the clone. Verified 0 containers, volumes and images.
- **Note:** The template's `package.json` declares `workspace:*` dependencies. Whether `pnpm install` would succeed in the template directory with network access could not be tested here, and is not claimed either way.

```
Payload
- Step E: ENVIRONMENT (ENV-2), not counted as an app fault: container t-payload-payload-1 exits 1 during its documented startup command:
  `Internal Error: Error when performing the request to https://registry.npmjs.org/pnpm; ... at fetch (/usr/local/lib/node_modules/corepack/dist/lib/corepack.cjs:22089:11)`.
  Points to templates/blank/docker-compose.yml `command:`; evidence/payload/container.log.
- Step F: NOT REACHED - blocked by step E: nothing listens on :3000.
- Step G: NOT REACHED - blocked by step E.
- Step H: NOT REACHED - blocked by step E.
- Step I: NOT REACHED - blocked by step E.
- Step J: done (logs captured); only the step E error.
```

---

## 15. Zulip

- **A.** `zulip/zulip` at `e45560dfcfc4`, committed 2026-10-16 ("zilencer: Sync communities directory fields from uploaded realm data.").
  - The documented Docker repo `zulip/docker-zulip` is at `916aeb2348c9`, committed 2026-09-22.
- **B.** The README says "Self-host Zulip ... in [Docker](https://github.com/zulip/docker-zulip)". That repo's `docs/how-to/compose-getting-started.md` (+ `compose-settings.md`, `compose-ssl.md`, `compose-secrets.md`) is the official setup. The zulip repo's own Dockerfiles are CI/dev only.
- **C.** As documented:
  - `cp compose.override.yaml.example compose.override.yaml`; kept `SETTING_EXTERNAL_HOST: zulip.example.com` and added it to `/etc/hosts`, as the file instructs;
  - `SETTING_ZULIP_ADMINISTRATOR: tester@example.com`; uncommented `CERTIFICATES: "self-signed"` (`override-diff.txt`);
  - six random `ZULIP__*` secrets in `.env` (`env-keys.txt`);
  - `COMPOSE_PROJECT_NAME=t-zulip`.
- **D.** `docker compose pull` exited 0. Prebuilt images.
- **E.** `docker compose run --rm zulip app:init` **exit 1**: `Error response from daemon: failed to create task for container: ... runc create failed: unable to start container process: error during container init: error setting rlimits for ready process: error setting rlimit type 7: operation not permitted`.
  - Cause: `compose.yaml:99-102` sets `ulimits: nofile: soft 1000000 / hard 1048576`, above this host's hard limit of 20000 (ENV-5).
  - The dependency containers (postgresql, memcached, rabbitmq, redis) started fine.
- **K.** `down -v --rmi all`; restored `/etc/hosts`; removed both clones. Verified 0 containers, volumes, networks and images.

```
Zulip
- Step E: ENVIRONMENT (ENV-5), not counted as an app fault: `docker compose run --rm zulip app:init` → `error setting rlimits for ready process: error setting rlimit type 7: operation not permitted`.
  The zulip service requests nofile 1048576 (docker-zulip compose.yaml:99-102) and the host hard limit is 20000.
  Points to docker-zulip/compose.yaml, service `zulip`; evidence/zulip/init.log.
- Step F: NOT REACHED - blocked by step E: the zulip container never starts.
- Step G: NOT REACHED - blocked by step E.
- Step H: NOT REACHED - blocked by step E.
- Step I: NOT REACHED - blocked by step E.
- Step J: done (dependency logs captured; no errors).
```

---

## 16. Illa Builder

- **A.** `illacloud/illa-builder` at `a468660903e2`, committed 2025-05-14 ("Update README.md (#3837)").
- **B.** The README's self-host link (`https://docs.illacloud.com/self-hosted-deployment`) was **unreachable** from this session: the egress proxy returned 502 `connect_rejected` for `docs.illacloud.com:443`. The cause (host down or policy) can't be distinguished from here.
  - The repo's `CONTRIBUTING.md` "Utilizing Docker (Recommended)" names the official all-in-one image `illasoft/illa-builder:latest` with `docker run -d -p <PORT>:2022 illasoft/illa-builder:latest`, and credentials root / password.
  - The root `Dockerfile` needs a prebuilt `apps/builder/dist` and is not the documented path.
- **C.** No config; documented defaults. Port 8000 is one of the documented port choices.
- **D.** `docker pull illasoft/illa-builder:latest` exited 0 in 34 s.
- **E.** `docker run -d -p 8000:2022 --name t-illa illasoft/illa-builder:latest` exited 0. Container Up.
- **F.** `http://localhost:8000/` returned 301 to `/cloud` (200). The first 200 arrived after ~5 min of startup. No health endpoint is documented; the Dockerfile's commented `/status` returned 404 and was not judged.
- **G.** Login with the documented `root` / `password` → `/cloud/workspace/0/apps`. Pages visited: apps, resources, settings/account, and the onboarding guide `/build/0/guide`.
- **H.**
  - Account "Save Changes" submitted.
  - "Create new" creates an "Untitled app", which appears in the list (`flow-create.txt`).
- **I.** See below.
  - The guide's `400 GET .../resources/ILAfx4p1C7dZ/meta` has body `failed to connect to host=onboarding-postgres01v.illacloud.com ...`. That is an external, ILLA-hosted demo DB over raw TCP, so the 400 itself is ENV and is not counted.
- **J.** See below.
- **K.** Removed the container, image, anonymous volume and clone. Verified 0 containers, volumes and images.
- **Safety skips:** Log out, Undeploy.

```
Illa Builder
- Step I: Uncaught exception in the builder UI when the onboarding resource cannot be loaded: Playwright `pageerror`: `Object {"name":""}` on http://localhost:8000/build/0/guide. Reproduced on a clean visit; the failed request's error is not handled.
  Points to URL /build/0/guide; evidence/illa-builder/flow.txt.
- Step J: Startup ordering error in the all-in-one image:
  `[illa-builder-backend] {"level":"error",...,"caller":"illa-builder-backend/main.go:64","msg":"Error in startup, drive init failed.","stacktrace":"main.initDrive ... main.go:64"}` (container.log:274).
  It is logged before MinIO reports `API: http://...:9000` (container.log:362); the backend continues running.
  Points to container t-illa, process illa-builder-backend.
- Step J: Credentials and secrets are printed in plaintext at default startup (`ServerMode:debug`). Examples: `"connected with db","db":{...,"User":"illa_builder","Password":"<redacted>"...}`, `"connected with redis","redis":{...,"Password":"<redacted>"...}`, and `run by following config: &{... SecretKey:<redacted> ...}`.
  Points to container t-illa, processes illa-builder-backend / illa-supervisor-backend; evidence/illa-builder/container.log.
```

---

## 17. Medusa

- **A.** `medusajs/medusa` at `906433ac6b1d`, committed 2026-10-16 ("fix(@medusajs/medusa): accept case-insensitive order directions and report single-query errors on POST /store/search (#17313)").
  - The documented Docker starter `medusajs/dtc-starter` is at `f677db438f27`, committed 2026-10-09.
- **B.** No Docker files are in the medusa repo. The official docs page https://docs.medusajs.com/learn/installation/docker (`docs-docker.md`) documents the Docker path: clone `medusajs/dtc-starter`, then create `docker-compose.yml`, `start.sh`, `start-storefront.sh`, `Dockerfile` and `.dockerignore` with the given contents.
- **C.** Created those files verbatim from the docs (pnpm variants; copies saved in `evidence/medusa/`). Ran `cp apps/backend/.env.template apps/backend/.env` and `cp apps/storefront/.env.template apps/storefront/.env`.
  - Docs steps 6 (package.json `docker:*` scripts) and 7 (`medusa-config.ts` runtime options) were not applied. They only affect runtime, which was never reached, and `docker:up` is just `docker compose up --build -d`.
- **D.** `docker compose -p t-medusa up --build -d` **exit 1** at Dockerfile step `RUN npm install pnpm@10.11.1 -g`: `npm error code SELF_SIGNED_CERT_IN_CHAIN ... request to https://registry.npmjs.org/pnpm failed`. This is ENV-2.
- **K.** `down -v --rmi all`; removed the postgres/redis images and both clones. Verified 0 containers, volumes, networks and images.
  - Rule deviation: the build cache was cleared with `docker builder prune -af`, a global prune. The daemon had been started fresh for this session and held no other projects, so only this test's build cache existed. It is still disclosed as a deviation from the cleanup rule.

```
Medusa
- Step D: ENVIRONMENT (ENV-2), not counted as an app fault: image build fails at `RUN npm install pnpm@10.11.1 -g` with `npm error request to https://registry.npmjs.org/pnpm failed, reason: self-signed certificate in certificate chain`.
  Points to the documented Dockerfile (docs step 5), services medusa/storefront; evidence/medusa/build.log.
- Step E: NOT REACHED - blocked by step D: no image built.
- Step F: NOT REACHED - blocked by step D.
- Step G: NOT REACHED - blocked by step D.
- Step H: NOT REACHED - blocked by step D.
- Step I: NOT REACHED - blocked by step D.
- Step J: NOT REACHED - blocked by step D: no containers started.
```

---

## 18. Meilisearch

- **A.** `meilisearch/meilisearch` at `541dd6e94c5d`, committed 2026-10-15 ("Merge pull request #6681 from meilisearch/give-up-embeddings").
- **B.** Official docs "Install locally → Docker" (https://www.meilisearch.com/docs/resources/self_hosting/getting_started/install_locally, `docs-install.html`): `docker pull getmeili/meilisearch:v1.37`, then `docker run -it --rm -p 7700:7700 -e MEILI_ENV='development' -v $(pwd)/meili_data:/meili_data getmeili/meilisearch:v1.37`.
- **C.** As documented. `-d --name t-meili` replaced `-it` because there is no TTY; no other changes.
- **D.** `docker pull` exited 0. Prebuilt image.
- **E.** `docker run` exited 0. Container Up, "Server listening on http://0.0.0.0:7700", version 1.37.0, development mode.
- **F.**
  - `/health` returned 200 `{"status":"available"}`.
  - `/version` returned 200 (pkgVersion 1.37.0).
  - `/` returned 200 (mini-dashboard).
- **G.** The mini-dashboard loads. No signup or login exists (development mode without a master key, as documented).
- **H.**
  - API: added 2 fictional documents to index `books`; task 0 succeeded in 2.5 s.
  - Typo-tolerant search `q=fictonal` found "Fictional Test Book".
  - Invalid `limit:"bad"` returned the correct `invalid_search_limit` error.
  - Dashboard: searching "imaginary" displayed "Another Imaginary Volume".
- **I.** See below.
- **J.** Only WARN lines for 4xx requests (the multi-search 400s and the deliberate bad-limit request). No startup or shutdown errors.
- **K.** Stopped the container (`--rm`), removed the image, data dir and clone. Verified 0 containers, volumes and images.
- **Safety skips:** "Subscribe" (newsletter form), "Export Dataset".

```
Meilisearch
- Step I: Mini-dashboard sends an invalid request on page load:
  `POST http://localhost:7700/multi-search` → 400, console `MeiliSearchApiError: Invalid value at `.queries[0].indexUid`: `` is not a valid index uid. ...` (at l0.multiSearch, assets/index-dDwzADMz.js:50).
  Reproduced on a clean load with an existing index (`books`); search works afterwards.
  Points to URL http://localhost:7700/ (mini-dashboard), container t-meili WARN log for route=/multi-search; evidence/meilisearch/playwright.json.
```

---

## 19. Indico

- **A.** `indico/indico` at `ddcf9153dcf2`, committed 2026-10-15 ("Add more path checks in static site generation (#7801)").
- **B.** No supported Docker setup or documented official image:
  - no Dockerfile or compose file in the repo;
  - the README does not mention Docker;
  - `docs/source/installation/production/` covers only `deb` and `rpm` installs, and https://docs.getindico.io/en/stable/installation/ (`docs-install.html`) has 0 "docker" mentions;
  - container references in the docs are limited to a Docker-backed PostgreSQL fixture for the test suite (`development.rst:335-382`), a TeXLive image for PDF generation (`pdf_generation.rst`) and a `podman run` note in `settings.rst`.
  - A separate `indico/indico-containers` project exists on GitHub but is not named in this app's documentation, so it was not substituted.
- **K.** No Docker resources were created; nothing to clean.

```
Indico
- Step B: No supported Docker setup or documented official image for running Indico: docs/source/installation (production: deb/rpm only); evidence/indico/docs-install.html.
- Step C: NOT REACHED - blocked by step B.
- Step D: NOT REACHED - blocked by step B.
- Step E: NOT REACHED - blocked by step B.
- Step F: NOT REACHED - blocked by step B.
- Step G: NOT REACHED - blocked by step B.
- Step H: NOT REACHED - blocked by step B.
- Step I: NOT REACHED - blocked by step B.
- Step J: NOT REACHED - blocked by step B.
```

---

## 20. Jitsi Meet

- **A.** `jitsi/jitsi-meet` at `f4a1c17e45a7`, committed 2026-10-16 ("fix(prosody): apply max_number_outgoing_calls to transcription dials (#18011)").
  - The documented Docker setup `jitsi/docker-jitsi-meet` latest release tag `stable-11248` is at `276801c7fd42`, committed 2026-09-14.
- **B.** README → handbook "comprehensive Docker setup", https://jitsi.github.io/handbook/docs/devops-guide/devops-guide-docker "Quick start" (`docs-docker.html`). Images: `jitsi/web`, `jitsi/prosody`, `jitsi/jicofo`, `jitsi/jvb`.
- **C.** The documented one-liner `wget $(wget -q -O - https://api.github.com/repos/jitsi/docker-jitsi-meet/releases/latest | grep zip | cut -d\" -f4)` produced nothing: `api.github.com` returned 403 (shared-IP rate limit, ENV) and github.com archive downloads returned 403 via the session proxy.
  - Equivalent content was obtained by `git clone --depth 1 --branch stable-11248` of the latest release tag. This is not a master clone, which the docs warn against.
  - Then, as documented: `cp env.example .env`; `./gen-passwords.sh` (6 passwords set, `env-changed-keys.txt`); created the `~/.jitsi-meet-cfg/...` directories with the documented `chmod 777`.
- **D.** `docker compose -p t-jitsi pull` exited 0. Prebuilt images.
- **E.** `docker compose -p t-jitsi up -d` exited 0, and all four containers report `Up`. However, nginx inside `web` crash-loops about once per second: `[emerg] socket() [::]:8000 failed (97: Address family not supported by protocol)`, 52 times in the log. Prosody also logs `Failed to open server port 5280 on ::` and `5222 on ::` with the same error. This is ENV-4 (no IPv6).
- **F.** The documented `https://localhost:8443/` and `http://localhost:8000/` returned connection closed or refused (`000`). Playwright got `net::ERR_CONNECTION_CLOSED`.
- **K.** `down -v --rmi all`; removed `~/.jitsi-meet-cfg` and the downloads/clones. Verified 0 containers, volumes, networks and images.

```
Jitsi Meet
- Step E: ENVIRONMENT (ENV-4), not counted as an app fault: web container's nginx fails to bind `[::]:8000`:
  `[emerg] 242#242: socket() [::]:8000 failed (97: Address family not supported by protocol)` (crash-loop; container still shows "Up").
  Points to container t-jitsi-web-1, /run/web/config/nginx/site-confs/default; evidence/jitsi-meet/container.log.
- Step F: https://localhost:8443/ → ERR_CONNECTION_CLOSED, consequence of step E; evidence/jitsi-meet/http.txt.
- Step G: NOT REACHED - blocked by step E: web UI never served.
- Step H: NOT REACHED - blocked by step E.
- Step I: NOT REACHED - blocked by step E.
- Step J: done (logs captured).
```

---

## 21. OpenCloud

- **A.** `opencloud-eu/opencloud` at `6b50d8075250`, committed 2026-10-16 ("[tx] updated from transifex").
- **B.** README → https://docs.opencloud.eu/ → "Getting Started › Container › Docker" (v7.2; rendered text in `docs-docker.txt`): mkdir the bind-mount dirs, `docker pull opencloudeu/opencloud-rolling:latest`, `docker run --rm -it ... init`, then `docker run ... -p 9200:9200 ...`. Login admin / admin.
- **C.** Documented dirs `$HOME/opencloud/opencloud-{config,data}` and `IDM_ADMIN_PASSWORD=admin`.
  - The first `init` attempt, without `-it` because there is no TTY, hit a fault (below).
  - Re-run with a real pseudo-TTY (`script -c "docker run --rm -it ... init"`), pressing Enter at the prompt (its default, "no").
  - That failed with `Could not create config: open /etc/opencloud/opencloud.yaml: permission denied`: the image runs as uid 1000 and the documented `mkdir` as root creates root-owned dirs.
  - Applied the docs' own "Volume Permissions" page (`chown -R 1000:1000`, `chmod -R 0700`; `docs-volume-permissions.txt`). Init then succeeded ("generated OpenCloud Config").
- **D.** `docker pull` exited 0. Prebuilt image.
- **E.** Documented `docker run --name t-opencloud --rm -d -p 9200:9200 ... -e OC_INSECURE=true -e PROXY_HTTP_ADDR=0.0.0.0:9200 -e OC_URL=https://localhost:9200 ...` exited 0. Container Up.
- **F.** `https://localhost:9200/` returned 200. `/status.php` returned 200 (`"installed": true`, productversion 8.1.0).
- **G.**
  - OIDC sign-in page → admin / admin → `/files/spaces/personal/admin`.
  - The crawl reached 20 views: personal, projects, favorites, shares with-me/with-others/via-link, trash, search; plus clean visits to admin-settings/general and account/information.
- **H.** New → Folder "Fictional Test Folder" was created and is visible (`folder.png`).
- **I.** See below.
  - Excluded: `RangeError: Invalid language tag: en-US@posix at new Collator` appears only with this headless Chromium's default `en-US@posix` locale (environment); it is absent with locale `en-US`.
  - Also excluded: "Failed to load theme 'TypeError: Failed to fetch'" and `ERR_ABORTED` (navigation-induced).
- **J.** All server `level:error` lines are `context canceled` from client navigation aborts (e.g. "error happened in MultiHostReverseProxy" for `/app/list`); harness-induced, not counted. No startup or shutdown errors.
- **K.** Stopped the container (`--rm`), removed the image and the `$HOME/opencloud` dirs. Verified 0 containers, volumes and images.
- **Incident:** the runaway `init` filled the disk ("No space left on device"). The log was truncated, the container removed, and the space recovered.

```
OpenCloud
- Step C: `opencloud init` spins forever when stdin is not a TTY/EOF: it repeats "Do you want to configure OpenCloud with certificate checking disabled? This is not recommended for public instances! [yes | no = default]" in a tight loop, producing unbounded output (it filled ~17 GB of disk before being killed).
  Triggered by running the documented init without `-it`.
  Points to `opencloudeu/opencloud-rolling:latest init`; evidence/opencloud/init.head.log.
- Step C: The documented quick start fails as written when the host user is root: `docker run --rm -it ... init` → `Could not create config: open /etc/opencloud/opencloud.yaml: permission denied` (container uid 1000 vs. root-owned dirs from the documented `mkdir -p`). The fix is only on a separate "Volume Permissions" page that the Docker quick start does not reference.
  Points to docs "Getting Started › Container › Docker"; evidence/opencloud/init-tty.log.
- Step I: Every page logs a console error for `GET https://localhost:9200/graph/v1.0/users/<id>/photo/$value` → 404 (user has no avatar), on a clean run with locale en-US.
  Points to service `graph`; evidence/opencloud/flow-en-US.txt.
```

---

## 22. ntfy

- **A.** `binwiederhier/ntfy` at `0c12448b3ef7`, committed 2026-10-17 ("Merge pull request #2060 from binwiederhier/schema-compat-floor").
- **B.** `docs/install.md` "## Docker" (lines 357-424) names the official image `binwiederhier/ntfy`.
  - "Basic usage (no cache or additional config)": `docker run -p 80:80 -it binwiederhier/ntfy serve`.
  - The repo's `docker-compose.yml` and the docs' compose example carry a `user: UID:GID` placeholder to "replace with your own", so they are templates and were not run unedited.
- **C.** No config (documented basic usage).
- **D.** `docker pull binwiederhier/ntfy` exited 0. Prebuilt image.
- **E.** `docker run -d --name t-ntfy -p 80:80 binwiederhier/ntfy serve` (`-d` instead of `-it`; no TTY) exited 0. "Listening on :80[http], ntfy 2.31.1".
- **F.** `/v1/health` returned 200 `{"healthy":true,"role":"active"}`. `/` returned 200.
- **G.** Web app `/` and `/settings` loaded; 4 safe clicks. No signup or login (open server by default).
- **H.** Opened topic `/fictional-test-topic` in the UI, then published "Fictional test notification" via `POST /fictional-test-topic` (200). The message appeared in the UI (`flow.txt`, `topic.png`). It stayed local; no upstream is configured by default.
- **I.** 0 console errors, 0 HTTP ≥ 400. Two `ERR_ABORTED` asset fetches (navigation).
- **J.** No errors or warnings in the logs.
- **K.** Removed the container (with `-v`) and image. Verified 0 containers, volumes and images.

```
ntfy
No faults observed in attempted steps
```

---

## 23. draw.io desktop

- **A.** `jgraph/drawio-desktop` at `584c6de66a07`, committed 2026-10-16 ("Closes the window on Discard Changes when the draft cannot be deleted ... [jgraph/drawio#5806]").
- **B.** No Docker setup exists:
  - this is an Electron desktop app distributed as Windows/macOS/Linux binaries (README "Download built binaries from the releases section");
  - the tree has no Dockerfile or compose file;
  - `README.md`, `DEVELOPMENT.md` and `doc/` contain 0 mentions of Docker or container images;
  - the separately maintained `jgraph/docker-drawio` serves the web editor, not this desktop app, and is not named in this repo's documentation, so it was not substituted.
- **K.** No Docker resources were created; nothing to clean.
- **Note:** By design, a desktop GUI app has no Docker setup. It is recorded as a Step B fault only because the test protocol requires it.

```
draw.io desktop
- Step B: No supported Docker setup or documented official image: Electron desktop app; repo has no Dockerfile/compose and docs (README.md, DEVELOPMENT.md, doc/) never mention Docker.
- Step C: NOT REACHED - blocked by step B.
- Step D: NOT REACHED - blocked by step B.
- Step E: NOT REACHED - blocked by step B.
- Step F: NOT REACHED - blocked by step B.
- Step G: NOT REACHED - blocked by step B.
- Step H: NOT REACHED - blocked by step B.
- Step I: NOT REACHED - blocked by step B.
- Step J: NOT REACHED - blocked by step B.
```

---

## 24. Continue

- **A.** `continuedev/continue` at `5522c6f44ca0`, committed 2026-07-20 ("docs: remove Sign in link (login flow retired) (#13005)"). The README states: "The `continuedev/continue` repository is no longer actively maintained and is read-only for all users."
- **B.** No supported Docker setup or documented official image for running Continue, which is an IDE extension / CLI coding agent. The only Docker-related files are:
  - `manual-testing-sandbox/Dockerfile`, a sample `python:3.9-slim` "app.py" fixture inside the manual-testing sandbox, not the app;
  - `extensions/vscode/textmate-syntaxes/Dockerfile.tmLanguage`, a syntax grammar.
  
  Docs mentions are limited to `which docker` for MCP servers (`docs/troubleshooting.mdx:103`) and connecting Docker containers to a host Ollama (`docs/faqs.mdx:224-249`).
- **K.** No Docker resources were created; nothing to clean.
- **Note:** By design, an editor extension has no Docker setup. It is recorded as a Step B fault only because the test protocol requires it.

```
Continue
- Step B: No supported Docker setup or documented official image: no app Dockerfile/compose; the only Dockerfile (manual-testing-sandbox/Dockerfile) is a sample test fixture; docs mention Docker only for MCP/Ollama connectivity (docs/faqs.mdx:224).
- Step C: NOT REACHED - blocked by step B.
- Step D: NOT REACHED - blocked by step B.
- Step E: NOT REACHED - blocked by step B.
- Step F: NOT REACHED - blocked by step B.
- Step G: NOT REACHED - blocked by step B.
- Step H: NOT REACHED - blocked by step B.
- Step I: NOT REACHED - blocked by step B.
- Step J: NOT REACHED - blocked by step B.
```

---

## Final summary table

The fault count includes only observed app faults. ENVIRONMENT blocks (ENV-1…5) are listed separately and are not counted. "First failed step" is the earliest A–K step with any failure, app or ENV.

| # | App | Observed app faults | First failed step | Environment blocks (not counted) |
|---|-----|--------------------:|-------------------|----------------------------------|
| 1 | ArchiveBox | 0 | None observed | — |
| 2 | Authelia | 3 | E (Traefik cannot parse setup.sh-generated cert) | — |
| 3 | Mixpost | 0 | None observed | — |
| 4 | Alexandrie | 4 | G (diagram editor embed 403) | api.github.com rate limit (excluded) |
| 5 | memos | 0 | None observed | — |
| 6 | OpenCut | 1 | B (no Docker setup) | — |
| 7 | open-notebook | 0 | None observed | AI provider unavailable (limitation) |
| 8 | Sendportal | 1 | B (no Docker setup) | — |
| 9 | Ghost | 0 | E — ENV-4 (`::` bind, Code -97); G–I not reached | ENV-4 |
| 10 | Actual | 2 | D (compose `environment must be a mapping`) | ENV-4 at E |
| 11 | Gitea | 0 | None observed | — |
| 12 | Supabase | 2 | E — ENV-5/ENV-2 (pooler, edge-functions); first app fault H | ENV-5, ENV-2 |
| 13 | Appsmith | 2 | G (setup fails silently on server-rejected email) | ENV-2 (cloud services) |
| 14 | Payload | 0 | E — ENV-2 (`pnpm` fetch in container); F–I not reached | ENV-2 |
| 15 | Zulip | 0 | E — ENV-5 (rlimit nofile 1048576); F–I not reached | ENV-5 |
| 16 | Illa Builder | 3 | I (uncaught page error on /build/0/guide) | external onboarding DB / docs host unreachable |
| 17 | Medusa | 0 | D — ENV-2 (`npm install pnpm` in build); E–J not reached | ENV-2 |
| 18 | Meilisearch | 1 | I (dashboard multi-search with empty indexUid → 400) | — |
| 19 | Indico | 1 | B (no Docker setup) | — |
| 20 | Jitsi Meet | 0 | E — ENV-4 (nginx `[::]:8000` errno 97); G–I not reached | ENV-4 |
| 21 | OpenCloud | 3 | C (init infinite prompt loop on non-TTY; documented quick start fails as root) | — |
| 22 | ntfy | 0 | None observed | — |
| 23 | draw.io desktop | 1 | B (no Docker setup; desktop app) | — |
| 24 | Continue | 1 | B (no Docker setup; IDE extension) | — |

**Totals:** 25 observed app faults across 13 apps. 6 apps ran their attempted steps with none observed (ArchiveBox, Mixpost, memos, open-notebook, Gitea, ntfy). 5 apps were blocked by environment limits before UI testing (Ghost, Payload, Zulip, Medusa, Jitsi Meet). No app is "Not run": every app was at least cloned and had its Docker setup located or ruled out.

### Apps that could change result on a less restricted host
These apps were blocked only by this sandbox. They should be re-run on a host with IPv6 enabled in the kernel, a `nofile` hard limit ≥ 1048576, and containers able to make outbound HTTPS calls:
- Ghost (ENV-4)
- Actual, runtime part (ENV-4)
- Jitsi Meet (ENV-4)
- Zulip (ENV-5)
- Supabase pooler (ENV-5) and edge-functions (ENV-2)
- Payload (ENV-2)
- Medusa (ENV-2)

### Deviations from the test rules (disclosed)
- **Medusa cleanup:** `docker builder prune -af` (global build-cache prune) was run once. The daemon was started fresh for this session with no other workloads, so it held only this test's cache.
- **Registry mirror (ENV-1):** pulls went through daemon-level `mirror.gcr.io`, a pull-through mirror of Docker Hub. No image references were changed.
- **Non-interactive substitutions:** `-d` replaced `-it` in documented `docker run` commands (memos, Meilisearch, ntfy, OpenCloud). Interactive scripts were fed answers via stdin or a pseudo-TTY (Authelia `setup.sh`, OpenCloud `init`).
- **Tester-induced errors**, excluded from counts:
  - Authelia `readonly database` (DB deleted under a running container; re-run clean).
  - Navigation-aborted requests (`ERR_ABORTED`, "Failed to fetch", server `context canceled`).
- **Harness fixes during the run:**
  - safety regex word boundaries;
  - Chromium proxy flags so loopback bypasses the session proxy (the Mixpost "405" came from the proxy, not the app);
  - post-login re-crawl.
  
  Affected crawls were re-run.

### Retained evidence
`evidence/<app>/` holds commit info, docs snapshots, pull/build/up/run logs, `http.txt`, `playwright.json`, screenshots and `container.log` for every app. The harness scripts are in `harness/`. Secrets printed by Illa Builder were redacted in its retained log.
