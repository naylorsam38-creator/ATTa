# H100_wired_addons-b1.zip (batch 1: Step 0, Item 0, Item 1)

- **SHA-256:** `8ef7795e26c019ebd2c683de42dfb2913466f32aaa87fa1a60e08b0eaed4a678`
- **Built from:** `baseline/H100_wired.zip` (sha256 2f335b281538e3fcf563720687dbae293c915d13044cd160ad3836a40b26f37d)
- **Item versions:** Item 0 `4c3ce563e4c26853f053d4637381e7bbb7859894c00dcb6d9eed87e642730458`; Item 1 `a0e637591aa4b6743b2954913e0e7250fe7f256642a30bb1649c6e75860e08ad`
- **Status:** Built, offline-tested, awaiting live verification. Nothing has been run on the server.
- **Changed original files:** one declared repair, `platform/hub/modules_gateway.py`. All stages, run.sh, lib/common.sh, lib/ssm.sh byte-identical (checked by the packaging script).

## Clean-copy validation (the delivered zip unzipped into an empty folder, 2026-11-11, this session)
```
== sha256sum -c
MANIFEST_OK
== run.sh verify
package integrity OK (every file matches MANIFEST.sha256)
== run_checks.py
Ran 551 tests in 188.606s

OK (skipped=80)
== run_addon_checks.py
PASS model_invoke
PASS scheduler_watchers
PASS every row has exactly one owner (41 modules)
Ran 93 tests in 150.047s
OK (skipped=4)
```
Environment: Python 3.13 venv with the hub's pinned requirements (fastapi 0.115.0, uvicorn 0.30.6, mcp 1.9.4,
pydantic 2.10.6) plus PyYAML; jq 1.x; bash. Run by the building session, not an independent agent.

Install: `airexploit/INSTALL_RUNBOOK.md` inside the zip. Owner checks: `airexploit/LIVE_ACCEPTANCE_CHECKLIST.md`.
