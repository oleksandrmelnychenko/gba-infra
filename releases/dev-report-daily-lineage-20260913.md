# DEV BUG-1245 exact report-lineage and direct-repair candidate — 2026-09-13

Status: images built and verified; no deployment or business sync performed. Per the operator's
instruction, no database backup was created. Consequently the required migration backup gate is
not satisfied and the dependent deployment remains pending. Fenix capture/repair additionally
requires a genuinely read-only source principal.

## Immutable inputs

- Server runtime source: `c581c77b9a2c15be6c65c24df4072a0ddb69420e`
- Console source: `67f59eeae920bcfd87aa28c43d32d66934be6bdc`
- API image: `gba-data-concord:main-c581c77b9-report-daily-candidate`
  (`sha256:d637761333101633f6aa1221f8159ff70abc36f8111d33045e49ee8339c33d5e`)
- Analytics image: `gba-data-analytics:main-c581c77b9-report-daily-candidate`
  (`sha256:483ad9bf96d8616f4de4273685d57c410cbfe37dcc76e8cea4224c56461c50f1`)
- Migrator image: `gba-db-migrator:main-c581c77b9-report-daily-candidate`
  (`sha256:3bbaa248f5fec9445ea0d25c115134db59b7130c6fb3c48692c7d97e51467383`)
- Console image: `gba-console:main-67f59eea-report-daily-candidate`
  (`sha256:0af44be91c2ec89ee5a1d044b5afa0b7ee1b413249fe3ea0cdfa5deedf8458de`)

Every runtime image has an exact `gba.git.sha` label matching the source commit from which its
runtime files were built.

## Verification before rollout

- Server customer/native-filter contracts: 28/28.
- Server disposable customer SQL: 1/1.
- Server read-only live Fenix adapter: 1/1.
- Server native-sales SQL integration matrix: 41/41.
- Full server solution build: 41 projects, zero errors; existing warnings remain.
- Console exact-filter tests: 45/45; TypeScript and production build pass.
- Explicit Fenix customer-capture command tests: 16/16; API hardening tests: 21/21.
- Exact missing-document API scope and controller tests: 37/37.
- Exact missing-document/platform and permission contracts: 16/16.
- Combined exact-repair, stale-document, customer-lineage, cost, and source-permission contracts:
  45/45.
- Real SQL Server disposable-database rollback/replay/exactly-once test: 1/1, not skipped;
  no disposable database remained afterward.
- Release API dependency build: 37 projects, zero errors; nine existing warnings remain.
- Full platform regression baseline: 5,648 passed, 747 environment-gated skipped, 10 failed.
  The failures are unrelated Akka timing/time-out tests outside the changed data-sync/migration
  files; therefore the full suite is recorded as not green even though every affected test passed.
- Server and console `main` and `development` remote refs are each aligned.
- Current exact-turnover focused tests: 37 passed, 6 environment-gated skipped;
  API report contracts: 57/57; agreement-discount regression contracts: 10/10.
- Current server Release build completes with zero errors and nine reviewed pre-existing
  warnings. Current console report tests are 42/42, changed-file lint is clean, and its
  production build succeeds; the existing large-chunk advisory remains.
- The current sales-and-cost ledger migration and atomic publication path passed its real
  disposable SQL Server test (1/1); the test database was removed and a postflight query
  found zero `GbaTurnoverTest_*` databases.
- The exact Daily 1C reference matrix is now regression-bound offline: all 21 rows and 10
  measures (210/210 cells, including blanks) produce canonical SHA-256
  `aee869048b1d74b471223d5341471cae0198afa39d02410708ef6cf82f632064` in a passing test.
  Its immutable inputs were re-hashed from disk: `expected-21x10.json`
  `6eb0c5db6991fa33906007d4282ceaf4d675f58dc4659eee5ec152b9f542e0c6` and original 1C XLS
  `377546f1cf6f96561f8afbf793dca76f4bc462dbef19a622aeb02438c8a5cabb`.
- The sync catalogue now publishes the exact retained Daily preset only when all five source
  organizations, product kind `Товар`, and buyer root `Покупці` are present in the permission-gated
  Fenix catalogue. The console pre-fills that preset but still requires explicit confirmation and
  fails closed for a choice absent from the catalogue. Server OneCTurnover tests pass 39/39;
  console report/sync tests pass 67/67, production build and changed-file lint pass, and React
  Doctor reports 100/100.

