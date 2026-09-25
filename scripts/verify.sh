#!/usr/bin/env bash
# Proves a Coolify install is healthy and locked down. Safe to run any time; changes nothing.
# Exit code: 0 = healthy (warnings allowed), 1 = at least one failed check.
#
# Usage: sudo ./scripts/verify.sh
#        sudo EXPECTED_VERSION=4.3.23 ./scripts/verify.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

APP_PORT="${APP_PORT:-8000}"
SOKETI_PORT="${SOKETI_PORT:-6001}"
WAIT_SECONDS="${WAIT_SECONDS:-120}"

FAILURES=0
WARNINGS=0
bad() {
    fail "$*"
    FAILURES=$((FAILURES + 1))
}
caution() {
    warn "$*"
    WARNINGS=$((WARNINGS + 1))
}

# Retry a command until it succeeds or WAIT_SECONDS elapse. Coolify runs migrations and
# seeders after the container reports healthy on first boot, so give it time.
wait_for() {
    local deadline=$((SECONDS + WAIT_SECONDS))
    until "$@"; do
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 3
    done
}

container_healthy() {
    [ "$(docker inspect --format '{{.State.Health.Status}}' "$1" 2>/dev/null)" = "healthy" ]
}

check_containers() {
    local c state
    for c in coolify coolify-db coolify-redis coolify-realtime; do
        if wait_for container_healthy "$c"; then
            ok "Container $c is healthy"
        else
            state="$(docker inspect --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$c" 2>/dev/null || echo missing)"
            bad "Container $c is not healthy (state: $state). Check: docker logs $c"
            if docker logs --tail 50 "$c" 2>&1 | grep -q 'Address family not supported'; then
                fail "  Cause: IPv6 is disabled in this server's kernel. Remove ipv6.disable=1 from GRUB_CMDLINE_LINUX in /etc/default/grub, run update-grub and reboot."
            fi
        fi
    done
}

check_proxy() {
    local usable image
    usable="$(coolify_db_query 'select is_usable from server_settings where server_id = 0')"
    if proxy_running; then
        image="$(docker inspect --format '{{.Config.Image}}' coolify-proxy 2>/dev/null)"
        if [[ $image != *traefik* ]]; then
            ok "Reverse proxy is running ($image)"
        elif [ "$(curl -fsS -m 5 http://127.0.0.1/ping 2>/dev/null)" = "OK" ]; then
            ok "Reverse proxy is running and answering on port 80 ($image)"
        else
            bad "Reverse proxy is running but not answering on port 80. Check: docker logs coolify-proxy"
        fi
    elif [ "$usable" = "t" ]; then
        bad "Reverse proxy (coolify-proxy) is down although the server is validated; apps are unreachable. Start it in Servers > localhost > Proxy, or check: docker logs coolify-proxy"
    else
        caution "Localhost server not validated yet, so the reverse proxy isn't running. Re-run scripts/install.sh, or finish onboarding in the dashboard (choose This Machine)."
    fi
}

