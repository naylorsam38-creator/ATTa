# ATTa v117 — Validation of the adaptive deployment & Recovery Spine

**System under test:** ATTa `v117` (release zip built by `tools/make_release.py` from
`claude/atta-hardening-release-c5n5tw` @ `23c1835`, manifest `d7fa6cb0…`), **unmodified**.
**Deployment engine:** Coolify `4.3.23` installed by ATTa's own `05-coolify/install-coolify.sh` from the supplied
`coolify-main.zip` (checksum-verified by that script).
**Date:** 2026-09-25. **Method:** observe, don't intervene — every deviation from that is listed in §2.4.

<!-- VERDICT -->

---

## 1. Verdict

<!-- filled in at the end -->

---

## 2. What was tested, and how

### 2.1 Topology (real systemd servers, real Docker, real Coolify)

```
 testbed network 172.30.0.0/24 (air-gapped: no general internet from any host below)
 ├── atta-server   172.30.0.10  Ubuntu 24.04, systemd PID 1, own dockerd — ATTa SERVER mode (`bash run` → bootstrap.sh,
 │                              atta-deployd, nginx, gateway/pipeline/watcher units)                     = production path
 ├── atta-laptop   172.30.0.12  Ubuntu 24.04, own dockerd — ATTa LAPTOP mode (`bash run local` as non-root user `sam`)
 ├── coolify-server 172.30.0.20 Ubuntu 24.04, systemd, own dockerd — Coolify 4.3.23 (ATTa's installer), API on :8000
 ├── gitsrv        172.30.0.8   git.testbed.internal — smart-HTTP git (the apps' source for Coolify, like GitHub)
 ├── localreg      172.30.0.6   Docker registry mirror (seeded with exactly the images the apps/Coolify reference)
 ├── pwmirror      172.30.0.5   Chromium-for-Testing 148 (Playwright 1.60 rev 1223) + Python wheel index (pinned reqs)
 └── localcdn      172.30.0.7   cdn.coollabs.io mirror (TLS, local CA) serving the files of the supplied Coolify source
```

Each "server" is a privileged `ubuntu:24.04` container with **systemd as PID 1** and its own Docker daemon
(`testbed/host-image/Dockerfile`, `testbed/start-host.sh`) — the same OS/version ATTa's `run` and `bootstrap.sh`
support (`ubuntu-24.04`). ATTa and Coolify are on **separate servers**, as `COOLIFY-HANDOFF.md` recommends, talking
over a private address (`COOLIFY_URL=http://172.30.0.20:8000`, plain HTTP allowed to private addresses by v116 rules).

### 2.2 Why two ATTa instances

The production path (server mode) was installed first and exercised first. It **stopped every app build at
the skin-install step** (Finding F2) — a defect no built-in mechanism can repair. To still exercise everything
*after* that step (app runner, six-stage watcher, repair tiers, rule learning, Coolify hand-off) the **same
unmodified release** was run in its other officially supported mode, `bash run local` (the "laptop instance",
v117 checklist H/I/J), by an ordinary user. Server mode was kept for deployd/ADM, which only exists there.

### 2.3 Application corpus (`apps/`) — every app pre-validated before it went near ATTa

Each app was first built with networking disabled, started and probed on the session host
(`testbed/preflight.sh`), so that "good" apps are known-good and each broken app fails **only** for its intended reason.

| App | Type | Intended behaviour | Preflight |
|---|---|---|---|
| `static-landing` | static site (nginx) | good | build OK, HTTP 200 |
| `static-nodocker` | static site, **no Dockerfile/compose** | edge case: no recipe | — |
| `vite-react-dashboard` | React/Vite SPA, multi-stage build | good | build OK, HTTP 200 |
| `next-notes` | Next.js 14 standalone | good | build OK, HTTP 200 |
| `node-api` | Node.js service | good | build OK, HTTP 200 |
| `fastapi-service` | Python/FastAPI | good | build OK, HTTP 200 |
| `compose-stack` | Docker Compose: web + Redis | good | `compose config` OK |
| `env-guestbook` | Compose app that **requires** `GUESTBOOK_SECRET` (`${VAR:?}`), `.env.example` present | env var missing | fails config without var, OK with it |
| `env-token-api` | Dockerfile app that exits when `API_TOKEN` is unset, no example file | env var missing | exits: `API_TOKEN must be set` |
| `wrong-port` | `EXPOSE 8080`, listens on 5055 | incorrect port | runs, nothing on 8080 |
| `bad-compose` | invalid `docker-compose.yml` + valid Dockerfile | invalid compose | YAML parse error; Dockerfile OK |
| `health-fail` | every page HTTP 500 | health-check failure | HTTP 500 |
| `crash-loop` | serves, exits(1) after 20 s | restart scenario | runs, then exits |
| `slow-start` | listens after 75 s | slow boot | — |
| `dep-broken` | package.json/lock out of sync | dependency issue | `npm ci` fails |
| `open-notebook-main.zip` | **your upload**, unchanged — Compose (SurrealDB + app image) | real-world app | — |
| `app-factory-controller.zip` | **your upload**, unchanged — Python library (no server) | real-world non-web | — |

