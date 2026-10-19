#!/usr/bin/env bash

SQL_CONTAINER="${SQL_CONTAINER:-gba-dev-gba-mssql-1}"
SERVER_IDENTITY="${SERVER_IDENTITY:-01934d77f334}"
QA_MARKER_NAME="GbaQaStandDb"
QA_DATABASES=(ConcordDb_V5_QA ConcordIdentityDb_QA ConcordDb_Data_QA GbaVehicleRegistry_QA)
QA_SOURCES=(ConcordDb_V5 ConcordIdentityDb ConcordDb_Data GbaVehicleRegistry)
QA_COMPOSE_PROJECT="gba-qa"
QA_BACKEND_CONTAINERS=(data-concord-qa data-analytics-qa)
QA_STATE_DIR="${QA_STATE_DIR:-/private/gba-qa}"
QA_DEV_COMPOSE="${QA_DEV_COMPOSE:-/private/claude-deploy-reopen-20261014/candidate-compose.private.json}"
QA_DATA_DIR="/var/opt/mssql/data/qa"
QA_BACKUP_DIR="/var/opt/mssql/backup/qa-clone"
QA_MAX_SOURCE_LOG_GB="${QA_MAX_SOURCE_LOG_GB:-40}"
QA_LOCK_FILE="${QA_LOCK_FILE:-/var/lock/gba-qa-stand.lock}"

qa_acquire_lock() {
  if [[ -n "${QA_LOCK_FD:-}" && -e "/proc/$$/fd/$QA_LOCK_FD" ]]; then
    return
  fi
  exec {QA_LOCK_FD}>>"$QA_LOCK_FILE"
  export QA_LOCK_FD
  if ! flock -n "$QA_LOCK_FD"; then
    echo "Another QA clone or start owns $QA_LOCK_FILE." >&2
    exit 1
  fi
}

qa_sql() {
  docker exec -i "$SQL_CONTAINER" sh -lc 'SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" exec /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C -b -I -W -w 65535 -d master -i /dev/stdin'
}

qa_sql_tsv() {
  docker exec -i "$SQL_CONTAINER" sh -lc 'SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" exec /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C -b -I -W -w 65535 -d master -h -1 -s "|" -i /dev/stdin'
}

qa_fence_sql() {
  cat <<SQL
IF CONVERT(nvarchar(128), @@SERVERNAME) <> N'$SERVER_IDENTITY'
    THROW 54910, N'Refusing to run QA stand SQL outside the approved DEV SQL instance.', 1;
SQL
}

qa_db_fence_sql() {
  local db="$1"
  cat <<SQL
IF N'$db' NOT LIKE N'%[_]QA'
    THROW 54911, N'Target database is not a _QA database: $db', 1;
IF DB_ID(N'$db') IS NULL
    THROW 54912, N'QA database is missing: $db', 1;
IF NOT EXISTS (
    SELECT 1 FROM [$db].sys.extended_properties
    WHERE class = 0 AND name = N'$QA_MARKER_NAME'
      AND CONVERT(nvarchar(4000), value) LIKE N'GBA[_]QA[_]STAND|%')
    THROW 54913, N'QA marker $QA_MARKER_NAME is absent on database: $db', 1;
SQL
}

qa_require_sql_container() {
  if ! docker ps --format '{{.Names}}' | grep -qx "$SQL_CONTAINER"; then
    echo "SQL container is not running: $SQL_CONTAINER" >&2
    exit 1
  fi
}

qa_backends_running() {
  docker ps --format '{{.Names}}' | grep -qxE 'data-concord-qa|data-analytics-qa'
}

qa_stop_backends() {
  local running
  running="$(docker ps --format '{{.Names}}' | grep -xE 'data-concord-qa|data-analytics-qa' || true)"
  if [[ -n "$running" ]]; then
    echo "== stop QA backends: $(echo "$running" | tr '\n' ' ')"
    docker stop $running >/dev/null
  fi
}
