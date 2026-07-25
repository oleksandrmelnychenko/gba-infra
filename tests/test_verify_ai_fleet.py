from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import verify_ai_fleet as gate  # noqa: E402


def exact_solvency_charts() -> dict:
    score_periods = [f"2026-{month:02d}" for month in range(1, 13)]
    turnover_periods = score_periods[-3:]
    return {
        "client_id": 7,
        "applicable": True,
        "limit_utilization_gauge": {
            "value": 0.5,
            "threshold_soft": 0.9,
            "threshold_hard": 1.0,
            "label": "limit_utilization",
        },
        "payment_discipline_donut": [
            {"label": "settled", "count": 8},
            {"label": "current", "count": 1},
            {"label": "overdue", "count": 1},
        ],
        "open_invoice_aging_bars": [
            {"bucket": "0-30", "count": 1, "amount_eur": 10.25},
            {"bucket": "31-60", "count": 0, "amount_eur": 0.0},
            {"bucket": "61-90", "count": 0, "amount_eur": 0.0},
            {"bucket": "90+", "count": 0, "amount_eur": 0.0},
        ],
        "turnover_vs_exposure": [
            {"period": period, "turnover_eur": 100.25, "exposure_eur": 10.25}
            for period in turnover_periods
        ],
        "score_sparkline": [
            {"period": period, "score": 80} for period in score_periods
        ],
        "turnover_trend": [
            {"period": period, "turnover_eur": 100.25} for period in turnover_periods
        ],
        "aging_over_time_heatmap": "pending",
        "as_of_date": "2026-12-25",
        "window_months": 12,
        "model_version": "creditscore-v3",
    }


class HttpSecurityTests(unittest.TestCase):
    def test_remote_http_is_rejected_while_loopback_http_is_allowed(self) -> None:
        self.assertEqual(
            "http://127.0.0.1:8000",
            gate._validated_base_url("http://127.0.0.1:8000"),
        )
        self.assertEqual(
            "http://[::1]:8000",
            gate._validated_base_url("http://[::1]:8000"),
        )
        with self.assertRaisesRegex(
            gate.GateHttpError,
            "non-loopback service base URLs must use HTTPS",
        ):
            gate._validated_base_url("http://ai.example.com")

    def test_redirect_handler_never_reissues_the_api_key_request(self) -> None:
        client = gate.HttpClient(timeout=1)
        self.assertTrue(
            any(
                isinstance(handler, gate._NoRedirectHandler)
                for handler in client._opener.handlers
            )
        )
        request = gate.urllib.request.Request(
            "https://ai.example.com/health",
            headers={"X-Internal-Api-Key": "do-not-forward"},
        )

        redirected = gate._NoRedirectHandler().redirect_request(
            request,
            fp=None,
            code=302,
            msg="Found",
            headers={},
            newurl="https://attacker.example/collect",
        )

        self.assertIsNone(redirected)

    def test_report_url_strips_credentials_query_and_fragment(self) -> None:
        self.assertEqual(
            "https://ai.example.com:8443/base",
            gate._base_url_for_report(
                "https://user:secret@ai.example.com:8443/base?token=secret#fragment"
            ),
        )


