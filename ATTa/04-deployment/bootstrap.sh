#!/usr/bin/env bash
# bootstrap.sh — install or upgrade ATTa on this server. Run by `sudo bash run`, by ADM (deployd/deployctl), or by
# hand. v117: a TRANSACTION. Nothing live changes until everything that can fail has passed off to the side, and a
# failure after the switch puts back EXACTLY what was live (docs/DEPLOYMENT.md).
#
#   0 lock      one deploy at a time, server-wide (the lock deployd/deployctl use; inherited from ADM, else taken here)
#   1 record    the deployment record: deployctl journal <id>   (created/validating -> building -> built ->
#               health_checking -> verified -> live; or failed/timed_out/interrupted -> rolled_back)
#   2 validate  .env as data (never shell), the OS, the settings; what is live now is recorded as the rollback target
#   3 build     OFF TO THE SIDE: the new release folder, packages, Docker, the browser, a smoke test of the new
#               gateway from its own folder. Live code, units and nginx are untouched.            -> built
#   4 switch    snapshot + restore.sh first; then units, nginx (whole site, HTTPS included), the code symlink;
#               restart; ATTa-specific checks on the .env port, through nginx, and in a real browser; the HTTPS
#               certificate when one is configured.                                                -> verified
#               Any failure here: restore.sh puts back units, nginx, proxy.json and the symlink exactly, restarts,
#               and re-checks the previous release.
#   5 live      by hand: the release becomes known-good now. Under ADM: ADM checks independently, then makes it live.
set -Eeuo pipefail
umask 022
ROOT=/srv/app-builder; APP=/opt/app-builder
# v114.1: code lives in a fresh folder per deploy under $RELS; $APP is a symlink to the live one.
RELS=/opt/app-builder-releases
ATTA_LIB_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$ATTA_LIB_DIR/bootstrap-lib.sh"
ADM_DIR="${ATTA_ADM_ROOT:-$ROOT/adm}"
export APP_BUILDER_ROOT="$ROOT" ATTA_ADM_ROOT="$ADM_DIR" ATTA_CODE_RELEASES="$RELS" APP_BUILDER_APP="$APP"
[ "$(id -u)" = 0 ] || { echo "DEPLOYMENT FAILED: run as root: sudo bash run" >&2; exit 1; }
mkdir -p "$ROOT" "$ADM_DIR/state"
SWITCHED=""; NEW_CODE=""; TXN=""; ATTA_LAST_FAIL=""

# ---------------------------------------------------------------------------------------------------- 0 lock
atta_deploy_lock "$ADM_DIR/state/deployd.lock" || exit 1

# ---------------------------------------------------------------------------------------------------- 1 record
ATTA_DEPLOYMENT_ID="$(atta_ds begin)" || { echo "DEPLOYMENT FAILED: could not open the deployment record" >&2; exit 1; }
export ATTA_DEPLOYMENT_ID
if [ -z "${ATTA_ADM_ACTIVE:-}" ]; then
  # By hand: name this run as the lock's owner, so nobody mistakes it for the leftovers of a killed deploy.
  python3 "$ATTA_LIB_DIR/deployd/adm/lock.py" owner "$ADM_DIR/state/deployd.lock" "$ATTA_DEPLOYMENT_ID" "$$"
fi
echo "deployment $ATTA_DEPLOYMENT_ID  (record: deployctl journal $ATTA_DEPLOYMENT_ID)"

