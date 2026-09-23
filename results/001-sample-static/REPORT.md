# 001-sample-static — PASS

Run: 2026-09-23T03:25:00.025Z

## Stages

| # | Stage | Status | Detail |
|---|---|---|---|
| 1 | build | PASS | nothing to build |
| 2 | serve | PASS | static . |
| 3 | proxy | PASS | http://127.0.0.1:35927 → http://127.0.0.1:37157 |
| 4 | playwright | PASS | 8/8 not failing |

## Playwright checks

| # | Check | Status | Detail |
|---|---|---|---|
| C1 | Page loads through proxy | PASS | HTTP 200 |
| C2 | proxy.js injects port.js before </head> | PASS | tag found directly before </head> |
| C3 | port.js runs in the live page | PASS | appId=001-sample-static |
| C4 | Mover slot is a Port <section> in the drawer body | PASS | data-trust=local, inDrawer=true, status=mounted |
| C5 | Mover numbers app buttons/links | PASS | 7 found: #1 Home, #2 About, #3 Contact, #4 Save, #5 Share, #6 Delete, #7 Late button |
| C6 | Move + reset on live DOM | PASS | #2: index 1 → 0 → reset 1 |
| C7 | Drawer opens from the CP toggle | PASS | open |
| C8 | No uncaught page errors | PASS | none |
