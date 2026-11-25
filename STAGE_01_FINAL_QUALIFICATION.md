# STAGE 01 FINAL QUALIFICATION

**Verdict: STAGE 01 FINAL GATE PASSED (pre-live qualification).** Run `run_01M892B6CWSF7TPB7CYFYK63A4` (FINAL run 4) qualified source revision
`3fb3916ff461e7f1fae95c07c3fe32a739338687` (base `3fb3916ff4`). S01F-T01…T04 PASSED; S01F-T05 (H100 hardware) **BLOCKED — not executed**
(no H100 on the qualification machine). Verified checkpoint `checkpoint-S01F-YFYK63A4`.

**Physical production qualification is outstanding** for Windows and NVIDIA H100 (PHYSICAL_QUALIFICATION.md): those
implementations are present and unchanged but have never run on that hardware, and are never reported as passed.

## Release source revision

| Item | Value |
|---|---|
| Final application source (frozen, sealed) | `86f3c88` (seal of the final candidate) |
| S01B gate run 6 PASSED on it | `run_01M891PVYCP7XPDTA9583Z3X2P` — evidence commit `86f3c8878b`… |
| FINAL gate run 4 PASSED on | `3fb3916ff461e7f1fae95c07c3fe32a739338687` (= final source + S01B evidence only) |
| Release tag | `v1-stage01` (local; the remote refuses tag pushes from this environment, HTTP 403 — not published) |

## Qualification environment

Linux x86_64 (kernel 6.18), 4 cores, 16 GB RAM, no GPU, cgroup v1, kernel network namespaces; PostgreSQL 16 +
pgvector, Redis 7, Node 22, Python 3.13. Verifier: root, Ed25519-signed seal (key encrypted at rest), OS protection
intact. Non-privileged steps ran as the unprivileged `atta-tester` identity. Every qualification suite used a
disposable PostgreSQL cluster, its own Redis port, runtime directory and bucket, a hermetic git configuration, a
private source copy for the earlier stages' unchanged suites and — for AIR_GAPPED — a new kernel network namespace with
no route to the Internet.

## Gate results — run `run_01M892B6CWSF7TPB7CYFYK63A4`

| Test | Requirement | Status | Identity | Started (UTC) | Finished (UTC) | Exit | Evidence |
|---|---|---|---|---|---|---|---|
| S01F-T01 | dependency locks match the installed environment | PASS | atta-tester | 2026-11-25T09:59:43.792Z | 2026-11-25T09:59:44.903Z | 0 | `S01F-T01.json` `9a7e6a848dfd…` |
| S01F-T02 | offline dependency bundle built from the locks | PASS | atta-tester | 2026-11-25T09:59:44.909Z | 2026-11-25T10:00:47.984Z | 0 | `S01F-T02.json` `67bca188bf30…` |
| S01F-T03 | LOCAL suite in isolated resources: Stage 01 regression with superseded-test accounting, Stage 0 | PASS | root | 2026-11-25T10:00:47.990Z | 2026-11-25T10:11:37.518Z | 0 | `S01F-T03.json` `67230a96631b…` |
| S01F-T04 | AIR_GAPPED suite inside a new kernel network namespace, incl. offline install from the bundle | PASS | root | 2026-11-25T10:11:37.522Z | 2026-11-25T10:18:10.639Z | 0 | `S01F-T04.json` `77cab70708ac…` |
| S01F-T05 | H100 hardware qualification (real H100 only) | BLOCKED | — | — | — | not executed | `S01F-T05.json` `bf445e02b486…` |

### LOCAL suite — `qual_01M892D5AM0HWGQAC5JVV4G07R`: **PASS** (10 PASS, 0 FAIL, 1 BLOCKED); isolation: host, disposable cluster on 127.0.0.1:40093, test identity `atta-tester`

| Step | What it qualifies | Status | Identity | Exit | Evidence SHA-256 |
|---|---|---|---|---|---|
| Q-DEPS | Dependency locks match installed packages (no drift) | PASS | atta-tester | 0 | `b012f44804bf` |
| Q-OFFLINE-AUDIT | No hidden Internet dependency in core configuration and frontend sources | PASS | atta-tester | 0 | `e01f2342601a` |
| Q-UNIT | Unit, integration and security suites | PASS | atta-tester | 0 | `bc936711c292` |
| Q-S01-REGRESSION | Stage 01 acceptance with explicit supersession (S01A-T48) | PASS | atta-tester | 0 | `79d57f5d1a55` |
| Q-S01A-CORE | Stage 01A controller acceptance T01-T27 | PASS | atta-tester | 0 | `27024b4a16f8` |
| Q-S01A-REST | Stage 01A acceptance T28-T53 (without long regressions and OS-protection check) | PASS | atta-tester | 0 | `648d3fd45138` |
| Q-S01B | Stage 01B acceptance (environment-independent subset) | PASS | atta-tester | 0 | `6502f2d8d585` |
| Q-S01B-PRIV | Stage 01B acceptance needing OS privileges (network namespaces, cgroups, user switching) | PASS | root | 0 | `6678460d51a3` |
| Q-LEGACY | Existing ATTa application builder regression suite | PASS | root | 0 | `23b74da5def1` |
| Q-FRONTEND | Frontend + backend startup end-to-end in a real browser | PASS | atta-tester | 0 | `17419e088be2` |
| Q-WINDOWS-JOBS | Windows worker isolation: Job Object tree kill + memory limit, restricted LOW-integrity to | BLOCKED | — | not executed: requires Windows (this is linux); Windows-specific behaviour | `—` |

