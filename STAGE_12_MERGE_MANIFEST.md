# STAGE 12 MERGE MANIFEST — Connectors, MCP, Skills & Plugins (Parallel Agent 4 sidecar)

**Status: IMPLEMENTED IN SIDECAR · VERIFIED IN SIDECAR · NOT YET INTEGRATED.**
This sidecar has not been merged into the authoritative application, and contains no work from Stage 01B, 04 or 11.

## 1. Baseline and branch

| | |
|---|---|
| Baseline (Stage 01 + 01A, gate passed) | `21104c413c30a3b2714f877892970e7db19a1d19` on `origin/claude/ai-app-stage-01-foundation-82jy3a` |
| Note | The supplied working branch `claude/lucid-maxwell-uc91v4` initially pointed at ATTa v121.5 (`cef7329`) **without** Stage 01/01A; the Stage 01A gate commit above was used as the authoritative baseline |
| Sidecar branch | `stage-12-integrations-sidecar` (local), published as `claude/lucid-maxwell-uc91v4` |
| Verified commit | `e137a92` (run `s12run_20261125T070151Z`); later commits contain documentation/evidence only |

## 2. Files

**Deleted files: NONE.** Full per-file list with SHA-256: `STAGE_12_CHANGE_MANIFEST.md`.

### Modified (existing) files

| File | Change | Why |
|---|---|---|
| `backend/orchestration/gateway.py` | +22 lines: `guards` and `result_filters` lists (default empty) and their loops at step 9b / 16b | Extension points; a guard can only refuse, a filter can only keep or reject. Empty = Stage 01A behaviour |
| `backend/orchestration/catalogue.py` | +26 lines: optional `extra_numbers` constructor argument + `merge_extra_numbers()` | Stage 12 permanent numbers in the SAME catalogue; collisions make nothing allocatable |
| `backend/api/app.py` | +6 lines: include the Stage 12 router; attach integrations in lifespan (failure never blocks start) | API contracts for Stage 04 |
| `.gitignore` | +2 lines (append only) | `configuration/integrations.local.yaml` |
| `DECISIONS.md`, `BLOCKERS.md`, `IMPLEMENTATION_PROGRESS.md` | appended Stage 12 sections | shared records (merge by section) |
| `documentation/requirements/requirement_register.yaml`, `MASTER_IMPLEMENTATION_REGISTER.md` (rendered) | CAP-21..24 NOT_STARTED → PARTIAL with sidecar note/evidence | register |
| `CHANGE_MANIFEST.md` | Stage 12 section prepended; Stage 01/01A sections untouched | |

No protected/sealed file (`configuration/build/**`, `tests/acceptance/**`, `backend/verification/**`,
`configuration/{controller.yaml,permissions.yaml,tool_numbers.yaml,capability_trust.json,*.pub}`,
`tests/regression/legacy_baseline.json`) and no legacy ATTa file was modified.

### Added

* `backend/integrations/` — `types, content, manifest, integration.schema.json, config, credentials, network, isolation,
  assessment, packaging, store, manager, runtime, verification, skills, upgrade, service, api, __main__`;
  `mcp/{wire,client,validation}.py`; `bridges/{mcp_bridge,verify,health}.py.tmpl` (templates)
* `database/migrations/0012_stage12_integrations.sql`
* `configuration/integrations.yaml`, `configuration/integrations/{mcp_servers.yaml,permissions.proposed.yaml}`
* `integrations/README.md`, `integrations/available/.gitkeep` (empty source directory — no production fixtures)
* `packages/shared_types/integrations.contracts.generated.json`
* `scripts/{stage12_verify.py,stage12_report.py,export_integration_contracts.py}`
* `tests/integrations/` — 6 test modules (38 tests), harness, HTTP MCP fixture, fixture builder, `fixtures/` (14 packages
  + stdio MCP test server) — **test-only**
* `documentation/{INTEGRATION_ARCHITECTURE,INTEGRATION_MANIFEST_SPEC,MCP_ARCHITECTURE,CONNECTOR_ARCHITECTURE,SKILL_ARCHITECTURE,PLUGIN_SECURITY}.md`
* `STAGE_12_VERIFICATION.md`, `STAGE_12_MERGE_MANIFEST.md`, `STAGE_12_CHANGE_MANIFEST.md`, `evidence/stage_12/`

