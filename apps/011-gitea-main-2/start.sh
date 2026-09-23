#!/bin/bash
# Start Gitea for the ATTa pipeline: write app.ini for this port (SQLite, install locked), make sure the admin exists, serve.
set -e
DATA="$1"; PORT="$2"; SRC="$(cd "$(dirname "$0")/src" && pwd)"
mkdir -p "$DATA/custom/conf" "$DATA/data" "$DATA/repos" "$DATA/log"
cat > "$DATA/custom/conf/app.ini" <<INI
APP_NAME = Gitea
RUN_MODE = prod
WORK_PATH = $DATA

[server]
HTTP_ADDR = 127.0.0.1
HTTP_PORT = $PORT
ROOT_URL = http://127.0.0.1:$PORT/
DISABLE_SSH = true
OFFLINE_MODE = true

[database]
DB_TYPE = sqlite3
PATH = $DATA/data/gitea.db

[repository]
ROOT = $DATA/repos

[security]
INSTALL_LOCK = true
SECRET_KEY = atta-test-secret-key-0123456789abcdef

[service]
DISABLE_REGISTRATION = false

[log]
MODE = console
LEVEL = warn
INI
# Gitea refuses to run as root: run it as an unprivileged user that owns the data dir.
id atta-gitea >/dev/null 2>&1 || useradd -r -m -s /bin/bash atta-gitea
chown -R atta-gitea "$DATA"
as_gitea() { runuser -u atta-gitea -- env GITEA_WORK_DIR="$DATA" GITEA_CUSTOM="$DATA/custom" HOME="$DATA" "$@"; }
as_gitea "$SRC/gitea" migrate --config "$DATA/custom/conf/app.ini" >/dev/null
as_gitea "$SRC/gitea" admin user create --config "$DATA/custom/conf/app.ini" --username atta-admin --password 'atta-admin-password-123' --email admin@example.com --admin --must-change-password=false >/dev/null 2>&1 || true
exec runuser -u atta-gitea -- env GITEA_WORK_DIR="$DATA" GITEA_CUSTOM="$DATA/custom" HOME="$DATA" "$SRC/gitea" web --config "$DATA/custom/conf/app.ini"
