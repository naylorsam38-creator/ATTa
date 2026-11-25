#!/usr/bin/env bash
# ATTa platform installer (Linux/macOS). Windows: INSTALL.bat
#   ./install.sh [--with-ai] [--with-browser] [--no-node] [--with-desktop] [--with-mobile] [--install-system]
#   ATTA_VENV=/path/to/venv ./install.sh    (default: ./.venv)
# Installs only locked, approved dependencies. Never downloads models; never starts paid infrastructure.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="${ATTA_VENV:-$ROOT/.venv}"
WITH_AI=0; WITH_BROWSER=0; WITH_NODE=1; WITH_DESKTOP=0; WITH_MOBILE=0; INSTALL_SYSTEM=0
for a in "$@"; do case "$a" in
  --with-ai) WITH_AI=1 ;; --with-browser) WITH_BROWSER=1 ;; --no-node) WITH_NODE=0 ;;
  --with-desktop) WITH_DESKTOP=1 ;; --with-mobile) WITH_MOBILE=1 ;; --install-system) INSTALL_SYSTEM=1 ;;
  -h|--help) sed -n '2,6p' "$0"; exit 0 ;; *) echo "unknown option $a" >&2; exit 2 ;; esac; done
say(){ printf '[install] %s\n' "$*"; }
fail(){ printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

# 1. Python >= 3.12
PY=""
for c in python3.13 python3.12 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,12)<=sys.version_info[:2]<(3,14) else 1)'; then PY="$(command -v "$c")"; break; fi
done
[ -n "$PY" ] || fail "Python 3.12 or 3.13 is required (https://www.python.org/downloads/)"
say "python: $PY ($("$PY" -V 2>&1))"
command -v git >/dev/null 2>&1 || fail "git is required"

# 2. Backend virtual environment from the hash-locked requirements
LOCK="$ROOT/configuration/locks/backend-core.lock.txt"
[ "$WITH_AI$WITH_BROWSER" != "00" ] && LOCK="$ROOT/configuration/locks/backend-full.lock.txt"
if [ ! -x "$VENV/bin/python" ]; then say "creating virtual environment $VENV"; "$PY" -m venv "$VENV"; fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -q --upgrade "pip>=25" >/dev/null
say "installing locked backend dependencies ($(basename "$LOCK"))"
"$VENV/bin/python" -m pip install --disable-pip-version-check -q --require-hashes --no-deps -r "$LOCK"
"$VENV/bin/python" -m pip check
SITE="$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
printf '%s\n' "$ROOT" > "$SITE/atta_platform.pth"   # makes `backend` importable without packaging

# 3. Node workspaces (web, desktop, shared packages) from package-lock.json
if [ "$WITH_NODE" = 1 ]; then
  if command -v node >/dev/null 2>&1 && node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)'; then
    say "installing locked Node workspaces (npm ci)"
    if [ "$WITH_DESKTOP" = 1 ]; then unset ELECTRON_SKIP_BINARY_DOWNLOAD; else export ELECTRON_SKIP_BINARY_DOWNLOAD=1; fi
    (cd "$ROOT" && npm ci --no-audit --no-fund --loglevel=error)
    if [ "$WITH_MOBILE" = 1 ]; then (cd "$ROOT/apps/mobile" && npm ci --no-audit --no-fund --loglevel=error); fi
  else
    say "WARNING: Node.js >= 20 not found — web/desktop clients skipped (backend still installs)"
  fi
fi

# 4. System services
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
say "INSTALL COMPLETE"
