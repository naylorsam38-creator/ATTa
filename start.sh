#!/usr/bin/env bash
# Start the ATTa platform through the central service manager. Capabilities register automatically:
# this script never needs editing when a capability is added.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; PY="${ATTA_VENV:-$ROOT/.venv}/bin/python"
[ -x "$PY" ] || { echo "not installed: run ./install.sh" >&2; exit 1; }
cd "$ROOT" && exec "$PY" -m backend.service_manager start "$@"
