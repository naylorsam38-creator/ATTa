#!/usr/bin/env bash
# start-host.sh NAME IP — an EC2-like Ubuntu 24.04 server (systemd PID 1, own Docker) on the air-gapped testbed.
set -eu
NAME="$1"; IP="$2"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --hostname "$NAME" --network testbed --ip "$IP" --privileged --cgroupns=host \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
  -v "$NAME-docker:/var/lib/docker" -v "$NAME-containerd:/var/lib/containerd" \
  atta-testbed-host:24.04 >/dev/null
sleep 5
docker exec "$NAME" bash -c '
cat > /etc/docker/daemon.json <<J
{
  "registry-mirrors": ["http://172.30.0.6:5000"],
  "insecure-registries": ["172.30.0.6:5000"]
}
J
printf "[global]\nno-index = true\nfind-links = http://172.30.0.5/wheels/\ntrusted-host = 172.30.0.5\n" > /etc/pip.conf
printf "PLAYWRIGHT_DOWNLOAD_HOST=http://172.30.0.5\n" >> /etc/environment
sed -i "s|^DefaultEnvironment=\"|DefaultEnvironment=\"PLAYWRIGHT_DOWNLOAD_HOST=http://172.30.0.5\" \"|" /etc/systemd/system.conf.d/10-ca.conf
systemctl daemon-reexec; systemctl restart docker'
docker exec "$NAME" docker run --rm hello-world >/dev/null && echo "HOST_READY $NAME $IP"
