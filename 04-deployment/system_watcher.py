#!/usr/bin/env python3
from __future__ import annotations
import argparse, asyncio, hashlib, json, os, re, sys, time, shutil
from pathlib import Path
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
import socket

# Files the installer must have written inside ui_dir. Missing any = INSTALLED fails. Add to this if the installer grows.
REQUIRED_FILES=['skin.css','skin.json','deployment.json','run-ui.sh','ui-bridge/proxy.js','capability-port/port.js']
# Marker the proxy stamps on the hook script tag. Must match HOOK_MARKER in proxy.js or HOOK always fails.
HOOK_MARKER='data-capability-hook'
# Route the proxy serves port.js from. Must match PORT_ROUTE in proxy.js.
PORT_ROUTE='/_cp/port.js'
# Route prefix the proxy serves skins from. Must match proxy.js.
SKIN_PREFIX='/_cs/'
# Customer header and id the proxy expects. Blank if the proxy injects without one.
CUSTOMER_HEADER='' 
# Seconds to wait for any single request before calling it down.
TIMEOUT=int(os.environ.get('APP_BUILDER_WATCHER_TIMEOUT','10'))
# Seconds between sweeps when run with --loop. Lower = faster detection, more load.
INTERVAL_SECONDS=int(os.environ.get('APP_BUILDER_WATCHER_INTERVAL','300'))
# Run the real-browser CLEAN stage (needs Playwright + Chromium installed). False marks stage 6 NOT_RUN; this is test-only and is not the production default.
BROWSER_CHECK=os.environ.get('APP_BUILDER_BROWSER_CHECK','true').lower() in {'1','true','yes'}
# Seconds the real browser may take to load a page. Heavy apps (Grafana, Appsmith) need more than TIMEOUT.
BROWSER_TIMEOUT=int(os.environ.get('APP_BUILDER_BROWSER_TIMEOUT','45'))
# After the page loads, seconds to wait for the network to go quiet. Apps with live connections
# (websockets, polling) never go quiet, so this is a grace period, not a requirement.
NETWORK_IDLE_GRACE=int(os.environ.get('APP_BUILDER_NETWORK_IDLE_GRACE','8'))
# true = old behaviour: ANY console error or failed request fails stage 6, including third-party
# noise (analytics, fonts, favicon, cancelled requests). false = those are recorded as warnings;
# errors from the app's own origin and uncaught page exceptions still fail.
BROWSER_STRICT=os.environ.get('APP_BUILDER_BROWSER_STRICT','false').lower() in {'1','true','yes'}
# true = when stage 6 sees errors, load the same page straight from the app (no proxy/skin/hook).
# Errors the bare app shows too are its own behaviour and are recorded, not failed; errors that only
# appear through the proxy still fail. false = any error fails, even ones the app ships with.
BASELINE_COMPARE=os.environ.get('APP_BUILDER_BASELINE_COMPARE','true').lower() in {'1','true','yes'}
# Where results go. One JSON line per app per sweep.
ROOT=Path(os.environ.get('APP_BUILDER_ROOT','/srv/app-builder'))
TARGET_DIR=ROOT/'state/apps'; LOG=ROOT/'state/system-watcher.log'
SKIN_RE=re.compile(r'<link\s+[^>]*href=["\'](/_cs/[^"\']+\.css)[^>]*>',re.I)
HOOK_RE=re.compile(r'<script\s+[^>]*(?:data-capability-hook=["\']1["\'][^>]*src=["\']([^"\']*?_cp/port\.js)["\']|src=["\']([^"\']*?_cp/port\.js)["\'][^>]*data-capability-hook=["\']1["\'])[^>]*>',re.I)

def sha(b): return hashlib.sha256(b).hexdigest()
_OPENER=build_opener()
def get(url, headers=None):
    req=Request(url,headers={'Accept-Encoding':'identity',**(headers or {})})
    try:
        with _OPENER.open(req,timeout=TIMEOUT) as r:return r.status,dict(r.headers.items()),r.read(),r.geturl()
    except HTTPError as e:return e.code,dict(e.headers.items()),e.read(),e.geturl()
    except (socket.timeout,TimeoutError) as e:return None,{'x-watcher-error':'TIMEOUT'},str(e).encode(),url
    except URLError as e:
        reason=getattr(e,'reason',None)
        code='TIMEOUT' if isinstance(reason,(socket.timeout,TimeoutError)) or 'timed out' in str(reason).lower() else 'UNREACHABLE'
        return None,{'x-watcher-error':code},str(e).encode(),url
    except Exception as e:return None,{},str(e).encode(),url

def stage(status='OK',code=None,detail=None):
    d={'status':status}
    if code:d['code']=code
    if detail:d['detail']=detail
    return d

def _app_numbers():
    cache=getattr(_app_numbers,"_c",None)
    if cache is not None: return cache
    m={}
    try:
        raw=json.loads((ROOT/"upstream_apps.json").read_text())
        for e in raw.get("entries",[]):
            name=e.get("app")
            if name is not None and "number" in e:
                m[name]=e["number"]
                m[re.sub(r"[^a-z0-9-]+","-",str(name).lower()).strip("-")]=e["number"]
    except Exception:
        m={}
    _app_numbers._c=m
    return m

def _number_for(app):
    m=_app_numbers()
    return m.get(app, m.get(re.sub(r"[^a-z0-9-]+","-",str(app).lower()).strip("-")))

def result(app,stages):
    bad=next(((k,v) for k,v in stages.items() if v.get('status')=='FAIL'),None)
    if bad:
        # The watcher is read-only: once a stage fails, all later acceptance stages are skipped.
        order=['1 INSTALLED','2 APP_UP','3 PROXY_UP','4 SKIN','5 HOOK','6 CLEAN']
        first=order.index(bad[0]) if bad[0] in order else len(order)
        for k in order[first+1:]:
            stages.setdefault(k,stage('SKIPPED','UPSTREAM_FAILURE'))
        verdict=f'BROKEN AT {bad[0]} — {bad[1].get("code","")}'
    elif stages.get('6 CLEAN',{}).get('status') in {'OK','NOT_RUN'}:
        verdict='PASS' if stages.get('6 CLEAN',{}).get('status')=='OK' else 'PASS (CLEAN not run)'
    else:
        verdict='FAIL'
    return {'app':app,'number':_number_for(app),'verdict':verdict,'broken_at':bad[0] if bad else None,'code':bad[1].get('code') if bad else None,'stages':stages,'ts':time.time()}

def fail(app, stages, failed_name, code, detail=None):
    stages[failed_name]=stage('FAIL',code,detail)
    return result(app,stages)

def base_stages():
    return {}

def _is_noise(url,text,origin):
    """Browser errors that say nothing about whether the app works."""
    u=(url or '').split('?',1)[0]
    if u.endswith('/favicon.ico'): return True                      # missing favicon
    if 'ERR_ABORTED' in (text or '') or 'NS_BINDING_ABORTED' in (text or ''): return True  # request cancelled by the page itself
    if u.startswith(('http://','https://','ws://','wss://')) and not u.startswith(origin): return True  # third-party (analytics, CDN fonts)
    # A logged-out visitor's page asks "who am I?" / "refresh my token" and gets 401/403. That is the
    # app working correctly (it then shows its login screen), not the app broken. Recorded as noise.
    if re.search(r'status of 40[13]\b',text or ''): return True
    return False

def _split_noise(origin,console_errors,request_failures):
    """(real console errors, real failed requests, ignored noise). Strict mode: nothing is noise."""
    if BROWSER_STRICT:
        return [t for _,t in console_errors],[f"{u}: {f}" for u,f in request_failures],[]
    bad_c,bad_r,warn=[],[],[]
    for src,text in console_errors:
        (warn if _is_noise(src,text,origin) else bad_c).append(text if not src else f"{text} ({src})")
    for u,f in request_failures:
        (warn if _is_noise(u,f,origin) else bad_r).append(f"{u}: {f}")
    return bad_c,bad_r,warn

