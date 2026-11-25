# Isolation model (Stage 01B)

Four environments never share mutable state:

| | BUILD | TEST | QUALIFICATION | PRODUCTION |
|---|---|---|---|---|
| Purpose | build controller gates (`backend/verification`) | developer/CI tests | `python -m backend.deployment qualify` and the installer's QUALIFY step | the activated installation |
| Database | none of its own | test env DB / per-test disposable clusters | **disposable PostgreSQL cluster** per run (`initdb` as `postgres`, random port, random superuser/migration/runtime passwords, migrated by the migration role, destroyed afterwards) | operator's cluster |
| Redis | — | per-test port | own port (`ATTA__REDIS__PORT`) → separate process and data | configured port |
| Object storage | — | — | own bucket name `atta-qual-…` | configured bucket |
| Catalogue namespace | — | per-test namespace | qualification id | `default` |
| Files | `evidence/` (root-owned ledger) | pytest tmp dirs, `runtime/test/` | `…/qualification/<id>/` (runtime, logs, deployment dir, evidence) | `runtime/`, `runtime/deployment/` |
| Ports | — | free ports | API and Redis on free ports | configured |
| Credentials | verifier keys outside the repo | test-only | generated per run, never written to reports | `configuration/local.env` (git-ignored) |
| Network | host | host | host, or **new kernel network namespace** for AIR_GAPPED | host |

The isolated endpoints are passed to children as `ATTA__…` variables **and** written to the run's own
`deployment/discovered.yaml`, so helpers that reset `ATTA__` overrides still resolve to isolated resources, never to
production. S01B-T24 fingerprints production configuration, deployment records and every production table
(row count + content hash) before and after a qualification run and requires them to be identical.

## Air-gap (AIR_GAPPED)

`unshare -n` creates an empty network namespace; `netns_exec.py` raises the loopback interface with an ioctl (no
iproute2 needed). Disposable PostgreSQL, Redis, the API and the web server run on loopback inside it. `Q-NETWORK-DENIED`
proves 1.1.1.1:443, 8.8.8.8:53 and 9.9.9.9:443 are unreachable; the Python audit hook records any attempt. Without
namespace support the whole suite is BLOCKED.

## Tool workers (Stage 01A runtime + Stage 01B OS isolation)

API → `supervisor.py` (own session, child subreaper, cgroups) → `child.py`:

| Control | Linux (VERIFIED, S01B-T42) | Windows |
|---|---|---|
| Process tree termination | subreaper + kill of all descendants, incl. detached ones | Job Object `KILL_ON_JOB_CLOSE` — implemented, untested (BLOCKED) |
| Memory / process limits | cgroup v2 or v1 (`memory`, `pids`) + rlimits | NOT implemented on Windows (the Job Object sets only kill-on-close) |
| Network | empty namespace when `deployment.worker_network: none` (H100 and AIR_GAPPED profiles); a task whose contract permits network destinations runs with host networking (destinations still enforced by the drift guard) | firewall rules NOT implemented (BLOCKED) |
| Identity | drop to an unprivileged account (`deployment.worker_user`, e.g. `atta-builder`) | restricted tokens / ACLs NOT implemented (BLOCKED) |
| GPU | `CUDA_VISIBLE_DEVICES=""` unless explicitly granted | same variable |
| Code integrity | package bytes hashed and verified before privileges are dropped | same |

`CapabilityRuntime(isolation_required=True)` makes a missing control a refusal instead of a recorded degradation.

## Service health

`HealthMonitor` probes continuously and on demand (max age). States: STARTING, HEALTHY, DEGRADED (recovering, awaiting
revalidation), UNHEALTHY; the service manager adds STOPPED and BLOCKED. A capability depending on an unhealthy
mandatory service becomes `dependency_missing` immediately (S01B-T27); after recovery it is restored only after
`recovery_confirmations` consecutive healthy probes (S01B-T28).
