#!/usr/bin/env bash
# ATTa v121.3 onto the running server, through the bundle's own Coolify kit. Run as root ON the server.
#
#   atta-upgrade-v121.3.sh --check       read-only: identity, bundle, rebuild v121.3 in a new release folder,
#                                        prove it is the exact tested tree, show server/ATTa/inbox state
#   atta-upgrade-v121.3.sh --deploy      the above, then keep the current image as a rollback tag and run
#                                        the kit's deploy-atta.sh (builds atta:v121 from v121.3, restarts the
#                                        Coolify service "atta"); verifies the RUNNING container's code
#   atta-upgrade-v121.3.sh --start-run   queue ONE full library run (inbox/<id>.library.json), only if the
#                                        pipeline is idle and no run is already queued
#
# v121.3 = the server's own read-only v121 bundle + tools/patches/v121-to-v121.3.patch (fetched from GitHub
# at a pinned commit, checksum-checked). No new zip transfer. Never deletes containers, volumes, Coolify or
# ATTa data; the old release folder and the old image (tagged) stay for rollback.
set -euo pipefail
export LC_ALL=C
MODE="${1:---check}"
TARGET_INSTANCE="i-0f13b8644b215931d"
BASE=/opt/atta
BUNDLE_SHA256=ccca34de3988653d4af13367dd86b5ae0297c16faffa5f784a2fbf858b31c601
PRISTINE="$BASE/bundles/ATTa-v121-collect-all-adaptive-ccca34de3988.zip"
PATCH_URL="https://raw.githubusercontent.com/naylorsam38-creator/ATTa/dd0489d/tools/patches/v121-to-v121.3.patch"
PATCH_SHA256=e611decaa36ae1b786a51d56c1c37dd7e461de2c9ea875aec8c443cf8e3fd7eb
TREE_FULL=145d3c550f315624f058e2113250f1abd6d473385ec9f4975f5e4d1a97978bf2   # 138 files, the tested tree
TREE_IMAGE=57e2630d1015131fcde9bcc2aa667d420e611fd90952d8a86a4171d5bffb2bcd  # 97 files, what the image holds
REL="$BASE/releases/v121.3-${TREE_FULL:0:12}"
DOMAINS="airexploit.com,www.airexploit.com"
ROOT_DATA=/srv/app-builder
say() { printf '[v121.3] %s\n' "$*"; }
die() { printf '[v121.3] STOP: %s (nothing deleted)\n' "$*"; exit 1; }
[ "$(id -u)" -eq 0 ] || die "run as root"

treehash() {   # <dir> [image]  -> "<sha> <files>"  (test-runner caches and __pycache__ are not code)
python3 - "$@" <<'PY'
import hashlib, sys
from pathlib import Path
root = Path(sys.argv[1]); image = len(sys.argv) > 2
h = hashlib.sha256(); n = 0
for p in sorted(root.rglob("*")):
    rel = p.relative_to(root)
    if {".git", "__pycache__", ".pytest_cache"} & set(rel.parts) or not p.is_file(): continue
    if image and rel.parts[0] in {"05-coolify", "tests", "Dockerfile", "docker-compose.coolify.yml"}: continue
    h.update(rel.as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest()); n += 1
print(h.hexdigest(), n)
PY
}
atta_ctr() { docker ps -a --filter label=coolify.type=service --format '{{.Names}}' | grep -E '^atta-[a-z0-9]{24}$' | head -1; }

