# Configuration

Layers (later wins): `configuration/base.yaml` → `configuration/environments/<ATTA_ENV>.yaml` →
`configuration/local.yaml` (untracked) → `configuration/local.env` (untracked) and process environment.

* Any setting: `ATTA__<SECTION>__<KEY>=value` (YAML-typed), e.g. `ATTA__API__PORT=8801`,
  `ATTA__CAPABILITIES__DIRECTORIES=["capabilities","D:/extra-caps"]`.
* Secrets — accepted **only** from the environment/local.env; a tracked YAML layer that sets one is rejected:
  `ATTA_DATABASE_PASSWORD`, `ATTA_REDIS_PASSWORD`, `ATTA_S3_ACCESS_KEY_ID`, `ATTA_S3_SECRET_ACCESS_KEY`.
  Model provider keys are referenced by variable *name* (`models.endpoints[].api_key_env`).
* Validate without starting anything: `python -m backend.service_manager validate`.

| Section | Keys |
|---|---|
| `environment` | development · test · staging · production (`ATTA_ENV`) |
| `api` | host (loopback), port, public_bind_allowed (false until Stage 02), request_timeout_s |
| `database` | host, port, name, user, password*, sslmode (production: require+), connect_timeout_s, require_pgvector |
| `redis` | host, port (6380), db, password*, socket_timeout_s |
| `object_storage` | endpoint_url, bucket, region, access_key_id*, secret_access_key* |
| `models` | storage_paths, endpoints[{name, kind, base_url, health_path, enabled, api_key_env}], default_endpoint, allow_model_downloads (false) |
| `capabilities` | directories, healthcheck_timeout_s |
| `workers` | max_concurrency, cpu_limit, memory_limit_mb |
| `deadlines` | default_execution_timeout_s, max_execution_timeout_s |
| `logging` | level, format (json/text), directory |
| `security` | default_decision (deny — the only accepted value), cors_origins, allow_paid_infrastructure (false) |
| `runtime` | directory, monitor_interval_s |
| `services.<name>` | essential, mode (managed/external/disabled), command, requires_binary, depends_on, health{type,…}, startup_timeout_s, max_restarts, restart_window_s, restart_backoff_s, env |

`*` = secret. Environments: `development` (text logs), `test` (ports 8810/6390, `runtime/test`), `production`
(TLS to PostgreSQL, object storage essential, password mandatory).
