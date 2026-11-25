# STAGE 11 VERIFICATION — web search and deep research (final qualification)

| | |
|---|---|
| Baseline | `21104c413c30a3b2714f877892970e7db19a1d19` (Stage 01A gate passed) |
| **Qualified source revision (frozen)** | **`__FROZEN__`** — no source file changed during this qualification. A first frozen revision (`a29fb230`) was superseded after the compatibility run found the listing defect in §3; every suite below was re-run on the new revision |
| Evidence commit | the commit containing this file: adds only evidence and documentation on top of `__FROZEN_SHORT__` (`git diff --stat __FROZEN_SHORT__ <evidence commit>` lists no file outside `evidence/stage_11/`, `STAGE_11_*.md`, `documentation/stage11/`, `BLOCKERS.md`) |
| Branch / tag | `claude/focused-cori-ajjjn0` (isolated working branch; never the authoritative branch); tag `stage11-rc1` |
| Date | 2026-11-25 (UTC) |
| Environment | Linux 6.18 x86_64 · Python 3.13.18 · Git 2.43.0 · PostgreSQL 16.15 + pgvector · Redis 7.0.15 · fastapi 0.162.2, pydantic 2.14.3, psycopg 3.3.8, pytest 9.2.0, jsonschema 4.26.0, PyYAML 6.0.3, playwright 1.63.0 (from `configuration/locks/backend-full.lock.txt`) |
| Network | **no Internet used by any test**; HTTP only against the deterministic local fixture server `tests/research/fixture_server.py` |
| Evidence | `evidence/stage_11/final/` — `environment.txt`, `*.cmd` (exact command), `*.log` (output + exit status), `*.xml` (JUnit), `failure_comparison.json`, `compat_*.json`, `records/` (per-test JSON records) |

This is not a sealed build-controller gate (the protected `configuration/build/` holds no Stage 11/S08 definition;
see STAGE_11_REQUIREMENT_MATRIX.md for the sources audited).

## 1. Final results (frozen revision `__FROZEN_SHORT__`)

| # | Suite | Command (`.cmd` file) | Result | Exit |
|---|---|---|---|---|
| 1 | **Stage 11** (`tests/research`, 9 modules, DB required — no skips) | `ATTA_REQUIRE_DB=1 python -m pytest tests/research` | **173 passed, 0 failed, 0 skipped** (348.5 s) | 0 |
| 2 | Stage 01 + 01A on the sidecar | `python -m pytest tests/unit tests/security tests/integration tests/acceptance` | 98 passed, 6 failed (all pre-existing, §2) | 1 |
| 3 | Legacy ATTa on the sidecar | `python -m pytest tests/test_*.py` | 255 passed, 5 skipped, 8 failed (all pre-existing, §2) | 1 |
| 4 | Stage 01 + 01A on the **unchanged baseline** (Git clone of `21104c4`, same environment) | same as 2 | 98 passed, 6 failed | 1 |
| 5 | Legacy ATTa on the **unchanged baseline** | same as 3 | 255 passed, 5 skipped, 8 failed | 1 |
| 6 | Compatibility harness, clean Stage 01A baseline, installer + all suites | `compat/check_compat.py --baseline … --baseline-ref 21104c4 --run-tests` | __COMPAT_BASELINE__ |
| 7 | Compatibility harness, baseline + synthetic Stage 01B + synthetic Stage 04, installer + all suites | `… --stage 01B=… --stage 04=… --run-tests` | __COMPAT_SYNTHETIC__ |
| 8 | Requirement register | `python scripts/requirement_register.py check` | valid, 40 requirements | 0 |

Stage 11 tests per module: url_security 68 · installer 29 · pipeline 23 · qualification 15 · integration_kits 12 ·
extraction_dedup 10 · contracts 9 · registration_tools 4 · api 3.

## 2. Baseline failures — evidenced on the unchanged baseline under equivalent conditions

`failure_comparison.json`: sidecar failures **= baseline failures** in both suites; **new in sidecar: none; fixed: none**.

