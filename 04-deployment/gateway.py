#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import hashlib,hmac,html,json,os,secrets,time,re,tempfile,threading
ROOT=Path(os.environ.get("APP_BUILDER_ROOT","/srv/app-builder"));
LOGIN_WINDOW=int(os.environ.get("APP_BUILDER_LOGIN_WINDOW","900")); LOGIN_MAX_FAILURES=int(os.environ.get("APP_BUILDER_LOGIN_MAX_FAILURES","8")); _LOGIN_FAILURES={}
_LOGIN_LOCK=threading.Lock()
INBOX=ROOT/"inbox"; STATUS=ROOT/"state/status.json"; FRONT=ROOT/"front-door.html"
HOST=os.environ.get("APP_BUILDER_HOST","127.0.0.1"); PORT=int(os.environ.get("APP_BUILDER_PORT","8787")); USER=os.environ.get("APP_BUILDER_USER","admin"); PASSWORD=os.environ.get("APP_BUILDER_PASSWORD",""); SECRET=os.environ.get("APP_BUILDER_SESSION_SECRET",""); MAX=int(os.environ.get("APP_BUILDER_MAX_UPLOAD","10737418240")); SESSION_TTL=int(os.environ.get("APP_BUILDER_SESSION_TTL","86400"))
# Local testing escape hatch: when APP_BUILDER_AUTH_DISABLED=1 the gateway skips
# the login entirely (every request is treated as authenticated) and no password
# or session secret is required. The `run` launcher sets this ONLY for the local
# laptop instance; the server/bootstrap path never sets it, so AWS keeps auth.
AUTH_DISABLED=os.environ.get("APP_BUILDER_AUTH_DISABLED","")=="1"
for p in (INBOX,STATUS.parent): p.mkdir(parents=True,exist_ok=True)
if not AUTH_DISABLED and (not PASSWORD or not SECRET): raise SystemExit("APP_BUILDER_PASSWORD and APP_BUILDER_SESSION_SECRET are required")
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
def token():
 ts=str(int(time.time())); n=secrets.token_urlsafe(24); value=ts+"."+n; return value+"."+sig(value)
def auth(h):
 if AUTH_DISABLED: return True
 c=h.headers.get("Cookie","")
 for x in c.split(";"):
  if x.strip().startswith("session="):
   try:
    value,s=x.strip().split("=",1)[1].rsplit(".",1)
    ts,n=value.split(".",1)
    if 0 <= int(time.time())-int(ts) <= SESSION_TTL and hmac.compare_digest(s,sig(value)): return True
   except Exception: pass
 return False
def page(title,body): return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>body{font-family:system-ui;margin:40px;max-width:900px}input,button{padding:10px;margin:6px 0}pre{background:#f4f4f4;padding:12px;overflow:auto}</style></head><body>'+body+'</body></html>').encode()
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
 def out(self,b,code=200,cookie=None):
  self.send_response(code); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(b))); self.send_header("Cache-Control","no-store")
  if cookie:self.send_header("Set-Cookie",cookie)
  self.end_headers(); self.wfile.write(b)
 def do_GET(self):
  p=urlparse(self.path).path
  if p=="/health": self.out(b"OK\n"); return
  if not auth(self):
   if p=="/login": self.out(page("Login","<h1>APP Builder</h1><form method=post action=/login><input name=user placeholder=User><br><input name=password type=password placeholder=Password><br><button>Login</button></form>")); return
   self.send_response(302); self.send_header("Location","/login"); self.end_headers(); return
  if p=="/" and AUTH_DISABLED: self.send_response(302); self.send_header("Location","/upload"); self.end_headers(); return
  if p=="/": self.out(page("APP Builder","<h1>APP Builder</h1><p>Authenticated.</p><p><a href=/upload>Upload system bundle</a></p><p><a href=/status>Status</a></p><p><a href=/front-door>Open Front Door</a></p>")); return
  if p=="/upload": self.out(page("Upload","<h1>Upload bundle</h1><form method=post action=/upload enctype=multipart/form-data><input type=file name=bundle accept=.zip><br><button>Upload and run pipeline</button></form>")); return
  if p=="/status":
   try:s=STATUS.read_text()
   except:s=json.dumps({"state":"IDLE"})
   self.out(page("Status","<h1>Status</h1><pre>"+html.escape(s)+"</pre><a href=/ >Back</a>")); return
  if p=="/front-door":
   if not FRONT.exists(): self.out(page("Missing","front-door.html not installed"),500); return
   b=FRONT.read_bytes(); self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b); return
  self.send_error(404)
 def do_POST(self):
  p=urlparse(self.path).path
  if p=="/login":
   n=int(self.headers.get("Content-Length","0")); q=parse_qs(self.rfile.read(n).decode())
   ip=client_ip(self)
   if not login_allowed(ip):
    self.out(page("Too many attempts","<h1>Too many login attempts</h1><p>Try again later.</p>"),429); return
   if hmac.compare_digest(q.get("user",[""])[0],USER) and hmac.compare_digest(q.get("password",[""])[0],PASSWORD):
    clear_login_failures(ip)
    secure="; Secure" if self.headers.get("X-Forwarded-Proto","").lower()=="https" else ""
    self.out(page("Logged in","<h1>Logged in</h1><a href=/upload>Continue</a>"),200,"session="+token()+"; HttpOnly"+secure+"; SameSite=Strict; Path=/; Max-Age=86400")
   else:
    record_login_failure(ip)
    self.out(page("Login failed","<h1>Login failed</h1><a href=/login>Try again</a>"),401)
   return
  if not auth(self): self.send_error(403); return
  if p=="/upload":
   try:n=int(self.headers.get("Content-Length","0"))
   except ValueError:n=0
   if n<=0 or n>MAX:self.send_error(413); return
   ctype=self.headers.get("Content-Type","")
   f=INBOX/f"upload-{int(time.time())}-{secrets.token_hex(6)}.zip"
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
    import zipfile
    with zipfile.ZipFile(f) as z: z.testzip()
   except Exception as exc:
    try:f.unlink()
    except OSError:pass
    try:f.with_suffix('.uploading').unlink()
    except OSError:pass
    self.out(page("Upload failed","<h1>Upload failed</h1><p>"+html.escape(str(exc))+"</p><a href=/upload>Try again</a>"),400); return
   self.out(page("Uploaded","<h1>Accepted</h1><p>Pipeline queued. <a href=/status>View status</a>.</p>")); return
  self.send_error(404)
 def log_message(self,*a): pass
ThreadingHTTPServer((HOST,PORT),H).serve_forever()
