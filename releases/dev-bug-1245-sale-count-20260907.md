# DEV native sale/product/price count — 2026-09-07 17:22 UTC

Only DEV `data-analytics` was recreated. Source runtime integration `0774bce39`
preserves the concurrent native quantity-unit release and adds count measure 16.
The capability-driven console needs no new deployment for this measure.

Image: `gba-data-analytics:bug-1245-sale-count-0774bce3`, ID
`sha256:ec5213f9003c7f056d74cfa7ac807ff7e854ed7974ff607db95eef342441cf08`.
It inherits the exact running `gba-data-analytics:report-native-units-6d252727`
image (`sha256:bf87aefcecb67e2227e0b296f471484caa3a0860a87add9cea41937f27a1b288`)
and adds only Contracts, Persistence and Documents DLL/PDB files. All six live
hashes match the verified candidate. This is a targeted module build, not a new
full-solution build, dependency upgrade or database migration.

Append `dev-bug-1245-sale-count-20260907.compose.yml` to the service's **actual
current Compose chain**, preserving every previous overlay including the units
release. Recreate only `data-analytics` with `up -d --no-deps --no-build`. Never
replace the entire DEV chain with this file. The prior image remains available;
no rollback or database restoration was performed.

Analytics is healthy with zero restarts. The other 24 running containers, including
Concord, console and PROD/E2E services, are unchanged. Environment, ports, command
and entrypoint matched directly. The initial order-sensitive `Mounts` array check
failed; the old array was not retained. A subsequent read-only check confirmed
all seven active bindings against both preserved prior/current Compose declarations;
service settings differ only by image. The initial failed receipt is retained.

## Verification

- New focused tests: 10 passed. Final integrated report regression: **200 passed**,
  zero failures/skips, including signed quantities, missing valuation, duplicate
  lines, distinct product IDs, filters, subtotals, workbook totals and quantity units.
- Current unit-aware baseline and candidate preserve **864 article cells**, **1,056
  price-row cells** and all eight totals exactly. Mixed-unit quantity total remains
  null; monetary totals are unchanged.
- Authenticated pre-existing export: **2,068 cells** (1,735 numeric) have identical
  values, types and formulas. Count export: **1,197 detail/total cells** match the
  native query, including the one blank mixed-unit quantity total. Largest Excel
  floating-point delta is below 2.85e-14; numeric count total 134 uses integer format.
- XLSX/PDF generation and protected downloads return HTTP 200 without URL correction.
  In the constructor, selecting the new count plus quantity and article grouping
  then clicking Generate for September 7 also returns both file links.
- Controls remain 324 migrations, 64,048 sales, 195,371 order items, 4,437 sale
  returns, 5,060 return items and six cost revisions. These bounded counts are not
  complete database equality. No sync, backup, permission or financial-data repair ran.
  Final browser controls show sync idle and unchanged catalogue/template hashes.

**Not full 1C parity.** MAY has 134 native sale/product/price incidences versus 57 in
the original. Its old 363 article/measure gaps remain; 155 newly compared count-cell
gaps are a separate metric. Original financial scope, filters, allocation grain,
product-directory columns and other reference files remain open. Counts are not
global distinct documents or item quantity; unknown price identity stays blank.

Private host receipts: `/root/bug1245-sale-count.EXgaAl/`, especially final TRX,
`native-units-conservation.json`, `export-{existing,count}-verification.json`,
`deployment-verified.json`, `candidate-artifacts.json` and authenticated browser receipts.
