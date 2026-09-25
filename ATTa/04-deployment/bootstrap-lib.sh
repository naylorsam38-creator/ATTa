# shellcheck shell=bash
# bootstrap-lib.sh — functions bootstrap.sh uses (v114.1; v117). Sourced, never run on its own.
# Kept separate so tests/ can exercise each step without root, systemd or a real server.

# ===================== CONFIG — edit here =====================
# Docker Compose used ONLY when the package manager has none. Pinned and checked: a new version is a
# deliberate edit of these three lines (hashes from the release's checksums.txt), never "latest".
ATTA_COMPOSE_VERSION="${ATTA_COMPOSE_VERSION:-v5.5.1}"
ATTA_COMPOSE_SHA256_X86_64="db1889184726840f75c4f9c001048430d4f25b3be3cb084d3ddd762bc0aed576"
ATTA_COMPOSE_SHA256_AARCH64="732e3a84c1a0f67256ce80bc2598a24546b10ca05f9faa97efceb1171ece2ef7"
# Code releases: each run of bootstrap.sh installs into a fresh folder here, and $APP becomes a
# symlink to it. How many to keep (the live one is never removed).
ATTA_KEEP_CODE_RELEASES="${ATTA_KEEP_CODE_RELEASES:-3}"
# ==============================================================

atta_arch() {
  # Supported machine types, mapped explicitly (never pasted into a URL unchecked).
  case "${ATTA_UNAME_M:-$(uname -m)}" in
    x86_64|amd64)  echo x86_64 ;;
    aarch64|arm64) echo aarch64 ;;
    *) echo "unsupported machine type: ${ATTA_UNAME_M:-$(uname -m)} (supported: x86_64, aarch64)" >&2; return 1 ;;
  esac
}

atta_compose_sha() {
  case "$1" in
    x86_64)  echo "$ATTA_COMPOSE_SHA256_X86_64" ;;
    aarch64) echo "$ATTA_COMPOSE_SHA256_AARCH64" ;;
    *) return 1 ;;
  esac
}

atta_verify_install() {
  # atta_verify_install FILE SHA256 DEST — installs FILE at DEST only if its hash matches.
  local file="$1" want="$2" dest="$3" got
  [ -n "$want" ] || { echo "no expected checksum given; refusing to install $dest" >&2; return 1; }
  got="$(sha256sum "$file" | awk '{print $1}')"
  if [ "$got" != "$want" ]; then
    echo "CHECKSUM MISMATCH for $(basename "$dest"): expected $want, got $got. Not installed." >&2
    rm -f "$file"; return 1
  fi
  mkdir -p "$(dirname "$dest")"
  if [ "$(id -u)" = 0 ]; then install -o root -g root -m 0755 "$file" "$dest"; else install -m 0755 "$file" "$dest"; fi
}

atta_install_compose() {
  # atta_install_compose DEST — pinned download over HTTPS only, checksum verified before install.
  local dest="${1:-/usr/local/lib/docker/cli-plugins/docker-compose}" arch sha tmp url
  arch="$(atta_arch)" || return 1
  sha="$(atta_compose_sha "$arch")" || return 1
  url="https://github.com/docker/compose/releases/download/${ATTA_COMPOSE_VERSION}/docker-compose-linux-${arch}"
  tmp="$(mktemp)"
  if ! curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
       --max-time 300 -o "$tmp" "$url"; then
    rm -f "$tmp"; echo "could not download Docker Compose ${ATTA_COMPOSE_VERSION}" >&2; return 1
  fi
  atta_verify_install "$tmp" "$sha" "$dest"; local rc=$?
  rm -f "$tmp"; return $rc
}

# v116: the services' own users. Only deployd (the installer itself) and Docker stay root.
ATTA_GROUP="${ATTA_GROUP:-atta}"                   # shared by the gateway and the runner (inbox, build records)
ATTA_WEB_USER="${ATTA_WEB_USER:-atta-web}"         # the web gateway: no Docker, no shell
ATTA_RUN_USER="${ATTA_RUN_USER:-atta-run}"         # pipeline + watcher: drives Docker, so Docker-group
ATTA_PROXY_USER="${ATTA_PROXY_USER:-atta-proxy}"   # skin proxies: nothing but its own copy of the overlay
ATTA_RUN_HOME="${ATTA_RUN_HOME:-/var/lib/atta-run}"
ATTA_BROWSERS="${ATTA_BROWSERS:-/opt/ms-playwright}"   # Playwright's Chromium, readable by the runner

