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
    ForecastApi__ApiKey
    GbaNbaApi__ApiKey
    ProcureCostInternalAuth__ApiKey
    PricingApi__ApiKey
    ProcurementApi__ApiKey
    ProductsApi__ApiKey
    RecommendationApi__ApiKey
    Security__JwtKey
    Security__JwtPrivateKey
    Security__JwtPublicKey
    ShopAuth__JwtKey
    Security__PriceEncryptionIV
    Security__PriceEncryptionKey
    SolvencyApi__ApiKey
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

require_nonempty_regular_file() {
    local path="$1"
    local label="$2"

    [[ ! -L "$path" && -f "$path" ]] ||
        die "$label must be a regular file and not a symbolic link."
    [[ -s "$path" ]] || die "$label must not be empty."
}

validate_secret_directory_shape() {
    [[ ! -L "$SECRET_DIR" && -d "$SECRET_DIR" ]] ||
        die "$SECRET_DIR must be a real directory and not a symbolic link."

    local non_regular_entries
    if ! non_regular_entries="$(find "$SECRET_DIR" -mindepth 1 -maxdepth 1 ! -type f -print)"; then
        die 'Unable to inspect secret directory entries.'
    fi
    if [[ -n "$non_regular_entries" ]]; then
        die 'secrets/dev may contain only regular files; links, directories, and special files are not allowed.'
    fi
}

validate_required_secret_files() {
    local secret
    for secret in "${REQUIRED_SECRETS[@]}"; do
        require_nonempty_regular_file "$SECRET_DIR/$secret" "Secret $secret"
    done
}

install_missing_secret_templates() {
    local template
    local target
    for template in "$SECRET_TEMPLATE_DIR"/*; do
        [[ -f "$template" && ! -L "$template" ]] ||
            die "Secret template must be a regular file: $template."
        [[ "${template##*/}" != 'README.md' ]] || continue

        target="$SECRET_DIR/${template##*/}"
        if [[ ! -e "$target" && ! -L "$target" ]]; then
            cp -- "$template" "$target"
            chmod 600 -- "$target"
            printf 'Created missing secret template %s.\n' "$target"
        fi
    done
}

ensure_token_keys() {
    local private_key="$SECRET_DIR/Security__JwtPrivateKey"
    local public_key="$SECRET_DIR/Security__JwtPublicKey"

    command -v openssl >/dev/null 2>&1 || die 'openssl is required to create the RS256 token key pair.'

    if [[ -s "$private_key" ]] && ! grep -q -- 'CHANGE_ME' "$private_key"; then
        if [[ ! -s "$public_key" ]] || grep -q -- 'CHANGE_ME' "$public_key"; then
            openssl pkey -in "$private_key" -pubout -out "$public_key"
            chmod 600 -- "$public_key"
            printf 'Derived %s from the existing private key.\n' "$public_key"
        fi
        return
    fi

    local generated
    generated="$(mktemp "$SECRET_DIR/.jwt-private.XXXXXX")"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$generated" 2>/dev/null
    chmod 600 -- "$generated"
    mv -f -- "$generated" "$private_key"
    openssl pkey -in "$private_key" -pubout -out "$public_key"
    chmod 600 -- "$public_key"
    printf 'Generated a local RS256 token key pair in %s.\n' "$SECRET_DIR"
}

check_token_keys() {
    command -v openssl >/dev/null 2>&1 || die 'openssl is required to validate the RS256 token key pair.'

    local derived
    derived="$(openssl pkey -in "$SECRET_DIR/Security__JwtPrivateKey" -pubout 2>/dev/null)" ||
        die 'Security__JwtPrivateKey is not a readable unencrypted private key.'
    [[ "$derived" == "$(cat -- "$SECRET_DIR/Security__JwtPublicKey")" ]] ||
        die "Security__JwtPublicKey does not match Security__JwtPrivateKey. Delete both and run '$0 init'."
}

init() {
    if [[ -L "$ENV_FILE" || (-e "$ENV_FILE" && ! -f "$ENV_FILE") ]]; then
        die "$ENV_FILE must be a regular file and not a symbolic link."
    fi
    if [[ ! -e "$ENV_FILE" ]]; then
        cp -- "$ENV_TEMPLATE" "$ENV_FILE"
        printf 'Created %s from the safe template.\n' "$ENV_FILE"
    fi

    if [[ -L "$SECRET_DIR" || (-e "$SECRET_DIR" && ! -d "$SECRET_DIR") ]]; then
        die "$SECRET_DIR must be a real directory and not a symbolic link."
    fi
    if [[ ! -d "$SECRET_DIR" ]]; then
        cp -R -- "$SECRET_TEMPLATE_DIR" "$SECRET_DIR"
        rm -f -- "$SECRET_DIR/README.md"
        printf 'Created %s from safe templates.\n' "$SECRET_DIR"
    fi

    require_nonempty_regular_file "$ENV_FILE" "$ENV_FILE"
    validate_secret_directory_shape
    install_missing_secret_templates
    validate_required_secret_files
    chmod 600 -- "$ENV_FILE"
    find "$SECRET_DIR" -type f -exec chmod 600 {} +
    ensure_token_keys
    printf 'Replace every CHANGE_ME value, then run: %s check\n' "$0"
}

check() {
    command -v docker >/dev/null 2>&1 || die 'docker is required.'
    docker compose version >/dev/null 2>&1 || die 'docker compose v2 is required.'

    [[ -e "$ENV_FILE" || -L "$ENV_FILE" ]] || die "Missing $ENV_FILE. Run '$0 init'."
    [[ -e "$SECRET_DIR" || -L "$SECRET_DIR" ]] || die "Missing $SECRET_DIR. Run '$0 init'."
    require_nonempty_regular_file "$ENV_FILE" "$ENV_FILE"
    validate_secret_directory_shape

    local repository
    for repository in "${REQUIRED_REPOSITORIES[@]}"; do
        [[ -d "$PROJECTS_ROOT/$repository/.git" ]] ||
            die "Missing sibling repository $PROJECTS_ROOT/$repository."
    done

    validate_required_secret_files

    local placeholder_status
    if grep -R -n -- 'CHANGE_ME' "$ENV_FILE" "$SECRET_DIR" >/dev/null; then
        die 'Placeholder values remain in .env.dev or secrets/dev.'
    else
        placeholder_status=$?
        [[ "$placeholder_status" -eq 1 ]] ||
            die 'Unable to scan .env.dev and secrets/dev for placeholder values.'
    fi

    check_token_keys

    local insecure_secret_files
    if ! insecure_secret_files="$(find "$ENV_FILE" "$SECRET_DIR" -type f -perm /037 -print)"; then
        die 'Unable to inspect secret file permissions.'
    fi
    if [[ -n "$insecure_secret_files" ]]; then
        die 'Secret files may not be group-writable/executable or world-accessible.'
    fi

    "$INFRA_ROOT/scripts/verify-secure-exposure.sh" \
        --env-file "$ENV_FILE" \
        --validate-config-only
    compose config --quiet
    printf 'Local DEV configuration is complete and Compose validation passed.\n'
}

usage() {
    cat <<'EOF'
Usage: scripts/local-dev.sh <command>

Commands:
  init   Create ignored .env.dev and secrets/dev files from safe templates
         and generate the local RS256 token key pair.
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
