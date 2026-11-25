#!/usr/bin/env bash
# ATTa platform installer (Linux/macOS). Windows: INSTALL.bat
#   ./install.sh [--with-ai] [--with-browser] [--no-node] [--with-desktop] [--with-mobile] [--install-system]
#                [--offline-bundle DIR]  [deployment options: --profile P --approve --suite S --no-start]
#   ATTA_VENV=/path/to/venv ./install.sh    (default: ./.venv)
# Thin launcher: bootstraps the locked dependencies (from the offline bundle when given), then hands over to the
# deployment installer (python -m backend.deployment install): discover -> plan -> qualify -> activate.
# Never downloads models; never starts paid infrastructure.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="${ATTA_VENV:-$ROOT/.venv}"
WITH_AI=0; WITH_BROWSER=0; WITH_NODE=1; WITH_DESKTOP=0; WITH_MOBILE=0; INSTALL_SYSTEM=0; BUNDLE=""; DEPLOY_ARGS=()
while [ $# -gt 0 ]; do case "$1" in
  --with-ai) WITH_AI=1 ;; --with-browser) WITH_BROWSER=1 ;; --no-node) WITH_NODE=0 ;;
  --with-desktop) WITH_DESKTOP=1 ;; --with-mobile) WITH_MOBILE=1 ;; --install-system) INSTALL_SYSTEM=1 ;;
  --offline-bundle) BUNDLE="$2"; DEPLOY_ARGS+=(--bundle "$2"); shift ;;
  --profile|--suite) DEPLOY_ARGS+=("$1" "$2"); shift ;; --approve|--no-start) DEPLOY_ARGS+=("$1") ;;
  -h|--help) sed -n '2,9p' "$0"; exit 0 ;; *) echo "unknown option $1" >&2; exit 2 ;; esac; shift; done
say(){ printf '[install] %s\n' "$*"; }
fail(){ printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

# 1. Python >= 3.12
PY=""
for c in python3.13 python3.12 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,12)<=sys.version_info[:2]<(3,14) else 1)'; then PY="$(command -v "$c")"; break; fi
done
if [ -z "$PY" ] && [ -n "$BUNDLE" ]; then           # offline target without Python: use the bundled CPython
  RT="$(ls -d "$BUNDLE"/python/cpython-*/ 2>/dev/null | head -1)"
  if [ -n "$RT" ]; then mkdir -p "$ROOT/runtime/python-runtime"; cp -a "$RT" "$ROOT/runtime/python-runtime/"
    PY="$ROOT/runtime/python-runtime/$(basename "$RT")/bin/python3"; say "using the bundled Python runtime"; fi
fi
[ -n "$PY" ] || fail "Python 3.12 or 3.13 is required (https://www.python.org/downloads/)"
say "python: $PY ($("$PY" -V 2>&1))"
command -v git >/dev/null 2>&1 || fail "git is required"

# 2. Backend virtual environment from the hash-locked requirements
LOCK="$ROOT/configuration/locks/backend-core.lock.txt"
[ "$WITH_AI$WITH_BROWSER" != "00" ] && LOCK="$ROOT/configuration/locks/backend-full.lock.txt"
if [ ! -x "$VENV/bin/python" ]; then say "creating virtual environment $VENV"; "$PY" -m venv "$VENV"; fi
if [ -n "$BUNDLE" ]; then
  say "installing locked backend dependencies OFFLINE from $BUNDLE/wheels"
  "$VENV/bin/python" -m pip install --disable-pip-version-check -q --no-index --find-links "$BUNDLE/wheels" --require-hashes --no-deps -r "$LOCK"
else
  "$VENV/bin/python" -m pip install --disable-pip-version-check -q --upgrade "pip>=25" >/dev/null
  say "installing locked backend dependencies ($(basename "$LOCK"))"
  "$VENV/bin/python" -m pip install --disable-pip-version-check -q --require-hashes --no-deps -r "$LOCK"
fi
"$VENV/bin/python" -m pip check
SITE="$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
printf '%s\n' "$ROOT" > "$SITE/atta_platform.pth"   # makes `backend` importable without packaging

