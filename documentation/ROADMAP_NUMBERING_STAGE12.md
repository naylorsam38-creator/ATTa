# Roadmap numbering discrepancy — Stage 12 (record only; protected files NOT modified)

## Facts

* The protected roadmap `configuration/build/roadmap.yaml` (SHA-256 `829d2fe4…cabd19b74`, identical to the value sealed in
  `configuration/build/protected.lock.json`; unchanged by this sidecar) defines:

  | Roadmap id | Title | Capabilities |
  |---|---|---|
  | S04 | Interface: task status, assigned tools, progress, failures, verification evidence; chat management | CAP-04 |
  | S08 | Web search and deep research | CAP-11, CAP-12 |
  | S11 | Code execution sandbox and application creation | CAP-19, CAP-18 |
  | **S12** | **Skills, plugins, subscription, administration and data management** | **CAP-23, CAP-24, CAP-36, CAP-37, CAP-38** |
  | S18 | Connectors, MCP, scheduling, notifications, collaboration and cross-device execution | CAP-21, CAP-22, CAP-30, CAP-31, CAP-33, CAP-34 |

* The parallel-worker plan numbers the work differently:

  | Parallel plan | Worker | Roadmap equivalent |
  |---|---|---|
  | Stage 01B — adaptive deployment / offline qualification | Agent 1 (Worker A) | not in the sealed roadmap (between S01A and S02) |
  | Stage 04 — complete application interface | Agent 2 | S04 (wider scope) |
  | Stage 11 — web search / deep research | Agent 3 (Worker 3) | **S08** |
  | **Stage 12 — connectors, MCP, skills & plugins** | **Agent 4 (this sidecar)** | **S12 (CAP-23, CAP-24 only) + S18 (CAP-21, CAP-22)** |

## Consequences

1. "Stage 12" in this sidecar's files, tests (`S12-Txx`) and evidence (`evidence/stage_12/`) means the parallel plan's
   Stage 12, not roadmap S12. CAP-36/37/38 (subscription, administration, data management) were NOT built here.
   CAP-21/22 were built ahead of roadmap S18; the requirement register keeps their `implementation_stage: S18` and
   marks them PARTIAL with a sidecar note (no stage field was renumbered).
2. No build-controller stage definition (`configuration/build/stages/S12.yaml`) was created and nothing was sealed: the
   stage definitions, roadmap and seal are protected criteria that only the operator/verifier may change. The sidecar
   was verified with `scripts/stage12_verify.py` and the trial runner, not the sealed build-controller gate.
3. Stage 11's sidecar uses capability numbers 011/012 (CAP-11/12) and migration `0011`; Stage 12 uses CAP-21/22/24
   numbers in the reserved TTT band 500–899 and migration `0012`. The numbers do not collide, whatever the stage naming.

## Decision needed from the operator (at merge)

Either (a) renumber the roadmap to the parallel plan (insert 01B, move web search to 11, split S12/S18), re-seal
`protected.lock.json` with the verifier key and write gate definitions for each merged stage; or (b) keep the roadmap
and map sidecars onto it (Stage 11 → S08, Stage 12 → S12 part + S18 part). Until then no gate result may be claimed
for "S12". See DECISIONS D-S12-11.
