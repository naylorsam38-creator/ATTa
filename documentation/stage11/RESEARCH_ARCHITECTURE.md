# Stage 11 — Research architecture (web search and deep research)

Stage 11 implements CAP-11 (web search) and CAP-12 (deep research) **on top of the Stage 01A runtime controller**.
It adds no Hub, LLM gateway, capability registry, policy engine, Focus-4 controller, task engine, service
manager, installer, authentication or approval system. Every executable research step is a Stage 01A task.

> Numbering note: the user's build plan calls this "Stage 11". The repository roadmap
> (`configuration/build/roadmap.yaml`, protected) lists web search and deep research as **S08**
> (CAP-11, CAP-12). This sidecar implements exactly CAP-11/CAP-12; see DECISIONS D-S11-01.

## Pipeline and who controls each step

| Step | Implemented by | Control |
|---|---|---|
| USER RESEARCH REQUEST | `ResearchService.create` / `POST /v1/research` (`backend/research/api.py`) | API is policy-DENIED until Stage 02 |
| RESEARCH CONTRACT | `backend/research/contracts.py` `ResearchContract` | strict schema, SHA-256 hash, append-only versions (`research.contract_versions`) |
| Stage 01A OBJECTIVE | `RuntimeController.create_objective` (one FR per subquestion) | must be operator-approved (Ed25519) before anything runs |
| QUESTION DECOMPOSITION | `backend/research/planner.py` | one level only, ≤ `max_subquestions`; model planning = Stage 03 (BLOCKED) |
| SEARCH PLAN | `ResearchService._candidates` (derived only from persisted state) | budgets reserved atomically before a task is planned |
| SOURCE DISCOVERY | tool `011.001 research.web:search` | provider endpoint host is the only permitted network destination of the task |
| SOURCE RETRIEVAL + VALIDATION | tool `011.002 research.web:fetch_source` (`netguard.py`) | trusted retrieval policy; SSRF / redirect / size / timeout / type controls; isolated process |
| CONTENT EXTRACTION | tool `011.003 research.web:extract_content` (`extract.py`) | typed, provenance for every metadata field, nothing invented |
| EVIDENCE STORAGE | `ResearchService._ingest_source` → `research.snapshots`, `research.sources`, `research.evidence` | bytes and text re-hashed by the service before storage; append-only |
| CLAIM CONSTRUCTION | `ClaimBuilder` (`claims.py`): `HubClaimBuilder` (Stage 03, BLOCKED) / `ExplicitClaims` (test fixture) | proposals only |
| CITATION BINDING | `claims.bind_claims` | citations minted by the service, must resolve to stored evidence of this research |
| CONTRADICTION ANALYSIS | `claims.analyse_contradictions` + explicit counter-evidence | recorded with sources, dates, scope; never hidden |
| SYNTHESIS | deterministic structured summary (`_assemble`) | natural-language synthesis BLOCKED until Stage 03 |
| INDEPENDENT VERIFICATION | `backend/research/verification.py` `ResearchVerifier` + Stage 01A `final_verification` | runs on persisted records, never on a completion claim |
| FINAL RESEARCH RESULT | `ResearchResult` stored hash-sealed in `research.results` | status + verification status + limitations |

## Execution through Stage 01A

```
ResearchService ──plan──▶ RuntimeController.plan(TaskSpec)          (task contract, ≤4 numbered tools, criteria)
                ──release──▶ Focus-4 (≤4 active subtasks per objective, row-locked slots)
                ──run_task(worker)──▶ ContextAssembler ─▶ DeterministicResearchWorker.propose (DATA only)
                                     ─▶ parse_proposal ─▶ ExecutionGateway (18 steps: drift guard, pool, URL
                                        destination check, policy engine, budgets, supervised isolated handler)
                ──ingest──▶ research.* tables (re-hash snapshot + extraction, dedup, quality, evidence)
                ──checkpoint──▶ research.checkpoints + control.checkpoints
```

Task types and their pools (allocated deterministically by the Stage 01A `ToolAllocator`):

| Task type | Operations | Pool | Network destinations |
|---|---|---|---|
| `research.search` | search | 011.001 | provider endpoint host |
| `research.retrieve` | fetch, extract, find | 011.002, 011.003, 011.006 | the URL's host |
| `research.follow` | fetch, extract, find | 011.005, 011.003, 011.006 | the link's host |
| `research.local` | extract, find | 011.003, 011.006 | none |
| `research.compare` | compare | 012.002 | none |

The **deterministic research worker** (`worker.py`) is a `ModelAdapter` that is not a model. It drives the
mechanical steps until Stage 03, is labelled `deterministic-research-worker`, and can only emit proposals.
A Stage 03 model plugs into the same interface; nothing else changes.

## Bounded deep research

