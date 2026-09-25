#!/usr/bin/env bash
# server_lifecycle.sh — the deploy lifecycle, for real, on a server where ATTa is installed (v117).
#
#     sudo bash run                                              # the normal install, from the unpacked release
#     sudo bash tests/staging/server_lifecycle.sh ATTa-<ver>.zip # this check (about 15 minutes)
#
# Nothing here is simulated: the release zip is the one tools/make_release.py built, unmodified; the installer,
# systemd, nginx, Chromium and atta-deployd are the real ones. Failures are produced the real way — a time limit
# (ATTA_ADM_DEPLOY_TIMEOUT, a real setting), a SIGKILL of deployctl, a second deploy while one runs.
# CI runs it on a fresh GitHub Ubuntu 24.04 runner; run it on a STAGING server, never production (it deploys and
# rolls back the release you give it).
#
# Checks, in order:
#   1 the fresh install is live and proves it is ATTa: direct, through nginx, and in a browser
#   2 deployctl deploy of the release (with its full test gate) -> DEPLOYED, checked again
#   3 a second deploy while one runs -> BUSY, and nothing new was started
#   4 deployctl killed with SIGKILL mid-deploy -> atta-deployd recovers it as INTERRUPTED, never re-run, lock freed
#   5 a deploy past its time limit -> TIMED_OUT, its whole process tree stopped, the live release untouched
#   6 deployctl rollback -> the previous known-good release, byte-identical to what was recorded, checked again
# Prints PASS/FAIL per check; exits non-zero if anything failed.
set -uo pipefail
ZIP="${1:?give the release zip (tools/make_release.py builds it)}"
ZIP="$(readlink -e "$ZIP")" || { echo "no such file: $1"; exit 2; }
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 2; }
ROOT=/srv/app-builder; APP=/opt/app-builder; RELS=/opt/app-builder-releases
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "PASS $*"; }
bad(){ FAIL=$((FAIL+1)); echo "FAIL $*"; }
result(){ sed -n 's/^RESULT: \([A-Z_]*\).*/\1/p' "$1" | tail -1; }
live(){ readlink -e "$APP"; }
tree_hash(){ python3 - "$1" <<'EOF'
import sys; sys.path.insert(0, "/opt/app-builder/deployd")
from pathlib import Path
from adm import releases
print(releases.tree_sha256(Path(sys.argv[1])))
EOF
}
job_state(){ python3 - "$1" <<'EOF'
import json, subprocess, sys
t = subprocess.run(["deployctl", "journal", sys.argv[1]], capture_output=True, text=True).stdout
d = json.JSONDecoder().raw_decode(t[t.index("{"):])[0]
print(d.get("state", ""), d.get("verdict", ""), " ".join(h["state"] for h in d.get("state_history", [])))
EOF
}
newest_job(){ ls -t "$ROOT/adm/logs" | sed -n 's/\.log$//p' | grep -v -- '-rollback$\|-backup$' | head -1; }
health(){ python3 "$APP/atta_health.py" --env "$ROOT/.env" --proxy-file "$ROOT/state/proxy.json" \
            --direct --proxy --browser --timeout 60 2>&1 | tail -1; }
