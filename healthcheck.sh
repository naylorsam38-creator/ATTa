#!/usr/bin/env bash
# Inspect activation, environment manifest, services, ports, controller and capabilities. Exit 0 = healthy.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; PY="${ATTA_VENV:-$ROOT/.venv}/bin/python"
cd "$ROOT" && exec "$PY" -m backend.deployment healthcheck "$@"
