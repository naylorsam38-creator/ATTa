# System Watcher — Revision 1 (fix list)

Date: 2026-09-22
Owner: Sam
Applies to: `system_watcher.py` as delivered. Governing spec: `System-Watcher-Build-Handoff.md`.

Deliver the complete revised file plus captured real stdout and exit codes for every test in §3. Nothing is accepted until every item below is done and every test has real output attached.

---

## 1. Bugs — fix exactly as written

### 1.1 Stage 6 `PORT_NOT_DORMANT` fires on a correct page

**Cause.** The `page.evaluate` dormant check flags any element whose attributes match `/capability|ui-capability|data-capability/`. The hook tag itself is `<script src="/_cp/port.js" data-capability-hook="1" defer>`, so a correctly hooked page always fails stage 6.

**Fix.** Replace the whole dormant check with this:

```js
() => {
  const all = [...document.querySelectorAll('*')];
  // Port anchors are `data-capability-anchor` (per capability-port/README.md). Nothing else counts.
  const anchors = all.filter(e => e.hasAttribute('data-capability-anchor'));
  // Port-owned nodes: anything the port mounts. Exclude the hook <script> itself.
  const owned = all.filter(e =>
    e.tagName !== 'SCRIPT' &&
    (e.hasAttribute('data-capability-port') || e.hasAttribute('data-capability-instance'))
  );
  return { anchors: anchors.length, owned: owned.length };
}
```

Fail with `PORT_NOT_DORMANT:<anchors>/<owned>` when either count is non-zero. Before shipping, grep `capability-port/port.js` for every `data-capability-*` attribute it writes and include each one in the `owned` filter. List them in a comment above the check.

### 1.2 `PROXY_STALE` handled in the wrong place

**Cause.** `validate_config()` rejects a proxy without `HOOK_MARKER` as a config error (exit 2). Spec §3 stage 1 and test T5 require `INSTALLED FAIL PROXY_STALE`, exit 1.

**Fix.** Delete the `proxy_path` block from `validate_config()` entirely. `installed_check()` already does it correctly.

### 1.3 `EMPTY_BODY` can never fire

**Cause.** `if not inner_text.strip() and not body.count()` — `count()` is ≥1 whenever a body exists, so the `and` is never true.

**Fix.**
```python
body = page.locator("body")
if body.count() == 0 or not body.inner_text().strip():
    browser.close()
    raise CheckError("EMPTY_BODY")
```

### 1.4 `BROWSER_CHECK = False` forces `FAIL`

**Cause.** Verdict becomes `NOT_RUN — CLEAN browser stage disabled`, and `run_once` counts anything not equal to `"PASS"` as failed.

**Fix.** When stages 1–5 are all `OK` and stage 6 is `NOT_RUN`, verdict is `PASS (CLEAN not run)`. In `run_once`, failed = `broken_at is not None`, not a string compare on the verdict. Stage 6 line prints `NOT_RUN` (add that branch to `stage_line`; today it would crash on a missing `code` key).

### 1.5 `stage_line` crashes on `NOT_RUN`

Same root as 1.4 — `stage_line` only handles `OK`, `SKIPPED`, and fail. Add `NOT_RUN`.

### 1.6 Dormant check reads `outerHTML.slice(0, 500)` of every element

Remove. Attribute checks only (covered by 1.1). Regex over serialised HTML of every node is slow on real apps and matched the hook tag.

---

## 2. Spec compliance

### 2.1 Config block comments

Every setting gets exactly the comment from the governing spec §2 — what it does **and what changes if altered**. Restore these verbatim:

```python
# Files the installer must have written inside ui_dir. Missing any = INSTALLED fails. Add to this if the installer grows.
# Marker the proxy stamps on the hook script tag. Must match HOOK_MARKER in proxy.js or HOOK always fails.
# Route the proxy serves port.js from. Must match PORT_ROUTE in proxy.js.
# Route prefix the proxy serves skins from. Must match proxy.js.
# Customer header and id the proxy expects. Blank if the proxy injects without one.
# Seconds to wait for any single request before calling it down.
# Seconds between sweeps when run with --loop. Lower = faster detection, more load.
# Run the real-browser CLEAN stage (needs Playwright + Chromium installed). False marks stage 6 NOT_RUN; this is test-only and is not the production default.
# Where results go. One JSON line per app per sweep.
```

