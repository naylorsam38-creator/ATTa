# Offline / air-gapped deployment

## 1. Build a bundle on a connected machine (same OS, architecture and Python X.Y as the target)

```
python -m backend.deployment bundle create --out /media/usb/atta-offline
python -m backend.deployment bundle verify --bundle /media/usb/atta-offline
```

Contents (`bundle coverage --bundle DIR` prints what was included and why anything was not):

| Path | Component | Status on this build machine |
|---|---|---|
| `wheels/` | every package of **both** Python locks (`backend-core` 48 packages, `backend-full` 106 — all extras), `--require-hashes --only-binary=:all:` | INCLUDED |
| `python/cpython-*` + `tools/uv` | relocatable CPython of the same X.Y (python-build-standalone via uv) + uv, so the target needs no Python | INCLUDED (3.13.14) |
| `npm-cache/` | npm cache for the root workspaces lock **and** `apps/mobile/package-lock.json`; every lock entry needed on this platform is checked by integrity hash (other-platform optional packages are skipped and counted) | INCLUDED (101 + 466 entries, 0 missing) |
| `browsers/` | Playwright Chromium builds (end-to-end tests, browser extra) | INCLUDED |
| `system/debs/` + `Packages` | Debian/Ubuntu `.deb` closure of PostgreSQL 16, pgvector, Redis, git computed by apt's own resolver against an empty package database (base-system packages excluded), with a local apt repository index | INCLUDED (33 packages) |
| `images/` | `docker save` of the pinned compose images with resolved digests | **BLOCKED** — no Docker daemon here |
| `assets/` | migrations, bootstrap SQL, every lock and manifest file | INCLUDED |
| `bundle_manifest.json` | SHA-256 + size of every file, lock hashes, platform, coverage | — |

**Windows target** (`bundle create --target windows`, buildable on this Linux machine): Windows `cp313-win_amd64`
wheels for both locks (markers evaluated for Windows; 49 + 107 entries, every one checked for a wheel), the Windows
CPython 3.13 runtime, npm caches resolved for win32-x64 (95 + 466 entries, 0 missing) and PostgreSQL 16 x64 binaries
(`@embedded-postgres/windows-x64`, integrity-checked by npm; `INSTALL.bat --offline-bundle DIR --install-system`
unpacks them and the qualification runner finds them). **Not obtainable from this build machine — BLOCKED in the
coverage until supplied:** pgvector for Windows (no upstream binaries; build with MSVC or use WSL/Docker), Redis or
Memurai for Windows, Git for Windows (only needed for build-controller gates), and the Windows Playwright Chromium
(CDN blocked). Supply them with `--windows-installers DIR/<pgvector|redis|git>/…`; they are hashed into the manifest.

Never included: private keys (verification fails if a file holds private-key material; certificate trust stores are
allowed), credentials, models (only with an explicit `--models PATH…`). A bundle targets the same OS, architecture and
Python X.Y as the machine that built it; build one per target platform. On Windows the system installers (PostgreSQL,
pgvector, Memurai/Redis) are operator-provided (`OPERATOR_PROVIDED` in the coverage).

`verify` reports: lock mismatch, missing or modified files, unlisted files, private-key material.

## 2. Install on the offline machine

```
./install.sh --offline-bundle /media/usb/atta-offline --profile AIR_GAPPED      # then --approve
INSTALL.bat --offline-bundle E:\atta-offline --profile AIR_GAPPED
```

pip runs with `--no-index --find-links bundle/wheels --require-hashes`; npm with `--offline` against a private copy
of the bundled cache (the bundle itself is never modified — verified after every offline install). Without a suitable
host Python the launchers copy the bundled CPython out of the bundle and use it. `--install-system` installs PostgreSQL,
pgvector and Redis from the bundle used as a local apt repository (`bundle install-system`, administrator). On this
build machine those packages were already installed, so the real installation could only be shown as a re-install
simulation that apt satisfied from the bundle alone (`install-system --simulate`, inside the network namespace).
`python -m backend.deployment bundle install-offline [--full] --bundle DIR --venv DIR` does the Python/Node part into a
fresh environment and import-checks it. Verified on this machine **inside a kernel network namespace with no network at all**
(AIR_GAPPED qualification `Q-OFFLINE-INSTALL`).

