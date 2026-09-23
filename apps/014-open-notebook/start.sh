#!/bin/bash
# Start open-notebook for the ATTa pipeline, as its supervisord does: SurrealDB (the official
# surrealdb/surrealdb:v2 container), the FastAPI API, the background worker, and the Next.js
# server on $PORT (which forwards /api to the API). Everything stops together.
set -e
DATA="$1"; PORT="$2"; SRC="$(cd "$(dirname "$0")/src" && pwd)"
free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])'; }
DB_PORT=$(free_port); API_PORT=5055   # the Next.js /api rewrite is fixed at build time to localhost:5055, as in their image
DB_NAME="atta-open-notebook-surreal-$PORT"
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; docker rm -f "$DB_NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
docker rm -f "$DB_NAME" >/dev/null 2>&1 || true
docker run -d --name "$DB_NAME" -p 127.0.0.1:$DB_PORT:8000 surrealdb/surrealdb:v2 start --user root --pass root memory >/dev/null
for i in $(seq 1 60); do curl -sf http://127.0.0.1:$DB_PORT/health >/dev/null && break; sleep 1; done
export SURREAL_URL=ws://127.0.0.1:$DB_PORT/rpc SURREAL_USER=root SURREAL_PASSWORD=root SURREAL_NAMESPACE=open_notebook SURREAL_DATABASE=open_notebook
export API_HOST=127.0.0.1 API_PORT=$API_PORT API_RELOAD=false
cd "$SRC"
uv run --no-sync python run_api.py & PIDS+=($!)
uv run --no-sync surreal-commands-worker --import-modules commands --max-tasks 5 & PIDS+=($!)
for i in $(seq 1 90); do curl -sf http://127.0.0.1:$API_PORT/health >/dev/null && break; sleep 1; done
# Next's standalone server may be nested (it traces from the outermost lockfile it finds).
SERVER_JS=$(find "$SRC/frontend/.next/standalone" -name server.js -not -path '*/node_modules/*' | head -1)
cd "$(dirname "$SERVER_JS")"
INTERNAL_API_URL=http://127.0.0.1:$API_PORT NODE_ENV=production PORT=$PORT HOSTNAME=127.0.0.1 node server.js & PIDS+=($!)
wait -n