atta_ensure_users() {
  # atta_ensure_users — create the service group and users (idempotent). Docker group membership is added
  # by atta_join_docker once Docker is installed.
  getent group "$ATTA_GROUP" >/dev/null || groupadd --system "$ATTA_GROUP"
  getent passwd "$ATTA_PROXY_USER" >/dev/null || \
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$ATTA_PROXY_USER"
  getent passwd "$ATTA_WEB_USER" >/dev/null || \
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$ATTA_WEB_USER"
  getent passwd "$ATTA_RUN_USER" >/dev/null || \
    useradd --system --create-home --home-dir "$ATTA_RUN_HOME" --shell /usr/sbin/nologin "$ATTA_RUN_USER"
  usermod -aG "$ATTA_GROUP" "$ATTA_WEB_USER"
  # the runner hands each proxy its run/ and state/ folders, so it is in the proxy user's group
  usermod -aG "$ATTA_GROUP,$ATTA_PROXY_USER" "$ATTA_RUN_USER"
  mkdir -p "$ATTA_RUN_HOME"; chown "$ATTA_RUN_USER:$ATTA_RUN_USER" "$ATTA_RUN_HOME"; chmod 700 "$ATTA_RUN_HOME"
}

atta_join_docker() {
  getent group docker >/dev/null || groupadd --system docker
  usermod -aG docker "$ATTA_RUN_USER"
}

atta_secure_state() {
  # atta_secure_state ROOT — every run: who owns what under the data folder (v116 layout).
  #   ROOT            root:atta 3771  services create/replace their OWN files here (catalogue, package/...);
  #                                   the sticky bit stops anyone but root deleting or renaming another's
  #                                   (.env above all); passable, not listable, by the proxy user
  #   .env, TEST_ACCOUNTS.txt   root 0600 (systemd reads .env as root before starting a service)
  #   inbox, state/{builds,alerts,front-door-choices}  runner:atta 2770  the gateway writes, the runner works
  #   state/users.json          web:atta 0640  the gateway may disable accounts; the runner reads roles
  #   state/gateway             web:atta 2770  login throttling, revoked sessions
  #   state/**, library, package, work, catalogue files  runner:atta, group-readable, nothing for others
  #   adm/requests              runner 0700    system-update requests handed to deployd (which re-checks admin)
  # App data written by containers under state/runner/work/<app>/data/ keeps its owners.
  local root="$1" f d
  local as_root=0; [ "$(id -u)" = 0 ] && getent passwd "$ATTA_RUN_USER" >/dev/null && as_root=1
  local run="$ATTA_RUN_USER" web="$ATTA_WEB_USER" g="$ATTA_GROUP"
  mkdir -p "$root"/{inbox,state,state/builds,state/alerts,state/front-door-choices,state/gateway,state/apps,state/runner,library,package,work,proxy,adm/requests}
  if [ "$as_root" = 1 ]; then
    chown root:"$g" "$root"; chmod 3771 "$root"   # sticky + setgid: new entries join the shared group
    for d in state library package work quarantine inbox; do
      [ -e "$root/$d" ] || continue
      find "$root/$d" -path "$root/state/runner/work/*/data/*" -prune -o -path "$root/state/gateway" -prune \
           -o \( -type f -o -type d \) -exec chown -h "$run:$g" {} +
      find "$root/$d" -path "$root/state/runner/work/*/data/*" -prune -o -type d -exec chmod g+rxs,o-rwx {} + \
           -o -type f -exec chmod g+r,o-rwx {} +
    done
    for f in app_catalogue.json upstream_apps.json targets.json coolify_resources.json; do
      [ -f "$root/$f" ] && { chown "$run:$g" "$root/$f"; chmod 640 "$root/$f"; }
    done
    for d in inbox state/builds state/alerts state/front-door-choices; do chmod 2770 "$root/$d"; done
    chown -R "$web:$g" "$root/state/gateway"; chmod 2770 "$root/state/gateway"
    [ -f "$root/state/users.json" ] && { chown "$web:$g" "$root/state/users.json"; chmod 640 "$root/state/users.json"; }
    chown "$run:$g" "$root/proxy"; chmod 755 "$root/proxy"
    chown root:root "$root/adm"; chmod 755 "$root/adm"
    # v117: bundles put on the server itself: root's alone (the pipeline reads, never writes, never forges).
    mkdir -p "$root/local-inbox"; chown root:root "$root/local-inbox"; chmod 755 "$root/local-inbox"
    chown "$run:$run" "$root/adm/requests"; chmod 700 "$root/adm/requests"
    # the pipeline replaces it from an admin's ATTa bundle; everyone reads it
    [ -f "$root/front-door.html" ] && { chown "$run:$g" "$root/front-door.html"; chmod 644 "$root/front-door.html"; }
    [ -f "$root/state/trusted_apps.json" ] && { chown root:root "$root/state/trusted_apps.json"; chmod 644 "$root/state/trusted_apps.json"; }
  fi
  for f in "$root/.env" "$root/TEST_ACCOUNTS.txt"; do
    [ -f "$f" ] || continue
    [ "$(id -u)" = 0 ] && chown root:root "$f"
    chmod 600 "$f"
  done
}

