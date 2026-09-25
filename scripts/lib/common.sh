#!/usr/bin/env bash
# Shared helpers for the ATTa Coolify deploy kit.
# Sourced by the scripts in scripts/. Safe to source more than once.

if [ -n "${ATTA_COMMON_LOADED:-}" ]; then
    return 0
fi
ATTA_COMMON_LOADED=1

# Where Coolify keeps its state on the host. Overridable for tests only.
COOLIFY_DATA_DIR="${COOLIFY_DATA_DIR:-/data/coolify}"
COOLIFY_ENV_FILE="${COOLIFY_ENV_FILE:-$COOLIFY_DATA_DIR/source/.env}"

# Symbols allowed in the admin password. Chosen so the value survives, unquoted and
# unescaped, every place Coolify's installer and runtime put it:
#   - install.sh writes it with `sed "s|...|${value}|"`   -> no | & \
#   - docker compose env_file interpolation                -> no $
#   - Laravel's phpdotenv parser                           -> no whitespace # " ' ` $ \
# Coolify itself requires at least one symbol, so we need a non-empty safe set.
ATTA_PASSWORD_SYMBOLS='!%*+,-./:;<=>?@^_~'

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RED=$'\033[0;31m'
    C_GRN=$'\033[0;32m'
    C_YEL=$'\033[0;33m'
    C_BLU=$'\033[0;34m'
    C_RST=$'\033[0m'
else
    C_RED=''
    C_GRN=''
    C_YEL=''
    C_BLU=''
    C_RST=''
fi

