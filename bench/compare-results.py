#!/usr/bin/env python3
"""Capture / compare search results for the queries.txt pool.

  python3 compare-results.py save    <file>   # save current results
  python3 compare-results.py compare <file>   # diff live results vs saved
"""
import sys, json, urllib.request, urllib.parse, concurrent.futures, os

BASE = os.environ.get("BASE_URL", "http://localhost:62506")
PATH = os.environ.get("SEARCH_PATH", "/api/v1/uk/elasticsearch/search")
PARAM = os.environ.get("QUERY_PARAM", "query")


def search(q):
    url = f"{BASE}{PATH}?{PARAM}={urllib.parse.quote(q)}&limit=20"
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            d = json.load(r)
        b = d.get("Body", {}) or {}
        ids = b.get("ProductIds") or b.get("productIds") or []
        return q, [int(x) for x in ids]
    except Exception:
        return q, None


def run_all(queries):
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        for q, ids in ex.map(search, queries):
            out[q] = ids
    return out


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    queries = [l.strip() for l in open(os.path.join(here, "queries.txt"), encoding="utf-8") if l.strip()]
    mode, path = sys.argv[1], sys.argv[2]

    if mode == "save":
        res = run_all(queries)
        json.dump(res, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"saved {len(res)} query results -> {path}")
        return

    base = json.load(open(path, encoding="utf-8"))
    now = run_all(queries)
    identical = changed = errors = 0
    jacc = top5 = 0.0
    n = 0
    for q in queries:
        b, c = base.get(q), now.get(q)
        if b is None or c is None:
            errors += 1
            continue
        n += 1
        identical += (b == c)
        changed += (b != c)
        sb, sc = set(b), set(c)
        jacc += len(sb & sc) / len(sb | sc) if (sb or sc) else 1.0
        t = min(5, len(b), len(c))
        top5 += (len(set(b[:5]) & set(c[:5])) / t) if t else 1.0
    print(f"compared:            {n}")
    print(f"identical order:     {identical} ({100*identical/n:.1f}%)")
    print(f"changed:             {changed} ({100*changed/n:.1f}%)")
    print(f"errors:              {errors}")
    print(f"avg Jaccard overlap: {jacc/n:.4f}  (same products returned)")
    print(f"avg top-5 overlap:   {top5/n:.4f}  (stability of the top results)")


if __name__ == "__main__":
    main()
