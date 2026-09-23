# 005-sendportal — FAIL

Run: 2026-09-23T04:09:54.559Z

> SendPortal (Laravel 11 + sendportal-core). Dev-only roave/security-advisories removed from composer.json because it blocks every Laravel 11 release; installed --no-dev. SQLite instead of MySQL, core assets published, migrations run; unauthenticated visit lands on /login. Checks open /login directly because / redirects to the upstream origin (see C13).

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | bundled proxy.js, port.js, mover.mjs match canonical |
| 1 | build | PASS | 7 step(s), 18s |
| 2 | serve | PASS | exec php artisan serve --host=127.0.0.1 --port=$PORT → http://127.0.0.1:42559 |
| 3 | proxy | PASS | http://127.0.0.1:40641 → http://127.0.0.1:42559 |
| 4 | playwright | FAIL | 15/16 not failing |
| 5 | video | PASS | 14 steps → demo.mp4, demo.webm |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /login, title "SendPortal" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=jquery, app id=005-sendportal |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 3 buttons/links on the page |
| C6 | Drag-move a link | PASS | <a> "Forgot Your Password?" (display:inline-block): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C7 | Drag-move a button | PASS | <button> "Login" (display:inline-block): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 2 (120px 60px, 120px 60px) |
| C9 | Rename an app control (double-click) | PASS | "Login" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | no CSP header |
| C14 | Redirects and links stay on the proxy | FAIL | GET / → 302 Location: <upstream>/login (bypasses the proxy); 10 link/form/asset URL(s) in the page point at <upstream> |
| C15 | Customer skins through the proxy | PASS | acme→midnight: applied; globex→sunrise: applied; initech→blueprint: applied; no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).

## Demo video

[`demo.mp4`](demo.mp4) · [`demo.webm`](demo.webm)

1. the app, served through ui-bridge/proxy.js
2. Capability Port (injected before </head>) — attach the button mover
3. button-mover attached in the drawer slot
4. Move: on — every button and link is now draggable
5. button "Login" moved
6. link "Forgot Your Password?" moved
7. "Login" renamed to "Renamed by ATTa"
8. sticky-notes capability attached — pin a note
9. note pinned on the live app and dragged into place
10. customer "acme" → skin "midnight" (moves, names and notes kept)
11. customer "globex" → skin "sunrise" (moves, names and notes kept)
12. customer "initech" → skin "blueprint" (moves, names and notes kept)
13. no customer header → original look
14. Reset + Clear — app back exactly as it was

## Console errors only seen with the Port

- `Failed to load resource: net::ERR_TOO_MANY_RETRIES`