# ---- 1. identity
TOKEN=$(curl -sf --noproxy '*' -m 3 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)
IID=$(curl -sf --noproxy '*' -m 3 ${TOKEN:+-H "X-aws-ec2-metadata-token: $TOKEN"} http://169.254.169.254/latest/meta-data/instance-id || echo "?")
[ "$IID" = "$TARGET_INSTANCE" ] || die "this is $IID, not $TARGET_INSTANCE"
say "instance $IID; disk free $(df -h / | awk 'NR==2{print $4}'); load $(cut -d' ' -f1-3 /proc/loadavg)"

# ---- 2. rebuild v121.3 from the read-only v121 bundle + the pinned patch, prove it is the tested tree
[ "$(sha256sum "$PRISTINE" | cut -d' ' -f1)" = "$BUNDLE_SHA256" ] || die "read-only v121 bundle missing or changed: $PRISTINE"
if [ -d "$REL/ATTa" ]; then
    say "release folder exists: $REL (verifying, not rebuilding)"
else
    tmp=$(mktemp -d "$BASE/releases/.v1213.XXXXXX")
    curl -fsS -m 60 -o "$tmp/p.patch" "$PATCH_URL" || die "could not fetch the patch"
    [ "$(sha256sum "$tmp/p.patch" | cut -d' ' -f1)" = "$PATCH_SHA256" ] || die "patch checksum mismatch"
    unzip -q "$PRISTINE" -d "$tmp"
    if command -v git >/dev/null; then
        git -C "$tmp/ATTa" apply --check "$tmp/p.patch" || die "patch does not apply"
        git -C "$tmp/ATTa" apply "$tmp/p.patch"
    else
        patch -d "$tmp/ATTa" -p1 --dry-run -s -f <"$tmp/p.patch" >/dev/null || die "patch does not apply"
        patch -d "$tmp/ATTa" -p1 -s -f <"$tmp/p.patch"
    fi
    install -m 444 "$tmp/p.patch" "$tmp/v121-to-v121.3.patch"; rm -f "$tmp/p.patch"
    mv "$tmp" "$REL"
    say "built $REL"
fi
chmod +x "$REL"/ATTa/05-coolify/kit/scripts/*.sh "$REL"/ATTa/run 2>/dev/null || true
read -r got n <<<"$(treehash "$REL/ATTa")"
[ "$got" = "$TREE_FULL" ] || die "tree $got ($n files) is NOT the tested tree $TREE_FULL"
say "tree = tested tree: $got ($n files); version $(python3 -c 'import json;print(json.load(open("'"$REL"'/ATTa/release.json"))["version"])')"

# ---- state now
C=$(atta_ctr || true)
say "ATTa container: ${C:-none} $( [ -n "$C" ] && docker inspect "$C" --format '{{.State.Status}} image={{.Config.Image}} ({{.Image}})' )"
say "images: $(docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | grep -E '^atta:' | tr '\n' ' ')"
say "inbox: $(find "$ROOT_DATA/inbox" -maxdepth 1 -type f -printf '%f ' 2>/dev/null)"
say "pipeline lock: $(cat "$ROOT_DATA/state/pipeline.lock" 2>/dev/null || echo none)"
say "status: $(python3 -c 'import json;d=json.load(open("'"$ROOT_DATA"'/state/status.json"));print(d.get("state"),d.get("updated_at"))' 2>/dev/null || echo ?)"
say "apps in library: $(find "$ROOT_DATA/library" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)"
[ "$MODE" = --check ] && { say "check only: nothing changed"; exit 0; }

if [ "$MODE" = --deploy ]; then
    if docker image inspect atta:v121 >/dev/null 2>&1; then
        tag="atta:v121-before-v121.3"
        docker image inspect "$tag" >/dev/null 2>&1 || docker tag atta:v121 "$tag"
        say "rollback image kept: $tag ($(docker image inspect "$tag" --format '{{.Id}}'))"
    fi
    "$REL/ATTa/05-coolify/kit/scripts/deploy-atta.sh" --atta-dir "$REL/ATTa" --domain "$DOMAINS" || die "deploy-atta.sh failed"
    for _ in $(seq 1 60); do
        C=$(atta_ctr || true)
        [ -n "$C" ] && [ "$(docker inspect "$C" --format '{{.State.Running}}')" = true ] && curl -fsS -m 5 http://127.0.0.1:8787/health >/dev/null 2>&1 && break
        sleep 5
    done
    C=$(atta_ctr); [ -n "$C" ] || die "no ATTa container after deploy"
    ver=$(docker exec "$C" python3 -c 'import json;print(json.load(open("/opt/atta/release.json"))["version"])')
    img=$(docker exec "$C" python3 - <<'PY'
import hashlib
from pathlib import Path
root = Path("/opt/atta"); h = hashlib.sha256(); n = 0
for p in sorted(root.rglob("*")):
    rel = p.relative_to(root)
    if {".git", "__pycache__", ".pytest_cache"} & set(rel.parts) or not p.is_file(): continue
    if rel.parts[0] in {"05-coolify", "tests", "Dockerfile", "docker-compose.coolify.yml"}: continue
    h.update(rel.as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest()); n += 1
print(h.hexdigest(), n)
PY
)
    say "running container $C: version $ver, code $img"
    [ "$ver" = v121.3 ] && [ "${img%% *}" = "$TREE_IMAGE" ] || die "the running container is NOT the tested v121.3 code"
    say "health: $(curl -fsS -m 5 http://127.0.0.1:8787/health || echo FAIL)"
    say "DEPLOYED: the running ATTa is the exact tested v121.3"
    exit 0
fi

if [ "$MODE" = --start-run ]; then
    C=$(atta_ctr); [ -n "$C" ] || die "ATTa is not running"
    [ "$(docker exec "$C" python3 -c 'import json;print(json.load(open("/opt/atta/release.json"))["version"])')" = v121.3 ] || die "running ATTa is not v121.3"
    q=$(find "$ROOT_DATA/inbox" -maxdepth 1 -name '*.library.json' -printf '%f ' 2>/dev/null)
    [ -z "$q" ] || die "a library run is already queued: $q (not queueing a second one)"
    [ ! -f "$ROOT_DATA/state/pipeline.lock" ] || die "the pipeline is busy: $(cat "$ROOT_DATA/state/pipeline.lock")"
    id="firstpass-$(date -u +%Y%m%dT%H%M%SZ)"
    umask 077; echo '{}' >"$ROOT_DATA/inbox/$id.library.json"
    say "queued ONE full library run: inbox/$id.library.json"
    exit 0
fi
die "unknown mode $MODE"