## Safety and remaining proof boundary

- The release persists exact Fenix product, organization, customer-graph, and customer-fact lineage; it does not copy 1C quantities or money into report calculations.
- Customer lineage can be captured only through the explicit permission-gated POST action. It uses a dedicated connection with `ApplicationIntent=ReadOnly`, a maximum inclusive range of 31 days, sequential source reads, per-publication CAS and exact receipts; it is never scheduled at startup.
- Before every Fenix graph/fact SELECT, the same open source session now verifies effective
  database and object permissions. The current DEV credential has effective write/control rights,
  so capture and exact repair intentionally fail before reading any source rows. Do not weaken the
  gate or mutate source permissions; configure a separate genuinely read-only principal.
- The required DEV secret file
  `/root/projects/gba-infra/secrets/dev/ConnectionStrings__FenixOneCReadOnlyConnectionString`
  is currently absent. The metadata-only live probe of the ordinary Fenix credential returned
  `1 1 1 1 1` for CONNECT, SELECT, unexpected database permission, unexpected server permission,
  and object mutation permission respectively, so the new fail-closed gate rejects it.
- Customer `InGroup` remains fail-closed. No immutable Fenix-to-AMG operation bridge exists for the Fenix-side AMG facts, so approximate matching by date, captions, products, quantities, or amounts is prohibited.
- The old non-equivalent native capture remains 45/210 matching cells because its settings and
  source observation were not the same as the 1C workbook. The exact 1C matrix is now pinned
  210/210 offline, but the live source-to-generated-report test remains environment-gated until
  the genuinely read-only Fenix credential exists. This is not yet a live 100% parity declaration.
- No business synchronization is part of this release.
- A fresh read-only DEV schema check on 2026-09-13 found the cost table absent and no
  migration at or after `20260912000000` applied. Exactly four Concord migrations from
  the candidate remain pending:
  `20260912220000_AddNativeProductSourceClassification`,
  `20260912230000_AddNativeOrganizationSourceLineage`, and
  `20260913090000_AddFenixCustomerLineageCaptures`, and
  `20260913120000_AddOneCTurnoverCostLedger`.
- The migration must run only through the approved one-shot migrator after the required
  affected-database backup. The operator declined that backup for this run, so do not run the
  migrator, deploy these dependent images, enable startup migrations, or recreate database
  services.

## Exact no-execution repair plan

- Plan: `/root/evidence/bug-1245-direct-sync-repair-plan-20260913/README.md`
- Requests: `/root/evidence/bug-1245-direct-sync-repair-plan-20260913/requests.json`
- Verification: `/root/evidence/bug-1245-direct-sync-repair-plan-20260913/verification.json`
- Exact scope: 41 documents / 43 product keys: three missing sales, 37 missing returns,
  and one stale existing return revision.
- The retained wire contract is bound to the older server source
  `29cd3758a8c0c127bbc4bb0221a723a110429902`:
  one POST per source document, one Kyiv day, one exact type, `forAmg=false`,
  `stockMode=DocumentsOnly`, and a unique canonical `X-GBA-Sync-Operation-Id`.
- Current plan hashes: README
  `c833edb703ad491308f4fdee0240261fd27d37dac8343093edd05766251f5ea6`, requests
  `3fd307159423accd7be71c8994d13a32bdde0404965bdc71b22a02adca7443cd`, verification
  `74566e9f3d8e54c1b4bd926d69801b097177a159f9f5d7c909e43353cc1d2fc6`.
- This plan is intentionally non-executable against the current candidate until it is
  regenerated and revalidated on `c581c77b9a2c15be6c65c24df4072a0ddb69420e`, and until
  deployment, ACL, authentication, idempotency-ledger, and rollback/verification gates pass.

## Current rollback pins

- API/Analytics: `gba-data-concord:main-b5b29841d-report-daily-vat-exact` and
  `gba-data-analytics:main-b5b29841d-report-daily-vat-exact`.
- Console: `gba-console:bugs-1251-1254-9d317fb0`.

The service overlay is `dev-report-daily-lineage-20260913.compose.yml`.
