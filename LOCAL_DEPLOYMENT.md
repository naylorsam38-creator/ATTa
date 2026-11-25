# Local deployment (LOCAL_CPU / LOCAL_NVIDIA)

## One-click flow

```
Linux/macOS/WSL                 Windows
./install.sh                    INSTALL.bat           # dependencies + discover + plan  (stops for approval)
./install.sh --approve          INSTALL.bat --approve # provision, configure, start, qualify, activate
./run.sh                        RUN.bat               # start the ACTIVATED configuration and open ATTa
./healthcheck.sh                HEALTHCHECK.bat       # activation, manifest, services, ports, controller, capabilities
```

The launchers are thin: they bootstrap the locked Python environment (and Node workspaces unless `--no-node`) and then
delegate everything to `python -m backend.deployment`. Adding a capability never requires editing them (S01B-T35).

## What the installer does (`python -m backend.deployment install`)

State machine (persisted in `runtime/deployment/install_state.json`, every transition appended to
`install_transitions.jsonl`):

`DISCOVER → VALIDATE → PLAN → AWAITING_OPERATOR_ACTION → PROVISION → CONFIGURE → START_SERVICES → QUALIFY → ACTIVATE → READY`
plus `BLOCKED`, `FAILED → ROLLING_BACK → ROLLED_BACK`.

1. **DISCOVER** — read-only discovery (`environment.json`), profile decision, model discovery (`model_index.json`),
   integrity-protected environment manifest (`environment_manifest.json` + history in `manifests/`).
2. **VALIDATE** — resolves configuration (operator > local > profile > discovered > defaults), validates it against the
   machine, writes `effective_config.json` and the human-readable `effective_config.txt` (every value with its source;
   secrets shown as `**********` with `secret:VAR`).
3. **PLAN** — `install_plan.json`: detected environment, profile, existing/missing dependencies, configuration changes,
   services, ports, model locations, storage, admin/network/offline actions, qualification suite, rollback actions.
   Nothing is changed before the operator approves (`--approve`). Prerequisites only an administrator can provide
   (PostgreSQL superuser bootstrap, system packages) stop the installer in **BLOCKED** with the action listed.
4. **PROVISION/CONFIGURE** — locked dependencies, rollback snapshot, database migrations (migration role).
5. **START_SERVICES** — service manager start.
6. **QUALIFY** — the INSTALL suite (and the H100 suite on the H100 profile) in isolated resources (see ISOLATION_MODEL.md).
   FAIL → rollback; mandatory BLOCKED → BLOCKED (never activated).
7. **ACTIVATE** — `active.json` binds profile, manifest hash and effective-configuration hash.

A re-run resumes from the recorded state; an approval continues the recorded plan unless the machine or configuration
changed (then it re-plans). Exit codes: 0 READY, 10 AWAITING_OPERATOR_ACTION, 11 BLOCKED, 12 FAILED, 13 ROLLED_BACK.

## RUN refuses silently-changed setups

`run` starts services only if `active.json` exists, the manifest verifies and matches, the GPU set has not changed,
the profile is still compatible and the effective configuration hash is unchanged. Otherwise it prints why and exits
20–23 (run INSTALL again to requalify).

## Useful commands

```
python -m backend.deployment discover                 # machine facts (JSON)
python -m backend.deployment profile --profile H100   # would this profile work here? (exit 11 = no, with reasons)
python -m backend.deployment models [--require ID]    # discovered models / deterministic runtime choice
python -m backend.deployment config                   # effective configuration report with provenance
python -m backend.deployment deps --check             # lock drift
python -m backend.deployment audit-offline            # zero-hidden-Internet audit
python -m backend.deployment qualify --suite LOCAL    # isolated qualification (results.json per run)
python -m backend.deployment rollback                 # restore the previous activation
```

## Models

Point `ATTA_MODEL_PATHS` / `models.storage_paths` at existing model folders (any drive). Removable drives are scanned
when `deployment.scan_removable_media` is true; on Windows `D:\models` and `D:\` are *candidates* only. Models are
referenced in place, never copied or downloaded. Inference itself arrives in Stage 03.
