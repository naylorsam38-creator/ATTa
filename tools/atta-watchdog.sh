#!/usr/bin/env bash
# Installed on the ATTa server as /usr/local/sbin/atta-watchdog, run every 5 minutes by
# atta-watchdog.timer. Keeps Docker, Coolify and ATTa running without touching healthy services:
#   - Docker daemon down               -> start it
#   - a Coolify platform / ATTa service container stopped -> start it (its data and state are on volumes/disk)
#   - a container unhealthy 3 checks in a row (15 min) -> restart that one container
#   - Docker Hub mirror missing         -> put it back (kit's ensure_registry_mirror)
#   - ATTa /health or Coolify /api/health failing is logged; the container rules above act on it
#   - free disk under 10%               -> logged as ALERT (no pruning: images are ATTa's cache)
# ATTa keeps its run state under /srv/app-builder, so a restarted container resumes from it.
set -u
LOG=/var/log/atta-watchdog.log
ST=/var/lib/atta-watchdog; mkdir -p "$ST"
log() { echo "$(date -u +%FT%TZ) $*" >>"$LOG"; }
KIT=$(ls -d /opt/atta/releases/v121-collect-all-adaptive-*/ATTa/05-coolify/kit 2>/dev/null | head -1)

if ! docker info >/dev/null 2>&1; then
  log "docker not answering - starting docker"; systemctl start docker; sleep 20
  docker info >/dev/null 2>&1 || { log "ALERT docker still down"; exit 1; }
fi

# Only the Coolify platform and the ATTa service itself. ATTa's own app test containers (also named
# atta-<app>) belong to ATTa's runner and are never touched here.
atta_svc=$(docker ps -a --filter label=coolify.type=service --filter ancestor=atta:v121 --format '{{.Names}}' | grep -E '^atta-[a-z0-9]{24}$')
for c in coolify coolify-db coolify-redis coolify-realtime coolify-proxy $atta_svc; do
  st=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null) || continue
  hl=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c" 2>/dev/null)
  if [ "$st" != running ]; then
    log "$c is $st - starting it"; docker start "$c" >/dev/null 2>&1 || log "ALERT could not start $c"
    rm -f "$ST/$c.bad"; continue
  fi
  if [ "$hl" = unhealthy ]; then
    n=$(( $(cat "$ST/$c.bad" 2>/dev/null || echo 0) + 1 )); echo $n >"$ST/$c.bad"
    if [ $n -ge 3 ]; then log "$c unhealthy for $n checks - restarting it"; docker restart "$c" >/dev/null 2>&1; rm -f "$ST/$c.bad"
    else log "$c unhealthy ($n/3)"; fi
  else rm -f "$ST/$c.bad"; fi
done

a=$(curl -s -m 5 http://127.0.0.1:8787/health); [ "$a" = OK ] || log "ATTa /health: '${a:-no answer}'"
k=$(curl -s -m 5 http://127.0.0.1:8000/api/health); [ "$k" = OK ] || log "Coolify /api/health: '${k:-no answer}'"

if ! docker info --format '{{json .RegistryConfig.Mirrors}}' 2>/dev/null | grep -q mirror.gcr.io; then
  log "Docker Hub mirror missing - restoring it"
  if [ -n "$KIT" ]; then ( . "$KIT/scripts/lib/common.sh"; ensure_registry_mirror ) >>"$LOG" 2>&1; else log "ALERT kit not found"; fi
fi

free=$(df --output=pcent / | tail -1 | tr -dc 0-9); [ "$free" -lt 90 ] || log "ALERT disk ${free}% used"
echo "$(date -u +%FT%TZ) ok atta=${a:-none} coolify=${k:-none} disk=${free}%" >"$ST/last"
