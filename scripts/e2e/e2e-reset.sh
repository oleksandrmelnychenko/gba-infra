#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ROOT="${INFRA_ROOT:-/root/projects/gba-infra}"
source "$SCRIPT_DIR/lib-e2e-sql.sh"
e2e_acquire_stand_lock

SNAPSHOT_DIR="/var/opt/mssql/data/e2e-snapshots"
ACTION="${1:-revert}"
PRUNE_UPLOADS=0
[[ "${2:-}" == "--prune-uploads" ]] && PRUNE_UPLOADS=1

compose=(docker compose -p "$E2E_COMPOSE_PROJECT" -f "$INFRA_ROOT/docker-compose.e2e.yml" --env-file "$INFRA_ROOT/.env.e2e")

exec 9>/var/lock/gba-e2e-reset.lock
if ! flock -n 9; then
  echo "Another e2e reset is already running." >&2
  exit 1
fi

e2e_require_sql_container

snapshot_sql() {
  local db="$1"
  cat <<SQL
SET NOCOUNT ON;
SET XACT_ABORT ON;
$(e2e_fence_sql)
$(e2e_db_fence_sql "$db")
DECLARE @snap sysname, @stmt nvarchar(max);
DECLARE snap_cur CURSOR LOCAL FAST_FORWARD FOR
    SELECT name FROM sys.databases WHERE source_database_id = DB_ID(N'$db');
OPEN snap_cur;
FETCH NEXT FROM snap_cur INTO @snap;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @stmt = N'DROP DATABASE ' + QUOTENAME(@snap) + N';';
    EXEC (@stmt);
    FETCH NEXT FROM snap_cur INTO @snap;
END
CLOSE snap_cur;
DEALLOCATE snap_cur;
SET @stmt = NULL;
SELECT @stmt = COALESCE(@stmt + N', ', N'') + N'(NAME = ' + QUOTENAME(name, '''') + N', FILENAME = N''$SNAPSHOT_DIR/${db}_' + CONVERT(nvarchar(10), file_id) + N'.ss'')'
FROM sys.master_files
WHERE database_id = DB_ID(N'$db') AND type = 0
ORDER BY file_id;
IF @stmt IS NULL
    THROW 54820, N'No data files found for database: $db', 1;
SET @stmt = N'CREATE DATABASE ' + QUOTENAME(N'${db}_SNAP') + N' ON ' + @stmt + N' AS SNAPSHOT OF ' + QUOTENAME(N'$db') + N';';
EXEC (@stmt);
SQL
}

revert_sql() {
  local db="$1"
  cat <<SQL
SET NOCOUNT ON;
SET XACT_ABORT ON;
$(e2e_fence_sql)
$(e2e_db_fence_sql "$db")
IF (SELECT COUNT(*) FROM sys.databases WHERE source_database_id = DB_ID(N'$db')) <> 1
    THROW 54821, N'Database $db must have exactly one snapshot to revert; run e2e-reset.sh drop-snapshots && e2e-reset.sh snapshot.', 1;
IF DB_ID(N'${db}_SNAP') IS NULL
    THROW 54822, N'Expected snapshot ${db}_SNAP is missing.', 1;
ALTER DATABASE [$db] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
RESTORE DATABASE [$db] FROM DATABASE_SNAPSHOT = N'${db}_SNAP';
ALTER DATABASE [$db] SET MULTI_USER;
SQL
}

drop_snapshots_sql() {
  local db="$1"
  cat <<SQL
SET NOCOUNT ON;
SET XACT_ABORT ON;
$(e2e_fence_sql)
$(e2e_db_fence_sql "$db")
DECLARE @snap sysname, @stmt nvarchar(500);
DECLARE snap_cur CURSOR LOCAL FAST_FORWARD FOR
    SELECT name FROM sys.databases WHERE source_database_id = DB_ID(N'$db');
OPEN snap_cur;
FETCH NEXT FROM snap_cur INTO @snap;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @stmt = N'DROP DATABASE ' + QUOTENAME(@snap) + N';';
    EXEC (@stmt);
    FETCH NEXT FROM snap_cur INTO @snap;
END
CLOSE snap_cur;
DEALLOCATE snap_cur;
SQL
}

wait_health() {
  local url="$1" name="$2" deadline=$((SECONDS + 180))
  until curl -fsS "$url" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "Health check timed out for $name ($url)" >&2
      exit 1
    fi
    sleep 3
  done
  echo "healthy: $name"
}

case "$ACTION" in
  snapshot)
    docker exec "$SQL_CONTAINER" mkdir -p "$SNAPSHOT_DIR"
    for db in "${E2E_DATABASES[@]}"; do
      echo "== snapshot: $db"
      snapshot_sql "$db" | e2e_sql
    done
    ;;
  revert)
    echo "== stopping e2e backends"
    "${compose[@]}" stop data-concord-e2e data-analytics-e2e >/dev/null 2>&1 || true
    for db in "${E2E_DATABASES[@]}"; do
      echo "== revert: $db"
      revert_sql "$db" | e2e_sql
    done
    if [[ "$PRUNE_UPLOADS" -eq 1 ]]; then
      echo "== pruning upload volume"
      docker run --rm -v gba-e2e_gba-e2e-concord-data:/prune alpine sh -c 'find /prune -mindepth 1 -delete'
    fi
    echo "== starting e2e backends"
    "${compose[@]}" start data-concord-e2e data-analytics-e2e
    wait_health "http://127.0.0.1:35991/health" data-concord-e2e
    wait_health "http://127.0.0.1:35994/health" data-analytics-e2e
    ;;
  drop-snapshots)
    for db in "${E2E_DATABASES[@]}"; do
      echo "== drop snapshots: $db"
      drop_snapshots_sql "$db" | e2e_sql
    done
    ;;
  status)
    e2e_sql <<SQL
SET NOCOUNT ON;
$(e2e_fence_sql)
SELECT d.name, d.state_desc, d.create_date,
       src.name AS source_db
FROM sys.databases d
LEFT JOIN sys.databases src ON src.database_id = d.source_database_id
WHERE d.name LIKE N'%[_]E2E' OR d.source_database_id IS NOT NULL
ORDER BY d.name;
SQL
    for db in "${E2E_DATABASES[@]}"; do
      printf '%s marker: ' "$db"
      e2e_sql_tsv <<SQL || true
SET NOCOUNT ON;
SELECT CONVERT(nvarchar(4000), value) FROM [$db].sys.extended_properties WHERE class = 0 AND name = N'$MARKER_NAME';
SQL
    done
    docker ps --filter name=-e2e --format '{{.Names}}\t{{.Status}}'
    ;;
  *)
    echo "Usage: e2e-reset.sh [snapshot|revert [--prune-uploads]|drop-snapshots|status]" >&2
    exit 1
    ;;
esac
