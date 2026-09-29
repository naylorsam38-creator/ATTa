#!/usr/bin/env python3
"""
app_discovery.py (v112) — find EVERY app inside an uploaded zip, at any depth.

Before v112 an upload was either "one folder = one app", "top-level folders that are all
apps", or "everything else = one app named after the top folder". A zip laid out as
    bundle/README.txt
    bundle/apps/memos-main/...
    bundle/apps/homepage-main/...
therefore went into the library as ONE app called "apps". This module walks the whole upload
and returns each app root it finds, so every app in the zip is queued, built, checked and
qualified on its own, through the existing pipeline and the existing parallel worker pool.

Rule (fixed, no AI, no network — like app_classifier.py):
  A folder is ONE app when it has a .git folder, or a code marker (package.json, go.mod,
  Dockerfile, compose file, ... — app_classifier.ROOT_MARKERS), or when the only code folders
  directly under it are named by ROLE (client/, server/, backend/, web/, api/ ...) — that is a
  repository split into parts, not several apps. An app is never descended into: a monorepo's
  packages/* stay part of that one app.
  Anything else is a container (apps/, a wrapper folder, a top level with loose files) and its
  child folders are walked, each judged by the same rule. So a zip of memos-main/ and
  homepage-main/ side by side is two apps, however deep they sit, whatever loose files sit
  beside them. Junk folders (node_modules, vendor, tests, examples, __MACOSX, ...) are never apps.

Command line (for a person):   python3 app_discovery.py /path/to/unpacked-upload
Prints one JSON list: [{"name","path","how"}...]. Nothing is written or moved.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

import app_classifier

# ===================== CONFIG — edit here, nothing below needs reading =====================
# How many folder levels below the upload's top to look for apps. Deeper = slower on huge zips.
# Raise it if someone zips apps five folders down; lower it to stop at shallower layouts.
MAX_DEPTH = 4
# Child-folder names that mean "a PART of one app" (a repo split into client/server), never a separate
# app. A folder whose code-carrying children are ALL named like this is one app. Add a name and folders
# by that name stop being treated as their own app; remove one and they start being. Container names
# (apps, packages, services, modules, plugins) are deliberately NOT here: they hold several apps.
ROLE_DIRS = {"app", "server", "servers", "backend", "backends", "frontend", "frontends", "client", "clients",
             "web", "webapp", "website", "www", "api", "apis", "ui", "gui", "cmd", "core", "src", "lib", "libs",
             "pkg", "service", "internal", "platform", "worker", "workers", "daemon",
             "desktop", "mobile", "admin", "dashboard", "console", "cli", "shared", "common",
             "themes", "templates", "config", "configs", "infra", "deploy", "deployment",
             "docker", "compose", "charts", "helm", "k8s", "kubernetes", "bin", "sdk", "proto", "protos"}
# Folders that are never an app and never walked (on top of app_classifier.SKIP_DIRS).
# Add a name here and that folder is ignored everywhere in every upload.
EXTRA_SKIP = {"__MACOSX", ".github", ".idea", ".vscode", "dist", "build", "target", "coverage", ".cache",
              "assets", "static", "public", "images", "img", "media", "locales", "i18n", "migrations"}
# Files that alone make a folder "just files" (never an app). Add extensions to widen it.
LOOSE_FILE_EXT = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip"}
# ==========================================================================================

VERSION_TAIL = re.compile(r"([-_ ](main|master|dev|develop|trunk|latest|release|stable|src|source|v?\d+(\.\d+)*[a-z0-9.-]*))+$", re.I)


def app_name(raw: str) -> str:
    """'gitea-main' -> 'gitea', 'ArchiveBox-dev' -> 'archivebox', 'foo (1)' -> 'foo'. Same rule as pipeline.py."""
    n = re.sub(r"\.zip\d*$", "", Path(raw).name, flags=re.I)
    n = re.sub(r"\s*\(\d+\)$", "", n)
    n = VERSION_TAIL.sub("", n) or n
    return re.sub(r"[^a-z0-9-]+", "-", n.lower()).strip("-") or "app"


def _junk(p: Path) -> bool:
    return (p.name.startswith("._") or p.name in (".DS_Store", "Thumbs.db") or p.name in EXTRA_SKIP
            or p.name in app_classifier.SKIP_DIRS or (p.name.startswith(".") and p.name != ".git"))


def _kids(d: Path) -> tuple[list[Path], list[Path]]:
    try:
        items = [p for p in d.iterdir() if not _junk(p)]
    except OSError:
        return [], []
    return sorted(p for p in items if p.is_dir()), sorted(p for p in items if p.is_file())


def _coded_children(d: Path) -> list[Path]:
    """Direct child folders that carry code (a marker here or within app_classifier's 3 levels)."""
    dirs, _ = _kids(d)
    return [k for k in dirs if app_classifier.find_app_root(k, 3)[0] is not None]


def is_app_root(d: Path) -> tuple[bool, str]:
    """(True, how) when this folder is one app and must not be walked into."""
    if (d / ".git").exists():
        return True, ".git folder"
    if app_classifier._has_marker(d):
        return True, "code marker"
    coded = _coded_children(d)
    if coded and all(k.name.lower() in ROLE_DIRS for k in coded):
        return True, "one app split into parts (" + ", ".join(k.name for k in coded) + ")"
    return False, ""


def discover(root: Path, max_depth: int | None = None) -> list[dict]:
    """Every app root under `root`. Each entry: {name, path (absolute), rel, how, depth}.
    The upload's own top folder counts as depth 0: if it is itself an app, that is the one result."""
    root = Path(root)
    max_depth = MAX_DEPTH if max_depth is None else max_depth
    found: list[dict] = []
    seen_names: dict[str, int] = {}

    def add(d: Path, how: str, depth: int) -> None:
        base = app_name(d.name if d != root else (root.name or "app"))
        n = seen_names.get(base, 0) + 1
        seen_names[base] = n
        name = base if n == 1 else f"{base}-{n}"   # two 'app' folders in one zip stay two apps
        found.append({"name": name, "path": str(d), "rel": str(d.relative_to(root)) if d != root else ".",
                      "how": how, "depth": depth})

    def walk(d: Path, depth: int) -> None:
        ok, how = is_app_root(d)
        if ok:
            add(d, how, depth); return
        if depth >= max_depth:
            return
        dirs, _files = _kids(d)
        for k in dirs:
            walk(k, depth + 1)

    # A top level that is just one wrapper folder (zip of a folder) is unwrapped first, so the
    # app's name comes from that folder, not from the wrapper's junk siblings.
    walk(root, 0)
    return found


def summary(found: list[dict]) -> str:
    if not found:
        return "no apps found"
    return ", ".join(f"{a['name']} ({a['rel']}: {a['how']})" for a in found)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__.split("Command line (for a person):", 1)[1]); sys.exit(2)
    print(json.dumps(discover(Path(sys.argv[1])), indent=2))
