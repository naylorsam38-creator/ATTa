# v107 — App catalogue sync (2026-09-24)

Problem: an app with no `APP_MAP` entry (Gitea, ArchiveBox) failed the whole install step, and
Gitea was classified as a `service`, so its UI would have skipped the skin/hook/browser stages.

## New
- `04-deployment/catalogue_sync.py` — fixed rules, no AI. Reads `upstream_apps.json` (repos
  fetched as blob-less shallow clones, cached) and the library folder; reads APP_MAP, ALIASES,
  BLOCKED_APPS and the skin category registry from the skins package. Writes
  `app_catalogue.json` and appends to `CATALOGUE_CHANGELOG.md` only when something changed.
  Skin category: APP_MAP → an existing MAPPED catalogue entry → upstream category name that
  exactly equals a registry category → otherwise NEEDS_REVIEW. Never invents a category, never
  overwrites a MAPPED value, never deletes an entry. Same inputs → byte-identical catalogue.
- `04-deployment/app_catalogue.json` — 34 apps. Review decisions are made here: set
  `skin_category` + `skin_status: "MAPPED"` (or `profile` + `profile_status: "MAPPED"`,
  `profile_source: "human"`).

## Changed
- `pipeline.py` — new `CATALOGUE_SYNC` step (offline, library only) before the installer.
  Runtime catalogue: `<ROOT>/app_catalogue.json`; the shipped one is its baseline. Apps held
  for review are listed on the build as `catalogue_review`.
- `install_all.py` (skins package) — after APP_MAP, uses the catalogue's mapping. An app the
  catalogue marks NEEDS_REVIEW is `PENDING_REVIEW` (no overlay) instead of an error. An app in
  neither still fails, with a message naming catalogue_sync.py.
- `intake.py` (ruleset 3) — Go apps with `web_src/` or `templates/` page files are `web`
  (Gitea). A reviewed (MAPPED) catalogue profile overrides the rules. New `web_evidence()`.

Unchanged: APP_MAP (all 31 entries), the category registry, upstream app source.
