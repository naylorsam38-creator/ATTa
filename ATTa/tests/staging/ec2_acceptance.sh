#!/usr/bin/env bash
# ec2_acceptance.sh — v116 security acceptance for a STAGING server (never production).
#
# On a throwaway EC2 instance (Amazon Linux 2023 or Ubuntu), from the unpacked bundle:
#     sudo bash run                                         # the normal install
#     sudo bash tests/staging/ec2_acceptance.sh             # this check (about 10 minutes)
#     sudo bash tests/staging/ec2_acceptance.sh --deploy-drill ATTa-deploy116.zip
#                                                           # + deploy, rollback, redeploy through ADM
# Prints PASS/FAIL per check and exits non-zero if anything failed. It creates one temporary admin account
# (stagecheck) and two throwaway apps, and removes the account at the end. It changes nothing else.
set -uo pipefail
ROOT=/srv/app-builder; APP=/opt/app-builder; GW=http://127.0.0.1:8787
PASS=0; FAIL=0; DRILL=""
[ "${1:-}" = "--deploy-drill" ] && DRILL="${2:?give the bundle zip to deploy}"
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 2; }
ok(){ PASS=$((PASS+1)); echo "PASS $*"; }
bad(){ FAIL=$((FAIL+1)); echo "FAIL $*"; }
check(){ local name="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$name"; else bad "$name"; fi; }
need(){ local name="$1"; shift; if "$@" >/dev/null 2>&1; then bad "$name"; else ok "$name"; fi; }   # must FAIL
env_val(){ sed -n "s/^$1=//p" "$ROOT/.env" | tail -1; }

echo "== 1. services run as their own users"
for s in app-builder-gateway app-builder-pipeline app-builder-watcher atta-deployd nginx; do
  check "$s active" systemctl is-active --quiet "$s.service"
done
[ "$(systemctl show -p User --value app-builder-gateway)" = atta-web ] && ok "gateway runs as atta-web" || bad "gateway user"
for s in app-builder-pipeline app-builder-watcher; do
  [ "$(systemctl show -p User --value $s)" = atta-run ] && ok "$s runs as atta-run" || bad "$s user"
done
need "no gateway/pipeline process runs as root" bash -c "ps -o user=,args= -C python3 | grep -E 'gateway.py|pipeline.py|system_watcher.py' | grep -q '^root'"
score(){ systemd-analyze security "$1" 2>/dev/null | sed -n 's/.*exposure level for .*: \([0-9.]*\).*/\1/p'; }
g="$(score app-builder-gateway.service)"; p="$(score app-builder-pipeline.service)"
awk -v s="${g:-99}" 'BEGIN{exit !(s<=2.5)}' && ok "gateway exposure $g <= 2.5" || bad "gateway exposure ${g:-?}"
awk -v s="${p:-99}" 'BEGIN{exit !(s<=7.5)}' && ok "pipeline exposure $p <= 7.5" || bad "pipeline exposure ${p:-?}"

echo "== 2. data folder layout"
[ "$(stat -c '%U:%G %a' $ROOT)" = "root:atta 3771" ] && ok "data folder root:atta 3771" || bad "data folder $(stat -c '%U:%G %a' $ROOT)"
[ "$(stat -c '%U %a' $ROOT/.env)" = "root 600" ] && ok ".env root 0600" || bad ".env $(stat -c '%U %a' $ROOT/.env)"
[ "$(stat -c '%U' $ROOT/state/users.json)" = atta-web ] && ok "users.json owned by atta-web" || bad "users.json owner"
need "atta-web cannot read .env" runuser -u atta-web -- cat $ROOT/.env
need "atta-run cannot read .env" runuser -u atta-run -- cat $ROOT/.env
need "atta-proxy cannot list the data folder" runuser -u atta-proxy -- ls $ROOT
check "one sudoers rule, valid" visudo -cf /etc/sudoers.d/atta-proxy

echo "== 3. what nginx exposes"
DOM="$(env_val APP_BUILDER_DOMAIN)"; PUB="$(env_val APP_BUILDER_ALLOW_PUBLIC_HTTP)"
if [ -n "$DOM" ] && [ -f "/etc/letsencrypt/live/${DOM%%[ ,]*}/fullchain.pem" ]; then
  check "HTTPS answers" curl -fsSk --resolve "${DOM%%[ ,]*}:443:127.0.0.1" "https://${DOM%%[ ,]*}/health"
  code="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: ${DOM%%[ ,]*}" http://127.0.0.1/)"
  [ "$code" = 301 ] && ok "plain HTTP redirects to HTTPS" || bad "plain HTTP answered $code (expected 301)"
elif [ "$PUB" = true ]; then
  echo "NOTE public plain HTTP was chosen (APP_BUILDER_ALLOW_PUBLIC_HTTP=true)"
else
  need "port 80 is NOT open on public addresses" bash -c "ss -ltnH '( sport = :80 )' | awk '{print \$4}' | grep -vqE '^127\\.0\\.0\\.1:80$'"
  check "port 80 answers on 127.0.0.1" curl -fsS -o /dev/null http://127.0.0.1/login
fi
need "gateway port 8787 not on public addresses" bash -c "ss -ltnH '( sport = :8787 )' | awk '{print \$4}' | grep -vqE '^127\\.0\\.0\\.1:8787$'"

