# BUG-1245 — verified DEV console release, 2026-09-07

## Published and deployed

- Console `main`: `11675bf423bb3887004b70ba03177e3fb55df9f2`.
- Preserves all four previously published console commits through `5b24f5a3`.
- Build: `2026.09.07.1111`.
- Image: `gba-console:bug-1245-main-11675bf4`.
- Image ID: `sha256:e636bd8808ccc6a6da126acf65a04fc1c655316a9bde94ae2572961e2f75f6af`.
- Container: `a677e1b1689a0fdb7048b82d4105374f86a90ba901647d7e49bc7dfab9b85dc9`.
- Started: `2026-09-07T11:12:34.071035773Z`; healthy, zero restarts.

Only the DEV console was recreated. All other 24 running container IDs remained
unchanged, including DEV API/Analytics, SQL, PROD and E2E. Console environment,
mounts and published ports were unchanged. No database migration, synchronization,
backup, role change or business-data correction was performed in this release.

The adjacent Compose overlay pins the source revision on the image. From the
infra repository, deploy **only the console**, without starting dependencies:

```bash
rtk proxy docker compose --env-file .env.dev -p gba-dev \
  -f docker-compose.yml -f docker-compose.dev.yml \
  -f releases/dev-bug-1245-console-20260907.compose.yml \
  up -d --no-deps --no-build gba-console
```

The previous `gba-console:bug-1245-20260906` image remains available for rollback.

## Verification

- Focused report/sync and newly fetched dashboard-module tests: 156/156 passed.
- Docker production TypeScript/Vite build passed; existing large-chunk warning.
- React Doctor: 91/100, no score regression. Two warnings: existing report-page
  complexity and bounded array lookup in the preset builder; no broad refactor.
- Added-source secret-pattern check and staged whitespace checks passed.
- Nginx configuration check and `/build.json` returned successfully.
- Authenticated browser: `/reports/stocks` HTTP 200; Operational is selected,
  product preset is visible/enabled, dates stay unchanged, generation remains
  explicit. No page errors or failed HTTP responses during the completed smoke.
  Initial smoke harness issues were corrected without application changes.
- Fresh protected native MAY generation and XLSX/PDF downloads returned HTTP 200.
  All 2,060 represented workbook cells, including 1,736 numeric cells, have the
  same types, values and formulas as the post-cost-repair workbook. No differences.
  Totals remain 279 units, EUR 4124.146960 sales, EUR 4070.59 cost and
  EUR 53.556960 margin. Downloads retain the explicitly recorded diagnostic
  local-origin correction; this is not an unchanged-UI-download-link pass.

**Original six-XLS parity is still not achieved.** The previous 363 MAY
article/measure mismatches remain; console deployment does not resolve them.
The reports continue to use native Operational data for this verification.

## Server mainline decision still required

Server work is saved locally on `development` as
`18012a9393bb652d0aa8a10b4470dace47967692`, including deferred experimental work.
It has not been pushed or deployed as a complete release. Server `main` last
changed in May; a non-mutating merge preview against the pre-checkpoint
development tip reported 642 conflicting files. No forced push or automatic
conflict-resolution policy was used. Replacing the old main tree with current
development, versus reconciling both sets of changes, needs an explicit decision.

CRM and both ecommerce working trees are clean and match their existing upstream
branches after fetch. No new versions of those services were deployed.
