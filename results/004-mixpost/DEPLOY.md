# 004-mixpost — deploy bundle FAIL

Built: 2026-09-23T05:09:59.827Z

Bundle: `dist/004-mixpost/` · zip: `dist/004-mixpost.zip` (2.0 MB)

> Mixpost Lite: frontend rebuilt from the received source, installed into a Laravel 12 host app (Apache + PHP 8.4) with MySQL 8 and a minimal email/password login (admin from MIXPOST_ADMIN_EMAIL / MIXPOST_ADMIN_PASSWORD). The test pipeline's auto-login shim is NOT part of this bundle.

Verification: containers up in 226s; 15/16 checks not failing

Same Playwright checks, run against the containers (proxy container → app container):

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /login, title "Sign in · Mixpost" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=plain, app id=004-mixpost |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 1 buttons/links on the page |
| C6 | Drag-move a link | SKIP | no links on this page |
| C7 | Drag-move a button | PASS | <button> "Sign in" (display:inline-block): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 1 (120px 60px) |
| C9 | Rename an app control (double-click) | PASS | "Sign in" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | no CSP header |
| C14 | Redirects and links stay on the proxy | FAIL | GET / → 302 Location: http://mixpost/mixpost (bypasses the proxy) |
| C15 | Customer skins through the proxy | PASS | acme→midnight: applied; globex→sunrise: applied; initech→blueprint: applied; no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |
