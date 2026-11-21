# Setup and login: how the gateway starts and how a person signs in

Written 2026-11-21 from the code on branch `claude/great-fermi-lbmo0l` (ATTa v121.5, commit `cef7329`) and from
a running instance started with the real launcher. Nothing live was touched. The instance ran in a throwaway
scratch folder, called `$ROOT` below.

## 0. Read this first: what this document covers

- **The request named `/AIRexploit`, a "Hub" and a "Desk login sheet". None of these exist here.**
  VERIFIED:
  ```
  $ ls -la /AIRexploit
  ls: cannot access '/AIRexploit': No such file or directory
  ```
  The only repository in this session is `naylorsam38-creator/ATTa`, and no computer was attached. In ATTa's
  code, "hub" appears only as "Docker Hub" and "desk" only as "helpdesk". This document therefore describes
  **ATTa's login**, using these names:
  - **"Hub"** = the ATTa web gateway, `04-deployment/gateway.py`.
  - **"Desk login sheet"** = the gateway's `GET /login` page.

  If AIRexploit is a different system, none of this applies to it.
- **Marks.** **VERIFIED** means the raw output is shown underneath. **UNVERIFIED** means the item was read
  in the code (file:line given) or in a script, but was not run here.
- **Edits to raw output.** Long scratch paths are shortened to `$ROOT`. Secrets are redacted (`<redacted>`,
  `<nonce>.<hmac>`, `<password printed>`). Nothing else was changed.
- **Where this ran.** A Linux container with no systemd (`ls -d /run/systemd/system` → `No such file or
  directory`), running as uid 0, with Python 3.13.18. Python Playwright 1.63.0 was installed for the browser
  steps (`pip install playwright==1.63.0`). Because there is no systemd here, `bash run` took its **local**
  path. The **server** path (`bootstrap.sh`, systemd, nginx, TLS) and the **Coolify/container** path
  (`container_main.py`) were not run. Every statement about them is UNVERIFIED.

---

## 1. How the gateway starts

### 1.1 The launcher

| Item | Value | Mark |
|---|---|---|
| Launcher | `bash run` (the `run` file at the repo root) | VERIFIED (below) |
| Which path it takes | `/run/systemd/system` exists on Linux → **full server deploy** (`sudo bash 04-deployment/bootstrap.sh`). Otherwise → **local instance** | UNVERIFIED for the server branch (`run`: `is_systemd_linux`, `full_deploy`). VERIFIED for the local branch |
| Data folder (local) | `$APP_BUILDER_ROOT`, default `~/.app-builder-local` | VERIFIED |
| Data folder (server) | `/srv/app-builder`. Code goes in `/opt/app-builder`, a symlink into `/opt/app-builder-releases/…` | UNVERIFIED (`bootstrap.sh:3-4`) |
| Address and port | `APP_BUILDER_HOST=127.0.0.1`, `APP_BUILDER_PORT=8787` from `$ROOT/.env`. The container image uses `0.0.0.0:8787` | VERIFIED locally. Container UNVERIFIED (`Dockerfile:10`) |
| Processes (local) | `python3 04-deployment/gateway.py` and `python3 04-deployment/pipeline.py`, started with `nohup`, cwd `$ROOT`, pids in `$ROOT/gateway.pid` and `$ROOT/pipeline.pid` | VERIFIED |
| Processes (server) | systemd units `app-builder-gateway`, `app-builder-pipeline`, `app-builder-watcher`, `atta-deployd`, plus nginx on :80 proxying to 127.0.0.1:8787. All run as root (no `User=` line) | UNVERIFIED (`bootstrap.sh:25-57,300-310`) |
| What `bash run` does before starting | Writes `.env` if missing (new random `APP_BUILDER_SESSION_SECRET`), runs `accounts.py init`, copies `02-front-door/front-door.html` to `$ROOT/front-door.html`, copies `upstream_apps.json` | VERIFIED (output and `ls` below) |
| Side effect | The last lines of `run` (`first_boot`) create `.initialized` in the **current directory**. The file's first two lines are function definitions placed *above* the `#!` line | VERIFIED: `ls $S/.initialized` printed the file after `cd $S; bash run`. `head -3 run` is shown in §4 |

