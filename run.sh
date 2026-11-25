#!/usr/bin/env bash
# RUN: start the activated ATTa deployment (refuses if the machine or configuration changed since qualification).
# Thin launcher: all logic is in `python -m backend.deployment run`. Never edited when capabilities are added.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; PY="${ATTA_VENV:-$ROOT/.venv}/bin/python"
[ -x "$PY" ] || { echo "not installed: run ./install.sh" >&2; exit 1; }
export NEXT_TELEMETRY_DISABLED=1
cd "$ROOT" && exec "$PY" -m backend.deployment run "$@"
