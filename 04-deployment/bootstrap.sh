#!/usr/bin/env bash
set -euo pipefail
ROOT=/srv/app-builder; APP=/opt/app-builder
mkdir -p "$ROOT" "$APP" "$ROOT"/{inbox,work,library,package,state,state/apps}
cp -a "$(dirname "$0")/." "$APP/"
# build.py is an offline/candidate-builder utility, not part of the AWS upload pipeline
# (see 04-deployment/README runtime boundary notes). Do not leave it in the runtime path.
rm -f "$APP/build.py"
cp -f "$APP/upstream_apps.json" "$ROOT/upstream_apps.json"
# Canonical front door source: 02-front-door/front-door.html in the handoff.
if [ -f "$(dirname "$0")/../02-front-door/front-door.html" ]; then cp -f "$(dirname "$0")/../02-front-door/front-door.html" "$ROOT/front-door.html"; elif [ -f "$APP/front-door.html" ]; then cp -f "$APP/front-door.html" "$ROOT/front-door.html"; fi
cat >/etc/systemd/system/app-builder-gateway.service <<EOF
[Unit]
Description=APP Builder web gateway
After=network.target
[Service]
WorkingDirectory=$ROOT
EnvironmentFile=-$ROOT/.env
ExecStart=/usr/bin/python3 $APP/gateway.py
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/app-builder-pipeline.service <<EOF
[Unit]
Description=APP Builder upload pipeline
After=network.target
[Service]
WorkingDirectory=$ROOT
EnvironmentFile=-$ROOT/.env
ExecStart=/usr/bin/python3 $APP/pipeline.py
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
EOF
if [ ! -f "$ROOT/.env" ]; then
 umask 077
 secret="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
 pass="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')"
 cat >"$ROOT/.env" <<EOF
APP_BUILDER_ROOT=$ROOT
APP_BUILDER_HOST=127.0.0.1
APP_BUILDER_PORT=8787
APP_BUILDER_USER=admin
APP_BUILDER_PASSWORD=$pass
APP_BUILDER_SESSION_SECRET=$secret
APP_BUILDER_BROWSER_CHECK=true
APP_BUILDER_WATCHER_INTERVAL=300
EOF
 echo "Initial APP_BUILDER_PASSWORD=$pass" >"$ROOT/INITIAL_LOGIN.txt"; chmod 600 "$ROOT/.env" "$ROOT/INITIAL_LOGIN.txt"
fi
# Runtime dependencies. This handoff targets Amazon Linux 2023 (dnf), while
# retaining apt support for Debian/Ubuntu hosts.
if command -v dnf >/dev/null 2>&1; then
  dnf install -y nodejs python3-pip nginx unzip git \
    nss nspr atk at-spi2-atk at-spi2-core cups-libs libdrm libxkbcommon \
    libXcomposite libXdamage libXext libXfixes libXrandr libxshmfence \
    pango cairo alsa-lib mesa-libgbm liberation-fonts
elif command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs python3-pip nginx unzip git
else
  echo 'Unsupported Linux package manager: expected dnf or apt-get.' >&2
  exit 1
fi

node_major="$(node --version | sed 's/^v//' | cut -d. -f1)"
if [ -z "$node_major" ] || [ "$node_major" -lt 18 ]; then
  echo "Node.js >= 18 is required; found $(node --version 2>/dev/null || echo missing)" >&2
  exit 1
fi

# Browser stage is part of the acceptance gate. Install Playwright and its
# Chromium browser if they are not already present. On Amazon Linux there is
# no apt chromium package, so use Playwright's managed Chromium binary.
if ! python3 -c 'import playwright' >/dev/null 2>&1; then
  python3 -m pip install --break-system-packages playwright
fi
python3 -m playwright install chromium

# Fail the deployment immediately if the real browser cannot launch.
python3 - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("data:text/html,<title>APP Builder browser gate</title>", wait_until="load")
    assert page.title() == "APP Builder browser gate"
    browser.close()
print("PLAYWRIGHT_BROWSER_PASS")
PY