class HealthContractTests(unittest.TestCase):
    def test_procurement_rejects_old_false_green_health(self) -> None:
        errors, _ = gate.validate_health(
            "procure",
            {
                "status": "healthy",
                "db_connected": True,
                "redis_connected": True,
                "version": "0.1.0",
                "model_version": "procure-hist120-v1",
            },
        )

        self.assertIn("business_ready must be true", errors)
        self.assertIn("source_readiness.ready must be true", errors)
        self.assertIn("canonical_cart_items must be a positive integer", errors)

    def test_forecast_requires_business_source_counts(self) -> None:
        errors, details = gate.validate_health(
            "forecast",
            {
                "status": "healthy",
                "db_connected": True,
                "cache_connected": True,
                "business_ready": True,
                "data": {
                    "source_ready": True,
                    "source_schema_present": True,
                    "source_exists": True,
                    "source_fresh": True,
                    "canonical_row_count": 100,
                    "history_row_count": 90,
                    "history_product_count": 10,
                    "history_client_count": 20,
                    "invalid_value_row_count": 0,
                },
            },
        )

        self.assertEqual([], errors)
        self.assertEqual(100, details["canonical_row_count"])

    def test_ready_requires_ready_status_not_healthy(self) -> None:
        errors, _ = gate.validate_health(
            "reco",
            {
                "status": "healthy",
                "db_connected": True,
                "redis_connected": True,
                "business_ready": True,
            },
            ready=True,
        )

        self.assertIn("status must be 'ready'", errors)

    def test_pricing_and_solvency_require_business_source_readiness(self) -> None:
        for service, extra in (
            ("pricing", {}),
            (
                "solvency",
                {
                    "synthetic_drift_ok": True,
                    "model_drift": {"drift_level": "ok", "psi_score": 0.01},
                },
            ),
        ):
            errors, _ = gate.validate_health(
                service,
                {
                    "status": "healthy",
                    "db_connected": True,
                    "redis_connected": True,
                    **extra,
                },
            )
            self.assertIn("business_ready must be true", errors)
            self.assertIn("source.business_ready must be true", errors)

    def test_nba_requires_exact_successful_generation_proof(self) -> None:
        payload = {
            "status": "healthy",
            "db_connected": True,
            "mongo_connected": True,
            "business_ready": True,
            "source_ready": True,
            "generation_ready": True,
            "manager_count": 2,
            "synthetic_product_count": 1,
            "last_generation_managers": 2,
            "last_generation_ok": 2,
            "last_generation_failed": 0,
            "last_generation_at": "2026-07-25T10:00:00+00:00",
            "task_count": 10,
            "active_task_count": 8,
            "latest_task_refresh_at": "2026-07-25T10:01:00+00:00",
        }

        errors, _ = gate.validate_health("nba", payload)
        self.assertEqual([], errors)

        payload["last_generation_ok"] = 1
        errors, _ = gate.validate_health("nba", payload)
        self.assertIn("last_generation_ok must equal last_generation_managers", errors)


