# ATTa Stage 12 plug-in installer

Transactional, stdlib-only installer for the Stage 12 integrations sidecar. Works on Linux/macOS (`install.sh`) and
Windows (`install.bat`); both call `install.py` (Python 3.10+). It never merges git branches and never pushes.

```
./install.sh  --target /path/to/ATTa --dry-run          # plan, conflicts, migration number — changes nothing
./install.sh  --target /path/to/ATTa                    # install / upgrade (transactional, verified, rolled back on error)
./install.sh  --target /path/to/ATTa --uninstall        # remove exactly what Stage 12 installed
./install.sh  --target /path/to/ATTa --recover          # undo an install interrupted by a crash / power loss
install.bat   --target C:\ATTa --dry-run                 # Windows, same options
```

Options: `--migration-number N|auto` (when another worker already uses 0012), `--check-db` (also compare with the
migrations the target database has APPLIED), `--python <target interpreter>` (default `<target>/.venv`),
`--no-verify-import`, `--json`.

## What it does

| Content | Strategy | Conflict when |
|---|---|---|
| 134 new Stage 12 files (code, migration, configuration, docs, tests, fixtures, evidence) | created; SHA-256 verified after writing | the path exists with different content (never overwritten) |
| `backend/orchestration/gateway.py`, `catalogue.py`, `backend/api/app.py`, `requirement_register.yaml` | **hunk by hunk** (exact original context must occur exactly once); other workers' edits elsewhere are preserved | another change overlaps a hunk's context |
| `DECISIONS.md`, `BLOCKERS.md`, `IMPLEMENTATION_PROGRESS.md`, `CHANGE_MANIFEST.md` | marker-delimited section `<!-- ATTA-STAGE12:BEGIN … -->` appended / replaced | markers damaged |
| `MASTER_IMPLEMENTATION_REGISTER.md` | re-rendered by the target's own `scripts/requirement_register.py` | generator fails |
| `.gitignore` | marker-delimited lines | — |
| `database/migrations/0012_stage12_integrations.sql` | number checked against every migration file (and, with `--check-db`, the DB ledger) | number used by another worker |

Every run verifies `SHA256SUMS.txt` + `manifest.json` first. Changes are journalled (`<target>/.atta-stage12/txn-*`)
with backups before any write; any failure — including the post-install verification (checksums, hunks, sections,
and an import of the patched modules with the target interpreter) — restores the target byte-for-byte. A second run of
the same version is a no-op; a newer payload upgrades only content still identical to what was installed.
Exit codes: 0 ok, 2 bad payload, 3 conflicts (nothing changed), 4 failed and rolled back, 5 not an ATTa tree.

After installing: `python -m backend.database migrate`, then `python scripts/stage12_verify.py`.
Line endings of CRLF (Windows) checkouts are preserved. Building a payload (sidecar repo only):
`python installer/stage12/build_payload.py --out installer/stage12/payload`.