def _norm_err(text):
    """An error message with hosts/ports taken out, so the proxied and bare copies compare equal."""
    return re.sub(r'https?://[^/\s)]+','<origin>',str(text)).strip()

def _baseline(p,executable,app_url):
    """Errors the bare app (no proxy) shows on the same page. None if it couldn't be loaded."""
    b=None
    try:
        b=p.chromium.launch(headless=True, executable_path=executable) if executable else p.chromium.launch(headless=True)
        pg=b.new_page(); errs=[]; cons=[]; reqs=[]
        pg.on('pageerror',lambda exc: errs.append(str(exc)))
        def on_console(m):
            if m.type!='error': return
            try: src=(m.location or {}).get('url','')
            except Exception: src=''
            cons.append(m.text if not src else f"{m.text} ({src})")
        pg.on('console',on_console)
        pg.on('requestfailed',lambda r: reqs.append(f"{r.url}: {r.failure}"))
        pg.goto(app_url,wait_until='load',timeout=BROWSER_TIMEOUT*1000)
        try: pg.wait_for_load_state('networkidle',timeout=NETWORK_IDLE_GRACE*1000)
        except Exception: pass
        found=set()
        for x in errs+reqs: found.add(_norm_err(x))
        for x in cons: found.add(_norm_err(x))
        return found
    except Exception:
        return None
    finally:
        try:
            if b: b.close()
        except Exception: pass

import threading
_TL=threading.local()   # per-thread: apps are checked in parallel

def adapter_verdict(expected,seen):
    """The capability adapter is in one of two states and each app says which it should be in:
      dormant  (default)  hook present, nothing mounted: no anchors, no capability elements
      active              exactly the listed capabilities mounted, nothing else
    Returns None when the page matches, else (code, detail)."""
    exp=expected or {'mode':'dormant'}
    if exp.get('mode','dormant')=='dormant':
        if seen['anchors'] or seen['owned']:
            return 'PORT_NOT_DORMANT',f"expected dormant; found {seen['anchors']} anchor(s), {seen['owned']} capability element(s): {seen.get('capabilities')}"
        return None
    want=sorted(set(exp.get('capabilities') or []))
    got=sorted(set(seen.get('capabilities') or []))
    if not got: return 'ADAPTER_NOT_ACTIVE',f"expected active with {want}; nothing mounted"
    missing=[c for c in want if c not in got]; extra=[c for c in got if c not in want]
    if missing: return 'ADAPTER_MISSING_CAPABILITY',f"expected {want}; missing {missing}"
    if extra: return 'ADAPTER_UNEXPECTED_CAPABILITY',f"expected {want}; also mounted {extra}"
    return None

def adapter_expected(t):
    """What the adapter should be doing for this app: target file, else the app catalogue entry's
    `adapter` field (edit it there; a sync never overwrites it), else dormant."""
    if isinstance(t.get('adapter_expected'),dict): return t['adapter_expected']
    try:
        cat=json.loads((ROOT/'app_catalogue.json').read_text()).get('apps',{})
        for k,e in cat.items():
            if re.sub(r'[^a-z0-9-]+','-',str(e.get('app',k)).lower()).strip('-')==t.get('app') and isinstance(e.get('adapter'),dict):
                return e['adapter']
    except Exception: pass
    return {'mode':'dormant'}

