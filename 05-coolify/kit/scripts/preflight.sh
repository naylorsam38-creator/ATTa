#!/usr/bin/env bash
# Checks that this server can run Coolify before anything is installed.
# Makes no changes. Exit code: 0 = ready (warnings allowed), 1 = at least one blocker.
#
# Usage: sudo ./scripts/preflight.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

# Thresholds. Coolify's own minimums are 2 CPU / 2 GB RAM / 30 GB disk; building apps
# on the same box needs more headroom, hence the warnings above the hard floor.
MIN_RAM_MB="${MIN_RAM_MB:-1900}"
REC_RAM_MB="${REC_RAM_MB:-3800}"
MIN_CPUS="${MIN_CPUS:-2}"
MIN_DISK_TOTAL_GB="${MIN_DISK_TOTAL_GB:-30}"
MIN_DISK_FREE_GB="${MIN_DISK_FREE_GB:-20}"
REQUIRED_PORTS="${REQUIRED_PORTS:-80 443 8000 6001 6002}"
OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"
IPV6_PROC_FILE="${IPV6_PROC_FILE:-/proc/net/if_inet6}"
# Space-separated blocker ids to downgrade to warnings, for people who have handled
# the problem another way. Currently supported: kernel-ipv6
PREFLIGHT_ALLOW="${PREFLIGHT_ALLOW:-}"

BLOCKERS=0
WARNINGS=0
block() {
    fail "$*"
    BLOCKERS=$((BLOCKERS + 1))
}
caution() {
    warn "$*"
    WARNINGS=$((WARNINGS + 1))
}
# block_unless_allowed <id> <message>: a blocker the operator may explicitly accept.
block_unless_allowed() {
    local id="$1"
    shift
    case " $PREFLIGHT_ALLOW " in
    *" $id "*) caution "$* (accepted via PREFLIGHT_ALLOW=$id)" ;;
    *) block "$*" ;;
    esac
}

check_root() {
    if [ "$(id -u)" -eq 0 ]; then
        ok "Running as root"
    else
        block "Not running as root. The Coolify installer needs root (use sudo)."
    fi
}

check_os() {
    local id version
    id="$(grep -E '^ID=' "$OS_RELEASE_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"')"
    version="$(grep -E '^VERSION_ID=' "$OS_RELEASE_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"')"
    case "$id" in
    ubuntu)
        case "$version" in
        22.04 | 24.04) ok "OS: Ubuntu $version (recommended)" ;;
        *) caution "OS: Ubuntu $version. Supported, but 24.04 LTS or 22.04 LTS is recommended." ;;
        esac
        ;;
    debian)
        case "$version" in
        12 | 13) ok "OS: Debian $version" ;;
        *) caution "OS: Debian $version. Debian 12+ is recommended." ;;
        esac
        ;;
    arch | archarm | manjaro | manjaro-arm | endeavouros | cachyos | raspbian | centos | fedora | fedora-asahi-remix | rhel | ol | rocky | sles | opensuse-leap | opensuse-tumbleweed | almalinux | amzn | alpine | postmarketos | tencentos | pop | linuxmint | zorin)
        caution "OS: $id $version. Supported by Coolify but not the tested path; Ubuntu 24.04 LTS is recommended."
        ;;
    '')
        block "Cannot read $OS_RELEASE_FILE; unable to identify the OS."
        ;;
    *)
        block "OS '$id' is not supported by the Coolify installer. Use Ubuntu 24.04 LTS."
        ;;
    esac
}

check_arch() {
    local arch
    arch="$(uname -m)"
    case "$arch" in
    x86_64 | amd64 | aarch64 | arm64) ok "CPU architecture: $arch" ;;
    *) block "CPU architecture '$arch' has no Coolify images (need x86_64 or aarch64)." ;;
    esac
}

# Coolify's web server (nginx inside the coolify image) listens on [::]:8080. With IPv6
# disabled in the kernel (ipv6.disable=1) that fails and the dashboard never starts.
# IPv6 does not need to be configured or routed; the kernel just has to support it.
check_kernel_ipv6() {
    if [ -e "$IPV6_PROC_FILE" ]; then
        ok "Kernel IPv6 support is enabled"
    else
        block_unless_allowed kernel-ipv6 "IPv6 is disabled in the kernel (ipv6.disable=1). Coolify's dashboard cannot start without it. Fix: remove ipv6.disable=1 from GRUB_CMDLINE_LINUX in /etc/default/grub, run update-grub, reboot."
    fi
}

check_cpu() {
    local cpus
    cpus="$(nproc 2>/dev/null || echo 1)"
    if [ "$cpus" -ge "$MIN_CPUS" ]; then
        ok "CPUs: $cpus"
    else
        caution "CPUs: $cpus. Coolify recommends at least $MIN_CPUS; builds will be slow."
    fi
}

