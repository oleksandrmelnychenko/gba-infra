#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ROOT="${INFRA_ROOT:-/root/projects/gba-infra}"
CONSOLE_ROOT="${CONSOLE_ROOT:-/root/deploy/gba-console-aug21}"
ENV_FILE="${ENV_FILE:-/etc/gba-e2e.env}"
source "$SCRIPT_DIR/lib-e2e-sql.sh"
e2e_acquire_stand_lock

MODE="${1:-smoke}"
case "$MODE" in
  smoke|full) PW_ARGS=(--project="$MODE") ;;
  --spec) PW_ARGS=(--project=full "${2:?usage: run-e2e.sh --spec <path>}") ;;
  *) echo "Usage: run-e2e.sh [smoke|full|--spec <path>]" >&2; exit 1 ;;
esac

compose=(docker compose -p "$E2E_COMPOSE_PROJECT" -f "$INFRA_ROOT/docker-compose.e2e.yml" --env-file "$INFRA_ROOT/.env.e2e")

echo "== preflight"
e2e_require_sql_container
for port in 8084 35991 35994; do
  if ss -ltn "sport = :$port" | tail -n +2 | grep -q . ; then
    owner="$(docker ps --format '{{.Names}} {{.Ports}}' | grep ":$port->" | awk '{print $1}' || true)"
    case "$owner" in
      gba-console-e2e|data-concord-e2e|data-analytics-e2e|"") ;;
      *) echo "Port $port is held by unexpected owner: $owner" >&2; exit 1 ;;
    esac
  fi
done

e2e_sql <<SQL
SET NOCOUNT ON;
$(e2e_fence_sql)
$(for db in "${E2E_DATABASES[@]}"; do e2e_db_fence_sql "$db"; done)
$(for db in "${E2E_DATABASES[@]}"; do cat <<INNER
IF (SELECT COUNT(*) FROM sys.databases WHERE source_database_id = DB_ID(N'$db')) <> 1
    THROW 54823, N'Database $db must have exactly one snapshot before a run; use e2e-reset.sh snapshot.', 1;
INNER
done)
SQL

marker="$(e2e_sql_tsv <<'SQL'
SET NOCOUNT ON;
SELECT CONVERT(nvarchar(4000), value) FROM [ConcordDb_V5_E2E].sys.extended_properties WHERE class = 0 AND name = N'GbaE2EStandDb';
SQL
)"
for image in gba-console:e2e-stand gba-data-concord:e2e-stand; do
  sha="$(docker image inspect -f '{{ index .Config.Labels "gba.git.sha" }}' "$image" 2>/dev/null || true)"
  if [[ -z "$sha" || "$sha" == "<no value>" ]]; then
    echo "Image $image is missing the required gba.git.sha label; rebuild it from a clean exact revision." >&2
    exit 1
  fi
  if [[ "$marker" != *"$sha"* ]]; then
    echo "WARN: $image sha $sha differs from the golden marker (stand may lag the images); consider refreshing golden." >&2
  fi
done

console_checkout_sha="$(git -C "$CONSOLE_ROOT" rev-parse HEAD 2>/dev/null || true)"
console_image_sha="$(docker image inspect -f '{{ index .Config.Labels "gba.git.sha" }}' gba-console:e2e-stand 2>/dev/null || true)"
if [[ -z "$console_checkout_sha" ]]; then
  echo "CONSOLE_ROOT is not a git checkout: $CONSOLE_ROOT" >&2
  exit 1
fi
if [[ -n "$(git -C "$CONSOLE_ROOT" status --porcelain)" ]]; then
  echo "CONSOLE_ROOT must be clean before an E2E run: $CONSOLE_ROOT" >&2
  exit 1
fi
if [[ "$console_checkout_sha" != "$console_image_sha" ]]; then
  echo "Console checkout/image mismatch: checkout=$console_checkout_sha image=$console_image_sha" >&2
  echo "Rebuild gba-console:e2e-stand from CONSOLE_ROOT before running Playwright." >&2
  exit 1
fi

echo "== stand up"
"${compose[@]}" up -d --wait

echo "== reset"
"$SCRIPT_DIR/e2e-reset.sh" revert

echo "== playwright ($MODE)"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi
if [[ -z "${E2E_SQL_PASSWORD:-}" ]]; then
  E2E_SQL_PASSWORD="$(grep -E '^SQL_SA_PASSWORD=' "$INFRA_ROOT/.env.dev" | cut -d= -f2-)"
  export E2E_SQL_PASSWORD
fi
export E2E_BASE_URL="${E2E_BASE_URL:-http://localhost:8084}"

status=0
(
  cd "$CONSOLE_ROOT"
  npx playwright test "${PW_ARGS[@]}"
) || status=$?

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$CONSOLE_ROOT/output/e2e-reports/$stamp-$MODE"
mkdir -p "$archive"
[[ -d "$CONSOLE_ROOT/playwright-report" ]] && cp -r "$CONSOLE_ROOT/playwright-report" "$archive/" || true
[[ -d "$CONSOLE_ROOT/test-results" ]] && cp -r "$CONSOLE_ROOT/test-results" "$archive/" || true
echo "report: $archive"

exit "$status"