`OBJECTIVE → subquestions (≤ max_subquestions) → Focus-4 batch (≤ 4 active) → checkpoint → evidence gaps →
next bounded batch`. Bounds: `max_batches`, `max_queries`, `max_fetches`, `max_sources`, `max_depth` (link
following), `time_limit_s` (DB deadline), `token_limit`, `network_requests`, `max_retries`. A subquestion never
spawns subquestions; a gap gets at most one reformulated query (attempt 1). There is no recursion.

## Budgets

Stored in `research.budgets` (`limit_value`, `consumed`; `CHECK consumed <= limit`; trigger: limits can never be
raised, consumption never decreases). Reserved by the **service** with a conditional UPDATE before a task is
planned; the worker has no API to touch them. Stage 01A per-task budgets (actions, tokens, CPU, failed steps,
concurrency) still apply inside each task; task token consumption is also charged to the research token budget.

## Context management

Full texts stay in PostgreSQL. `evidence.select_passages` hands a worker only bounded, task-relevant passages,
round-robin across sources, each with provenance (source, evidence, snapshot, exact span, hashes, URL, dates).
Truncation keeps the span actually shown. Retrieved content is always placed under `untrusted` in the Stage 01A
context packet.

## Checkpoints, restart, cancellation

* After every batch: `research.checkpoints` (completed subquestions, queries, sources, claims, contradictions,
  gaps, budgets, pending tasks, ingested tasks) **and** a Stage 01A controller checkpoint.
* All planning state is derived from persisted rows/events, so `ResearchService.resume` after a restart calls
  `RuntimeController.recover` (RUNNING tasks → new attempt; uncertain external outcomes → BLOCKED) and continues
  without re-fetching stored sources.
* `cancel`: persists `cancel_requested` (cannot be withdrawn), cancels active Stage 01A tasks (supervisor kills the
  handler process tree), ingests everything already retrieved, records unfinished work, status `CANCELLED`. A DB
  trigger forbids a cancelled run from ever becoming `COMPLETED`.

## Offline behaviour (Stage 01B coexistence)

`network.py` asks a `NetworkModeProvider` (Stage 01B integration point; until then `ATTA_NETWORK_MODE` or
`configuration/research.yaml`, unknown values fail closed to AIR_GAPPED). In OFFLINE/AIR_GAPPED no search/fetch task
is ever planned; web research ends `BLOCKED / OFFLINE_UNAVAILABLE`; research over attached local sources still
runs. Nothing in the offline core imports or contacts the network.

## Local / project sources (Stage 07)

`local_sources.py` defines `ProjectKnowledgeSource` (Stage 07 implements it; default BLOCKED). Today, trusted code
may attach files listed in the contract (`attach_local_source`); they are extracted by the same isolated tool.

## Statuses

`CREATED → RUNNING ⇄ PAUSED → COMPLETED | COMPLETED_WITH_LIMITATIONS | FAILED | BLOCKED | CANCELLED`
(`CANCELLING` while a cancel is in progress). Non-QUICK research currently always carries the limitation
"natural-language synthesis BLOCKED (S03)", so it reports `COMPLETED_WITH_LIMITATIONS` at best — honestly.
Without a claim builder (default, Stage 03 absent) non-QUICK research ends `BLOCKED / BLOCKED_DEPENDENCY` with
its sources and evidence stored.

## Persistence

Migration `database/migrations/0011_stage11_research.sql` (schema `research`): `runs`, `contract_versions`,
`budgets`, `queries`, `snapshots`, `sources`, `evidence`, `claims`, `claim_sources`, `citations`,
`contradictions`, `checkpoints`, `events`, `results`. Append-only tables refuse UPDATE/DELETE by trigger and are
granted SELECT/INSERT only to the runtime role.

## Controlled integration points (prepared, not activated)

| Stage | Entry point | Behaviour until integrated |
|---|---|---|
| 02 permissions | `python -m backend.research.operator_config check / render --out DIR`; `documentation/stage11/integration/stage02_research_permissions.yaml` | packages `untrusted`; research API policy-DENIED |
| 03 model | `ResearchService(hub=ResearchHub, worker_factory=...)` (`backend/research/hub.py`) | deterministic decomposition; claims/synthesis BLOCKED |
| 04 API | `backend.research.wiring.wire_research_api(app)` | router not mounted |
| provider | `configuration/research.yaml` + `python -m backend.research.provider_check` | external search PROVIDER_UNAVAILABLE |
| 01B network mode | `ResearchService(network=<01B NetworkModeProvider>)` | `ATTA_NETWORK_MODE` / configuration |

Model output never bypasses validation: subquestions are bounded and stored once in the contract; claims carry the
forced label `hub:<name>` and their citations must resolve to stored evidence; a synthesis is accepted only when
every `[cit_...]` it uses is a verified citation of the research, otherwise it is rejected and listed as a limitation.
