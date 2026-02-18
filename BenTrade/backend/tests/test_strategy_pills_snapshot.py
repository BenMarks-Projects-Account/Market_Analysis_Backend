from __future__ import annotations

from pathlib import Path

import pytest

from app.services.strategy_service import StrategyService


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


@pytest.mark.parametrize(
    ("strategy_id", "expiration", "trade", "expected_pills"),
    [
        (
            "credit_spread",
            "2026-03-20",
            {
                "underlying": "QQQ",
                "spread_type": "credit_put_spread",
                "dte": 31,
                "short_strike": 510,
                "long_strike": 500,
                "p_win_used": 0.68,
                "open_interest": 820,
                "volume": 310,
            },
            {
                "strategy_label": "Put Credit Spread",
                "dte": 31.0,
                "pop": 0.68,
                "oi": 820.0,
                "vol": 310.0,
                "regime_label": None,
            },
        ),
        (
            "debit_spreads",
            "2026-03-20",
            {
                "underlying": "QQQ",
                "spread_type": "debit_call_spread",
                "dte": 31,
                "short_strike": 520,
                "long_strike": 510,
                "open_interest": 410,
                "volume": 155,
            },
            {
                "strategy_label": "Call Debit Spread",
                "dte": 31.0,
                "pop": None,
                "oi": 410.0,
                "vol": 155.0,
                "regime_label": None,
            },
        ),
        (
            "calendars",
            "2026-04-17",
            {
                "underlying": "QQQ",
                "spread_type": "calendar_call_spread",
                "dte": 59,
                "dte_near": 31,
                "dte_far": 59,
                "short_strike": 515,
                "long_strike": 515,
                "open_interest": 290,
                "volume": 90,
            },
            {
                "strategy_label": "Call Calendar Spread",
                "dte": 59.0,
                "pop": None,
                "oi": 290.0,
                "vol": 90.0,
                "regime_label": None,
                "dte_front": 31.0,
                "dte_back": 59.0,
                "dte_label": "DTE 31/59",
            },
        ),
        (
            "iron_condor",
            "2026-03-20",
            {
                "underlying": "QQQ",
                "spread_type": "iron_condor",
                "dte": 31,
                "put_short_strike": 505,
                "put_long_strike": 500,
                "call_short_strike": 530,
                "call_long_strike": 535,
                "p_win_used": 0.64,
                "open_interest": 1500,
                "volume": 450,
            },
            {
                "strategy_label": "Iron Condor",
                "dte": 31.0,
                "pop": 0.64,
                "oi": 1500.0,
                "vol": 450.0,
                "regime_label": None,
            },
        ),
        (
            "income",
            "2026-03-20",
            {
                "underlying": "QQQ",
                "spread_type": "csp",
                "dte": 31,
                "short_strike": 500,
                "p_win_used": 0.72,
                "open_interest": 980,
                "volume": 240,
            },
            {
                "strategy_label": "Cash Secured Put",
                "dte": 31.0,
                "pop": 0.72,
                "oi": 980.0,
                "vol": 240.0,
                "regime_label": None,
            },
        ),
    ],
)
def test_strategy_pills_snapshot_contract(
    tmp_path: Path,
    strategy_id: str,
    expiration: str,
    trade: dict,
    expected_pills: dict,
) -> None:
    row = _svc(tmp_path)._normalize_trade(strategy_id=strategy_id, expiration=expiration, trade=trade)
    assert row["pills"] == expected_pills
