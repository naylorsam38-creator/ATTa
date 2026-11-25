# STAGE 01B VERIFICATION — adaptive deployment, isolated offline qualification

**Result: STAGE S01B GATE PASSED** — run `run_01M891PVYCP7XPDTA9583Z3X2P` (gate run 6) on the frozen final source revision
`86f3c8878b53a09b96f5a70ca0cf40139940101d`. 43 acceptance tests PASSED, 0 failed, 2 environment-specific tests
**BLOCKED (not executed)**: S01B-T41 (Windows only) and S01B-T43 (NVIDIA H100 only). BLOCKED is never counted as PASS;
both implementations are present and unchanged (PHYSICAL_QUALIFICATION.md). Verified checkpoint
`checkpoint-S01B-583Z3X2P`. The Stage 01 FINAL gate subsequently PASSED on this revision
(STAGE_01_FINAL_QUALIFICATION.md).

Executed by the independent build controller (`python -I -P backend/verification/verify.py verify-stage S01B`) under the
Ed25519-signed seal of the protected criteria, with OS protection verified before the run. Every result is appended to
the hash-chained ledger `evidence/build_controller/ledger.jsonl`; the per-test evidence files (command, cwd, identity,
UTC start/finish, exit code, output and its SHA-256) are in `evidence/build_controller/runs/run_01M891PVYCP7XPDTA9583Z3X2P82YJ/`.

## Environment

