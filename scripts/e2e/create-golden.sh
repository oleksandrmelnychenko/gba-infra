#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ROOT="${INFRA_ROOT:-/root/projects/gba-infra}"
SERVER_ROOT="${SERVER_ROOT:-/root/projects/gba-server}"
source "$SCRIPT_DIR/lib-e2e-sql.sh"
e2e_acquire_stand_lock

EXPORT_STAMP="${EXPORT_STAMP:-20260817T153552Z}"
EXPORT_DIR="/var/opt/mssql/backup/gba-dev-export-$EXPORT_STAMP"
V5_BAK="$EXPORT_DIR/ConcordDb_V5_$EXPORT_STAMP.bak"
IDENTITY_BAK="$EXPORT_DIR/ConcordIdentityDb_$EXPORT_STAMP.bak"
DATA_DIR="/var/opt/mssql/data/e2e"
GOLDEN_BAK_DIR="/var/opt/mssql/backup/e2e-golden"
SKIP_MIGRATOR=0
[[ "${1:-}" == "--skip-migrator" ]] && SKIP_MIGRATOR=1

exec 9>/var/lock/gba-e2e-golden.lock
if ! flock -n 9; then
  echo "Another golden build is already running." >&2
  exit 1
fi

e2e_require_sql_container
if e2e_backends_running; then
  echo "Stop the gba-e2e backends first: docker compose -p gba-e2e -f docker-compose.e2e.yml --env-file .env.e2e stop" >&2
  exit 1
fi

"$SCRIPT_DIR/gen-e2e-secrets.sh"

for bak in "$V5_BAK" "$IDENTITY_BAK"; do
  if ! docker exec "$SQL_CONTAINER" test -f "$bak"; then
    echo "Backup not found in container: $bak" >&2
    exit 1
  fi
done

docker exec "$SQL_CONTAINER" mkdir -p "$DATA_DIR" "$GOLDEN_BAK_DIR"

console_sha="$(docker image inspect -f '{{ index .Config.Labels "gba.git.sha" }}' gba-console:e2e 2>/dev/null || echo unknown)"
concord_sha="$(docker image inspect -f '{{ index .Config.Labels "gba.git.sha" }}' gba-data-concord:e2e 2>/dev/null || echo unknown)"
created="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

drop_e2e_db() {
  local db="$1"
  echo "== drop (if present): $db"
  e2e_sql <<SQL
SET NOCOUNT ON;
SET XACT_ABORT ON;
$(e2e_fence_sql)
IF N'$db' NOT LIKE N'%[_]E2E'
    THROW 54811, N'Target database is not an _E2E database: $db', 1;
IF DB_ID(N'$db') IS NOT NULL
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM [$db].sys.extended_properties
        WHERE class = 0 AND name = N'$MARKER_NAME'
          AND CONVERT(nvarchar(4000), value) LIKE N'GBA[_]E2E[_]STAND|%')
        THROW 54814, N'Existing database $db lacks the E2E marker; refusing to drop.', 1;
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
    ALTER DATABASE [$db] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE [$db];
END
SQL
}

move_clauses() {
  local src="$1" target="$2"
  e2e_sql_tsv <<SQL | awk -F'|' -v dir="$DATA_DIR" -v target="$target" '
    NF >= 3 {
      name=$1; type=$2; fid=$3;
      gsub(/^[ \t]+|[ \t]+$/, "", name); gsub(/^[ \t]+|[ \t]+$/, "", type); gsub(/^[ \t]+|[ \t]+$/, "", fid);
      ext = (type == "1") ? "ldf" : ((fid == "1") ? "mdf" : "ndf");
      printf "MOVE N%c%s%c TO N%c%s/%s_%s.%s%c,\n", 39, name, 39, 39, dir, target, fid, ext, 39;
    }'
SET NOCOUNT ON;
SELECT name, type, file_id FROM sys.master_files WHERE database_id = DB_ID(N'$src') ORDER BY file_id;
SQL
}

