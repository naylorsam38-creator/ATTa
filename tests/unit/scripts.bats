#!/usr/bin/env bats
# Unit tests for preflight.sh and install.sh that run without Docker or network.

load test_helper

setup() {
    # Network-dependent helpers are stubbed: DNS always resolves, HIBP says "not pwned".
    stub dig 'echo "10 mx.example.com."'
    stub curl 'printf "0000000000000000000000000000000000A:1\r\n"'
    CFG="$BATS_TEST_TMPDIR/coolify.env"
    export CREDENTIALS_FILE="$BATS_TEST_TMPDIR/creds.txt"
}

write_cfg() {
    cat >"$CFG" <<EOF
COOLIFY_VERSION=${1:-4.3.23}
ROOT_USERNAME=${2:-sam}
ROOT_USER_EMAIL=${3:-sam@example.com}
ROOT_USER_PASSWORD=${4-Correct-Horse7Battery}
AUTOUPDATE=${5:-true}
EOF
}

# ------------------------------------------------------------------ preflight
preflight_os() {
    printf 'ID=%s\nVERSION_ID="%s"\n' "$1" "$2" >"$BATS_TEST_TMPDIR/os-release"
    OS_RELEASE_FILE="$BATS_TEST_TMPDIR/os-release" bash -c '
        . "$0/scripts/preflight.sh"
        check_os
        echo "BLOCKERS=$BLOCKERS WARNINGS=$WARNINGS"
    ' "$KIT_DIR"
}

@test "preflight: Ubuntu 24.04 is recommended" {
    run preflight_os ubuntu 24.04
    [[ $output == *"recommended"* ]]
    [[ $output == *"BLOCKERS=0 WARNINGS=0"* ]]
}

@test "preflight: older Ubuntu warns but does not block" {
    run preflight_os ubuntu 20.04
    [[ $output == *"BLOCKERS=0 WARNINGS=1"* ]]
}

@test "preflight: unsupported OS blocks" {
    run preflight_os gentoo 2.15
    [[ $output == *"not supported"* ]]
    [[ $output == *"BLOCKERS=1"* ]]
}

@test "preflight: sourcing does not run the checks" {
    run bash -c '. "$0/scripts/preflight.sh"; echo sourced-ok' "$KIT_DIR"
    [ "$status" -eq 0 ]
    [ "$output" = "sourced-ok" ]
}

@test "preflight: port held by a non-Docker process blocks" {
    stub ss 'echo "LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:((\"nginx\",pid=1,fd=6))"'
    run bash -c '. "$0/scripts/preflight.sh"; REQUIRED_PORTS=80 check_ports; echo "BLOCKERS=$BLOCKERS"' "$KIT_DIR"
    [[ $output == *"already in use"*nginx* ]]
    [[ $output == *"BLOCKERS=1"* ]]
}

@test "preflight: port held by docker-proxy (existing Coolify) is fine" {
    stub ss 'echo "LISTEN 0 4096 0.0.0.0:8000 0.0.0.0:* users:((\"docker-proxy\",pid=9,fd=7))"'
    run bash -c '. "$0/scripts/preflight.sh"; REQUIRED_PORTS=8000 check_ports; echo "BLOCKERS=$BLOCKERS"' "$KIT_DIR"
    [[ $output == *"BLOCKERS=0"* ]]
}

# ------------------------------------------------------------------ install.sh
@test "install.sh --help prints usage" {
    run "$KIT_DIR/scripts/install.sh" --help
    [ "$status" -eq 0 ]
    [[ $output == *"--dry-run"* ]]
}

@test "install.sh rejects unknown options" {
    run "$KIT_DIR/scripts/install.sh" --bogus
    [ "$status" -eq 1 ]
    [[ $output == *"Unknown option"* ]]
}

@test "install.sh fails clearly with no config file" {
    run "$KIT_DIR/scripts/install.sh" --config "$BATS_TEST_TMPDIR/missing.env" --dry-run
    [ "$status" -eq 1 ]
    [[ $output == *"Config file not found"* ]]
}

@test "install.sh --dry-run passes on a valid config and writes nothing" {
    write_cfg
    run "$KIT_DIR/scripts/install.sh" --config "$CFG" --dry-run --skip-preflight
    [ "$status" -eq 0 ]
    [[ $output == *"Configuration valid: Coolify 4.3.23"* ]]
    [[ $output == *"Dry run complete"* ]]
    [ ! -e "$CREDENTIALS_FILE" ]
}

@test "install.sh generates a password when none is set" {
    write_cfg 4.3.23 sam sam@example.com ""
    run "$KIT_DIR/scripts/install.sh" --config "$CFG" --dry-run --skip-preflight
    [ "$status" -eq 0 ]
    [[ $output == *"generated a strong one"* ]]
}

