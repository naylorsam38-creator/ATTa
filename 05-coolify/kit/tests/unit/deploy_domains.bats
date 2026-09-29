#!/usr/bin/env bats
# deploy-atta.sh end to end with Coolify, Docker and the network stubbed: the Docker Hub mirror is
# set before the build, and --domain (one name or a comma list) becomes one Traefik route + checks.

load test_helper

setup() {
    T="$BATS_TEST_TMPDIR"
    mkdir -p "$T/kit/scripts/lib" "$T/proxy" "$T/out" "$T/root" "$T/etc/docker" "$T/coolify/source"
    cp "$KIT_DIR/scripts/deploy-atta.sh" "$T/kit/scripts/"
    cp "$KIT_DIR/scripts/lib/common.sh" "$T/kit/scripts/lib/"
    cat >"$T/kit/scripts/connect-atta.sh" <<'SH'
#!/usr/bin/env bash
mkdir -p "$OUT_DIR"; echo 'COOLIFY_TOKEN="tok-123"' >"$OUT_DIR/atta.env"
SH
    chmod +x "$T/kit/scripts/connect-atta.sh"
    echo 'APP_VERSION=4.3.23' >"$T/coolify/source/.env"
    echo '{"log-driver":"json-file"}' >"$T/etc/docker/daemon.json"
    echo '[]' >"$T/active"
    : >"$T/calls"
    export T
    export COOLIFY_ENV_FILE="$T/coolify/source/.env" PROXY_DYNAMIC_DIR="$T/proxy" OUT_DIR="$T/out" ATTA_ROOT="$T/root"
    export ATTA_DIR="$(cd "$KIT_DIR/../.." && pwd)" DOCKER_DAEMON_JSON="$T/etc/docker/daemon.json" DOCKER_READY_SECONDS=2
    export DOCKER_RESTART_CMD='echo restart >>"$T/calls"; jq -c ".[\"registry-mirrors\"] // []" "$DOCKER_DAEMON_JSON" >"$T/active"'
    stub docker '
echo "docker $*" >>"$T/calls"
case "$1" in
  info) [ "${2:-}" = --format ] && cat "$T/active"; exit 0;;
  build|image) exit 0;;
esac
exit 0'
    stub iptables 'exit 0'
    stub ip6tables 'exit 0'
    stub systemctl 'exit 0'
    stub curl '
url=""; method=GET; w=""
while [ $# -gt 0 ]; do case "$1" in -X) method="$2"; shift 2;; -w) w="$2"; shift 2;; -H|-d|-m|-o) shift 2;; http*) url="$1"; shift;; *) shift;; esac; done
echo "curl $method $url" >>"$T/calls"
case "$url" in
  */api/health) echo OK; exit 0;;
  */api/v1/servers) body="[{\"name\":\"localhost\",\"uuid\":\"srv1\"}]";;
  */api/v1/projects) [ "$method" = POST ] && body="{\"uuid\":\"p1\"}" || body="[]";;
  */api/v1/services) [ "$method" = POST ] && body="{\"uuid\":\"svc1\"}" || body="[]";;
  */health) exit 0;;
  *) exit 7;;
esac
printf "%s\n%s" "$body" 200'
}

@test "one domain: route for that host, mirror set before the build" {
    run "$T/kit/scripts/deploy-atta.sh" --domain airexploit.com --no-build
    [ "$status" -eq 0 ]
    grep -q 'rule: "Host(`airexploit.com`)"' "$T/proxy/atta.yaml"
    [[ "$output" == *"ATTa is live: https://airexploit.com/"* ]]
    jq -e '.["registry-mirrors"] == ["https://mirror.gcr.io"] and .["log-driver"] == "json-file"' "$DOCKER_DAEMON_JSON"
    grep -q 'curl GET https://airexploit.com/health' "$T/calls"
}

@test "comma list: one route and certificate for every name, each one checked over https" {
    run "$T/kit/scripts/deploy-atta.sh" --domain 'https://airexploit.com, www.airexploit.com/' --no-build
    [ "$status" -eq 0 ]
    [ "$(grep -c 'rule: "Host(`airexploit.com`) || Host(`www.airexploit.com`)"' "$T/proxy/atta.yaml")" -eq 2 ]
    grep -q 'curl GET https://airexploit.com/health' "$T/calls"
    grep -q 'curl GET https://www.airexploit.com/health' "$T/calls"
    [[ "$output" == *"ATTa is live: https://www.airexploit.com/"* ]]
}

@test "a bad name anywhere in the list is refused before anything changes" {
    run "$T/kit/scripts/deploy-atta.sh" --domain 'airexploit.com,bad_name!' --no-build
    [ "$status" -ne 0 ]
    [[ "$output" == *"is not a plain host name"* ]]
    [ ! -e "$T/proxy/atta.yaml" ]
    [ ! -s "$T/calls" ]
}
