"""Fixed-rule app understanding: where an app's code lives, and which skin category fits it.
No AI, no network. Used by catalogue_sync.py (and mirrored in the installer for discovery).

The point: no app list governs the system. Any app that lands in the library gets a root,
a category and a profile from what is in its own files, so the library can grow without
anyone editing a mapping first. Every decision carries its evidence and a confidence, and a
decision, once made, is kept (catalogue_sync.py never overwrites a MAPPED entry).
"""
from __future__ import annotations
import json, re
from pathlib import Path

# Files that mark "an app's code starts here". Superset of the installer's original list, so
# Java, Ruby, PHP, .NET and plain-Python apps are no longer invisible.
ROOT_MARKERS = ('package.json', 'go.mod', 'pyproject.toml', 'setup.py', 'requirements.txt', 'manage.py',
                'composer.json', 'Cargo.toml', 'pom.xml', 'build.gradle', 'build.gradle.kts', 'Gemfile',
                'docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml', 'app.json',
                'Dockerfile', 'mix.exs', 'deno.json', 'global.json')
SLN = re.compile(r'.+\.(sln|slnx|csproj|fsproj)$')
SKIP_DIRS = {'node_modules', '.git', 'vendor', 'test', 'tests', 'testing', 'docs', 'doc', 'examples', 'example',
             'samples', 'sample', 'cookbook', 'fixtures', 'benchmark', 'benchmarks', '.ui-capability', 'third_party',
             'deps', 'e2e', '__pycache__', 'scripts', 'tools', 'hack', 'contrib'}
PREFERRED = ('app', 'server', 'backend', 'web', 'api', 'platform', 'src')


def _has_marker(d: Path) -> bool:
    try:
        return any((d / m).is_file() for m in ROOT_MARKERS) or any(SLN.match(p.name) for p in d.iterdir() if p.is_file())
    except OSError:
        return False


def find_app_root(app: Path, depth: int = 3) -> tuple[Path | None, str]:
    """The folder where the app's code starts. Top level if it has a marker; otherwise the
    shallowest marked folder (monorepos: libs/agno, apps/web), preferring server/app/web-like
    names, then alphabetical. Returns (root or None, how it was found)."""
    if _has_marker(app):
        return app, 'top level'
    level = [app]
    for d in range(1, depth + 1):
        nxt = []
        for parent in level:
            try:
                kids = sorted(p for p in parent.iterdir() if p.is_dir() and not p.name.startswith('.') and p.name not in SKIP_DIRS)
            except OSError:
                continue
            nxt += kids
        hits = [k for k in nxt if _has_marker(k)]
        if hits:
            hits.sort(key=lambda p: (0 if p.name in PREFERRED else 1, PREFERRED.index(p.name) if p.name in PREFERRED else 0, p.as_posix()))
            return hits[0], f'depth {d} ({len(hits)} candidate{"s" if len(hits) > 1 else ""})'
        level = nxt
    return None, 'no code markers within 3 levels'


