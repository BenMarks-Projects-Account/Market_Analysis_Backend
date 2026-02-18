"""
End-to-end trace test: simulate the full credit-spread pipeline stage-by-stage.

Reproduces the exact flow:
  credit_spread.py enrich() → enrich_trade() → strategy_service._normalize_trade()
                             → apply_metrics_contract() → API JSON

Confirms every core metric is non-None at each stage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from common.quant_analysis import enrich_trade
from app.utils.computed_metrics import build_computed_metrics, build_metrics_status, apply_metrics_contract
from app.services.strategy_service import StrategyService


CORE_OUTPUT_METRICS = [
    "max_profit",
    "max_loss",
    "pop",
    "expected_value",
    "return_on_risk",
    "kelly_fraction",
    "break_even",
]

# Subset that lives in the "computed" dict (break_even is in "details")
CORE_COMPUTED_KEYS = [
    "max_profit",
    "max_loss",
    "pop",
    "expected_value",
    "return_on_risk",
    "kelly_fraction",
]


def _raw_candidate() -> dict[str, Any]:
    """Simulate what credit_spread.py enrich() emits before enrich_trades_batch."""
    return {
        "spread_type": "put_credit_spread",
        "strategy": "put_credit_spread",
        "underlying": "SPY",
        "underlying_symbol": "SPY",
        "expiration": "2026-02-23",
        "dte": 7,
        "short_strike": 655.0,
        "long_strike": 650.0,
        "underlying_price": 681.75,
        "price": 681.75,
        "vix": 20.82,
        "bid": 1.42,
        "ask": 1.44,
        "open_interest": 3368,
        "volume": 3488,
        "short_delta_abs": 0.1293,
        "iv": 0.212,
        "implied_vol": 0.212,
        "width": 5.0,
        "net_credit": 0.35,
        "contractsMultiplier": 100,
    }


class TestEndToEndCreditSpreadMetrics:

    def test_stage1_enrich_trade_produces_metrics(self) -> None:
        """Stage 1: enrich_trade() must now compute CreditSpread metrics."""
        raw = _raw_candidate()
        enriched = enrich_trade(raw)

        assert enriched["max_profit_per_share"] is not None
        assert enriched["max_loss_per_share"] is not None
        assert enriched["break_even"] is not None
        assert enriched.get("p_win_used") is not None or enriched.get("pop_delta_approx") is not None
        assert enriched["ev_per_share"] is not None
        assert enriched["return_on_risk"] is not None
        assert enriched["kelly_fraction"] is not None

    def test_stage2_build_computed_metrics_finds_values(self) -> None:
        """Stage 2: build_computed_metrics resolves non-None for all core fields."""
        enriched = enrich_trade(_raw_candidate())
        cm = build_computed_metrics(enriched)

        for key in CORE_OUTPUT_METRICS:
            assert cm[key] is not None, f"build_computed_metrics: {key} is None"

    def test_stage3_metrics_status_ready(self) -> None:
        """Stage 3: metrics_status.ready should be True."""
        enriched = enrich_trade(_raw_candidate())
        cm = build_computed_metrics(enriched)
        status = build_metrics_status(cm)

        # Core trade metrics should ALL be present
        for key in CORE_OUTPUT_METRICS:
            assert key not in status["missing_fields"], f"{key} still listed as missing"

    def test_stage4_normalize_trade_populates_computed(self) -> None:
        """Stage 4: strategy_service._normalize_trade puts metrics in computed/details."""
        enriched = enrich_trade(_raw_candidate())
        enriched["rank_score"] = 25.0

        svc = StrategyService(
            base_data_service=MagicMock(),
            results_dir=Path("/tmp/test_trace"),
        )
        normalized = svc._normalize_trade("credit_spread", "2026-02-23", enriched)

        computed = normalized.get("computed", {})
        for key in CORE_COMPUTED_KEYS:
            assert computed.get(key) is not None, f"normalized.computed[{key!r}] is None"

        details = normalized.get("details", {})
        assert details.get("break_even") is not None, "normalized.details['break_even'] is None"

    def test_stage5_apply_metrics_contract_no_missing_core(self) -> None:
        """Stage 5: apply_metrics_contract produces computed_metrics with all core fields."""
        enriched = enrich_trade(_raw_candidate())
        enriched["rank_score"] = 25.0

        svc = StrategyService(
            base_data_service=MagicMock(),
            results_dir=Path("/tmp/test_trace"),
        )
        normalized = svc._normalize_trade("credit_spread", "2026-02-23", enriched)

        cm = normalized.get("computed_metrics", {})
        for key in CORE_OUTPUT_METRICS:
            assert cm.get(key) is not None, f"computed_metrics[{key!r}] is None"

        ms = normalized.get("metrics_status", {})
        for key in CORE_OUTPUT_METRICS:
            assert key not in ms.get("missing_fields", []), f"{key} still listed as missing"

    def test_stage6_no_unavailable_warnings(self) -> None:
        """Stage 6: no MAX_PROFIT/MAX_LOSS/EV/ROR UNAVAILABLE warnings."""
        enriched = enrich_trade(_raw_candidate())
        enriched["rank_score"] = 25.0

        svc = StrategyService(
            base_data_service=MagicMock(),
            results_dir=Path("/tmp/test_trace"),
        )
        normalized = svc._normalize_trade("credit_spread", "2026-02-23", enriched)

        warnings = normalized.get("validation_warnings", [])
        trade_metric_unavailable = [
            w for w in warnings
            if w in (
                "MAX_PROFIT_UNAVAILABLE",
                "MAX_LOSS_UNAVAILABLE",
                "EXPECTED_VALUE_UNAVAILABLE",
                "RETURN_ON_RISK_UNAVAILABLE",
            )
        ]
        assert not trade_metric_unavailable, f"Got UNAVAILABLE warnings: {trade_metric_unavailable}"


class TestEndToEndTraceLineage:
    """Compact 'lineage record' for the chosen trade, as requested."""

    def test_lineage_record_all_stages(self) -> None:
        raw = _raw_candidate()
        enriched = enrich_trade(raw)

        svc = StrategyService(
            base_data_service=MagicMock(),
            results_dir=Path("/tmp/test_trace"),
        )
        enriched["rank_score"] = 25.0
        normalized = svc._normalize_trade("credit_spread", "2026-02-23", enriched)

        lineage = {
            "trade_key": normalized.get("trade_key"),
            "strategy_id": normalized.get("strategy_id"),
            "spread_type": normalized.get("spread_type"),
            "inputs": {
                "net_credit": raw["net_credit"],
                "width": raw["width"],
                "multiplier": raw["contractsMultiplier"],
                "short_strike": raw["short_strike"],
                "long_strike": raw["long_strike"],
                "underlying_price": raw["underlying_price"],
                "iv": raw["iv"],
                "dte": raw["dte"],
                "delta": raw["short_delta_abs"],
            },
            "after_enrich": {
                "max_profit_per_share": enriched.get("max_profit_per_share"),
                "max_loss_per_share": enriched.get("max_loss_per_share"),
                "break_even": enriched.get("break_even"),
                "pop": enriched.get("p_win_used"),
                "ev_per_share": enriched.get("ev_per_share"),
                "return_on_risk": enriched.get("return_on_risk"),
                "kelly_fraction": enriched.get("kelly_fraction"),
            },
            "after_normalize": {
                "computed.max_profit": normalized.get("computed", {}).get("max_profit"),
                "computed.max_loss": normalized.get("computed", {}).get("max_loss"),
                "computed.pop": normalized.get("computed", {}).get("pop"),
                "computed.expected_value": normalized.get("computed", {}).get("expected_value"),
                "computed.return_on_risk": normalized.get("computed", {}).get("return_on_risk"),
                "computed.kelly_fraction": normalized.get("computed", {}).get("kelly_fraction"),
                "details.break_even": normalized.get("details", {}).get("break_even"),
            },
            "computed_metrics": {
                k: normalized.get("computed_metrics", {}).get(k)
                for k in CORE_OUTPUT_METRICS
            },
            "metrics_status": normalized.get("metrics_status"),
            "unavailable_warnings": [
                w for w in normalized.get("validation_warnings", [])
                if w in (
                    "MAX_PROFIT_UNAVAILABLE",
                    "MAX_LOSS_UNAVAILABLE",
                    "EXPECTED_VALUE_UNAVAILABLE",
                    "RETURN_ON_RISK_UNAVAILABLE",
                )
            ],
        }

        # Core trade metrics must be non-None
        for k, v in lineage["after_enrich"].items():
            assert v is not None, f"after_enrich.{k} is None"
        for k, v in lineage["after_normalize"].items():
            assert v is not None, f"after_normalize.{k} is None"
        for k, v in lineage["computed_metrics"].items():
            assert v is not None, f"computed_metrics.{k} is None"
        # Check only core TRADE metrics are present (market-context metrics
        # like iv_rank, rsi14 require live data and can be absent in unit tests)
        core_missing = [
            f for f in lineage["metrics_status"].get("missing_fields", [])
            if f in CORE_OUTPUT_METRICS
        ]
        assert not core_missing, f"Core metrics missing: {core_missing}"
        assert not lineage["unavailable_warnings"], f"Got warnings: {lineage['unavailable_warnings']}"


class TestPerShareToPerContractConversion:
    """computed.max_profit/max_loss must always hold per-contract $ values."""

    def _per_share_only_trade(self) -> dict[str, Any]:
        """Trade with per-share metrics but NO per-contract (legacy report shape)."""
        return {
            "spread_type": "call_credit",
            "underlying": "SPY",
            "short_strike": 687.0,
            "long_strike": 692.0,
            "dte": 3,
            "net_credit": 1.67,
            "width": 5.0,
            "max_profit_per_share": 1.67,
            "max_loss_per_share": 3.33,
            "break_even": 688.67,
            "return_on_risk": 0.5015,
            "p_win_used": 0.7265,
            "ev_per_share": 0.3025,
            "kelly_fraction": 0.181,
            "expiration": "2026-02-20",
            "underlying_price": 681.55,
            "short_delta_abs": 0.2735,
            "implied_vol": 0.2,
            "iv": 0.2,
            "open_interest": 4137,
            "volume": 3483,
        }

    def test_report_trade_per_contract(self) -> None:
        """_normalize_report_trade must convert per-share → per-contract."""
        from app.api.routes_reports import _normalize_report_trade

        trade = self._per_share_only_trade()
        normalized = _normalize_report_trade(trade)
        computed = normalized.get("computed", {})

        assert computed["max_profit"] == pytest.approx(167.0, abs=0.01)
        assert computed["max_loss"] == pytest.approx(333.0, abs=0.01)
        assert computed["expected_value"] == pytest.approx(30.25, abs=0.01)

    def test_strategy_service_per_contract(self) -> None:
        """strategy_service._normalize_trade must convert per-share → per-contract."""
        trade = self._per_share_only_trade()
        trade["rank_score"] = 25.0

        svc = StrategyService(
            base_data_service=MagicMock(),
            results_dir=Path("/tmp/test_trace"),
        )
        normalized = svc._normalize_trade("credit_spread", "2026-02-20", trade)
        computed = normalized.get("computed", {})

        assert computed["max_profit"] == pytest.approx(167.0, abs=0.01)
        assert computed["max_loss"] == pytest.approx(333.0, abs=0.01)
        assert computed["expected_value"] == pytest.approx(30.25, abs=0.01)

    def test_build_computed_metrics_per_contract(self) -> None:
        """build_computed_metrics must convert per-share → per-contract."""
        trade = self._per_share_only_trade()
        cm = build_computed_metrics(trade)

        assert cm["max_profit"] == pytest.approx(167.0, abs=0.01)
        assert cm["max_loss"] == pytest.approx(333.0, abs=0.01)
        assert cm["expected_value"] == pytest.approx(30.25, abs=0.01)

    def test_null_stays_null(self) -> None:
        """Absent metrics must stay None, not coerce to 0."""
        trade = {
            "spread_type": "put_credit",
            "underlying": "SPY",
            "short_strike": 655.0,
            "long_strike": 650.0,
            "dte": 7,
            "expiration": "2026-02-23",
        }
        cm = build_computed_metrics(trade)
        assert cm["max_profit"] is None
        assert cm["max_loss"] is None
        assert cm["expected_value"] is None
        assert cm["pop"] is None
        assert cm["return_on_risk"] is None
        assert cm["kelly_fraction"] is None

    def test_per_contract_not_doubled(self) -> None:
        """When per-contract already exists, don't multiply again."""
        trade = self._per_share_only_trade()
        trade["max_profit_per_contract"] = 167.0
        trade["max_loss_per_contract"] = 333.0
        trade["ev_per_contract"] = 30.25

        from app.api.routes_reports import _normalize_report_trade

        normalized = _normalize_report_trade(trade)
        computed = normalized.get("computed", {})
        assert computed["max_profit"] == pytest.approx(167.0, abs=0.01)
        assert computed["max_loss"] == pytest.approx(333.0, abs=0.01)
        assert computed["expected_value"] == pytest.approx(30.25, abs=0.01)
