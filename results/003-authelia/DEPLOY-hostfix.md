# 003-authelia — deploy bundle FAIL (what-if: hostfix proxy)

> What-if run: same bundle, but the capability proxy is `proposals/proxy.host-preserving.js` instead of ui-bridge/proxy.js. Not what ships.

Built: 2026-09-23T05:43:54.997Z

Bundle: `dist/003-authelia-hostfix/` · zip: `dist/003-authelia-hostfix.zip` (14.5 MB)

> Authelia built from the received source (web build + api copy + go build, as authelia-scripts does) into an Alpine image; config templated from environment (cookie domain, public URL, secrets). Local verification uses cookie domain 127.0.0.1.

Verification: containers up in 132s; 15/16 checks not failing

Same Playwright checks, run against the containers (proxy container → app container):

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /, title "Login - Authelia" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=plain, app id=003-authelia |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 6 buttons/links on the page |
| C6 | Drag-move a link | PASS | <a> "Powered by Authelia" (display:block): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C7 | Drag-move a button | PASS | <button> "Toggle password visibility" (display:block): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,74px, visible and clickable at the new spot |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 2 (120px 60px, 120px 60px) |
| C9 | Rename an app control (double-click) | PASS | "" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | FAIL | 14 inline-style refusal(s) (style-src has no 'unsafe-inline'): the Port's stylesheet is blocked, so its button renders as a bare '+' at the end of the page and the drawer does not overlay — works programmatically, not usable by a person |
| C14 | Redirects and links stay on the proxy | PASS | no redirects; no upstream URLs in page |
| C15 | Customer skins through the proxy | PASS | acme→midnight: applied; globex→sunrise: applied; initech→blueprint: applied; no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |
