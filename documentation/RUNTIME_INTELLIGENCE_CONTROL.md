# Runtime intelligence control architecture (authoritative, Stage 01A)

**Principle:** the model supplies interpretation, plans and proposals. Deterministic application code controls
task allocation, available tools, execution boundaries, permissions, progress, verification and completion.
Every executable capability in every later stage runs through this controller; no stage may add a competing
registry, intelligence engine, orchestration system or startup path.

> Stage 01A status: the controller, its persistence and its interfaces are implemented and verified with explicit
> test proposals and harmless fixture handlers. **No model inference exists yet** (Stage 03). No user can reach it
> yet: authenticated users, tenants and human approvals arrive in Stage 02. Nothing here is production-ready.

## Where it lives (one engine, extending Stage 01)

| Concern | Module | Notes |
|---|---|---|
| Controller loop, Focus-4, repair, checkpoints, recovery | `backend/orchestration/controller.py` | the single intelligence engine (`backend/orchestration`) |
| Outcome / task contracts, proposals | `backend/orchestration/contracts.py` | strict pydantic schemas, SHA-256 contract hashes |
| Persistence | `backend/orchestration/store.py`, `database/migrations/0002_runtime_controller.sql` | schema `control`, runtime role only |
| State machine | `backend/orchestration/states.py` + DB trigger `control.task_state_guard` | enforced in code **and** in PostgreSQL |
| Numbered catalogue | `backend/orchestration/catalogue.py`, `backend/capability_registry/numbering.py`, `configuration/tool_numbers.yaml` | extends the Stage 01 registry |
| Tool allocation | `backend/orchestration/allocation.py` | deterministic filter, ≤ 4 tools per task |
| Context packets | `backend/orchestration/context.py` | trusted vs untrusted, provenance, capacity |
| Execution gateway | `backend/orchestration/gateway.py` | the ONLY execution route |
| Drift guard | `backend/orchestration/drift.py` | structural rules, outside the model |
| Proposal parsing | `backend/orchestration/proposals.py` | never interpreted as commands |
| Independent verification | `backend/orchestration/verification.py` | reads the workspace and persisted records |
| Traceability | `backend/orchestration/traceability.py` | requirement → evidence chain |
| Operator authorisations | `backend/orchestration/authorization.py` | Ed25519, public key only in the process |
| Model adapter interface | `backend/orchestration/models.py` | protocol only; `ExplicitTestProposals` is a labelled test fixture |
| Supervised execution | `backend/capability_runtime/` | supervisor (subreaper) + isolated child, tree kill |
| Trusted configuration | `configuration/controller.yaml`, `configuration/permissions.yaml` | OS-protected, sealed |
| Factory + health | `backend/orchestration/service.py`, `GET /v1/health/controller` | essential service in the service manager |

The **build controller** (`backend/verification/`) is a different thing: it governs how this repository is built
(stage gates for the engineering work). It shares the evidence/checkpoint ideas, not the runtime path.

## Mandatory execution sequence and who controls each transition

| Step | Controlled by |
|---|---|
| USER OBJECTIVE → OUTCOME CONTRACT | `create_objective` stores the original instructions + SHA-256 verbatim (append-only versions) |
| approval / amendment | `approve_objective` / `amend_objective` — operator Ed25519 authorisation bound to the exact payload; original instructions immutable |
| REQUIREMENT EXTRACTION → TASK DECOMPOSITION | `TaskSpec`s (a model may propose them; the controller validates) |
| DEPENDENCY VALIDATION | `find_cycle`, unknown-dependency checks |
| TASK CONTRACT GENERATION | `build_contract` + `validate_contract` (limits ≤ trusted config, criteria reference task requirements) |
| NUMBERED TOOL ALLOCATION | `ToolAllocator` — BLOCKED with a structured reason when nothing fits |
| TASK CONTEXT ASSEMBLY | `ContextAssembler` — only the task's requirements, tools, inputs, verified dependency results |
| LLM EXECUTION PROPOSAL | `ModelAdapter.propose` → raw data only |
| STRUCTURAL VALIDATION | `parse_proposal` — field errors + permitted alternatives, bounded corrections |
| DRIFT / PERMISSION / APPROVAL VALIDATION | gateway steps 5–13 |
| CONTROLLED TOOL EXECUTION | gateway steps 14–15 (supervised process) |
| RESULT VALIDATION | gateway step 16 (output schema, disk budget) |
| INDEPENDENT OUTCOME VERIFICATION | `IndependentVerifier` on completion claims — a tool success is never a task success |
| PERSISTENT CHECKPOINT | `checkpoint` after each Focus-4 batch (verified only when integration passes) |
| NEXT AUTHORISED TASK | `release` — only dependency-ready tasks, only while the objective is open |
| FINAL INTEGRATION / FINAL OUTCOME VERIFICATION | integration checks + `final_verification` (traceability must be complete) |
| USER DELIVERY | deliverables marked verified in the trace (UI in Stage 04) |