class SemanticContractTests(unittest.TestCase):
    def test_solvency_score_and_charts_require_same_identity_and_snapshot(self) -> None:
        client_uid = "11111111-1111-1111-1111-111111111111"
        score = {
            "client_id": 7,
            "client_net_uid": client_uid,
            "applicable": True,
            "score": 80,
            "rating": "A",
            "pd": 0.1,
            "currency_breakdown": [],
            "as_of_date": "2026-12-25",
            "window_months": 12,
            "model_version": "creditscore-v3",
        }
        charts = exact_solvency_charts()

        score_errors, _ = gate.validate_solvency_score(
            score,
            client_id=7,
            client_net_uid=client_uid,
            expected_as_of="2026-12-25",
            window_months=12,
        )
        chart_errors, _ = gate.validate_solvency_charts(
            charts,
            client_id=7,
            expected_as_of="2026-12-25",
            window_months=12,
        )

        self.assertEqual([], score_errors)
        self.assertEqual([], chart_errors)

        score["as_of_date"] = "2026-12-24"
        score_errors, _ = gate.validate_solvency_score(
            score,
            client_id=7,
            client_net_uid=client_uid,
            expected_as_of="2026-12-25",
            window_months=12,
        )
        self.assertIn("as_of_date must equal 2026-12-25", score_errors)

    def test_solvency_charts_fail_on_missing_empty_or_inexact_proofs(self) -> None:
        missing_gauge = exact_solvency_charts()
        del missing_gauge["limit_utilization_gauge"]
        errors, _ = gate.validate_solvency_charts(
            missing_gauge,
            client_id=7,
            expected_as_of="2026-12-25",
            window_months=12,
        )
        self.assertIn("limit_utilization_gauge must be an object", errors)

        empty_trend = exact_solvency_charts()
        empty_trend["turnover_trend"] = []
        errors, _ = gate.validate_solvency_charts(
            empty_trend,
            client_id=7,
            expected_as_of="2026-12-25",
            window_months=12,
        )
        self.assertIn("turnover_trend must be a non-empty array", errors)

        inexact_money = exact_solvency_charts()
        inexact_money["open_invoice_aging_bars"][0]["amount_eur"] = 10.255
        errors, _ = gate.validate_solvency_charts(
            inexact_money,
            client_id=7,
            expected_as_of="2026-12-25",
            window_months=12,
        )
        self.assertIn(
            "open_invoice_aging_bars[0].amount_eur must be exact EUR cents",
            errors,
        )

    def test_nba_inbox_requires_numeric_and_guid_identity(self) -> None:
        manager_uid = "11111111-1111-1111-1111-111111111111"
        payload = {
            "manager_id": 7,
            "manager_net_uid": manager_uid,
            "count": 1,
            "tasks": [
                {
                    "task_key": "manager:7|client:8|type:test",
                    "manager_id": 7,
                    "client_id": 8,
                    "priority": 1.0,
                    "p_outcome": 0.5,
                    "expected_value": 12.35,
                    "ev_score": 6.175,
                }
            ],
        }

        errors, _ = gate.validate_nba_inbox(
            payload,
            manager_id=7,
            manager_net_uid=manager_uid,
            limit=50,
            require_nonempty=True,
        )
        self.assertEqual([], errors)

        payload["manager_net_uid"] = "22222222-2222-2222-2222-222222222222"
        errors, _ = gate.validate_nba_inbox(
            payload,
            manager_id=7,
            manager_net_uid=manager_uid,
            limit=50,
            require_nonempty=True,
        )
        self.assertIn("manager_net_uid identity mismatch", errors)

    def test_recommendations_require_identity_count_unique_ids_and_ranks(self) -> None:
        payload = {
            "customer_id": 7,
            "as_of_date": "2026-07-25",
            "model_version": "reco-v1",
            "cached": False,
            "segment": "HEAVY",
            "recommendations": [
                {
                    "product_id": 101,
                    "score": 0.9,
                    "rank": 1,
                    "segment": "HEAVY",
                    "source": "repurchase",
                },
                {
                    "product_id": 102,
                    "score": 0.8,
                    "rank": 2,
                    "segment": "HEAVY",
                    "source": "discovery",
                },
            ],
            "count": 2,
            "discovery_count": 1,
            "precision_estimate": 0.1,
            "latency_ms": 10.2,
        }

        errors, details = gate.validate_recommendation(
            payload,
            customer_id=7,
            expected_as_of="2026-07-25",
            top_n=25,
            require_nonempty=True,
        )
        self.assertEqual([], errors)
        self.assertEqual(2, details["count"])

        payload["recommendations"][1]["product_id"] = 101
        payload["recommendations"][1]["rank"] = 3
        errors, _ = gate.validate_recommendation(
            payload,
            customer_id=7,
            expected_as_of="2026-07-25",
            top_n=25,
            require_nonempty=True,
        )
        self.assertIn("recommendation product_id values must be unique", errors)
        self.assertIn("recommendation ranks must be contiguous and one-based", errors)

        payload["recommendations"][1]["product_id"] = 102
        payload["recommendations"][1]["rank"] = 2
        payload["recommendations"][1]["segment"] = "LIGHT"
        errors, _ = gate.validate_recommendation(
            payload,
            customer_id=7,
            expected_as_of="2026-07-25",
            top_n=25,
            require_nonempty=True,
        )
        self.assertIn("recommendations[1].segment must equal response segment", errors)

    def test_pricing_requires_cent_money_and_floor_invariant(self) -> None:
        agreement = "11111111-1111-1111-1111-111111111111"
        product_uid = "22222222-2222-2222-2222-222222222222"
        payload = {
            "product_id": 5,
            "product_net_uid": product_uid,
            "client_agreement_netuid": agreement,
            "currency": "EUR",
            "as_of_date": "2026-07-25",
            "model_version": "pricing-v1",
            "baseline_price": 12.00,
            "recommended_price": 10.00,
            "price_floor": 9.00,
            "unit_cost_eur": 7.50,
            "suggested_discount_pct": 16.67,
            "discount_band": {"min_pct": 10.0, "target_pct": 16.67, "max_pct": 20.0},
            "peer_band": {"p25": 9.00, "p50": 10.00, "p75": 11.00, "n": 20},
            "elastic_optimal_price": None,
            "rationale": "peer-median",
        }

        errors, _ = gate.validate_pricing(
            payload,
            product_id=5,
            product_net_uid=product_uid,
            agreement_net_uid=agreement,
            expected_as_of="2026-07-25",
        )
        self.assertEqual([], errors)

        payload["recommended_price"] = 8.001
        errors, _ = gate.validate_pricing(
            payload,
            product_id=5,
            product_net_uid=product_uid,
            agreement_net_uid=agreement,
            expected_as_of="2026-07-25",
        )
        self.assertIn(
            "recommended_price must be present as exact non-negative EUR cents", errors
        )

    def test_pricing_allows_above_baseline_only_for_exact_margin_loss_flag(
        self,
    ) -> None:
        product_uid = "22222222-2222-2222-2222-222222222222"
        agreement = "11111111-1111-1111-1111-111111111111"
        payload = {
            "product_id": 5,
            "product_net_uid": product_uid,
            "client_agreement_netuid": agreement,
            "currency": "EUR",
            "as_of_date": "2026-07-25",
            "model_version": "pricing-v1",
            "baseline_price": 10.00,
            "recommended_price": 12.00,
            "price_floor": 12.00,
            "unit_cost_eur": 11.00,
            "suggested_discount_pct": 0.0,
            "discount_band": {"min_pct": 0.0, "target_pct": 0.0, "max_pct": 0.0},
            "peer_band": {"p25": None, "p50": None, "p75": None, "n": 0},
            "elastic_optimal_price": None,
            "rationale": "below-margin-loss-flag",
        }

        errors, _ = gate.validate_pricing(
            payload,
            product_id=5,
            product_net_uid=product_uid,
            agreement_net_uid=agreement,
            expected_as_of="2026-07-25",
        )
        self.assertEqual([], errors)

        payload["rationale"] = "peer-median"
        errors, _ = gate.validate_pricing(
            payload,
            product_id=5,
            product_net_uid=product_uid,
            agreement_net_uid=agreement,
            expected_as_of="2026-07-25",
        )
        self.assertIn(
            "recommended_price may exceed baseline_price only at a higher "
            "margin floor with below-margin-loss-flag",
            errors,
        )

    def test_product_regions_require_exact_count_identity_and_unique_regions(
        self,
    ) -> None:
        payload = {
            "as_of": "2026-07-25",
            "window_days": 365,
            "product_id": 88,
            "count": 1,
            "regions": [
                {
                    "region_id": 2,
                    "regional_units": 3.5,
                    "regional_revenue_eur": 17.25,
                    "regional_order_count": 2,
                    "regional_client_count": 1,
                }
            ],
        }

        errors, _ = gate.validate_product_regions(
            payload,
            product_id=88,
            expected_as_of="2026-07-25",
            expected_window_days=365,
            limit=20,
            require_nonempty=True,
        )
        self.assertEqual([], errors)

    def test_product_analytics_requires_dense_exact_product_series(self) -> None:
        rows = []
        for month in range(1, 13):
            label = f"2026-{month:02d}"
            if month == 12:
                period_end = "2026-12-25"
                complete = False
            else:
                period_end = f"2026-{month + 1:02d}-01"
                complete = True
            rows.append(
                {
                    "month": label,
                    "period_start": f"{label}-01",
                    "period_end_exclusive": period_end,
                    "is_complete": complete,
                    "units": 2.3456,
                    "order_count": 1,
                    "revenue_eur": 12.35,
                    "avg_price_eur": 5.2652,
                }
            )
        payload = {
            "product_id": 88,
            "as_of": "2026-12-25",
            "model_version": "products-v1",
            "window": {
                "months": 12,
                "start": "2026-01-01",
                "end_exclusive": "2026-12-25",
                "includes_partial_current_month": True,
            },
            "snapshot": {"product_id": 88, "found": True},
            "sales_series": rows,
            "data_quality": {
                "sales_date_field": "Order.Created",
                "sales_validity_filter": "OrderItem.IsValidForCurrentSale = 1",
                "sales_window_end": "exclusive",
                "zero_months_filled": True,
                "stock_is_current": True,
                "stock_history_available": False,
            },
        }

        errors, details = gate.validate_product_analytics(
            payload,
            product_id=88,
            expected_as_of="2026-12-25",
            months=12,
        )

        self.assertEqual([], errors)
        self.assertEqual(12, details["months"])

        payload["sales_series"][0].update(
            {
                "units": 2.0,
                "revenue_eur": 47.46,
                "avg_price_eur": 23.7291,
            }
        )
        errors, _ = gate.validate_product_analytics(
            payload,
            product_id=88,
            expected_as_of="2026-12-25",
            months=12,
        )
        self.assertEqual([], errors)

        payload["sales_series"][0]["avg_price_eur"] = 23.70
        errors, _ = gate.validate_product_analytics(
            payload,
            product_id=88,
            expected_as_of="2026-12-25",
            months=12,
        )
        self.assertIn(
            "product analytics sales_series[0].avg_price_eur is not coherent "
            "with independently rounded revenue_eur / units",
            errors,
        )
        payload["sales_series"][0].update(
            {
                "units": 2.3456,
                "revenue_eur": 12.35,
                "avg_price_eur": 5.2652,
            }
        )

        payload["sales_series"][-1]["revenue_eur"] = 12.345
        errors, _ = gate.validate_product_analytics(
            payload,
            product_id=88,
            expected_as_of="2026-12-25",
            months=12,
        )
        self.assertIn(
            "product analytics sales_series[11].revenue_eur must be exact cents",
            errors,
        )

        payload["sales_series"][-1]["revenue_eur"] = 12.35
        payload["sales_series"][0], payload["sales_series"][1] = (
            payload["sales_series"][1],
            payload["sales_series"][0],
        )
        errors, _ = gate.validate_product_analytics(
            payload,
            product_id=88,
            expected_as_of="2026-12-25",
            months=12,
        )
        self.assertIn(
            "product analytics sales_series[0] is not in exact month order",
            errors,
        )

        payload["sales_series"][0], payload["sales_series"][1] = (
            payload["sales_series"][1],
            payload["sales_series"][0],
        )
        payload["sales_series"][0]["period_end_exclusive"] = "2026-03-01"
        payload["window"]["start"] = "2025-12-01"
        errors, _ = gate.validate_product_analytics(
            payload,
            product_id=88,
            expected_as_of="2026-12-25",
            months=12,
        )
        self.assertIn(
            "product analytics window.start must match the requested dense window",
            errors,
        )
        self.assertIn(
            "product analytics sales_series[0] period_end_exclusive is not contiguous",
            errors,
        )

    def test_forecast_artifact_requires_exact_identity_horizon_and_cents(self) -> None:
        client_uid = "11111111-1111-1111-1111-111111111111"
        payload = {
            "ByClient": [
                {"SaleAmount": 10.25, "MonthNameUK": "Серпень"},
                {"SaleAmount": 11.00, "MonthNameUK": "Вересень"},
            ],
            "ByProduct": [],
            "ByClientAndProduct": [],
            "meta": {
                "status": "ready",
                "as_of": "2026-07-25",
                "requested_as_of": "2026-07-25",
                "horizon_months": 2,
                "currency": "EUR",
                "model_version": "forecast-v1",
                "source_fingerprint": "source-epoch",
                "history_window_months": 24,
                "minimum_non_zero_months": 3,
                "requested": {"client_net_id": client_uid, "product_net_id": None},
                "resolved": {
                    "client_id": 7,
                    "client_net_id": client_uid,
                    "product_id": None,
                    "product_net_id": None,
                },
                "identity": {"client": "resolved", "product": "not_requested"},
                "history": {
                    "ByClient": {
                        "status": "sufficient",
                        "month_count": 12,
                        "non_zero_month_count": 8,
                        "total_eur": 125.20,
                        "sufficient": True,
                    },
                    "ByProduct": {
                        "status": "not_requested",
                        "month_count": 0,
                        "non_zero_month_count": 0,
                        "total_eur": 0.0,
                        "sufficient": False,
                    },
                    "ByClientAndProduct": {
                        "status": "not_requested",
                        "month_count": 0,
                        "non_zero_month_count": 0,
                        "total_eur": 0.0,
                        "sufficient": False,
                    },
                },
            },
        }

        errors, details = gate.validate_forecast(
            payload,
            client_net_id=client_uid,
            product_net_id=None,
            expected_as_of="2026-07-25",
            require_nonempty=True,
        )
        self.assertEqual([], errors)
        self.assertEqual(1, details["populated_series"])

        payload["ByClient"][0]["SaleAmount"] = 10.255
        errors, _ = gate.validate_forecast(
            payload,
            client_net_id=client_uid,
            product_net_id=None,
            expected_as_of="2026-07-25",
            require_nonempty=True,
        )
        self.assertIn("ByClient[0].SaleAmount must be exact EUR cents", errors)


