# DEV native report quantity units — 2026-09-07

Analytics uses `gba-data-analytics:report-native-units-6d252727` (runtime source
`6d252727f`), and console uses `gba-console:report-native-units-cb9527ca`
(`cb9527caf223c712e4dc1787e90a81e56d12cca2`). Both images built successfully.
Server commits after the runtime revision only add tests, diagnostic-tool fixes
and documentation; the runtime `src/` tree is unchanged.

Append `dev-report-native-units-20260907.compose.yml` to each target service's
actual active Compose chain. The guarded rollout checked each target's baseline
container identity and recreated only `data-analytics` and `gba-console` with
`up -d --no-deps --no-build`, preserving prior AI, cost, native dataset and price
overlays. Both services are healthy. Data Concord retains its prior container
and `gba-data-concord:bug-1245-cost-coverage-9679420f` image and remains healthy.
No migration or source-data repair ran.

Native datasets 0, 2 and 3 now expose exact unit grouping 28 and filter 20.
Previously deployed price grouping IDs 26 and 27 are retained. Mixed or unknown
units leave quantity cells and totals blank; current unit metadata does not
establish historical conversion factors. Monetary currency/cost rules and exact
ClientAgreement identity remain independent. No stock dataset 4 was added.

The combined native suite passed 190/190 with zero skips; the final console
report suite passed 142/142. TypeScript/Vite, ESLint and image builds passed.
Authenticated generation and actual XLSX/PDF downloads passed for all three
datasets, with same-origin emitted links. Exact unit lookup supports a single
character; invalid capabilities and ambiguous identities return 400, anonymous
catalogue access returns 401. Exported June quantities match independent native
controls for every unit, with blank mixed-unit totals. Prior gross/receipt money
and separately confirmed negative return money remain correct; unproven June
return currency still produces blank dependent totals.

The full browser workflow passed presets, exact unit lookup, saved-template
reload/apply, all three chart types and XLSX/filtered-CSV reimport without invented
mixed-unit quantity totals. Final GET confirmed both test templates were removed
and pre-existing baseline IDs preserved. Evidence and cleanup receipts are in
`/root/evidence/report-port-wave3-2026-09-07/REPORT.md`. This release corrects three
existing datasets; it is not full functional or numerical parity with the
377-entry 1C catalogue. No 1C record, form, report, module or setting was modified
or executed by this delivery.
