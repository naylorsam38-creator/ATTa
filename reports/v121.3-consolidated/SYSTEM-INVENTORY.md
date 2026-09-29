# ATTa v121.3-consolidated: complete file inventory

Generated from the files themselves (SHA-256 of every file), not written by hand. "unchanged since v121" means
byte-identical to ATTa-v121.zip. Caches (`__pycache__`, `.pytest_cache`) are not code and are left out.

Sources: v121 = ATTa-v121.zip (= ATTa-v121-consolidated.zip); server original v121 bundle =
ATTa-v121-collect-all-adaptive-ccca34de3988.zip (what the server was first installed from); v121.2 = ATTa-v121.2.zip;
v121.3 (server) = the release running on i-0f13b8644b215931d (git 684d768); additive control =
ATTa-v121.3-additive-control-final.zip.

**153 files**: 106 unchanged since v121, 18 changed, 29 added since v121.
Files of any source that are missing here: **0**.

## (top level) (10 files)

| File | Origin | What it is |
|---|---|---|
| `.dockerignore` | unchanged since v121 |  |
| `.gitignore` | unchanged since v121 |  |
| `COLLECT-ALL-APPS.md` | from server original v121 bundle | ATTa — Collect All Upstream Apps |
| `DISPATCH-RUNBOOK.md` | from server original v121 bundle | ATTa — Dispatch Operating Contract |
| `Dockerfile` | unchanged since v121 |  |
| `README.md` | changed in server original v121 bundle, consolidation | APP Builder |
| `START-HERE.txt` | changed in v121.2, v121.3 (server) |  |
| `docker-compose.coolify.yml` | unchanged since v121 |  |
| `release.json` | changed in v121.2, v121.3 (server), additive control, consolidation |  |
| `run` | as in server original v121 bundle | !/usr/bin/env bash |

## 01-specs (4 files)

| File | Origin | What it is |
|---|---|---|
| `01-specs/Dormant-Hook-and-Watcher-Build-Handoff.md` | unchanged since v121 | Dormant Hook + Hook Watcher — Build Handoff |
| `01-specs/System-Watcher-Build-Handoff.md` | unchanged since v121 | System Watcher — Build Handoff |
| `01-specs/System-Watcher-Revision-1.md` | unchanged since v121 | System Watcher — Revision 1 (fix list) |
| `01-specs/UPLOAD-TO-LOGIN-GOAL.md` | unchanged since v121 | Upload-to-Login Goal — Governing Addition |

## 02-front-door (1 files)

| File | Origin | What it is |
|---|---|---|
| `02-front-door/front-door.html` | unchanged since v121 |  |

## 03-ui-skins-capability-package (1 files)

| File | Origin | What it is |
|---|---|---|
| `03-ui-skins-capability-package/UI_Skin_Capability_OneShot_v2.zip` | unchanged since v121 |  |

## 04-deployment (63 files)