cat >/etc/systemd/system/app-builder-watcher.service <<EOF
[Unit]
Description=APP Builder six-stage system watcher
After=network.target app-builder-gateway.service app-builder-pipeline.service
[Service]
WorkingDirectory=$ROOT
EnvironmentFile=-$ROOT/.env
ExecStart=/usr/bin/python3 $APP/system_watcher.py --loop
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
# One persistent watcher service owns its own interval; do not also schedule the same service with a timer.
rm -f /etc/systemd/system/app-builder-watcher.timer
if [ -f "$APP/app-builder-nginx.conf" ]; then
  if ! command -v nginx >/dev/null 2>&1; then
    if command -v dnf >/dev/null 2>&1; then
      dnf install -y nginx
    elif command -v apt-get >/dev/null 2>&1; then
      apt-get update -y
      DEBIAN_FRONTEND=noninteractive apt-get install -y nginx
    else
      echo "nginx is required but no supported package manager is available." >&2
      exit 1
    fi
  fi
  sed "s/YOUR_DOMAIN/${APP_BUILDER_DOMAIN:-_}/g" "$APP/app-builder-nginx.conf" > /etc/nginx/conf.d/app-builder.conf
  rm -f /etc/nginx/conf.d/app-builder-default.conf
  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx
  # Automatic TLS. Set APP_BUILDER_DOMAIN and APP_BUILDER_LETSENCRYPT_EMAIL and
  # certbot issues + installs the certificate and adds the HTTPS redirect here,
  # so the login never rides plain HTTP. This still needs the two things no
  # script can supply: the domain's DNS must already point at this server, and
  # ports 80+443 must be open in the security group. If the domain isn't set,
  # we skip and say so clearly rather than pretending HTTPS is on.
  if [ -n "${APP_BUILDER_DOMAIN:-}" ] && [ "${APP_BUILDER_DOMAIN}" != "_" ] && [ -n "${APP_BUILDER_LETSENCRYPT_EMAIL:-}" ]; then
    if ! command -v certbot >/dev/null 2>&1; then
      if command -v dnf >/dev/null 2>&1; then dnf install -y certbot python3-certbot-nginx
      elif command -v apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get install -y certbot python3-certbot-nginx; fi
    fi
    if certbot --nginx -n --agree-tos --redirect \
        -m "${APP_BUILDER_LETSENCRYPT_EMAIL}" -d "${APP_BUILDER_DOMAIN}"; then
      systemctl reload nginx
      echo "TLS ISSUED for ${APP_BUILDER_DOMAIN} — login is now HTTPS."
    else
      echo "TLS NOT ISSUED. Check that ${APP_BUILDER_DOMAIN} points at this server and ports 80/443 are open, then re-run. Serving HTTP only for now." >&2
    fi
  else
    echo "TLS SKIPPED: set APP_BUILDER_DOMAIN and APP_BUILDER_LETSENCRYPT_EMAIL to get an automatic HTTPS certificate. Serving HTTP only until then."
  fi
fi
systemctl daemon-reload
systemctl enable --now app-builder-gateway.service app-builder-pipeline.service app-builder-watcher.service

# Deployment acceptance gate: do not report success until all runtime services
# are active, the gateway health endpoint answers, and nginx answers locally.
for svc in app-builder-gateway.service app-builder-pipeline.service app-builder-watcher.service nginx.service; do
  systemctl is-active --quiet "$svc" || {
    echo "DEPLOYMENT FAILED: $svc is not active" >&2
    systemctl --no-pager --full status "$svc" || true
    exit 1
  }
done
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8787/health >/tmp/app-builder-health.txt 2>/dev/null; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    echo "DEPLOYMENT FAILED: gateway health check did not pass" >&2
    journalctl -u app-builder-gateway.service -n 100 --no-pager || true
    exit 1
  fi
  sleep 1
done
curl -fsSI http://127.0.0.1/ >/dev/null || {
  echo "DEPLOYMENT FAILED: nginx did not answer locally" >&2
  nginx -t || true
  journalctl -u nginx.service -n 100 --no-pager || true
  exit 1
}
echo "DEPLOYMENT VERIFIED"
echo "Gateway health: $(cat /tmp/app-builder-health.txt)"
echo "Open http://<EC2-IP>/ and read $ROOT/INITIAL_LOGIN.txt. For production, put nginx behind HTTPS before exposing it publicly."
