#!/usr/bin/env python3
from pathlib import Path
import json, os, re, shutil, subprocess, time, zipfile
import builds, coolify_handoff, maintenance, intake, app_classifier

ROOT=Path(os.environ.get('APP_BUILDER_ROOT','/srv/app-builder'))
INBOX=ROOT/'inbox'; WORK=ROOT/'work'; LIB=ROOT/'library'; STATE=ROOT/'state'; PKG=ROOT/'package'
STATUS=STATE/'status.json'; LOCK=STATE/'pipeline.lock'; MANIFEST=ROOT/'upstream_apps.json'
# App catalogue: the runtime copy accumulates; the shipped copy next to this file is the baseline.
HERE=Path(__file__).resolve().parent; CATALOGUE=ROOT/'app_catalogue.json'
os.environ['ATTA_APP_CATALOGUE']=str(CATALOGUE)   # read by install_all.py and intake.py
MAX_EXTRACTED=int(os.environ.get('APP_BUILDER_MAX_EXTRACTED','53687091200'))
GIT_TIMEOUT=int(os.environ.get('APP_BUILDER_GIT_TIMEOUT','900'))
INSTALL_TIMEOUT=int(os.environ.get('APP_BUILDER_INSTALL_TIMEOUT','1800'))
for p in (INBOX,WORK,LIB,STATE,PKG): p.mkdir(parents=True,exist_ok=True)

def state(**kw):
    d={}
    try: d=json.loads(STATUS.read_text())
    except Exception: pass
    d.update(kw,updated_at=time.time()); STATUS.write_text(json.dumps(d,indent=2)+'\n')

CURRENT_BUILD=None; LAST_BUILD=None
def step(name,**kw):
    # System-wide status (admin view) and the owning user's build record move together.
    state(state=name,**kw)
    if CURRENT_BUILD: builds.update(CURRENT_BUILD,state=name,**kw)

