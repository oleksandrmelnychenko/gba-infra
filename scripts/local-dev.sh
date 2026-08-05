#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly INFRA_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly PROJECTS_ROOT="$(cd -- "$INFRA_ROOT/.." && pwd)"
readonly ENV_FILE="$INFRA_ROOT/.env.dev"
readonly ENV_TEMPLATE="$INFRA_ROOT/.env.dev.example"
readonly SECRET_DIR="$INFRA_ROOT/secrets/dev"
readonly SECRET_TEMPLATE_DIR="$INFRA_ROOT/secrets/dev.example"
readonly COMPOSE_PROJECT="${GBA_LOCAL_COMPOSE_PROJECT:-gba-local}"

readonly -a REQUIRED_REPOSITORIES=(
    gba-server
    gba-ecommerce-api
    gba_client
    gba_console
    gba_ecommerce
)

readonly -a REQUIRED_SECRETS=(
    Anthropic__ApiKey
    ConnectionStrings__AmgOneCConnectionString
    ConnectionStrings__FenixOneCConnectionString
    ConnectionStrings__LocalConnectionString
    ConnectionStrings__LocalConnectionStringDataAnalitic
    ConnectionStrings__LocalIdentityConnectionString
    ConnectionStrings__RemoteConnectionString
    ConnectionStrings__RemoteIdentityConnectionString
    ConnectionStrings__VehicleRegistryConnectionString
    ConnectionStrings__VehicleRegistryMigrationConnectionString
    EcommerceInternalAuth__ApiKey
    Elasticsearch__AppPassword
    Elasticsearch__Password
    Security__JwtKey
    Security__PriceEncryptionIV
    Security__PriceEncryptionKey
    Storefront__PriceTokenSecret
)

compose() {
    docker compose \
        -p "$COMPOSE_PROJECT" \
        -f "$INFRA_ROOT/docker-compose.yml" \
        -f "$INFRA_ROOT/docker-compose.dev.yml" \
        --env-file "$ENV_FILE" \
        "$@"
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

init() {
    if [[ ! -e "$ENV_FILE" ]]; then
        cp -- "$ENV_TEMPLATE" "$ENV_FILE"
        printf 'Created %s from the safe template.\n' "$ENV_FILE"
    fi

    if [[ ! -d "$SECRET_DIR" ]]; then
        cp -R -- "$SECRET_TEMPLATE_DIR" "$SECRET_DIR"
        rm -f -- "$SECRET_DIR/README.md"
        printf 'Created %s from safe templates.\n' "$SECRET_DIR"
    fi

    chmod 600 -- "$ENV_FILE"
    find "$SECRET_DIR" -type f -exec chmod 600 {} +
    printf 'Replace every CHANGE_ME value, then run: %s check\n' "$0"
}

check() {
    command -v docker >/dev/null 2>&1 || die 'docker is required.'
    docker compose version >/dev/null 2>&1 || die 'docker compose v2 is required.'

    [[ -f "$ENV_FILE" ]] || die "Missing $ENV_FILE. Run '$0 init'."
    [[ -d "$SECRET_DIR" ]] || die "Missing $SECRET_DIR. Run '$0 init'."

    local repository
    for repository in "${REQUIRED_REPOSITORIES[@]}"; do
        [[ -d "$PROJECTS_ROOT/$repository/.git" ]] ||
            die "Missing sibling repository $PROJECTS_ROOT/$repository."
    done

    local secret
    for secret in "${REQUIRED_SECRETS[@]}"; do
        [[ -s "$SECRET_DIR/$secret" ]] || die "Missing or empty secret: $secret."
    done

    if grep -R -n -- 'CHANGE_ME' "$ENV_FILE" "$SECRET_DIR" >/dev/null; then
        die 'Placeholder values remain in .env.dev or secrets/dev.'
    fi

    if find "$SECRET_DIR" -type f -perm /027 -print -quit | grep -q .; then
        die 'Secret files may not be group-writable/executable or world-accessible.'
    fi

    compose config --quiet
    printf 'Local DEV configuration is complete and Compose validation passed.\n'
}

usage() {
    cat <<'EOF'
Usage: scripts/local-dev.sh <command>

Commands:
  init   Create ignored .env.dev and secrets/dev files from safe templates.
  check  Validate repositories, secrets, permissions, and Compose configuration.
  up     Validate, build, and start the complete Docker Compose stack.
  ps     Show the local stack status.
  logs   Follow stack logs.
  down   Stop the stack without deleting database/search volumes.
EOF
}

case "${1:-}" in
    init)
        init
        ;;
    check)
        check
        ;;
    up)
        check
        compose up -d --build
        compose ps
        ;;
    ps)
        compose ps
        ;;
    logs)
        compose logs -f
        ;;
    down)
        compose down
        ;;
    *)
        usage
        exit 2
        ;;
esac
