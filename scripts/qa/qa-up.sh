#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib-qa.sh"
qa_acquire_lock
qa_require_sql_container

qa_sql <<SQL >/dev/null
SET NOCOUNT ON;
$(qa_fence_sql)
$(for db in "${QA_DATABASES[@]}"; do qa_db_fence_sql "$db"; done)
SQL

compose_file="$(python3 "$SCRIPT_DIR/gen-qa-compose.py")"
docker compose --project-name "$QA_COMPOSE_PROJECT" -f "$compose_file" up -d --no-build --pull never --force-recreate

deadline=$((SECONDS + 600))
until [[ "$(docker ps --format '{{.Names}} {{.Status}}' | grep -cE '^data-(concord|analytics)-qa .*\(healthy\)')" -eq 2 ]]; do
  if (( SECONDS > deadline )); then
    docker ps --format '{{.Names}} {{.Status}}' | grep -E '^data-(concord|analytics)-qa' >&2 || true
    echo "QA backends did not become healthy in time." >&2
    exit 1
  fi
  sleep 5
done

for name in "${QA_BACKEND_CONTAINERS[@]}"; do
  echo "$name $(docker inspect --format '{{.Config.Image}} {{index .Config.Labels "gba.git.sha"}} {{.State.Health.Status}}' "$name")"
done