def qualify(only=None, previous=None):
    """The QUALIFIED gate, per app. The app runner starts each library app (one at a time),
    puts its skin proxy in front, and runs the real six-stage watcher on it; system projects and
    packages get the watcher's build-readiness check instead. An app qualifies only if it passed
    every stage AND stage 6 is OK. The build is QUALIFIED (all passed), PARTIALLY_QUALIFIED (some
    passed; only those go to Coolify) or NOT_QUALIFIED (none).
    only/previous: re-check just these apps and keep the earlier results for the rest (healing).
    Returns (state, all apps, problems, results)."""
    import system_watcher as w, app_runner
    names=app_runner.library_apps()
    if not names: return builds.NOT_QUALIFIED,[],'NO_APPS: the library has no installed apps yet',[]
    keep={r.get('app'):r for r in (previous or []) if only is not None and r.get('app') not in only}
    todo=[n for n in names if only is None or app_runner.safe_id(n) in only or n in only]
    step('QUALIFYING',apps=len(todo),parallel=app_runner.PARALLEL)
    # The app runner checks APP_BUILDER_RUN_PARALLEL apps at a time (memory permitting).
    fresh={r['app']:r for r in app_runner.qualify_all(todo,log=lambda m:print(m,flush=True))}
    results=[fresh.get(app_runner.safe_id(n)) or keep.get(app_runner.safe_id(n)) for n in names]
    results=[r for r in results if r]
    w.LOG.parent.mkdir(parents=True,exist_ok=True)
    with w.LOG.open('a') as f:
        for r in results: f.write(json.dumps(r,default=str)+'\n')
    apps=[]; problems=[]
    for r in results:
        clean=r.get('stages',{}).get('6 CLEAN',{}).get('status')
        why=None
        if r.get('broken_at') is not None: why=f"{r['app']}: {r['verdict']}"
        elif clean!='OK': why=f"{r['app']}: browser stage 6 not passed ({clean or 'missing'})"
        if why: problems.append(why)
        try: t=json.loads((ROOT/'state/apps'/f"{r['app']}.json").read_text())
        except (OSError,ValueError): t={}
        run=r.get('runner') or {}
        apps.append({'app':r['app'],'verdict':r['verdict'],'qualified':why is None,'ui_dir':t.get('ui_dir'),
                     'target_url':t.get('target_url'),'proxy_url':t.get('proxy_url'),
                     'started_by':run.get('part') or run.get('why'),'kept_running':run.get('kept_running',False)})
    # The numbered checklist: every library app must have a ticked result. A missing one fails the run.
    cl=app_runner.checklist(names,results)
    if CURRENT_BUILD:
        builds.update(CURRENT_BUILD,checklist=cl)
        (STATE/'checklists').mkdir(parents=True,exist_ok=True)
        (STATE/'checklists'/f'{CURRENT_BUILD}.md').write_text(app_runner.checklist_text(cl,f'build {CURRENT_BUILD}'))
    if not cl['complete']:
        problems.append('CHECKLIST_INCOMPLETE: '+', '.join(f"{x['no']} {x['app']}" for x in cl['rows'] if x['tick']=='NOT_CHECKED'))
    # Regressions: an app that PASSED in the previous full run and fails now. Named on the checklist.
    prev=_previous_checklist()
    if prev:
        was={x['app']:x['tick'] for x in prev.get('rows',[])}
        reg=[x for x in cl['rows'] if was.get(x['app'])=='PASS' and x['tick']!='PASS']
        for x in cl['rows']: x['was']=was.get(x['app'])
        cl['regressions']=[f"{x['no']} {x['app']}" for x in reg]
        cl['newly_passing']=[f"{x['no']} {x['app']}" for x in cl['rows'] if x['tick']=='PASS' and was.get(x['app']) not in (None,'PASS')]
        if CURRENT_BUILD: builds.update(CURRENT_BUILD,checklist=cl)
        if reg: problems.append('REGRESSION (passed last run, fails now): '+', '.join(cl['regressions']))
    passed=[a for a in apps if a['qualified']]
    st=builds.QUALIFIED if not problems else builds.PARTIALLY_QUALIFIED if passed else builds.NOT_QUALIFIED
    return st,apps,'; '.join(problems),results

def _previous_checklist():
    """The checklist of the most recent OTHER build that has one (the last full run)."""
    best=None
    for r in builds.all_builds():
        if r.get('id')==CURRENT_BUILD or not r.get('checklist'): continue
        if best is None or (r.get('updated') or 0)>(best.get('updated') or 0): best=r
    return best.get('checklist') if best else None

def _record_qualification(bid,st,apps,why,results):
    fields=dict(qualification=apps,qualification_results=results,
                qualified_apps=[a['app'] for a in apps if a.get('qualified')])
    if st==builds.NOT_QUALIFIED:
        builds.update(bid,state=st,error=why,**fields); return False
    builds.update(bid,state=st,qualified_at=time.time(),error=(why or None),**fields)
    try: coolify_handoff.hand_off(bid,[a for a in apps if a.get('qualified')])
    except Exception as e: print(f'coolify hand-off {bid}: {e} (retried from the loop)',flush=True)
    return st==builds.QUALIFIED

def requalify(bid):
    """Run the QUALIFIED gate again for a build (used by self-healing to verify a fix).
    True only when every app now passes."""
    rec=builds.get(bid) or {}
    # Every run re-checks the WHOLE catalogue, healing runs included: a fix for one app must be seen
    # not to break any other before it counts.
    st,apps,why,results=qualify()
    return _record_qualification(bid,st,apps,why,results)

def run(c):
    try:
        timeout=INSTALL_TIMEOUT if any(str(x).endswith('install_all.py') for x in c) else GIT_TIMEOUT
        r=subprocess.run(c,text=True,capture_output=True,timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f'{c} -> timed out after {timeout}s') from e
    if r.returncode:
        raise RuntimeError(f'{c} -> {r.returncode}: {r.stdout[-1000:]} {r.stderr[-1000:]}')

