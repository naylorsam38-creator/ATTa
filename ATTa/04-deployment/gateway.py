#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import hashlib,hmac,html,json,os,secrets,time,re,threading,zipfile
import accounts, builds, alerts, app_owners, customer_secrets
ROOT=Path(os.environ.get("APP_BUILDER_ROOT","/srv/app-builder"));
LOGIN_WINDOW=int(os.environ.get("APP_BUILDER_LOGIN_WINDOW","900")); LOGIN_MAX_FAILURES=int(os.environ.get("APP_BUILDER_LOGIN_MAX_FAILURES","8")); _LOGIN_FAILURES={}
_LOGIN_LOCK=threading.Lock()
INBOX=ROOT/"inbox"; STATUS=ROOT/"state/status.json"; FRONT=ROOT/"front-door.html"; CHOICES=ROOT/"state/front-door-choices"
HOST=os.environ.get("APP_BUILDER_HOST","127.0.0.1"); PORT=int(os.environ.get("APP_BUILDER_PORT","8787")); SECRET=os.environ.get("APP_BUILDER_SESSION_SECRET",""); MAX=int(os.environ.get("APP_BUILDER_MAX_UPLOAD","10737418240")); SESSION_TTL=int(os.environ.get("APP_BUILDER_SESSION_TTL","86400"))
# Local testing escape hatch: when APP_BUILDER_AUTH_DISABLED=1 the gateway skips the login
# entirely and every request acts as a local admin. Never set on a server.
# v115: that identity is not an account, so its uploads add apps but can never update the system itself
# (pipeline.may_update_system needs a real enabled admin account for a web build).
AUTH_DISABLED=os.environ.get("APP_BUILDER_AUTH_DISABLED","")=="1"
LOCAL_ADMIN={"name":"local-admin","role":"admin"}
for p in (INBOX,STATUS.parent,CHOICES): p.mkdir(parents=True,exist_ok=True)
if not AUTH_DISABLED and not SECRET: raise SystemExit("APP_BUILDER_SESSION_SECRET is required")
if not AUTH_DISABLED and not accounts.load()["users"]: raise SystemExit("No accounts exist. Run: python3 accounts.py init")
def client_ip(h):
 x=h.headers.get("X-Real-IP") or h.client_address[0]
 return x.split(",",1)[0].strip()

def login_allowed(ip):
 now=time.time()
 with _LOGIN_LOCK:
  rec=_LOGIN_FAILURES.get(ip)
  if not rec:return True
  if now-rec[0]>LOGIN_WINDOW:
   _LOGIN_FAILURES.pop(ip,None); return True
  return rec[1] < LOGIN_MAX_FAILURES

def record_login_failure(ip):
 now=time.time()
 with _LOGIN_LOCK:
  rec=_LOGIN_FAILURES.get(ip)
  if not rec or now-rec[0]>LOGIN_WINDOW: _LOGIN_FAILURES[ip]=(now,1)
  else: _LOGIN_FAILURES[ip]=(rec[0],rec[1]+1)

def clear_login_failures(ip):
 with _LOGIN_LOCK: _LOGIN_FAILURES.pop(ip,None)

def sig(v): return hmac.new(SECRET.encode(),v.encode(),hashlib.sha256).hexdigest()
def token(u):
 # name:session_version:issued:nonce, signed. Bumping session_version (password reset,
 # disable) ends every session that account already has.
 value=f"{u['name']}:{int(u.get('session_version',1))}:{int(time.time())}:{secrets.token_urlsafe(18)}"
 return value+"."+sig(value)
def auth(h):
 """The logged-in account (dict with name + role), or None."""
 if AUTH_DISABLED: return LOCAL_ADMIN
 for x in h.headers.get("Cookie","").split(";"):
  if x.strip().startswith("session="):
   try:
    value,s=x.strip().split("=",1)[1].rsplit(".",1)
    if not hmac.compare_digest(s,sig(value)): continue
    name,ver,ts,_=value.split(":",3)
    if not 0 <= int(time.time())-int(ts) <= SESSION_TTL: continue
    u=accounts.get(name)
    if u and not u.get("disabled") and int(u.get("session_version",1))==int(ver): return u
   except Exception: pass
 return None
