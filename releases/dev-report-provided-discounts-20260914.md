# DEV native 1C provided-discounts report — 2026-09-14

Status: database migration and Fenix immutable captures published; runtime image deployment pending.

## Immutable inputs

- Server source: `e32d5f83366df188803d241e6bcf4bd16c7da586`
- 1C report: `ПредоставленныеСкидки`, UUID `fa0ec96c-9345-4faf-8e35-747fc5f8e679`
- Capture manifest SHA-256: `f832c05ffb52262b1620b112baf9c25172f0df0d8ee888c57d0a7d46a857d80d`
- Capture: 26 contiguous partitions, 1,751,752 rows, 2010-01-01 through
  2026-09-12 23:59:59; signed totals 10,943,765.56 discount and 696,109.04 VAT.
- API release archive SHA-256: `58a17ef27f00d3a73a12906ca47027d9fc37f25ea8fc85066d8f742126008be8`
- Analytics release archive SHA-256: `b1552650d101771096d0e90e94eb8443c4793fd83d936a626120ba3184ab9bac`
- Release build manifest SHA-256: `cabea3635ff2709e9eae2b56a3d097a7fcdebc468807a66824b52bbe4e730ea9`

## Applied gates

- Fresh `COPY_ONLY`, compressed, checksum backup passed `VERIFYONLY`, `HEADERONLY`, and
  `LABELONLY`. Backup SHA-256: `fcaaa35480348053e7bfc4fe9f953e11a2d424c351015f6758bf99b6636d7baf`.
- The approved one-shot migrator acquired `GBA_DATABASE_MIGRATIONS` and applied exactly
  `20260914200000_AddProvidedDiscountSnapshots`; the database now has 333 migrations.
- Five Fenix reference roles were published atomically: product 371,291 rows, client 7,702,
  agreement 8,381, price type 17, and characteristic 0.
- The provided-discount snapshot published 1,751,752 immutable rows with
  `sourceConnections=0` during import and `sourceWrites=0` throughout capture/import.
- Offline Release report tests passed 2,008/2,008; report-access tests passed 6/6.

## Runtime and rollback

The release overlay is `dev-report-provided-discounts-20260914.compose.yml`. Append it after
the actual active DEV Compose chain and recreate only `data-concord` and `data-analytics` with
`up -d --no-deps --no-build`.

Rollback runtime pins are:

- API: `gba-data-concord:main-b5b29841d-report-daily-vat-exact`
- Analytics: `gba-data-analytics:main-b5b29841d-report-daily-vat-exact`
- Console remains `gba-console:bugs-1251-1254-9d317fb0` and is outside this release.

The SQL migration and immutable imported evidence are retained during runtime rollback.
Restoring the pre-migration backup is the database rollback path and requires a separate
operator-reviewed maintenance window.

## Parity boundary

Fenix has executable native source 24 with exact agreement identity, agreement-owner client,
signed discount/VAT measures, filters, TOP, threshold and ABC. AMG remains captured only.
`SnapshotConsistencyVerified` remains false because the capture proves two identical physical
passes, not a source transaction snapshot. Full visual parity with every 1C layout is not claimed.