def exact_procurement_artifact() -> dict:
    return {
        "schema_version": 1,
        "ok": True,
        "exit_code": 0,
        "exit_name": "exact",
        "as_of": "2026-07-25",
        "source_epoch_before": "same",
        "source_epoch_after": "same",
        "plan_digests": ["digest", "digest"],
        "metrics": {
            "plan_items": 3,
            "unique_plan_products": 3,
            "products_checked": 3,
            "producer_product_pairs_checked": 3,
            "priced_selected_pairs": 3,
            "computed_unpriced_items": 0,
            "consignment_drift_keys": 0,
            "deterministic_builds": True,
            "computed_priced_cost_eur": "15.20",
            "computed_total_suggested_qty": "7.50",
        },
        "source_readiness": {
            "ready": True,
            "producer_count": 2,
            "producer_product_pair_count": 4,
            "product_count": 3,
            "sellable_storage_count": 2,
            "source_fingerprint": "fingerprint",
        },
        "issues": [{"severity": "warning", "code": "G001"}],
    }


class ProcurementArtifactTests(unittest.TestCase):
    def test_exact_artifact_passes_and_cross_checks_cart_count(self) -> None:
        errors, details = gate.validate_procurement_artifact(
            exact_procurement_artifact(),
            expected_as_of="2026-07-25",
            canonical_cart_items=3,
            expected_source_fingerprint="fingerprint",
        )

        self.assertEqual([], errors)
        self.assertEqual("15.20", details["total_cost_eur"])

    def test_epoch_or_item_drift_fails_closed(self) -> None:
        payload = exact_procurement_artifact()
        payload["source_epoch_after"] = "changed"

        errors, _ = gate.validate_procurement_artifact(
            payload,
            expected_as_of="2026-07-25",
            canonical_cart_items=4,
            expected_source_fingerprint="fingerprint",
        )

        self.assertIn("source epochs must be non-empty and identical", errors)
        self.assertIn(
            "health canonical_cart_items must equal reconciled plan_items", errors
        )

    def test_health_and_artifact_source_fingerprints_must_match(self) -> None:
        errors, _ = gate.validate_procurement_artifact(
            exact_procurement_artifact(),
            expected_as_of="2026-07-25",
            canonical_cart_items=3,
            expected_source_fingerprint="different",
        )

        self.assertIn(
            "artifact source_readiness.source_fingerprint must equal health fingerprint",
            errors,
        )


