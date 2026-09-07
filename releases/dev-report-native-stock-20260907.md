# DEV current warehouse report — 2026-09-07

This overlay pins Analytics to `gba-data-analytics:report-native-stock-cf06b8ea`
and console to `gba-console:report-native-stock-c47ca7b6`. Append it to each
service's actual active Compose chain so previous AI, cost, price, native
dataset, unit and sale-count overlays remain in effect. Recreate only
`data-analytics` and `gba-console` with `up -d --no-deps --no-build` after the
guarded baseline check. Data Concord and database configuration are outside
this release.

Dataset 4 exposes current physical quantity, recorded free quantity and recorded
reservations separately, by exact warehouse and current measurement unit.
It has no historical period or monetary valuation. Unknown or mixed units and
missing source facts remain blank; actual zero and negative quantities remain
numeric. No equality between physical stock and free stock plus reservations
is asserted. Current-unit metadata is not historical conversion evidence.

The native query uses one consistent snapshot and restores the isolation level
of a borrowed connection. It refuses to execute when database snapshot support
is unavailable, without changing database options. It does not access 1C.

The console includes a warehouse/unit preset, exact native lookups, private
templates, current-state/read-time export attribution and eight-decimal stock
display. Saved unsupported conditions stay visible; an active unsupported
contract valuation or historical period is rejected. ClientAgreement pricing
for monetary reports remains an independent requirement.

Verification receipts and deployment status are recorded in
`/root/evidence/report-port-wave4-2026-09-07/REPORT.md` after live acceptance.
This is one native current-stock delivery, not full functional or numerical
parity with the 377-entry 1C catalogue or historical warehouse reports.
