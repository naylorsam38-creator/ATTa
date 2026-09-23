# 008-sendportal-master3 — FAIL

Run: 2026-09-23T06:30:29.997Z

> SendPortal master3 re-pack (same source as 005; out/ bundle now inside the app folder). SendPortal (Laravel 11 + sendportal-core). Dev-only roave/security-advisories removed from composer.json because it blocks every Laravel 11 release; installed --no-dev. SQLite instead of MySQL, core assets published, migrations run; unauthenticated visit lands on /login. Checks open /login directly because / redirects to the upstream origin (see C13).

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | bundled proxy.js, port.js, mover.mjs match canonical |
| 1 | build | PASS | 7 step(s), 9s |
| 2 | serve | PASS | exec php artisan serve --host=127.0.0.1 --port=$PORT → http://127.0.0.1:33335 |
| 3 | proxy | PASS | http://127.0.0.1:45761 → http://127.0.0.1:33335 |
| 4 | playwright | FAIL | 15/16 not failing |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /login, title "SendPortal" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=jquery, app id=008-sendportal-master3 |
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
| C15 | Customer skins through the proxy | PASS | acme→skin-002 "Minimal Neutral": applied (13 rules); globex→skin-003 "Bold Contrast": applied (13 rules); initech→skin-004 "Warm Editorial": applied (13 rules); umbrella→skin-005 "Soft Rounded": applied (13 rules); no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).