def extract(z,d):
    total=0
    root=d.resolve()
    seen_paths=set()
    for i in z.infolist():
        if i.filename in seen_paths: raise RuntimeError(f'duplicate archive member: {i.filename}')
        seen_paths.add(i.filename)
        t=(d/i.filename).resolve()
        if not str(t).startswith(str(root)+os.sep): raise RuntimeError('unsafe archive path')
        if i.is_dir(): t.mkdir(parents=True,exist_ok=True); continue
        if i.file_size < 0 or total + i.file_size > MAX_EXTRACTED:
            raise RuntimeError(f'archive extracted size exceeds {MAX_EXTRACTED} bytes')
        t.parent.mkdir(parents=True,exist_ok=True)
        with z.open(i,'r') as src, t.open('wb') as dst:
            shutil.copyfileobj(src,dst,1024*1024)
        total += i.file_size

def _sha_file(p):
    import hashlib
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def nested(root,name):
    """The one copy of `name` in the upload. Tolerates two harmless slips: identical duplicate
    copies (only one is used), and a renamed copy like 'NAME (1).zip' when it is the only one."""
    def real(p): return p.is_file() and '__MACOSX' not in p.parts and not p.name.startswith('._')
    hits=[p for p in root.rglob(name) if real(p)]
    if not hits:
        stem,suffix=os.path.splitext(name)
        hits=[p for p in root.rglob(stem+'*'+suffix) if real(p)]
        if len(hits)>1: raise RuntimeError(f'Uploaded bundle does not contain {name}; found several renamed candidates: '+', '.join(sorted(p.name for p in hits)))
    if len(hits)>1:
        if len({_sha_file(p) for p in hits})==1: return sorted(hits)[0]
        raise RuntimeError(f'multiple copies of {name} found in upload')
    return hits[0] if hits else None

def validate_package(pkg):
    installer=pkg/'out/install_all.py'; ready=pkg/'out/DEPLOYMENT_READY.json'; index=pkg/'out/skins_library/_index.json'
    if not installer.is_file(): raise RuntimeError(f'UI capability installer missing: {installer}')
    if not ready.is_file(): raise RuntimeError('Deployment readiness marker missing')
    if not index.is_file(): raise RuntimeError('Skin library index missing')
    readiness=json.loads(ready.read_text())
    if readiness.get('status') not in {'READY','READY_FOR_LIVE_VERIFICATION'}: raise RuntimeError('Deployment readiness status is not deployable')
    cats=json.loads(index.read_text()).get('categories',{})
    cats=set(cats) if not isinstance(cats,dict) else set(cats)
    required=set(readiness.get('required_real_categories',[]))
    missing=sorted(required-cats)
    if missing: raise RuntimeError('Required skin categories missing: '+', '.join(missing))
    preflight=pkg/'out/STATIC_DEPLOYMENT_PREFLIGHT.json'
    if preflight.is_file():
        pf=json.loads(preflight.read_text())
        if pf.get('result') != 'PASS': raise RuntimeError('Static deployment preflight is not PASS')
    for category in sorted(cats):
        d=pkg/'out/skins_library'/category
        for variant in ('skin-002','skin-003','skin-004','skin-005'):
            if not (d/(variant+'.css')).is_file(): raise RuntimeError(f'Missing {category}/{variant}.css')
            if not (d/(variant+'.json')).is_file(): raise RuntimeError(f'Missing {category}/{variant}.json')
    return readiness

# Git errors that mean "try again later" (network, timeouts). Anything else on a fresh clone
# (repo deleted, made private, renamed without a redirect) means the repo is unavailable.
TRANSIENT_GIT=re.compile(r'timed out after|Could not resolve host|Failed to connect|Connection (timed out|reset|refused)|early EOF|RPC failed|unable to access|TLS|SSL',re.I)
os.environ.setdefault('GIT_TERMINAL_PROMPT','0')  # a private repo fails fast instead of waiting for a password

