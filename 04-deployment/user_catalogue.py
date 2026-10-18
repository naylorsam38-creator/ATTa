#!/usr/bin/env python3
"""
user_catalogue.py — v118/v120: the catalogue people see. QUALIFIED apps only, nothing else.

v120: a user is never shown a list or a name. for_user() returns one chosen foundation per category,
as {id, url}; build() (admin view) keeps names and every candidate.

THE GUARANTEE. An app is shown to a user (on the Front Door and the Library page) only when
ALL of these hold, checked fresh on every request:
  1. Its LATEST check passed every stage, including stage 6 in a real browser:
     state/runner/results/<id>.json has broken_at == None and stages["6 CLEAN"].status == "OK".
     This is the same rule pipeline.qualify() uses to call an app qualified.
  2. It is live: Coolify runs it and ATTa knows its address
     (coolify_resources.json -> managed_by_atta[<id>].url).
  3. It belongs to a Front Door category (see CATEGORY below).
  4. The app catalogue hasn't blocked it (status BLOCKED or NOT_AN_APP).
There is no "coming soon", "beta" or "maybe works". An app that fails a later check drops out
at once, because its latest result no longer passes, and comes back when it passes again.
A category with no qualified app is simply empty; the Front Door says so plainly.

CATEGORY. The Front Door uses the 52 openapps.pro categories (ids in front-door.html). An app's
Front Door category comes from, in order:
  a) "frontdoor_categories": ["crm", ...] set by hand on its entry in app_catalogue.json (kept by
     catalogue_sync.py), or
  b) its seed categories from upstream_apps.json, matched to the Front Door names; spelling
     differences are listed in ALIASES below.
An app with neither is kept off the Front Door and listed for the admin with the reason.

Command line: python3 user_catalogue.py   prints what users see now, and what's excluded and why.
"""
from __future__ import annotations
import json, os, re, sys
from pathlib import Path

# ===================== CONFIG — edit here, nothing below needs reading =====================
ROOT = Path(os.environ.get("APP_BUILDER_ROOT", "/srv/app-builder"))
# Seed-list category names spelled differently from the Front Door's (key = letters/digits only,
# lower case). Add a line when a new seed category doesn't match a Front Door name.
ALIASES = {
    "abtestingexperiments": "ab-testing-experimentation",
    "codeeditor": "code-editors",
    "communityapps": "community",
    "digitalsignatures": "digital-signiture",       # the Front Door's own id is spelled this way
    "hostingcontrolpanels": "hosting-control-panel",
    "productionmanagement": "product-management",   # the seed list files ClearFlask (product feedback) here
    "videoeditor": "video-editors",
}
# ==========================================================================================


def safe_id(name: str) -> str:
    """Same id the app runner, watcher and Coolify hand-off use."""
    return re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-") or "app"


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def front_door_categories(root: Path = ROOT) -> dict[str, str]:
    """{id: name} read from the installed Front Door page, so the two can never disagree."""
    for p in (root / "front-door.html", Path(__file__).resolve().parent.parent / "02-front-door" / "front-door.html"):
        try:
            txt = p.read_text()
        except OSError:
            continue
        block = txt.split("const CATEGORIES", 1)[-1].split("];", 1)[0]
        cats = dict(re.findall(r'\["([a-z0-9-]+)","([^"]+)"\]', block))
        if cats:
            return cats
    return {}


def categories_for(entry: dict, cats: dict[str, str]) -> list[str]:
    hand = [c for c in (entry.get("frontdoor_categories") or []) if c in cats]
    if hand:
        return hand
    by_key = {_key(n): i for i, n in cats.items()}
    by_key.update({_key(i): i for i in cats})
    out = []
    for s in entry.get("seed_categories") or []:
        k = _key(s)
        c = ALIASES.get(k) or by_key.get(k) or by_key.get(k + "s") or (by_key.get(k[:-1]) if k.endswith("s") else None)
        if c and c not in out:
            out.append(c)
    return out