The testbed is air-gapped (§2.4), so the good apps carry their dependencies (an offline npm cache or a wheel folder
— the standard reproducible-build pattern; `apps/vendor-deps.sh` regenerates them). Their Dockerfiles install with
`--offline`/`--no-index`, which also makes each app's failure reason deterministic.

### 2.4 Environment adaptations (none touch ATTa's or Coolify's code)

The session sandbox gives containers **no general internet egress** (TLS to the internet is re-terminated by a
gateway whose CA is not available to containers; routing containers through the session's own proxy was refused by
the sandbox as a containment boundary, and was not pursued). Everything was therefore run **air-gapped**, the way a
locked-down enterprise site runs, with local mirrors seeded from the session host:

| Need | How it was met | Touches ATTa/Coolify code? |
|---|---|---|
| Docker images | local registry configured as the hosts' `registry-mirrors` (ATTa's bootstrap keeps an existing `daemon.json`) | no |
| ATTa's pinned Python packages | wheel index; `/etc/pip.conf` `find-links` | no |
| Playwright Chromium rev 1223 | Chrome-for-Testing 148.0.7778.96 served at Playwright's own path layout; `PLAYWRIGHT_DOWNLOAD_HOST` | no |
| Coolify's CDN files (`install.sh` fetches from `cdn.coollabs.io`) | local TLS mirror of the **supplied** source's files; hosts entry + local CA on the Coolify host | no |
| Coolify on a kernel without IPv6 | `NGINX_LISTEN_IP_PROTOCOL=ipv4` in Coolify's `.env` (a documented setting of its base image) | no (config) |
| Coolify root user | Coolify's `RootUserSeeder` needs internet DNS/HIBP; the same fields were set through Coolify's models (`testbed/coolify-owner-setup.php`) | no |
| Coolify "Server is not functional" | Coolify's own `validateConnection()`/`validateDockerEngine()` (what onboarding does) | no |
| Git source for Coolify resources | smart-HTTP git server (`git http-backend`) at `git.testbed.internal` | no |
| Coolify owner setup (`COOLIFY-HANDOFF.md` table) | API access on; a **read+deploy** token for ATTa; one resource per app **named as ATTa names it** | no — this *is* the documented contract |

Operator actions taken **on ATTa**, all documented options, each recorded where it happened:

1. **Seed list removed** (`upstream_apps.json`) — README: *"Delete it, and nothing breaks."* Taken only **after**
   Finding F1 was fully captured; the failed builds were left untouched.
2. **Tier-4 human response** to an escalated alert (F5): `playwright install-deps chromium`, exactly what the alert's
   evidence called for. ATTa's design has a human as tier 4; nothing else was done on its behalf.
3. `.env` Coolify settings + `coolify_resources.json` (2 of 7 apps mapped, the rest left for ATTa to find) — the
   documented hand-off setup, done after Phase A had shown the "not configured" behaviour.
4. ADM run once with `ATTA_ADM_RUN_TESTS=0` — ADM's documented emergency switch — only because Finding F6 makes the
   test gate refuse every real release, so the activation/rollback path is otherwise unreachable. Labelled below.

No ATTa file was edited to make anything pass; no failed build was requeued, repaired or deleted by hand.

<!-- RESULTS -->

---

## 4. Findings — where the Recovery Spine stopped, and who should have owned it

Severity: **S1** blocks all deployments · **S2** wrong/unsafe outcome or a chain that cannot complete · **S3** degraded
behaviour, cost or UX. Every finding was reproduced from ATTa's own records (`evidence/`), and the root causes marked
*reproduced* were re-created in isolation.

### F1 — S1 · One unreachable seed repo fails **every** build; tier 1 cannot recognise git's error (regex vs newline)
- **Detected by:** pipeline `FETCHING_LIBRARY` (`pipeline.library()` cloning `upstream_apps.json` seeds, e.g. `moby/moby`).
- **What happened:** two different users' uploads (`static-landing`, `node-api`) both ended `FAILED` in < 2 s, before
  their own app was touched. Chain: tier 1 `NOT_RECOGNISED` → tier 2 `NOT_MINE` → tier 3 `NO_FIX` (build layer has
  no probe list) → tier 4 `ALERTED`. Status `ESCALATED`; never retried (an hour later still `FAILED`).
