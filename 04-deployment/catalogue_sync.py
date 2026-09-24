#!/usr/bin/env python3
"""App catalogue sync. The library grows by itself: any app that arrives (uploaded as a zip,
added from a git URL, cloned from a seed repo) gets a code root, a skin category and a
qualification profile from its own files, with no list to edit first.

Where apps come from (none of these is a gate)
  - the library folder            the source of truth: whatever is there is catalogued
  - upstream_apps.json (optional)  seed repos only; a missing or gone repo is just skipped
  - the existing catalogue        entries are kept forever, never deleted

What decides the skin category, first match wins
  1. APP_MAP in the skins package          (hand-made choices, always respected)
  2. a category already in the catalogue   (once decided, an app keeps it; your edits stick)
  3. seed category == a registry category name
  4. the app's own words (README, package description, keywords) scored against the
     registry's categories (app_classifier.py). Nothing matched -> the generic fallback skin.
  Every result is a category that exists in skins_library/_index.json. Low-confidence picks
  are listed under "worth a look" in the report; they never stop anything.

Profile (web / service / system / package): intake.profile_of() on the app's code root.

Writes app_catalogue.json and appends to CATALOGUE_CHANGELOG.md when something changed.
Same inputs -> byte-identical catalogue, no changelog entry.

Usage
  python3 catalogue_sync.py --library DIR          # catalogue the library (+ seeds, cached clones)
  python3 catalogue_sync.py --library DIR --offline   # no network (what the pipeline runs)
  python3 catalogue_sync.py --check                # report only; exit 1 if it would change
"""
from __future__ import annotations
import argparse, ast, json, os, re, shutil, subprocess, sys, time, zipfile
from pathlib import Path

import intake, app_classifier

HERE = Path(__file__).resolve().parent
SCHEMA = 'ATTA_APP_CATALOGUE.v2'
RULESET = 2
FETCH_FILES = ('composer.json', 'package.json', 'go.mod', 'pyproject.toml', 'setup.py', 'requirements.txt',
               'README.md', 'readme.md', 'README.rst', 'README')
GIT_TIMEOUT = int(os.environ.get('APP_BUILDER_GIT_TIMEOUT', '900'))
CONTROL_DIRS = {'skins_library', 'capability-port', 'ui-bridge', '_system'}


def norm(s: str) -> str:            # same as install_all.norm: the installer's app key
    return re.sub(r'[^a-z0-9]+', ' ', (s or '').lower()).strip()


def slug(s: str) -> str:            # same as pipeline.library(): the library folder name
    return re.sub(r'[^a-z0-9-]+', '-', (s or '').lower()).strip('-')


def cat_key(s: str) -> str:         # "Project Management" -> "project_management"
    return re.sub(r'[^a-z0-9]+', '_', (s or '').lower()).strip('_')


# ---------------------------------------------------------------- authorities (read only)

def _package_files(package: Path) -> tuple[str, dict]:
    """install_all.py source and the category registry, from the skins zip or an out/ folder."""
    if package.is_file():
        with zipfile.ZipFile(package) as z:
            names = z.namelist()
            inst = next(n for n in names if n.endswith('out/install_all.py'))
            idx = next(n for n in names if n.endswith('out/skins_library/_index.json'))
            return z.read(inst).decode(), json.loads(z.read(idx))
    out = package if (package / 'install_all.py').is_file() else package / 'out'
    return (out / 'install_all.py').read_text(), json.loads((out / 'skins_library/_index.json').read_text())


def load_authorities(package: Path) -> dict:
    src, index = _package_files(package)
    found = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ('APP_MAP', 'ALIASES', 'BLOCKED_APPS'):
                found[node.targets[0].id] = ast.literal_eval(node.value)
    cats = index.get('categories', {})
    return {'app_map': found.get('APP_MAP', {}), 'aliases': found.get('ALIASES', {}),
            'blocked': set(found.get('BLOCKED_APPS', set())), 'categories': set(cats)}


def resolve_skin(category: str, auth: dict) -> str | None:
    """The registry category the installer would actually use, or None if it can't."""
    if category in auth['categories']:
        return category
    for alt in auth['aliases'].get(category, []):
        if alt in auth['categories']:
            return alt
    return None


