# BUG-1274 integrated main release

Prepared on 2026-10-07. This overlay is not currently active on DEV.

| Component | Main code revision | Local image |
| --- | --- | --- |
| API | `0071ea0363021c2e746b821981b93f397846edff` | `gba-data-concord:bug1274-integrated-main-0071ea0` |
| Analytics | `0071ea0363021c2e746b821981b93f397846edff` | `gba-data-analytics:bug1274-integrated-main-0071ea0` |
| Console | `753e7f19b03401d2cdf1cbdaa61000d9a25eb555` | `gba-console:bug1274-integrated-main-753e7f1` |

Console build version: `2026.10.07.0758`. Code is pushed to the server and
Console main branches. Subsequent delivery-document commits do not change the
source compiled into these images.

The Console activates the six BUG-1274 workbook forms on datasets
35 / 38 / 39 / 40 / 41 / 41. Other implemented native catalogue entries are
disabled with a ready chip; captured-only and unassessed entries are hidden.

All six production repository requests generated inline previews and Excel
workbooks on our SQL. Eight existing read-only day/supplier SQL smoke cases
passed. All six workbooks also converted to PDF through the production helper
in an isolated container with `--network none`. These execution checks do not
establish complete numeric, visual or authenticated browser acceptance.
The checked day-profit, cash and both settlement requests contain missing
classification or period data. The user requires current synchronized data,
not historical spreadsheet figures.

DEV entered separately configured maintenance mode
`dev-full-reset-20261007-no-backups` during preparation. Its API image and
disabled writers were restored; Analytics and Console remain stopped. The
existing `dev-bug-1274-main-20261007.compose.yml` was restored unchanged.
No report release should override the maintenance operation. This overlay
keeps source sync, source capture, scheduling and background writers off.

After maintenance is cleared, append this overlay to the actual complete DEV
configuration chain. Historical overlays require `GBA_REPORT_CONCORD_IMAGE`
and `GBA_REPORT_CONCORD_COMMIT`; bind those to the API image and revision above.
Recreate only the authorized report services and verify image labels, healthy
runtime, source workers off, and zero source sockets. A new genuine DEV login
is required for the six-form browser, preview and Excel/PDF download gate.
Reverify SQL on the resulting database; pre-maintenance receipts cannot prove
post-reset data coverage. BUG-1274 remains open.

The supplier quantity-caption follow-up passed 45/45 server and 50/50 Console
focused tests. Preview, Excel and the form now name the executed quantity
basis consistently; saved legacy modes retain their register-quantity label.
The supplier management-currency witness and storage-unit equivalence, exact
return cost/supplier coverage, and matrix source-formula/selector acceptance
remain open. These prepared images do not close those data/calculation gates.
