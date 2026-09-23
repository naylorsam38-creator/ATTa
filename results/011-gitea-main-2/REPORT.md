# 011-gitea-main-2 — WARN

Run: 2026-09-23T06:07:52.107Z

> Gitea built from the received source the way its Makefile does (make build: frontend + backend, TAGS=bindata sqlite sqlite_unlock_notify). Runs as an unprivileged user (Gitea refuses root) with SQLite, install locked, admin atta-admin created via the gitea CLI (start.sh).

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | no bundle shipped; canonical files used |
| 1 | build | PASS | 2 step(s), 4s |
| 2 | serve | PASS | exec ../start.sh "$DATA" $PORT → http://127.0.0.1:43431 |
| 3 | proxy | PASS | http://127.0.0.1:33731 → http://127.0.0.1:43431 |
| 4 | playwright | PASS | 16/16 not failing |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /, title "Gitea" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=jquery, app id=011-gitea-main-2 |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 46 buttons/links on the page |
| C6 | Drag-move a link | PASS | <a> "Home" (display:flex): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C7 | Drag-move a button | SKIP | 3 on page, none hittable in the viewport |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 1 (120px 60px) |
| C9 | Rename an app control (double-click) | PASS | "" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | no CSP header |
| C14 | Redirects and links stay on the proxy | PASS | no redirects; no upstream URLs in page |
| C15 | Customer skins through the proxy | PASS | acme→skin-002 "Minimal Neutral": applied (13 rules); globex→skin-003 "Bold Contrast": applied (13 rules); initech→skin-004 "Warm Editorial": applied (13 rules); umbrella→skin-005 "Soft Rounded": applied (13 rules); no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).