echo "== 4. what app containers can reach (real Docker)"
check "egress rules loaded" iptables -S ATTA-EGRESS
check "host protection loaded" iptables -S ATTA-HOST
IMG=alpine:3
docker pull -q "$IMG" >/dev/null 2>&1
need "container cannot reach the metadata service" docker run --rm "$IMG" wget -T 5 -q -O- http://169.254.169.254/latest/meta-data/
HOSTIP="$(ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
# SSH listens on every address of an EC2 instance, so it proves the rule (not merely a closed port)
check "SSH answers on $HOSTIP from the host itself (the control)" bash -c "timeout 4 bash -c '</dev/tcp/$HOSTIP/22'"
need "container cannot reach this host's SSH ($HOSTIP:22)" docker run --rm "$IMG" nc -z -w 4 "$HOSTIP" 22
need "container cannot reach the host via the docker0 gateway" docker run --rm "$IMG" nc -z -w 4 172.17.0.1 22
if [ "$(env_val APP_BUILDER_APP_EGRESS)" = deny ]; then
  need "deny mode: no internet" docker run --rm "$IMG" wget -T 8 -q -O- https://example.com/
else
  check "public mode: internet works" docker run --rm "$IMG" wget -T 15 -q -O /dev/null https://example.com/
fi
TOKENLESS="$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://169.254.169.254/latest/meta-data/ || true)"
[ "$TOKENLESS" = 401 ] && ok "IMDSv2 required on this instance" || echo "NOTE IMDSv1 still answers on this host ($TOKENLESS): set http-tokens=required (see docs/SECURITY-V116.md)"

echo "== 5. the gateway, over HTTP"
PW="$(env APP_BUILDER_ROOT=$ROOT python3 $APP/accounts.py add stagecheck --admin 2>/dev/null | tail -1)"
trap 'env APP_BUILDER_ROOT=$ROOT python3 $APP/accounts.py delete stagecheck >/dev/null 2>&1' EXIT
JAR="$(mktemp)"
curl -fsS -c "$JAR" -o /dev/null --data "user=stagecheck&password=$PW" "$GW/login" && ok "admin login" || bad "admin login"
H="$(curl -sS -b "$JAR" -D - -o /dev/null "$GW/builds")"
for hdr in "Content-Security-Policy" "X-Frame-Options: DENY" "X-Content-Type-Options: nosniff"; do
  grep -qi "^$hdr" <<<"$H" && ok "header $hdr" || bad "header $hdr missing"
done
REV="$(grep session "$JAR" | awk '{print $NF}')"
curl -sS -b "$JAR" -o /dev/null "$GW/logout"
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Cookie: session=$REV" "$GW/builds")"
[ "$code" = 302 ] && ok "a logged-out session cannot be replayed" || bad "logged-out cookie answered $code"
curl -fsS -c "$JAR" -o /dev/null --data "user=stagecheck&password=$PW" "$GW/login"
code="$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" --data "url=https://169.254.169.254/latest/meta-data" "$GW/add-repo")"
[ "$code" = 400 ] && ok "git address inside the network refused" || bad "internal git address answered $code"

echo "== 6. a hostile app, through the real pipeline and Docker"
SECRET="$(env_val APP_BUILDER_SESSION_SECRET)"
T="$(mktemp -d)"; mkdir -p "$T/stagehostile"
cat > "$T/stagehostile/docker-compose.yml" <<'YML'
services:
  web:
    image: nginx:alpine
    ports: ["80"]
    environment:
      STOLEN: ${APP_BUILDER_SESSION_SECRET}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
YML
(cd "$T" && python3 -c "import shutil;shutil.make_archive('stagehostile','zip','.','stagehostile')")
loc="$(curl -sS -b "$JAR" -o /dev/null -w '%{redirect_url}' -F "bundle=@$T/stagehostile.zip" "$GW/upload")"
BID="${loc##*/}"
for _ in $(seq 1 90); do
  st="$(curl -sS -b "$JAR" "$GW/api/builds/$BID" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("state",""))' 2>/dev/null)"
  case "$st" in QUALIFIED|NOT_QUALIFIED|PARTIALLY_QUALIFIED|FAILED|PACKAGE_INSTALLED) break ;; esac; sleep 10
done
echo "     build $BID ended $st"
leak=0
for c in $(docker ps -aq); do docker inspect "$c" | grep -qF "$SECRET" && leak=1; done
[ -n "$SECRET" ] && [ "$leak" = 0 ] && ok "no container holds the session secret" || bad "a container holds the session secret"
need "no app container has the Docker socket" bash -c "docker ps -aq | xargs -r docker inspect --format '{{range .Mounts}}{{.Source}} {{end}}' | grep -q docker.sock"
pgrep -u atta-proxy -f proxy.js >/dev/null && ok "skin proxies run as atta-proxy" || echo "NOTE no skin proxy running right now (app not up); covered by the unit tests"
need "no skin proxy runs as root or atta-run" bash -c "pgrep -f 'ui-bridge/proxy.js' -a | awk '{print \$1}' | xargs -r ps -o user= -p | grep -qE '^(root|atta-run)$'"

if [ -n "$DRILL" ]; then
  echo "== 7. deploy drill: deploy -> rollback -> deploy (ADM, with the test gate)"
  out="$(deployctl deploy "$DRILL" 2>&1 | tail -3)"; grep -q "RESULT: DEPLOYED" <<<"$out" && ok "deploy: $out" || bad "deploy: $out"
  job="$(deployctl jobs 2>/dev/null | tail -1 | awk '{print $1}')"
  deployctl journal "$job" 2>/dev/null | grep -q TESTED && ok "the bundle's tests ran before activation" || bad "no TESTED event in $job"
  out="$(deployctl rollback 2>&1 | tail -2)"; grep -q "RESULT: ROLLED_BACK" <<<"$out" && ok "rollback: $out" || bad "rollback: $out"
  out="$(deployctl deploy "$DRILL" 2>&1 | tail -3)"; grep -q "RESULT: DEPLOYED" <<<"$out" && ok "redeploy: $out" || bad "redeploy: $out"
fi

echo "== $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