def library():
    """Make sure every upstream app is present in the library. An app already in the library is
    used as-is and is never re-fetched from git; only genuinely new apps are cloned. One
    unavailable repo no longer fails the whole build: it is recorded and skipped, and the build
    only fails if a required category ends up with no app."""
    out=[]; seen=set()
    try: entries=json.loads(MANIFEST.read_text()).get('entries',[]) if MANIFEST.is_file() else []
    except (OSError,ValueError): entries=[]   # seeds are optional; a broken seed file never stops a build
    for e in entries:
        repo=e.get('repository') or ''; dest=LIB/re.sub(r'[^a-z0-9-]+','-',str(e.get('app','')).lower()).strip('-')
        if not dest.name: continue
        if repo in seen:
            out.append({**e,'result':'DUPLICATE_REPO_REUSED'}); continue
        seen.add(repo)
        if repo in ('—','') or repo.startswith('OPENAI-'):
            out.append({**e,'result':'BLOCKED_NEEDS_REPO'}); continue
        if dest.exists() and (dest/'.git').exists():
            # The library is authoritative. If the app is already here, use it as it is and
            # never touch the network: no fetch, so no network problem can affect the build.
            out.append({**e,'result':'USING_LOCAL_LIBRARY'})
        elif dest.exists():
            raise RuntimeError(f'CONFLICT_NON_GIT: {dest}')
        else:
            try:
                run(['git','clone','--depth','1',f'https://github.com/{repo}.git',str(dest)])
                out.append({**e,'result':'CLONED'})
            except RuntimeError as err:
                if TRANSIENT_GIT.search(str(err)): raise   # known fix retries these
                shutil.rmtree(dest,ignore_errors=True)     # never leave a half-clone behind (it would block the next run)
                out.append({**e,'result':'REPO_UNAVAILABLE','error':str(err)[-500:]})
    (STATE/'library-results.json').write_text(json.dumps(out,indent=2)+'\n')
    return out

SHIPPED_PKG=[HERE/'UI_Skin_Capability_OneShot_v2.zip', HERE.parent/'03-ui-skins-capability-package/UI_Skin_Capability_OneShot_v2.zip']
def ensure_package():
    """The skins/capability package to install with. The last uploaded bundle's copy, else
    the copy shipped with this deployment, so an app can be added without re-uploading ATTa."""
    try:
        validate_package(PKG); return 'current'
    except Exception:
        pass
    for z in SHIPPED_PKG:
        if z.is_file():
            if PKG.exists(): shutil.rmtree(PKG)
            PKG.mkdir(parents=True,exist_ok=True)
            with zipfile.ZipFile(z) as zz: extract(zz,PKG)
            validate_package(PKG); return f'shipped ({z.name})'
    raise RuntimeError('No skins package installed yet: upload the ATTa bundle once, then apps can be added on their own')

VERSION_TAIL=re.compile(r'([-_ ](main|master|dev|develop|trunk|latest|release|stable|src|source|v?\d+(\.\d+)*[a-z0-9.-]*))+$',re.I)
def app_name(raw):
    """'gitea-main' -> 'gitea', 'ArchiveBox-dev' -> 'archivebox', 'foo (1).zip' -> 'foo'."""
    n=re.sub(r'\.zip\d*$','',Path(raw).name,flags=re.I)
    n=re.sub(r'\s*\(\d+\)$','',n)
    n=VERSION_TAIL.sub('',n) or n
    return re.sub(r'[^a-z0-9-]+','-',n.lower()).strip('-') or 'app'