info() { printf '%s[INFO]%s %s\n' "$C_BLU" "$C_RST" "$*"; }
ok() { printf '%s[ OK ]%s %s\n' "$C_GRN" "$C_RST" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$C_YEL" "$C_RST" "$*" >&2; }
fail() { printf '%s[FAIL]%s %s\n' "$C_RED" "$C_RST" "$*" >&2; }
die() {
    fail "$*"
    exit 1
}

# For set -e scripts: never exit silently. Report where an unexpected failure happened.
enable_error_trap() {
    set -E
    trap 'rc=$?; fail "Unexpected error (exit $rc) at ${BASH_SOURCE[0]:-$0}:${LINENO}: ${BASH_COMMAND}"' ERR
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "Run this as root (for example: sudo $0)."
    fi
}

# Load KEY=VALUE lines from a kit config file without executing it as shell code.
# Only keys in the allow-list are exported; anything else is rejected loudly so a
# typo in a key name can't silently drop a setting.
load_config() {
    local file="$1"
    local allowed=" COOLIFY_VERSION COOLIFY_SOURCE_ZIP COOLIFY_SOURCE_ZIP_SHA256 ROOT_USERNAME ROOT_USER_EMAIL ROOT_USER_PASSWORD AUTOUPDATE DOCKER_ADDRESS_POOL_BASE DOCKER_ADDRESS_POOL_SIZE REGISTRY_URL CREDENTIALS_FILE "
    local line key value lineno=0

    [ -f "$file" ] || die "Config file not found: $file (copy config/coolify.env.example to config/coolify.env)"

    while IFS= read -r line || [ -n "$line" ]; do
        lineno=$((lineno + 1))
        line="${line%$'\r'}"
        case "$line" in
        '' | '#'*) continue ;;
        esac
        if ! [[ $line =~ ^[A-Z_][A-Z0-9_]*= ]]; then
            die "$file:$lineno: expected KEY=VALUE, got: $line"
        fi
        key="${line%%=*}"
        value="${line#*=}"
        case "$allowed" in
        *" $key "*) ;;
        *) die "$file:$lineno: unknown setting '$key'" ;;
        esac
        # Allow optional matching surrounding quotes for readability.
        if [[ $value =~ ^\"(.*)\"$ ]] || [[ $value =~ ^\'(.*)\'$ ]]; then
            value="${BASH_REMATCH[1]}"
        fi
        # Empty means "use the default": leave it unset, because Coolify's installer
        # treats a set-but-empty REGISTRY_URL / DOCKER_ADDRESS_POOL_* as a real value.
        # Environment wins over the file so one-off overrides work: FOO=x ./install.sh
        if [ -n "$value" ] && [ -z "${!key:-}" ]; then
            printf -v "$key" '%s' "$value"
            export "${key?}"
        fi
    done <"$file"
}

# ---------------------------------------------------------------------------
# Validators. Each prints a reason to stderr and returns 1 on failure.
# They mirror (and in places tighten) Coolify's RootUserSeeder rules. If the seeder
# rejects the values it does NOT abort: it logs an error and leaves public sign-up
# open, so the first visitor to the dashboard becomes the admin. We refuse to install
# rather than let that happen.
# ---------------------------------------------------------------------------

validate_version() {
    local v="$1"
    if [[ $v =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?(-[0-9A-Za-z.]+)?$ ]]; then
        return 0
    fi
    fail "COOLIFY_VERSION '$v' is not a version like 4.3.23"
    return 1
}

validate_username() {
    local u="$1"
    if [[ $u =~ ^[A-Za-z0-9_-]{3,64}$ ]]; then
        return 0
    fi
    fail "ROOT_USERNAME must be 3-64 characters of letters, digits, '_' or '-' (no spaces)"
    return 1
}

# Syntax check only; domain resolvability is checked separately because it needs DNS.
validate_email_syntax() {
    local e="$1"
    if [[ ${#e} -le 254 && $e =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$ ]]; then
        return 0
    fi
    fail "ROOT_USER_EMAIL '$e' is not a valid email address"
    return 1
}

# Reserved / private top-level names that Coolify's email validator (egulias
# DNSCheckValidation, used by Laravel's email:dns rule) always rejects.
ATTA_RESERVED_TLDS=" test example invalid localhost local intranet internal private corp home lan "

# One DNS level passes when it has any MX/A/AAAA record and none of its MX records is
# an RFC 7505 "null MX" ("0 ."), which means the domain accepts no mail.
_email_dns_level_ok() {
    local host="$1" mx a aaaa
    mx="$(dig +short +time=3 +tries=2 MX "$host" 2>/dev/null)"
    a="$(dig +short +time=3 +tries=2 A "$host" 2>/dev/null)"
    aaaa="$(dig +short +time=3 +tries=2 AAAA "$host" 2>/dev/null)"
    [ -n "$mx$a$aaaa" ] || return 1
    if [ -n "$mx" ] && printf '%s\n' "$mx" | awk '$2 == "." || $2 == "" {found=1} END {exit !found}'; then
        return 1
    fi
    return 0
}

# Mirrors egulias DNSCheckValidation: reject single-label and reserved domains, then
# accept if the domain or any parent (at least two labels) passes the DNS level check.
# Sets EMAIL_DOMAIN_REASON on failure.
email_domain_acceptable() {
    local domain="${1#*@}"
    domain="$(printf '%s' "${domain%.}" | tr '[:upper:]' '[:lower:]')"
    EMAIL_DOMAIN_REASON=""
    if [[ $domain != *.* ]]; then
        EMAIL_DOMAIN_REASON="is a local name, not an internet domain"
        return 1
    fi
    case "$ATTA_RESERVED_TLDS" in
    *" ${domain##*.} "*)
        EMAIL_DOMAIN_REASON="uses the reserved top-level name '.${domain##*.}'"
        return 1
        ;;
    esac
    if ! command -v dig >/dev/null 2>&1; then
        # Without dig we can only check A/AAAA; null-MX detection needs dig.
        getent ahosts "$domain" >/dev/null 2>&1 && return 0
        EMAIL_DOMAIN_REASON="does not resolve (install dnsutils for a full check)"
        return 1
    fi
    local parts i n host
    IFS=. read -ra parts <<<"$domain"
    n=${#parts[@]}
    for ((i = n - 2; i >= 0; i--)); do
        host="$(
            IFS=.
            echo "${parts[*]:i}"
        )"
        if _email_dns_level_ok "$host"; then
            return 0
        fi
    done
    EMAIL_DOMAIN_REASON="has no usable mail DNS (no MX/A/AAAA record, or a 'null MX' saying it accepts no mail)"
    return 1
}

validate_email() {
    local e="$1"
    validate_email_syntax "$e" || return 1
    if ! email_domain_acceptable "$e"; then
        fail "ROOT_USER_EMAIL domain '${e#*@}' $EMAIL_DOMAIN_REASON; Coolify would reject it and leave sign-up open"
        return 1
    fi
    return 0
}

validate_password() {
    local p="$1"
    local ok_flag=0
    # Build a bracket expression from the symbol set. '-' goes last so it is literal.
    local sym_class='!%*+,./:;<=>?@^_~-'
    local allowed_re="^[A-Za-z0-9${sym_class}]+$"
    local symbol_re="[${sym_class}]"

    if [ "${#p}" -lt 12 ]; then
        fail "ROOT_USER_PASSWORD must be at least 12 characters"
        ok_flag=1
    fi
    if [ "${#p}" -gt 128 ]; then
        fail "ROOT_USER_PASSWORD must be at most 128 characters"
        ok_flag=1
    fi
    if ! [[ $p =~ $allowed_re ]]; then
        fail "ROOT_USER_PASSWORD may only use letters, digits and these symbols: $ATTA_PASSWORD_SYMBOLS"
        fail "  (characters such as \$ # & | \\ quotes and spaces break Coolify's .env handling)"
        ok_flag=1
    fi
    [[ $p =~ [a-z] ]] || {
        fail "ROOT_USER_PASSWORD needs a lowercase letter"
        ok_flag=1
    }
    [[ $p =~ [A-Z] ]] || {
        fail "ROOT_USER_PASSWORD needs an uppercase letter"
        ok_flag=1
    }
    [[ $p =~ [0-9] ]] || {
        fail "ROOT_USER_PASSWORD needs a digit"
        ok_flag=1
    }
    [[ $p =~ $symbol_re ]] || {
        fail "ROOT_USER_PASSWORD needs a symbol from: $ATTA_PASSWORD_SYMBOLS"
        ok_flag=1
    }
    return "$ok_flag"
}

# Coolify also rejects passwords found in the Have I Been Pwned corpus. Uses the
# k-anonymity API: only the first 5 hex chars of the SHA-1 leave the machine.
# Returns 0 = not pwned, 1 = pwned, 2 = could not check.
password_pwned_status() {
    local p="$1" hash prefix suffix body
    hash="$(printf '%s' "$p" | sha1sum | awk '{print toupper($1)}')"
    prefix="${hash:0:5}"
    suffix="${hash:5}"
    if ! body="$(curl -fsS --max-time 8 -H 'Add-Padding: true' "https://api.pwnedpasswords.com/range/$prefix" 2>/dev/null)"; then
        return 2
    fi
    if printf '%s\n' "$body" | tr -d '\r' | grep -q "^${suffix}:[1-9]"; then
        return 1
    fi
    return 0
}

# Generate a password that satisfies validate_password.
generate_password() {
    local body sym
    body="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 28)"
    sym="$(LC_ALL=C tr -dc "$ATTA_PASSWORD_SYMBOLS" </dev/urandom | head -c 4)"
    # Guarantee each class is present regardless of what urandom produced.
    printf '%s%s%s%s%s' "$body" "$sym" 'a' 'Z' '7'
}

# Tag of the running coolify image (e.g. 4.3.23), or nothing if there is no container.
# Always succeeds, like env_file_get.
coolify_running_version() {
    local image
    image="$(docker inspect --format '{{.Config.Image}}' coolify 2>/dev/null || true)"
    [ -z "$image" ] || printf '%s\n' "${image##*:}"
}

# Prints a key's value from an env file, or nothing if absent. Always succeeds, so it is
# safe in `x="$(env_file_get KEY)"` under set -e / pipefail (grep exits 1 on no match).
env_file_get() {
    local key="$1" file="${2:-$COOLIFY_ENV_FILE}"
    { grep -E "^${key}=" "$file" 2>/dev/null || true; } | tail -n1 | cut -d= -f2-
}

# True if coollabsio/coolify:<version> is published on Docker Hub.
coolify_image_exists() {
    local v="$1" code
    code="$(curl -sS -o /dev/null -m 15 -w '%{http_code}' \
        "https://hub.docker.com/v2/repositories/coollabsio/coolify/tags/$v" 2>/dev/null)"
    [ "$code" = "200" ]
}

# SHA-256 of the coolify-main.zip (Coolify 4.3.23 source) that ATTa v116 ships and pins.
# Its scripts/install.sh is byte-identical to the official v4.3.23 release installer.
ATTA_COOLIFY_ZIP_SHA256="509f4abb54c0a5fab0bce1c7447a5dcb35e91a3cfab636a3c35f57e8bfdc6609"

# Put Coolify's installer at $2, taken from a checksum-verified source zip ($1).
installer_from_zip() {
    local zip="$1" dest="$2" want="${COOLIFY_SOURCE_ZIP_SHA256:-$ATTA_COOLIFY_ZIP_SHA256}" got
    [ -f "$zip" ] || {
        fail "COOLIFY_SOURCE_ZIP not found: $zip"
        return 1
    }
    got="$(sha256sum "$zip" | awk '{print $1}')"
    if [ "$got" != "$want" ]; then
        fail "REFUSED: $zip has SHA-256 $got, expected $want (set COOLIFY_SOURCE_ZIP_SHA256 only for a zip you trust)"
        return 1
    fi
    command -v unzip >/dev/null 2>&1 || {
        fail "unzip is not installed (apt-get install -y unzip)"
        return 1
    }
    # The archive's top folder name varies (coolify-main/, coolify-4.3.23/ ...).
    local member
    member="$(unzip -Z1 "$zip" | grep -E '^[^/]+/scripts/install\.sh$' | head -n1 || true)"
    [ -n "$member" ] || {
        fail "$zip does not contain <folder>/scripts/install.sh"
        return 1
    }
    unzip -p "$zip" "$member" >"$dest"
    ok "Installer taken from checksum-verified $zip ($member)"
}

# Run Coolify's official installer for a version. The installer comes from
# COOLIFY_SOURCE_ZIP (checksum-verified) when set, otherwise it is downloaded
# (COOLIFY_INSTALLER_URL overrides the download location, for mirrors and tests).
# Extra KEY=VALUE arguments after the version are passed to it as environment.
run_coolify_installer() {
    local version="$1"
    shift
    local url="${COOLIFY_INSTALLER_URL:-https://cdn.coollabs.io/coolify/install.sh}"
    local dir installer rc
    dir="$(mktemp -d)"
    installer="$dir/coolify-install.sh"

    if [ -n "${COOLIFY_SOURCE_ZIP:-}" ]; then
        if ! installer_from_zip "$COOLIFY_SOURCE_ZIP" "$installer"; then
            rm -rf "$dir"
            return 1
        fi
    else
        info "Downloading Coolify installer from $url"
        if ! curl -fsSL --retry 3 --max-time 60 "$url" -o "$installer"; then
            rm -rf "$dir"
            fail "Could not download the Coolify installer from $url"
            return 1
        fi
    fi
    # Guard against a captive portal or error page being executed as root.
    if ! head -n1 "$installer" | grep -q '^#!/bin/bash' ||
        ! grep -q 'Coolify Installation' "$installer" ||
        ! grep -q 'ROOT_USER_PASSWORD' "$installer"; then
        rm -rf "$dir"
        fail "Downloaded file does not look like the Coolify installer. Refusing to run it."
        return 1
    fi
    info "Installer sha256: $(sha256sum "$installer" | awk '{print $1}')"
    info "Running the Coolify installer for version $version (this takes a few minutes)"

    # The installer names Coolify's host key after $USER and installs it into ~/.ssh, and
    # Coolify then logs into its own host as that user. Under cloud-init, cron or some
    # sudo setups $USER is empty or $HOME points elsewhere, which leaves Coolify unable
    # to deploy anything. Pin both to the account actually running the install.
    local run_user run_home
    run_user="$(id -un)"
    run_home="$(getent passwd "$run_user" | cut -d: -f6)"
    rc=0
    env USER="$run_user" HOME="${run_home:-/root}" "$@" bash "$installer" "$version" || rc=$?
    rm -rf "$dir"
    if [ "$rc" -ne 0 ]; then
        fail "The Coolify installer exited with status $rc. Log: $COOLIFY_DATA_DIR/source/installation-*.log"
        return "$rc"
    fi
    # The installer exits 0 on some fatal paths (unsupported OS, not root), so confirm
    # it actually produced a running Coolify at the requested version.
    local running
    running="$(coolify_running_version)"
    if [ "$running" != "$version" ]; then
        fail "Installer finished but coolify is running '${running:-nothing}', not $version"
        return 1
    fi
    return 0
}

# Query Coolify's database. Prints the result, or nothing if it can't connect.
coolify_db_query() {
    local user db
    user="$(env_file_get DB_USERNAME)"
    # DB_DATABASE is usually absent from .env; the compose default is "coolify".
    db="$(env_file_get DB_DATABASE)"
    docker exec coolify-db psql -U "${user:-coolify}" -d "${db:-coolify}" -tAc "$1" 2>/dev/null
}

coolify_admin_exists() {
    [ "$(coolify_db_query 'select count(*) from users where id = 0')" = "1" ]
}

# Make sure the admin account exists, re-running Coolify's RootUserSeeder if its
# first-boot attempt failed. Returns 1 (with Coolify's reason) if it still can't.
ensure_admin_account() {
    local attempts="${ADMIN_SEED_ATTEMPTS:-5}" delay="${ADMIN_SEED_DELAY:-10}" i out
    for ((i = 1; i <= attempts; i++)); do
        if coolify_admin_exists; then
            ok "Admin account is in place"
            return 0
        fi
        warn "Admin account not created yet (attempt $i/$attempts); re-running Coolify's admin seeder"
        out="$(docker exec coolify php artisan db:seed --class=RootUserSeeder --force 2>&1 || true)"
        if coolify_admin_exists; then
            ok "Admin account created by Coolify's seeder"
            return 0
        fi
        if printf '%s' "$out" | grep -q 'ERROR'; then
            printf '%s\n' "$out" | grep -A5 'ERROR' | sed 's/^/    coolify: /' >&2
        fi
        [ "$i" -eq "$attempts" ] || sleep "$delay"
    done
    fail "Coolify would not create the admin account. Public sign-up may be OPEN: firewall port 8000 now. See docs/TROUBLESHOOTING.md"
    return 1
}

# Path of the private key Coolify uses to manage its own host ("localhost", server 0).
# Coolify keeps keys in its database and writes them to ssh/keys/ssh_key@<uuid>.
coolify_localhost_key_file() {
    local uuid
    uuid="$(coolify_db_query 'select pk.uuid from servers s join private_keys pk on pk.id = s.private_key_id where s.id = 0')"
    [ -n "$uuid" ] || return 1
    printf '%s/ssh/keys/ssh_key@%s\n' "$COOLIFY_DATA_DIR" "$uuid"
}

# authorized_keys file for the user Coolify logs into its own host as (normally root).
coolify_localhost_authorized_keys() {
    local user home
    user="$(coolify_db_query 'select "user" from servers where id = 0')"
    home="$(getent passwd "${user:-root}" | cut -d: -f6)"
    printf '%s/.ssh/authorized_keys\n' "${home:-/root}"
}

proxy_running() {
    [ "$(docker inspect --format '{{.State.Running}}' coolify-proxy 2>/dev/null)" = "true" ]
}

# Have Coolify validate its own host ("This Machine" in onboarding) using its own job.
# On success Coolify marks the server usable and starts the reverse proxy (Traefik on
# ports 80/443), which every app, ATT included, is served through.
# Coolify creates its "localhost" server record (and its settings row) during first-boot
# seeding, which can finish after the container already reports healthy.
localhost_server_seeded() {
    [ "$(coolify_db_query 'select count(*) from servers s join server_settings ss on ss.server_id = s.id where s.id = 0')" = "1" ]
}

activate_localhost_server() {
    local out i
    info "Waiting for Coolify to register its localhost server"
    for ((i = 0; i < ${SERVER_SEED_WAIT:-60}; i++)); do
        localhost_server_seeded && break
        sleep 3
    done
    if ! localhost_server_seeded; then
        fail "Coolify never registered its localhost server. Check: docker logs coolify 2>&1 | grep -i seed"
        return 1
    fi
    info "Validating the localhost server with Coolify (this also starts the reverse proxy)"
    # shellcheck disable=SC2016 # PHP code: $s is a PHP variable, not shell
    out="$(timeout 300 docker exec coolify php artisan tinker --execute '
        App\Jobs\ValidateAndInstallServerJob::dispatchSync(App\Models\Server::find(0));
        $s = App\Models\Server::find(0);
        echo $s->settings->is_usable ? "usable" : "unusable: ".trim(strip_tags((string) $s->validation_logs));
    ' 2>&1 | tail -n1)"
    if [ "$out" != "usable" ]; then
        fail "Coolify could not validate its localhost server: ${out:-no output}"
        return 1
    fi
    ok "Localhost server validated by Coolify"
    wait_for_stable_proxy
}

proxy_answers() {
    proxy_running && [ "$(curl -fsS -m 3 http://127.0.0.1/ping 2>/dev/null)" = "OK" ]
}

# Coolify can start the proxy twice at once (the validation job's async start plus its
# once-a-minute server check). Each start removes and recreates coolify-proxy, so the two
# can race and leave no proxy at all. Only declare success once the proxy has stayed up
# for longer than one server-check cycle. If it stays down, start it again with Coolify's
# synchronous StartProxy (up to 3 times) and require stability again.
wait_for_stable_proxy() {
    local stable_needed="${PROXY_STABLE_SECONDS:-75}" deadline=$((SECONDS + ${PROXY_WAIT_SECONDS:-420}))
    local up_since=-1 down_since=$SECONDS restarts=0
    info "Waiting for the reverse proxy to be up and stable for ${stable_needed}s"
    while [ "$SECONDS" -lt "$deadline" ]; do
        if proxy_answers; then
            [ "$up_since" -ge 0 ] || up_since=$SECONDS
            if [ $((SECONDS - up_since)) -ge "$stable_needed" ]; then
                ok "Reverse proxy is running and answering on port 80 (stable for ${stable_needed}s)"
                return 0
            fi
            down_since=-1
        else
            up_since=-1
            [ "$down_since" -ge 0 ] || down_since=$SECONDS
            if [ "$restarts" -lt 3 ] && [ $((SECONDS - down_since)) -ge 90 ]; then
                restarts=$((restarts + 1))
                warn "Reverse proxy has been down for 90s; starting it with Coolify's StartProxy (attempt $restarts/3)"
                timeout 300 docker exec coolify php artisan tinker --execute \
                    'App\Actions\Proxy\StartProxy::run(App\Models\Server::find(0), async: false, force: true);' >/dev/null 2>&1 || true
                down_since=$SECONDS
            fi
        fi
        sleep 3
    done
    fail "Reverse proxy did not come up and stay up. Check: docker logs coolify-proxy, and Servers > localhost > Proxy in the dashboard"
    return 1
}
