# v121.3 record-driven diagnostic

This replaces the diagnostic *analysis contract* with a read-only, record-driven
procedure. It does not replace `app_runner.diagnose()`: that function is the
live operational failure-rule matcher and remains part of the runner.

The record diagnostic consumes completed failure records and related evidence.
It never repairs, reruns, deploys, or rewrites an input artifact.

## Four-part finding

Each finding contains:

- `classification`: observable condition supported by evidence
- `evidence`: auditable artifact path, fingerprint, field and observation
- `allowed_action`: one of `AUTO`, `RETRY_ONLY`, `APPROVAL_REQUIRED`, `ESCALATE`, `NO_ACTION`
- `root_cause`: independently assessed; `UNKNOWN` unless the record itself proves causation

Actions are resolved by the versioned policy in `04-deployment/record_diagnostic.py`.
No LLM, error string, or heuristic can authorize an action outside that policy.

## Real 22-record regression

The supplied v121.3 run is represented by
`tests/fixtures/v121_3_22_failure_manifest.json`. It binds the expected 22 apps
to fleet `fleet-20260929T061331Z-9b37b3` and revision
`code:e46676963529e1afb54f957b`.

The manifest is **not** a replacement for the original failure JSON. The real
records must be present at the supplied `--records-root`.

Run:

```bash
PYTHONPATH=04-deployment python -m pytest -q tests/test_v121_3_record_diagnostic.py
```

To run the real 22-record regression:

```bash
PYTHONPATH=04-deployment ATTA_RECORD_DIAGNOSTIC_ROOT=/srv/app-builder/state/runner/failures   python -m pytest -q tests/test_v121_3_record_diagnostic.py -k real_22
```

Or generate an auditable report without modifying the input tree:

```bash
python 04-deployment/run_record_diagnostic.py   --records-root /srv/app-builder/state/runner/failures   --output /srv/app-builder/state/runner/diagnostic-record-report.json
```

The command exits non-zero if any of the 22 expected records is missing.

## Remediation boundary

This diagnostic never executes the returned action. Any later executor must
require an explicit remediation ID and matching policy rule, record approval,
deployment identity and before/after evidence, bound retries, and stop if
identity or verification changes.
