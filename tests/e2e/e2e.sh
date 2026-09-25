#!/usr/bin/env bash
# End-to-end test of the deploy kit against a REAL Coolify install.
#
#   !!! DESTRUCTIVE: removes any Coolify install, its containers, volumes and /data/coolify.
#   !!! Run only on a throwaway VM or CI runner.
#
# Scenario:
#   1. install.sh --dry-run validates and changes nothing
#   2. install.sh installs E2E_FROM_VERSION; verify.sh passes; admin can log in;
#      public sign-up is closed (GET redirects, POST /register is 403)
#   3. upgrade.sh moves to E2E_TO_VERSION (backup taken first)
#   4. install.sh re-run is idempotent (secrets unchanged, still healthy)
#   5. backup.sh, then the server is wiped and reinstalled from scratch with a
#      different password, then restore.sh brings back the original data:
#      original admin logs in, a marker row survives, and Coolify can still run
#      commands on the host over SSH with the restored key.
#
# Usage: sudo E2E_CONFIRM_WIPE=yes tests/e2e/e2e.sh
# Env:
#   E2E_FROM_VERSION   (default 4.3.22)  version installed first
#   E2E_TO_VERSION     (default 4.3.23)  version upgraded to
#   E2E_CDN_MODE       live (default) | mirror
#       mirror: serve Coolify's CDN files locally from the GitHub tag E2E_CDN_TAG, for
#       networks that block cdn.coollabs.io. Same files, different host.
#   E2E_CDN_TAG        (default v$E2E_TO_VERSION)
#   E2E_IPV4_ONLY      yes = the test machine's kernel has IPv6 disabled and cannot be
#       rebooted (e.g. a cloud sandbox). Mounts an IPv4-only nginx listen config into the
#       coolify container via docker-compose.custom.yml (a Coolify-supported override
#       file) and accepts the preflight kernel-ipv6 blocker. NOT for real servers: fix
#       the kernel there instead (see docs/TROUBLESHOOTING.md).
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$KIT_DIR/scripts/lib/common.sh"

FROM_VERSION="${E2E_FROM_VERSION:-4.3.22}"
TO_VERSION="${E2E_TO_VERSION:-4.3.23}"
CDN_MODE="${E2E_CDN_MODE:-live}"
CDN_TAG="${E2E_CDN_TAG:-v$TO_VERSION}"
IPV4_ONLY="${E2E_IPV4_ONLY:-no}"
ADMIN_USER="e2e-admin"
# Must be a domain that really accepts mail: Coolify rejects example.com (null MX).
ADMIN_EMAIL="${E2E_ADMIN_EMAIL:-e2e-admin@gmail.com}"

[ "${E2E_CONFIRM_WIPE:-}" = "yes" ] || die "Refusing to run: this wipes Coolify. Set E2E_CONFIRM_WIPE=yes on a throwaway machine."
require_root

WORK="$(mktemp -d)"
CDN_PID=""
cleanup() {
    [ -z "$CDN_PID" ] || kill "$CDN_PID" 2>/dev/null || true
    rm -rf "$WORK"
}
trap cleanup EXIT

STEP=0
step() {
    STEP=$((STEP + 1))
    echo
    printf '%s\n' "================================================================"
    printf 'E2E step %d: %s\n' "$STEP" "$*"
    printf '%s\n' "================================================================"
}
assert() {
    local msg="$1"
    shift
    if "$@"; then
        ok "ASSERT: $msg"
    else
        die "ASSERTION FAILED: $msg"
    fi
}
assert_fails() {
    local msg="$1"
    shift
    if "$@"; then
        die "ASSERTION FAILED (expected failure): $msg"
    else
        ok "ASSERT: $msg"
    fi
}

