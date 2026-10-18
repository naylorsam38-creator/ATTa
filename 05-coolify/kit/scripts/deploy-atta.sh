#!/usr/bin/env bash
# Puts ATTa on this Coolify server, run BY Coolify. Run after scripts/install.sh.
#
# Usage: sudo ./scripts/deploy-atta.sh --domain atta.example.com[,www.atta.example.com] [--atta-dir PATH] [--no-build]
#
#   1. Connects ATTa to this Coolify (connect-atta.sh: API on, read+write+deploy token).
#   2. Builds the ATTa image (atta:v121) from the ATTa folder this kit ships in.
#   3. Blocks app containers from the cloud metadata service (same rule ATTa's own installer adds).
#   4. Creates (or updates and restarts) the Coolify service "atta" from docker-compose.coolify.yml.
#   5. Waits for ATTa's site to answer on port 8787.
#   6. (v118) With --domain: serves ATTa at https://<domain> through Coolify's own proxy (Traefik,
#      Let's Encrypt certificate), HTTP redirected to HTTPS, then proves the HTTPS address answers.
#      Point the domain's DNS A record at this server first. Without --domain, ATTa is only on
#      http://<server>:8787 and stage 1 of the goal (domain + HTTPS) is NOT done.
#
# From then on ATTa does the rest by itself: on its first start it fetches every app on its
# app list, builds each one, checks it (six stages, the last in a real Chromium browser) and
# gives every app that passes its own Coolify service. Apps asked for later go the same way.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
enable_error_trap

ATTA_DIR="${ATTA_DIR:-$(cd "$SCRIPT_DIR/../../.." 2>/dev/null && pwd || true)}"
ATTA_IMAGE="${ATTA_IMAGE:-atta:v121}"
ATTA_ROOT="${ATTA_ROOT:-/srv/app-builder}"
ATTA_PORT="${ATTA_PORT:-8787}"
APP_PORT="${APP_PORT:-8000}"
OUT_DIR="${OUT_DIR:-/root/atta-coolify}"
SERVICE_NAME="${ATTA_SERVICE_NAME:-atta}"
ATTA_DOMAIN="${ATTA_DOMAIN:-}"
PROXY_DYNAMIC_DIR="${PROXY_DYNAMIC_DIR:-$COOLIFY_DATA_DIR/proxy/dynamic}"
PROJECT_NAME="${ATTA_PROJECT_NAME:-ATTa}"
build=true

while [ $# -gt 0 ]; do
    case "$1" in
    --atta-dir)
        ATTA_DIR="${2:?--atta-dir needs a value}"
        shift 2
        ;;
    --domain)
        ATTA_DOMAIN="${2:?--domain needs a value}"
        shift 2
        ;;
    --no-build)
        build=false
        shift
        ;;
    -h | --help)
        sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    *) die "Unknown option: $1 (see --help)" ;;
    esac
done

[ -n "$ATTA_DIR" ] && [ -f "$ATTA_DIR/Dockerfile" ] && [ -f "$ATTA_DIR/docker-compose.coolify.yml" ] ||
    die "No ATTa folder with a Dockerfile at '${ATTA_DIR:-?}'. Pass --atta-dir /path/to/ATTa"
command -v jq >/dev/null 2>&1 || die "jq is not installed (apt-get install -y jq)"
# --domain takes one host name or a comma-separated list (e.g. example.com,www.example.com): every
# name gets the same route and certificate. The first one is the address ATTa is announced at.
ATTA_DOMAINS=()
if [ -n "$ATTA_DOMAIN" ]; then
    IFS=',' read -r -a _doms <<<"$ATTA_DOMAIN"
    for d in "${_doms[@]}"; do
        d="${d// /}"; d="${d#https://}"; d="${d#http://}"; d="${d%%/*}"
        [ -n "$d" ] || continue
        printf '%s' "$d" | grep -Eq '^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$' ||
            die "--domain '$d' is not a plain host name (e.g. atta.example.com)"
        ATTA_DOMAINS+=("$d")
    done
    [ "${#ATTA_DOMAINS[@]}" -gt 0 ] || die "--domain has no host name in it"
    ATTA_DOMAIN="${ATTA_DOMAINS[0]}"
