# ATTa v117.4 — where it's at

Deliverable: `ATTa-v117.4-4f767fc798fc.zip`, your v117 with the script fixes below, built by ATTa's own
`tools/make_release.py`. Install it the usual way: `bash run` on a new server, or `sudo deployctl deploy <zip>` on
a running one. No app was changed.

## Proven on a real server (Ubuntu 24.04, systemd, Docker, ATTa server mode)
- `deployctl deploy` accepted each fixed release through its own test gate: v117 → v117.1 → v117.2 → v117.3 →
  v117.4, all `DEPLOYED`, all services active. v117 itself was always rejected at that gate.
- Your apps, uploaded unchanged through the web page:

| App | v117 | v117.4 |
|---|---|---|
| open-notebook-main.zip | never reached (every upload failed at the skin install) | **PASS**, all six stages incl. real browser (screenshot saved) |
| app-factory-controller.zip | FAILED "has no app code" | **PASS** as a Python package (build-readiness check) |

- Also passing on the same server: static site, FastAPI, Node, open-notebook (compose).
- Test suite: 360 tests pass (14 new for these fixes); v117 had 346.

## What was fixed (file → problem)
| # | File(s) | Problem in v117 | Fix |
|---|---|---|---|
| 1 | known_fixes.py, failure_keys.py, pipeline.py | git's two-line error never matched the retry rule; one unreachable seed repo failed every upload | patterns span lines; certificate/not-found seeds are skipped and recorded |
| 2 | skins `install_all.py`, proxy_launch.py, app_runner.py, new safe_copy.py | folder copies re-applied setgid, which the services' RestrictSUIDSGID refuses → every server upload failed | copies never chmod setgid; an old installed skins package is refreshed |
| 3 | proxy_launch.py | proxy folders chmod 2770 → RUNNER_EXCEPTION on every app | 0770 (group already correct) |
| 4 | app_runner.py | server files are 0640, Dockerfile COPY kept that → nginx 403 | images build from a copy with normal modes; library stays locked |
| 5 | maintenance.py, pipeline.py | restart mid-repair left it stuck forever and containers running | pipeline start resumes the repair and stops orphaned containers |
| 6 | repair_actions.py, known_fixes.py | browser repair skipped OS libraries, unpinned version; BROWSER_EXCEPTION had no rule | pinned, installs libraries (root) or names the exact command, launch-checked; rule added |
| 7 | tests/test_v116_hardening.py | bundle test always failed on the release manifest → ATTa could not update itself | test zip carries a matching manifest |
| 8 | pipeline.py, app_runner.py | owner's re-upload ignored, old code shipped; other users' same-name upload silently dropped | owner's upload updates and rebuilds; others get a clear refusal |
| 9 | app_classifier.py, skins `install_all.py`, intake.py | plain Python projects not recognised / treated as web apps | recognised; libraries qualify as packages |
| 10 | pipeline.py | an app the gate never ran could hide in a QUALIFIED build | reported as NOT CHECKED; build not fully qualified |
| 11 | proxy_launch.py, local_launcher.py | `bash run local` as root behaved like a server | stays a laptop instance |
| 12 | local_launcher.py, bootstrap.sh | deleted seed list came back on every start | installed once |

## Files
- `fixes/0001…0004-*.patch`: the exact code changes, applied on top of your v117 (commit 23c1835)
- `REPORT.md`, `evidence/`: the original investigation