## 3. Migrations

`0012_stage12_integrations.sql` — new schema `integrations` only (18 tables, append-only triggers, runtime-role grants).
Depends only on `0001`/`0002` (reads `control.tool_identifiers`, `control.tasks`, `control.task_contracts`). If another
stage also chose `0012`, rename this file (no other file references its name) **before** it is applied anywhere;
applied migrations are checksum-immutable.

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

| Stage | Point | What to do at merge |
|---|---|---|
| **01B** (deployment/offline) | `ConnectivityMode` / `NetworkPolicy` (`backend/integrations/network.py`), `IsolationProvider` (`isolation.py`), configuration hierarchy | Feed 01B's AIR_GAPPED/ONLINE profile into `IntegrationsConfig.connectivity`; implement `IsolationProvider` with the real sandbox (unblocks RESTRICTED + `ISOLATED_SANDBOX`); fold `integrations.yaml` into the 01B settings hierarchy; place `install_root` under the 01B runtime path |
| **02** (identity/approvals/credentials) | `CredentialBroker` protocol (`credentials.py`), `OperatorAuthorization` uses in `manager.py`, policy operations `integration.*`, `ExecutionGuard`/audit actors, side-effect + risk metadata on every tool, `permissions.proposed.yaml` | Implement the broker (declared-only resolution, no enumeration); replace operator Ed25519 approvals with authenticated human approvals bound to `approval_binding`; add policy rules for `integration.*`; approve medium+ tool calls using `side_effect`/`risk`/`arguments_hash`; set actors from authenticated principals; add proposed permissions to `permissions.yaml` and re-seal |
| **03** (model/Hub) | Context packets: integration tool descriptions (validated) in `trusted.tools`, outputs in `untrusted` with provenance; `IntegrationService.list_scripts()` | Qwen adapter consumes the packets unchanged; expose `list_scripts` to the Hub as data |
| **04** (interface) | `backend/integrations/api.py`, `service.py` typed models, `integrations.contracts.generated.json`, UI extension slots (`integration.settings`, `integration.panel`, `task.sidebar`), health/events | Build integration gallery, install/approval, health, MCP server, skills/scripts and plugin views on these contracts; `backend/api/app.py` hunk will conflict — keep both router includes |
| **11** (web search / deep research, parallel numbering) | Packaging a search provider as a CONNECTOR or MCP_SERVER; `network_requirements` + connectivity policy; result filter | No code conflict expected. If Stage 11 introduces its own network policy module, reconcile with `network.py` (single policy) |

## 8. Known conflicts / risks

1. **Roadmap numbering**: protected `configuration/build/roadmap.yaml` has S12 = skills/plugins/subscription/admin/data and
   Connectors/MCP = S18, web search = S08. The parallel plan differs. Not changed here (D-S12-11); the operator must
   reconcile and create/seal an S12 stage definition before a build-controller gate can run.
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

No Stage 12 test is blocked or skipped. BLOCKED dependencies are demonstrated as BLOCKED behaviour (BLOCKERS S12-B01…B09):
OS isolation (01B), credential broker / approvals / authenticated routes (02), model inference (03), S12 build gate
(roadmap), S01A-T51 in this container (environment, identical on baseline).

## 10. Required post-merge tests

1. `python -m backend.database migrate` (applies 0012) on the merged database.
2. `scripts/stage12_verify.py` — all S12 tests + REG suites (expect the same baseline-identical regression results).
3. With Stage 01B: RESTRICTED plugin executes only under the real sandbox; AIR_GAPPED from the 01B profile; T35/T45 again.
4. With Stage 02: credential broker (T36 must become executable with a stored credential; undeclared refs refused),
   approval of a medium `create` (T32 path), authenticated `/v1/integrations` routes (T60 with an authorised principal),
   identity-bound audit actors.
5. With Stage 04: UI against `integrations.contracts.generated.json`; re-export contracts if models changed.
6. Build-controller gate for the reconciled stage, with the sealed criteria and OS protection (S01A-T51) on a provisioned host.
