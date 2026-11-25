# STAGE 11 MERGE MANIFEST — web search and deep research (sidecar)

Produced by Parallel Agent 3. **Not merged, not pushed to the authoritative branch.** For independent review.

## 1. Baseline

| | |
|---|---|
| Baseline commit | `21104c413c30a3b2714f877892970e7db19a1d19` (Stage 01A gate passed) |
| Supplied archive | `ATTa-stage01A.zip` SHA-256 `ccf1e9435e7e3a810157fb3d5dc6588ffa0a32548a0f73cc4b9005c51f5d5592`, byte-identical to the commit |
| Sidecar branch | `stage-11-research-sidecar` (local), commit listed in STAGE_11_VERIFICATION.md |
| Merge method | the sidecar commit applies on top of `21104c4` without conflicts; against a tree that already merged 01B/04, see §11 |

## 2. Files

__FILE_TABLE__

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

1. Append `documentation/stage11/tool_numbers.stage11.yaml` entries to `configuration/tool_numbers.yaml`.
2. Merge `documentation/stage11/permissions.stage11.yaml` into `configuration/permissions.yaml`
   (`research.search.query`, `network.web.read` + controller grant).
3. Sign both packages into `configuration/capability_trust.json` with the operator trust key
   (`python -m backend.capability_registry trust capabilities/research_web --key <operator private key> --operator NAME`,
   same for `capabilities/research_evidence`).
4. Re-seal the build controller's protected files.
5. Configure a provider in `configuration/research.yaml` (optional; without it external search is BLOCKED).
Until then the production registry reports both packages `untrusted` and nothing in Stage 11 can execute (fail closed).

## 6. API additions (contract only, not mounted)

`backend/research/api.py` → `build_research_router()`; mount with
`app.include_router(build_research_router(), prefix="/v1")` and set `app.state.research_service =
build_research_service(app.state.controller_service)` in the API lifespan. Every route evaluates the central policy
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

* `CHANGE_MANIFEST.md`, `DECISIONS.md`, `BLOCKERS.md`, `IMPLEMENTATION_PROGRESS.md`,
  `MASTER_IMPLEMENTATION_REGISTER.md`, `requirement_register.yaml`: other sidecars append too → textual conflicts,
  resolve by keeping both sections; re-render the master register from the merged YAML.
* Migration numbering: if the integrator wants contiguous numbers, `0011` can be renamed **before** it is applied
  anywhere (the ledger is checksummed by name).
* `backend/api/app.py`: mounting the router (one line + lifespan wiring) should be done together with Stage 04.
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
