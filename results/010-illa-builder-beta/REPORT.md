# 010-illa-builder-beta — WARN

Run: 2026-09-23T06:06:10.539Z

> ILLA Builder frontend (pnpm/turbo monorepo, Vite). The zip ships its two git submodules (packages/illa-design, packages/illa-public-component) empty, as GitHub zips always do, so setup clones them from their upstream repos (illa-public-component at branch beta, per .gitmodules). Self-host build served as a static SPA. ILLA's backend services are separate projects and not part of this zip, so pages that need the API show their not-connected state.

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | no bundle shipped; canonical files used |
| 1 | build | PASS | 4 step(s), 90s |
| 2 | serve | PASS | static apps/builder/dist → http://127.0.0.1:42947 |
| 3 | proxy | PASS | http://127.0.0.1:34533 → http://127.0.0.1:42947 |
| 4 | playwright | PASS | 16/16 not failing |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /cloud, title "404" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=plain, app id=010-illa-builder-beta |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 2 buttons/links on the page |
| C6 | Drag-move a link | SKIP | no links on this page |
| C7 | Drag-move a button | PASS | <button> "Refresh" (display:flex): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 1 (120px 60px) |
| C9 | Rename an app control (double-click) | PASS | "Refresh" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | no CSP header |
| C14 | Redirects and links stay on the proxy | PASS | no redirects; no upstream URLs in page |
| C15 | Customer skins through the proxy | PASS | acme→skin-002 "Minimal Neutral": applied (13 rules); globex→skin-003 "Bold Contrast": applied (13 rules); initech→skin-004 "Warm Editorial": applied (13 rules); umbrella→skin-005 "Soft Rounded": applied (13 rules); no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).
