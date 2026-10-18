# Fewer false failures (2026-09-24, after syntax triage)

## Browser stage 6 (system_watcher.py)
- Page load waits for `load`, then gives the network a short grace period to go quiet.
  Before, it required `networkidle` within 10 s, which apps with live connections
  (websockets, polling) and heavy first loads could never meet.
  New settings: `APP_BUILDER_BROWSER_TIMEOUT` (45 s), `APP_BUILDER_NETWORK_IDLE_GRACE` (8 s).
- Console errors and failed requests from third-party origins, a missing favicon, and requests
  the page cancelled itself (ERR_ABORTED) are recorded under `ignored_noise`, not failures.
  Errors from the app's own origin and uncaught page exceptions still fail.
  `APP_BUILDER_BROWSER_STRICT=true` restores the old any-error-fails behaviour.

## Upstream repos (pipeline.py)
- A failed update of an existing checkout keeps the local copy instead of failing the build.
- A fresh clone of a repo that's gone/private/renamed is recorded as REPO_UNAVAILABLE and
  skipped; the build fails only if that leaves a required category with no app, and the error
  names the repo. Network errors and timeouts still fail and are retried (known fix).
- Half-finished clones are removed so they can't block the next run as CONFLICT_NON_GIT.
- `GIT_TERMINAL_PROMPT=0`: a private repo fails immediately instead of waiting for a password.
- Checked 2026-09-24: all 31 repos in upstream_apps.json are reachable.

## Upload (pipeline.py)
- Identical duplicate copies of UI_Skin_Capability_OneShot_v2.zip are accepted (one is used).
- A single renamed copy (e.g. "UI_Skin_Capability_OneShot_v2 (1).zip") is accepted.
- macOS `__MACOSX` / `._` junk files are ignored.
- Different duplicate copies, or several renamed candidates, still go to a human.

## known_fixes.py
- `build.repo_unavailable`: tells the human exactly which repo to fix.
