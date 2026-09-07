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

## Server mainline transition approved and published — follow-up

After the user explicitly approved replacing the old main tree with current
development while preserving both histories, server `main` was published as
`c84c42cf11093093645d74d4487a42a9710f4ad3`. Its first parent is the former main,
`5858412c6a8834e479267473457d9eff9493a6a9`; its second parent is the saved
development checkpoint, `18012a9393bb652d0aa8a10b4470dace47967692`.
The merge tree is exactly the development tree
`659fb677c5119d8b1c1ebf466d54d3df58ddb9a7`. Both ancestry checks and the tree
identity check passed; the normal push and remote main SHA were verified.
No force-push, history deletion or implicit 642-file conflict policy was used.

This publishes the source checkpoint, not a new complete backend release.
The verified live API and Platform.Actors authored source sets are unchanged
relative to the new main; all five checked live API-release assembly hashes
match the prior native-cost release receipts. In those five assemblies, the
remaining source differences are the deferred inventory components, their
factory contract and migration. This bounded comparison does not cover every
application assembly. No experimental inventory path or migration was deployed.

Full-main offline dependency restore and the complete Release solution build
passed: zero warnings/errors, 16m06s. The isolated compiler used the pinned SDK,
2 CPU and a 16 GiB memory limit; no network or database credentials were passed.
The API native-repair regression passed 98/98 tests, with no failures or skips.
Report/worker regression passed 122 tests, with zero failures and two SQL-dependent
tests skipped because this run intentionally had no database access: 220 passed,
two skipped in total. Earlier live SQL receipts are separate evidence, not newly
executed tests of this main build. Existing DEV API/Analytics images remain
unchanged; build/test success is not a deployment or six-XLS parity claim.

Private host evidence: `server-main-release-build-20260907.log`,
`server-main-native-source-audit-20260907.json` and
`test-results/server-main-c84c42cf1-{api,reports}-20260907.trx` under
`/root/evidence/bug-1245-report-parity/`.

CRM and both ecommerce working trees are clean and match their existing upstream
branches after fetch. No new versions of those services were deployed.
