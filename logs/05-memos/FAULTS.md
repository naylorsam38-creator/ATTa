# 5. memos — commit 1a51572406e1 (2026-10-17), image neosmemo/memos:stable (reports 0.31.0, commit 2b2192d4)
Setup used: README Quick Start `docker run -d --name memos -p 5230:5230 -v <dir>:/var/opt/memos neosmemo/memos:stable`.

Steps: A ok · B ok · C ok · D ok · E ok · F ok (/ 200, /healthz 200) · G ok (first-run admin signup, open signup of 2nd user, 30 pages, 62 clicks, 0 click errors, 0 JS errors) · H ok (memo with #tag + task list + image attachment saved; thumbnail served) · I minor fault · J clean

## App faults
1. **I — Console error on every logged-out page load.** `401 POST /memos.api.v1.AuthService/RefreshToken` → "Failed to load resource: the server responded with a status of 401 (Unauthorized)" on `/` and `/auth` before login (the web client always attempts a token refresh). Minor.

## Observations (not counted)
- Version reporting: startup log `Memos 0.31.0 started successfully!` and `GetInstanceProfile` → `"version":"0.31.0"` while the same start log says `database initialized successfully schemaVersion=0.31.8`; README describes YY.MM.N releases (e.g. 26.10.0). Version string inconsistent; impact unknown.
- `GET /api/v1/sse ERR_ABORTED` (x96) — SSE stream closed by test navigation.

Fault count: 1 (minor) · first failing step: I
