#!/usr/bin/env bash
# coolify_deploy_wait.sh APP_UUID — owner-side: trigger a deploy and wait; prints status + last log lines.
T=$(cat /root/owner-token); API=http://127.0.0.1:8000/api/v1; U="$1"
D=$(curl -sS -X POST -H "Authorization: Bearer $T" "$API/deploy?uuid=$U&force=false" | jq -r ".deployments[0].deployment_uuid")
bash /root/coolify_dep_status.sh "$D"
