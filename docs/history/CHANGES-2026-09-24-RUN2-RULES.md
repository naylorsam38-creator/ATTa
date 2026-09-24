# Run-2 script rules (2026-09-24)

Run 2's first 16 failures, each turned into a rule for the class of failure it is, never for the app.
v109 as shipped (`ATTa-deploy109_2.zip`) contained **none** of the five updates described for it.
Each one was first reproduced against v109 and is now in the scripts, with a test that fails on v109
and passes now (`tests/`: 49 tests, including 3 through real Docker and 8 through real Chromium).

| Failure seen | Class | v109 as shipped | Now |
|---|---|---|---|
| billionmail, tidb: runner crashed (`BadStatusLine`) | a port answers but isn't a website | `http_probe` caught only `URLError/OSError/ValueError`, so a mail/database greeting raised `http.client.BadStatusLine` out of `_wait_http` and the app ended `RUNNER_EXCEPTION` (reproduced with real Docker: `BadStatusLine: 220 mail ESMTP`) | any non-HTTP answer is "no web answer here" (`NOT_HTTP`); the runner keeps looking for the web port and, if none appears, says which ports answered in another protocol |
| krayin-crm, flarum: "No space left on device" | disk full during a build | rule was lower-case only, so BuildKit's `failed to solve ... No space left on device` matched `build.failed` and the part was dropped | `disk.full` matches any case plus `ENOSPC`, apt's and quota wording; it clears Docker's build cache and images and retries the **same** part |
| graphite: download broke off mid-build | network hiccup during a download | no rule: `unrecognised` or `build.failed`, so the next part; a registry `i/o timeout` was even filed as `image.missing` | `net.transient`: pause `APP_BUILDER_NET_RETRY_WAIT` (30 s), retry the same part once. Matched only against download/build output (`phase: start`), never the running app's own logs |
| colanode: page blank at check time | pages drawn by their own JavaScript are blank at "load" | `EMPTY_BODY` checked right after load + network idle | the browser waits up to `APP_BUILDER_CONTENT_WAIT` (15 s) for visible text or a real-sized canvas; a spinner alone doesn't count; an empty `<body>` still fails (spec T13). The no-proxy baseline gets the same wait, so errors thrown while drawing compare fairly |
| plane: skin link missing in the browser, present on the server | not proven yet | failure detail was just `"0"` | `BROWSER_SKIN_LINK_COUNT` / `BROWSER_HOOK_COUNT` now record: requested and final address, every main-frame navigation, the page served at the final address (did it contain the skin link and the hook, status, content type and encoding), the live `<head>`, and any skin link or hook the page's own script removed (with when). `observed` summarises: `SERVED_WITHOUT_SKIN`, `REMOVED_BY_PAGE_SCRIPT`, `NAVIGATED_TO:<path>` or `UNEXPLAINED` |

## Found while fixing these (same code paths)

- **Fix budgets were shared.** v109 counted every failure on a part against every fix (`fixes_here`), so
  a variable filled in first used up the disk-full retry (and `new_ports` only worked on the first two
  failures). Each fix now has its own budget per part (`FIX_BUDGET`).
- **Short-form compose ports were dropped.** `ports: ["3000"]` publishes on a random host port in
  compose; v109 treated it as internal and skipped it, so such an app looked like it "publishes no port".
  Every `ports:` entry now gets a host port (`expose:` stays internal, as compose defines it).
- **evidence.jsonl was write-only.** Its docstring pointed at a `tests/run_tests.py` that wasn't in the
  bundle. Now `app_runner.py replay [file]` re-diagnoses every recorded failed start under the current
  rules (what changed, and what nothing recognises yet, grouped by line), and browser/stage failures of
  started apps are written to the same file (`"kind": "check"`), so plane's evidence comes back with it.
- **The reusable runtimes' own downloads now retry in place:** apt `Acquire::Retries`, cargo
  `CARGO_NET_RETRY`, npm fetch retries, and the binaryen release is downloaded with `curl --retry` to a
  file before `tar` (a retried download piped into `tar` could corrupt the stream).
- `add_runner_rule` (the LLM tier) can teach `retry_net` rules; they are always `phase: start`.

## Not provable from the bundle
- "grafana and frp now pass", "container builds can download on the test machine": run results, not
  code. The frp path (an app answering non-HTML is checked as a service) is in v109's code.
- plane's actual cause: the evidence above decides it on the next run; no rule was guessed.

## Settings added (defaults in code; first-install `.env` lists them)
`APP_BUILDER_CONTENT_WAIT=15`, `APP_BUILDER_NET_RETRY_WAIT=30`.
