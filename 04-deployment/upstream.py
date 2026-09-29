#!/usr/bin/env python3
"""
upstream.py — when an app's own folder doesn't say how to run it, look at what its UPSTREAM publishes
before anyone (the AI) invents a way.

Three places, all found from the app's own identity, never from a list of app names:
  1. its own documentation: links in its README that are about docker / install / deploy / self-hosting
     are opened, and the docker commands, image names and compose files in their code blocks are read
     (Krayin's README links its Docker page, which names its official image webkul/krayin)
  2. its owner's deployment repositories: the conventional names an owner gives a deployment repo
     (<repo>-docker, docker-<repo>, <repo>-deploy, helm-charts, ...) are checked on the same git host;
     any that exist are cloned (shallow) into the runner's work folder and their compose files used
  3. its owner's images on Docker Hub: images in the owner's own namespace named after the app

Everything found is only a CANDIDATE: the runner starts it, adapts it and runs the full six-stage
check exactly like any other way of running an app; it's saved as the app's recipe only if it passes.
Findings are cached per app (state/runner/upstream/<app>.json) so a re-run doesn't re-fetch.
"""
from __future__ import annotations
import html, json, re, subprocess, time
from pathlib import Path
from urllib.request import Request, urlopen

# ============================ RULES / CONFIG — edit here, nothing below needs reading ============================
# Words that make a README link worth opening (in the link's address or its text).
DOC_LINK_WORDS = re.compile(r"docker|install|deploy|self-?host|setup|getting.?started|quick.?start|container|compose", re.I)
# Most documentation pages opened per app, and the most bytes read from each.
MAX_DOC_PAGES = 6
MAX_DOC_BYTES = 800_000
# Seconds to wait for any one page / git check.
FETCH_TIMEOUT = 20
# Conventional names owners give their deployment repos. {repo} is the app's repo name.
DEPLOY_REPO_NAMES = ["{repo}-docker", "docker-{repo}", "{repo}-deploy", "{repo}-deployment", "{repo}-compose",
                     "{repo}-self-hosted", "{repo}-selfhost", "{repo}-helm", "helm-charts", "docker", "deploy"]
# How long a finding is trusted before upstream is looked at again (seconds).
CACHE_SECONDS = 7 * 86400
# ==============================================================================================================

HEADERS = {"User-Agent": "ATTa-app-runner (deployment discovery)", "Accept": "text/html,text/plain,*/*"}


def _get(url: str) -> str:
    try:
        with urlopen(Request(url, headers=HEADERS), timeout=FETCH_TIMEOUT) as r:
            ctype = r.headers.get("Content-Type", "")
            if not any(t in ctype for t in ("html", "text", "markdown", "json", "yaml")):
                return ""
            return r.read(MAX_DOC_BYTES).decode("utf-8", "replace")
    except Exception:
        return ""


def _readme_text(d: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in d.glob("README*") if p.is_file() and p.stat().st_size < 2_000_000)


def doc_links(d: Path) -> list[str]:
    """Links in the app's README about running it (docker/install/deploy/self-host), most specific first."""
    text = _readme_text(d)
    found = []
    for m in re.finditer(r"\[([^\]]{0,120})\]\((https?://[^)\s]+)\)|(https?://[^\s)>\"']+)", text):
        label, url = (m.group(1) or ""), (m.group(2) or m.group(3) or "")
        url = url.rstrip(".,;")
        if not url or any(x in url for x in ("shields.io", "badge", ".png", ".svg", ".jpg", ".gif", "twitter.com", "discord")):
            continue
        if DOC_LINK_WORDS.search(url) or DOC_LINK_WORDS.search(label):
            score = (0 if re.search(r"docker|compose|container", url + label, re.I) else 1)
            found.append((score, url))
    seen, out = set(), []
    for _, u in sorted(found):
        if u not in seen:
            seen.add(u); out.append(u)
    return out[:MAX_DOC_PAGES]


def code_blocks(page: str) -> list[str]:
    """Code blocks from an HTML or markdown page, as plain text."""
    blocks = [html.unescape(re.sub(r"<[^>]+>", "", b)) for b in re.findall(r"<pre[^>]*>(.*?)</pre>", page, re.S)]
    blocks += re.findall(r"```[a-zA-Z]*\n(.*?)```", page, re.S)
    return [b.strip() for b in blocks if b.strip()]


def owner_deploy_repos(owner: str, repo: str, host: str = "https://github.com") -> list[str]:
    """Deployment repos that really exist under the same owner (checked with git, nothing guessed into use)."""
    out = []
    for pat in DEPLOY_REPO_NAMES:
        name = pat.format(repo=repo)
        if name.lower() == repo.lower():
            continue
        url = f"{host}/{owner}/{name}"
        try:
            r = subprocess.run(["git", "ls-remote", "-q", url, "HEAD"], capture_output=True, timeout=FETCH_TIMEOUT,
                               env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"})
            if r.returncode == 0 and r.stdout.strip():
                out.append(url)
        except Exception:
            continue
    return out


def hub_images(owner: str, keys: set[str]) -> list[str]:
    """Images in the owner's own Docker Hub namespace named after the app."""
    try:
        data = json.loads(_get(f"https://hub.docker.com/v2/repositories/{owner}/?page_size=100") or "{}")
    except ValueError:
        return []
    return [f"{owner}/{r['name']}" for r in data.get("results") or []
            if isinstance(r, dict) and any(k in str(r.get("name", "")).lower() for k in keys)]


def discover(app_id: str, d: Path, owner: str, repo: str, keys: set[str], work: Path, cache_dir: Path) -> dict:
    """Everything upstream publishes about running this app. Cached."""
    cache = cache_dir / f"{app_id}.json"
    try:
        c = json.loads(cache.read_text())
        if time.time() - c.get("at", 0) < CACHE_SECONDS:
            return c
    except (OSError, ValueError):
        pass
    found = {"at": time.time(), "doc_pages": [], "doc_text": "", "compose_blocks": [], "deploy_repos": [], "hub_images": []}
    texts = []
    for url in doc_links(d):
        page = _get(url)
        if not page:
            continue
        found["doc_pages"].append(url)
        for b in code_blocks(page):
            texts.append(b)
            if re.search(r"^\s*services\s*:", b, re.M) and "image:" in b:
                found["compose_blocks"].append({"from": url, "yaml": b})
    found["doc_text"] = "\n".join(texts)[:200_000]
    if owner and repo:
        for url in owner_deploy_repos(owner, repo):
            dest = work / "upstream" / url.rstrip("/").rsplit("/", 1)[-1]
            if not dest.exists():
                subprocess.run(["git", "clone", "--depth", "1", "-q", url, str(dest)], capture_output=True, timeout=300,
                               env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"})
            if dest.is_dir():
                found["deploy_repos"].append({"url": url, "path": str(dest)})
        found["hub_images"] = hub_images(owner, keys)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(found, indent=1))
    return found
