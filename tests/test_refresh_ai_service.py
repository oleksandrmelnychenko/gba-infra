"""Refresh receipts must cover the single worker-owned procurement build."""
from __future__ import annotations

import copy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refresh_ai_service as adapter


def warmed_result() -> dict:
    return {
        "producers": 506, "ok": 505, "failed": 0, "skipped": 1,
        "business_ready": True, "as_of": "2026-09-08",
        "cart_items": 710, "charts_top_items": 30,
        "cart": {"key": "new-contract:cart:all", "items": 710,
                 "business_ready": True, "as_of": "2026-09-08"},
        "charts": {"key": "new-contract:charts:all:30", "top_items": 30,
                   "as_of": "2026-09-08"},
    }


class ProcurementRefreshAdapterTests(unittest.TestCase):
    def invoke(self, payload: dict, *, redis_ready: bool = True):
        cache = types.ModuleType("app.data.cache")
        cache.health = Mock(return_value=redis_ready)
        worker = types.ModuleType("app.services.replenishment.worker")
        worker.run = Mock(return_value=payload)
        worker.warm_cart = Mock(side_effect=AssertionError("second cohort build"))
        worker.warm_charts = Mock(side_effect=AssertionError("second chart build"))
        modules = {name: types.ModuleType(name) for name in (
            "app", "app.data", "app.services", "app.services.replenishment")}
        modules["app.data"].cache = cache
        modules["app.services.replenishment"].worker = worker
        modules["app.data.cache"] = cache
        modules["app.services.replenishment.worker"] = worker
        with patch.dict(sys.modules, modules):
            result = adapter.refresh("procure", "2026-09-08", "test-cycle")
        worker.run.assert_called_once_with(as_of="2026-09-08", warm_cart_key=True)
        worker.warm_cart.assert_not_called()
        worker.warm_charts.assert_not_called()
        return result

    def test_single_build_preserves_verified_nested_receipts(self):
        payload = warmed_result()
        self.assertIs(payload, self.invoke(payload))

    def test_incomplete_producer_population_cannot_complete_cycle(self):
        changes = ({"failed": 1}, {"ok": 504}, {"skipped": True},
                   {"business_ready": False}, {"as_of": "2026-09-07"})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.invoke({**warmed_result(), **change})

    def test_missing_or_inconsistent_warm_receipt_is_rejected(self):
        changes = [
            ("cart", None), ("charts", None), ("cart_items", 709),
            ("charts_top_items", 29),
        ]
        for field, value in changes:
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.invoke({**warmed_result(), field: value})
        for target, field, value in [
            ("cart", "business_ready", False), ("cart", "key", ""),
            ("cart", "items", True), ("cart", "as_of", "2026-09-07"),
            ("charts", "key", None), ("charts", "top_items", -1),
            ("charts", "as_of", "2026-09-07"),
        ]:
            payload = copy.deepcopy(warmed_result())
            payload[target][field] = value
            with self.subTest(target=target, field=field), self.assertRaises(RuntimeError):
                self.invoke(payload)

    def test_redis_failure_cannot_produce_refresh_receipt(self):
        with self.assertRaisesRegex(RuntimeError, "Redis unavailable"):
            self.invoke(warmed_result(), redis_ready=False)


if __name__ == "__main__":
    unittest.main()
