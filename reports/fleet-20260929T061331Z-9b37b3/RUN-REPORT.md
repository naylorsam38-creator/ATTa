# Fleet run fleet-20260929T061331Z-9b37b3

Apps requested 29, reported 29. NOT_STARTED 0, START_FAILED 8, TIMEOUT 3, INCONCLUSIVE 0, FAILED 11, QUALIFIED 7, UNVERIFIED 0.

Deployment revision: code:e46676963529e1afb54f957b -> code:e46676963529e1afb54f957b (stable)

Concurrency: mode parallel, cap 29, peak 29 apps at once; waited for machine room 0 time(s); sequential second pass 0 app(s)

Timeouts: billionmail, clearflask, polar

## Not qualified, by class

| State | Stage | Code | Apps |
|---|---|---|---|
| START_FAILED | 2 APP_UP | RUNNER_EXHAUSTED | botpress, ghost, langflow, open-design, opencart, opencloud, plane, umami |
| FAILED | 6 CLEAN | JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL | colanode, medplum, supabase, traefik |
| TIMEOUT | 2 APP_UP | RUNNER_EXHAUSTED | billionmail, clearflask, polar |
| FAILED | 6 CLEAN | BROWSER_HTTP | appsmith |
| FAILED | 6 CLEAN | CONSOLE_ERRORS:151 | super-productivity |
| FAILED | 6 CLEAN | CONSOLE_ERRORS:19 | memos |
| FAILED | 6 CLEAN | CONSOLE_ERRORS:4 | flarum |
| FAILED | 6 CLEAN | JOURNEY_NOT_AUTHORED:ACCOUNT_NO_STRATEGY | apache-airflow |
| FAILED | 3 PROXY_UP | NOT_HTML | coolify |
| FAILED | 4 SKIN | NO_LINK | krayin-crm |

## Failure report

Each app stopped at its first failed stage. The original failure is kept even when a bounded repair
later resolved it. Nothing here is marked VERIFIED unless its state column says QUALIFIED.

### apache-airflow: FAILED

- **Failed stage:** 6 CLEAN `JOURNEY_NOT_AUTHORED:ACCOUNT_NO_STRATEGY`
- **Exact location:** 6 CLEAN (part published image apache/airflow:latest, named in the app's own files, attempt 1, rule cli.needs_command)
- **Error/output:** `login required; no account class worked: signup: no sign-up or first-run form found`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/apache-airflow/run-1790662411393-apache-airflow-9b24d976-a1-f1.json`, container log `/srv/app-builder/state/runner/failures/apache-airflow/run-1790662411393-apache-airflow-9b24d976-a1-f1.container.log`, browser `/srv/app-builder/state/runner/evidence/apache-airflow/run-1790662411393-apache-airflow-9b24d976-a1`
- **Likely classification:** JOURNEY_NOT_AUTHORED
- **What needs to change:** author a login/smoke journey for this app (account strategy)
- **What to retest:** app stays up; re-run 6 CLEAN; re-check 1 INSTALLED, 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): author a login/smoke journey for this app (account strategy)
- **Result:** FAILED (repair NO_MATCHING_REPAIR)

### appsmith: FAILED

- **Failed stage:** 6 CLEAN `BROWSER_HTTP`
- **Exact location:** 6 CLEAN
- **Error/output:** `502`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/appsmith/run-1790662411395-appsmith-051c24b3-a1-f1.json`, container log `/srv/app-builder/state/runner/failures/appsmith/run-1790662411395-appsmith-051c24b3-a1-f1.container.log`, browser `/srv/app-builder/state/runner/evidence/appsmith/run-1790662411395-appsmith-051c24b3-a1-w1`
- **Likely classification:** APP_SERVER_ERROR
- **What needs to change:** the real browser got an error status: read the app log excerpt
- **What to retest:** restart the app with the change, then re-run 6 CLEAN; re-check 1 INSTALLED, 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): the real browser got an error status: read the app log excerpt
- **Result:** FAILED (repair NO_MATCHING_REPAIR)

### billionmail: TIMEOUT

