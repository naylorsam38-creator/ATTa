# 24-app end-to-end run behind the unmodified proxy / port.js / mover (2026-10-17)

Each app started fresh in Docker, one at a time, then `node e2e/run.mjs <app>` (checks a–k). Proxy on 92NN in front of the app on 91NN.
Nothing in `adapter/` or `e2e/bed/` was changed. Evidence per app: `e2e/evidence/<app>/` (result.json, console.txt, screenshots, videos).
Sandbox-only start-up changes (no IPv6, file-limit cap, HTTPS-proxy CA) are noted in each app's compose/SETUP file.

| # | App | Result |
|---|---|---|
| 1 | ArchiveBox | 3 fail: f "BASE_URL MISMATCH" banner on every page (proxy sends target as Host); j inline `<a>` not moved; k 404 |
| 2 | Authelia | 5 fail: c login portal blank (title ""); f no links; h 25 MB POST -> 502 "Proxy error: write EPIPE"; j nothing to move; k CSP refuses base-uri + inline styles, 403 |
| 3 | Mixpost | 6 fail: c/d/e redirect leaves proxy (Location untouched) so port.js never loads; f; i (port.js quirk); j |
| 4 | Alexandrie | 3 fail: e login (form filler left Username empty — test issue); j inline `<a>`; k 11×404 |
| 5 | memos | 1 fail: g SSE 404 — harness probes /api/sse, app uses /api/v1/sse (not proven) |
| 6 | OpenCut | start-up fail: vite preview "Failed to listen at ::" (@cloudflare/vite-plugin inspector port, no IPv6) |
| 7 | open-notebook | 2 fail: i (port.js quirk); j inline `<a>` not recorded |
| 8 | Sendportal | 8 fail: c/d/e/f/j redirect leaves proxy; e/k sign-up 500 "An email must have a From or a Sender header" (app config); h/i port.js quirk |
| 9 | Ghost | 2 fail: k admin API 403 NoPermissionError "You are not authorised" (×24) only via proxy; i port.js quirk |
| 10 | Actual | 3 fail: e app rate limit "Too many requests" (also direct); i 429 pages; j move not restored after reload |
| 11 | Gitea | 5 fail: g wrong SSE path in test (404 direct too); h/i port.js quirk; j inline `<a>`; k 404 |
| 12 | Supabase | 2 fail: d/i raw checks lacked basic auth (test issue); edge-functions + pooler blocked by sandbox |
| 13 | Appsmith | unusable: 502 "FAIL: Unsupported Content-Encoding: zstd" (proxy__100_adapter.js:206/222) |
| 14 | Payload | start-up fail: ERR_PNPM_IGNORED_BUILDS (template compose uses pnpm@latest = 12; pnpm.onlyBuiltDependencies ignored) |
| 15 | Zulip | cannot start in sandbox: supervisord minfds 40000 > sandbox max 20000 |
| 16 | Illa Builder | 3 fail: c/d `/` 301 to direct origin leaves proxy; k 400 + pageerror |
| 17 | Medusa | start-up fail: db:migrate times out after 10 s (modules-sdk/src/medusa-app.ts:72) though DB reachable |
| 18 | Meilisearch | 4 fail, none proxy-caused (no login form, no links, port.js quirk) |
| 19 | Indico | unusable: "Invalid URL — can only be accessed via http://127.0.0.1:9219" (proxy rewrites Host) |
| 20 | Jitsi Meet | calls cannot connect: /xmpp-websocket opens direct, never via proxy (no upgrade handler) |
| 21 | OpenCloud | cannot run behind proxy: idp "invalid iss value, URL must start with https://"; proxy is HTTP-only (line 377) |
| 22 | ntfy | g SSE: direct first event in 96 ms, via proxy nothing in 6 s (proxy buffers body); j inline `<a>`; h/i quirk |
| 23 | draw.io desktop | not a web app (Electron, file:// windows) — nothing to proxy |
| 24 | Continue | not a web app (IDE extension / CLI) — nothing to proxy |

"port.js quirk": checks h/i compare the proxy's `/_cp/port.js` against the app's own 404 for that path, so they fail regardless of the proxy.
