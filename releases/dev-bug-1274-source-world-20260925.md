# DEV BUG-1274 supplier source-world rollout, 2026-09-25

The Compose override `dev-bug-1274-source-world-20260925.compose.yml` recreated
only `data-analytics` and `gba-console` in project `gba-dev`. The report reads
GBA SQL. No 1C connection, source write, GBA data write, full reset or
`data-concord` activation was part of this rollout.

| Service | Commit | Running image ID | Health |
| --- | --- | --- | --- |
| `data-analytics` | `23b58b2` | `sha256:387b34688913e827448cf586291b1cf41cf05c5034e6859659dab65e803d47de` | healthy |
| `gba-console` | `379a6b2` | `sha256:5ccb4463f90348fbd2632735caaa8e79a6a1263b5451ed130cac22e2ed6ea841` | healthy |
| `data-concord` | unchanged | `sha256:40e405cdd4b2a2382bcd778704ce71040791d7f72382f30d9a68f30c247ca77e` | healthy |

The previous running images were tagged before switching:

- `gba-data-analytics:pre-sourceworld-20260925` =
  `sha256:e43bc7aa12f91a00e66ab917bc568aaeb9f5f05eb88285962afa7f4ee5cc38e4`;
- `gba-console:pre-sourceworld-20260925` =
  `sha256:2a83441c83deb67f36c77d052fc3fa8d258426c776abb26631e74bf5523fd7c9`.

The rollback override is
`dev-bug-1274-source-world-rollback-20260925.compose.yml`. Recreate only the
same two services with that override if rollback is needed.

## Acceptance

The authenticated Console proxy returned 35 datasets and advertised source 38
capability `Version=1`, `SourceWorlds=[0,1]`, with complete-period lineage
required. An authenticated 2026-09-05 Fenix source-38 preview returned HTTP
200: 22 materialized rows, ten page rows and six selected measures. Its signed
XLSX and PDF links each returned HTTP 200 with `PK` and `%PDF` signatures.
The same request without the source-world choice returned HTTP 400 because
the mixed day includes incomplete AMG supplier batches. The Console route
returned HTTP 200 and both replaced containers are healthy.

Before deployment, the exact DEV SQL read-only smoke passed, 56 focused server
tests passed, the report suite without local SQL fixtures passed 2,654 tests,
the Console production build and lint passed, and its full Vitest suite passed
6,665 tests with one skip. Two additional focused Console tests for AMG launch
and saved-world restoration passed after that full run. A broad concurrent
local SQL-fixture run had seven failures outside the focused source-38 gate;
the offline report-capture suite had one preexisting pinned-hash mismatch for
`ClientsSyncRepository.cs`. Neither result is represented as a clean full gate.

The source-38 result remains `native_partial`: AMG supplier identity, the 1C
residual-sale behavior, exact quantity unit and XLS numerical parity are still
open. The current `data-concord` lacks the new Provider-role publisher, so a
full reset can remove the 16 locally bridged Fenix allocation links. Its
separate candidate remains held because its ordinary 1C lineage reader can
make broad period scans. The retained current DEV pilot is the accepted scope.
