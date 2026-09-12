# DEV BUG-1245 exact report-lineage release — 2026-09-13

Status: images built and verified; deployment pending the required database-migration backup gate.

## Immutable inputs

- Server source: `fec24bcff5c4941f2f67c4b8d0d7a7709420bc64`
- Console source: `d78cc9608dafefa17e6e9a7773957cc4727209cc`
- API image: `gba-data-concord:main-fec24bcff-report-daily-lineage`
  (`sha256:6a90a0488b3f0675a00d77bb045fd5367e508951dc9b61331908741f0ab19fc3`)
- Analytics image: `gba-data-analytics:main-fec24bcff-report-daily-lineage`
  (`sha256:a7f1b1bb2ab151b2d75b6e0b1933fd4666409c7ebbc1d86aa8ea5f2008dd08c3`)
- Migrator image: `gba-db-migrator:main-fec24bcff-report-daily-lineage`
  (`sha256:51b945e385133247cfd81afdc39653759e3d115f58396dc2fadd27b34707c63c`)
- Console image: `gba-console:main-d78cc960-report-daily-lineage`
  (`sha256:e2feb24a9fb9a8463f05a70cc57aabe5d138ebc1f65009f1b81e3f27a126ab80`)

Every image has an exact `gba.git.sha` label matching its source commit.

## Verification before rollout

- Server customer/native-filter contracts: 28/28.
- Server disposable customer SQL: 1/1.
- Server read-only live Fenix adapter: 1/1.
- Server native-sales SQL integration matrix: 41/41.
- Full server solution build: 41 projects, zero errors; existing warnings remain.
- Console exact-filter tests: 45/45; TypeScript and production build pass.
- Server and console `main` and `development` remote refs are each aligned.

## Safety and remaining proof boundary

- The release persists exact Fenix product, organization, customer-graph, and customer-fact lineage; it does not copy 1C quantities or money into report calculations.
- Customer `InGroup` remains fail-closed. No immutable Fenix-to-AMG operation bridge exists for the Fenix-side AMG facts, so approximate matching by date, captions, products, quantities, or amounts is prohibited.
- The last sealed native Daily matrix remains 45/210 matching numeric cells. This release is infrastructure toward parity, not a 100% parity declaration.
- No business synchronization is part of this release.
- The migration must run only through the approved one-shot migrator after the required affected-database backup. Do not enable startup migrations or recreate database services.

## Current rollback pins

- API/Analytics: `gba-data-concord:main-b5b29841d-report-daily-vat-exact` and
  `gba-data-analytics:main-b5b29841d-report-daily-vat-exact`.
- Console: `gba-console:bugs-1251-1254-9d317fb0`.

The service overlay is `dev-report-daily-lineage-20260913.compose.yml`.