on_failure() {
  # The ERR / INT / TERM / EXIT trap. Records why, puts back what was live if anything was switched, and says what is
  # live now. Runs once: whichever trap fires first.
  local rc=$? why="$1" kind="${2:-failed}" st restored=""
  [ "$BASHPID" = "$$" ] || exit "$rc"          # inside $( ) or a subshell: let the main shell handle it, once
  trap - ERR INT TERM EXIT
  set +e
  [ -n "$ATTA_LAST_FAIL" ] && why="$ATTA_LAST_FAIL"
  echo "DEPLOYMENT FAILED: $why" >&2
  st="$(atta_ds state 2>/dev/null)"
  case "$st" in failed|timed_out|interrupted|rolled_back|live) ;; *) atta_ds to "$kind" "$why" ;; esac
  if [ -n "$SWITCHED" ]; then
    echo "Putting back exactly what was live (snapshot $TXN) ..." >&2
    if bash "$TXN/restore.sh"; then restored=1; atta_ds set local_restore '{"result": "verified"}'
    else atta_ds set local_restore '{"result": "failed"}'; fi
  fi
  [ -n "$NEW_CODE" ] && [ -d "$NEW_CODE" ] && atta_rel failed "$NEW_CODE" "$ATTA_DEPLOYMENT_ID" "$why"
  if [ -z "${ATTA_ADM_ACTIVE:-}" ]; then
    # By hand this run settles its own record. (Under ADM, ADM re-checks and decides.)
    if [ -z "$SWITCHED" ]; then atta_ds rollback not_needed "it failed before anything live was switched"
    elif [ -n "$restored" ]; then atta_ds rollback succeeded && atta_ds to rolled_back
    else atta_ds rollback failed "restore.sh did not bring the previous release back (see above)"; fi
    atta_rel prune "$RELS" "$APP" "$ATTA_KEEP_CODE_RELEASES" >/dev/null
  fi
  if [ -z "$SWITCHED" ]; then echo "RESULT: FAILED — nothing live was changed. Still live: $(readlink -e "$APP" 2>/dev/null || echo "nothing (first install)")" >&2
  elif [ -n "$restored" ]; then
    if [ -e "$APP" ]; then echo "RESULT: ROLLED BACK — live again, and checked: $(readlink -e "$APP")" >&2
    else echo "RESULT: ROLLED BACK — the first install was undone; nothing is live (fix the cause above and run again)" >&2; fi
  else echo "RESULT: ROLLBACK FAILED — a person is needed. Live now: $(readlink -e "$APP" 2>/dev/null || echo "unclear"). See deployctl journal $ATTA_DEPLOYMENT_ID" >&2; fi
  exit 1
}
trap 'on_failure "a step failed at line $LINENO (see the output above)"' ERR
# Safety net: ANY other way out with a non-zero status (a plain `exit 1`, set -e in a place ERR doesn't cover) still
# goes through on_failure, so no run ever ends with its record half-written or something half-switched.
trap 'rc=$?; [ "$rc" = 0 ] || on_failure "stopped with status $rc (see the output above)"' EXIT
if [ -n "${ATTA_ADM_ACTIVE:-}" ]; then
  trap 'exit 143' TERM; trap 'exit 130' INT     # ADM stops the whole tree itself and then rolls back
else
  trap 'on_failure "stopped by a signal" interrupted' INT TERM
fi

# ---------------------------------------------------------------------------------------------------- 2 validate
if [ ! -f "$ROOT/.env" ]; then
  atta_write_default_env "$ROOT/.env" "$ROOT"
  echo "created $ROOT/.env with a new session secret (edit it for the domain/email; it is data, never run)"
fi
# v114.1/v117: .env is read as data (envfile.py), never run as shell. A malformed or unsafe line stops the install.
python3 "$ATTA_LIB_DIR/envfile.py" check "$ROOT/.env" || atta_fail "fix $ROOT/.env (the line named above); nothing was changed"
PORT="$(atta_env_get "$ROOT/.env" APP_BUILDER_PORT 8787)"
{ [[ "$PORT" =~ ^[0-9]{2,5}$ ]] && [ "$PORT" -lt 65536 ] && [ "$PORT" != 80 ] && [ "$PORT" != 443 ]; } \
  || atta_fail "APP_BUILDER_PORT=$PORT is not a usable port (a number, not 80/443: nginx serves those)"
DOMAINS="$(atta_env_get "$ROOT/.env" APP_BUILDER_DOMAIN)"
EMAIL="$(atta_env_get "$ROOT/.env" APP_BUILDER_LETSENCRYPT_EMAIL)"
ALLOW_PUBLIC_HTTP="$(atta_env_get "$ROOT/.env" APP_BUILDER_ALLOW_PUBLIC_HTTP false)"
FIRST_DOMAIN="$(printf '%s' "$DOMAINS" | tr ',' ' ' | awk '{print $1}')"
# The settings (domain names, email) are checked now, before anything changes (nothing is written).
python3 "$ATTA_LIB_DIR/nginx_site.py" check --port "$PORT" --domains "$DOMAINS" --email "$EMAIL" \
    --allow-public-http "$ALLOW_PUBLIC_HTTP" >/dev/null \
  || atta_fail "fix APP_BUILDER_DOMAIN / APP_BUILDER_LETSENCRYPT_EMAIL in $ROOT/.env (see above); nothing was changed"
OS="$(atta_os)"
if ! atta_os_supported "$OS"; then
  [ "${ATTA_ALLOW_UNSUPPORTED_OS:-}" = 1 ] \
    || atta_fail "$OS is not a supported system (Ubuntu 24.04 or Amazon Linux 2023). ATTA_ALLOW_UNSUPPORTED_OS=1 tries anyway"
  echo "WARNING: $OS is not a supported system; continuing because ATTA_ALLOW_UNSUPPORTED_OS=1" >&2
