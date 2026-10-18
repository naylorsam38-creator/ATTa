#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Install Coolify on this server from the supplied source (coolify-main.zip).
#
#   sudo bash 05-coolify/install-coolify.sh
#
# Uses Coolify's OWN installer from the supplied source (scripts/install.sh), unmodified,
# pinned to the version of that source (versions.json -> coolify.v4.version, 4.3.23 at the
# time of packaging) with auto-update OFF, so it stays on the version you supplied.
# The installer installs Docker if needed and runs Coolify's official release images for that
# version (from Docker Hub / ghcr.io); the server needs normal internet access.
#
# Optional, to skip the first-login sign-up screen:
#   ROOT_USERNAME=admin ROOT_USER_EMAIL=you@example.com ROOT_USER_PASSWORD='...' sudo -E bash ...
#
# Recommended: run Coolify on its OWN server. Coolify's proxy takes ports 80/443 to serve
# the apps it deploys, which clashes with the APP Builder's nginx on the same machine.
# ---------------------------------------------------------------------------
set -euo pipefail

# ===================== CONFIG — edit here, nothing below needs reading =====================
# Where the unpacked source goes.
SRC=/opt/coolify-source
# Keep Coolify on the supplied version. Set to "true" to let Coolify update itself.
AUTOUPDATE="${AUTOUPDATE:-false}"
# Seconds to wait for Coolify's health check after install.
HEALTH_WAIT=600
# ==========================================================================================

HERE="$(cd "$(dirname "$0")" && pwd)"
ZIP="$HERE/coolify-main.zip"
[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo bash $0" >&2; exit 1; }
[ -f "$ZIP" ] || { echo "Missing $ZIP" >&2; exit 1; }
command -v unzip >/dev/null 2>&1 || { (command -v apt-get >/dev/null && apt-get update -y && apt-get install -y unzip) || dnf install -y unzip; }

tmp="$(mktemp -d)"; unzip -q "$ZIP" -d "$tmp"
rm -rf "$SRC"; mv "$tmp/coolify-main" "$SRC"; rm -rf "$tmp"
VERSION="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["coolify"]["v4"]["version"])' "$SRC/versions.json" 2>/dev/null \
          || grep -m1 -o '"version": *"[^"]*"' "$SRC/versions.json" | cut -d'"' -f4)"
[ -n "$VERSION" ] || { echo "Could not read the Coolify version from $SRC/versions.json" >&2; exit 1; }
echo "Installing Coolify $VERSION from the supplied source (auto-update: $AUTOUPDATE)"

if ss -ltn 2>/dev/null | grep -qE '[:.]80\s'; then
  echo "NOTE: something already listens on port 80 (the APP Builder's nginx?). Coolify's dashboard"
  echo "      (port 8000) will work, but its proxy can't take 80/443 here. Use a separate server"
  echo "      for Coolify if it should serve apps on 80/443."
fi

AUTOUPDATE="$AUTOUPDATE" bash "$SRC/scripts/install.sh" "$VERSION"

echo "Waiting for Coolify to answer on http://127.0.0.1:8000/api/health ..."
for _ in $(seq 1 "$HEALTH_WAIT"); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "COOLIFY UP ($VERSION)"
    cat <<EOF

Next, in the Coolify dashboard (http://<this-server>:8000):
  1. Create the admin account (unless ROOT_* was set) and finish the onboarding.
  2. Settings -> Advanced -> turn API Access ON.
  3. Keys & Tokens -> API tokens -> create a token with "deploy" and "read" permissions.
  4. Add each app as a resource. Name it exactly as the APP Builder names it (e.g. grafana)
     so the hand-off can find its UUID by itself, or list the UUIDs in coolify_resources.json.
Then on the APP Builder server, in /srv/app-builder/.env:
     COOLIFY_URL=http://<this-server>:8000
     COOLIFY_TOKEN=<the token>
  and: systemctl restart app-builder-pipeline
EOF
    exit 0
  fi
  sleep 1
done
echo "COOLIFY NOT UP after ${HEALTH_WAIT}s. Check: docker ps; /data/coolify/source/installation-*.log" >&2
exit 1
