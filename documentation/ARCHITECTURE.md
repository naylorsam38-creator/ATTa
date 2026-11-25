# Architecture (Stage 01)

> Stage 01 is the foundation. It is **not** a production-ready application: no conversation, authentication or
> AI capability exists yet. What exists is real and tested: the service manager, the capability registry, the
> policy boundary, the build controller, the `/v1` API with measured health, configuration, installers and
> client shells.

## Component map

```
 apps/web (Next.js 16)   apps/desktop (Electron 45 → web UI)   apps/mobile (React Native 0.88)
            \                     |                                 /
             └──── packages/api_client ── packages/shared_types ───┘   packages/design_system
                                   │  HTTP /v1  (X-Correlation-ID, error envelope)
                                   ▼
 ┌────────────────────────── backend/api (FastAPI) ──────────────────────────┐
 │ middleware: correlation id · request id · structured JSON log · headers   │
 │ authentication (S01: everyone ANONYMOUS) → policy_engine.evaluate()       │
 │ /v1/health/live · /v1/health/ready · /v1/system/version   (public)        │
 │ /v1/capabilities · /v1/capabilities/{id}/executions        (DENY in S01)  │
 └───────────────┬───────────────────────────────┬───────────────────────────┘
                 │                               │
    backend/capability_registry          backend/service_manager/probes
    (discover · validate · dedupe ·      (postgres+pgvector · redis · s3 ·
     resolve deps · health · execute     tcp · http) — shared with readiness
     via policy)                                 │
                 │                               ▼
        capabilities/*/capability.yaml   PostgreSQL 16 + pgvector · Redis · S3 · Temporal · model endpoints

 backend/service_manager  — supervises redis (managed) and api (managed); probes postgres/s3/temporal (external)
 backend/verification     — build controller + evidence runner (dev-time, independent of the runtime)
 backend/model_gateway    — model endpoint inventory + health (LiteLLM routing from Stage 03)
 backend/orchestration    — LangGraph/Temporal engine (Stage 03+); the ONE intelligence engine
 backend/task_manager     — task records + permitted state transitions + execution events
 backend/contracts        — ids, UTC time, context, errors, pagination, idempotency, correlation, logging,
                            health, availability, task states, events, evidence
 backend/settings         — layered, validated configuration
 backend/database         — checksummed migrations (database/migrations) + RLS policies (database/policies)
```

## Startup flow (START.bat / start.sh → `python -m backend.service_manager start`)

1. Load and validate configuration (`backend/settings`) — invalid configuration stops here (exit 4).
2. Validate required dependencies: Python version, packages, binaries of managed essential services, writable
   runtime directory, acyclic service graph.
3. Detect services: probe every configured service and model endpoint.
4. Resolve startup order (topological over `depends_on`): `postgres → redis → api → object_storage → temporal`.
5. Start: external services are waited for (essential) or probed once (optional); managed services are spawned
   in their own process group with logs in `runtime/logs/<name>.log`. A managed port that already answers before
   spawn is a failure (no adopting foreign processes).
6. Health: each service must pass its probe within `startup_timeout_s`.
7–10. Discover capabilities in every `capabilities.directories` entry, validate manifests, register valid ones,
   report the rest with reasons (`invalid`, `rejected_duplicate`, `dependency_missing`, `unhealthy`, `disabled`).
11. Monitor every `monitor_interval_s`; on crash or repeated failed probes restart managed services with
   exponential backoff, at most `max_restarts` within `restart_window_s`; beyond that the service is `failed`.
12. Readiness: manager state `ready`/`degraded` only when every essential service is healthy; otherwise
   `not_ready` (recoverable) or `failed`. The API's own `/v1/health/ready` probes dependencies independently.
13. STOP writes a stop request; the manager stops services in reverse order (SIGTERM, then SIGKILL after grace),
   records `stopped`, removes its PID file. STOP falls back to terminating recorded PIDs if the manager is gone.

## Request flow for a capability execution (contract fixed now, allowed from Stage 02)

`POST /v1/capabilities/{id}/executions` → authenticate → **policy.evaluate(capability.execute, id, risk,
permissions)** → idempotency lookup → registry: must be `available` → input JSON-Schema validation → handler with
deadline (min(manifest timeout, request)) → output JSON-Schema validation → verification handler → response.
At Stage 01 the policy step denies every caller, so nothing executes through the API.

## Security boundaries

* Loopback binding; public bind is a configuration error until Stage 02 (`api.public_bind_allowed`).
* Default DENY for every protected operation; permissions on a principal can only come from the authentication
  service (Stage 02) — capabilities, agents and workers can never grant.
* Secrets only from environment / `configuration/local.env` (git-ignored); tracked YAML containing a secret is
  rejected by the loader; logs redact secret-looking keys.
* PostgreSQL: application role without SUPERUSER/BYPASSRLS; FORCE RLS with no policies (deny) on tenant tables.
* Capability handlers load only from inside their own directory; schemas validate input and output.

## Relationship to the existing ATTa app builder

The v121.5 app builder (gateway, pipeline, Coolify hand-off) is untouched and still runs with `bash run`.
It is the intended engine for CAP-18 (application creation), integrated as a capability in Stage 11.
