# 005-sendportal — deploy bundle FAIL

Built: 2026-09-23T06:50:50.528Z

Bundle: `dist/005-sendportal/` · zip: `dist/005-sendportal.zip` (0.2 MB)

> SendPortal built from the received source (Apache + PHP 8.4, composer --no-dev, core assets published) with PostgreSQL 16; APP_KEY generated once per deployment into the storage volume unless set.

Verification: containers up in 287s; 14/16 checks not failing

Same Playwright checks, run against the containers (proxy container → app container):

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /login, title "SendPortal" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=plain, app id=005-sendportal |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 3 buttons/links on the page |
| C6 | Drag-move a link | FAIL | <a> "Forgot Your Password?" (display:inline): mover recorded translate="120px 60px" (saved=true); on screen it moved 0,0px — NOT moved visually: CSS translate has no effect on display:inline elements |
| C7 | Drag-move a button | PASS | <button> "Login" (display:inline-block): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 2 (120px 60px, 120px 60px) |
| C9 | Rename an app control (double-click) | PASS | "Login" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | WARN | ReferenceError: $ is not defined (no direct-to-app baseline in deploy mode — may be the app's own) |
| C13 | App CSP lets the Port run | PASS | no CSP header |
| C14 | Redirects and links stay on the proxy | FAIL | GET / → 302 Location: http://sendportal/login (bypasses the proxy); 10 link/form/asset URL(s) in the page point at <upstream> (internal service hostname) |
| C15 | Customer skins through the proxy | PASS | acme→skin-002 "skin-002": applied (13 rules); globex→skin-003 "skin-003": applied (13 rules); initech→skin-004 "skin-004": applied (13 rules); umbrella→skin-005 "skin-005": applied (13 rules); no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |
