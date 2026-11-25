# STAGE 11 MERGE MANIFEST — web search and deep research (sidecar)

Produced by Parallel Agent 3. **Not merged, not pushed to the authoritative branch.** For independent review.

## 1. Baseline and final commits

| | |
|---|---|
| Baseline commit | `21104c413c30a3b2714f877892970e7db19a1d19` (Stage 01A gate passed) |
| Supplied archive | `ATTa-stage01A.zip` SHA-256 `ccf1e9435e7e3a810157fb3d5dc6588ffa0a32548a0f73cc4b9005c51f5d5592`, byte-identical to the baseline |
| **Final Stage 11 source commit (qualified, frozen)** | `098cf5283a8e8d3d60f4ecaf2ec6c85919b17b7b` |
| Release candidate | tag `stage11-rc1` = the evidence commit on top of `098cf528` (evidence + documentation only) |
| Branch | `claude/focused-cori-ajjjn0` (isolated; never pushed to or merged into the authoritative branch) |
| Package | `ATTa-STAGE-11-COMPLETE.zip` (changes only; SHA-256 recorded in the hand-off and `evidence/stage_11/final/PACKAGE_SHA256.txt`) |
| History | `history/stage11.bundle` in the package (`git fetch stage11.bundle HEAD:refs/heads/stage11` in a clone containing the baseline) |

## 2. Files (baseline → final source commit `098cf528`)

Totals: 90 added, 6 modified, 0 deleted (the evidence commit adds further files under `evidence/stage_11/final/`
and updates `STAGE_11_*.md`). Per-file SHA-256: the package's `SHA256SUMS`; the Stage 11 section of `CHANGE_MANIFEST.md`.

