# STAGE 01 FINAL QUALIFICATION

**Verdict: NOT YET PASSED.** The Stage 01 FINAL gate (S01F) has been executed three times; none reached a PASS
verdict. Runs 1 and 2 were aborted after genuine qualification-environment defects were observed; run 3 was **stopped
externally before completion**. Every defect found has been corrected and the affected step re-verified individually,
but a complete S01F pass on the final source revision has **not** been recorded. Stage 01B itself is qualified
(STAGE_01B_VERIFICATION.md: gate run 5 PASSED, 43 PASS / 0 FAIL / 2 BLOCKED, revision `42edea8`).

This report states exactly what was executed. It does not claim a pass that has not occurred.

## Release source revision

Final source: the commit carrying this report (tag `v1-stage01-rc1`). Code changed after the qualified S01B revision
`42edea8`: one fix in `backend/deployment/bundle.py` (rebuilding a previous bundle from scratch, found by S01F-T02),
re-verified individually, not yet covered by a full gate run.

## Qualification environment

Linux x86_64, 4 cores, 16 GB, no GPU, cgroup v1, kernel network namespaces available; PostgreSQL 16 + pgvector,
Redis 7, Node 22, Python 3.13. Verifier root; non-privileged tests ran as the unprivileged `atta-tester` identity;
qualification steps use disposable PostgreSQL clusters, their own Redis ports, runtime and bucket, a private source
copy and, for AIR_GAPPED, a new kernel network namespace.

## S01F runs (all kept in `evidence/build_controller/ledger.jsonl`)

| Run | S01F-T01 deps | S01F-T02 bundle | S01F-T03 LOCAL | S01F-T04 AIR_GAPPED | S01F-T05 H100 | Outcome |
|---|---|---|---|---|---|---|
| 1 `…4RY47CGJ` | PASS | PASS | FAIL: Stage 01 platforms reached the production DB (relative isolation-overlay path) | not reached | not reached | aborted; fixed (absolute paths, loader fails closed) |
| 2 `…FX3044MX` | PASS | FAIL: bundle rebuild into an existing folder | FAIL: nested isolation, S01-T11 overlay precedence, nested air-gap manifest, S01B-T15 database | not reached | not reached | aborted; fixed, re-verified individually |
| 3 `…AJR78Z4K` | PASS | FAIL: same bundle rebuild defect (fixed afterwards) | FAIL: Q-FRONTEND only (stale root-owned `test-results/`) | partial, below | not reached | **stopped externally** |

### Run 3: LOCAL suite (`qual_01M88ZT4JJCDGGEB9KYK6R57GA`, status FAIL)

| Step | Title | Status | Identity | Exit |
|---|---|---|---|---|
| Q-DEPS | Dependency locks match installed packages (no drift) | PASS | atta-tester | 0 |
| Q-OFFLINE-AUDIT | No hidden Internet dependency in core configuration and frontend sources | PASS | atta-tester | 0 |
| Q-UNIT | Unit, integration and security suites | PASS | atta-tester | 0 |
| Q-S01-REGRESSION | Stage 01 acceptance with explicit supersession (S01A-T48) | PASS | atta-tester | 0 |
| Q-S01A-CORE | Stage 01A controller acceptance T01-T27 | PASS | atta-tester | 0 |
| Q-S01A-REST | Stage 01A acceptance T28-T53 (without long regressions and OS-protection check) | PASS | atta-tester | 0 |
| Q-S01B | Stage 01B acceptance (environment-independent subset) | PASS | atta-tester | 0 |
| Q-S01B-PRIV | Stage 01B acceptance needing OS privileges (network namespaces, cgroups, user sw | PASS | root | 0 |
| Q-LEGACY | Existing ATTa application builder regression suite | PASS | root | 0 |
| Q-FRONTEND | Frontend + backend startup end-to-end in a real browser | FAIL | atta-tester | 1 |
| Q-WINDOWS-JOBS | Windows worker isolation: Job Object tree kill + memory limit, restricted LOW-in | BLOCKED | — | not executed |

Q-FRONTEND failed only because a git-ignored `test-results/` folder left by earlier root executions could not be
removed by the test identity. The folder was removed (no source change) and Q-FRONTEND re-run individually as
`atta-tester`: **PASS**. Q-WINDOWS-JOBS is environment-specific and **BLOCKED** (no Windows host).

### Run 3: AIR_GAPPED suite, steps completed before the stop (`qual_01M890DF4JZDPAGJS9NSGTZPPM`)

| Step | Result |
|---|---|
| Q-DEPS | completed (builtin/check step; see its log) |
| Q-LEGACY | 1 passed in 70.27s (0:01:10) |
| Q-NETWORK-DENIED | completed (builtin/check step; see its log) |
| Q-OFFLINE-AUDIT | completed (builtin/check step; see its log) |
| Q-S01-OFFLINE | 9 passed, 7 deselected in 41.87s |
| Q-S01A-CORE | 27 passed in 85.32s (0:01:25) |
| Q-S01A-REST | 23 passed, 3 deselected in 113.71s (0:01:53) |
| Q-UNIT | 43 passed in 9.84s |

Not reached before the stop: Q-FRONTEND (air-gapped) and Q-OFFLINE-INSTALL. Both passed in the earlier standalone
AIR_GAPPED qualification of Stage 01B (10/10), but not inside an S01F run.

## Requirement coverage

Stage 01 foundation, the Stage 01A controller (Focus-4, tool pool, gateway, drift guard, budgets, cancellation,
recovery, verification, audit records) and Stage 01B deployment (discovery, profiles, manifests, installer, offline
bundles, isolation, health) are covered by their passed gates: S01 (passed), S01A (passed), S01B (gate run 5 passed).
The FINAL gate's purpose, one combined isolated re-qualification on the final revision, is outstanding.

## Outstanding

1. **One complete S01F run on the final revision**: re-run S01B first (`bundle.py` changed after run 5, ~25 min), then
   `verify.py verify-stage S01F --base <S01B evidence commit>` (~50 min).
2. Physical qualification (PHYSICAL_QUALIFICATION.md): Windows (S01B-T41, Q-WINDOWS-JOBS) and H100 (S01B-T43,
   S01F-T05) remain BLOCKED: implemented, never executed on that hardware.
