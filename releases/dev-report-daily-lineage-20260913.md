# DEV BUG-1245 exact report-lineage release — 2026-09-13

Status: images built and verified; deployment pending the required database-migration backup gate.

## Immutable inputs

- Server source: `8077442893422ce131df0b4a89a19065a0bcd8fa`
- Console source: `d78cc9608dafefa17e6e9a7773957cc4727209cc`
- API image: `gba-data-concord:main-807744289-report-daily-lineage`
  (`sha256:1fae071f749153e40f79d6916b8e1dc05dfe4e93a558063752a88b846093ec7e`)
- Analytics image: `gba-data-analytics:main-807744289-report-daily-lineage`
  (`sha256:5df96c1657058d7b85ac99a43e342e1511c64d3c6990df29a9fbbbe9a4f12e6c`)
- Migrator image: `gba-db-migrator:main-807744289-report-daily-lineage`
  (`sha256:ce4ce0d494023800e7322bb176d4244b90f7d8290cdf7d25dc0ea343da285fa0`)
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
- Explicit Fenix customer-capture command tests: 16/16; API hardening tests: 21/21.
- Server and console `main` and `development` remote refs are each aligned.

## Safety and remaining proof boundary

- The release persists exact Fenix product, organization, customer-graph, and customer-fact lineage; it does not copy 1C quantities or money into report calculations.
- Customer lineage can be captured only through the explicit permission-gated POST action. It uses a dedicated connection with `ApplicationIntent=ReadOnly`, a maximum inclusive range of 31 days, sequential source reads, per-publication CAS and exact receipts; it is never scheduled at startup.
- Customer `InGroup` remains fail-closed. No immutable Fenix-to-AMG operation bridge exists for the Fenix-side AMG facts, so approximate matching by date, captions, products, quantities, or amounts is prohibited.
- The last sealed native Daily matrix remains 45/210 matching numeric cells. This release is infrastructure toward parity, not a 100% parity declaration.
- No business synchronization is part of this release.
- The migration must run only through the approved one-shot migrator after the required affected-database backup. Do not enable startup migrations or recreate database services.

## Current rollback pins

- API/Analytics: `gba-data-concord:main-b5b29841d-report-daily-vat-exact` and
  `gba-data-analytics:main-b5b29841d-report-daily-vat-exact`.
- Console: `gba-console:bugs-1251-1254-9d317fb0`.

The service overlay is `dev-report-daily-lineage-20260913.compose.yml`.
