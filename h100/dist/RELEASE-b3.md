# H100_wired_addons-b3.zip (cumulative: Step 0, Items 0, 1, 2, Stage 19 repair; replaces b2)

- **SHA-256:** `32038f702f68e7d93d17c6dd7be3ca9bdb84445548419380568001511dbfd3e3`
- **Built from:** `baseline/H100_wired.zip` (sha256 2f335b281538e3fcf563720687dbae293c915d13044cd160ad3836a40b26f37d)
- **Item versions:** Item 0 `4c3ce563e4c2...`; Item 1 `4a1e1a811a9b...` (model_invoke 0.1.1); Item 2 `a28955ea2d187d43c7245a3444021f37246080b33568fc0f4842d933affdc9f4` (document_extraction 0.1.0, file_knowledge 0.1.1)
- **Status:** Built, offline-tested, awaiting live verification. Nothing has been run on the server.
- **Changed original files (declared repairs):** `platform/hub/modules_gateway.py`, `remote/s19c-durable-hub.sh`, `remote/s19d-modules.sh`.

## Clean-copy validation (this zip unzipped into an empty folder, session 3)
```
== sha256sum -c
MANIFEST_OK
== run.sh verify
package integrity OK (every file matches MANIFEST.sha256)
== run_checks.py
Ran 551 tests in 210.710s

OK (skipped=80)
== run_addon_checks.py (with AIRX_REAL_TIKA_URL: real Tika 4.2.0)
PASS document_extraction
PASS file_knowledge
PASS model_invoke
PASS scheduler_watchers
PASS every row has exactly one owner (42 modules)
Ran 143 tests in 216.996s
OK (skipped=8)
```
Real-container emulation of the whole chain on a fresh server (same code): all PASS - platform/addons/emulation/README.md.