def page(title,body,me=None):
 nav=""
 if me:
  links='<a href=/>Front Door</a> <a href=/builds>'+("All builds" if me["role"]=="admin" else "My builds")+'</a> <a href=/upload>Add app</a> <a href=/library>Library</a>'
  if me["role"]=="admin": links+=' <a href=/status>System status</a> <a href=/alerts>Alerts</a>'
  nav='<nav>'+links+'<span>'+html.escape(me["name"])+' ('+html.escape(me["role"])+')'+(' <a href=/logout>Log out</a>' if not AUTH_DISABLED else '')+'</span></nav>'
 return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>body{font-family:system-ui;margin:40px;max-width:900px}input,button{padding:10px;margin:6px 0}pre{background:#f4f4f4;padding:12px;overflow:auto}nav{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:24px;padding-bottom:12px;border-bottom:1px solid #ddd}nav span{margin-left:auto;color:#666}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #eee}.QUALIFIED,.MAPPED{color:#0a7a2f;font-weight:600}.PARTIALLY_QUALIFIED{color:#9a6700;font-weight:600}.FAILED,.NOT_QUALIFIED{color:#b00020;font-weight:600}</style></head><body>'+nav+body+'</body></html>').encode()
EVIDENCE_FILES={"screenshot.png":"image/png","page.html":"text/plain; charset=utf-8","browser.json":"application/json"}
EVIDENCE_DIR=ROOT/"state/runner/evidence"
# v115: security headers. Every ATTa page is built here with no script at all, so its CSP allows none.
# The Front Door carries its own inline script and keeps a looser policy that still forbids framing.
CSP_PAGE="default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
CSP_FRONT="frame-ancestors 'self'; base-uri 'self'; object-src 'none'; form-action 'self'"
CSP_DATA="default-src 'none'; frame-ancestors 'none'; sandbox"
CSP_EVIDENCE="default-src 'none'; img-src 'self'; frame-ancestors 'none'; sandbox"
PNG_MAGIC=b"\x89PNG\r\n\x1a\n"
def evidence_response(f):
 """v115: stage-6 evidence is DATA from an untrusted app, never ATTa UI. (body, content type, disposition).
 A screenshot is shown only if it really is a PNG; JSON is JSON; page.html and anything else download as
 plain text/bytes, so the page the app produced can never run inside ATTa's origin."""
 b=f.read_bytes(); name=re.sub(r"[^A-Za-z0-9._-]","_",f.parent.name+"-"+f.name)
 if f.suffix.lower()==".png" and b.startswith(PNG_MAGIC): return b,"image/png","inline; filename=\""+name+"\""
 if f.suffix.lower()==".json":
  try: json.loads(b); return b,"application/json","inline; filename=\""+name+"\""
  except ValueError: pass
 if f.suffix.lower() in (".html",".htm",".txt",".log",".json"): return b,"text/plain; charset=utf-8","attachment; filename=\""+name+".txt\""
 return b,"application/octet-stream","attachment; filename=\""+name+"\""
def same_origin_post(h):
 """v115: a POST must come from ATTa's own pages. Browsers send Sec-Fetch-Site and Origin on every POST;
 another site (or another port / subdomain, e.g. an app deployed next to ATTa) is refused. A client that
 sends neither header (curl, API scripts with the session cookie) is not a browser and can't be tricked."""
 sfs=h.headers.get("Sec-Fetch-Site")
 if sfs and sfs.lower() not in ("same-origin","none"): return False
 o=h.headers.get("Origin") or h.headers.get("Referer")
 if not o: return True
 if o=="null": return False
 try:
  u=urlparse(o); hst=urlparse("//"+h.headers.get("Host",""))
  if not u.hostname or (u.hostname or "").lower()!=(hst.hostname or "").lower(): return False
  op=u.port or {"https":443,"http":80}.get(u.scheme)
  return op==hst.port if hst.port else op in (80,443)
 except ValueError: return False
_RATE_LOCK=threading.Lock(); _RATE={}
def rate_ok(user,what,per_min):
 now=time.time()
 with _RATE_LOCK:
  q=[t for t in _RATE.get((user,what),[]) if now-t<60]
  if len(q)>=per_min: _RATE[(user,what)]=q; return False
  q.append(now); _RATE[(user,what)]=q; return True
LIB=ROOT/"library"
def app_exists(a): return bool(a) and (LIB/a).is_dir()
def secrets_page(me,app,msg=""):
 """v115: the customer enters their own integration tokens for an app they added. The values go straight
 to Coolify (customer_secrets.py); this page only ever shows names, fingerprints and when."""
 r=customer_secrets.receipts(app).get("vars") or {}
 rows="".join("<tr><td><code>"+html.escape(n)+"</code></td><td><code>"+html.escape(v.get("fingerprint",""))+"</code></td><td>"+when(v.get("at"))+"</td><td>"+html.escape(str(v.get("build_id") or ""))+"</td><td>"+html.escape(str(v.get("by") or ""))+"</td><td>"+html.escape(str(v.get("delivered_to") or ""))+"</td></tr>" for n,v in sorted(r.items()))
 table=("<table><tr><th>Variable</th><th>Fingerprint</th><th>Delivered</th><th>Build</th><th>By</th><th>Held by</th></tr>"+rows+"</table>") if rows else "<p>No tokens delivered yet.</p>"
 form="<form method=post action=/apps/"+html.escape(app)+"/secrets autocomplete=off>"+"".join("<input name=name placeholder=VARIABLE_NAME size=28 autocomplete=off> <input name=value type=password placeholder=value size=40 autocomplete=new-password><br>" for _ in range(3))+"<button>Send to the app</button></form>"
 check="<h2>Check a token</h2><form method=post action=/apps/"+html.escape(app)+"/secrets/check autocomplete=off><input name=name placeholder=VARIABLE_NAME size=28 autocomplete=off> <input name=value type=password size=40 autocomplete=new-password> <button>Check</button></form>"
 return page("Tokens: "+app,"<h1>Integration tokens for "+html.escape(app)+"</h1>"+(("<p><b>"+html.escape(msg)+"</b></p>") if msg else "")+"<p>Your own service tokens (payments, AI, email, ...). Each value goes straight to this app's deployment in Coolify and is used by the running app. ATTa keeps only the variable name and a short fingerprint so you can check later which token was sent; the value itself is not kept here.</p>"+table+"<h2>Send tokens</h2>"+form+check,me)
def discovered_html(r,me=None):
 """v112: what the upload held (every app found in the zip) and, once checked, what the browser saw."""
 d=r.get("apps_discovered")
 ev={a.get("app"):a.get("evidence") for a in (r.get("qualification") or []) if a.get("evidence")}
 if not d and not ev: return ""
 out=""
 if d:
  out+="<h2>Apps found in the upload</h2><table><tr><th>App</th><th>Where in the zip</th><th>How it was recognised</th><th>Library</th></tr>"
  added=set(r.get("apps_added") or []); kept=set(r.get("apps_already_in_library") or [])
  out+="".join("<tr><td>"+html.escape(a.get("name",""))+"</td><td><code>"+html.escape(a.get("rel",""))+"</code></td><td>"+html.escape(a.get("how",""))+"</td><td>"+("added" if a.get("name") in added else "already there (library copy kept)" if a.get("name") in kept else "")+"</td></tr>" for a in d)+"</table>"
 if ev:
  ev={a:e for a,e in ev.items() if app_owners.can_access(me,a)}
  if ev: out+="<h2>What the browser saw (stage 6 evidence)</h2><p>"+" &middot; ".join("<b>"+html.escape(a)+"</b>: "+" ".join("<a href=/evidence/"+html.escape(a)+"/"+f+">"+f+"</a>" for f in EVIDENCE_FILES if e.get({"screenshot.png":"screenshot","page.html":"html","browser.json":"browser"}[f])) for a,e in ev.items())+"</p>"
 return out
def checklist_html(r):
 cl=r.get("checklist")
 if not cl: return ""
 sym={"PASS":"&#10003;","FAIL":"&#10007;","SKIP":"&middot;","N/A":"&ndash;","INFO":"i"}
 nos=[s["no"] for s in (cl.get("rows") or [{}])[0].get("steps",[])]
 names={s["no"]:s["name"] for s in (cl.get("rows") or [{}])[0].get("steps",[])}
 def cells(x):
  st={s["no"]:s for s in x.get("steps",[])}
  return "".join("<td title=\""+html.escape(names.get(n,"")+": "+st.get(n,{}).get("note",""))+"\" class="+("QUALIFIED" if st.get(n,{}).get("tick")=="PASS" else "FAILED" if st.get(n,{}).get("tick")=="FAIL" else "")+">"+sym.get(st.get(n,{}).get("tick"),"?")+"</td>" for n in nos)
 rows="".join("<tr><td>"+html.escape(x["no"])+"</td><td>"+html.escape(x["app"])+"</td><td class="+("QUALIFIED" if x["tick"]=="PASS" else "FAILED")+">"+html.escape(x["tick"])+"</td>"+cells(x)+"<td>"+html.escape(str(x.get("stopped_at") or ""))+"</td><td>"+html.escape(next((s.get("note","") for s in x.get("steps",[]) if s["no"]=="S11"),""))+"</td><td>"+html.escape(str(x.get("seconds") if x.get("seconds") is not None else ""))+"</td></tr>" for x in cl.get("rows",[]))
 key="<p><small>"+" &middot; ".join(html.escape(n+" "+names[n]) for n in nos)+"<br>&#10003; pass &nbsp; &#10007; fail &nbsp; &middot; not reached &nbsp; &ndash; not applicable &nbsp; i recorded</small></p>"
 head="<p>Expected <b>"+str(cl["expected"])+"</b> apps, ticked <b>"+str(cl["ticked"])+"</b>: "+str(cl["PASS"])+" pass, "+str(cl["FAIL"])+" fail, "+str(cl["NOT_CHECKED"])+" not checked &middot; <b>"+("COMPLETE" if cl["complete"] else "INCOMPLETE")+"</b></p>"
 if "regressions" in cl:
  head+="<p><b class="+("FAILED" if cl["regressions"] else "QUALIFIED")+">Regressions since last full run: "+str(len(cl["regressions"]))+"</b> "+html.escape(", ".join(cl["regressions"]))+" &middot; newly passing: "+html.escape(", ".join(cl.get("newly_passing") or []) or "none")+"</p>"
 return "<h2>Test checklist</h2>"+head+key+"<div style=overflow-x:auto><table><tr><th>No.</th><th>App</th><th>Result</th>"+"".join("<th>"+html.escape(n)+"</th>" for n in nos)+"<th>Stopped at</th><th>Adapter</th><th>Secs</th></tr>"+rows+"</table></div>"

ADM_JOURNAL=Path(os.environ.get("ATTA_ADM_ROOT",str(ROOT/"adm")))/"state/journal"
def adm_html(r):
    """System-update (ADM) outcome for a bundle build. Read from ADM's own journal — the pipeline only records the job id."""
    a=r.get("adm")
    if not a: return ""
    if not a.get("queued"): return "<h2>System update</h2><p>Not applied: "+html.escape(str(a.get("reason") or a.get("error") or ""))+"</p>"
    try: j=json.loads((ADM_JOURNAL/(a["job_id"]+".json")).read_text())
    except (OSError,ValueError,KeyError): return "<h2>System update</h2><p>Queued with ADM (job "+html.escape(str(a.get("job_id")))+"), no journal yet.</p>"
    ev=j.get("events") or []; last=ev[-1] if ev else {}
    rows="".join("<tr><td>"+html.escape(e.get("at",""))+"</td><td>"+html.escape(e.get("event",""))+"</td><td>"+html.escape(str(e.get("reason") or e.get("version") or ""))+"</td></tr>" for e in ev)
    return ("<h2>System update</h2><p>ADM job <code>"+html.escape(j.get("job_id",""))+"</code> &middot; verdict <b>"+html.escape(str(j.get("verdict")))+"</b>"
            +(" &middot; version "+html.escape(str(j.get("version"))) if j.get("version") else "")
            +(" &middot; log <code>"+html.escape(str(j.get("log")))+"</code>" if j.get("log") else "")+"</p><table>"+rows+"</table>")

def healing_html(r):
 h=r.get("healing") or {}
 if not h: return ""
 out="<h2>Self-healing</h2>"
 for layer,d in h.items():
  out+="<h3>"+html.escape(layer)+": "+html.escape(d.get("status",""))+" ("+str(d.get("rounds",0))+" round(s))</h3><table><tr><th>When</th><th>Tier</th><th>Item</th><th>Outcome</th><th>Detail</th></tr>"
  for c in d.get("chain",[]):
   it=c.get("item") or {}
   what=(it.get("app")+": " if it.get("app") else "")+it.get("key","") if it else ", ".join((x.get("app") or "")+" "+x.get("key","") for x in c.get("items",[]))
   det=c.get("why") or c.get("note") or "; ".join(a["name"]+(" ✗ " if a.get("error") else " ✓ ")+str(a.get("result",""))[:160] for a in c.get("actions",[])) or ", ".join(c.get("findings",[]))
   out+="<tr><td>"+when(c.get("at"))+"</td><td>"+str(c.get("tier"))+" "+html.escape(c.get("tier_name",""))+"</td><td>"+html.escape(what)+"</td><td>"+html.escape(c.get("outcome",""))+"</td><td>"+html.escape(str(det)[:600])+"</td></tr>"
  out+="</table>"
 return out
def when(t): return time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(t)) if t else ""
def builds_table(rows,me):
 if not rows: return "<p>No builds yet. <a href=/upload>Upload a bundle</a> to start one.</p>"
 admin=me["role"]=="admin"
 h="<table><tr><th>Build</th>"+("<th>Owner</th>" if admin else "")+"<th>State</th><th>Coolify</th><th>Started</th></tr>"
 for r in rows:
  st=html.escape(r.get("state","")); c=(r.get("coolify") or {}).get("status","")
  h+="<tr><td><a href=/builds/"+html.escape(r["id"])+">"+html.escape(r["id"])+"</a></td>"+("<td>"+html.escape(r.get("owner",""))+"</td>" if admin else "")+"<td class="+st+">"+st+"</td><td>"+html.escape(c)+"</td><td>"+when(r.get("created"))+"</td></tr>"
 return h+"</table>"
