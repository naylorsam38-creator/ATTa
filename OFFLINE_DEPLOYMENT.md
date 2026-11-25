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
