# Dormant Hook + Hook Watcher — Build Handoff

Date: 2026-09-22
Owner: Sam
Applies to: `UI_Skin_Capability_OneShot_v2.zip` (1035 archive entries, folder `out/`)

---

## 1. Purpose

This adds two small pieces to the delivered package.

**The problem.** The package copies `capability-port/port.js` into every app's `.ui-capability/` folder and writes `"installed": true` in a manifest. That is an assertion, not a connection. No served page anywhere contains the port script. The Capability Port is bolted on, not hooked in.

**What done means.** Every HTML page served through the ui-bridge proxy carries one extra `<script>` tag, before `</head>`, that loads `port.js`. The port sits there dormant — loaded, idle, never invoked — until the owner explicitly calls it. A separate watcher fetches the live served page and proves the tag is present, and puts it back if an upgrade removed it.

**The one rule.** The hook is DORMANT. Nothing in the proxy, installer, or watcher may call, initialise, mount, or attach a capability. Loading the script is the whole job. If you find yourself writing code that *uses* the port, you have gone too far.

**Standards.**
- No mocks, no simulated servers, no fake HTTP. Every test runs a real proxy process against a real upstream and fetches the real bytes.
- App source is never modified. All changes live in `out/ui-bridge/`, `out/install_all.py`, and one new `out/hook-watcher/` folder.
- Every script opens with a plainly labelled, editable config block above any logic — one comment per setting saying what it does and what changes if altered.
- Deliver complete files, not diffs.

---

## 2. Where it lives — the real hook point

Read before touching anything:

| File | What it does today |
|---|---|
| `out/ui-bridge/proxy.js` | Node reverse proxy. `--app-id`, `--target`, `--host`, `--port`, `--customer-header`. Buffers upstream responses. For `text/html` responses **only when a customer layout exists**, it decodes the body, calls `injectStylesheet()`, re-encodes, fixes `Content-Length`. Serves the CSS at `/_cs/<appId>/<layout>.<version>.css`. |
| `injectStylesheet(html, href)` in `proxy.js` | `html.replace(/<\/head\s*>/i, `${tag}</head>`)`. **This is the hook point.** The port tag goes in the same place, same mechanism. |
| `out/install_all.py` → `install_one()` | Copies `capability-port/` and `ui-bridge/` into `<app>/.ui-capability/`, writes `deployment.json` and `run-ui.sh`. |
| `run-ui.sh` (written by installer) | `node ui-bridge/proxy.js --app-id <id> --target "$TARGET_URL" --port "${UI_PORT:-0}"` |
| `out/capability-port/port.js` | The Capability Port itself (31 KB). Do not modify. |

Path layout after install:

```
<APP>/.ui-capability/
  skin.css
  skin.json
  deployment.json
  run-ui.sh
  ui-bridge/proxy.js
  capability-port/port.js      <-- must be served, never is today
```

---

## 3. Part A — Proxy change: inject the dormant hook

All work in `out/ui-bridge/proxy.js`.

### A1. Config block

Add at the top, before any logic, editable constants with a one-line comment each:

```js
// ===================== CONFIG — edit here, nothing below needs reading =====================
// Route the proxy serves port.js from. Change if it clashes with a real app route.
const PORT_ROUTE = '/_cp/port.js';
// Where port.js lives relative to this file. Change only if the folder layout changes.
const PORT_FILE = path.join(__dirname, '..', 'capability-port', 'port.js');
// Attribute stamped on the injected tag so the watcher can find it. Change both here and in the watcher.
const HOOK_MARKER = 'data-capability-hook';
// Inject the hook on every HTML page (true) or only when a customer layout exists (false). Dormant hook must be universal — leave true.
const HOOK_ALWAYS = true;
// ==========================================================================================
```

### A2. Serve the file

New route: `GET <PORT_ROUTE>` → read `PORT_FILE`, respond `200`, `Content-Type: application/javascript; charset=utf-8`, `Cache-Control: no-store`, `Content-Length` set. File missing → `404`. This route does **not** require the customer header (the hook must load even when no layout is selected).

### A3. Inject the tag

New function `injectHook(html)`:

```js
const tag = `<script src="${PORT_ROUTE}" ${HOOK_MARKER}="1" defer></script>`;
```

Inserted immediately before `</head>` using the same regex as `injectStylesheet`. If `</head>` is absent, HTML is returned unchanged (same behaviour as the stylesheet).

Idempotence: if the HTML already contains `HOOK_MARKER`, do not inject a second tag.

### A4. Change the HTML branch in `proxyRequest`

