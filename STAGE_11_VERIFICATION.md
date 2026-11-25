# STAGE 11 VERIFICATION — web search and deep research (sidecar)

Baseline `21104c4` (Stage 01A gate passed). Sidecar branch `stage-11-research-sidecar`. Environment: Linux,
Python 3.13.18, PostgreSQL 16 + pgvector (local cluster, `database/bootstrap.sql` + `python -m backend.database
migrate`, including `0011_stage11_research.sql`), Redis on the test port, packages from
`configuration/locks/backend-full.lock.txt`. **No Internet access was used by any test**; every HTTP interaction is
against the deterministic local fixture server `tests/research/fixture_server.py`.

**This is not a build-controller stage gate.** The Stage 11 tests are not sealed in `configuration/build/` (protected,
operator-controlled). Results below are reproducible with the commands in §5. Raw logs and JUnit XML:
`evidence/stage_11/runs/`; per-test evidence documents: `evidence/stage_11/*.json`.

## 1. Results

| Suite | Tree | Result |
|---|---|---|
| Stage 11 (`tests/research`, 6 modules) | sidecar | **117 passed, 0 failed** (185.6 s) |
| Stage 01 + 01A (`tests/unit tests/security tests/integration tests/acceptance`) | sidecar | 98 passed, 6 failed — the 6 are pre-existing (below) |
| Stage 01 + 01A | baseline archive (unmodified) | 94 passed, 10 failed — the same 6 + 4 that only fail because the archive copy is not a git checkout (T01, T16, T48, T49 read git history) |
| Legacy ATTa (`tests/test_*.py`) | sidecar | 255 passed, 5 skipped, 8 failed |
| Legacy ATTa | baseline archive | 255 passed, 5 skipped, 8 failed — **identical failure list** |
| Requirement register (`scripts/requirement_register.py check`) | sidecar | valid, 40 requirements |

Pre-existing failures (identical before and after Stage 11, none caused by it):
* `S01-T06/T07/T08/T09/T13` — superseded Stage 01 tests, documented in BLOCKERS B-017 / DECISIONS D-029.
* `S01A-T51` — needs the OS-protection provisioning (`atta-builder` account, root-only verifier key) of the build
  host; running `scripts/protect_verifier.sh` here would regenerate a protected public key, so it was not run
  (BLOCKERS B-016).
* Legacy `test_v119_login_proof` (7) and `test_v121_journey_author::test_login_submit_is_inside_the_password_form` —
  Playwright: Chromium not installed at the path those tests use (`/usr/bin/chromium`) and the sync API refusing to
  run inside an asyncio loop in this environment. Identical on the untouched baseline.

**Regression conclusion:** no test that passes on the baseline fails on the sidecar.

## 2. Required tests (§36) → evidence

