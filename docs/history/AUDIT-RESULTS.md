# Deployment Audit Results — 2026-09-22

## Current result

Fresh line-by-line audit performed from the shipped CLEAN5 bundle. Confirmed specification gaps were corrected before this package was rebuilt. This report does not claim AWS/browser completion.

### Five-pass checks

- Outer ZIP integrity: PASS
- Nested capability ZIP integrity: PASS
- Python syntax: PASS
- Bash syntax: PASS
- JSON parsing: PASS
- 46 skin categories present: PASS
- `skin-002`..`skin-005` CSS + metadata for all 46: PASS
- Required categories `dashboard`, `developer_tools`, `devops_console`: PASS
- Required-category mappings exercised by real catalogue apps: PASS
- `Next AI Draw.io` normalisation: PASS
- Codex unresolved-repository block: PASS
- No shipped Python bytecode: PASS
- Archive duplicate/path traversal checks: PASS

## Runtime checks

- Real multipart browser-style upload into the gateway: PASS
- Uploaded ZIP re-opened successfully with Python ZIP reader: PASS
- Real upstream HTTP server + real Node UI proxy: PASS for stages 1–5
- Skin bytes served by proxy match installed `skin.css`: PASS
- `port.js` bytes served by proxy match installed capability port: PASS
- Browser-check disabled path: exercised in the local harness after the final watcher fix.
- Stage-3 proxy 400 path: `PROXY_400` and later stages `SKIPPED`.
- App-down path: `2 APP_UP FAIL`; stages 3–6 `SKIPPED`.
- Final stdout now ends with `PASS`/`FAIL` as required by the governing specification.

## Browser-stage limitation

The local execution environment blocks Chromium navigation with `net::ERR_BLOCKED_BY_ADMINISTRATOR`. This is an environment restriction, not a deployment result. No browser-stage PASS is fabricated.

On AWS, bootstrap installs Playwright and Chromium and production defaults `APP_BUILDER_BROWSER_CHECK=true`. Stage 6 must reach `OK` for a fully green live watcher result.

## Fix made during this audit

The watcher previously returned exit code 1 when `BROWSER_CHECK=false` even though the revision specification explicitly permits `PASS (CLEAN not run)`. The exit-code calculation now uses `broken_at is None`, matching the governing revision.

The watcher also now performs the specified dormant-port check, reports later stages as `SKIPPED` after an upstream failure, emits the six-stage stdout structure, follows redirects while checking the final proxy origin, and uses the specified browser-stage failure codes.
