#!/usr/bin/env bash
# Installed on the ATTa server as /usr/local/sbin/atta-https-retry, run every 2 minutes by
# atta-https-retry.timer until HTTPS works for every ATTa domain, then the timer disables itself.
# Waits for public DNS to point at this server, then makes Coolify's Traefik (re)request the
# Let's Encrypt certificate by re-publishing ATTa's route file; restarts only the proxy if needed.
# ATTa itself is never restarted, so a running library qualification is not interrupted.
set -u
DOMAINS="airexploit.com www.airexploit.com"
ROUTE=/data/coolify/proxy/dynamic/atta.yaml
STATE=/var/lib/atta-https-retry; mkdir -p "$STATE"
LOG=/var/log/atta-https-retry.log
log() { echo "$(date -u +%FT%TZ) $*" >>"$LOG"; }
TOKEN=$(curl -sf -m 3 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
MYIP=$(curl -sf -m 3 -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4)
resolve() { curl -sf -m 8 -H 'accept: application/dns-json' "https://cloudflare-dns.com/dns-query?name=$1&type=A" | jq -r '[.Answer[]? | select(.type==1) | .data] | join(",")'; }
all_dns=1 all_https=1
for d in $DOMAINS; do
  ip=$(resolve "$d"); [ "$ip" = "$MYIP" ] || all_dns=0
  if curl -fsS -m 10 -o /dev/null "https://$d/health"; then h=ok; else h=no; all_https=0; fi
  log "$d dns=${ip:-none} (want $MYIP) https=$h"
done
if [ "$all_https" = 1 ]; then
  log "HTTPS verified for all domains - done"; echo verified >"$STATE/status"
  systemctl disable --now atta-https-retry.timer >/dev/null 2>&1; exit 0
fi
if [ "$all_dns" = 0 ]; then echo "waiting-for-dns" >"$STATE/status"; exit 0; fi
n=$(cat "$STATE/attempts" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" >"$STATE/attempts"
echo "dns-ok-attempt-$n" >"$STATE/status"
if [ -f "$ROUTE" ]; then cp -p "$ROUTE" "$ROUTE.tmp" && mv -f "$ROUTE.tmp" "$ROUTE"; log "attempt $n: route re-published"; fi
if [ $((n % 3)) -eq 0 ]; then docker restart coolify-proxy >/dev/null 2>&1 && log "attempt $n: coolify-proxy restarted"; fi
