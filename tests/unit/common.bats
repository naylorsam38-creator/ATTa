#!/usr/bin/env bats
# Unit tests for scripts/lib/common.sh

load test_helper

setup() {
    load_common
}

# ------------------------------------------------------------------ versions
@test "validate_version accepts release and rc versions" {
    run validate_version 4.3.23
    [ "$status" -eq 0 ]
    run validate_version 4.4-rc.1
    [ "$status" -eq 0 ]
    run validate_version 5.0
    [ "$status" -eq 0 ]
}

@test "validate_version rejects junk and injection attempts" {
    for v in "" latest "4.3.23; rm -rf /" "4.3.x" "v" "4..3"; do
        run validate_version "$v"
        [ "$status" -eq 1 ] || {
            echo "accepted: $v"
            false
        }
    done
}

# ------------------------------------------------------------------ usernames
@test "validate_username accepts safe names" {
    for u in sam sam_naylor Sam-N1 abc; do
        run validate_username "$u"
        [ "$status" -eq 0 ] || {
            echo "rejected: $u"
            false
        }
    done
}

@test "validate_username rejects spaces (they break Coolify's .env parser) and symbols" {
    for u in "ab" "sam naylor" 'sam$' "sam|x" "" "$(printf 'a%.0s' {1..65})"; do
        run validate_username "$u"
        [ "$status" -eq 1 ] || {
            echo "accepted: $u"
            false
        }
    done
}

# ------------------------------------------------------------------ emails
@test "validate_email_syntax accepts normal addresses" {
    for e in a@b.co first.last+tag@mail.example.com naylorsam38@gmail.com; do
        run validate_email_syntax "$e"
        [ "$status" -eq 0 ] || {
            echo "rejected: $e"
            false
        }
    done
}

@test "validate_email_syntax rejects malformed or unsafe addresses" {
    for e in "" no-at "a@b" "a b@c.com" "a|b@c.com" "a&b@c.com" 'a$b@c.com' "a@-.com." "@c.com"; do
        run validate_email_syntax "$e"
        [ "$status" -eq 1 ] || {
            echo "accepted: $e"
            false
        }
    done
}

@test "validate_email fails when the domain has no DNS records" {
    stub dig 'exit 0' # prints nothing = no records
    run validate_email someone@no-such-domain.io
    [ "$status" -eq 1 ]
    [[ $output == *"no usable mail DNS"* ]]
}

@test "validate_email passes when only an MX record exists" {
    stub dig 'for a in "$@"; do [ "$a" = MX ] && echo "10 mx.example.com."; done; exit 0'
    run validate_email someone@company.io
    [ "$status" -eq 0 ]
}

@test "validate_email passes on A record only (implicit MX)" {
    stub dig 'for a in "$@"; do [ "$a" = A ] && echo "203.0.113.7"; done; exit 0'
    run validate_email someone@company.io
    [ "$status" -eq 0 ]
}

@test "validate_email rejects a null-MX domain (RFC 7505), like example.com" {
    # This is what made Coolify's seeder reject e2e-admin@example.com during testing.
    stub dig 'for a in "$@"; do [ "$a" = MX ] && echo "0 ."; [ "$a" = A ] && echo "93.184.215.14"; done; exit 0'
    run validate_email someone@example.com
    [ "$status" -eq 1 ]
    [[ $output == *"null MX"* ]]
}

@test "validate_email rejects reserved and private top-level names without any DNS lookup" {
    stub dig 'echo "10 mx.anything."; touch "$BATS_TEST_TMPDIR/dig-called"'
    local e
    for e in a@site.test a@site.example a@site.invalid a@box.localhost a@nas.local a@wiki.intranet \
        a@svc.internal a@x.private a@ad.corp a@router.home a@pi.lan; do
        run validate_email "$e"
        [ "$status" -eq 1 ] || {
            echo "accepted: $e"
            false
        }
        [[ $output == *"reserved top-level name"* ]]
    done
    [ ! -e "$BATS_TEST_TMPDIR/dig-called" ]
}

@test "validate_email falls back to a parent domain like Coolify does" {
    # Only company.io has records; mail.company.io has none.
    stub dig 'host="${@: -1}"; [ "$host" = company.io ] && [ "${@: -2:1}" = MX ] && echo "10 mx.company.io."; exit 0'
    run validate_email someone@mail.company.io
    [ "$status" -eq 0 ]
}

