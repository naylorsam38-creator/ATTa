# 003-authelia — FAIL

Run: 2026-09-23T06:22:05.921Z

> Authelia (Go server + React/Vite portal). Built the way authelia-scripts does it: web build into internal/server/public_html, copy api/, go build. Minimal config (file users, SQLite, filesystem notifier) with cookie domain 127.0.0.1; unauthenticated visit shows the login portal. LEFTHOOK=0 stops web/'s lefthook postinstall writing git hooks into the enclosing ATTa repo.

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 0 | intake | PASS | bundled proxy.js, port.js, mover.mjs match canonical |
| 1 | build | PASS | 4 step(s), 77s |
| 2 | serve | PASS | cd "$DATA" && exec ./authelia --config ./configuration.yml → http://127.0.0.1:42183 |
| 3 | proxy | PASS | http://127.0.0.1:46291 → http://127.0.0.1:42183 |
| 4 | playwright | FAIL | 14/16 not failing |

## Playwright checks

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
| C13 | App CSP lets the Port run | FAIL | 14 inline-style refusal(s) (style-src has no 'unsafe-inline'): the Port's stylesheet is blocked, so its button renders as a bare '+' at the end of the page and the drawer does not overlay — works programmatically, not usable by a person; 2 other violation(s) only seen through the proxy: Refused to set the document's base URI to 'http://127.0.0.1:42183/' because it violates the following Content Security Policy directive: "base-uri 'se |
| C14 | Redirects and links stay on the proxy | FAIL | 1 link/form/asset URL(s) in the page point at <upstream> |
| C15 | Customer skins through the proxy | PASS | acme→skin-002 "Minimal Neutral": applied (13 rules); globex→skin-003 "Bold Contrast": applied (13 rules); initech→skin-004 "Warm Editorial": applied (13 rules); umbrella→skin-005 "Soft Rounded": applied (13 rules); no header → original look |
| C16 | Sticky-notes capability on the live page | PASS | pinned=true ("Pinned by ATTa×", visible=true), after reload=true, clear=true |

Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).

## Console errors only seen with the Port

- `Refused to set the document's base URI to 'http://127.0.0.1:42183/' because it violates the following Content Security Policy directive: "base-uri 'self'". `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-MxaTu3UnyL0YHo17TFkZpi0PxmWTIHow'". Either the 'unsafe-inline' keyword, a hash ('sha256-U6wSeOW2avPvSzZHV2gX7Iis6tBsMIZ4dEkjeHcpKZs='), or a nonce ('nonce-...') is required to `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-MxaTu3UnyL0YHo17TFkZpi0PxmWTIHow'". Either the 'unsafe-inline' keyword, a hash ('sha256-eiViVwsy86V7BX5KEdN6ArVYFAAhC85KOOxoj4qBKDE='), or a nonce ('nonce-...') is required to `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-MxaTu3UnyL0YHo17TFkZpi0PxmWTIHow'". Either the 'unsafe-inline' keyword, a hash ('sha256-RnPKpo3kmlxYrU1tfD8ecp2cmyiEkjMmX7Gcapmdxaw='), or a nonce ('nonce-...') is required to `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-MxaTu3UnyL0YHo17TFkZpi0PxmWTIHow'". Either the 'unsafe-inline' keyword, a hash ('sha256-DQqKKl2A7U3zsSWzqZEipFEzzcQUytbboRk/Tc+uToc='), or a nonce ('nonce-...') is required to `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-MxaTu3UnyL0YHo17TFkZpi0PxmWTIHow'". Either the 'unsafe-inline' keyword, a hash ('sha256-5VKfPBSBCI24fxCIR4srPYTw8oPeGtcOujI+MXZyOdQ='), or a nonce ('nonce-...') is required to `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-MxaTu3UnyL0YHo17TFkZpi0PxmWTIHow'". Either the 'unsafe-inline' keyword, a hash ('sha256-VxPOjCw5ObvPlb/s4Tfaydf32XZv8t+fKHM6amFuPzA='), or a nonce ('nonce-...') is required to `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-MxaTu3UnyL0YHo17TFkZpi0PxmWTIHow'". Either the 'unsafe-inline' keyword, a hash ('sha256-RtONP2gdafT0L3Uw1Cifj6SnPRMblPOeT05wVuCAie4='), or a nonce ('nonce-...') is required to `
- `Refused to set the document's base URI to 'http://127.0.0.1:42183/' because it violates the following Content Security Policy directive: "base-uri 'self'". `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-2UbD1foSgbbULPiQGYvjnMm4ILoNyPbu'". Either the 'unsafe-inline' keyword, a hash ('sha256-U6wSeOW2avPvSzZHV2gX7Iis6tBsMIZ4dEkjeHcpKZs='), or a nonce ('nonce-...') is required to `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-2UbD1foSgbbULPiQGYvjnMm4ILoNyPbu'". Either the 'unsafe-inline' keyword, a hash ('sha256-eiViVwsy86V7BX5KEdN6ArVYFAAhC85KOOxoj4qBKDE='), or a nonce ('nonce-...') is required to `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-2UbD1foSgbbULPiQGYvjnMm4ILoNyPbu'". Either the 'unsafe-inline' keyword, a hash ('sha256-RnPKpo3kmlxYrU1tfD8ecp2cmyiEkjMmX7Gcapmdxaw='), or a nonce ('nonce-...') is required to `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-2UbD1foSgbbULPiQGYvjnMm4ILoNyPbu'". Either the 'unsafe-inline' keyword, a hash ('sha256-DQqKKl2A7U3zsSWzqZEipFEzzcQUytbboRk/Tc+uToc='), or a nonce ('nonce-...') is required to `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-2UbD1foSgbbULPiQGYvjnMm4ILoNyPbu'". Either the 'unsafe-inline' keyword, a hash ('sha256-5VKfPBSBCI24fxCIR4srPYTw8oPeGtcOujI+MXZyOdQ='), or a nonce ('nonce-...') is required to `
- `Refused to apply inline style because it violates the following Content Security Policy directive: "style-src 'self' 'nonce-2UbD1foSgbbULPiQGYvjnMm4ILoNyPbu'". Either the 'unsafe-inline' keyword, a hash ('sha256-VxPOjCw5ObvPlb/s4Tfaydf32XZv8t+fKHM6amFuPzA='), or a nonce ('nonce-...') is required to `