atta_install_proxy_runner() {
  # atta_install_proxy_runner SRC — the root-owned proxy wrapper and the ONE sudoers rule that lets the runner
  # start/stop skin proxies as the proxy user (a user with fewer rights than the runner itself).
  local src="$1" rule
  install -d -o root -g root -m 0755 /usr/local/lib/atta
  install -o root -g root -m 0755 "$src" /usr/local/lib/atta/run-proxy
  rule="$(mktemp)"
  printf '%s\n' "# v116 (ATTa): the runner may start and stop skin proxies as $ATTA_PROXY_USER. Nothing else." \
    "Defaults:$ATTA_RUN_USER !requiretty" \
    "$ATTA_RUN_USER ALL=($ATTA_PROXY_USER) NOPASSWD: /usr/local/lib/atta/run-proxy" > "$rule"
  visudo -cf "$rule" >/dev/null || { rm -f "$rule"; echo "sudoers rule for the proxy runner is invalid" >&2; return 1; }
  install -o root -g root -m 0440 "$rule" /etc/sudoers.d/atta-proxy; rm -f "$rule"
}

atta_write_default_env() {
  # atta_write_default_env FILE ROOT — a fresh server's .env, created 0600 in one step (v117).
  # The fixed text is a QUOTED heredoc: bash never runs anything inside it. v114.2-v116 used an unquoted one
  # whose comment held `bash run` in backticks, so writing .env re-ran the whole deploy, nested, as root, and
  # pasted its output into the file. The two generated values are added with printf, never interpolated.
  local f="$1" root="$2" secret tmp
  [[ "$root" =~ ^/[A-Za-z0-9._/-]+$ ]] || { echo "atta_write_default_env: bad root folder: $root" >&2; return 1; }
  secret="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')" || return 1
  [[ "$secret" =~ ^[A-Za-z0-9_-]{48,}$ ]] || { echo "atta_write_default_env: could not make a secret" >&2; return 1; }
  tmp="$(mktemp "$(dirname "$f")/.env.new.XXXXXX")" || return 1
  chmod 600 "$tmp"
  {
    printf 'APP_BUILDER_ROOT=%s\n' "$root"
    printf 'APP_BUILDER_SESSION_SECRET=%s\n' "$secret"
    cat <<'EOF'
APP_BUILDER_HOST=127.0.0.1
APP_BUILDER_PORT=8787
APP_BUILDER_BROWSER_CHECK=true
APP_BUILDER_WATCHER_INTERVAL=300
# This file is DATA, read by envfile.py and systemd; it is never run as a shell script. Values with spaces or
# any of ; & | < > ( ) # ~ * ? ! must be in quotes, and $ ` \ are refused (nothing is ever expanded).
# Coolify hand-off (docs/COOLIFY-HANDOFF.md). Qualified builds wait in the outbox until both are set.
COOLIFY_URL=
COOLIFY_TOKEN=
# Self-healing tier 3 (LLM). Blank = that tier is skipped and failures go straight to a human.
ANTHROPIC_API_KEY=
# v116: the LLM repair tier needs this as well as the key (the Front Door uses the same key). It can only
# edit overlay data (CSS/JSON), never code, and every proxy it restarts runs unprivileged.
APP_BUILDER_HEAL_LLM=false
# Public web address(es) and the email for the free HTTPS certificate (Let's Encrypt).
# DNS for each name must already point at this server, ports 80+443 open. Run "sudo bash run" again after setting.
APP_BUILDER_DOMAIN=
APP_BUILDER_LETSENCRYPT_EMAIL=
# v116: without HTTPS nginx answers on 127.0.0.1 only. true = serve plain HTTP publicly anyway (not advised).
APP_BUILDER_ALLOW_PUBLIC_HTTP=false
# v116: pre-made test accounts (tester01..NN). 0 on a server; set a number to create them, then hand them out.
APP_BUILDER_TEST_ACCOUNTS=0
# v116: what app containers may reach besides each other: public = the internet (never metadata, private
# networks or this server); deny = nothing outside their own network.
APP_BUILDER_APP_EGRESS=public
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
  } >"$tmp" || { rm -f "$tmp"; return 1; }
  mv -f "$tmp" "$f"
}

