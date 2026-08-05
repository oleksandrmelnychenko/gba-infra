# gba-infra

Orchestration for the GBA stack — a single `docker-compose.yml` that runs
the frontends, APIs, SQL Server and search engine on one Docker network.

## Repository layout

The compose build contexts point at sibling repositories, so all repos
must be checked out next to each other:

```
projects/
├── gba-infra/            ← this repo
├── gba-server/           ← data.concord (:35981), data.analytics (:35982)
├── gba-ecommerce-api/    ← GBA.Ecommerce API (:62506)
├── gba_client/           ← CRM frontend (:8082)
└── gba_ecommerce/        ← e-commerce frontend (:8081)
```

## Services

| Service             | Port  | Description                         |
|---------------------|-------|-------------------------------------|
| `gba-client`        | 8082  | CRM frontend (React, nginx)         |
| `gba-ecommerce`     | 8081  | E-commerce frontend (React, nginx)  |
| `gba-data-concord`  | 35981 | Main API                            |
| `gba-data-analytics`| 35982 | Analytics / history API             |
| `gba-ecommerce-api` | 62506 | E-commerce API                      |
| `gba-mssql`         | 1433  | SQL Server 2022                     |
| `gba-typesense`     | 8108  | Search engine                       |
| `gba-forecast`      | 8006  | Host-run sales forecast service     |

## Usage

```bash
# build images and start the whole stack
docker compose up -d --build

# status / logs
docker compose ps
docker compose logs -f data-concord

# stop
docker compose down
```

For a reproducible teammate handoff, including safe secret templates,
configuration validation, local endpoints, database expectations, and the
post-sync reconciliation entry points, use
[`docs/local-development-handoff.md`](docs/local-development-handoff.md) and
`scripts/local-dev.sh`.

## Notes

- **Connection strings** are injected as `ConnectionStrings__*` environment
  variables so the application repos' committed `appsettings.json` stay
  untouched. All APIs point at the `gba-mssql` container as `sa`.
- Elasticsearch security is enabled. The `elastic` superuser password is used
  only by the one-shot `elasticsearch-setup` service; `gba-ecommerce-api`
  uses the restricted `gba_products_app` user from
  `Elasticsearch__AppPassword`.
- Keep Elasticsearch secret files readable only by the owner
  (`chmod 600 secrets/<env>/Elasticsearch__*`). Elasticsearch rejects
  world-readable password files.
- Elasticsearch destructive wildcard deletes are blocked with
  `action.destructive_requires_name=true`. Do not publish `9200` or `9300`
  outside localhost.
- The `gba-mssql-data` volume is declared **external** — create it (or run
  SQL Server once) before the first `up`, so database data survives
  `docker compose down`.
- Databases (`ConcordDb_V5`, `ConcordIdentityDb`, `ConcordDb_Data`) are
  created by the EF Core migrations in `gba-server` — run those against
  `gba-mssql` before the APIs can serve real data.
- Credentials here are for **local development only**.
- In production, `FORECAST_API_KEY` in `.env.prod` must match
  `INTERNAL_API_KEY` in `/root/projects/gba-forecast/.env`; otherwise
  `/sales/prediction/get` returns `503 ai_auth_misconfigured`.

## Security checks

Run this after compose changes or a server restart:

```bash
./scripts/verify-secure-exposure.sh
```

Expected public ports are only `80`, `443`, and `22`. Development ports
(`1433`, `35981`, `35982`, `62506`, `8081`, `8082`, `9200`) must bind to
`127.0.0.1` only.

To re-apply Docker's host firewall guardrail:

```bash
./scripts/harden-docker-firewall.sh
```

It adds idempotent `DOCKER-USER` rules that allow only `80/443` from the
public interface into Docker-published services.

## AI fleet release gate

`scripts/verify_ai_fleet.py` is the fail-closed, read-only promotion gate for all seven AI
services. It verifies `/health` and `/ready`, strict response identity, exact EUR cents,
quantity/count invariants, dense product/forecast series, a recent complete NBA generation,
an independently generated procurement reconciliation artifact, and the common source-history
floor.

| Service | Default URL | Readiness |
| --- | --- | --- |
| reco | `http://127.0.0.1:8000` | `/ready` |
| procure | `http://127.0.0.1:8001` | `/ready` |
| nba | `http://127.0.0.1:8002` | `/ready` |
| solvency | `http://127.0.0.1:8003` | `/ready` |
| pricing | `http://127.0.0.1:8004` | `/ready` |
| products | `http://127.0.0.1:8005` | `/ready` |
| forecast | `http://127.0.0.1:8006` | `/ready` |

The AI processes remain host-run services; this repository's DEV/PROD Compose overlays only
configure the .NET proxies that consume them. The history-floor gate does not merge environment
files or change that separation.

