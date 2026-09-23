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
    return {'app':app,'verdict':verdict,'broken_at':bad[0] if bad else None,'code':bad[1].get('code') if bad else None,'stages':stages,'ts':time.time()}

def fail(app, stages, failed_name, code, detail=None):
    stages[failed_name]=stage('FAIL',code,detail)
    return result(app,stages)

def base_stages():
    return {}

def browser_check(url):
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
            page.on('console',lambda msg: console_errors.append(msg.text) if msg.type=='error' else None)
            page.on('requestfailed',lambda req: request_failures.append(f"{req.url}: {req.failure}"))
            def on_response(resp):
                if resp.url.startswith(expected_origin+'/_cs/') and resp.url.endswith('.css'): css_ok.append(resp.status)
                if resp.url.startswith(expected_origin+PORT_ROUTE): hook_ok.append(resp.status)
            page.on('response',on_response)
            resp=page.goto(url,wait_until='networkidle',timeout=TIMEOUT*1000)
            if not resp or resp.status>=500:return stage('FAIL','BROWSER_HTTP',str(resp.status if resp else 'NO_RESPONSE'))
            if not page.url.startswith(expected_origin):return stage('FAIL','REDIRECT_OFF_PROXY',page.url)
            if errors:return stage('FAIL','PAGE_ERROR',errors[0])
            if console_errors:return stage('FAIL',f'CONSOLE_ERRORS:{len(console_errors)}',json.dumps(console_errors[:10]))
            if request_failures:return stage('FAIL','REQUEST_FAILED',json.dumps(request_failures[:10]))
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
            dormant=page.evaluate("""() => {
              const all=[...document.querySelectorAll('*')];
              const anchors=all.filter(e=>e.hasAttribute('data-capability-anchor'));
              const owned=all.filter(e=>e.tagName!=='SCRIPT' && (e.hasAttribute('data-capability') || e.hasAttribute('data-capability-ui')));
              return {anchors:anchors.length,owned:owned.length};
            }""")
            if dormant['anchors'] or dormant['owned']:
                return stage('FAIL','PORT_NOT_DORMANT',f"{dormant['anchors']}/{dormant['owned']}")
        return stage('OK','CLEAN_OK')
    except Exception as e:return stage('FAIL','BROWSER_EXCEPTION',str(e))
    finally:
        try:
            if browser: browser.close()
        except Exception: pass

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
        stages['5 HOOK']=stage(); current_stage='6 CLEAN'; stages['6 CLEAN']=browser_check(proxy)
        return result(app,stages)
    except Exception as e:
        return fail(app,stages,current_stage,'WATCHER_EXCEPTION',str(e))

def load_targets():
    out=[]
    for p in sorted(TARGET_DIR.glob('*.json')):
        try:out.append(json.loads(p.read_text()))
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
