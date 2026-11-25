# STAGE 12 MERGE MANIFEST — Connectors, MCP, Skills & Plugins (Parallel Agent 4 sidecar)

**Status: IMPLEMENTED IN SIDECAR · VERIFIED IN SIDECAR · NOT YET INTEGRATED.**
This sidecar has not been merged into the authoritative application, and contains no work from Stage 01B, 04 or 11.

## 1. Baseline and branch

| | |
|---|---|
| Baseline (Stage 01 + 01A, gate passed) | `21104c413c30a3b2714f877892970e7db19a1d19` on `origin/claude/ai-app-stage-01-foundation-82jy3a` |
| Note | The supplied working branch `claude/lucid-maxwell-uc91v4` initially pointed at ATTa v121.5 (`cef7329`) **without** Stage 01/01A; the Stage 01A gate commit above was used as the authoritative baseline |
| Sidecar branch | `stage-12-integrations-sidecar` (local), published as `claude/lucid-maxwell-uc91v4` |
| Final code | backend/tests `86ffda7` (trial-verified on the real Stage 01B and Stage 11 trees; full regression run `s12run_20261125T101512Z`); installer fixes `d84cdc3` (Stage 12 suite rerun `s12run_20261125T103504Z`: 53 PASS + 1 BLOCKED, 0 FAIL); later commits: documentation only |
| Other workers' trees used (read-only, never merged) | Stage 01B `416ca30` (the package supplied by the owner; byte-identical to that git commit) · Stage 11 `bb1a60b` (`origin/claude/focused-cori-ajjjn0`) · Stage 04: not published |

## 1a. How to install — transactional plug-in installer (changes-only ZIP)

`installer/install.sh` (Linux/macOS) or `installer\install.bat` (Windows) → `install.py` (stdlib only, Python 3.10+):
`--dry-run` (plan + conflicts, no change) · install / upgrade · `--uninstall` · `--recover` · `--migration-number N|auto` ·
`--check-db` · `--json`. Payload integrity (`SHA256SUMS.txt`, `manifest.json`) is verified first. New files are never
written over existing different content; `gateway.py`, `catalogue.py`, `app.py` and the requirement register are
patched **hunk by hunk** (other workers' additions elsewhere are preserved; an overlapping change is a CONFLICT and
nothing is changed); shared documents receive a marker-delimited Stage 12 section; `MASTER_IMPLEMENTATION_REGISTER.md`
is re-rendered by the target's own generator. Every write is journalled with backups; any failure, including the
post-install verification (checksums, hunks, sections, import of the patched modules), restores the target
byte-for-byte. Repeat install = no-op; uninstall restores the pre-install bytes. Tested: S12-T80…T88 (see
`installer/README.md`).

## 1b. Trial installs on the other workers' REAL trees (`scripts/stage12_trial.py`)

Same suites, same environment, before ("pre") and after ("post") installing Stage 12 with the installer.
Regression = failing post but passing pre.

| Tree | UNIT | S01A-A | S01A-B | S01 | Worker's own suite | Stage 12 suite (post) | Regressions |
|---|---|---|---|---|---|---|---|
| Stage 01B `416ca30` | 43 → 43 | 27 → 27 | 25+1F → 25+1F | 11+5F → 11+5F | S01B 39+6F → 39+6F (identical tests) | **45/45** incl. T48 (real 01B network namespace) | **none** |
| Stage 11 `bb1a60b` | 35 → 35 | 27 → 27 | 25+1F → 25+1F | 11+5F → 11+5F | S11 117 → 117 | **44/44** + T48 BLOCKED (needs 01B) | **none** |

Pre-existing failures (identical before and after): S01A-T51 (OS protection, environment), the five Stage 01 tests
superseded by Stage 01A (D-029), and on 01B: S01B-T36/T37/T38 (Worker A's own defect at `416ca30` — the qualification
source copy lacked git history/local configuration — fixed by Worker A in `c6d0928`), T41 (Windows only), T42 (needs the
verifier key/worker account from `protect_verifier.sh`), T43 (H100 only).

