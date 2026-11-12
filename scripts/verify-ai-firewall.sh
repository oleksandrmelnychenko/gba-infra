#!/usr/bin/env sh
set -eu

iface="${1:-}"
if [ -z "$iface" ]; then
  iface="$(ip route get 1.1.1.1 | awk '{ for (i = 1; i <= NF; i++) if ($i == "dev") { print $(i + 1); exit } }')"
fi

if [ -z "$iface" ]; then
  echo 'Could not detect the public network interface.' >&2
  exit 1
fi

command -v iptables >/dev/null 2>&1 || {
  echo 'Cannot verify AI firewall: iptables is not installed.' >&2
  exit 1
}

ai_ports='8000:8006,8012,8015'

first_rule_is_ai_drop() {
  tool="$1"
  "$tool" -S INPUT | awk -v iface="$iface" -v ports="$ai_ports" '
    $1 == "-A" && $2 == "INPUT" {
      found = 1
      interface_ok = port_ok = verdict_ok = 0
      for (i = 1; i <= NF; i++) {
        if ($i == "-i" && $(i + 1) == iface) interface_ok = 1
        if ($i == "--dports" && $(i + 1) == ports) port_ok = 1
        if ($i == "-j" && $(i + 1) == "DROP") verdict_ok = 1
      }
      exit !(interface_ok && port_ok && verdict_ok)
    }
    END { if (!found) exit 1 }
  '
}

first_rule_is_ai_drop iptables || {
  echo "The IPv4 AI DROP rule is missing or shadowed on $iface." >&2
  exit 1
}

ipv6_state_file="${GBA_IPV6_DISABLE_STATE_FILE:-/proc/sys/net/ipv6/conf/all/disable_ipv6}"
if [ -r "$ipv6_state_file" ] && [ "$(cat "$ipv6_state_file")" = '0' ]; then
  command -v ip6tables >/dev/null 2>&1 || {
    echo 'IPv6 is enabled but ip6tables is not installed.' >&2
    exit 1
  }
  first_rule_is_ai_drop ip6tables || {
    echo "The IPv6 AI DROP rule is missing or shadowed on $iface." >&2
    exit 1
  }
fi

echo "OK: public ingress to AI ports $ai_ports is blocked on $iface."