- **Root cause (reproduced):** `pipeline.library()` deliberately *re-raises* transient git errors "so the known fix
  retries them" (`pipeline.py:213,245` — `TRANSIENT_GIT` matches `unable to access`, `TLS`, `SSL`). The known fix
  `build.git_network` (`known_fixes.py:42-43`) and the failure family `GIT_NETWORK` (`failure_keys.py:46`) match
  `\['git'.*(…|unable to access)` — but git prints `Cloning into '…'...\nfatal: unable to access …`, and `.` does not
  cross the newline. Against the recorded error: `TRANSIENT_GIT` → **True**, `build.git_network` → **False**
  (True with `re.S`), `failure_keys.normalise` → `UNKNOWN_FAILURE` (so it can never be learned either).
- **Chain stopped at:** tier 1. **Should have owned it:** tier 1 `build.git_network` (retry), or — better —
  `library()` itself, whose docstring promises *"One unavailable repo no longer fails the whole build: it is
  recorded and skipped"*. A certificate failure is also not transient; retrying it cannot succeed.
- **Recommendation:** add `re.S` (or search only the `fatal:` line) in both patterns; treat TLS/404/auth as
  `REPO_UNAVAILABLE` (skip, record, continue) and only re-raise genuinely transient errors. No test in `tests/`
  exercises `build.git_network` / `GIT_NETWORK` at all — add one fed with a *real* multi-line git error.
- **Also:** `bash run local` re-copies the seed list on every start, so "delete it" does not survive a restart unless
  `04-deployment/upstream_apps.json` itself is deleted (seen: laptop build `…d62fe5cd`).

### F2 — S1 · Server mode: every app build fails at `INSTALLING_UI_CAPABILITY` (the v116 sandbox forbids what the skin installer does)
- **Detected by:** pipeline, `install_all.py` exit 2: `[Errno 1] Operation not permitted: '…/.ui-capability.tmp-static-landing/capability-port/…'`.
- **Root cause (reproduced with `systemd-run`):** the data root is `root:atta 3771` (setgid, `bootstrap-lib.sh:107`),
  so every directory under it carries the setgid bit. The skin installer copies the overlay with
  `shutil.copytree(src, dst)` (`install_all.py:172` inside `UI_Skin_Capability_OneShot_v2.zip`), whose `copystat`
  re-applies the source mode — setgid included — with `chmod`. The pipeline unit runs with `RestrictSUIDSGID=yes`
  (`bootstrap.sh:237/278/319`), which makes exactly that `chmod` fail with `EPERM`. Same copy: fails with the
  property, succeeds without it.
- **Chain:** tier 1 `NOT_RECOGNISED` → tier 2 `NOT_MINE` (the capability adapter only handles *qualify*-layer overlay
  faults) → tier 3 `NO_FIX` → tier 4 `ALERTED`. **Nothing** in the spine owns build-layer installer failures except
  timeouts.
- **Impact:** on a fresh Ubuntu 24.04 server, v117 cannot qualify — and therefore cannot hand to Coolify — any app.
- **Why CI is green anyway:** `tests/staging/ec2_acceptance.sh:125-133` uploads an app, accepts `FAILED` as a normal
  terminal state, and then only checks for secret leakage; the "no skin proxy running" line is printed as a NOTE.
- **Recommendation:** copy overlays with `copy_function=shutil.copy`/`copy2` **and** `copystat` that masks
  `S_ISGID|S_ISUID` (or `dirs_exist_ok` + explicit `os.chmod(d, mode & 0o1777)`), or drop setgid from the data
  root's subtree; make the acceptance script *require* `QUALIFIED` for its known-good app.

### F3 — S3 · `bash run local` run as root can never start a skin proxy; the spine loops generic probes
- `proxy_launch._on_server()` is `euid == 0 or INVOCATION_ID` (`proxy_launch.py:50-51`), so a root laptop instance
  is treated as a server and requires the `atta-proxy` user that only `bootstrap.sh` creates. The error text is
  exact ("user atta-proxy does not exist; re-run `bash run`…"), but tier 1 `NOT_RECOGNISED`, tier 2 `NOT_MINE`, and
  tier 3 runs `reinstall_app_overlay` → `chmod_launcher` → `install_browser`, each followed by a full-catalogue
  re-qualification, then `ALERTED` (4 rounds). **Recommendation:** map `PROXY_NOT_STARTED` + "does not exist" to a
  human-only tier-1 rule on round 1 (no probe can create an OS user), and let `bash run local` refuse/explain root.

### F4 — S2 · A restart during healing orphans the heal forever (and leaks the app container)
- Laptop instance restarted at 14:13:38 while build `…cbc9ea56` was in qualify-heal round 1 (a probe had just
  re-qualified). Ten minutes later, and at the end of the run: `healing.qualify.status = "HEALING"`, round 1, chain
  unchanged; `state/status.json` still says `QUALIFYING`; **no alert**; the app container `atta-static-landing` from
  the killed qualification was still running 11 min later although `APP_BUILDER_KEEP_RUNNING=false`.
