# DEV current lot quantities by organization — 2026-09-07

The overlay pins Analytics to `gba-data-analytics:stock-lots-3195c00b` and console to `gba-console:stock-lots-094cd30e`. Append it to each target's actual active Compose chain. Recreate only those two services with `up -d --no-deps --no-build`; retain all prior AI, price, cost, unit, sale-count and stock overlays. Data Concord and existing database settings are outside this release.

Dataset7 exposes every active recorded lot remaining quantity once, including zeros, negatives, virtual rows and archived/missing parents. Warehouse and organization come from the direct consignment, preserving the current owner after transfer; source receipts and root lots do not replace it. Group34/filter23 provide an exact organization identity without changing the legacy name-based organization grouping. Quantity20 has independent unit coverage and cannot alias physical/free/reserved quantity. Lot33/filter22 retains the exact lot line. No historical period or monetary valuation is claimed.

The console adds the organization/warehouse/unit preset, native identity pickers, private templates, current read-time metadata and XLSX/PDF/CSV/three chart types. Sources0/2/3/4/5/6 retain their existing meaning. No connection or write to1C, source execution, business-data correction, database migration or existing database-option change is part of this release.

Evidence and actual acceptance status are recorded in `/root/evidence/report-port-wave6-2026-09-07/REPORT.md`. This release file alone does not certify deployment or live acceptance. Agreement-price and captured-history audits document prerequisites; this release does not publish their monetary/historical calculations.
