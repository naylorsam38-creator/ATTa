#!/usr/bin/env bash
# Restore the last verified, database-compatible checkpoint recorded by the build controller.
#   ./recovery.sh            show the plan (dry run)
#   ./recovery.sh --yes      stop services, stash current work, switch to a recovery branch at the checkpoint
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; PY="${ATTA_VENV:-$ROOT/.venv}/bin/python"
cd "$ROOT"
"$PY" -m backend.verification checkpoints
if [ "${1:-}" != "--yes" ]; then "$PY" -m backend.verification recover; echo "Dry run. Re-run with --yes to restore."; exit 0; fi
read -r -p "Type RESTORE to stop services and restore the checkpoint: " ans
[ "$ans" = "RESTORE" ] || { echo "cancelled"; exit 1; }
"$PY" -m backend.service_manager stop || true
"$PY" -m backend.verification recover --yes
echo "Restored. Your previous work is in 'git stash list'. Run ./install.sh then ./start.sh."