atta_env_get() {
  # atta_env_get ENVFILE NAME [DEFAULT] — one value, read as data by the same parser systemd's view is checked
  # against (never sed/grep: those disagree with it on quotes and repeated names).
  python3 "$ATTA_LIB_DIR/envfile.py" get "$@"
}

atta_env_run() {
  # atta_env_run ENVFILE KEYS -- command... — run a command with KEYS read from ENVFILE as data.
  local envf="$1" keys="$2"; shift 2; [ "${1:-}" = "--" ] && shift
  if [ -f "$envf" ]; then
    python3 "$ATTA_LIB_DIR/envfile.py" run "$envf" --keys "$keys" -- "$@"
  else
    "$@"
  fi
}

# ============================================================================================================ v117
# The deploy is a transaction: everything that can fail is done OFF to the side first (stage, packages, Docker,
# browser, a smoke test of the new gateway); then a snapshot of every file the switch will change is taken, with a
# restore.sh that puts it all back exactly; then the switch; then ATTa-specific checks through the real proxy. The
# release becomes known-good only after all of that (adm/releases.py). See docs/DEPLOYMENT.md.

ATTA_UNITS="app-builder-gateway.service app-builder-pipeline.service app-builder-watcher.service atta-deployd.service atta-block-metadata.service atta-tls-check.service atta-tls-check.timer atta-certbot-renew.service atta-certbot-renew.timer"
ATTA_RESTART_UNITS="app-builder-gateway.service app-builder-pipeline.service app-builder-watcher.service"
ATTA_ACME_ROOT="${ATTA_ACME_ROOT:-/var/lib/atta-acme}"
ATTA_LETSENCRYPT="${ATTA_LETSENCRYPT:-/etc/letsencrypt}"

atta_fail() {
  # atta_fail MESSAGE — the one line an operator reads; bootstrap.sh's ERR trap repeats it as the reason.
  # shellcheck disable=SC2034  # read by bootstrap.sh's ERR trap (on_failure)
  ATTA_LAST_FAIL="$*"
  echo "DEPLOYMENT FAILED: $*" >&2
  return 1
}

atta_ds() { python3 "$ATTA_LIB_DIR/deployd/deploystate.py" "$@"; }
atta_rel() { python3 "$ATTA_LIB_DIR/deployd/adm/releases.py" "$@"; }

atta_stage_code() {
  # atta_stage_code SRC RELEASES — a NEW, checked release folder built from SRC (printed). Nothing live changes.
  # (adm/releases.py stage: regular files only, compile-checked, readable by the service users, renamed into place.)
  python3 "$ATTA_LIB_DIR/deployd/adm/releases.py" stage "$1" "$2"
}

atta_deploy_lock() {
  # atta_deploy_lock LOCKFILE — one deploy at a time, server-wide: the SAME lock deployd and deployctl take.
  # Under ADM it is inherited (ATTA_DEPLOY_LOCK_FD, checked to really be that lock and held); run by hand it is
  # taken here. The fd stays open in bash and every child inherits it, so the whole install holds the lock.
  local lock="$1" fd="${ATTA_DEPLOY_LOCK_FD:-}"
  mkdir -p "$(dirname "$lock")"
  if [ -n "$fd" ]; then
    [[ "$fd" =~ ^[0-9]+$ ]] && [ "$(readlink -f "/proc/self/fd/$fd" 2>/dev/null)" = "$(readlink -f "$lock")" ] \
      || atta_fail "ATTA_DEPLOY_LOCK_FD=$fd is not open on the deploy lock $lock" || return 1
    flock -n "$fd" || atta_fail "the inherited deploy lock is not held" || return 1
    return 0
  fi
  exec {ATTA_LOCK_FD}>>"$lock"
  if ! flock -n "$ATTA_LOCK_FD"; then
    local who
    who="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("deployment %s (pid %s since %s)" % (d.get("deployment_id"), d.get("pid"), d.get("acquired_at")))' "$lock.owner.json" 2>/dev/null || echo "another deployment")"
    atta_fail "$who is running (deploy lock $lock). Nothing was changed. See: deployctl status"
    return 1
  fi
  export ATTA_DEPLOY_LOCK_FD="$ATTA_LOCK_FD"
}

