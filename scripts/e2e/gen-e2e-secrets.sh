#!/usr/bin/env bash
set -euo pipefail

INFRA_ROOT="${INFRA_ROOT:-/root/projects/gba-infra}"
SRC_DIR="$INFRA_ROOT/secrets/dev"
DST_DIR="$INFRA_ROOT/secrets/e2e"

declare -A REWRITES=(
  [ConnectionStrings__LocalConnectionString]="ConcordDb_V5:ConcordDb_V5_E2E"
  [ConnectionStrings__LocalConnectionStringDataAnalitic]="ConcordDb_Data:ConcordDb_Data_E2E"
  [ConnectionStrings__LocalIdentityConnectionString]="ConcordIdentityDb:ConcordIdentityDb_E2E"
  [ConnectionStrings__VehicleRegistryConnectionString]="GbaVehicleRegistry:GbaVehicleRegistry_E2E"
)

mkdir -p "$DST_DIR"

for name in "${!REWRITES[@]}"; do
  src="$SRC_DIR/$name"
  dst="$DST_DIR/$name"
  if [[ ! -f "$src" ]]; then
    echo "Missing dev secret: $src" >&2
    exit 1
  fi
  from="${REWRITES[$name]%%:*}"
  to="${REWRITES[$name]##*:}"
  sed "s/Database=${from};/Database=${to};/" "$src" > "$dst.tmp"
  if ! grep -q "Database=${to};" "$dst.tmp"; then
    echo "Rewrite failed for $name (expected Database=${from};)" >&2
    rm -f "$dst.tmp"
    exit 1
  fi
  if grep -q "Database=${from};" "$dst.tmp"; then
    echo "Rewrite left the source database name in $name" >&2
    rm -f "$dst.tmp"
    exit 1
  fi
  chmod --reference="$src" "$dst.tmp"
  chown --reference="$src" "$dst.tmp"
  mv "$dst.tmp" "$dst"
  echo "wrote $dst"
done