FRONT_NAV_TMPL='<div id="ab-nav" style="position:fixed;top:8px;right:12px;z-index:99;font:13px system-ui;background:rgba(255,255,255,.9);padding:4px 10px;border-radius:8px;border:1px solid #ddd"><a href="/builds">{b}</a> &middot; <a href="/upload">Add app</a> &middot; <a href="/library">Library</a>{extra}</div>'
# ---- Front Door model (server side). Config via .env; nothing below needs editing.
FD_MODEL=os.environ.get("APP_BUILDER_FRONTDOOR_MODEL","claude-haiku-4-5-20251001")
FD_PER_MIN=int(os.environ.get("APP_BUILDER_FRONTDOOR_PER_MIN","20"))
FD_PER_DAY=int(os.environ.get("APP_BUILDER_FRONTDOOR_PER_DAY","300"))
_FD_LOCK=threading.Lock(); _FD_USE={}
def _fd_allowed(user):
 now=time.time(); day=time.strftime("%Y-%m-%d")
 with _FD_LOCK:
  r=_FD_USE.setdefault(user,{"min":[],"day":day,"n":0})
  if r["day"]!=day: r["day"],r["n"]=day,0
  r["min"]=[t for t in r["min"] if now-t<60]
  if len(r["min"])>=FD_PER_MIN or r["n"]>=FD_PER_DAY: return False
  r["min"].append(now); r["n"]+=1; return True