# ------------------------------------------------------------------ category vocabulary
# Words that point at each registry category. This is vocabulary about kinds of software,
# not a list of apps: new apps match it without anyone adding them anywhere. Categories that
# a future registry adds get their own name words automatically (see vocabulary()).
VOCAB = {
    'accounting_ledger': 'accounting ledger bookkeeping journal double-entry general-ledger balance-sheet',
    'appointment_booking': 'appointment booking scheduling patient clinic healthcare fhir medical reservation',
    'auction': 'auction bid bidding lot',
    'calendar_and_scheduling': 'calendar schedule scheduling event caldav ical meeting availability',
    'collaborative_document_editor': 'collaborative editor document docs diagram whiteboard drawing design canvas realtime co-editing drawio',
    'crm': 'crm customer lead leads sales pipeline contacts deals',
    'dashboard': 'dashboard dashboards analytics metrics monitoring observability charts visualization visualisation grafana insights reporting database backend postgres admin',
    'dating': 'dating match matchmaking',
    'developer_tools': 'developer git repository repositories code forge sdk cli ide api framework devtools version-control pull-request issues ci github gitlab library agents',
    'devops_console': 'devops deploy deployment docker container containers kubernetes k8s self-hosting self-hosted paas server servers infrastructure proxy reverse-proxy load-balancer tunnel orchestration',
    'e_commerce_storefront': 'ecommerce e-commerce shop store storefront cart checkout product products payments billing subscriptions merchant',
    'email_client': 'email mail smtp imap newsletter mailing inbox',
    'event_ticketing': 'ticketing tickets event events venue',
    'expense_tracker': 'expense expenses budget budgeting spending personal-finance',
    'file_storage_and_sync': 'files file storage sync archive archiving backup drive upload s3 webdav cloud snapshot wayback preservation',
    'fitness_tracking': 'fitness workout exercise training gym',
    'fleet_tracking': 'fleet vehicle vehicles gps telematics',
    'food_delivery': 'food delivery restaurant order courier',
    'form_builder_and_survey': 'form forms survey surveys questionnaire low-code no-code builder internal-tools',
    'habit_tracker': 'habit habits streak',
    'helpdesk_ticketing': 'helpdesk help-desk support ticket tickets feedback customer-support chatbot conversational roadmap',
    'inventory_and_warehouse': 'inventory warehouse stock sku',
    'invoicing': 'invoice invoices invoicing quote',
    'language_learning': 'language learning vocabulary',
    'meditation_and_wellbeing': 'meditation wellbeing mindfulness',
    'multi_vendor_marketplace': 'marketplace vendors multi-vendor',
    'music_streaming': 'music audio songs playlist streaming',
    'note_taking': 'notes note note-taking memo memos markdown wiki knowledge journal blog blogging publishing writing',
    'online_course_lms': 'course courses lms learning education workflow workflows ai agent visual',
    'parcel_tracking': 'parcel shipment shipping tracking package',
    'payroll': 'payroll salary salaries employees',
    'photo_sharing': 'photo photos gallery images',
    'podcast': 'podcast podcasts episodes',
    'project_management': 'project projects task tasks issue issues kanban sprint board planning pipelines dag orchestration',
    'property_rental': 'property rental rentals real-estate listing',
    'quiz_and_flashcards': 'quiz flashcards flashcard spaced-repetition',
    'recipe_and_meal_planning': 'recipe recipes meal cooking',
    'restaurant_pos': 'pos point-of-sale restaurant orders',
    'ride_hailing': 'ride rides driver drivers taxi',
    'short_video_feed': 'short video videos reels',
    'social_feed': 'social forum community discussion posts feed',
    'spreadsheet': 'spreadsheet spreadsheets table tables grid',
    'team_chat': 'chat messaging team slack channels realtime collaboration workspace',
    'todo_list': 'todo to-do productivity tasks timer pomodoro',
    'video_conferencing': 'video conferencing webrtc meeting meetings call',
    'video_streaming': 'video streaming media player',
}
# A skin that suits any admin-style web app. Used only when nothing matches, so a new app
# is never stuck; the catalogue marks it low confidence so it shows up for a look.
FALLBACK_CATEGORY = 'dashboard'

WORD = re.compile(r'[a-z][a-z0-9+-]{1,30}')


def vocabulary(categories: set[str]) -> dict[str, set[str]]:
    """Terms per registry category: VOCAB words plus the category's own name words."""
    out = {}
    for c in sorted(categories):
        terms = set(VOCAB.get(c, '').split()) | {w for w in c.split('_') if len(w) > 2 and w not in {'and', 'the'}}
        out[c] = terms
    return out


