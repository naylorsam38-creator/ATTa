# Offline / air-gapped deployment

## 1. Build a bundle on a connected machine (same OS, architecture and Python X.Y as the target)

```
python -m backend.deployment bundle create --out /media/usb/atta-offline
python -m backend.deployment bundle verify --bundle /media/usb/atta-offline
```

Contents:

| Path | What |
|---|---|
| `wheels/` | every package of `configuration/locks/backend-core.lock.txt`, downloaded with `--require-hashes --only-binary=:all:` |
| `npm-cache/` | npm cache populated by `npm ci` from `package-lock.json` (Electron binary skipped) |
| `assets/database`, `assets/configuration/locks` | migrations, bootstrap SQL, all lock files |
| `bundle_manifest.json` | SHA-256 + size of every file, the Python and Node lock hashes, notes |

Never included: private keys (verification fails if one is found), credentials, models (only with an explicit
`--models PATH…`), container images when no Docker daemon is available (recorded in `notes`).

`verify` reports: lock mismatch, missing or modified files, unlisted files, private-key material.

## 2. Install on the offline machine

```
./install.sh --offline-bundle /media/usb/atta-offline --profile AIR_GAPPED      # then --approve
INSTALL.bat --offline-bundle E:\atta-offline --profile AIR_GAPPED
```

pip runs with `--no-index --find-links bundle/wheels --require-hashes`; npm with `--offline --cache bundle/npm-cache`.
`python -m backend.deployment bundle install-offline --bundle DIR --venv DIR` does the same into a fresh environment
and import-checks the result. Verified on this machine **inside a kernel network namespace with no network at all**
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
