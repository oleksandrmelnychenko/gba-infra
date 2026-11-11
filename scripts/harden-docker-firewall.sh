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

while iptables -C INPUT -i "$iface" -p tcp --dport 8000:8006 -j DROP 2>/dev/null; do
  iptables -D INPUT -i "$iface" -p tcp --dport 8000:8006 -j DROP
done
iptables -I INPUT 1 -i "$iface" -p tcp --dport 8000:8006 -j DROP

ipv6_state_file="${GBA_IPV6_DISABLE_STATE_FILE:-/proc/sys/net/ipv6/conf/all/disable_ipv6}"
if [ -r "$ipv6_state_file" ] && [ "$(cat "$ipv6_state_file")" = '0' ]; then
  command -v ip6tables >/dev/null 2>&1 || {
    echo 'IPv6 is enabled but ip6tables is not installed.' >&2
    exit 1
  }
  while ip6tables -C INPUT -i "$iface" -p tcp --dport 8000:8006 -j DROP 2>/dev/null; do
    ip6tables -D INPUT -i "$iface" -p tcp --dport 8000:8006 -j DROP
  done
  ip6tables -I INPUT 1 -i "$iface" -p tcp --dport 8000:8006 -j DROP
fi

echo "Docker firewall hardened on ${iface}: container ingress is limited to 80/443, host AI ports 8000-8006 are dropped from ${iface} for IPv4/IPv6, established egress replies allowed."
