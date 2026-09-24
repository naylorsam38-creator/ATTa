# APP Builder

People log in, describe the app they want on the Front Door page, and builds run through a
pipeline. A build is only **QUALIFIED** once a real browser check passes, and only qualified
builds are handed to Coolify to run.

## Flow

1. **Log in** with your own account (admin, or one of the 10 test accounts).
2. **Front Door**, the first page after login: describe the app you want. It asks one
   narrowing question if needed, then asks "Is this the app you're talking about?" A **Yes** is
   recorded against your account.
3. **Add an app** (the "Add app" page): upload any app's source zip (e.g. `gitea-main.zip`),
   or paste a git address (`https://github.com/owner/name`). Uploading the ATTa bundle itself
   updates the system. Each one starts a build that you own.
4. **The library grows by itself.** A new app goes into the library under its own name, and
   the catalogue works out where its code is, which skin category fits (from its own README
   and package description), and whether it's a web app, service, system or package. No list
   has to name it first. See it all on the **Library** page.
5. **Pipeline**: add app → catalogue → install skin → intake → watcher stages 1–6, with stage 6
   being the real Playwright browser check.
6. **Per-app qualification.** An app is QUALIFIED only if it passed every stage, including the
   browser. A build is QUALIFIED (all apps passed), PARTIALLY_QUALIFIED (some did) or
   NOT_QUALIFIED (none). One broken app no longer holds the others back.
7. **Coolify**: every qualified app is handed off automatically. See
   [docs/COOLIFY-HANDOFF.md](docs/COOLIFY-HANDOFF.md).

`04-deployment/upstream_apps.json` is now only a **seed list**: repos cloned into the library if
they aren't there yet. Delete it, and nothing breaks. APP_MAP in the skins package is kept as
hand-made choices; apps it doesn't mention are mapped automatically.

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

Once it's up, log in and upload **this same zip** as the bundle. Each upload installs the Front
Door page inside it, so an older bundle would bring back the older page.

Coolify: `sudo bash 05-coolify/install-coolify.sh`, ideally on a separate server (its proxy wants
ports 80/443). Then follow docs/COOLIFY-HANDOFF.md to connect the two.

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
| `04-deployment/catalogue_sync.py` | App catalogue: every app in the library gets a code root, skin category and profile automatically |
| `04-deployment/app_classifier.py` | Finds an app's code root (monorepos too) and picks its skin category from its own words |
| `04-deployment/app_catalogue.json` | The app catalogue (edit a category here and it sticks) + `CATALOGUE_CHANGELOG.md` |
| `04-deployment/system_watcher.py` | Six-stage checker (stage 6 = real browser) |
| `04-deployment/coolify_handoff.py` | Hands qualified builds to Coolify |
| `04-deployment/maintenance.py` + tiers | Self-healing: known fixes → capability adapter → LLM → human alert |
| `05-coolify/coolify-main.zip` | The supplied Coolify source, unchanged (version 4.3.23) |
| `05-coolify/install-coolify.sh` | Installs that Coolify version on a server with Coolify's own installer |
| `docs/history/` | Audit and fix notes from the 2026-09-22 bundle |

## Known gaps

- **Apps are started by app_runner.py (v109).** See docs/history/CHANGES-2026-09-24-APP-RUNNER.md.
  nothing starts them yet: each app needs to be running with its `.ui-capability/run-ui.sh`
  (TARGET_URL set) before it can pass stages 2–6. Until then a build ends NOT_QUALIFIED with
  "unreachable". An app runner (compose/Dockerfile per app, ports assigned) is the next step.
- **Auto categories are word-based.** They're right for most apps (20/31 agree with the
  hand-made APP_MAP, and most disagreements are debatable), but not all. Change any app's
  `skin_category` in `app_catalogue.json`; a sync never overwrites it.
- **The Front Door's questions need `ANTHROPIC_API_KEY`** on the server.
- **The Front Door's app library (`LIBRARY`) is empty**, and the Front Door choice isn't
  linked to a build yet.
- **Running apps aren't isolated per user**; builds run one at a time.
- **Only the server can prove the full chain.** DNS, TLS and the live browser stage can only be
  proven there.
