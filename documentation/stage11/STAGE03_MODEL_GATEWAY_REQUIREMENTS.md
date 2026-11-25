# Stage 11 → Stage 03: model gateway requirements for research (C§25)

Stage 11 is model-independent. It never imports a model SDK, never names a model, and has no model server. A Stage 03
implementation connects through ONE object implementing `backend.research.hub.ResearchHub`, built on the Stage 03
model-independent gateway (`backend/model_gateway`, `backend/orchestration/models.ModelAdapter`). Any compatible LLM
(Llama, Qwen, others) can sit behind it; no production model is a Stage 11 dependency.

| Research step | Hub method | Input (all untrusted source text is under provenance tags) | Required output | Stage 11 validation (never bypassed) |
|---|---|---|---|---|
| Research planning / decomposition | `plan_subquestions(contract)` | the research contract (question, scope, mode, budgets) | list of subquestion strings | ≤ `max_subquestions`, 3–500 chars each, de-duplicated; stored ONCE in the contract (stable on resume); 1 model request charged |
| Query generation | `formulate_query(contract, subquestion, attempt)` | contract + one subquestion; attempt 0 or 1 | one query string | ≤ 400 chars, whitespace-normalised; attempts > 1 never requested; 1 model request each |
| Evidence extraction / claim construction | `build_claims(contract, evidence_packet)` | `evidence_packet` from `evidence.select_passages`: bounded passages with source/evidence ids, spans, hashes, URLs, dates | list of `ClaimInput` dicts (text, material, subquestion_index, optional subject/attribute/value/as_of/scope, `support` / `contradicts` references) | schema-validated; builder label forced to `hub:<name>`; every reference must resolve to stored evidence of THIS research (evidence id issued by the service, or an exact quote present in a stored source); invented citation ids/URLs rejected and listed |
| Synthesis | `synthesise(contract, material)` | verified claims with their verified citation ids, contradictions | `{"text": ..., "citation_ids": [...]}` | accepted only if every `[cit_...]` marker and listed id is a VERIFIED citation of this research and markers == listed ids; otherwise REJECTED (limitation), never silently used |
| Research worker (optional) | `ResearchService(worker_factory=...)` returning a `ModelAdapter` | Stage 01A context packet (trusted task / untrusted data) | `ToolProposal` / `CompletionClaim` JSON | Stage 01A proposal parsing, drift guard, numbered pool (≤ 4 tools), policy, gateway, independent verification |
| Verification | — (not delegated) | — | — | `ResearchVerifier` and Stage 01A `final_verification` are deterministic; a model can never mark research verified |

Requirements on the Stage 03 side
* `available()` must report real availability (Stage 11 treats exceptions as unavailable → BLOCKED, never faked).
* Calls must respect `timeout`/budget limits; Stage 11 charges `model_requests` before each call and stops at zero.
* Token use of model-backed workers is charged to Stage 01A task token budgets and the research `tokens` budget.
* Outputs are data. Instructions found in retrieved text must not be followed; Stage 11 enforces this structurally.
* Model identity (provider/model/version) should be exposed via `name` so claims and syntheses are attributable.

Qualification status: the contract is VERIFIED with a labelled test hub (`tests/research/test_s11_integration_kits.py`,
`FakeHub`); live LLM qualification is BLOCKED until Stage 03 exists. Deterministic infrastructure tests and live
model qualification are reported separately in STAGE_11_VERIFICATION.md.
