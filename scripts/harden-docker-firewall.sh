#!/usr/bin/env sh
set -eu

iface="${1:-}"
if [ -z "$iface" ]; then
  iface="$(ip route get 1.1.1.1 | awk '{ for (i = 1; i <= NF; i++) if ($i == "dev") { print $(i + 1); exit } }')"
fi

if [ -z "$iface" ]; then
  echo "Could not detect the public network interface."
  exit 1
fi

iptables -N DOCKER-USER 2>/dev/null || true

iptables -C DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN 2>/dev/null \
  || iptables -I DOCKER-USER 1 -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN

iptables -C DOCKER-USER -i "$iface" -p tcp --dport 80 -j RETURN 2>/dev/null \
  || iptables -I DOCKER-USER 2 -i "$iface" -p tcp --dport 80 -j RETURN

iptables -C DOCKER-USER -i "$iface" -p tcp --dport 443 -j RETURN 2>/dev/null \
  || iptables -I DOCKER-USER 3 -i "$iface" -p tcp --dport 443 -j RETURN

iptables -C DOCKER-USER -i "$iface" -j DROP 2>/dev/null \
  || iptables -A DOCKER-USER -i "$iface" -j DROP

iptables -C DOCKER-USER -j RETURN 2>/dev/null \
  || iptables -A DOCKER-USER -j RETURN

iptables -C INPUT -i "$iface" -p tcp --dport 8000:8006 -j DROP 2>/dev/null \
  || iptables -I INPUT 1 -i "$iface" -p tcp --dport 8000:8006 -j DROP

echo "Docker firewall hardened on ${iface}: container ingress is limited to 80/443, host AI ports 8000-8006 are dropped from ${iface}, established egress replies allowed."
