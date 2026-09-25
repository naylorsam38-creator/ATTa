#!/usr/bin/env bash
# run-proxy.sh — v116: start or stop ONE app's skin proxy as the unprivileged proxy user.
#
#   run-proxy start <proxy_dir> <app_id> <target_url> <port>
#   run-proxy stop  <proxy_dir>
#
# The pipeline calls this through `setpriv` (when it runs as root) or `sudo -u atta-proxy` (when it runs as its
# own user; the sudoers rule names this file and nothing else). <proxy_dir> is a verified COPY of the app's
# .ui-capability overlay made by proxy_launch.py, never the library itself (which holds customers' .env files).
# The proxy gets an empty environment plus four values: no ATTa secret, no Docker access, no library access.
set -euo pipefail
umask 027

die(){ echo "run-proxy: $*" >&2; exit 2; }
mode="${1:-}"; dir="${2:-}"
[ -n "$dir" ] && [ -d "$dir/.ui-capability" ] || die "not a proxy folder: ${dir:-(none)}"
dir="$(cd "$dir" && pwd -P)"
[[ "$(basename "$dir")" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] || die "bad proxy folder name"
pidfile="$dir/run/proxy.pid"

case "$mode" in
  start)
    app="${3:-}"; target="${4:-}"; port="${5:-}"
    [[ "$app" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] || die "bad app id"
    [[ "$target" =~ ^http://127\.0\.0\.1:[0-9]{1,5}/?$ ]] || die "target must be http://127.0.0.1:<port>/"
    [[ "$port" =~ ^[0-9]{1,5}$ ]] && [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || die "bad proxy port"
    mkdir -p "$dir/run" "$dir/state/apps"
    echo "$$" > "$pidfile"
    # exec keeps this pid: bash run-ui.sh, which in turn execs node. So the pid file names the proxy itself.
    exec env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$dir" LANG=C.UTF-8 \
      TARGET_URL="$target" UI_PORT="$port" APP_BUILDER_ROOT="$dir" \
      bash "$dir/.ui-capability/run-ui.sh"
    ;;
  stop)
    [ -f "$pidfile" ] || exit 0
    pid="$(tr -dc 0-9 < "$pidfile")"; rm -f "$pidfile"
    [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] || exit 0
    # pid reuse guard: only a process started from THIS proxy folder is stopped
    tr '\0' ' ' < "/proc/$pid/cmdline" | grep -qF "$dir/" || exit 0
    kill -TERM "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || exit 0; sleep 0.3; done
    kill -KILL "$pid" 2>/dev/null || true
    ;;
  *) die "usage: run-proxy start <dir> <app> <target_url> <port> | stop <dir>" ;;
esac
