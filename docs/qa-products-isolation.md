# QA Products Intelligence isolation

DEV products (`:8005`, `ConcordDb_V5`, Redis DB 2 / `products`) and QA products
(`:8015`, `ConcordDb_V5_QA`, Redis DB 12 / `products-qa`) must use distinct web
processes, API keys and caches. QA starts with `WARMERS_ENABLED=false`; there is
no automatic portfolio/stock rebuild or scheduled business write.

Both the Products service and .NET Products proxy must include the source fence
before routing QA traffic: `ProductsApi:SourceEnvironment` becomes
`X-GBA-Source-Environment` on each upstream request, and the proxy validates the
matching response header. QA rejects a missing or wrong expected source with 409.
A wrong expected source is rejected on DEV too. `/source` is authenticated and
returns actual checked SQL database identity and cache/warmers configuration.

## Prepare and check

Run `scripts/qa/prepare-products-qa.py` using the existing products Python venv.
The script reads running DEV products tuning and running QA NBA read-only SQL
credentials through `runtime_settings`; secrets are never printed. It writes
mode-0600 `qa.env`, `api-key`, `api-routing.json` and the service unit in
`/private/gba-qa/products` (0700). It never changes running services or SQL.
The existing NBA QA user must already have SELECT permission in the QA database;
there is no SQL grant, database copy or sync in this workflow.

The canonical unit uses `/root/projects/gba-ai-services/gba-products` as source
and `/root/projects/gba-products/.venv/bin/python` as the existing interpreter.
`--products-repo` can prepare a review worktree unit for a bounded pre-release
check. Replace that unit with the canonical unit after integration, before
removing the review worktree. Startup validates the actual database and refuses
write-capable QA credentials. Default DEV settings and cache keys stay unchanged.

After tests and installation/start of the new QA unit, run:

```
/root/projects/gba-products/.venv/bin/python scripts/qa/verify-products-isolation.py \
  --qa-only --output /private/gba-qa/products/pre-release-verification.json
```

Use `--products-repo` when checking an isolated source worktree. QA-only validation
explicitly reports DEV lineage and API routing as unverified. It verifies SQL
SELECT-only identity, wrong-source rejection and independent Redis values, then
removes only newly created UUID probe keys (60-second expiry). Existing business
cache keys are not flushed or modified.

## Release and acceptance

Integrate the source fence into both Products service deployments and .NET first.
Set `ProductsApi__SourceEnvironment=dev` on DEV. Apply QA's private routing with
`scripts/qa/route-products-qa.py`, then release the API containers using the normal
reviewed deployment. This route helper edits only the QA compose document and
never restarts anything. `gen-qa-compose.py` preserves isolated QA routing/key
across refresh and refuses regeneration if the private QA key is absent or equals
DEV's key.

After both services and API containers run the reviewed code, run the verifier
without `--qa-only` and with `--check-api`. Final acceptance requires both
`devLineageVerified=true` and `apiRoutingVerified=true`. Test representative
read-only assortment endpoints through both environment proxies before closing
the release. Do not enable QA warmers, daily jobs, full sync or copy databases.
