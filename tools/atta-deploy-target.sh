#!/usr/bin/env bash
# ATTa v121 collect-all-adaptive: full deploy on the confirmed 500 GB target, through Coolify.
#
# Runs ON the target as root (the laptop copies the bundle over and starts this; see
# tools/deploy-from-laptop.ps1). Uses only the bundle's own machinery:
#   Coolify   : kept exactly as it is if already installed; otherwise the bundle's
#               05-coolify/kit/scripts/install.sh (pinned Coolify 4.3.23).
#   ATTa      : the bundle's 05-coolify/kit/scripts/deploy-atta.sh (builds atta:v121, runs it as
#               the Coolify service "atta", routes the domain with Let's Encrypt).
#   Library   : ATTa itself - on first start it reads 04-deployment/upstream_apps.json, fetches every
#               VERIFIED entry, qualifies them in parallel and hands each qualified app to Coolify.
# Plus one shared fix (tools/patches/v121-kit-registry-mirror-domains.patch): Docker Hub mirror on the
# host after Coolify's installer (the HTTP 429 cause), and --domain accepting apex + www.
#
# Never deletes containers, volumes, Coolify data or ATTa data. Safe to re-run.
#
# Usage (root): atta-deploy-target.sh [--dry-run] --zip /root/ATTa-v121-collect-all-adaptive.zip \
#                 --patch /root/v121-kit.patch --domain airexploit.com,www.airexploit.com \
#                 --admin-email you@yourdomain.com [--admin-user sam]
set -uo pipefail
export LC_ALL=C DEBIAN_FRONTEND=noninteractive

TARGET_INSTANCE="${TARGET_INSTANCE:-i-0e8fa0a292a5a63d4}"
BUNDLE_SHA256="${BUNDLE_SHA256:-ccca34de3988653d4af13367dd86b5ae0297c16faffa5f784a2fbf858b31c601}"
BASE="${ATTA_BASE:-/opt/atta}"
LOG=/var/log/atta-deploy-target.log
ZIP="" PATCH="" DOMAINS="" ADMIN_EMAIL="" ADMIN_USER="sam" DRY_RUN="${DRY_RUN:-0}"
MIN_FREE_GB="${MIN_FREE_GB:-60}"

while [ $# -gt 0 ]; do
    case "$1" in
    --zip) ZIP="$2"; shift 2 ;;
    --patch) PATCH="$2"; shift 2 ;;
    --domain) DOMAINS="$2"; shift 2 ;;
    --admin-email) ADMIN_EMAIL="$2"; shift 2 ;;
    --admin-user) ADMIN_USER="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown option $1"; exit 64 ;;
    esac
done

mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
step() { printf '\n==================== %s ====================\n' "$*"; }
say() { printf '[deploy] %s\n' "$*"; }
stop() { printf '\n[deploy] STOPPED: %s\n[deploy] Nothing was deleted. Full log: %s\n' "$*" "$LOG"; exit 1; }

[ "$(id -u)" -eq 0 ] || stop "run as root (sudo)"
[ -n "$ZIP" ] && [ -f "$ZIP" ] || stop "bundle zip not found: '${ZIP:-?}'"
[ -n "$PATCH" ] && [ -f "$PATCH" ] || stop "kit patch not found: '${PATCH:-?}'"
[ -n "$DOMAINS" ] || stop "--domain is required"