def _git_snapshot(dest,message):
    env={**os.environ,'GIT_TERMINAL_PROMPT':'0'}
    ident=['-c','user.name=ATTa intake','-c','user.email=intake@atta.local','-c','commit.gpgsign=false']
    if not (dest/'.git').exists(): subprocess.run(['git','init','-q',str(dest)],check=True,env=env)
    subprocess.run(['git','-C',str(dest),'add','-A'],check=True,env=env,capture_output=True)
    subprocess.run(['git',*ident,'-C',str(dest),'commit','-q','--allow-empty','-m',message],check=True,env=env,capture_output=True)

def ingest_upload(stage,bid,owner,original):
    """An uploaded zip that isn't an ATTa bundle is an app (or several). Each goes into the
    library under its own name, as a git snapshot, and is catalogued like any other app."""
    def junk(p): return p.name=='__MACOSX' or p.name.startswith('._') or p.name in ('.DS_Store',)
    top=[p for p in stage.iterdir() if not junk(p)]
    dirs=[p for p in top if p.is_dir()]; files=[p for p in top if p.is_file()]
    if len(dirs)==1 and not files: cands=[(dirs[0],app_name(dirs[0].name))]
    elif dirs and not files and all(app_classifier.find_app_root(d)[0] for d in dirs): cands=[(d,app_name(d.name)) for d in dirs]
    else:
        # Loose files, or folders that aren't all apps: the whole upload is one app. Name it after
        # the folder its code lives in (ArchiveBox-dev/ -> archivebox), else after the file.
        root,_=app_classifier.find_app_root(stage)
        first=root.relative_to(stage).parts[0] if root is not None and root!=stage else None
        cands=[(stage,app_name(first or original or 'app'))]
    added=[]; kept=[]
    for src,name in cands:
        if app_classifier.find_app_root(src)[0] is None:
            raise RuntimeError(f'"{original or src.name}" is not an ATTa bundle and has no app code (no package.json, go.mod, pyproject.toml, composer.json, Dockerfile, ...)')
        dest=LIB/name
        if dest.exists():
            kept.append(name); continue   # the library copy is authoritative (repairs live there)
        shutil.move(str(src),str(dest))
        _git_snapshot(dest,f'intake: {name} uploaded by {owner} (build {bid})')
        added.append(name)
    return added,kept

REPO_URL=re.compile(r'^https://[A-Za-z0-9.-]+/[A-Za-z0-9._~/-]+?(\.git)?/?$')
def ingest_repo(url,bid,owner):
    if not REPO_URL.match(url or ''): raise RuntimeError(f'Not a usable https git URL: {url!r}')
    name=app_name(url.rstrip('/').removesuffix('.git').rsplit('/',1)[-1]); dest=LIB/name
    if dest.exists(): return [],[name]
    try: run(['git','clone','--depth','1',url,str(dest)])
    except RuntimeError:
        shutil.rmtree(dest,ignore_errors=True); raise
    return [name],[]

def lock_is_stale():
    if not LOCK.exists(): return False
    try:
        data=json.loads(LOCK.read_text())
        pid=int(data['pid'])
        created=float(data['created'])
    except Exception:
        try: LOCK.unlink(); return True
        except OSError: return False
    try:
        os.kill(pid,0)
    except ProcessLookupError:
        return True
    except (PermissionError,OSError):
        return False
    # A live PID alone is not sufficient: PIDs can be reused after a crash.
    # Never reclaim a live lock solely because it is old; the owner remains authoritative.
    return False

def build_id_of(b):
    return b.name.split('.',1)[0]

