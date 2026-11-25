#!/usr/bin/env bash
# Apply OS-permission protection to the verification boundary (Stage 01A correction 8). Run as root (operator).
#   sudo scripts/protect_verifier.sh [--worker atta-builder] [--key /etc/atta-verifier/build_signing_ed25519.pem]
# Effect:
#   * creates group atta-dev and the unprivileged implementation account (default atta-builder);
#   * the working tree becomes group-editable for atta-dev EXCEPT the protected set (acceptance tests, verification
#     logic, build-controller definitions + seal + ledger, trusted controller configuration, trust store, public keys),
#     which become root-owned 0644/0755;
#   * every ancestor directory of a protected entry becomes root:atta-dev 1775 (sticky): the worker may add files but
#     can never rename, delete or replace root-owned entries;
#   * creates the Ed25519 build-signing key in a root-only directory (0700/0600) and installs its public key.
# Re-run after every checkout/pull: git does not preserve ownership.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKER=atta-builder; KEY=/etc/atta-verifier/build_signing_ed25519.pem
while [ $# -gt 0 ]; do case "$1" in --worker) WORKER="$2"; shift 2;; --key) KEY="$2"; shift 2;; *) echo "unknown $1"; exit 2;; esac; done
[ "$(id -u)" -eq 0 ] || { echo "run as root (the verifier/operator account)" >&2; exit 1; }
PY="${ATTA_VENV:-$ROOT/.venv}/bin/python"
getent group atta-dev >/dev/null || groupadd atta-dev
id "$WORKER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash --gid atta-dev "$WORKER"
usermod -g atta-dev "$WORKER"
# 1. tree: group-editable for the worker
chgrp -R atta-dev "$ROOT"
chmod -R g+rwX "$ROOT"
git -C "$ROOT" config core.sharedRepository group
su -s /bin/sh "$WORKER" -c "git config --global --add safe.directory '$ROOT'" || true
# 2. protected set: root-owned, not group/other writable
PROTECTED=$(cd "$ROOT" && "$PY" -I -P - <<'PYEOF'
import sys
from pathlib import Path
sys.path.insert(0, "backend/verification")
from build_controller import BuildController
from protection import protected_paths
bc = BuildController(Path(".").resolve())
for p in protected_paths(bc):
    print(p)
PYEOF
)
# 2a. ancestors first: root:atta-dev 1775 (worker may add files, never rename/delete root-owned entries)
while IFS= read -r p; do
  [ -n "$p" ] || continue
  d="$(dirname "$p")"
  while [ "${#d}" -ge "${#ROOT}" ]; do chown root:atta-dev "$d"; chmod 1775 "$d"; [ "$d" = "$ROOT" ] && break; d="$(dirname "$d")"; done
done <<< "$PROTECTED"
# 2b. then the protected entries themselves: root-owned, no group/other write (a protected directory must not even
#     accept new files, e.g. a planted conftest.py in tests/acceptance)
while IFS= read -r p; do
  [ -n "$p" ] || continue
  if [ -d "$p" ]; then chown -R root:root "$p"; chmod -R go-w "$p"; chmod 0755 "$p"
  else chown root:root "$p"; chmod 0644 "$p"; fi
done <<< "$PROTECTED"
# 3. signing key (root-only) + public key in the protected build directory
install -d -m 0700 -o root -g root "$(dirname "$KEY")"
if [ ! -f "$KEY" ]; then
  "$PY" -c "from backend.capability_registry.trust import generate_keypair; from pathlib import Path; generate_keypair(Path('$KEY'), Path('$ROOT/configuration/build/verifier_ed25519.pub'))"
fi
chown root:root "$KEY" "$ROOT/configuration/build/verifier_ed25519.pub"; chmod 0600 "$KEY"; chmod 0644 "$ROOT/configuration/build/verifier_ed25519.pub"
echo "protection applied: worker=$WORKER verifier=root key=$KEY"
echo "verify:  $PY -m backend.verification protection"
