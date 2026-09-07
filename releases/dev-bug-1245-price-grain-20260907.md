# DEV native report price grouping — 2026-09-07 16:41 UTC

Source implementation: server `d0f42310d`. This adds native gross unit-sales-price
and complete gross unit-cost grouping to operational sales. Amounts remain native;
this does **not** close the 363 original MAY article/measure differences.

## Image and deployment scope

Only `gba-dev-data-analytics-1` was recreated with
`gba-data-analytics:bug-1245-price-grain-d0f42310`, image ID
`sha256:c54e409279870ab24adcac656184c673882192fa68e02c3f9bfba57ea2199231`.
It inherits the exact prior `gba-data-analytics:report-native-wave2-54d351ec` image
(`sha256:aa884b9cbe1ee5812767af09dfe8869665fd9ce86d6eea2b94bfed76635cbb45`)
and adds one layer containing only Contracts/Persistence DLL/PDB files. No full
solution build, dependency upgrade or console deployment is claimed.

Append `dev-bug-1245-price-grain-20260907.compose.yml` to Analytics' **actual current
Compose chain**, preserving all earlier native-boundary, workspace, cost-coverage
and wave-2 overlays. Target only `data-analytics` with `up -d --no-deps --no-build`.
Do not use this overlay to replace the entire DEV chain. Baseline image remains
available; no rollback or data restoration was performed.

Guarded deployment checked the baseline container/image identity, clean source,
candidate baseline layers and service scope. The other 24 running containers,
including Concord, console, PROD/E2E and auxiliary services, were preserved.
Analytics is healthy with zero restarts. Environment, mounts, ports, command and
entrypoint are unchanged. Live DLL SHA-256 matches the tested build:

- Contracts: `a5292a0b90fa0c6e2615d1b5e4b971773ce00f076c27c30f3a6d556c05ed0cc3`.
- Persistence: `a3d6b3bf05bd36234337c75312f73890881cfa8c4f0a4f760f0c0ac778149b8d`.

## Checks and remaining limitations

Focused new cases: 6 passed; native report regression: 164 passed, zero failures
or skips. Contracts, Persistence and native parity diagnostic builds passed.
Before/candidate native queries preserve all 864 decimal article cells and eight
totals exactly. New grouping produces 132 native rows versus 55 original rows;
620 four-measure differences at price grain are distinct from the unchanged
363 article/measure differences, not an increase in defects.

Authenticated XLSX/PDF generation and protected downloads pass without local-origin
correction. All 2,060 pre-existing exported cells (1,736 numeric) are unchanged.
Price-grain XLSX has numeric price keys and all 1,064 detail/total amounts agree
with the native query within 1e-10 Excel floating-point tolerance. The largest
observed difference is below 2.85e-14; native decimal conservation is checked
separately. The current console exposes both new fields without a frontend build.
Selecting article, both price fields and quantity then clicking Generate for
September 7 returned HTTP 200 with both file links (separate today's-date UI smoke,
not original-file parity). No template was saved.

Database controls remained 324 migrations, 64,048 sales, 195,371 order items,
4,437 sale returns, 5,060 return items and six cost revisions. No sync, backup,
migration, permissions change or financial-data repair ran. These bounded counts
are not proof of complete database equality. Sales-count semantics, original
financial scope/filters, allocation differences and product-directory columns
remain open. PDF rendering/design and full business-posting equivalence are not
claimed. The reference workbook is diagnostic evidence, never runtime input.

Private host receipts: `/root/bug1245-native-price-grain.pu2e5V/`, including the
TRX files in `test-results/`, native before/candidate/conservation JSON, three
authenticated export pairs, workbook comparison JSON, runtime assembly hashes,
deployment configuration checks and before/after container/database controls.
