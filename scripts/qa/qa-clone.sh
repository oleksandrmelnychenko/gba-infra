#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib-qa.sh"
qa_acquire_lock
qa_require_sql_container

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
created="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

source_log_gb="$(qa_sql_tsv <<SQL | tr -d '[:space:]'
SET NOCOUNT ON;
$(qa_fence_sql)
SELECT CAST(SUM(size) * 8 / 1024 / 1024 AS int) FROM sys.master_files WHERE database_id = DB_ID(N'ConcordDb_V5') AND type = 1;
SQL
)"
if (( source_log_gb > QA_MAX_SOURCE_LOG_GB )); then
  echo "ConcordDb_V5 log is ${source_log_gb} GB; a restore would zero a log that large. Shrink it first (gba-docs/scripts/shrink-dev-v5-log.sh)." >&2
  exit 1
fi

qa_stop_backends
docker exec -u root "$SQL_CONTAINER" sh -c "mkdir -p '$QA_DATA_DIR' '$QA_BACKUP_DIR' && chown mssql:mssql '$QA_DATA_DIR' '$QA_BACKUP_DIR'"

backup_source() {
  local src="$1" bak="$2"
  echo "== copy-only backup: $src"
  qa_sql <<SQL
SET NOCOUNT ON;
$(qa_fence_sql)
BACKUP DATABASE [$src] TO DISK = N'$bak' WITH COPY_ONLY, INIT, COMPRESSION, CHECKSUM, STATS = 25;
SQL
}

drop_target() {
  local db="$1"
  echo "== drop (if present): $db"
  qa_sql <<SQL
SET NOCOUNT ON;
SET XACT_ABORT ON;
$(qa_fence_sql)
IF N'$db' NOT LIKE N'%[_]QA'
    THROW 54911, N'Target database is not a _QA database: $db', 1;
IF DB_ID(N'$db') IS NOT NULL
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM [$db].sys.extended_properties
        WHERE class = 0 AND name = N'$QA_MARKER_NAME'
          AND CONVERT(nvarchar(4000), value) LIKE N'GBA[_]QA[_]STAND|%')
        THROW 54914, N'Existing database $db lacks the QA marker; refusing to drop.', 1;
    ALTER DATABASE [$db] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE [$db];
END
SQL
}

move_clauses() {
  local src="$1" target="$2"
  qa_sql_tsv <<SQL | awk -F'|' -v dir="$QA_DATA_DIR" -v target="$target" '
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

restore_target() {
  local src="$1" target="$2" bak="$3"
  local moves
  moves="$(move_clauses "$src" "$target")"
  if [[ -z "$moves" ]]; then
    echo "No files enumerated for source database $src" >&2
    exit 1
  fi
  echo "== restore: $target <= $src"
  qa_sql <<SQL
SET NOCOUNT ON;
SET XACT_ABORT ON;
$(qa_fence_sql)
IF DB_ID(N'$target') IS NOT NULL
    THROW 54915, N'Target database already exists: $target', 1;
RESTORE DATABASE [$target] FROM DISK = N'$bak'
WITH $moves RECOVERY, CHECKSUM, STATS = 25;
ALTER DATABASE [$target] SET RECOVERY SIMPLE;
DECLARE @log sysname;
SELECT TOP 1 @log = name FROM [$target].sys.database_files WHERE type = 1;
DECLARE @shrink nvarchar(400) = N'USE ' + QUOTENAME(N'$target') + N'; CHECKPOINT; DBCC SHRINKFILE(' + QUOTENAME(@log) + N', 1024) WITH NO_INFOMSGS;';
EXEC (@shrink);
EXEC [$target].sys.sp_addextendedproperty
    @name = N'$QA_MARKER_NAME',
    @value = N'GBA_QA_STAND|Source=$src|Clone=$stamp|Server=$SERVER_IDENTITY|Created=$created|Version=1';
SQL
}

for i in "${!QA_DATABASES[@]}"; do
  src="${QA_SOURCES[$i]}"
  target="${QA_DATABASES[$i]}"
  bak="$QA_BACKUP_DIR/${src}_qa_${stamp}.bak"
  backup_source "$src" "$bak"
  drop_target "$target"
  restore_target "$src" "$target" "$bak"
  docker exec -u root "$SQL_CONTAINER" rm -f "$bak"
done

echo "== verify"
qa_sql <<SQL
SET NOCOUNT ON;
$(qa_fence_sql)
$(for db in "${QA_DATABASES[@]}"; do qa_db_fence_sql "$db"; done)
IF (SELECT COUNT(*) FROM sys.databases WHERE name LIKE N'%[_]QA' AND state_desc = N'ONLINE') <> ${#QA_DATABASES[@]}
    THROW 54916, N'Expected every _QA database to be ONLINE.', 1;
IF (SELECT COUNT(*) FROM [ConcordDb_V5_QA].dbo.__EFMigrationsHistory) <> (SELECT COUNT(*) FROM [ConcordDb_V5].dbo.__EFMigrationsHistory)
    THROW 54917, N'__EFMigrationsHistory of ConcordDb_V5_QA differs from ConcordDb_V5.', 1;
IF (SELECT COUNT(*) FROM [ConcordIdentityDb_QA].dbo.AspNetUsers) < 1
    THROW 54918, N'ConcordIdentityDb_QA has no users.', 1;
SELECT d.name, d.state_desc, d.recovery_model_desc, d.is_read_committed_snapshot_on,
       CAST(SUM(f.size) * 8.0 / 1024 / 1024 AS decimal(10, 2)) AS allocated_gb
FROM sys.databases d JOIN sys.master_files f ON f.database_id = d.database_id
WHERE d.name LIKE N'%[_]QA'
GROUP BY d.name, d.state_desc, d.recovery_model_desc, d.is_read_committed_snapshot_on
ORDER BY d.name;
SQL

echo "QA databases cloned from DEV at $created."