**Defect found and fixed by the trials**: the first 01B trial showed S01B-T18/T23/T24 newly failing — Stage 01B's
dependency-import coverage parsed the Stage 12 bridge files, which import modules that exist only inside generated
packages. The bridges are now templates (`backend/integrations/bridges/*.py.tmpl`); the second trial shows no
regression. Both runs are kept: `evidence/stage_12/trials/stage01b-run1-regression-found/` and `.../stage01b/`.

## 2. Files

**Deleted files: NONE.** Full per-file list with SHA-256: `STAGE_12_CHANGE_MANIFEST.md`.

### Modified (existing) files

| File | Change | Why |
|---|---|---|
| `backend/orchestration/gateway.py` | +22 lines: `guards` and `result_filters` lists (default empty) and their loops at step 9b / 16b | Extension points; a guard can only refuse, a filter can only keep or reject. Empty = Stage 01A behaviour |
| `backend/orchestration/catalogue.py` | +26 lines: optional `extra_numbers` constructor argument + `merge_extra_numbers()` | Stage 12 permanent numbers in the SAME catalogue; collisions make nothing allocatable |
| `backend/api/app.py` | +6 lines: idempotent `mount_integrations()`; attach integrations in lifespan (failure never blocks start) | API contracts for Stage 04 |
| `.gitignore` | +2 lines (append only) | `configuration/integrations.local.yaml` |
| `DECISIONS.md`, `BLOCKERS.md`, `IMPLEMENTATION_PROGRESS.md` | appended Stage 12 sections | shared records (merge by section) |
| `documentation/requirements/requirement_register.yaml`, `MASTER_IMPLEMENTATION_REGISTER.md` (rendered) | CAP-21..24 NOT_STARTED → PARTIAL with sidecar note/evidence | register |
| `CHANGE_MANIFEST.md` | Stage 12 section prepended; Stage 01/01A sections untouched | |

No protected/sealed file (`configuration/build/**`, `tests/acceptance/**`, `backend/verification/**`,
`configuration/{controller.yaml,permissions.yaml,tool_numbers.yaml,capability_trust.json,*.pub}`,
`tests/regression/legacy_baseline.json`) and no legacy ATTa file was modified.

### Added

* `backend/integrations/` — `types, content, manifest, integration.schema.json, config, credentials, network, isolation,
  adapters, assessment, packaging, store, manager, runtime, verification, skills, upgrade, service, api, __main__`;
  `mcp/{wire,client,validation}.py`; `bridges/{mcp_bridge,verify,health}.py.tmpl` (templates)
* `database/migrations/0012_stage12_integrations.sql`
* `configuration/integrations.yaml`, `configuration/integrations/{mcp_servers.yaml,permissions.proposed.yaml}`
* `integrations/README.md`, `integrations/available/.gitkeep` (empty source directory — no production fixtures)
* `packages/shared_types/integrations.contracts.generated.json`
* `scripts/{stage12_verify.py,stage12_report.py,stage12_trial.py,export_integration_contracts.py}`
* `installer/stage12/{install.py,install.sh,install.bat,build_payload.py,README.md}` (in the ZIP as `installer/`)
* `documentation/ROADMAP_NUMBERING_STAGE12.md`
* `tests/integrations/` — 9 test modules (54 tests: S12-T01…T88), harness, HTTP MCP fixture, fixture builder,
  `fixtures/` (14 packages + stdio MCP test server) — **test-only**
* `documentation/{INTEGRATION_ARCHITECTURE,INTEGRATION_MANIFEST_SPEC,MCP_ARCHITECTURE,CONNECTOR_ARCHITECTURE,SKILL_ARCHITECTURE,PLUGIN_SECURITY}.md`
* `STAGE_12_VERIFICATION.md`, `STAGE_12_MERGE_MANIFEST.md`, `STAGE_12_CHANGE_MANIFEST.md`, `evidence/stage_12/`

