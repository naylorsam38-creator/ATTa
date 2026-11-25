#!/usr/bin/env sh
# POSIX wrapper: runs the transactional installer with Python 3. Usage: sh install.sh <ATTa root> [--check]
DIR="$(cd "$(dirname "$0")" && pwd)"
for PY in python3 python; do
  if command -v "$PY" >/dev/null 2>&1 && "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
    exec "$PY" "$DIR/install.py" "$@"
  fi
done
echo "Python 3.8+ is required" >&2
exit 2
