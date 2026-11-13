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
        # One worker-owned observation cohort serves all producers and the full
        # cart; charts reuse that evaluated cart. Separate warm calls would read
        # and verify every receipt again under a different observation window.
        result = worker.run(as_of=as_of, warm_cart_key=True)
        counts = [result.get(name) for name in ("producers", "ok", "failed", "skipped")]
        if (
            any(type(value) is not int or value < 0 for value in counts)
            or result.get("failed") != 0
            or result.get("ok", 0) + result.get("skipped", 0) != result.get("producers")
            or result.get("business_ready") is not True
            or result.get("as_of") != as_of
        ):
            raise RuntimeError(f"procurement refresh incomplete: {result}")
        cart, charts = result.get("cart"), result.get("charts")
        if (
            not isinstance(cart, dict) or not isinstance(charts, dict)
            or cart.get("business_ready") is not True
            or cart.get("as_of") != as_of or charts.get("as_of") != as_of
            or not isinstance(cart.get("key"), str) or not cart["key"]
            or not isinstance(charts.get("key"), str) or not charts["key"]
            or type(cart.get("items")) is not int or cart["items"] < 0
            or type(charts.get("top_items")) is not int or charts["top_items"] < 0
            or result.get("cart_items") != cart["items"]
            or result.get("charts_top_items") != charts["top_items"]
            or charts["top_items"] > cart["items"]
        ):
            raise RuntimeError("procurement cart/charts warm or verified readback incomplete")
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
    if hasattr(cache, "get_many"):
        cache.get_many = lambda keys: [fresh_get(key) for key in keys]
    if hasattr(cache, "set_many"):
        def checked_set_many(items, ttl=None):
            for key, value in items.items():
                checked_set(key, value, ttl=ttl)
        cache.set_many = checked_set_many
    result = {}
    if stage == "products":
        from app.api import main
        portfolio = main._build_and_cache_portfolio(cache.make_key("assortment", "portfolio", as_of), as_of)
        main._build_and_cache_stock(as_of)
        if hasattr(main, "_build_and_cache_product_facts"):
            main._build_and_cache_product_facts(as_of)
            result["product_facts"] = True
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
        from app.services.forecasting import accuracy_gate
        result["accuracy_gate"] = accuracy_gate.run(as_of)
        result["clients"] = 0
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


def check(stage: str) -> dict:
    """Import what the stage needs and confirm the stores answer. Writes nothing."""
    import app
    report = {"stage": stage, "app": str(Path(app.__file__).resolve().parent)}
    if stage in ("nba_feedback", "nba"):
        from app.services.worker import push_reco_feedback, run  # noqa: F401
        return report
    from app.data import cache
    report["cache"] = bool(cache.health())
    if stage == "reco":
        from app.services.recommendations.worker import run  # noqa: F401
    elif stage == "procure":
        from app.services.replenishment import worker
        worker.run
    elif stage == "products":
        from app.api import main
        main._build_and_cache_portfolio, main._build_and_cache_stock, cache.make_key
    elif stage == "solvency":
        from app.core.config import get_settings
        from app.services.solvency import service
        from app.data.db import query  # noqa: F401
        get_settings().window_months, service.score_batch
    elif stage == "forecast":
        from app.services.forecasting import accuracy_gate  # noqa: F401
        from app.data.db import query  # noqa: F401
    elif stage == "pricing":
        from app.services.pricing.service import recommend_price  # noqa: F401
        from app.data import pricing_repository as repo
        repo.resolve_product, repo.resolve_client_agreement, cache._get_client
    else:
        raise ValueError(stage)
    if not report["cache"]:
        raise RuntimeError("Redis unavailable; refresh cannot be persisted")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--as-of")
    parser.add_argument("--token")
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path.cwd()))
    if args.check:
        print(json.dumps(check(args.stage)))
        sys.exit(0)
    if not (args.as_of and args.token and args.result):
        parser.error("--as-of, --token and --result are required")
    result = refresh(args.stage, args.as_of, args.token)
    args.result.write_text(json.dumps({"status": "complete", **result}, indent=2, default=str) + "\n")
