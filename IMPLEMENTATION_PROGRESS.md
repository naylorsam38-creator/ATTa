# IMPLEMENTATION PROGRESS

| Stage | Title | Status | Gate evidence |
|---|---|---|---|
| **S01** | Architecture and build infrastructure | **GATE PASSED** 2026-11-25 (run `run_01M88CEBEHP90WASN470WNT557`, 9/9 tasks, 25/25 tests); verified checkpoint `checkpoint-S01-70WNT557` at `fa406b3` | STAGE_01_VERIFICATION.md, `evidence/build_controller/` |
| **S01A** | Deterministic LLM control, tool allocation and drift prevention | **GATE PASSED** 2026-11-25 (run `run_01M88H4W3NJ3NSPWSHPV29RR48`, 10/10 tasks incl. Checkpoints A and B, 61/61 test entries; an earlier run FAILED and was fixed — see report); verified checkpoint `checkpoint-S01A-PV29RR48` at `5662dd2` | `evidence/build_controller/`, `evidence/stage_01a/` |
| S02 | Authentication, tenant isolation, permissions, credential protection, human approvals → controller | NOT STARTED — not to be started in this stage | — |
| S03 – S20 | revised roadmap: `configuration/build/roadmap.yaml` | NOT STARTED | — |

**The application is not production-ready.** Stages 01 and 01A deliver infrastructure and the runtime control
foundation only; 0 of 40 capabilities are complete (CAP-39 and CAP-40 are PARTIAL foundations). No model
inference exists yet (Stage 03). See MASTER_IMPLEMENTATION_REGISTER.md.

## Stage 01 — what was built

| Deliverable | Where | Verified by |
|---|---|---|
| Repository structure (all mandated directories, additive to ATTa v121.5) | repo root | S01-T01 |
| Dependency selection + locks (uv.lock, hash-locked requirements, package-lock.json ×2) | `pyproject.toml`, `uv.lock`, `configuration/locks/`, `package-lock.json`, `apps/mobile/package-lock.json` | S01-T02 |
| Installers / operational entry points | `INSTALL.bat START.bat STOP.bat HEALTHCHECK.bat RECOVERY.bat` + `install.sh start.sh stop.sh healthcheck.sh recovery.sh` | S01-T02, T03, T09, T14 (Linux); Windows: BLOCKERS B-002 |
| Configuration management (layered, validated, secrets outside source control) | `configuration/`, `backend/settings/` | S01-SUP-01, S01-T13 |
| Shared backend contracts | `backend/contracts/` | S01-SUP-01 |
| Versioned API with real liveness/readiness | `backend/api/` | S01-T04, T05 |
| Central service manager | `backend/service_manager/` | S01-T03, T10, T11, T14 |
| Capability registry (automatic discovery, validation, dedupe, dependency resolution, health) | `backend/capability_registry/`, `capabilities/` | S01-T06 – T09 |
| Policy engine (default DENY, no self-grant), RLS default deny | `backend/policy_engine/`, `database/policies/` | S01-T13, S01-SUP-01 |
| Build controller (ordering, file authorisation, sealed criteria, ledger, gates, checkpoints, recovery) | `backend/verification/`, `configuration/build/` | S01-T12 |
| Database migrations (pgvector asserted) | `database/`, `backend/database/` | S01-SUP-01 |
| Model gateway endpoint inventory + health (no downloads, no paid calls) | `backend/model_gateway/` | S01-T10 |
| TypeScript packages (shared_types, api_client, design_system, capability_sdk) | `packages/` | S01-SUP-02, SUP-03 |
| Web (Next.js 16 status page), desktop (Electron 45 shell), mobile (React Native 0.88 app) | `apps/` | S01-SUP-02 – SUP-05 (mobile/desktop runtime: B-005, B-010) |
| Docker Compose + Terraform (local, zero-cost) | `infrastructure/` | `docker compose config`, `terraform fmt -check` (validate: B-004) |
| Requirement register (40 capabilities) | `documentation/requirements/`, `MASTER_IMPLEMENTATION_REGISTER.md` | S01-T15 |
| Regression protection of existing ATTa functionality | `tests/regression/legacy_baseline.json` | S01-T16 |

## Open items carried forward

See BLOCKERS.md (B-001 … B-012). Stage 02 starts with B-001 and B-009 (operator seal).

## Stage 01A — what was built