atta_os() {
  # atta_os — "<ID>-<VERSION_ID>" from /etc/os-release, read as DATA (never sourced): e.g. ubuntu-24.04, amzn-2023.
  python3 - "${ATTA_OS_RELEASE:-/etc/os-release}" <<'PY'
import shlex, sys
d = {}
try:
    for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            try:
                d[k] = (shlex.split(v) or [""])[0]
            except ValueError:
                pass
except OSError:
    pass
print(f'{d.get("ID", "unknown")}-{d.get("VERSION_ID", "unknown")}')
PY
}

atta_os_supported() {
  # The two systems this release is built and tested for. Anything else needs ATTA_ALLOW_UNSUPPORTED_OS=1.
  case "$1" in ubuntu-24.04|amzn-2023) return 0 ;; *) return 1 ;; esac
}

atta_install_packages() {
  # atta_install_packages OS — the system packages ATTa needs (browser libraries: see atta_install_browser).
  local os="$1"
  case "$os" in
    amzn-*|fedora-*|rhel-*|rocky-*|almalinux-*)
      dnf install -y nodejs python3-pip nginx unzip git openssl \
        nss nspr atk at-spi2-atk at-spi2-core cups-libs libdrm libxkbcommon \
        libXcomposite libXdamage libXext libXfixes libXrandr libxshmfence \
        pango cairo alsa-lib mesa-libgbm liberation-fonts \
        || atta_fail "could not install the system packages with dnf on $os (see the lines above)" ;;
    ubuntu-*|debian-*)
      { apt-get update -y && DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs python3-pip nginx unzip git \
          curl ca-certificates openssl; } \
        || atta_fail "could not install the system packages with apt-get on $os (see the lines above)" ;;
    *) atta_fail "no package list for $os (supported: Ubuntu 24.04, Amazon Linux 2023)" ;;
  esac
}

atta_install_browser() {
  # atta_install_browser OS CODE — pinned Playwright + its Chromium, the OS libraries Chromium needs, then a REAL
  # launch as the runner user, using the checker in the new release CODE (readable by the service users; the
  # unpacked bundle often sits in a private home folder they cannot enter). Every failure is one
  # "DEPLOYMENT FAILED: ..." line with the reason, never a bare traceback.
  local os="$1" code="$2" out rc
  pip_install(){ python3 -m pip install --break-system-packages "$@" 2>/dev/null || python3 -m pip install "$@"; }
  pip_install --disable-pip-version-check -r "$ATTA_LIB_DIR/requirements-server.txt" \
    || atta_fail "could not install the pinned Python packages (requirements-server.txt)" || return 1
  for m in yaml playwright anthropic; do
    python3 -c "import $m" >/dev/null 2>&1 || atta_fail "Python package $m is missing after install" || return 1
  done
  export PLAYWRIGHT_BROWSERS_PATH="$ATTA_BROWSERS"
  out="$(mktemp)"
  rc=0; python3 -m playwright install chromium >"$out" 2>&1 || rc=$?   # (|| keeps set -e / ERR out of it)
  if [ "$rc" != 0 ]; then
    tail -n 5 "$out" >&2; rm -f "$out"
    atta_fail "could not download the pinned Chromium ($(python3 -c 'import importlib.metadata as m;print("playwright "+m.version("playwright"))' 2>/dev/null))"
    return 1
  fi
  case "$os" in
    ubuntu-*|debian-*)
      # Playwright's own list for this Ubuntu, installed with apt (the dnf path lists them in atta_install_packages).
      rc=0; python3 -m playwright install-deps chromium >"$out" 2>&1 || rc=$?
      if [ "$rc" != 0 ]; then
        tail -n 8 "$out" >&2; rm -f "$out"
        atta_fail "the browser's system libraries could not be installed on $os (playwright install-deps chromium)"
        return 1
      fi ;;
  esac
  rm -f "$out"
  chmod -R a+rX "$ATTA_BROWSERS"
  atta_browser_launch_check "$code"
}

atta_browser_launch_check() {
  # atta_browser_launch_check CODE — a real headless Chromium, as the runner user (the one the watcher's browser
  # stage runs as), with the checker from the release folder CODE.
  local code="$1" out
  out="$(runuser -u "$ATTA_RUN_USER" -- env HOME="$ATTA_RUN_HOME" PLAYWRIGHT_BROWSERS_PATH="$ATTA_BROWSERS" \
         python3 "$code/atta_health.py" --launch-only 2>&1)" \
    || { printf '%s\n' "$out" | tail -n 3 >&2; atta_fail "the browser cannot launch: $(printf '%s\n' "$out" | tail -n 1)"; return 1; }
  echo "PLAYWRIGHT_BROWSER_PASS"
}

