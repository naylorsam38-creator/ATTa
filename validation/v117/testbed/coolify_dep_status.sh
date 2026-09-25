#!/usr/bin/env bash
# coolify_dep_status.sh DEPLOYMENT_UUID [WAIT_S] — wait for a Coolify deployment to end; print status + visible log tail.
T=$(cat /root/owner-token); API=http://127.0.0.1:8000/api/v1; D="$1"; W="${2:-900}"
for i in $(seq 1 $((W/5))); do s=$(curl -sS -H "Authorization: Bearer $T" $API/deployments/$D | jq -r .status); case $s in finished|failed|cancelled*) break;; esac; sleep 5; done
echo "deployment=$D status=$s"
docker exec coolify php artisan tinker --execute="echo App\Models\ApplicationDeploymentQueue::where(\"deployment_uuid\",\"$D\")->first()->logs;" 2>&1 | python3 -c "
import json,sys
raw=sys.stdin.read(); s=raw[raw.find('['):]
try:
  for e in [x for x in json.loads(s) if not x.get('hidden')][-8:]: print('   ', str(e.get('output',''))[:220].replace(chr(10),' | '))
except Exception: print(raw[-600:])"