| Package | Deliverable | Where | Verified by |
|---|---|---|---|
| 01 | Persistent versioned outcome contracts (original instructions immutable), 31-field task contracts with SHA-256 hashes, append-only `control` schema (DB-enforced) | `backend/orchestration/{contracts,store,states,authorization}.py`, `database/migrations/0002_runtime_controller.sql` | S01A-T01, T02, T06, T07, T08, T22 |
| 02 | Permanent `CCC.TTT` tool numbers (DB-enforced permanence), tool manifests, deterministic allocation (≤ 4 tools), trusted/untrusted context packets | `backend/orchestration/{catalogue,allocation,context}.py`, `backend/capability_registry/numbering.py`, `configuration/tool_numbers.yaml` | T03, T04, T05, T09, T12, T45, T46 |
| 03 | Focus-4 controller (row-locked slots), dependency readiness, state machine in code + DB trigger, resource locks | `backend/orchestration/controller.py` | T13, T14, T15, T16, T33, T52 |
| 04 | 18-step execution gateway, structured proposals with bounded corrections, deterministic drift guard, argument-bound policy | `backend/orchestration/{gateway,proposals,drift}.py`, `backend/policy_engine/` | T10, T11, T17–T21, T23, T31, T32 |
| A | Checkpoint A: packages 01–04 together | — | S01A-CPA-01/02 |
| 05 | Atomic persistent budgets, supervised process runtime (subreaper, tree kill), cancellation, persistent idempotent executions, uncertain-outcome handling | `backend/capability_runtime/`, `configuration/controller.yaml` | T24–T30, T40, T44 |
| 06 | Independent verification, bounded repair tasks, requirement traceability | `backend/orchestration/{verification,traceability}.py` | T34–T37, T47 |
| 07 | Verified checkpoints + restart recovery, controller as essential service, `/v1/health/controller`, model-adapter interface | `backend/orchestration/{service,models}.py`, `backend/api/`, `backend/service_manager/` | T38, T39, T50 |
| 08 | Defect corrections (live health, topological propagation, no untrusted imports, DB privilege separation, Ed25519 seal + OS protection), regressions | `backend/capability_registry/`, `backend/service_manager/health_monitor.py`, `backend/verification/`, `scripts/protect_verifier.sh` | T41–T43, T48, T49, T51 |
| B | Checkpoint B: whole Stage 01 + 01A application | — | T53, S01A-CPB-01…06 |

Stage 01A §24 defect corrections: 1 thread timeouts → supervised processes (T29, T30) · 2 propagation (T41) ·
3 live health (T42) · 4 no untrusted imports (T43) · 5 persistent atomic idempotency for controller executions
(T44; API-level for authenticated callers: Stage 02) · 6 argument binding in policy requests (T47 trace shows
`arguments_hash` bound) · 7 runtime vs migration DB privileges (readiness check + T02/T22/T33/T39 SQL denials) ·
8 Ed25519 seal + OS protection (T51; independence of keys: BLOCKERS B-009/B-016).

## Stage 11 sidecar — web search and deep research (CAP-11, CAP-12; roadmap S08)

**Status: SIDECAR — implemented and verified with deterministic local fixtures; NOT merged, NOT gate-sealed by the
build controller; live provider and model integration BLOCKED.** Baseline: `21104c4` (Stage 01A gate passed).
Details: STAGE_11_VERIFICATION.md, STAGE_11_MERGE_MANIFEST.md, documentation/stage11/.

| Deliverable | Where |
|---|---|
| Research contracts (versioned, hashed), 5 modes, trusted ceilings | `backend/research/contracts.py`, `config.py`, `configuration/research.yaml` |
| Provider-independent search interface (SearXNG adapter; Brave BLOCKED) | `capabilities/research_web/providers.py`, RESEARCH_PROVIDER_INTERFACE.md |
| Controlled retrieval with SSRF/redirect/size/timeout/type controls | `capabilities/research_web/netguard.py`, `retrieval_policy.py`, `backend/research/urls.py` |
| Typed extraction with metadata provenance | `capabilities/research_web/extract.py` |
| Evidence tools (capture, compare, cite, verify citation) | `capabilities/research_evidence/` |
| Research service on the Stage 01A controller (Focus-4 batches, budgets, checkpoints, resume, cancel) | `backend/research/service.py`, `worker.py`, `planner.py` |
| Persistence (schema `research`, 14 tables) | `database/migrations/0011_stage11_research.sql`, `backend/research/store.py` |
| Claims, citation binding, contradictions, independent verification | `backend/research/claims.py`, `verification.py`, CITATION_MODEL.md |
| Stage 02 / 03 / 04 / 07 / 01B / 15 interfaces | `security.py`, `planner.py`/`claims.py` (Hub), `api.py`, `local_sources.py`, `network.py` |
| Tests | `tests/research/` |
