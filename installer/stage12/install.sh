#!/usr/bin/env bash
# ATTa Stage 12 installer (Linux/macOS).  ./install.sh --target /path/to/ATTa [--dry-run|--uninstall|--recover] ...
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Python 3.10+ is required" >&2; exit 5; }
exec "$PY" "$HERE/install.py" "$@"
