#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import hashlib,hmac,html,json,os,secrets,time,re,threading,zipfile
import accounts, builds, alerts
ROOT=Path(os.environ.get("APP_BUILDER_ROOT","/srv/app-builder"));
LOGIN_WINDOW=int(os.environ.get("APP_BUILDER_LOGIN_WINDOW","900")); LOGIN_MAX_FAILURES=int(os.environ.get("APP_BUILDER_LOGIN_MAX_FAILURES","8")); _LOGIN_FAILURES={}
_LOGIN_LOCK=threading.Lock()
INBOX=ROOT/"inbox"; STATUS=ROOT/"state/status.json"; FRONT=ROOT/"front-door.html"; CHOICES=ROOT/"state/front-door-choices"
HOST=os.environ.get("APP_BUILDER_HOST","127.0.0.1"); PORT=int(os.environ.get("APP_BUILDER_PORT","8787")); SECRET=os.environ.get("APP_BUILDER_SESSION_SECRET",""); MAX=int(os.environ.get("APP_BUILDER_MAX_UPLOAD","10737418240")); SESSION_TTL=int(os.environ.get("APP_BUILDER_SESSION_TTL","86400"))
# Local testing escape hatch: when APP_BUILDER_AUTH_DISABLED=1 the gateway skips the login
# entirely and every request acts as a local admin. Never set on a server.
AUTH_DISABLED=os.environ.get("APP_BUILDER_AUTH_DISABLED","")=="1"
LOCAL_ADMIN={"name":"local","role":"admin"}
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
  links='<a href=/>Front Door</a> <a href=/builds>'+("All builds" if me["role"]=="admin" else "My builds")+'</a> <a href=/upload>Upload</a>'
  if me["role"]=="admin": links+=' <a href=/status>System status</a> <a href=/alerts>Alerts</a>'
  nav='<nav>'+links+'<span>'+html.escape(me["name"])+' ('+html.escape(me["role"])+')'+(' <a href=/logout>Log out</a>' if not AUTH_DISABLED else '')+'</span></nav>'
 return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>body{font-family:system-ui;margin:40px;max-width:900px}input,button{padding:10px;margin:6px 0}pre{background:#f4f4f4;padding:12px;overflow:auto}nav{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:24px;padding-bottom:12px;border-bottom:1px solid #ddd}nav span{margin-left:auto;color:#666}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #eee}.QUALIFIED{color:#0a7a2f;font-weight:600}.FAILED,.NOT_QUALIFIED{color:#b00020;font-weight:600}</style></head><body>'+nav+body+'</body></html>').encode()
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
FRONT_NAV_TMPL='<div id="ab-nav" style="position:fixed;top:8px;right:12px;z-index:99;font:13px system-ui;background:rgba(255,255,255,.9);padding:4px 10px;border-radius:8px;border:1px solid #ddd"><a href="/builds">{b}</a> &middot; <a href="/upload">Upload</a>{extra}</div>'
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
 def out(self,b,code=200,cookie=None,ctype="text/html; charset=utf-8"):
  self.send_response(code); self.send_header("Content-Type",ctype); self.send_header("Content-Length",str(len(b))); self.send_header("Cache-Control","no-store")
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
   self.out(front_door(me)); return
  if p=="/upload": self.out(page("Upload","<h1>Upload bundle</h1><p>Starts a new build owned by <b>"+html.escape(me["name"])+"</b>. It is only <b>QUALIFIED</b> after the real-browser check passes, and only qualified builds go to Coolify.</p><form method=post action=/upload enctype=multipart/form-data><input type=file name=bundle accept=.zip><br><button>Upload and run pipeline</button></form>",me)); return
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
   self.out(page(r["id"],"<h1>Build "+html.escape(r["id"])+"</h1><p>Owner: <b>"+html.escape(r.get("owner",""))+"</b> &middot; State: <b class="+html.escape(r.get("state",""))+">"+html.escape(r.get("state",""))+"</b></p>"+("<p>"+html.escape(str(r["error"]))+"</p>" if r.get("error") else "")+"<h2>History</h2><table>"+hist+"</table>"+healing_html(r)+"<h2>Full record</h2><pre>"+html.escape(json.dumps(r,indent=2))+"</pre>",me)); return
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
   b=builds.create(me["name"],bundle_bytes=written)
   f.replace(INBOX/(b["id"]+".zip"))
   self.out(page("Uploaded","<h1>Accepted</h1><p>Build <a href=/builds/"+b["id"]+">"+b["id"]+"</a> is queued.</p>",me)); return
  self.send_error(404)
 def log_message(self,*a): pass
ThreadingHTTPServer((HOST,PORT),H).serve_forever()