def browser_check(url, app_url=None, expected=None):
    if not BROWSER_CHECK:return stage('NOT_RUN','BROWSER_DISABLED')
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return stage('FAIL','PLAYWRIGHT_UNAVAILABLE',str(e))
    errors=[]; console_errors=[]; request_failures=[]; css_ok=[]; hook_ok=[]
    browser=None
    try:
        expected=urlparse(url)
        expected_origin=f"{expected.scheme}://{expected.netloc}"
        with sync_playwright() as p:
            executable=os.environ.get('APP_BUILDER_CHROMIUM_PATH') or next((x for x in (shutil.which('chromium'),shutil.which('chromium-browser'),shutil.which('google-chrome')) if x),None)
            browser=p.chromium.launch(headless=True, executable_path=executable) if executable else p.chromium.launch(headless=True)
            page=browser.new_page()
            page.on('pageerror',lambda exc: errors.append(str(exc)))
            def on_console(msg):
                if msg.type!='error': return
                try: src=(msg.location or {}).get('url','')
                except Exception: src=''
                console_errors.append((src,msg.text))
            page.on('console',on_console)
            page.on('requestfailed',lambda req: request_failures.append((req.url,str(req.failure))))
            def on_response(resp):
                if resp.url.startswith(expected_origin+'/_cs/') and resp.url.endswith('.css'): css_ok.append(resp.status)
                if resp.url.startswith(expected_origin+PORT_ROUTE): hook_ok.append(resp.status)
            page.on('response',on_response)
            resp=page.goto(url,wait_until='load',timeout=BROWSER_TIMEOUT*1000)
            try: page.wait_for_load_state('networkidle',timeout=NETWORK_IDLE_GRACE*1000)
            except Exception: pass  # live connections keep the network busy; that is not a failure
            if not resp or resp.status>=500:return stage('FAIL','BROWSER_HTTP',str(resp.status if resp else 'NO_RESPONSE'))
            if not page.url.startswith(expected_origin):return stage('FAIL','REDIRECT_OFF_PROXY',page.url)
            bad_console,bad_requests,warnings=_split_noise(expected_origin,console_errors,request_failures)
            app_own=[]
            if (errors or bad_console or bad_requests) and BASELINE_COMPARE and app_url:
                # Same page, straight from the app with no proxy, skin or hook in front. An error the
                # bare app throws too is the app's own behaviour, not something the overlay broke:
                # recorded, not failed. Anything that only happens through the proxy still fails.
                base=_baseline(p,executable,app_url)
                if base is not None:
                    own=lambda x:_norm_err(x) in base
                    app_own=[x for x in errors+bad_console+bad_requests if own(x)]
                    errors=[x for x in errors if not own(x)]
                    bad_console=[x for x in bad_console if not own(x)]
                    bad_requests=[x for x in bad_requests if not own(x)]
            if errors:return stage('FAIL','PAGE_ERROR',errors[0])
            if bad_console:return stage('FAIL',f'CONSOLE_ERRORS:{len(bad_console)}',json.dumps(bad_console[:10]))
            if bad_requests:return stage('FAIL','REQUEST_FAILED',json.dumps(bad_requests[:10]))
            body=page.locator('body')
            if body.count()==0 or not body.inner_text().strip():return stage('FAIL','EMPTY_BODY')
            links=page.locator('link[href^="/_cs/"][href$=".css"]')
            hooks=page.locator('script[data-capability-hook="1"][src*="/_cp/port.js"]')
            if links.count()!=1:return stage('FAIL','BROWSER_SKIN_LINK_COUNT',str(links.count()))
            if hooks.count()!=1:return stage('FAIL','BROWSER_HOOK_COUNT',str(hooks.count()))
            if not css_ok or any(x!=200 for x in css_ok):return stage('FAIL','CSS_FAILED_TO_LOAD',json.dumps(css_ok))
            if not hook_ok or any(x!=200 for x in hook_ok):return stage('FAIL','PORT_FAILED_TO_LOAD',json.dumps(hook_ok))
            # Dormant-port check: only the port's explicit anchor/owned attributes count.
            # Attributes capability-port/port.js actually writes at runtime (grepped against the
            # delivered port.js, 2026-09-22):
            #   data-capability        - slot.dataset.capability on the mounted capability's <section>
            #                            (port.js: attach(), sandboxModule() 'mounted' handler)
            #   data-capability-ui     - dataset.capabilityUi on injected capability UI overlays
            #                            (capability-port/caps/inspector.mjs)
            #   data-capability-anchor - target.dataset.capabilityAnchor, written by placementRecord()
            #                            when a mount needs a runtime anchor and the target has no id
            #                            (port.js); also the attribute an app author may pre-author in
            #                            their own markup (capability-port/README.md), which is why it
            #                            is counted separately as `anchors`, not `owned`.
            # `data-capability-port` and `data-capability-instance` do not appear anywhere in port.js
            # or its caps/*.mjs modules and were removed from the owned filter below — checking only
            # those two names let an actually-mounted capability (data-capability) pass as dormant.
            seen=page.evaluate("""() => {
              const all=[...document.querySelectorAll('*')];
              const anchors=all.filter(e=>e.hasAttribute('data-capability-anchor'));
              const owned=all.filter(e=>e.tagName!=='SCRIPT' && (e.hasAttribute('data-capability') || e.hasAttribute('data-capability-ui')));
              const caps=[...new Set(all.filter(e=>e.hasAttribute('data-capability')).map(e=>e.dataset.capability))].sort();
              return {anchors:anchors.length,owned:owned.length,capabilities:caps};
            }""")
            _TL.seen=seen
            # The adapter being hooked in is checked in stage 5 (HOOK). Dormant or active is recorded
            # here (result['adapter']), never failed: the adapter may legitimately be either.
        extra={k:v for k,v in (('ignored_noise',warnings[:20]),('app_own_errors',app_own[:20])) if v}
        return stage('OK','CLEAN_OK',json.dumps(extra) if extra else None)
    except Exception as e:return stage('FAIL','BROWSER_EXCEPTION',str(e))
    finally:
        try:
            if browser: browser.close()
        except Exception: pass

