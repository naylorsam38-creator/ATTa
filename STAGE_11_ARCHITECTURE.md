# STAGE 11 ARCHITECTURE — web search and deep research (CAP-11, CAP-12; roadmap S08)

Stage 11 adds research to ATTa **without** a second Hub, model server, task engine, capability registry, tool
catalogue, policy engine, approval system or browser automation. Every executable step is a Stage 01A task run by the
existing runtime controller and execution gateway.

```
request ─▶ ResearchContract (versioned, hashed) ─▶ Stage 01A objective (operator-approved; Stage 02 later)
        ─▶ decomposition (deterministic, or Stage 03 hub — validated, stored once)
        ─▶ per batch (≤ 4 active, Focus-4): Stage 01A task contracts with numbered pools (≤ 4 tools)
              research.search   011.001                     destination = provider host
              research.retrieve 011.002 011.003 011.006     destination = page host
              research.follow   011.005 011.003 011.006     depth-bounded
              research.local    011.003 011.006             offline-capable
              research.compare  012.002
           ─▶ gateway: drift guard · policy · budgets · supervised isolated handler (netguard: SSRF/redirect/size/
              timeout/type controls; content-addressed snapshots; typed extraction)
           ─▶ ingestion: re-hash, dedup/syndication, quality factors, evidence spans ─▶ checkpoint ─▶ gaps ─▶ next batch
        ─▶ claims (Stage 03 hub or labelled fixture) ─▶ citation binding to stored evidence ─▶ contradictions
        ─▶ independent verification (ResearchVerifier + Stage 01A final_verification) ─▶ synthesis (validated) ─▶ result
```

| Concern | Where |
|---|---|
| Contracts, modes, statuses, budgets | `backend/research/contracts.py`, `config.py`, `configuration/research.yaml` |
| Orchestration on Stage 01A | `backend/research/service.py`, `worker.py` (labelled non-model adapter), `planner.py` |
| Retrieval and extraction (isolated) | `capabilities/research_web/` (`netguard.py`, `retrieval_policy.py`, `extract.py`, `providers.py`) |
| Evidence tools | `capabilities/research_evidence/` |
| Persistence | `database/migrations/0011_stage11_research.sql`, `backend/research/store.py` |
| Claims / citations / contradictions / verification | `claims.py`, `verification.py`, `evidence.py`, `dedup.py`, `quality.py` |
| Security, offline, local sources | `security.py`, `network.py`, `urls.py`, `local_sources.py` |
| Integration points | Stage 02 `security.py` + `operator_config.py`; Stage 03 `hub.py`; Stage 04 `api.py` + `wiring.py`; provider `provider_check.py`; Stage 01B `network.py` |
| Installation | `packaging/stage11/` (transactional installer, wrappers, compatibility harness), `scripts/build_stage11_package.py` |

Budgets (all DB-enforced, never raisable by a worker or model): queries, sources, fetches, depth, batches, time
(deadline also bounds running tasks), tokens, network requests, retries, evidence storage, model requests; plus
per-provider rate limits, Focus-4 parallelism, Stage 01A per-task budgets and the retrieval size caps.

Detailed design: `documentation/stage11/RESEARCH_ARCHITECTURE.md`, `SOURCE_SECURITY.md`, `CITATION_MODEL.md`,
`RESEARCH_PROVIDER_INTERFACE.md`, `STAGE03_MODEL_GATEWAY_REQUIREMENTS.md`. Requirement coverage:
`STAGE_11_REQUIREMENT_MATRIX.md`. Verification: `STAGE_11_VERIFICATION.md`. Merge: `STAGE_11_MERGE_MANIFEST.md`.