@test "install.sh strips a leading v from the version" {
    write_cfg v4.3.23
    run "$KIT_DIR/scripts/install.sh" --config "$CFG" --dry-run --skip-preflight
    [ "$status" -eq 0 ]
    [[ $output == *"Coolify 4.3.23,"* ]]
}

@test "install.sh reports every config problem at once" {
    write_cfg latest "a b" not-an-email 'weak' maybe
    run "$KIT_DIR/scripts/install.sh" --config "$CFG" --dry-run --skip-preflight
    [ "$status" -eq 1 ]
    [[ $output == *"COOLIFY_VERSION"* ]]
    [[ $output == *"AUTOUPDATE"* ]]
    [[ $output == *"ROOT_USERNAME"* ]]
    [[ $output == *"ROOT_USER_EMAIL"* ]]
    [[ $output == *"ROOT_USER_PASSWORD"* ]]
    [[ $output == *"5 problem(s)"* ]]
}

@test "install.sh refuses a password found in breach lists" {
    local p='Correct-Horse7Battery' hash
    hash="$(printf '%s' "$p" | sha1sum | awk '{print toupper($1)}')"
    stub curl "printf '${hash:5}:12\r\n'"
    write_cfg 4.3.23 sam sam@example.com "$p"
    run "$KIT_DIR/scripts/install.sh" --config "$CFG" --dry-run --skip-preflight
    [ "$status" -eq 1 ]
    [[ $output == *"breach list"* ]]
}

@test "install.sh continues with a warning when the breach API is unreachable" {
    stub curl 'exit 6'
    write_cfg
    run "$KIT_DIR/scripts/install.sh" --config "$CFG" --dry-run --skip-preflight
    [ "$status" -eq 0 ]
    [[ $output == *"Could not reach api.pwnedpasswords.com"* ]]
}

@test "install.sh requires the admin username and email" {
    printf 'COOLIFY_VERSION=4.3.23\n' >"$CFG"
    unset ROOT_USERNAME ROOT_USER_EMAIL
    run "$KIT_DIR/scripts/install.sh" --config "$CFG" --dry-run --skip-preflight
    [ "$status" -eq 1 ]
    [[ $output == *"ROOT_USERNAME is not set"* ]]
    [[ $output == *"ROOT_USER_EMAIL is not set"* ]]
}

# ------------------------------------------------------------------ other scripts
@test "upgrade.sh requires a version argument" {
    run "$KIT_DIR/scripts/upgrade.sh"
    [ "$status" -eq 1 ]
    [[ $output == *"Usage"* ]]
}

@test "upgrade.sh rejects a malformed version" {
    run "$KIT_DIR/scripts/upgrade.sh" "latest"
    [ "$status" -eq 1 ]
}

@test "restore.sh requires an archive argument" {
    [ "$(id -u)" -eq 0 ] || skip "needs root"
    run "$KIT_DIR/scripts/restore.sh"
    [ "$status" -eq 1 ]
    [[ $output == *"Usage"* ]]
}

@test "backup.sh rejects a bad KEEP value" {
    [ "$(id -u)" -eq 0 ] || skip "needs root"
    KEEP=0 run "$KIT_DIR/scripts/backup.sh"
    [ "$status" -eq 1 ]
    [[ $output == *"KEEP must be a positive number"* ]]
}

@test "every script is executable and passes bash -n" {
    local f
    for f in "$KIT_DIR"/scripts/*.sh "$KIT_DIR"/tests/e2e/*.sh; do
        [ -x "$f" ] || {
            echo "not executable: $f"
            false
        }
        bash -n "$f"
    done
}

@test "preflight: kernel with IPv6 disabled blocks" {
    run bash -c '. "$0/scripts/preflight.sh"; IPV6_PROC_FILE=/nonexistent check_kernel_ipv6; echo "BLOCKERS=$BLOCKERS"' "$KIT_DIR"
    [[ $output == *"ipv6.disable=1"* ]]
    [[ $output == *"BLOCKERS=1"* ]]
}

@test "preflight: IPv6 blocker can be explicitly accepted" {
    run bash -c 'PREFLIGHT_ALLOW=kernel-ipv6; . "$0/scripts/preflight.sh"; IPV6_PROC_FILE=/nonexistent check_kernel_ipv6; echo "BLOCKERS=$BLOCKERS WARNINGS=$WARNINGS"' "$KIT_DIR"
    [[ $output == *"accepted via PREFLIGHT_ALLOW"* ]]
    [[ $output == *"BLOCKERS=0 WARNINGS=1"* ]]
}

@test "preflight: kernel with IPv6 support passes" {
    touch "$BATS_TEST_TMPDIR/if_inet6"
    run bash -c '. "$0/scripts/preflight.sh"; IPV6_PROC_FILE="$1" check_kernel_ipv6; echo "BLOCKERS=$BLOCKERS"' "$KIT_DIR" "$BATS_TEST_TMPDIR/if_inet6"
    [[ $output == *"BLOCKERS=0"* ]]
}