fi
echo "system: $OS"
atta_rel migrate-legacy "$APP" "$RELS" >/dev/null
if [ -z "${ATTA_ADM_ACTIVE:-}" ]; then
  PREV="$(atta_ds previous-live)"      # ADM records this itself before it starts bootstrap
  echo "rollback target: ${PREV:-none (first install, or nothing verified is live)}"
fi
[ "$(atta_ds state)" = validating ] && atta_ds to building

# ---------------------------------------------------------------------------------------------------- 3 build
mkdir -p "$ROOT"/{inbox,work,library,package,state,state/apps}
# v116: the services run as their own users (atta-web, atta-run, atta-proxy); only deployd stays root.
atta_ensure_users
# Stage the new code in its own folder and finish it there; $APP is switched only in step 4.
NEW_CODE="$(atta_stage_code "$ATTA_LIB_DIR" "$RELS")" || atta_fail "the new release could not be staged (see above)"
# A zip carries no exec bits: the two commands that are run directly get them here (deployctl is linked into PATH).
chmod 755 "$NEW_CODE/deployd/deployctl" "$NEW_CODE/deployd/deployd.py"
# build.py is an offline/candidate-builder utility, not part of the AWS upload pipeline. Not in the runtime path.
rm -f "$NEW_CODE/build.py"
# The skins package ships with the deployment, so apps can be added before any bundle upload.
SKINS="$ATTA_LIB_DIR/../03-ui-skins-capability-package/UI_Skin_Capability_OneShot_v2.zip"
if [ -f "$SKINS" ]; then install -m 0644 "$SKINS" "$NEW_CODE/UI_Skin_Capability_OneShot_v2.zip"
elif [ -f "$APP/UI_Skin_Capability_OneShot_v2.zip" ]; then install -m 0644 "$APP/UI_Skin_Capability_OneShot_v2.zip" "$NEW_CODE/"; fi
# The Front Door page travels with the release (installed into $ROOT at the switch).
FRONT="$ATTA_LIB_DIR/../02-front-door/front-door.html"
[ -f "$FRONT" ] && install -m 0644 "$FRONT" "$NEW_CODE/front-door.html"
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version","unknown"))' "$NEW_CODE/release.json" 2>/dev/null || echo unknown)"
atta_ds set attempted "$(python3 -c 'import json,sys; print(json.dumps({"release": sys.argv[1], "version": sys.argv[2], "code_sha256": sys.argv[3]}))' "$NEW_CODE" "$VERSION" "$(atta_rel sha "$NEW_CODE")")"
echo "new release, not live yet: $NEW_CODE ($VERSION)"
atta_install_packages "$OS"

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
    atta_fail "Docker Compose ${ATTA_COMPOSE_VERSION} could not be installed and verified"
fi
docker compose version >/dev/null 2>&1 || atta_fail "docker compose is not available"
docker run --rm hello-world >/dev/null || atta_fail "docker cannot run a container"
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

node_major="$(node --version 2>/dev/null | sed 's/^v//' | cut -d. -f1)"
if [ -z "$node_major" ] || [ "$node_major" -lt 18 ]; then
  atta_fail "Node.js >= 18 is required; found $(node --version 2>/dev/null || echo none)"
fi
# The browser stage is part of the acceptance gate: pinned Playwright + Chromium, the OS libraries it needs (per OS),
# and a real launch as the runner user. Failures end in one "DEPLOYMENT FAILED: ..." line.
atta_install_browser "$OS" "$NEW_CODE"
# The new gateway, from its own folder, against a throwaway data folder: it must start and prove itself.
atta_candidate_check "$NEW_CODE"
atta_ds to built