# ------------------------------------------------------------------ helpers
wipe_coolify() {
    info "Wiping any existing Coolify install"
    docker ps -aq --filter 'name=^coolify' 2>/dev/null | xargs -r docker rm -f >/dev/null
    docker ps -aq --filter 'label=coolify.managed=true' 2>/dev/null | xargs -r docker rm -f >/dev/null
    docker volume rm -f coolify-db coolify-redis >/dev/null 2>&1 || true
    docker network rm coolify >/dev/null 2>&1 || true
    rm -rf /data/coolify
    if [ -f /root/.ssh/authorized_keys ]; then
        sed -i '/coolify/d' /root/.ssh/authorized_keys
    fi
    rm -f /root/coolify-admin-credentials.txt
}

start_cdn_mirror() {
    local dir="$WORK/cdn" port=18080 f
    mkdir -p "$dir"
    info "Mirroring Coolify CDN files from GitHub tag $CDN_TAG"
    for f in scripts/install.sh scripts/upgrade.sh scripts/upgrade-postgres.sh \
        docker-compose.yml docker-compose.prod.yml .env.production versions.json; do
        curl -fsSL --retry 3 "https://raw.githubusercontent.com/coollabsio/coolify/$CDN_TAG/$f" \
            -o "$dir/$(basename "$f")"
    done
    # Point the installer and upgrader at the local copy instead of cdn.coollabs.io.
    sed -i "s|^CDN=\"https://cdn.coollabs.io/coolify\"|CDN=\"http://127.0.0.1:$port\"|" \
        "$dir/install.sh" "$dir/upgrade.sh"
    grep -q "127.0.0.1:$port" "$dir/install.sh" || die "Could not rewrite CDN URL in install.sh"
    grep -q "127.0.0.1:$port" "$dir/upgrade.sh" || die "Could not rewrite CDN URL in upgrade.sh"
    (cd "$dir" && exec python3 -m http.server "$port" --bind 127.0.0.1 >/dev/null 2>&1) &
    CDN_PID=$!
    for _ in $(seq 1 20); do
        curl -fs "http://127.0.0.1:$port/versions.json" >/dev/null 2>&1 && break
        sleep 0.5
    done
    export COOLIFY_INSTALLER_URL="http://127.0.0.1:$port/install.sh"
    # The kit's preflight probes the real CDN; the mirror replaces it here.
    export SKIP_NETWORK_CHECKS=true
    ok "CDN mirror serving on 127.0.0.1:$port"
}

# See E2E_IPV4_ONLY above. Uses the listen config shipped in the target image, minus [::].
prepare_ipv4_only() {
    [ "$IPV4_ONLY" = "yes" ] || return 0
    local version="$1" src=/data/coolify/source
    mkdir -p "$src"
    docker pull -q "coollabsio/coolify:$version" >/dev/null
    docker run --rm --entrypoint cat "coollabsio/coolify:$version" /etc/nginx/site-opts.d/http.conf |
        grep -v 'listen \[::\]' >"$src/e2e-ipv4-http.conf"
    grep -q 'listen 8080' "$src/e2e-ipv4-http.conf" || die "Unexpected nginx config in coolify:$version"
    cat >"$src/docker-compose.custom.yml" <<EOF
services:
  coolify:
    volumes:
      - $src/e2e-ipv4-http.conf:/etc/nginx/site-opts.d/http.conf:ro
EOF
    info "IPv4-only override prepared for coolify:$version"
}

write_config() {
    local version="$1" password="$2"
    cat >"$KIT_DIR/config/coolify.env" <<EOF
COOLIFY_VERSION=$version
ROOT_USERNAME=$ADMIN_USER
ROOT_USER_EMAIL=$ADMIN_EMAIL
ROOT_USER_PASSWORD=$password
CREDENTIALS_FILE=/root/coolify-admin-credentials.txt
AUTOUPDATE=false
EOF
    chmod 600 "$KIT_DIR/config/coolify.env"
}

