from __future__ import annotations

from pathlib import Path
import pytest

from app.services.strategy_service import StrategyService
from app.utils.computed_metrics import CORE_COMPUTED_METRIC_FIELDS


class _BaseStub:
    tradier_client = None

    @staticmethod
    def get_source_health_snapshot() -> dict:
        return {}


class _RiskStub:
    @staticmethod
    def get_policy() -> dict:
        return {}


def _svc(tmp_path: Path) -> StrategyService:
    return StrategyService(
        base_data_service=_BaseStub(),
        results_dir=tmp_path,
        risk_policy_service=_RiskStub(),
        signal_service=None,
        regime_service=None,
    )


def test_normalize_trade_includes_canonical_contract_fields(tmp_path):
    svc = _svc(tmp_path)

    row = svc._normalize_trade(
        strategy_id="butterflies",
        expiration="2026-03-20",
        trade={
            "underlying": "qqq",
            "spread_type": "debit_call_butterfly",
            "center_strike": 565,
            "lower_strike": 560,
            "upper_strike": 570,
            "dte": 7,
            "_trade_key": "legacy",
        },
    )

    assert row["underlying_symbol"] == "QQQ"
    assert row["expiration"] == "2026-03-20"
    assert row["dte"] == 7
    assert row["strategy_id"] == "butterfly_debit"
    assert row["trade_key"] == "QQQ|2026-03-20|butterfly_debit|565|L560|U570|7"
    assert "_trade_key" not in row


@pytest.mark.parametrize(
    ("strategy_id", "trade", "expect_pop_warning", "expected_strategy_label"),
    [
        (
            "credit_spread",
            {
                "underlying": "QQQ",
                "spread_type": "credit_put_spread",
                "expiration": "2026-03-20",
                "dte": 31,
                "short_strike": 510,
                "long_strike": 500,
                "max_profit": 145.0,
                "max_loss": 855.0,
                "p_win_used": 0.68,
                "return_on_risk": 0.17,
                "ev_per_contract": 35.0,
            },
            False,
            "Put Credit Spread",
        ),
        (
            "debit_spreads",
            {
                "underlying": "QQQ",
                "spread_type": "debit_call_spread",
                "expiration": "2026-03-20",
                "dte": 31,
                "short_strike": 520,
                "long_strike": 510,
                "max_profit": 600.0,
                "max_loss": 400.0,
                "return_on_risk": 1.5,
                "ev_per_contract": 40.0,
            },
            True,
            "Call Debit Spread",
        ),
        (
            "butterflies",
            {
                "underlying": "QQQ",
                "spread_type": "debit_call_butterfly",
                "expiration": "2026-03-20",
                "dte": 31,
                "center_strike": 515,
                "lower_strike": 510,
                "upper_strike": 520,
                "max_profit": 480.0,
                "max_loss": 220.0,
                "return_on_risk": 2.18,
                "ev_per_contract": 28.0,
            },
            True,
            "Debit Butterfly",
        ),
        (
            "calendars",
            {
                "underlying": "QQQ",
                "spread_type": "calendar_call_spread",
                "expiration": "2026-04-17",
                "dte": 59,
                "dte_near": 31,
                "dte_far": 59,
                "short_strike": 515,
                "long_strike": 515,
                "max_profit": 280.0,
                "max_loss": 150.0,
                "return_on_risk": 1.86,
                "expected_value": 22.0,
                "expected_move_near": 12.5,
            },
            True,
            "Call Calendar Spread",
        ),
        (
            "iron_condor",
            {
                "underlying": "QQQ",
                "spread_type": "iron_condor",
                "expiration": "2026-03-20",
                "dte": 31,
                "put_short_strike": 505,
                "put_long_strike": 500,
                "call_short_strike": 530,
                "call_long_strike": 535,
                "max_profit": 220.0,
                "max_loss": 780.0,
                "p_win_used": 0.64,
                "return_on_risk": 0.28,
                "ev_per_contract": 16.0,
            },
            False,
            "Iron Condor",
        ),
        (
            "income",
            {
                "underlying": "QQQ",
                "spread_type": "csp",
                "expiration": "2026-03-20",
                "dte": 31,
                "short_strike": 500,
                "max_profit": 120.0,
                "max_loss": 4880.0,
                "p_win_used": 0.72,
                "return_on_risk": 0.024,
                "expected_value": 8.0,
            },
            False,
            "Cash Secured Put",
        ),
    ],
)
def test_normalize_trade_contract_has_computed_and_details(
    tmp_path: Path,
    strategy_id: str,
    trade: dict,
    expect_pop_warning: bool,
    expected_strategy_label: str,
) -> None:
    svc = _svc(tmp_path)
    row = svc._normalize_trade(strategy_id=strategy_id, expiration=str(trade.get("expiration") or "2026-03-20"), trade=trade)

    assert isinstance(row.get("computed"), dict)
    assert isinstance(row.get("details"), dict)
    assert isinstance(row.get("pills"), dict)
    assert isinstance(row.get("computed_metrics"), dict)
    assert isinstance(row.get("metrics_status"), dict)

    computed = row["computed"]
    details = row["details"]
    pills = row["pills"]
    computed_metrics = row["computed_metrics"]
    metrics_status = row["metrics_status"]
    warnings = row.get("validation_warnings") if isinstance(row.get("validation_warnings"), list) else []

    assert "max_profit" in computed
    assert "max_loss" in computed
    assert "expected_value" in computed
    assert "return_on_risk" in computed
    assert "dte" in details
    assert set(["strategy_label", "dte", "pop", "oi", "vol", "regime_label"]).issubset(set(pills.keys()))
    assert pills["strategy_label"] == expected_strategy_label
    assert pills["pop"] == computed["pop"]
    assert pills["oi"] == computed["open_interest"]
    assert pills["vol"] == computed["volume"]
    assert pills["regime_label"] == details["market_regime"]
    assert isinstance(row.get("trade_key"), str) and row["trade_key"]
    assert isinstance(row.get("strategy_id"), str) and row["strategy_id"]
    assert set(CORE_COMPUTED_METRIC_FIELDS).issubset(set(computed_metrics.keys()))
    assert isinstance(metrics_status.get("ready"), bool)
    assert isinstance(metrics_status.get("missing_fields"), list)
    assert set(metrics_status.get("missing_fields") or []).issubset(set(CORE_COMPUTED_METRIC_FIELDS))

    if computed["max_profit"] is None:
        assert "MAX_PROFIT_UNAVAILABLE" in warnings
    if computed["max_loss"] is None:
        assert "MAX_LOSS_UNAVAILABLE" in warnings
    if computed["expected_value"] is None:
        assert "EXPECTED_VALUE_UNAVAILABLE" in warnings
    if computed["return_on_risk"] is None:
        assert "RETURN_ON_RISK_UNAVAILABLE" in warnings

    if expect_pop_warning:
        assert "POP_NOT_IMPLEMENTED_FOR_STRATEGY" in warnings
    else:
        assert computed["pop"] is not None

    if pills["regime_label"] is None:
        assert "REGIME_UNAVAILABLE" in warnings

    if row["strategy_id"] in {"calendar_call_spread", "calendar_put_spread", "calendar_spread"}:
        assert pills.get("dte_front") is not None
        assert pills.get("dte_back") is not None
        assert isinstance(pills.get("dte_label"), str) and pills.get("dte_label", "").startswith("DTE ")
