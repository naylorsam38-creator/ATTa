"""Intake check: runs when apps come into the library, fixes what it can with fixed rules,
and records every change. No AI, no network, no cost.

An app that passes is stamped READY in <app>/.atta-intake.json and is never re-checked.
An app with something left over is stamped NEEDS_ATTENTION with what and where; it is
re-checked on the next build, so once you fix it by hand it flips to READY by itself.

The untouched original is the app's own git history: the ledger records the commit the
library copy came from, and `git -C <app> diff` shows every change made since.
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, sys, time
from pathlib import Path

import syntax_triage
import app_classifier

LEDGER = '.atta-intake.json'
RULESET = 5   # bump when rules change, so READY apps get one fresh check
# 3: Go apps with web_src/ or templates/ are web (Gitea); catalogue profile wins when reviewed.
# 4: rules run on the app's code root (monorepos); profile_of() decides with evidence + confidence.
# 5: the subdomain note comes from the app's own settings (a trait), not from its name.

# Marker text each current capability file carries. An overlay file without its marker
# is older than the package and is refreshed from the package copy.
CURRENT_MARKERS = {
    'ui-bridge/proxy.js': 'NO_INJECT_PATHS',
    'capability-port/caps/button-mover.mjs': 'fights',
}


def _git_head(app: Path) -> str | None:
    try:
        r = subprocess.run(['git', '-C', str(app), 'rev-parse', 'HEAD'],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def _hide_ledger_from_git(app: Path) -> None:
    ex = app / '.git' / 'info' / 'exclude'
    try:
        ex.parent.mkdir(parents=True, exist_ok=True)
        text = ex.read_text() if ex.exists() else ''
        if LEDGER not in text.split():
            ex.write_text(text + ('' if text.endswith('\n') or not text else '\n') + LEDGER + '\n')
    except OSError:
        pass


def fingerprint(app: Path) -> list[str]:
    """Which frameworks this app is, from the files it has. Decides which checks apply."""
    has = lambda *names: any((app / n).exists() for n in names)
    found = []
    if has('manage.py') or list(app.glob('*/manage.py')): found.append('django')
    if has('artisan'): found.append('laravel')
    if has('composer.json'): found.append('php')
    if has('package.json'): found.append('node')
    if has('docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml'): found.append('docker')
    if has('go.mod'): found.append('go')
    return found


def classify(app: Path, frameworks: list[str]) -> str:
    """What kind of project this is, which decides how stage six qualifies it:
    web      - has pages people open in a browser (browser check)
    service  - runs and answers on a port, no web pages (port/HTTP check)
    system   - infrastructure you build, not run as an app, e.g. Moby (build-readiness check)
    package  - code meant to be installed into another app (build-readiness check)"""
    if 'php' in frameworks and 'laravel' not in frameworks:
        try:
            if json.loads((app / 'composer.json').read_text()).get('type') == 'library':
                return 'package'
        except (OSError, ValueError):
            pass
    if 'go' in frameworks:
        if (app / 'daemon').is_dir() or (app / 'cmd' / 'dockerd').is_dir():
            return 'system'
        web_ui = any((app / d).is_dir() and (any((app / d).glob('package.json')) or any((app / d).glob('index.html')))
                     for d in ('web', 'ui', 'frontend', 'webui', 'static'))
        # Server-rendered Go apps (Gitea): the UI is Go templates plus a web_src/ bundle.
        web_ui = web_ui or (app / 'web_src').is_dir() or _has_page_templates(app)
        return 'web' if web_ui else 'service'
    return 'web'


def _has_page_templates(app: Path) -> bool:
    t = app / 'templates'
    if not t.is_dir():
        return False
    return any(f.suffix in ('.tmpl', '.html', '.gohtml') for f in t.rglob('*'))


# Things that show an app has pages a person opens in a browser. Checked to depth 3 so
# monorepos (apps/web/next.config.js) count. Evidence only: used to flag, never to guess.
WEB_DIRS = {'web', 'web_src', 'webui', 'ui', 'frontend', 'templates', 'static', 'public', 'pages'}
WEB_FILES = {'manage.py', 'artisan', 'index.html', 'angular.json', 'svelte.config.js',
             'vite.config.js', 'vite.config.ts', 'vite.config.mjs', 'vite.config.mts',
             'next.config.js', 'next.config.mjs', 'next.config.ts', 'nuxt.config.js', 'nuxt.config.ts'}
_SKIP = {'node_modules', '.git', 'vendor', 'test', 'tests', 'docs', 'doc', 'examples', 'example', 'cookbook', '.ui-capability'}


def web_evidence(app: Path, depth: int = 3) -> list[str]:
    found = []
    def walk(d: Path, level: int):
        try:
            kids = sorted(d.iterdir())
        except OSError:
            return
        for k in kids:
            if k.name in _SKIP or k.name.startswith(('.', 'e2e')):
                continue
            rel = k.relative_to(app).as_posix()
            if k.is_dir():
                if k.name in WEB_DIRS: found.append(rel + '/')
                if level < depth: walk(k, level + 1)
            elif k.name in WEB_FILES:
                found.append(rel)
    walk(app, 1)
    return found


TOP_WEB_DIRS = ('web', 'web_src', 'webui', 'ui', 'frontend', 'public', 'templates', 'static', 'client')


def profile_of(app: Path) -> dict:
    """Where the code is and what kind of project it is, with the evidence and a confidence.
    Never blocks: every app gets a profile. Low confidence just means 'worth a look'."""
    root, how = app_classifier.find_app_root(app)
    if root is None:
        return {'root': None, 'root_found': how, 'frameworks': [], 'profile': None, 'confidence': 'low',
                'why': 'no code markers within 3 levels: not an app ATTa can build'}
    fw = fingerprint(root)
    if 'docker' not in fw and root != app and 'docker' in fingerprint(app): fw.append('docker')
    web = web_evidence(app)
    top_web = [d + '/' for d in TOP_WEB_DIRS if (root / d).is_dir() or (app / d).is_dir()]
    kind = classify(root, fw); conf = 'high'; why = 'fixed rules'
    has = lambda *n: any((root / x).exists() for x in n)
    deploy_files = any((app / n).is_file() or (root / n).is_file() for n in ('Dockerfile', 'docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml'))
    if kind == 'service' and top_web:
        kind, why = 'web', 'Go app with a top-level web folder: ' + ', '.join(top_web)
    elif kind == 'service':
        conf, why = ('medium', 'Go app, web folders only deeper down: ' + ', '.join(web[:3])) if web else ('high', 'Go app with no web folders')
    elif kind == 'web' and 'go' not in fw and 'php' not in fw:
        strong = [e for e in web if not e.endswith('/')] + top_web
        dotnet = has('global.json') or any(app_classifier.SLN.match(p.name) for d in [root, *[x for x in root.iterdir() if x.is_dir()]]
                                              for p in (d.iterdir() if d.is_dir() else []) if p.is_file())
        if strong:
            why = 'web evidence: ' + ', '.join(strong[:4])
        elif has('pyproject.toml', 'setup.py', 'requirements.txt') and not deploy_files and not has('manage.py'):
            kind, conf, why = 'package', 'medium', 'Python code with no web pages and no container recipe: a library/framework'
        elif dotnet:
            kind, conf, why = 'system', 'medium', '.NET solution with no web pages: built, not browsed'
        elif web:
            conf, why = 'medium', 'only folder-name web evidence: ' + ', '.join(web[:3])
        elif deploy_files:
            kind, conf, why = 'service', 'medium', 'runs in a container but has no web pages'
        else:
            conf, why = 'low', 'no web evidence found; assumed web'
    rel = '.' if root == app else root.relative_to(app).as_posix()
    return {'root': rel, 'root_found': how, 'frameworks': fw, 'profile': kind, 'confidence': conf, 'why': why,
            'web_evidence': web[:10]}


def catalogue_profile(app: Path) -> str | None:
    """Profile from the app catalogue (catalogue_sync.py), when one has been set and reviewed."""
    path = os.environ.get('ATTA_APP_CATALOGUE')
    if not path:
        return None
    try:
        apps = json.loads(Path(path).read_text()).get('apps', {})
    except (OSError, ValueError):
        return None
    key = re.sub(r'[^a-z0-9]+', ' ', app.name.lower()).strip()
    e = apps.get(key) or {}
    if e.get('profile_status') == 'MAPPED' and e.get('profile') in {'web', 'service', 'system', 'package'}:
        return e['profile']
    return None


def _version_of(cmd: list[str]) -> tuple[int, int] | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    m = re.search(r'(\d+)\.(\d+)', r.stdout + ' ' + r.stderr)
    return (int(m.group(1)), int(m.group(2))) if r.returncode == 0 and m else None


def _needs(app: Path) -> dict[str, tuple[int, int]]:
    """Minimum toolchain versions the app declares in its own manifest files."""
    needs = {}
    py = _python_requirement(app)
    if py: needs['python'] = py
    gm = app / 'go.mod'
    if gm.is_file():
        text = gm.read_text(errors='replace')
        m = re.search(r'^go\s+(\d+)\.(\d+)', text, re.M)
        if m: needs['go'] = (int(m.group(1)), int(m.group(2)))
    for name, key, tool in (('package.json', 'engines', 'node'), ('composer.json', 'require', 'php')):
        f = app / name
        if f.is_file():
            try:
                spec = str((json.loads(f.read_text()).get(key) or {}).get(tool, ''))
            except (OSError, ValueError, AttributeError):
                spec = ''
            m = re.search(r'(\d+)(?:\.(\d+))?', spec)
            if m: needs[tool] = (int(m.group(1)), int(m.group(2) or 0))
    return needs


HAVE_CMDS = {'python': [sys.executable, '--version'], 'go': ['go', 'version'],
             'node': ['node', '--version'], 'php': ['php', '-r', 'echo PHP_VERSION;']}


def toolchain(app: Path) -> list[dict]:
    """Evidence only: what the app needs versus what this server has. Never repairs."""
    out = []
    for tool, need in _needs(app).items():
        have = _version_of(HAVE_CMDS[tool]) if shutil.which(HAVE_CMDS[tool][0]) or tool == 'python' else None
        out.append({'tool': tool, 'needs': f'{need[0]}.{need[1]}',
                    'has': f'{have[0]}.{have[1]}' if have else None,
                    'ok': bool(have and have >= need)})
    return out


def check_app(app: Path, pkg_out: Path | None) -> dict:
    changes, attention, notes = [], [], []
    prof = profile_of(app)
    code = app / prof['root'] if prof['root'] else app
    frameworks = prof['frameworks'] or fingerprint(app)
    if prof['root'] not in (None, '.'):
        notes.append(f"code root is {prof['root']}/ ({prof['root_found']})")
    overlay = app / '.ui-capability'

    # 1. Overlay folders the proxy reads from must exist.
    if overlay.is_dir():
        for sub in ('ui-bridge/apps', 'ui-bridge/layouts', 'ui-bridge/skins'):
            d = overlay / sub
            if not d.is_dir():
                d.mkdir(parents=True, exist_ok=True)
                changes.append(f'created folder .ui-capability/{sub}')
    else:
        attention.append('no .ui-capability overlay: the installer did not map this app')

    # 2. Capability files must be the current, fixed versions.
    if overlay.is_dir():
        for rel, marker in CURRENT_MARKERS.items():
            f = overlay / rel
            if f.is_file() and marker not in f.read_text(errors='replace'):
                src = pkg_out / rel if pkg_out else None
                if src and src.is_file():
                    shutil.copy2(src, f)
                    changes.append(f'refreshed .ui-capability/{rel} from the current package')
                else:
                    attention.append(f'.ui-capability/{rel} is an old version and no package copy was found')

    # 3. Syntax of everything in the overlay.
    if overlay.is_dir():
        for f in sorted(overlay.rglob('*')):
            if f.is_file() and 'node_modules' not in f.parts and syntax_triage.has_checker(f):
                err = syntax_triage.check_file(f)
                if err:
                    attention.append(f'SYNTAX_ERROR {f.relative_to(app)}: {err}')

    # 4. Framework checks.
    kind = catalogue_profile(app) or prof['profile'] or 'package'
    if prof['root'] is None:
        notes.append(prof['why'])
    if kind == 'package':
        notes.append('this is a package, not a standalone app: it needs a host app to run in')
    # Trait (not a name): the app's own settings examples configure separate subdomains/hosts for parts
    # of the app. Behind the single-address skin proxy those parts would be lost, so it's flagged.
    sub_keys = subdomain_settings(code)
    if sub_keys:
        notes.append('splits parts of the app across subdomains/hosts (settings: ' + ', '.join(sub_keys[:5]) + '); '
                     'set it to single-domain mode so everything stays behind the proxy')
    # 5. Toolchain versions (evidence). With a Dockerfile the build uses the container's own
    #    toolchain, so a mismatch with this server is only a note, not a blocker.
    tools = toolchain(code)
    in_docker = any((d / n).is_file() for d in {app, code} for n in ('Dockerfile', 'dockerfile'))
    for t in tools:
        if not t['ok']:
            msg = f"needs {t['tool']} {t['needs']}+, this server has {t['has'] or 'none'}"
            (notes if in_docker else attention).append(msg + (' (builds in Docker, so not blocking)' if in_docker else ''))

    return {'frameworks': frameworks, 'type': kind, 'toolchain': tools, 'docker_build': in_docker,
            'changes': changes, 'attention': attention, 'notes': notes}


def subdomain_settings(app: Path) -> list[str]:
    """Setting names in the app's own example settings files that give parts of it their own
    subdomain or host (e.g. ADMIN_DOMAIN, USE_SUBDOMAINS). Evidence only; used for a note."""
    import re
    keys = set()
    for n in ('.env.example', '.env.sample', 'example.env', 'env.example', '.env.template', 'env_init'):
        for f in {app / n, app.parent / n}:
            try:
                txt = f.read_text(errors='replace')
            except OSError:
                continue
            keys |= set(re.findall(r'^\s*#?\s*([A-Z0-9_]*(?:SUBDOMAIN|_DOMAINS?|_HOST)[A-Z0-9_]*)\s*=', txt, re.M)) \
                & {k for k in re.findall(r'[A-Z0-9_]+', txt) if 'SUBDOMAIN' in k or re.search(r'(ADMIN|API|ARCHIVE|WEB|APP|UI)_(DOMAIN|HOST)', k)}
    return sorted(keys)


def _python_requirement(app: Path) -> tuple[int, int] | None:
    import re
    for name in ('pyproject.toml', 'backend/pyproject.toml'):
        p = app / name
        if p.is_file():
            m = re.search(r'requires-python\s*=\s*["\']\s*>=\s*(\d+)\.(\d+)', p.read_text(errors='replace'))
            if m:
                return int(m.group(1)), int(m.group(2))
    return None


def run_all(lib: Path, pkg_out: Path | None = None) -> dict:
    ready, attention_all = [], {}
    for app in sorted(p for p in lib.iterdir() if p.is_dir() and (p / '.git').exists()):
        ledger_path = app / LEDGER
        try:
            ledger = json.loads(ledger_path.read_text()) if ledger_path.is_file() else {}
        except (OSError, ValueError):
            ledger = {}
        if ledger.get('status') == 'READY' and ledger.get('ruleset') == RULESET:
            ready.append(app.name)
            continue
        result = check_app(app, pkg_out)
        history = ledger.get('changes', [])
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        history += [{'at': stamp, 'change': c} for c in result['changes']]
        status = 'NEEDS_ATTENTION' if result['attention'] else 'READY'
        ledger_path.write_text(json.dumps({
            'app': app.name,
            'status': status,
            'ruleset': RULESET,
            'checked_at': stamp,
            'original_commit': ledger.get('original_commit') or _git_head(app),
            'frameworks': result['frameworks'],
            'type': result['type'],
            'qualification': result['type'],
            'toolchain': result['toolchain'],
            'docker_build': result['docker_build'],
            'changes': history,
            'attention': result['attention'],
            'notes': result['notes'],
        }, indent=2) + '\n')
        _hide_ledger_from_git(app)
        if status == 'READY':
            ready.append(app.name)
        else:
            attention_all[app.name] = result['attention']
    return {'ready': ready, 'needs_attention': attention_all}


if __name__ == '__main__':
    lib = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/srv/app-builder/library')
    pkg = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    print(json.dumps(run_all(lib, pkg), indent=2))