atta_candidate_check() {
  # atta_candidate_check CODE — BEFORE anything live changes: start the NEW release's gateway from its own folder,
  # as the web user, on a free loopback port, against a throwaway data folder; it must prove its identity and serve
  # ATTa's login as that release. Proves the new code starts, imports and answers — off to the side.
  local code="$1" tmp port secret pid ok=0 as=()
  tmp="$(mktemp -d /tmp/atta-candidate.XXXXXX)" || return 1
  port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
  secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  mkdir -p "$tmp/root"
  printf 'APP_BUILDER_ROOT=%s\nAPP_BUILDER_HOST=127.0.0.1\nAPP_BUILDER_PORT=%s\nAPP_BUILDER_SESSION_SECRET=%s\nAPP_BUILDER_TEST_ACCOUNTS=0\n' \
    "$tmp/root" "$port" "$secret" >"$tmp/.env"
  chmod 600 "$tmp/.env"
  if [ "$(id -u)" = 0 ] && getent passwd "$ATTA_WEB_USER" >/dev/null; then
    # setpriv EXECs (runuser forks): the pid below is the gateway itself, so killing it leaves nothing behind.
    chown -R "$ATTA_WEB_USER" "$tmp"
    as=(setpriv --reuid="$(id -u "$ATTA_WEB_USER")" --regid="$(id -g "$ATTA_WEB_USER")" --init-groups --)
  fi
  "${as[@]}" env -i PATH="$PATH" HOME="$tmp" python3 "$code/envfile.py" run "$tmp/.env" --all -- \
      python3 "$code/accounts.py" init >"$tmp/init.log" 2>&1 \
    && { "${as[@]}" env -i PATH="$PATH" HOME="$tmp" python3 "$code/envfile.py" run "$tmp/.env" --all -- \
           python3 "$code/gateway.py" >"$tmp/gateway.log" 2>&1 & pid=$!; } \
    && python3 "$code/atta_health.py" --env "$tmp/.env" --expect-release "$(basename "$code")" --timeout 40 \
    && ok=1
  if [ -n "${pid:-}" ]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi
  if [ "$ok" != 1 ]; then
    tail -n 15 "$tmp/init.log" "$tmp/gateway.log" >&2 2>/dev/null
    rm -rf "$tmp"
    atta_fail "the new release's gateway did not start and prove itself (smoke test, nothing live was changed)"
    return 1
  fi
  rm -rf "$tmp"
  echo "CANDIDATE_PASS $(basename "$code")"
}

