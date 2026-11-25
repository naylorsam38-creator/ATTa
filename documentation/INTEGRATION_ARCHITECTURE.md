# Integration architecture (Stage 12 sidecar)

Status: **IMPLEMENTED IN SIDECAR, VERIFIED IN SIDECAR, NOT YET INTEGRATED** into the authoritative application.
Baseline: Stage 01 + Stage 01A at `21104c4` (`origin/claude/ai-app-stage-01-foundation-82jy3a`).

## The chain

```
EXTERNAL INTEGRATION (package dir / operator MCP definition)
  → DISCOVERY            IntegrationManager.discover()        reads integration.yaml only; never imports/executes
  → MANIFEST             manifest.load_integration_manifest()  JSON Schema (additionalProperties:false everywhere)
  → VALIDATION           manifest.semantic_problems()          identity, version, compat, tools, schemas, risk floors,
                                                               permissions ⊆ operator catalog, network, credentials
                                                               (refs only), entry points, files, integrity hashes
  → TRUST CLASSIFICATION operator chooses CORE/TRUSTED/RESTRICTED/UNTRUSTED; bound into the approval payload
  → (OPERATOR APPROVAL)  Ed25519 OperatorAuthorization over approval_binding() (same mechanism as Stage 01A)
  → CAPABILITY REGISTRATION  packaging.build_package() → a normal capability package in <install_root>/packages,
                             discovered by the ONE Stage 01A CapabilityRegistry
  → PERMISSION MAPPING   tool permissions ⊆ permissions_requested ⊆ configuration/permissions.yaml (no wildcards)
  → TOOL REGISTRATION    permanent CCC.TTT numbers (TTT 500–899) merged into the ONE ToolCatalogue and
                         control.tool_identifiers (DB-enforced permanence)
  → (INDEPENDENT VERIFICATION) IntegrationVerifier against the approved snapshot; MCP negotiation for MCP servers
  → ENABLE               separate operator authorisation
  → TASK-SPECIFIC TOOL ALLOCATION   Stage 01A ToolAllocator (≤ max_tools_per_task); AuditingAllocator only audits
  → POLICY CHECK         Stage 01A PolicyEngine (LOW risk + controller-granted permissions only until Stage 02)
  → APPROVAL WHEN REQUIRED  medium/high/critical → denied until Stage 02 human approvals
  → CONTROLLED EXECUTION Stage 01A ExecutionGateway + supervised runtime (no second engine)
  → RESULT VALIDATION    gateway output-schema check + Stage 12 ResultFilter (size, secret redaction, injection flags)
  → AUDIT                integrations.integration_events / installation_events (append-only, DB-enforced)
  → HUB                  outputs reach the model only as `untrusted` context with provenance
```

## Composition, not replacement

`backend/integrations/service.attach()` is the single wiring point:

| Stage 01A component | Stage 12 addition | Can it grant anything? |
|---|---|---|
| `CapabilityRegistry.trust` | `IntegrationTrustGate` — for `integration.*` capabilities checks approved digest/version/permissions/risk and live state (enabled, trust, isolation, connectivity, credentials, dependencies); every other capability is delegated to the Stage 01A operator-signed TrustStore unchanged | No — it can only return *untrusted / disabled / dependency_missing / integrity_failed* |
| `CapabilityRegistry.directories` | `+ <install_root>/packages` | No |
| `ToolCatalogue.extra_numbers` (new optional parameter, default `None`) | Stage 12 permanent numbers from `integrations.tool_numbers` | No — collisions make the catalogue refuse to allocate anything |
| `ExecutionGateway.guards` (new list, default empty) | `ExecutionGuard`: integration must be ENABLED and executable now; version pin; audit | Refuse only |
| `ExecutionGateway.result_filters` (new list, default empty) | `ResultFilter`: bounded size, secret redaction, injection flags, provenance | Keep or reject only |
| `ExecutionGateway.execute` | wrapped by `RejectionAuditor` | Audit only |
| `RuntimeController.allocator` | wrapped by `AuditingAllocator` | Audit only (decisions unchanged) |
| `RuntimeController.plan` / `submit_contract` | wrapped by `PlanPinner`: records `integrations.task_pins` (integration version + package digest per allocated integration tool) when the contract is created | Records only |