def qualification_profile(t):
    """web (default) | service | system | package. From the target, else from the app's intake
    ledger (<app>/.atta-intake.json, written by intake.py). Unknown -> web, the old behaviour."""
    prof=t.get('profile')
    if not prof:
        try: prof=json.loads((Path(t['ui_dir']).parent/'.atta-intake.json').read_text()).get('qualification')
        except Exception: prof=None
    return prof if prof in {'web','service','system','package'} else 'web'

def build_readiness(t):
    """Stage six for system projects and packages: nothing to open, so check the toolchain
    evidence intake recorded. Docker builds use the container's toolchain, so they pass."""
    try: led=json.loads((Path(t['ui_dir']).parent/'.atta-intake.json').read_text())
    except Exception: return stage('FAIL','NO_INTAKE_RECORD','run intake.py for this app')
    bad=[x for x in led.get('toolchain',[]) if not x.get('ok')]
    if bad and not led.get('docker_build'):
        return stage('FAIL','TOOLCHAIN_TOO_OLD','; '.join(f"{x['tool']} needs {x['needs']}, has {x.get('has') or 'none'}" for x in bad))
    return stage('OK','BUILD_READY')

NOT_APPLICABLE=lambda why: stage('NOT_APPLICABLE',why)

def check(t):
    app=t.get('app',t.get('app_id','unknown')) if isinstance(t,dict) else 'unknown'
    stages={}
    current_stage="1 INSTALLED"
    required_keys={'ui_dir','target_url','proxy_url'}
    if not isinstance(t,dict) or not required_keys <= t.keys():
        return result(app,{'TARGETS':stage('FAIL','INVALID_TARGET_CONFIG')})
    try:
        ui=Path(t['ui_dir']); app_url=t.get('app_url',t['target_url']); proxy=t['proxy_url'].rstrip('/')+'/'
        required=REQUIRED_FILES
        for f in required:
            p=ui/f
            if not p.is_file(): return fail(app,stages,'1 INSTALLED','FILE_MISSING:'+f)
            if p.stat().st_size==0: return fail(app,stages,'1 INSTALLED','EMPTY:'+f)
        if not os.access(ui/'run-ui.sh',os.X_OK): return fail(app,stages,'1 INSTALLED','NOT_EXECUTABLE')
        try: json.loads((ui/'skin.json').read_text())
        except Exception: return fail(app,stages,'1 INSTALLED','BAD_JSON')
        if 'data-capability-hook' not in (ui/'ui-bridge/proxy.js').read_text(errors='replace'):
            return fail(app,stages,'1 INSTALLED','PROXY_STALE')
        stages['1 INSTALLED']=stage()
        profile=qualification_profile(t)
        if profile in {'system','package'}:
            # Nothing runs as a web app: the web stages don't apply, stage six checks build readiness.
            for k in ('2 APP_UP','3 PROXY_UP','4 SKIN','5 HOOK'): stages[k]=NOT_APPLICABLE(profile.upper()+'_PROJECT')
            current_stage='6 CLEAN'; stages['6 CLEAN']=build_readiness(t)
            return result(app,stages)
        ac,ah,ab,au=get(app_url)
        if ac is None:
            return fail(app,stages,'2 APP_UP', 'TIMEOUT' if ah.get('x-watcher-error')=='TIMEOUT' else 'UNREACHABLE',str(ac))
        if ac>=500: return fail(app,stages,'2 APP_UP','UPSTREAM_5XX',str(ac))
        stages['2 APP_UP']=stage(detail=str(ac))
        pc,ph,pb,pu=get(proxy)
        if pc is None: return fail(app,stages,'3 PROXY_UP','TIMEOUT' if ph.get('x-watcher-error')=='TIMEOUT' else 'REFUSED',str(pc))
        if pc>=500:
            if pc==502: return fail(app,stages,'3 PROXY_UP','UPSTREAM_502',str(pc))
            return fail(app,stages,'3 PROXY_UP','HTTP_'+str(pc),str(pc))
        if pc==400 and pb.lstrip().startswith(b'FAIL:'):
            return fail(app,stages,'3 PROXY_UP','PROXY_400',pb.decode('utf-8','replace').strip())
        # Spec 2.4: confirm the final URL is still on the proxy *origin* (scheme+host+port), not that
        # it is the exact same path — urllib already follows same- and cross-origin redirects, and a
        # same-origin redirect to a different path (e.g. `/` -> `/index.html`) is not an off-proxy hop.
        proxy_origin=f"{urlparse(proxy).scheme}://{urlparse(proxy).netloc}"
        if f"{urlparse(pu).scheme}://{urlparse(pu).netloc}" != proxy_origin:
            return fail(app,stages,'3 PROXY_UP','REDIRECT_OFF_PROXY',pu)
        if 300<=pc<400: return fail(app,stages,'3 PROXY_UP','REDIRECT',str(pc))
        if profile=='service':
            # A service answers on a port; it has no page to skin, hook or open in a browser.
            stages['3 PROXY_UP']=stage(detail=str(pc))
            stages['4 SKIN']=NOT_APPLICABLE('SERVICE'); stages['5 HOOK']=NOT_APPLICABLE('SERVICE')
            current_stage='6 CLEAN'; stages['6 CLEAN']=stage('OK','SERVICE_RESPONDS',f'app {ac}, proxy {pc}')
            return result(app,stages)
        ctype=next((v for k,v in ph.items() if k.lower()=='content-type'),'')
        if 'text/html' not in ctype.lower(): return fail(app,stages,'3 PROXY_UP','NOT_HTML')
        stages['3 PROXY_UP']=stage(detail=str(pc))
        current_stage='4 SKIN'
        html=pb.decode('utf-8','replace'); head=html.lower().find('</head>')
        skin_matches=list(SKIN_RE.finditer(html)); hook_matches=list(HOOK_RE.finditer(html)); links=[m.group(1) for m in skin_matches]
        if head<0: return fail(app,stages,'4 SKIN','NO_HEAD')
        if len(skin_matches)==0: return fail(app,stages,'4 SKIN','NO_LINK')
        if len(skin_matches)>1: return fail(app,stages,'4 SKIN','DUPLICATE_LINK')
        if skin_matches[0].start()>head: return fail(app,stages,'4 SKIN','LINK_AFTER_HEAD')
        css_url=proxy.rstrip('/')+links[0]; cc,ch,cb,cu=get(css_url)
        if cc!=200: return fail(app,stages,'4 SKIN','CSS_404',str(cc))
        ctype=next((v for k,v in ch.items() if k.lower()=='content-type'),'')
        if 'text/css' not in ctype.lower(): return fail(app,stages,'4 SKIN','NOT_CSS',ctype)
        if not cb: return fail(app,stages,'4 SKIN','CSS_EMPTY')
        if sha(cb)!=sha((ui/'skin.css').read_bytes()): return fail(app,stages,'4 SKIN','CSS_MISMATCH')
        stages['4 SKIN']=stage()
        current_stage='5 HOOK'
        if len(hook_matches)==0: return fail(app,stages,'5 HOOK','NO_TAG')
        if len(hook_matches)>1: return fail(app,stages,'5 HOOK','DUPLICATE_TAG')
        if hook_matches[0].start()>head: return fail(app,stages,'5 HOOK','TAG_AFTER_HEAD')
        rc,rh,rb,ru=get(proxy.rstrip('/')+PORT_ROUTE)
        if rc!=200: return fail(app,stages,'5 HOOK','PORT_404',str(rc))
        jtype=next((v for k,v in rh.items() if k.lower()=='content-type'),'')
        if 'javascript' not in jtype.lower(): return fail(app,stages,'5 HOOK','NOT_JAVASCRIPT',jtype)
        if not rb: return fail(app,stages,'5 HOOK','PORT_EMPTY')
        if ru.split('?',1)[0].rstrip('/') != (proxy.rstrip('/')+PORT_ROUTE).rstrip('/'):
            return fail(app,stages,'5 HOOK','PORT_REDIRECT_OFF_PROXY',ru)
        if sha(rb)!=sha((ui/'capability-port/port.js').read_bytes()): return fail(app,stages,'5 HOOK','PORT_MISMATCH')
        stages['5 HOOK']=stage(); current_stage='6 CLEAN'
        exp=adapter_expected(t); _TL.seen=None
        stages['6 CLEAN']=browser_check(proxy,app_url,exp)
        r=result(app,stages)
        seen=getattr(_TL,'seen',None)
        r['adapter']={'state':('dormant' if seen and not seen['anchors'] and not seen['owned'] else
                               'active' if seen else None),
                      'capabilities':(seen or {}).get('capabilities') or [],'seen':seen,'expected':exp,
                      'matches_expected':bool(seen) and adapter_verdict(exp,seen) is None}
        return r
    except Exception as e:
        return fail(app,stages,current_stage,'WATCHER_EXCEPTION',str(e))