class GateAggregationTests(unittest.TestCase):
    class FakeClient:
        def request(self, **kwargs):
            service = next(
                spec.name
                for spec in gate.SERVICE_SPECS
                if kwargs["base_url"] == spec.default_url
            )
            if kwargs["path"] == "/health":
                return {
                    "status": "healthy",
                    "db_connected": True,
                    "redis_connected": True,
                    "cache_connected": True,
                    "mongo_connected": True,
                }
            if kwargs["path"] == "/ready":
                return {"status": "ready"}
            raise AssertionError(f"unexpected semantic call for {service}: {kwargs}")

    def test_missing_business_flags_and_pricing_fixture_make_gate_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / "procure.json"
            artifact.write_text(
                json.dumps(exact_procurement_artifact()), encoding="utf-8"
            )
            report = gate.run_gate(
                env={},
                timeout=1,
                expected_as_of="2026-07-25",
                procurement_artifact=str(artifact),
                require_fixtures=True,
                require_nonempty=True,
                client=self.FakeClient(),
            )

        self.assertFalse(report["ok"])
        self.assertEqual(1, report["exit_code"])
        self.assertGreater(report["summary"]["checks_failed"], 0)
        pricing = next(
            service for service in report["services"] if service["name"] == "pricing"
        )
        semantic = next(
            check for check in pricing["checks"] if check["name"] == "semantic"
        )
        self.assertEqual("fail", semantic["status"])


