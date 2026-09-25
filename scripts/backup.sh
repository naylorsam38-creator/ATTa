#!/usr/bin/env bash
# Backs up everything needed to rebuild this Coolify instance on a new server:
#   - Coolify's database (projects, apps, env vars, servers, settings)
#   - /data/coolify/source/.env  (APP_KEY decrypts the secrets stored in the database)
#   - /data/coolify/ssh/keys     (keys Coolify uses to reach its servers)
#   - /data/coolify/proxy        (proxy config and Let's Encrypt certificates)
#
# NOT included: the data inside your apps' own databases and volumes. Configure
# scheduled backups for those in Coolify (Database > Backups, ideally to S3).
#
# Usage: sudo ./scripts/backup.sh
# Env:   BACKUP_DIR (default /root/coolify-backups), KEEP (default 14 newest kept)
# Cron:  17 3 * * * root /opt/atta/scripts/backup.sh >>/var/log/coolify-backup.log 2>&1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
enable_error_trap

BACKUP_DIR="${BACKUP_DIR:-/root/coolify-backups}"
KEEP="${KEEP:-14}"

require_root
[[ $KEEP =~ ^[1-9][0-9]*$ ]] || die "KEEP must be a positive number, got '$KEEP'"
[ -f "$COOLIFY_ENV_FILE" ] || die "No Coolify install found ($COOLIFY_ENV_FILE missing)."
[ "$(docker inspect --format '{{.State.Running}}' coolify-db 2>/dev/null)" = "true" ] ||
    die "The coolify-db container is not running; cannot dump the database."

umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="coolify-backup-$(hostname -s)-$stamp"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/$name"
out="$stage/$name"

db_user="$(env_file_get DB_USERNAME)"
db_name="$(env_file_get DB_DATABASE)"
db_user="${db_user:-coolify}"
db_name="${db_name:-coolify}"

info "Dumping database '$db_name'"
docker exec coolify-db pg_dump -U "$db_user" -d "$db_name" -Fc >"$out/coolify-db.dump" ||
    die "pg_dump failed"
# Prove the dump is readable before we call this a backup.
docker exec -i coolify-db pg_restore --list <"$out/coolify-db.dump" >/dev/null ||
    die "The database dump is not readable by pg_restore"
ok "Database dump verified ($(du -h "$out/coolify-db.dump" | cut -f1))"

cp "$COOLIFY_ENV_FILE" "$out/coolify.env"
tar -C "$COOLIFY_DATA_DIR" -cf "$out/ssh-keys.tar" ssh/keys
if [ -d "$COOLIFY_DATA_DIR/proxy" ]; then
    tar -C "$COOLIFY_DATA_DIR" -cf "$out/proxy.tar" proxy
fi

version="$(coolify_running_version)"
{
    echo "created=$stamp"
    echo "host=$(hostname)"
    echo "coolify_version=${version:-unknown}"
    echo "db_user=$db_user"
    echo "db_name=$db_name"
} >"$out/MANIFEST"
(cd "$out" && sha256sum -- * >SHA256SUMS)

archive="$BACKUP_DIR/$name.tar.gz"
tar -C "$stage" -czf "$archive.partial" "$name"
mv "$archive.partial" "$archive"
chmod 600 "$archive"
ok "Backup written: $archive ($(du -h "$archive" | cut -f1))"

# Retention: keep the newest $KEEP archives.
mapfile -t old < <(find "$BACKUP_DIR" -maxdepth 1 -name 'coolify-backup-*.tar.gz' -printf '%T@ %p\n' | sort -rn | awk -v keep="$KEEP" 'NR > keep {print $2}')
for f in "${old[@]}"; do
    rm -f -- "$f"
    info "Pruned old backup $(basename "$f")"
done

warn "This backup contains Coolify's secrets. Copy it OFF this server (password manager, encrypted storage) - a backup that dies with the server is not a backup."
