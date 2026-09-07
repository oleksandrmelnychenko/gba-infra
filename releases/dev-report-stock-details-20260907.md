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
