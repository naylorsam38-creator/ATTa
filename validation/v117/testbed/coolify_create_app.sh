#!/usr/bin/env bash
# coolify_create_app.sh NAME BUILD_PACK PORT [BRANCH] — owner setup: one Coolify application from the testbed git server.
set -eu
N="$1"; BP="$2"; P="$3"; BR="${4:-main}"
T=$(cat /root/owner-token); API=http://127.0.0.1:8000/api/v1
PROJ=$(curl -sS -H "Authorization: Bearer $T" $API/projects | jq -r '.[] | select(.name=="atta-validation") | .uuid')
SRV=$(curl -sS -H "Authorization: Bearer $T" $API/servers | jq -r '.[0].uuid')
body=$(jq -nc --arg p "$PROJ" --arg s "$SRV" --arg n "$N" --arg bp "$BP" --arg port "$P" --arg br "$BR" \
  '{project_uuid:$p, server_uuid:$s, environment_name:"production", name:$n, git_repository:("http://172.30.0.8/"+$n+".git"),
    git_branch:$br, build_pack:$bp, ports_exposes:$port, instant_deploy:false}
   + (if $bp=="dockercompose" then {docker_compose_location:"/docker-compose.yml"} else {} end)')
curl -sS -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" -d "$body" $API/applications/public
echo