def process(b):
    """One inbox item. Three kinds, same pipeline after intake:
      <id>.zip        containing UI_Skin_Capability_OneShot_v2.zip  -> ATTa bundle (package update)
      <id>.zip        anything else with app code                   -> new app(s) for the library
      <id>.repo.json  {"url": "https://..."}                        -> clone a new app into the library
    Returns the build's final state."""
    global CURRENT_BUILD, LAST_BUILD
    bid=build_id_of(b)
    if not builds.ID_RE.match(bid) or builds.get(bid) is None:
        # A zip dropped straight into the inbox (not via the gateway) still gets a build record.
        bid=builds.create('system',bundle=b.name)['id']
    rec=builds.get(bid) or {}
    CURRENT_BUILD=LAST_BUILD=bid
    step('VALIDATING',bundle=b.name)
    stage=WORK/f'run-{int(time.time())}-{os.getpid()}'; stage.mkdir()
    try:
        added=kept=[]
        if b.name.endswith('.repo.json'):
            url=json.loads(b.read_text()).get('url','')
            step('ADDING_APP',kind='git',source=url)
            added,kept=ingest_repo(url,bid,rec.get('owner','system'))
            builds.update(bid,package=ensure_package())
        else:
            with zipfile.ZipFile(b) as z: extract(z,stage)
            n=nested(stage,'UI_Skin_Capability_OneShot_v2.zip')
            if n:
                builds.update(bid,kind='bundle')
                if PKG.exists(): shutil.rmtree(PKG)
                PKG.mkdir(parents=True,exist_ok=True)
                with zipfile.ZipFile(n) as z: extract(z,PKG)
                validate_package(PKG)
                fronts=[p for p in stage.rglob('front-door.html') if p.parent.name=='02-front-door' and p.is_file()]
                if len(fronts)==1: shutil.copy2(fronts[0],ROOT/'front-door.html')
            else:
                step('ADDING_APP',kind='upload',source=rec.get('original_name') or b.name)
                added,kept=ingest_upload(stage,bid,rec.get('owner','system'),rec.get('original_name'))
                builds.update(bid,package=ensure_package())
        if added or kept: builds.update(bid,apps_added=added,apps_already_in_library=kept)
        step('FETCHING_LIBRARY'); lib=library()
        unavailable=[f"{x['app']} ({x['repository']})" for x in lib if x.get('result')=='REPO_UNAVAILABLE']
        if unavailable: builds.update(bid,library_warnings=unavailable)
        # Catalogue sync (offline, fixed rules): every app in the library gets a code root, a skin
        # category and a profile from its own files. No list has to name it first.
        step('CATALOGUE_SYNC')
        run(['python3',str(HERE/'catalogue_sync.py'),'--offline','--quiet','--library',str(LIB),'--manifest',str(MANIFEST),
             '--package',str(PKG),'--catalogue',str(CATALOGUE),'--baseline',str(HERE/'app_catalogue.json'),
             '--report',str(STATE/'catalogue-report.md')])
        try:
            cat=json.loads(CATALOGUE.read_text()).get('apps',{})
            look={k:v['look'] for k,v in cat.items() if v.get('look')}
            if look: builds.update(bid,catalogue_look=look)
            if added: builds.update(bid,catalogue_new={a:{f:cat.get(re.sub(r'[^a-z0-9]+',' ',a).strip(),{}).get(f) for f in ('skin_category','skin_source','skin_confidence','profile','root')} for a in added})
        except (OSError,ValueError): pass
        installer=PKG/'out/install_all.py'
        step('INSTALLING_UI_CAPABILITY'); run(['python3',str(installer),str(LIB)])
        manifest=LIB/'UI_CAPABILITY_DEPLOYMENT_MANIFEST.json'
        if not manifest.is_file(): raise RuntimeError('Installer did not produce a deployment manifest')
        md=json.loads(manifest.read_text())
        if md.get('errors'): raise RuntimeError('Application skin mapping failed: '+json.dumps(md['errors'],separators=(',',':')))
        if md.get('pending_review'):
            builds.update(bid,catalogue_skipped=[f"{Path(x['path']).name}: {x['reason']}" for x in md['pending_review']])
        # Intake: fixed-rule check of each library app, once. Free, no AI. Leftovers are reported.
        step('INTAKE_CHECK'); checked=intake.run_all(LIB,PKG/'out')
        if checked['needs_attention']: builds.update(bid,intake_warnings=checked['needs_attention'])
        # Skin coverage: which of the package's live-verification categories have an app yet.
        # Reported, never a gate: the library grows into them.
        required=set(json.loads((PKG/'out/DEPLOYMENT_READY.json').read_text()).get('required_real_categories',[]))
        used={a.get('skin',{}).get('category') for a in md.get('apps',[]) if a.get('status')=='READY'}
        if required-used: builds.update(bid,coverage_warnings=['no app uses skin category yet: '+', '.join(sorted(required-used))])
        targets=ROOT/'targets.json'
        state_apps=ROOT/'state/apps'; state_apps.mkdir(parents=True,exist_ok=True)
        # Reconcile watcher targets instead of deleting live registrations. Existing
        # proxies remain valid across an overlay replacement because their ui_dir is stable.
        desired={}
        if targets.is_file():
            cfg=json.loads(targets.read_text())
            for item in cfg.get('targets',[]):
                if {'app','target_url','proxy_url','ui_dir'} <= item.keys():
                    aid=re.sub(r'[^a-z0-9-]+','-',item['app'].lower()).strip('-')
                    desired[aid]=item
        for old_target in state_apps.glob('*.json'):
            try:
                old=json.loads(old_target.read_text())
                ui=Path(old.get('ui_dir',''))
                aid=old_target.stem
                if aid not in desired and ui.is_dir():
                    desired[aid]=old
            except Exception:
                old_target.unlink(missing_ok=True)
        for old_target in state_apps.glob('*.json'):
            if old_target.stem not in desired: old_target.unlink(missing_ok=True)
        for aid,item in desired.items():
            (state_apps/f'{aid}.json').write_text(json.dumps(item,indent=2)+'\n')
        if not md.get('apps') and not list((ROOT/'state/apps').glob('*.json')):
            # Nothing in the library yet: the package is installed and waiting for apps.
            step(builds.PACKAGE_INSTALLED); return builds.PACKAGE_INSTALLED
        step('READY_FOR_WATCHER',library=str(LIB),targets=str(ROOT/'state/apps'))
        maintenance.on_build_progress(bid)
        # Gate: nothing goes downstream until the browser check has passed, per app.
        step('QUALIFYING')
        st,apps,why,results=qualify()
        state(state=st)
        _record_qualification(bid,st,apps,why,results)
        return st
    except Exception as e:
        step('FAILED',error=str(e)); return builds.FAILED
    finally:
        shutil.rmtree(stage,ignore_errors=True)
        CURRENT_BUILD=None