step "1/7 TARGET IDENTITY"
TOKEN=$(curl -sf --noproxy '*' -m 3 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300' 2>/dev/null || true)
md() { curl -sf --noproxy '*' -m 3 ${TOKEN:+-H "X-aws-ec2-metadata-token: $TOKEN"} "http://169.254.169.254/latest/meta-data/$1" 2>/dev/null || echo "?"; }
IID=$(md instance-id); PUB=$(md public-ipv4)
say "instance=$IID type=$(md instance-type) az=$(md placement/availability-zone) public=$PUB private=$(md local-ipv4)"
[ "$IID" = "$TARGET_INSTANCE" ] || stop "this is $IID, not the confirmed target $TARGET_INSTANCE"
say "cpus=$(nproc) ram=$(free -h | awk '/^Mem:/{print $2}') load=$(cut -d' ' -f1-3 /proc/loadavg)"
lsblk -d -o NAME,SIZE,TYPE | sed 's/^/[deploy]   /'
df -h / | sed 's/^/[deploy]   /'
FREE_GB=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
[ "${FREE_GB:-0}" -ge "$MIN_FREE_GB" ] || stop "only ${FREE_GB}G free on / (need ${MIN_FREE_GB}G+). If the EBS volume is bigger than the filesystem, it needs growpart/resize2fs first - not done automatically"

step "2/7 PREREQUISITES"
need=()
for c in jq unzip curl patch ss; do command -v "$c" >/dev/null 2>&1 || need+=("$c"); done
if [ "${#need[@]}" -gt 0 ]; then
    pk=(); for c in "${need[@]}"; do case "$c" in ss) pk+=(iproute2) ;; *) pk+=("$c") ;; esac; done
    say "installing: ${pk[*]}"
    apt-get update -qq && apt-get install -y -qq "${pk[@]}" >/dev/null || stop "apt-get install ${pk[*]} failed"
fi
say "jq unzip curl patch ss: present"

step "3/7 BUNDLE"
got=$(sha256sum "$ZIP" | cut -d' ' -f1)
[ "$got" = "$BUNDLE_SHA256" ] || stop "bundle checksum $got does not match the expected ATTa-v121-collect-all-adaptive.zip ($BUNDLE_SHA256)"
say "bundle checksum OK ($got)"
mkdir -p "$BASE/bundles" "$BASE/releases"
PRISTINE="$BASE/bundles/ATTa-v121-collect-all-adaptive-${got:0:12}.zip"
[ -f "$PRISTINE" ] || install -m 444 "$ZIP" "$PRISTINE"
say "untouched copy of the bundle: $PRISTINE (read-only)"
REL="$BASE/releases/v121-collect-all-adaptive-${got:0:12}"
if [ -d "$REL/ATTa" ]; then
    say "release folder exists, reusing it (not overwritten): $REL"
else
    tmpd=$(mktemp -d "$BASE/releases/.unpack.XXXXXX")
    unzip -q "$PRISTINE" -d "$tmpd" || stop "unzip failed"
    [ -f "$tmpd/ATTa/Dockerfile" ] || stop "bundle has no ATTa/Dockerfile"
    mv "$tmpd" "$REL"
    say "unpacked to $REL"
fi
ATTA_DIR="$REL/ATTa"
KIT="$ATTA_DIR/05-coolify/kit"
if patch -d "$ATTA_DIR" -p1 -R --dry-run -s -f <"$PATCH" >/dev/null 2>&1; then
    say "shared fix already applied to this release"
else
    patch -d "$ATTA_DIR" -p1 --dry-run -s -f <"$PATCH" >/dev/null || stop "shared fix does not apply cleanly to this bundle"
    patch -d "$ATTA_DIR" -p1 -s -f <"$PATCH" || stop "applying the shared fix failed"
    say "shared fix applied (Docker Hub mirror after Coolify install; apex + www domain)"