# 3. Node workspaces (web, desktop, shared packages) from package-lock.json
if [ "$WITH_NODE" = 1 ]; then
  if command -v node >/dev/null 2>&1 && node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)'; then
    say "installing locked Node workspaces (npm ci)"
    if [ "$WITH_DESKTOP" = 1 ]; then unset ELECTRON_SKIP_BINARY_DOWNLOAD; else export ELECTRON_SKIP_BINARY_DOWNLOAD=1; fi
    NPM_OFF=()
    if [ -n "$BUNDLE" ]; then       # private copy of the bundled cache: npm writes into its cache, the bundle stays intact
      rm -rf "$ROOT/runtime/npm-offline-cache"; mkdir -p "$ROOT/runtime"; cp -a "$BUNDLE/npm-cache" "$ROOT/runtime/npm-offline-cache"
      NPM_OFF=(--offline --logs-max=0 --cache "$ROOT/runtime/npm-offline-cache"); fi
    (cd "$ROOT" && npm ci "${NPM_OFF[@]}" --no-audit --no-fund --loglevel=error)
    if [ "$WITH_MOBILE" = 1 ]; then (cd "$ROOT/apps/mobile" && npm ci "${NPM_OFF[@]}" --no-audit --no-fund --loglevel=error); fi
  else
    say "WARNING: Node.js >= 20 not found — web/desktop clients skipped (backend still installs)"
  fi
fi

# 4. System services (offline: from the bundle used as a local apt repository)
if [ "$INSTALL_SYSTEM" = 1 ] && [ -n "$BUNDLE" ] && [ -f "$BUNDLE/system/debs/Packages" ]; then
  say "installing system packages OFFLINE from $BUNDLE/system/debs"
  if [ "$(id -u)" -eq 0 ]; then "$VENV/bin/python" -m backend.deployment bundle install-system --bundle "$BUNDLE"
  else sudo "$VENV/bin/python" -m backend.deployment bundle install-system --bundle "$BUNDLE"; fi
fi
[ -n "$BUNDLE" ] && [ -d "$BUNDLE/browsers" ] && say "browsers: set PLAYWRIGHT_BROWSERS_PATH=$BUNDLE/browsers (or copy it) for browser features"
if ! command -v redis-server >/dev/null 2>&1; then
  if [ "$INSTALL_SYSTEM" = 1 ] && command -v apt-get >/dev/null 2>&1; then
    say "installing redis-server (approved system dependency)"; sudo apt-get install -y redis-server
  else
    say "WARNING: redis-server not found. Install it (apt-get install redis-server), re-run with --install-system,"
    say "         or run Redis via infrastructure/docker/compose.yaml and set ATTA__SERVICES__REDIS__MODE=external"
  fi
fi
command -v psql >/dev/null 2>&1 || say "NOTE: psql not found; PostgreSQL 16 + pgvector can run via infrastructure/docker/compose.yaml"

# 5. Local configuration (never committed)
if [ ! -f "$ROOT/configuration/local.env" ]; then
  cp "$ROOT/configuration/local.env.example" "$ROOT/configuration/local.env"; chmod 600 "$ROOT/configuration/local.env"
  say "created configuration/local.env from the example — set ATTA_DATABASE_PASSWORD before START"
fi
mkdir -p "$ROOT/runtime"
"$VENV/bin/python" - "$ROOT" "$LOCK" <<'PYEOF'
import json, sys, hashlib, platform, datetime, pathlib
root, lock = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
rec = {"installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "python": platform.python_version(),
       "lock": lock.name, "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest()}
(root / "runtime" / "install.json").write_text(json.dumps(rec, indent=2))
PYEOF
(cd "$ROOT" && "$VENV/bin/python" -m backend.service_manager validate)
say "INSTALL COMPLETE: locked dependencies installed"
# Hand over to the deployment installer (resumable): discovery, profile, plan, provisioning, qualification, activation.
set +e
(cd "$ROOT" && "$VENV/bin/python" -m backend.deployment install "${DEPLOY_ARGS[@]}")
code=$?
[ "$code" -eq 10 ] && { say "awaiting operator review of runtime/deployment/install_plan.json: re-run with --approve"; exit 0; }
exit "$code"