check_ram() {
    local mb
    mb="$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)"
    if [ "$mb" -lt "$MIN_RAM_MB" ]; then
        block "RAM: ${mb} MB. Coolify needs at least 2 GB."
    elif [ "$mb" -lt "$REC_RAM_MB" ]; then
        caution "RAM: ${mb} MB. Works, but 4 GB+ is recommended when apps are built on this server."
    else
        ok "RAM: ${mb} MB"
    fi
}

check_disk() {
    local total free
    total="$(df -BG / | awk 'NR==2 {gsub("G","",$2); print $2}')"
    free="$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')"
    if [ "$free" -lt "$MIN_DISK_FREE_GB" ]; then
        block "Disk: ${free} GB free on /. Need at least ${MIN_DISK_FREE_GB} GB free."
    elif [ "$total" -lt "$MIN_DISK_TOTAL_GB" ]; then
        caution "Disk: ${total} GB total on /. Coolify recommends ${MIN_DISK_TOTAL_GB} GB+; images and builds fill disks fast."
    else
        ok "Disk: ${total} GB total, ${free} GB free"
    fi
}

check_snap_docker() {
    if command -v snap >/dev/null 2>&1 && snap list docker >/dev/null 2>&1; then
        block "Docker is installed via snap, which Coolify does not support. Run: snap remove docker"
    fi
}

check_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        ok "Docker not installed yet (the installer will install it)"
        return
    fi
    local major
    major="$(docker version --format '{{.Server.Version}}' 2>/dev/null | cut -d. -f1)"
    if [ -z "$major" ]; then
        caution "Docker is installed but the daemon is not responding."
    elif [ "$major" -lt 24 ]; then
        block "Docker $major is too old; Coolify needs Docker 24+."
    else
        ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
    fi
}

# A port is fine if it is free, or if it is already held by Coolify's own containers
# (re-running the installer on an existing box).
check_ports() {
    if ! command -v ss >/dev/null 2>&1; then
        caution "'ss' not found; skipping port checks (install iproute2)."
        return
    fi
    local port holders
    for port in $REQUIRED_PORTS; do
        holders="$(ss -Hltnp "sport = :$port" 2>/dev/null)"
        if [ -z "$holders" ]; then
            ok "Port $port is free"
        elif printf '%s' "$holders" | grep -q 'docker-proxy'; then
            ok "Port $port is held by Docker (existing Coolify install?)"
        else
            block "Port $port is already in use: $(printf '%s' "$holders" | grep -o 'users:.*' | head -n1). Stop that service first."
        fi
    done
}

check_existing_install() {
    if [ -f "$COOLIFY_ENV_FILE" ]; then
        caution "Existing Coolify install found ($COOLIFY_ENV_FILE). The installer will keep its secrets and upgrade in place. The admin account settings in config/coolify.env only apply if no admin exists yet."
    else
        ok "No existing Coolify install"
    fi
}

check_url() {
    local label="$1" url="$2" code
    code="$(curl -sS -o /dev/null -m 10 -w '%{http_code}' "$url" 2>/dev/null)"
    code="${code:-000}"
    if [ "$code" = "000" ]; then
        block "Cannot reach $label ($url). Check DNS, outbound firewall and proxy settings."
    else
        ok "Can reach $label"
    fi
}

check_network() {
    if ! command -v curl >/dev/null 2>&1; then
        block "curl is not installed (apt-get install -y curl)."
        return
    fi
    check_url "Coolify CDN" "https://cdn.coollabs.io/coolify/versions.json"
    check_url "Docker Hub registry" "https://registry-1.docker.io/v2/"
    if ! command -v docker >/dev/null 2>&1; then
        check_url "Docker installer" "https://get.docker.com"
    fi
}

check_ssh() {
    if command -v sshd >/dev/null 2>&1; then
        ok "OpenSSH server present (Coolify manages this host over SSH to localhost)"
    else
        caution "OpenSSH server not installed; the Coolify installer will install it."
    fi
}

main() {
    echo "== Coolify preflight: $(hostname) =="
    check_root
    check_os
    check_arch
    check_kernel_ipv6
    check_cpu
    check_ram
    check_disk
    check_snap_docker
    check_docker
    check_ssh
    check_ports
    check_existing_install
    if [ "${SKIP_NETWORK_CHECKS:-false}" != "true" ]; then
        check_network
    fi
    echo
    if [ "$BLOCKERS" -gt 0 ]; then
        fail "Preflight: $BLOCKERS blocker(s), $WARNINGS warning(s). Fix the blockers above before installing."
        return 1
    fi
    ok "Preflight passed with $WARNINGS warning(s)."
    return 0
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