# Log in through the real web form. Success = redirect somewhere other than /login.
can_login() {
    local email="$1" password="$2" jar page token result code location
    jar="$(mktemp)"
    page="$(curl -fsS -c "$jar" -b "$jar" http://127.0.0.1:8000/login)"
    token="$(printf '%s' "$page" | grep -o 'name="_token" value="[^"]*"' | head -n1 | sed 's/.*value="//; s/"$//')"
    if [ -z "$token" ]; then
        token="$(printf '%s' "$page" | grep -o 'name="csrf-token" content="[^"]*"' | head -n1 | sed 's/.*content="//; s/"$//')"
    fi
    [ -n "$token" ] || {
        rm -f "$jar"
        fail "No CSRF token on /login"
        return 1
    }
    result="$(curl -sS -c "$jar" -b "$jar" -o /dev/null -w '%{http_code} %{redirect_url}' \
        -X POST http://127.0.0.1:8000/login \
        --data-urlencode "_token=$token" \
        --data-urlencode "email=$email" \
        --data-urlencode "password=$password")"
    rm -f "$jar"
    code="${result%% *}"
    location="${result#* }"
    info "POST /login -> $code $location"
    [ "$code" = "302" ] && [[ $location != */login* ]]
}

signup_page_closed() {
    local result
    result="$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' http://127.0.0.1:8000/register)"
    info "GET /register -> $result"
    [[ $result == 302\ */login* ]]
}

signup_post_rejected() {
    local jar page token code
    jar="$(mktemp)"
    page="$(curl -fsS -c "$jar" -b "$jar" http://127.0.0.1:8000/login)"
    token="$(printf '%s' "$page" | grep -o 'name="_token" value="[^"]*"' | head -n1 | sed 's/.*value="//; s/"$//')"
    [ -n "$token" ] || token="$(printf '%s' "$page" | grep -o 'name="csrf-token" content="[^"]*"' | head -n1 | sed 's/.*content="//; s/"$//')"
    code="$(curl -sS -c "$jar" -b "$jar" -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8000/register \
        --data-urlencode "_token=$token" --data-urlencode "name=attacker" \
        --data-urlencode "email=attacker@example.com" \
        --data-urlencode "password=Att4cker!Passw0rd" --data-urlencode "password_confirmation=Att4cker!Passw0rd")"
    rm -f "$jar"
    info "POST /register (attacker) -> $code"
    [ "$code" = "403" ] && [ "$(db "select count(*) from users where email = 'attacker@example.com'")" = "0" ]
}

db() {
    docker exec coolify-db psql -U coolify -d coolify -tAc "$1"
}

running_version() {
    coolify_running_version
}

# Coolify runs a command on its own host over SSH, the same path every deployment uses.
coolify_can_reach_host() {
    local out
    out="$(docker exec coolify php artisan tinker --execute \
        'echo trim(instant_remote_process(["echo e2e-ssh-ok"], App\Models\Server::find(0)));' 2>&1 | tail -n1)"
    info "Coolify -> host SSH: $out"
    [ "$out" = "e2e-ssh-ok" ]
}

# ---- Coolify API: deploy a real container and reach it through the proxy, the same path
# ATT will use. traefik/whoami is a tiny HTTP server that echoes the request.
TEST_APP_HOST="whoami.e2e.127.0.0.1.sslip.io"
API="http://127.0.0.1:8000/api/v1"

create_api_token() {
    db "update instance_settings set is_api_enabled = true where id = 0" >/dev/null
    docker exec coolify php artisan tinker --execute \
        'session(["currentTeam" => App\Models\Team::find(0)]); echo App\Models\User::find(0)->createToken("e2e", ["*"])->plainTextToken;' 2>&1 | tail -n1
}

api() {
    local method="$1" path="$2" body="${3:-}"
    curl -sS -m 30 -X "$method" -H "Authorization: Bearer $API_TOKEN" \
        -H 'Content-Type: application/json' -H 'Accept: application/json' \
        ${body:+-d "$body"} "$API$path"
}

deploy_test_app() {
    local project server app
    project="$(api POST /projects '{"name":"e2e"}' | jq -r .uuid)"
    server="$(api GET /servers | jq -r '.[] | select(.name == "localhost") | .uuid')"
    [ -n "$project" ] && [ "$project" != null ] && [ -n "$server" ] || return 1
    app="$(api POST /applications/dockerimage "{\"project_uuid\":\"$project\",\"server_uuid\":\"$server\",\"environment_name\":\"production\",\"docker_registry_image_name\":\"traefik/whoami\",\"docker_registry_image_tag\":\"latest\",\"ports_exposes\":\"80\",\"domains\":\"http://$TEST_APP_HOST\",\"instant_deploy\":true}" | jq -r .uuid)"
    [ -n "$app" ] && [ "$app" != null ] || return 1
    TEST_APP_UUID="$app"
    info "Deployed test app $app"
}

test_app_answers_now() {
    curl -fsS -m 5 -H "Host: $TEST_APP_HOST" http://127.0.0.1/ 2>/dev/null | grep -q '^Hostname:'
}

test_app_reachable() {
    local _
    for _ in $(seq 1 60); do
        test_app_answers_now && return 0
        sleep 5
    done
    return 1
}

redeploy_test_app() {
    api POST "/deploy?uuid=$TEST_APP_UUID&force=true" | grep -q deployment_uuid
}

password_a="$(generate_password)"
password_b="$(generate_password)"

# ------------------------------------------------------------------ run
wipe_coolify
if [ "$CDN_MODE" = "mirror" ]; then
    start_cdn_mirror
fi
if [ "$IPV4_ONLY" = "yes" ]; then
    export PREFLIGHT_ALLOW="kernel-ipv6"
fi

step "Dry run validates and changes nothing"
write_config "$FROM_VERSION" "$password_a"
"$KIT_DIR/scripts/install.sh" --dry-run
assert "dry run did not create /data/coolify" test ! -e /data/coolify
assert "dry run did not write a credentials file" test ! -e /root/coolify-admin-credentials.txt

step "Bad config is rejected before anything is installed"
# shellcheck disable=SC2016 # literal $ is the point: it must be rejected
write_config "$FROM_VERSION" 'has space$and&pipe|'
assert_fails "install.sh rejects an unsafe password" "$KIT_DIR/scripts/install.sh" --dry-run
assert "rejected run did not create /data/coolify" test ! -e /data/coolify
write_config "$FROM_VERSION" "$password_a"

step "Install Coolify $FROM_VERSION"
prepare_ipv4_only "$FROM_VERSION"
"$KIT_DIR/scripts/install.sh"
assert "running version is $FROM_VERSION" test "$(running_version)" = "$FROM_VERSION"
assert "credentials file is root-only" test "$(stat -c '%a' /root/coolify-admin-credentials.txt)" = "600"
assert "admin can log in through the web form" can_login "$ADMIN_EMAIL" "$password_a"
assert_fails "wrong password is rejected" can_login "$ADMIN_EMAIL" "${password_a}x"
assert "sign-up page redirects to login" signup_page_closed
assert "attacker sign-up POST is refused and creates no user" signup_post_rejected
assert "Coolify can run commands on its host over SSH" coolify_can_reach_host
assert "reverse proxy answers on port 80" test "$(curl -fsS -m 5 http://127.0.0.1/ping)" = "OK"

step "Deploy a real app through Coolify's API and reach it through the proxy"
API_TOKEN="$(create_api_token)"
assert "API token created" test "${#API_TOKEN}" -gt 20
assert "API answers with version $FROM_VERSION" test "$(api GET /version)" = "$FROM_VERSION"
assert "test app deploy accepted" deploy_test_app
assert "test app is served through the proxy at $TEST_APP_HOST" test_app_reachable

step "Admin self-heal: simulate Coolify's first-boot seeding having failed"
db "delete from team_user where user_id = 0; delete from users where id = 0; update instance_settings set is_registration_enabled = true where id = 0" >/dev/null
assert_fails "verify.sh detects the missing admin and open sign-up" env WAIT_SECONDS=3 "$KIT_DIR/scripts/verify.sh"
assert "ensure_admin_account re-runs Coolify's seeder and restores the admin" ensure_admin_account
assert "sign-up is closed again" signup_page_closed
assert "admin logs in after self-heal" can_login "$ADMIN_EMAIL" "$password_a"
assert "admin is owner of the root team again" test "$(db "select role from team_user where user_id = 0 and team_id = 0")" = "owner"

step "Upgrade $FROM_VERSION -> $TO_VERSION"
prepare_ipv4_only "$TO_VERSION"
"$KIT_DIR/scripts/upgrade.sh" "$TO_VERSION"
assert "running version is $TO_VERSION" test "$(running_version)" = "$TO_VERSION"
assert "upgrade took a backup first" compgen -G '/root/coolify-backups/coolify-backup-*.tar.gz'
assert "admin still logs in after upgrade" can_login "$ADMIN_EMAIL" "$password_a"
assert "test app still served after upgrade" test_app_reachable
assert "upgrade to the current version is a no-op" "$KIT_DIR/scripts/upgrade.sh" "$TO_VERSION"
assert_fails "upgrade to a non-existent version is refused" "$KIT_DIR/scripts/upgrade.sh" 0.0.1

step "Re-running install.sh is idempotent"
app_key_before="$(env_file_get APP_KEY)"
db_pass_before="$(env_file_get DB_PASSWORD)"
write_config "$TO_VERSION" "$password_a"
"$KIT_DIR/scripts/install.sh"
assert "APP_KEY unchanged" test "$(env_file_get APP_KEY)" = "$app_key_before"
assert "DB_PASSWORD unchanged" test "$(env_file_get DB_PASSWORD)" = "$db_pass_before"
assert "exactly one admin user" test "$(db 'select count(*) from users')" = "1"
assert "admin still logs in" can_login "$ADMIN_EMAIL" "$password_a"

step "Backup, wipe the server, reinstall fresh, restore"
db "update users set name = 'marker-before-backup' where id = 0" >/dev/null
rm -rf /root/coolify-backups
"$KIT_DIR/scripts/backup.sh"
archive="$(compgen -G '/root/coolify-backups/coolify-backup-*.tar.gz' | head -n1)"
assert "backup archive exists" test -f "$archive"
assert "backup archive is root-only" test "$(stat -c '%a' "$archive")" = "600"
cp "$archive" "$WORK/"
archive="$WORK/$(basename "$archive")"
db "update users set name = 'marker-after-backup' where id = 0" >/dev/null
old_app_key="$(env_file_get APP_KEY)"

wipe_coolify
prepare_ipv4_only "$TO_VERSION"
write_config "$TO_VERSION" "$password_b"
"$KIT_DIR/scripts/install.sh"
assert "fresh install has a new APP_KEY" test "$(env_file_get APP_KEY)" != "$old_app_key"
assert "fresh install uses the new password" can_login "$ADMIN_EMAIL" "$password_b"

"$KIT_DIR/scripts/restore.sh" "$archive" --yes
assert "APP_KEY restored from backup" test "$(env_file_get APP_KEY)" = "$old_app_key"
assert "data is from the backup (marker row)" test "$(db 'select name from users where id = 0')" = "marker-before-backup"
assert "ORIGINAL admin password works after restore" can_login "$ADMIN_EMAIL" "$password_a"
assert_fails "fresh-install password no longer works" can_login "$ADMIN_EMAIL" "$password_b"
assert "Coolify can still reach its host over SSH with the restored key" coolify_can_reach_host
assert "sign-up is still closed after restore" signup_page_closed
assert "API token from the original server works after restore" test "$(api GET /version)" = "$TO_VERSION"
assert_fails "test app is gone after the wipe (not yet redeployed)" test_app_answers_now
assert "restored app redeploys through the API" redeploy_test_app
assert "restored app is served through the proxy again" test_app_reachable

step "Final verification"
EXPECTED_VERSION="$TO_VERSION" "$KIT_DIR/scripts/verify.sh"

rm -f "$KIT_DIR/config/coolify.env"
echo
ok "ALL E2E CHECKS PASSED"
