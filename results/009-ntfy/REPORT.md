# 009-ntfy — FAIL

Run: 2026-09-23T06:14:45.179Z

> ntfy (Go server + React/Vite web app). Built as its Makefile does: make web (npm ci + vite build into server/site), placeholder docs (make cli-deps-static-sites), go build. Served with ntfy serve.

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | no bundle shipped; canonical files used |
| 1 | build | PASS | 3 step(s), 21s |
| 2 | serve | PASS | exec "$DATA/ntfy" serve --listen-http 127.0.0.1:$PORT --cache-file "$DATA/cache.db" → http://127.0.0.1:33343 |
| 3 | proxy | PASS | http://127.0.0.1:46575 → http://127.0.0.1:33343 |
| 4 | playwright | FAIL | 15/16 not failing |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /, title "ntfy" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=plain, app id=009-ntfy |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 13 buttons/links on the page |
| C6 | Drag-move a link | FAIL | <a> "website" (display:inline): mover recorded translate="120px 60px" (saved=true); on screen it moved 0,0px — NOT moved visually: CSS translate has no effect on display:inline elements |
| C7 | Drag-move a button | PASS | <div> "All notifications" (display:flex): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 2 (120px 60px, 120px 60px) |
| C9 | Rename an app control (double-click) | PASS | "website" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | no CSP header |
| C14 | Redirects and links stay on the proxy | PASS | no redirects; no upstream URLs in page |
| C15 | Customer skins through the proxy | PASS | acme→skin-002 "Minimal Neutral": applied (13 rules); globex→skin-003 "Bold Contrast": applied (13 rules); initech→skin-004 "Warm Editorial": applied (13 rules); umbrella→skin-005 "Soft Rounded": applied (13 rules); no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).
