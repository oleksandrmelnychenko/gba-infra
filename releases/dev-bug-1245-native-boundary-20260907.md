# BUG-1245 — native-only DEV report release, 2026-09-07

## Released source and runtime

- Server main: `188d5fc95a9832b2a0c1b1f930d81e7bb66cc1e6`.
  Preserves the AI fleet/agreement changes through `7754bddf0`.
- Console main: `02f64578504293bccdf2f81c770e81edc8178819`.
  Includes native report change `c605f807`, all three local AI commits and all
  five remote console updates through `196624d9`. Normal merge and push; no history replacement.
- Console build: `2026.09.07.1257`.
- API image: `gba-data-concord:bug-1245-native-boundary-188d5fc9`,
  `sha256:19c4955fb6cb6799cee1ef37a3d892e3dd304d83085324bce3a0686881413f45`.
- Analytics image: `gba-data-analytics:bug-1245-native-boundary-188d5fc9`,
  `sha256:de068e93d5a54a10735818604581f5893d6884ff6f3a7ed15d378fc579f4f684`.
- Console image: `gba-console:bug-1245-native-boundary-02f64578`,
  `sha256:035dfa2d0d90135b7e0caf7fc53d44ad61bc43f22fccb7bfd7568d6e82ac6f3e`.

All three became healthy with zero restarts after deployment at 13:17 UTC.
Only those three DEV containers changed. The other 22 running containers,
including PROD, E2E, SQL and the auxiliary `reports-v9-analytics`, retained IDs.
The auxiliary container shares a service label but has no Compose config hash;
read-only Compose discovery confirmed it was not in the managed target set.

## Behavior and safety

Both report-generation actions and the service dispatch accept only native GBA
report requests. Source-register catalog/scope/inventory methods are not MVC
actions. Both daily-sync aliases reject legacy report-register query fields,
including empty fields, before replay lookup or start dispatch. Manager and worker
also reject direct experimental messages. Normal document synchronization and
native repair interfaces remain available. The console no longer mounts the
archived report-source selector or register-sync panel.

The unapplied inventory experiment has no EF migration discovery attributes.
Its isolated source-oracle code/tests remain archived; no existing table or applied
migration was removed. No synchronization, database migration, backup, authorization
change or business-data correction was run for this release.

Immediately before deployment the authenticated sync-status endpoint returned
idle, with no global lock and a successful lock probe. The deploy guard verified
the exact previous image IDs, single managed target per service, complete resolved
environment, every volume/secret mount, ports and baked configuration hashes.
Each service used its own existing Compose overlay chain plus the adjacent new
image-only overlay, with `up -d --no-deps --no-build <service>`. The AI fleet mounts
and configuration were preserved. After deployment all environment/mount/port
hashes still matched their baselines.

Backend images contain the complete published code closure from the isolated
source-matched build, layered on the prior runtime images. Existing API/Analytics
`appsettings*.json` and Analytics `crm-configuration.json` were retained exactly.
All 6,127 candidate source files matched the committed server tree. No database
credentials or network access were supplied to the isolated build/test runs.

## Verification

- Full Release solution build: 14m52.75s, zero errors/warnings. Pinned .NET SDK,
  network disabled, 2 CPUs, 16 GiB memory limit; no OOM.
- API/data-sync/AI/report tests: 285 passed, zero skipped. One existing nullable
  warning in the imported AI test; no product build warning.
- Report/worker tests: 206 passed, 13 SQL/1C integration tests skipped because the
  run had no database/source access. This suite includes archived source-oracle
  unit tests and is not six-XLS parity evidence.
- Console regression after merging remote changes: 345 passed across 56 files.
  TypeScript/Vite and the Docker build passed; existing large-chunk warning remains.
- React Doctor: 91/100 for the four native report/sync files, no regression.
  The broader merged AI/dashboard scope scores 89/100 with pre-existing complexity,
  component-size and effect warnings; not a claim that the whole console scores 91.
- Authenticated browser: native preset ready, dates preserved, no source selector
  or archived sync switch, no automatic report/sync API POST, no page errors or
  unexpected failed HTTP responses during the UI check. The normal SignalR
  negotiation POST is not a synchronization start.
- All three checked retired catalog/scope routes return 404. Both report actions
  reject non-native requests with the native-only 400 message. Both daily aliases
  reject incomplete legacy scopes with the disabled-register 400 message.
- Fresh native MAY generation and both protected downloads return 200. All 2,060
  represented cells, including 1,736 numeric cells, retain their exact values,
  types and formulas versus the pre-release native workbook. Zero cell differences.
  XLSX/PDF bytes/layout are not identical; PDF design was not an acceptance gate.
- Downloads still require the recorded diagnostic localhost-origin correction.
  This is not a proof that unchanged UI-generated download URLs preserve port 8083.
- Selected native database controls remain unchanged: 323 migrations; inventory
  experiment unapplied/table absent; 64,048 sales; 195,371 order items; 4,437 sale
  returns; 5,060 return items; six immutable cost revisions. These selected controls
  are not a byte-for-byte proof of the entire database.

Initial test-harness issues (missing legacy folder-manager initialization, a hidden
Mantine input locator and SignalR request classification) were corrected without
relaxing product assertions. Final receipts below supersede those failed harness runs.

## Remaining acceptance gates

**BUG-1245 remains In progress; six-file native equivalence is not achieved.**
MAY still has 279 units / EUR 4124.146960 gross sales / EUR 4070.59 gross cost /
EUR 53.556960 margin, with 363 article/measure mismatches against the original XLS.
The original infobase, filters and financial recognition scope must be established
before another inclusion/valuation rule is selected. Historical native inventory
movement and valuation coverage remain separate unresolved gates. No amount or
scope was adjusted merely to fit an XLS total.

## Receipts and rollback

Private evidence root: `/root/evidence/bug-1245-report-parity/`:

- `native-runtime-boundary-containers-{before,after}-20260907.json`
- `native-runtime-boundary-{before,after}-20260907.json`
- `native-runtime-boundary-browser-v2-20260907.json`
- `native-runtime-boundary-workbook-20260907.json`
- `native-may-api-{before,after}-native-boundary-20260907.{xlsx,pdf}`
- `test-results/native-boundary-api-20260907.trx`
- `test-results/native-boundary-reports-v2-20260907.trx`

The isolated build/publish source, pinned Dockerfiles, exact-scope deployment guard
and read-only validators are retained under `/root/bug1245-main-release.Z8inBs/`.
Previous runtime images remain available: API `gba-data-concord:ai-fleet-20260907-v3`,
Analytics `gba-data-analytics:bug-1245-return-attribution-20260907`, console
`gba-console:ai-fleet-20260907-v2`. Rollback changes only those image references in
the same per-service overlay chains; it does not require a schema or data rollback.