deploy_procs(){ python3 - "$1" <<'EOF'
import sys; sys.path.insert(0, "/opt/app-builder/deployd")
from adm import proc
print(len(proc.find_by_env("ATTA_DEPLOYMENT_ID", sys.argv[1])))
EOF
}
lock_held(){ python3 -c 'import sys; sys.path.insert(0, "/opt/app-builder/deployd")
from adm import lock, config; print(lock.holder(config.LOCK)[0])'; }
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT

echo "== 1. the installed ATTa is live and is ATTa"
for s in app-builder-gateway app-builder-pipeline atta-deployd nginx; do
  systemctl is-active --quiet "$s" && ok "$s active" || bad "$s not active"
done
h="$(health)"; [ "$h" = "HEALTH PASS" ] && ok "identity: direct, through nginx, in a browser" || bad "health: $h"

echo "== 2. deploy the release through ADM (full test gate)"
deployctl deploy "$ZIP" >"$T/deploy.out" 2>&1
r="$(result "$T/deploy.out")"
[ "$r" = DEPLOYED ] && ok "deployctl deploy -> DEPLOYED" || { bad "deployctl deploy -> ${r:-no RESULT}"; tail -30 "$T/deploy.out"; }
h="$(health)"; [ "$h" = "HEALTH PASS" ] && ok "after deploy: identity checks" || bad "after deploy: $h"
KG="$(live)"; KG_HASH="$(tree_hash "$KG")"
PREV="$(python3 -c 'import json;print(json.load(open("'"$RELS"'/.previous-known-good.json"))["release"])' 2>/dev/null)"
PREV_HASH="$(python3 -c 'import json;print(json.load(open("'"$RELS"'/.previous-known-good.json"))["code_sha256"])' 2>/dev/null)"
[ -n "$PREV" ] && ok "previous known-good recorded: $(basename "$PREV")" || bad "no previous known-good after two installs"

echo "== 3. one deploy at a time"
( ATTA_ADM_RUN_TESTS=0 deployctl deploy "$ZIP" >"$T/first.out" 2>&1 ) &
for _ in $(seq 1 60); do [ "$(lock_held)" = True ] && break; sleep 0.5; done
jobs_before="$(ls "$ROOT/adm/logs" | wc -l)"
deployctl deploy "$ZIP" >"$T/second.out" 2>&1
r="$(result "$T/second.out")"
[ "$r" = BUSY ] && ok "second deploy while one runs -> BUSY" || bad "second deploy -> ${r:-no RESULT}"
[ "$(ls "$ROOT/adm/logs" | wc -l)" = "$jobs_before" ] && ok "the refused deploy started nothing" || bad "the refused deploy left a job"

echo "== 4. deployctl killed mid-deploy"
sleep 3
J="$(newest_job)"
killed=""
for p in $(pgrep -x python3); do
  if tr '\0' ' ' </proc/"$p"/cmdline 2>/dev/null | grep -q 'deployctl deploy'; then kill -9 "$p" && killed="$p"; fi
done
wait 2>/dev/null
[ -n "$killed" ] && ok "SIGKILLed deployctl ($killed) during $J" || bad "no running deployctl to kill (the deploy ended too soon)"
st=""
for _ in $(seq 1 60); do
  st="$(job_state "$J")"; case "$st" in interrupted*|rolled_back*|failed*|timed_out*|live*) break ;; esac; sleep 2
done
case "$st" in
  "interrupted INTERRUPTED"*|"rolled_back ROLLED_BACK"*interrupted*) ok "atta-deployd recovered it: $st" ;;
  *) bad "after the kill the deploy is: ${st:-unknown}" ;;
esac
case "$st" in *failed*) bad "the killed deploy was recorded as failed (the reason is lost)" ;; esac
[ "$(deploy_procs "$J")" = 0 ] && ok "nothing of the killed deploy still runs" || bad "processes of $J still run"
[ "$(lock_held)" = False ] && ok "deploy lock freed" || bad "deploy lock still held"
sleep 5
[ "$(job_state "$J" | cut -d' ' -f1)" != building ] && [ "$(newest_job)" = "$J" ] && ok "the killed deploy was not re-run" \
  || bad "a new job appeared after recovery (re-run?)"
h="$(health)"; [ "$h" = "HEALTH PASS" ] && ok "after recovery: identity checks" || bad "after recovery: $h"

echo "== 5. a deploy past its time limit"
before="$(live)"
ATTA_ADM_DEPLOY_TIMEOUT=8 ATTA_ADM_RUN_TESTS=0 deployctl deploy "$ZIP" >"$T/timeout.out" 2>&1
r="$(result "$T/timeout.out")"; J="$(newest_job)"
[ "$r" = TIMED_OUT ] || [ "$r" = ROLLED_BACK ] && ok "time limit -> $r" || bad "time limit -> ${r:-no RESULT}"
case "$(job_state "$J")" in *timed_out*) ok "recorded as timed_out" ;; *) bad "recorded as: $(job_state "$J")" ;; esac
[ "$(deploy_procs "$J")" = 0 ] && ok "its whole process tree was stopped" || bad "processes of $J still run"
[ "$(live)" = "$before" ] && ok "the live release is untouched" || bad "live changed: $before -> $(live)"
h="$(health)"; [ "$h" = "HEALTH PASS" ] && ok "after the time limit: identity checks" || bad "after the time limit: $h"

echo "== 6. manual rollback to the previous known-good"
deployctl rollback >"$T/rollback.out" 2>&1
r="$(result "$T/rollback.out")"
[ "$r" = DEPLOYED ] && ok "deployctl rollback -> DEPLOYED" || { bad "deployctl rollback -> ${r:-no RESULT}"; tail -30 "$T/rollback.out"; }
[ -n "$PREV_HASH" ] && [ "$(tree_hash "$(live)")" = "$PREV_HASH" ] && ok "live code is byte-identical to the recorded previous known-good" \
  || bad "live code hash differs from the previous known-good"
h="$(health)"; [ "$h" = "HEALTH PASS" ] && ok "after rollback: identity checks" || bad "after rollback: $h"
[ "$KG_HASH" != "" ] && ok "(the release rolled back from: $(basename "$KG"))"

echo "== $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