| Area | Added | Modified |
|---|---|---|
| `(root)` | `STAGE_11_ARCHITECTURE.md`<br>`STAGE_11_CHANGE_MANIFEST.md`<br>`STAGE_11_MERGE_MANIFEST.md`<br>`STAGE_11_REQUIREMENT_MATRIX.md`<br>`STAGE_11_VERIFICATION.md` | `BLOCKERS.md`<br>`CHANGE_MANIFEST.md`<br>`DECISIONS.md`<br>`IMPLEMENTATION_PROGRESS.md`<br>`MASTER_IMPLEMENTATION_REGISTER.md` |
| `backend/research` | `backend/research/__init__.py`<br>`backend/research/api.py`<br>`backend/research/claims.py`<br>`backend/research/config.py`<br>`backend/research/contracts.py`<br>`backend/research/dedup.py`<br>`backend/research/errors.py`<br>`backend/research/evidence.py`<br>`backend/research/hub.py`<br>`backend/research/ids.py`<br>`backend/research/local_sources.py`<br>`backend/research/network.py`<br>`backend/research/operator_config.py`<br>`backend/research/planner.py`<br>`backend/research/provider_check.py`<br>`backend/research/quality.py`<br>`backend/research/security.py`<br>`backend/research/service.py`<br>`backend/research/store.py`<br>`backend/research/urls.py`<br>`backend/research/verification.py`<br>`backend/research/wiring.py`<br>`backend/research/worker.py` | — |
| `capabilities/research_evidence` | `capabilities/research_evidence/capability.yaml`<br>`capabilities/research_evidence/handler.py` | — |
| `capabilities/research_web` | `capabilities/research_web/capability.yaml`<br>`capabilities/research_web/extract.py`<br>`capabilities/research_web/handler.py`<br>`capabilities/research_web/netguard.py`<br>`capabilities/research_web/providers.py`<br>`capabilities/research_web/retrieval_policy.py` | — |
| `configuration` | `configuration/research.yaml` | — |
| `database` | `database/migrations/0011_stage11_research.sql` | — |
| `documentation` | — | `documentation/requirements/requirement_register.yaml` |
| `documentation/stage11` | `documentation/stage11/CITATION_MODEL.md`<br>`documentation/stage11/RESEARCH_ARCHITECTURE.md`<br>`documentation/stage11/RESEARCH_PROVIDER_INTERFACE.md`<br>`documentation/stage11/SOURCE_SECURITY.md`<br>`documentation/stage11/STAGE03_MODEL_GATEWAY_REQUIREMENTS.md`<br>`documentation/stage11/integration/research.providers.example.yaml`<br>`documentation/stage11/integration/stage02_research_permissions.yaml`<br>`documentation/stage11/integration/stage04_app_wiring.patch`<br>`documentation/stage11/permissions.stage11.yaml`<br>`documentation/stage11/tool_numbers.stage11.yaml` | — |
| `evidence/stage_11` | `evidence/stage_11/S11-CANCELLATION.json`<br>`evidence/stage_11/S11-CREDENTIAL-ISOLATION.json`<br>`evidence/stage_11/S11-EVIDENCE-TOOLS.json`<br>`evidence/stage_11/S11-HUB-INTEGRATION.json`<br>`evidence/stage_11/S11-INJECTION-CONTAINED.json`<br>`evidence/stage_11/S11-MIGRATION.json`<br>`evidence/stage_11/S11-OFFLINE-AIR_GAPPED.json`<br>`evidence/stage_11/S11-OFFLINE-OFFLINE.json`<br>`evidence/stage_11/S11-PIPELINE-STANDARD.json`<br>`evidence/stage_11/S11-RESTART-RECOVERY.json`<br>`evidence/stage_11/SHA256SUMS`<br>`evidence/stage_11/runs/legacy_baseline.log`<br>`evidence/stage_11/runs/legacy_baseline.xml`<br>`evidence/stage_11/runs/legacy_sidecar.log`<br>`evidence/stage_11/runs/legacy_sidecar.xml`<br>`evidence/stage_11/runs/platform_baseline.log`<br>`evidence/stage_11/runs/platform_baseline.xml`<br>`evidence/stage_11/runs/platform_sidecar.log`<br>`evidence/stage_11/runs/platform_sidecar.xml`<br>`evidence/stage_11/runs/s11.log`<br>`evidence/stage_11/runs/s11.xml` | — |
| `packaging/stage11` | `packaging/stage11/INSTALL.md`<br>`packaging/stage11/compat/check_compat.py`<br>`packaging/stage11/install.bat`<br>`packaging/stage11/install.ps1`<br>`packaging/stage11/install.py`<br>`packaging/stage11/install.sh` | — |
| `scripts` | `scripts/build_stage11_package.py` | — |
| `tests/research` | `tests/research/conftest.py`<br>`tests/research/fixture_server.py`<br>`tests/research/fixtures/envprobe/capability.yaml`<br>`tests/research/fixtures/envprobe/handler.py`<br>`tests/research/harness.py`<br>`tests/research/test_s11_api.py`<br>`tests/research/test_s11_contracts.py`<br>`tests/research/test_s11_extraction_dedup.py`<br>`tests/research/test_s11_installer.py`<br>`tests/research/test_s11_integration_kits.py`<br>`tests/research/test_s11_pipeline.py`<br>`tests/research/test_s11_qualification.py`<br>`tests/research/test_s11_registration_tools.py`<br>`tests/research/test_s11_url_security.py` | — |

Deleted files: **none**.

## 3. Migrations

| File | Depends on | Notes |
|---|---|---|
| `database/migrations/0011_stage11_research.sql` | 0001, 0002 (runtime role `atta_app`) | new schema `research`, 14 tables; numbered 0011 to avoid collisions with parallel stages' 0003+; references no other stage's tables; applied by the existing `python -m backend.database migrate` (checksummed ledger) |