fi
require_root
[ -f "$COOLIFY_ENV_FILE" ] || die "Coolify is not installed here. Run scripts/install.sh first."

# 0. Docker Hub mirror: shared fix for HTTP 429 rate limits, before the image build and before ATTa
#    pulls any app's images. Restarting Docker restarts Coolify, so wait for its API afterwards.
ensure_registry_mirror || warn "Continuing WITHOUT the Docker Hub mirror: expect HTTP 429 from Docker Hub under load"
wait_for_coolify_api || die "Coolify's API is not answering on port $APP_PORT. Check: docker ps; docker logs coolify"

# 1. Token. ATTa runs on this same server with host networking, so it reaches Coolify on loopback.
url="http://127.0.0.1:$APP_PORT"
OUT_DIR="$OUT_DIR" APP_PORT="$APP_PORT" "$SCRIPT_DIR/connect-atta.sh" --url "$url"
conn="$OUT_DIR/atta.env"
token="$(sed -n 's/^COOLIFY_TOKEN="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$conn" | tail -n1)"
[ -n "$token" ] || die "connect-atta.sh wrote no token to $conn"

# ATTa reads these two lines on every start (they win over its .env). Root-only.
umask 077
mkdir -p "$ATTA_ROOT"
chmod 700 "$ATTA_ROOT"
install -m 600 "$conn" "$ATTA_ROOT/.coolify-connection"
ok "ATTa will reach Coolify at $url"

# 2. Image.
if [ "$build" = true ]; then
    info "Building $ATTA_IMAGE from $ATTA_DIR (first build downloads Chromium; allow several minutes)"
    docker build -t "$ATTA_IMAGE" "$ATTA_DIR" || die "The ATTa image did not build (see the output above)"
fi
docker image inspect "$ATTA_IMAGE" >/dev/null 2>&1 || die "Image $ATTA_IMAGE is not on this server"
ok "Image $ATTA_IMAGE is ready"

# 3. Metadata block (DOCKER-USER), re-applied whenever Docker restarts.
cat >/usr/local/sbin/atta-block-metadata <<'BLOCKSH'
#!/usr/bin/env bash
set -u
ok=0
if command -v iptables >/dev/null 2>&1; then
  iptables -N DOCKER-USER 2>/dev/null || true
  if iptables -C DOCKER-USER -d 169.254.169.254/32 -j DROP 2>/dev/null || iptables -I DOCKER-USER 1 -d 169.254.169.254/32 -j DROP; then ok=1; fi
fi
if command -v ip6tables >/dev/null 2>&1; then
  ip6tables -N DOCKER-USER 2>/dev/null || true
  ip6tables -C DOCKER-USER -d fd00:ec2::254/128 -j DROP 2>/dev/null || ip6tables -I DOCKER-USER 1 -d fd00:ec2::254/128 -j DROP || true
fi
[ "$ok" = 1 ] || { echo "atta-block-metadata: iptables not available; NOT blocked" >&2; exit 1; }
BLOCKSH
chmod 755 /usr/local/sbin/atta-block-metadata
if [ -d /run/systemd/system ]; then
    cat >/etc/systemd/system/atta-block-metadata.service <<'UNIT'
[Unit]
Description=ATTa: block container access to the cloud metadata service
After=docker.service
PartOf=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/atta-block-metadata
[Install]
WantedBy=docker.service
UNIT
    systemctl daemon-reload
    systemctl enable atta-block-metadata.service >/dev/null 2>&1 || true
    systemctl restart atta-block-metadata.service || warn "Could not block the metadata service for containers"
else
    /usr/local/sbin/atta-block-metadata || warn "Could not block the metadata service for containers"
fi

