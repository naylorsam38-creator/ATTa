#!/usr/bin/env bash
# ATTa target inspection - READ-ONLY.
# Runs on the target EC2 host (piped over ssh). Reads state; never installs,
# starts, stops, deletes or edits anything. sudo is used only to read.
#
# Usage from the laptop (PowerShell):
#   $key="$env:USERPROFILE\Downloads\airexploit-key.pem"
#   $s=(Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/naylorsam38-creator/ATTa/claude/move-atta-to-500gb-ec2-fkvkit/tools/atta-inspect.sh).Content
#   $s | ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -i $key ubuntu@16.26.56.70 "tr -d '\r' | bash -s" | Tee-Object "$env:USERPROFILE\Desktop\atta-inspect.txt"

set -u
export LC_ALL=C
S() { sudo -n "$@" 2>&1; }                    # read-only commands needing root
sec() { printf '\n===== %s =====\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

sec "IDENTITY (proves which machine this is)"
TOKEN=$(curl -sf --noproxy '*' -m 3 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)
md() { curl -sf --noproxy '*' -m 3 ${TOKEN:+-H "X-aws-ec2-metadata-token: $TOKEN"} "http://169.254.169.254/latest/meta-data/$1" 2>/dev/null || echo "?"; }
echo "instance-id:   $(md instance-id)"
echo "instance-type: $(md instance-type)"
echo "az:            $(md placement/availability-zone)"
echo "public-ipv4:   $(md public-ipv4)"
echo "local-ipv4:    $(md local-ipv4)"
echo "hostname:      $(hostname)"
echo "whoami:        $(whoami)   sudo-nopasswd: $(sudo -n true 2>/dev/null && echo yes || echo NO)"

sec "OS / CPU / RAM / LOAD"
grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release
uname -r
echo "cpus: $(nproc)"
free -h
uptime

sec "DISKS (EBS size vs filesystem size)"
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
df -hT -x tmpfs -x devtmpfs -x overlay -x squashfs

sec "DOCKER"
if have docker; then
  S docker version --format 'client {{.Client.Version}} / server {{.Server.Version}}'
  S docker compose version
  S docker info --format 'root={{.DockerRootDir}} driver={{.Driver}} containers={{.Containers}} running={{.ContainersRunning}} images={{.Images}} mirrors={{.RegistryConfig.Mirrors}}'
  echo "--- /etc/docker/daemon.json"; S cat /etc/docker/daemon.json || true
  echo "--- containers"; S docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  echo "--- networks"; S docker network ls
  echo "--- volumes: $(S docker volume ls -q | wc -l)"
  echo "--- disk use"; S docker system df
else
  echo "docker: NOT INSTALLED"
fi
systemctl is-active docker 2>/dev/null | sed 's/^/docker.service: /'

sec "COOLIFY"
if S test -d /data/coolify; then
  echo "/data/coolify: present"; S ls -la /data/coolify
  echo "--- version (APP_VERSION / image tag)"
  S grep -E '^(APP_VERSION|APP_ENV|APP_URL|PUSHER_HOST)=' /data/coolify/source/.env
  S docker inspect coolify --format 'image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}}'
  echo "--- proxy"; S ls -la /data/coolify/proxy
  S docker ps --filter name=coolify-proxy --format '{{.Names}} {{.Image}} {{.Status}}'
else
  echo "/data/coolify: ABSENT (Coolify not installed via standard installer)"
fi
echo "--- local API health: $(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/health || true) (000 = nothing answering)"

sec "ATTa"
for d in /srv/app-builder /opt/atta /opt/ATTa /home/*/ATTa* /root/ATTa*; do S test -e "$d" && { echo "found: $d"; S ls -la "$d" | head -30; }; done
echo "--- ATTa env keys (names only, no values)"
S sh -c 'test -f /srv/app-builder/.env && cut -d= -f1 /srv/app-builder/.env | grep -v "^#" | sort | tr "\n" " "' ; echo
echo "--- ATTa version markers"
S sh -c 'cat /srv/app-builder/VERSION /srv/app-builder/*/VERSION 2>/dev/null; ls /srv/app-builder/releases 2>/dev/null | tail -5'
echo "--- systemd units"
systemctl list-units --all --no-pager --plain 'app-builder*' 'atta*' 'deployd*' 2>/dev/null | head -20
echo "--- bundles on disk"
S find / -xdev \( -iname 'ATTa*.zip' -o -iname 'coolify-aws-ready*.zip' \) -printf '%TY-%Tm-%Td %s %p\n' 2>/dev/null | head -20
echo "--- upstream app list(s)"
S find / -xdev -path '*/04-deployment/upstream_apps.json' -printf '%p\n' 2>/dev/null | head -5

sec "LISTENING PORTS"
S ss -ltnp | awk 'NR==1 || /:(22|80|443|8000|6001|6002|8080)\b/'

sec "FIREWALL"
S ufw status verbose || true
echo "--- iptables INPUT policy/first rules"; S iptables -S INPUT | head -15

sec "WEB / TLS / DOMAIN"
for u in http://127.0.0.1/ https://127.0.0.1/; do echo "$u -> $(curl -sk -m 5 -o /dev/null -w '%{http_code}' "$u" || true)"; done
sudo -n ls /etc/nginx/sites-enabled 2>/dev/null | sed 's/^/nginx site: /'
sudo -n ls /etc/letsencrypt/live 2>/dev/null | sed 's/^/letsencrypt: /'
sudo -n grep -rl airexploit /etc/nginx /data/coolify/proxy 2>/dev/null | sed 's/^/mentions airexploit: /' | head

sec "DONE - read-only, nothing changed"
