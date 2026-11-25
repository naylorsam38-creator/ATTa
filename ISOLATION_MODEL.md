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
| Process tree termination | subreaper + kill of all descendants, incl. detached ones | Job Object `KILL_ON_JOB_CLOSE`, `DIE_ON_UNHANDLED_EXCEPTION`, no breakaway (`windows_isolation.create_job`) |
| Memory / process limits | cgroup v2 or v1 (`memory`, `pids`) + rlimits | Job Object per-process memory limit (`JOB_OBJECT_LIMIT_PROCESS_MEMORY`) |
| Network | empty namespace when `deployment.worker_network: none` (H100 and AIR_GAPPED profiles); a task whose contract permits network destinations runs with host networking (destinations still enforced by the drift guard) | Windows Defender Firewall rules bound to the dedicated worker interpreter `runtime/worker-python/atta-worker.exe`: all non-loopback outbound blocked, all inbound blocked; API inbound allowed from loopback only (`backend/deployment/windows_hardening.py`, applied by INSTALL as an administrator action) |
| Identity | drop to an unprivileged account (`deployment.worker_user`, e.g. `atta-builder`) | restricted identity: before capability code loads, the child removes every token privilege except SeChangeNotify and lowers itself to LOW integrity (cannot write to the profile, repository, configuration, model folders or registry; cannot regain privileges). Workspace ACL: inheritance removed, SYSTEM full + service account modify, LOW integrity label (icacls) |
| GPU | `CUDA_VISIBLE_DEVICES=""` unless explicitly granted | same variable |
| Code integrity | package bytes hashed and verified before privileges are dropped | same |

`CapabilityRuntime(isolation_required=True)` makes a missing control a refusal instead of a recorded degradation.

**Windows status:** every Windows control above is implemented; none has been executed on Windows yet. The logic that
can be checked on Linux (firewall address ranges, ACL commands, Job Object flags, worker-interpreter provisioning) is
covered by `tests/unit/test_windows_isolation.py`; behaviour is certified only by S01B-T41 run as Administrator on a
Windows host, which the verifier records BLOCKED until then (BLOCKERS B-S01B-01).

## Service health

`HealthMonitor` probes continuously and on demand (max age). States: STARTING, HEALTHY, DEGRADED (recovering, awaiting
revalidation), UNHEALTHY; the service manager adds STOPPED and BLOCKED. A capability depending on an unhealthy
mandatory service becomes `dependency_missing` immediately (S01B-T27); after recovery it is restored only after
`recovery_confirmations` consecutive healthy probes (S01B-T28).

### Windows: reading protected secrets, child processes and firewall evasion

* **Reading secrets.** LOW integrity alone blocks writes, not reads. Protected secrets (`configuration/local.env`,
  key material under `configuration/`, `deployment.windows_protected_paths`) additionally carry a MEDIUM mandatory label
  with **No-Read-Up, No-Write-Up and No-Execute-Up** (`S:(ML;OICI;NRNWNX;;;ME)`, applied by INSTALL in CONFIGURE). A
  LOW-integrity worker can therefore neither read nor modify them, whatever their DACL says; the API and operator
  (medium) are unaffected. S01B-T41 asserts the read and the write are both denied (and that a medium process still
  reads the file).
* **Child processes / alternative executables.** Firewall rules bind to `atta-worker.exe`. To stop a worker from
  starting another program (`cmd.exe`, curl, a renamed copy of the interpreter) to escape them, each worker puts
  itself into its own nested Job Object with an **active-process limit of 1** before any capability code loads
  (`deployment.windows_worker_child_processes: false`, default). S01B-T41 attempts `cmd.exe`, a renamed copy of the
  interpreter and a direct outbound connection from the firewall-bound worker; all three must be denied.
* **Verifier keys.** The build seal's Ed25519 private key is unreadable by the worker account (0600 root, 0700 directory)
  and **encrypted at rest**; its passphrase is supplied only while sealing and every `ATTA_BUILD_*` variable is removed
  from test environments, so even the root-run gate tests can read only ciphertext (S01B-T42; the verifier refuses an
  unencrypted key, `verify.py protection`).
* **Regression runs** of the unchanged Stage 01/01A tests execute in a private copy of the source tree (qualification
  `source_copy` and S01B-T29…T33/T36…T38), so they cannot overwrite the repository's recorded Stage 01/01A evidence;
  the tests assert the evidence digest is unchanged.
