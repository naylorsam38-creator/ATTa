# 002-archivebox — FAIL

Run: 2026-09-23T04:05:18.371Z

> ArchiveBox (Python/Django, served by daphne). Fresh collection initialised in data/; unauthenticated visit lands on the admin login screen.

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | bundled proxy.js, port.js, mover.mjs match canonical |
| 1 | build | PASS | 2 step(s), 6s |
| 2 | serve | PASS | cd "$DATA" && exec "$SRC/.venv/bin/archivebox" server 127.0.0.1:$PORT → http://127.0.0.1:41439 |
| 3 | proxy | PASS | http://127.0.0.1:36039 → http://127.0.0.1:41439 |
| 4 | playwright | FAIL | 14/16 not failing |
| 5 | video | PASS | 14 steps → demo.mp4, demo.webm |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /admin/login/?next=/, title "Set up ArchiveBox \| Admin \| ArchiveBox" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=jquery, app id=002-archivebox |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 2 buttons/links on the page |
| C6 | Drag-move a link | FAIL | <a> "ArchiveBox" (display:inline): mover recorded translate="120px 60px" (saved=true); on screen it moved 0,0px — NOT moved visually: CSS translate has no effect on display:inline elements |
| C7 | Drag-move a button | FAIL | <input> "Create admin and continue" (display:flex): mover recorded translate="120px -60px" (saved=true); on screen it moved 44,-25px — NOT moved visually |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 2 (120px 60px, 120px -60px) |
| C9 | Rename an app control (double-click) | PASS | "ArchiveBox" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | CSP present, no violations added by the Port |
| C14 | Redirects and links stay on the proxy | PASS | GET / → 302 /admin/login/?next=/ |
| C15 | Customer skins through the proxy | PASS | acme→midnight: applied; globex→sunrise: applied; initech→blueprint: applied; no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).

## Demo video

[`demo.mp4`](demo.mp4) · [`demo.webm`](demo.webm)

1. the app, served through ui-bridge/proxy.js
2. Capability Port (injected before </head>) — attach the button mover
3. button-mover attached in the drawer slot
4. Move: on — every button and link is now draggable
5. button "Create admin and continu" moved
6. link "ArchiveBox" moved
7. "ArchiveBox" renamed to "Renamed by ATTa"
8. sticky-notes capability attached — pin a note
9. note pinned on the live app and dragged into place
10. customer "acme" → skin "midnight" (moves, names and notes kept)
11. customer "globex" → skin "sunrise" (moves, names and notes kept)
12. customer "initech" → skin "blueprint" (moves, names and notes kept)
13. no customer header → original look
14. Reset + Clear — app back exactly as it was
