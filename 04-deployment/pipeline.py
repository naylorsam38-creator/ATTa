#!/usr/bin/env python3
from pathlib import Path
import json, os, re, shutil, subprocess, time, zipfile, io
import builds, coolify_handoff

ROOT=Path(os.environ.get('APP_BUILDER_ROOT','/srv/app-builder'))
INBOX=ROOT/'inbox'; WORK=ROOT/'work'; LIB=ROOT/'library'; STATE=ROOT/'state'; PKG=ROOT/'package'
STATUS=STATE/'status.json'; LOCK=STATE/'pipeline.lock'; MANIFEST=ROOT/'upstream_apps.json'
MAX_EXTRACTED=int(os.environ.get('APP_BUILDER_MAX_EXTRACTED','53687091200'))
GIT_TIMEOUT=int(os.environ.get('APP_BUILDER_GIT_TIMEOUT','900'))
INSTALL_TIMEOUT=int(os.environ.get('APP_BUILDER_INSTALL_TIMEOUT','1800'))
for p in (INBOX,WORK,LIB,STATE,PKG): p.mkdir(parents=True,exist_ok=True)

def state(**kw):
    d={}
    try: d=json.loads(STATUS.read_text())
    except Exception: pass
    d.update(kw,updated_at=time.time()); STATUS.write_text(json.dumps(d,indent=2)+'\n')

CURRENT_BUILD=None
def step(name,**kw):
    # System-wide status (admin view) and the owning user's build record move together.
    state(state=name,**kw)
    if CURRENT_BUILD: builds.update(CURRENT_BUILD,state=name,**kw)

def qualify():
    """The QUALIFIED gate. Runs the real six-stage watcher once, against every registered target.
    QUALIFIED only if every target passed all stages AND stage 6 (real browser) is OK.
    'PASS (CLEAN not run)' does not qualify. No targets does not qualify."""
    import system_watcher as w
    targets=w.load_targets()
    if not targets: return False,[],'NO_TARGETS: no app targets registered in state/apps'
    results=[w.check(t) for t in targets]
    w.LOG.parent.mkdir(parents=True,exist_ok=True)
    with w.LOG.open('a') as f:
        for r in results: f.write(json.dumps(r)+'\n')
    apps=[]; problems=[]
    for t,r in zip(targets,results):
        clean=r.get('stages',{}).get('6 CLEAN',{}).get('status')
        if r.get('broken_at') is not None: problems.append(f"{r['app']}: {r['verdict']}")
        elif clean!='OK': problems.append(f"{r['app']}: browser stage 6 not passed ({clean or 'missing'})")
        apps.append({'app':r['app'],'verdict':r['verdict'],'ui_dir':t.get('ui_dir'),'target_url':t.get('target_url'),'proxy_url':t.get('proxy_url')})
    return (not problems),apps,'; '.join(problems)

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

def nested(root,name):
    hits=[p for p in root.rglob(name) if p.is_file()]
    if len(hits)>1: raise RuntimeError(f'multiple copies of {name} found in upload')
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

def library():
    out=[]; seen=set()
    for e in json.loads(MANIFEST.read_text())['entries']:
        repo=e['repository']; dest=LIB/re.sub(r'[^a-z0-9-]+','-',e['app'].lower()).strip('-')
        if repo in seen:
            out.append({**e,'result':'DUPLICATE_REPO_REUSED'}); continue
        seen.add(repo)
        if repo in ('—','') or repo.startswith('OPENAI-'):
            out.append({**e,'result':'BLOCKED_NEEDS_REPO'}); continue
        if dest.exists() and (dest/'.git').exists():
            run(['git','-C',str(dest),'fetch','--depth','1','origin'])
            out.append({**e,'result':'FETCHED_PRESERVE_LOCAL'})
        elif dest.exists():
            raise RuntimeError(f'CONFLICT_NON_GIT: {dest}')
        else:
            run(['git','clone','--depth','1',f'https://github.com/{repo}.git',str(dest)])
            out.append({**e,'result':'CLONED'})
    (STATE/'library-results.json').write_text(json.dumps(out,indent=2)+'\n')

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

def process(b):
    global CURRENT_BUILD
    bid=b.stem
    if not builds.ID_RE.match(bid) or builds.get(bid) is None:
        # A zip dropped straight into the inbox (not via the gateway) still gets a build record.
        bid=builds.create('system',bundle=b.name)['id']
    CURRENT_BUILD=bid
    step('VALIDATING',bundle=b.name)
    stage=WORK/f'run-{int(time.time())}-{os.getpid()}'; stage.mkdir()
    try:
        with zipfile.ZipFile(b) as z: extract(z,stage)
        n=nested(stage,'UI_Skin_Capability_OneShot_v2.zip')
        if not n: raise RuntimeError('Uploaded bundle does not contain UI_Skin_Capability_OneShot_v2.zip')
        if PKG.exists(): shutil.rmtree(PKG)
        PKG.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(n) as z: extract(z,PKG)
        validate_package(PKG)
        fronts=[p for p in stage.rglob('front-door.html') if p.parent.name=='02-front-door' and p.is_file()]
        if len(fronts)==1: shutil.copy2(fronts[0],ROOT/'front-door.html')
        step('FETCHING_LIBRARY'); library()
        installer=PKG/'out/install_all.py'
        step('INSTALLING_UI_CAPABILITY'); run(['python3',str(installer),str(LIB)])
        manifest=LIB/'UI_CAPABILITY_DEPLOYMENT_MANIFEST.json'
        if not manifest.is_file(): raise RuntimeError('Installer did not produce a deployment manifest')
        md=json.loads(manifest.read_text())
        if md.get('errors'): raise RuntimeError('Application skin mapping failed: '+json.dumps(md['errors'],separators=(',',':')))
        required=set(json.loads((PKG/'out/DEPLOYMENT_READY.json').read_text()).get('required_real_categories',[]))
        used={a.get('skin',{}).get('category') for a in md.get('apps',[]) if a.get('status')=='READY'}
        unused=sorted(required-used)
        if unused: raise RuntimeError('Required skin categories have no deployable app mapping: '+', '.join(unused))
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
        step('READY_FOR_WATCHER',library=str(LIB),targets=str(ROOT/'state/apps'))
        # Gate: nothing goes downstream until the browser check has passed.
        step('QUALIFYING')
        ok,apps,why=qualify()
        if not ok:
            step('NOT_QUALIFIED',error=why,qualification=apps); return False
        step('QUALIFIED',qualified_at=time.time(),qualification=apps,error=None)
        # Every qualified build goes to Coolify. Retries happen in loop() until it gets there.
        try: coolify_handoff.hand_off(bid,apps)
        except Exception as e: print(f'coolify hand-off {bid}: {e} (retried from the loop)',flush=True)
        return True
    except Exception as e:
        step('FAILED',error=str(e)); return False
    finally:
        shutil.rmtree(stage,ignore_errors=True)
        CURRENT_BUILD=None

def loop():
    while True:
        bs=sorted(p for p in INBOX.glob('*.zip') if not p.name.endswith('.processed.zip') and not p.name.endswith('.failed.zip'))
        if LOCK.exists() and lock_is_stale():
            LOCK.unlink(missing_ok=True)
        if bs and not LOCK.exists():
            LOCK.write_text(json.dumps({'pid':os.getpid(),'created':time.time()}))
            try:
                b=bs[0]; ok=process(b)
                b.rename(b.with_suffix('.processed.zip' if ok else '.failed.zip'))
            finally: LOCK.unlink(missing_ok=True)
        coolify_handoff.dispatch_pending()
        time.sleep(2)

if __name__=='__main__': loop()
