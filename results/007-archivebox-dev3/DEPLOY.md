# 007-archivebox-dev3 — deploy bundle FAIL

Built: 2026-09-23T05:16:02.380Z

Bundle: `dist/007-archivebox-dev3/` · zip: `dist/007-archivebox-dev3.zip` (3.2 MB)

> ArchiveBox web app built from the received source in a lean python:3.13 image (UI/admin/API; archiving extractors not included), data in a named volume; the capability proxy is the only public entrypoint.

Verification: containers up in 57s; 15/16 checks not failing

Same Playwright checks, run against the containers (proxy container → app container):

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200, landed on /admin/login/?next=/, title "Set up ArchiveBox \| Admin \| ArchiveBox" |
| C2 | Proxy injects port.js immediately before </head> | PASS | tag found once, directly before </head> |
| C3 | port.js runs in the live page | PASS | v0.2.0, framework=jquery, app id=007-archivebox-dev3 |
| C4 | Mover attached into Port drawer slot | PASS | section[data-capability=button-mover] data-trust=trusted, in drawer=true, mover UI=true |
| C5 | App controls found for the mover | PASS | 2 buttons/links on the page |
| C6 | Drag-move a link | FAIL | <a> "ArchiveBox" (display:inline): mover recorded translate="120px 60px" (saved=true); on screen it moved 0,0px — NOT moved visually: CSS translate has no effect on display:inline elements |
| C7 | Drag-move a button | PASS | <input> "Create admin and continue" (display:flex): mover recorded translate="120px -60px" (saved=true); on screen it moved 120,-60px, visible and clickable at the new spot |
| C8 | Move survives reload | PASS | mover re-attached from storage; moved controls after reload: 2 (120px 60px, 120px -60px) |
| C9 | Rename an app control (double-click) | PASS | "ArchiveBox" → "ATTa renamed" |
| C10 | Reset restores positions and labels | PASS | moved left=0, renamed left=0, saved positions=0, label restored=true |
| C11 | Port button opens the drawer | PASS | panel open |
| C12 | No new uncaught errors with the Port | PASS | none (0 uncaught error(s) seen, all also raised by the app without the Port) |
| C13 | App CSP lets the Port run | PASS | CSP present, no violations added by the Port |
| C14 | Redirects and links stay on the proxy | PASS | GET / → 302 /admin/login/?next=/ |
| C15 | Customer skins through the proxy | PASS | acme→midnight: applied; globex→sunrise: applied; initech→blueprint: applied; no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |
