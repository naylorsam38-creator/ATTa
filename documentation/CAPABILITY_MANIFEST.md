# Capability manifests

A capability is a directory containing `capability.yaml` and its handler module(s). Schema:
`backend/capability_registry/manifest.schema.json` (also used by `@atta/capability_sdk`).

```yaml
manifest_version: 1
id: ops.system_status            # unique, dotted lowercase
version: 1.0.0                   # semver
description: What it does (≥ 10 chars)
requirement: CAP-40              # optional link to the requirement register
handlers:                        # module:function, resolved inside this directory only
  execute: handler:execute       # execute(payload: dict, context: RequestContext) -> dict
  healthcheck: handler:healthcheck   # healthcheck() -> bool | {"healthy": bool, ...}
  verify: handler:verify         # verify(payload, output) -> bool | {"verified": bool, ...}
input_schema: {...}              # JSON Schema 2020-12, validated before execute
output_schema: {...}             # validated after execute
permissions: [ops.system.read]   # REQUIRED permissions (declarations, never grants)
risk: low                        # low | medium | high | critical
dependencies: {capabilities: [], services: [redis], python: [psutil]}
timeout_s: 10                    # ≤ deadlines.max_execution_timeout_s
enabled: true
```

Create one: `python scripts/new_capability.py area.name --requirement CAP-NN` (created disabled).

Availability states: `available` (the only executable state) · `disabled` · `invalid` (schema, handler import,
signature or schema errors) · `rejected_duplicate` (every manifest sharing an id) · `dependency_missing` ·
`unhealthy` (health check false, raised or timed out). Every execution additionally passes the policy engine.