Tables: `research.runs` (research_runs), `research.contract_versions`, `research.budgets`, `research.queries`
(research_queries), `research.snapshots` (source_snapshots / evidence store), `research.sources` (research_sources),
`research.evidence`, `research.claims` (research_claims), `research.claim_sources` (claim_sources),
`research.citations` (research_citations), `research.contradictions` (research_contradictions),
`research.checkpoints` (research_checkpoints), `research.events` (research_events), `research.results`.
Append-only by trigger: contract_versions, queries, snapshots, sources, evidence, claim_sources, contradictions,
checkpoints, events, results. Guarded mutable: runs (identity immutable; final statuses frozen; cancellation cannot
be withdrawn; a cancelled run can never become COMPLETED), budgets (limits never raised, consumption never
decreases), claims (content immutable), citations (binding immutable). `tenant_id`/`principal_id` columns exist for
Stage 02 RLS; no RLS policies are added (same as the `control` schema at Stage 01A).

## 4. Capability and tool identifiers

| Capability | Requirement | Package |
|---|---|---|
| `research.web` | CAP-11 (Web search) | `capabilities/research_web/` |
| `research.evidence` | CAP-12 (Deep research) | `capabilities/research_evidence/` |

| Number | Tool | Operation | Task types | Permissions |
|---|---|---|---|---|
| 011.001 | `research.web:search` | search | research.search | research.search.query, workspace.read |
| 011.002 | `research.web:fetch_source` | fetch | research.retrieve | network.web.read, workspace.write |
| 011.003 | `research.web:extract_content` | extract | research.retrieve, research.local, research.follow | workspace.read, workspace.write |
| 011.004 | `research.web:inspect_metadata` | inspect | research.retrieve, research.local, research.analyse | workspace.read |
| 011.005 | `research.web:follow_link` | fetch | research.follow | network.web.read, workspace.read, workspace.write |
| 011.006 | `research.web:search_within_source` | find | research.retrieve, research.local, research.follow, research.analyse | workspace.read |
| 012.001 | `research.evidence:capture_evidence` | capture | research.analyse, research.retrieve, research.local | workspace.read |
| 012.002 | `research.evidence:compare_sources` | compare | research.compare, research.analyse | workspace.read |
| 012.003 | `research.evidence:construct_citation` | cite | research.cite | workspace.read |
| 012.004 | `research.evidence:verify_citation` | verify | research.cite | workspace.read |

Reconciled with the current registry: production numbering has only `040.001`; no existing tool uses 011/012; no
existing tool provides these operations (`test_proposed_numbers_reconcile_with_existing_numbering`). All tools are
LOW risk, read-oriented (no form submission, POST, purchases, messages, account changes, browser or desktop control).
Checkpoint / resume / cancel / authoritative citation verification are deliberately service operations, not tools
(DECISIONS D-S11-04).

## 5. Operator actions required to make Stage 11 executable (protected files — not edited by the sidecar)

1–2. `python -m backend.research.operator_config check` then `render --out <review dir>`: merged copies of
   `configuration/tool_numbers.yaml` (+ `documentation/stage11/tool_numbers.stage11.yaml`) and
   `configuration/permissions.yaml` (+ `documentation/stage11/permissions.stage11.yaml`: `research.search.query`,
   `network.web.read` + controller grant) with their SHA-256; the tool never writes into `configuration/`.
   The operator reviews and copies them into place.
3. Sign both packages into `configuration/capability_trust.json` with the operator trust key
   (`python -m backend.capability_registry trust capabilities/research_web --key <operator private key> --operator NAME`,
   same for `capabilities/research_evidence`).
4. Re-seal the build controller's protected files.
5. Configure a provider in `configuration/research.yaml` (optional; without it external search is BLOCKED);
   validate with `python -m backend.research.provider_check`; example:
   `documentation/stage11/integration/research.providers.example.yaml`.
6. Stage 02 (when it exists): register the API operations/permissions/approvals in
   `documentation/stage11/integration/stage02_research_permissions.yaml`.
