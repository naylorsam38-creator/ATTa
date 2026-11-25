#!/usr/bin/env bash
# Stop every service the service manager started (reverse dependency order, graceful then forced).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; PY="${ATTA_VENV:-$ROOT/.venv}/bin/python"
cd "$ROOT" && exec "$PY" -m backend.service_manager stop "$@"
