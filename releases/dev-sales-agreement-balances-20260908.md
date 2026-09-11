# DEV agreement balance read-path release

Deployed 2026-09-08 18:30 Kyiv. API only; console and every other container preserved.

- Image: `gba-data-concord:agreement-balances-4116d4b77`
- Image ID: `sha256:86999fffb71615f756edc3d294c01952ea7da26b64c40cba217b5c025af369a8`
- Server release commit: `4116d4b77ec224d7ee18835955c14850d9335187` (local release branch, not pushed/merged).
- Base image/source: `gba-data-concord:verified-cost-2864f040` / `2864f04058013a1f375a8ea0f64cb695b794740f`.
- Replaced only persistence DLL/PDB on the verified base image. No API/report/console dependency changes or database migration.
- Release overlay: `dev-sales-agreement-balances-20260908.compose.yml`; append to the API container's existing Compose file chain. Always target only `data-concord` with `up -d --no-deps --no-build data-concord`; the older chain contains an outdated console tag.
- Verified: 7 targeted Release tests, 11 exact pre/post API-body comparisons, first-step browser smoke test, API healthy, 52 other containers unchanged.
- Sample steady-state uncached full agreement endpoint: 316 ms before / 156–173 ms after. First post-restart request: 1218 ms. Search latency unchanged.

Host-local evidence and guarded rollback: `/root/sales-agreements-latency-20260908/deployment/REPORT.md`. Old image retained; rollback only the API, after checking no newer deployment/configuration exists.
