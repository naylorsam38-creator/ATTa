# 014-open-notebook — FAIL

Run: 2026-09-23T06:10:41.902Z

> open-notebook: FastAPI API + background worker (uv) + Next.js frontend (standalone build), with SurrealDB v2 in the official container (in-memory for tests). Started as its supervisord does (start.sh); the browser only talks to the Next.js server, which forwards /api to the API on 5055.

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | no bundle shipped; canonical files used |
| 1 | build | PASS | 4 step(s), 22s |
| 2 | serve | PASS | PATH=$HOME/.local/bin:$PATH exec ../start.sh "$DATA" $PORT → http://127.0.0.1:37643 |
| 3 | proxy | PASS | http://127.0.0.1:33989 → http://127.0.0.1:37643 |
| 4 | playwright | FAIL | 15/16 not failing |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /notebooks, title "Open Notebook" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=plain, app id=014-open-notebook |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 27 buttons/links on the page |
| C6 | Drag-move a link | SKIP | 9 on page, none hittable in the viewport |
| C7 | Drag-move a button | PASS | <button> "" (display:flex): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 1 (120px 60px) |
| C9 | Rename an app control (double-click) | PASS | "" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | no CSP header |
| C14 | Redirects and links stay on the proxy | PASS | GET / → 307 /notebooks |
| C15 | Customer skins through the proxy | FAIL | acme→skin-002 "Minimal Neutral": NOT applied (link=false, rules=0); globex→skin-003 "Bold Contrast": NOT applied (link=false, rules=0); initech→skin-004 "Warm Editorial": NOT applied (link=false, rules=0); umbrella→skin-005 "Soft Rounded": NOT applied (link=false, rules=0); no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).
