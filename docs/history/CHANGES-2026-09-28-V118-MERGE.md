# v118 — merge of v111a, v111a-ADM, v114.1 and v117 against the locked goal (2026-09-28)

The four bundles are one line of development (100 → 111a → 111a-ADM → 114.1 → 117), each built on the
last. v117 carries everything from the others, so it is the base. What changed, and why.

## Taken as-is from v117 (and through it from 111a, ADM, 114.1)
App runner (starts each app in Docker, learns recipes), self-growing library and catalogue, intake,
six-stage watcher with real-browser stage 6 and evidence, per-app qualification, repair loop with
failure keys and rule lifecycle, ADM system updates with rollback, v114 security hardening, v114.1
installer fixes, v114.2 script probe, v117 container image + Coolify service for ATTa itself + one
Coolify service per qualified app. All 76 existing tests pass unchanged.

## Left out
- The loose files at the top of the 114.1 zip (`04-deployment/*.py`, `bootstrap.sh`, `aws-cloud-init.yaml`,
  `deployd/atta.service`, `scripts/`). Outside `ATTa/`, never loaded; `repair.py` "fixed" apps by writing a
  made-up `styles.css`, and `rollback.py` was a placeholder comment.
- `05-coolify/kit/scripts/shellcheck_py-*.whl` (3.8 MB). Nothing referenced it; `make lint` uses shellcheck.

## Changed
1. **Catalogue guarantee (new `04-deployment/user_catalogue.py`).** Users see an app only when its LATEST
   check passed all six stages (the pipeline's own rule: nothing broken, stage 6 CLEAN = OK), it is live in
   Coolify with an address, and it has a Front Door category. Checked fresh on every request, so an app that
   fails a later check disappears at once and comes back when it passes. No "coming soon", no placeholders.
   - `GET /api/front-door/library` returns only that (no failures, no reasons).
   - `/library`: testers see only qualified, live apps; admins also see what users see, and every app kept
     off with the reason, above the full library table.
   - Front Door: after "Yes", it shows the qualified apps in that category to pick from (one = opens
     straight away), or says plainly that nothing there has passed yet. The old sample page never shows.
     Reopening a saved app only works while that app still qualifies.
   - Front Door categories come from the seed list's categories; seven spellings differ from openapps.pro
     (`ALIASES`). A hand-set `frontdoor_categories` on an app's catalogue entry wins and survives syncs.
2. **Never guess a repository.** `pipeline.library()` clones only seed entries marked `VERIFIED`. v117 would
   have tried `git clone https://github.com/OPENREEL-NEEDS-EXACT-REPO.git` and the Zen Browser placeholder;
   `VERIFIED_CANDIDATE` (Next AI Draw.io, FormBee) now waits for a person too. catalogue_sync.py takes no
   repo from an unverified entry either.
3. **Start gates restored.** v114.2 removed the memory and disk start gates and the download queue. Under
   v117, ATTa shares one machine with Coolify and every live app, and v114.1 recorded 31 simultaneous
   downloads filling a 20 GB disk. Gates are back; the v114.2 default stays (0 = every app queued at once,
   each starts when there's room).
4. **Live apps' images protected.** The runner's "disk full" fix and second pass ran `docker image prune -a`,
   which deletes every image not running at that moment, including the `atta-qualified/...` images live
   Coolify apps restart from. Now dangling images only.
5. **Stage 1: domain + HTTPS under Coolify.** `deploy-atta.sh --domain atta.example.com` writes a route into
   Coolify's own Traefik (`/data/coolify/proxy/dynamic/atta.yaml`: Let's Encrypt, HTTP→HTTPS) to ATTa on
   the host, then fails loudly unless `https://<domain>/health` answers with a valid certificate. Checked
   against the supplied Coolify 4.3.23 source (`bootstrap/helpers/proxy.php`: file provider directory,
   `letsencrypt` resolver, `host.docker.internal:host-gateway`). Login cookies get `Secure` behind it.
6. **Self-heal model:** `claude-opus-5` (not a model id) → `claude-opus-5-5`.
7. Image tag `atta:v118`.

## What proved it (here)
- `tests/test_v118_merge.py`, 9 new tests, 85 in all, pass: only qualified + live apps shown; failed, browser-
  not-passed, not-live and blocked apps kept off with reasons; an app drops out and returns with its latest
  check; every seed category reaches a Front Door category; placeholders and candidates are never cloned
  (clone attempt = test failure); gates and prune rule; model id; the Traefik route renders valid YAML.
- Real gateway + real Chromium on scratch data (two stand-in pages as the "live" addresses): the tester's
  Library listed only the two qualified apps, the failed one never appeared; the Front Door offered both,
  opened the picked one; a category whose only app failed got the plain "nothing has passed yet" line.

## NOT proved here
- `docker build` of the image: Docker Hub is blocked in this workspace. First thing to run on the server.
- The HTTPS route on a real Coolify, the full catalogue run, Coolify provisioning of qualified apps.
  Those are the server's run (RUNBOOK: deploy-atta.sh --domain).

## Still open (stage 3 of the goal; not in any version yet)
- Visual editor: moving elements and buttons (button-mover.mjs exists in the package; not exposed to users).
- Adding/removing features per user through the capability port (removed = dormant).
- Per-user isolation: each qualified app is one shared Coolify service; users don't get their own copy.
- The Front Door choice doesn't start a per-user build; it opens the shared live app.
- Apps may refuse to be shown inside the Front Door's frame (X-Frame-Options); needs checking per app.
