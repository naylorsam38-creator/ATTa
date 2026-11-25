# BLOCKERS

Unresolved dependencies, recorded as **BLOCKED**, with what unblocks them. Nothing below is reported as working.
Updated at Stage 01A.

| ID | Item | Status | Why | Unblocked by |
|---|---|---|---|---|
| B-001 | Authenticated users, tenants, persistent permissions, human approvals (CAP-35, CAP-39 completion) | BLOCKED — Stage 02 by design | Stage 01A keeps every user-facing protected operation DENIED; only the attested controller can execute LOW-risk tools | Stage 02 connects identities, tenant isolation (RLS policies on the `control` schema: `tenant_id` columns exist), credential protection and approvals to the Stage 01A controller |
| B-002 | Windows execution of the `.bat` entry points | BLOCKED — not executed | Linux build environment | Run INSTALL/START/STOP/HEALTHCHECK/RECOVERY on Windows 10/11 and record output |
| B-003 | Real model inference (Llama, Qwen, LiteLLM routing) | BLOCKED — Stage 03 | Only the adapter interface exists; Stage 01A tests use explicit test proposals (labelled `explicit-test`), never claimed as model output | Stage 03 adapters implementing `backend/orchestration/models.ModelAdapter` |
| B-004 | `terraform validate` / provider lock | BLOCKED | registry.terraform.io refused by the proxy (403) | `terraform init && validate` on an operator machine |
| B-005 | Mobile native builds | BLOCKED | No Android SDK/Xcode (npm ci, type check and Metro bundle are verified) | RN 0.88 toolchain |
| B-006 | Running the Docker Compose stack / building `api.Dockerfile` | BLOCKED | No Docker daemon (compose config validates) | A Docker host |
| B-007 | S3-compatible object storage | BLOCKED (optional) | Not available; readiness reports it unhealthy/optional | MinIO profile or S3 credentials |
| B-008 | Temporal | BLOCKED (optional) | Not running; durable execution is Stage 13 | Temporal server + Stage 13 |
| B-009 | Independently controlled signing keys | PARTIAL | The build seal, capability trust store and controller authorisations are now Ed25519-signed and the private keys sit in root-only directories outside the repository (`/etc/atta-verifier`, `/etc/atta-operator`). But they were generated in this session by the same agent that wrote the code | The human operator regenerates all three keys on their own machine (`capability_registry keygen`, `protect_verifier.sh`), re-trusts packages, re-seals, and keeps the private keys from the implementation account |
| B-010 | Electron runtime launch | BLOCKED | Binary download skipped, no display | `INSTALL --with-desktop` on a desktop |
| B-011 | Local/GPU model endpoints | BLOCKED (optional) | Not installed; no models downloaded | Operator installs a runtime + models (e.g. D:\models) |
| B-012 | PostgreSQL superuser bootstrap | Manual step | pgvector + the two roles need a superuser (`database/bootstrap.sql`) | DBA runs it once; Docker path automatic |
| B-013 | **Stage 02 corrections not part of Stage 01A** | BLOCKED — Stage 02 | Correction 5 (approvals bound to authenticated principal, tenant, operation, capability version, canonical arguments, resource, execution id) needs Stage 02 identities — the binding fields already exist in policy requests (contract hash, tool, arguments hash, execution id). Correction 9 found a legacy bypass: `APP_BUILDER_AUTH_DISABLED=1` makes the legacy ATTa gateway treat every request as an admin; it is a local-test hatch not set by any deploy path, but it must be fenced (refuse when proxied / non-loopback) — that edits protected legacy code and is scheduled for Stage 02. Correction 6's API-level persistent idempotency for authenticated callers is Stage 02 (controller executions are already persistent and idempotent) | Stage 02 |
| B-014 | Network isolation of handlers | BLOCKED | Handlers run as separate processes with rlimits and a clean environment but share the host network namespace; network destinations are enforced structurally by the drift guard only | Sandbox stage (S11): namespaces/containers |
| B-015 | Windows process-tree control | BLOCKED | Subreaper-based tree kill is Linux-only; on Windows the runtime falls back to process-group termination without Job Objects (untested) | Job Object implementation + Windows test run |
| B-016a | Tests run with verifier privileges in this environment | LIMITATION | The stage gate executes acceptance tests (and therefore the code under test) as root. Verifier variables are now scrubbed from test environments (found by the first S01A gate run, which failed), but a root test process could still read the signing key | Run gate test commands as a dedicated unprivileged test account (needs DB credentials and runtime directories provisioned for it) |
| B-016 | OS protection against THIS build agent | NOT ENFORCEABLE HERE | The protection is applied and verified against the worker account `atta-builder` (S01A-T51), but this session's agent runs as root (the verifier identity) and is therefore not constrained by it | Run future implementation work as `atta-builder` without sudo |
| B-017 | Five Stage 01 acceptance tests superseded | RECORDED | S01-T06/T07/T08/T09/T13 encode behaviour removed by corrections 1/4 and gateway-only execution; files unchanged; causes proven by S01A-T48 (DECISIONS D-029) | Not a defect — stricter replacements pass |
| B-018 | Semantic relevance of proposals | BY DESIGN | The drift guard is structural; it does not judge meaning. Criteria needing semantic judgement use `human_review`, which blocks completion until a human accepts (UI in Stage 04) | Stage 04 review UI |

