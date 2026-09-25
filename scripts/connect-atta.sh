#!/usr/bin/env bash
# Connects this Coolify to the ATTa APP Builder, so ATTa can hand qualified builds to Coolify.
# Run on the COOLIFY server after scripts/install.sh.
#
# Usage: sudo ./scripts/connect-atta.sh [--url http://<coolify-private-ip>:8000] [--map-apps]
#
# It does ATTa's manual Coolify-side steps (ATTa docs/COOLIFY-HANDOFF.md) for you:
#   1. Turns on Coolify's API access.
#   2. Creates an API token for ATTa with only the "deploy" and "read" permissions,
#      revoking any earlier token this script made (so re-running rotates it).
#   3. Proves the token works: it can read, and it cannot write.
#   4. Writes the two lines for ATTa's /srv/app-builder/.env to a root-only file.
#   5. With --map-apps: writes coolify_resources.json (app name -> Coolify UUID) for
#      every application Coolify has, for ATTa's <ROOT>/coolify_resources.json.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
enable_error_trap

TOKEN_NAME="atta-app-builder"
OUT_DIR="${OUT_DIR:-/root/atta-coolify}"
APP_PORT="${APP_PORT:-8000}"
url=""
map_apps=false

while [ $# -gt 0 ]; do
    case "$1" in
    --url)
        url="${2:?--url needs a value}"
        shift 2
        ;;
    --map-apps)
        map_apps=true
        shift
        ;;
    -h | --help)
        sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    *) die "Unknown option: $1 (see --help)" ;;
    esac
done

# ATTa only sends its token over https:// or plain http:// to a private address, so default
# to this server's primary (private, on a cloud VPC) address.
if [ -z "$url" ]; then
    ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}' | head -n1)"
    [ -n "$ip" ] || die "Could not work out this server's private address; pass --url http://<address>:$APP_PORT"
    url="http://$ip:$APP_PORT"
fi
url="${url%/}"
case "$url" in
http://* | https://*) ;;
*) die "--url must start with http:// or https://" ;;
esac

# Mirror ATTa's rule (coolify_handoff.token_route_ok): plain http only to a non-public address.
if [[ $url == http://* ]]; then
    host="${url#http://}"
    host="${host%%[:/]*}"
    if ! python3 - "$host" <<'PY'; then
import ipaddress, socket, sys
host = sys.argv[1]
try:
    addrs = [ipaddress.ip_address(host)]
except ValueError:
    addrs = [ipaddress.ip_address(i[4][0]) for i in socket.getaddrinfo(host, None)]
sys.exit(0 if addrs and not any(a.is_global for a in addrs) else 1)
PY
        die "$url is plain http:// to a PUBLIC address, so ATTa will refuse to send its token there. Use the dashboard's https:// domain (--url https://coolify.yourdomain.com), or a private network between the two servers (--url http://<private-ip>:$APP_PORT)."
    fi
fi

require_root
[ -f "$COOLIFY_ENV_FILE" ] || die "Coolify is not installed here. Run scripts/install.sh first."
coolify_admin_exists || die "Coolify has no admin account yet. Run scripts/install.sh first."

info "Turning on Coolify API access"
coolify_db_query "update instance_settings set is_api_enabled = true where id = 0" >/dev/null
[ "$(coolify_db_query 'select is_api_enabled from instance_settings where id = 0')" = "t" ] ||
    die "Could not turn on API access"
ok "API access is on"

info "Creating a deploy+read API token for ATTa (replacing any earlier one)"
# Coolify's own User::createToken, scoped to the root team like a token made in the UI.
# shellcheck disable=SC2016 # PHP code
token="$(docker exec coolify php artisan tinker --execute '
    $u = App\Models\User::find(0);
    $u->tokens()->where("name", "'"$TOKEN_NAME"'")->delete();
    session(["currentTeam" => App\Models\Team::find(0)]);
    echo $u->createToken("'"$TOKEN_NAME"'", ["deploy", "read"])->plainTextToken;
' 2>/dev/null | tail -n1)"
[[ $token =~ ^[0-9]+\|[A-Za-z0-9]+$ ]] || die "Coolify did not return a token"

api_code() {
    curl -sS -o /dev/null -m 15 -w '%{http_code}' -X "$1" -H "Authorization: Bearer $token" \
        -H 'Accept: application/json' -H 'Content-Type: application/json' ${3:+-d "$3"} \
        "http://127.0.0.1:$APP_PORT/api/v1$2" 2>/dev/null || true
}
[ "$(api_code GET /applications)" = "200" ] || die "The new token cannot read applications"
ok "Token can read (needed for ATTa's name -> UUID lookup)"
# Coolify answers 403 when a token lacks the needed permission. A write must be refused.
[ "$(api_code POST /projects '{"name":"atta-permission-probe"}')" = "403" ] ||
    die "The token is NOT limited: it was allowed to create a project. Refusing to hand it out."
ok "Token cannot write (least privilege)"
# The deploy permission itself is exercised by ATTa's first real hand-off; an unknown UUID
# must answer 404 "No resources found" (not 403), which proves the permission is granted.
[ "$(api_code POST '/deploy?uuid=atta-permission-probe&force=false')" = "404" ] ||
    die "The token was not allowed to call the deploy endpoint"
ok "Token can call the deploy endpoint"

umask 077
mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"
env_out="$OUT_DIR/atta.env"
{
    echo "# Add these lines to /srv/app-builder/.env on the ATTa server, then run:"
    echo "#   systemctl restart app-builder-pipeline"
    # Quoted: Coolify tokens contain '|', which a shell reading this file would treat as a
    # pipe. systemd's EnvironmentFile, ATTa's envfile.py and bash all strip the quotes.
    echo "COOLIFY_URL=\"$url\""
    echo "COOLIFY_TOKEN=\"$token\""
} >"$env_out"
chmod 600 "$env_out"
ok "ATTa settings written to $env_out (root-only)"

if [ "$map_apps" = true ]; then
    map_out="$OUT_DIR/coolify_resources.json"
    curl -fsS -m 15 -H "Authorization: Bearer $token" -H 'Accept: application/json' \
        "http://127.0.0.1:$APP_PORT/api/v1/applications" |
        jq '{apps: (map({key: .name, value: .uuid}) | from_entries)}' >"$map_out" ||
        die "Could not list Coolify applications"
    ok "App map written to $map_out ($(jq '.apps | length' "$map_out") app(s)); copy it to /srv/app-builder/coolify_resources.json"
fi

echo
echo "Next, on the ATTa server:"
echo "  1. Append $env_out to /srv/app-builder/.env and run: systemctl restart app-builder-pipeline"
echo "  2. In your cloud firewall, allow port $APP_PORT on this server ONLY from the ATTa server."
if [[ $url == http://* ]]; then
    echo "     ($url is plain http: ATTa accepts it only because it is a private address.)"
fi
echo "  3. In Coolify, name each app exactly as ATTa names it (e.g. grafana): ATTa then finds"
echo "     its UUID by itself. Or copy coolify_resources.json (--map-apps) to /srv/app-builder/."
