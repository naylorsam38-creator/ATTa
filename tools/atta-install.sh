#!/usr/bin/env bash
# ATTa target setup, stage 1: Coolify on THIS machine, only if it is missing.
#
# - Refuses to run on any instance except the confirmed 500 GB target.
# - If Coolify is already installed, it is left exactly as it is (no reinstall,
#   no upgrade) and only its health is reported.
# - If Coolify is missing, the official Coolify installer is run once.
# - Never deletes containers, volumes or data. ATTa itself is stage 2
#   (it needs the ATTa-v121-collect-all-adaptive.zip bundle on the server).
#
# Usage from the laptop (PowerShell):
#   $k="$env:USERPROFILE\Downloads\airexploit-key.pem"; (Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/naylorsam38-creator/ATTa/claude/move-atta-to-500gb-ec2-fkvkit/tools/atta-install.sh).Content | ssh -o StrictHostKeyChecking=accept-new -i $k ubuntu@16.26.56.70 "tr -d '\r' | sudo bash -s"
#
# DRY_RUN=1 prints the decision without installing anything.

set -uo pipefail
export LC_ALL=C
TARGET_ID="i-0e8fa0a292a5a63d4"
LOG=/var/log/atta-install.log
CDIR="${COOLIFY_DIR:-/data/coolify}"   # overridable for testing only
say() { printf '[atta-install] %s\n' "$*" | tee -a "$LOG"; }

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
touch "$LOG" 2>/dev/null || LOG=/dev/null

# 1. Identity guard - never touch any other machine.
TOKEN=$(curl -sf --noproxy '*' -m 3 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)
IID=$(curl -sf --noproxy '*' -m 3 ${TOKEN:+-H "X-aws-ec2-metadata-token: $TOKEN"} http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null || echo unknown)
say "instance: $IID"
if [ "$IID" != "$TARGET_ID" ] && [ "${ALLOW_ANY_HOST:-0}" != 1 ]; then
  say "STOP: this is not $TARGET_ID. Nothing changed."; exit 2
fi

# 2. Facts before any change.
say "os: $(. /etc/os-release; echo "$PRETTY_NAME")  cpus: $(nproc)  ram: $(free -h | awk '/^Mem:/{print $2}')"
say "root fs: $(df -h / | awk 'NR==2{print $2" size, "$4" free"}')"
lsblk -d -o NAME,SIZE,TYPE | tee -a "$LOG"
FREE_GB=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
if [ "${FREE_GB:-0}" -lt 30 ]; then
  say "STOP: only ${FREE_GB}G free on / - Coolify needs at least 30G. Nothing changed."; exit 3
fi

coolify_present() {
  [ -f "$CDIR"/source/.env ] || { command -v docker >/dev/null && docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx coolify; }
}
coolify_health() {
  local code i
  for i in $(seq 1 "${1:-1}"); do
    code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/health || true)
    [ "$code" = 200 ] && { echo 200; return 0; }
    [ "$i" -lt "${1:-1}" ] && sleep 10
  done
  echo "${code:-000}"; return 1
}

# 3. Coolify: keep if present, install only if missing.
if coolify_present; then
  VER=$(grep -E '^APP_VERSION=' "$CDIR"/source/.env 2>/dev/null | cut -d= -f2)
  IMG=$(docker inspect coolify --format '{{.Config.Image}}' 2>/dev/null || echo "not running")
  say "Coolify ALREADY INSTALLED - keeping it (no reinstall). version=${VER:-?} image=$IMG"
  ACTION=kept
else
  say "Coolify NOT installed - installing the official current release on this machine."
  if [ "${DRY_RUN:-0}" = 1 ]; then say "DRY_RUN: would run the official installer"; exit 0; fi
  curl -fsSL https://cdn.coollabs.io/coolify/install.sh -o /tmp/coolify-install.sh || { say "STOP: could not download Coolify installer"; exit 4; }
  bash /tmp/coolify-install.sh 2>&1 | tee -a "$LOG"
  ACTION=installed
fi

# 4. Verify - do not claim success without it.
HC=$(coolify_health 18)
say "Coolify API health (127.0.0.1:8000/api/health): $HC"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>&1 | grep -Ei 'NAMES|coolify' | tee -a "$LOG"
PUB=$(curl -sf --noproxy '*' -m 3 ${TOKEN:+-H "X-aws-ec2-metadata-token: $TOKEN"} http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "?")
if [ "$HC" = 200 ]; then
  say "RESULT: Coolify $ACTION and HEALTHY. Dashboard: http://$PUB:8000 (port 8000 is open to your IP only)."
  [ "$ACTION" = installed ] && say "NEXT: open the dashboard now and create the admin account before anyone else can."
  exit 0
else
  say "RESULT: Coolify $ACTION but NOT healthy (health=$HC). Log: $LOG"; exit 5
fi
