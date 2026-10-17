# 1. ArchiveBox — commit eec1d7db5fc0 (2026-10-16), image archivebox/archivebox:latest (v0.9.73)
Setup used: repo docker-compose.yml, `docker compose pull` + `docker compose up -d --wait` (README Quickstart). First admin created via web wizard, first-time setup wizard completed via "Review and save in Machine config".

Steps: A ok · B ok (docker-compose.yml) · C ok · D ok (image pull) · E ok (healthy) · F ok (302 -> /admin/login/) · G faults · H ok (Add URL -> crawl created; Tag created) · I faults · J no server errors

## App faults
1. **G/I — Uncaught JS SyntaxError on Tag add/change page.** Browser: `Invalid or unexpected token` (pageerror) on `/admin/core/tag/add/`. The inline snapshot-preview script is never executed.
   Points to: `archivebox/templates/admin/core/tag/change_form.html:233` — `'... onerror="this.style.display=\\'none\\'">'` — the `\\'` closes the single-quoted JS string.
2. **G/I — Snapshot list requests an extractor error message as a file URL (404).** Console: `Failed to load resource: 404` for
   `http://snap-9e3b50bf5e7a.archivebox.localhost:5797/Error%3A%20net%3A%3AERR_CERT_AUTHORITY_INVALID%20at%20https%3A//example.com` (x104) and `.../Error%3A%20net%3A%3AERR_BLOCKED_BY_CLIENT%20at%20http%3A//neverssl.com/` (x70), also with `?preview=1&titlebar=0`. Pages: `/admin/core/snapshot/`.
   Points to: `archivebox/core/models.py:4747-4786` `ArchiveResult.resolve_embed_path()` — when a result failed and has no output files, `output_str` (the error text, which contains '/') is returned as the embed path.
3. **G/I — 404 polling screencast frame.** `404 GET /api/v1/crawls/crawl/<id>/files/chrome_screencast/latest.jpg?...` on `/admin/environment/plugins/` while a crawl runs (runner log: `5 screencast frames (0 kept)`).

## Environment-caused (NOT counted as app faults)
- Every HTTPS extractor failed: `net::ERR_CERT_AUTHORITY_INVALID at https://example.com`, yt-dlp `[SSL: CERTIFICATE_VERIFY_FAILED]`, `wget failed (exit=5)` — sandbox intercepts container TLS with an untrusted CA.
- Plain-HTTP test (`http://neverssl.com/`): wget/readability/mercury/trafilatura/WACZ succeeded; all Chrome extractors failed `net::ERR_BLOCKED_BY_CLIENT` after Chrome upgraded the URL to https (consolelog: `Request failed: https://neverssl.com/ net::ERR_BLOCKED_BY_CLIENT`); yt-dlp `Read timed out. (read timeout=5.0)`. Inconclusive / likely network sandbox.
- GitHub-origin 404/403 (`github.com/_global-navigation/payloads.json`, `github.com/signup`) came from the DOCS link opening the GitHub wiki — not ArchiveBox.
- `Progress fetch error: TypeError: Failed to fetch` / `progress.json ERR_ABORTED` — fetch aborted by the test navigating away.

Fault count: 3 · first failing step: G
