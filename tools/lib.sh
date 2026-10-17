# Helpers sourced per app. APP and L (log dir) must be set.
SP=/tmp/claude-0/-home-user-ATTa/c994121a-09ca-5a78-86c9-8561209a3bdd/scratchpad
T=/home/user/ATTa/tools
step() { echo "=== [$APP] $* ($(date -u +%H:%M:%S))" | tee -a $L/steps.log; }
# grab container logs for all containers of a compose project / or listed containers
grab_logs() { mkdir -p $L/containers; for c in $(docker ps -a --format '{{.Names}}'); do docker logs --timestamps $c > $L/containers/$c.log 2>&1; done; docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' > $L/containers/ps.txt; }
log_errors() { grep -i -E 'error|exception|fatal|panic|traceback|failed|critical|\bERR\b|segfault|denied|refused|unhealthy' $L/containers/*.log | grep -v -i -E 'error_log|errorlog|0 errors|no error|errors: 0|level=info' > $L/container-errors.txt; wc -l < $L/container-errors.txt; }
nuke() { docker ps -aq | xargs -r docker rm -f >/dev/null; docker volume ls -q | xargs -r docker volume rm -f >/dev/null; docker network prune -f >/dev/null; docker images -aq | xargs -r docker rmi -f >/dev/null 2>&1; docker builder prune -af >/dev/null; docker system prune -af --volumes >/dev/null; df -h / | tail -1; }
waithttp() { local url=$1 t=${2:-300} i=0; while [ $i -lt $t ]; do code=$(curl -sk -o /dev/null -m 5 -w '%{http_code}' "$url"); if [ "$code" != "000" ]; then echo "HTTP $code after ${i}s"; return 0; fi; sleep 5; i=$((i+5)); done; echo "NO RESPONSE after ${t}s"; return 1; }