- **Failed stage:** 2 APP_UP `RUNNER_EXHAUSTED`
- **Exact location:** 2 APP_UP (part the app's own Dockerfile Dockerfiles/dovecot/Dockerfile (builds from source), attempt 8, rule app.still_starting)
- **Error/output:** `8 attempts over 5 ways of running it; last: the app's own Dockerfile Dockerfiles/dovecot/Dockerfile (builds from source) -> app.still_starting
#1 published image billionmail/core:4.9.3, named in the app's own files: app.still_starting (wait_longer)
#2 published image billionmail/core:4.9.3, named in the app's own files: app.still_starting (wait_longer)
#3 the app's own Dockerfile Dockerfiles/core/`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/billionmail/run-1790662411397-billionmail-37fa19d0-a1-f1.json`
- **Likely classification:** START_TIMEOUT
- **What needs to change:** the app never answered within the boot timeout
- **What to retest:** restart the app with the change, then re-run 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): the app never answered within the boot timeout
- **Result:** TIMEOUT (repair NO_MATCHING_REPAIR)

### botpress: START_FAILED

- **Failed stage:** 2 APP_UP `RUNNER_EXHAUSTED`
- **Exact location:** 2 APP_UP (part the app's own Dockerfile integrations/chat/Dockerfile (builds from source), attempt 3, rule build.failed)
- **Error/output:** `3 attempts over 2 ways of running it; last: the app's own Dockerfile integrations/chat/Dockerfile (builds from source) -> build.failed
#1 the app's own Dockerfile Dockerfile (builds from source): app.still_starting (wait_longer)
#2 the app's own Dockerfile Dockerfile (builds from source): app.still_starting (wait_longer)
#3 the app's own Dockerfile integrations/chat/Dockerfile (builds from source)`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/botpress/run-1790662411398-botpress-ad788b63-a1-f1.json`
- **Likely classification:** BUILD_FAILED
- **What needs to change:** fix the image build (the failing build step is in the log excerpt)
- **What to retest:** restart the app with the change, then re-run 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): fix the image build (the failing build step is in the log excerpt)
- **Result:** START_FAILED (repair NO_MATCHING_REPAIR)

### clearflask: TIMEOUT

- **Failed stage:** 2 APP_UP `RUNNER_EXHAUSTED`
- **Exact location:** 2 APP_UP (part published image ghcr.io/clearflask/clearflask-server:latest, named in the app's own files, attempt 3, rule app.stalled)
- **Error/output:** `3 attempts over 2 ways of running it; last: published image ghcr.io/clearflask/clearflask-server:latest, named in the app's own files -> app.stalled
#1 published image ghcr.io/clearflask/clearflask-connect:latest, named in the app's own files: container.missing_path (add_volume)
#2 published image ghcr.io/clearflask/clearflask-connect:latest, named in the app's own files: container.missing_path (a`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/clearflask/run-1790662411396-clearflask-bb982647-a1-f1.json`
- **Likely classification:** START_TIMEOUT
- **What needs to change:** the app stopped printing and never answered: read the log excerpt
- **What to retest:** restart the app with the change, then re-run 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): the app stopped printing and never answered: read the log excerpt
- **Result:** TIMEOUT (repair NO_MATCHING_REPAIR)

### colanode: FAILED

- **Failed stage:** 6 CLEAN `JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL`
- **Exact location:** 6 CLEAN (part published image ghcr.io/colanode/server:latest, named in the app's own files, attempt 3, rule container.exited)
- **Error/output:** `no login form and no internal link that changes the page`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/colanode/run-1790662411401-colanode-48ff93cd-a1-f1.json`, container log `/srv/app-builder/state/runner/failures/colanode/run-1790662411401-colanode-48ff93cd-a1-f1.container.log`, browser `/srv/app-builder/state/runner/evidence/colanode/run-1790662411401-colanode-48ff93cd-a1`
- **Likely classification:** JOURNEY_NOT_AUTHORED
- **What needs to change:** author a login/smoke journey for this app (account strategy)
- **What to retest:** app stays up; re-run 6 CLEAN; re-check 1 INSTALLED, 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): author a login/smoke journey for this app (account strategy)
- **Result:** FAILED (repair NO_MATCHING_REPAIR)

### coolify: FAILED

