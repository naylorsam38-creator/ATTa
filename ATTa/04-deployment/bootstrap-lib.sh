# bootstrap-lib.sh — functions bootstrap.sh uses (v114.1). Sourced, never run on its own.
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

atta_secure_state() {
  # atta_secure_state ROOT — every run, not just the first: owner root, nothing readable by others.
  # Directories are not recursed into: app containers write their own data under state/runner/work
  # with their own users, and bind mounts don't depend on the host folders above them.
  local root="$1" f
  [ -f "$root/.env" ] && { [ "$(id -u)" = 0 ] && chown root:root "$root/.env"; chmod 600 "$root/.env"; }
  # v115: the local inbox is where a bundle placed on the server itself is trusted from; it must stay
  # root-only or the pipeline refuses what is in it. Customer-secret receipts and their fingerprint key too.
  mkdir -p "$root/local-inbox"
  for f in "$root/coolify_resources.json" "$root/TEST_ACCOUNTS.txt" "$root"/state/*.json "$root"/state/apps/*.json \
           "$root/state/secret-fingerprint.key" "$root"/state/customer_secrets/*.json; do
    [ -f "$f" ] || continue
    [ "$(id -u)" = 0 ] && chown root:root "$f"
    chmod 600 "$f"
  done
  for f in "$root/state" "$root/state/apps" "$root/state/customer_secrets" "$root/local-inbox"; do
    [ -d "$f" ] || continue
    [ "$(id -u)" = 0 ] && chown root:root "$f"
    chmod 700 "$f"
  done
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

atta_stage_code() {
  # atta_stage_code SRC RELEASES — copy SRC into a fresh release folder and check it. Prints the folder.
  # Nothing live is touched; atta_activate_code switches to it. Nothing from an older release is
  # carried over, so files deleted upstream really disappear.
  local src="$1" rels="$2" ver stamp new bad rj
  rj="$src/../release.json"; [ -f "$rj" ] || rj="$src/release.json"   # a bundle, or an ADM backup of the live folder
  ver="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version","unknown"))' "$rj" 2>/dev/null || echo unknown)"
  ver="$(printf '%s' "$ver" | tr -c 'A-Za-z0-9._-' '_')"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$rels"
  find "$rels" -maxdepth 1 -name '.incoming-*' -mmin +60 -exec rm -rf {} + 2>/dev/null || true   # leftovers of an interrupted run
  new="$(mktemp -d "$rels/.incoming-${ver}-${stamp}-XXXX")"
  # Copy only what the code needs: regular files and folders, no symlinks, no caches.
  (cd "$src" && find . -path '*/__pycache__' -prune -o \( -type f -o -type d \) -print0 \
     | while IFS= read -r -d '' p; do
         if [ -d "$p" ]; then mkdir -p "$new/$p"; else cp -p "$p" "$new/$p"; fi
       done)
  [ -n "$(find "$src" -type l -not -path '*/__pycache__/*' -print -quit)" ] && \
    echo "note: symlinks in $src were not copied into the release" >&2
  bad="$(cd "$new" && python3 - <<'PY'
import pathlib
for p in sorted(pathlib.Path(".").rglob("*.py")):
    try: compile(p.read_bytes(), str(p), "exec")   # checked in memory: nothing written
    except (SyntaxError, ValueError): print(p)
PY
)"
  if [ -n "$bad" ]; then
    echo "DEPLOYMENT FAILED: python does not compile in the new release: $bad" >&2
    rm -rf "$new"; return 1
  fi
  local final="$rels/${ver}-${stamp}"
  [ -e "$final" ] && final="${final}-$$"
  mv "$new" "$final"
  echo "$final"
}

atta_activate_code() {
  # atta_activate_code APP RELEASES NEW — point APP at NEW with one atomic rename. The release that was
  # live is remembered in RELEASES/.previous and kept until pruned after a healthy deploy.
  local app="$1" rels="$2" final="$3" stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  # First run on a server laid out the old way: the live folder becomes a release of its own.
  if [ -d "$app" ] && [ ! -L "$app" ]; then
    mv "$app" "$rels/legacy-${stamp}"
    echo "note: moved the old code folder to $rels/legacy-${stamp}" >&2
  fi
  if [ -L "$app" ]; then readlink "$app" > "$rels/.previous"; else rm -f "$rels/.previous"; fi
  ln -sfn "$final" "$app.next"
  mv -Tf "$app.next" "$app"      # rename(2): the switch is atomic
}

atta_prune_code_releases() {
  # atta_prune_code_releases APP RELEASES — after a healthy deploy: keep the newest N and never the
  # live or previous one.
  local app="$1" rels="$2" live prev n=0 d
  live="$(readlink -f "$app" 2>/dev/null || true)"
  prev="$(cat "$rels/.previous" 2>/dev/null || true)"
  [ -n "$prev" ] && prev="$(readlink -f "$prev" 2>/dev/null || true)"
  for d in $(ls -1dt "$rels"/*/ 2>/dev/null); do
    d="${d%/}"; n=$((n + 1))
    [ "$(readlink -f "$d")" = "$live" ] && continue
    [ -n "$prev" ] && [ "$(readlink -f "$d")" = "$prev" ] && continue
    [ "$n" -le "$ATTA_KEEP_CODE_RELEASES" ] && continue
    rm -rf "$d"
  done
}

atta_restore_previous_code() {
  # atta_restore_previous_code APP RELEASES — point APP back at the release that was live before.
  local app="$1" rels="$2" prev
  prev="$(cat "$rels/.previous" 2>/dev/null || true)"
  [ -n "$prev" ] && [ -d "$prev" ] || return 1
  ln -sfn "$prev" "$app.next" && mv -Tf "$app.next" "$app"
}
