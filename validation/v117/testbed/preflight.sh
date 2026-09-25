#!/usr/bin/env bash
# Preflight the test corpus on the session host (not ATTa): offline build + run + probe each Dockerfile app.
A="$1"; shift
for app in "$@"; do
  d="$A/$app"; t="preflight/$app"
  if ! out=$(docker build --network none -q -t "$t" "$d" 2>&1); then echo "$app BUILD_FAIL: $(echo "$out" | grep -E 'ERR|error|npm' | tail -2 | tr '\n' ' ' | cut -c1-220)"; continue; fi
  port=$(grep -m1 -oE 'EXPOSE [0-9]+' "$d/Dockerfile" | awk '{print $2}')
  cid=$(docker run -d -e PORT="$port" "$t"); ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$cid")
  code=000; for i in $(seq 1 20); do code=$(curl -s --noproxy '*' -o /dev/null -m 2 -w '%{http_code}' "http://$ip:$port/"); [ "$code" != 000 ] && break; sleep 1; done
  echo "$app BUILD_OK port=$port http=$code state=$(docker inspect -f '{{.State.Status}}' "$cid") log=$(docker logs "$cid" 2>&1 | tail -1 | cut -c1-80)"
  docker rm -f "$cid" >/dev/null
done
