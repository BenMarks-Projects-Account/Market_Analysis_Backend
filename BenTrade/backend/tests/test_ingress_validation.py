from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.api.routes_reports import _sanitize_finite
from app.services.base_data_service import BaseDataService
from app.services.trade_lifecycle_service import TradeLifecycleService


class _DummySettings:
    TRADIER_TOKEN = "x"
    FINNHUB_KEY = "x"
    FRED_KEY = "x"
    POLYGON_API_KEY = "x"


class _DummyClient:
    def __init__(self):
        self.settings = _DummySettings()


def _make_service() -> BaseDataService:
    return BaseDataService(
        tradier_client=_DummyClient(),
        finnhub_client=_DummyClient(),
        fred_client=_DummyClient(),
        polygon_client=_DummyClient(),
    )


def test_symbol_normalization_accepts_and_rejects() -> None:
    svc = _make_service()
    assert svc._normalize_symbol("spy") == "SPY"
    assert svc._normalize_symbol("BRK.B") == "BRK.B"
    assert svc._normalize_symbol("") is None
    assert svc._normalize_symbol("TOO_LONG_SYMBOL") is None
    assert svc._normalize_symbol("A$") is None


def test_expiration_parse_and_dte() -> None:
    svc = _make_service()
    future = (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()

    normalized, dte = svc._parse_expiration(future)
    assert normalized == future
    assert dte is not None and dte >= 0

    old, old_dte = svc._parse_expiration("2000-01-01")
    assert old is None
    assert old_dte is not None and old_dte < 0

    bad, bad_dte = svc._parse_expiration("2026/01/01")
    assert bad is None
    assert bad_dte is None


def test_quote_sanity_cleans_invalid_bid_ask() -> None:
    svc = _make_service()

    cleaned, warnings = svc._validate_quote_sanity("tradier", {"bid": -1, "ask": 1.2})
    assert cleaned["bid"] is None
    assert cleaned["ask"] == 1.2
    assert warnings

    cleaned2, warnings2 = svc._validate_quote_sanity("tradier", {"bid": 1.3, "ask": 1.2})
    assert cleaned2["bid"] is None
    assert cleaned2["ask"] is None
    assert "quote ask_lt_bid" in warnings2


def test_normalize_chain_applies_bounds_and_filters() -> None:
    svc = _make_service()
    future = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()

    rows = [
        {
            "type": "put",
            "strike": 100,
            "expiration": future,
            "bid": 1.0,
            "ask": 1.1,
            "open_interest": 100,
            "volume": 10,
            "greeks": {"delta": -0.2},
        },
        {
            "type": "put",
            "strike": 101,
            "expiration": future,
            "bid": 1.2,
            "ask": 1.1,
            "open_interest": 100,
            "volume": 10,
            "greeks": {"delta": -0.2},
        },
        {
            "type": "call",
            "strike": 102,
            "expiration": future,
            "bid": 0.9,
            "ask": 1.0,
            "open_interest": -5,
            "volume": -1,
            "greeks": {"delta": 1.8},
        },
        {
            "type": "call",
            "strike": 103,
            "expiration": "2000-01-01",
            "bid": 0.9,
            "ask": 1.0,
        },
    ]

    normalized = svc.normalize_chain(rows)
    assert len(normalized) == 2
    assert normalized[0].option_type == "put"

    bounded = normalized[1]
    assert bounded.delta == 1.0
    assert bounded.open_interest == 0
    assert bounded.volume == 0


def test_routes_reports_sanitize_finite_replaces_nan_inf() -> None:
    payload = {
        "a": float("inf"),
        "b": float("nan"),
        "c": 1.0,
        "nested": [1, float("-inf"), {"x": float("nan")}],
    }

    cleaned = _sanitize_finite(payload)
    assert cleaned["a"] is None
    assert cleaned["b"] is None
    assert cleaned["c"] == 1.0
    assert cleaned["nested"][1] is None
    assert cleaned["nested"][2]["x"] is None


def test_trade_lifecycle_sanitizes_non_finite_payload(tmp_path) -> None:
    svc = TradeLifecycleService(results_dir=tmp_path)
    trade_key_value = "SPY|2026-03-20|put_credit_spread|580|575|7"

    svc.append_event(
        event="NOTE",
        trade_key_value=trade_key_value,
        source="unit",
        payload={"metric": float("nan"), "risk": float("inf"), "ok": 1.25},
    )

    rows = svc.list_events()
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["metric"] is None
    assert payload["risk"] is None
    assert payload["ok"] == 1.25


def test_trade_lifecycle_canonicalizes_trade_key_and_marks_model_unavailable(tmp_path) -> None:
    svc = TradeLifecycleService(results_dir=tmp_path)

    svc.append_event(
        event="WATCHLIST",
        trade_key_value="QQQ|2026-02-23|credit_put_spread|565|560|7",
        source="scanner",
        payload={
            "underlying": "QQQ",
            "expiration": "2026-02-23",
            "spread_type": "credit_put_spread",
            "short_strike": 565,
            "long_strike": 560,
            "dte": 7,
            "model_evaluation": None,
        },
    )

    rows = svc.list_events()
    assert len(rows) == 1
    row = rows[0]
    assert row["trade_key"] == "QQQ|2026-02-23|put_credit_spread|565|560|7"
    assert row["payload"]["spread_type"] == "put_credit_spread"
    assert row["payload"]["strategy"] == "put_credit_spread"
    assert row["payload"]["model_status"] == "unavailable"
    assert "TRADE_KEY_NON_CANONICAL" in row.get("warnings", [])