fi
chmod +x "$KIT"/scripts/*.sh
APPS=$(jq '[.entries[]? | select((.status // "") == "VERIFIED" and ((.repo // .repository // "") | length > 0)) | (.repo // .repository) | ascii_downcase] | unique | length' "$ATTA_DIR/04-deployment/upstream_apps.json" 2>/dev/null || echo "?")
ALL=$(jq '[.entries[]?] | length' "$ATTA_DIR/04-deployment/upstream_apps.json" 2>/dev/null || echo "?")
say "configured app list: $ALL entries, $APPS unique VERIFIED repositories (the rest are unresolved and are not guessed)"
if [ "$DRY_RUN" = 1 ]; then
    say "DRY RUN: stopping before Coolify/ATTa. Would now: keep or install Coolify, verify it, run deploy-atta.sh --domain $DOMAINS"
    exit 0
fi

step "4/7 COOLIFY"
# shellcheck source=/dev/null
. "$KIT/scripts/lib/common.sh"
if [ -f "$COOLIFY_ENV_FILE" ]; then
    say "Coolify is already installed - keeping it exactly as it is (no reinstall, no upgrade)"
    say "running version: $(coolify_running_version 2>/dev/null || echo ?)"
    [ "$(coolify_running_version 2>/dev/null)" = "4.3.23" ] || say "NOTE: ATTa's hand-off was verified against 4.3.23; continuing with the installed version"
else
    [ -n "$ADMIN_EMAIL" ] || stop "Coolify is not installed and --admin-email was not given (Coolify's admin account needs a real address)"
    cfg="$KIT/config/coolify.env"
    umask 077
    {
        echo "COOLIFY_VERSION=4.3.23"
        echo "ROOT_USERNAME=$ADMIN_USER"
        echo "ROOT_USER_EMAIL=$ADMIN_EMAIL"
        echo "ROOT_USER_PASSWORD="
        echo "CREDENTIALS_FILE=/root/coolify-admin-credentials.txt"
        echo "AUTOUPDATE=false"
        echo "COOLIFY_SOURCE_ZIP=$ATTA_DIR/05-coolify/coolify-main.zip"
    } >"$cfg"
    umask 022
    say "Coolify not installed - installing with the bundle's kit (Coolify 4.3.23, pinned source zip)"
    "$KIT/scripts/install.sh" --config "$cfg" || stop "Coolify install failed (see above). Safe to re-run."
fi
"$KIT/scripts/verify.sh" || stop "Coolify is not healthy (see the checks above) - not deploying ATTa onto it"
say "Coolify healthy"

step "5/7 ATTa THROUGH COOLIFY (+ domain, HTTPS)"
"$KIT/scripts/deploy-atta.sh" --atta-dir "$ATTA_DIR" --domain "$DOMAINS" || stop "deploy-atta.sh did not finish (see above). Safe to re-run."

step "6/7 END-TO-END CHECKS"
pass=0 failc=0
chk() { if eval "$2" >/dev/null 2>&1; then say "PASS  $1"; pass=$((pass + 1)); else say "FAIL  $1"; failc=$((failc + 1)); fi; }
chk "Coolify API /api/health = OK" '[ "$(curl -fsS -m 5 http://127.0.0.1:8000/api/health)" = OK ]'
chk "ATTa /health on 127.0.0.1:8787" 'curl -fsS -m 5 http://127.0.0.1:8787/health'
IFS=',' read -r -a DL <<<"$DOMAINS"
for d in "${DL[@]}"; do
    d="${d// /}"; [ -n "$d" ] || continue
    chk "DNS $d -> $PUB" '[ "$(getent ahostsv4 '"$d"' | awk "NR==1{print \$1}")" = "$PUB" ]'
    chk "http://$d redirects to https" 'curl -sS -m 10 -o /dev/null -w "%{http_code} %{redirect_url}" http://'"$d"'/ | grep -Eq "^30[178] https://"'
    chk "https://$d/health valid certificate" 'curl -fsS -m 10 https://'"$d"'/health'
done
chk "Docker Hub mirror active" 'docker info --format "{{json .RegistryConfig.Mirrors}}" | grep -q mirror.gcr.io'

step "7/7 WHAT IS RUNNING"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' | sed 's/^/[deploy]   /'
df -h / | awk 'NR==2{print "[deploy] disk: "$2" total, "$4" free"}'
say "checks: $pass passed, $failc failed"
say "ATTa: https://${DL[0]// /}/   (logins: sudo cat /srv/app-builder/TEST_ACCOUNTS.txt)"
say "Coolify dashboard: http://$PUB:8000   (first login: sudo cat /root/coolify-admin-credentials.txt, if it was installed now)"
say "ATTa is now working through the whole app list by itself; qualified apps appear in Coolify under 'ATTa Apps'."
say "Progress later: sudo ls -t /srv/app-builder/state/checklists/ | head -1 | xargs -I{} sudo cat /srv/app-builder/state/checklists/{}"
[ "$failc" -eq 0 ] || exit 2