| File | Origin | What it is |
|---|---|---|
| `04-deployment/CATALOGUE_CHANGELOG.md` | unchanged since v121 | App catalogue changelog |
| `04-deployment/accounts.py` | unchanged since v121 | accounts.py — user accounts for the APP Builder gateway. |
| `04-deployment/alerts.py` | unchanged since v121 | alerts.py — tier 4 of self-healing: tell a human. The last resort only. |
| `04-deployment/app-builder-nginx.conf` | unchanged since v121 |  |
| `04-deployment/app_catalogue.json` | unchanged since v121 |  |
| `04-deployment/app_classifier.py` | unchanged since v121 | Fixed-rule app understanding: where an app's code lives, and which skin category fits it. No AI, no network. Used by catalogue_sync.py (and mirrored in the installer for  |
| `04-deployment/app_discovery.py` | unchanged since v121 | app_discovery.py (v112) — find EVERY app inside an uploaded zip, at any depth. |
| `04-deployment/app_runner.py` | changed in v121.2, v121.3 (server), consolidation | app_runner.py — starts each library app so the six-stage watcher has something real to check. |
| `04-deployment/atta_control/__init__.py` | from additive control | ATTa additive control layer. |
| `04-deployment/atta_control/adapter.py` | from additive control |  |
| `04-deployment/atta_control/gate.py` | from additive control |  |
| `04-deployment/atta_control/integration.py` | from additive control | Compatibility bridge into the existing ATTa repair machinery. |
| `04-deployment/atta_control/models.py` | from additive control |  |
| `04-deployment/atta_control/policy.py` | from additive control |  |
| `04-deployment/atta_control/report.py` | from additive control |  |
| `04-deployment/atta_control/store.py` | from additive control |  |
| `04-deployment/bootstrap-lib.sh` | unchanged since v121 | Kept separate so tests/ can exercise each step without root, systemd or a real server. |
| `04-deployment/bootstrap.sh` | unchanged since v121 | v114.1: code lives in a fresh folder per deploy under $RELS; $APP is a symlink to the live one. |
| `04-deployment/builds.py` | unchanged since v121 | builds.py — one record per build, owned by the account that started it. |
| `04-deployment/capability_adapter.py` | unchanged since v121 | capability_adapter.py — tier 2 of self-healing: the Capability adapter. |
| `04-deployment/catalogue_sync.py` | unchanged since v121 | App catalogue sync. The library grows by itself: any app that arrives (uploaded as a zip, added from a git URL, cloned from a seed repo) gets a code root, a skin category |
| `04-deployment/container_main.py` | unchanged since v121 | container_main.py — v117: ATTa in one container, the way Coolify runs it. |
| `04-deployment/coolify_handoff.py` | unchanged since v121 | coolify_handoff.py — hands QUALIFIED builds to Coolify. Nothing else reaches Coolify. |
| `04-deployment/coolify_provision.py` | unchanged since v121 | coolify_provision.py — v117: give every QUALIFIED app its own home in Coolify, automatically. |
| `04-deployment/deployd/README.md` | unchanged since v121 | v112 Automatic Upload Pipeline |
| `04-deployment/deployd/adm/__init__.py` | unchanged since v121 |  |
| `04-deployment/deployd/adm/activation.py` | unchanged since v121 | Activation = run the bundle's own `bash run` (which runs bootstrap.sh: copies code to the live folder, rewrites the systemd units, restarts services, and refuses unless i |
| `04-deployment/deployd/adm/authz.py` | unchanged since v121 | Who may deploy. v114: a job queued from a web upload names the account that uploaded it; ADM runs it only if that account is an enabled admin in the live accounts file. J |
| `04-deployment/deployd/adm/backup.py` | unchanged since v121 | Snapshot of the LIVE code folder (config.APP) before each activation, and pruning of old ones. A backup is a complete copy of 04-deployment as it runs now; bootstrap.sh c |
| `04-deployment/deployd/adm/config.py` | unchanged since v121 | ADM (ATTa Deploy Manager) — settings. Every other adm module reads from here. |
| `04-deployment/deployd/adm/health.py` | unchanged since v121 | Independent post-deploy check. bootstrap.sh has its own gate; this one runs AFTER it from outside, so a deploy is only DEPLOYED when both agree. Returns a list of FAIL li |
| `04-deployment/deployd/adm/journal.py` | unchanged since v121 | One JSON file per deploy job. Every event is appended; the file is rewritten atomically. Read a job's verdict with: deployctl journal <job> (last event tells you what hap |
| `04-deployment/deployd/adm/manager.py` | unchanged since v121 | The deploy itself: stage -> check -> back up -> activate -> health -> DEPLOYED, or on any failure: ROLLING_BACK -> re-run the previous release (or the backup) -> health - |
| `04-deployment/deployd/adm/queue.py` | unchanged since v121 | The deploy queue: a zip plus a small meta file per job, processed oldest first. |
| `04-deployment/deployd/adm/staging.py` | unchanged since v121 | Stage and check a bundle before anything live is touched. A bundle is refused here (never activated) if it lacks a required file, its release.json is unreadable, or any P |
| `04-deployment/deployd/deployctl` | unchanged since v121 |  |
| `04-deployment/deployd/deployd.py` | unchanged since v121 | deployd — ATTa Deploy Manager daemon (runs as the atta-deployd systemd service). |
| `04-deployment/deployd/systemd/atta-deployd.service` | unchanged since v121 |  |
| `04-deployment/envfile.py` | unchanged since v121 | Read <ROOT>/.env as data, never as shell (v114.1). |
| `04-deployment/failure_keys.py` | unchanged since v121 | failure_keys.py — ONE key for one failure. v111a. |
| `04-deployment/gateway.py` | changed in v121.2 |  |
| `04-deployment/intake.py` | unchanged since v121 | Intake check: runs when apps come into the library, fixes what it can with fixed rules, and records every change. No AI, no network, no cost. |
| `04-deployment/journey_author.py` | unchanged since v121 | journey_author.py — v121: the half that FEEDS the gate. |
| `04-deployment/known_fixes.py` | unchanged since v121 | known_fixes.py — tier 1 of self-healing: deterministic, no AI. |
| `04-deployment/llm_repair.py` | changed in additive control | llm_repair.py — tier 3 of self-healing: Claude diagnoses and attempts a fix. |
| `04-deployment/maintenance.py` | changed in v121.3 (server), additive control, consolidation | maintenance.py — the self-healing layer. Runs when a build fails at any of the three layers: |
| `04-deployment/pipeline.py` | changed in v121.2 |  |
| `04-deployment/redact.py` | unchanged since v121 | v114: strip secrets from text before it leaves this server (the LLM repair step sends logs, build records and file contents to an external API). Keys stay visible so the  |
| `04-deployment/repair_actions.py` | unchanged since v121 | repair_actions.py — the fixed set of repair actions the self-healing layer may take. |
| `04-deployment/resilience/__init__.py` | from v121.2, changed in v121.3 (server) | ATTa resilience boundary: safe observation, deployment/app identity, outcome vocabulary and the fleet report. Existing deployd, watcher and lifecycle code remain authorit |
| `04-deployment/resilience/failures.py` | from v121.3 (server) | Where an app failed, what it most likely means, what has to change and what must be retested. |
| `04-deployment/resilience/identity.py` | from v121.2 |  |
| `04-deployment/resilience/probes.py` | from v121.2 |  |
| `04-deployment/resilience/report.py` | from v121.2, changed in v121.3 (server) | The aggregate report of one fleet run: per-app outcome plus totals, failures by class, the concurrency actually used, and every timeout / resource event. Built only from  |
| `04-deployment/resilience/states.py` | from v121.2 | The per-app outcome vocabulary of a fleet run, and the observation ladder behind it. |
| `04-deployment/rule_lifecycle.py` | unchanged since v121 | rule_lifecycle.py — the trust layer over everything the self-healer LEARNS. |
| `04-deployment/runtimes.py` | unchanged since v121 | runtimes.py — reusable ways to run an app that ships its source but no way to start it (no compose file, no published image, no Dockerfile). |
| `04-deployment/syntax_triage.py` | unchanged since v121 | syntax_triage.py — tells a simple syntax slip apart from a real breakage, before healing starts. |
| `04-deployment/system_watcher.py` | changed in v121.2, v121.3 (server) |  |
| `04-deployment/upstream.py` | unchanged since v121 | upstream.py — when an app's own folder doesn't say how to run it, look at what its UPSTREAM publishes before anyone (the AI) invents a way. |
| `04-deployment/upstream_apps.json` | changed in server original v121 bundle, consolidation |  |
| `04-deployment/user_catalogue.py` | unchanged since v121 | user_catalogue.py — v118/v120: the catalogue people see. QUALIFIED apps only, nothing else. |
| `04-deployment/wait-for-docker.sh` | unchanged since v121 | ExecStartPre for services that drive Docker (v114.1). systemd's After=docker.service only orders the |