def front_door_model(user,prompt):
 """Returns (error_code or None, parsed JSON answer)."""
 import urllib.request, urllib.error
 key=os.environ.get("ANTHROPIC_API_KEY","")
 if not key: return "no_model",None
 if not _fd_allowed(user): return "rate_limited",None
 body=json.dumps({"model":FD_MODEL,"max_tokens":1500,
  "system":"Answer with a single JSON object only. No prose, no code fences.",
  "messages":[{"role":"user","content":prompt}]}).encode()
 req=urllib.request.Request("https://api.anthropic.com/v1/messages",data=body,method="POST",
  headers={"x-api-key":key,"anthropic-version":"2023-06-01","content-type":"application/json"})
 try:
  with urllib.request.urlopen(req,timeout=60) as r: d=json.loads(r.read())
 except urllib.error.HTTPError as e:
  return {429:"rate_limited",413:"prompt_too_large",401:"no_model",403:"no_model"}.get(e.code,"upstream_error"),None
 except Exception: return "upstream_error",None
 text="".join(b.get("text","") for b in d.get("content",[]) if b.get("type")=="text").strip()
 text=re.sub(r"^```(?:json)?|```$","",text).strip()
 i,j=text.find("{"),text.rfind("}")
 try: return None,json.loads(text[i:j+1])
 except Exception: return "invalid_json",None
def front_door(me):
 b=FRONT.read_bytes()
 extra=(' &middot; '+html.escape(me["name"])+' <a href="/logout">Log out</a>') if not AUTH_DISABLED else ''
 nav=FRONT_NAV_TMPL.format(b="All builds" if me["role"]=="admin" else "My builds",extra=extra).encode()
 i=b.lower().rfind(b"</body>")
 return b[:i]+nav+b[i:] if i>=0 else b+nav
