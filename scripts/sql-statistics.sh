#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONTAINER=${GBA_MSSQL_CONTAINER:?set GBA_MSSQL_CONTAINER}
DATABASES=${GBA_MSSQL_DATABASES:-ConcordDb_V5 ConcordDb_Data}
SQLCMD=/opt/mssql-tools18/bin/sqlcmd

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -qx true; then
    echo "sql-statistics: container $CONTAINER is not running" >&2
    exit 1
fi

PASSWORD=$(docker exec "$CONTAINER" printenv MSSQL_SA_PASSWORD)
status=0
for db in $DATABASES; do
    start=$(date +%s)
    if docker exec -i -e SQLCMDPASSWORD="$PASSWORD" "$CONTAINER" "$SQLCMD" \
        -S localhost -U sa -C -I -d "$db" -b -r1 \
        -v MaxMinutes="${GBA_STATS_MAX_MINUTES:-90}" \
           FullScanRowLimit="${GBA_STATS_FULLSCAN_ROW_LIMIT:-20000000}" \
           MinModifiedRows="${GBA_STATS_MIN_MODIFIED_ROWS:-500}" \
           ModifiedPercent="${GBA_STATS_MODIFIED_PERCENT:-5}" \
           StatsMaxDop="${GBA_STATS_MAXDOP:-8}" \
           ScanMode="${GBA_STATS_SCAN_MODE:-FULLSCAN}" \
           PauseMs="${GBA_STATS_PAUSE_MS:-0}" \
           SyncLockDatabase="${GBA_STATS_SYNC_LOCK_DATABASE:-ConcordDb_V5}" \
        -i /dev/stdin < "$ROOT/sql/maintenance/update-statistics.sql"; then
        echo "sql-statistics: $CONTAINER/$db ok in $(( $(date +%s) - start ))s"
    else
        echo "sql-statistics: $CONTAINER/$db failed" >&2
        status=1
    fi
done
exit $status
