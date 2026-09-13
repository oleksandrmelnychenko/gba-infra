# DEV BUG-1245 exact report-lineage and direct-repair release — 2026-09-13

Status: images built and verified; no deployment or business sync performed. Per the operator's
instruction, no database backup was created. Consequently the required migration backup gate is
not satisfied and the dependent deployment remains pending. Fenix capture/repair additionally
requires a genuinely read-only source principal.

## Immutable inputs

- Server source: `29cd3758a8c0c127bbc4bb0221a723a110429902`
- Console source: `d78cc9608dafefa17e6e9a7773957cc4727209cc`
- API image: `gba-data-concord:main-29cd3758-report-daily-exact-repair`
  (`sha256:e3754d48ae59ac929de597a9a8327340518c9ca1de9ce4d11c3a8f6656b120e2`)
- Analytics image: `gba-data-analytics:main-29cd3758-report-daily-exact-repair`
  (`sha256:ff2252d9e0c1d9c1d9f1edc675fa3f1abc366708650835a670e0b58ddafeeae1`)
- Migrator image: `gba-db-migrator:main-29cd3758-report-daily-exact-repair`
  (`sha256:651a8e85ce1db097e763d3ad5ab4801db366f39b70b9ecd2839e0753fd6262c1`)
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
- The last sealed native Daily matrix remains 45/210 matching numeric cells. This release is infrastructure toward parity, not a 100% parity declaration.
- No business synchronization is part of this release.
- Exactly three Concord migrations remain pending:
  `20260912220000_AddNativeProductSourceClassification`,
  `20260912230000_AddNativeOrganizationSourceLineage`, and
  `20260913090000_AddFenixCustomerLineageCaptures`.
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
- The wire contract is bound to server source `29cd3758a8c0c127bbc4bb0221a723a110429902`:
  one POST per source document, one Kyiv day, one exact type, `forAmg=false`,
  `stockMode=DocumentsOnly`, and a unique canonical `X-GBA-Sync-Operation-Id`.
- Current plan hashes: README
  `c833edb703ad491308f4fdee0240261fd27d37dac8343093edd05766251f5ea6`, requests
  `3fd307159423accd7be71c8994d13a32bdde0404965bdc71b22a02adca7443cd`, verification
  `74566e9f3d8e54c1b4bd926d69801b097177a159f9f5d7c909e43353cc1d2fc6`.
- This plan is intentionally non-executable until deployment, ACL, authentication,
  idempotency-ledger, live revalidation, and rollback/verification gates pass.

## Current rollback pins

- API/Analytics: `gba-data-concord:main-b5b29841d-report-daily-vat-exact` and
  `gba-data-analytics:main-b5b29841d-report-daily-vat-exact`.
- Console: `gba-console:bugs-1251-1254-9d317fb0`.

The service overlay is `dev-report-daily-lineage-20260913.compose.yml`.