def multipart_upload(handler, content_type, content_length, destination, max_bytes):
    m=re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type or '', re.I)
    if not m:
        raise ValueError("multipart/form-data boundary missing")
    boundary=(m.group(1) or m.group(2)).encode()
    if not boundary or len(boundary)>200:
        raise ValueError("invalid multipart boundary")
    # Stream the request to a temporary file. We never keep the complete upload in memory.
    tmp=destination.with_suffix('.uploading')
    written=0
    file_parts=0
    remaining=content_length
    marker=b'--'+boundary
    with tmp.open('wb') as out:
        buf=b''
        while remaining:
            chunk=handler.rfile.read(min(1024*1024, remaining))
            if not chunk: break
            remaining-=len(chunk); buf+=chunk
            # Keep enough tail to recognise a boundary split across chunks.
            while True:
                start=buf.find(marker)
                if start < 0:
                    # We have not found the first boundary yet, so anything buffered here is
                    # preamble (RFC 2046: discarded, never part of a body part) or a partial
                    # boundary split across two socket reads -- never file content, since file
                    # bytes are only ever written from inside the per-part loop below once a
                    # part has been identified as the file part. Writing it to `out` here would
                    # silently prepend garbage to the saved upload.
                    keep=min(len(buf),len(marker)+4)
                    buf=buf[-keep:] if keep else b''
                    break
                if start>0:
                    data=buf[:start]
                    # Multipart framing before the first boundary is discarded.
                    if not data.startswith(b'\r\n'):
                        raise ValueError("invalid multipart framing")
                    buf=buf[start:]
                # Need the complete boundary delimiter line.
                end=buf.find(b'\r\n',len(marker))
                if end<0: break
                line=buf[:end]
                buf=buf[end+2:]
                if line.startswith(marker+b'--'):
                    # End of multipart. No more file bytes should follow except CRLF.
                    out.flush(); tmp.replace(destination); return written
                if line != marker:
                    raise ValueError("invalid multipart boundary delimiter")
                # Parse this part header block from the buffered stream. This implementation
                # accepts the browser's normal single-file form and ignores form fields.
                sep=buf.find(b'\r\n\r\n')
                while sep<0 and remaining:
                    chunk=handler.rfile.read(min(64*1024,remaining)); remaining-=len(chunk); buf+=chunk; sep=buf.find(b'\r\n\r\n')
                if sep<0: raise ValueError("multipart headers incomplete")
                headers=buf[:sep].decode('utf-8','replace').split('\r\n')
                buf=buf[sep+4:]
                disposition=''.join(x[1] for x in [re.match(r'(?i)content-disposition:\s*(.*)',h) for h in headers] if x)
                is_file=('filename=' in disposition.lower())
                if is_file:
                    fm=re.search(r'filename="([^"]*)"',disposition)
                    handler._upload_name=(fm.group(1) if fm else '').replace('\\','/').rsplit('/',1)[-1][:200]
                    file_parts += 1
                    if file_parts > 1: raise ValueError('multiple file parts are not supported')
                # Consume this part until the next boundary. Only the file part is written.
                while True:
                    idx=buf.find(b'\r\n'+marker)
                    if idx<0:
                        keep=len(marker)+2
                        data=buf[:-keep] if len(buf)>keep else b''
                        if is_file and data:
                            written+=len(data)
                            if written>max_bytes: raise ValueError("upload exceeds maximum size")
                            out.write(data)
                        buf=buf[-keep:] if len(buf)>keep else buf
                        if remaining:
                            chunk=handler.rfile.read(min(1024*1024,remaining)); remaining-=len(chunk); buf+=chunk; continue
                        raise ValueError("multipart terminating boundary missing")
                    data=buf[:idx]
                    if is_file and data:
                        written+=len(data)
                        if written>max_bytes: raise ValueError("upload exceeds maximum size")
                        out.write(data)
                    buf=buf[idx+2:]
                    break
    raise ValueError("multipart upload incomplete")