def load_targets():
    out=[]
    for p in sorted(TARGET_DIR.glob('*.json')):
        try:
            t=json.loads(p.read_text())
            # Parked = the app runner stopped it after its check (APP_BUILDER_KEEP_RUNNING=false).
            # A stopped app is not "down"; its last result lives in state/runner/results/.
            if isinstance(t,dict) and t.get('parked'): continue
            out.append(t)
        except Exception as e:out.append({'app':p.stem,'_load_error':str(e)})
    return out

def stage_line(name,d):
    st=d.get('status','?'); code=d.get('code','')
    return f"  {name:<15} {st:<8} {code}".rstrip()

def print_result(r):
    print(r['app'])
    for name in ['1 INSTALLED','2 APP_UP','3 PROXY_UP','4 SKIN','5 HOOK','6 CLEAN']:
        if name in r['stages']: print(stage_line(name,r['stages'][name]))
    print(f"  VERDICT: {r['verdict']}")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--loop',action='store_true'); args=ap.parse_args(); last={}; last_code=0
    try:
        while True:
            targets=load_targets()
            if not targets:
                r={'app':'__watcher__','verdict':'FAIL','broken_at':'TARGETS','code':'NO_TARGETS','stages':{'TARGETS':stage('FAIL','NO_TARGETS')},'ts':time.time()}
                results=[r]
            else: results=[check(t) for t in targets]
            LOG.parent.mkdir(parents=True,exist_ok=True)
            with LOG.open('a') as f:
                for r in results:f.write(json.dumps(r)+'\n')
            current_code=0 if targets and all(r.get('broken_at') is None for r in results) else 1
            if not args.loop:
                for r in results: print_result(r)
                print('PASS' if current_code==0 else 'FAIL')
                return current_code
            for r in results:
                if last.get(r['app'])!=r['verdict']:
                    print_result(r)
                last[r['app']]=r['verdict']
            last_code=current_code
            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print('PASS' if last_code==0 else 'FAIL')
        return last_code

if __name__=='__main__':raise SystemExit(main())