## 05-coolify (29 files)

| File | Origin | What it is |
|---|---|---|
| `05-coolify/coolify-main.zip` | unchanged since v121 |  |
| `05-coolify/install-coolify.sh` | unchanged since v121 | Install Coolify on this server from the supplied source (coolify-main.zip). |
| `05-coolify/kit/.github/workflows/ci.yml` | unchanged since v121 |  |
| `05-coolify/kit/.github/workflows/e2e.yml` | unchanged since v121 |  |
| `05-coolify/kit/.gitignore` | unchanged since v121 |  |
| `05-coolify/kit/HANDOVER.md` | unchanged since v121 | Handover — ATTa v117 + Coolify kit |
| `05-coolify/kit/Makefile` | unchanged since v121 |  |
| `05-coolify/kit/README.md` | unchanged since v121 | ATTa: Coolify deploy kit |
| `05-coolify/kit/config/coolify.env.example` | unchanged since v121 |  |
| `05-coolify/kit/docs/DEPLOY-ATT.md` | unchanged since v121 | Running ATTa with Coolify |
| `05-coolify/kit/docs/FIXES.md` | unchanged since v121 | Everything fixed |
| `05-coolify/kit/docs/RUNBOOK.md` | unchanged since v121 | Runbook: from nothing to ATTa handing apps to Coolify |
| `05-coolify/kit/docs/TROUBLESHOOTING.md` | unchanged since v121 | Troubleshooting |
| `05-coolify/kit/scripts/backup.sh` | unchanged since v121 | Backs up everything needed to rebuild this Coolify instance on a new server: |
| `05-coolify/kit/scripts/connect-atta.sh` | unchanged since v121 | Connects this Coolify to the ATTa APP Builder, so ATTa can hand qualified builds to Coolify. |
| `05-coolify/kit/scripts/deploy-atta.sh` | changed in v121.2 | Puts ATTa on this Coolify server, run BY Coolify. Run after scripts/install.sh. |
| `05-coolify/kit/scripts/install.sh` | changed in v121.2 | Installs Coolify on this server, pinned to a known version, with the admin account |
| `05-coolify/kit/scripts/lib/common.sh` | changed in v121.2 | Shared helpers for the ATTa Coolify deploy kit. |
| `05-coolify/kit/scripts/preflight.sh` | unchanged since v121 | Checks that this server can run Coolify before anything is installed. |
| `05-coolify/kit/scripts/restore.sh` | unchanged since v121 | Restores a backup made by scripts/backup.sh onto a server that already has a fresh, |
| `05-coolify/kit/scripts/upgrade.sh` | changed in v121.2 | Upgrades Coolify to a specific version: backup first, then the official installer, |
| `05-coolify/kit/scripts/verify.sh` | unchanged since v121 | Proves a Coolify install is healthy and locked down. Safe to run any time; changes nothing. |
| `05-coolify/kit/tests/e2e/e2e.sh` | unchanged since v121 | End-to-end test of the deploy kit against a REAL Coolify install. |
| `05-coolify/kit/tests/e2e/sandbox-prep.sh` | unchanged since v121 | Prepares a container WITHOUT systemd (e.g. a cloud dev sandbox) to run tests/e2e/e2e.sh. |
| `05-coolify/kit/tests/unit/common.bats` | unchanged since v121 |  |
| `05-coolify/kit/tests/unit/deploy_domains.bats` | from v121.2 |  |
| `05-coolify/kit/tests/unit/mirror.bats` | from v121.2 |  |
| `05-coolify/kit/tests/unit/scripts.bats` | unchanged since v121 |  |
| `05-coolify/kit/tests/unit/test_helper.bash` | unchanged since v121 | Put stub commands first on PATH. stub NAME 'shell body' |

