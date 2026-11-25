# STAGE 11 CHANGE MANIFEST (sidecar)

Parallel Agent 3 — Stage 11 web search and deep research. Working branch `stage-11-research-sidecar`.

## Baseline (recorded before any modification)

| Item | Value |
|---|---|
| Baseline commit | `21104c413c30a3b2714f877892970e7db19a1d19` — "Stage 01A: gate passed — verification report, change manifest, progress and evidence" (branch `claude/ai-app-stage-01-foundation-82jy3a`) |
| Supplied baseline archive | `ATTa-stage01A.zip`, SHA-256 `ccf1e9435e7e3a810157fb3d5dc6588ffa0a32548a0f73cc4b9005c51f5d5592` |
| Archive vs commit | byte-identical (`diff -rq` over the whole tree: 0 differences) |
| Contents | ATTa v121.5 application + Stage 01 + Stage 01A |

## Change log

1. Recorded baseline; set up PostgreSQL 16 + pgvector and Redis locally; ran the baseline suites.
2. Added capability packages `capabilities/research_web/` (CAP-11) and `capabilities/research_evidence/` (CAP-12).
3. Added `backend/research/` (contracts, config, network, urls, store, dedup, quality, evidence selection, planner,
   worker, claims, verification, service, api, security, local_sources, ids).
4. Added migration `database/migrations/0011_stage11_research.sql` and `configuration/research.yaml`.
5. Added proposed operator configuration `documentation/stage11/tool_numbers.stage11.yaml`,
   `documentation/stage11/permissions.stage11.yaml` (protected files themselves NOT edited).
6. Added tests `tests/research/` (harness, deterministic HTTP fixture server, 6 test modules).
7. Added documentation `documentation/stage11/` and the stage reports at the repository root.
8. Updated (append-only sections) `DECISIONS.md`, `BLOCKERS.md`, `IMPLEMENTATION_PROGRESS.md`, `CHANGE_MANIFEST.md`;
   updated CAP-11/CAP-12 in `documentation/requirements/requirement_register.yaml` and re-rendered
   `MASTER_IMPLEMENTATION_REGISTER.md` with `scripts/requirement_register.py render`.

9. **Audit corrections (after the preliminary audit was accepted):** transactional cross-platform installer
   (`packaging/stage11/install.py`, `.bat`, `.ps1`, `.sh`) with package checksum verification, clean-tree and
   nested-repository rules, rehearsal, journalled rollback; deterministic package builder
   (`scripts/build_stage11_package.py`); compatibility harness (`packaging/stage11/compat/check_compat.py`);
   controlled-integration kits — Stage 03 `backend/research/hub.py`, Stage 04 `backend/research/wiring.py`,
   Stage 02 `backend/research/operator_config.py` + `documentation/stage11/integration/stage02_research_permissions.yaml`,
   provider `backend/research/provider_check.py` + example configuration; `ResearchService` gained optional
   `hub` / `worker_factory` parameters (defaults unchanged); tests `test_s11_integration_kits.py`,
   `test_s11_installer.py`; `ATTA_REQUIRE_DB=1` makes DB-backed tests fail instead of skipping.
   No functionality was removed or disabled. Commits preserved on the isolated branch `claude/focused-cori-ajjjn0`.

The complete file list with hashes is in STAGE_11_MERGE_MANIFEST.md and in the Stage 11 section of
CHANGE_MANIFEST.md.

No file was deleted. No protected file (`configuration/build/**`, `tests/acceptance/**`,
`tests/regression/legacy_baseline.json`, `backend/verification/**`, `configuration/controller.yaml`,
`configuration/permissions.yaml`, `configuration/tool_numbers.yaml`, `configuration/capability_trust*`,
`configuration/controller_operator_ed25519.pub`) and no legacy ATTa file (`01-specs/` … `05-coolify/`, `docs/`,
`run`, `tests/test_*.py`, …) was modified. No shared runtime file (`backend/api/app.py`, `backend/contracts/**`,
`backend/orchestration/**`, `backend/capability_registry/**`, `backend/policy_engine/**`) was modified.