| Test | Fails on baseline | Reason (from JUnit) |
|---|---|---|
| `test_S01_T06_discover_valid_capability_manifest` | yes | capability reported `untrusted` instead of `available` — superseded by the Stage 01A trust control (BLOCKERS B-017, DECISIONS D-029) |
| `test_S01_T07_reject_invalid_capability_manifest` | yes | same (trust control precedes the arity check) — B-017 |
| `test_S01_T08_reject_duplicate_capability_identifiers` | yes | same — B-017 |
| `test_S01_T09_new_capability_registers_without_changing_start` | yes | `'untrusted' == 'available'` — B-017 |
| `test_S01_T13_protected_operations_default_to_deny` | yes | direct execution removed (gateway-only, S01A-T31) — B-017 |
| `test_S01A_T51_worker_cannot_alter_protected_verification_criteria` | yes | "OS protection is not in place: run scripts/protect_verifier.sh" — this container has no `atta-builder` account; running the script would regenerate a protected public key (B-016) |
| 7 × `V119JourneyProof::*`, `SubmitSelection::test_login_submit_is_inside_the_password_form` | yes | Playwright: Chromium not at `/usr/bin/chromium` (1) and "Playwright Sync API inside the asyncio loop" (7) in this environment |

**S01A-T48 investigation (C§01).** In the first compatibility run S01A-T48 failed inside the combined tree. Isolated
individually: T48 re-runs the Stage 01 suite, whose T09 calls `start.sh`, which refuses with "not installed: run
./install.sh" in any fresh clone without its own `.venv`. Reproduced identically on a pristine baseline clone
(`21104c4`), and T48 **passes** in the combined tree once `ATTA_VENV` points at the installed environment. The
harness now sets `ATTA_VENV` (equivalent conditions); suites 4–7 above ran that way. Not a Stage 11 regression.
An earlier comparison run on a non-Git copy of the baseline showed 4 extra failures (T01/T16/T48/T49 read Git
history); the Git-clone baseline above supersedes it.

## 3. Defects found and corrected during qualification

| Found by | Defect | Correction | Re-verified by |
|---|---|---|---|
| compatibility run | package builder packaged other stages' files when run inside a combined tree | builder packages only Stage 11-owned paths | inst::test_builder_ignores_other_stages_files, run 7 |
| time-limit test | research deadline did not bound a task already running (slow source) | each task's Stage 01A `execution_deadline` = research time left | q::test_research_time_limit_is_enforced |
| time-limit test | BLOCKED results dropped other limitations (e.g. exhausted time) | `_finish_blocked` includes all applicable limitations | same |
| requirement audit | register CAP-11 "Rate limits" not implemented | per-provider `max_requests` / `rate_window_s` | q::test_provider_rate_limit |
| security review | a refused URL with `user:password@` was stored verbatim in an event | `redact_url` before storing | q::test_no_plaintext_credentials_in_research_records |
| installer test | CRLF target: regenerated master register written with LF | regenerated files take the target's line endings | inst::test_crlf_target_windows_checkout |
| compatibility run (clean baseline) | `GET /v1/research` listed oldest first, so with more than one page of runs the newest research was not on the first page; the test assumed a single page | research history lists newest first with cursor pagination; the test pages through | kit::test_wiring_end_to_end_with_simulated_stage02_grant; full requalification on the new frozen revision |
| installer test | fault injection fired during the rehearsal | injection only in the real install; rehearsal errors exit 4 | inst::test_failure_midway_rolls_back_completely |

## 4. Installer qualification (C§32) — actual outcomes

| # | Scenario | Outcome | Test / evidence |
|---|---|---|---|
| 1 | Clean Stage 01A baseline | installs; tree equals the sidecar's Stage 11 files; all suites pass except pre-existing | inst::test_install_reproduces_the_sidecar…, run 6 |
| 2 | Stage 01B candidate | **BLOCKED — actual package not supplied**; synthetic 01B tree: installs (union-merged logs) | run 7 |
| 3 | Stage 04 candidate | **BLOCKED — actual package not supplied**; synthetic 04 tree: installs | run 7 |
| 4 | Combined 01B + 04 | **BLOCKED — actual packages not supplied**; synthetic combined tree: installs, suites pass | run 7 |
| 5 | Repeated installation | "already installed", exit 0 | inst::test_install_reproduces…_and_is_idempotent |
| 6 | Dirty working tree | refused (exit 2), nothing changed | inst::test_dirty_tree_refused |
| 7 | Nested repository | refused: nested target, sub-directory target, nested repo on a written path | inst::test_subdirectory_nested…, inst::test_nested_repository_on_a_written_path_refused |
| 8 | Conflicting existing files | exit 3 in `--check` and install; nothing changed | inst::test_conflicting_new_file…, inst::test_conflicting_register_yaml… |
| 9 | Invalid checksums | exit 7 for modified, extra, missing file | inst::test_tampered_package_refused… |
| 10 | Migration conflicts | another `0011_*.sql` → exit 3 (also tool-number and capability-id conflicts) | inst::test_registry_conflicts_detected_before_installation |
| 11 | Interrupted installation | hard kill → next run refuses → `--rollback-interrupted` restores the clean tree | inst::test_hard_kill_is_recoverable |
| 12 | Rollback | injected failure after 1 / 40 / 85 writes → full rollback, clean tree, journal removed | inst::test_failure_midway_rolls_back_completely |
| — | Windows | CRLF checkout preserved; `.bat`/`.ps1` wrappers present; no POSIX-only calls | inst::test_crlf_target…, inst::test_package_layout_and_windows_wrappers · **execution on Windows not performed (B-S11-12)** |
| — | Evidence | JSON report in `.git/` and `--report` | inst::test_installation_evidence_report |