## docs (33 files)

| File | Origin | What it is |
|---|---|---|
| `docs/ADM.md` | unchanged since v121 | ADM — ATTa Deploy Manager |
| `docs/AWS-RUN.md` | unchanged since v121 | Running v109 on AWS (airexploit.com) — and what to check |
| `docs/CATALOGUE-SYNC-REPORT.md` | unchanged since v121 | Catalogue report (v108) |
| `docs/COOLIFY-HANDOFF.md` | unchanged since v121 | Coolify hand-off — the boundary |
| `docs/SYSTEM-INVENTORY.md` | NEW in consolidation | This inventory |
| `docs/V121.3-ADDITIVE-CONTROL-LAYER.md` | from additive control | v121.3 additive control layer |
| `docs/history/AUDIT-RESULTS.md` | unchanged since v121 | Deployment Audit Results — 2026-09-22 |
| `docs/history/BUNDLE-README-2026-09-22.md` | unchanged since v121 | Handoff Bundle — 2026-09-22 — READY FOR AWS LIVE VALIDATION |
| `docs/history/CHANGES-2026-09-22-ENGINEER-PASS.md` | unchanged since v121 | Engineer pass — 2026-09-22 — findings and fixes |
| `docs/history/CHANGES-2026-09-24-APP-RUNNER.md` | unchanged since v121 | v109: the app runner (2026-09-24) |
| `docs/history/CHANGES-2026-09-24-CATALOGUE-SYNC.md` | unchanged since v121 | v107 — App catalogue sync (2026-09-24) |
| `docs/history/CHANGES-2026-09-24-FALSE-FAILS.md` | unchanged since v121 | Fewer false failures (2026-09-24, after syntax triage) |
| `docs/history/CHANGES-2026-09-24-FRONT-DOOR-OFF-CLAUDE.md` | unchanged since v121 | Change: Front Door no longer depends on Claude (2026-09-24) |
| `docs/history/CHANGES-2026-09-24-INTAKE.md` | unchanged since v121 | v105 — Intake check and capability fixes (2026-09-24) |
| `docs/history/CHANGES-2026-09-24-LOCAL-LIBRARY.md` | unchanged since v121 | Change: library is authoritative (2026-09-24) |
| `docs/history/CHANGES-2026-09-24-SELF-GROWING-LIBRARY.md` | unchanged since v121 | v108 — The library grows by itself (2026-09-24) |
| `docs/history/CHANGES-2026-09-24-SYNTAX-TRIAGE.md` | unchanged since v121 | Syntax triage (2026-09-24) |
| `docs/history/CHANGES-2026-09-25-ADM.md` | unchanged since v121 | 2026-09-25 — ADM (ATTa Deploy Manager) built and integrated |
| `docs/history/CHANGES-2026-09-25-RULE-LIFECYCLE.md` | unchanged since v121 | Learned-rule lifecycle (2026-09-25) — v110 |
| `docs/history/CHANGES-2026-09-25-V111A-FAILURE-KEYS.md` | unchanged since v121 | v111a — FailureKey, normaliser, pending barrier (2026-09-25) |
| `docs/history/CHANGES-2026-09-25-V112.md` | unchanged since v121 | v112 — every app in an upload is found, run, checked and evidenced (2026-09-25) |
| `docs/history/CHANGES-2026-09-25-V114-HARDENING.md` | unchanged since v121 | v114 — security hardening (2026-09-25) |
| `docs/history/CHANGES-2026-09-25-V114.1-INSTALLER.md` | unchanged since v121 | v114.1 — installer fixes (2026-09-25) |
| `docs/history/CHANGES-2026-09-25-V114.2-ALL-AT-ONCE.md` | unchanged since v121 | v114.2 — every app at once, script probe instead of the LLM |
| `docs/history/CHANGES-2026-09-28-V118-MERGE.md` | unchanged since v121 | v118 — merge of v111a, v111a-ADM, v114.1 and v117 against the locked goal (2026-09-28) |
| `docs/history/CHANGES-2026-09-28-V119-RUNTIME-PROOF.md` | unchanged since v121 | v119 — runtime journey qualification gate |
| `docs/history/CHANGES-2026-09-28-V120-STAGE2-GATE.md` | unchanged since v121 | v120 — v118 base + the v119 runtime-journey gate (2026-09-28) |
| `docs/history/CHANGES-2026-09-28-V121-JOURNEY-AUTHOR.md` | unchanged since v121 | v121 — journey_author.py: the half that feeds the gate (2026-09-28) |
| `docs/history/CHANGES-2026-09-29-V121.1-RESILIENCE.md` | from v121.2 | ATTa v121.1 — integrated resilience hardening |
| `docs/history/CHANGES-2026-09-29-V121.2-ISOLATED-PARALLEL-JOBS.md` | from v121.2 | ATTa v121.2: every app is its own isolated, parallel, revision-bound job |
| `docs/history/CHANGES-2026-09-29-V121.3-CONSOLIDATED.md` | NEW in consolidation | ATTa v121.3-consolidated |
| `docs/history/CHANGES-2026-09-29-V121.3-STOP-FIX-RETEST.md` | from v121.3 (server) | ATTa v121.3: stop, record, one bounded fix, retest, with no blind retries |
| `docs/history/FIXES-APPLIED.md` | unchanged since v121 | Deployment Fixes Applied |

