# 001-alexandrie — deploy bundle FAIL

Built: 2026-09-23T04:29:15.841Z

Bundle: `dist/001-alexandrie/` · zip: `dist/001-alexandrie.zip` (22.3 MB)

> Full Alexandrie stack (frontend + Go backend + MySQL + RustFS) built from the received source with Alexandrie's own Dockerfiles; the capability proxy is the only public web entrypoint (backend API and CDN are also public because the browser calls them directly).

Verification: containers up in 72s; 15/16 checks not failing

Same Playwright checks, run against the containers (proxy container → app container):

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /, title "Alexandrie" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=vue, app id=001-alexandrie |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 45 buttons/links on the page |
| C6 | Drag-move a link | PASS | <a> "Get Started" (display:block): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C7 | Drag-move a button | FAIL | <button> "01 Capture Ideas Instantly Quick notes &" (display:flex): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,36px — NOT moved visually |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 2 (120px 60px, 120px 60px) |
| C9 | Rename an app control (double-click) | PASS | "Get Started" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | no CSP header |
| C14 | Redirects and links stay on the proxy | PASS | no redirects; no upstream URLs in page |
| C15 | Customer skins through the proxy | PASS | acme→midnight: applied; globex→sunrise: applied; initech→blueprint: applied; no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |
