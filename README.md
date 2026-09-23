# APP Builder

People log in, describe the app they want on the Front Door page, and builds run through a
pipeline. A build is only **QUALIFIED** once a real browser check passes, and only qualified
builds are handed to Coolify to run.

## Flow

1. **Log in** with your own account (admin, or one of the 10 test accounts).
2. **Front Door**, the first page after login: describe the app you want. It asks one
   narrowing question if needed, then asks "Is this the app you're talking about?" A **Yes** is
   recorded against your account.
3. **Upload** a bundle. This starts a build that you own.
4. **Pipeline**: validate → fetch library → install skin → watcher stages 1–6, with stage 6
   being the real Playwright browser check.
5. **QUALIFIED** only if every stage passed, including the browser stage.
6. **Coolify**: every qualified build is handed off automatically. See
   [docs/COOLIFY-HANDOFF.md](docs/COOLIFY-HANDOFF.md).

## Accounts and roles

| Role | Sees |
|---|---|
| `admin` | every user's builds, plus system status |
| `user` | only their own builds. Another person's build returns "not found" |

Setup creates `admin` and `tester01` … `tester10` and writes their passwords **once** to
`<ROOT>/TEST_ACCOUNTS.txt` (permissions 0600). Hand one line to each tester, then delete the file.
Passwords are stored only as salted PBKDF2 hashes.

```
python3 04-deployment/accounts.py list
python3 04-deployment/accounts.py add NAME [--admin]   # prints the new password once
python3 04-deployment/accounts.py reset NAME           # also logs that account out everywhere
python3 04-deployment/accounts.py disable NAME
```

On a server that's being upgraded, the old shared `APP_BUILDER_PASSWORD` becomes the admin's
password, so the existing login keeps working.

## Run it

`bash run`. On an AWS/systemd server this runs the full deploy (`04-deployment/bootstrap.sh`).
On a laptop it starts a local instance at http://127.0.0.1:8787/. See `START-HERE.txt`.

## Layout

| Path | What |
|---|---|
| `01-specs/` | Original build specifications (reference) |
| `02-front-door/front-door.html` | The questions page |
| `03-ui-skins-capability-package/` | Skins library + capability port (consumed by the pipeline) |
| `04-deployment/gateway.py` | Login, Front Door, upload, builds pages |
| `04-deployment/accounts.py` | Accounts and roles |
| `04-deployment/builds.py` | Per-user build records and states |
| `04-deployment/pipeline.py` | Build pipeline and the QUALIFIED gate |
| `04-deployment/system_watcher.py` | Six-stage checker (stage 6 = real browser) |
| `04-deployment/coolify_handoff.py` | Hands qualified builds to Coolify |
| `docs/history/` | Audit and fix notes from the 2026-09-22 bundle |

## Known gaps

- **The Front Door's questions need Claude.** Matching calls `window.claude`, which only
  exists when the page is open inside Claude. Served from this gateway, the page loads but says
  "Open this page inside Claude to talk to it." Connecting it to the Claude API on the server is
  the next step.
- **The Front Door's app library (`LIBRARY`) is empty**, so a confirmed app shows a sample
  card, not a real app.
- **The Front Door choice and the build aren't linked yet.** The confirmed choice is recorded
  per user in `state/front-door-choices/`, but a build still comes from an uploaded bundle.
- **The pipeline's library and app targets are shared.** A build re-installs the whole library,
  so builds from different users run one at a time against the same targets. Records and
  visibility are per user; the running apps aren't isolated per user.
- **Only the server can prove the full chain.** DNS, TLS and the live browser stage can only be
  proven there.
