# 012-opencut — WARN

Run: 2026-09-23T06:16:25.626Z

> OpenCut web (apps/web: TanStack Start + Vite, built for Cloudflare Workers). Installed and built with Bun (the repo's package manager: bunfig.toml; npm install crashes on this tree), served with vite preview, which runs the built worker locally in workerd via @cloudflare/vite-plugin. apps/api (Worker) and apps/desktop (Rust) are not browser UIs.

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | no bundle shipped; canonical files used |
| 1 | build | PASS | 2 step(s), 11s |
| 2 | serve | PASS | cd apps/web && exec ~/.bun/bin/bun x vite preview --port $PORT --host 127.0.0.1 --strictPort → http://127.0.0.1:38903 |
| 3 | proxy | PASS | http://127.0.0.1:42043 → http://127.0.0.1:38903 |
| 4 | playwright | PASS | 16/16 not failing |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /, title "OpenCut rewrite \| beta.opencut.app" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=plain, app id=012-opencut |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | WARN | 0 buttons/links on the page |
| C6 | Drag-move a link | SKIP | no links on this page |
| C7 | Drag-move a button | SKIP | no buttons on this page |
| C8 | Move survives reload | SKIP | nothing moved |
| C9 | Rename an app control (double-click) | SKIP | no hittable control |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | no CSP header |
| C14 | Redirects and links stay on the proxy | PASS | no redirects; no upstream URLs in page |
| C15 | Customer skins through the proxy | PASS | acme→skin-002 "Minimal Neutral": applied (13 rules); globex→skin-003 "Bold Contrast": applied (13 rules); initech→skin-004 "Warm Editorial": applied (13 rules); umbrella→skin-005 "Soft Rounded": applied (13 rules); no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).
