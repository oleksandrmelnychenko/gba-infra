#!/usr/bin/env bash

SQL_CONTAINER="${SQL_CONTAINER:-gba-dev-gba-mssql-1}"
SERVER_IDENTITY="${SERVER_IDENTITY:-01934d77f334}"
MARKER_NAME="GbaE2EStandDb"
E2E_DATABASES=(ConcordDb_V5_E2E ConcordIdentityDb_E2E ConcordDb_Data_E2E GbaVehicleRegistry_E2E)
E2E_SOURCES=(ConcordDb_V5 ConcordIdentityDb ConcordDb_Data GbaVehicleRegistry)
E2E_COMPOSE_PROJECT="gba-e2e"
E2E_BACKEND_SERVICES=(data-concord-e2e data-analytics-e2e)
E2E_STAND_LOCK_FILE="${E2E_STAND_LOCK_FILE:-/var/lock/gba-e2e-stand.lock}"

e2e_acquire_stand_lock() {
  if [[ -n "${E2E_STAND_LOCK_FD:-}" && -e "/proc/$$/fd/$E2E_STAND_LOCK_FD" ]]; then
    return
  fi

  exec {E2E_STAND_LOCK_FD}>>"$E2E_STAND_LOCK_FILE"
  export E2E_STAND_LOCK_FD
  if ! flock -n "$E2E_STAND_LOCK_FD"; then
    echo "Another E2E run, reset, or golden refresh owns $E2E_STAND_LOCK_FILE." >&2
    exit 1
  fi
}

e2e_sql() {
  docker exec -i "$SQL_CONTAINER" sh -lc 'SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" exec /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C -b -W -w 65535 -d master -i /dev/stdin'
}

e2e_sql_tsv() {
  docker exec -i "$SQL_CONTAINER" sh -lc 'SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" exec /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C -b -W -w 65535 -d master -h -1 -s "|" -i /dev/stdin'
}

e2e_fence_sql() {
  cat <<SQL
IF CONVERT(nvarchar(128), @@SERVERNAME) <> N'$SERVER_IDENTITY'
    THROW 54810, N'Refusing to run E2E stand SQL outside the approved DEV SQL instance.', 1;
SQL
}

e2e_db_fence_sql() {
  local db="$1"
  cat <<SQL
IF N'$db' NOT LIKE N'%[_]E2E'
    THROW 54811, N'Target database is not an _E2E database: $db', 1;
IF DB_ID(N'$db') IS NULL
    THROW 54812, N'E2E database is missing: $db', 1;
IF NOT EXISTS (
    SELECT 1 FROM [$db].sys.extended_properties
    WHERE class = 0 AND name = N'$MARKER_NAME'
      AND CONVERT(nvarchar(4000), value) LIKE N'GBA[_]E2E[_]STAND|%')
    THROW 54813, N'E2E marker $MARKER_NAME is absent on database: $db', 1;
SQL
}

e2e_require_sql_container() {
  if ! docker ps --format '{{.Names}}' | grep -qx "$SQL_CONTAINER"; then
    echo "SQL container is not running: $SQL_CONTAINER" >&2
    exit 1
  fi
}

e2e_backends_running() {
  docker ps --format '{{.Names}}' | grep -qxE 'data-concord-e2e|data-analytics-e2e'
}