atta_snapshot() {
  # atta_snapshot TXN APP ROOT CHECKER_DIR — copy every file the switch is about to change, and write TXN/restore.sh,
  # which puts them ALL back (units, egress script, nginx site + main config + stock site link, proxy.json, the
  # code symlink), restarts, and verifies the previous release with its own kind of checks. Self-contained: it
  # carries its own copy of the checker, so it works whatever state the new release is in.
  local txn="$1" app="$2" root="$3" chk="$4" u prev
  mkdir -p "$txn/units" "$txn/nginx" "$txn/checker"
  chmod 700 "$txn"
  for u in $ATTA_UNITS; do
    if [ -f "/etc/systemd/system/$u" ]; then cp -p "/etc/systemd/system/$u" "$txn/units/$u"; else : >"$txn/units/$u.absent"; fi
  done
  if [ -f /usr/local/sbin/atta-block-metadata ]; then cp -p /usr/local/sbin/atta-block-metadata "$txn/egress"; else : >"$txn/egress.absent"; fi
  if [ -f /etc/nginx/conf.d/app-builder.conf ]; then cp -p /etc/nginx/conf.d/app-builder.conf "$txn/nginx/site"; else : >"$txn/nginx/site.absent"; fi
  [ -f /etc/nginx/nginx.conf ] && cp -p /etc/nginx/nginx.conf "$txn/nginx/main"
  if [ -L /etc/nginx/sites-enabled/default ]; then readlink /etc/nginx/sites-enabled/default >"$txn/nginx/stock-link"; fi
  if [ -f "$root/state/proxy.json" ]; then cp -p "$root/state/proxy.json" "$txn/proxy.json"; else : >"$txn/proxy.json.absent"; fi
  cp -p "$chk/atta_health.py" "$chk/atta_identity.py" "$chk/envfile.py" "$txn/checker/"
  # readlink -e: the target must EXIST. (-f echoes a missing path back: on a first install that made "the previous
  # release" /opt/app-builder itself, and the restore pointed it at itself.)
  prev="$(readlink -e "$app" 2>/dev/null || true)"
  if [ -n "$prev" ] && { [ "$prev" = "$app" ] || [ -L "$prev" ] || [ ! -d "$prev" ]; }; then
    atta_fail "the live code at $app does not resolve to a release folder ($prev); refusing to snapshot it"; return 1
  fi
  {
    echo '#!/usr/bin/env bash'
    echo '# restore.sh — written by bootstrap.sh BEFORE it switched anything. Puts back exactly what was live.'
    printf 'PREV=%q\nAPP=%q\nROOT=%q\nRESTART_UNITS=%q\n' "$prev" "$app" "$root" "$ATTA_RESTART_UNITS"
    cat <<'RESTORE'
set -uo pipefail
TXN="$(cd "$(dirname "$0")" && pwd)"
fail(){ echo "RESTORE FAILED: $*" >&2; exit 1; }
if [ -n "$PREV" ]; then
  # The previous release must be a real folder of its own, never APP itself or a link.
  { [ -d "$PREV" ] && [ ! -L "$PREV" ] && [ "$PREV" != "$APP" ]; } || fail "the previous release $PREV is not a release folder"
fi
for f in "$TXN"/units/*; do
  n="$(basename "$f")"
  case "$n" in *.absent) rm -f "/etc/systemd/system/${n%.absent}" ;; *) install -m 0644 "$f" "/etc/systemd/system/$n" ;; esac
done
if [ -f "$TXN/egress" ]; then install -m 0755 "$TXN/egress" /usr/local/sbin/atta-block-metadata; else rm -f /usr/local/sbin/atta-block-metadata; fi
if [ -f "$TXN/nginx/site" ]; then install -m 0644 "$TXN/nginx/site" /etc/nginx/conf.d/app-builder.conf; else rm -f /etc/nginx/conf.d/app-builder.conf; fi
[ -f "$TXN/nginx/main" ] && install -m 0644 "$TXN/nginx/main" /etc/nginx/nginx.conf
mkdir -p "$ROOT/state"
if [ -f "$TXN/proxy.json" ]; then install -m 0644 "$TXN/proxy.json" "$ROOT/state/proxy.json"; else rm -f "$ROOT/state/proxy.json"; fi
if [ -z "$PREV" ]; then
  # First install: nothing was live before. Undo it: ATTa's services stopped and disabled, the code link removed.
  for u in $RESTART_UNITS atta-deployd.service; do systemctl disable --now "$u" >/dev/null 2>&1 || true; done
  [ -L "$APP" ] && rm -f "$APP"
else
  ln -sfn "$PREV" "$APP.restore" && mv -Tf "$APP.restore" "$APP" || fail "could not point $APP back at $PREV"
fi
systemctl daemon-reload || fail "systemctl daemon-reload"
if [ -n "$PREV" ]; then
  for u in $RESTART_UNITS; do [ -f "/etc/systemd/system/$u" ] && { systemctl restart "$u" || fail "could not restart $u"; }; done
  systemctl restart atta-block-metadata.service >/dev/null 2>&1 || true
fi
if command -v nginx >/dev/null 2>&1; then
  if [ -f "$TXN/nginx/stock-link" ] && [ -d /etc/nginx/sites-enabled ]; then
    ln -sfn "$(cat "$TXN/nginx/stock-link")" /etc/nginx/sites-enabled/default
    # The stock site comes back only if nginx accepts it here (e.g. it listens on [::] where IPv6 is off).
    nginx -t >/dev/null 2>&1 || { rm -f /etc/nginx/sites-enabled/default; echo "restore: the stock nginx site stays disabled (it does not pass nginx -t here)" >&2; }
  fi
  nginx -t || fail "nginx -t"
  if systemctl is-active --quiet nginx; then systemctl reload nginx || fail "nginx reload"; fi
fi
if [ -z "$PREV" ]; then
  if python3 "$TXN/checker/atta_health.py" --env "$ROOT/.env" --direct --timeout 3 >/dev/null 2>&1; then
    fail "ATTa still answers after undoing the first install"
  fi
  echo "RESTORE VERIFIED: first install undone (nothing was live before; ATTa's services are stopped)"
  exit 0
fi
if python3 -c 'import json,sys; sys.exit(0 if "health_identity_v1" in (json.load(open(sys.argv[1])).get("features") or []) else 1)' "$PREV/release.json" 2>/dev/null; then
  python3 "$TXN/checker/atta_health.py" --env "$ROOT/.env" --direct --proxy --proxy-file "$ROOT/state/proxy.json" \
    --expect-release "$(basename "$PREV")" --timeout 90 || fail "the previous release did not pass its checks again"
else
  python3 "$TXN/checker/atta_health.py" --env "$ROOT/.env" --direct --legacy --timeout 90 \
    || fail "the previous (pre-v117) release did not answer its health check again"
fi
echo "RESTORE VERIFIED: $PREV"
RESTORE
  } >"$txn/restore.sh"
  chmod 700 "$txn/restore.sh"
}

atta_cert_renewal() {
  # atta_cert_renewal APP ROOT — certificates renew by themselves, nginx picks the new one up, and a failure is SEEN:
  # a deploy hook reloads nginx after each renewal; the distribution's certbot timer (or ATTa's own) runs renewals;
  # atta-tls-check.timer checks the served and on-disk certificate every day and raises an ATTa alert (and a failed
  # unit in `systemctl --failed`) when it is close to expiry or renewals stopped working.
  local app="$1" root="$2" t=""
  mkdir -p "$ATTA_LETSENCRYPT/renewal-hooks/deploy"
  printf '#!/bin/sh\n# ATTa: pick up a renewed certificate.\nnginx -t && systemctl reload nginx\n' \
    >"$ATTA_LETSENCRYPT/renewal-hooks/deploy/atta-reload-nginx"
  chmod 755 "$ATTA_LETSENCRYPT/renewal-hooks/deploy/atta-reload-nginx"
  for u in certbot.timer certbot-renew.timer snap.certbot.renew.timer; do
    systemctl cat "$u" >/dev/null 2>&1 && { t="$u"; break; }
  done
  if [ -n "$t" ]; then
    systemctl enable --now "$t" >/dev/null 2>&1 || atta_fail "could not enable the certificate renewal timer $t" || return 1
    rm -f /etc/systemd/system/atta-certbot-renew.service /etc/systemd/system/atta-certbot-renew.timer
  else
    printf '[Unit]\nDescription=ATTa: renew Let'"'"'s Encrypt certificates\n[Service]\nType=oneshot\nExecStart=/usr/bin/env certbot renew -q\n' \
      >/etc/systemd/system/atta-certbot-renew.service
    printf '[Unit]\nDescription=ATTa: renew certificates twice a day\n[Timer]\nOnCalendar=*-*-* 03,15:17:00\nRandomizedDelaySec=1h\nPersistent=true\n[Install]\nWantedBy=timers.target\n' \
      >/etc/systemd/system/atta-certbot-renew.timer
    t=atta-certbot-renew.timer
  fi
  printf '[Unit]\nDescription=ATTa: check the HTTPS certificate (expiry, renewal, what nginx serves)\n[Service]\nType=oneshot\nEnvironmentFile=-%s/.env\nExecStart=/usr/bin/python3 %s/tls_check.py --renewal-timer %s\n' \
    "$root" "$app" "$t" >/etc/systemd/system/atta-tls-check.service
  printf '[Unit]\nDescription=ATTa: daily HTTPS certificate check\n[Timer]\nOnCalendar=daily\nRandomizedDelaySec=2h\nPersistent=true\n[Install]\nWantedBy=timers.target\n' \
    >/etc/systemd/system/atta-tls-check.timer
  systemctl daemon-reload
  systemctl enable --now "$t" atta-tls-check.timer >/dev/null 2>&1 || atta_fail "could not enable the certificate timers" || return 1
}

atta_issue_certificate() {
  # atta_issue_certificate EMAIL DOMAIN... — first certificate, by webroot (nginx is already serving the ACME path).
  # certbot never edits nginx: ATTa renders the HTTPS site itself once the files exist (nginx_site.py).
  local email="$1"; shift
  local args=() d
  for d in "$@"; do args+=(-d "$d"); done
  if ! command -v certbot >/dev/null 2>&1; then
    if command -v dnf >/dev/null 2>&1; then dnf install -y certbot || return 1
    elif command -v apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get install -y certbot || return 1
    else return 1; fi
  fi
  mkdir -p "$ATTA_ACME_ROOT"; chmod 755 "$ATTA_ACME_ROOT"
  certbot certonly --webroot -w "$ATTA_ACME_ROOT" -n --agree-tos -m "$email" --keep-until-expiring \
    --cert-name "$1" "${args[@]}"
}
