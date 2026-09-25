# ATTa v117 — where it's at, what's left

Date: 2026-09-25. No ATTa script has been changed yet. Your uploaded ATTa-v117-9ee9953634ce.zip has the
same 04-deployment code as the one tested, so everything below applies to it.

## Done
- ATTa v117 installed on a real Ubuntu 24.04 systemd server with its own `bash run`. It passed its own gate:
  DEPLOYMENT VERIFIED, all services active.
- Apps uploaded through ATTa's real login and upload pages, and every result recorded (evidence/ folder).
- deployd/ADM tested. It correctly refused broken bundles and rolled itself back automatically when a bad
  release broke after the switch (ROLLED_BACK in 4 seconds, all services back up).

## Your scripts that break (not fixed yet)
| # | What breaks | Script(s) | Effect |
|---|---|---|---|
| 1 | Retry rule for git network errors never matches git's two-line error | known_fixes.py:42, failure_keys.py:46, pipeline.py:213/245 | One unreachable seed repo fails EVERY upload |
| 2 | Skin installer copy hits the pipeline service's RestrictSUIDSGID setting (the data folder is setgid) | install_all.py:172 (skins zip), bootstrap.sh:237/278/319, bootstrap-lib.sh:107 | On a server, EVERY app upload fails at INSTALLING_UI_CAPABILITY |
| 3 | A restart in the middle of a repair leaves that repair stuck forever; the app container keeps running | maintenance.py (sweep only covers handoff), pipeline startup | Stuck builds, leaked containers |
| 4 | install_browser downloads Chromium but not its system libraries, and installs Playwright unpinned | repair_actions.py:381-393 | Browser check can't pass on a fresh laptop |
| 5 | A bundle test always fails against the release manifest | tests/test_v116_hardening.py:1067 | `deployctl deploy` rejects every real release zip; ATTa can't update itself |
| 6 | Re-uploading a changed app is ignored and the old version kept | pipeline.py:295-304 (ingest_upload) | New versions never deploy |
| 7 | Running `bash run local` as root treats the laptop as a server | proxy_launch.py:50 | Skin proxy never starts |
| 8 | Seed list copied back in on every local start | local_launcher.py | "Delete upstream_apps.json" doesn't stick |

## What's left
1. Fix the scripts above in ATTa (no changes to any app).
2. Rebuild the ATTa zip with the fixes.
3. Run your two apps through it unchanged: open-notebook-main.zip and app-factory-controller.zip.
   Neither has been run through ATTa yet.
4. Show you exactly what happens, fix whatever breaks next in the scripts, repeat until stable.
5. Push the fixed scripts to branch claude/atta-v117-validation-xw4i86 (draft PR #8).

## In this zip
- STATUS.md: this file
- REPORT.md: the detailed write-up with file and line references
- evidence/: ATTa's own records (builds, repair chains, alerts, deployd journals) captured at each step
- harness/, testbed/, apps/: the scripts and test apps used
