#!/usr/bin/env bash
# container-egress.sh — v116: what app containers may reach (installed as /usr/local/sbin/atta-block-metadata,
# run by atta-block-metadata.service after every Docker start, because Docker rebuilds its chains then).
#
# Uploaded apps are untrusted. Their containers (any Docker bridge: docker0, br-*) may:
#   - talk to each other on their own network (Docker's own isolation still separates networks)
#   - answer connections made to them (published ports, the skin proxy)
#   - reach the public internet (APP_BUILDER_APP_EGRESS=public, the default), or nothing (=deny)
# and may NEVER reach:
#   - the cloud metadata service (it hands out this server's credentials)
#   - private, link-local, loopback or carrier-grade-NAT networks (the VPC, internal services)
#   - this server itself (nginx, Coolify, SSH, anything listening on the host)
# DNS (port 53) to a private resolver stays allowed, or name lookups inside the containers would fail.
# Idempotent: its own chains are flushed and refilled on every run.
set -u
IPT="${IPTABLES:-iptables}"; IPT6="${IP6TABLES:-ip6tables}"
MODE="${APP_BUILDER_APP_EGRESS:-public}"
case "$MODE" in public|deny) ;; *) echo "atta-egress: APP_BUILDER_APP_EGRESS must be public or deny (got '$MODE'); using public" >&2; MODE=public ;; esac
BRIDGES=(docker0 br-+)

apply() {   # apply IPTABLES_CMD FAMILY
  local ipt="$1" fam="$2" b net
  command -v "$ipt" >/dev/null 2>&1 || return 1
  "$ipt" -N DOCKER-USER 2>/dev/null || true
  for c in ATTA-EGRESS ATTA-HOST; do "$ipt" -N "$c" 2>/dev/null || true; "$ipt" -F "$c" || return 1; done
  "$ipt" -C DOCKER-USER -j ATTA-EGRESS 2>/dev/null || "$ipt" -I DOCKER-USER 1 -j ATTA-EGRESS || return 1
  "$ipt" -C INPUT -j ATTA-HOST 2>/dev/null || "$ipt" -I INPUT 1 -j ATTA-HOST || return 1

  # --- traffic a container sends THROUGH this server (FORWARD, via Docker's DOCKER-USER chain)
  "$ipt" -A ATTA-EGRESS -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
  for b in "${BRIDGES[@]}"; do
    "$ipt" -A ATTA-EGRESS -i "$b" -o docker0 -j RETURN
    "$ipt" -A ATTA-EGRESS -i "$b" -o br-+ -j RETURN
    if [ "$fam" = 4 ]; then
      "$ipt" -A ATTA-EGRESS -i "$b" -d 169.254.169.254/32 -j DROP
    else
      "$ipt" -A ATTA-EGRESS -i "$b" -d fd00:ec2::254/128 -j DROP
    fi
    "$ipt" -A ATTA-EGRESS -i "$b" -p udp --dport 53 -j RETURN
    "$ipt" -A ATTA-EGRESS -i "$b" -p tcp --dport 53 -j RETURN
    if [ "$fam" = 4 ]; then
      for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 127.0.0.0/8 0.0.0.0/8 \
                 192.0.0.0/24 198.18.0.0/15 224.0.0.0/4 240.0.0.0/4; do
        "$ipt" -A ATTA-EGRESS -i "$b" -d "$net" -j DROP
      done
    else
      for net in fc00::/7 fe80::/10 ::1/128 ff00::/8; do "$ipt" -A ATTA-EGRESS -i "$b" -d "$net" -j DROP; done
    fi
    [ "$MODE" = deny ] && "$ipt" -A ATTA-EGRESS -i "$b" -j DROP
  done
  "$ipt" -A ATTA-EGRESS -j RETURN

  # --- traffic a container sends TO this server itself (INPUT)
  "$ipt" -A ATTA-HOST -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
  for b in "${BRIDGES[@]}"; do "$ipt" -A ATTA-HOST -i "$b" -j DROP; done
  "$ipt" -A ATTA-HOST -j RETURN
}

ok=0
apply "$IPT" 4 && ok=1
apply "$IPT6" 6 || true
if [ "$ok" != 1 ]; then
  echo "atta-egress: iptables not available; app containers are NOT restricted (metadata, internal networks, this host)" >&2
  exit 1
fi
echo "atta-egress: containers restricted (mode: $MODE)"