# ---------------------------------------------------------------- source trees

def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)


def repo_url(repo: str) -> str | None:
    """owner/name -> GitHub URL; a full https git URL is used as given."""
    if not repo or repo.startswith('OPENAI-') or repo in ('—', ''):
        return None
    if re.match(r'^https://[\w.-]+/[\w.~/-]+$', repo):
        return repo
    if re.match(r'^[\w.-]+/[\w.-]+$', repo):
        return f'https://github.com/{repo}.git'
    return None


def fetch_tree(repo: str, cache: Path, refresh: bool) -> tuple[Path | None, str | None]:
    """Blob-less shallow clone (trees only), then a skeleton folder: every path to depth 4 as an
    empty file, plus the real manifests and README at the top and at the app's code root, so
    the same rules run on it as on a real checkout. Returns (skeleton, error)."""
    url = repo_url(repo)
    if not url:
        return None, f'no usable repository ({repo!r})'
    name = slug(re.sub(r'^https://|\.git$', '', url).replace('github.com/', '').replace('/', '--'))
    bare, skel = cache / 'git' / name, cache / 'tree' / name
    if refresh or not (bare / 'HEAD').exists():
        shutil.rmtree(bare, ignore_errors=True); bare.parent.mkdir(parents=True, exist_ok=True)
        r = _run_git(['clone', '--quiet', '--bare', '--depth', '1', '--filter=blob:none', url, str(bare)])
        if r.returncode:
            shutil.rmtree(bare, ignore_errors=True)
            return None, (r.stderr or r.stdout).strip()[-300:]
        shutil.rmtree(skel, ignore_errors=True)
    if not (skel / '.atta-skeleton').exists():
        shutil.rmtree(skel, ignore_errors=True)
        r = _run_git(['ls-tree', '-r', '--name-only', 'HEAD'], cwd=bare)
        if r.returncode:
            return None, r.stderr.strip()[-300:]
        tmp = skel.with_name(skel.name + '.tmp'); shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
        for path in r.stdout.splitlines():
            parts = path.split('/')
            if len(parts) > 4:
                (tmp / '/'.join(parts[:4])).mkdir(parents=True, exist_ok=True); continue
            f = tmp / path; f.parent.mkdir(parents=True, exist_ok=True)
            if not f.exists(): f.touch()
        def fill(prefix: Path):
            for mf in FETCH_FILES:
                f = tmp / prefix / mf
                if f.is_file() and f.stat().st_size == 0:
                    r2 = _run_git(['show', f'HEAD:{(prefix / mf).as_posix()}'], cwd=bare)
                    if r2.returncode == 0: f.write_text(r2.stdout)
        fill(Path('.'))
        root, _ = app_classifier.find_app_root(tmp)
        if root is not None and root != tmp: fill(root.relative_to(tmp))
        (tmp / '.atta-skeleton').write_text(url + '\n')
        tmp.rename(skel)
    return skel, None


def deployable(app: Path) -> list[str]:
    hits = []
    for pat in ('Dockerfile', 'Dockerfile.*', 'docker-compose*.yml', 'docker-compose*.yaml', 'compose.yml', 'compose.yaml'):
        hits += [p.relative_to(app).as_posix() for p in app.glob(pat)]
        hits += [p.relative_to(app).as_posix() for p in app.glob('*/' + pat) if '.ui-capability' not in p.parts]
    return sorted(set(hits))


def inspect(tree: Path, categories: set[str]) -> dict:
    prof = intake.profile_of(tree)
    root = tree / prof['root'] if prof['root'] else tree
    guess = app_classifier.choose_category(root, tree, categories)
    return {**prof, 'deploy_files': deployable(tree)[:10], 'category_guess': guess}


# ---------------------------------------------------------------- sync

