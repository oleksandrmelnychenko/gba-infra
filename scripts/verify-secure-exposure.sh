#!/usr/bin/env sh
set -eu

blocked_ports='9200|9300|1433|35981|35982|62506|8081|8082|8083'
default_gateway='172.17.0.1'
env_file=''
validate_config_only='false'
bad_listeners=''

while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      [ "$#" -ge 2 ] || {
        echo 'Missing value for --env-file.' >&2
        exit 2
      }
      env_file="$2"
      shift 2
      ;;
    --validate-config-only)
      validate_config_only='true'
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

trusted_gateway=''
if [ "${GBA_DOCKER_HOST_GATEWAY_IP+x}" = 'x' ]; then
  trusted_gateway="${GBA_DOCKER_HOST_GATEWAY_IP:-$default_gateway}"
elif [ -n "$env_file" ]; then
  [ -f "$env_file" ] || {
    echo "Missing environment file: $env_file" >&2
    exit 1
  }
  trusted_gateway="$(awk '
    /^[[:space:]]*GBA_DOCKER_HOST_GATEWAY_IP[[:space:]]*=/ {
      value = $0
      sub(/^[^=]*=[[:space:]]*/, "", value)
      sub(/[[:space:]]*#.*$/, "", value)
      sub(/[[:space:]]*$/, "", value)
      if ((value ~ /^\047.*\047$/) || (value ~ /^".*"$/)) {
        value = substr(value, 2, length(value) - 2)
      }
      gateway = value
    }
    END { print gateway }
  ' "$env_file")"
fi
trusted_gateway="${trusted_gateway:-$default_gateway}"

if ! printf '%s\n' "$trusted_gateway" | awk -F. '
  NF == 4 &&
  $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/ && $4 ~ /^[0-9]+$/ &&
  $1 <= 255 && $2 <= 255 && $3 <= 255 && $4 <= 255 &&
  ($1 == 10 || ($1 == 172 && $2 >= 16 && $2 <= 31) || ($1 == 192 && $2 == 168)) {
    valid = 1
  }
  END { exit valid ? 0 : 1 }
'; then
  echo "Unsafe GBA_DOCKER_HOST_GATEWAY_IP: expected an RFC1918 IPv4 address." >&2
  exit 1
fi

if [ "$validate_config_only" = 'true' ]; then
  echo "OK: GBA_DOCKER_HOST_GATEWAY_IP is a private RFC1918 IPv4 address."
  exit 0
fi

command -v ss >/dev/null 2>&1 || {
  echo 'Cannot verify host listeners: ss is not installed.' >&2
  exit 1
}
if ! listener_output="$(ss -ltnH)"; then
  echo 'Cannot verify host listeners: ss failed.' >&2
  exit 1
fi
bad_listeners="$(printf '%s\n' "$listener_output" | awk -v ports=":(${blocked_ports})$" -v gateway="$trusted_gateway" '
  $4 ~ ports {
    host = $4
    sub(ports, "", host)
    if (host != "127.0.0.1" && host != "[::1]" && host != gateway) print
  }
')"

if [ -n "$bad_listeners" ]; then
  echo "Unsafe public listeners detected:"
  echo "$bad_listeners"
  exit 1
fi

command -v docker >/dev/null 2>&1 || {
  echo 'Cannot verify Docker port mappings: docker is not installed.' >&2
  exit 1
}
if ! docker_output="$(docker ps --format '{{.Names}} {{.Ports}}')"; then
  echo 'Cannot verify Docker port mappings: docker ps failed.' >&2
  exit 1
fi
bad_docker_ports="$(printf '%s\n' "$docker_output" | awk -v ports=":(${blocked_ports})$" -v gateway="$trusted_gateway" '
  {
    original = $0
    sub(/^[^[:space:]]+[[:space:]]+/, "")
    count = split($0, mappings, /,[[:space:]]*/)
    for (i = 1; i <= count; i++) {
      published = mappings[i]
      sub(/->.*$/, "", published)
      if (published ~ ports) {
        host = published
        sub(ports, "", host)
        if (host != "127.0.0.1" && host != "[::1]" && host != gateway) {
          print original
          break
        }
      }
    }
  }
')"
if [ -n "$bad_docker_ports" ]; then
  echo "Unsafe Docker published ports detected:"
  echo "$bad_docker_ports"
  exit 1
fi

echo "OK: DEV data, API, storefront and console ports are limited to loopback or the trusted Docker host gateway."
