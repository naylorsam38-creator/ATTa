# 2026-09-25 — ADM (ATTa Deploy Manager) built and integrated

The uploaded `ATTa-ADM-v111.zip` was a 50-line skeleton (print-statement entrypoints, a config
constant that didn't exist, a compile step that could never run, a systemd unit that couldn't be
enabled). Rebuilt in full and wired into the bundle.

New: `04-deployment/deployd/` — `deployd.py` (daemon), `deployctl` (CLI), `adm/{config,journal,queue,
staging,backup,activation,health,manager}.py`, `systemd/atta-deployd.service`.

Changed:
- `pipeline.py`: on the server, an uploaded ATTa bundle is also queued with ADM (`queue_system_update`),
  so a web upload now updates the system code — after the build finishes. Records `adm` on the build.
- `gateway.py`: build page shows a "System update" section read from ADM's journal.
- `bootstrap.sh`: installs + enables `atta-deployd`, puts `deployctl` on PATH, creates `adm/` dirs,
  gates deployment on the service being active. Does not restart deployd (it restarts itself).

Full description and the 12-step server checklist: `docs/ADM.md`. Not run here.
