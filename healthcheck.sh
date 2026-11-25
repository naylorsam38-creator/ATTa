#!/usr/bin/env bash
# Inspect actual services, ports, dependencies, model endpoints and registered capabilities. Exit 0 = healthy.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; PY="${ATTA_VENV:-$ROOT/.venv}/bin/python"
cd "$ROOT" && exec "$PY" -m backend.service_manager healthcheck "$@"