class H(BaseHTTPRequestHandler):
 _csp=None
 def end_headers(self):
  # v115: on every response, including redirects and errors.
  self.send_header("X-Content-Type-Options","nosniff"); self.send_header("Referrer-Policy","same-origin")
  self.send_header("Content-Security-Policy",self._csp or CSP_PAGE)
  if (self._csp or CSP_PAGE)!=CSP_FRONT: self.send_header("X-Frame-Options","DENY")
  self._csp=None
  super().end_headers()
 def out(self,b,code=200,cookie=None,ctype="text/html; charset=utf-8",csp=None,disposition=None):
  self._csp=csp or (CSP_DATA if not ctype.startswith("text/html") else CSP_PAGE)
  self.send_response(code); self.send_header("Content-Type",ctype); self.send_header("Content-Length",str(len(b))); self.send_header("Cache-Control","no-store")
  if disposition: self.send_header("Content-Disposition",disposition)
  if cookie:self.send_header("Set-Cookie",cookie)
  self.end_headers(); self.wfile.write(b)
 def redirect(self,to,cookie=None):
  self.send_response(302); self.send_header("Location",to)
  if cookie:self.send_header("Set-Cookie",cookie)
  self.end_headers()
 def json_out(self,d,code=200): self.out((json.dumps(d,indent=2)+"\n").encode(),code,ctype="application/json")
 def do_GET(self):
  p=urlparse(self.path).path
  if p=="/health": self.out(b"OK\n"); return
  me=auth(self)
  if not me:
   if p=="/login": self.out(page("Login","<h1>APP Builder</h1><form method=post action=/login><input name=user placeholder=User autocomplete=username><br><input name=password type=password placeholder=Password autocomplete=current-password><br><button>Log in</button></form>")); return
   self.redirect("/login"); return
  if p=="/logout": self.redirect("/login","session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"); return
  # The Front Door (the questions page) is the first page everyone lands on.
  if p in ("/","/front-door","/login"):
   if not FRONT.exists(): self.out(page("Missing","<h1>front-door.html not installed</h1>",me),500); return
   self.out(front_door(me),csp=CSP_FRONT); return
  if p=="/upload": self.out(page("Add","<h1>Add an app</h1><p>Anything you add joins the library and gets its skin and checks automatically. No list needs updating first. The build is owned by <b>"+html.escape(me["name"])+"</b>. Each app is only <b>QUALIFIED</b> after the real-browser check passes, and only qualified apps go to Coolify.</p><h2>Upload a zip</h2><p>An app's source (e.g. <code>gitea-main.zip</code>), or the ATTa bundle to update the system itself.</p><form method=post action=/upload enctype=multipart/form-data><input type=file name=bundle accept=.zip><br><button>Upload and run pipeline</button></form><h2>Or add from a git address</h2><form method=post action=/add-repo><input name=url size=60 placeholder=https://github.com/owner/name><br><button>Add and run pipeline</button></form>",me)); return
  if p=="/library":
   try: cat=json.loads((ROOT/"app_catalogue.json").read_text()).get("apps",{})
   except Exception: cat={}
   inlib=lambda e:(ROOT/"library"/re.sub(r"[^a-z0-9-]+","-",str(e.get("app","")).lower()).strip("-")).is_dir()
   order=sorted(cat.items(),key=lambda kv:(not inlib(kv[1]),kv[0]))
   n_in=sum(1 for _,e in order if inlib(e))
   def last_check(e):
    # The app runner's last real result for this app: verdict + how it was started.
    try: r=json.loads((ROOT/"state/runner/results"/(re.sub(r"[^a-z0-9-]+","-",str(e.get("app","")).lower()).strip("-")+".json")).read_text())
    except Exception: return "—"
    run=r.get("runner") or {}
    how=run.get("part") or run.get("why") or (run.get("detail") or "")[:160]
    cls="QUALIFIED" if r.get("verdict")=="PASS" else "FAILED"
    return "<span class="+cls+">"+html.escape(r.get("verdict",""))+"</span><br><small>"+html.escape(how)+"</small>"
   def tok(e):
    a=re.sub(r"[^a-z0-9-]+","-",str(e.get("app","")).lower()).strip("-")
    return (" <small><a href=/apps/"+html.escape(a)+"/secrets>tokens</a></small>") if inlib(e) and app_owners.can_access(me,a) else ""
   rows="".join("<tr><td>"+html.escape(e.get("app",k))+tok(e)+"</td><td>"+("yes" if inlib(e) else "not yet")+"</td><td>"+last_check(e)+"</td><td>"+html.escape(e.get("skin_category") or "—")+"</td><td>"+html.escape(str(e.get("skin_source") or ""))+" / "+html.escape(str(e.get("skin_confidence") or ""))+"</td><td>"+html.escape(str(e.get("profile") or "—"))+"</td><td>"+html.escape(str(e.get("root") or ""))+"</td><td>"+html.escape(", ".join(e.get("sources",[])))+"</td><td class="+html.escape(e.get("status",""))+">"+html.escape(e.get("status",""))+("<br><small>"+html.escape("; ".join(e["look"]))+"</small>" if e.get("look") else "")+"</td></tr>" for k,e in order)
   self.out(page("Library","<h1>Library</h1><p>"+str(n_in)+" apps in the library, "+str(len(cat))+" known in total (seed repos are cloned on the next build). Grows with every app added; skin and profile come from each app's own files.</p><table><tr><th>App</th><th>In library</th><th>Last check (started how)</th><th>Skin</th><th>Decided by / confidence</th><th>Profile</th><th>Code root</th><th>Came from</th><th>Status</th></tr>"+rows+"</table>",me)); return
  m=re.fullmatch(r"/evidence/([a-z0-9-]+)/([A-Za-z0-9][A-Za-z0-9._-]{0,99})",p)
  if m:
   # v115: only the app's owner (or an admin) sees its evidence; anyone else gets the same 404 as "missing".
   if not app_owners.can_access(me,m.group(1)): self.send_error(404); return
   base=(EVIDENCE_DIR/m.group(1))
   try: f=(base/m.group(2)).resolve(strict=True)
   except (OSError,RuntimeError): self.send_error(404); return
   if (base/m.group(2)).is_symlink() or not f.is_relative_to(base.resolve()) or not f.is_file(): self.send_error(404); return
   b,ct,disp=evidence_response(f)
   self.out(b,ctype=ct,csp=CSP_EVIDENCE,disposition=disp); return
  m=re.fullmatch(r"/apps/([a-z0-9-]+)/secrets",p)
  if m:
   if not app_exists(m.group(1)) or not app_owners.can_access(me,m.group(1)): self.send_error(404); return
   self.out(secrets_page(me,m.group(1))); return
  if p=="/builds": self.out(page("Builds","<h1>"+("All builds" if me["role"]=="admin" else "My builds")+"</h1>"+builds_table(builds.visible_to(me),me),me)); return
  if p=="/api/me": self.json_out({"name":me["name"],"role":me["role"]}); return
  if p=="/api/builds": self.json_out(builds.visible_to(me)); return
  m=re.fullmatch(r"/(api/)?builds/([A-Za-z0-9-]+)",p)
  if m:
   r=builds.get(m.group(2)) if builds.ID_RE.match(m.group(2)) else None
   # Someone else's build answers exactly like a missing one.
   if not r or not builds.can_see(me,r): self.send_error(404); return
   if m.group(1): self.json_out(r); return
   hist="".join("<tr><td>"+when(h.get("at"))+"</td><td class="+html.escape(h.get("state",""))+">"+html.escape(h.get("state",""))+"</td><td>"+html.escape(h.get("error",""))+"</td></tr>" for h in r.get("history",[]))
   self.out(page(r["id"],"<h1>Build "+html.escape(r["id"])+"</h1><p>Owner: <b>"+html.escape(r.get("owner",""))+"</b> &middot; State: <b class="+html.escape(r.get("state",""))+">"+html.escape(r.get("state",""))+"</b></p>"+("<p>"+html.escape(str(r["error"]))+"</p>" if r.get("error") else "")+discovered_html(r,me)+checklist_html(r)+adm_html(r)+"<h2>History</h2><table>"+hist+"</table>"+healing_html(r)+"<h2>Full record</h2><pre>"+html.escape(json.dumps(r,indent=2))+"</pre>",me)); return
  if p=="/alerts":
   # Human escalations (tier 4 of self-healing) span every user's builds: admin-only.
   if me["role"]!="admin": self.redirect("/builds"); return
   rows=alerts.recent()
   body="<h1>Alerts</h1><p>Raised only after the known-fix script, the capability adapter and the LLM could not fix a failure.</p>"
   body+="".join("<h3>"+when(a.get("at"))+" &middot; <a href=/builds/"+html.escape(a.get("build_id",""))+">"+html.escape(a.get("build_id",""))+"</a> &middot; "+html.escape(a.get("layer",""))+"</h3><pre>"+html.escape(a.get("text",""))+"</pre>" for a in rows) or "<p>No alerts.</p>"
   self.out(page("Alerts",body,me)); return
  if p=="/status":
   # System-wide pipeline state spans every user's builds, so it is admin-only.
   if me["role"]!="admin": self.redirect("/builds"); return
   try:s=STATUS.read_text()
   except:s=json.dumps({"state":"IDLE"})
   self.out(page("Status","<h1>System status</h1><pre>"+html.escape(s)+"</pre>",me)); return
  self.send_error(404)
 def do_POST(self):
  p=urlparse(self.path).path
  if not same_origin_post(self):
   # v115: a form or script on another site (or an app deployed next to ATTa) can't act as the user.
   self.send_error(403,"cross-site request refused"); return
  if p=="/login":
   try:n=int(self.headers.get("Content-Length","0"))
   except ValueError:n=0
   if n<0 or n>8192: self.send_error(413); return
   q=parse_qs(self.rfile.read(n).decode("utf-8","replace"))
   ip=client_ip(self)
   if not login_allowed(ip):
    self.out(page("Too many attempts","<h1>Too many login attempts</h1><p>Try again later.</p>"),429); return
   u=accounts.verify(q.get("user",[""])[0].strip().lower(),q.get("password",[""])[0])
   if u:
    clear_login_failures(ip)
    secure="; Secure" if self.headers.get("X-Forwarded-Proto","").lower()=="https" else ""
    self.redirect("/","session="+token(u)+"; HttpOnly"+secure+"; SameSite=Strict; Path=/; Max-Age="+str(SESSION_TTL))
   else:
    record_login_failure(ip)
    self.out(page("Login failed","<h1>Login failed</h1><a href=/login>Try again</a>"),401)
   return
  me=auth(self)
  if not me: self.send_error(403); return
  if p=="/api/front-door/model":
   # The Front Door's questions. Runs here on the server with the server's own API key,
   # so the page works in any browser, not only inside Claude.
   try:
    n=int(self.headers.get("Content-Length","0"))
    if not 0<n<=200000 or "application/json" not in self.headers.get("Content-Type",""): raise ValueError
    prompt=str(json.loads(self.rfile.read(n)).get("prompt",""))
    if not prompt.strip(): raise ValueError
   except Exception: self.json_out({"ok":False,"code":"prompt_too_large"},400); return
   code,data=front_door_model(me["name"],prompt)
   self.json_out({"ok":code is None,"code":code,"data":data},200); return
  if p=="/api/front-door/choice":
   # The Front Door records the app a person confirmed ("Is this the app?" -> Yes) against their account.
   try:
    n=int(self.headers.get("Content-Length","0"))
    if not 0<n<=65536 or "application/json" not in self.headers.get("Content-Type",""): raise ValueError("expected a JSON body up to 64 KB")
    d=json.loads(self.rfile.read(n))
    rec={"at":time.time(),"user":me["name"],"category_id":str(d.get("category_id",""))[:80],"label":str(d.get("label",""))[:200],"capabilities":[str(c)[:300] for c in (d.get("capabilities") or [])][:20]}
   except Exception as e: self.json_out({"ok":False,"error":str(e)},400); return
   with (CHOICES/(me["name"]+".jsonl")).open("a") as f: f.write(json.dumps(rec)+"\n")
   self.json_out({"ok":True}); return
  if p=="/upload":
   try:n=int(self.headers.get("Content-Length","0"))
   except ValueError:n=0
   if n<=0 or n>MAX:self.send_error(413); return
   ctype=self.headers.get("Content-Type","")
   # Written under a temporary name; it only enters the inbox (as <build id>.zip) once it is a valid ZIP.
   f=INBOX/f".incoming-{int(time.time())}-{secrets.token_hex(6)}.part"
   try:
    if ctype.lower().startswith("multipart/form-data"):
     written=multipart_upload(self,ctype,n,f,MAX)
    else:
     # Backward-compatible raw ZIP upload for API clients.
     with f.open("wb") as out:
      remaining=n
      written=0
      while remaining:
       chunk=self.rfile.read(min(1024*1024,remaining));
       if not chunk:break
       out.write(chunk); written+=len(chunk); remaining-=len(chunk)
      if remaining:
       raise ValueError("upload body ended before Content-Length was received")
    if written<=0 or written>MAX: raise ValueError("empty or oversized upload")
    # Validate the upload before queuing it; a malformed browser form must never enter the pipeline.
    with zipfile.ZipFile(f) as z: z.testzip()
   except Exception as exc:
    try:f.unlink()
    except OSError:pass
    try:f.with_suffix('.uploading').unlink()
    except OSError:pass
    self.out(page("Upload failed","<h1>Upload failed</h1><p>"+html.escape(str(exc))+"</p><a href=/upload>Try again</a>",me),400); return
   b=builds.create(me["name"],origin="web",bundle_bytes=written,original_name=getattr(self,"_upload_name","") or None)
   f.replace(INBOX/(b["id"]+".zip"))
   # Redirect (Post/Redirect/Get): refreshing the build page can never upload the file again.
   self.redirect("/builds/"+b["id"]); return
  if p=="/add-repo":
   try:n=int(self.headers.get("Content-Length","0"))
   except ValueError:n=0
   if n<=0 or n>4096: self.send_error(413); return
   url=parse_qs(self.rfile.read(n).decode("utf-8","replace")).get("url",[""])[0].strip()
   if not re.fullmatch(r"https://[A-Za-z0-9.-]+/[A-Za-z0-9._~/-]+?(\.git)?/?",url):
    self.out(page("Not a repo URL","<h1>That isn't a usable repository address</h1><p>Use an https address like https://github.com/owner/name</p><a href=/upload>Back</a>",me),400); return
   b=builds.create(me["name"],origin="web",source=url,kind="git")
   tmp=INBOX/(".incoming-"+b["id"]+".part")
   tmp.write_text(json.dumps({"url":url})+"\n"); tmp.replace(INBOX/(b["id"]+".repo.json"))
   self.redirect("/builds/"+b["id"]); return
  m=re.fullmatch(r"/(api/)?apps/([a-z0-9-]+)/secrets(/check)?",p)
  if m:
   api,app,chk=bool(m.group(1)),m.group(2),bool(m.group(3))
   if not app_exists(app) or not app_owners.can_access(me,app): self.send_error(404); return
   try:
    n=int(self.headers.get("Content-Length","0"))
    if not 0<n<=512000: raise ValueError("body must be 1 byte to 500 KB")
    raw=self.rfile.read(n)
    if api:
     if "application/json" not in self.headers.get("Content-Type",""): raise ValueError("expected application/json")
     d=json.loads(raw)
     if not isinstance(d,dict): raise ValueError("expected a JSON object")
    else:
     q=parse_qs(raw.decode("utf-8","replace"),keep_blank_values=True)
     names=[x.strip() for x in q.get("name",[])]; vals=q.get("value",[])
     if chk: d={"name":names[0] if names else "","value":vals[0] if vals else ""}
     else: d={"secrets":{k:v for k,v in zip(names,vals) if k or v}}
   except (ValueError,UnicodeDecodeError) as e:
    self.json_out({"ok":False,"error":str(e)},400) if api else self.out(secrets_page(me,app,"Not sent: "+str(e)),400); return
   if chk:
    if not rate_ok(me["name"],"secret-check",20): self.json_out({"ok":False,"error":"too many checks; wait a minute"},429) if api else self.out(secrets_page(me,app,"Too many checks; wait a minute."),429); return
    r=customer_secrets.check(app,str(d.get("name","")),d.get("value"))
    if api: self.json_out({"ok":True,**r}); return
    msg=("No token recorded for that variable." if not r["recorded"] else "Matches the token delivered "+when(r.get("at"))+"." if r["matches"] else "Does NOT match the token delivered "+when(r.get("at"))+".")
    self.out(secrets_page(me,app,msg)); return
   if not rate_ok(me["name"],"secret-deliver",10): self.json_out({"ok":False,"error":"too many deliveries; wait a minute"},429) if api else self.out(secrets_page(me,app,"Too many deliveries; wait a minute."),429); return
   bid=d.get("build_id") if api else None
   if bid is not None and not (isinstance(bid,str) and builds.ID_RE.match(bid) and (builds.get(bid) or None) and builds.can_see(me,builds.get(bid))): bid=None
   bid=bid or (app_owners.get(app) or {}).get("build_id")
   try: r=customer_secrets.deliver(app,d.get("secrets"),me["name"],bid)
   except customer_secrets.SecretError as e:
    self.json_out({"ok":False,"error":str(e)},400) if api else self.out(secrets_page(me,app,"Not sent: "+str(e)),400); return
   if api: self.json_out({"ok":True,**r}); return
   self.out(secrets_page(me,app,"Sent to Coolify: "+", ".join(r["delivered"])+("" if (r.get("redeploy") or {}).get("ok",True) else " (the redeploy call failed: "+str(r["redeploy"]["detail"])[:200]+")"))); return
  self.send_error(404)
 def log_message(self,*a): pass
ThreadingHTTPServer((HOST,PORT),H).serve_forever()
