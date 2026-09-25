#!/usr/bin/env bash
# Restores a backup made by scripts/backup.sh onto a server that already has a fresh,
# working Coolify install of the SAME version (run scripts/install.sh first).
#
# Usage: sudo ./scripts/restore.sh /path/to/coolify-backup-<host>-<stamp>.tar.gz [--yes]
#
# Steps: verify checksums -> stop Coolify -> restore database (replacing current data)
#        -> restore APP_KEY, SSH keys and proxy config -> recreate containers -> verify.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
enable_error_trap

archive=""
assume_yes=false
for arg in "$@"; do
    case "$arg" in
    --yes) assume_yes=true ;;
    -h | --help)
        sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    *) archive="$arg" ;;
    esac
done

require_root
[ -n "$archive" ] || die "Usage: $0 /path/to/coolify-backup-*.tar.gz [--yes]"
[ -f "$archive" ] || die "Backup not found: $archive"
[ -f "$COOLIFY_ENV_FILE" ] || die "Coolify is not installed here. Run scripts/install.sh (same version as the backup) first."
[ "$(docker inspect --format '{{.State.Running}}' coolify-db 2>/dev/null)" = "true" ] ||
    die "coolify-db is not running. Is Coolify installed and started?"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
tar -C "$stage" -xzf "$archive"
src="$(find "$stage" -mindepth 1 -maxdepth 1 -type d -name 'coolify-backup-*' | head -n1)"
[ -n "$src" ] || die "Archive does not contain a coolify-backup-* directory"
(cd "$src" && sha256sum --quiet -c SHA256SUMS) || die "Backup checksum mismatch - archive is corrupt"
ok "Backup checksums verified"

manifest_get() { env_file_get "$1" "$src/MANIFEST"; }
backup_version="$(manifest_get coolify_version)"
[ -n "$backup_version" ] || die "Backup MANIFEST has no coolify_version - not a backup made by backup.sh?"
running_version="$(coolify_running_version)"
if [ "$backup_version" != "$running_version" ]; then
    die "Backup is from Coolify $backup_version but this server runs $running_version. Install $backup_version first (COOLIFY_VERSION=$backup_version in config/coolify.env), restore, then upgrade."
fi

if [ "$assume_yes" != true ]; then
    warn "This REPLACES all Coolify data on $(hostname) with the backup from $(manifest_get host) ($(manifest_get created))."
    read -r -p "Type 'restore' to continue: " answer
    [ "$answer" = "restore" ] || die "Aborted."
fi

db_user="$(env_file_get DB_USERNAME)"
db_name="$(env_file_get DB_DATABASE)"
db_user="${db_user:-coolify}"
db_name="${db_name:-coolify}"

info "Taking a safety backup of the current state first"
safety_dir="${BACKUP_DIR:-/root/coolify-backups}/pre-restore"
BACKUP_DIR="$safety_dir" KEEP=5 "$SCRIPT_DIR/backup.sh" >/dev/null ||
    die "Could not take a safety backup; refusing to continue"
# Names embed a UTC timestamp, so the lexically last one is the newest.
safety="$(find "$safety_dir" -maxdepth 1 -name 'coolify-backup-*.tar.gz' | sort | tail -n1)"
ok "Safety backup: $safety"

info "Stopping Coolify application containers"
docker stop coolify coolify-realtime >/dev/null

info "Restoring database"
if ! docker exec -i coolify-db pg_restore --clean --if-exists --no-owner --no-acl \
    -U "$db_user" -d "$db_name" <"$src/coolify-db.dump"; then
    docker start coolify coolify-realtime >/dev/null 2>&1 || true
    die "pg_restore failed; Coolify was restarted on its previous data, which may now be partially overwritten. To roll back: $0 $safety"
fi
ok "Database restored"

# The database's secrets are encrypted with the old APP_KEY, so it must come back.
# Everything else in .env (DB/Redis passwords, realtime keys) belongs to THIS server's
# containers and volumes and must stay as-is.
old_key="$(env_file_get APP_KEY "$src/coolify.env")"
[ -n "$old_key" ] || die "Backup .env has no APP_KEY"
cp "$COOLIFY_ENV_FILE" "$COOLIFY_ENV_FILE.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
tmp_env="$(mktemp)"
awk -v k="$old_key" 'BEGIN{done=0} /^APP_KEY=/{print "APP_KEY=" k; done=1; next} {print} END{if(!done) print "APP_KEY=" k}' \
    "$COOLIFY_ENV_FILE" >"$tmp_env"
cat "$tmp_env" >"$COOLIFY_ENV_FILE"
rm -f "$tmp_env"
ok "APP_KEY restored"

info "Restoring SSH keys and proxy config"
tar -C "$COOLIFY_DATA_DIR" -xf "$src/ssh-keys.tar"
if [ -f "$src/proxy.tar" ]; then
    tar -C "$COOLIFY_DATA_DIR" -xf "$src/proxy.tar"
fi
chown -R 9999:root "$COOLIFY_DATA_DIR/ssh" "$COOLIFY_DATA_DIR/proxy" 2>/dev/null || true
chmod -R 700 "$COOLIFY_DATA_DIR/ssh"

# The restored database manages "localhost" with the OLD server's key; authorise it here.
key="$(coolify_localhost_key_file)" || die "Restored database has no SSH key for localhost"
[ -f "$key" ] || die "Restored key file $key is missing from the backup"
auth="$(coolify_localhost_authorized_keys)"
mkdir -p "$(dirname "$auth")"
chmod 700 "$(dirname "$auth")"
touch "$auth"
chmod 600 "$auth"
pub="$(ssh-keygen -y -f "$key")" || die "Could not read the restored SSH key $key"
if ! grep -qF "$(printf '%s' "$pub" | awk '{print $2}')" "$auth"; then
    printf '%s coolify\n' "$pub" >>"$auth"
fi
ok "Localhost SSH access authorised for the restored key ($auth)"

info "Recreating Coolify containers so they pick up the restored APP_KEY"
compose_files=(-f "$COOLIFY_DATA_DIR/source/docker-compose.yml" -f "$COOLIFY_DATA_DIR/source/docker-compose.prod.yml")
if [ -f "$COOLIFY_DATA_DIR/source/docker-compose.custom.yml" ]; then
    compose_files+=(-f "$COOLIFY_DATA_DIR/source/docker-compose.custom.yml")
fi
LATEST_IMAGE="$running_version" docker compose --env-file "$COOLIFY_ENV_FILE" "${compose_files[@]}" \
    up -d --force-recreate --wait --wait-timeout 180 coolify soketi ||
    die "Containers failed to start after restore. Check: docker logs coolify"
docker exec coolify php artisan optimize:clear >/dev/null 2>&1 || true

EXPECTED_VERSION="$running_version" "$SCRIPT_DIR/verify.sh" || die "Restore finished but verification failed."
ok "Restore complete. Log in with the admin account from the ORIGINAL server."
info "If this is a new server, point your DNS records (dashboard and apps) at this server's IP."
