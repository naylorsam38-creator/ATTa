# evidence

Machine-written verification evidence. Do not edit by hand — the build controller detects altered files.

* `build_controller/ledger.jsonl` — hash-chained record of task begins, test results, task results, stage gates, checkpoints
* `build_controller/runs/<run-id>/<test-id>.json` — command, cwd, UTC start/finish, exit code, stdout/stderr, output digest
* `build_controller/checkpoints.json` — verified recovery checkpoints
* `stage_01/acceptance/<test-id>.json` — structured observations written by each acceptance test