@test "validate_email against live DNS: gmail.com passes, example.com fails" {
    command -v dig >/dev/null && [ -n "$(dig +short +time=2 +tries=1 MX gmail.com 2>/dev/null)" ] || skip "no DNS"
    run validate_email someone@gmail.com
    [ "$status" -eq 0 ]
    run validate_email someone@example.com
    [ "$status" -eq 1 ]
}

# ------------------------------------------------------------------ passwords
@test "validate_password accepts a compliant password" {
    run validate_password 'Correct-Horse7Battery'
    [ "$status" -eq 0 ]
}

@test "validate_password enforces length and character classes" {
    run validate_password 'Sh0rt!'
    [ "$status" -eq 1 ]
    [[ $output == *"at least 12"* ]]
    run validate_password 'alllowercase7!!'
    [[ $output == *"uppercase"* ]]
    run validate_password 'ALLUPPERCASE7!!'
    [[ $output == *"lowercase"* ]]
    run validate_password 'NoDigitsHere!!'
    [[ $output == *"digit"* ]]
    run validate_password 'NoSymbolsHere77'
    [[ $output == *"symbol"* ]]
}

@test "validate_password rejects every character that breaks Coolify's .env handling" {
    local c
    for c in '$' '#' '&' '|' '\' ' ' '"' "'" '`' '(' ')'; do
        run validate_password "GoodPassw0rd!${c}x"
        [ "$status" -eq 1 ] || {
            echo "accepted char: [$c]"
            false
        }
        [[ $output == *"may only use"* ]]
    done
}

@test "validate_password accepts each allowed symbol" {
    local i c
    for ((i = 0; i < ${#ATTA_PASSWORD_SYMBOLS}; i++)); do
        c="${ATTA_PASSWORD_SYMBOLS:i:1}"
        run validate_password "GoodPassw0rd${c}"
        [ "$status" -eq 0 ] || {
            echo "rejected allowed symbol: [$c] $output"
            false
        }
    done
}

@test "generate_password always produces valid, unique passwords" {
    local seen=" " p i
    for i in $(seq 1 200); do
        p="$(generate_password)"
        validate_password "$p" 2>/dev/null || {
            echo "invalid generated password: $p"
            false
        }
        [[ $seen != *" $p "* ]] || {
            echo "duplicate: $p"
            false
        }
        seen="$seen$p "
    done
}

@test "generated password survives Coolify's sed-based .env writer unchanged" {
    # Replays install.sh's update_env_var on a scratch file.
    local env="$BATS_TEST_TMPDIR/.env" p i got
    for i in $(seq 1 50); do
        p="$(generate_password)"
        printf 'ROOT_USER_PASSWORD=\n' >"$env"
        sed -i "s|^ROOT_USER_PASSWORD=$|ROOT_USER_PASSWORD=${p}|" "$env"
        got="$(env_file_get ROOT_USER_PASSWORD "$env")"
        [ "$got" = "$p" ] || {
            echo "mangled: $p -> $got"
            false
        }
    done
}

# ------------------------------------------------------------------ breach check
@test "password_pwned_status: pwned when the hash suffix is listed" {
    # SHA-1("P@ssw0rd123!") = ... compute suffix dynamically.
    local hash suffix
    hash="$(printf '%s' 'P@ssw0rd123!' | sha1sum | awk '{print toupper($1)}')"
    suffix="${hash:5}"
    stub curl "printf '0000000000000000000000000000000000A:3\r\n${suffix}:42\r\n'"
    run password_pwned_status 'P@ssw0rd123!'
    [ "$status" -eq 1 ]
}

@test "password_pwned_status: padding entries with count 0 are not a match" {
    local hash suffix
    hash="$(printf '%s' 'Unique-Pass9' | sha1sum | awk '{print toupper($1)}')"
    suffix="${hash:5}"
    stub curl "printf '${suffix}:0\r\n'"
    run password_pwned_status 'Unique-Pass9'
    [ "$status" -eq 0 ]
}

@test "password_pwned_status: returns 2 when the API is unreachable" {
    stub curl 'exit 7'
    run password_pwned_status 'Unique-Pass9'
    [ "$status" -eq 2 ]
}

# ------------------------------------------------------------------ config loading
@test "load_config reads values, strips quotes and handles CRLF" {
    local f="$BATS_TEST_TMPDIR/c.env"
    printf '# comment\n\nCOOLIFY_VERSION="4.3.23"\r\nROOT_USERNAME=sam\nROOT_USER_EMAIL='"'"'a@b.co'"'"'\n' >"$f"
    unset COOLIFY_VERSION ROOT_USERNAME ROOT_USER_EMAIL
    load_config "$f"
    [ "$COOLIFY_VERSION" = "4.3.23" ]
    [ "$ROOT_USERNAME" = "sam" ]
    [ "$ROOT_USER_EMAIL" = "a@b.co" ]
}

@test "load_config never executes the file" {
    local f="$BATS_TEST_TMPDIR/c.env"
    printf 'ROOT_USERNAME=$(touch %s/pwned)\n' "$BATS_TEST_TMPDIR" >"$f"
    unset ROOT_USERNAME
    load_config "$f"
    [ ! -e "$BATS_TEST_TMPDIR/pwned" ]
    [ "$ROOT_USERNAME" = "\$(touch $BATS_TEST_TMPDIR/pwned)" ]
}

@test "load_config rejects unknown keys (typos must not be silently ignored)" {
    local f="$BATS_TEST_TMPDIR/c.env"
    printf 'ROOT_USER_PASWORD=x\n' >"$f"
    run load_config "$f"
    [ "$status" -eq 1 ]
    [[ $output == *"unknown setting 'ROOT_USER_PASWORD'"* ]]
}

@test "load_config rejects malformed lines" {
    local f="$BATS_TEST_TMPDIR/c.env"
    printf 'ROOT_USERNAME sam\n' >"$f"
    run load_config "$f"
    [ "$status" -eq 1 ]
    [[ $output == *"expected KEY=VALUE"* ]]
}

@test "load_config leaves empty values unset (installer treats set-but-empty as a value)" {
    local f="$BATS_TEST_TMPDIR/c.env"
    printf 'REGISTRY_URL=\nDOCKER_ADDRESS_POOL_BASE=\n' >"$f"
    unset REGISTRY_URL DOCKER_ADDRESS_POOL_BASE
    load_config "$f"
    [ -z "${REGISTRY_URL+x}" ]
    [ -z "${DOCKER_ADDRESS_POOL_BASE+x}" ]
}

@test "load_config lets the environment override the file" {
    local f="$BATS_TEST_TMPDIR/c.env"
    printf 'COOLIFY_VERSION=4.3.22\n' >"$f"
    COOLIFY_VERSION=4.3.23
    load_config "$f"
    [ "$COOLIFY_VERSION" = "4.3.23" ]
}

@test "load_config fails clearly when the file is missing" {
    run load_config "$BATS_TEST_TMPDIR/nope.env"
    [ "$status" -eq 1 ]
    [[ $output == *"copy config/coolify.env.example"* ]]
}

@test "the shipped example config loads cleanly" {
    unset COOLIFY_VERSION ROOT_USERNAME ROOT_USER_EMAIL ROOT_USER_PASSWORD AUTOUPDATE REGISTRY_URL COOLIFY_SOURCE_ZIP
    load_config "$KIT_DIR/config/coolify.env.example"
    [ "$COOLIFY_VERSION" = "4.3.23" ]
    [ "$AUTOUPDATE" = "false" ]
    [ -z "${ROOT_USER_PASSWORD+x}" ]
    [ -z "${COOLIFY_SOURCE_ZIP+x}" ]
}

# ------------------------------------------------------------------ env file
@test "env_file_get returns the last value and keeps '=' inside values" {
    local f="$BATS_TEST_TMPDIR/.env"
    printf 'APP_KEY=old\nAPP_KEY=base64:abc==\nOTHER=1\n' >"$f"
    [ "$(env_file_get APP_KEY "$f")" = "base64:abc==" ]
    [ "$(env_file_get MISSING "$f")" = "" ]
}

@test "env_file_get is safe under set -euo pipefail when the key is missing" {
    # Regression: DB_DATABASE is absent from Coolify's .env; this killed backup.sh silently.
    local f="$BATS_TEST_TMPDIR/.env"
    printf 'DB_USERNAME=coolify\n' >"$f"
    run bash -c 'set -euo pipefail; . "$1/scripts/lib/common.sh"; v="$(env_file_get DB_DATABASE "$2")"; echo "reached:[$v]"' _ "$KIT_DIR" "$f"
    [ "$status" -eq 0 ]
    [ "$output" = "reached:[]" ]
    run bash -c 'set -euo pipefail; . "$1/scripts/lib/common.sh"; env_file_get X /nonexistent/file; echo reached' _ "$KIT_DIR"
    [ "$output" = "reached" ]
}

@test "enable_error_trap reports the failing command instead of exiting silently" {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n. "%s/scripts/lib/common.sh"\nenable_error_trap\nf() { grep -q nothere /dev/null; }\nf\necho unreachable\n' "$KIT_DIR" >"$BATS_TEST_TMPDIR/t.sh"
    run bash "$BATS_TEST_TMPDIR/t.sh"
    [ "$status" -ne 0 ]
    [[ $output == *"Unexpected error (exit 1)"*"grep -q nothere"* ]]
    [[ $output != *unreachable* ]]
}

# ------------------------------------------------------------------ installer from source zip
make_zip() {
    # make_zip <zipfile> <top-folder> [installer-content]
    local d="$BATS_TEST_TMPDIR/zipsrc"
    rm -rf "$d"
    mkdir -p "$d/$2/scripts"
    [ -z "${3:-}" ] || printf '%s' "$3" >"$d/$2/scripts/install.sh"
    touch "$d/$2/README.md"
    (cd "$d" && zip -qr "$1" "$2")
}

@test "installer_from_zip extracts scripts/install.sh from a checksum-matching zip" {
    command -v zip >/dev/null || skip "zip not installed"
    make_zip "$BATS_TEST_TMPDIR/c.zip" coolify-4.3.23 '#!/bin/bash
echo real-installer'
    COOLIFY_SOURCE_ZIP_SHA256="$(sha256sum "$BATS_TEST_TMPDIR/c.zip" | awk '{print $1}')"
    run installer_from_zip "$BATS_TEST_TMPDIR/c.zip" "$BATS_TEST_TMPDIR/out.sh"
    [ "$status" -eq 0 ]
    [ "$(tail -n1 "$BATS_TEST_TMPDIR/out.sh")" = "echo real-installer" ]
}

@test "installer_from_zip refuses a zip whose checksum does not match" {
    command -v zip >/dev/null || skip "zip not installed"
    make_zip "$BATS_TEST_TMPDIR/c.zip" coolify-main '#!/bin/bash'
    unset COOLIFY_SOURCE_ZIP_SHA256 # default: the pinned ATTa v116 checksum
    run installer_from_zip "$BATS_TEST_TMPDIR/c.zip" "$BATS_TEST_TMPDIR/out.sh"
    [ "$status" -eq 1 ]
    [[ $output == *"REFUSED"*"$ATTA_COOLIFY_ZIP_SHA256"* ]]
    [ ! -s "$BATS_TEST_TMPDIR/out.sh" ]
}

@test "installer_from_zip refuses a zip without scripts/install.sh" {
    command -v zip >/dev/null || skip "zip not installed"
    make_zip "$BATS_TEST_TMPDIR/c.zip" coolify-main
    COOLIFY_SOURCE_ZIP_SHA256="$(sha256sum "$BATS_TEST_TMPDIR/c.zip" | awk '{print $1}')"
    run installer_from_zip "$BATS_TEST_TMPDIR/c.zip" "$BATS_TEST_TMPDIR/out.sh"
    [ "$status" -eq 1 ]
    [[ $output == *"does not contain"* ]]
}

@test "installer_from_zip fails clearly when the zip is missing" {
    run installer_from_zip "$BATS_TEST_TMPDIR/nope.zip" "$BATS_TEST_TMPDIR/out.sh"
    [ "$status" -eq 1 ]
    [[ $output == *"not found"* ]]
}

# ------------------------------------------------------------------ localhost activation
@test "activate_localhost_server waits for Coolify's seeding instead of passing a missing server" {
    # Regression (CI): on a fast machine Server 0 did not exist yet and Coolify threw
    # "ValidateAndInstallServerJob::__construct(): Argument #1 must be of type Server, null given".
    local n="$BATS_TEST_TMPDIR/polls"
    echo 0 >"$n"
    coolify_db_query() { # seeded on the 3rd poll
        local c
        c=$(($(cat "$n") + 1))
        echo "$c" >"$n"
        [ "$c" -ge 3 ] && echo 1 || echo 0
    }
    sleep() { :; }
    stub docker 'echo usable'
    wait_for_stable_proxy() { echo "proxy-wait-called"; }
    run activate_localhost_server
    [ "$status" -eq 0 ]
    [[ $output == *"validated by Coolify"* ]]
    [[ $output == *"proxy-wait-called"* ]]
}

@test "activate_localhost_server fails clearly if Coolify never registers localhost" {
    coolify_db_query() { echo 0; }
    sleep() { :; }
    stub docker 'echo "should not be called"; exit 1'
    SERVER_SEED_WAIT=3 run activate_localhost_server
    [ "$status" -eq 1 ]
    [[ $output == *"never registered its localhost server"* ]]
    [[ $output != *"should not be called"* ]]
}

# ------------------------------------------------------------------ admin lockdown
# Fake Coolify database: files in $DB hold seeded / admin / registration state, and the
# stubbed `docker` changes them the way Coolify's seeder and settings update would.
fake_coolify_db() {
    DB="$BATS_TEST_TMPDIR/db"
    mkdir -p "$DB"
    echo "${1:-2}" >"$DB/seeded"  # 2 = settings row + localhost server exist
    echo "${2:-0}" >"$DB/admin"   # users with id 0
    echo "${3:-t}" >"$DB/reg"     # is_registration_enabled
    export DB
    coolify_db_query() {
        case "$1" in
        *"from users where id = 0"*) cat "$DB/admin" ;;
        *"is_registration_enabled from instance_settings"*) cat "$DB/reg" ;;
        *"from servers where id = 0"*) cat "$DB/seeded" ;;
        esac
    }
    sleep() { :; }
    stub docker '
case "$*" in
  *RootUserSeeder*) echo 1 >"$DB/admin"; echo f >"$DB/reg"; echo "SUCCESS Root user created" ;;
  *is_registration_enabled*) echo f >"$DB/reg" ;;
esac'
}

@test "ensure_admin_account: admin exists but sign-up left ON gets locked down" {
    # Regression (CI): the seeder re-run beat Coolify's migrations; admin was created but the
    # settings row came later with the default (sign-up on).
    fake_coolify_db 2 1 t
    ADMIN_BOOT_WAIT_TRIES=1 run ensure_admin_account
    [ "$status" -eq 0 ]
    [[ $output == *"switching it off"* ]]
    [ "$(cat "$DB/reg")" = "f" ]
}

@test "ensure_admin_account: waits for first-boot seeding before touching anything" {
    fake_coolify_db 1 0 t # settings row exists, localhost server not yet
    SEED_WAIT_TRIES=3 run ensure_admin_account
    [ "$status" -eq 1 ]
    [[ $output == *"never finished its first-boot setup"* ]]
    [ "$(cat "$DB/admin")" = "0" ] # seeder was never run early
}

@test "ensure_admin_account: missing admin is created by re-running Coolify's seeder" {
    fake_coolify_db 2 0 t
    ADMIN_BOOT_WAIT_TRIES=1 run ensure_admin_account
    [ "$status" -eq 0 ]
    [[ $output == *"re-running Coolify's admin seeder"* ]]
    [ "$(cat "$DB/admin")" = "1" ]
    [ "$(cat "$DB/reg")" = "f" ]
}

@test "ensure_admin_account: already locked down is a quiet no-op" {
    fake_coolify_db 2 1 f
    stub docker 'echo "docker should not be called" >&2; exit 1'
    ADMIN_BOOT_WAIT_TRIES=1 run ensure_admin_account
    [ "$status" -eq 0 ]
    [[ $output != *"should not be called"* ]]
    [[ $output == *"Public sign-up is off"* ]]
}

@test "ensure_admin_account: fails loudly if sign-up cannot be switched off" {
    fake_coolify_db 2 1 t
    stub docker 'exit 0' # the settings update silently does nothing
    ADMIN_BOOT_WAIT_TRIES=1 run ensure_admin_account
    [ "$status" -eq 1 ]
    [[ $output == *"Could not switch off public sign-up"* ]]
}
