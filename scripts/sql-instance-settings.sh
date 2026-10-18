#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONTAINER=${GBA_MSSQL_CONTAINER:?set GBA_MSSQL_CONTAINER}
SQLCMD=/opt/mssql-tools18/bin/sqlcmd
PASSWORD=$(docker exec "$CONTAINER" printenv MSSQL_SA_PASSWORD)

docker exec -i -e SQLCMDPASSWORD="$PASSWORD" "$CONTAINER" "$SQLCMD" \
    -S localhost -U sa -C -I -d master -b -r1 -W \
    -v MaxDop="${GBA_SQL_MAXDOP:-8}" \
       CostThreshold="${GBA_SQL_COST_THRESHOLD:-50}" \
       MaxServerMemoryMB="${GBA_SQL_MAX_SERVER_MEMORY_MB:-32768}" \
       OptimizeForAdHoc="${GBA_SQL_OPTIMIZE_FOR_AD_HOC:-1}" \
       TempdbFileCount="${GBA_SQL_TEMPDB_FILES:-8}" \
       TempdbFileSizeMB="${GBA_SQL_TEMPDB_FILE_MB:-4096}" \
       TempdbFileGrowthMB="${GBA_SQL_TEMPDB_GROWTH_MB:-512}" \
       TempdbLogSizeMB="${GBA_SQL_TEMPDB_LOG_MB:-1024}" \
       QueryStoreMaxMB="${GBA_SQL_QUERY_STORE_MB:-2048}" \
       QueryStoreDatabases="${GBA_SQL_QUERY_STORE_DATABASES:-ConcordDb_V5,ConcordDb_Data}" \
    -i /dev/stdin < "$ROOT/sql/instance-settings.sql"
