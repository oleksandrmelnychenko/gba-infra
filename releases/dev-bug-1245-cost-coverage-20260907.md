# DEV native report cost coverage — 2026-09-07 14:58 UTC

Deployed source correction: `9679420f700fd45fc51078fa52972a868b136f79`, merged and
pushed through server main. This fixes complete-quantity cost coverage and exact
conservation of split cost/VAT; it does **not** close the 363 original MAY gaps.

## Images and scope

- API: `gba-data-concord:bug-1245-cost-coverage-9679420f`, image ID
  `sha256:7dc98953bcce3ebe2229bd4fb1e7137b8ae4f70691c49383ddbab2b06614d03e`.
- Analytics: `gba-data-analytics:bug-1245-cost-coverage-9679420f`, image ID
  `sha256:82dee39e3fe49e677c99767937fb0496f6af1f2d0e89588e7895ec92394b4f8d`.

Both images inherit their exact live baselines and add only the tested Persistence
DLL/PDB. DLL SHA-256:
`af10c167ae5ccf32a267428157528ef8cd1693ae0881724bb5dda0ee293cec2a`.
Published, SQL-tested, native-proof and live module hashes match. Only one runtime
source file changed from the verified `16eec91f9` baseline. This is a targeted module
build, not a claim of a new full-solution build. No configuration or schema changed.

Append `dev-bug-1245-cost-coverage-20260907.compose.yml` to each service's **actual
current Compose chain**, then target only that service with `up -d --no-deps
--no-build`. API retains stock/native-boundary/AI-fleet and other overlays; Analytics
retains its report-workspace overlay. Never replace the entire DEV chain with this
file or recreate unrelated services. The previous API/Analytics images remain
available; no rollback or database restoration was performed.

Guarded preflight verified exact baseline identities, configuration hashes,
environment, mounts, ports, candidate source labels and service scope. Only API and
Analytics were recreated; the other 23 running containers, including console,
PROD/E2E and auxiliary services, remained unchanged in the recorded window. Both
targets are healthy with zero restarts. No sync, backup, migration or business-data
repair ran. Controls remained 324 migrations, 64,048 sales, 195,371 order items,
4,437 sale returns, 5,060 return items and six cost revisions. These bounded counts
are not proof of complete database-content equality.

## Verification

Old code failed 9/10 initial synthetic SQL cases. Final correction passed 11/11;
expanded native report/history regression passed 107/107 with no skipped tests.
The fixtures include quantity edits, partial/excess coverage, valid fallback,
unchanged complete/zero costs and conservation through eight source decimal places.
All created synthetic databases were cleaned up; no user records were removed.

Read-only before/candidate native queries: 864 exactly equal article/measure values,
108 articles, zero unknown cells. Fresh authenticated Excel/PDF generation and
downloads passed. All 2,060 represented Excel cells (1,736 numeric) are unchanged.
Original reference still differs in 363 article/measure cells: 77 quantity, 83 sales,
101 cost, 102 margin. Catalogue/template hashes match; sync is idle. The known
localhost-port correction was still required for downloading report files; full
UI-link/PDF rendered parity and business-posting end-to-end tests are not claimed.

Private host evidence: `/root/evidence/bug-1245-report-parity/`, especially
`native-cost-coverage-deployed-20260907.md`, `native-cost-coverage-artifacts-20260907.json`,
before/after container/database/browser receipts, `native-may-cost-coverage-audit-20260907.json`
and `test-results/cost-coverage-regression.trx`.