### 2.2 Loop mode exit

`--loop` currently never prints a summary line and swallows `KeyboardInterrupt` to exit 0 regardless of state. On Ctrl-C, print `PASS`/`FAIL` for the last sweep and exit 0/1 accordingly.

### 2.3 Timeout detection

`request()` detects timeouts by string-matching `"timed out"`. Catch `socket.timeout` / `TimeoutError` explicitly (both may arrive wrapped in `URLError.reason`) and fall back to the string match only after that.

### 2.4 Redirects

Spec: follow redirects on the proxy fetch. `urllib` does by default — confirm the final URL is still on the proxy origin; if it redirected off-proxy, fail `PROXY_UP` with `REDIRECT_OFF_PROXY:<url>`.

---

## 3. Testing — run these, attach output

"Cannot test from this environment" is rejected. Every test below runs with what you already have:

- **Upstream app:** `python3 -m http.server 5230` serving a real `index.html` with a `<head>`. This is a real HTTP server, not a mock.
- **ui_dir:** run `python3 install_all.py` against a folder containing one app dir with a `package.json`. That produces a real `.ui-capability/`.
- **Proxy:** the patched `proxy.js` from `Dormant-Hook-and-Watcher-Build-Handoff.md` §3, started via that app's `run-ui.sh` with `TARGET_URL=http://127.0.0.1:5230/`. If the proxy patch is not yet built, build it first — this watcher cannot be signed off without it.

Attach, for each test: the exact command, full stdout, exit code (`echo $?`).

| # | Setup | Expected |
|---|---|---|
| T1 | All up, proxy patched, layout file present | Six `OK`, `VERDICT: PASS`, `PASS`, exit 0 |
| T2 | Kill http.server | `2 APP_UP FAIL REFUSED`, 3–6 `SKIPPED`, exit 1 |
| T3 | App up, kill proxy | `3 PROXY_UP FAIL REFUSED`, exit 1 |
| T4 | Proxy started with `TARGET_URL=http://127.0.0.1:1/` | `3 PROXY_UP FAIL UPSTREAM_502`, exit 1 |
| T5 | Copy unpatched `proxy.js` over `ui-bridge/proxy.js` | `1 INSTALLED FAIL PROXY_STALE`, exit **1** (not 2) |
| T6 | Append one byte to `skin.css` source the proxy serves, leave `ui_dir/skin.css` alone | `4 SKIN FAIL CSS_MISMATCH` |
| T7 | Set `proxy_url` = `app_url` (bypass proxy) | `4 SKIN FAIL NO_LINK` |
| T8 | Delete `ui_dir/skin.json` | `1 INSTALLED FAIL FILE_MISSING:skin.json` |
| T9 | `--loop`, `INTERVAL_SECONDS=5`, kill app, restart app | First sweep printed in full; then exactly two change blocks (down, up); nothing else |
| T10 | `BROWSER_CHECK=True`, all up | `6 CLEAN OK`, `PASS`, exit 0 — this proves 1.1 |
| T11 | `BROWSER_CHECK=True`, upstream page contains `<script>console.error('x')</script>` | `6 CLEAN FAIL CONSOLE_ERRORS:1` |
| T12 | `BROWSER_CHECK=False`, all up | `6 CLEAN NOT_RUN`, `VERDICT: PASS (CLEAN not run)`, exit 0 — this proves 1.4 |
| T13 | `BROWSER_CHECK=True`, upstream `<body></body>` | `6 CLEAN FAIL EMPTY_BODY` — this proves 1.3 |

T1, T10, T12 are the acceptance gate. If any of the three is not attached with real output, the delivery is returned again.

---

## 4. Unchanged

Everything not listed above stays as delivered. Do not refactor, rename, or reorganise.
