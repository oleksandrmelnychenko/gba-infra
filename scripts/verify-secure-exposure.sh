#!/usr/bin/env sh
set -eu

blocked_ports='9200|9300|1433|35981|35982|62506|8081|8082'
bad_listeners=''

if command -v ss >/dev/null 2>&1; then
  bad_listeners="$(ss -ltnH | awk -v ports=":(${blocked_ports})$" '
    $4 ~ ports && $4 !~ /^127[.]0[.]0[.]1:/ && $4 !~ /^\[::1\]:/ { print }
  ')"
fi

if [ -n "$bad_listeners" ]; then
  echo "Unsafe public listeners detected:"
  echo "$bad_listeners"
  exit 1
fi

if command -v docker >/dev/null 2>&1; then
  bad_docker_ports="$(docker ps --format '{{.Names}} {{.Ports}}' | grep -E "(0\\.0\\.0\\.0|\\[::\\]):(${blocked_ports})->" || true)"
  if [ -n "$bad_docker_ports" ]; then
    echo "Unsafe Docker published ports detected:"
    echo "$bad_docker_ports"
    exit 1
  fi
fi

echo "OK: Elasticsearch, SQL Server, API and dev frontend ports are not publicly published."