def _inbox_items():
    done=('.processed.zip','.failed.zip','.processed.json','.failed.json')
    zips=[p for p in INBOX.glob('*.zip') if not p.name.endswith(done)]
    repos=[p for p in INBOX.glob('*.repo.json')]
    return sorted(zips+repos,key=lambda p:p.name)

def loop():
    while True:
        bs=_inbox_items()
        if LOCK.exists() and lock_is_stale():
            LOCK.unlink(missing_ok=True)
        if bs and not LOCK.exists():
            LOCK.write_text(json.dumps({'pid':os.getpid(),'created':time.time()}))
            try:
                b=bs[0]; st=process(b)
                ok=st in (builds.QUALIFIED,builds.PARTIALLY_QUALIFIED,builds.PACKAGE_INSTALLED)
                ext='.json' if b.name.endswith('.json') else '.zip'
                b.rename(INBOX/(build_id_of(b)+('.processed' if ok else '.failed')+ext))
                # Self-healing: script -> adapter -> LLM -> human, for whatever didn't pass.
                if st not in (builds.QUALIFIED,builds.PACKAGE_INSTALLED) and LAST_BUILD:
                    try: maintenance.on_failure(LAST_BUILD)
                    except Exception as e: print(f'maintenance {LAST_BUILD}: {e}',flush=True)
            finally: LOCK.unlink(missing_ok=True)
        coolify_handoff.dispatch_pending()
        maintenance.sweep()
        time.sleep(2)

if __name__=='__main__': loop()
