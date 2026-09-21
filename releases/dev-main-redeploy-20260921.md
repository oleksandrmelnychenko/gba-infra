# DEV main redeploy — 2026-09-21

## Released sources

| Service | Source branch | Revision | Image |
| --- | --- | --- | --- |
| Data Concord API | `main` | `c0846109dba398af75027111c016ff35f173873f` | `gba-data-concord:main-c0846109-20260921` |
| Console | `main` | `bb46c50c49aa567a368c0ab4c4f5e598d31d5eb7` | `gba-console:main-bb46c50c-20260921` |
| Ecommerce API | `development` | `62a71105c6a3681aafaca41dbacb65e0a7bea167` | `gba-ecommerce-api:development-62a71105-20260921-fixed` |
| Ecommerce storefront | `development` | `58745b21de3bef2c2cb40484bed526f48fa0ca97` | `gba-ecommerce:development-58745b21-20260921` |

The Data Concord `main` branch advanced from `39c51a6e` to `c0846109` while the deployment was in progress, so the API was rebuilt and switched to the newer revision before final verification. The ecommerce repositories use `development` as the DEV integration branch. At release time it was 77 commits ahead of `main` for the API and 110 commits ahead for the storefront. The primary storefront worktree contained unrelated local edits and was not modified; the release was built from a clean detached worktree.

## Artifact verification

- Data Concord `Global.Business.Assistant.Api.dll`: `833dd3bfcb5cdcb839f5e08ffc4186248af52daa8a919a8a5e5197e81d126171`
- Console `build.json`: `df99822923576558846eb3b78d86282617e4b2bfa12a1c2d5b8cf4de7f26d8ea`
- Ecommerce API `GBA.Ecommerce.dll`: `f9a41467fc1570c73aa48a8d75bae6bec83520f7507428773815d3d770b41832`
- Ecommerce storefront `server.js`: `091f484bcb013ba97120cf59d7eb283702604d993ef3fcb40a89a7cf2cc13369`
- Console build marker: `2026.09.21.1632`

The ecommerce API runtime image includes the Dockerfile-required ownership of `/app` by UID `1654`; this fixes write access to `/app/Data` and `/app/logs` while retaining the non-root runtime user.

## DEV runtime verification

All released containers were running with `RestartCount=0`, health status `healthy`, restart policy `unless-stopped`, the expected immutable image ID, and the expected full source revision label.

| Endpoint | Result |
| --- | --- |
| `https://gba-api-dev.85.17.167.167.nip.io/health` | `Healthy` |
| `https://gba-console-dev.85.17.167.167.nip.io/build.json` | `{"build":"2026.09.21.1632"}` |
| `https://gba-console-dev.85.17.167.167.nip.io/qa-desk/api/builds/current` | HTTP `200`; build `2026.09.21.1632` |
| `https://shop-dev.85.17.167.167.nip.io/` | HTTP `200` |
| `https://shop-dev.85.17.167.167.nip.io/api/health` | `{"status":"ok"}` |
| `https://ecom-api-dev.85.17.167.167.nip.io/health` | `Healthy`; `db-main` and `search-index` healthy |

Local checks on ports `35981`, `8083`, `62506`, and `8081` returned the same successful results. The Desk web, worker, and sentinel containers were also restarted and verified running. Superseded and temporary verification containers have restart policy `no`; all four live application containers have Compose `oneoff=False`.

## Restored supporting DEV services

- `gba-reco-redis` was restored with its existing volume, returned `PONG`, and now uses restart policy `unless-stopped`.
- `gba-nba-mongo` was restored with its existing volume, returned an authenticated admin ping, and now uses restart policy `unless-stopped`.
- `reports-v9-analytics` was restored on `127.0.0.1:35992`, returned `Healthy`, and now uses restart policy `unless-stopped`.
- The active Analytics service has the read-only `ProcureCostInternalAuth__ApiKey` secret mount restored. Its internal purchase-cost route returned HTTP `200` instead of the previous authentication failure.
- `gba-procure-scheduler.service` completed a full watchdog cycle for all 506 producers with zero failures; Redis and Mongo connections were confirmed in the scheduler log.
- `gba-docker-firewall.service` is active. `DOCKER-USER` permits established traffic and public ports `80/443`, then drops other ingress on `ens10f0`; host AI ports `8000:8006` are also dropped on that interface.

## Runtime recovery record

- Removed 10,448 stale BuildKit leases dated before 2026-09-01; non-build container and image leases were left intact.
- Preserved the pre-recovery BuildKit databases as recoverable backups:
  - `/var/lib/docker/buildkit/containerd-overlayfs/metadata_v2.db.backup-20260921T1808`
  - `/var/lib/docker/buildkit/cache.db.backup-20260921T1812`
  - `/var/lib/docker/buildkit/history_c8d.db.backup-20260921T1812`
- Removed the temporary Containerd GC deferral after identifying that synchronous lease deletion was waiting on it.
- Added `/etc/systemd/system/containerd.service.d/startup-timeout.conf` with a 10-minute startup timeout because the existing metadata database can require several minutes to fault in under host I/O load.
- Moved three already-dead Docker container metadata directories that blocked daemon recovery into the recoverable quarantine `/var/lib/docker/quarantine-20260921/containers/`; no application data or live container metadata was removed.

## Rollback images

- Console: `gba-console:bugs-1244-1253-1254-1257-1260-f29a3f6`
- Data Concord API: `gba-data-concord:bugs-1244-1253-1254-1257-1260-77cb104`
- Ecommerce storefront: `gba-ecommerce:latest`
- Ecommerce API: `gba-ecommerce-api:latest`
