# STAGE 01B VERIFICATION — adaptive deployment, isolated offline qualification

**Result: STAGE S01B GATE PASSED** — run `run_01M88Z4MBQAZEHH2SC614682YJ` (gate run 5), qualified source revision `42edea8ad5c04e29212c0d05ee4192fc4ad4c80e`, base
`21104c413c`. 43 acceptance tests PASSED, 0 failed, 2 environment-specific tests
**BLOCKED (not executed)**: S01B-T41 (Windows only) and S01B-T43 (NVIDIA H100 only). BLOCKED is never counted as PASS;
both implementations are present and unchanged (PHYSICAL_QUALIFICATION.md). Verified checkpoint
`checkpoint-S01B-614682YJ`.

Executed by the independent build controller (`python -I -P backend/verification/verify.py verify-stage S01B`) under the
Ed25519-signed seal of the protected criteria, with OS protection verified before the run. Every result is appended to
the hash-chained ledger `evidence/build_controller/ledger.jsonl`; the per-test evidence files (command, cwd, identity,
UTC start/finish, exit code, output and its SHA-256) are in `evidence/build_controller/runs/run_01M88Z4MBQAZEHH2SC614682YJ/`.

## Environment

Linux x86_64 (kernel 6.18), 4 cores, 16 GB RAM, no GPU, cgroup v1, PostgreSQL 16 + pgvector 0.6, Redis 7.0, Node 22,
Python 3.13. Verifier: root. **Test identity: `atta-tester`** (uid 30034, unprivileged) for every test not declared
`privileged`; privileged tests (network namespaces, cgroups/user switching, the legacy suite's `chown`) ran as the
verifier and are marked `root` below. Test processes receive no `ATTA_BUILD_*` secrets; the signing key is encrypted at
rest and unreadable by `atta-tester` and the worker account (S01B-T42).

Example command (each row's exact command is in its evidence file): `/usr/sbin/runuser -u atta-tester -- /home/user/ATTa/.venv/bin/python -m pytest -q -p no:cacheprovider tests/acceptance/test_s01b_acceptance.py::test_S01B_T01_environment_discovery_on_supported_os`

## Results — run `run_01M88Z4MBQAZEHH2SC614682YJ`

| Test | Requirement | Package | Status | Identity | Started (UTC) | Finished (UTC) | Exit | Evidence (file, SHA-256) |
|---|---|---|---|---|---|---|---|---|
| S01B-T01 | environment discovery on supported os | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:03:43.013Z | 2026-11-25T09:03:45.123Z | 0 | `S01B-T01.json` `4b8a58e3f05a…` |
| S01B-T02 | deterministic profile selection | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:03:45.126Z | 2026-11-25T09:03:46.950Z | 0 | `S01B-T02.json` `921ee25a6b40…` |
| S01B-T03 | explicit profile override validation | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:03:46.953Z | 2026-11-25T09:03:48.846Z | 0 | `S01B-T03.json` `b4011a554d58…` |
| S01B-T04 | reject incompatible h100 profile | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:03:48.849Z | 2026-11-25T09:03:54.892Z | 0 | `S01B-T04.json` `4f36857a8f7d…` |
| S01B-T05 | environment manifest persistence | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:03:54.897Z | 2026-11-25T09:03:56.817Z | 0 | `S01B-T05.json` `7141a4f638ef…` |
| S01B-T06 | manifest integrity validation | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:03:56.820Z | 2026-11-25T09:03:58.931Z | 0 | `S01B-T06.json` `ae19692bf95e…` |
| S01B-T07 | model discovery from configured path | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:03:58.934Z | 2026-11-25T09:04:00.072Z | 0 | `S01B-T07.json` `20b9bba9586f…` |
| S01B-T08 | removable model path discovery | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:04:00.075Z | 2026-11-25T09:04:01.231Z | 0 | `S01B-T08.json` `7010b4daa786…` |
| S01B-T09 | no unnecessary model copying | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:04:01.235Z | 2026-11-25T09:04:02.646Z | 0 | `S01B-T09.json` `fa01055b0f74…` |
| S01B-T10 | missing model returns blocked | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:04:02.650Z | 2026-11-25T09:04:05.974Z | 0 | `S01B-T10.json` `32b33f981d12…` |
| S01B-T11 | runtime discovery | S01B-PKG01 | PASS | atta-tester | 2026-11-25T09:04:05.979Z | 2026-11-25T09:04:07.964Z | 0 | `S01B-T11.json` `222c2dde4870…` |
| S01B-T12 | port conflict detection | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:04:07.973Z | 2026-11-25T09:04:18.208Z | 0 | `S01B-T12.json` `e8faba6ee415…` |
| S01B-T13 | configuration precedence | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:04:18.211Z | 2026-11-25T09:04:19.403Z | 0 | `S01B-T13.json` `2011f50b5895…` |
| S01B-T14 | secret exclusion from generated reports | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:04:19.407Z | 2026-11-25T09:04:21.828Z | 0 | `S01B-T14.json` `e79d54f62d30…` |
| S01B-T15 | installation plan generation | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:04:21.834Z | 2026-11-25T09:04:25.410Z | 0 | `S01B-T15.json` `fed2fa517091…` |
| S01B-T16 | resumable installation | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:04:25.414Z | 2026-11-25T09:04:29.348Z | 0 | `S01B-T16.json` `b6aac1a95ccb…` |
| S01B-T17 | rollback after controlled failure | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:04:29.351Z | 2026-11-25T09:04:47.715Z | 0 | `S01B-T17.json` `0cc89ba5cf8f…` |
| S01B-T34 | thin launcher test | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:04:47.718Z | 2026-11-25T09:04:48.970Z | 0 | `S01B-T34.json` `86ff2f973980…` |
| S01B-T35 | new capability discovery requires no launcher modification | S01B-PKG02 | PASS | atta-tester | 2026-11-25T09:04:48.975Z | 2026-11-25T09:04:50.490Z | 0 | `S01B-T35.json` `0df1edeb9259…` |
| S01B-T18 | dependency lock validation | S01B-PKG03 | PASS | atta-tester | 2026-11-25T09:04:50.499Z | 2026-11-25T09:04:54.305Z | 0 | `S01B-T18.json` `39a917d9a215…` |
| S01B-T19 | offline dependency manifest validation | S01B-PKG03 | PASS | atta-tester | 2026-11-25T09:04:54.308Z | 2026-11-25T09:05:29.120Z | 0 | `S01B-T19.json` `745c3c33557a…` |
| S01B-T20 | core frontend contains no mandatory external assets | S01B-PKG03 | PASS | atta-tester | 2026-11-25T09:05:29.123Z | 2026-11-25T09:05:30.200Z | 0 | `S01B-T20.json` `fdbb72b9f365…` |
| S01B-T21 | isolated test database | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:05:30.210Z | 2026-11-25T09:05:33.323Z | 0 | `S01B-T21.json` `4c142e9d9cba…` |
| S01B-T22 | isolated redis state | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:05:33.326Z | 2026-11-25T09:05:41.156Z | 0 | `S01B-T22.json` `36cd691dd33f…` |
| S01B-T23 | isolated filesystem workspace | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:05:41.161Z | 2026-11-25T09:05:45.683Z | 0 | `S01B-T23.json` `311d404ea995…` |
| S01B-T24 | production state remains unchanged by qualification | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:05:45.687Z | 2026-11-25T09:05:50.996Z | 0 | `S01B-T24.json` `f60808640577…` |
| S01B-T25 | external network denial detection | S01B-PKG04 | PASS | root | 2026-11-25T09:05:51.002Z | 2026-11-25T09:05:55.732Z | 0 | `S01B-T25.json` `c1d12593d89c…` |
| S01B-T26 | offline core startup | S01B-PKG04 | PASS | root | 2026-11-25T09:05:55.736Z | 2026-11-25T09:06:03.883Z | 0 | `S01B-T26.json` `b4631d88b577…` |
| S01B-T27 | dynamic dependency failure propagation | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:06:03.887Z | 2026-11-25T09:06:07.501Z | 0 | `S01B-T27.json` `82aaf87d5d08…` |
| S01B-T28 | service recovery revalidation | S01B-PKG04 | PASS | atta-tester | 2026-11-25T09:06:07.505Z | 2026-11-25T09:06:11.084Z | 0 | `S01B-T28.json` `9ec7d85e1da1…` |
| S01B-T41 | windows specific tests | S01B-PKG04 | BLOCKED | — | — | — | not executed | `S01B-T41.json` `8968e81ef4e9…` |
| S01B-T42 | linux specific tests | S01B-PKG04 | PASS | root | 2026-11-25T09:06:11.094Z | 2026-11-25T09:06:16.274Z | 0 | `S01B-T42.json` `bcd031affba3…` |
| S01B-T29 | stage01a controller works under local cpu profile | S01B-PKG05 | PASS | atta-tester | 2026-11-25T09:06:16.282Z | 2026-11-25T09:06:33.679Z | 0 | `S01B-T29.json` `e33073af148d…` |
| S01B-T30 | focus4 works in isolated qualification | S01B-PKG05 | PASS | atta-tester | 2026-11-25T09:06:33.683Z | 2026-11-25T09:06:52.319Z | 0 | `S01B-T30.json` `2d8d001f5e31…` |
| S01B-T31 | drift guard works in isolated qualification | S01B-PKG05 | PASS | atta-tester | 2026-11-25T09:06:52.322Z | 2026-11-25T09:07:09.433Z | 0 | `S01B-T31.json` `5299f238150b…` |
| S01B-T32 | cancellation leaves no subsequent local side effects | S01B-PKG05 | PASS | atta-tester | 2026-11-25T09:07:09.437Z | 2026-11-25T09:07:29.235Z | 0 | `S01B-T32.json` `463afbc57cb0…` |
| S01B-T33 | restart restores verified state | S01B-PKG05 | PASS | atta-tester | 2026-11-25T09:07:29.238Z | 2026-11-25T09:07:53.026Z | 0 | `S01B-T33.json` `3a9a6a8d551f…` |
| S01B-T36 | legacy atta regression suite | S01B-PKG06 | PASS | root | 2026-11-25T09:07:53.035Z | 2026-11-25T09:09:16.474Z | 0 | `S01B-T36.json` `ecc46f910ff1…` |
| S01B-T37 | stage01 regression suite | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:09:16.479Z | 2026-11-25T09:10:34.911Z | 0 | `S01B-T37.json` `89bcb065cc86…` |
| S01B-T38 | stage01a regression suite | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:10:34.914Z | 2026-11-25T09:13:56.054Z | 0 | `S01B-T38.json` `8ed522b97630…` |
| S01B-T39 | clean installation qualification | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:13:56.059Z | 2026-11-25T09:14:21.810Z | 0 | `S01B-T39.json` `69ac267efde5…` |
| S01B-T40 | upgrade from current baseline qualification | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:14:21.814Z | 2026-11-25T09:14:38.210Z | 0 | `S01B-T40.json` `4485da4faa74…` |
| S01B-T43 | h100 hardware tests pass only on actual hardware | S01B-PKG06 | BLOCKED | — | — | — | not executed | `S01B-T43.json` `b07b9335e73d…` |
| S01B-T44 | unavailable h100 tests remain blocked elsewhere | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:14:38.216Z | 2026-11-25T09:14:44.332Z | 0 | `S01B-T44.json` `d44f18ff79a0…` |
| S01B-T45 | complete requirement to evidence traceability | S01B-PKG06 | PASS | atta-tester | 2026-11-25T09:14:44.335Z | 2026-11-25T09:14:45.306Z | 0 | `S01B-T45.json` `ae01220116d1…` |

## Gate history (every run is kept in the ledger)

| # | Run | Gate | Commit |
|---|---|---|---|
| 1 | `run_01M88SX6G9K5GJ8WJN0FYAYG8J` | FAILED | `006ff39403` |
| 2 | `run_01M88T3976DA0CFMNV76HY9MQY` | FAILED | `416ca300e9` |
| 3 | `run_01M88VWJP33FAQN0VZEPVXRNCV` | PASSED | `70778c95c0` |
| 4 | `run_01M88XAQFVJ9Q4B1Y1YXKX1H1E` | PASSED | `efc81d5ad3` |
| 5 | `run_01M88Z4MBQAZEHH2SC614682YJ` | PASSED | `42edea8ad5` |

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
  fixes) — this report.

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
