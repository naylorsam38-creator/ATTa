#!/usr/bin/env bats
# ensure_registry_mirror: the shared Docker Hub 429 fix (scripts/lib/common.sh).
# Docker is a stub: "restarting" it loads daemon.json's mirrors into the running config,
# unless $BATS_TEST_TMPDIR/broken exists (then Docker refuses to start with the new file).

load test_helper

setup() {
    load_common
    export DOCKER_DAEMON_JSON="$BATS_TEST_TMPDIR/etc/docker/daemon.json"
    export ATTA_REGISTRY_MIRROR="https://mirror.gcr.io"
    export DOCKER_READY_SECONDS=2
    mkdir -p "$BATS_TEST_TMPDIR/etc/docker"
    ACTIVE="$BATS_TEST_TMPDIR/active"   # mirrors the "running daemon" uses
    RESTARTS="$BATS_TEST_TMPDIR/restarts"
    echo '[]' >"$ACTIVE"
    : >"$RESTARTS"
    export ACTIVE RESTARTS
    stub docker '
if [ "$1" = info ]; then
  [ -f "$BATS_TEST_TMPDIR/down" ] && exit 1
  if [ "${2:-}" = --format ]; then cat "$ACTIVE"; fi
  exit 0
fi
exit 0'
    export DOCKER_RESTART_CMD='echo x >>"$RESTARTS";
      if [ -f "$BATS_TEST_TMPDIR/broken" ] && jq -e ".[\"registry-mirrors\"]" "$DOCKER_DAEMON_JSON" >/dev/null 2>&1; then
        touch "$BATS_TEST_TMPDIR/down"; false;
      else rm -f "$BATS_TEST_TMPDIR/down";
        if [ -f "$DOCKER_DAEMON_JSON" ]; then jq -c ".[\"registry-mirrors\"] // []" "$DOCKER_DAEMON_JSON" >"$ACTIVE"; else echo "[]" >"$ACTIVE"; fi
      fi'
}

coolify_daemon_json() {
    cat >"$DOCKER_DAEMON_JSON" <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"},
  "default-address-pools": [{"base": "10.0.0.0/8", "size": 24}]
}
JSON
}

@test "mirror is merged into Coolify's daemon.json, its settings kept, Docker restarted once" {
    coolify_daemon_json
    run ensure_registry_mirror
    [ "$status" -eq 0 ]
    [[ "$output" == *"now go through https://mirror.gcr.io"* ]]
    jq -e '.["registry-mirrors"] == ["https://mirror.gcr.io"]' "$DOCKER_DAEMON_JSON"
    jq -e '.["log-opts"]["max-size"] == "10m" and .["default-address-pools"][0].base == "10.0.0.0/8"' "$DOCKER_DAEMON_JSON"
    [ "$(wc -l <"$RESTARTS")" -eq 1 ]
    ls "$DOCKER_DAEMON_JSON".atta-backup-* >/dev/null
}

@test "already set and active: nothing written, Docker not restarted" {
    coolify_daemon_json
    ensure_registry_mirror
    before="$(sha256sum "$DOCKER_DAEMON_JSON")"
    : >"$RESTARTS"
    run ensure_registry_mirror
    [ "$status" -eq 0 ]
    [[ "$output" == *"already go through"* ]]
    [ "$(sha256sum "$DOCKER_DAEMON_JSON")" = "$before" ]
    [ ! -s "$RESTARTS" ]
}

@test "in the file but not loaded yet: Docker restarted, file untouched" {
    echo '{"registry-mirrors":["https://mirror.gcr.io"]}' >"$DOCKER_DAEMON_JSON"
    before="$(sha256sum "$DOCKER_DAEMON_JSON")"
    run ensure_registry_mirror
    [ "$status" -eq 0 ]
    [ "$(sha256sum "$DOCKER_DAEMON_JSON")" = "$before" ]
    [ "$(wc -l <"$RESTARTS")" -eq 1 ]
}

@test "no daemon.json: one is created with just the mirror" {
    run ensure_registry_mirror
    [ "$status" -eq 0 ]
    [ "$(jq -c . "$DOCKER_DAEMON_JSON")" = '{"registry-mirrors":["https://mirror.gcr.io"]}' ]
}

@test "an existing mirror is kept and ours is added after it" {
    echo '{"registry-mirrors":["https://my.mirror.example"]}' >"$DOCKER_DAEMON_JSON"
    run ensure_registry_mirror
    [ "$status" -eq 0 ]
    jq -e '.["registry-mirrors"] == ["https://my.mirror.example","https://mirror.gcr.io"]' "$DOCKER_DAEMON_JSON"
}

@test "a daemon.json that is not a JSON object is never changed" {
    printf 'not json {' >"$DOCKER_DAEMON_JSON"
    run ensure_registry_mirror
    [ "$status" -eq 1 ]
    [[ "$output" == *"not changing it"* ]]
    [ "$(cat "$DOCKER_DAEMON_JSON")" = 'not json {' ]
    [ ! -s "$RESTARTS" ]
}

@test "Docker will not start with the new file: previous file restored and Docker started again" {
    coolify_daemon_json
    original="$(jq -S . "$DOCKER_DAEMON_JSON")"
    touch "$BATS_TEST_TMPDIR/broken"
    run ensure_registry_mirror
    [ "$status" -eq 1 ]
    [[ "$output" == *"Putting the previous"* ]]
    [ "$(jq -S . "$DOCKER_DAEMON_JSON")" = "$original" ]
    [ "$(wc -l <"$RESTARTS")" -eq 2 ]
    [ ! -f "$BATS_TEST_TMPDIR/down" ]
}

@test "Docker will not start with a file this step created: the file is removed again" {
    touch "$BATS_TEST_TMPDIR/broken"
    run ensure_registry_mirror
    [ "$status" -eq 1 ]
    [ ! -f "$DOCKER_DAEMON_JSON" ]
    [ ! -f "$BATS_TEST_TMPDIR/down" ]
}

@test "ATTA_REGISTRY_MIRROR empty: Docker left exactly as it is" {
    coolify_daemon_json
    before="$(sha256sum "$DOCKER_DAEMON_JSON")"
    ATTA_REGISTRY_MIRROR="" run ensure_registry_mirror
    [ "$status" -eq 0 ]
    [ "$(sha256sum "$DOCKER_DAEMON_JSON")" = "$before" ]
    [ ! -s "$RESTARTS" ]
}

@test "deploy-atta.sh, install.sh and upgrade.sh all set the mirror after Coolify's installer" {
    grep -q '^ensure_registry_mirror' "$KIT_DIR/scripts/deploy-atta.sh"
    for s in install.sh upgrade.sh; do
        awk '/run_coolify_installer/{i=NR} /^ensure_registry_mirror/{m=NR} END{exit !(i && m && m>i)}' "$KIT_DIR/scripts/$s"
    done
    # deploy-atta.sh: before the image build (the build pulls base images from Docker Hub)
    awk '/^ensure_registry_mirror/{m=NR} /docker build -t/{b=NR} END{exit !(m && b && m<b)}' "$KIT_DIR/scripts/deploy-atta.sh"
}
