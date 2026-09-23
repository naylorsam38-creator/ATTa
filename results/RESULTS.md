# ATTa results

| # | App | Verdict | Intake | Build | Serve | Proxy | Playwright | Deploy bundle | Checks | Run |
|---|---|---|---|---|---|---|---|---|---|---|
| 001 | [001-alexandrie](001-alexandrie/REPORT.md) | **PASS** | PASS | PASS | PASS | PASS | PASS | [PASS](001-alexandrie/DEPLOY.md) | C1:P C2:P C3:P C4:P C5:P C6:P C7:P C8:P C9:P C10:P C11:P C12:P C13:P C14:P C15:P C16:P | 2026-09-23 04:31 |
| 002 | [002-archivebox](002-archivebox/REPORT.md) | **FAIL** | PASS | PASS | PASS | PASS | FAIL | [FAIL](002-archivebox/DEPLOY.md) | C1:P C2:P C3:P C4:P C5:P C6:F C7:P C8:P C9:P C10:P C11:P C12:P C13:P C14:P C15:P C16:P | 2026-09-23 04:14 |
| 003 | [003-authelia](003-authelia/REPORT.md) | **FAIL** | PASS | PASS | PASS | PASS | FAIL | [FAIL](003-authelia/DEPLOY.md) | C1:P C2:P C3:P C4:P C5:P C6:P C7:P C8:P C9:P C10:P C11:P C12:P C13:F C14:P C15:P C16:P | 2026-09-23 04:15 |
| 004 | [004-mixpost](004-mixpost/REPORT.md) | **FAIL** | PASS | PASS | PASS | PASS | FAIL | [WARN](004-mixpost/DEPLOY.md) | C1:P C2:P C3:P C4:P C5:P C6:F C7:P C8:P C9:P C10:P C11:P C12:P C13:P C14:F C15:P C16:P | 2026-09-23 04:16 |
| 005 | [005-sendportal](005-sendportal/REPORT.md) | **FAIL** | PASS | PASS | PASS | PASS | FAIL | [FAIL](005-sendportal/DEPLOY.md) | C1:P C2:P C3:P C4:P C5:P C6:P C7:P C8:P C9:P C10:P C11:P C12:P C13:P C14:F C15:P C16:P | 2026-09-23 04:19 |
| 006 | [006-app-factory-controller3](006-app-factory-controller3/REPORT.md) | **PASS** | PASS | PASS | N/A | N/A | PASS | — | T1:P | 2026-09-23 04:11 |
| 007 | [007-archivebox-dev3](007-archivebox-dev3/REPORT.md) | **FAIL** | PASS | PASS | PASS | PASS | FAIL | [FAIL](007-archivebox-dev3/DEPLOY.md) | C1:P C2:P C3:P C4:P C5:P C6:F C7:P C8:P C9:P C10:P C11:P C12:P C13:P C14:P C15:P C16:P | 2026-09-23 04:20 |
| 008 | [008-sendportal-master3](008-sendportal-master3/REPORT.md) | **FAIL** | PASS | PASS | PASS | PASS | FAIL | [FAIL](008-sendportal-master3/DEPLOY.md) | C1:P C2:P C3:P C4:P C5:P C6:P C7:P C8:P C9:P C10:P C11:P C12:P C13:P C14:F C15:P C16:P | 2026-09-23 04:22 |
| 009 | [009-ntfy](009-ntfy/REPORT.md) | **FAIL** | PASS | PASS | PASS | PASS | FAIL | — | C1:P C2:P C3:P C4:P C5:P C6:F C7:P C8:P C9:P C10:P C11:P C12:P C13:P C14:P C15:P C16:P | 2026-09-23 06:14 |
| 010 | [010-illa-builder-beta](010-illa-builder-beta/REPORT.md) | **WARN** | PASS | PASS | PASS | PASS | PASS | — | C1:P C2:P C3:P C4:P C5:P C6:S C7:P C8:P C9:P C10:P C11:P C12:P C13:P C14:P C15:P C16:P | 2026-09-23 06:06 |
| 011 | [011-gitea-main-2](011-gitea-main-2/REPORT.md) | **WARN** | PASS | PASS | PASS | PASS | PASS | — | C1:P C2:P C3:P C4:P C5:P C6:P C7:S C8:P C9:P C10:P C11:P C12:P C13:P C14:P C15:P C16:P | 2026-09-23 06:07 |
| 012 | [012-opencut](012-opencut/REPORT.md) | **WARN** | PASS | PASS | PASS | PASS | PASS | — | C1:P C2:P C3:P C4:P C5:W C6:S C7:S C8:S C9:S C10:P C11:P C12:P C13:P C14:P C15:P C16:P | 2026-09-23 06:16 |
| 013 | [013-moby](013-moby/REPORT.md) | **PASS** | PASS | PASS | N/A | N/A | PASS | — | T1:P | 2026-09-23 06:17 |
| 014 | [014-open-notebook](014-open-notebook/REPORT.md) | **FAIL** | PASS | PASS | PASS | PASS | FAIL | — | C1:P C2:P C3:P C4:P C5:P C6:S C7:P C8:P C9:P C10:P C11:P C12:P C13:P C14:P C15:F C16:P | 2026-09-23 06:10 |

Checks: C1 page loads · C2 hook tag before </head> · C3 port.js live · C4 mover attached in drawer slot · C5 app controls found · C6 drag a link · C7 drag a button · C8 move survives reload · C9 rename · C10 reset restores · C11 drawer opens · C12 no new errors with the Port · C13 app CSP lets the Port run · C14 redirects and links stay on the proxy · C15 customer skins via the proxy · C16 sticky-notes capability.
P=pass F=fail W=warn S=skip.
