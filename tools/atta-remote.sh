#!/usr/bin/env bash
# Run ON the server as the ssh user (ubuntu). Fetches the pinned, checksum-checked v121.3 upgrade script and
# runs one step as root. Read-only except --deploy-bg and --start-run.
#
#   --check        rebuild/verify v121.3 in a new release folder; show server + ATTa state (read-only)
#   --verify       prove the RUNNING ATTa container is the tested v121.3 code (read-only)
#   --deploy-bg    deploy v121.3 in the background (systemd unit atta-upgrade-v1213; survives ssh drops)
#   --log [N]      last N lines of the deploy log and whether it is still running
#   --start-run    queue exactly ONE full library run
#   --progress     live run: pipeline state, app containers, results written, recent runner lines
#   --live         each handed-off app's real Coolify state: DISPATCHED/DEPLOYING/RUNNING/VERIFIED/LIVE_FAILED
#   --dump <path>  print a file under /srv/app-builder/state (reports, results, failure records)
#   --ls <path>    list a folder under /srv/app-builder/state
set -euo pipefail
URL="https://raw.githubusercontent.com/naylorsam38-creator/ATTa/54d623d/tools/atta-upgrade.sh"
SHA=25c99d6fbcd286377bf5e287e8fd7af2c931d0a50b9764f8cd47c835c09934de
S=/tmp/atta-upgrade.sh
LOG=/var/log/atta-upgrade-v121.3.log
DATA=/srv/app-builder
mode="${1:---check}"

fetch() {
    curl -fsS -m 60 -o "$S" "$URL"
    echo "$SHA  $S" | sha256sum -c --quiet || { echo "upgrade script checksum mismatch"; exit 9; }
}
inside() {   # refuse anything outside $DATA/state
    local p
    p=$(sudo -n realpath -m "$DATA/state/$1")
    case "$p" in "$DATA/state"|"$DATA/state/"*) echo "$p" ;; *) echo "refused: $1" >&2; exit 2 ;; esac
}
atta_ctr() { sudo -n docker ps --filter label=coolify.type=service --format '{{.Names}}' | grep -E '^atta-[a-z0-9]{24}$' | head -1; }

case "$mode" in
--check | --verify)
    fetch; sudo -n bash "$S" "$mode" ;;
--start-run)
    fetch
    sudo -n bash "$S" --start-run "${2:-}"
    sudo -n touch "$DATA/state/runner/.firstpass-start" ;;
--deploy-bg)
    fetch
    if sudo -n systemctl is-active --quiet atta-upgrade-v1213; then echo "deploy already running"; exit 0; fi
    sudo -n systemctl reset-failed atta-upgrade-v1213 >/dev/null 2>&1 || true
    sudo -n systemd-run --unit=atta-upgrade-v1213 --collect --quiet bash -c "bash $S --deploy >>$LOG 2>&1; echo \"exit=\$?\" >>$LOG"
    echo "deploy started (unit atta-upgrade-v1213, log $LOG)" ;;
--log)
    sudo -n tail -n "${2:-60}" "$LOG" 2>/dev/null || echo "(no log yet)"
    echo "unit: $(sudo -n systemctl is-active atta-upgrade-v1213 2>/dev/null || true)" ;;
--progress)
    c=$(atta_ctr || true)
    echo "time: $(date -u +%FT%TZ)  load: $(cut -d' ' -f1-3 /proc/loadavg)  mem free: $(free -m | awk '/^Mem:/{print $7}') MB  disk free: $(df -h / | awk 'NR==2{print $4}')"
    echo "ATTa container: ${c:-NONE} $( [ -n "$c" ] && sudo -n docker inspect "$c" --format '{{.State.Status}} since {{.State.StartedAt}}' )"
    sudo -n python3 -c 'import json;d=json.load(open("'"$DATA"'/state/status.json"));print("pipeline:",{k:d.get(k) for k in ("state","apps","parallel","updated_at","error") if k in d})' 2>/dev/null || true
    echo "inbox: $(sudo -n find "$DATA/inbox" -maxdepth 1 -type f -printf '%f ' 2>/dev/null)"
    echo "pipeline lock: $(sudo -n cat "$DATA/state/pipeline.lock" 2>/dev/null || echo none)"
    echo "app containers running: $(sudo -n docker ps --format '{{.Names}}' | grep -cE '^atta-[a-z0-9-]+' || true) (all atta-*: includes ATTa itself)"
    sudo -n docker ps --format '{{.Names}}\t{{.Status}}' | grep -E '^atta-' | grep -vE '^atta-[a-z0-9]{24}\b' | head -60 || true
    echo "results newer than the run marker: $(sudo -n find "$DATA/state/runner/results" -maxdepth 1 -name '*.json' -newer "$DATA/state/runner/.firstpass-start" 2>/dev/null | wc -l)"
    echo "failure records: $(sudo -n find "$DATA/state/runner/failures" -name '*.json' 2>/dev/null | wc -l)"
    echo "RUN-REPORT: $(sudo -n stat -c '%y %s bytes' "$DATA/state/runner/RUN-REPORT.json" 2>/dev/null || echo not yet)"
    [ -n "$c" ] && sudo -n docker logs --since 15m "$c" 2>&1 | grep -E '^\[|fleet|qualif|FAILED|repair|WORKER|TIMEOUT|RESOLVED|QUALIFIED|WARNING' | tail -n "${2:-40}" || true ;;
--dump)
    p=$(inside "${2:?path}"); sudo -n cat "$p" ;;
--ls)
    p=$(inside "${2:-.}"); sudo -n ls -la "$p" ;;
--live)
    # Every handed-off app's REAL Coolify state from the build records (coolify_live.py).
    sudo -n python3 - "$DATA/state/builds" <<'PY'
import json, sys
from pathlib import Path
for f in sorted(Path(sys.argv[1]).glob("*.json"), key=lambda p: p.stat().st_mtime)[-3:]:
    r = json.loads(f.read_text()); c = r.get("coolify") or {}
    if not c: continue
    print(f"build {r.get('id')} state {r.get('state')} coolify {c.get('status')} live {c.get('live_summary')}")
    for a, st in sorted((c.get("apps") or {}).items()):
        lv = st.get("live") or {}
        ch = lv.get("check") or {}
        print(f"  {a:<20} {st.get('status'):<10} {lv.get('state') or '-':<12} uuid={st.get('uuid')} url={lv.get('url')} "
              f"http={(lv.get('http') or {}).get('http_status')} svc={(lv.get('coolify_service') or {}).get('status')} "
              f"live={ch.get('broken_at') or ''} {ch.get('code') or ''} {lv.get('failed_at') or ''} {str(lv.get('reason') or '')[:160]}")
PY
    ;;
--logs-atta)
    c=$(atta_ctr); sudo -n docker logs --tail "${2:-200}" "$c" 2>&1 ;;
*) echo "unknown mode $mode"; exit 64 ;;
esac
