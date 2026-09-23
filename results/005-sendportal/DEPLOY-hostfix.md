# 005-sendportal — deploy bundle PASS (what-if: hostfix proxy)

> What-if run: same bundle, but the capability proxy is `proposals/proxy.host-preserving.js` instead of ui-bridge/proxy.js. Not what ships.

Built: 2026-09-23T05:49:18.433Z

Bundle: `dist/005-sendportal-hostfix/` · zip: `dist/005-sendportal-hostfix.zip` (0.2 MB)

> SendPortal built from the received source (Apache + PHP 8.4, composer --no-dev, core assets published) with PostgreSQL 16; APP_KEY generated once per deployment into the storage volume unless set.

Verification: containers up in 310s; 16/16 checks not failing

Same Playwright checks, run against the containers (proxy container → app container):

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
| C14 | Redirects and links stay on the proxy | PASS | GET / → 302 http://127.0.0.1:35871/login |
| C15 | Customer skins through the proxy | PASS | acme→midnight: applied; globex→sunrise: applied; initech→blueprint: applied; no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |
