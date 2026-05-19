# Search benchmark

k6 load test for the product search path. Every request uses a distinct
query (`queries.txt`, 2000 entries) so the API OutputCache is bypassed and
the benchmark measures real cold Elasticsearch search work — not cache hits.

## Run

```bash
# Elasticsearch search endpoint (default)
k6 run search-load.js

# the store endpoint /products/search (param is `value`)
k6 run -e SEARCH_PATH=/api/v1/uk/products/search -e QUERY_PARAM=value search-load.js
```

Env vars: `BASE_URL`, `SEARCH_PATH`, `QUERY_PARAM`, `LIMIT`, `VUS`.

## Baseline — cold search, /api/v1/uk/elasticsearch/search, 20 VUs, 2000 distinct queries

| metric | value |
|--------|-------|
| median | 226 ms |
| p90 | 378 ms |
| p95 | 409 ms |
| min / max | 29 ms / 691 ms |
| throughput | 85 req/s |
| errors | 0% |

Cached (repeated) query: ~1 ms.

The ~230 ms cold latency is dominated by leading-wildcard (`*term*`)
Elasticsearch queries that cannot use the index. Re-run this benchmark
after any search-query change and compare against this baseline.
