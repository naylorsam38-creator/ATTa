#!/usr/bin/env bash
# Upgrades Coolify to a specific version: backup first, then the official installer,
# then verification. Use this when AUTOUPDATE=false, or to move to a version on purpose.
#
# Usage: sudo ./scripts/upgrade.sh 4.3.24
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
enable_error_trap

target="${1:-}"
target="${target#v}"
[ -n "$target" ] || die "Usage: $0 <version>   (for example: $0 4.3.24)"
validate_version "$target" || exit 1
require_root
[ -f "$COOLIFY_ENV_FILE" ] || die "Coolify is not installed; use scripts/install.sh."

current="$(coolify_running_version)"
if [ "$current" = "$target" ]; then
    ok "Already on Coolify $target."
    exit 0
fi

if ! coolify_image_exists "$target"; then
    die "No coollabsio/coolify:$target image on Docker Hub. Check the version at https://github.com/coollabsio/coolify/releases"
fi

info "Upgrading Coolify $current -> $target"
"$SCRIPT_DIR/backup.sh" || die "Backup failed; not upgrading."
run_coolify_installer "$target" || die "Upgrade failed. Restore with scripts/restore.sh and the backup above if needed."
EXPECTED_VERSION="$target" "$SCRIPT_DIR/verify.sh" || die "Upgrade ran but verification failed."
ok "Coolify upgraded to $target."
