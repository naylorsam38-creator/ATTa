# System Watcher — Build Handoff

Date: 2026-09-22
Owner: Sam
Applies to: `UI_Skin_Capability_OneShot_v2.zip` (folder `out/`) and the apps it installs into.
Companion to: `Dormant-Hook-and-Watcher-Build-Handoff.md` (the hook change and defect list — read that first; this spec replaces its §4 "Hook Watcher" with something wider).

---

## 1. Purpose

One script that watches the whole chain for every app and says, per app, exactly which stage is broken.

It is a script, not an app. No web UI, no database, no service. Python 3, stdlib only, one file, runs from a terminal or cron.

**The chain it watches, in order:**

```
1. INSTALLED   installer ran for this app and wrote the right files
2. APP_UP      the real app answers on its own port
3. PROXY_UP    the ui-bridge proxy answers on its port
4. SKIN        the served page contains the stylesheet link and the CSS actually serves
5. HOOK        the served page contains the dormant port script tag and port.js actually serves
6. CLEAN       the proxied page loads in a real browser with zero console errors
```

The first stage that fails is the verdict. Everything after it is skipped and marked `SKIPPED`. That tells you exactly where to look.

**Standards.**
- No mocks. Every check is a real request to a real process or a real read of a real file. Nothing here trusts a manifest, a `deployment.json`, or an `installed: true` flag — those are written by the installer and prove nothing.
- Never modifies app source. Never modifies anything except its own log file.
- Config block at the top, one comment per setting, above any logic.
- Deliver the complete file plus captured real output from the acceptance tests.

---

## 2. Config block

```python
# ===================== CONFIG — edit here, nothing below needs reading =====================
# Every app to watch. One entry each. Add a dict per app.
#   name        — label used in output
#   ui_dir      — path to the app's .ui-capability folder written by install_all.py
#   app_url     — the real app's own URL (what TARGET_URL points at)
#   proxy_url   — the ui-bridge proxy's URL in front of it
APPS = [
    # {"name": "memos", "ui_dir": "/srv/apps/memos/.ui-capability",
    #  "app_url": "http://127.0.0.1:5230/", "proxy_url": "http://127.0.0.1:8080/"},
]
# Files the installer must have written inside ui_dir. Missing any = INSTALLED fails. Add to this if the installer grows.
REQUIRED_FILES = ["skin.css", "skin.json", "deployment.json", "run-ui.sh",
                  "ui-bridge/proxy.js", "capability-port/port.js"]
# Marker the proxy stamps on the hook script tag. Must match HOOK_MARKER in proxy.js or HOOK always fails.
HOOK_MARKER = "data-capability-hook"
# Route the proxy serves port.js from. Must match PORT_ROUTE in proxy.js.
PORT_ROUTE = "/_cp/port.js"
# Route prefix the proxy serves skins from. Must match proxy.js.
SKIN_ROUTE_PREFIX = "/_cs/"
# Customer header and id the proxy expects. Blank if the proxy injects without one.
CUSTOMER_HEADER = ""
CUSTOMER_ID = ""
# Seconds to wait for any single request before calling it down.
TIMEOUT_SECONDS = 10
# Seconds between sweeps when run with --loop. Lower = faster detection, more load.
INTERVAL_SECONDS = 300
# Run the real-browser CLEAN stage (needs Playwright + Chromium installed). False marks stage 6 NOT_RUN; this is test-only and is not the production default.
BROWSER_CHECK = True
# Where results go. One JSON line per app per sweep.
LOG_FILE = "system-watcher.log"
# ==========================================================================================
```

---

## 3. The six stages — what each check actually does

### Stage 1 — INSTALLED
- `ui_dir` exists and is a directory.
- Every path in `REQUIRED_FILES` exists and is non-empty.
- `run-ui.sh` is executable.
- `skin.css` and `skin.json` are not zero bytes; `skin.json` parses as JSON.
- `ui-bridge/proxy.js` contains the string `HOOK_MARKER` (if not → the proxy in this app is the old unpatched one → detail `PROXY_STALE`).
- `capability-port/port.js` SHA-256 is recorded for stage 5.

