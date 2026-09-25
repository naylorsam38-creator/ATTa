#!/usr/bin/env bash
# drain.sh [MAX_S] — as sam on atta-laptop: wait until no build is queued/running and no heal is mid-round.
R=$HOME/.app-builder-local; MAX=${1:-3600}; t0=$(date +%s)
while :; do
  busy=$(python3 - <<'PY'
import json, glob, os
R = os.path.expanduser("~/.app-builder-local")
n = 0
for f in glob.glob(R + "/state/builds/*.json"):
    d = json.load(open(f))
    if d["state"] not in ("QUALIFIED", "PARTIALLY_QUALIFIED", "NOT_QUALIFIED", "FAILED", "PACKAGE_INSTALLED"): n += 1
print(n)
PY
)
  inbox=$(ls $R/inbox | grep -vcE '\.(processed|failed)\.')
  [ "$busy" = 0 ] && [ "$inbox" = 0 ] && { echo "DRAINED after $(( $(date +%s)-t0 ))s"; break; }
  [ $(( $(date +%s)-t0 )) -gt $MAX ] && { echo "STILL BUSY after ${MAX}s: busy=$busy inbox=$inbox"; break; }
  sleep 10
done
