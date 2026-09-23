# Deployment Fixes Applied

1. Skin injection uses a deterministic `default` customer when the trusted customer header is absent; nginx can still forward a real customer header.
2. Browser upload now parses `multipart/form-data` as a streamed file, validates the ZIP before queueing it, and rejects malformed uploads.
3. The persistent watcher is installed by bootstrap. It has six real stages when Playwright/Chromium is available; stage 6 is not silently treated as PASS when disabled. An empty target set is FAIL.
4. Docker Moby / Traefik / Coolify / FRP map exactly to `devops_console`; Grafana / Umami / Supabase map exactly to `dashboard`. Codex is blocked until its exact repository is identified. Next AI Draw.io normalisation is fixed.
5. `front-door.html` has one canonical source under `02-front-door/`.
6. The extracted top-level duplicate `skin/out/` tree and duplicate deployment `front-door.html` were removed.
7. Pipeline validates the uploaded capability package before library operations, checks required real categories and variants, and rejects installer/mapping errors.
8. Failed uploads are renamed `.failed.zip`; only successful runs become `.processed.zip`.
9. Existing Git repositories are fetched without reset/checkout, preserving local harvesting patches.
10. Gateway is bound to localhost behind nginx rather than exposing port 8787 directly. Session cookies use `Secure` when the request arrived through HTTPS.
11. Bootstrap installs Playwright/Chromium for the browser acceptance stage and removes the redundant watcher timer; one persistent watcher owns its interval.

AWS live proof still required: DNS, TLS, network reachability, actual application processes/targets, and the external browser login/upload/verification run.

12. Processed/failed uploads are excluded from the inbox worker scan so they are never reprocessed indefinitely. Archive extraction is streamed with a configurable uncompressed-size ceiling.
13. Static preflight is regenerated to PASS for all 46 categories and the readiness marker explicitly states READY_FOR_LIVE_VERIFICATION until the three newly-added categories have real browser evidence.

14. `developer_tools` is exercised by the real Agno catalogue entry, and the pipeline now fails if any required deployment category has no deployable app mapping. Session tokens are now server-expiring, and generated Python bytecode is stripped from the shipped package.