Modified Stage 01A files: `backend/orchestration/catalogue.py` (+26 lines), `backend/orchestration/gateway.py`
(+22 lines: two hook lists and their loops, inert when empty), `backend/api/app.py` (+6 lines: router + attach).
No protected/sealed file was modified.

## Why an integration is a capability package

The registry already guarantees: no code import during discovery, isolated probe, operator trust, digest
re-verification by the child that executes, live dependency/health evaluation and `revalidate()` before every
execution. Generating a capability package from the approved manifest reuses all of it. Generated modules
(`_atta_*`) embed the approved configuration as literals so it is covered by the package digest.

## Authority model

* Install, upgrade, enable and trust changes need an operator Ed25519 authorisation bound to the exact payload
  (`approval_binding`: identity, bytes, trust, permissions, every tool's schemas/side effect/risk, hosts, credentials,
  upgrade diff hash). Disable and remove are authority-reducing and need none.
* No API route, method or integration code path lets a model, worker or integration install, enable, trust, approve,
  grant, change risk, change budgets, add itself to a task, alter another integration or touch policy/contract
  configuration. All `/v1` integration routes are default-DENY until Stage 02.
* Trust never grants task permission: a TRUSTED, ENABLED tool still needs allocation to the task, LOW risk and
  controller-granted permissions (or a Stage 02 approval).

## Persistence (migration `0012_stage12_integrations.sql`, schema `integrations`)

integrations, integration_versions, integration_manifests, integration_capabilities, integration_tools,
tool_numbers, integration_permissions, integration_credentials_refs, integration_health, integration_events,
installation_events, mcp_servers, mcp_capabilities, skills, skill_versions, plugins, plugin_versions, task_pins.
History tables are append-only by trigger and by grant; `tool_numbers` and `integration_tools` identities are
immutable.

## Failure isolation

Every install step runs in one try/rollback block; a failure removes staging and installed bytes, restores the
previous version on upgrade, marks tool rows `rolled_back`, re-discovers the registry and reloads the catalogue.
Interrupted installs are rolled back at start (`load_state`). One integration's failure never changes another's
state; MCP servers run per call in supervised processes; there is no retry loop and no fallback provider.

## Adapters to the other parallel stages (final package)

`backend/integrations/adapters.py` reads — never imports or modifies — the published interfaces of the other workers:

| Source | Interface used | Stage 12 behaviour |
|---|---|---|
| Worker A, Stage 01B (`0ec939c`) | `CapabilityRuntime(network=, worker_user=, isolation_required=)`, `settings.deployment.profile`, gateway's per-contract `network="host"` | `RuntimeIsolation` = isolation AVAILABLE only when the runtime REQUIRES it (RESTRICTED / `ISOLATED_SANDBOX` integrations then run); `AIR_GAPPED` profile → network integrations OFFLINE_UNAVAILABLE |
| Worker 3, Stage 11 (`bb1a60b`) | `NetworkModeProvider.current_mode()`, `ATTA_NETWORK_MODE` | same signal for research and integrations; unknown values fail closed |
| All | — | `ConnectivitySources`: the **most restrictive** of provider / `ATTA_NETWORK_MODE` / 01B profile / `integrations.yaml`, evaluated live on every check |
| Worker 2, Stage 04 | not published | `mount_integrations(app, authorize)` is idempotent; contracts in `packages/shared_types/integrations.contracts.generated.json` |

`attach()` defaults to these adapters, so the same code runs on the 01A baseline (isolation honestly UNAVAILABLE),
on 01B, on 11 and on their merge. Verified by installing into each real tree (`scripts/stage12_trial.py`).
One registry, one catalogue, one gateway: `attach()` only composes around `controller.registry`,
`controller.catalogue`, `controller.gateway` and `controller.allocator` (S12-T73).
