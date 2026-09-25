#!/usr/bin/env bash
# Seed the air-gapped testbed registry (a docker.io mirror) with images pulled on the session host.
# usage: seed-image.sh IMAGE[:TAG] ...   (Docker Hub names only, e.g. nginx:alpine, coollabsio/coolify:4.3.23)
set -u
for img in "$@"; do
  name="${img%%:*}"; tag="${img#*:}"; [ "$tag" = "$img" ] && tag=latest
  case "$name" in */*) repo="$name" ;; *) repo="library/$name" ;; esac
  if docker pull -q "$img" >/dev/null 2>&1 && docker tag "$img" "localhost:5000/$repo:$tag" && docker push -q "localhost:5000/$repo:$tag" >/dev/null 2>&1; then
    echo "SEEDED $repo:$tag"
  else
    echo "SEED_FAILED $img"
  fi
done