The fixed source-history contract is `2025-01-01`. Every health/readiness response must expose
`source_history_start` with that value and `source_history_contract_ready: true`; services with a
nested source-readiness object must repeat the same date there. Missing or mismatched values fail
the gate. `AI_FLEET_SOURCE_HISTORY_START_DATE` (or `--source-history-start`) is an assertion input
and is rejected if it differs from the fixed contract.

First generate the procurement proof from `gba-procure`:

```bash
.venv/bin/dotenv run -- .venv/bin/python scripts/procure_reconcile.py \
  --as-of YYYY-MM-DD \
  --repeat-builds 2 \
  --output /tmp/gba-procure-reconciliation.json
```

Then run the fleet gate from this repository with current, known-good fixture identities:

```bash
AI_FLEET_RECO_CUSTOMER_ID=... \
AI_FLEET_NBA_MANAGER_ID=... \
AI_FLEET_NBA_MANAGER_NET_UID=... \
AI_FLEET_SOLVENCY_CLIENT_ID=... \
AI_FLEET_SOLVENCY_CLIENT_NET_UID=... \
AI_FLEET_PRICING_PRODUCT_ID=... \
AI_FLEET_PRICING_PRODUCT_NET_UID=... \
AI_FLEET_PRICING_CLIENT_AGREEMENT_NET_UID=... \
AI_FLEET_PRODUCTS_PRODUCT_ID=... \
AI_FLEET_FORECAST_CLIENT_NET_ID=... \
AI_FLEET_FORECAST_PRODUCT_NET_ID=... \
python3 scripts/verify_ai_fleet.py \
  --as-of YYYY-MM-DD \
  --source-history-start 2025-01-01 \
  --procure-reconciliation /tmp/gba-procure-reconciliation.json \
  --require-semantic-fixtures
```

Provide `AI_FLEET_API_KEY`, or a service-specific
`AI_FLEET_<SERVICE>_API_KEY`, when internal authentication is enabled. Semantic fixtures are
fail-closed by default; production promotion must keep that default and require exit code `0`.
For isolated DEV health-check work only, `--allow-missing-semantic-fixtures` (or
`AI_FLEET_ALLOW_MISSING_SEMANTIC_FIXTURES=1`) explicitly downgrades missing fixtures to skipped.
Non-loopback service URLs must use HTTPS; HTTP is accepted only for loopback development services.

## Safe AI service synchronization

The systemd DEV units execute the standalone trees under `/root/projects/gba-<service>`, while
`gba-ai-services` is the pushable monorepo. `scripts/publish_gba_ai_services.sh` synchronizes only
the seven service subdirectories, defaults to a read-only checksum preview, scans publishable files
for credential-like values, and preserves Git metadata plus ignored runtime state (`.env`, `.venv`,
`data`, caches). A real sync refuses dirty destinations. Deletes are confined to the selected
service roots, and pushes use normal fast-forward checks.

When both sides contain work, do not overwrite either side. Merge them as two branches from their
shared base:

```bash
# 1. Checkpoint the monorepo fleet work on its own branch/commit.
git -C /root/projects/gba-ai-services switch -c fleet-history-contract
git -C /root/projects/gba-ai-services add -A
git -C /root/projects/gba-ai-services commit -m "feat(ai-fleet): enforce source history contract"

# 2. Import standalone work into a separate clean worktree based on the pre-fleet commit.
git -C /root/projects/gba-ai-services worktree add \
  -b standalone-sync /tmp/gba-ai-services-standalone-sync <shared-base-commit>
GBA_AI_SERVICES_DEST=/tmp/gba-ai-services-standalone-sync \
  ./scripts/publish_gba_ai_services.sh --to-monorepo --dry-run
GBA_AI_SERVICES_DEST=/tmp/gba-ai-services-standalone-sync \
  ./scripts/publish_gba_ai_services.sh --to-monorepo --apply --commit \
  --message "feat(ai-fleet): import standalone service changes"

# 3. Merge the import branch into the fleet branch and resolve overlapping health/config files.
git -C /root/projects/gba-ai-services merge standalone-sync
```

Run all service and fleet-gate tests on the resolved tree before a normal push. In particular,
verify that all seven services retain `SOURCE_HISTORY_START_DATE=2025-01-01`, and that procurement
retains `/ready`.

After the combined monorepo commit is final, make local checkpoint commits in every standalone
repository so their worktrees are clean. Then update the actual systemd runtime copies:

```bash
./scripts/publish_gba_ai_services.sh --to-standalone --dry-run
./scripts/publish_gba_ai_services.sh --to-standalone --apply
```

Review each standalone `git status`, rerun its tests, and only then restart DEV services. Reverse
sync intentionally has no commit/push option and will refuse a dirty monorepo or dirty standalone
destination.
