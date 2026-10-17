# 014-open-notebook — deploy bundle PASS

Built: 2026-09-23T07:09:28.271Z

Bundle: `dist/014-open-notebook/` · zip: `dist/014-open-notebook.zip` (2.8 MB)

> open-notebook built from the received source with its own Dockerfile (target runtime: API + worker + Next.js), SurrealDB v2 with a persistent volume.

Verification: containers up in 128s; 16/16 checks not failing

Same Playwright checks, run against the containers (proxy container → app container):

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /notebooks, title "Open Notebook" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=plain, app id=014-open-notebook |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 3 buttons/links on the page |
| C6 | Drag-move a link | PASS | <a> "Open Notebook Documentation" (display:inline-flex): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C7 | Drag-move a button | PASS | <button> "Show Technical Details" (display:inline-flex): mover recorded translate="120px 60px" (saved=true); on screen it moved 120,60px, visible and clickable at the new spot |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 2 (120px 60px, 120px 60px) |
| C9 | Rename an app control (double-click) | PASS | "Open Notebook Documentation" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | no CSP header |
| C14 | Redirects and links stay on the proxy | PASS | GET / → 307 /notebooks |
| C15 | Customer skins through the proxy | PASS | acme→skin-002 "skin-002": applied (13 rules); globex→skin-003 "skin-003": applied (13 rules); initech→skin-004 "skin-004": applied (13 rules); umbrella→skin-005 "skin-005": applied (13 rules); no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |
