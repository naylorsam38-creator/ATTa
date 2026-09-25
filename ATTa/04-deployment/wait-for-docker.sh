#!/usr/bin/env bash
# ExecStartPre for services that drive Docker (v114.1). systemd's After=docker.service only orders the
# start; this waits until the daemon actually answers. Fails after ATTA_DOCKER_WAIT seconds, so systemd
# retries the service later instead of starting it against a dead daemon.
wait="${ATTA_DOCKER_WAIT:-90}"; step=2; waited=0
until docker info >/dev/null 2>&1; do
  if [ "$waited" -ge "$wait" ]; then
    echo "docker did not answer within ${wait}s" >&2; exit 1
  fi
  sleep "$step"; waited=$((waited + step))
done