def describe(root: Path, app: Path) -> dict[str, str]:
    """The text an app says about itself: manifest descriptions/keywords (strong) and the
    first part of its README (weak)."""
    strong, weak = [app.name.replace('-', ' ')], []
    for f in ('package.json', 'composer.json'):
        for base in {root, app}:
            p = base / f
            if p.is_file():
                try:
                    d = json.loads(p.read_text(errors='replace') or '{}')
                    strong += [str(d.get('description') or ''), ' '.join(map(str, d.get('keywords') or []))]
                except (OSError, ValueError, AttributeError):
                    pass
    for base in {root, app}:
        p = base / 'pyproject.toml'
        if p.is_file():
            t = p.read_text(errors='replace')
            m = re.search(r'^description\s*=\s*"([^"]*)"', t, re.M); strong.append(m.group(1) if m else '')
            m = re.search(r'^keywords\s*=\s*\[([^\]]*)\]', t, re.M); strong.append(m.group(1) if m else '')
    lead, body = [], []
    for base in (app, root):
        for name in ('README.md', 'readme.md', 'README.MD', 'README.rst', 'README'):
            p = base / name
            if p.is_file() and p.stat().st_size:
                txt = p.read_text(errors='replace')
                txt = re.sub(r'```.*?```', ' ', txt, flags=re.S)                 # code blocks
                txt = re.sub(r'<[^>]+>|!\[[^\]]*\]\([^)]*\)|\]\([^)]*\)|https?://\S+', ' ', txt)  # html, images, links
                lines = [l for l in txt.splitlines() if l.strip() and not re.match(r'\s*([-*+]\s*)?(\[|\||---|===|>\s*\[!)', l)]
                prose = ' '.join(lines)
                lead.append(prose[:700]); body.append(prose[700:7000]); break
        if lead: break
    return {'strong': ' '.join(strong).lower(), 'lead': ' '.join(lead).lower(), 'weak': ' '.join(body).lower()}


# Words nearly every README uses in its install/contributing sections. They only count when
# the app uses them to describe itself (manifest description or the README's opening).
GENERIC = {'github', 'issues', 'issue', 'code', 'tools', 'api', 'library', 'framework', 'repository', 'developer',
           'deploy', 'deployment', 'docker', 'server', 'servers', 'self-hosted', 'self-hosting', 'kubernetes', 'k8s',
           'container', 'containers', 'cli', 'sdk', 'database', 'backend', 'admin', 'management', 'project', 'projects',
           'agents', 'cloud', 'infrastructure', 'workflow', 'workflows', 'ai', 'visual', 'realtime', 'builder',
           'pull-request', 'ci', 'community', 'files', 'file', 'upload', 'events', 'event',
           'tasks', 'task', 'board', 'notes', 'note', 'writing', 'order', 'orders', 'products', 'product', 'table'}


def _counts(text: str) -> dict[str, int]:
    c = {}
    for w in WORD.findall(text):
        c[w] = c.get(w, 0) + 1
        if len(w) > 4 and w.endswith('s'):          # merchants -> merchant, stores -> store
            c[w[:-1]] = c.get(w[:-1], 0) + 1
    return c


def score_categories(text: dict[str, str], categories: set[str]) -> list[tuple[float, str, list[str]]]:
    """Manifest description/keywords: 3 per mention. README opening: 3 per mention (1 for
    words every README uses). README body: 1 per mention, generic words ignored."""
    sc, sl, sw = _counts(text['strong']), _counts(text.get('lead', '')), _counts(text['weak'])
    out = []
    for c, terms in vocabulary(categories).items():
        s, used = 0, []
        for t in terms:
            g = t in GENERIC
            v = 3 * min(sc.get(t, 0), 2) + (1 if g else 3) * min(sl.get(t, 0), 2) + (0 if g else min(sw.get(t, 0), 3))
            if v: s += v; used.append((v, t))
        if s:
            out.append((float(s), c, [t for _, t in sorted(used, key=lambda x: (-x[0], x[1]))[:6]]))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


def choose_category(root: Path, app: Path, categories: set[str]) -> dict:
    """Best registry category from the app's own words. Never returns a category that is not
    in the registry. confidence: high (clear winner), medium (winner by a small margin),
    low (nothing matched: fallback)."""
    ranked = score_categories(describe(root, app), categories)
    if not ranked:
        cat = FALLBACK_CATEGORY if FALLBACK_CATEGORY in categories else sorted(categories)[0]
        return {'category': cat, 'confidence': 'low', 'evidence': ['no category words found; fallback skin'], 'runner_up': None}
    top = ranked[0]; second = ranked[1] if len(ranked) > 1 else None
    margin = top[0] - (second[0] if second else 0)
    conf = 'high' if top[0] >= 6 and margin >= max(3, 0.25 * top[0]) else 'medium' if top[0] >= 3 else 'low'
    return {'category': top[1], 'confidence': conf, 'score': top[0],
            'evidence': [f'words: {", ".join(top[2])}'],
            'runner_up': f'{second[1]} ({second[0]:g})' if second else None}