## Stage 12 sidecar blockers (connectors, MCP, skills, plugins)

| ID | Item | Status | Why | Unblocked by |
|---|---|---|---|---|
| S12-B01 | OS isolation for RESTRICTED/UNTRUSTED plugins and `ISOLATED_SANDBOX` | BLOCKED — Stage 01B | No sandbox provider exists; `NoOsSandbox` reports unavailable and such integrations are BLOCKED (never claimed isolated). Contract verified with a labelled test double (S12-T14) | Stage 01B `IsolationProvider` implementation |
| S12-B02 | Credential resolution for credential-dependent connectors/MCP servers | BLOCKED — Stage 02 | Only references exist; `UnavailableCredentialBroker` → AUTH_REQUIRED (S12-T36) | Stage 02 credential store implementing `CredentialBroker` |
| S12-B03 | Human approvals for medium/high/critical integration tools; authenticated `/v1/integrations` routes; identity-bound audit actors | BLOCKED — Stage 02 | Policy engine denies all protected operations and medium+ risk until Stage 02 (S12-T32, T60) | Stage 02 policy rules for `integration.*` operations + approval engine |
| S12-B04 | Integration permissions beyond the existing catalog | PROPOSED | `configuration/permissions.yaml` is protected; proposals in `configuration/integrations/permissions.proposed.yaml` are not loaded | Operator adds them to permissions.yaml (and grants) and re-seals |
| S12-B05 | Live connectivity contract | BLOCKED — Stage 01B | Mode comes from `configuration/integrations.yaml` (`ONLINE/RESTRICTED/AIR_GAPPED`) | Stage 01B deployment profile |
| S12-B06 | Real Qwen/Hub use of integration tools | BLOCKED — Stage 03 | No model adapter yet; tests use explicit test proposals and `atta-script` | Stage 03 adapters |
| S12-B07 | Build-controller stage gate for Stage 12 | NOT APPLICABLE IN SIDECAR | Roadmap numbering differs (DECISIONS D-S12-11); stage definitions are protected | Operator reconciles roadmap, writes + seals `S12.yaml`, runs the gate post-merge |
| S12-B08 | S01A-T51 (OS protection of the verifier) in this container | ENVIRONMENT | `scripts/protect_verifier.sh` would regenerate the sealed verifier public key here; not applied. Fails identically on the untouched baseline | Run on a host provisioned with `protect_verifier.sh` |
| S12-B09 | TRUSTED integrations can read files readable by the service account | LIMITATION (documented) | Supervised runtime has no filesystem namespace (see B-014) | Stage 01B sandbox; keep untrusted code RESTRICTED |