Fail codes: `DIR_MISSING`, `FILE_MISSING:<name>`, `NOT_EXECUTABLE`, `BAD_JSON`, `PROXY_STALE`.

### Stage 2 — APP_UP
- `GET app_url`. Any HTTP response (even 401/403 — some apps gate the root) counts as up. Connection refused or timeout = down.

Fail codes: `REFUSED`, `TIMEOUT`, `DNS`.

### Stage 3 — PROXY_UP
- `GET proxy_url` with `Accept-Encoding: identity` and the customer header if set. Must return HTTP and `Content-Type: text/html`.
- A `502` from the proxy means the proxy is up but can't reach the app → fail code `UPSTREAM_502` (distinct from stage 2 failing, because it points at the `TARGET_URL` the launcher was given).
- A `400` with body starting `FAIL:` means the proxy rejected the request → fail code `PROXY_400:<message>`.

Fail codes: `REFUSED`, `TIMEOUT`, `NOT_HTML`, `UPSTREAM_502`, `HTTP_5XX`, `PROXY_400:<msg>`, `REDIRECT_OFF_PROXY:<url>`, `REDIRECT:<status>`.
A proxy CSS response must also be `text/css` and non-empty; failures are `NOT_CSS` or `CSS_EMPTY`. A port response must contain `javascript` in Content-Type and be non-empty; failures are `NOT_JAVASCRIPT` or `PORT_EMPTY`.

### Stage 4 — SKIN
Using the body from stage 3:
- Exactly one `<link rel="stylesheet" href="/_cs/...css">` present, before `</head>`.
- `GET proxy_url + that href`. Must be `200`, `Content-Type: text/css`, non-empty.
- Body of that CSS equals `ui_dir/skin.css` byte for byte (SHA-256).

Fail codes: `NO_LINK`, `DUPLICATE_LINK`, `LINK_AFTER_HEAD`, `CSS_404`, `NOT_CSS`, `CSS_EMPTY`, `CSS_MISMATCH`.

### Stage 5 — HOOK
Using the same body:
- Exactly one `<script ... HOOK_MARKER ... src="...PORT_ROUTE">` present, before `</head>`.
- `GET proxy_url + PORT_ROUTE`. Must be `200`, `Content-Type` contains `javascript`, non-empty.
- SHA-256 equals `ui_dir/capability-port/port.js`.

Fail codes: `NO_TAG`, `DUPLICATE_TAG`, `TAG_AFTER_HEAD`, `PORT_404`, `NOT_JAVASCRIPT`, `PORT_EMPTY`, `PORT_MISMATCH`.

### Stage 6 — CLEAN
Only if `BROWSER_CHECK` is true:
- Open `proxy_url` in real headless Chromium (Playwright). Wait for network idle.
- Assert: stylesheet request returned 200; `port.js` request returned 200; zero `console.error` entries; zero uncaught page errors; the page `<body>` is not empty.
- Assert the port is DORMANT: no element on the page carries a capability anchor added by the port, no port-owned DOM nodes exist. (Port present, port idle.)

Fail codes: `CSS_FAILED_TO_LOAD`, `PORT_FAILED_TO_LOAD`, `CONSOLE_ERRORS:<n>`, `PAGE_ERROR:<msg>`, `EMPTY_BODY`, `PORT_NOT_DORMANT`.

---

## 4. Output

**Stdout — one block per app:**

```
memos
  1 INSTALLED   OK
  2 APP_UP      OK       http://127.0.0.1:5230/  200
  3 PROXY_UP    OK       http://127.0.0.1:8080/  200 text/html
  4 SKIN        FAIL     NO_LINK
  5 HOOK        SKIPPED
  6 CLEAN       SKIPPED
  VERDICT: BROKEN AT SKIN — NO_LINK
```

