#!/usr/bin/env python3
"""Service adapter, executed in the service's directory and virtualenv."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import UUID


def active_clients(as_of: str) -> list[dict]:
    from app.data.db import query
    return query("""
        SELECT DISTINCT c.ID AS id, CONVERT(varchar(36), c.NetUID) AS uid
        FROM dbo.Client c
        JOIN dbo.ClientAgreement ca ON ca.ClientID = c.ID
        JOIN dbo.[Order] o ON o.ClientAgreementID = ca.ID
        JOIN dbo.OrderItem oi ON oi.OrderID = o.ID
        WHERE c.Deleted = 0 AND c.NetUID IS NOT NULL AND oi.IsValidForCurrentSale = 1
          AND o.Created >= DATEADD(day, -180, :asof)
          AND o.Created >= '20250101' AND o.Created < :asof
        ORDER BY c.ID
        """, {"asof": as_of})


def refresh(stage: str, as_of: str, token: str) -> dict:
    if stage == "nba_feedback":
        from app.services.worker import push_reco_feedback
        result = push_reco_feedback()
        if result["sent"] != result["clients"]:
            raise RuntimeError(f"feedback incomplete: {result}")
        return result
    if stage == "nba":
        from app.services.worker import run
        result = run(as_of=as_of, push_feedback=False)
        if result["failed"] or result["ok"] != result["managers"]:
            raise RuntimeError(f"NBA refresh incomplete: {result}")
        return result
    from app.data import cache
    if not cache.health():
        raise RuntimeError("Redis unavailable; refresh cannot be persisted")
    if stage == "reco":
        from app.services.recommendations.worker import run
        result = run(as_of=as_of, workers=4, refresh_token=token)
        if result["failed"] or result["copurchase_failed"]:
            raise RuntimeError(f"recommendation refresh incomplete: {result}")
        return result
    if stage == "procure":
        from app.services.replenishment import worker
        result = worker.run(as_of=as_of, warm_cart_key=False)
        if result["failed"]:
            raise RuntimeError(f"procurement refresh incomplete: {result}")
        result["cart"] = worker.warm_cart(as_of=as_of)
        result["charts"] = worker.warm_charts(as_of=as_of)
        return result

    # Force the first calculation per cycle and verify every cache write by reading
    # it back. Successful keys survive a partial-stage retry, and are safe to reuse.
    original_get, original_set = cache.get, cache.set
    verified = set()

    def fresh_get(key):
        value = original_get(key)
        if value and value.get("_fleet_refresh") == token:
            verified.add(key)
            return value
        return None

    def checked_set(key, value, ttl=None):
        payload = {**value, "_fleet_refresh": token}
        original_set(key, payload, ttl=ttl)
        if original_get(key) != payload:
            raise RuntimeError(f"cache write/readback failed for {key}")
        verified.add(key)
        return True

    cache.get, cache.set = fresh_get, checked_set
    result = {}
    if stage == "products":
        from app.api import main
        portfolio = main._build_and_cache_portfolio(cache.make_key("assortment", "portfolio", as_of), as_of)
        main._build_and_cache_stock(as_of)
        result["products"] = portfolio.get("count")
    elif stage == "solvency":
        from app.core.config import get_settings
        from app.services.solvency import service
        clients = active_clients(as_of)
        count = 0
        for offset in range(0, len(clients), 100):
            scores, errors = service.score_batch(
                [c["id"] for c in clients[offset:offset + 100]], as_of,
                get_settings().window_months, use_cache=True,
            )
            if errors:
                raise RuntimeError(f"score batch incomplete: {errors}")
            count += len(scores)
        result["clients"] = count
    elif stage == "forecast":
        from app.api.main import forecast_sales
        clients = active_clients(as_of)
        for i, client in enumerate(clients, 1):
            forecast_sales(client_net_id=UUID(client["uid"]), months=None, use_cache=True)
            if i % 50 == 0:
                print(f"forecast clients {i}/{len(clients)}", flush=True)
        result["clients"] = len(clients)
    elif stage == "pricing":
        from app.services.pricing.service import recommend_price
        from app.data import pricing_repository as repo
        redis = cache._get_client()
        pairs = set()
        for key in redis.scan_iter(match="price:*", count=200):
            parts = key.split(":")
            if parts[-1].startswith("daily-"):
                parts.pop()
            if len(parts) != 8:
                continue
            _, _, product, agreement, _, margin, vat, culture = parts
            pairs.add((int(product), agreement, float(margin), bool(int(vat)), culture))
        retired = 0
        for product, agreement, margin, vat, culture in sorted(pairs):
            if repo.resolve_product(product, None) is None or repo.resolve_client_agreement(agreement) is None:
                retired += 1
                continue
            recommend_price(product_id=product, product_net_uid=None, client_agreement_net_uid=agreement,
                            target_margin_pct=margin, with_vat=vat, culture=culture,
                            as_of_date=as_of, use_cache=True)
        result.update(working_pairs=len(pairs), retired_pairs=retired)
    else:
        raise ValueError(stage)
    result["verified_cache_keys"] = len(verified)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path.cwd()))
    result = refresh(args.stage, args.as_of, args.token)
    args.result.write_text(json.dumps({"status": "complete", **result}, indent=2, default=str) + "\n")
