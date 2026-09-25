#!/usr/bin/env python3
"""Drive ATTa's real web gateway the way a browser user does (login -> upload -> watch).

    atta_client.py upload USER ZIP            -> prints build id (uses POST /upload multipart, field "bundle")
    atta_client.py repo USER URL              -> prints build id (POST /add-repo)
    atta_client.py build USER BUILD_ID        -> prints the build JSON (GET /api/builds/<id>)
    atta_client.py builds USER                -> prints all builds visible to USER
Credentials: /root/testcreds ("name password" per line, root-only). Talks to nginx at http://127.0.0.1/ .
"""
import http.cookiejar, json, os, sys, urllib.parse, urllib.request, uuid

BASE = os.environ.get("ATTA_BASE", "http://127.0.0.1")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None

def session(user):
    pw = dict(l.split(None, 1) for l in open(os.path.expanduser("~/testcreds")).read().split("\n") if l.strip())[user].strip()
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)
    data = urllib.parse.urlencode({"user": user, "password": pw}).encode()
    try: op.open(urllib.request.Request(BASE + "/login", data=data, method="POST"))
    except urllib.error.HTTPError as e:
        if e.code != 302: raise SystemExit(f"LOGIN_FAILED {user}: HTTP {e.code}")
    if not any(c.name == "session" for c in jar): raise SystemExit(f"LOGIN_FAILED {user}: no session cookie")
    return op

def post(op, path, body, ctype):
    req = urllib.request.Request(BASE + path, data=body, method="POST", headers={"Content-Type": ctype})
    try: r = op.open(req); return r.status, r.headers.get("Location", ""), r.read().decode(errors="replace")
    except urllib.error.HTTPError as e: return e.code, e.headers.get("Location", ""), e.read().decode(errors="replace")

def main():
    cmd, user = sys.argv[1], sys.argv[2]
    op = session(user)
    if cmd == "upload":
        path = sys.argv[3]; b = uuid.uuid4().hex
        head = (f"--{b}\r\nContent-Disposition: form-data; name=\"bundle\"; filename=\"{os.path.basename(path)}\"\r\n"
                "Content-Type: application/zip\r\n\r\n").encode()
        body = head + open(path, "rb").read() + f"\r\n--{b}--\r\n".encode()
        code, loc, text = post(op, "/upload", body, f"multipart/form-data; boundary={b}")
        print(loc.rsplit("/", 1)[-1] if code == 302 and "/builds/" in loc else f"UPLOAD_REFUSED HTTP {code}: {text[:300]}")
    elif cmd == "repo":
        code, loc, text = post(op, "/add-repo", urllib.parse.urlencode({"url": sys.argv[3]}).encode(), "application/x-www-form-urlencoded")
        print(loc.rsplit("/", 1)[-1] if code == 302 and "/builds/" in loc else f"REPO_REFUSED HTTP {code}: {text[:300]}")
    elif cmd in ("build", "builds"):
        path = "/api/builds/" + sys.argv[3] if cmd == "build" else "/api/builds"
        try: print(op.open(BASE + path).read().decode())
        except urllib.error.HTTPError as e: print(json.dumps({"http": e.code, "body": e.read().decode()[:300]}))
    else: raise SystemExit(__doc__)

if __name__ == "__main__": main()
