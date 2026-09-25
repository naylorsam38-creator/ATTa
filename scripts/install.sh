#!/usr/bin/env bash
# Installs Coolify on this server, pinned to a known version, with the admin account
# created up front so the dashboard is never left open for anyone to claim.
#
# Usage:
#   sudo ./scripts/install.sh                 # uses config/coolify.env
#   sudo ./scripts/install.sh --dry-run       # validate + preflight only, change nothing
#   sudo ./scripts/install.sh --config /path/to/coolify.env
#
# What it does:
#   1. Loads and validates config (version, admin username/email/password).
#   2. Runs preflight checks (scripts/preflight.sh).
#   3. Downloads Coolify's official installer and runs it for the pinned version.
#   4. Runs scripts/verify.sh to prove the install is healthy and locked down.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
enable_error_trap

DEFAULT_COOLIFY_VERSION="4.3.23"

CONFIG_FILE="$KIT_DIR/config/coolify.env"
DRY_RUN=false
SKIP_PREFLIGHT=false

usage() {
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
    --config)
        CONFIG_FILE="${2:?--config needs a path}"
        shift 2
        ;;
    --dry-run)
        DRY_RUN=true
        shift
        ;;
    --skip-preflight)
        SKIP_PREFLIGHT=true
        shift
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    *) die "Unknown option: $1 (see --help)" ;;
    esac
done

load_config "$CONFIG_FILE"

COOLIFY_VERSION="${COOLIFY_VERSION:-$DEFAULT_COOLIFY_VERSION}"
COOLIFY_VERSION="${COOLIFY_VERSION#v}"
AUTOUPDATE="${AUTOUPDATE:-false}"
CREDENTIALS_FILE="${CREDENTIALS_FILE:-/root/coolify-admin-credentials.txt}"

# ---------------------------------------------------------------- validation
info "Validating configuration from $CONFIG_FILE"
errors=0
validate_version "$COOLIFY_VERSION" || errors=$((errors + 1))

case "$AUTOUPDATE" in
true | false) ;;
*)
    fail "AUTOUPDATE must be 'true' or 'false', got '$AUTOUPDATE'"
    errors=$((errors + 1))
    ;;
esac

[ -n "${ROOT_USERNAME:-}" ] || {
    fail "ROOT_USERNAME is not set"
    errors=$((errors + 1))
}
[ -n "${ROOT_USER_EMAIL:-}" ] || {
    fail "ROOT_USER_EMAIL is not set"
    errors=$((errors + 1))
}
[ -z "${ROOT_USERNAME:-}" ] || validate_username "$ROOT_USERNAME" || errors=$((errors + 1))
[ -z "${ROOT_USER_EMAIL:-}" ] || validate_email "$ROOT_USER_EMAIL" || errors=$((errors + 1))

PASSWORD_GENERATED=false
if [ -z "${ROOT_USER_PASSWORD:-}" ]; then
    ROOT_USER_PASSWORD="$(generate_password)"
    PASSWORD_GENERATED=true
    info "No ROOT_USER_PASSWORD set; generated a strong one."
fi
validate_password "$ROOT_USER_PASSWORD" || errors=$((errors + 1))

if [ "$errors" -eq 0 ]; then
    pwned=0
    password_pwned_status "$ROOT_USER_PASSWORD" || pwned=$?
    case "$pwned" in
    0) ok "Admin password is not in any known breach list" ;;
    1)
        fail "ROOT_USER_PASSWORD appears in a public breach list; Coolify would reject it. Pick another."
        errors=$((errors + 1))
        ;;
    *) warn "Could not reach api.pwnedpasswords.com to check the password against breach lists." ;;
    esac
fi

[ "$errors" -eq 0 ] || die "Configuration has $errors problem(s). Fix $CONFIG_FILE and re-run."
ok "Configuration valid: Coolify $COOLIFY_VERSION, admin '$ROOT_USERNAME' <$ROOT_USER_EMAIL>, AUTOUPDATE=$AUTOUPDATE"