- **Why:** `maintenance.heal()` only runs from `pipeline` right after a build finishes (`on_failure`) and
  `maintenance.sweep()` — the only periodic owner — handles the *handoff* layer only (`maintenance.py:328-343`).
  Nothing reconciles a heal whose process died.
- **Recommendation:** on pipeline start, resume or escalate every build whose `healing.*.status == "HEALING"`, reset
  `status.json`, and `down()` containers labelled `atta.app` that no live qualification owns.

### F5 — S2 · Laptop browser self-repair is half a repair (browser without its OS libraries); `BROWSER_EXCEPTION` has no owner
- Stage 6 `PLAYWRIGHT_UNAVAILABLE` → tier 1 `install_browser` (correct rule). It `pip install`s **unpinned**
  `playwright` (bypassing `requirements-server.txt`, `repair_actions.py:385`) and runs `playwright install chromium`
  — but never `install-deps`. Next run the code becomes `6 CLEAN:BROWSER_EXCEPTION` (`libglib-2.0.so.0: cannot open
  shared object file`, 19 libraries missing), which **no tier-1 rule matches**; the probe walked all five qualify
  probes (the 5th, `install_browser`, "succeeded" again) and escalated after 6 rounds, each a full re-qualification.
- **Positive:** the alert carried the exact loader error, so the tier-4 human action was obvious; after
  `playwright install-deps chromium` the next build passed stage 6 in a real browser.
- **Recommendation:** make `install_browser` verify with a real launch (as `atta_install_browser` in bootstrap does)
  and report the missing-library case as a human item on round 1; pin the version it installs.

### F6 — S1 (for self-update) · deployd/ADM rejects every genuine v117 release: its own test gate fails on the manifest
- `sudo deployctl deploy ATTa-v117-23c1835dd8e2.zip` (the exact release that is live) → `FAILED (nothing live was
  touched)` after 3 m 50 s: `test_this_bundle_passes` and `test_run_must_start_with_its_shebang` fail with
  *"the bundle does not match its manifest: missing: 05-coolify/coolify-main.zip"*.
- **Root cause:** the tests' `bundle_zip()` deliberately leaves out "big binary zips" (`tests/test_v116_hardening.py:1067`),
  but since v117 ADM verifies every bundle against `MANIFEST.sha256` (`deployd/adm/staging.py:90,137`), which lists
  that file. In a source checkout there is no `MANIFEST.sha256`, so CI's unit job cannot see this; in every
  release zip there is. **Consequence:** ATTa cannot update itself through ADM (web upload or `deployctl`) at all;
  the only way through is the emergency switch `ATTA_ADM_RUN_TESTS=0`.
- **Recommendation:** have `bundle_zip()` regenerate the manifest for the zip it builds (or include the file), and
  add a CI step that runs the ADM test gate against the *built release zip*.

### F7 — S2 · Every build re-dispatches every qualified app in the library — across owners, for unchanged code
- `_record_qualification()` hands off *all* apps that passed the full-catalogue run (`pipeline.py:106-114`); the
  hand-off has no owner filter and no "already deployed this commit" check. Coolify ended Phase B with
  **node-api ×4, static-landing ×4, vite-react-dashboard ×4** deployments of identical commits; `tester03`'s FastAPI
  build dispatched `tester01`'s and `tester02`'s apps (manifest `owner` = tester03).
- **Recommendation:** dispatch only the build's own apps (or apps whose source hash changed since their last
  `ACCEPTED` dispatch), and record the owner per app in the manifest.

### F8 — S2 · A repeated deployment with new code is silently ignored — the old version is qualified and shipped
- `tester01` re-uploaded `static-landing` with a changed page (`<h1>Static Landing v2</h1>`). Build `…f5dde487`:
  `apps_already_in_library: ["static-landing"]`, state **`QUALIFIED`**, "recipe that worked before"; the library copy
  still says `Static Landing` and its git log has only the first intake commit. The v1 code was then handed to
  Coolify under the v2 build. When a *different* user uploads an app with an existing name (`tester02`, build
  `…0b82aed1`), the upload is dropped entirely and their build reports `QUALIFIED` for their other apps, with no
  mention of the upload (ownership filter hides it).
- **Why:** `ingest_upload()` — "the library copy is authoritative (repairs live there)" (`pipeline.py:295-304`).
- **Recommendation:** treat an owner's re-upload as a new revision (snapshot commit on the library repo, keep repairs
  as a patch on top), and make the build `REFUSED`/`NOT_QUALIFIED` with a clear reason when an upload is ignored.
