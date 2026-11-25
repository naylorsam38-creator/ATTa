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
# Dedicated unprivileged TEST identity (Stage 01B): the build controller runs every non-privileged acceptance test as
# this account, so test code cannot read the verifier's key directory or alter protected criteria and the ledger.
TESTER=atta-tester
id "$TESTER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash --gid atta-dev "$TESTER"
su -s /bin/sh "$TESTER" -c "git config --global --add safe.directory '*'" || true
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
# Encrypt the private key at rest (Stage 01B): test processes run by the gate are root in this environment, so file
# permissions alone cannot keep the key from them. The passphrase is supplied only while sealing.
if ! grep -q "ENCRYPTED PRIVATE KEY" "$KEY"; then
  [ -n "${ATTA_BUILD_SIGNING_KEY_PASSPHRASE:-}" ] || { echo "set ATTA_BUILD_SIGNING_KEY_PASSPHRASE (operator secret, never stored) to encrypt $KEY" >&2; exit 1; }
  "$PY" -I - "$KEY" <<'PYEOF'
import os, sys
from cryptography.hazmat.primitives import serialization
p = sys.argv[1]
key = serialization.load_pem_private_key(open(p, "rb").read(), password=None)
pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.BestAvailableEncryption(os.environ["ATTA_BUILD_SIGNING_KEY_PASSPHRASE"].encode()))
tmp = p + ".enc"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, pem); os.close(fd); os.replace(tmp, p)
PYEOF
  echo "signing key encrypted at rest"
fi
chown root:root "$KEY" "$ROOT/configuration/build/verifier_ed25519.pub"; chmod 0600 "$KEY"; chmod 0644 "$ROOT/configuration/build/verifier_ed25519.pub"
echo "protection applied: worker=$WORKER verifier=root key=$KEY"
echo "verify:  $PY -m backend.verification protection"
