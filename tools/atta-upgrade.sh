#!/usr/bin/env bash
# ATTa v121.3.x onto the running server, through the bundle's own Coolify kit. Run as root ON the server.
#
#   atta-upgrade.sh --check       read-only: identity, bundle, rebuild the target version in a new release folder,
#                                        prove it is the exact tested tree, show server/ATTa/inbox state
#   atta-upgrade.sh --deploy      the above, then keep the current image as a rollback tag and run
#                                        the kit's deploy-atta.sh (builds atta:v121 from it, restarts the
#                                        Coolify service "atta"); verifies the RUNNING container's code
#   atta-upgrade.sh --verify      check the RUNNING container is the tested version (read-only)
#   atta-upgrade.sh --start-run [a,b]  queue ONE full library run, or a re-check of only apps a,b (inbox/<id>.library.json), only if the
#                                        pipeline is idle and no run is already queued
#
# The target (VERSION below) = the server's own read-only v121 bundle + tools/patches/v121-to-v121.3.1.patch (fetched from GitHub
# at a pinned commit, checksum-checked). No new zip transfer. Never deletes containers, volumes, Coolify or
# ATTa data; the old release folder and the old image (tagged) stay for rollback.
set -euo pipefail
export LC_ALL=C
MODE="${1:---check}"
TARGET_INSTANCE="i-0f13b8644b215931d"
BASE=/opt/atta
BUNDLE_SHA256=ccca34de3988653d4af13367dd86b5ae0297c16faffa5f784a2fbf858b31c601
PRISTINE="$BASE/bundles/ATTa-v121-collect-all-adaptive-ccca34de3988.zip"
PATCH_URL="https://raw.githubusercontent.com/naylorsam38-creator/ATTa/b7c15ba/tools/patches/v121-to-v121.3.1.patch"
PATCH_SHA256=1c8ddb491df6c61939f6d5116a04f9d8d1e63e4f3d3b430175b1a87bbbd9b93f
TREE_FULL=63251477bf478bc5f61822b4158986d91ed67a19bc2265762c8e210dabf3b80c   # 140 files, the tested tree
TREE_IMAGE=7c8369ebf021f8a36194f8b430529c797f7a1da7f3c67b5efb2b1f21f92d4112  # 99 files, what the image holds
VERSION=v121.3.1
REL="$BASE/releases/$VERSION-${TREE_FULL:0:12}"
DOMAINS="airexploit.com,www.airexploit.com"
ROOT_DATA=/srv/app-builder
say() { printf "[%s] %s\n" "$VERSION" "$*"; }
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
    install -m 444 "$tmp/p.patch" "$tmp/v121-to-v121.3.1.patch"; rm -f "$tmp/p.patch"
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

if [ "$MODE" = --verify ]; then
    MODE=--deploy-verify-only
fi
if [ "$MODE" = --deploy ] || [ "$MODE" = --deploy-verify-only ]; then
    if [ "$MODE" = --deploy ] && docker image inspect atta:v121 >/dev/null 2>&1; then
        tag="atta:v121-before-$VERSION"
        docker image inspect "$tag" >/dev/null 2>&1 || docker tag atta:v121 "$tag"
        say "rollback image kept: $tag ($(docker image inspect "$tag" --format '{{.Id}}'))"
    fi
    if [ "$MODE" = --deploy ]; then
        "$REL/ATTa/05-coolify/kit/scripts/deploy-atta.sh" --atta-dir "$REL/ATTa" --domain "$DOMAINS" || die "deploy-atta.sh failed"
    fi
    for _ in $(seq 1 60); do
        C=$(atta_ctr || true)
        [ -n "$C" ] && [ "$(docker inspect "$C" --format '{{.State.Running}}')" = true ] && curl -fsS -m 5 http://127.0.0.1:8787/health >/dev/null 2>&1 && break
        sleep 5
    done
    C=$(atta_ctr); [ -n "$C" ] || die "no ATTa container after deploy"
    ver=$(docker exec "$C" python3 -c 'import json;print(json.load(open("/opt/atta/release.json"))["version"])')
    img=$(docker exec -i "$C" python3 - <<'PY'
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
    [ "$ver" = "$VERSION" ] && [ "${img%% *}" = "$TREE_IMAGE" ] || die "the running container is NOT the tested $VERSION code"
    say "health: $(curl -fsS -m 5 http://127.0.0.1:8787/health || echo FAIL)"
    say "DEPLOYED: the running ATTa is the exact tested $VERSION"
    exit 0
fi

if [ "$MODE" = --start-run ]; then
    set -- "$MODE" "${2:-}"
    C=$(atta_ctr); [ -n "$C" ] || die "ATTa is not running"
    [ "$(docker exec "$C" python3 -c 'import json;print(json.load(open("/opt/atta/release.json"))["version"])')" = "$VERSION" ] || die "running ATTa is not $VERSION"
    q=$(find "$ROOT_DATA/inbox" -maxdepth 1 -name '*.library.json' -printf '%f ' 2>/dev/null)
    [ -z "$q" ] || die "a library run is already queued: $q (not queueing a second one)"
    [ ! -f "$ROOT_DATA/state/pipeline.lock" ] || die "the pipeline is busy: $(cat "$ROOT_DATA/state/pipeline.lock")"
    id="firstpass-$(date -u +%Y%m%dT%H%M%SZ)"
    ONLY="${2:-}"
    [ -n "$ONLY" ] && id="rerun-$(date -u +%Y%m%dT%H%M%SZ)"
    umask 077
    if [ -n "$ONLY" ]; then
        python3 -c 'import json,sys; print(json.dumps({"only": [a for a in sys.argv[1].split(",") if a]}))' "$ONLY" >"$ROOT_DATA/inbox/$id.library.json"
        say "queued a re-check of ONLY: $ONLY (inbox/$id.library.json); every other app keeps its result"
    else
        echo '{}' >"$ROOT_DATA/inbox/$id.library.json"
        say "queued ONE full library run: inbox/$id.library.json"
    fi
    exit 0
fi
die "unknown mode $MODE"