VERIFIED: start.
```
$ bash /home/user/ATTa/run          (cwd = scratch, APP_BUILDER_ROOT=$ROOT)
created admin (admin)
created tester01 (user)
… tester02 … tester10 …
11 account(s) created. Passwords: $ROOT/TEST_ACCOUNTS.txt
------------------------------------------------------------
RUNNING (local instance)
------------------------------------------------------------
  Open:      http://127.0.0.1:8787/
  Logins:    $ROOT/TEST_ACCOUNTS.txt   (admin + tester01..tester10)
  Stop it any time with:   bash run stop
exit=0
```

VERIFIED: the process, the health check and the status command.
```
$ cat gateway.pid pipeline.pid; ps -o pid,args -p ...
367
368
  PID COMMAND
  367 python3 /home/user/ATTa/04-deployment/gateway.py
  368 python3 /home/user/ATTa/04-deployment/pipeline.py

$ curl -sS -i http://127.0.0.1:8787/health
HTTP/1.0 200 OK
Server: BaseHTTP/0.6 Python/3.13.18
Content-Type: text/html; charset=utf-8
Content-Length: 3
...
OK

$ APP_BUILDER_ROOT=... bash run status
Local instance: UP  (http://127.0.0.1:8787/)
```

### 1.2 How to check it is up

1. `bash run status` prints `Local instance: UP` or `down`. VERIFIED (above, and `down` after the stop in §1.3).
2. `curl -fsS http://127.0.0.1:8787/health` returns exactly `OK`. It needs no login. VERIFIED.
3. Check that the process on the port is the code you think it is:
   `ps -o pid,args -p $(cat $ROOT/gateway.pid)`. VERIFIED (above). `ss` is not installed in this container
   (`ss: command not found`), so a port→pid check with `ss -ltnp` is UNVERIFIED here.
4. Server: `systemctl is-active app-builder-gateway` and `curl -fsSI http://127.0.0.1/` (nginx). These are the
   checks `bootstrap.sh`'s acceptance gate and ADM's `deployd/adm/health.py` use. UNVERIFIED (not run).

### 1.3 How to stop it

VERIFIED:
```
$ APP_BUILDER_ROOT=$ROOT bash run stop
Stopped.
$ APP_BUILDER_ROOT=$ROOT bash run status
Local instance: down.  Start it with:  bash run
$ ps aux | grep -E "gateway.py|pipeline.py"
(none)
```
`stop` kills only the pids recorded in **that** `$ROOT`'s pid files (`run`: `stop_local`).

---

## 2. The full login path, from the login sheet to a session

### 2.1 Endpoints

| Step | Request | Response | Code | Mark |
|---|---|---|---|---|
| Any page without a session | `GET /`, `/builds`, `/api/me`, … | `302 Location: /login` | `gateway.py:350-353` | VERIFIED |
| A POST without a session | `POST /upload` etc. | `403 Forbidden` | `gateway.py:458-459` | VERIFIED |
| The login sheet | `GET /login` | 200 with a form: `POST /login`, fields `user`, `password`, button "Log in" | `gateway.py:352` | VERIFIED |
| Sign in | `POST /login`, form-encoded `user=…&password=…`, body ≤ 8192 bytes | Right password: `302 Location: /` + `Set-Cookie: session=…`. Wrong: `401` "Login failed". Too many failures: `429`. Cross-site: `403`. Too large: `413` | `gateway.py:441-457` | VERIFIED |
| After sign-in | `GET /` | 200, the Front Door (`$ROOT/front-door.html` plus an injected `#ab-nav` naming the account, with a "Log out" link) | `gateway.py:235-240,356-358` | VERIFIED (self-test step 6) |
| Who am I | `GET /api/me` | `{"name": …, "role": …}` | `gateway.py:406` | VERIFIED |
| Sign out | `GET /logout` | `302 Location: /login`, `Set-Cookie: session=; … Max-Age=0` | `gateway.py:354` | VERIFIED |
| Health | `GET /health` | `OK`, no login needed | `gateway.py:349` | VERIFIED |