# ---------------------------------------------------------------------------------------------------- 4 switch
TXN="$RELS/.txn/$ATTA_DEPLOYMENT_ID"
atta_snapshot "$TXN" "$APP" "$ROOT" "$NEW_CODE"
atta_ds set transaction "\"$TXN\""
atta_ds to health_checking
SWITCHED=1
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
# Seed repos are optional: the library grows from uploads and git addresses as well.
[ -f "$NEW_CODE/upstream_apps.json" ] && cp -f "$NEW_CODE/upstream_apps.json" "$ROOT/upstream_apps.json"
[ -f "$NEW_CODE/front-door.html" ] && install -m 0644 "$NEW_CODE/front-door.html" "$ROOT/front-door.html"
# App id -> Coolify resource UUID. Template only; the owner fills it in.
[ -f "$ROOT/coolify_resources.json" ] || printf '{\n  "apps": {}\n}\n' >"$ROOT/coolify_resources.json"
# Accounts: admin (+ test accounts if APP_BUILDER_TEST_ACCOUNTS says so). Idempotent: existing accounts never change.
atta_env_run "$ROOT/.env" APP_BUILDER_ROOT,APP_BUILDER_USER,APP_BUILDER_PASSWORD,APP_BUILDER_TEST_ACCOUNTS -- python3 "$NEW_CODE/accounts.py" init
# ADM — Deploy Manager (deployd/). Enabled, started if not running. Under ADM it is NOT restarted here: deployd
# restarts itself after a successful deploy when its own code changed (restarting it mid-deploy would kill that deploy).
install -m 0644 "$NEW_CODE/deployd/systemd/atta-deployd.service" /etc/systemd/system/atta-deployd.service
sed -i "s#/srv/app-builder#$ROOT#g; s#/opt/app-builder#$APP#g" /etc/systemd/system/atta-deployd.service
ln -sfn "$APP/deployd/deployctl" /usr/local/bin/deployctl
mkdir -p "$ADM_DIR"/{incoming,staging,releases,backups,logs,state/queue,state/running,state/journal,requests}
# v116: skin proxies run as the proxy user through one sudoers rule.
atta_install_proxy_runner "$NEW_CODE/run-proxy.sh" || atta_fail "could not install the proxy runner"
# nginx: the distribution's stock site out of the way; ATTa's WHOLE site (HTTPS too, once a certificate exists)
# rendered from .env + the certificate on disk; nginx -t must pass WITHOUT warnings.
command -v nginx >/dev/null 2>&1 || atta_fail "nginx is not installed"
mkdir -p "$ATTA_ACME_ROOT" /etc/nginx/conf.d; chmod 755 "$ATTA_ACME_ROOT"
python3 "$NEW_CODE/nginx_site.py" neutralize
rm -f /etc/nginx/conf.d/app-builder-default.conf
render_site() {
  NGINX_MODE="$(python3 "$NEW_CODE/nginx_site.py" render --port "$PORT" --domains "$DOMAINS" --email "$EMAIL" \
      --allow-public-http "$ALLOW_PUBLIC_HTTP" --letsencrypt "$ATTA_LETSENCRYPT" --acme-root "$ATTA_ACME_ROOT" \
      --out /etc/nginx/conf.d/app-builder.conf --proxy-json "$ROOT/state/proxy.json")" || return 1
  local t; t="$(mktemp)"
  if ! nginx -t >"$t" 2>&1 || ! python3 "$NEW_CODE/nginx_site.py" check-output "$t" >/dev/null; then
    cat "$t" >&2; rm -f "$t"; atta_fail "nginx -t failed or warned (above): the site may not be served as rendered"; return 1
  fi
  rm -f "$t"
}
render_site
# v116: ownership/permissions of the data folder, re-applied for the new units and files.
atta_secure_state "$ROOT"
# ---- the switch itself: one atomic rename of the code symlink, then restart onto it
atta_rel switch "$APP" "$NEW_CODE"
echo "code: $APP -> $NEW_CODE"
# v114: containers may not reach the cloud metadata service (on AWS it hands out this server's IAM
# credentials). v116: nor private networks, link-local, loopback or this server itself; public internet only
# (APP_BUILDER_APP_EGRESS=public, default) or nothing (=deny). The rules live in container-egress.sh; a
# oneshot unit re-applies them after every Docker start, because Docker rebuilds its chains then.
install -o root -g root -m 0755 "$APP/container-egress.sh" /usr/local/sbin/atta-block-metadata
cat >/etc/systemd/system/atta-block-metadata.service <<BLOCKUNIT
[Unit]
Description=ATTa: restrict what app containers can reach (metadata, internal networks, this host)
After=docker.service
PartOf=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=-$ROOT/.env
ExecStart=/usr/local/sbin/atta-block-metadata
[Install]
WantedBy=docker.service
BLOCKUNIT
systemctl daemon-reload
systemctl enable atta-block-metadata.service >/dev/null 2>&1 || true
systemctl restart atta-block-metadata.service || echo "WARNING: could not restrict app containers' network access (see: systemctl status atta-block-metadata)" >&2
systemctl daemon-reload
systemctl enable app-builder-gateway.service app-builder-pipeline.service app-builder-watcher.service atta-deployd.service
systemctl is-active --quiet atta-deployd.service || systemctl start atta-deployd.service
# restart, not just start: on a server that's already running ATTa, the new code only runs once restarted.
systemctl restart app-builder-gateway.service app-builder-pipeline.service app-builder-watcher.service
systemctl enable --now nginx
systemctl reload nginx
# ---- the gate: every service active; THIS installation (identity proof) answering as THIS release, on the .env port
# and through nginx; then HTTPS (if configured); then a real browser through nginx.
for svc in app-builder-gateway.service app-builder-pipeline.service app-builder-watcher.service atta-deployd.service nginx.service; do
  systemctl is-active --quiet "$svc" || { systemctl --no-pager --full status "$svc" >&2 || true; atta_fail "$svc is not active"; }