Today the branch returns the upstream body untouched when `readLayout()` returns `null`. Change to:

1. Decode body.
2. If a layout exists → `injectStylesheet` as today.
3. If `HOOK_ALWAYS` (or a layout exists) → `injectHook`.
4. Re-encode, fix `Content-Length` / `Content-Encoding` exactly as today.

The customer-header check must move so a missing header no longer throws on HTML responses when `HOOK_ALWAYS` is true and no layout lookup is needed. Missing header → no stylesheet, hook still injected, page still served.

### A5. Nothing else

No call to any port function. No `window.CapabilityPort` access. No inline scripts. The tag is the deliverable.

---

## 4. Part B — Hook Watcher

New folder `out/hook-watcher/` containing `watch.py` (Python 3, stdlib only, no pip installs) and `README.md`.

### B1. Config block

```python
# ===================== CONFIG — edit here, nothing below needs reading =====================
# List of served app URLs to check. One per line. Add or remove apps here.
TARGETS = [
    # "http://127.0.0.1:8080/",
]
# Marker the proxy stamps on the hook tag. Must match HOOK_MARKER in proxy.js or every check fails.
HOOK_MARKER = "data-capability-hook"
# Route the proxy serves port.js from. Must match PORT_ROUTE in proxy.js.
PORT_ROUTE = "/_cp/port.js"
# Seconds between sweeps when run with --loop. Lower = faster detection, more requests.
INTERVAL_SECONDS = 300
# Seconds to wait for a page before calling it unreachable.
TIMEOUT_SECONDS = 10
# Header name and value the proxy expects to identify the customer. Leave blank if HOOK_ALWAYS is true in the proxy.
CUSTOMER_HEADER = ""
CUSTOMER_ID = ""
# Path to the app's .ui-capability folder, used by --repair to re-run run-ui.sh. Leave blank to disable repair.
REPAIR_DIRS = {
    # "http://127.0.0.1:8080/": "/path/to/APP/.ui-capability",
}
# Where results are written. Every sweep appends one JSON line per target.
LOG_FILE = "hook-watcher.log"
# ==========================================================================================
```

### B2. What one check does

For each URL in `TARGETS`:

1. `GET` the page with `TIMEOUT_SECONDS`. Follow redirects. Send `Accept-Encoding: identity` so the body is plain. If `CUSTOMER_HEADER` is set, send it.
2. Confirm `Content-Type` is `text/html`.
3. Confirm the body contains a `<script` tag with `HOOK_MARKER` **and** `src` ending in `PORT_ROUTE`, and that it appears before `</head>`.
4. Confirm exactly one such tag (zero = missing, two or more = double injection).
5. `GET` `<origin><PORT_ROUTE>`. Confirm `200`, `Content-Type` contains `javascript`, body non-empty, and body is byte-identical to `capability-port/port.js` on disk when a `REPAIR_DIRS` path is known (SHA-256 compare).
6. Result per target is one of: `HOOKED`, `MISSING_TAG`, `DUPLICATE_TAG`, `TAG_AFTER_HEAD`, `PORT_404`, `PORT_MISMATCH`, `NOT_HTML`, `UNREACHABLE`.

### B3. Output

- Stdout: one line per target, `STATUS  url  detail`. Final line `PASS` if every target is `HOOKED`, else `FAIL`.
- Exit code `0` on PASS, `1` on FAIL, `2` on config error (empty `TARGETS`, marker mismatch, etc.).
- `LOG_FILE`: one JSON line per target per sweep — `{ts, url, status, detail, port_sha256}`.

### B4. Modes

- `python3 watch.py` — one sweep, exit.
- `python3 watch.py --loop` — sweep every `INTERVAL_SECONDS` forever. Prints only status changes after the first sweep so the log stays readable.
- `python3 watch.py --repair` — after a sweep, for any target not `HOOKED` that has a `REPAIR_DIRS` entry: confirm `ui-bridge/proxy.js` in that dir contains `HOOK_MARKER` (if not, the proxy itself was replaced by an upgrade → status `PROXY_STALE`, print the exact `cp` command to restore it from `out/ui-bridge/proxy.js`, do not run it); then re-run `run-ui.sh` for that dir and re-check once. Repair never touches app source.

### B5. What the watcher must NOT do

Load the page in a browser. Execute `port.js`. Call any port API. Trust `deployment.json` or `UI_CAPABILITY_DEPLOYMENT_MANIFEST.json` — those files are inputs to nothing here. The served bytes are the only evidence.

---

## 5. Known defects in the delivered package — fix these or nothing above can be tested

Found by reading the source. Each must be fixed and the fix proven.