# 4. The Coolify service.
api() { # api METHOD PATH [JSON] -> sets API_BODY and API_CODE (no subshell, so both survive)
    local out
    out="$(curl -sS -m 30 -w '\n%{http_code}' -X "$1" -H "Authorization: Bearer $token" \
        -H 'Accept: application/json' -H 'Content-Type: application/json' ${3:+-d "$3"} \
        "$url/api/v1$2")" || die "Coolify API did not answer ($1 $2)"
    API_CODE="${out##*$'\n'}"
    API_BODY="${out%$'\n'*}"
}
api GET /servers
servers="$API_BODY"
[ "$API_CODE" = 200 ] || die "Could not list Coolify servers (HTTP $API_CODE)"
server="$(jq -r '(if type=="array" then . else (.data // []) end) as $s
    | ([$s[] | select((.name // "") | ascii_downcase == "localhost")] + $s)[0].uuid // empty' <<<"$servers")"
[ -n "$server" ] || die "Coolify lists no server to run ATTa on"

api GET /projects
projects="$API_BODY"
project="$(jq -r --arg n "$PROJECT_NAME" '(if type=="array" then . else (.data // []) end)
    | map(select(.name == $n))[0].uuid // empty' <<<"$projects")"
if [ -z "$project" ]; then
    api POST /projects "$(jq -nc --arg n "$PROJECT_NAME" '{name:$n, description:"ATTa itself"}')"
    body="$API_BODY"
    project="$(jq -r '.uuid // empty' <<<"$body")"
    [ -n "$project" ] || die "Could not create the Coolify project $PROJECT_NAME (HTTP $API_CODE): $body"
fi

compose_b64="$(sed "s#image: atta:v121#image: $ATTA_IMAGE#; s#ATTA_IMAGE: atta:v121#ATTA_IMAGE: $ATTA_IMAGE#; s#/srv/app-builder:/srv/app-builder#$ATTA_ROOT:$ATTA_ROOT#; s#APP_BUILDER_ROOT: /srv/app-builder#APP_BUILDER_ROOT: $ATTA_ROOT#; s#APP_BUILDER_PORT: \"8787\"#APP_BUILDER_PORT: \"$ATTA_PORT\"#" \
    "$ATTA_DIR/docker-compose.coolify.yml" | base64 -w0)"

api GET /services
services="$API_BODY"
svc="$(jq -r --arg n "$SERVICE_NAME" '(if type=="array" then . else (.data // []) end)
    | map(select(.name == $n))[0].uuid // empty' <<<"$services")"
if [ -n "$svc" ]; then
    info "Updating the existing Coolify service $SERVICE_NAME ($svc) and restarting it"
    api PATCH "/services/$svc" "$(jq -nc --arg c "$compose_b64" '{docker_compose_raw:$c}')"
    body="$API_BODY"
    [ "$API_CODE" = 200 ] || [ "$API_CODE" = 201 ] || die "Coolify refused the update (HTTP $API_CODE): $body"
    api POST "/deploy?uuid=$svc&force=false"
    body="$API_BODY"
    [ "$API_CODE" = 200 ] || die "Coolify did not restart $SERVICE_NAME (HTTP $API_CODE): $body"
else
    info "Creating the Coolify service $SERVICE_NAME"
    api POST /services "$(jq -nc --arg p "$project" --arg s "$server" --arg n "$SERVICE_NAME" --arg c "$compose_b64" \
        '{project_uuid:$p, server_uuid:$s, environment_name:"production", name:$n,
          description:"ATTa: fetches, builds and qualifies apps, then gives each its own Coolify service",
          docker_compose_raw:$c, instant_deploy:true}')"
    body="$API_BODY"
    svc="$(jq -r '.uuid // empty' <<<"$body")"
    [ -n "$svc" ] || die "Coolify refused to create $SERVICE_NAME (HTTP $API_CODE): $body"
fi
ok "Coolify service $SERVICE_NAME: $svc"

# 5. Up?
info "Waiting for ATTa on port $ATTA_PORT (up to 5 minutes)"
up=false
for _ in $(seq 1 100); do
    if curl -fsS -o /dev/null -m 5 "http://127.0.0.1:$ATTA_PORT/health"; then up=true; break; fi
    sleep 3
done
[ "$up" = true ] || die "ATTa did not answer on port $ATTA_PORT. Look at its logs in Coolify (service $SERVICE_NAME)."
ok "ATTa is up on port $ATTA_PORT"

# 6. Domain + HTTPS through Coolify's proxy (v118). ATTa runs with host networking, so Coolify's Traefik
#    can't route to it by container labels; a file-provider route does it. Coolify's Traefik watches
#    <data>/proxy/dynamic/, has the "letsencrypt" resolver and maps host.docker.internal to this host
#    (checked in the supplied Coolify 4.3.23 source: bootstrap/helpers/proxy.php).
if [ -n "$ATTA_DOMAIN" ]; then
    [ -d "$PROXY_DYNAMIC_DIR" ] || die "Coolify's proxy folder $PROXY_DYNAMIC_DIR is missing. Is Coolify's proxy (Traefik) running?"
    tmp="$(mktemp "$PROXY_DYNAMIC_DIR/.atta.XXXXXX")"
    host_rule=""
    for d in "${ATTA_DOMAINS[@]}"; do host_rule="${host_rule:+$host_rule || }Host(\`$d\`)"; done
    cat >"$tmp" <<ROUTE
# Written by ATTa deploy-atta.sh (v118). ATTa at https://${ATTA_DOMAINS[*]:-$ATTA_DOMAIN}. Delete this file to remove the route.
http:
  routers:
    atta-http:
      rule: "${host_rule:-Host(\`$ATTA_DOMAIN\`)}"
      entryPoints: [http]
      middlewares: [atta-to-https]
      service: atta
    atta-https:
      rule: "${host_rule:-Host(\`$ATTA_DOMAIN\`)}"
      entryPoints: [https]
      service: atta
      tls:
        certResolver: letsencrypt
  middlewares:
    atta-to-https:
      redirectScheme:
        scheme: https
        permanent: true
  services:
    atta:
      loadBalancer:
        servers:
          - url: "http://host.docker.internal:$ATTA_PORT"
ROUTE
    chmod 644 "$tmp"
    mv -f "$tmp" "$PROXY_DYNAMIC_DIR/atta.yaml"
    ok "Coolify's proxy now routes ${ATTA_DOMAINS[*]} (https) to ATTa"
    for d in "${ATTA_DOMAINS[@]}"; do
        info "Waiting for https://$d (certificate from Let's Encrypt; DNS must already point here)"
        https_ok=false
        for _ in $(seq 1 60); do
            if curl -fsS -o /dev/null -m 10 "https://$d/health"; then https_ok=true; break; fi
            sleep 5
        done
        if [ "$https_ok" != true ]; then
            warn "https://$d does not answer with a valid certificate yet."
            warn "Check: dig +short $d (must be this server's IP), ports 80/443 open, then re-run this script."
            die "Stage 1 is NOT done: no working HTTPS address yet for $d."
        fi
        ok "ATTa is live: https://$d/ (valid certificate)"
    done
    echo "Close port $ATTA_PORT to the internet (AWS security group): people use https://$ATTA_DOMAIN/ only."
    echo "Stage 1 is proven only when a person opens https://$ATTA_DOMAIN/ in a browser, logs in and reaches the Front Door."
else
    warn "No --domain given: ATTa is only on http://<this-server>:$ATTA_PORT (plain HTTP, logins unencrypted)."
    warn "Stage 1 needs a domain and HTTPS: re-run with --domain atta.yourdomain.com"
fi
echo "Logins (admin + testers): $ATTA_ROOT/TEST_ACCOUNTS.txt (root-only). Hand them out, then delete it."
echo "ATTa is now fetching and checking every app on its list. Each one that passes appears in"
echo "Coolify under the project 'ATTa Apps' as its own service, with its own address."