## 3. Zero hidden Internet

`python -m backend.deployment audit-offline` fails the core if the web/desktop sources reference external CDNs,
remote fonts/scripts/CSS, if Next.js telemetry is not disabled by the RUN launchers, or if model downloads are allowed.
Features that inherently need the Internet are listed as `OFFLINE_UNAVAILABLE` (e.g. an enabled remote provider endpoint,
remote object storage, an external network probe); they are never silent fallbacks.

During qualification every Python process loads an audit hook (`backend/deployment/netaudit/sitecustomize.py`) that
records each non-loopback `connect`/`getaddrinfo` in `network_audit.jsonl`; inside the AIR_GAPPED namespace such an
attempt also fails at the kernel. Limitation: the hook sees Python processes only — non-Python processes (node,
redis-server, postgres) are constrained by the namespace, not recorded (BLOCKERS B-S01B-04).

## 4. Qualifying an air-gapped installation

```
python -m backend.deployment qualify --suite AIR_GAPPED --bundle /media/usb/atta-offline
```

The runner re-executes itself in a new network namespace (`unshare -n`, loopback brought up, no other interface),
verifies that no external address is reachable (`Q-NETWORK-DENIED`), then runs the offline-capable suites, the
frontend end-to-end test and the offline installation. Where no OS network isolation exists (Windows, macOS,
unprivileged Linux) every AIR_GAPPED test is **BLOCKED** — an air-gapped PASS is never claimed on a machine that
could still reach the Internet.

## 5. Bundle inventory (measured on the delivered bundles)

Primary application package: ~26 MB — source, installers, configuration templates, migrations, tests, reports and
evidence only. **No offline bundle is needed for online installation** (`install.sh` / `INSTALL.bat` without
`--offline-bundle` install from the hash-locked package registries; verified by S01B-T39 clean install and S01B-T40
upgrade).

| Component | Linux x86_64 bundle | Windows x64 bundle | Purpose / required? |
|---|---|---|---|
| `browsers/` | **680 MB**: `chromium-1243` 392 MB (headed Chromium), `chromium_headless_shell-1243` 283 MB (headless, used by the end-to-end tests), `ffmpeg-1011` 5 MB | **absent — BLOCKED** (Playwright CDN unreachable from the build machine) | end-to-end tests and the browser extra; both Chromium builds are what Playwright installs (headed + headless); this is the main reason the Linux bundle is ~1.5 GB |
| `npm-cache/` | 225 MB (101 workspace + 466 mobile entries, 0 missing) | 173 MB (95 + 466, 0 missing) | web/desktop workspaces and the React Native mobile app |
| `wheels/` | 161 MB, 106 wheels (largest: playwright 46 MB, litellm 36 MB, botocore 16 MB, temporalio 14 MB) | 136 MB, 107 wheels (`win_amd64`) | both Python locks (core + AI/browser extras) |
| `python/` | 104 MB CPython 3.13.14 | 69 MB CPython 3.13.14 (Windows) | targets without a suitable Python |
| `tools/uv` | 64 MB | absent (a Linux uv binary is useless on Windows) | bundled Python bootstrap |
| `system/` | 58 MB, 33 `.deb` (PostgreSQL 16, pgvector, Redis, git closure) + local apt index | 37 MB PostgreSQL 16 x64 only; **pgvector, Redis/Memurai, Git for Windows BLOCKED** | system services |
| `images/` | absent — BLOCKED (no Docker daemon) | absent — BLOCKED | Docker deployment path only |
| `assets/` | 0.2 MB | 0.2 MB | migrations, bootstrap SQL, locks |

No duplicate dependency sets, no model weights, no GPU/CUDA distributions and no operating-system images are included.
The Windows bundle is smaller than the Linux one chiefly because its browsers (~680 MB on Linux) and uv could not be
obtained from the build machine — **Windows offline installation is therefore NOT complete and not qualified**
(PHYSICAL_QUALIFICATION.md §1.1, BLOCKERS B-S01B-10).