## Gateway (18 steps — each can only refuse)

1 worker-context signature · 2 authoritative contract · 3 version + integrity hash · 4 task RUNNING with this
attempt · 5 tool id known · 6 tool in the pool · 7 operation + JSON-Schema arguments · 8 resources, workspace paths
(no absolute, `..`, symlink escapes, protected prefixes), network destinations · 9 registry revalidation (package
digest, trust, dependencies, live service health) · 10 drift (repeated failures, circular patterns, no progress,
requirement scope) · 11 deadline + action budget · 12 policy engine (attested controller principal, LOW risk,
granted permissions, bound to contract hash + tool + arguments hash + execution id) · 13 approvals (denied until
Stage 02) · 14 idempotent execution reservation, concurrent-tool and action budgets, resource locks · 15 supervised
execution with an execute ticket only the gateway can sign · 16 output validation · 17 evidence record ·
18 persistent state (step failures, failed-step limit, uncertain external outcome → BLOCKED).

## Limits (trusted: `configuration/controller.yaml`)

Active tasks per objective 4 · tools per task 4 (trusted per-task-type exceptions only) · actions per task 30 ·
retries per step 2 · failed steps per task 3 · concurrent tool executions per task 2 · task duration 30 min ·
bounded proposal corrections 2 · repairs per task 2 · plus token, CPU-seconds, memory, disk, network-request,
external-API and spend budgets. Budgets are reserved before and settled after execution with atomic conditional
updates; a database trigger forbids raising limits or lowering consumption, so a restart never resets them.

## Task states

`CREATED → VALIDATING → PLANNING → READY | WAITING_FOR_DEPENDENCY | AWAITING_APPROVAL → QUEUED → RUNNING →
VERIFYING → COMPLETED`, with `RETRYING` (bounded repair), `RECOVERING` (restart), `BLOCKED`, `FAILED`,
`CANCELLED`. RUNNING→COMPLETED does not exist; VERIFYING→COMPLETED requires a passed verification for every
criterion of the current attempt; COMPLETED/FAILED are terminal; CANCELLED resumes only through an
operator-authorised new attempt. Every transition is logged append-only (previous/new state, reason, controller,
attempt, evidence, timestamp).

## Isolation and cancellation

Handlers never run in the API/controller process. `supervisor.py` runs in its own session, becomes a Linux child
subreaper, starts `child.py` (`python -I`, clean environment, rlimits) which re-hashes the package bytes it will
execute and loads them from memory. When the handler returns, times out, is cancelled or the controller dies,
the supervisor kills every descendant — including double-forked `setsid` daemons — before anything can act
later (S01A-T29/T30). External effects are different: a tool marked `external_uncertain` that is interrupted is
recorded `outcome_unknown`, its task BLOCKED, never retried automatically; `reconcile` asks the tool's isolated
reconcile handler what really happened (S01A-T40).

## Trust and numbering

Capability packages execute only when listed in the operator-signed trust store (Ed25519; API has the public key
only) with matching version, package digest, permissions and risk. Tool numbers `CCC.TTT` (CCC = requirement
register capability) are permanent: the database refuses reassignment, deletion and reactivation of retired numbers.

## What Stage 01A does not do (see BLOCKERS.md)

No model inference (S03) · no authenticated users, tenants or human approvals (S02) · no network isolation of
handlers (later sandbox stage) · Windows process-tree control via Job Objects not implemented (POSIX verified only).
