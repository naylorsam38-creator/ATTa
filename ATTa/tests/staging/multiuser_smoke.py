#!/usr/bin/env python3
"""
multiuser_smoke.py — v116 staging smoke test: the REAL gateway and the REAL pipeline, each running as its own
service user on the v116 data-folder layout, driven over HTTP the way a person would.

Run as root on a THROWAWAY machine or container (it creates the atta-web / atta-run / atta-proxy users):

    sudo python3 tests/staging/multiuser_smoke.py            # prints PASS/FAIL per check, exits 1 on any FAIL

No Docker daemon is needed: apps fail at the runner stage, which is after everything this checks
(upload -> build record -> pipeline -> library -> installer -> overlay integrity -> build page, and an admin's
ATTa bundle -> request handed to deployd). Nothing outside a temporary folder is written except the users.
"""
import http.cookiejar, json, os, pwd, re, shutil, subprocess, sys, tempfile, time, urllib.parse, urllib.request, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE.parent.parent                     # ATTa/
DEP = BUNDLE / "04-deployment"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""), flush=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def as_user(user, cmd, env, log):
    u = pwd.getpwnam(user)
    return subprocess.Popen(["setpriv", f"--reuid={u.pw_uid}", f"--regid={u.pw_gid}", "--init-groups",
                             "bash", "-c", "umask 027; exec \"$@\"", "x", *cmd],
                            env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def main():
    if os.geteuid() != 0:
        print("run as root on a throwaway machine"); return 2
    base = Path(tempfile.mkdtemp(prefix="atta-staging-")); os.chmod(base, 0o755)
    root, code = base / "srv", base / "app"
    shutil.copytree(DEP, code, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(BUNDLE / "03-ui-skins-capability-package" / "UI_Skin_Capability_OneShot_v2.zip", code)
    shutil.copy(BUNDLE / "02-front-door" / "front-door.html", base / "front-door.html")
    os.system(f"chmod -R a+rX {code}")
    made = [d for d in (Path("/run/systemd"), Path("/run/systemd/system")) if not d.exists()]
    for d in made:
        d.mkdir()                                                  # so the pipeline acts as on a server
    procs = []
    try:
        lib = f'. "{code}/bootstrap-lib.sh"; '
        r = sh(["bash", "-c", lib + "atta_ensure_users"], env={**os.environ, "ATTA_LIB_DIR": str(code)})
        check("service users created", r.returncode == 0, r.stderr)
        port = 18787
        secret = "staging-session-secret-" + "s" * 32
        env_file = {"APP_BUILDER_ROOT": str(root), "APP_BUILDER_HOST": "127.0.0.1", "APP_BUILDER_PORT": str(port),
                    "APP_BUILDER_SESSION_SECRET": secret, "APP_BUILDER_BROWSER_CHECK": "false",
                    "ANTHROPIC_API_KEY": "sk-ant-atta-staging-" + "k" * 30, "APP_BUILDER_TEST_ACCOUNTS": "10"}
        root.mkdir()
        (root / ".env").write_text("".join(f"{k}={v}\n" for k, v in env_file.items()))
        shutil.copy(base / "front-door.html", root / "front-door.html")
        base_env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", **env_file}
        r = sh(["python3", str(code / "accounts.py"), "init"], env=base_env)
        check("accounts created", r.returncode == 0, r.stderr)
        r = sh(["bash", "-c", lib + f'atta_secure_state "{root}"'], env={**os.environ, "ATTA_LIB_DIR": str(code)})
        check("v116 layout applied", r.returncode == 0, r.stderr)
        logs = {n: open(base / f"{n}.log", "w") for n in ("gateway", "pipeline")}
        procs.append(as_user("atta-web", ["python3", str(code / "gateway.py")], base_env, logs["gateway"]))
        run_env = {**base_env, "HOME": "/var/lib/atta-run"}
        procs.append(as_user("atta-run", ["python3", str(code / "pipeline.py")], run_env, logs["pipeline"]))
        url = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(url + "/health", timeout=1); break
            except Exception:
                time.sleep(0.5)
        check("gateway up as atta-web", sh(["pgrep", "-u", "atta-web", "-f", "gateway.py"]).returncode == 0)
        check("pipeline up as atta-run", sh(["pgrep", "-u", "atta-run", "-f", "pipeline.py"]).returncode == 0)
        for user in ("atta-web", "atta-run"):
            pid = sh(["pgrep", "-u", user, "-f", "gateway.py|pipeline.py"]).stdout.split()[0]
            status = Path(f"/proc/{pid}/status").read_text()
            check(f"{user} process has no root uid", re.search(r"^Uid:\s+0\b", status, re.M) is None)
            r = sh(["setpriv", f"--reuid={pwd.getpwnam(user).pw_uid}", "--init-groups", "cat", str(root / ".env")])
            check(f"{user} cannot read .env", r.returncode != 0)

        creds = dict(re.findall(r"^(\S+)\s+\S+\s+(\S+)$", (root / "TEST_ACCOUNTS.txt").read_text(), re.M))

        def session(user):
            jar = http.cookiejar.CookieJar()
            op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            op.open(url + "/login", data=urllib.parse.urlencode({"user": user, "password": creds[user]}).encode())
            return op

        def upload(op, name, data):
            b = "----attastaging"
            body = (f"--{b}\r\nContent-Disposition: form-data; name=\"bundle\"; filename=\"{name}\"\r\n"
                    f"Content-Type: application/zip\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
            req = urllib.request.Request(url + "/upload", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={b}"})
            return op.open(req).geturl().rsplit("/", 1)[-1]

        def wait_state(op, bid, done, secs=240):
            for _ in range(secs):
                rec = json.loads(op.open(url + f"/api/builds/{bid}").read())
                if rec.get("state") in done:
                    return rec
                time.sleep(1)
            return rec

        # 1. a tester adds an app: gateway (atta-web) writes, pipeline (atta-run) processes, gateway shows it
        t = session("tester01")
        app = base / "shop.zip"
        with zipfile.ZipFile(app, "w") as z:
            z.writestr("shop/package.json", '{"name":"shop","scripts":{"start":"node index.js"}}')
            z.writestr("shop/index.js", "require('http').createServer((q,s)=>s.end('shop')).listen(3000)")
            z.writestr("shop/.ui-capability/run-ui.sh", "#!/bin/sh\ntouch /tmp/atta-staging-pwned\n")   # must never run
        bid = upload(t, "shop.zip", app.read_bytes())
        rec = wait_state(t, bid, {"FAILED", "NOT_QUALIFIED", "QUALIFIED", "PARTIALLY_QUALIFIED", "PACKAGE_INSTALLED"})
        check("tester build processed by the pipeline", rec.get("state") in {"NOT_QUALIFIED", "FAILED"} and "shop" in
              (rec.get("apps_added") or []), json.dumps({k: rec.get(k) for k in ("state", "error", "apps_added")})[:400])
        check("build page readable by the gateway after the pipeline rewrote it",
              t.open(url + f"/builds/{bid}").status == 200)
        lib_app = root / "library" / "shop"
        check("app joined the library, owned by atta-run", lib_app.is_dir() and lib_app.stat().st_uid == pwd.getpwnam("atta-run").pw_uid)
        check("uploaded overlay was replaced by the installer's",
              not (lib_app / ".ui-capability" / "run-ui.sh").exists() or
              "atta-staging-pwned" not in (lib_app / ".ui-capability" / "run-ui.sh").read_text())
        check("installer overlay recorded", (root / "state" / "overlay-integrity" / "shop.json").is_file()
              or not (lib_app / ".ui-capability").exists())
        check("uploaded launcher never ran", not Path("/tmp/atta-staging-pwned").exists())
        import grp
        shared = grp.getgrnam("atta").gr_gid
        for rel in ("app_catalogue.json", "state/status.json"):
            f = root / rel
            check(f"{rel} rewritten by the pipeline stays readable by the gateway",
                  f.is_file() and f.stat().st_gid == shared, f"{f} gid={f.stat().st_gid if f.exists() else None}")
        stranger = session("tester02")
        try:
            stranger.open(url + f"/builds/{bid}"); check("other tester cannot see the build", False)
        except urllib.error.HTTPError as e:
            check("other tester cannot see the build", e.code == 404)

        # 2. an admin uploads an ATTa bundle: the non-root pipeline hands it to deployd, never queues it itself
        a = session("admin")
        bundle = base / "ATTa-deploy116.zip"
        with zipfile.ZipFile(bundle, "w") as z:
            for f in sorted(BUNDLE.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts and "05-coolify" not in f.parts:
                    z.write(f, "ATTa/" + f.relative_to(BUNDLE).as_posix())
        bid2 = upload(a, bundle.name, bundle.read_bytes())
        rec2 = wait_state(a, bid2, {"FAILED", "NOT_QUALIFIED", "QUALIFIED", "PARTIALLY_QUALIFIED", "PACKAGE_INSTALLED"})
        adm = rec2.get("adm") or {}
        req = root / "adm" / "requests" / f"{bid2}.json"
        check("admin bundle handed to deployd as a request", adm.get("request") == bid2 and req.is_file(), json.dumps(adm))
        if req.is_file():
            sys.path[:0] = [str(code), str(code / "deployd")]
            os.environ["APP_BUILDER_ROOT"] = str(root); os.environ["ATTA_ADM_ROOT"] = str(root / "adm")
            from adm import queue as q, authz
            jobs = q.sweep_requests()
            meta = [m for m in q.pending() if m["job_id"] in jobs]
            check("deployd queues it as a WEB job for the admin", len(meta) == 1 and meta[0]["origin"] == "web"
                  and meta[0]["requested_by"] == "admin" and authz.allowed(meta[0])[0], json.dumps(meta)[:300])
            for m in meta:
                q.finish(m)
        # a tester's bundle never gets that far
        bid3 = upload(t, bundle.name, bundle.read_bytes())
        rec3 = wait_state(t, bid3, {"FAILED"})
        check("tester's ATTa bundle refused", rec3.get("refused") is True and not (root / "adm" / "requests" / f"{bid3}.json").exists())
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        for d in reversed(made):                                   # only what this script created
            d.rmdir()
        print(f"logs and data: {base}")
    print(f"{sum(RESULTS)}/{len(RESULTS)} checks passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
