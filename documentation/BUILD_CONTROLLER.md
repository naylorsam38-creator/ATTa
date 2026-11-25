# Build controller

`python -m backend.verification <command>` — deterministic, independent of whoever writes the code.

| Command | Effect |
|---|---|
| `plan S01` / `status S01` | tasks in dependency order with ledger status; stage gate status |
| `begin S01 S01-TASK-04` | refuses unless criteria intact, ledger intact, previous stage gate passed and dependencies PASSED; records the base commit |
| `verify-task S01 S01-TASK-04` | checks changed files against the task's permitted paths and the stage's protected paths, runs the task's acceptance tests, records evidence |
| `verify-stage S01` | the gate: file authorisation over the whole stage diff, every task in order (a task whose dependency failed is BLOCKED and not run), checkpoint on pass at S01/S04/S08/… |
| `integrity` | verifies sealed criteria and the ledger hash chain + evidence digests |
| `seal --operator NAME` | operator only: re-seal protected criteria (HMAC-signed with `ATTA_BUILD_OPERATOR_KEY_FILE`, which must be outside the repo) |
| `checkpoints` / `recover [--yes]` | list checkpoints / restore the last compatible one (see OPERATIONS.md) |

Files: `configuration/build/roadmap.yaml`, `configuration/build/stages/SNN.yaml` (tasks: id, objective,
depends_on, permitted_paths, required_changes, acceptance_tests[{id, command, expected_exit_code,
expected_result, timeout_s}], rollback, verification_evidence), `configuration/build/protected.lock.json`,
`evidence/build_controller/{ledger.jsonl, runs/<run>/<test>.json, checkpoints.json}`.

There is deliberately no command to approve, skip or override a failed test. See DECISIONS D-018 and
BLOCKERS B-009 for the trust model and its limits.
