# H100_wired_addons-b4.zip (cumulative: Step 0, Items 0, 1, 2, Item M, Stage 19 repair; replaces b3)

- **SHA-256:** `3036b36477648aaa688040ab0135349fa951b9a00a46c6a146a536a20dfc5774`
- **Built from:** `baseline/H100_wired.zip` (sha256 2f335b281538e3fcf563720687dbae293c915d13044cd160ad3836a40b26f37d)
- **Item versions:** Item 0 `4c3ce563e4c2...`; Item 1 `4a1e1a811a9b...` (model_invoke 0.1.1); Item 2 `a28955ea2d18...`; Item M checker `dd41a24cafd30987fa5034489feb16e71769ae18377ce93195599c62428bf4c9`
- **Status:** Built, offline-tested, awaiting live verification. Nothing has been run on the server.
- **Changed original files (declared repairs):** `platform/hub/modules_gateway.py`, `remote/s19c-durable-hub.sh`, `remote/s19d-modules.sh`, `platform/run_checks.py` (coverage step added).
- **New in b4:** blueprint v2 (docs/BLUEPRINT.md), Item M coverage reconciliation (COVERAGE_RECONCILIATION.md, coverage_reconciliation.csv, platform/addons/coverage/), 12 new requirements M-*, owner decisions OD-16..OD-19, deviations D31-D38. No change to what installs on the server.

## Clean-copy validation (this zip unzipped into an empty folder, session 4)
```
== sha256sum -c
MANIFEST_OK
== run.sh verify
package integrity OK (every file matches MANIFEST.sha256)
== coverage
PASS coverage reconciliation: 204 rows, a=65 b=125 c=14, every row decided with evidence
== run_checks.py
== coverage reconciliation (Item M) ==
PASS coverage reconciliation: 204 rows, a=65 b=125 c=14, every row decided with evidence
Ran 551 tests in 193.437s

OK (skipped=80)
rc=0
== run_addon_checks.py (with AIRX_REAL_TIKA_URL: real Tika 4.2.0)
PASS document_extraction
PASS file_knowledge
PASS model_invoke
PASS scheduler_watchers
PASS every row has exactly one owner (42 modules)
Ran 143 tests in 211.911s

OK (skipped=8)
```
