# DEV BUG-1274 native report rollout, 2026-09-25

Only `data-analytics` and `gba-console` were recreated through
`dev-bug-1274-native-reports-20260925.compose.yml` under Compose project
`gba-dev`. `data-concord` stayed on server commit `e35c0a19`.

| Service | Candidate commit | Running image ID | Result |
| --- | --- | --- | --- |
| `data-analytics` | `a5b8483a25315160563e35f9c6c9f50c34a8811c` | `sha256:e43bc7aa12f91a00e66ab917bc568aaeb9f5f05eb88285962afa7f4ee5cc38e4` | Healthy |
| `gba-console` | `6510270e699586dffcaf9282fc4d34ed16cb5159` | `sha256:8bf83ac3bfb141bdbfd72cec7fd96b9d3ac3aa03a4ca7130daa8b60c90cc4de3` | Healthy |

Preflight: isolated Analytics Release build passed, 160 focused report
contract tests passed, source 35 GBA read-only SQL smoke 2/2 passed, source 38
allocation/fail-closed tests 5/5 passed, migration/schema/Goods receipt guard
passed, and the Console release build plus 3,456 report tests passed. The
cross-repository gate found 105/105 partial report mappings, 124 launch
combinations, 106 configured variants and 22 distinct ready requests.

Post-switch checks: `GET /report/datasets` through authenticated Console
proxy returned 35 datasets; a source-35 2026-09-05 request with exact Goods,
Buyers subtree, source organization and agreement returned HTTP 200 with one
preview row and ten columns. Its signed XLSX and PDF URLs returned HTTP 200
with `PK` and `%PDF` file signatures. Source 38 on the same incomplete whole
day returned HTTP 400 as intended. A source-36 request for its separately
certified exact product and 2026-01-01–2026-09-23 scope returned HTTP 200
with one row, three columns and three cells. The Console report route returned HTTP
200. No report request contacted 1C.

Rollback images were tagged before the switch:
`gba-data-analytics:pre-bug1274-20260925` at
`sha256:adbc06a9747f6f2616deb3bc47bd0742a345e4e324a390b81a30788f0f784f3a`
and `gba-console:pre-report1274-20260925` at
`sha256:acbadb21300d6748c76fb10b9e8964ae3a9cd3d160ede5f35805d6cda7ce4e88`.
The ready rollback override is
`dev-bug-1274-native-reports-rollback-20260925.compose.yml`; recreate only
`data-analytics` and `gba-console` with that override if needed.

This DEV rollout certifies the narrow accepted source-35 pilot, not the
historical XLS numerical parity or all periods. Source 38 still refuses the
AMG-incomplete day. The current `data-concord` image does not contain the
new supplier Provider-role publisher, so a full reset needs a separate sync
release and guarded data restoration before these pilot scopes are ready.