## 5. Security qualification (C§33)

| Attack / check | Denied by | Test |
|---|---|---|
| Unauthorised research execution | operator approval of the Stage 01A objective; API policy deny | pl::test_unapproved_research_does_not_execute, api::test_every_research_operation_denied_before_stage02 |
| Unassigned capability use / cross-task access | numbered pools, signed worker context | q::test_cross_task_and_unassigned_capability_denied, pl::test_compromised_worker… |
| Network policy violations (SSRF, metadata, private, redirects, ports, schemes) | netguard + planning checks + task destinations | url (68 tests), pl::test_unsafe_search_results… |
| Unauthorised credential access | clean handler environment | q::test_handlers_cannot_access_credentials (canary secrets not visible) |
| Malicious retrieved instructions | structural separation, gateway | pl::test_prompt_injection…, pl::test_compromised_worker… |
| Invalid citations / fabricated evidence | `bind_claims` resolution, verifier | pl::test_invalid_citations_are_rejected, pl::test_tampered_evidence… |
| Budget expansion | DB trigger, ceilings, amendment rules | pl::test_budget_ceiling_and_db_guards, pl::test_contract_changes_are_versioned… |
| Cancellation bypass / revoked authority | permanent `cancel_requested`, DB trigger, revoke | pl::test_cancellation…, q::test_authority_revocation_stops_research |
| Verification tampering | verifier recomputes; append-only results/evidence/events/control verifications | q::test_verification_tampering_is_undone_or_refused |
| Invalid API requests | schema `extra=forbid` after policy | api::test_validation_runs_after_authorisation… |
| Authorised paths work | — | pl::test_standard_research…, kit::test_wiring_end_to_end_with_simulated_stage02_grant |

## 6. Deterministic infrastructure vs live qualification

All research runs in the tests use the labelled `deterministic-research-worker` (not a model) and claims from the
labelled `ExplicitClaims` fixture or the labelled `FakeHub` test hub. **Not qualified, BLOCKED:** live web search
(no provider configured), live LLM planning/claims/synthesis (Stage 03), Stage 02 authentication/approvals/credential
injection, Stage 04 UI, Stage 07 project knowledge, Stage 15 browser execution, actual Stage 01B / 04 / 12 packages,
Windows execution. Details: BLOCKERS.md B-S11-01 … B-S11-15; coverage per requirement: STAGE_11_REQUIREMENT_MATRIX.md.

## 7. Reproduce

```
sudo -u postgres psql -v "migration_password='''…'''" -v "app_password='''…'''" -f database/bootstrap.sql
printf 'ATTA_DATABASE_PASSWORD=…\nATTA_DATABASE_MIGRATION_PASSWORD=…\n' > configuration/local.env
uv venv .venv -p 3.13 && uv pip install -r configuration/locks/backend-full.lock.txt
redis-server --port 6390 --daemonize yes --save "" --appendonly no
ATTA_ENV=test .venv/bin/python -m backend.database migrate
ATTA_ENV=test ATTA_REQUIRE_DB=1 .venv/bin/python -m pytest -q tests/research
ATTA_ENV=test .venv/bin/python -m pytest -q tests/unit tests/security tests/integration tests/acceptance
.venv/bin/python -m pytest -q tests/test_*.py
```
The exact commands of the recorded runs are in `evidence/stage_11/final/*.cmd`. Running the Stage 01/01A suites
rewrites `evidence/stage_01*/acceptance/*.json`; those committed baseline files were restored after the runs.
