# Testbed "server" image: an EC2-like Ubuntu 24.04 box with systemd as PID 1.
# The only non-stock item is the sandbox egress CA (this session's network re-terminates TLS);
# it is installed the way a corporate TLS-inspecting network would require.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive container=docker
COPY sandbox-egress-ca.crt /usr/local/share/ca-certificates/sandbox-egress-ca.crt
RUN sed -i 's|http://|http://|' /etc/apt/sources.list.d/ubuntu.sources \
 && apt-get -o Acquire::https::Verify-Peer=false update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && update-ca-certificates \
 && apt-get install -y --no-install-recommends systemd systemd-sysv dbus sudo openssh-server curl wget git jq openssl unzip zip \
      python3 python3-venv python3-pip iproute2 iptables procps less vim-tiny nginx docker.io docker-compose-v2 \
 && apt-get clean && rm -rf /var/lib/apt/lists/* \
 && systemctl mask systemd-logind getty.target console-getty.service systemd-udevd.service systemd-udev-trigger.service \
 && mkdir -p /etc/docker && echo '{"registry-mirrors":["https://mirror.gcr.io"]}' > /etc/docker/daemon.json \
 && mkdir -p /etc/systemd/system.conf.d && printf '[Manager]\nDefaultEnvironment="NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt" "REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt" "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt" "PIP_CERT=/etc/ssl/certs/ca-certificates.crt"\n' > /etc/systemd/system.conf.d/10-ca.conf
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
# pip/npm use their own CA stores; point them at the OS store (which holds the sandbox egress CA).
RUN printf '[global]\ncert = /etc/ssl/certs/ca-certificates.crt\n' > /etc/pip.conf \
 && printf 'cafile=/etc/ssl/certs/ca-certificates.crt\n' > /etc/npmrc \
 && printf 'NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt\nREQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt\nSSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt\n' >> /etc/environment
