# DEV current lot quantities by organization — 2026-09-07

The overlay pins Analytics to `gba-data-analytics:stock-lots-3195c00b` and console to `gba-console:stock-lots-094cd30e`. Append it to each target's actual active Compose chain. Recreate only those two services with `up -d --no-deps --no-build`; retain all prior AI, price, cost, unit, sale-count and stock overlays. Data Concord and existing database settings are outside this release.

Dataset7 exposes every active recorded lot remaining quantity once, including zeros, negatives, virtual rows and archived/missing parents. Warehouse and organization come from the direct consignment, preserving the current owner after transfer; source receipts and root lots do not replace it. Group34/filter23 provide an exact organization identity without changing the legacy name-based organization grouping. Quantity20 has independent unit coverage and cannot alias physical/free/reserved quantity. Lot33/filter22 retains the exact lot line. No historical period or monetary valuation is claimed.

The console adds the organization/warehouse/unit preset, native identity pickers, private templates, current read-time metadata and XLSX/PDF/CSV/three chart types. Sources0/2/3/4/5/6 retain their existing meaning. No connection or write to1C, source execution, business-data correction, database migration or existing database-option change is part of this release.

Evidence and actual acceptance status are recorded in `/root/evidence/report-port-wave6-2026-09-07/REPORT.md`. This release file alone does not certify deployment or live acceptance. Agreement-price and captured-history audits document prerequisites; this release does not publish their monetary/historical calculations.

## Completed DEV acceptance

Both pinned images were built and deployed, and both services are healthy. The
original Data Concord container and all prior active overlays were preserved.
Server checks passed 327/327 without skips; console report tests passed 230/230,
build/lint passed, and Doctor scored 100→100 on the same 10 changed files.

Independent native SQL and actual XLSX matched 39,085 active lot rows, including
23,811 zeros, across 18 warehouses, two organizations and 43 organization/warehouse/
unit groups. All seven genuinely zero groups remain numeric zero. The transferred
lot retains its direct current owner; filtering it by its root owner is empty.
A separate exhausted lot retains quantity and total zero. Mixed-unit totals stay
blank, and empty reports retain their current title and UTC read window.

API acceptance verified seven real XLSX/PDF pairs and 13 rejected incompatible
requests. UI acceptance generated two additional XLSX and verified one PDF,
exercised preset/exact lookups and template save/reload/apply/update/delete,
and confirmed column/bar/line charts and CSV roundtrips for both files. The
independent final template GET confirmed the exact owned ID/name absent, with
baseline and final counts both zero. The anonymous catalogue returned 401.

Actual export regression checks passed for datasets 0/2/3/4/5/6, preserving
unit-specific quantities, exact ClientAgreement, monetary completeness guards
and the confirmed return of -2 and -85.43 EUR. No runtime fix was needed during
live acceptance; one private test expectation was corrected to use the existing
warehouse name-search directory rather than assume numeric text search.

No 1C connection, source execution, business-data mutation, migration or existing
database-option change occurred. Agreement monetary valuation and captured-history
readiness audits are documented separately and remain prerequisites for later
report capabilities. Final deployment identities and receipts are in the evidence
directory referenced above.
