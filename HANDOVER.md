# ATTa + Coolify: handover (2026-09-25)

## Where things stand, honestly

| Item | State |
|---|---|
| Coolify deploy kit (this folder) | **DONE.** On branch `claude/att-readiness-o2tfin`, PR #6 (draft). All CI green, no conflicts, no review comments. Needs a human to mark it ready and merge. |
| ATTa ↔ Coolify hand-off | **DONE and proven.** ATTa v116's unmodified `coolify_handoff.py` was run against a live Coolify installed by this kit: `DISPATCHED`, the deployment finished. |
| ATTa's own `05-coolify/install-coolify.sh` fixes | **NOT DONE.** Only the preparation below was done. No code was changed. |
| ATTa application code | **Not modified**, by design. |

## What was built (in this zip)

- `scripts/install.sh`: installs Coolify 4.3.23 safely (admin is created and verified, sign-up closed, proxy stable, localhost validated), then runs `verify.sh`.
- `scripts/preflight.sh` / `scripts/verify.sh`: read-only checks before and after install.
- `scripts/connect-atta.sh`: turns on Coolify's API, creates a deploy+read-only token, and writes `COOLIFY_URL`/`COOLIFY_TOKEN` and `coolify_resources.json` for ATTa.
- `scripts/backup.sh` / `restore.sh` / `upgrade.sh`: recovery, including restore onto a new server.
- `docs/RUNBOOK.md` (setup steps), `docs/DEPLOY-ATT.md` (the ATTa link), `docs/TROUBLESHOOTING.md`, and `docs/FIXES.md` (all 30 fixes, with what/why).
- Tests: 72 unit tests (`make test`) and a 53-check end-to-end test on real Coolify (`tests/e2e/e2e.sh`, which is destructive, so throwaway VM only). Both pass locally and on GitHub.

## The one remaining job: patch ATTa's `05-coolify/install-coolify.sh`

That file is on branch `claude/atta-hardening-v116` (ATTa release v116).
The fixes needed are already written and tested in this kit; port them over:

1. **Admin verification.** Today `ROOT_*` is optional, so Coolify's sign-up can be left open for anyone.
   - Require `ROOT_USER_EMAIL`, default `ROOT_USERNAME`, and generate the password if it's empty.
   - Validate the details before installing, using the same rules as `scripts/lib/common.sh`: `validate_email`, `validate_password`, `validate_username`.
   - After install, confirm the admin exists and sign-up is off, re-running Coolify's seeder if needed (see `ensure_admin_account` in `scripts/lib/common.sh`).
2. **Valid username for automated installs.** Run Coolify's installer as `env USER=root HOME=/root ...` (see `run_coolify_installer`). With an empty `$USER`, Coolify can't deploy anything.
3. **Proxy stability.** After install:
   - wait for Coolify to register its localhost server (`localhost_server_seeded`);
   - run Coolify's validation (`activate_localhost_server`);
   - then require 75 seconds of stable proxy (`wait_for_stable_proxy`).
   - **Skip this step** if port 80 was already used by ATTa's nginx on the same machine. The script already warns about that case.

Constraints (from the handover brief): don't redesign ATTa, don't replace Coolify, don't move deployment ownership, and don't remove existing validation or recovery.

Tests: add `ATTa/tests/test_v117_coolify_installer.py` in ATTa's style (plain `unittest`, no Docker; see `PinnedDependencies` in `test_v116_hardening.py`). `tests/` is an allowed bundle folder. Bump `release.json` to v117.

### Preparation already done for that job

- The ATTa v116 test suite passes: **215 tests OK, 5 skipped**. Run it from a world-readable folder, e.g. `/opt`. Run from a private folder (mode 700), 3 `SkinProxyRunsUnprivileged` tests fail with exit 126. That's the environment, not a bug.
  Command: `cd ATTa && python3 -m unittest discover -s tests`.
- Your uploaded `coolify-main.zip` matches ATTa's pinned SHA-256 (`509f4abb…6609`), and its `scripts/install.sh` is byte-identical to the official Coolify v4.3.23 installer.

## How to pick it up

1. Merge PR #6: https://github.com/naylorsam38-creator/ATTa/pull/6
2. `git checkout claude/atta-hardening-v116`, then patch `ATTa/05-coolify/install-coolify.sh` as above and add the tests.
3. Run ATTa's suite (it must stay 215+ OK) plus the new tests. Then run a real install on a throwaway Ubuntu 24.04 VM. `tests/e2e/e2e.sh` in this kit shows how to prove it end to end.

## Real server setup (once merged)

Follow `docs/RUNBOOK.md`:
1. Ubuntu 24.04, 4 GB RAM, 40 GB disk.
2. Run `./scripts/install.sh`.
3. Set the domain and HTTPS.
4. Restrict port 8000 to the ATTa server.
5. Run `./scripts/connect-atta.sh --map-apps`, then copy the output to ATTa's `/srv/app-builder/.env`.