1. **`run-ui.sh` cannot start.** It passes `--port "${UI_PORT:-0}"`. `parseArguments()` rejects any port below 1 → proxy prints `FAIL` and exits. Default must be a real port (e.g. `8080`) or `UI_PORT` must be required like `TARGET_URL`.
2. **`run-ui.sh` never sets the customer header, and the proxy 400s every HTML request without it.** `customerIdFromRequest()` throws inside `proxyRequest` → `FAIL: Trusted customer header ...`. Part A4 fixes the HTML path; confirm non-HTML assets were never affected.
3. **Installer output does not match the proxy's expected folder layout.** Installer writes `.ui-capability/skin.css`. Proxy reads `ui-bridge/skins/<appId>/<layout>.css` and `ui-bridge/layouts/<customerId>/<appId>.json`. Neither is written by the installer, so `readLayout()` always returns `null` and the stylesheet is never injected. Either the installer writes those paths or the proxy is pointed at `.ui-capability/`. Pick one, document it, prove a stylesheet actually injects.
4. **`pick-app.js` is missing** but `ui-bridge/package.json` declares `"pick"` as a script. Add the file or remove the script.
5. **Three mapped categories do not exist** in `skins_library/`: `devops_console`, `dashboard`, `developer_tools`. Apps mapped to them (Docker Moby, Traefik, Grafana, Umami, Supabase, Coolify, frp, Unleash, Codex) fall through to alias/token matching. Either add the categories or fix `APP_MAP`.
6. **`todo_list` has no `_color.png` evidence** in `_verification_evidence/` while every other category does.

---

## 6. Acceptance tests — what PASS means

All tests are real. A test that never started a process is not a test.

**T1 — Hook serves.** Start any real HTTP upstream serving a page with a `<head>` (a Python `http.server` on a real HTML file is fine — it is a real server, not a mock). Start `proxy.js` against it. `curl` the proxy. Assert body contains exactly one `<script src="/_cp/port.js" data-capability-hook="1" defer></script>` and it precedes `</head>`. Assert `Content-Length` matches actual body length.

**T2 — Port serves.** `curl <proxy>/_cp/port.js`. Assert `200`, JS content type, SHA-256 equals `capability-port/port.js`.

**T3 — Dormant.** Load the proxied page in real Chromium (Playwright or the same harness `test_live.py` uses). Assert: script loaded (network 200), zero console errors, zero capability attachments, no DOM mutation beyond the tag itself. Then, in the same page, call the port manually from the console once and confirm it responds — proving it is loaded and idle, not absent.

**T4 — Gzip path.** Upstream serving gzip. Assert hook is injected and the response decodes cleanly.

**T5 — No `</head>`.** Upstream page without `</head>`. Assert body passes through unchanged and the watcher reports `MISSING_TAG` (not a crash).

**T6 — Stylesheet still works.** With defect 3 fixed and a real layout file present, assert both `<link>` and `<script>` are injected, stylesheet first.

**T7 — Watcher PASS.** Run `watch.py` against T1's proxy. Assert stdout ends `PASS`, exit `0`, one `HOOKED` log line.

**T8 — Watcher catches removal.** Stop the proxy, point `TARGETS` straight at the upstream. Assert `MISSING_TAG`, exit `1`.

**T9 — Watcher catches stale proxy.** Copy the original unpatched `proxy.js` over the patched one in a `.ui-capability/` dir, run `--repair`. Assert `PROXY_STALE` and the printed `cp` command is correct. Do not auto-run it.

**T10 — Full install.** Run `python3 install_all.py` against a real library dir with at least one real app. Run its `run-ui.sh` with `TARGET_URL` pointing at the real running app. Run `watch.py`. Assert `PASS`.

**T11 — Existing suites still green.** `python3 capability-port/test_live.py` (62), `test_inspector.py` (27), `test_hardening.py` if present. Unchanged, all passing.

Deliver test scripts and captured real output (stdout + exit codes) alongside the code.

---

## 7. Out of scope and open rulings

**Out of scope for this handoff**
- Browser-extension injector (separate piece, not started).
- Any change to `port.js`.
- Coolify / AWS deployment of the proxy.
- The Layout arrangement conversation interface.

**Rulings the owner still holds**
- Cover-before-swap on the dormant in-page hook: whether the watcher's repair should hold the old page live while the proxy restarts, or accept a brief gap. Not decided — build repair as a plain restart and flag it.

**Take / push-back**
- Package returned with the six defects in §5. Fix them; do not paper over defect 3 by claiming the stylesheet injects when the folders don't line up.