## tests (12 files)

| File | Origin | What it is |
|---|---|---|
| `tests/test_consolidated_control.py` | NEW in consolidation | Consolidation: the additive control layer (atta_control) gates BOTH repair paths of the consolidated system: maintenance.py's self-healing (as the additive-control releas |
| `tests/test_resilience_boundary.py` | from v121.2 |  |
| `tests/test_v1141_installer.py` | unchanged since v121 | v114.1 installer tests: .env parsing, pinned Compose, clean code releases, permissions, Docker readiness, unit ordering. Bash functions from 04-deployment/bootstrap-lib.s |
| `tests/test_v114_hardening.py` | unchanged since v121 | v114 hardening tests. No Docker, no network, no root needed. |
| `tests/test_v117_coolify.py` | changed in v121.2, v121.3 (server), consolidation | v117 tests: every qualified app gets its own Coolify service, ATTa runs in a container under Coolify and fetches the whole app list on first start. No Docker, no network, |
| `tests/test_v118_merge.py` | changed in v121.2 | v118 merge: the catalogue guarantee, no guessed repos, runner gates, model name, HTTPS route. |
| `tests/test_v119_login_proof.py` | unchanged since v121 | v119/v120 runtime-journey gate tests. |
| `tests/test_v120_stage2.py` | changed in v121.2, v121.3 (server), consolidation | v120: v118 base + the v119 runtime-journey gate, and the Locked Goal's "the system chooses" rule. |
| `tests/test_v121_2_parallel_isolation.py` | from v121.2 | v121.2: every app is discovered and tested as its own isolated job, in parallel; one app's failure never stops, corrupts or hides another's; evidence is bound to its app, |
| `tests/test_v121_3_additive_control.py` | from additive control |  |
| `tests/test_v121_3_stop_fix_retest.py` | from v121.3 (server), changed in consolidation | v121.3: TEST -> FAIL -> STOP THAT APP -> RECORD EXACTLY WHERE -> ONE BOUNDED FIX (ATTa's own repair tiers) -> RETEST THE FAILED STAGE -> REGRESSION -> CONTINUE -> FINAL V |
| `tests/test_v121_journey_author.py` | unchanged since v121 | v121: journey_author.py — the half that feeds the gate. |

