# Connector architecture (Stage 12 sidecar)

A connector is an integration of type `CONNECTOR` whose package contains a **provider adapter** (ordinary Python
functions, one per operation) and whose manifest declares, per tool:

* `connector_operation` — READ, SEARCH, CREATE, UPDATE, DELETE, SEND, EXECUTE, ADMINISTRATIVE;
* `side_effect` (≥ the operation's minimum) and `risk` (≥ the side effect's minimum);
* `permissions` from the operator catalog; `resource_scope` (resource types, path arguments, URL arguments);
* `credentials` it needs (names declared in `credential_requirements`), `network_requirements.hosts`;
* typed `input_schema`, `output_schema`, `error_schema`, `timeout_s`, `cancellable`, `idempotency`.

There is no vendor code in Stage 12: email, calendar, storage, repositories, business apps, data services and
communications platforms are all provider adapters packaged this way. "Connected" (installed + enabled) is never
permission to perform every operation: each operation is a separate numbered tool, allocated per task by the
Stage 01A allocator and evaluated per call by the policy engine. Writes (medium+) need Stage 02 approvals; until
then they are refused (S12-T32). Path and URL arguments are confined by the drift guard to the task's permitted
filesystem paths and network destinations.

## Credentials

Manifests carry no credential values (validation rejects them). ATTa derives `credref://<integration_id>/<name>`
references (`integrations.integration_credentials_refs`, DB-checked format). A `CredentialBroker` resolves only
references declared by the approved manifest and owned by the same integration and offers no enumeration. The
sidecar ships `UnavailableCredentialBroker`: any connector with a required credential reports **AUTH_REQUIRED** and
is not executable until Stage 02 provides the broker (BLOCKERS S12-B02).

## Offline

Connectors declaring non-loopback hosts report **OFFLINE_UNAVAILABLE** when the deployment is AIR_GAPPED (or the host
is not allowlisted when RESTRICTED); local connectors keep working; ATTa start never depends on them (S12-T35).

## Fixtures (tests only)

`valid_connector` (local JSON notes: search/read low, create medium, send high), `invalid_connector` (DELETE
classified READ_ONLY + embedded token: rejected), `network_connector` (remote host + required credential).
