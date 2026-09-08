# Verified purchase-cost observations

The Analytics release adds a dedicated authenticated reader for current posted
purchase receipts. Its native repository verifies complete immutable receipt
chains and document financial closures. Unknown evidence produces unknown cost;
the reader performs no fallback currency conversion. It uses only native GBA
reads and never opens a 1C connection or writes business records.

The release overlay mounts a new independent 32–128 character secret from the
existing ignored DEV secret directory through key-per-file configuration. The
key must not be reused from an existing integration or exposed in logs. Missing
or invalid keys deny this endpoint without preventing ordinary Analytics startup.

Append the overlay to Analytics' actual active Compose chain and recreate only
`data-analytics`. The image revision is
`ed7f03946cb682424efd6bfa76a8748b579ccd64`, including accepted ABC and the strict
procurement consumer implementation. This stage does not deploy Concord or the
Python plan contract. Root coordinates those releases after independent cost
reconciliation, cache checks and console acceptance.

Pre-release evidence: native foundation 24/24 checks (14 disposable SQL cases),
dedicated authentication/HTTP transport 20/20 checks plus one fixture correction
rerun, and procurement/Solvency consumer 95/95 checks. The live endpoint latency
and current certified coverage are measured separately; these offline results
do not establish them. Request scopes are bounded, and separate product chunks
remain separate snapshots.