def qualified(result: dict | None) -> tuple[bool, str]:
    """The pipeline's own rule, applied to the app's latest result."""
    if not result:
        return False, "never checked"
    if result.get("broken_at") is not None:
        return False, f"latest check failed at {result.get('broken_at')} ({result.get('verdict')})"
    clean = (result.get("stages") or {}).get("6 CLEAN", {}).get("status")
    if clean != "OK":
        return False, f"browser stage 6 not passed ({clean or 'missing'})"
    return True, ""


def _url(u: str | None) -> str | None:
    if not u or not isinstance(u, str):
        return None
    u = u.split(",")[0].strip()
    if not u:
        return None
    return u if re.match(r"^https?://", u) else "https://" + u


def build(root: Path = ROOT) -> dict:
    """{"categories": {front_door_id: [app, ...]}, "apps": [...], "excluded": {id: reason}}."""
    cats = front_door_categories(root)
    catalogue = _json(root / "app_catalogue.json", {}).get("apps", {})
    managed = _json(root / "coolify_resources.json", {}).get("managed_by_atta", {}) or {}
    results_dir = root / "state" / "runner" / "results"
    seen: dict[str, dict] = {}
    for k, e in catalogue.items():
        seen.setdefault(safe_id(e.get("app", k)), e)
    # Apps that were checked but aren't in the catalogue file (e.g. added before a sync) still count.
    if results_dir.is_dir():
        for f in results_dir.glob("*.json"):
            seen.setdefault(f.stem, {"app": f.stem})
    by_cat: dict[str, list] = {}
    shown, excluded = [], {}
    for aid, e in sorted(seen.items()):
        if e.get("status") in ("BLOCKED", "NOT_AN_APP"):
            excluded[aid] = f"catalogue status {e['status']}"; continue
        ok, why = qualified(_json(results_dir / f"{aid}.json", None))
        if not ok:
            excluded[aid] = why; continue
        live = managed.get(aid) if isinstance(managed.get(aid), dict) else {}
        url = _url(live.get("url"))
        if not url:
            excluded[aid] = "qualified, but not live in Coolify yet (no address)"; continue
        in_cats = categories_for(e, cats)
        if not in_cats:
            excluded[aid] = ("qualified and live, but no Front Door category: set frontdoor_categories "
                             "on its entry in app_catalogue.json")
            continue
        app = {"id": aid, "name": e.get("app") or aid, "url": url, "family": e.get("family") or "",
               "categories": in_cats}
        shown.append(app)
        for c in in_cats:
            by_cat.setdefault(c, []).append({k: app[k] for k in ("id", "name", "url", "family")})
    return {"categories": by_cat, "apps": shown, "excluded": excluded}


def choose(candidates: list[dict]) -> dict | None:
    """v120: the system chooses the foundation; the person never does. One per category, chosen the
    same way every time: the qualified, live app with the lowest id (ids are stable, sorted in build()).
    Change this function to prefer something else (e.g. the most recently passed check); keep it
    deterministic so two requests never get two different apps for the same description."""
    return candidates[0] if candidates else None


def for_user(root: Path = ROOT) -> dict:
    """What any logged-in person may see: for each Front Door category, the ONE qualified, live
    foundation the system chose, as {id, url} only. No app names, no families, no alternatives,
    no reasons, no failures: the Locked Goal says the person receives an application, not a library
    entry, and is never asked to choose an underlying application."""
    b = build(root)
    out = {}
    for c, apps in b["categories"].items():
        a = choose(apps)
        if a:
            out[c] = [{"id": a["id"], "url": a["url"]}]
    return {"categories": out}


if __name__ == "__main__":
    b = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT)
    print(f"Users see {len(b['apps'])} app(s):")
    for a in b["apps"]:
        print(f"  {a['name']:<24} {', '.join(a['categories']):<40} {a['url']}")
    print(f"\nKept off the catalogue ({len(b['excluded'])}):")
    for k, why in b["excluded"].items():
        print(f"  {k:<24} {why}")