restore_from_bak() {
  local src="$1" target="$2" bak="$3"
  local moves
  moves="$(move_clauses "$src" "$target")"
  if [[ -z "$moves" ]]; then
    echo "No files enumerated for source database $src" >&2
    exit 1
  fi
  echo "== restore: $target <= $bak"
  e2e_sql <<SQL
SET NOCOUNT ON;
SET XACT_ABORT ON;
$(e2e_fence_sql)
IF DB_ID(N'$target') IS NOT NULL
    THROW 54815, N'Target database already exists: $target', 1;
RESTORE DATABASE [$target] FROM DISK = N'$bak'
WITH $moves RECOVERY, STATS = 10;
ALTER DATABASE [$target] SET RECOVERY SIMPLE;
DECLARE @log sysname;
SELECT TOP 1 @log = name FROM [$target].sys.database_files WHERE type = 1;
DECLARE @shrink nvarchar(400) = N'USE ' + QUOTENAME(N'$target') + N'; DBCC SHRINKFILE(' + QUOTENAME(@log) + N', 2048);';
EXEC (@shrink);
SQL
}

backup_copy_only() {
  local src="$1"
  local bak="$GOLDEN_BAK_DIR/${src}_golden_$EXPORT_STAMP.bak"
  echo "== copy-only backup: $src => $bak"
  e2e_sql <<SQL
SET NOCOUNT ON;
$(e2e_fence_sql)
BACKUP DATABASE [$src] TO DISK = N'$bak' WITH COPY_ONLY, INIT, COMPRESSION, STATS = 25;
SQL
  echo "$bak"
}

stamp_marker() {
  local db="$1" src="$2"
  local value="GBA_E2E_STAND|Source=$src|Export=$EXPORT_STAMP|Server=$SERVER_IDENTITY|ConsoleSha=$console_sha|ConcordSha=$concord_sha|Created=$created|Version=1"
  e2e_sql <<SQL
SET NOCOUNT ON;
$(e2e_fence_sql)
EXEC [$db].sys.sp_addextendedproperty @name = N'$MARKER_NAME', @value = N'$value';
SQL
}

for db in "${E2E_DATABASES[@]}"; do
  drop_e2e_db "$db"
done

restore_from_bak "ConcordDb_V5" "ConcordDb_V5_E2E" "$V5_BAK"
restore_from_bak "ConcordIdentityDb" "ConcordIdentityDb_E2E" "$IDENTITY_BAK"

data_bak="$(backup_copy_only "ConcordDb_Data" | tail -n 1)"
restore_from_bak "ConcordDb_Data" "ConcordDb_Data_E2E" "$data_bak"
vr_bak="$(backup_copy_only "GbaVehicleRegistry" | tail -n 1)"
restore_from_bak "GbaVehicleRegistry" "GbaVehicleRegistry_E2E" "$vr_bak"

for i in "${!E2E_DATABASES[@]}"; do
  stamp_marker "${E2E_DATABASES[$i]}" "${E2E_SOURCES[$i]}"
done

if [[ "$SKIP_MIGRATOR" -eq 0 ]]; then
  echo "== migrator (expected no-op against the freshly restored schema)"
  (
    cd "$SERVER_ROOT"
    IMAGE=gba-db-migrator:dev \
    SECRETS_DIR="$INFRA_ROOT/secrets/e2e" \
    DOCKER_NETWORK=gba-dev_default \
    ./scripts/run-concord-migrations-docker.sh
  )
fi

echo "== verify"
e2e_sql <<SQL
SET NOCOUNT ON;
$(e2e_fence_sql)
$(for db in "${E2E_DATABASES[@]}"; do e2e_db_fence_sql "$db"; done)
IF (SELECT COUNT(*) FROM sys.databases WHERE name LIKE N'%[_]E2E' AND state_desc = N'ONLINE') <> 4
    THROW 54816, N'Expected exactly 4 ONLINE _E2E databases.', 1;
IF (SELECT COUNT(*) FROM [ConcordDb_V5_E2E].dbo.__EFMigrationsHistory) <> (SELECT COUNT(*) FROM [ConcordDb_V5].dbo.__EFMigrationsHistory)
    THROW 54817, N'__EFMigrationsHistory of ConcordDb_V5_E2E differs from ConcordDb_V5.', 1;
IF (SELECT COUNT(*) FROM [ConcordDb_V5_E2E].sys.tables) < 250
    THROW 54818, N'ConcordDb_V5_E2E looks empty (fewer than 250 tables).', 1;
IF (SELECT COUNT(*) FROM [ConcordIdentityDb_E2E].dbo.AspNetUsers) < 1
    THROW 54819, N'ConcordIdentityDb_E2E has no users.', 1;
SELECT name, state_desc, recovery_model_desc FROM sys.databases WHERE name LIKE N'%[_]E2E' ORDER BY name;
SQL

echo "Golden E2E databases are ready."