def gather_apps(manifest: Path | None, library: Path | None) -> dict:
    apps = {}
    if library and library.is_dir():
        for p in sorted(library.iterdir()):
            if not p.is_dir() or p.name.startswith('.') or p.name in CONTROL_DIRS:
                continue
            apps[norm(p.name)] = {'app': p.name, 'repository': _origin(p), 'numbers': [], 'seed_categories': [],
                                  'sources': {'library'}, 'library_dir': p}
    if manifest and manifest.is_file():
        try:
            entries = json.loads(manifest.read_text()).get('entries', [])
        except (OSError, ValueError):
            entries = []
        for e in entries:
            k = norm(e.get('app', ''))
            if not k: continue
            a = apps.setdefault(k, {'app': e['app'], 'repository': None, 'numbers': [], 'seed_categories': [], 'sources': set()})
            a['repository'] = a['repository'] or e.get('repository')
            a['numbers'].append(e.get('number')); a['sources'].add('seed')
            if e.get('category') and e['category'] not in a['seed_categories']:
                a['seed_categories'].append(e['category'])
    return apps


def _origin(p: Path) -> str | None:
    try:
        r = subprocess.run(['git', '-C', str(p), 'remote', 'get-url', 'origin'], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def sync(args) -> tuple[dict, dict, list[str]]:
    auth = load_authorities(args.package)
    cats = auth['categories']
    old = {}
    if args.catalogue.is_file():
        old = json.loads(args.catalogue.read_text()).get('apps', {})
    if args.baseline and args.baseline.is_file() and args.baseline.resolve() != args.catalogue.resolve():
        for k, v in json.loads(args.baseline.read_text()).get('apps', {}).items():
            old.setdefault(k, v)   # shipped entries fill gaps; the runtime copy wins
    found = gather_apps(args.manifest, args.library)
    new, notes = {}, []
    for k in sorted(set(found) | set(old)):
        src, prev = found.get(k), old.get(k, {})
        if src is None:
            new[k] = prev          # not seen this run: kept exactly as it was, never deleted
            continue
        e = {'app': src['app'], 'repository': src['repository'] or prev.get('repository'),
             'numbers': sorted(n for n in src['numbers'] if n is not None) or prev.get('numbers', []),
             'seed_categories': src['seed_categories'] or prev.get('seed_categories') or prev.get('upstream_categories', []),
             'sources': sorted(src['sources'] | {('seed' if x == 'upstream_apps.json' else x) for x in prev.get('sources', [])})}

        # --- read the app's files (library copy first, else a cached skeleton of its repo)
        tree, err = None, None
        if src.get('library_dir'):
            tree, e['tree_from'] = src['library_dir'], 'library'
        elif args.offline:
            err = 'offline and not in the library yet'
        else:
            tree, err = fetch_tree(e['repository'] or '', args.cache, args.refresh); e['tree_from'] = 'git'
        info = inspect(tree, cats) if tree else None
        if info is None and prev.get('frameworks') is not None:
            info = {x: prev.get(x) for x in ('root', 'root_found', 'frameworks', 'web_evidence', 'deploy_files')}
            info.update(profile=prev.get('rule_profile'), confidence=prev.get('profile_confidence'), why=prev.get('profile_why'),
                        category_guess=prev.get('category_guess'))
            e['tree_from'] = prev.get('tree_from')
        look = []

        # --- skin category
        prev_ok = prev.get('skin_status') == 'MAPPED' and resolve_skin(prev.get('skin_category') or '', auth)
        seed = sorted({cat_key(c) for c in e['seed_categories'] if cat_key(c) in cats})
        if k in auth['blocked']:
            e.update(skin_category=None, skin_source='BLOCKED_APPS', skin_status='BLOCKED', skin_confidence=None)
        elif k in auth['app_map'] and resolve_skin(auth['app_map'][k], auth):
            wanted = auth['app_map'][k]
            e.update(skin_category=wanted, skin_source='APP_MAP', skin_status='MAPPED', skin_confidence='set by hand')
            if resolve_skin(wanted, auth) != wanted: e['skin_resolves_to'] = resolve_skin(wanted, auth)
        elif prev_ok:
            e.update(skin_category=prev['skin_category'], skin_source=prev.get('skin_source') or 'catalogue',
                     skin_status='MAPPED', skin_confidence=prev.get('skin_confidence') or 'kept')
            for f in ('skin_evidence', 'skin_runner_up'):
                if f in prev: e[f] = prev[f]
        elif len(seed) == 1:
            e.update(skin_category=seed[0], skin_source='seed-category', skin_status='MAPPED', skin_confidence='high')
        elif info and info.get('category_guess'):
            g = info['category_guess']
            e.update(skin_category=g['category'], skin_source='auto', skin_status='MAPPED', skin_confidence=g['confidence'],
                     skin_evidence=g['evidence'], skin_runner_up=g.get('runner_up'))
        else:
            cat = app_classifier.FALLBACK_CATEGORY if app_classifier.FALLBACK_CATEGORY in cats else sorted(cats)[0]
            e.update(skin_category=cat, skin_source='fallback', skin_status='MAPPED', skin_confidence='low',
                     skin_evidence=['source not readable yet: ' + (err or 'unknown')])
        if k in auth['app_map'] and not resolve_skin(auth['app_map'][k], auth):
            look.append(f"APP_MAP says {auth['app_map'][k]!r}, which is not in the registry; used {e['skin_category']!r} instead")
        if e.get('skin_confidence') == 'low':
            look.append(f"skin {e['skin_category']!r} is a low-confidence pick ({'; '.join(e.get('skin_evidence') or [])})")
        if info and info.get('category_guess') and e['skin_source'] in ('APP_MAP', 'catalogue') and info['category_guess']['confidence'] == 'high' \
                and info['category_guess']['category'] != e['skin_category']:
            notes.append(f"{k}: kept {e['skin_category']!r} ({e['skin_source']}); its own words point to {info['category_guess']['category']!r}")

        # --- profile
        if prev.get('profile_status') == 'MAPPED' and prev.get('profile') and prev.get('profile_source') == 'human':
            e.update(profile=prev['profile'], profile_source='human', profile_status='MAPPED', profile_confidence='set by hand')
        elif info and info.get('profile'):
            e.update(profile=info['profile'], profile_source='rules', profile_status='MAPPED', profile_confidence=info['confidence'])
            if info['confidence'] == 'low': look.append(f"profile {info['profile']!r} is a guess: {info.get('why')}")
        elif info and info.get('root') is None and tree:
            e.update(profile=None, profile_source='rules', profile_status='NOT_AN_APP', profile_confidence='high')
        else:
            e.update(profile=prev.get('profile'), profile_source=prev.get('profile_source'), profile_status=prev.get('profile_status') or 'PENDING',
                     profile_confidence=prev.get('profile_confidence'))
        if info:
            e.update(rule_profile=info.get('profile'), profile_why=info.get('why'), root=info.get('root'), root_found=info.get('root_found'),
                     frameworks=info.get('frameworks'), web_evidence=info.get('web_evidence'), deploy_files=info.get('deploy_files'),
                     deployable=bool(info.get('deploy_files')), category_guess=info.get('category_guess'))
        e['look'] = look
        e['status'] = ('BLOCKED' if e['skin_status'] == 'BLOCKED' else
                       'NOT_AN_APP' if e.get('profile_status') == 'NOT_AN_APP' else 'MAPPED')
        new[k] = e
    return old, new, notes


FIELDS = ('skin_category', 'skin_source', 'profile', 'root', 'deployable', 'status')


def diff(old: dict, new: dict) -> list[str]:
    out = []
    for k in sorted(new):
        o, n = old.get(k), new[k]
        if not o:
            out.append(f"ADDED {k}: skin={n.get('skin_category')} ({n.get('skin_source')}, {n.get('skin_confidence')}), "
                       f"profile={n.get('profile')} ({n.get('profile_confidence')}), root={n.get('root')}, deployable={n.get('deployable')}")
            continue
        for f in FIELDS:
            if o.get(f) != n.get(f):
                out.append(f'CHANGED {k}.{f}: {o.get(f)!r} -> {n.get(f)!r}')
        if o != n and not any(x.startswith('CHANGED ' + k + '.') for x in out):
            out.append(f'UPDATED {k}: evidence refreshed')
    return out


def report(old: dict, new: dict, notes: list[str], changes: list[str]) -> str:
    L = ['# App catalogue sync report', '']
    added = [k for k in sorted(new) if k not in old]
    look = [k for k in sorted(new) if new[k].get('look')]
    kept = [k for k in sorted(new) if k in old and old[k].get('skin_category') == new[k].get('skin_category')]
    L += [f"Apps: {len(new)} · new this run: {len(added)} · mapped: {sum(1 for e in new.values() if e.get('status') == 'MAPPED')} · "
          f"worth a look: {len(look)} · blocked: {sum(1 for e in new.values() if e.get('status') == 'BLOCKED')} · "
          f"not an app: {sum(1 for e in new.values() if e.get('status') == 'NOT_AN_APP')} · changes written: {len(changes)}", '']
    L += ['## All apps', '', '| App | Sources | Code root | Skin category | Decided by | Confidence | Profile | Deployable | Status |',
          '|---|---|---|---|---|---|---|---|---|']
    for k in sorted(new):
        e = new[k]
        L.append(f"| {k} | {', '.join(e.get('sources', []))} | {e.get('root') or '—'} | {e.get('skin_category') or '—'} | "
                 f"{e.get('skin_source') or '—'} | {e.get('skin_confidence') or '—'} | {e.get('profile') or '—'} "
                 f"({e.get('profile_confidence') or '—'}) | {'yes' if e.get('deployable') else 'no'} | {e.get('status')} |")
    L += ['', '## New this run', '']
    L += [f"- **{k}**: {new[k].get('skin_category')} ({new[k].get('skin_source')}, {new[k].get('skin_confidence')}), "
          f"profile {new[k].get('profile')}" for k in added] or ['- none']
    L += ['', '## Worth a look (nothing is blocked by these)', '']
    for k in look:
        L.append(f'- **{k}**'); L += [f'  - {r}' for r in new[k]['look']]
    if not look: L.append('- none')
    L += ['', '## Kept as they were', '', ', '.join(kept) or 'none']
    if notes: L += ['', '## Notes', ''] + [f'- {n}' for n in notes]
    if changes: L += ['', '## Changes written this run', ''] + [f'- {c}' for c in changes]
    return '\n'.join(L) + '\n'


def write_catalogue(path: Path, apps: dict) -> None:
    data = {'schema': SCHEMA, 'ruleset': RULESET,
            'how_to_change': 'Set skin_category to any category in out/skins_library/_index.json (keep skin_status '
                             'MAPPED), or set profile + profile_source "human". A sync never overwrites either.',
            'apps': apps}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + '\n')
    tmp.replace(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--manifest', type=Path, default=HERE / 'upstream_apps.json', help='optional seed repos')
    ap.add_argument('--package', type=Path, default=HERE.parent / '03-ui-skins-capability-package/UI_Skin_Capability_OneShot_v2.zip',
                    help='skins zip, or an extracted package folder (with out/)')
    ap.add_argument('--catalogue', type=Path, default=HERE / 'app_catalogue.json')
    ap.add_argument('--baseline', type=Path, default=HERE / 'app_catalogue.json', help='shipped catalogue merged under the one being written')
    ap.add_argument('--changelog', type=Path, default=None, help='default: CATALOGUE_CHANGELOG.md next to the catalogue')
    ap.add_argument('--library', type=Path, default=None)
    ap.add_argument('--cache', type=Path, default=Path(os.environ.get('ATTA_CATALOGUE_CACHE', Path.home() / '.cache/atta-catalogue')))
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--refresh', action='store_true', help='re-clone cached repos to pick up upstream changes')
    ap.add_argument('--check', action='store_true', help='report only; exit 1 if the catalogue would change')
    ap.add_argument('--report', type=Path, default=None)
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    a.changelog = a.changelog or a.catalogue.parent / 'CATALOGUE_CHANGELOG.md'
    old, new, notes = sync(a)
    changes = diff(old, new) if (a.catalogue.is_file() or old) else diff({}, new)
    text = report(old, new, notes, changes)
    if a.report: a.report.write_text(text)
    if not a.quiet: print(text)
    if a.check:
        return 1 if changes else 0
    if changes or not a.catalogue.is_file():
        write_catalogue(a.catalogue, new)
    if changes:
        stamp = time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())
        head = '' if a.changelog.is_file() else '# App catalogue changelog\n\nWritten by catalogue_sync.py. One section per run that changed something.\n'
        with a.changelog.open('a') as f:
            f.write(head + f'\n## {stamp}\n\n' + ''.join(f'- {c}\n' for c in changes))
    return 0


if __name__ == '__main__':
    sys.exit(main())