dashboard_up() {
    [ "$(curl -fsS -m 5 "http://127.0.0.1:${APP_PORT}/api/health" 2>/dev/null)" = "OK" ]
}

realtime_up() {
    curl -fsS -m 5 "http://127.0.0.1:${SOKETI_PORT}/ready" >/dev/null 2>&1
}

check_endpoints() {
    if wait_for dashboard_up; then
        ok "Dashboard API answers on port $APP_PORT (/api/health = OK)"
    else
        bad "Dashboard is not answering on http://127.0.0.1:${APP_PORT}/api/health"
    fi
    if wait_for realtime_up; then
        ok "Realtime server answers on port $SOKETI_PORT"
    else
        bad "Realtime server is not answering on http://127.0.0.1:${SOKETI_PORT}/ready"
    fi
}

check_env_file() {
    local key missing=""
    if [ ! -f "$COOLIFY_ENV_FILE" ]; then
        bad "$COOLIFY_ENV_FILE is missing"
        return
    fi
    for key in APP_ID APP_KEY DB_PASSWORD REDIS_PASSWORD PUSHER_APP_ID PUSHER_APP_KEY PUSHER_APP_SECRET; do
        [ -n "$(env_file_get "$key")" ] || missing="$missing $key"
    done
    if [ -n "$missing" ]; then
        bad "Secrets missing from $COOLIFY_ENV_FILE:$missing"
    else
        ok "All generated secrets are present in $COOLIFY_ENV_FILE"
    fi
    # What matters is whether another account can read the secrets. Coolify's own
    # upgrade rewrites .env as mode 644 and relies on /data/coolify being 700, so test
    # effective access as an unprivileged user rather than the file mode alone.
    if runuser -u nobody -- test -r "$COOLIFY_ENV_FILE" 2>/dev/null; then
        bad "$COOLIFY_ENV_FILE (Coolify's secrets) is readable by other users. Fix: chmod 700 $COOLIFY_DATA_DIR"
    else
        ok "$COOLIFY_ENV_FILE is not readable by other users"
    fi
}

check_version() {
    local image tag
    image="$(docker inspect --format '{{.Config.Image}}' coolify 2>/dev/null)"
    tag="${image##*:}"
    if [ -z "$image" ]; then
        return # already reported by check_containers
    fi
    if [ -n "${EXPECTED_VERSION:-}" ] && [ "$tag" != "${EXPECTED_VERSION#v}" ]; then
        bad "Coolify is running $tag but $EXPECTED_VERSION was expected"
    else
        ok "Coolify version: $tag"
    fi
    if [ "$(env_file_get AUTOUPDATE)" = "false" ]; then
        info "Auto-update is OFF: upgrade deliberately with scripts/upgrade.sh"
    else
        info "Auto-update is ON: Coolify will update itself (Settings > Update to change)"
    fi
}

check_lockdown() {
    if wait_for coolify_admin_exists; then
        local who
        who="$(coolify_db_query 'select email from users where id = 0')"
        ok "Admin account exists ($who)"
    else
        bad "No admin account was created. Coolify rejected the ROOT_USER_* values; see: docker logs coolify 2>&1 | grep -A5 'Root User'"
    fi
    local reg
    reg="$(coolify_db_query 'select is_registration_enabled from instance_settings where id = 0')"
    case "$reg" in
    f | false) ok "Public sign-up is disabled" ;;
    t | true) bad "Public sign-up is ENABLED: anyone who can reach port $APP_PORT can create an account. Disable it in Settings > Configuration now." ;;
    *) bad "Could not read the sign-up setting from the database" ;;
    esac
}

# Coolify deploys to its own host over SSH. Prove its key is on disk and authorised,
# then prove it end to end by having Coolify run a command on this host.
check_ssh_key() {
    local key auth pub ssh_user out
    ssh_user="$(coolify_db_query 'select "user" from servers where id = 0')"
    if [ -z "$ssh_user" ]; then
        bad "Coolify's localhost server has no SSH user (the installer ran with \$USER unset). Fix: in Coolify open Servers > localhost, set User to root, save, then Validate Server."
        return
    fi
    if ! key="$(coolify_localhost_key_file)"; then
        bad "Coolify has no SSH key recorded for its localhost server"
        return
    fi
    if [ ! -f "$key" ]; then
        bad "Coolify's localhost SSH key file is missing: $key (restart the coolify container to rewrite it)"
        return
    fi
    auth="$(coolify_localhost_authorized_keys)"
    pub="$(ssh-keygen -y -f "$key" 2>/dev/null | awk '{print $2}')"
    if [ -n "$pub" ] && grep -qF "$pub" "$auth" 2>/dev/null; then
        ok "Coolify's SSH key is authorised to manage this host ($auth)"
    else
        bad "Coolify's SSH key is not in $auth, so Coolify cannot deploy to this host"
        return
    fi
    out="$(timeout 60 docker exec coolify php artisan tinker --execute \
        'echo trim(instant_remote_process(["echo coolify-ssh-ok"], App\Models\Server::find(0)));' 2>&1 | tail -n1)"
    if [ "$out" = "coolify-ssh-ok" ]; then
        ok "Coolify can run commands on this host over SSH as $ssh_user"
    else
        bad "Coolify cannot run commands on this host over SSH: $out"
    fi
}

main() {
    echo "== Coolify verification: $(hostname) =="
    if [ "$(id -u)" -ne 0 ]; then
        die "Run as root (sudo); the checks read Docker and $COOLIFY_DATA_DIR."
    fi
    if ! docker info >/dev/null 2>&1; then
        die "Docker is not running."
    fi
    check_containers
    check_endpoints
    check_proxy
    check_env_file
    check_version
    check_lockdown
    check_ssh_key
    echo
    if [ "$FAILURES" -gt 0 ]; then
        fail "Verification: $FAILURES failure(s), $WARNINGS warning(s)."
        return 1
    fi
    ok "Verification passed with $WARNINGS warning(s)."
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
