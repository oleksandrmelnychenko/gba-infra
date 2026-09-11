# DEV redeployment — 2026-09-10

API, analytics, and console redeployed around 14:56–14:57 Kyiv. All healthy; other 50 containers and PROD unchanged.

- Server release `1a2e5387e` = local main `e4c664000` plus preserved agreement-balance optimization.
- Console merge `4b23e03f2` includes remote main `1013ae78` and local report work; build `2026.09.10.1447`.
- Images pinned in `dev-redeploy-20260910.compose.yml`; append to each service's existing Compose chain and target that service explicitly with `--no-deps --no-build`.
- 3189 console tests and 39 targeted server tests passed; API/analytics/console builds passed. React Doctor equal-scope score unchanged at 82.
- Authenticated endpoints passed; agreement/debt/template payloads unchanged. Browser sales step and constructor checked; daily contract-sales report generated with status `ГОТОВО`.
- No database migration, sync, data backup, PROD deployment, or Git push. Existing local edits preserved; release images retained for rollback.

Host evidence and guarded per-service rollback instructions: `/root/evidence/dev-redeploy-20260910/REPORT.md`.