class CliSafetyTests(unittest.TestCase):
    def test_semantic_fixtures_are_required_by_default_with_explicit_dev_opt_out(
        self,
    ) -> None:
        with patch.dict(gate.os.environ, {}, clear=True):
            default_args = gate._parser().parse_args([])
            dev_args = gate._parser().parse_args(["--allow-missing-semantic-fixtures"])

        self.assertTrue(default_args.require_semantic_fixtures)
        self.assertFalse(dev_args.require_semantic_fixtures)

    def test_dev_opt_out_environment_variable_is_explicit(self) -> None:
        with patch.dict(
            gate.os.environ,
            {"AI_FLEET_ALLOW_MISSING_SEMANTIC_FIXTURES": "1"},
            clear=True,
        ):
            args = gate._parser().parse_args([])

        self.assertFalse(args.require_semantic_fixtures)


class RepositoryHygieneTests(unittest.TestCase):
    def test_python_cache_artifacts_are_ignored(self) -> None:
        ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(
            encoding="utf-8"
        )

        self.assertIn("__pycache__/", ignore)
        self.assertIn("*.py[cod]", ignore)
        self.assertIn(".pytest_cache/", ignore)
        self.assertIn(".ruff_cache/", ignore)


if __name__ == "__main__":
    unittest.main()
