# 4. Alexandrie — commit ccf5603ea067 (2026-09-21), images ghcr.io/smaug6739/alexandrie-{backend,frontend}:latest + mysql:8.0 + rustfs/rustfs:latest
Setup used: README Quick Start — `cp .env.example .env` (unchanged), `docker compose up -d`.

Steps: A ok · B ok · C fault (shipped .env) · D ok · E ok (all healthy) · F ok (8200 → 200) · G ok (signup creates owner, duplicate signup rejected "Username already exists", login → /dashboard/home; 23 pages crawled, 236 clicks) · H fault (document create OK; CDN upload stored but not viewable) · I faults · J clean

## App faults
1. **C/H — Uploaded files never display: shipped `.env.example` points the CDN at a port nothing listens on.** `.env.example:21 CDN_URL=http://localhost:9005` while `.env.example:64 RUSTFS_EXTERNAL_PORT=9000` (compose maps `${RUSTFS_EXTERNAL_PORT}:9000`, docker-compose.yml:40). Frontend log at start: `CDN: http://localhost:9005`. After uploading test-image.png (POST /api/resources 200), the CDN preview page requests `http://localhost:9005/alexandrie/766692675570237441/766693986629976073.png` → `net::ERR_CONNECTION_REFUSED` (x18), image naturalWidth=0. Same object is served fine at `http://localhost:9000/alexandrie/.../766693986629976073.png` (200 image/png). Points to: `.env.example:21` (used by docker-compose.yml:82 `MINIO_PUBLIC_URL` and :122 `NUXT_PUBLIC_BASE_CDN`).
2. **I — Nuxt hydration mismatch on dashboard home.** Console: `Hydration completed but contains mismatches.` on `/dashboard/home` (`/_nuxt/DiwvtjLI.js`). Minor.
3. **I — Console error on every login page view when OIDC is not configured.** `404 GET http://localhost:8201/api/auth/oidc/providers` → `{"message":"OIDC is not configured"}` → console "Failed to load resource: 404". Minor.

## Not counted (environment / test / external)
- `403 https://api.github.com/repos/Smaug6739/Alexandrie(/releases/latest)` "rate limit exceeded" — shared sandbox IP.
- `403 https://embed.diagrams.net/?embed=1&ui=min&spin=1&proto=json` in /dashboard/cdn/diagram iframe — external service refuses headless browser (curl gets 200).
- `Failed to execute 'writeText' on 'Clipboard': Write permission denied.` — headless browser without clipboard permission.
- `400 POST /api/users` — deliberate duplicate signup; `PUT /api/user/settings ERR_ABORTED` — aborted by test navigation.
- MySQL image warnings (deprecated --skip-host-cache, zoneinfo) — upstream image.

Fault count: 3 · first failing step: C (.env.example) → visible at H
