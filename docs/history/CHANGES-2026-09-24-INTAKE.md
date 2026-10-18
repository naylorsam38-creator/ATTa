# v105 — Intake check and capability fixes (2026-09-24)

## Fixed once at the source (skins package, `UI_Skin_Capability_OneShot_v2.zip`)
Every app's overlay is copied from here, so every build gets these for free.

`out/ui-bridge/proxy.js`
- HEAD, 204, 304 and empty responses pass straight through (the empty-gzip 502 is gone).
- Anything it doesn't change (images, files, non-UTF-8 pages) is streamed, never held in memory.
- Pages with a non-UTF-8 charset are passed through untouched instead of garbled.
- No skin or "+" hook on `/admin/` or `/archive/` (change with `APP_BUILDER_NO_INJECT_PATHS`).
- `x-authenticated-customer-id` is ignored unless the request also carries
  `x-atta-gateway-secret` equal to `APP_BUILDER_GATEWAY_SECRET`. Nothing sets that header
  today; the proxy's `--customer-id` is what's used, so nothing changes in normal running.
- Origin and Referer are rewritten to the app's own address, so Django CSRF checks pass
  through the proxy without editing the app's settings.
- Redirects naming the app's own address are brought back through the proxy.

`out/capability-port/caps/button-mover.mjs`
- Attaching twice drops the old watcher first (no leak).
- A rename the app keeps overwriting (React/Vue) is retried 3 times, then left alone.
- Reset also clears the positioning it added.

Already fixed in the current package (the report was against an older copy):
raw code/HTML always confirms, pasted code isn't replayed on reload, sandboxed code is
never run on the page first, remote .json manifests are fetched before sandboxing.

## New: `04-deployment/intake.py`
Runs after the installer (`INTAKE_CHECK` step). Fixed rules only — no AI, no network.
Per app it: creates missing overlay folders, refreshes any out-of-date capability file from
the package, syntax-checks the overlay, fingerprints the framework, flags PHP packages that
aren't standalone apps, and flags a too-old Python. ArchiveBox gets a note about
single-domain mode.

Writes `<app>/.atta-intake.json` (the ledger): status, original git commit, frameworks,
every change with a timestamp, and anything left over. READY apps are not re-checked.
NEEDS_ATTENTION apps are re-checked each build and flip to READY once fixed.
Leftovers show on the build as `intake_warnings`; they don't stop the build.
Manual run: `python3 intake.py /srv/app-builder/library <package>/out`

## v106 — Classification, toolchain evidence, qualification profiles

`intake.py` (ruleset 2, so every app gets one fresh check)
- Classifies each app: `web`, `service`, `system` (e.g. Moby: has `daemon/` or `cmd/dockerd`),
  or `package` (PHP `composer.json` type library). Go apps with a `web/`, `ui/` or `frontend/`
  folder are `web` (ntfy); other Go apps are `service`.
- Toolchain evidence for Python, Go, Node, PHP: what the app declares (`requires-python`,
  `go.mod`, `engines.node`, `require.php`) versus what the server has. Evidence only, never
  repaired. With a Dockerfile a mismatch is a note, since the build uses the container.
- Ledger now records `type`, `qualification`, `toolchain`, `docker_build`.

`system_watcher.py` — stage six picks its profile from the ledger (or a target's `profile`):
- web: unchanged, real browser.
- service: app and proxy must answer; skin and hook are NOT_APPLICABLE; stage six SERVICE_RESPONDS.
- system / package: stages 2–5 NOT_APPLICABLE; stage six checks build readiness from the
  toolchain evidence (BUILD_READY or TOOLCHAIN_TOO_OLD).
- No ledger means web, the old behaviour.

`known_fixes.py` — TOOLCHAIN_TOO_OLD / NO_INTAKE_RECORD go straight to a human, never the LLM.