| Required test | Test(s) |
|---|---|
| research contract validation | `test_s11_contracts::test_contract_has_all_required_fields_and_hash_is_stable`, `…rejects_unknown_fields_and_inconsistent_limits`, `test_s11_pipeline::test_contract_changes_are_versioned_and_authorised` |
| query-budget enforcement | `test_s11_pipeline::test_query_budget_enforced` |
| source-budget enforcement | `test_s11_pipeline::test_source_and_fetch_budgets_enforced` |
| fetch-budget enforcement | `test_s11_pipeline::test_source_and_fetch_budgets_enforced`, `test_budget_ceiling_and_db_guards` |
| depth limits | `test_s11_pipeline::test_link_depth_limit`, `test_s11_contracts::test_planner_is_bounded_and_hub_is_blocked` |
| Focus-4 research decomposition | `test_s11_pipeline::test_deep_research_uses_focus4_bounded_batches` (observed active slots ≤ 4 across > 4 tasks; ≥ 3 checkpoints), `test_s11_contracts::test_focus4_ceiling_cannot_exceed_four` |
| provider unavailable | `test_s11_pipeline::test_provider_unavailable_never_fabricates`, `test_broken_provider_and_credentialed_provider` |
| offline mode | `test_s11_pipeline::test_offline_mode_external_unavailable_local_research_works[AIR_GAPPED/OFFLINE]`, `test_s11_contracts::test_network_mode_provider` |
| URL validation | `test_s11_url_security::test_unsafe_urls_refused_by_handler_and_planner` (24 cases), `test_safe_url_normalised` |
| SSRF prevention | `test_s11_url_security::test_private_loopback_metadata_and_special_addresses_denied` (25 addresses), `test_dns_rebinding_mixed_answers_denied`, `test_dns_resolving_to_private_denied_and_public_pinned`, `test_s11_pipeline::test_unsafe_search_results_refused_before_and_during_retrieval` |
| redirect validation | `test_s11_url_security::test_fetch_ok_and_same_host_redirect`, `test_unsafe_redirects_refused` (metadata, cross-host, loop, scheme) |
| private-network denial | `test_s11_url_security::test_production_policy_denies_loopback_fixture`, `test_test_port_allowance_is_port_specific` |
| response-size limit | `test_s11_url_security::test_response_size_limits` (declared, streamed, gzip bomb) |
| timeout | `test_s11_url_security::test_timeout` |
| HTML extraction | `test_s11_extraction_dedup::test_html_extraction_preserves_metadata_with_provenance`, `test_json_ld_metadata_and_content_boundaries`, `test_plain_text_json_and_pdf_handling` |
| metadata preservation | same + `test_missing_metadata_is_never_invented`, `test_s11_registration_tools::test_evidence_tools_through_the_gateway` (inspect) |
| source fingerprint | `test_s11_extraction_dedup::test_source_fingerprint_is_content_addressed`, `test_s11_pipeline::test_tampered_evidence_detected_by_independent_verifier` |
| source deduplication | `test_s11_extraction_dedup::test_duplicate_detection_relations`, `test_dedup_same_algorithm_in_service_and_tool`, `test_s11_pipeline::test_standard_research_end_to_end…` (syndicated copy shares the independence group) |
| citation creation | `test_s11_pipeline::test_standard_research_end_to_end…`, `test_s11_registration_tools::test_evidence_tools_through_the_gateway` |
| invalid citation rejection | `test_s11_pipeline::test_invalid_citations_are_rejected` (invented id, foreign evidence, absent quote, unknown source, self-chosen id) |
| claim/source binding | `test_s11_pipeline::test_standard_research_end_to_end…` (claim_sources, trace) |
| contradiction recording | `test_s11_pipeline::test_standard_research_end_to_end…` (12 M vs 15 M, sources, dates), `test_fact_verification_and_source_comparison_modes` |
| freshness enforcement | `test_s11_pipeline::test_freshness_requirement_not_silently_satisfied`, `test_s11_extraction_dedup::test_freshness_never_silently_satisfied` |
| prompt injection cannot alter task contract | `test_s11_pipeline::test_prompt_injection_cannot_alter_contract_tools_or_budgets`, `test_compromised_worker_obeying_injected_content_is_contained`, `test_s11_extraction_dedup::test_hidden_injection_text_excluded…` |
| web content cannot grant tools | same two pipeline tests (pool unchanged, `019.001` refused, no policy grants), `test_s11_registration_tools::test_policy_denies_stage11_tools_without_operator_grant` |
| checkpoint | `test_s11_pipeline::test_checkpoint_and_restart_recovery` (all checkpoint fields) |
| restart recovery | same (new controller instance, task left RUNNING by a "dead" process, resumed, no source fetched twice) |
| cancellation | `test_s11_pipeline::test_cancellation_stops_work_keeps_evidence_never_completed` (slow fetch killed, CANCELLED, evidence kept, DB refuses COMPLETED, no silent restart) |
| evidence persistence | `test_s11_pipeline::test_tampered_evidence_detected_by_independent_verifier` (append-only DB, raw-byte re-hash) + every pipeline test |
| API schema | `test_s11_api::test_openapi_schema_exposes_typed_contracts_for_stage04`, `test_every_research_operation_denied_before_stage02`, `test_validation_runs_after_authorisation…` |
| Stage 01 regression | §1 (sidecar vs baseline) |
| Stage 01A regression | §1 |
| existing ATTa regression | §1 |
| Additionally | registration through the existing registry (`test_production_registry_discovers_stage11_packages_but_does_not_execute_them`), numbering reconciliation, unapproved research does not execute, uncited material claim fails verification, QUICK_SEARCH bounded, SOURCE_COMPARISON / FACT_VERIFICATION modes |