Final line: `PASS` if every app reaches stage 6 `OK`, otherwise `FAIL` followed by a one-line-per-app summary of where it broke.

**Exit code:** `0` PASS · `1` FAIL · `2` config error (empty `APPS`, unreadable `ui_dir`, marker mismatch).

**Log file:** one JSON line per app per sweep:
`{"ts":..., "app":..., "verdict":..., "broken_at":..., "code":..., "stages":{...}}`

**`--loop` mode:** sweep every `INTERVAL_SECONDS`. After the first sweep, print only apps whose verdict changed. Keeps the terminal readable overnight.

---

## 5. What it must NOT do

- Restart, repair, redeploy, or touch anything. Read-only. (Repair is a separate decision the owner has not made — see §7.)
- Trust `deployment.json`, `UI_CAPABILITY_DEPLOYMENT_MANIFEST.json`, or `RESOLVED_APP_SKIN_MAPPING.json`. They may be read for display only, never used as evidence.
- Call any Capability Port function. Stage 6 checks the port is present and idle; it never invokes it.
- Depend on anything outside the Python standard library, except Playwright for stage 6.

---

## 6. Acceptance tests — real processes, real bytes

**T1 — Full green.** One real app running (any app from the library, e.g. Memos in Docker). `install_all.py` run against its library dir. `run-ui.sh` started with the real `TARGET_URL`. Watcher reports all six stages `OK`, `PASS`, exit `0`. Capture stdout.

**T2 — App down.** Stop the app. Watcher reports `APP_UP FAIL REFUSED`, stages 3–6 `SKIPPED`, exit `1`.

**T3 — Proxy down.** App up, proxy stopped. `PROXY_UP FAIL REFUSED`.

**T4 — Proxy up, wrong target.** Start the proxy with a `TARGET_URL` pointing at a dead port. `PROXY_UP FAIL UPSTREAM_502`.

**T5 — Stale proxy.** Copy the original unpatched `proxy.js` over the installed one. `INSTALLED FAIL PROXY_STALE` before any request is made.

**T6 — Skin drift.** Edit one byte of the served CSS source. `SKIN FAIL CSS_MISMATCH`.

**T7 — Hook stripped.** Serve the page straight from the app (bypass proxy) by pointing `proxy_url` at `app_url`. `SKIN FAIL NO_LINK` (skin is checked first, so it breaks there — correct).

**T8 — Missing file.** Delete `skin.json`. `INSTALLED FAIL FILE_MISSING:skin.json`.

**T9 — Loop mode.** Run `--loop` with `INTERVAL_SECONDS=5`, kill the app mid-run, restart it. Output shows exactly two changed-verdict lines (down, then up) and nothing else between.

**T10 — Browser stage.** With `BROWSER_CHECK=True` and a real page, stage 6 `OK`. Inject a deliberate `console.error` via a test upstream page and confirm `CONSOLE_ERRORS:1`.

Deliver captured stdout and exit codes for every test.

---

## 7. Out of scope and open rulings

**Out of scope**
- Repairing anything. The watcher reports; it does not fix. Auto-repair (re-run `run-ui.sh`, re-copy `proxy.js`) is a separate small script once the owner rules on cover-before-swap.
- Alerting (email, Slack, etc). Exit code and log file are the interface; anything that reads them is a later add.
- Fleet-wide scheduling / Coolify integration. Owner does Coolify himself.
- The browser-extension injector.

**Rulings the owner holds**
- Cover-before-swap on repair: not decided. Build nothing that repairs until it is.
- Whether stage 6 (browser) runs on every sweep or only on demand — default on, owner may flip `BROWSER_CHECK` off for speed.

**Dependencies on the other handoff**
- Stage 5 cannot pass until the proxy change in `Dormant-Hook-and-Watcher-Build-Handoff.md` §3 is built.
- Stage 4 cannot pass until defect 3 in that document (installer/proxy folder mismatch) is fixed.
- Stage 3 cannot pass until defects 1 and 2 (`run-ui.sh` port 0, missing customer header) are fixed.
