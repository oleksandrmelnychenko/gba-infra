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

## Notes

- **Connection strings** are injected as `ConnectionStrings__*` environment
  variables so the application repos' committed `appsettings.json` stay
  untouched. All APIs point at the `gba-mssql` container as `sa`.
- The `gba-mssql-data` volume is declared **external** — create it (or run
  SQL Server once) before the first `up`, so database data survives
  `docker compose down`.
- Databases (`ConcordDb_V5`, `ConcordIdentityDb`, `ConcordDb_Data`) are
  created by the EF Core migrations in `gba-server` — run those against
  `gba-mssql` before the APIs can serve real data.
- Credentials here are for **local development only**.
