## Additional evidence and limits

* `evidence/stage_01a/acceptance/S01A-T*.json` — structured observations written by each acceptance test during the
  gate run (contracts, rejections with rule names, budgets, timings, recovery actions, traceability chains).
* S01A-T48 re-ran every Stage 01 acceptance test: the ten still applicable passed (T16 via S01A-T49); the five
  superseded tests (S01-T06, T07, T08, T09, T13) failed and S01A-T48 proved, by re-running them with trust satisfied
  through a test-only plugin, that each fails only because of a deliberate Stage 01A correction (untrusted packages
  refused, no in-process handlers, gateway-only execution). See DECISIONS D-029 and `S01A-T48.json`.
* Proposals in these tests are explicit test data (`source = explicit-test` in `control.proposals`). **No LLM
  inference was performed or is claimed** (Stage 03).
* The verifier ran as root through the standalone entry point `backend/verification/verify.py` (no worker-editable
  modules loaded), with an Ed25519-signed seal and OS protection verified for the worker account `atta-builder`.
  The keys were generated in this session (BLOCKERS B-009) and this session's agent ran as root (B-016).
* Code under test necessarily executes inside the test processes; OS protection prevents the worker from editing
  criteria, verifier, ledger, interpreter and libraries, but a deliberately malicious implementation could still try
  to subvert a test process. Independent review of implementation changes remains required.
* Not covered: Windows `.bat` execution, Windows process-tree control, network isolation of handlers, Docker,
  Terraform validate, native mobile builds (BLOCKERS).

## Gate history (append-only ledger)

* `run_01M88GJKMJR17ATCGEZQ6JDW8X` — **FAILED**: S01A-T48 failed because the gate leaked `ATTA_BUILD_SIGNING_KEY_FILE`
  into test subprocesses (S01-T12's temporary repositories were signed without a matching public key and correctly
  refused). CHECKPOINT-B was BLOCKED and no checkpoint was written — the controller prevented progression.
  Fixed in `5662dd2` (runner scrubs `ATTA_BUILD_*` from test environments); seal re-signed.
* `run_01M88H4W3NJ3NSPWSHPV29RR48` — **PASSED** (this report).
