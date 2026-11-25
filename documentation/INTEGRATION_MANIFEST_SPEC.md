# Integration manifest specification — `integration.yaml`, manifest_version 1

Schema: `backend/integrations/integration.schema.json` (Draft 2020-12). Validator: `backend/integrations/manifest.py`.
**Every object has `additionalProperties: false`** — an unknown field is a validation error, so no unrecognised
field can ever grant authority (e.g. `trust_class`, `grant_admin`, `budget_override` are rejected).

## Top level (all required unless marked optional)

| Field | Meaning |
|---|---|
| `manifest_version` | `1` |
| `integration_id` | `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`, ≤ 80 chars. Registry capability id = `integration.<integration_id>` |
| `integration_type` | `CONNECTOR`, `MCP_SERVER`, `SKILL`, `PLUGIN`, `CUSTOM_PROVIDER` |
| `name`, `version` (semver), `provider.name`, `description` | identity; descriptions may not contain instruction-like text (they reach the model's trusted tool list) |
| `requirement` (optional) | CUSTOM_PROVIDER only: `CAP-21/22/24/40`; others derive it from the type |
| `compatibility.integration_api` | must be `"1"` |
| `execution_mode` | `SUPERVISED_PROCESS`, `ISOLATED_SANDBOX`, `MCP_STDIO`, `MCP_HTTP`, `DECLARATIVE` (must match the type) |
| `capabilities[]` | logical capabilities `{id, description}`; tools may reference one |
| `tools[]` | see below (SKILL: none) |
| `permissions_requested[]` | must exist in `configuration/permissions.yaml`; no wildcards |
| `network_requirements.hosts[]` | every host the integration may contact (MCP HTTP url host must be listed) |
| `credential_requirements[]` | `{name, kind, description, required, scopes?}` — declarations only; ATTa derives `credref://<integration_id>/<name>` |
| `configuration_schema` | JSON Schema (type object) for operator configuration |
| `dependencies` | `{integrations[], python[]}` |
| `health.handler` (optional) | `module:function` inside the package |
| `integrity.files` | `{relative path: sha256}` for EVERY file except integration.yaml — required when the package contains code |
| `mcp` | MCP_SERVER only: `transport` (`stdio`/`streamable_http`), `command` (argv) or `url`, `protocol_versions`, timeouts |
| `skill` | SKILL only: `skill_id`, `instructions` (file), `templates`, `schemas`, `tool_references`, `resource_references`, `workflows`, `validation_rules`, `examples` |
| `plugin` | PLUGIN only: `contributes.{ui_extensions, workflows, schemas, providers}` (inert metadata) |

## Tool

`name`, `description`, `side_effect`, `operations[]`, `task_types[]`, `input_schema`, `output_schema`,
`error_schema` (all JSON Schema; input/output/error type object; local `#/` refs only; ≤ 64 KiB), `permissions[]`
(⊆ permissions_requested), `risk`, `timeout_s` (≤ 900), `cancellable`, `idempotency`; optional `handler` (required
except for MCP tools), `mcp_name`, `capability`, `connector_operation` (required for connectors), `resource_scope`
`{resource_types, path_arguments, url_arguments}`, `credentials[]` (declared names), `max_result_bytes`.

### Side effects and minimum risk (a tool may declare more, never less)

| side_effect | minimum risk |
|---|---|
| READ_ONLY | low |
| REVERSIBLE_WRITE | medium |
| EXTERNAL_COMMUNICATION, DESTRUCTIVE, ADMINISTRATIVE, OTHER_HIGH_RISK | high |
| FINANCIAL, SECURITY_SENSITIVE | critical |

### Connector operations → minimum side effect

READ, SEARCH → READ_ONLY · CREATE, UPDATE → REVERSIBLE_WRITE · DELETE → DESTRUCTIVE · SEND → EXTERNAL_COMMUNICATION ·
EXECUTE → OTHER_HIGH_RISK · ADMINISTRATIVE → ADMINISTRATIVE. `operations` must include the lower-case operation.

## Rejected outright

Unknown fields; raw credentials anywhere (key patterns, token formats, private keys, credentials in URLs);
instruction-like descriptions; wildcard or unknown permissions; under-declared risk; shells/launchers, `-c`/`-m`,
or non-allowlisted absolute executables in MCP commands; files named `capability.yaml` or `_atta_*`; symlinks;
files missing from or extra to integrity metadata; hash mismatches; non-local `$ref`; incompatible API.

## Approval payload

`approval_binding()` = SHA-256 of identity, version, manifest digest, package digest, trust class, permissions,
network hosts, credential names, every tool's side effect/risk/permissions/schema hash and, for upgrades, the
upgrade-report hash. The operator signs `(integration.install|integration.upgrade, <id>@<version>, binding)`.
`python -m backend.integrations approval <dir>` prints it; `... sign --key <operator.pem> ...` signs it.
