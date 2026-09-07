# DEV current placement and contract reservation reports — 2026-09-07

This overlay pins Analytics to `gba-data-analytics:stock-details-01f53be3`
and console to `gba-console:stock-details-f9fde584`. Append it to each target's
actual active Compose chain, preserving previous AI, price, cost, unit, sale-count
and current-stock overlays. The guarded rollout recreates only `data-analytics`
and `gba-console` with `up -d --no-deps --no-build`; Data Concord and existing
database options are outside this release.

Dataset 5 exposes current physical placements with exact warehouse/address/lot-line
identities. Location codes retain their parent hierarchy, case, leading zeroes
and Unicode; the writer's `N` sentinel remains unknown while literal `0` stays a
recorded code. A recorded ConsignmentItem identity does not assert a receipt
document or add a second quantity.

Dataset 6 exposes every active recorded reservation once, attributed to a customer
and exact ClientAgreement only when its owner path is proven and unambiguous.
Shared Agreement terms cannot replace that binding. Missing or contradictory
ownership preserves the quantity with unknown attribution. Neither source claims
historical balances or monetary valuation. Quantity-unit and precision guards,
snapshot consistency and borrowed-session restoration remain in effect.

Both sources include exact native lookups, private templates, current-state UTC
read-time attribution, XLSX/PDF and eight-decimal CSV/viewer support. The four
previous datasets retain their existing capabilities and semantics. No 1C
connection, source execution, source-data correction or database migration is
part of this release. Acceptance status and receipts are recorded in
`/root/evidence/report-port-wave5-2026-09-07/REPORT.md`; a release file alone is
not evidence that deployment or live acceptance has completed.

## Completed DEV acceptance

Both pinned images were built, deployed and verified healthy. Data Concord kept
its original container. No source 1C connection, business-data correction,
database migration or existing database-option change occurred.

The integrated server suite passed 298/298 without skips; console report tests
passed 208/208, build and lint passed, and React Doctor improved from 90 to 91.
Independent native controls and generated XLSX reconciled 15,274 physical facts
across 15 warehouses and 37 location/unit groups with zero mismatches. The exact
lot retained literal location 0/0/0; the exact ClientAgreement retained quantity
1 and its negative filter complement was empty. Missing recorded addresses
remain explicitly unknown, and mixed-unit physical totals remain blank.

Authenticated API acceptance generated seven XLSX/PDF pairs. The browser generated
four additional XLSX and verified two PDF downloads, exercised both presets,
exact native pickers, private template save/reload/apply/update/delete, three
chart types and CSV roundtrips for all four files. An independent final GET
confirmed all exact owned template identities absent, including initial evidence
script runs; baseline and final template counts were both zero. Two evidence-only
assertions were corrected to respect full parent captions and exact numeric
contract lookup; deployed application code did not require a subsequent change.

Real XLSX/PDF regression checks for datasets 0/2/3/4 preserved unit-specific
quantities and monetary guards; the confirmed return remained -2 and -85.43 EUR.
The anonymous catalogue returned 401. Runtime receipts, initial and final logs,
independent controls and final cleanup evidence are retained in the report above.
Full historical and monetary parity with the complete 1C catalogue remains
outside this completed current-detail release.