Until then the production registry reports both packages `untrusted` and nothing in Stage 11 can execute (fail closed).

## 6. API additions (contract only, not mounted)

`backend/research/api.py` → `build_research_router()`. Stage 04 mounts it with ONE call after the app is created:
`from backend.research.wiring import wire_research_api; wire_research_api(app)` — idempotent; the service is resolved
lazily from `app.state.controller_service` (the one controller). The exact additive patch for `backend/api/app.py`
(two lines; applies to the baseline app.py; verified by q::test_stage04_app_patch_applies_and_wires_the_api) is
`documentation/stage11/integration/stage04_app_wiring.patch` — apply it only together with Stage 04 so no other
worker's app.py changes are overwritten. Every route evaluates the central policy
engine (operations `research.create|read|list|start|cancel`) → **DENIED until Stage 02**.

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/v1/research` | `CreateResearchRequest` | `ResearchView` (201) |
| GET | `/v1/research` | `limit`, `cursor` | `Page` |
| GET | `/v1/research/{research_id}` | — | `ResearchView` |
| POST | `/v1/research/{research_id}/start` | `StartRequest` (start/resume; approval) | `Progress` (202) — Stage 02 |
| POST | `/v1/research/{research_id}/cancel` | `CancelRequest` | `Progress` |
| GET | `/v1/research/{research_id}/progress` | — | `Progress` |
| GET | `/v1/research/{research_id}/queries` | `limit`, `cursor` | `Page` of query records |
| GET | `/v1/research/{research_id}/sources` | `limit`, `cursor` | `Page` of source records |
| GET | `/v1/research/{research_id}/sources/{source_id}` | — | source record |
| GET | `/v1/research/{research_id}/claims` | `limit`, `cursor` | `Page` of claims |
| GET | `/v1/research/{research_id}/citations` | `limit`, `cursor` | `Page` of citations |
| GET | `/v1/research/{research_id}/contradictions` | `limit`, `cursor` | `Page` of contradictions |
| GET | `/v1/research/{research_id}/checkpoint` | — | latest checkpoint |
| GET | `/v1/research/{research_id}/result` | — | `ResearchResult` |
| GET | `/v1/research/{research_id}/events` | `limit`, `cursor` | `Page` of audit events |

Errors use the existing envelope `{"error": {code, message, correlation_id, details}}`.

## 7. Stage 04 (UI) contracts — owned by Agent 2

| UI need | Data contract |
|---|---|
| Research creation form | `CreateResearchRequest` (question, research_type enum of 5 modes, scope, subquestions, claims_to_verify, freshness, source/domain/time restrictions, citation/output requirements, budget ≤ mode ceiling, follow_links, provider_ids) |
| Approval prompt | `ResearchView.approval_required` = `{action, subject, payload_hash}` (Stage 01A operator approval; Stage 02 human approval later) |
| Progress | `Progress`: status, phase, batch, verification_status, cancel_requested, error `{code,message,details}`, budgets `{metric: {limit, consumed, remaining}}`, tasks `[{task_id, state, kind}]`, `active_subtasks ≤ 4`, counts, latest_checkpoint |
| Queries | rows of `research.queries` (query_text, provider, status, error_code, results[rank,title,url,snippet,source,published_at,retrieved_at]) |
| Sources | `ResearchResult.sources[]` (URLs, title, publisher, author, published_at, retrieved_at, content hash, extraction status, error code, quality factors, duplicate_of/relation, independence group, depth) |
| Claims / citations | `claims[]` (support/contradiction/verification status), `citations[]` (locator, span, metadata, verified) |
| Contradictions | `contradictions[]` (claims, supporting/contradicting sources, date/scope differences, status, detail) |
| Checkpoint / cancel / resume | checkpoint document; cancel → `Progress`; resume = `StartRequest{resume: true}` |
| Final result | `ResearchResult` (objective, scope, summary, findings, claims, citations, sources, contradictions, limitations[{code, detail, …}], unresolved_questions, research_status, verification_status, verification report, synthesis{status: BLOCKED}) |

Statuses: `CREATED, RUNNING, PAUSED, CANCELLING, CANCELLED, COMPLETED, COMPLETED_WITH_LIMITATIONS, FAILED, BLOCKED`.
Verification: `NOT_VERIFIED, VERIFIED, VERIFICATION_FAILED`. Error codes: §35 list (`backend/research/errors.py`).

## 8. Dependencies

| Kind | Addition |
|---|---|
| Python packages | **none** (stdlib in handlers; backend uses already-locked pydantic, psycopg, PyYAML, jsonschema, fastapi) |
| Node packages | none |
| Services | none new (PostgreSQL, existing supervised runtime) |
| Configuration | new `configuration/research.yaml` (providers, network mode, ceilings, Focus-4 cap, evidence selection) |
| Environment | optional `ATTA_NETWORK_MODE` = ONLINE / OFFLINE / AIR_GAPPED (unknown → AIR_GAPPED) |

## 9. Cross-stage dependencies

| Stage | Interface in this sidecar | State |
|---|---|---|
| Stage 01A | controller, gateway, Focus-4, budgets, verification, authorisation, catalogue | used, unchanged |
| Stage 01B | `backend/research/network.py` `NetworkModeProvider` | consumed; pass the 01B provider to `ResearchService(network=…)` at merge |
| Stage 02 | `backend/research/security.py` `ResearchSecurityContext` / `ResearchSecurityProvider`; `research.runs.tenant_id/principal_id`; API policy operations; credentialed providers | BLOCKED until Stage 02 |
| Stage 03 | `HubPlanner`, `HubClaimBuilder` (`hub.build_claims`), `ModelAdapter` for the research worker; synthesis slot `ResearchResult.synthesis` | BLOCKED until Stage 03 |
| Stage 04 | §6, §7 | contracts only |
| Stage 07 | `backend/research/local_sources.py` `ProjectKnowledgeSource` | BLOCKED until Stage 07 |
| Stage 15 (roadmap S16) | thin HTML pages reported as `BLOCKED_DEPENDENCY: interactive-browser`; no browser automation here | BLOCKED |

## 10. Shared files modified

Only documentation/registers, all additive: `DECISIONS.md`, `BLOCKERS.md`, `IMPLEMENTATION_PROGRESS.md`,
`CHANGE_MANIFEST.md` (new Stage 11 section), `documentation/requirements/requirement_register.yaml` (CAP-11, CAP-12
entries only) and the rendered `MASTER_IMPLEMENTATION_REGISTER.md`. No runtime source file outside the new
directories was modified.

## 11. Known conflicts / merge notes

* Shared documents (`CHANGE_MANIFEST.md`, `DECISIONS.md`, `BLOCKERS.md`, `IMPLEMENTATION_PROGRESS.md`,
  `requirement_register.yaml`, `MASTER_IMPLEMENTATION_REGISTER.md`): the installer merges them by explicit rules
  (union for append-only logs, strict 3-way for the register YAML, master register regenerated); a conflict it
  cannot resolve stops the installation with nothing changed (DECISIONS D-S11-15).
* Migration numbering: if the integrator wants contiguous numbers, `0011` can be renamed **before** it is applied
  anywhere (the ledger is checksummed by name).
* `backend/api/app.py`: not edited; Stage 04 adds `wire_research_api(app)` (one line).
* Stage 01B / Stage 04 compatibility: run `compat/check_compat.py` with the actual packages (not available here;
  BLOCKERS B-S11-11).
* Stage numbering: plan "Stage 11" = roadmap S08 (DECISIONS D-S11-01).
* Build-controller seal: Stage 11 is not a sealed build-controller stage (no `configuration/build/stages/S08.yaml`
  was created, because that directory is protected).

## 12. Post-merge tests

```
ATTA_ENV=test python -m backend.database migrate
ATTA_ENV=test python -m pytest -q tests/research                       # Stage 11
ATTA_ENV=test python -m pytest -q tests/unit tests/security tests/integration tests/acceptance   # Stage 01/01A
python -m pytest -q tests/test_*.py                                     # legacy ATTa
python scripts/requirement_register.py check
```
Then, after the operator actions of §5: discover the production registry and confirm `research.web` /
`research.evidence` are `available` and numbered; with a configured provider run one live QUICK_SEARCH and record it.

## 13. Installation package (transactional)

`ATTa-STAGE-11-COMPLETE.zip`, built deterministically by `scripts/build_stage11_package.py --release` from the
sidecar commit. Installer `install.py` (+ `install.bat`, `install.ps1`, `install.sh`): checksum verification of
every package file → target rules (top level of a clean, non-nested Git repository with Stage 01A) → plan
(conflicts = exit 3, also in `--check`) → full rehearsal in a temporary `git worktree` → journal + backups →
apply → verification (content, exact `git status` set, protected files unchanged) → automatic rollback on any
failure; `--rollback-interrupted` after a hard kill. Tests: `tests/research/test_s11_installer.py`. Details and
exit codes: `packaging/stage11/INSTALL.md` (shipped as the package's INSTALL.md).

## 14. Compatibility harness

`packaging/stage11/compat/check_compat.py` (shipped as `compat/check_compat.py`): combines the Stage 01A baseline
with the actual Stage 01B / Stage 04 packages in a temporary repository, runs static overlap checks and this
installer, and optionally all test suites (new failures separated from documented pre-existing ones).
Run against the clean Stage 01A baseline: COMPATIBLE (STAGE_11_VERIFICATION.md run 6). Runs against the actual
Stage 01B / Stage 04 packages are BLOCKED until they are supplied (B-S11-11); the actual Stage 12 sidecar
(`claude/lucid-maxwell-uc91v4` @ `732ba5e`) exists but its compatibility run is still to be performed (B-S11-13).

## 15. Installation and rollback commands

```
# verify the zip SHA-256 against the hand-off, unzip, then (Linux/macOS | Windows cmd | PowerShell):
python install.py <ATTa root> --check            install.bat C:\ATTa --check        .\install.ps1 C:\ATTa --check
python install.py <ATTa root> --report /tmp/stage11-install.json
git -C <ATTa root> add -A && git -C <ATTa root> commit -m "Stage 11: web search and deep research"
ATTA_ENV=<env> python -m backend.database migrate
# alternative for an unchanged 01A branch:  git fetch history/stage11.bundle HEAD:refs/heads/stage11 && git merge stage11
```
Rollback: failed installs roll back automatically (exit 5); a killed install: `python install.py <root>
--rollback-interrupted`; installed but uncommitted: `git checkout -- $(cat MODIFIED_FILES.txt)` + delete
`ADDED_FILES.txt` paths; committed: `git revert <commit>`; database: `DROP SCHEMA research CASCADE;` and
`DELETE FROM public.atta_schema_migrations WHERE name = 'migration:0011_stage11_research.sql';` (migration role).

## 16. Remaining external dependencies

Live search provider (B-S11-01) · operator trust/numbering/permission activation (B-S11-02) · Stage 03 model
(B-S11-03, B-S11-14) · Stage 02 credentials/API/approvals (B-S11-04, B-S11-05) · Stage 07 (B-S11-06) · Stage 15
browser (B-S11-07) · PDF parser (B-S11-08) · network-namespace isolation (B-S11-09) · Stage 01B provider (B-S11-10) ·
actual Stage 01B/04 packages (B-S11-11) · Windows execution (B-S11-12) · Stage 12 package (B-S11-13) · upgrade from a
01B-migrated database (B-S11-15). Requirement-level status: STAGE_11_REQUIREMENT_MATRIX.md.

