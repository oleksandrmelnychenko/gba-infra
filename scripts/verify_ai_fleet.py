#!/usr/bin/env python3
"""Read-only, fail-closed release gate for the GBA AI service fleet.

The gate calls operational and explicitly read-only semantic endpoints.  It never
connects to SQL/Mongo/Redis and never calls feedback, generation, cache-delete, or
other mutation routes.  Procurement is reconciled by validating the JSON artifact
produced by gba-procure's read-only ``procure_reconcile.py`` command.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
DEFAULT_SOURCE_HISTORY_START = "2025-01-01"
JSON_OBJECT = dict[str, Any]
Validator = Callable[[object], tuple[list[str], JSON_OBJECT]]


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    default_url: str
    ready_path: str | None


SERVICE_SPECS = (
    ServiceSpec("reco", "http://127.0.0.1:8000", "/ready"),
    ServiceSpec("procure", "http://127.0.0.1:8001", "/ready"),
    ServiceSpec("nba", "http://127.0.0.1:8002", "/ready"),
    ServiceSpec("solvency", "http://127.0.0.1:8003", "/ready"),
    ServiceSpec("pricing", "http://127.0.0.1:8004", "/ready"),
    ServiceSpec("products", "http://127.0.0.1:8005", "/ready"),
    ServiceSpec("forecast", "http://127.0.0.1:8006", "/ready"),
)


@dataclass
class CheckResult:
    name: str
    status: str
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)
    details: JSON_OBJECT = field(default_factory=dict)


@dataclass
class ServiceResult:
    name: str
    base_url: str
    ok: bool
    checks: list[CheckResult]


class GateHttpError(RuntimeError):
    """An HTTP, transport, or JSON-contract failure."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects so internal API credentials are never forwarded to another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _is_loopback_host(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validated_base_url(base_url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(base_url)
        _ = parsed.port
    except (TypeError, ValueError) as exc:
        raise GateHttpError("service base URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise GateHttpError(
            "service base URL must use HTTP or HTTPS and include a host"
        )
    if parsed.username is not None or parsed.password is not None:
        raise GateHttpError("service base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise GateHttpError("service base URL must not contain a query or fragment")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise GateHttpError("non-loopback service base URLs must use HTTPS")
    return base_url.rstrip("/")


def _base_url_for_report(base_url: str) -> str:
    """Strip URL credentials/query/fragment before serializing a report."""
    try:
        parsed = urllib.parse.urlsplit(base_url)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return "<invalid-url>"
    if not parsed.scheme or not hostname:
        return "<invalid-url>"
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{display_host}:{port}" if port is not None else display_host
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


class HttpClient:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def request(
        self,
        *,
        base_url: str,
        api_key: str | None,
        method: str,
        path: str,
        query: Mapping[str, object] | None = None,
        body: object | None = None,
    ) -> object:
        url = f"{_validated_base_url(base_url)}/{path.lstrip('/')}"
        if query:
            encoded = urllib.parse.urlencode(
                {key: value for key, value in query.items() if value is not None}
            )
            url = f"{url}?{encoded}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "gba-ai-fleet-release-gate/1",
        }
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if api_key:
            headers["X-Internal-Api-Key"] = api_key
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read(512)
            detail = _safe_http_detail(raw)
            raise GateHttpError(f"HTTP {exc.code}{detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GateHttpError(f"transport error: {exc}") from exc
        if not 200 <= status < 300:
            raise GateHttpError(f"HTTP {status}")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GateHttpError("response is not valid UTF-8 JSON") from exc


def _safe_http_detail(raw: bytes) -> str:
    """Return a short server error code without reflecting arbitrary response data."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail")
    if isinstance(detail, str) and len(detail) <= 160:
        return f": {detail}"
    return ""


def _is_strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_id(value: object) -> bool:
    return _is_strict_int(value) and value > 0


def _nonnegative_int(value: object) -> bool:
    return _is_strict_int(value) and value >= 0


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _finite_number(value: object, *, minimum: Decimal | None = None) -> bool:
    number = _decimal(value)
    return number is not None and (minimum is None or number >= minimum)


def _cents(value: object, *, minimum: Decimal | None = Decimal("0")) -> bool:
    number = _decimal(value)
    if number is None or (minimum is not None and number < minimum):
        return False
    try:
        return number == number.quantize(Decimal("0.01"))
    except InvalidOperation:
        return False


def _scaled_nonnegative(value: object, scale: str) -> bool:
    number = _decimal(value)
    if number is None or number < 0:
        return False
    try:
        return number == number.quantize(Decimal(scale))
    except InvalidOperation:
        return False


def _rounded_ratio_is_coherent(
    *,
    numerator: Decimal,
    denominator: Decimal,
    ratio: Decimal,
    numerator_quantum: Decimal,
    denominator_quantum: Decimal,
    ratio_quantum: Decimal,
) -> bool:
    """Check one exact ratio behind three independently rounded JSON values."""
    if numerator < 0 or denominator <= 0 or ratio < 0:
        return False

    numerator_half = numerator_quantum / 2
    denominator_half = denominator_quantum / 2
    ratio_half = ratio_quantum / 2
    denominator_min = denominator - denominator_half
    if denominator_min <= 0:
        return False

    numerator_min = max(Decimal("0"), numerator - numerator_half)
    numerator_max = numerator + numerator_half
    denominator_max = denominator + denominator_half
    exact_ratio_min = numerator_min / denominator_max
    exact_ratio_max = numerator_max / denominator_min
    reported_ratio_min = max(Decimal("0"), ratio - ratio_half)
    reported_ratio_max = ratio + ratio_half
    return (
        reported_ratio_max >= exact_ratio_min and reported_ratio_min <= exact_ratio_max
    )


def _iso_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _month_start(value: object) -> date | None:
    if not isinstance(value, str) or len(value) != 7:
        return None
    try:
        parsed = date.fromisoformat(f"{value}-01")
    except ValueError:
        return None
    return parsed if parsed.strftime("%Y-%m") == value else None


def _add_months(value: date, months: int) -> date:
    offset = value.month - 1 + months
    return date(value.year + offset // 12, offset % 12 + 1, 1)


def _expected_month_starts(as_of: str, months: int) -> list[date] | None:
    resolved = _iso_date(as_of)
    if resolved is None or months <= 0:
        return None
    current = resolved.replace(day=1)
    first = _add_months(current, -(months - 1))
    return [_add_months(first, index) for index in range(months)]


def _object(
    payload: object, errors: list[str], name: str = "response"
) -> JSON_OBJECT | None:
    if not isinstance(payload, dict):
        errors.append(f"{name} must be a JSON object")
        return None
    return payload


def _path(payload: Mapping[str, object], dotted: str) -> object:
    value: object = payload
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _require_true(
    payload: Mapping[str, object], paths: tuple[str, ...], errors: list[str]
) -> None:
    for dotted in paths:
        if _path(payload, dotted) is not True:
            errors.append(f"{dotted} must be true")


def validate_health(
    service: str,
    payload: object,
    *,
    ready: bool = False,
    expected_source_history_start: str = DEFAULT_SOURCE_HISTORY_START,
) -> tuple[list[str], JSON_OBJECT]:
    errors: list[str] = []
    obj = _object(payload, errors)
    if obj is None:
        return errors, {}

    expected_status = "ready" if ready else "healthy"
    if obj.get("status") != expected_status:
        errors.append(f"status must be {expected_status!r}")

    source_history_start = obj.get("source_history_start")
    if source_history_start != expected_source_history_start:
        errors.append(
            "source_history_start must equal "
            f"{expected_source_history_start!r}"
        )
    if obj.get("source_history_contract_ready") is not True:
        errors.append("source_history_contract_ready must be true")

    nested_history_paths = {
        "procure": "source_readiness.source_history_start",
        "solvency": "source.source_history_start",
        "pricing": "source.source_history_start",
        "products": "stock_source_readiness.source_history_start",
        "forecast": "data.source_history_start",
    }
    nested_history_path = nested_history_paths.get(service)
    if (
        nested_history_path is not None
        and _path(obj, nested_history_path) != expected_source_history_start
    ):
        errors.append(
            f"{nested_history_path} must equal "
            f"{expected_source_history_start!r}"
        )

    flags: dict[str, tuple[str, ...]] = {
        "reco": ("db_connected", "redis_connected", "business_ready"),
        "procure": (
            "db_connected",
            "redis_connected",
            "business_ready",
            "source_readiness.ready",
        ),
        "nba": (
            "db_connected",
            "mongo_connected",
            "business_ready",
            "source_ready",
            "generation_ready",
        ),
        "solvency": (
            "db_connected",
            "redis_connected",
            "business_ready",
            "source.business_ready",
            "synthetic_drift_ok",
        ),
        "pricing": (
            "db_connected",
            "redis_connected",
            "business_ready",
            "source.business_ready",
        ),
        "products": (
            "db_connected",
            "cache_connected",
            "business_ready",
            "stock_source_readiness.ready",
        ),
        "forecast": (
            "db_connected",
            "cache_connected",
            "business_ready",
            "data.source_ready",
            "data.source_schema_present",
            "data.source_exists",
            "data.source_fresh",
        ),
    }
    _require_true(obj, flags[service], errors)

    details: JSON_OBJECT = {
        "status": obj.get("status"),
        "version": obj.get("version"),
        "model_version": obj.get("model_version"),
        "source_history_start": source_history_start,
    }
    if service == "procure":
        canonical_items = obj.get("canonical_cart_items")
        if not _positive_id(canonical_items):
            errors.append("canonical_cart_items must be a positive integer")
        details["canonical_cart_items"] = canonical_items
        source = obj.get("source_readiness")
        if not isinstance(source, dict):
            errors.append("source_readiness must be an object")
        else:
            fingerprint = source.get("source_fingerprint")
            if not isinstance(fingerprint, str) or not fingerprint:
                errors.append("source_readiness.source_fingerprint must be non-empty")
            details["source_fingerprint"] = fingerprint
    elif service == "nba":
        for field_name in ("manager_count", "task_count", "active_task_count"):
            value = obj.get(field_name)
            if not _nonnegative_int(value):
                errors.append(f"{field_name} must be a non-negative integer")
            details[field_name] = value
        if obj.get("manager_count") == 0:
            errors.append("manager_count must be positive")
        if obj.get("task_count") == 0:
            errors.append("task_count must be positive")
        if not _positive_id(obj.get("synthetic_product_count")):
            errors.append("synthetic_product_count must be positive")
        generation_managers = obj.get("last_generation_managers")
        generation_ok = obj.get("last_generation_ok")
        generation_failed = obj.get("last_generation_failed")
        if not _positive_id(generation_managers):
            errors.append("last_generation_managers must be positive")
        if generation_ok != generation_managers:
            errors.append("last_generation_ok must equal last_generation_managers")
        if generation_failed != 0:
            errors.append("last_generation_failed must equal zero")
        for field_name in ("last_generation_at", "latest_task_refresh_at"):
            value = obj.get(field_name)
            try:
                parsed = (
                    datetime.fromisoformat(value) if isinstance(value, str) else None
                )
            except ValueError:
                parsed = None
            if parsed is None or parsed.tzinfo is None:
                errors.append(f"{field_name} must be a timezone-aware ISO timestamp")
    elif service == "forecast":
        data = obj.get("data")
        if not isinstance(data, dict):
            errors.append("data must be an object")
        else:
            for field_name in (
                "canonical_row_count",
                "history_row_count",
                "history_product_count",
                "history_client_count",
            ):
                value = data.get(field_name)
                if not _positive_id(value):
                    errors.append(f"data.{field_name} must be a positive integer")
            if data.get("invalid_value_row_count") != 0:
                errors.append("data.invalid_value_row_count must equal zero")
            details["canonical_row_count"] = data.get("canonical_row_count")
            details["history_row_count"] = data.get("history_row_count")
    elif service == "solvency":
        drift = obj.get("model_drift")
        if not isinstance(drift, dict):
            errors.append("model_drift must be an object")
        else:
            if drift.get("drift_level") != "ok":
                errors.append("model_drift.drift_level must be 'ok'")
            if not _finite_number(drift.get("psi_score"), minimum=Decimal("0")):
                errors.append("model_drift.psi_score must be finite and non-negative")

    return errors, details


def validate_recommendation(
    payload: object,
    *,
    customer_id: int,
    expected_as_of: str,
    top_n: int,
    require_nonempty: bool,
) -> tuple[list[str], JSON_OBJECT]:
    errors: list[str] = []
    obj = _object(payload, errors)
    if obj is None:
        return errors, {}
    if obj.get("customer_id") != customer_id:
        errors.append("customer_id identity mismatch")
    if obj.get("as_of_date") != expected_as_of:
        errors.append(f"as_of_date must equal {expected_as_of}")
    if not isinstance(obj.get("model_version"), str) or not obj.get("model_version"):
        errors.append("model_version must be non-empty")
    if not isinstance(obj.get("cached"), bool):
        errors.append("cached must be a boolean")
    segment = obj.get("segment")
    if not isinstance(segment, str) or not segment:
        errors.append("segment must be non-empty")
    rows = obj.get("recommendations")
    if not isinstance(rows, list):
        errors.append("recommendations must be an array")
        return errors, {}
    if obj.get("count") != len(rows):
        errors.append("count must equal recommendations length")
    if len(rows) > top_n:
        errors.append("recommendations length exceeds requested top_n")
    if require_nonempty and not rows:
        errors.append("recommendations must not be empty")

    product_ids: list[int] = []
    ranks: list[int] = []
    discovery_count = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"recommendations[{index}] must be an object")
            continue
        product_id = row.get("product_id")
        rank = row.get("rank")
        if not _positive_id(product_id):
            errors.append(f"recommendations[{index}].product_id must be positive")
        else:
            product_ids.append(product_id)
        if not _positive_id(rank):
            errors.append(f"recommendations[{index}].rank must be positive")
        else:
            ranks.append(rank)
        if not _finite_number(row.get("score"), minimum=Decimal("0")):
            errors.append(
                f"recommendations[{index}].score must be finite and non-negative"
            )
        if row.get("source") not in {"repurchase", "discovery"}:
            errors.append(f"recommendations[{index}].source is invalid")
        if row.get("segment") != segment:
            errors.append(
                f"recommendations[{index}].segment must equal response segment"
            )
        if row.get("source") == "discovery":
            discovery_count += 1
    if len(product_ids) != len(set(product_ids)):
        errors.append("recommendation product_id values must be unique")
    if ranks != list(range(1, len(rows) + 1)):
        errors.append("recommendation ranks must be contiguous and one-based")
    if obj.get("discovery_count") != discovery_count:
        errors.append("discovery_count must equal rows with source=discovery")
    if not _finite_number(obj.get("latency_ms"), minimum=Decimal("0")):
        errors.append("latency_ms must be finite and non-negative")
    if not _finite_number(obj.get("precision_estimate"), minimum=Decimal("0")):
        errors.append("precision_estimate must be finite and non-negative")
    elif _decimal(obj.get("precision_estimate")) > Decimal("1"):
        errors.append("precision_estimate must not exceed one")
    return errors, {"customer_id": obj.get("customer_id"), "count": len(rows)}


def validate_nba_inbox(
    payload: object,
    *,
    manager_id: int,
    manager_net_uid: str,
    limit: int,
    require_nonempty: bool,
) -> tuple[list[str], JSON_OBJECT]:
    errors: list[str] = []
    obj = _object(payload, errors)
    if obj is None:
        return errors, {}
    if obj.get("manager_id") != manager_id:
        errors.append("manager_id identity mismatch")
    if obj.get("manager_net_uid") != manager_net_uid.casefold():
        errors.append("manager_net_uid identity mismatch")
    rows = obj.get("tasks")
    if not isinstance(rows, list):
        errors.append("tasks must be an array")
        return errors, {}
    if obj.get("count") != len(rows):
        errors.append("count must equal tasks length")
    if len(rows) > limit:
        errors.append("tasks length exceeds requested limit")
    if require_nonempty and not rows:
        errors.append("tasks must not be empty")

    keys: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"tasks[{index}] must be an object")
            continue
        key = row.get("task_key")
        if not isinstance(key, str) or not key:
            errors.append(f"tasks[{index}].task_key must be non-empty")
        else:
            keys.append(key)
        if row.get("manager_id") != manager_id:
            errors.append(f"tasks[{index}].manager_id identity mismatch")
        if not _positive_id(row.get("client_id")):
            errors.append(f"tasks[{index}].client_id must be positive")
        for name in ("priority", "p_outcome", "expected_value", "ev_score"):
            if not _finite_number(row.get(name), minimum=Decimal("0")):
                errors.append(f"tasks[{index}].{name} must be finite and non-negative")
        if not _cents(row.get("expected_value")):
            errors.append(f"tasks[{index}].expected_value must be exact EUR cents")
    if len(keys) != len(set(keys)):
        errors.append("task_key values must be unique")
    return errors, {"manager_id": obj.get("manager_id"), "count": len(rows)}


def validate_solvency_score(
    payload: object,
    *,
    client_id: int,
    client_net_uid: str,
    expected_as_of: str,
    window_months: int,
) -> tuple[list[str], JSON_OBJECT]:
    errors: list[str] = []
    obj = _object(payload, errors)
    if obj is None:
        return errors, {}
    if obj.get("client_id") != client_id:
        errors.append("client_id identity mismatch")
    if obj.get("client_net_uid") != client_net_uid.casefold():
        errors.append("client_net_uid identity mismatch")
    if obj.get("as_of_date") != expected_as_of:
        errors.append(f"as_of_date must equal {expected_as_of}")
    if obj.get("window_months") != window_months:
        errors.append("window_months identity mismatch")
    if not isinstance(obj.get("model_version"), str) or not obj.get("model_version"):
        errors.append("model_version must be non-empty")
    if obj.get("applicable") is not True:
        errors.append("applicable must be true for the release fixture")
    score = obj.get("score")
    if not _is_strict_int(score) or not 0 <= score <= 100:
        errors.append("score must be an integer from 0 through 100")
    if obj.get("rating") not in {"A", "B", "C", "D"}:
        errors.append("rating must be A, B, C, or D")
    pd = obj.get("pd")
    if not _finite_number(pd, minimum=Decimal("0")) or (
        _decimal(pd) is not None and _decimal(pd) > Decimal("1")
    ):
        errors.append("pd must be finite and within 0..1")

    currencies = obj.get("currency_breakdown")
    if currencies is not None:
        if not isinstance(currencies, list):
            errors.append("currency_breakdown must be an array or null")
        else:
            currency_ids: list[int] = []
            for index, row in enumerate(currencies):
                if not isinstance(row, dict):
                    errors.append(f"currency_breakdown[{index}] must be an object")
                    continue
                currency_id = row.get("currency_id")
                if not _positive_id(currency_id):
                    errors.append(
                        f"currency_breakdown[{index}].currency_id must be positive"
                    )
                else:
                    currency_ids.append(currency_id)
                for key in ("turnover_eur", "exposure_eur"):
                    if not _cents(row.get(key)):
                        errors.append(
                            f"currency_breakdown[{index}].{key} must be exact EUR cents"
                        )
            if len(currency_ids) != len(set(currency_ids)):
                errors.append("currency_breakdown currency_id values must be unique")
    return errors, {
        "client_id": obj.get("client_id"),
        "score": score,
        "rating": obj.get("rating"),
    }


def validate_solvency_charts(
    payload: object,
    *,
    client_id: int,
    expected_as_of: str,
    window_months: int,
) -> tuple[list[str], JSON_OBJECT]:
    errors: list[str] = []
    obj = _object(payload, errors)
    if obj is None:
        return errors, {}
    if obj.get("client_id") != client_id:
        errors.append("client_id identity mismatch")
    if obj.get("as_of_date") != expected_as_of:
        errors.append(f"charts.as_of_date must equal {expected_as_of}")
    if obj.get("window_months") != window_months:
        errors.append("charts.window_months identity mismatch")
    if not isinstance(obj.get("model_version"), str) or not obj.get("model_version"):
        errors.append("charts.model_version must be non-empty")
    if obj.get("applicable") is not True:
        errors.append("charts.applicable must be true")

    gauge = obj.get("limit_utilization_gauge")
    if not isinstance(gauge, dict):
        errors.append("limit_utilization_gauge must be an object")
    else:
        for field_name in ("value", "threshold_soft", "threshold_hard"):
            if not _finite_number(gauge.get(field_name), minimum=Decimal("0")):
                errors.append(
                    f"limit_utilization_gauge.{field_name} must be finite and non-negative"
                )
        soft = _decimal(gauge.get("threshold_soft"))
        hard = _decimal(gauge.get("threshold_hard"))
        if soft is not None and hard is not None and soft > hard:
            errors.append(
                "limit_utilization_gauge.threshold_soft must not exceed threshold_hard"
            )
        if gauge.get("label") != "limit_utilization":
            errors.append("limit_utilization_gauge.label must equal limit_utilization")

    donut = obj.get("payment_discipline_donut")
    expected_donut_labels = ["settled", "current", "overdue"]
    donut_labels: list[str] = []
    if not isinstance(donut, list) or not donut:
        errors.append("payment_discipline_donut must be a non-empty array")
    else:
        for index, row in enumerate(donut):
            if not isinstance(row, dict):
                errors.append(f"payment_discipline_donut[{index}] must be an object")
                continue
            label = row.get("label")
            if not isinstance(label, str) or not label:
                errors.append(
                    f"payment_discipline_donut[{index}].label must be non-empty"
                )
            else:
                donut_labels.append(label)
            if not _nonnegative_int(row.get("count")):
                errors.append(
                    f"payment_discipline_donut[{index}].count must be non-negative"
                )
        if donut_labels != expected_donut_labels:
            errors.append(
                "payment_discipline_donut labels must be settled,current,overdue in order"
            )

    aging = obj.get("open_invoice_aging_bars")
    expected_aging_buckets = ["0-30", "31-60", "61-90", "90+"]
    aging_buckets: list[str] = []
    if not isinstance(aging, list) or not aging:
        errors.append("open_invoice_aging_bars must be a non-empty array")
    else:
        for index, row in enumerate(aging):
            if not isinstance(row, dict):
                errors.append(f"open_invoice_aging_bars[{index}] must be an object")
                continue
            bucket = row.get("bucket")
            if not isinstance(bucket, str) or not bucket:
                errors.append(
                    f"open_invoice_aging_bars[{index}].bucket must be non-empty"
                )
            else:
                aging_buckets.append(bucket)
            if not _nonnegative_int(row.get("count")):
                errors.append(
                    f"open_invoice_aging_bars[{index}].count must be non-negative"
                )
            if not _cents(row.get("amount_eur")):
                errors.append(
                    f"open_invoice_aging_bars[{index}].amount_eur must be exact EUR cents"
                )
        if aging_buckets != expected_aging_buckets:
            errors.append(
                "open_invoice_aging_bars buckets must be 0-30,31-60,61-90,90+ in order"
            )

    collection_money = {
        "turnover_vs_exposure": ("turnover_eur", "exposure_eur"),
        "turnover_trend": ("turnover_eur",),
    }
    period_rows: dict[str, list[dict[str, object]]] = {}
    collection_periods: dict[str, list[str]] = {}
    for collection, money_fields in collection_money.items():
        rows = obj.get(collection)
        if not isinstance(rows, list) or not rows:
            errors.append(f"{collection} must be a non-empty array")
            continue
        periods: list[str] = []
        valid_rows: list[dict[str, object]] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append(f"{collection}[{index}] must be an object")
                continue
            valid_rows.append(row)
            period = row.get("period")
            if _month_start(period) is None:
                errors.append(f"{collection}[{index}].period must be YYYY-MM")
            else:
                periods.append(str(period))
            for money_field in money_fields:
                if not _cents(row.get(money_field)):
                    errors.append(
                        f"{collection}[{index}].{money_field} must be exact EUR cents"
                    )
        if len(periods) != len(set(periods)):
            errors.append(f"{collection} periods must be unique")
        if periods != sorted(periods):
            errors.append(f"{collection} periods must be ordered ascending")
        period_rows[collection] = valid_rows
        collection_periods[collection] = periods

    exposure_periods = collection_periods.get("turnover_vs_exposure")
    trend_periods = collection_periods.get("turnover_trend")
    if exposure_periods is not None and trend_periods is not None:
        if exposure_periods != trend_periods:
            errors.append("turnover chart periods must match exactly")
        else:
            exposure_rows = period_rows["turnover_vs_exposure"]
            trend_rows = period_rows["turnover_trend"]
            if len(exposure_rows) != len(trend_rows):
                errors.append("turnover chart row counts must match exactly")
            else:
                for index, (exposure_row, trend_row) in enumerate(
                    zip(exposure_rows, trend_rows, strict=True)
                ):
                    if _decimal(exposure_row.get("turnover_eur")) != _decimal(
                        trend_row.get("turnover_eur")
                    ):
                        errors.append(
                            f"turnover chart turnover_eur mismatch at index {index}"
                        )

    sparkline = obj.get("score_sparkline")
    expected_starts = _expected_month_starts(expected_as_of, window_months)
    expected_score_periods = (
        [value.strftime("%Y-%m") for value in expected_starts]
        if expected_starts is not None
        else []
    )
    score_periods: list[str] = []
    if not isinstance(sparkline, list) or not sparkline:
        errors.append("score_sparkline must be a non-empty array")
    else:
        if len(sparkline) != window_months:
            errors.append("score_sparkline length must equal window_months")
        for index, row in enumerate(sparkline):
            if not isinstance(row, dict):
                errors.append(f"score_sparkline[{index}] must be an object")
                continue
            period = row.get("period")
            if _month_start(period) is None:
                errors.append(f"score_sparkline[{index}].period must be YYYY-MM")
            else:
                score_periods.append(str(period))
            score = row.get("score")
            if not _is_strict_int(score) or not 0 <= score <= 100:
                errors.append(f"score_sparkline[{index}].score must be within 0..100")
        if score_periods != expected_score_periods:
            errors.append(
                "score_sparkline periods must be the exact contiguous requested window"
            )

    if obj.get("aging_over_time_heatmap") != "pending":
        errors.append("aging_over_time_heatmap must equal pending")
    return errors, {
        "client_id": obj.get("client_id"),
        "turnover_periods": len(trend_periods or []),
        "score_periods": len(score_periods),
    }


def validate_pricing(
    payload: object,
    *,
    product_id: int,
    product_net_uid: str,
    agreement_net_uid: str,
    expected_as_of: str,
) -> tuple[list[str], JSON_OBJECT]:
    errors: list[str] = []
    obj = _object(payload, errors)
    if obj is None:
        return errors, {}
    if obj.get("product_id") != product_id:
        errors.append("product_id identity mismatch")
    if obj.get("product_net_uid") != product_net_uid.casefold():
        errors.append("product_net_uid identity mismatch")
    agreement = obj.get("client_agreement_netuid")
    if (
        not isinstance(agreement, str)
        or agreement.casefold() != agreement_net_uid.casefold()
    ):
        errors.append("client_agreement_netuid identity mismatch")
    if obj.get("currency") != "EUR":
        errors.append("currency must equal EUR")
    if obj.get("as_of_date") != expected_as_of:
        errors.append(f"as_of_date must equal {expected_as_of}")
    if not isinstance(obj.get("model_version"), str) or not obj.get("model_version"):
        errors.append("model_version must be non-empty")

    required_money = (
        "baseline_price",
        "recommended_price",
        "price_floor",
        "unit_cost_eur",
    )
    money: dict[str, Decimal] = {}
    for name in required_money:
        value = obj.get(name)
        if not _cents(value):
            errors.append(f"{name} must be present as exact non-negative EUR cents")
        elif (parsed := _decimal(value)) is not None:
            money[name] = parsed
    elastic = obj.get("elastic_optimal_price")
    if elastic is not None and not _cents(elastic):
        errors.append("elastic_optimal_price must be null or exact EUR cents")

    if {"recommended_price", "price_floor"} <= money.keys():
        if money["recommended_price"] < money["price_floor"]:
            errors.append("recommended_price must not be below price_floor")
    if {"recommended_price", "baseline_price"} <= money.keys():
        if money["recommended_price"] > money["baseline_price"]:
            allowed_loss_flag = (
                money.get("price_floor", Decimal("-1")) > money["baseline_price"]
                and money["recommended_price"] == money["price_floor"]
                and obj.get("rationale") == "below-margin-loss-flag"
            )
            if not allowed_loss_flag:
                errors.append(
                    "recommended_price may exceed baseline_price only at a higher "
                    "margin floor with below-margin-loss-flag"
                )

    discount = obj.get("suggested_discount_pct")
    if not _finite_number(discount, minimum=Decimal("0")) or (
        _decimal(discount) is not None and _decimal(discount) > Decimal("100")
    ):
        errors.append("suggested_discount_pct must be finite and within 0..100")
    band = obj.get("discount_band")
    if not isinstance(band, dict):
        errors.append("discount_band must be an object")
    else:
        values = [
            _decimal(band.get(key)) for key in ("min_pct", "target_pct", "max_pct")
        ]
        if any(value is None for value in values):
            errors.append("discount_band values must be finite")
        else:
            if not values[0] <= values[1] <= values[2]:
                errors.append("discount_band must be monotone")
            if values[0] < 0 or values[2] > 100:
                errors.append("discount_band values must be within 0..100")

    peer = obj.get("peer_band")
    if not isinstance(peer, dict):
        errors.append("peer_band must be an object")
    else:
        if not _nonnegative_int(peer.get("n")):
            errors.append("peer_band.n must be a non-negative integer")
        percentiles = [
            _decimal(peer.get(key))
            for key in ("p25", "p50", "p75")
            if peer.get(key) is not None
        ]
        if any(value is None for value in percentiles):
            errors.append("peer_band percentiles must be finite")
        elif percentiles != sorted(percentiles):
            errors.append("peer_band percentiles must be monotone")
        for key in ("p25", "p50", "p75"):
            if peer.get(key) is not None and not _cents(peer.get(key)):
                errors.append(f"peer_band.{key} must be exact EUR cents")

    return errors, {
        "product_id": obj.get("product_id"),
        "currency": obj.get("currency"),
        "recommended_price": obj.get("recommended_price"),
    }


def validate_product_regions(
    payload: object,
    *,
    product_id: int,
    expected_as_of: str,
    expected_window_days: int,
    limit: int,
    require_nonempty: bool,
) -> tuple[list[str], JSON_OBJECT]:
    errors: list[str] = []
    obj = _object(payload, errors)
    if obj is None:
        return errors, {}
    if obj.get("product_id") != product_id:
        errors.append("product_id identity mismatch")
    if obj.get("as_of") != expected_as_of:
        errors.append(f"product regions as_of must equal {expected_as_of}")
    if obj.get("window_days") != expected_window_days:
        errors.append("product regions window_days identity mismatch")
    rows = obj.get("regions")
    if not isinstance(rows, list):
        errors.append("regions must be an array")
        return errors, {}
    if obj.get("count") != len(rows):
        errors.append("count must equal regions length")
    if len(rows) > limit:
        errors.append("regions length exceeds requested limit")
    if require_nonempty and not rows:
        errors.append("regions must not be empty")
    region_ids: list[int] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"regions[{index}] must be an object")
            continue
        region_id = row.get("region_id")
        if not _positive_id(region_id):
            errors.append(f"regions[{index}].region_id must be positive")
        else:
            region_ids.append(region_id)
        if not _finite_number(row.get("regional_units"), minimum=Decimal("0")):
            errors.append(
                f"regions[{index}].regional_units must be finite and non-negative"
            )
        if not _cents(row.get("regional_revenue_eur")):
            errors.append(
                f"regions[{index}].regional_revenue_eur must be exact EUR cents"
            )
        for name in ("regional_order_count", "regional_client_count"):
            if not _nonnegative_int(row.get(name)):
                errors.append(f"regions[{index}].{name} must be a non-negative integer")
    if len(region_ids) != len(set(region_ids)):
        errors.append("region_id values must be unique")
    return errors, {"product_id": obj.get("product_id"), "count": len(rows)}


def validate_product_analytics(
    payload: object,
    *,
    product_id: int,
    expected_as_of: str,
    months: int,
) -> tuple[list[str], JSON_OBJECT]:
    errors: list[str] = []
    obj = _object(payload, errors)
    if obj is None:
        return errors, {}
    if obj.get("product_id") != product_id:
        errors.append("product analytics product_id identity mismatch")
    if obj.get("as_of") != expected_as_of:
        errors.append(f"product analytics as_of must equal {expected_as_of}")
    if not isinstance(obj.get("model_version"), str) or not obj.get("model_version"):
        errors.append("product analytics model_version must be non-empty")

    expected_starts = _expected_month_starts(expected_as_of, months)
    expected_window_start = expected_starts[0] if expected_starts else None
    window = obj.get("window")
    if not isinstance(window, dict):
        errors.append("product analytics window must be an object")
    else:
        if window.get("months") != months:
            errors.append("product analytics window.months identity mismatch")
        if window.get("end_exclusive") != expected_as_of:
            errors.append("product analytics window.end_exclusive must equal as_of")
        if window.get("includes_partial_current_month") is not True:
            errors.append(
                "product analytics window.includes_partial_current_month must be true"
            )
        start = _iso_date(window.get("start"))
        end = _iso_date(window.get("end_exclusive"))
        if start != expected_window_start:
            errors.append(
                "product analytics window.start must match the requested dense window"
            )
        if end != _iso_date(expected_as_of):
            errors.append(
                "product analytics window.end_exclusive must be the exact as_of date"
            )

    snapshot = obj.get("snapshot")
    if not isinstance(snapshot, dict):
        errors.append("product analytics snapshot must be an object")
    else:
        if snapshot.get("product_id") != product_id:
            errors.append("product analytics snapshot product_id identity mismatch")
        if snapshot.get("found") is not True:
            errors.append("product analytics fixture must be found in the portfolio")

    rows = obj.get("sales_series")
    if not isinstance(rows, list):
        errors.append("product analytics sales_series must be an array")
        rows = []
    elif len(rows) != months:
        errors.append(
            "product analytics sales_series length must equal requested months"
        )
    month_labels: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"product analytics sales_series[{index}] must be an object")
            continue
        label = row.get("month")
        try:
            period_start = _iso_date(row.get("period_start"))
            period_end = _iso_date(row.get("period_end_exclusive"))
        except (TypeError, ValueError):
            period_start = period_end = None
        if (
            not isinstance(label, str)
            or period_start is None
            or label != period_start.strftime("%Y-%m")
        ):
            errors.append(
                f"product analytics sales_series[{index}] month/period_start mismatch"
            )
        else:
            month_labels.append(label)
        expected_start = (
            expected_starts[index]
            if expected_starts is not None and index < len(expected_starts)
            else None
        )
        expected_end = (
            _add_months(expected_start, 1)
            if expected_start is not None and index < len(rows) - 1
            else _iso_date(expected_as_of)
        )
        if period_start != expected_start:
            errors.append(
                f"product analytics sales_series[{index}] is not in exact month order"
            )
        if period_end != expected_end:
            errors.append(
                f"product analytics sales_series[{index}] period_end_exclusive is not contiguous"
            )
        is_last = index == len(rows) - 1
        if row.get("is_complete") is not (not is_last):
            errors.append(
                f"product analytics sales_series[{index}].is_complete is inconsistent"
            )
        if is_last and str(row.get("period_end_exclusive")) != expected_as_of:
            errors.append(
                "product analytics final period_end_exclusive must equal as_of"
            )
        units = _decimal(row.get("units"))
        revenue = _decimal(row.get("revenue_eur"))
        order_count = row.get("order_count")
        if not _scaled_nonnegative(row.get("units"), "0.0001"):
            errors.append(
                f"product analytics sales_series[{index}].units must use quantity scale"
            )
        if not _nonnegative_int(order_count):
            errors.append(
                f"product analytics sales_series[{index}].order_count must be non-negative"
            )
        if not _cents(row.get("revenue_eur")):
            errors.append(
                f"product analytics sales_series[{index}].revenue_eur must be exact cents"
            )
        average = row.get("avg_price_eur")
        if units is None or revenue is None:
            pass
        elif units == 0:
            if revenue != 0 or average is not None:
                errors.append(
                    f"product analytics sales_series[{index}] zero-units proof is inconsistent"
                )
        else:
            if not _scaled_nonnegative(average, "0.0001"):
                errors.append(
                    f"product analytics sales_series[{index}].avg_price_eur must use price scale"
                )
            elif (parsed_average := _decimal(average)) is not None:
                if not _rounded_ratio_is_coherent(
                    numerator=revenue,
                    denominator=units,
                    ratio=parsed_average,
                    numerator_quantum=Decimal("0.01"),
                    denominator_quantum=Decimal("0.0001"),
                    ratio_quantum=Decimal("0.0001"),
                ):
                    errors.append(
                        f"product analytics sales_series[{index}].avg_price_eur "
                        "is not coherent with independently rounded revenue_eur / units"
                    )
        if (
            _nonnegative_int(order_count)
            and order_count == 0
            and units is not None
            and revenue is not None
            and (units != 0 or revenue != 0)
        ):
            errors.append(
                f"product analytics sales_series[{index}] has values without an order"
            )
    if len(month_labels) != len(set(month_labels)):
        errors.append("product analytics month values must be unique")

    quality = obj.get("data_quality")
    if not isinstance(quality, dict):
        errors.append("product analytics data_quality must be an object")
    else:
        expected_quality = {
            "sales_date_field": "Order.Created",
            "sales_validity_filter": "OrderItem.IsValidForCurrentSale = 1",
            "sales_window_end": "exclusive",
            "zero_months_filled": True,
            "stock_is_current": True,
            "stock_history_available": False,
        }
        for key, value in expected_quality.items():
            if quality.get(key) != value:
                errors.append(f"product analytics data_quality.{key} is invalid")
    return errors, {
        "product_id": obj.get("product_id"),
        "months": len(rows),
        "as_of": obj.get("as_of"),
    }


def validate_forecast(
    payload: object,
    *,
    client_net_id: str | None,
    product_net_id: str | None,
    expected_as_of: str,
    require_nonempty: bool,
) -> tuple[list[str], JSON_OBJECT]:
    errors: list[str] = []
    obj = _object(payload, errors)
    if obj is None:
        return errors, {}
    meta = obj.get("meta")
    if not isinstance(meta, dict):
        errors.append("meta must be an object")
        return errors, {}
    requested = meta.get("requested")
    resolved = meta.get("resolved")
    identity = meta.get("identity")
    history = meta.get("history")
    if not all(
        isinstance(item, dict) for item in (requested, resolved, identity, history)
    ):
        errors.append("meta requested/resolved/identity/history must be objects")
        return errors, {}
    expected_client = client_net_id.casefold() if client_net_id else None
    expected_product = product_net_id.casefold() if product_net_id else None
    if requested.get("client_net_id") != expected_client:
        errors.append("requested.client_net_id identity mismatch")
    if requested.get("product_net_id") != expected_product:
        errors.append("requested.product_net_id identity mismatch")
    if client_net_id and (
        identity.get("client") != "resolved"
        or not _positive_id(resolved.get("client_id"))
        or resolved.get("client_net_id") != expected_client
    ):
        errors.append("client identity must resolve exactly")
    if product_net_id and (
        identity.get("product") != "resolved"
        or not _positive_id(resolved.get("product_id"))
        or resolved.get("product_net_id") != expected_product
    ):
        errors.append("product identity must resolve exactly")
    if meta.get("status") not in {"ready", "partial"}:
        errors.append("meta.status must be ready or partial for a release fixture")
    if meta.get("as_of") != expected_as_of:
        errors.append(f"meta.as_of must equal {expected_as_of}")
    if meta.get("requested_as_of") != expected_as_of:
        errors.append(f"meta.requested_as_of must equal {expected_as_of}")
    if meta.get("currency") != "EUR":
        errors.append("meta.currency must equal EUR")
    for field_name in ("model_version", "source_fingerprint"):
        if not isinstance(meta.get(field_name), str) or not meta.get(field_name):
            errors.append(f"meta.{field_name} must be non-empty")
    horizon = meta.get("horizon_months")
    if not _positive_id(horizon):
        errors.append("meta.horizon_months must be positive")
        horizon = 0
    history_window = meta.get("history_window_months")
    minimum_history = meta.get("minimum_non_zero_months")
    if not _positive_id(history_window):
        errors.append("meta.history_window_months must be positive")
        history_window = 0
    if not _positive_id(minimum_history):
        errors.append("meta.minimum_non_zero_months must be positive")
        minimum_history = 0
    if minimum_history > history_window:
        errors.append(
            "meta.minimum_non_zero_months must not exceed history_window_months"
        )

    populated = 0
    for key in ("ByClient", "ByProduct", "ByClientAndProduct"):
        rows = obj.get(key)
        history_item = history.get(key)
        if not isinstance(rows, list):
            errors.append(f"{key} must be an array")
            continue
        if not isinstance(history_item, dict):
            errors.append(f"meta.history.{key} must be an object")
            continue
        if rows:
            populated += 1
            if len(rows) != horizon:
                errors.append(f"{key} length must equal horizon_months")
        months: list[str] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append(f"{key}[{index}] must be an object")
                continue
            if set(row) != {"SaleAmount", "MonthNameUK"}:
                errors.append(f"{key}[{index}] has an unexpected shape")
            if not _cents(row.get("SaleAmount")):
                errors.append(f"{key}[{index}].SaleAmount must be exact EUR cents")
            month = row.get("MonthNameUK")
            if not isinstance(month, str) or not month:
                errors.append(f"{key}[{index}].MonthNameUK must be non-empty")
            else:
                months.append(month)
        if len(months) != len(set(months)):
            errors.append(f"{key} MonthNameUK values must be unique")
        total = history_item.get("total_eur")
        if not _cents(total):
            errors.append(f"meta.history.{key}.total_eur must be exact EUR cents")
        month_count = history_item.get("month_count")
        non_zero = history_item.get("non_zero_month_count")
        status = history_item.get("status")
        if status not in {
            "not_requested",
            "unknown_identity",
            "excluded_synthetic",
            "insufficient_history",
            "sufficient",
        }:
            errors.append(f"meta.history.{key}.status is invalid")
        if not _nonnegative_int(month_count) or not _nonnegative_int(non_zero):
            errors.append(f"meta.history.{key} counts must be non-negative integers")
        else:
            if non_zero > month_count:
                errors.append(
                    f"meta.history.{key}.non_zero_month_count exceeds month_count"
                )
            if month_count > history_window:
                errors.append(
                    f"meta.history.{key}.month_count exceeds history_window_months"
                )
            parsed_total = _decimal(total)
            if parsed_total is not None and (non_zero > 0) != (parsed_total > 0):
                errors.append(
                    f"meta.history.{key} non-zero count and total_eur disagree"
                )
            sufficient = non_zero >= minimum_history
            if history_item.get("sufficient") is not sufficient:
                errors.append(
                    f"meta.history.{key}.sufficient disagrees with minimum history"
                )
            if status == "sufficient" and not sufficient:
                errors.append(f"meta.history.{key}.status is falsely sufficient")
            if status == "insufficient_history" and sufficient:
                errors.append(f"meta.history.{key}.status is falsely insufficient")
        if rows and history_item.get("status") != "sufficient":
            errors.append(f"{key} has points without sufficient history")
        if not rows and history_item.get("status") == "sufficient":
            errors.append(f"{key} is empty despite sufficient history")
    if require_nonempty and populated == 0:
        errors.append("at least one forecast series must be populated")
    return errors, {
        "status": meta.get("status"),
        "horizon_months": horizon,
        "populated_series": populated,
    }


def validate_procurement_artifact(
    payload: object,
    *,
    expected_as_of: str,
    canonical_cart_items: int | None,
    expected_source_fingerprint: str | None,
) -> tuple[list[str], JSON_OBJECT]:
    errors: list[str] = []
    obj = _object(payload, errors, "procurement reconciliation artifact")
    if obj is None:
        return errors, {}
    if obj.get("schema_version") != 1:
        errors.append("schema_version must equal 1")
    if (
        obj.get("ok") is not True
        or obj.get("exit_code") != 0
        or obj.get("exit_name") != "exact"
    ):
        errors.append("artifact result must be ok=true, exit_code=0, exit_name='exact'")
    if obj.get("as_of") != expected_as_of:
        errors.append(f"artifact as_of must equal {expected_as_of}")
    before = obj.get("source_epoch_before")
    after = obj.get("source_epoch_after")
    if not isinstance(before, str) or not before or before != after:
        errors.append("source epochs must be non-empty and identical")
    digests = obj.get("plan_digests")
    if (
        not isinstance(digests, list)
        or len(digests) < 2
        or any(not isinstance(item, str) or not item for item in digests)
        or len(set(digests)) != 1
    ):
        errors.append("at least two non-empty identical plan digests are required")

    metrics = obj.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics must be an object")
        return errors, {}
    required_positive = (
        "plan_items",
        "unique_plan_products",
        "products_checked",
        "producer_product_pairs_checked",
        "priced_selected_pairs",
    )
    for name in required_positive:
        if not _positive_id(metrics.get(name)):
            errors.append(f"metrics.{name} must be a positive integer")
    plan_items = metrics.get("plan_items")
    for name in (
        "unique_plan_products",
        "products_checked",
        "producer_product_pairs_checked",
        "priced_selected_pairs",
    ):
        if metrics.get(name) != plan_items:
            errors.append(f"metrics.{name} must equal metrics.plan_items")
    if metrics.get("computed_unpriced_items") != 0:
        errors.append("metrics.computed_unpriced_items must equal zero")
    if metrics.get("consignment_drift_keys") != 0:
        errors.append("metrics.consignment_drift_keys must equal zero")
    if metrics.get("deterministic_builds") is not True:
        errors.append("metrics.deterministic_builds must be true")
    if not _cents(metrics.get("computed_priced_cost_eur")):
        errors.append("metrics.computed_priced_cost_eur must be exact EUR cents")
    qty = _decimal(metrics.get("computed_total_suggested_qty"))
    if qty is None or qty <= 0:
        errors.append(
            "metrics.computed_total_suggested_qty must be finite and positive"
        )
    if canonical_cart_items is not None and plan_items != canonical_cart_items:
        errors.append("health canonical_cart_items must equal reconciled plan_items")

    readiness = obj.get("source_readiness")
    if not isinstance(readiness, dict):
        errors.append("source_readiness must be an object")
    else:
        if readiness.get("ready") is not True:
            errors.append("source_readiness.ready must be true")
        for name in (
            "producer_count",
            "producer_product_pair_count",
            "product_count",
            "sellable_storage_count",
        ):
            if not _positive_id(readiness.get(name)):
                errors.append(f"source_readiness.{name} must be positive")
        if not isinstance(
            readiness.get("source_fingerprint"), str
        ) or not readiness.get("source_fingerprint"):
            errors.append("source_readiness.source_fingerprint must be non-empty")
        elif (
            expected_source_fingerprint is not None
            and readiness["source_fingerprint"] != expected_source_fingerprint
        ):
            errors.append(
                "artifact source_readiness.source_fingerprint must equal health fingerprint"
            )
    issues = obj.get("issues")
    if not isinstance(issues, list):
        errors.append("issues must be an array")
    elif any(
        not isinstance(issue, dict) or issue.get("severity") not in {"warning", "info"}
        for issue in issues
    ):
        errors.append("issues may contain warning/info entries only")
    return errors, {
        "as_of": obj.get("as_of"),
        "plan_items": plan_items,
        "total_suggested_qty": metrics.get("computed_total_suggested_qty"),
        "total_cost_eur": metrics.get("computed_priced_cost_eur"),
        "warning_count": len(issues) if isinstance(issues, list) else None,
    }


def _env_positive_int(env: Mapping[str, str], name: str) -> int | None:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _env_uuid(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return str(uuid.UUID(raw.strip())).lower()
    except ValueError as exc:
        raise ValueError(f"{name} must be a UUID") from exc


def _api_key(env: Mapping[str, str], service: str) -> str | None:
    return env.get(f"AI_FLEET_{service.upper()}_API_KEY") or env.get("AI_FLEET_API_KEY")


def _result_from_call(
    name: str,
    call: Callable[[], object],
    validator: Validator,
) -> tuple[CheckResult, object | None]:
    started = time.monotonic()
    try:
        payload = call()
        errors, details = validator(payload)
    except (GateHttpError, OSError, ValueError) as exc:
        payload = None
        errors, details = [str(exc)], {}
    duration = round((time.monotonic() - started) * 1000, 2)
    return (
        CheckResult(
            name=name,
            status="fail" if errors else "pass",
            duration_ms=duration,
            errors=errors,
            details=details,
        ),
        payload,
    )


def _skip(name: str, reason: str, *, required: bool) -> CheckResult:
    return CheckResult(
        name=name,
        status="fail" if required else "skipped",
        errors=[reason] if required else [],
        details={"reason": reason},
    )


def _load_json_file(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON") from exc


def _semantic_check(
    *,
    spec: ServiceSpec,
    client: HttpClient,
    env: Mapping[str, str],
    expected_as_of: str,
    require_fixtures: bool,
    require_nonempty: bool,
) -> CheckResult:
    base_url = env.get(f"AI_FLEET_{spec.name.upper()}_URL", spec.default_url)
    key = _api_key(env, spec.name)

    if spec.name == "reco":
        customer_id = _env_positive_int(env, "AI_FLEET_RECO_CUSTOMER_ID")
        if customer_id is None:
            return _skip(
                "semantic",
                "AI_FLEET_RECO_CUSTOMER_ID is not configured",
                required=require_fixtures,
            )
        top_n = 25
        return _result_from_call(
            "semantic",
            lambda: client.request(
                base_url=base_url,
                api_key=key,
                method="POST",
                path="/recommend",
                body={
                    "customer_id": customer_id,
                    "as_of_date": expected_as_of,
                    "top_n": top_n,
                    "include_discovery": True,
                    "use_cache": False,
                },
            ),
            lambda payload: validate_recommendation(
                payload,
                customer_id=customer_id,
                expected_as_of=expected_as_of,
                top_n=top_n,
                require_nonempty=require_nonempty,
            ),
        )[0]

    if spec.name == "nba":
        manager_id = _env_positive_int(env, "AI_FLEET_NBA_MANAGER_ID")
        manager_net_uid = _env_uuid(env, "AI_FLEET_NBA_MANAGER_NET_UID")
        if manager_id is None or manager_net_uid is None:
            return _skip(
                "semantic",
                "AI_FLEET_NBA_MANAGER_ID and AI_FLEET_NBA_MANAGER_NET_UID are required",
                required=require_fixtures,
            )
        limit = 50
        return _result_from_call(
            "semantic",
            lambda: client.request(
                base_url=base_url,
                api_key=key,
                method="GET",
                path="/cockpit/inbox",
                query={"manager_net_uid": manager_net_uid, "limit": limit},
            ),
            lambda payload: validate_nba_inbox(
                payload,
                manager_id=manager_id,
                manager_net_uid=manager_net_uid,
                limit=limit,
                require_nonempty=require_nonempty,
            ),
        )[0]

    if spec.name == "solvency":
        client_id = _env_positive_int(env, "AI_FLEET_SOLVENCY_CLIENT_ID")
        client_net_uid = _env_uuid(env, "AI_FLEET_SOLVENCY_CLIENT_NET_UID")
        if client_id is None or client_net_uid is None:
            return _skip(
                "semantic",
                "AI_FLEET_SOLVENCY_CLIENT_ID and "
                "AI_FLEET_SOLVENCY_CLIENT_NET_UID are required",
                required=require_fixtures,
            )
        window_months = 12
        score_result, _ = _result_from_call(
            "score",
            lambda: client.request(
                base_url=base_url,
                api_key=key,
                method="POST",
                path="/score",
                body={
                    "client_id": client_id,
                    "client_net_uid": client_net_uid,
                    "as_of_date": expected_as_of,
                    "window_months": window_months,
                    "use_cache": False,
                },
            ),
            lambda payload: validate_solvency_score(
                payload,
                client_id=client_id,
                client_net_uid=client_net_uid,
                expected_as_of=expected_as_of,
                window_months=window_months,
            ),
        )
        charts_result, _ = _result_from_call(
            "charts",
            lambda: client.request(
                base_url=base_url,
                api_key=key,
                method="GET",
                path=f"/charts/{client_id}",
                query={
                    "as_of_date": expected_as_of,
                    "months": window_months,
                },
            ),
            lambda payload: validate_solvency_charts(
                payload,
                client_id=client_id,
                expected_as_of=expected_as_of,
                window_months=window_months,
            ),
        )
        errors = [
            f"{result.name}: {error}"
            for result in (score_result, charts_result)
            for error in result.errors
        ]
        return CheckResult(
            name="semantic",
            status="fail" if errors else "pass",
            duration_ms=round(score_result.duration_ms + charts_result.duration_ms, 2),
            errors=errors,
            details={
                "score": score_result.details,
                "charts": charts_result.details,
            },
        )

    if spec.name == "pricing":
        product_id = _env_positive_int(env, "AI_FLEET_PRICING_PRODUCT_ID")
        product_net_uid = _env_uuid(env, "AI_FLEET_PRICING_PRODUCT_NET_UID")
        agreement = _env_uuid(env, "AI_FLEET_PRICING_CLIENT_AGREEMENT_NET_UID")
        if product_id is None or product_net_uid is None or agreement is None:
            return _skip(
                "semantic",
                "AI_FLEET_PRICING_PRODUCT_ID, AI_FLEET_PRICING_PRODUCT_NET_UID and "
                "AI_FLEET_PRICING_CLIENT_AGREEMENT_NET_UID are required",
                required=require_fixtures,
            )
        return _result_from_call(
            "semantic",
            lambda: client.request(
                base_url=base_url,
                api_key=key,
                method="POST",
                path="/price",
                body={
                    "product_id": product_id,
                    "product_net_uid": product_net_uid,
                    "client_agreement_net_uid": agreement,
                    "culture": "uk",
                    "with_vat": True,
                    "use_cache": False,
                    "as_of_date": expected_as_of,
                },
            ),
            lambda payload: validate_pricing(
                payload,
                product_id=product_id,
                product_net_uid=product_net_uid,
                agreement_net_uid=agreement,
                expected_as_of=expected_as_of,
            ),
        )[0]

    if spec.name == "products":
        product_id = _env_positive_int(env, "AI_FLEET_PRODUCTS_PRODUCT_ID")
        if product_id is None:
            return _skip(
                "semantic",
                "AI_FLEET_PRODUCTS_PRODUCT_ID is not configured",
                required=require_fixtures,
            )
        limit = 20
        region_window_days = 365
        regions_result, _ = _result_from_call(
            "regions",
            lambda: client.request(
                base_url=base_url,
                api_key=key,
                method="GET",
                path=f"/product/{product_id}/regions",
                query={
                    "as_of_date": expected_as_of,
                    "window_days": region_window_days,
                    "limit": limit,
                },
            ),
            lambda payload: validate_product_regions(
                payload,
                product_id=product_id,
                expected_as_of=expected_as_of,
                expected_window_days=region_window_days,
                limit=limit,
                require_nonempty=require_nonempty,
            ),
        )
        months = 12
        analytics_result, _ = _result_from_call(
            "analytics",
            lambda: client.request(
                base_url=base_url,
                api_key=key,
                method="GET",
                path=f"/product/{product_id}/analytics",
                query={"as_of_date": expected_as_of, "months": months},
            ),
            lambda payload: validate_product_analytics(
                payload,
                product_id=product_id,
                expected_as_of=expected_as_of,
                months=months,
            ),
        )
        errors = [
            f"{result.name}: {error}"
            for result in (regions_result, analytics_result)
            for error in result.errors
        ]
        return CheckResult(
            name="semantic",
            status="fail" if errors else "pass",
            duration_ms=round(
                regions_result.duration_ms + analytics_result.duration_ms,
                2,
            ),
            errors=errors,
            details={
                "regions": regions_result.details,
                "analytics": analytics_result.details,
            },
        )

    if spec.name == "forecast":
        client_net_id = _env_uuid(env, "AI_FLEET_FORECAST_CLIENT_NET_ID")
        product_net_id = _env_uuid(env, "AI_FLEET_FORECAST_PRODUCT_NET_ID")
        if not client_net_id and not product_net_id:
            return _skip(
                "semantic",
                "AI_FLEET_FORECAST_CLIENT_NET_ID or AI_FLEET_FORECAST_PRODUCT_NET_ID "
                "is not configured",
                required=require_fixtures,
            )
        return _result_from_call(
            "semantic",
            lambda: client.request(
                base_url=base_url,
                api_key=key,
                method="GET",
                path="/forecast/sales",
                query={
                    "client_net_id": client_net_id,
                    "product_net_id": product_net_id,
                    "as_of_date": expected_as_of,
                    "use_cache": "false",
                },
            ),
            lambda payload: validate_forecast(
                payload,
                client_net_id=client_net_id,
                product_net_id=product_net_id,
                expected_as_of=expected_as_of,
                require_nonempty=require_nonempty,
            ),
        )[0]

    return _skip("semantic", "procurement uses reconciliation artifact", required=False)


def run_gate(
    *,
    env: Mapping[str, str],
    timeout: float,
    expected_as_of: str,
    procurement_artifact: str | None,
    require_fixtures: bool,
    require_nonempty: bool,
    expected_source_history_start: str = DEFAULT_SOURCE_HISTORY_START,
    client: HttpClient | None = None,
) -> JSON_OBJECT:
    started_wall = datetime.now(KYIV)
    started = time.monotonic()
    http = client or HttpClient(timeout)
    service_results: list[ServiceResult] = []

    for spec in SERVICE_SPECS:
        base_url = env.get(f"AI_FLEET_{spec.name.upper()}_URL", spec.default_url)
        key = _api_key(env, spec.name)
        checks: list[CheckResult] = []
        health_check, health_payload = _result_from_call(
            "health",
            lambda spec=spec, base_url=base_url, key=key: http.request(
                base_url=base_url,
                api_key=key,
                method="GET",
                path="/health",
            ),
            lambda payload, name=spec.name: validate_health(
                name,
                payload,
                expected_source_history_start=expected_source_history_start,
            ),
        )
        checks.append(health_check)

        if spec.ready_path:
            ready_check, _ = _result_from_call(
                "ready",
                lambda spec=spec, base_url=base_url, key=key: http.request(
                    base_url=base_url,
                    api_key=key,
                    method="GET",
                    path=spec.ready_path or "/ready",
                ),
                lambda payload, name=spec.name: validate_health(
                    name,
                    payload,
                    ready=True,
                    expected_source_history_start=expected_source_history_start,
                ),
            )
            checks.append(ready_check)

        if spec.name == "procure":
            canonical_items = None
            health_source_fingerprint = None
            if isinstance(health_payload, dict) and _positive_id(
                health_payload.get("canonical_cart_items")
            ):
                canonical_items = health_payload["canonical_cart_items"]
            if isinstance(health_payload, dict):
                health_source = health_payload.get("source_readiness")
                if isinstance(health_source, dict):
                    fingerprint = health_source.get("source_fingerprint")
                    if isinstance(fingerprint, str) and fingerprint:
                        health_source_fingerprint = fingerprint
            if not procurement_artifact:
                checks.append(
                    _skip(
                        "reconciliation",
                        "--procure-reconciliation or "
                        "AI_FLEET_PROCURE_RECONCILIATION_JSON is required",
                        required=True,
                    )
                )
            else:
                artifact_check, _ = _result_from_call(
                    "reconciliation",
                    lambda path=procurement_artifact: _load_json_file(path),
                    lambda payload: validate_procurement_artifact(
                        payload,
                        expected_as_of=expected_as_of,
                        canonical_cart_items=canonical_items,
                        expected_source_fingerprint=health_source_fingerprint,
                    ),
                )
                checks.append(artifact_check)
        else:
            try:
                checks.append(
                    _semantic_check(
                        spec=spec,
                        client=http,
                        env=env,
                        expected_as_of=expected_as_of,
                        require_fixtures=require_fixtures,
                        require_nonempty=require_nonempty,
                    )
                )
            except ValueError as exc:
                checks.append(
                    CheckResult(name="semantic", status="fail", errors=[str(exc)])
                )

        service_results.append(
            ServiceResult(
                name=spec.name,
                base_url=_base_url_for_report(base_url),
                ok=all(check.status != "fail" for check in checks),
                checks=checks,
            )
        )

    checks = [check for service in service_results for check in service.checks]
    failed_checks = sum(check.status == "fail" for check in checks)
    passed_checks = sum(check.status == "pass" for check in checks)
    skipped_checks = sum(check.status == "skipped" for check in checks)
    ok = failed_checks == 0
    return {
        "schema_version": 1,
        "ok": ok,
        "exit_code": 0 if ok else 1,
        "as_of": expected_as_of,
        "source_history_start": expected_source_history_start,
        "started_at": started_wall.isoformat(),
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "summary": {
            "services_passed": sum(service.ok for service in service_results),
            "services_failed": sum(not service.ok for service in service_results),
            "checks_passed": passed_checks,
            "checks_failed": failed_checks,
            "checks_skipped": skipped_checks,
        },
        "services": [asdict(service) for service in service_results],
        "limitations": [
            "The gate compares API contracts and the independent procurement artifact; "
            "it does not independently query business databases.",
            "Semantic fixtures are required by default; skipped checks are possible only with "
            "the explicit DEV opt-out.",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.set_defaults(
        require_semantic_fixtures=(
            os.environ.get("AI_FLEET_ALLOW_MISSING_SEMANTIC_FIXTURES") != "1"
        )
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("AI_FLEET_TIMEOUT_SECONDS", "15")),
        help="per-request timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--as-of",
        default=os.environ.get("AI_FLEET_AS_OF")
        or datetime.now(KYIV).date().isoformat(),
        help="expected Kyiv business date for reconciliation",
    )
    parser.add_argument(
        "--source-history-start",
        default=(
            os.environ.get("AI_FLEET_SOURCE_HISTORY_START_DATE")
            or os.environ.get("SOURCE_HISTORY_START_DATE")
            or DEFAULT_SOURCE_HISTORY_START
        ),
        help="required common source-history floor (fixed fleet contract: 2025-01-01)",
    )
    parser.add_argument(
        "--procure-reconciliation",
        default=os.environ.get("AI_FLEET_PROCURE_RECONCILIATION_JSON"),
        help="path to a gba-procure reconciliation JSON artifact",
    )
    parser.add_argument(
        "--require-semantic-fixtures",
        dest="require_semantic_fixtures",
        action="store_true",
        help="fail when a service fixture is missing (enabled by default)",
    )
    parser.add_argument(
        "--allow-missing-semantic-fixtures",
        dest="require_semantic_fixtures",
        action="store_false",
        help="DEV only: allow missing service fixtures to be reported as skipped",
    )
    parser.add_argument(
        "--allow-empty-samples",
        action="store_true",
        help="allow deterministic sample entities to return no rows",
    )
    parser.add_argument(
        "--output",
        help="write the same JSON report to this path (stdout is always emitted)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    try:
        datetime.strptime(args.as_of, "%Y-%m-%d")
    except ValueError:
        parser.error("--as-of must be YYYY-MM-DD")
    try:
        source_history_start = date.fromisoformat(args.source_history_start)
    except (TypeError, ValueError):
        parser.error("--source-history-start must be YYYY-MM-DD")
    if source_history_start.isoformat() != args.source_history_start:
        parser.error("--source-history-start must be YYYY-MM-DD")
    if args.source_history_start != DEFAULT_SOURCE_HISTORY_START:
        parser.error(
            "--source-history-start must equal the fleet contract "
            f"{DEFAULT_SOURCE_HISTORY_START}"
        )
    report = run_gate(
        env=os.environ,
        timeout=args.timeout,
        expected_as_of=args.as_of,
        procurement_artifact=args.procure_reconciliation,
        require_fixtures=args.require_semantic_fixtures,
        require_nonempty=not args.allow_empty_samples,
        expected_source_history_start=args.source_history_start,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        try:
            Path(args.output).write_text(f"{encoded}\n", encoding="utf-8")
        except OSError as exc:
            report["ok"] = False
            report["exit_code"] = 1
            report["output_error"] = str(exc)
            encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(encoded)
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
