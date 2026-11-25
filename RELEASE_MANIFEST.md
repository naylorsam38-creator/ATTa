# ATTa V1 — Stage 01 release manifest

| Item | Value |
|---|---|
| Release | ATTa Version 1 — Stage 01 + Stage 01A + Stage 01B, consolidated |
| Primary package | `ATTa-V1-STAGE-01-COMPLETE.zip` (this source tree; no offline bundle required for online installation) |
| Final application source | `86f3c88` (sealed); qualified by S01B gate run 6 and Stage 01 FINAL gate run 4 (on `3fb3916` = final source + gate evidence) |
| Release tag | `v1-stage01` (local; remote tag push refused, HTTP 403 — BLOCKERS B-S01F-02) |
| Gates | S01 PASSED · S01A PASSED · S01B PASSED (43 PASS / 0 FAIL / 2 BLOCKED) · S01F PASSED (4 PASS / 1 BLOCKED) |
| Physical qualification outstanding | Windows (S01B-T41, Q-WINDOWS-JOBS), NVIDIA H100 (S01B-T43, S01F-T05) — PHYSICAL_QUALIFICATION.md |
| Optional offline bundles | `ATTa-V1-offline-bundle-linux-x86_64.zip` (complete except container images), `ATTa-V1-offline-bundle-windows-x64.zip` (**incomplete**: pgvector, Redis/Memurai, Git for Windows, browsers missing) — OFFLINE_DEPLOYMENT.md §5 |
| Checksums | `SHA256SUMS.txt` delivered with the packages |

## Contents of the primary package

Source (backend, Hub/runtime controller, capability registry, tool catalogue, policy engine, build controller, service
manager, deployment/adaptive installer, model gateway adapters), frontend (web, desktop, mobile, shared packages),
installers and launchers (`install.sh`, `INSTALL.bat`, `run.sh`, `RUN.bat`, `healthcheck.sh`, `HEALTHCHECK.bat`,
`start/stop/recovery`), configuration templates and profiles, dependency declarations and locks, database schema and
migrations, tests (unit, integration, security, acceptance S01/S01A/S01B, legacy regression), verification reports,
the build-controller ledger and per-run evidence, requirement registers and the existing ATTa application builder.

Excluded: `configuration/local.env`, private keys, passphrases, credentials, `runtime/` (installation state,
qualification workspaces, bundles), virtual environments, `node_modules`, model weights, OS images.

## Install

Online: `./install.sh` (Linux/macOS) or `INSTALL.bat` (Windows) → review `runtime/deployment/install_plan.json` →
re-run with `--approve` → `./run.sh` / `RUN.bat`. Offline: add `--offline-bundle DIR` (see OFFLINE_DEPLOYMENT.md).
Details: LOCAL_DEPLOYMENT.md, H100_DEPLOYMENT.md, ENVIRONMENT_COMPATIBILITY_MATRIX.md.
