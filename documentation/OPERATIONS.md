# Operations

## Prerequisites

| | Windows 10/11 | Linux |
|---|---|---|
| Python | 3.12 or 3.13 (`py` launcher) | 3.12 or 3.13 |
| Git, Node.js ≥ 20.9 | required / recommended | required / recommended |
| PostgreSQL 16 + pgvector | Docker Desktop (`infrastructure/docker/compose.yaml`) | package `postgresql-16-pgvector` or Docker |
| Redis 7 | Docker Desktop (set `ATTA__SERVICES__REDIS__MODE=external`) | `redis-server` (managed by the platform) |

## Install

    INSTALL.bat [--with-ai] [--with-browser] [--no-node] [--with-desktop] [--with-mobile]
    ./install.sh  [same options] [--install-system]

Creates `.venv`, installs the hash-locked Python requirements (`configuration/locks/`), runs `pip check`, installs
the locked Node workspaces (`npm ci`), creates `configuration/local.env` from the example, and validates the
configuration. It never downloads models and never provisions paid infrastructure.

Database (once): run `database/bootstrap.sql` as a PostgreSQL superuser (Docker does it automatically), then

    .venv\Scripts\python -m backend.database migrate      (Linux: .venv/bin/python …)

## Model locations

Models are never required on the Windows system drive. In `configuration/local.env`:

    ATTA_MODEL_PATHS=D:\models;E:\atta-models        (Windows, ';'-separated — USB drives are fine)
    ATTA_MODEL_PATHS=/mnt/models:/media/usb/models    (Linux, ':'-separated)

A path on the system drive produces a warning; an absent (unplugged) drive produces a warning, not a failure.

## Start / stop / health

    START.bat        | ./start.sh         start everything, wait for readiness; exit 0 = READY
    STOP.bat         | ./stop.sh          graceful reverse-order shutdown
    HEALTHCHECK.bat  | ./healthcheck.sh   real probes of services, ports, API live/ready, capabilities, models
    python -m backend.service_manager status [--json]

Exit codes: 0 ready/healthy · 1 not ready · 2 startup failed · 3 already running · 4 invalid configuration ·
5 dependency problems. Logs: `runtime/logs/` (`manager.jsonl`, `<service>.log`). State: `runtime/state/manager.json`.

Adding a capability: drop a directory with `capability.yaml` into `capabilities/` (or any configured directory) and
START again — the start scripts never change.

## Recovery

    RECOVERY.bat | ./recovery.sh          show the last verified, database-compatible checkpoint (dry run)
    RECOVERY.bat --yes | ./recovery.sh --yes
        → STOP, stash current work (git stash, includes untracked), create and switch to
          recovery/<stage>-<timestamp> at the checkpoint commit

Then INSTALL and START. Your previous work is recoverable with `git stash list` / `git stash pop`. A checkpoint
older than the database's applied migrations is refused: restore a database backup first.
