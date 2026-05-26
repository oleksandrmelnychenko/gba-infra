#!/usr/bin/env sh
set -eu

ELASTICSEARCH_URL="${ELASTICSEARCH_URL:-http://elasticsearch:9200}"
ELASTIC_PASSWORD_FILE="${ELASTIC_PASSWORD_FILE:-/run/secrets/elastic_password}"
ELASTIC_APP_PASSWORD_FILE="${ELASTIC_APP_PASSWORD_FILE:-/run/secrets/app_password}"
ELASTIC_APP_USERNAME="${ELASTIC_APP_USERNAME:-gba_products_app}"
ELASTIC_APP_ROLE="${ELASTIC_APP_ROLE:-gba_products_app}"

ELASTIC_PASSWORD="$(cat "$ELASTIC_PASSWORD_FILE")"
ELASTIC_APP_PASSWORD="$(cat "$ELASTIC_APP_PASSWORD_FILE")"

echo "Waiting for Elasticsearch security API..."
until curl -fsS -u "elastic:${ELASTIC_PASSWORD}" "${ELASTICSEARCH_URL}/_cluster/health" >/dev/null; do
  sleep 2
done

curl -fsS -u "elastic:${ELASTIC_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X PUT "${ELASTICSEARCH_URL}/_cluster/settings" \
  -d '{"persistent":{"action.destructive_requires_name":true}}' >/dev/null

curl -fsS -u "elastic:${ELASTIC_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X PUT "${ELASTICSEARCH_URL}/_security/role/${ELASTIC_APP_ROLE}" \
  -d '{
    "cluster": ["monitor"],
    "indices": [
      {
        "names": ["products", "products-*"],
        "privileges": [
          "read",
          "view_index_metadata",
          "create_index",
          "write",
          "delete",
          "manage",
          "maintenance"
        ]
      }
    ]
  }' >/dev/null

USER_PAYLOAD="$(printf '{"password":"%s","roles":["%s"],"full_name":"GBA products search application"}' "$ELASTIC_APP_PASSWORD" "$ELASTIC_APP_ROLE")"

curl -fsS -u "elastic:${ELASTIC_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X PUT "${ELASTICSEARCH_URL}/_security/user/${ELASTIC_APP_USERNAME}" \
  -d "$USER_PAYLOAD" >/dev/null

echo "Elasticsearch role and application user are configured."
