# Local development handoff

This runbook reproduces the complete Docker-hosted GBA DEV stack without
placing credentials in Git. Application code, Compose manifests, safe secret
templates, bootstrap validation, and reconciliation runners are versioned.
Real credentials, database files, Docker volumes, and AI `.env` files are not.

## Repository layout

Clone these repositories as siblings under one directory:

```text
projects/
├── gba-infra/
├── gba-server/
├── gba-ecommerce-api/
├── gba_client/
├── gba_console/
└── gba_ecommerce/
```

The optional host-run AI fleet uses sibling repositories `gba-reco`,
`gba-procure`, `gba-nba`, `gba-solvency`, `gba-pricing`, `gba-products`, and
`gba-forecast` on ports `8000..8006`.

## Secrets

For a new isolated environment:

```bash
cd projects/gba-infra
./scripts/local-dev.sh init
```

Replace every `CHANGE_ME` value in `.env.dev` and `secrets/dev`. For an existing
team environment, transfer those two ignored paths over SSH or an approved
secret manager instead. Never commit, paste, or log their contents.

## Start and verify

```bash
./scripts/local-dev.sh check
./scripts/local-dev.sh up
./scripts/local-dev.sh ps
```

Local endpoints:

| Component | URL |
|---|---|
| Console | `http://localhost:8083` |
| CRM | `http://localhost:8082` |
| Storefront | `http://localhost:8081` |
| Main API | `http://localhost:35981` |
| Analytics API | `http://localhost:35982` |
| Ecommerce API | `http://localhost:62506` |
| Elasticsearch | `http://localhost:9200` |
| SQL Server | `127.0.0.1:1433` |

Connect from the host with the `sa` password stored in `.env.dev`:

```text
Server=127.0.0.1,1433;Database=ConcordDb_V5;User Id=sa;Password=<SQL_SA_PASSWORD>;Encrypt=False;TrustServerCertificate=True
```

The local stack uses four databases: `ConcordDb_V5`, `ConcordDb_Data`,
`ConcordIdentityDb`, and `VehicleRegistryDb`. Change only the `Database` value
to connect to another one. Applications running inside Compose use
`Server=gba-mssql,1433`; host tools such as SSMS, Azure Data Studio, DataGrip,
or `sqlcmd` use `127.0.0.1,1433`.

`down` preserves database and Elasticsearch volumes:

```bash
./scripts/local-dev.sh down
```

## Database and source data

A fresh SQL volume has no business data. Apply the current `gba-server`
migrations, then use the reviewed DataSync flow to populate it from Fenix and
AMG. Do not copy a shared DEV connection string into an isolated environment
and assume that it is local. Do not run reset/sync scripts against an
unverified database identity.

The complete post-sync verification is versioned in `gba-server`:

```bash
docs/datasync-maintenance/scripts/run-onec-inbound-parity.sh
docs/datasync-maintenance/scripts/run-established-reconciliation-matrices.sh
```

Both runners require explicit pipeline IDs/cutoffs and read connection strings
only from ignored secret files. See their `--help` output and
`gba-server/docs/datasync-maintenance/reconciliation-runbook.md`.

## AI fleet

The AI fleet is deliberately not bundled into this Compose stack. Each service
owns its `.env`, virtual environment, tests, and health/readiness contract. Run
the seven services on `8000..8006`, then execute
`gba-infra/scripts/verify_ai_fleet.py`. The main API reaches them through
`host.docker.internal` as configured in `docker-compose.dev.yml`.