- **Failed stage:** 3 PROXY_UP `NOT_HTML`
- **Exact location:** 3 PROXY_UP (part image coollabsio/coolify-helper from the owner's Docker Hub namespace, attempt 4, rule app.still_starting)
- **Error/output:** ``
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/coolify/run-1790662411408-coolify-c5ddc6ee-a1-f1.json`, container log `/srv/app-builder/state/runner/failures/coolify/run-1790662411408-coolify-c5ddc6ee-a1-f1.container.log`
- **Likely classification:** NO_WEB_UI
- **What needs to change:** the page is not HTML: check it as a service, or open its web UI path
- **What to retest:** restart the app with the change, then re-run 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED, 2 APP_UP (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): the page is not HTML: check it as a service, or open its web UI path
- **Result:** FAILED (repair NO_MATCHING_REPAIR)

### flarum: FAILED

- **Failed stage:** 6 CLEAN `CONSOLE_ERRORS:4`
- **Exact location:** 6 CLEAN
- **Error/output:** `["Access to font at 'http://127.0.0.1:20000/assets/fonts/fa-regular-400.woff2' from origin 'http://127.0.0.1:8426' has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present on the requested resource. (http://127.0.0.1:8426/)", "Access to font at 'http://127.0.0.1:20000/assets/fonts/fa-solid-900.woff2' from origin 'http://127.0.0.1:8426' has been blocked by CORS policy: No`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/flarum/run-1790662411410-flarum-14195f66-a1-f1.json`, container log `/srv/app-builder/state/runner/failures/flarum/run-1790662411410-flarum-14195f66-a1-f1.container.log`, browser `/srv/app-builder/state/runner/evidence/flarum/run-1790662411410-flarum-14195f66-a1`
- **Likely classification:** HUMAN_REVIEW
- **What needs to change:** evidence insufficient: no classification covers this failure code; read the evidence
- **What to retest:** app stays up; re-run 6 CLEAN; re-check 1 INSTALLED, 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): evidence insufficient: no classification covers this failure code; read the evidence
- **Result:** FAILED (repair NO_MATCHING_REPAIR)

### ghost: START_FAILED

- **Failed stage:** 2 APP_UP `RUNNER_EXHAUSTED`
- **Exact location:** 2 APP_UP (part the app's own Dockerfile docker/tb-cli/Dockerfile (builds from source), attempt 8, rule build.failed)
- **Error/output:** `8 attempts over 7 ways of running it; last: the app's own Dockerfile docker/tb-cli/Dockerfile (builds from source) -> build.failed
#1 published image ghcr.io/tryghost/ghost:latest, named in the app's own files: app.needs_database (next_part)
#2 published image ghcr.io/tryghost/ghost-devcontainer:latest, named in the app's own files: container.exited (next_part)
#3 the app's own Dockerfile docker/d`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/ghost/run-1790662411408-ghost-98feb11a-a1-f1.json`
- **Likely classification:** BUILD_FAILED
- **What needs to change:** fix the image build (the failing build step is in the log excerpt)
- **What to retest:** restart the app with the change, then re-run 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): fix the image build (the failing build step is in the log excerpt)
- **Result:** START_FAILED (repair NO_MATCHING_REPAIR)

### krayin-crm: FAILED

- **Failed stage:** 4 SKIN `NO_LINK`
- **Exact location:** 4 SKIN
- **Error/output:** ``
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/krayin-crm/run-1790662411413-krayin-crm-b8ebcb55-a1-f1.json`, container log `/srv/app-builder/state/runner/failures/krayin-crm/run-1790662411413-krayin-crm-b8ebcb55-a1-f1.container.log`
- **Likely classification:** SKIN_INJECTION
- **What needs to change:** the skin <link> is not in the page the proxy serves (injection did not happen on this page)
- **What to retest:** app stays up; re-run 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED, 2 APP_UP, 3 PROXY_UP (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): the skin <link> is not in the page the proxy serves (injection did not happen on this page)
- **Result:** FAILED (repair NO_MATCHING_REPAIR)

### langflow: START_FAILED

- **Failed stage:** 2 APP_UP `RUNNER_EXHAUSTED`
- **Exact location:** 2 APP_UP (part compose file from the app's docs (https://docs.langflow.org/deployment-docker), attempt 5, rule unrecognised)
- **Error/output:** `5 attempts over 4 ways of running it; last: compose file from the app's docs (https://docs.langflow.org/deployment-docker) -> unrecognised
#1 published image langflowai/langflow:latest, named in the app's own files: container.exited (next_part)
#2 published image langflowai/langflow-backend:latest, named in the app's own files: container.exited (next_part)
#3 published image langflowai/langflow-fr`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/langflow/run-1790662411427-langflow-2aa2dd01-a1-f1.json`
- **Likely classification:** HUMAN_REVIEW
- **What needs to change:** evidence insufficient: no rule recognises this failure; read the log excerpt
- **What to retest:** restart the app with the change, then re-run 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): evidence insufficient: no rule recognises this failure; read the log excerpt
- **Result:** START_FAILED (repair NO_MATCHING_REPAIR)

### medplum: FAILED

- **Failed stage:** 6 CLEAN `JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL`
- **Exact location:** 6 CLEAN (part published image medplum/medplum-server:latest, named in the app's own files, attempt 1, rule container.exited)
- **Error/output:** `no login form and no internal link that changes the page`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/medplum/run-1790662411427-medplum-6560f4b0-a1-f1.json`, container log `/srv/app-builder/state/runner/failures/medplum/run-1790662411427-medplum-6560f4b0-a1-f1.container.log`, browser `/srv/app-builder/state/runner/evidence/medplum/run-1790662411427-medplum-6560f4b0-a1`
- **Likely classification:** JOURNEY_NOT_AUTHORED
- **What needs to change:** author a login/smoke journey for this app (account strategy)
- **What to retest:** app stays up; re-run 6 CLEAN; re-check 1 INSTALLED, 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): author a login/smoke journey for this app (account strategy)
- **Result:** FAILED (repair NO_MATCHING_REPAIR)

### memos: FAILED

- **Failed stage:** 6 CLEAN `CONSOLE_ERRORS:19`
- **Exact location:** 6 CLEAN
- **Error/output:** `["Failed to load resource: net::ERR_NETWORK_CHANGED (http://127.0.0.1:8185/_cs/memos/skin-002.1.css)", "Failed to load resource: net::ERR_NETWORK_CHANGED (http://127.0.0.1:8185/assets/dist-Buz-No-8.js)", "Failed to load resource: net::ERR_NETWORK_CHANGED (http://127.0.0.1:8185/assets/button-s6wbZU6S.js)", "Failed to load resource: net::ERR_NETWORK_CHANGED (http://127.0.0.1:8185/assets/react-dom-De`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/memos/run-1790662411421-memos-e2b8ce1d-a1-f1.json`, container log `/srv/app-builder/state/runner/failures/memos/run-1790662411421-memos-e2b8ce1d-a1-f1.container.log`, browser `/srv/app-builder/state/runner/evidence/memos/run-1790662411421-memos-e2b8ce1d-a1`
- **Likely classification:** HUMAN_REVIEW
- **What needs to change:** evidence insufficient: no classification covers this failure code; read the evidence
- **What to retest:** app stays up; re-run 6 CLEAN; re-check 1 INSTALLED, 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): evidence insufficient: no classification covers this failure code; read the evidence
- **Result:** FAILED (repair NO_MATCHING_REPAIR)

### open-design: START_FAILED

- **Failed stage:** 2 APP_UP `RUNNER_EXHAUSTED`
- **Exact location:** 2 APP_UP (part the app's own Dockerfile deploy/Dockerfile (builds from source), attempt 1, rule build.failed)
- **Error/output:** `1 attempts over 1 ways of running it; last: the app's own Dockerfile deploy/Dockerfile (builds from source) -> build.failed
#1 the app's own Dockerfile deploy/Dockerfile (builds from source): build.failed (next_part)`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/open-design/run-1790662411429-open-design-5a0fee65-a1-f1.json`
- **Likely classification:** BUILD_FAILED
- **What needs to change:** fix the image build (the failing build step is in the log excerpt)
- **What to retest:** restart the app with the change, then re-run 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): fix the image build (the failing build step is in the log excerpt)
- **Result:** START_FAILED (repair NO_MATCHING_REPAIR)

### opencart: START_FAILED

- **Failed stage:** 2 APP_UP `RUNNER_EXHAUSTED`
- **Exact location:** 2 APP_UP (part the app's own Dockerfile docker/apache/Dockerfile (builds from source), attempt 8, rule build.failed)
- **Error/output:** `8 attempts over 6 ways of running it; last: the app's own Dockerfile docker/apache/Dockerfile (builds from source) -> build.failed
#1 compose file from the app's docs (https://docs.docker.com/compose/profiles/): image.missing (next_part)
#2 compose file from the app's docs (https://docs.docker.com/compose/profiles/): image.missing (next_part)
#3 compose file from the app's docs (https://docs.docke`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/opencart/run-1790662411431-opencart-0d28f42e-a1-f1.json`
- **Likely classification:** BUILD_FAILED
- **What needs to change:** fix the image build (the failing build step is in the log excerpt)
- **What to retest:** restart the app with the change, then re-run 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): fix the image build (the failing build step is in the log excerpt)
- **Result:** START_FAILED (repair NO_MATCHING_REPAIR)

### opencloud: START_FAILED

- **Failed stage:** 2 APP_UP `RUNNER_EXHAUSTED`
- **Exact location:** 2 APP_UP (part the app's own Dockerfile Dockerfile (builds from source), attempt 1, rule container.exited)
- **Error/output:** `1 attempts over 1 ways of running it; last: the app's own Dockerfile Dockerfile (builds from source) -> container.exited
#1 the app's own Dockerfile Dockerfile (builds from source): container.exited (next_part)`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/opencloud/run-1790662411425-opencloud-0418f34a-a1-f1.json`
- **Likely classification:** CONTAINER_EXITED
- **What needs to change:** read the container log excerpt: the process exits on start
- **What to retest:** restart the app with the change, then re-run 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): read the container log excerpt: the process exits on start
- **Result:** START_FAILED (repair NO_MATCHING_REPAIR)

### plane: START_FAILED

- **Failed stage:** 2 APP_UP `RUNNER_EXHAUSTED`
- **Exact location:** 2 APP_UP (part image makeplane/plane-live from the owner's Docker Hub namespace, attempt 8, rule container.exited)
- **Error/output:** `8 attempts over 8 ways of running it; last: image makeplane/plane-live from the owner's Docker Hub namespace -> container.exited
#1 published image makeplane/plane-backend:latest, named in the app's own files: container.exited (next_part)
#2 published image makeplane/plane-frontend:latest, named in the app's own files: container.exited (next_part)
#3 published image makeplane/plane-space:latest, n`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/plane/run-1790662411432-plane-32342756-a1-f1.json`
- **Likely classification:** CONTAINER_EXITED
- **What needs to change:** read the container log excerpt: the process exits on start
- **What to retest:** restart the app with the change, then re-run 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): read the container log excerpt: the process exits on start
- **Result:** START_FAILED (repair NO_MATCHING_REPAIR)

### polar: TIMEOUT

- **Failed stage:** 2 APP_UP `RUNNER_EXHAUSTED`
- **Exact location:** 2 APP_UP (part the app's own Dockerfile infra/tailscale/Dockerfile (builds from source), attempt 7, rule app.still_starting)
- **Error/output:** `7 attempts over 3 ways of running it; last: the app's own Dockerfile infra/tailscale/Dockerfile (builds from source) -> app.still_starting
#1 the app's own Dockerfile server/Dockerfile (builds from source): build.failed (next_part)
#2 the app's own Dockerfile infra/pgbouncer/Dockerfile (builds from source): app.env_required (set_env)
#3 the app's own Dockerfile infra/pgbouncer/Dockerfile (builds f`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/polar/run-1790662411434-polar-12b7a70d-a1-f1.json`
- **Likely classification:** START_TIMEOUT
- **What needs to change:** the app never answered within the boot timeout
- **What to retest:** restart the app with the change, then re-run 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): the app never answered within the boot timeout
- **Result:** TIMEOUT (repair NO_MATCHING_REPAIR)

### supabase: FAILED

- **Failed stage:** 6 CLEAN `JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL`
- **Exact location:** 6 CLEAN (part published image supabase/studio:2026.09.07-sha-7996410, named in the app's own files, attempt 1, rule app.stalled)
- **Error/output:** `no login form and no internal link that changes the page`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/supabase/run-1790662411444-supabase-7e51f756-a1-f1.json`, container log `/srv/app-builder/state/runner/failures/supabase/run-1790662411444-supabase-7e51f756-a1-f1.container.log`, browser `/srv/app-builder/state/runner/evidence/supabase/run-1790662411444-supabase-7e51f756-a1`
- **Likely classification:** JOURNEY_NOT_AUTHORED
- **What needs to change:** author a login/smoke journey for this app (account strategy)
- **What to retest:** app stays up; re-run 6 CLEAN; re-check 1 INSTALLED, 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): author a login/smoke journey for this app (account strategy)
- **Result:** FAILED (repair NO_MATCHING_REPAIR)

### super-productivity: FAILED

- **Failed stage:** 6 CLEAN `CONSOLE_ERRORS:151`
- **Exact location:** 6 CLEAN
- **Error/output:** `["Failed to load resource: net::ERR_NETWORK_CHANGED (http://127.0.0.1:8815/chunk-MOUDD2QE.js)", "Failed to load resource: net::ERR_NETWORK_CHANGED (http://127.0.0.1:8815/chunk-H4PCEQEH.js)", "Failed to load resource: net::ERR_NETWORK_CHANGED (http://127.0.0.1:8815/chunk-T453MWBS.js)", "Failed to load resource: net::ERR_NETWORK_CHANGED (http://127.0.0.1:8815/chunk-SQRNYO5K.js)", "Failed to load res`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/super-productivity/run-1790662411454-super-productivity-6adfb2c6-a1-f1.json`, container log `/srv/app-builder/state/runner/failures/super-productivity/run-1790662411454-super-productivity-6adfb2c6-a1-f1.container.log`, browser `/srv/app-builder/state/runner/evidence/super-productivity/run-1790662411454-super-productivity-6adfb2c6-a1`
- **Likely classification:** HUMAN_REVIEW
- **What needs to change:** evidence insufficient: no classification covers this failure code; read the evidence
- **What to retest:** app stays up; re-run 6 CLEAN; re-check 1 INSTALLED, 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): evidence insufficient: no classification covers this failure code; read the evidence
- **Result:** FAILED (repair NO_MATCHING_REPAIR)

### traefik: FAILED

- **Failed stage:** 6 CLEAN `JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL`
- **Exact location:** 6 CLEAN (part image traefik/traefikee from the owner's Docker Hub namespace, attempt 4, rule cli.needs_command)
- **Error/output:** `no login form and no internal link that changes the page`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/traefik/run-1790662411453-traefik-ac582852-a1-f1.json`, container log `/srv/app-builder/state/runner/failures/traefik/run-1790662411453-traefik-ac582852-a1-f1.container.log`, browser `/srv/app-builder/state/runner/evidence/traefik/run-1790662411453-traefik-ac582852-a1`
- **Likely classification:** JOURNEY_NOT_AUTHORED
- **What needs to change:** author a login/smoke journey for this app (account strategy)
- **What to retest:** app stays up; re-run 6 CLEAN; re-check 1 INSTALLED, 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): author a login/smoke journey for this app (account strategy)
- **Result:** FAILED (repair NO_MATCHING_REPAIR)

### umami: START_FAILED

- **Failed stage:** 2 APP_UP `RUNNER_EXHAUSTED`
- **Exact location:** 2 APP_UP (part the app's own Dockerfile Dockerfile (builds from source), attempt 4, rule container.exited)
- **Error/output:** `4 attempts over 4 ways of running it; last: the app's own Dockerfile Dockerfile (builds from source) -> container.exited
#1 published image umamisoftware/umami:latest, named in the app's own files: container.exited (next_part)
#2 published image docker.umami.is/umami-software/umami:latest, named in the app's own files: container.exited (next_part)
#3 published image ghcr.io/umami-software/umami:la`
- **Logs/evidence:** record `/srv/app-builder/state/runner/failures/umami/run-1790662411461-umami-4e629c4e-a1-f1.json`
- **Likely classification:** CONTAINER_EXITED
- **What needs to change:** read the container log excerpt: the process exits on start
- **What to retest:** restart the app with the change, then re-run 2 APP_UP, 3 PROXY_UP, 4 SKIN, 5 HOOK, 6 CLEAN; re-check 1 INSTALLED (regression)
- **Repair 1:** none -> NO_MATCHING_REPAIR; next: next repair decision needed (no evidence-based fix in ATTa's repair tiers): read the container log excerpt: the process exits on start
- **Result:** START_FAILED (repair NO_MATCHING_REPAIR)

## Every app

| App | State | TCP | HTTP | Healthy | Qualified | Verified | Stage | Code | Attempt | Run |
|---|---|---|---|---|---|---|---|---|---|---|
| agno | QUALIFIED | n/a | n/a | n/a | yes | yes |  |  | 1 | run-1790662411393-agno-a4681950 |
| apache-airflow | FAILED | yes | yes | yes | no | no | 6 CLEAN | JOURNEY_NOT_AUTHORED:ACCOUNT_NO_STRATEGY | 1 | run-1790662411393-apache-airflow-9b24d976 |
| appsmith | FAILED | yes | yes | yes | no | no | 6 CLEAN | BROWSER_HTTP | 1 | run-1790662411395-appsmith-051c24b3 |
| billionmail | TIMEOUT | no | no | no | no | no | 2 APP_UP | RUNNER_EXHAUSTED | 1 | run-1790662411397-billionmail-37fa19d0 |
| botpress | START_FAILED | no | no | no | no | no | 2 APP_UP | RUNNER_EXHAUSTED | 1 | run-1790662411398-botpress-ad788b63 |
| clearflask | TIMEOUT | no | no | no | no | no | 2 APP_UP | RUNNER_EXHAUSTED | 1 | run-1790662411396-clearflask-bb982647 |
| colanode | FAILED | yes | yes | yes | no | no | 6 CLEAN | JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL | 1 | run-1790662411401-colanode-48ff93cd |
| coolify | FAILED | yes | yes | no | no | no | 3 PROXY_UP | NOT_HTML | 1 | run-1790662411408-coolify-c5ddc6ee |
| docker-moby | QUALIFIED | n/a | n/a | n/a | yes | yes |  |  | 1 | run-1790662411412-docker-moby-d5daaffa |
| flarum | FAILED | yes | yes | yes | no | no | 6 CLEAN | CONSOLE_ERRORS:4 | 1 | run-1790662411410-flarum-14195f66 |
| frp | QUALIFIED | yes | yes | yes | yes | yes |  |  | 1 | run-1790662411408-frp-cbf45de1 |
| ghost | START_FAILED | no | no | no | no | no | 2 APP_UP | RUNNER_EXHAUSTED | 1 | run-1790662411408-ghost-98feb11a |
| grafana | QUALIFIED | yes | yes | yes | yes | yes |  |  | 1 | run-1790662411414-grafana-b1e5b48f |
| graphite | QUALIFIED | yes | yes | yes | yes | yes |  |  | 1 | run-1790662411409-graphite-bd73edb9 |
| krayin-crm | FAILED | yes | yes | yes | no | no | 4 SKIN | NO_LINK | 1 | run-1790662411413-krayin-crm-b8ebcb55 |
| langflow | START_FAILED | no | no | no | no | no | 2 APP_UP | RUNNER_EXHAUSTED | 1 | run-1790662411427-langflow-2aa2dd01 |
| medplum | FAILED | yes | yes | yes | no | no | 6 CLEAN | JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL | 1 | run-1790662411427-medplum-6560f4b0 |
| memos | FAILED | yes | yes | yes | no | no | 6 CLEAN | CONSOLE_ERRORS:19 | 1 | run-1790662411421-memos-e2b8ce1d |
| open-design | START_FAILED | no | no | no | no | no | 2 APP_UP | RUNNER_EXHAUSTED | 1 | run-1790662411429-open-design-5a0fee65 |
| opencart | START_FAILED | no | no | no | no | no | 2 APP_UP | RUNNER_EXHAUSTED | 1 | run-1790662411431-opencart-0d28f42e |
| opencloud | START_FAILED | no | no | no | no | no | 2 APP_UP | RUNNER_EXHAUSTED | 1 | run-1790662411425-opencloud-0418f34a |
| plane | START_FAILED | no | no | no | no | no | 2 APP_UP | RUNNER_EXHAUSTED | 1 | run-1790662411432-plane-32342756 |
| polar | TIMEOUT | no | no | no | no | no | 2 APP_UP | RUNNER_EXHAUSTED | 1 | run-1790662411434-polar-12b7a70d |
| stride | QUALIFIED | n/a | n/a | n/a | yes | yes |  |  | 1 | run-1790662411435-stride-001f913a |
| supabase | FAILED | yes | yes | yes | no | no | 6 CLEAN | JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL | 1 | run-1790662411444-supabase-7e51f756 |
| super-productivity | FAILED | yes | yes | yes | no | no | 6 CLEAN | CONSOLE_ERRORS:151 | 1 | run-1790662411454-super-productivity-6adfb2c6 |
| tidb | QUALIFIED | yes | yes | yes | yes | yes |  |  | 1 | run-1790662411456-tidb-abf55480 |
| traefik | FAILED | yes | yes | yes | no | no | 6 CLEAN | JOURNEY_NOT_AUTHORED:SMOKE_NO_CONTROL | 1 | run-1790662411453-traefik-ac582852 |
| umami | START_FAILED | no | no | no | no | no | 2 APP_UP | RUNNER_EXHAUSTED | 1 | run-1790662411461-umami-4e629c4e |