### AIR_GAPPED suite — `qual_01M8930ZMHC2TW2X5SYKVMHEWT`: **PASS** (10 PASS, 0 FAIL, 0 BLOCKED); isolation: new kernel network namespace (loopback only), disposable cluster on 127.0.0.1:38555, test identity `atta-tester`

| Step | What it qualifies | Status | Identity | Exit | Evidence SHA-256 |
|---|---|---|---|---|---|
| Q-NETWORK-DENIED | External network is unreachable inside the qualification namespace | PASS | root | 0 | `1940eda9e09d` |
| Q-DEPS | Dependency locks match installed packages (no drift) | PASS | atta-tester | 0 | `b012f44804bf` |
| Q-OFFLINE-AUDIT | No hidden Internet dependency in core configuration and frontend sources | PASS | atta-tester | 0 | `e01f2342601a` |
| Q-UNIT | Unit, integration and security suites | PASS | atta-tester | 0 | `d1ba9cfda618` |
| Q-S01-OFFLINE | Stage 01 acceptance that needs no package registry | PASS | atta-tester | 0 | `4d142a391e88` |
| Q-S01A-CORE | Stage 01A controller acceptance T01-T27 | PASS | atta-tester | 0 | `dc26b1c68f9e` |
| Q-S01A-REST | Stage 01A acceptance T28-T53 (without long regressions and OS-protection check) | PASS | atta-tester | 0 | `964ff9f09a71` |
| Q-LEGACY | Existing ATTa application builder regression suite | PASS | root | 0 | `f95a6bd4051c` |
| Q-FRONTEND | Frontend + backend startup end-to-end in a real browser | PASS | atta-tester | 0 | `dd20134f47e2` |
| Q-OFFLINE-INSTALL | Clean installation from the offline dependency bundle with no network | PASS | root | 0 | `532da2d093fe` |

## Combined Stage 01 coverage (what these suites exercised)

| Area | Where qualified |
|---|---|
| Existing ATTa application builder (legacy suite, unchanged baseline) | Q-LEGACY (LOCAL + AIR_GAPPED), S01B-T36 |
| Stage 01 foundation: structure, clean install, service manager start/stop/restart, liveness/readiness, dependency failure, build controller, register, default-DENY | Q-S01-REGRESSION (S01A-T48: Stage 01 suite with the 5 superseded tests proven to fail only under stricter controls), Q-S01-OFFLINE |
| Stage 01A controller: contracts, numbered tool catalogue, allocation, Focus-4, gateway, drift guard, budgets, cancellation, verification, repair, checkpoints, recovery, traceability, controller startup | Q-S01A-CORE, Q-S01A-REST (LOCAL + AIR_GAPPED) |
| Stage 01B deployment: discovery, profiles, manifests, model/runtime selection, configuration precedence, installer/plan/resume/rollback, locks, offline audit, isolation, health, launchers, clean install, upgrade | Q-S01B, Q-S01B-PRIV, S01B gate run 6 (43 PASS / 2 BLOCKED) |
| Frontend ↔ backend in a real browser | Q-FRONTEND (LOCAL + AIR_GAPPED) |
| Offline operation and offline installation from the bundle | Q-NETWORK-DENIED, Q-OFFLINE-INSTALL (AIR_GAPPED) |
| Dependency integrity | Q-DEPS, S01F-T01, S01F-T02 |

## Earlier FINAL runs (kept in the ledger)

| Run | Outcome |
|---|---|
| 1 `…4RY47CGJ` | aborted: Stage 01 platforms reached the production database (relative isolation-overlay path) — fixed |
| 2 `…FX3044MX` | aborted: nested isolation contexts, S01-T11 overlay precedence, nested air-gap manifest, S01B-T15 database, bundle rebuild — fixed |
| 3 `…AJR78Z4K` | stopped externally; LOCAL Q-FRONTEND failed (frontend test wrote `test-results/` into the repository; fixed at the cause: runs in the private source copy), S01F-T02 bundle rebuild — fixed |
| 4 `…YFYK63A4` | **PASSED** (this report) |

## Outstanding (physical, not software)

1. Windows 10/11 x64: S01B-T41 and Q-WINDOWS-JOBS — BLOCKED (no Windows host). Windows offline bundle lacks pgvector,
   Redis/Memurai, Git for Windows and browsers (not obtainable from the build machine): **Windows offline installation is
   not complete and not qualified.**
2. NVIDIA H100 80 GB: S01B-T43 and S01F-T05 — BLOCKED (no H100). Inference qualification additionally needs Stage 03.
3. Container images in the offline bundle — BLOCKED (no Docker daemon).
