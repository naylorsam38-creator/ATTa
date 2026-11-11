# H100_wired_addons-b2.zip (cumulative: Step 0, Items 0 and 1, Stage 19 repair; replaces b1)

- **SHA-256:** `16824e63721e936cf058c2d21706c35d75fe699078ffc3aff7e7dfa0f10ce6c2`
- **Built from:** `baseline/H100_wired.zip` (sha256 2f335b281538e3fcf563720687dbae293c915d13044cd160ad3836a40b26f37d)
- **Item versions:** Item 0 `4c3ce563e4c26853f053d4637381e7bbb7859894c00dcb6d9eed87e642730458`; Item 1 `4a1e1a811a9bbdc0d87998d4cc31a35e58e46eb72d713e039dad54595b6d6229` (model_invoke 0.1.1)
- **Status:** Built, offline-tested, awaiting live verification. Nothing has been run on the server.
- **Changed original files (declared repairs):** `platform/hub/modules_gateway.py`, `remote/s19c-durable-hub.sh`, `remote/s19d-modules.sh` (one line each in the two scripts; DEVIATIONS D22, owner veto OD-15). All other stages, run.sh, lib/common.sh, lib/ssm.sh byte-identical (checked by the packaging script).

## Clean-copy validation (this zip unzipped into an empty folder, session 2)
```
== sha256sum -c
MANIFEST_OK
== run.sh verify
package integrity OK (every file matches MANIFEST.sha256)
== run_checks.py
Ran 551 tests in 208.036s

OK (skipped=80)
== run_addon_checks.py
PASS model_invoke
PASS scheduler_watchers
PASS every row has exactly one owner (41 modules)
Ran 96 tests in 173.728s
OK (skipped=4)
```
Real-container emulation of the install sequence (before packaging, same code): all PASS - platform/addons/emulation/README.md.
Run by the building session, not an independent agent.
