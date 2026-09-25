#!/usr/bin/env bash
set -euo pipefail
ROOT=/srv/app-builder; APP=/opt/app-builder
# v114.1: code lives in a fresh folder per deploy under $RELS; $APP is a symlink to the live one.
RELS=/opt/app-builder-releases
ATTA_LIB_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$ATTA_LIB_DIR/bootstrap-lib.sh"
mkdir -p "$ROOT" "$ROOT"/{inbox,work,library,package,state,state/apps}
# v116: the services run as their own users (atta-web, atta-run, atta-proxy); only deployd stays root.
atta_ensure_users
# Stage the new code in its own folder and finish it there, THEN switch $APP to it in one step.
NEW_CODE="$(atta_stage_code "$ATTA_LIB_DIR" "$RELS")"
# build.py is an offline/candidate-builder utility, not part of the AWS upload pipeline
# (see 04-deployment/README runtime boundary notes). Do not leave it in the runtime path.
rm -f "$NEW_CODE/build.py"
# The skins package ships with the deployment, so apps can be added before any bundle upload.
SKINS="$(dirname "$0")/../03-ui-skins-capability-package/UI_Skin_Capability_OneShot_v2.zip"
[ -f "$SKINS" ] && cp -f "$SKINS" "$NEW_CODE/UI_Skin_Capability_OneShot_v2.zip"
# A bundle carries the skins package beside 04-deployment; an ADM backup already has it inside.
[ -f "$NEW_CODE/UI_Skin_Capability_OneShot_v2.zip" ] || { [ -L "$APP" ] && [ -f "$APP/UI_Skin_Capability_OneShot_v2.zip" ] && cp -f "$APP/UI_Skin_Capability_OneShot_v2.zip" "$NEW_CODE/"; } || true
atta_activate_code "$APP" "$RELS" "$NEW_CODE"
echo "code: $APP -> $NEW_CODE"
# Seed repos are optional: the library grows from uploads and git addresses as well.
[ -f "$APP/upstream_apps.json" ] && cp -f "$APP/upstream_apps.json" "$ROOT/upstream_apps.json"
# Canonical front door source: 02-front-door/front-door.html in the handoff.
if [ -f "$(dirname "$0")/../02-front-door/front-door.html" ]; then cp -f "$(dirname "$0")/../02-front-door/front-door.html" "$ROOT/front-door.html"; elif [ -f "$APP/front-door.html" ]; then cp -f "$APP/front-door.html" "$ROOT/front-door.html"; fi
cat >/etc/systemd/system/app-builder-gateway.service <<EOF
[Unit]
Description=APP Builder web gateway
Wants=network-online.target
After=network-online.target
# v114.1: a broken start is retried every 5s; 10 failures inside 5 minutes stops the loop (see status).
StartLimitIntervalSec=300
StartLimitBurst=10
[Service]
WorkingDirectory=$ROOT
EnvironmentFile=-$ROOT/.env
ExecStart=/usr/bin/python3 $APP/gateway.py
Restart=always
RestartSec=5
# v116: its own user, no Docker, and a sandbox: it only ever writes under $ROOT.
User=$ATTA_WEB_USER
Group=$ATTA_GROUP
UMask=0027
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$ROOT
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
CapabilityBoundingSet=
[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/app-builder-pipeline.service <<EOF
[Unit]
Description=APP Builder upload pipeline
Wants=network-online.target
Requires=docker.service
After=network-online.target docker.service
StartLimitIntervalSec=300
StartLimitBurst=10
[Service]
WorkingDirectory=$ROOT
EnvironmentFile=-$ROOT/.env
# Ordering isn't readiness: wait until the Docker daemon actually answers.
ExecStartPre=/usr/bin/env bash $APP/wait-for-docker.sh
TimeoutStartSec=150
ExecStart=/usr/bin/python3 $APP/pipeline.py
Restart=always
RestartSec=5
# v116: the runner user. Docker access is root-equivalent, so it gets no more than it needs besides:
# no new SUID files, a private /tmp, read-only /usr /boot /etc. (No NoNewPrivileges: skin proxies are started
# through one sudoers rule that DROPS to the proxy user. No W^X filter: node and Chromium JIT.)
User=$ATTA_RUN_USER
Group=$ATTA_GROUP
SupplementaryGroups=docker $ATTA_PROXY_USER
UMask=0027
Environment=HOME=$ATTA_RUN_HOME PLAYWRIGHT_BROWSERS_PATH=$ATTA_BROWSERS
ProtectSystem=full
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictRealtime=yes
ProtectClock=yes
ProtectHostname=yes
SystemCallArchitectures=native
[Install]
WantedBy=multi-user.target
EOF
if [ ! -f "$ROOT/.env" ]; then
 umask 077
 secret="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
 cat >"$ROOT/.env" <<EOF
APP_BUILDER_ROOT=$ROOT
APP_BUILDER_HOST=127.0.0.1
APP_BUILDER_PORT=8787
APP_BUILDER_SESSION_SECRET=$secret
APP_BUILDER_BROWSER_CHECK=true
APP_BUILDER_WATCHER_INTERVAL=300
# Coolify hand-off (docs/COOLIFY-HANDOFF.md). Qualified builds wait in the outbox until both are set.
COOLIFY_URL=
COOLIFY_TOKEN=
# Self-healing tier 3 (LLM). Blank = that tier is skipped and failures go straight to a human.
ANTHROPIC_API_KEY=
# v116: the LLM repair tier needs this as well as the key (the Front Door uses the same key). It can only
# edit overlay data (CSS/JSON), never code, and every proxy it restarts runs unprivileged.
APP_BUILDER_HEAL_LLM=false
# Public web address(es) and the email for the free HTTPS certificate (Let's Encrypt).
# DNS for each name must already point at this server, ports 80+443 open. Re-run `bash run` after setting.
APP_BUILDER_DOMAIN=
APP_BUILDER_LETSENCRYPT_EMAIL=
# v116: without HTTPS nginx answers on 127.0.0.1 only. true = serve plain HTTP publicly anyway (not advised).
APP_BUILDER_ALLOW_PUBLIC_HTTP=false
# v116: pre-made test accounts (tester01..NN). 0 on a server; set a number to create them, then hand them out.
APP_BUILDER_TEST_ACCOUNTS=0
# App runner (app_runner.py). true = apps stay running after their check; false = stopped after it
# (frees memory for the next app; Coolify runs the qualified ones for real).
APP_BUILDER_KEEP_RUNNING=false
# true = delete an app's downloaded images after its check (use on small disks).
APP_BUILDER_PRUNE_IMAGES=false
# Seconds an app gets to answer after it starts, and the most it gets while still visibly starting.
APP_BUILDER_BOOT_TIMEOUT=240
APP_BUILDER_BOOT_TIMEOUT_MAX=900
# Self-healing tier 4: incoming-webhook URL for human alerts (Slack/Discord/any JSON POST). Blank = /alerts page only.
ALERT_WEBHOOK_URL=
EOF
 chmod 600 "$ROOT/.env"
fi
# Accounts: admin + 10 pre-made test accounts. Idempotent: existing accounts are never changed.
# On an upgraded server the old shared APP_BUILDER_PASSWORD (if still in .env) becomes the admin password.
# v114.1: .env is read as data (envfile.py), never run as shell. A malformed or unsafe line stops the install.
python3 "$APP/envfile.py" check "$ROOT/.env" || { echo "DEPLOYMENT FAILED: fix $ROOT/.env (line named above)" >&2; exit 1; }
atta_env_run "$ROOT/.env" APP_BUILDER_ROOT,APP_BUILDER_USER,APP_BUILDER_PASSWORD,APP_BUILDER_TEST_ACCOUNTS -- python3 "$APP/accounts.py" init
# App id -> Coolify resource UUID. Template only; the owner fills it in.
if [ ! -f "$ROOT/coolify_resources.json" ]; then
 printf '{\n  "apps": {}\n}\n' >"$ROOT/coolify_resources.json"
fi
# v114.1: ownership and permissions enforced on every run, not only when the files are first made.
atta_secure_state "$ROOT"
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

# Docker + Compose: the app runner (app_runner.py) starts every library app in a container so the
# watcher can check it. Without Docker nothing can be started and every app stops at 2 APP_UP.
if ! command -v docker >/dev/null 2>&1; then
  if command -v dnf >/dev/null 2>&1; then dnf install -y docker
  else DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io; fi
fi
# Docker Hub limits anonymous downloads; send them through Google's public Docker Hub cache first.
if [ ! -f /etc/docker/daemon.json ]; then
  mkdir -p /etc/docker
  printf '{\n  "registry-mirrors": ["https://mirror.gcr.io"]\n}\n' > /etc/docker/daemon.json
fi
systemctl enable --now docker
atta_join_docker   # v116: the runner user drives Docker (group added now that Docker exists)
if ! docker compose version >/dev/null 2>&1; then
  # Amazon Linux has no compose package: install Docker's own compose plugin binary.
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2 >/dev/null 2>&1 || \
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin >/dev/null 2>&1 || \
  atta_install_compose /usr/local/lib/docker/cli-plugins/docker-compose || \
    { echo "DEPLOYMENT FAILED: Docker Compose ${ATTA_COMPOSE_VERSION} could not be installed and verified" >&2; exit 1; }
fi
docker compose version >/dev/null 2>&1 || { echo "DEPLOYMENT FAILED: docker compose is not available" >&2; exit 1; }
docker run --rm hello-world >/dev/null || { echo "DEPLOYMENT FAILED: docker cannot run a container" >&2; exit 1; }
# v114: containers may not reach the cloud metadata service (on AWS it hands out this server's IAM
# credentials). A oneshot unit re-adds the firewall rule after every Docker start, because Docker rebuilds
# its chains then. DOCKER-USER is the chain Docker leaves for rules like this; INPUT rules don't see container traffic.
cat >/usr/local/sbin/atta-block-metadata <<'BLOCKSH'
#!/usr/bin/env bash
# Drop container traffic to the cloud metadata service. Idempotent; safe to run repeatedly.
set -u
ok=0
if command -v iptables >/dev/null 2>&1; then
  iptables -N DOCKER-USER 2>/dev/null || true
  if iptables -C DOCKER-USER -d 169.254.169.254/32 -j DROP 2>/dev/null || iptables -I DOCKER-USER 1 -d 169.254.169.254/32 -j DROP; then ok=1; fi
fi
if command -v ip6tables >/dev/null 2>&1; then
  ip6tables -N DOCKER-USER 2>/dev/null || true
  ip6tables -C DOCKER-USER -d fd00:ec2::254/128 -j DROP 2>/dev/null || ip6tables -I DOCKER-USER 1 -d fd00:ec2::254/128 -j DROP || true
fi
if [ "$ok" != 1 ]; then
  echo "atta-block-metadata: iptables not available; container access to 169.254.169.254 is NOT blocked" >&2
  exit 1
fi
BLOCKSH
chmod 755 /usr/local/sbin/atta-block-metadata
cat >/etc/systemd/system/atta-block-metadata.service <<'BLOCKUNIT'
[Unit]
Description=ATTa: block container access to the cloud metadata service
After=docker.service
PartOf=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/atta-block-metadata
[Install]
WantedBy=docker.service
BLOCKUNIT
systemctl daemon-reload
systemctl enable atta-block-metadata.service >/dev/null 2>&1 || true
systemctl restart atta-block-metadata.service || echo "WARNING: could not block container access to the metadata service (see: systemctl status atta-block-metadata)" >&2
# Second layer, on AWS: require IMDSv2 with a hop limit of 1, so a container that got past the rule still
# can't get a token. Needs the AWS CLI and ec2:ModifyInstanceMetadataOptions; skipped otherwise.
if command -v aws >/dev/null 2>&1; then
  TOK=$(curl -fsS -m 2 -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || true)
  if [ -n "$TOK" ]; then
    IID=$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $TOK" http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null || true)
    REG=$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $TOK" http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null || true)
    if [ -n "$IID" ] && [ -n "$REG" ]; then
      if aws ec2 modify-instance-metadata-options --region "$REG" --instance-id "$IID" --http-tokens required \
           --http-put-response-hop-limit 1 >/dev/null 2>&1; then
        echo "IMDSv2 required, hop limit 1 (instance $IID)"
      else
        echo "NOTE: could not set IMDSv2 / hop limit 1 (the instance role may lack ec2:ModifyInstanceMetadataOptions). The firewall rule still applies." >&2
      fi
    fi
  fi
fi
echo "DOCKER_PASS"

node_major="$(node --version | sed 's/^v//' | cut -d. -f1)"
if [ -z "$node_major" ] || [ "$node_major" -lt 18 ]; then
  echo "Node.js >= 18 is required; found $(node --version 2>/dev/null || echo missing)" >&2
  exit 1
fi

# Browser stage is part of the acceptance gate. Install Playwright and its
# Chromium browser if they are not already present. On Amazon Linux there is
# no apt chromium package, so use Playwright's managed Chromium binary.
# pip >= 23 needs --break-system-packages on distro Pythons; older pip (Amazon Linux 2023's) rejects it.
pip_install(){ python3 -m pip install --break-system-packages "$@" 2>/dev/null || python3 -m pip install "$@"; }
if ! python3 -c 'import playwright' >/dev/null 2>&1; then
  pip_install playwright
fi
# v116: Chromium goes where the runner user can read it (not root's home).
export PLAYWRIGHT_BROWSERS_PATH="$ATTA_BROWSERS"
python3 -m playwright install chromium
chmod -R a+rX "$ATTA_BROWSERS"
# v116: compose_guard reads every app's compose YAML (include:/extends: and their env files) before Docker
# does; without PyYAML an older Docker Compose leaves it nothing to check with and apps are refused.
python3 -c 'import yaml' >/dev/null 2>&1 || pip_install pyyaml
python3 -c 'import yaml' >/dev/null 2>&1 || { echo "DEPLOYMENT FAILED: PyYAML could not be installed" >&2; exit 1; }
# Self-healing LLM tier uses the official Anthropic SDK.
python3 -c 'import anthropic' >/dev/null 2>&1 || pip_install anthropic

# Fail the deployment immediately if the real browser cannot launch AS THE RUNNER USER (v116).
runuser -u "$ATTA_RUN_USER" -- env HOME="$ATTA_RUN_HOME" PLAYWRIGHT_BROWSERS_PATH="$ATTA_BROWSERS" python3 - <<'PY'
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
Wants=network-online.target
Requires=docker.service
After=network-online.target docker.service app-builder-gateway.service app-builder-pipeline.service
StartLimitIntervalSec=300
StartLimitBurst=10
[Service]
WorkingDirectory=$ROOT
EnvironmentFile=-$ROOT/.env
# Ordering isn't readiness: wait until the Docker daemon actually answers.
ExecStartPre=/usr/bin/env bash $APP/wait-for-docker.sh
TimeoutStartSec=150
ExecStart=/usr/bin/python3 $APP/system_watcher.py --loop
Restart=always
RestartSec=5
# v116: the runner user. Docker access is root-equivalent, so it gets no more than it needs besides:
# no new SUID files, a private /tmp, read-only /usr /boot /etc. (No NoNewPrivileges: skin proxies are started
# through one sudoers rule that DROPS to the proxy user. No W^X filter: node and Chromium JIT.)
User=$ATTA_RUN_USER
Group=$ATTA_GROUP
SupplementaryGroups=docker $ATTA_PROXY_USER
UMask=0027
Environment=HOME=$ATTA_RUN_HOME PLAYWRIGHT_BROWSERS_PATH=$ATTA_BROWSERS
ProtectSystem=full
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictRealtime=yes
ProtectClock=yes
ProtectHostname=yes
SystemCallArchitectures=native
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
  # Domain + certificate email can live in .env (set once, kept across re-runs) or be passed in the environment.
  if [ -z "${APP_BUILDER_DOMAIN:-}" ] && [ -f "$ROOT/.env" ]; then
    APP_BUILDER_DOMAIN="$(sed -n 's/^APP_BUILDER_DOMAIN=//p' "$ROOT/.env" | tail -1)"
    APP_BUILDER_LETSENCRYPT_EMAIL="${APP_BUILDER_LETSENCRYPT_EMAIL:-$(sed -n 's/^APP_BUILDER_LETSENCRYPT_EMAIL=//p' "$ROOT/.env" | tail -1)}"
  fi
  # Several names allowed, comma or space separated: "airexploit.com,www.airexploit.com".
  DOMAINS="$(printf '%s' "${APP_BUILDER_DOMAIN:-}" | tr ',' ' ' | xargs)"
  # v116: HTTPS by default. Without a domain + certificate email nginx answers on 127.0.0.1 only, so logins
  # never cross the internet in clear text. APP_BUILDER_ALLOW_PUBLIC_HTTP=true is the explicit opt-out.
  [ -z "${APP_BUILDER_ALLOW_PUBLIC_HTTP:-}" ] && [ -f "$ROOT/.env" ] && \
    APP_BUILDER_ALLOW_PUBLIC_HTTP="$(sed -n 's/^APP_BUILDER_ALLOW_PUBLIC_HTTP=//p' "$ROOT/.env" | tail -1)"
  NGINX_LISTEN="$(atta_nginx_listen "${DOMAINS:-}" "${APP_BUILDER_LETSENCRYPT_EMAIL:-}" "${APP_BUILDER_ALLOW_PUBLIC_HTTP:-}")"
  atta_nginx_conf "$APP/app-builder-nginx.conf" "${DOMAINS:-_}" "$NGINX_LISTEN" > /etc/nginx/conf.d/app-builder.conf \
    || { echo "DEPLOYMENT FAILED: could not write the nginx site" >&2; exit 1; }
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
  if [ -n "${DOMAINS:-}" ] && [ "${DOMAINS}" != "_" ] && [ -n "${APP_BUILDER_LETSENCRYPT_EMAIL:-}" ]; then
    if ! command -v certbot >/dev/null 2>&1; then
      if command -v dnf >/dev/null 2>&1; then dnf install -y certbot python3-certbot-nginx
      elif command -v apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get install -y certbot python3-certbot-nginx; fi
    fi
    if certbot --nginx -n --agree-tos --redirect \
        -m "${APP_BUILDER_LETSENCRYPT_EMAIL}" $(for d in $DOMAINS; do printf -- '-d %s ' "$d"; done); then
      systemctl reload nginx
      echo "TLS ISSUED for ${DOMAINS} — login is now HTTPS."
    else
      echo "TLS NOT ISSUED. Check that ${DOMAINS} points at this server and ports 80/443 are open, then re-run." >&2
      if [ "${APP_BUILDER_ALLOW_PUBLIC_HTTP:-}" != "true" ]; then
        # v116: no certificate, no public plain-HTTP login page.
        atta_nginx_conf "$APP/app-builder-nginx.conf" "${DOMAINS:-_}" "127.0.0.1:80" > /etc/nginx/conf.d/app-builder.conf
        nginx -t && systemctl reload nginx
        echo "Serving on 127.0.0.1 only until HTTPS works (ssh -L 8080:127.0.0.1:80 this-server)." >&2
      fi
    fi
  elif [ "$NGINX_LISTEN" = "80" ]; then
    echo "WARNING: serving PUBLIC plain HTTP (APP_BUILDER_ALLOW_PUBLIC_HTTP=true): passwords cross the network in clear text." >&2
  else
    echo "TLS SKIPPED: set APP_BUILDER_DOMAIN and APP_BUILDER_LETSENCRYPT_EMAIL for automatic HTTPS. Until then ATTa answers on 127.0.0.1 only (ssh -L 8080:127.0.0.1:80 this-server)."
  fi
fi
# ADM — Deploy Manager (04-deployment/deployd/). Watches its queue for uploaded ATTa bundles and
# deploys them with backup + health check + rollback. Enabled, started if not running, but NOT
# restarted here: deployd restarts itself after a successful deploy when its own code changed
# (restarting it from inside a deploy it is running would kill that deploy).
cp -f "$APP/deployd/systemd/atta-deployd.service" /etc/systemd/system/atta-deployd.service
sed -i "s#/srv/app-builder#$ROOT#g; s#/opt/app-builder#$APP#g" /etc/systemd/system/atta-deployd.service
chmod +x "$APP/deployd/deployctl" "$APP/deployd/deployd.py"
ln -sf "$APP/deployd/deployctl" /usr/local/bin/deployctl
mkdir -p "$ROOT/adm"/{incoming,staging,releases,backups,logs,state/queue,state/journal,requests}
# v116: skin proxies run as the proxy user through one sudoers rule; ownership re-applied for the new units.
atta_install_proxy_runner "$APP/run-proxy.sh" || { echo "DEPLOYMENT FAILED: could not install the proxy runner" >&2; exit 1; }
atta_secure_state "$ROOT"
systemctl daemon-reload
systemctl enable app-builder-gateway.service app-builder-pipeline.service app-builder-watcher.service atta-deployd.service
systemctl is-active --quiet atta-deployd.service || systemctl start atta-deployd.service
# restart, not just start: on a server that's already running ATTa, the new scripts only take effect
# once the services are restarted (enable --now leaves an already-running service on the old code).
systemctl restart app-builder-gateway.service app-builder-pipeline.service app-builder-watcher.service

# Deployment acceptance gate: do not report success until all runtime services
# are active, the gateway health endpoint answers, and nginx answers locally.
# v114.1: on failure, a run started by hand puts the previous code back and restarts it. Under ADM
# (ATTA_ADM_ACTIVE=1) nothing is undone here: ADM's rollback is the one that decides.
gate_failed() {
  if [ -z "${ATTA_ADM_ACTIVE:-}" ] && atta_restore_previous_code "$APP" "$RELS"; then
    echo "Restored the previous code ($(readlink "$APP")) and restarted it." >&2
    systemctl restart app-builder-gateway.service app-builder-pipeline.service app-builder-watcher.service || true
  fi
  exit 1
}
for svc in app-builder-gateway.service app-builder-pipeline.service app-builder-watcher.service atta-deployd.service nginx.service; do
  systemctl is-active --quiet "$svc" || {
    echo "DEPLOYMENT FAILED: $svc is not active" >&2
    systemctl --no-pager --full status "$svc" || true
    gate_failed
  }
done
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8787/health >/tmp/app-builder-health.txt 2>/dev/null; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    echo "DEPLOYMENT FAILED: gateway health check did not pass" >&2
    journalctl -u app-builder-gateway.service -n 100 --no-pager || true
    gate_failed
  fi
  sleep 1
done
curl -fsSI http://127.0.0.1/ >/dev/null || {
  echo "DEPLOYMENT FAILED: nginx did not answer locally" >&2
  nginx -t || true
  journalctl -u nginx.service -n 100 --no-pager || true
  gate_failed
}
echo "DEPLOYMENT VERIFIED"
atta_prune_code_releases "$APP" "$RELS"   # healthy: older code folders can go (live + previous always kept)
echo "Gateway health: $(cat /tmp/app-builder-health.txt)"
case "${NGINX_LISTEN:-}" in
  127.0.0.1:80) echo "Open it through an SSH tunnel: ssh -L 8080:127.0.0.1:80 <server>, then http://127.0.0.1:8080/ (public access needs HTTPS: set APP_BUILDER_DOMAIN + APP_BUILDER_LETSENCRYPT_EMAIL)." ;;
  *) echo "Open https://${DOMAINS%% *}/ (or http://<server>/ if APP_BUILDER_ALLOW_PUBLIC_HTTP=true)." ;;
esac
echo "Logins are in $ROOT/TEST_ACCOUNTS.txt (0600): hand each person one line, then delete the file."