done
gate() {
  python3 "$APP/atta_health.py" --env "$ROOT/.env" --direct --proxy --proxy-file "$ROOT/state/proxy.json" \
    --expect-release "$(basename "$NEW_CODE")" --timeout "${1:-90}"
}
gate 90 || { journalctl -u app-builder-gateway.service -n 60 --no-pager >&2 || true
             atta_fail "ATTa did not answer as release $(basename "$NEW_CODE") on port $PORT and through nginx"; }
if [ "$NGINX_MODE" = pending ]; then
  # A domain + email are set and there is no certificate yet: nginx serves the ACME path on :80, so issue it now.
  read -r -a DOMAIN_LIST <<<"$(printf '%s' "$DOMAINS" | tr ',' ' ')"
  if atta_issue_certificate "$EMAIL" "${DOMAIN_LIST[@]}"; then
    render_site
    [ "$NGINX_MODE" = tls ] || atta_fail "a certificate was issued but $ATTA_LETSENCRYPT/live/$FIRST_DOMAIN is missing"
    systemctl reload nginx
    gate 60 || atta_fail "HTTPS was set up but ATTa did not answer through it"
    echo "TLS ISSUED for $DOMAINS — ATTa is served over HTTPS (http redirects to https)."
  else
    echo "TLS NOT ISSUED: check that $DOMAINS points at this server and ports 80/443 are open, then run this again." >&2
    echo "Until then ATTa answers on 127.0.0.1 only (public :80 serves only the certificate check)." >&2
  fi
fi
if [ "$NGINX_MODE" = tls ]; then
  atta_cert_renewal "$APP" "$ROOT"
  python3 "$APP/tls_check.py" --env "$ROOT/.env" --letsencrypt "$ATTA_LETSENCRYPT" --warn-days 0 \
    || atta_fail "the HTTPS certificate check failed (above)"
fi
runuser -u "$ATTA_RUN_USER" -- env HOME="$ATTA_RUN_HOME" PLAYWRIGHT_BROWSERS_PATH="$ATTA_BROWSERS" \
    python3 "$APP/atta_health.py" --browser --proxy-file "$ROOT/state/proxy.json" --timeout 90 \
  || atta_fail "a real browser could not open ATTa's login through nginx"

# ---------------------------------------------------------------------------------------------------- 5 verified / live
atta_rel verify "$APP" "$NEW_CODE" "$ATTA_DEPLOYMENT_ID" >/dev/null
atta_ds to verified
echo "DEPLOYMENT VERIFIED: $(basename "$NEW_CODE") answers as itself on port $PORT, through nginx ($NGINX_MODE), and in a browser"
if [ -n "${ATTA_ADM_ACTIVE:-}" ]; then
  echo "ADM checks it independently now and makes it live."
  trap - ERR INT TERM EXIT
  exit 0
fi
atta_rel promote "$RELS" "$APP" "$NEW_CODE" "$ATTA_DEPLOYMENT_ID" >/dev/null
atta_ds to live
SWITCHED=""; trap - ERR INT TERM EXIT
# deployd runs the new code from now on (it waits for this run's lock, then starts).
systemctl restart atta-deployd.service || echo "WARNING: could not restart atta-deployd (systemctl status atta-deployd)" >&2
atta_rel prune "$RELS" "$APP" "$ATTA_KEEP_CODE_RELEASES" | sed 's/^/pruned: /'
find "$RELS/.txn" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} + 2>/dev/null || true
echo "LIVE: $(basename "$NEW_CODE") is the known-good release (previous known-good kept for rollback: deployctl rollback)"
case "$NGINX_MODE" in
  tls) echo "Open https://${FIRST_DOMAIN}/" ;;
  public) echo "Open http://<server>/ (plain HTTP: APP_BUILDER_ALLOW_PUBLIC_HTTP=true — logins cross the network in clear text)" ;;
  *) echo "Open it through an SSH tunnel: ssh -L 8080:127.0.0.1:80 <server>, then http://127.0.0.1:8080/ (public access needs HTTPS: set APP_BUILDER_DOMAIN + APP_BUILDER_LETSENCRYPT_EMAIL)" ;;
esac
echo "Logins are in $ROOT/TEST_ACCOUNTS.txt (0600): hand each person one line, then delete the file."
