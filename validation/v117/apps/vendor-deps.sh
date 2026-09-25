#!/usr/bin/env bash
# Regenerate the vendored dependencies that are NOT committed (they are large binaries):
#   vite-react-dashboard/.npm-cache, next-notes/.npm-cache, dep-broken/.npm-cache, fastapi-service/wheels
# The apps build fully offline from these (the validation testbed is air-gapped), so run this on a machine
# with internet access before zipping the apps.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

npm_cache() {  # npm_cache APP — populate APP/.npm-cache from its package-lock.json
  (cd "$HERE/$1" && rm -rf node_modules .npm-cache \
     && npm ci --cache ./.npm-cache --no-audit --no-fund --loglevel=error >/dev/null \
     && rm -rf node_modules && echo "vendored npm cache: $1 ($(du -sh .npm-cache | cut -f1))")
}

npm_cache vite-react-dashboard
npm_cache next-notes
# dep-broken deliberately has a package.json that no longer matches its lock file; it reuses the
# vite-react-dashboard cache (same lock file), which is exactly what the broken commit would have had.
rm -rf "$HERE/dep-broken/.npm-cache" && cp -r "$HERE/vite-react-dashboard/.npm-cache" "$HERE/dep-broken/.npm-cache"
echo "vendored npm cache: dep-broken (copied from vite-react-dashboard)"

(cd "$HERE/fastapi-service" && rm -rf wheels \
   && pip download -q --only-binary=:all: --python-version 3.12 \
        --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform any \
        -d wheels -r requirements.txt \
   && echo "vendored wheels: fastapi-service ($(ls wheels | wc -l) files)")
