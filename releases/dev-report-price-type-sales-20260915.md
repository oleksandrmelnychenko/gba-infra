# DEV native 1C price-type sales comparison report — 2026-09-15

Status: database migration, immutable Fenix capture publication, and API, Analytics, and Console runtime deployment completed.

## Release revisions

- Server: `60f3484fcf040c00fbc67e1426b0ce460e683da9`
- Console: `03035c7bee2f9888d3457423ab0cd4751ca397a9`
- API image: `gba-data-concord:reports-price-type-60f3484fc`, digest
  `sha256:6e924cd82072414e35181b6e821ae6594fa466cb906cbf95020e3830ecdc22dd`
- Analytics image: `gba-data-analytics:reports-price-type-60f3484fc`, digest
  `sha256:2d38deba7a189bb14b54305709e4854833585b79c2d0196805f54b591fc59cc4`
- Console image: `gba-console:reports-price-type-03035c7b`, digest
  `sha256:3609556c5ec8c622295f161d1aae61417a05c966abeb5c4be3c3de864d89636e`
- Console build: `2026.09.15.1230`

## Database and immutable input

- Pre-migration backup:
  `/var/opt/mssql/backup/ConcordDb_V5_pre_price_register_20260915_0200.bak`.
  It was created with `CHECKSUM` and passed `RESTORE VERIFYONLY`.
- The one-shot migrator applied `20260915120000_AddPriceRegisterSnapshots`. The previously
  pending `20260914210000_AddOneCClientDiscountReport` was also applied by the ordered
  migration chain.
- Fenix price-register manifest SHA-256:
  `ef2ab37fb59187d4cd0e695f0eb555133f0c4f2a41c89c291b29c22a14653590`.
- Published snapshot: `5c283322-7350-4a92-8d51-13e5cac74e0e`, 5,294,666 rows, physical
  period from 4008-05-03 through 4026-09-14.
- Import evidence records `sourceConnections=0`, `sourceWrites=0`, and
  `parityVerified=false`. A post-commit SQL check confirmed the manifest, row count,
  ready state, and current publication.
- The exact last-slice function returned the expected Fenix row at physical period
  4026-09-14 for report date 2026-09-15.

## Verification

- Server report suite: 2,068 passed.
- Catalogue contract tests: 32 passed.
- Console source-27 tests: 11 passed; the related UI rerun passed 16/16.
- Console production build and report-feature ESLint passed. React Doctor reported 100/100
  for the new panel and no regression in the pre-existing workspace finding.
- DEV API, Analytics, and Console containers are healthy and carry release label
  `report-price-type-sales-20260915`.
- API and Analytics health endpoints returned HTTP 200. The protected Analytics dataset
  and lookup routes returned HTTP 401 without credentials, confirming that the deployed
  routes exist and retain authentication. The public constructor returned HTTP 200.
- No application `fail`, critical exception, or unhandled-error entries appeared in the
  post-deployment API or Analytics logs.

## Runtime and rollback

Append `dev-report-price-type-sales-20260915.compose.yml` after the active DEV Compose
chain and recreate `data-concord`, `data-analytics`, and `gba-console` without building.

The pre-release runtime pins are:

- API: `gba-data-concord:bugs-1253-1254-1257-dd1e6c525`
- Analytics: `gba-data-analytics:bugs-1253-1254-1257-dd1e6c525`
- Console: `gba-console:bugs-1253-1254-1257-e3c287f8`

The additive database tables and immutable snapshot remain in place during an image
rollback. Restoring the verified backup is a separate database rollback procedure.

## Parity boundary

Fenix source 27 is executable as a native partial report with the extracted 1C dimensions,
filters, and comparison behavior. AMG remains captured only. The report uses the global
1C price-type register; it does not calculate or recommend client agreement prices.
`parityVerified` remains false until an authenticated 1C output oracle is captured and
compared cell by cell.