VERIFIED: the whole path with curl.
```
$ curl -sS -i $B/   (no cookie)
HTTP/1.0 302 Found
Location: /login

$ curl -sS $B/login | grep -o "<form.*</form>"
<form method=post action=/login><input name=user placeholder=User autocomplete=username><br><input name=password type=password placeholder=Password autocomplete=current-password><br><button>Log in</button></form>

$ curl -sS -i -X POST -d user=docprobe01&password=WRONG $B/login
HTTP/1.0 401 Unauthorized
<h1>Login failed</h1><a href=/login>Try again</a></body></html>

$ curl -sS -i -c jar -X POST -d user=docprobe01&password=<pw> $B/login
HTTP/1.0 302 Found
Location: /
Set-Cookie: session=docprobe01:1:1795232177:<nonce>.<hmac>; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400

$ curl -sS -b jar -i $B/api/me
HTTP/1.0 200 OK
{
  "name": "docprobe01",
  "role": "user"
}

$ curl -sS -b jar -o /dev/null -w "%{http_code} %{content_type}\n" $B/
200 text/html; charset=utf-8

$ curl -sS -b jar -i $B/status  (non-admin)
HTTP/1.0 302 Found
Location: /builds

$ curl -sS -b jar -i $B/logout
HTTP/1.0 302 Found
Location: /login
Set-Cookie: session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0
```

**Username handling.** The username is lowercased and trimmed before the check (`gateway.py:449`). The
password is used exactly as typed. VERIFIED:
```
$ curl ... --data-urlencode "user= DocProbe01 " ... /login        → HTTP/1.0 302 Found
$ curl ... --data-urlencode "password=<pw> " (trailing space)     → 401
```

### 2.2 Where users are stored

- **File:** `$ROOT/state/users.json`, mode 0600. It is written atomically: a temp file `.users.*`, then
  `os.replace` (`accounts.py:73-79`). It holds `{"schema": "APP_BUILDER_USERS.v1", "users": {name: {...}}}`.
- **Passwords:** stored as PBKDF2-SHA256 with 600 000 iterations and a 16-byte salt. No plaintext anywhere
  except the one-time printout and `TEST_ACCOUNTS.txt`.
- The gateway **reads the file on every request that carries a cookie** (`auth()` → `accounts.get()` →
  `load()`). Changes made with `accounts.py` therefore apply immediately, with no restart.

VERIFIED (hash and salt truncated):
```
{
 "name": "docprobe01",
 "role": "user",
 "password": {"algo": "pbkdf2_sha256", "iterations": 600000, "salt": "d85de5e0...", "hash": "c321ea43..."},
 "created": 1795232176.878427,
 "session_version": 1,
 "note": ""
}
$ stat -c "%a %U %n" users.json TEST_ACCOUNTS.txt .env
600 root $ROOT/state/users.json
600 root $ROOT/TEST_ACCOUNTS.txt
600 root $ROOT/.env
```

### 2.3 Where sessions are stored: nowhere on the server

There is **no session table**. The session is a signed cookie:
`session=<name>:<session_version>:<issued-unix-time>:<random nonce>.<HMAC-SHA256(value, APP_BUILDER_SESSION_SECRET)>`
(`gateway.py:44-49`). The cookie is `HttpOnly; SameSite=Strict; Path=/; Max-Age=86400`. It also gets `Secure`
when the request has `X-Forwarded-Proto: https` (`gateway.py:452-453`). VERIFIED:
```
$ curl ... -H 'X-Forwarded-Proto: https' -X POST ... /login | grep -i set-cookie
Set-Cookie: session=<token>; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400
```

### 2.4 How create account works

**ATTa has no web sign-up or "create account" endpoint.** The only POST routes are `/login`,
`/api/front-door/model`, `/api/front-door/choice`, `/upload`, `/add-repo` and `/apps/<app>/secrets…`
(`gateway.py:436-560`). Accounts are made only on the machine itself, with `04-deployment/accounts.py`:

| Command | Effect | Mark |
|---|---|---|
| `accounts.py init` | Creates `admin` and `tester01`…`tester10` if they are missing. Appends their passwords to `$ROOT/TEST_ACCOUNTS.txt` (0600). Never changes existing accounts. Run by `bash run`, `bootstrap.sh` and `container_main.py` | VERIFIED (§1.1 output) |
| `accounts.py add NAME [--admin]` | Creates one account and prints its password **once** (stdout) | VERIFIED |
| `accounts.py reset NAME` | Sets a new password, prints it, and bumps `session_version` (ends that account's sessions) | VERIFIED (§2.5) |
| `accounts.py disable NAME` / `enable NAME` | Blocks or allows logins, bumps `session_version` | VERIFIED (§2.5) |
| `accounts.py list` | Lists names, roles, active or DISABLED | VERIFIED |

Rules: a name must match `^[a-z0-9][a-z0-9_.-]{1,31}$`, and names in `reserved.py` (`system`, `root`, `atta`,
`adm`, `local`, `gateway`, `pipeline`, …; `admin` is *not* reserved) are refused. VERIFIED:
```
$ accounts.py add system
error: 'system' is reserved for ATTa's own processes and can't be an account
exit=1
$ accounts.py add Bob
error: bad username 'Bob': 2-32 chars, lowercase letters, digits, _ . -
exit=1
```
**`accounts.py` writes to whichever `APP_BUILDER_ROOT` it is given.** Without the variable it writes to
`/srv/app-builder`. VERIFIED:
```
$ env -u APP_BUILDER_ROOT python3 -c "...import accounts;print(accounts.USERS_FILE)"
/srv/app-builder/state/users.json
```

### 2.5 How a session is checked on each page

`auth()` (`gateway.py:50-63`) runs on every request except `/health` and `POST /login`. For the `session`
cookie it:
1. checks the HMAC against `APP_BUILDER_SESSION_SECRET` (constant-time compare);
2. checks the age: `0 ≤ now − issued ≤ APP_BUILDER_SESSION_TTL` (default 86400 s);
3. loads the account from `users.json`: it must exist, must not be disabled or reserved, and its
   `session_version` must equal the one in the cookie.

Any failure → no user → `GET` redirects to `/login`, `POST` gets 403. On top of that, every POST must pass
`same_origin_post()` (`gateway.py:91-105`): `Sec-Fetch-Site` must be same-origin or none, and
`Origin`/`Referer`, if sent, must match `Host` (host and port). Admin-only pages (`/status`, `/alerts`)
redirect a `user` to `/builds`.

VERIFIED, one row per check:
```
forged cookie (right shape, wrong HMAC) → 302 -> /login
cookie from a gateway with a DIFFERENT SESSION_SECRET → 302 -> http://127.0.0.1:8798/login
same secret, APP_BUILDER_SESSION_TTL=1, cookie 3 s old → 302 -> /login   (same cookie on TTL 86400: 200)
accounts.py reset docprobe03, then replay the cookie → 302 -> /login
accounts.py disable docprobe01 → new login 401; replay of the cookie from before → 302
Origin: http://evil.example → 403 cross-site request refused
Sec-Fetch-Site: cross-site  → 403
```

---

## 3. Files the login depends on

### 3.1 Code (in the checkout; the server copy is in `/opt/app-builder`)

| File | Role in the login |
|---|---|
| `run` | Launcher: writes `.env`, runs `accounts.py init`, copies the Front Door, starts the gateway, waits on `/health` |
| `04-deployment/gateway.py` | `/login`, `/logout`, `auth()`, cookie signing, rate limit, same-origin check, every protected page |
| `04-deployment/accounts.py` | `users.json`, PBKDF2 hashing, `verify()`, the `init/add/reset/disable/enable/list` CLI |
| `04-deployment/reserved.py` | Names that can never be accounts. Imported by `accounts.py`. If it is missing, the gateway cannot start |
| `04-deployment/builds.py`, `alerts.py`, `user_catalogue.py`, `app_owners.py`, `customer_secrets.py` | Imported at the top of `gateway.py` (line 6). If any one is missing or broken, the gateway never starts, so nobody can log in (VERIFIED by the self-test in §5.3, case 3) |
| `02-front-door/front-door.html` | The page you land on after login. Copied to `$ROOT/front-door.html` |
| `04-deployment/bootstrap.sh`, `bootstrap-lib.sh`, `envfile.py` | Server install: `.env`, `accounts.py init`, systemd units (UNVERIFIED here) |
| `04-deployment/app-builder-nginx.conf` | Server proxy: sets `Host $host`, `X-Real-IP`, `X-Forwarded-Proto` (UNVERIFIED here) |
| `04-deployment/container_main.py`, `Dockerfile`, `docker-compose.coolify.yml` | Container/Coolify start: same `.env`, `accounts.py init` (UNVERIFIED here) |

### 3.2 Data (in `$ROOT`): a change must never overwrite these

| File | What overwriting it does | Mark |
|---|---|---|
| `$ROOT/state/users.json` | **Every account and password hash is lost.** If the file ends up with no users, the gateway refuses to start (`No accounts exist`) | VERIFIED (startup refusal, §4) |
| `$ROOT/.env`, specifically `APP_BUILDER_SESSION_SECRET` | A new secret **signs everyone out at once**; old cookies redirect to `/login`. Note that `run` itself deletes an `.env` that has no `APP_BUILDER_SESSION_SECRET=` line (`run`: `ensure_env`) | VERIFIED (different-secret test, §2.5) |
| `$ROOT/TEST_ACCOUNTS.txt` | The **only** copy of the generated passwords. It is appended to, never rewritten. Lose it and the fix is `accounts.py reset NAME` | VERIFIED (contents header, §1.1) |
| `$ROOT/state/front-door-choices/` | Per-account Front Door answers (not needed to log in, but per-user history) | UNVERIFIED (`gateway.py:479`) |

These are **meant** to be overwritten on every start or deploy, so don't hand-edit the copies in `$ROOT`:
`$ROOT/front-door.html` (replaced from `02-front-door/` by `run`, `bootstrap.sh` and `container_main.py`) and
`$ROOT/upstream_apps.json`.

---

## 4. Common reasons login fails, and the check that proves each one

| # | Symptom | Cause in the code | Check | Mark |
|---|---|---|---|---|
| 1 | Site doesn't load at all | Gateway not running | `bash run status`; `curl -fsS http://127.0.0.1:8787/health` | VERIFIED (§1.2) |
| 2 | Gateway exits at start: `APP_BUILDER_SESSION_SECRET is required` | `.env` missing or not loaded (`gateway.py:19`) | `grep -c ^APP_BUILDER_SESSION_SECRET= $ROOT/.env`; `tail $ROOT/gateway.log` | VERIFIED: `APP_BUILDER_SESSION_SECRET is required` / `exit=1` |
| 3 | Gateway exits at start: `No accounts exist. Run: python3 accounts.py init` | Empty or missing `users.json` (`gateway.py:20`) | `APP_BUILDER_ROOT=$ROOT python3 04-deployment/accounts.py list` | VERIFIED: that message / `exit=1` |
| 4 | Gateway exits at start: `OSError: [Errno 98] Address already in use` | Something else holds the port | `curl http://127.0.0.1:8787/health`; find the owner of the port | VERIFIED |
| 5 | `bash run` says `Already running.` but your change isn't there | `run` only checks that *something* answers `/health` on the port, then skips starting. The old process (old code, or another `$ROOT`) keeps serving | `ps -o pid,args -p $(cat $ROOT/gateway.pid)`; then `bash run stop` and `bash run` | VERIFIED: second `bash run` printed `all accounts already exist` / `Already running.` |
| 6 | "Login failed" with the right password | (a) the account was made in a **different `APP_BUILDER_ROOT`** (default `/srv/app-builder`); (b) the account is disabled; (c) the password has a stray space (it's not trimmed); (d) the name is reserved | `APP_BUILDER_ROOT=<the gateway's root> accounts.py list`; check the gateway's root with `cat /proc/$(cat gateway.pid)/environ \| tr '\0' '\n' \| grep ROOT` or the cwd | VERIFIED: (a) default path printout, (b) disabled → 401, (c) trailing space → 401, (d) reserved refused |
| 7 | "Too many login attempts" (429) even with the right password | 8 failures (`APP_BUILDER_LOGIN_MAX_FAILURES`) from one client IP within 900 s (`APP_BUILDER_LOGIN_WINDOW`). IP = `X-Real-IP`, else the TCP peer (`gateway.py:21-42`). **Behind a proxy that does not set `X-Real-IP`, every user shares the proxy's IP**, so one person's failures lock everyone out. The counter lives in memory: a gateway restart clears it (UNVERIFIED, code) | Wait 15 min or restart the gateway; check that the proxy sets `X-Real-IP` | VERIFIED: `401 ×8`, then `429` with the right password, `302` from another `X-Real-IP` |
| 8 | 403 "cross-site request refused" on login | `Origin` doesn't match `Host`. Examples: nginx on a non-default port (`proxy_set_header Host $host` drops the port), opening the site under one name while the proxy sends another `Host`, or `Sec-Fetch-Site: cross-site` | Compare the browser address with the `Host` the gateway receives | VERIFIED: `Origin: http://atta.example:8080` + `Host: atta.example` → 403; `Origin: https://atta.example` → 302; `Origin: http://localhost:8787` with Host `127.0.0.1:8787` → 403 |
| 9 | Login "succeeds" but you land back on the login sheet | The browser drops the cookie. (a) It is `Secure` because the proxy sent `X-Forwarded-Proto: https` while the browser is on plain http; (b) `SameSite=Strict` when arriving from a link on another site | DevTools → Application → Cookies; `curl -i` the POST and look at `Set-Cookie` | VERIFIED that `Secure` is added with `X-Forwarded-Proto: https`. The browser-drop itself is UNVERIFIED |
| 10 | Everyone was suddenly logged out | `APP_BUILDER_SESSION_SECRET` changed (new `.env`); or `accounts.py reset/disable` (that account only); or 24 h passed (`APP_BUILDER_SESSION_TTL`) | Compare `.env`'s modified time; check `session_version` in `users.json` | VERIFIED (§2.5) |
| 11 | After login: 500 "front-door.html not installed" | `$ROOT/front-door.html` missing | `ls -l $ROOT/front-door.html`; `bash run` copies it back | VERIFIED: `<h1>front-door.html not installed</h1>` / `500` |
| 12 | No login at all, everyone is "local-admin" | `APP_BUILDER_AUTH_DISABLED=1` in the environment (`gateway.py:16`). Must never be set on a server | `grep AUTH_DISABLED $ROOT/.env`; `GET /` returns 200 without a cookie | UNVERIFIED (code). The self-test strips it and fails at step 4 if it were active |
| 13 | Gateway crashes at start after a code change | Import error in `gateway.py` or one of its imported modules | `tail $ROOT/gateway.log`; `python3 -c "import ast,sys;ast.parse(open('04-deployment/gateway.py').read())"` | VERIFIED: self-test §5.3, case 3 (`ModuleNotFoundError`) |
| 14 | Request body rejected (413) | `POST /login` body > 8192 bytes | — | VERIFIED: `413` |
| 15 | `./run` behaves oddly | Lines 1-2 of `run` come **before** `#!/usr/bin/env bash`, so `./run` gets no interpreter line. Always use `bash run` | `head -3 run` | VERIFIED: `head -3 run` shows `detect_aws(){…`, `first_boot(){…`, then `#!/usr/bin/env bash` |

### 4.1 Security notes found while doing this (nothing was changed)

- **Logging out does not end the session on the server.** `/logout` only tells the browser to drop its
  cookie. Anyone holding a copy of the cookie can keep using it until it expires (24 h), the password is
  reset, or the account is disabled. VERIFIED:
  ```
  $ curl -sS -b jar -i $B/logout                                  → 302 Location: /login, Set-Cookie: session=; Max-Age=0
  $ curl -sS -b jar -i $B/api/me   (SAME old cookie replayed)     → HTTP/1.0 200 OK {"name": "docprobe01", ...}
  ```
  A fix (bump `session_version` on logout, which signs that account out on every device) would change
  `gateway.py`. It was **not** made, because this task was add-only.
- **`X-Real-IP` is taken from the client as given.** When the gateway is reachable without a proxy that
  overwrites the header (the container binds `0.0.0.0`), a client can send a new `X-Real-IP` with every try
  and dodge the 8-failure limit. VERIFIED that the limit is per header value (row 7). Whether Coolify's proxy
  overwrites it is UNVERIFIED.

---

## 5. The Login self-test

### 5.1 What it is and how to run it

`04-deployment/login_selftest.py`. It uses only stdlib plus Playwright, with a real launcher, real accounts and
a real Chromium. **No mocks.**

```
python3 04-deployment/login_selftest.py          # exit 0 = passed, 1 = failed
python3 -m unittest discover -s tests -v         # the existing suite; includes tests/test_login_selftest.py
```

| Step | Name | What it proves |
|---|---|---|
| 0 | preflight | Required files exist, `curl` and Playwright are present, and this is **not** a systemd host (there, `bash run` would be the live server deploy) |
| 1 | launcher | `bash run` starts a fresh instance in a new temp `APP_BUILDER_ROOT`, on a free port it checked nothing answers on. It must print `RUNNING (local instance)` at that port, not `Already running.` |
| 2 | health | `/health` = `OK`, and the pid in `gateway.pid` runs **this checkout's** `gateway.py` |
| 3 | account | `accounts.py add selftest-<random>` creates a usable `user` entry in `users.json` |
| 4 | desk | Chromium opens `/`, is redirected to `/login`, and finds the user, password and "Log in" fields |
| 5 | wrong-pw | A wrong password shows "Login failed" and sets no session cookie |
| 6 | sign-in | The right password lands on the Front Door at `/`, with an HttpOnly `session` cookie and `#ab-nav` naming the account with "Log out" |
| 7 | protected | `/builds` loads (200, "My builds"); `/api/me` with the browser's cookies names the account |
| 8 | sign-out | Clicking "Log out" lands on `/login` and clears the cookie; `/builds` now redirects to `/login` |
| 9 | sign-in-again | Sign in again, then the step 7 checks again |
| 10 | stop | `bash run stop` prints `Stopped.` and the port stops answering |

On failure it prints `LOGIN SELF-TEST FAILED at step N (<name>)`, what was expected versus what happened, a
screenshot path, the tail of `gateway.log` and the steps that passed. It stops its own instance, keeps the temp
folder for inspection, and exits 1. **It is never skipped.** Missing Playwright, or a systemd host, is a
failure, because a login that wasn't checked is not proof that it works.

**Safety.** The instance lives only in its temp folder. The launcher's cwd is that folder, so `.initialized`
lands there. The environment passed to the launcher has `APP_BUILDER_*`, `ATTA_*`, `COOLIFY_*`,
`DOCKERHUB_*`, `ANTHROPIC_API_KEY` and `ALERT_WEBHOOK_URL` removed. It stops only the pids in its own folder.

### 5.2 It passes. VERIFIED
```
$ python3 04-deployment/login_selftest.py
LOGIN SELF-TEST  2026-11-21 03:39:26  checkout /home/user/ATTa
PASS  step  0 preflight      checkout /home/user/ATTa
PASS  step  1 launcher       `bash run` -> RUNNING at http://127.0.0.1:59017/ (root /tmp/atta-login-selftest-nr_8aeuz)
PASS  step  2 health         /health 200 OK, pid 824 runs /home/user/ATTa/04-deployment/gateway.py
PASS  step  3 account        created selftest-f4c61120 (role user) in /tmp/atta-login-selftest-nr_8aeuz/state/users.json
PASS  step  4 desk           GET / -> /login sheet with user, password and 'Log in'
PASS  step  5 wrong-pw       wrong password -> 'Login failed', no session cookie
PASS  step  6 sign-in        signed in as selftest-f4c61120: Front Door at /, HttpOnly session cookie, name + 'Log out' in nav
PASS  step  7 protected      /builds 200 'My builds'; /api/me = {"name": "selftest-f4c61120", "role": "user"}
PASS  step  8 sign-out       'Log out' -> /login, cookie cleared, /builds now redirects to /login
PASS  step  9 sign-in-again  signed in as selftest-f4c61120: Front Door at /, HttpOnly session cookie, name + 'Log out' in nav
PASS  step  9 sign-in-again  /builds 200 'My builds'; /api/me = {"name": "selftest-f4c61120", "role": "user"}
PASS  step 10 stop           `bash run stop` -> Stopped., port 59017 closed
LOGIN SELF-TEST PASSED: 11/11 steps
exit=0

$ python3 -m unittest tests.test_login_selftest -v
...
Ran 1 test in 7.686s
OK
```

### 5.3 It catches real breakage. VERIFIED

Each case broke one line in a **throwaway copy** of the checkout (`cp -a`, never the repo), then ran that
copy's self-test. The copy was deleted afterwards.
```
########## sabotage: verify  (accounts.verify never accepts)
<     if u and not u.get("disabled") and hmac.compare_digest(got, p["hash"]):
>     if False:
LOGIN SELF-TEST FAILED at step 6 (sign-in)
after signing in expected the Front Door at /, got http://127.0.0.1:46395/login: 'Login failed\nTry again'
exit=1
########## sabotage: logout  (/logout no longer clears the cookie)
<   if p=="/logout": self.redirect("/login","session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"); return
>   if p=="/logout": self.redirect("/login"); return
LOGIN SELF-TEST FAILED at step 8 (sign-out)
exit=1
########## sabotage: gatewaycrash  (an import that doesn't exist)
> import accounts, builds, no_such_module_xyz, alerts, ...
LOGIN SELF-TEST FAILED at step 1 (launcher)
ModuleNotFoundError: No module named 'no_such_module_xyz'
exit=1
########## systemd host  (mkdir /run/systemd/system for one run in the throwaway container, then rmdir)
LOGIN SELF-TEST FAILED at step 0 (preflight)
this is a systemd Linux host: here `bash run` is the FULL SERVER DEPLOY (bootstrap.sh into /srv/app-builder and /opt/app-builder), i.e. the live system. Refusing. ...
exit=1
```

### 5.4 How it fits the existing verification

- The suite every change already runs is `python3 -m unittest discover -s tests -v`, documented in
  `tests/test_v114_hardening.py:3` and `tests/test_v117_coolify.py:5`. `tests/test_login_selftest.py` is picked
  up by that command automatically, with no other file changed. `CLAUDE.md` (new) tells every agent that work
  is not complete until the suite and the self-test pass.
- **The existing suite already had 8 errors before this change.** The same 8 appear with and without the new
  file. All are Playwright "Sync API inside the asyncio loop" errors in `test_v119_login_proof` (7) and
  `test_v121_journey_author` (1). The self-test runs in its own process, so it is not affected. VERIFIED:
  ```
  before (clean worktree at HEAD):  Ran 257 tests ... FAILED (errors=8, skipped=4)
  after  (with the self-test):      Ran 258 tests ... FAILED (errors=8, skipped=4)   test_login_self_test_passes_end_to_end ... ok
  ```

### 5.5 Not done, because each needs approval or touches the live path

- **Server gate.** Running the self-test inside `bootstrap.sh`'s acceptance gate or ADM's
  `deployd/adm/health.py` would mean editing those files and creating accounts on the live server. As built,
  the self-test refuses to run on a systemd host.
- **Root CI.** The repo has CI only in `05-coolify/kit/.github/`. A root workflow that runs the suite plus the
  self-test on every push would be a new, outward-facing step.
- **Logout revocation.** Fixing the server-side logout issue in §4.1 would mean editing `gateway.py`.
