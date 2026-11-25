# STAGE 01B VERIFICATION — adaptive deployment, isolated offline qualification

**Result: STAGE S01B GATE PASSED** — run `run_01M88VWJP33FAQN0VZEPVXRNCV` at commit `70778c95c0a585734c20a00ff8535fca7d884286`, base `21104c413c`.
43 acceptance tests PASSED, 0 failed, 2 environment-specific tests **BLOCKED
(not executed)**: S01B-T41 (Windows only) and S01B-T43 (NVIDIA H100 only). BLOCKED is never counted as PASS; both
implementations are present and unchanged (see PHYSICAL_QUALIFICATION.md). Verified checkpoint `checkpoint-S01B-EPVXRNCV`.

Executed by the independent build controller (`python -I -P backend/verification/verify.py verify-stage S01B`) under the
Ed25519-signed seal of the protected criteria, with OS protection verified before the run. Every result is appended to
the hash-chained ledger `evidence/build_controller/ledger.jsonl`; the per-test evidence files (command, cwd, identity,
UTC start/finish, exit code, output and its SHA-256) are in `evidence/build_controller/runs/run_01M88VWJP33FAQN0VZEPVXRNCV/`.

## Environment

Linux x86_64 (kernel 6.18), 4 cores, 16 GB RAM, no GPU, cgroup v1, PostgreSQL 16 + pgvector 0.6, Redis 7.0, Node 22,
Python 3.13. Verifier: root. **Test identity: `atta-tester`** (uid 30034, unprivileged) for every test not declared
`privileged`; privileged tests (network namespaces, cgroups/user switching, the legacy suite's `chown`) ran as the
verifier and are marked `root` below. Test processes receive no `ATTA_BUILD_*` secrets; the signing key is encrypted at
rest and unreadable by `atta-tester` and the worker account (S01B-T42).

Example command (each row's exact command is in its evidence file): `/usr/sbin/runuser -u atta-tester -- /home/user/ATTa/.venv/bin/python -m pytest -q -p no:cacheprovider tests/acceptance/test_s01b_acceptance.py::test_S01B_T01_environment_discovery_on_supported_os`

## Results — run `run_01M88VWJP33FAQN0VZEPVXRNCV`

| Test | Requirement | Package | Status | Identity | Started (UTC) | Finished (UTC) | Exit | Evidence (file, SHA-256) |
|---|---|---|---|---|---|---|---|---|
| S01B-T01 | environment discovery on supported os | S01B-PKG01 | PASS | atta-tester | 2026-11-25T08:06:53.431Z | 2026-11-25T08:06:56.176Z | 0 | `S01B-T01.json` `14e3ad3bc00f…` |
| S01B-T02 | deterministic profile selection | S01B-PKG01 | PASS | atta-tester | 2026-11-25T08:06:56.179Z | 2026-11-25T08:06:58.488Z | 0 | `S01B-T02.json` `9deb7cb5aefd…` |
| S01B-T03 | explicit profile override validation | S01B-PKG01 | PASS | atta-tester | 2026-11-25T08:06:58.490Z | 2026-11-25T08:07:02.976Z | 0 | `S01B-T03.json` `430fd996193c…` |
| S01B-T04 | reject incompatible h100 profile | S01B-PKG01 | PASS | atta-tester | 2026-11-25T08:07:02.982Z | 2026-11-25T08:07:05.385Z | 0 | `S01B-T04.json` `6decf765d78f…` |
| S01B-T05 | environment manifest persistence | S01B-PKG01 | PASS | atta-tester | 2026-11-25T08:07:05.388Z | 2026-11-25T08:07:07.560Z | 0 | `S01B-T05.json` `47a2fe0ab65b…` |
| S01B-T06 | manifest integrity validation | S01B-PKG01 | PASS | atta-tester | 2026-11-25T08:07:07.562Z | 2026-11-25T08:07:09.901Z | 0 | `S01B-T06.json` `3543503d065d…` |
| S01B-T07 | model discovery from configured path | S01B-PKG01 | PASS | atta-tester | 2026-11-25T08:07:09.906Z | 2026-11-25T08:07:11.292Z | 0 | `S01B-T07.json` `360f32975cf7…` |
| S01B-T08 | removable model path discovery | S01B-PKG01 | PASS | atta-tester | 2026-11-25T08:07:11.295Z | 2026-11-25T08:07:12.660Z | 0 | `S01B-T08.json` `999402697a78…` |
| S01B-T09 | no unnecessary model copying | S01B-PKG01 | PASS | atta-tester | 2026-11-25T08:07:12.664Z | 2026-11-25T08:07:13.896Z | 0 | `S01B-T09.json` `36c4e442d87d…` |
| S01B-T10 | missing model returns blocked | S01B-PKG01 | PASS | atta-tester | 2026-11-25T08:07:13.898Z | 2026-11-25T08:07:17.374Z | 0 | `S01B-T10.json` `00dcd53fd710…` |
| S01B-T11 | runtime discovery | S01B-PKG01 | PASS | atta-tester | 2026-11-25T08:07:17.378Z | 2026-11-25T08:07:20.039Z | 0 | `S01B-T11.json` `8f3daf069ab5…` |
| S01B-T12 | port conflict detection | S01B-PKG02 | PASS | atta-tester | 2026-11-25T08:07:20.048Z | 2026-11-25T08:07:31.202Z | 0 | `S01B-T12.json` `425aa69a8917…` |
| S01B-T13 | configuration precedence | S01B-PKG02 | PASS | atta-tester | 2026-11-25T08:07:31.205Z | 2026-11-25T08:07:32.834Z | 0 | `S01B-T13.json` `18c313b517ec…` |
| S01B-T14 | secret exclusion from generated reports | S01B-PKG02 | PASS | atta-tester | 2026-11-25T08:07:32.837Z | 2026-11-25T08:07:36.173Z | 0 | `S01B-T14.json` `e40145a8fdd6…` |
| S01B-T15 | installation plan generation | S01B-PKG02 | PASS | atta-tester | 2026-11-25T08:07:36.175Z | 2026-11-25T08:07:38.682Z | 0 | `S01B-T15.json` `8717976dcc6d…` |
| S01B-T16 | resumable installation | S01B-PKG02 | PASS | atta-tester | 2026-11-25T08:07:38.684Z | 2026-11-25T08:07:43.426Z | 0 | `S01B-T16.json` `3a896a69df6f…` |
| S01B-T17 | rollback after controlled failure | S01B-PKG02 | PASS | atta-tester | 2026-11-25T08:07:43.429Z | 2026-11-25T08:08:02.614Z | 0 | `S01B-T17.json` `cf8fca58221d…` |
| S01B-T34 | thin launcher test | S01B-PKG02 | PASS | atta-tester | 2026-11-25T08:08:02.616Z | 2026-11-25T08:08:04.492Z | 0 | `S01B-T34.json` `a3b42e19b4a7…` |
| S01B-T35 | new capability discovery requires no launcher modification | S01B-PKG02 | PASS | atta-tester | 2026-11-25T08:08:04.495Z | 2026-11-25T08:08:06.430Z | 0 | `S01B-T35.json` `eb35568e6b79…` |
| S01B-T18 | dependency lock validation | S01B-PKG03 | PASS | atta-tester | 2026-11-25T08:08:06.437Z | 2026-11-25T08:08:11.201Z | 0 | `S01B-T18.json` `a8dfc4c0070e…` |
| S01B-T19 | offline dependency manifest validation | S01B-PKG03 | PASS | atta-tester | 2026-11-25T08:08:11.203Z | 2026-11-25T08:08:52.552Z | 0 | `S01B-T19.json` `9c5c11a1f5eb…` |
| S01B-T20 | core frontend contains no mandatory external assets | S01B-PKG03 | PASS | atta-tester | 2026-11-25T08:08:52.555Z | 2026-11-25T08:08:53.979Z | 0 | `S01B-T20.json` `170fa7c74248…` |
| S01B-T21 | isolated test database | S01B-PKG04 | PASS | atta-tester | 2026-11-25T08:08:53.988Z | 2026-11-25T08:08:57.646Z | 0 | `S01B-T21.json` `a727a6a80682…` |
| S01B-T22 | isolated redis state | S01B-PKG04 | PASS | atta-tester | 2026-11-25T08:08:57.650Z | 2026-11-25T08:09:07.722Z | 0 | `S01B-T22.json` `b57b4a8dd7fb…` |
| S01B-T23 | isolated filesystem workspace | S01B-PKG04 | PASS | atta-tester | 2026-11-25T08:09:07.726Z | 2026-11-25T08:09:13.168Z | 0 | `S01B-T23.json` `42a2d9ee2688…` |
| S01B-T24 | production state remains unchanged by qualification | S01B-PKG04 | PASS | atta-tester | 2026-11-25T08:09:13.171Z | 2026-11-25T08:09:19.280Z | 0 | `S01B-T24.json` `c049d9818611…` |
| S01B-T25 | external network denial detection | S01B-PKG04 | PASS | root | 2026-11-25T08:09:19.284Z | 2026-11-25T08:09:28.763Z | 0 | `S01B-T25.json` `bf3eece87785…` |
| S01B-T26 | offline core startup | S01B-PKG04 | PASS | root | 2026-11-25T08:09:28.766Z | 2026-11-25T08:09:38.039Z | 0 | `S01B-T26.json` `3b36ec7b7663…` |
| S01B-T27 | dynamic dependency failure propagation | S01B-PKG04 | PASS | atta-tester | 2026-11-25T08:09:38.044Z | 2026-11-25T08:09:41.514Z | 0 | `S01B-T27.json` `7283762c0c15…` |
| S01B-T28 | service recovery revalidation | S01B-PKG04 | PASS | atta-tester | 2026-11-25T08:09:41.518Z | 2026-11-25T08:09:45.684Z | 0 | `S01B-T28.json` `e7bbdf2fe309…` |
| S01B-T41 | windows specific tests | S01B-PKG04 | BLOCKED | — | — | — | not executed | `S01B-T41.json` `971200cf9a84…` |
| S01B-T42 | linux specific tests | S01B-PKG04 | PASS | root | 2026-11-25T08:09:45.688Z | 2026-11-25T08:09:51.045Z | 0 | `S01B-T42.json` `efc7ba077d17…` |
| S01B-T29 | stage01a controller works under local cpu profile | S01B-PKG05 | PASS | atta-tester | 2026-11-25T08:09:51.053Z | 2026-11-25T08:10:12.105Z | 0 | `S01B-T29.json` `5c3cbd047c65…` |
| S01B-T30 | focus4 works in isolated qualification | S01B-PKG05 | PASS | atta-tester | 2026-11-25T08:10:12.108Z | 2026-11-25T08:10:35.398Z | 0 | `S01B-T30.json` `569c73eac40b…` |
| S01B-T31 | drift guard works in isolated qualification | S01B-PKG05 | PASS | atta-tester | 2026-11-25T08:10:35.401Z | 2026-11-25T08:10:54.821Z | 0 | `S01B-T31.json` `d9e55d9a59b0…` |
| S01B-T32 | cancellation leaves no subsequent local side effects | S01B-PKG05 | PASS | atta-tester | 2026-11-25T08:10:54.825Z | 2026-11-25T08:11:17.197Z | 0 | `S01B-T32.json` `117e3c7d4bd1…` |
| S01B-T33 | restart restores verified state | S01B-PKG05 | PASS | atta-tester | 2026-11-25T08:11:17.201Z | 2026-11-25T08:11:47.720Z | 0 | `S01B-T33.json` `e5aa9cd4c81d…` |
| S01B-T36 | legacy atta regression suite | S01B-PKG06 | PASS | root | 2026-11-25T08:11:47.727Z | 2026-11-25T08:13:18.285Z | 0 | `S01B-T36.json` `5c5928c6fb80…` |
| S01B-T37 | stage01 regression suite | S01B-PKG06 | PASS | atta-tester | 2026-11-25T08:13:18.287Z | 2026-11-25T08:14:54.737Z | 0 | `S01B-T37.json` `a5fc3096d39c…` |
| S01B-T38 | stage01a regression suite | S01B-PKG06 | PASS | atta-tester | 2026-11-25T08:14:54.739Z | 2026-11-25T08:19:13.344Z | 0 | `S01B-T38.json` `5c4be08d277c…` |
| S01B-T39 | clean installation qualification | S01B-PKG06 | PASS | atta-tester | 2026-11-25T08:19:13.347Z | 2026-11-25T08:19:47.204Z | 0 | `S01B-T39.json` `fd44ee5f4645…` |
| S01B-T40 | upgrade from current baseline qualification | S01B-PKG06 | PASS | atta-tester | 2026-11-25T08:19:47.208Z | 2026-11-25T08:20:06.837Z | 0 | `S01B-T40.json` `3ec86ea6ad72…` |
| S01B-T43 | h100 hardware tests pass only on actual hardware | S01B-PKG06 | BLOCKED | — | — | — | not executed | `S01B-T43.json` `23a57b10d5d6…` |
| S01B-T44 | unavailable h100 tests remain blocked elsewhere | S01B-PKG06 | PASS | atta-tester | 2026-11-25T08:20:06.845Z | 2026-11-25T08:20:13.718Z | 0 | `S01B-T44.json` `b0e4b10389c9…` |
| S01B-T45 | complete requirement to evidence traceability | S01B-PKG06 | PASS | atta-tester | 2026-11-25T08:20:13.720Z | 2026-11-25T08:20:14.871Z | 0 | `S01B-T45.json` `6a9aa1191869…` |

## Gate history (all runs kept in the ledger)

| Run | Gate | Commit | Packages |
|---|---|---|---|
| run_01M88SX6G9K5GJ8WJN0FYAYG8J | FAILED | `006ff39403` | S01B-PKG01=PASSED, S01B-PKG02=PASSED, S01B-PKG03=PASSED, S01B-PKG04=FAILED, S01B-PKG05=BLOCKED, S01B-PKG06=BLOCKED |
| run_01M88T3976DA0CFMNV76HY9MQY | FAILED | `416ca300e9` | S01B-PKG01=PASSED, S01B-PKG02=PASSED, S01B-PKG03=PASSED, S01B-PKG04=PASSED, S01B-PKG05=PASSED, S01B-PKG06=FAILED |
| run_01M88VWJP33FAQN0VZEPVXRNCV | PASSED | `70778c95c0` | S01B-PKG01=PASSED, S01B-PKG02=PASSED, S01B-PKG03=PASSED, S01B-PKG04=PASSED, S01B-PKG05=PASSED, S01B-PKG06=PASSED |

* **Run 1 FAILED** — S01B-T42's check for leaked verifier variables also matched `ATTA_BUILD_CONTROLLER_RUN`, the
  non-secret run id the verifier passes on; narrowed to secret-bearing variables. PKG05/PKG06 were BLOCKED behind it.
* **Run 2 FAILED** — T36/T37/T38: the private source copy used for re-running the unchanged Stage 01/01A suites had no
  git history and no local configuration (database credentials), so the legacy diff and database-backed tests failed.
  The copy is now a shared clone overlaid with the working tree, the git-ignored local configuration and links to the
  installed dependency trees (`.venv`, `node_modules`).
* Pre-checks for run 3 (unprivileged identity) found and fixed: the proxy CA bundle of this build machine is under
  `/root` and unreadable by `atta-tester` (public certificate bundles are now shared read-only with the test identity;
  files containing private keys never are); the legacy suite changes file ownership and therefore runs privileged.
* **Run 3 PASSED** (this report).

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
