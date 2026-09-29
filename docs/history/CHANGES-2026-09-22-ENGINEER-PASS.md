# Engineer pass — 2026-09-22 — findings and fixes

Scope covered this pass: 01-specs (read as governing spec), 02-front-door/front-door.html
(the Skins IIFE verified line-for-line against skins_library/design_tokens.py +
render_css.py — no bug found, port is byte-faithful), 03-ui-skins-capability-package
(capability-port/port.js read in full; ui-bridge/proxy.js read in full and cross-checked
against system_watcher.py's HOOK_MARKER/PORT_ROUTE/SKIN_PREFIX constants and regexes — no
bug found there), 04-deployment (system_watcher.py, pipeline.py, gateway.py, bootstrap.sh,
app-builder-nginx.conf). All .py/.sh/.js files in the bundle pass `py_compile` / `bash -n` /
`node --check`.

**Not yet reviewed**, so treat as open: `04-deployment/build.py` (112KB, offline
candidate-builder, not part of the runtime path per the README so lower priority),
`install_all.py`, `hook-watcher/`, `capability-port/caps/*.mjs`, `capability-port/port.js`
internals beyond the attributes it writes, the front-door.html chat/matching JS beyond the
Skins engine (~600 lines), and the 46×4 generated skin CSS/JSON assets. Say the word and
I'll keep going into any of those.

## Fixed

### 1. `system_watcher.py` — dormant-port check used attribute names port.js never writes (critical)

Revision-1 spec §1.1 explicitly said to grep `capability-port/port.js` for every
`data-capability-*` attribute it writes and list them in a comment. The delivered file's
"owned" filter checked for `data-capability-port` and `data-capability-instance` — neither
of which appears anywhere in `port.js` or its `caps/*.mjs` modules. What the port actually
writes at runtime is `data-capability` (on the mounted capability's `<section>`, via
`slot.dataset.capability`) and `data-capability-ui` (on injected capability UI overlays, in
`caps/inspector.mjs`). Net effect: a page with an actively-mounted capability — i.e. one
that is *not* dormant — would report `owned: 0` and pass stage 6 CLEAN as if dormant. Fixed
the filter to check the attributes port.js actually writes, with the grep results recorded
in a comment as the spec asked.

### 2. `system_watcher.py` — TIMEOUT misreported as UNREACHABLE on one code path

`get()`'s `except (socket.timeout, TimeoutError)` branch returned `{}` for headers, while
the `URLError` branch (which can also carry a wrapped timeout) set
`{'x-watcher-error': 'TIMEOUT'}`. Callers key off `ah.get('x-watcher-error') == 'TIMEOUT'` to
tell timeout apart from a plain refused/unreachable connection, so a direct (non-wrapped)
socket timeout — which `urllib` raises for read timeouts after the connection is already
open — was reported as `UNREACHABLE` instead of `TIMEOUT`. Fixed to set the same header.

### 3. `system_watcher.py` — stage 3 redirect check compared full path, not origin

Revision-1 spec §2.4: "confirm the final URL is still on the proxy *origin*." The delivered
check compared the entire final URL string (path included) against the proxy URL, so a
same-origin redirect to a different path (e.g. `/` → `/index.html`) would have been wrongly
reported as `REDIRECT_OFF_PROXY`. Fixed stage 3 to compare scheme+host+port only, matching
what the browser-stage check (`browser_check()`) already did correctly. Left the stage 5
HOOK route check as exact-path (fetching a specific named resource, not the site root, so
exact-path is the correct check there — flagging only in case that's worth a second look).

### 4. `gateway.py` — multipart parser could write pre-boundary bytes to the saved file

In `multipart_upload()`, the "haven't found the boundary yet" branch wrote whatever was
buffered straight to the output file, unconditionally — including before the *first*
boundary is ever found, i.e. true RFC 2046 preamble (which must be discarded, never
written), or a boundary line split across two socket reads (plausible on a real TCP
connection, not just a contrived case). A hit there would prepend garbage to the saved
upload; `zipfile.ZipFile` would then reject it as invalid, so the practical effect is a
false upload rejection rather than corrupted data reaching the pipeline. Fixed to discard
instead of write, consistent with how the "preamble after `start>0`" branch already treats it.

### 5. `bootstrap.sh` — `build.py` was copied into the runtime path

`04-deployment/README`'s "Runtime boundary notes" state build.py "is not part of the AWS
upload pipeline and is not copied into the runtime package," but `bootstrap.sh` did
`cp -a "$(dirname "$0")/." "$APP/"`, which copies everything in `04-deployment/` —
build.py included — into `/opt/app-builder`. Added `rm -f "$APP/build.py"` right after the
copy so the shipped behavior matches the documented boundary.

## Flagged, not changed (judgment calls / infra gaps, not code bugs)

- **`app-builder-nginx.conf` only has a `listen 80` block; `bootstrap.sh` never provisions
  TLS.** The admin login form and session cookie both go out over plain HTTP by default.
  `bootstrap.sh`'s final line does warn "put nginx behind HTTPS before exposing it
  publicly," but it's an easy-to-miss echo at the end of a long install, not something
  automated (e.g. certbot). Worth automating before calling this "ready for live AWS
  validation" with a real domain.
- **`gateway.py` `/login`'s `Content-Length` parse isn't wrapped in try/except** (the
  `/upload` handler's is). A malformed header would raise unhandled inside `do_POST`. Low
  severity, easy one-line fix if you want it (`try: n=int(...) except ValueError: n=0`,
  matching the `/upload` pattern), left alone this pass since it's a judgment call whether
  to bundle it in.
- **`pipeline.py`'s `process()` wipes the staging `PACKAGE` dir before validating the new
  one.** Confirmed `PKG` is transient/staging-only (nothing else reads from it — the live
  watcher and proxy never touch it), so a failed upload doesn't affect anything serving
  traffic. It does mean a failed run leaves `package/` in a half-extracted state until the
  next upload, which could confuse anyone inspecting it by hand. Not fixed since low risk.

## Verification

`python3 -m py_compile` on every `.py`, `bash -n` on every `.sh`, `node --check` on every
`.js` in the bundle — all pass after the above edits.
