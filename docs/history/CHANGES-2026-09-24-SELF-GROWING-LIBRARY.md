# v108 — The library grows by itself (2026-09-24)

Goal: the system is not governed by a fixed list. Any app added to it is picked up, skinned,
checked and (once it passes) sent to Coolify, without anyone editing a mapping first.

## What no longer gates anything
- `upstream_apps.json` — now seed repos only. Missing file, missing repo: nothing breaks.
- `APP_MAP` — kept as hand-made choices; any app it doesn't mention is mapped automatically.
- `required_real_categories` — now a coverage note on the build, not a failure.
- One broken app — no longer holds back the others (per-app qualification).

## New
- **Add an app from the browser.** "Add app" page: upload an app's source zip, or paste a git
  address. The app goes into `library/<name>` as a git snapshot (`gitea-main.zip` → `gitea`).
  Uploading the ATTa bundle still updates the system. An app already in the library is kept
  (the library copy is where repairs live).
- **Apps can be added before any bundle upload**: the skins package ships with the deployment.
- `app_classifier.py` — finds an app's code root up to three folders down (Agno is in
  `libs/agno`, ArchiveBox's zip is in `ArchiveBox-dev/`), and picks a skin category from the
  app's own README/package description, scored against the registry. Always a real registry
  category; nothing matched → the generic `dashboard` skin, marked low confidence.
- **Library page** (`/library`): every app, its skin, who decided it, its profile, code root.
- **PARTIALLY_QUALIFIED**: apps that passed go to Coolify; the rest are healed or reported.
- **PACKAGE_INSTALLED**: a bundle upload with no apps yet is a success, not a failure.

## Changed
- `catalogue_sync.py` (schema v2) — never leaves an app unmapped. Order: APP_MAP → value already
  in the catalogue (your edits stick) → seed category that equals a registry name → the app's
  own words. Low-confidence picks are listed under "worth a look"; they block nothing.
- `intake.py` (ruleset 4) — rules run on the code root. New `profile_of()`: Go app with a
  top-level web folder → web (Grafana); Python code with no web pages and no container → package
  (Agno); .NET solution → system (Stride); container with no pages → service.
- `install_all.py` — discovers apps by code markers up to three levels down (Java, Ruby, PHP,
  .NET, plain Python too); an app with no mapping gets the default skin instead of failing;
  an empty library is fine.
- Upload now redirects to the build page (refreshing no longer uploads the file again).

## Fixed (found while testing)
- `run-ui.sh` was broken for every app whose id has a hyphen (`hello-notes="hello-notes"`:
  command not found). The template placeholder `APP_ID` was also replaced inside the variable
  name. Affected seed apps: docker-moby, super-productivity, apache-airflow, krayin-crm,
  open-design, next-ai-draw-io. Placeholders are now `__ATTA_APP_ID__` / `__ATTA_DEFAULT_PORT__`.

## Tested (local instance, real Chromium)
- No seed list present, no bundle uploaded: uploaded `gitea-main.zip` → added as `gitea`,
  auto `developer_tools` (high), web. ArchiveBox zip → `archivebox`, code root `ArchiveBox-dev`,
  auto `file_storage_and_sync` (high). Coolify zip → `devops_console` (APP_MAP).
  `https://github.com/mealie-recipes/mealie` → `recipe_and_meal_planning` (high).
- A small web app (hello-notes) running behind its proxy passed all six stages in the browser
  while gitea (not running) failed: build PARTIALLY_QUALIFIED, only hello-notes sent to Coolify.
- Installer over all 33 known app trees: 33 READY, 0 failed, every required category covered
  (before: 26 READY, 5 silently skipped, `developer_tools` missing).
- Catalogue sync rerun: byte-identical, no changelog entry.