# ---------------------------------------------------------------- preflight
if [ "$SKIP_PREFLIGHT" = true ]; then
    warn "Skipping preflight checks (--skip-preflight)."
else
    "$SCRIPT_DIR/preflight.sh" || die "Preflight failed. Nothing was changed."
fi

if [ "$DRY_RUN" = true ]; then
    echo
    ok "Dry run complete. Nothing was changed. Re-run without --dry-run to install."
    exit 0
fi

require_root

# ---------------------------------------------------------------- credentials
# Written before the install so they can't be lost if the install fails halfway.
umask 077
{
    echo "# Coolify admin login for $(hostname) - created $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# Move this into a password manager, then delete this file: shred -u $CREDENTIALS_FILE"
    echo "username=$ROOT_USERNAME"
    echo "email=$ROOT_USER_EMAIL"
    echo "password=$ROOT_USER_PASSWORD"
} >"$CREDENTIALS_FILE"
chmod 600 "$CREDENTIALS_FILE"
ok "Admin credentials saved to $CREDENTIALS_FILE (root-only)"

# ---------------------------------------------------------------- run installer
# Only pass optional settings that were actually set; the installer distinguishes
# "unset" from "empty" for these.
install_env=(
    "ROOT_USERNAME=$ROOT_USERNAME"
    "ROOT_USER_EMAIL=$ROOT_USER_EMAIL"
    "ROOT_USER_PASSWORD=$ROOT_USER_PASSWORD"
    "AUTOUPDATE=$AUTOUPDATE"
)
for key in REGISTRY_URL DOCKER_ADDRESS_POOL_BASE DOCKER_ADDRESS_POOL_SIZE; do
    if [ -n "${!key:-}" ]; then
        install_env+=("$key=${!key}")
    fi
done

run_coolify_installer "$COOLIFY_VERSION" "${install_env[@]}" ||
    die "Coolify installation failed. Fix the error above and re-run; the installer is safe to re-run."

# ---------------------------------------------------------------- admin lockdown
# Coolify creates the admin (and closes sign-up) once, during first boot. If its checks
# fail at that moment (e.g. a DNS hiccup while validating the email domain) it carries
# on with sign-up OPEN. Re-run Coolify's own seeder, with its own validation, until the
# admin exists. We never write the user row ourselves.
ensure_admin_account || die "Install stopped: no admin account. Nothing is lost; fix the reason above and re-run install.sh."
activate_localhost_server || die "Install stopped: Coolify can't manage this host yet. Fix the reason above and re-run install.sh."
echo
info "Verifying the installation"
EXPECTED_VERSION="$COOLIFY_VERSION" "$SCRIPT_DIR/verify.sh" ||
    die "Coolify installed but verification failed. See the checks above and docs/TROUBLESHOOTING.md."

echo
ok "Coolify $COOLIFY_VERSION is installed and verified."
echo
echo "Next steps (docs/RUNBOOK.md has the details):"
echo "  1. Open http://<this-server-ip>:8000, log in with the credentials in $CREDENTIALS_FILE,"
echo "     and in the onboarding choose 'This Machine' (already validated - it just confirms)"
if [ "$PASSWORD_GENERATED" = true ]; then
    echo "     (the password was generated for you - it is only in that file)"
fi
echo "  2. Copy $CREDENTIALS_FILE and $COOLIFY_ENV_FILE into your password manager"
echo "  3. Point a domain at this server and set it as the Coolify instance domain (gives you HTTPS)"
echo "  4. Once the domain works: close 6001/6002, allow 8000 only from the ATTa server"
echo "  5. Run: sudo ./scripts/backup.sh   (and schedule it - see RUNBOOK)"
echo "  6. Connect ATTa: sudo ./scripts/connect-atta.sh --map-apps   (docs/DEPLOY-ATT.md)"