Linux x86_64 (kernel 6.18), 4 cores, 16 GB RAM, no GPU, cgroup v1, PostgreSQL 16 + pgvector 0.6, Redis 7.0, Node 22,
Python 3.13. Verifier: root. **Test identity: `atta-tester`** (uid 30034, unprivileged) for every test not declared
`privileged`; privileged tests (network namespaces, cgroups/user switching, the legacy suite's `chown`) ran as the
verifier and are marked `root` below. Test processes receive no `ATTA_BUILD_*` secrets; the signing key is encrypted at
rest and unreadable by `atta-tester` and the worker account (S01B-T42).

Example command (each row's exact command is in its evidence file): `/usr/sbin/runuser -u atta-tester -- /home/user/ATTa/.venv/bin/python -m pytest -q -p no:cacheprovider tests/acceptance/test_s01b_acceptance.py::test_S01B_T01_environment_discovery_on_supported_os`

## Results — run `run_01M891PVYCP7XPDTA9583Z3X2P`

| Test | Requirement | Package | Status | Identity | Started (UTC) | Finished (UTC) | Exit | Evidence (file, SHA-256) |
|---|---|---|---|---|---|---|---|---|
| S01B-T01 | environment discovery on supported os | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:48:37.789Z | 2026-11-25T09:48:40.271Z | 0 | `S01B-T01.json` `6e609e37df4e…` |
| S01B-T02 | deterministic profile selection | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:48:40.274Z | 2026-11-25T09:48:42.020Z | 0 | `S01B-T02.json` `59238978bcb8…` |
| S01B-T03 | explicit profile override validation | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:48:42.023Z | 2026-11-25T09:48:44.340Z | 0 | `S01B-T03.json` `e9a5b6dad501…` |
| S01B-T04 | reject incompatible h100 profile | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:48:44.345Z | 2026-11-25T09:48:46.699Z | 0 | `S01B-T04.json` `127a20c8142b…` |
| S01B-T05 | environment manifest persistence | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:48:46.702Z | 2026-11-25T09:48:48.753Z | 0 | `S01B-T05.json` `9e0c0558a67e…` |
| S01B-T06 | manifest integrity validation | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:48:48.758Z | 2026-11-25T09:48:50.581Z | 0 | `S01B-T06.json` `be8245da63a3…` |
| S01B-T07 | model discovery from configured path | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:48:50.584Z | 2026-11-25T09:48:51.543Z | 0 | `S01B-T07.json` `aa9bb3d31bf7…` |
| S01B-T08 | removable model path discovery | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:48:51.546Z | 2026-11-25T09:48:52.583Z | 0 | `S01B-T08.json` `72dcc81d34b3…` |
| S01B-T09 | no unnecessary model copying | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:48:52.586Z | 2026-11-25T09:48:53.593Z | 0 | `S01B-T09.json` `92c67b3476b9…` |
| S01B-T10 | missing model returns blocked | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:48:53.597Z | 2026-11-25T09:48:56.509Z | 0 | `S01B-T10.json` `5463cd3a20b4…` |
| S01B-T11 | runtime discovery | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:48:56.513Z | 2026-11-25T09:48:58.422Z | 0 | `S01B-T11.json` `cc6c803eebdc…` |
| S01B-T12 | port conflict detection | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:48:58.431Z | 2026-11-25T09:49:09.200Z | 0 | `S01B-T12.json` `ea8ca2fb58d4…` |
| S01B-T13 | configuration precedence | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:49:09.205Z | 2026-11-25T09:49:10.361Z | 0 | `S01B-T13.json` `d1bee34f3d1a…` |
| S01B-T14 | secret exclusion from generated reports | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:49:10.364Z | 2026-11-25T09:49:12.827Z | 0 | `S01B-T14.json` `7440f49325ce…` |
| S01B-T15 | installation plan generation | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:49:12.831Z | 2026-11-25T09:49:16.382Z | 0 | `S01B-T15.json` `5a63953adaf4…` |
| S01B-T16 | resumable installation | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:49:16.385Z | 2026-11-25T09:49:20.692Z | 0 | `S01B-T16.json` `64b369f9d797…` |
| S01B-T17 | rollback after controlled failure | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:49:20.698Z | 2026-11-25T09:49:39.190Z | 0 | `S01B-T17.json` `55b125ce081c…` |
| S01B-T34 | thin launcher test | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:49:39.193Z | 2026-11-25T09:49:40.361Z | 0 | `S01B-T34.json` `a99bd01d4776…` |
| S01B-T35 | new capability discovery requires no launcher modification | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:49:40.365Z | 2026-11-25T09:49:41.800Z | 0 | `S01B-T35.json` `2cdd2fd7b9a8…` |
| S01B-T18 | dependency lock validation | S01B-PKG03 | PASS | atta-tester | 2026-11-25T09:49:41.808Z | 2026-11-25T09:49:45.815Z | 0 | `S01B-T18.json` `3b4fb1de2bec…` |
| S01B-T19 | offline dependency manifest validation | S01B-PKG03 | PASS | atta-tester | 2026-11-25T09:49:45.818Z | 2026-11-25T09:50:19.167Z | 0 | `S01B-T19.json` `0bc8b1b648f2…` |
| S01B-T20 | core frontend contains no mandatory external assets | S01B-PKG03 | PASS | atta-tester | 2026-11-25T09:50:19.171Z | 2026-11-25T09:50:20.144Z | 0 | `S01B-T20.json` `2734db57fdf1…` |
| S01B-T21 | isolated test database | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:50:20.152Z | 2026-11-25T09:50:22.887Z | 0 | `S01B-T21.json` `ee847aa0155b…` |
| S01B-T22 | isolated redis state | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:50:22.891Z | 2026-11-25T09:50:33.459Z | 0 | `S01B-T22.json` `887003a12884…` |
| S01B-T23 | isolated filesystem workspace | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:50:33.462Z | 2026-11-25T09:50:37.460Z | 0 | `S01B-T23.json` `28d6615d4fe4…` |
| S01B-T24 | production state remains unchanged by qualification | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:50:37.465Z | 2026-11-25T09:50:41.956Z | 0 | `S01B-T24.json` `e2937e388fd4…` |
| S01B-T25 | external network denial detection | S01B-PKG04 | PASS | root | 2026-11-25T09:50:41.961Z | 2026-11-25T09:50:46.342Z | 0 | `S01B-T25.json` `7f6d70260a8e…` |
| S01B-T26 | offline core startup | S01B-PKG04 | PASS | root | 2026-11-25T09:50:46.346Z | 2026-11-25T09:50:54.178Z | 0 | `S01B-T26.json` `2fb5823a3ebd…` |
| S01B-T27 | dynamic dependency failure propagation | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:50:54.184Z | 2026-11-25T09:50:57.302Z | 0 | `S01B-T27.json` `359d3a1a8d1b…` |
| S01B-T28 | service recovery revalidation | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:50:57.306Z | 2026-11-25T09:51:00.543Z | 0 | `S01B-T28.json` `0b10bf41a584…` |
| S01B-T41 | windows specific tests | S01B-PKG04 | BLOCKED | — | — | — | not executed | `S01B-T41.json` `351d0f7f2057…` |
| S01B-T42 | linux specific tests | S01B-PKG04 | PASS | root | 2026-11-25T09:51:00.552Z | 2026-11-25T09:51:05.845Z | 0 | `S01B-T42.json` `391df980f899…` |
| S01B-T29 | stage01a controller works under local cpu profile | S01B-PKG05 | PASS | atta-tester | 2026-11-25T09:51:05.855Z | 2026-11-25T09:51:21.499Z | 0 | `S01B-T29.json` `c59a8152914b…` |
| S01B-T30 | focus4 works in isolated qualification | S01B-PKG05 | PASS | atta-tester | 2026-11-25T09:51:21.503Z | 2026-11-25T09:51:39.240Z | 0 | `S01B-T30.json` `75e763fa8c89…` |
| S01B-T31 | drift guard works in isolated qualification | S01B-PKG05 | PASS | atta-tester | 2026-11-25T09:51:39.244Z | 2026-11-25T09:51:54.368Z | 0 | `S01B-T31.json` `ef607929a535…` |
| S01B-T32 | cancellation leaves no subsequent local side effects | S01B-PKG05 | PASS | atta-tester | 2026-11-25T09:51:54.371Z | 2026-11-25T09:52:14.174Z | 0 | `S01B-T32.json` `569a4858f54e…` |
| S01B-T33 | restart restores verified state | S01B-PKG05 | PASS | atta-tester | 2026-11-25T09:52:14.179Z | 2026-11-25T09:52:36.842Z | 0 | `S01B-T33.json` `2746f229dcad…` |
| S01B-T36 | legacy atta regression suite | S01B-PKG06 | PASS | root | 2026-11-25T09:52:36.851Z | 2026-11-25T09:53:58.364Z | 0 | `S01B-T36.json` `147a1fa220b3…` |
| S01B-T37 | stage01 regression suite | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:53:58.368Z | 2026-11-25T09:55:13.116Z | 0 | `S01B-T37.json` `64254252c0d7…` |
| S01B-T38 | stage01a regression suite | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:55:13.119Z | 2026-11-25T09:58:48.989Z | 0 | `S01B-T38.json` `a05e8bba6a78…` |
| S01B-T39 | clean installation qualification | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:58:48.993Z | 2026-11-25T09:59:18.814Z | 0 | `S01B-T39.json` `a57a4b907e8b…` |
| S01B-T40 | upgrade from current baseline qualification | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:59:18.818Z | 2026-11-25T09:59:35.777Z | 0 | `S01B-T40.json` `78ade273e6c6…` |
| S01B-T43 | h100 hardware tests pass only on actual hardware | S01B-PKG06 | BLOCKED | — | — | — | not executed | `S01B-T43.json` `1e6ed6279213…` |
| S01B-T44 | unavailable h100 tests remain blocked elsewhere | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:59:35.784Z | 2026-11-25T09:59:42.087Z | 0 | `S01B-T44.json` `7875f67df655…` |
| S01B-T45 | complete requirement to evidence traceability | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:59:42.091Z | 2026-11-25T09:59:43.159Z | 0 | `S01B-T45.json` `75f37494014b…` |

## Gate history (every run is kept in the ledger)

| # | Run | Gate | Commit |
|---|---|---|---|
| 1 | `run_01M88SX6G9K5GJ8WJN0FYAYG8J` | FAILED | `006ff39403` |
| 2 | `run_01M88T3976DA0CFMNV76HY9MQY` | FAILED | `416ca300e9` |
| 3 | `run_01M88VWJP33FAQN0VZEPVXRNCV` | PASSED | `70778c95c0` |
| 4 | `run_01M88XAQFVJ9Q4B1Y1YXKX1H1E` | PASSED | `efc81d5ad3` |
| 5 | `run_01M88Z4MBQAZEHH2SC614682YJ` | PASSED | `42edea8ad5` |
| 6 | `run_01M891PVYCP7XPDTA9583Z3X2P` | PASSED | `86f3c8878b` |

* **Run 1 FAILED** — S01B-T42's check for leaked verifier variables also matched `ATTA_BUILD_CONTROLLER_RUN`, the
  non-secret run id the verifier passes on; narrowed to secret-bearing variables. PKG05/PKG06 were BLOCKED behind it.
* **Run 2 FAILED** — T36/T37/T38: the private source copy used for re-running the unchanged Stage 01/01A suites had no
  git history and no local configuration (database credentials), so the legacy diff and database-backed tests failed.
  The copy is now a shared clone overlaid with the working tree, the git-ignored local configuration and links to the
  installed dependency trees (`.venv`, `node_modules`).
* Pre-checks for run 3 (unprivileged identity) found and fixed: the proxy CA bundle of this build machine is under
  `/root` and unreadable by `atta-tester` (public certificate bundles are now shared read-only with the test identity;
  files containing private keys never are); the legacy suite changes file ownership and therefore runs privileged.
* **Run 3 PASSED** at `70778c9`; the Stage 01 FINAL gate then found qualification-environment defects (relative
  isolation-overlay path; nested isolation contexts; S01-T11 closed-port setting overridden by the overlay; the nested
  air-gap run re-reading a manifest another test had overwritten; S01B-T15 depending on the outer database). Each was
  reproduced individually and corrected; S01B was re-qualified:
* **Run 4 PASSED** at `efc81d5` (after the overlay-path fix) and **run 5 PASSED** at `42edea8` (after the remaining
  fixes).
* **Run 6 PASSED** at `86f3c88`, the frozen final revision (after: the frontend test runs in the private source
  copy instead of writing `test-results/` into the repository; build tools provisioned for the test identity; bundle
  rebuild into an existing folder) — this report.

## Superseded tests

None in Stage 01B. The five Stage 01 tests superseded in Stage 01A (S01-T06/T07/T08/T09/T13, DECISIONS D-029) remain
superseded; S01B-T37 re-runs the unchanged Stage 01 suite through S01A-T48, which requires the other tests to pass and
proves each superseded failure is caused only by the stricter Stage 01A controls.

## Preserved evidence

The recorded Stage 01 and Stage 01A evidence (`evidence/stage_01`, `evidence/stage_01a`) is unchanged by Stage 01B:
regression runs execute in a private source copy and assert the evidence digest is identical before and after
(S01B-T29…T33, T36…T38).

## Not qualified on this machine

| Item | Status | Where documented |
|---|---|---|
| Windows worker isolation (S01B-T41) | BLOCKED — implemented, never executed on Windows | PHYSICAL_QUALIFICATION.md §1, BLOCKERS B-S01B-01 |
| H100 hardware (S01B-T43) | BLOCKED — implemented, no H100 | PHYSICAL_QUALIFICATION.md §2, H100_DEPLOYMENT.md |
| Windows offline system components (pgvector, Redis, Git, browsers) | BLOCKED from this build machine | PHYSICAL_QUALIFICATION.md §1.1, B-S01B-10 |
| Container images in the offline bundle | BLOCKED — no Docker daemon | B-S01B-03 |
| Fresh system-package install from the bundle | simulated only (packages present here) | B-S01B-09 |
