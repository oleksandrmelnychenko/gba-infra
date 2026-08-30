#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ROOT="${INFRA_ROOT:-/root/projects/gba-infra}"
CONSOLE_ROOT="${CONSOLE_ROOT:-/root/projects/gba_console}"
source "$SCRIPT_DIR/lib-e2e-sql.sh"
e2e_acquire_stand_lock

compose=(docker compose -p "$E2E_COMPOSE_PROJECT" -f "$INFRA_ROOT/docker-compose.e2e.yml" --env-file "$INFRA_ROOT/.env.e2e")

e2e_require_sql_container
console_sha="$(docker image inspect -f '{{ index .Config.Labels "gba.git.sha" }}' gba-console:e2e-stand 2>/dev/null || true)"
concord_sha="$(docker image inspect -f '{{ index .Config.Labels "gba.git.sha" }}' gba-data-concord:e2e-stand 2>/dev/null || true)"
console_checkout_sha="$(git -C "$CONSOLE_ROOT" rev-parse HEAD 2>/dev/null || true)"

for value in "$console_sha" "$concord_sha"; do
  if [[ ! "$value" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Stand images must carry exact 40-character gba.git.sha labels." >&2
    exit 1
  fi
done
if [[ "$console_checkout_sha" != "$console_sha" ]]; then
  echo "Console checkout/image mismatch: checkout=$console_checkout_sha image=$console_sha" >&2
  exit 1
fi
if [[ -n "$(git -C "$CONSOLE_ROOT" status --porcelain)" ]]; then
  echo "CONSOLE_ROOT must be clean before refreshing the golden marker: $CONSOLE_ROOT" >&2
  exit 1
fi

e2e_sql <<SQL
SET NOCOUNT ON;
$(e2e_fence_sql)
$(for db in "${E2E_DATABASES[@]}"; do e2e_db_fence_sql "$db"; done)
$(for db in "${E2E_DATABASES[@]}"; do cat <<INNER
IF (SELECT COUNT(*) FROM sys.databases WHERE source_database_id = DB_ID(N'$db')) <> 1
    THROW 54824, N'Database $db must have exactly one snapshot before restamping.', 1;
INNER
done)
SQL

echo "== reverting to the current clean golden"
"$SCRIPT_DIR/e2e-reset.sh" revert

echo "== stopping e2e backends"
"${compose[@]}" stop data-concord-e2e data-analytics-e2e >/dev/null
restart_backends=1
cleanup() {
  if [[ "$restart_backends" -eq 1 ]]; then
    "${compose[@]}" up -d >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "== restamping exact image revisions"
e2e_sql <<SQL
SET NOCOUNT ON;
SET XACT_ABORT ON;
$(e2e_fence_sql)
BEGIN TRANSACTION;
$(for i in "${!E2E_DATABASES[@]}"; do
    db="${E2E_DATABASES[$i]}"
    src="${E2E_SOURCES[$i]}"
    cat <<INNER
$(e2e_db_fence_sql "$db")
DECLARE @old$i nvarchar(4000) = (
    SELECT CONVERT(nvarchar(4000), value)
    FROM [$db].sys.extended_properties
    WHERE class = 0 AND name = N'$MARKER_NAME'
);
DECLARE @consoleAt$i int = CHARINDEX(N'|ConsoleSha=', @old$i);
DECLARE @concordAt$i int = CHARINDEX(N'|ConcordSha=', @old$i);
DECLARE @createdAt$i int = CHARINDEX(N'|Created=', @old$i);
IF @old$i NOT LIKE N'GBA[_]E2E[_]STAND|Source=$src|%'
   OR @consoleAt$i = 0 OR @concordAt$i <= @consoleAt$i OR @createdAt$i <= @concordAt$i
    THROW 54825, N'Unexpected marker shape for $db; refusing to restamp.', 1;
DECLARE @new$i nvarchar(4000) =
    LEFT(@old$i, @consoleAt$i + LEN(N'|ConsoleSha=') - 1)
    + N'$console_sha|ConcordSha=$concord_sha'
    + SUBSTRING(@old$i, @createdAt$i, 4000);
EXEC [$db].sys.sp_updateextendedproperty
    @name = N'$MARKER_NAME',
    @value = @new$i;
INNER
done)
COMMIT TRANSACTION;
SQL

echo "== replacing snapshots with the restamped golden"
"$SCRIPT_DIR/e2e-reset.sh" drop-snapshots
"$SCRIPT_DIR/e2e-reset.sh" snapshot

echo "== starting e2e stand"
"${compose[@]}" up -d --wait
restart_backends=0
trap - EXIT

echo "== verifying marker and snapshots"
e2e_sql <<SQL
SET NOCOUNT ON;
$(e2e_fence_sql)
$(for db in "${E2E_DATABASES[@]}"; do cat <<INNER
$(e2e_db_fence_sql "$db")
IF NOT EXISTS (
    SELECT 1 FROM [$db].sys.extended_properties
    WHERE class = 0 AND name = N'$MARKER_NAME'
      AND CONVERT(nvarchar(4000), value) LIKE N'%|ConsoleSha=$console_sha|ConcordSha=$concord_sha|%')
    THROW 54826, N'Exact image revisions are absent from the marker for $db.', 1;
IF (SELECT COUNT(*) FROM sys.databases WHERE source_database_id = DB_ID(N'$db')) <> 1
    THROW 54827, N'Database $db does not have exactly one refreshed snapshot.', 1;
INNER
done)
SQL

echo "Golden image marker now matches console=$console_sha concord=$concord_sha"
