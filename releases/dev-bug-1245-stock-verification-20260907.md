# DEV native stock verification — 2026-09-07

Status: deployed and verified at 14:26 UTC. Full BUG-1245 six-XLS parity is **not**
achieved. This release does not repair historical accounting coverage or change
native MAY totals.

## Source and runtime

Tested server revision: `16eec91f97c763be36ece96358bd8c5abbcc6a4c`, including stock
selection correction `4716a2e2b`, report catalogue/templates and exact selected
client-agreement identity. The 6,143-file source tree matched the revision. Full
solution build: zero warnings/errors. Final targeted regression: 56 passed, zero
failed/skipped; actual SQL tests used uniquely named synthetic databases only.

- API image: `gba-data-concord:bug-1245-stock-verification-16eec91f`;
  image ID `sha256:feab451aa50e2c8d7a411194b759301be14d3013ae849ab7e83e8cc2dbe21091`.
- Analytics preserved: `gba-data-analytics:report-workspace-20260907-v2`;
  image ID `sha256:d8ef222c5d3eeeafbc0e032383509916defb3377f2bca2071aa0eea9864798ef`.
  All 121 expected published DLL/PDB/dependency/runtime files matched the tested
  Analytics build byte for byte. A redundant replacement was not needed.
- Console preserved: `gba-console:report-workspace-20260907`;
  image ID `sha256:f7ae40794971c1267fdae14937ae94c74f912b4162c899e33b55ebebb9ffa701`.

Append `dev-bug-1245-stock-verification-20260907.compose.yml` to API's **current**
Compose chain, retaining its native-boundary, console and AI-fleet overlays. Only
`data-concord` is targeted (`up -d --no-deps --no-build`). Do not use this overlay
as a replacement for the rest of the DEV chain. Analytics remains on the separate
`dev-report-workspace-20260907.compose.yml` release.

## Guardrails and checks

Two earlier preflights detected concurrent Analytics releases and aborted before
any recreation. The final deployment preserved their functionality and recreated
only API. The other 24 running containers, including Analytics, console, PROD, E2E
and auxiliary report services, were unchanged. API environment, mounts, ports and
baked settings were compared without exposing secrets and stayed identical.

API and Analytics are healthy with zero restarts. Authenticated live checks passed
for sync status, catalogue (377 entries), private templates and native verification
with/without search. Catalogue/template content hashes stayed identical; sync was
idle. Verification has no rows in the checked interval because current historical
captures are scheduled type 16, still outside its operational-event filter.

No sync, backup, migration or accounting correction was run. Before/after controls:
324 migrations, 64,048 sales, 195,371 order items, 4,437 sale returns, 5,060 return
items, six imported-sale cost revisions. The inventory experiment remains
unapplied. Control counts are bounded evidence, not full database equality.
All temporary databases created by the selected SQL tests were cleaned up.

Fresh native MAY Excel/PDF generation/download: HTTP 200. Excel comparison:
2,060 represented cells / 1,736 numeric cells, no changed values/types/formulas.
Original hash-checked XLS comparison still has 363 article/measure gaps
(77 quantity, 83 sales, 101 cost, 102 margin). Actual totals: 279 units,
4124.146960 EUR sales, 4070.59 EUR cost, 53.556960 EUR margin; original quantity 110.
The localhost-port download correction is still recorded explicitly. PDF rendered
numbers/readability and the unchanged UI download link were not proved.

Host evidence: `/root/evidence/bug-1245-report-parity/`:
`native-stock-verification-deployed-20260907.md`, source/runtime/database/browser
receipts, `test-results/stock-verification-merged.trx`, final build log and
`native-may-stock-release-audit-20260907.json`.

The earlier API image remains available as
`gba-data-concord:bug-1245-native-boundary-188d5fc9`; no rollback was performed.
