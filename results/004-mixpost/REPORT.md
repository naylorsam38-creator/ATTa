# 004-mixpost — FAIL

Run: 2026-09-23T04:07:37.873Z

> Mixpost Lite is a Laravel package, not an app. Frontend (Vue/Inertia/Vite) rebuilt from source, then installed into a fresh Laravel 12 host app (path repository, SQLite). The host gets a test-only /login route that logs in a seeded user (host-login-shim.php), so the checks run on the real Mixpost dashboard.

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | bundled proxy.js, port.js, mover.mjs match canonical |
| 1 | build | PASS | 6 step(s), 85s |
| 2 | serve | PASS | cd "$DATA/host" && exec php artisan serve --host=127.0.0.1 --port=$PORT → http://127.0.0.1:34007 |
| 3 | proxy | PASS | http://127.0.0.1:42321 → http://127.0.0.1:34007 |
| 4 | playwright | FAIL | 14/16 not failing |
| 5 | video | PASS | 14 steps → demo.mp4, demo.webm |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /mixpost, title "Dashboard - Mixpost - Laravel" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=plain, app id=004-mixpost |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 20 buttons/links on the page |
| C6 | Drag-move a link | FAIL | <a> "" (display:inline): mover recorded translate="120px 60px" (saved=true); on screen it moved 0,0px — NOT moved visually: CSS translate has no effect on display:inline elements |
| C7 | Drag-move a button | PASS | <div> "Unlock Pro Features" (display:block): mover recorded translate="120px -60px" (saved=true); on screen it moved 120,-60px, visible and clickable at the new spot |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 2 (120px 60px, 120px -60px) |
| C9 | Rename an app control (double-click) | PASS | "" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | no CSP header |
| C14 | Redirects and links stay on the proxy | FAIL | 15 link/form/asset URL(s) in the page point at <upstream> |
| C15 | Customer skins through the proxy | PASS | acme→midnight: applied; globex→sunrise: applied; initech→blueprint: applied; no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).

## Demo video

[`demo.mp4`](demo.mp4) · [`demo.webm`](demo.webm)

1. the app, served through ui-bridge/proxy.js
2. Capability Port (injected before </head>) — attach the button mover
3. button-mover attached in the drawer slot
4. Move: on — every button and link is now draggable
5. button "Unlock Pro Features" moved
6. link "" moved
7. "" renamed to "Renamed by ATTa"
8. sticky-notes capability attached — pin a note
9. note pinned on the live app and dragged into place
10. customer "acme" → skin "midnight" (moves, names and notes kept)
11. customer "globex" → skin "sunrise" (moves, names and notes kept)
12. customer "initech" → skin "blueprint" (moves, names and notes kept)
13. no customer header → original look
14. Reset + Clear — app back exactly as it was