## 3. Migrations

`0012_stage12_integrations.sql` — new schema `integrations` only (18 tables, append-only triggers, runtime-role grants).
Depends only on `0001`/`0002`. Stage 11 uses `0011`, Stage 01B adds none: no collision today. The installer detects a
collision before changing anything (every `database/migrations/*.sql` number; with `--check-db` also the target
database's applied ledger, including "already applied under another number") and installs under a free number only
when asked (`--migration-number N|auto`). Never renumber a migration a database has already applied.

## 4. Capability IDs, tool IDs, schemas

* Registry capability id per integration: `integration.<integration_id>`; requirement CAP-21 (connector), CAP-22 (MCP),
  CAP-24 (plugin), declared CAP-21/22/24/40 (custom provider). Skills register no capability (CAP-23 content in DB).
* Tool numbers: `CCC.TTT`, TTT **500–899 reserved for Stage 12 automatic assignment** (operators keep 001–499, 900–999).
  **No production numbers are assigned by this sidecar** (no integration ships); tests use their own namespaces.
* Schemas: `backend/integrations/integration.schema.json` (manifest v1), generated capability manifests conform to the
  Stage 01A `manifest.schema.json`, API contracts in `packages/shared_types/integrations.contracts.generated.json`.

## 5. API additions (all default-DENY by the policy engine until Stage 02)

`GET /v1/integrations[?type]`, `POST /v1/integrations/discover`, `POST /v1/integrations/validate`,
`POST /v1/integrations/install`, `GET /v1/integrations/{id}`, `POST /v1/integrations/{id}/enable`,
`POST /v1/integrations/{id}/disable`, `POST /v1/integrations/{id}/upgrade`, `DELETE /v1/integrations/{id}`,
`GET /v1/integrations/{id}/{health,configuration-schema,capabilities,tools,permissions,events}`, `GET /v1/mcp/servers`,
`GET /v1/mcp/servers/{id}/capabilities`, `GET /v1/skills`, `GET /v1/skills/scripts`, `GET /v1/plugins`.
Policy operations used: `integration.{list,discover,validate,install,get,enable,disable,upgrade,remove,health,audit}`.

## 6. Dependencies and configuration

* No new Python or Node dependencies (stdlib, PyYAML, jsonschema, psycopg, cryptography, FastAPI — all locked).
  Test environment additionally used Playwright 1.63.0 from the existing `backend-full` lock (legacy browser tests).
* Configuration: `configuration/integrations.yaml` (+ untracked `integrations.local.yaml`, env `ATTA_INTEGRATIONS__*`):
  `source_directories`, `install_root`, `connectivity`, `allowed_network_hosts`, `allowed_mcp_executables`,
  `mcp_definitions`, `max_result_bytes`, `health_max_age_s`, `mcp_request_timeout_s`, `mcp_startup_timeout_s`,
  `proposed_permissions`. No usernames, drives, hosts, ports, credentials or tenant ids are hard-coded.

## 7. Integration points

| Stage | Point | Status |
|---|---|---|
| **01B** (Worker A, `416ca30`) | `adapters.RuntimeIsolation(controller.runtime)` reads 01B's `CapabilityRuntime(isolation_required, network, worker_user)`; `ConnectivitySources` reads `settings.deployment.profile`; the guard requires integration hosts in the task contract, which is exactly when 01B grants a worker host networking; stdio MCP servers start from the verified snapshot so 01B's unprivileged worker account can run them | **Prepared and trial-verified** on 01B's real tree (T48 in a real private network namespace; RESTRICTED plugin runs under 01B isolation; no S01B regression). Optional at merge: pass a 01B qualified-mode provider via `attach(connectivity_provider=…)` |
| **02** | `CredentialBroker` protocol, `OperatorAuthorization` uses, policy operations `integration.*`, side-effect/risk metadata, `permissions.proposed.yaml` | Unchanged: interfaces only; credential-dependent integrations AUTH_REQUIRED, medium+ tools need approval (BLOCKED until Stage 02) |
| **03** | context packets (tool descriptions validated, outputs untrusted with provenance), `list_scripts()` | Unchanged |
| **04** (Worker 2) | `mount_integrations(app, authorize)` (idempotent), typed contracts `packages/shared_types/integrations.contracts.generated.json` | **Prepared**; Stage 04 code not published → not trial-verified (BLOCKERS S12-B11) |
| **11** (Worker 3, `bb1a60b`) | `ConnectivitySources` honours Stage 11's `ATTA_NETWORK_MODE` / `NetworkModeProvider` (most restrictive wins, unknown → AIR_GAPPED); tool numbers 011/012 vs Stage 12's 5xx band; migrations 0011 vs 0012 | **Trial-verified** on 11's real tree (S11 117/117 before and after) |

One capability registry, one catalogue, one execution controller: `attach()` only composes around
`controller.registry`, `controller.catalogue`, `controller.gateway`, `controller.allocator` (S12-T73; S12-T10).

## 8. Known conflicts / risks

1. **Roadmap numbering**: see `documentation/ROADMAP_NUMBERING_STAGE12.md` (protected roadmap unchanged; its SHA-256
   still equals the sealed value). The operator must reconcile and seal a stage definition before any gate result.
2. `backend/api/app.py`, `backend/orchestration/gateway.py` — likely touched by other stages; hunks are small and additive.
3. Shared docs (`DECISIONS.md`, `BLOCKERS.md`, `IMPLEMENTATION_PROGRESS.md`, `CHANGE_MANIFEST.md`, register) — merge by section.
4. Migration number `0012` (see §3).
5. **Catalogue construction**: once Stage 12 numbers exist in `control.tool_identifiers` for a namespace, every
   `ToolCatalogue` for that namespace must be built with `extra_numbers` (as `attach()` does); a catalogue without it
   reports "numbers disappeared" and allocates nothing (fail-closed). `build_controller_service` + `attach_to_controller_service`
   already do this in the API process; any other construction path must call `attach()`.
6. `RuntimeController.plan/submit_contract`, `allocator` and `ExecutionGateway.execute` are wrapped at attach time
   (composition). A later stage replacing these attributes must keep the wrappers (or call `attach()` afterwards).

## 9. Blocked tests / dependencies

No Stage 12 test is blocked or skipped in the sidecar except S12-T48, which needs Worker A's Stage 01B runtime and is
skipped with an explicit BLOCKED reason on the 01A baseline — it RAN AND PASSED in the 01B trial tree. BLOCKED dependencies are demonstrated as BLOCKED behaviour (BLOCKERS S12-B01…B09):
OS isolation (01B), credential broker / approvals / authenticated routes (02), model inference (03), S12 build gate
(roadmap), S01A-T51 in this container (environment, identical on baseline).

## 10. Required post-merge tests

0. `installer/install.sh --target <merged tree> --dry-run` (expect DRY_RUN_OK; resolve any CONFLICT by hand), then install.
1. `python -m backend.database migrate` (applies 0012) on the merged database.
2. `scripts/stage12_verify.py` — all S12 tests + REG suites (expect the same baseline-identical regression results).
3. With Stage 01B: RESTRICTED plugin executes only under the real sandbox; AIR_GAPPED from the 01B profile; T35/T45 again.
4. With Stage 02: credential broker (T36 must become executable with a stored credential; undeclared refs refused),
   approval of a medium `create` (T32 path), authenticated `/v1/integrations` routes (T60 with an authorised principal),
   identity-bound audit actors.
5. With Stage 04: UI against `integrations.contracts.generated.json`; re-export contracts if models changed.
6. Build-controller gate for the reconciled stage, with the sealed criteria and OS protection (S01A-T51) on a provisioned host.