## 3. Completion gate (§40)

| Gate item | State | Evidence |
|---|---|---|
| research contracts exist | MET | `backend/research/contracts.py`; contract tests |
| provider abstraction exists | MET | `capabilities/research_web/providers.py`, RESEARCH_PROVIDER_INTERFACE.md |
| controlled retrieval exists | MET | `netguard.py`, `fetch_source` |
| SSRF/network protections exist | MET | URL-security tests, SOURCE_SECURITY.md |
| source evidence is persistent | MET | `research.snapshots/sources/evidence` (append-only), tamper test |
| claim/evidence relationships exist | MET | `research.claims`, `claim_sources`, `citations` |
| citations are independently validated | MET | `ResearchVerifier`, invalid-citation and tamper tests |
| contradictions are preserved | MET | `research.contradictions`, pipeline test |
| freshness is represented | MET | `Freshness`, freshness tests |
| research budgets are enforced | MET | `research.budgets` + trigger, budget tests |
| Focus-4 is used | MET | Stage 01A `release` + `max_active_subtasks ≤ 4`; deep-research test |
| research can checkpoint/recover | MET | restart test |
| offline behaviour is correct | MET (Stage 01B provider pending) | offline tests |
| Stage 01A guardrails remain authoritative | MET | all execution via gateway; injection containment; no Stage 01A file modified |
| existing functionality remains intact | MET | §1 |
| applicable deterministic tests pass | MET | 117/117 |
| live dependencies not available are BLOCKED | MET | BLOCKERS B-S11-01…10; live provider, Stage 02/03/07/15 |
| merge manifest is complete | MET | STAGE_11_MERGE_MANIFEST.md |

**Not claimed:** live Deep Research. No live search provider was configured or exercised, and no model exists
(Stage 03). Natural-language synthesis, model-driven decomposition and claim construction are BLOCKED; in the
tests, claims come from the labelled `ExplicitClaims` fixture and mechanical steps from the labelled
`deterministic-research-worker`.

## 4. What a run looks like (from `evidence/stage_11/S11-PIPELINE-STANDARD.json`)

STANDARD_RESEARCH, 2 subquestions → Stage 01A objective with FR-01/FR-02 (operator-approved) → batch 1: two
`research.search` tasks (pool `011.001`, destination = provider host) → batch 2: three `research.retrieve` tasks
(pool `011.002, 011.003, 011.006`, destination = page host), the syndicated copy recorded as duplicate of the
original → gap check (both subquestions covered, so no reformulated query was needed. The gap-reformulation path
— at most one extra query per uncovered subquestion — has no dedicated assertion in the suite; it is bounded by
the query budget, which is tested) → checkpoints after each batch → 3 explicit-fixture claims bound to
3 minted citations → structured contradiction (12 M vs 15 M euros) recorded `unresolved` → independent verification
VERIFIED → status `COMPLETED_WITH_LIMITATIONS` (limitations: unresolved contradiction; synthesis BLOCKED on S03).

## 5. Reproduce

```
sudo -u postgres psql -v "migration_password='''…'''" -v "app_password='''…'''" -f database/bootstrap.sql
printf 'ATTA_DATABASE_PASSWORD=…\nATTA_DATABASE_MIGRATION_PASSWORD=…\n' > configuration/local.env
uv venv .venv -p 3.13 && uv pip install -r configuration/locks/backend-full.lock.txt
redis-server --port 6390 --daemonize yes --save "" --appendonly no
ATTA_ENV=test .venv/bin/python -m backend.database migrate
ATTA_ENV=test .venv/bin/python -m pytest -q tests/research
ATTA_ENV=test .venv/bin/python -m pytest -q tests/unit tests/security tests/integration tests/acceptance
.venv/bin/python -m pytest -q tests/test_*.py
```
Note: running the Stage 01/01A acceptance suite rewrites `evidence/stage_01*/acceptance/*.json`; those baseline
evidence files were restored to their committed versions in this sidecar.
