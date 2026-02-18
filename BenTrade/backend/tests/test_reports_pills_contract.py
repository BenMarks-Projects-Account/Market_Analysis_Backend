from __future__ import annotations

import pytest

from app.api.routes_reports import _normalize_report_trade
from app.utils.computed_metrics import CORE_COMPUTED_METRIC_FIELDS


@pytest.mark.parametrize(
    ("row", "expected_strategy_id", "expected_label"),
    [
        (
            {
                "underlying": "QQQ",
                "spread_type": "credit_put_spread",
                "expiration": "2026-03-20",
                "dte": 31,
                "short_strike": 510,
                "long_strike": 500,
                "p_win_used": 0.68,
                "open_interest": 820,
                "volume": 310,
            },
            "put_credit_spread",
            "Put Credit Spread",
        ),
        (
            {
                "underlying": "QQQ",
                "spread_type": "calendar_call_spread",
                "expiration": "2026-04-17",
                "dte": 59,
                "dte_near": 31,
                "dte_far": 59,
                "short_strike": 515,
                "long_strike": 515,
            },
            "calendar_call_spread",
            "Call Calendar Spread",
        ),
        (
            {
                "underlying": "QQQ",
                "spread_type": "iron_condor",
                "expiration": "2026-03-20",
                "dte": 31,
                "put_short_strike": 505,
                "put_long_strike": 500,
                "call_short_strike": 530,
                "call_long_strike": 535,
                "p_win_used": 0.64,
            },
            "iron_condor",
            "Iron Condor",
        ),
    ],
)
def test_reports_trade_normalization_includes_canonical_pills(row: dict, expected_strategy_id: str, expected_label: str) -> None:
    normalized = _normalize_report_trade(row)

    assert normalized["strategy_id"] == expected_strategy_id
    assert isinstance(normalized.get("trade_key"), str) and normalized["trade_key"]

    pills = normalized.get("pills")
    assert isinstance(pills, dict)
    assert set(["strategy_label", "dte", "pop", "oi", "vol", "regime_label"]).issubset(set(pills.keys()))
    assert pills["strategy_label"] == expected_label

    warnings = normalized.get("validation_warnings") if isinstance(normalized.get("validation_warnings"), list) else []
    computed_metrics = normalized.get("computed_metrics")
    metrics_status = normalized.get("metrics_status")
    assert isinstance(computed_metrics, dict)
    assert isinstance(metrics_status, dict)
    assert set(CORE_COMPUTED_METRIC_FIELDS).issubset(set(computed_metrics.keys()))
    assert isinstance(metrics_status.get("ready"), bool)
    assert isinstance(metrics_status.get("missing_fields"), list)
    assert set(metrics_status.get("missing_fields") or []).issubset(set(CORE_COMPUTED_METRIC_FIELDS))

    if pills.get("regime_label") is None:
        assert "REGIME_UNAVAILABLE" in warnings

    if expected_strategy_id.startswith("calendar_"):
        assert isinstance(pills.get("dte_label"), str) and pills["dte_label"].startswith("DTE ")
        assert pills.get("dte_front") is not None
        assert pills.get("dte_back") is not None
