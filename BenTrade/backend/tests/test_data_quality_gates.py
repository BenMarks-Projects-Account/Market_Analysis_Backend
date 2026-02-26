"""Unit tests for data-quality hardening of the credit spread pipeline.

Tests verify:
- validate_quote() centralised quote validation rules
- validate_spread_quotes() two-leg quote validation
- evaluate() Gate 6 data-quality modes (strict/balanced/lenient)
- DQ_MISSING:* rejection codes
- QUOTE_INVALID:* rejection codes via Gate 1
- missing_field_counts in filter trace
- data_quality_mode in presets and filter trace
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.strategies.credit_spread import (
    CreditSpreadStrategyPlugin,
    validate_quote,
    validate_spread_quotes,
    _DATA_QUALITY_MODES,
    _DEFAULT_DATA_QUALITY_MODE,
    _DEFAULT_MIN_CREDIT_FOR_DQ_WAIVER,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeContract:
    strike: float
    bid: float | None
    ask: float | None
    option_type: str = "put"
    delta: float | None = None
    iv: float | None = None
    open_interest: int | None = 1000
    volume: int | None = 100


def _make_trade(**overrides) -> dict[str, Any]:
    """Return a healthy enriched trade dict suitable for evaluate()."""
    base = {
        "underlying": "SPY",
        "expiration": "2025-03-21",
        "short_strike": 595.0,
        "long_strike": 590.0,
        "width": 5.0,
        "net_credit": 1.50,
        "p_win_used": 0.72,
        "pop_delta_approx": 0.72,
        "ev_per_share": 0.10,
        "ev_to_risk": 0.04,
        "return_on_risk": 0.03,
        "bid_ask_spread_pct": 0.008,   # 0.8%
        "open_interest": 2000,
        "volume": 150,
        "_quote_rejection": None,
        "_short_bid": 3.00,
        "_short_ask": 3.20,
        "_long_bid": 1.50,
        "_long_ask": 1.80,
    }
    base.update(overrides)
    return base


def _evaluate_with_mode(trade_overrides: dict, mode: str = "balanced",
                        payload_overrides: dict | None = None) -> tuple[bool, list[str]]:
    """Convenience: run evaluate with a specific data_quality_mode."""
    payload = {"data_quality_mode": mode}
    if payload_overrides:
        payload.update(payload_overrides)
    trade = _make_trade(**trade_overrides, _request=payload)
    plugin = CreditSpreadStrategyPlugin()
    return plugin.evaluate(trade)


# ===========================================================================
# 1. validate_quote() — single-leg validation
# ===========================================================================

class TestValidateQuote:
    def test_valid_quote(self):
        ok, reason = validate_quote(1.50, 1.80)
        assert ok is True
        assert reason is None

    def test_zero_bid_valid(self):
        """bid = 0 is valid (deep OTM option)."""
        ok, reason = validate_quote(0.0, 0.05)
        assert ok is True

    def test_missing_bid(self):
        ok, reason = validate_quote(None, 1.80)
        assert ok is False
        assert reason == "missing_bid"

    def test_missing_ask(self):
        ok, reason = validate_quote(1.50, None)
        assert ok is False
        assert reason == "missing_ask"

    def test_negative_bid(self):
        ok, reason = validate_quote(-0.10, 1.80)
        assert ok is False
        assert reason == "negative_bid"

    def test_zero_or_negative_ask(self):
        ok, reason = validate_quote(0.0, 0.0)
        assert ok is False
        assert reason == "zero_or_negative_ask"

    def test_negative_ask(self):
        ok, reason = validate_quote(0.0, -1.0)
        assert ok is False
        assert reason == "zero_or_negative_ask"

    def test_inverted_market(self):
        ok, reason = validate_quote(2.00, 1.50)
        assert ok is False
        assert reason == "inverted_market"

    def test_zero_mid(self):
        """bid=0, ask=0 → ask check catches it first (zero_or_negative_ask)."""
        ok, reason = validate_quote(0.0, 0.0)
        assert ok is False
        # zero ask is caught before mid check
        assert reason == "zero_or_negative_ask"

    def test_both_none(self):
        ok, reason = validate_quote(None, None)
        assert ok is False
        assert reason == "missing_bid"  # bid checked first


# ===========================================================================
# 2. validate_spread_quotes() — two-leg validation
# ===========================================================================

class TestValidateSpreadQuotes:
    def test_valid_spread(self):
        ok, code = validate_spread_quotes(3.00, 3.20, 1.50, 1.80)
        assert ok is True
        assert code is None

    def test_short_leg_missing_bid(self):
        ok, code = validate_spread_quotes(None, 3.20, 1.50, 1.80)
        assert ok is False
        assert code == "QUOTE_INVALID:short_leg:missing_bid"

    def test_short_leg_inverted(self):
        ok, code = validate_spread_quotes(3.50, 3.20, 1.50, 1.80)
        assert ok is False
        assert code == "QUOTE_INVALID:short_leg:inverted_market"

    def test_long_leg_missing_ask(self):
        ok, code = validate_spread_quotes(3.00, 3.20, 1.50, None)
        assert ok is False
        assert code == "QUOTE_INVALID:long_leg:missing_ask"

    def test_long_leg_zero_ask(self):
        ok, code = validate_spread_quotes(3.00, 3.20, 1.50, 0.0)
        assert ok is False
        assert code == "QUOTE_INVALID:long_leg:zero_or_negative_ask"

    def test_long_leg_negative_bid(self):
        ok, code = validate_spread_quotes(3.00, 3.20, -0.01, 1.80)
        assert ok is False
        assert code == "QUOTE_INVALID:long_leg:negative_bid"

    def test_short_checked_before_long(self):
        """If both legs are bad, short leg failure reported first."""
        ok, code = validate_spread_quotes(None, None, None, None)
        assert ok is False
        assert code.startswith("QUOTE_INVALID:short_leg:")


# ===========================================================================
# 3. evaluate() Gate 1 — quote rejection pass-through
# ===========================================================================

class TestEvaluateGate1QuoteRejection:
    def test_quote_rejection_returns_single_reason(self):
        plugin = CreditSpreadStrategyPlugin()
        trade = _make_trade(_quote_rejection="QUOTE_INVALID:short_leg:missing_bid")
        ok, reasons = plugin.evaluate(trade)
        assert ok is False
        assert reasons == ["QUOTE_INVALID:short_leg:missing_bid"]

    def test_no_quote_rejection_proceeds(self):
        plugin = CreditSpreadStrategyPlugin()
        trade = _make_trade()
        ok, reasons = plugin.evaluate(trade)
        # Should pass (healthy trade)
        assert ok is True
        assert reasons == []


# ===========================================================================
# 4. evaluate() Gate 6 — data quality modes
# ===========================================================================

class TestEvaluateGate6DataQuality:
    """Test OI/volume missing-data handling under each mode."""

    # ── STRICT mode ──

    def test_strict_rejects_missing_oi(self):
        ok, reasons = _evaluate_with_mode({"open_interest": None}, mode="strict")
        assert ok is False
        assert "DQ_MISSING:open_interest" in reasons

    def test_strict_rejects_missing_volume(self):
        ok, reasons = _evaluate_with_mode({"volume": None}, mode="strict")
        assert ok is False
        assert "DQ_MISSING:volume" in reasons

    def test_strict_rejects_both_missing(self):
        ok, reasons = _evaluate_with_mode(
            {"open_interest": None, "volume": None}, mode="strict"
        )
        assert "DQ_MISSING:open_interest" in reasons
        assert "DQ_MISSING:volume" in reasons

    def test_strict_passes_present_oi_volume(self):
        ok, reasons = _evaluate_with_mode(
            {"open_interest": 2000, "volume": 150}, mode="strict"
        )
        assert ok is True

    # ── BALANCED mode ──

    def test_balanced_rejects_missing_oi(self):
        ok, reasons = _evaluate_with_mode({"open_interest": None}, mode="balanced")
        assert ok is False
        assert "DQ_MISSING:open_interest" in reasons

    def test_balanced_rejects_missing_volume(self):
        ok, reasons = _evaluate_with_mode({"volume": None}, mode="balanced")
        assert ok is False
        assert "DQ_MISSING:volume" in reasons

    # ── LENIENT mode ──

    def test_lenient_waives_missing_oi_when_pricing_ok(self):
        ok, reasons = _evaluate_with_mode(
            {"open_interest": None}, mode="lenient"
        )
        # bid_ask_spread_pct=0.008 < 1.5, net_credit=1.50 >= 0.10
        assert ok is True
        assert "DQ_MISSING:open_interest" not in reasons

    def test_lenient_waives_missing_volume_when_pricing_ok(self):
        ok, reasons = _evaluate_with_mode(
            {"volume": None}, mode="lenient"
        )
        assert ok is True

    def test_lenient_waives_both_missing_when_pricing_ok(self):
        ok, reasons = _evaluate_with_mode(
            {"open_interest": None, "volume": None}, mode="lenient"
        )
        assert ok is True

    def test_lenient_rejects_missing_oi_when_spread_wide(self):
        ok, reasons = _evaluate_with_mode(
            {"open_interest": None, "bid_ask_spread_pct": 0.05},  # 5% > 1.5
            mode="lenient",
        )
        assert ok is False
        assert "DQ_MISSING:open_interest" in reasons

    def test_lenient_rejects_missing_vol_when_credit_too_low(self):
        ok, reasons = _evaluate_with_mode(
            {"volume": None, "net_credit": 0.05},  # 0.05 < 0.10
            mode="lenient",
        )
        assert ok is False
        assert "DQ_MISSING:volume" in reasons

    def test_lenient_custom_min_credit_waiver(self):
        """Custom min_credit_for_dq_waiver overrides default."""
        ok, reasons = _evaluate_with_mode(
            {"volume": None, "net_credit": 0.05},
            mode="lenient",
            payload_overrides={"min_credit_for_dq_waiver": 0.03},
        )
        assert ok is True  # 0.05 >= 0.03

    # ── Threshold checks when data IS present ──

    def test_low_oi_still_rejected_in_lenient(self):
        """Even in lenient mode, present but low OI is rejected by threshold."""
        ok, reasons = _evaluate_with_mode(
            {"open_interest": 5, "volume": 150},  # 5 < 300
            mode="lenient",
        )
        assert ok is False
        assert "open_interest_below_min" in reasons

    def test_low_volume_still_rejected_in_strict(self):
        ok, reasons = _evaluate_with_mode(
            {"open_interest": 2000, "volume": 1},
            mode="strict",
        )
        assert ok is False
        assert "volume_below_min" in reasons

    # ── Invalid mode falls back to balanced ──

    def test_invalid_mode_defaults_to_balanced(self):
        ok, reasons = _evaluate_with_mode(
            {"open_interest": None}, mode="bogus"
        )
        assert ok is False
        assert "DQ_MISSING:open_interest" in reasons


# ===========================================================================
# 5. Constants & configuration
# ===========================================================================

class TestConstants:
    def test_data_quality_modes_frozenset(self):
        assert isinstance(_DATA_QUALITY_MODES, frozenset)
        assert _DATA_QUALITY_MODES == {"strict", "balanced", "lenient"}

    def test_default_mode_is_balanced(self):
        assert _DEFAULT_DATA_QUALITY_MODE == "balanced"

    def test_default_min_credit_waiver(self):
        assert _DEFAULT_MIN_CREDIT_FOR_DQ_WAIVER == 0.10


# ===========================================================================
# 6. Presets include data_quality_mode
# ===========================================================================

class TestPresetsDataQualityMode:
    def test_all_credit_spread_presets_have_dq_mode(self):
        from app.services.strategy_service import StrategyService
        presets = StrategyService._PRESETS.get("credit_spread", {})
        for level_name, level_cfg in presets.items():
            assert "data_quality_mode" in level_cfg, \
                f"credit_spread preset '{level_name}' missing data_quality_mode"

    def test_strict_preset_uses_strict_mode(self):
        from app.services.strategy_service import StrategyService
        cfg = StrategyService._PRESETS["credit_spread"]["strict"]
        assert cfg["data_quality_mode"] == "strict"

    def test_conservative_preset_uses_balanced_mode(self):
        from app.services.strategy_service import StrategyService
        cfg = StrategyService._PRESETS["credit_spread"]["conservative"]
        assert cfg["data_quality_mode"] == "balanced"

    def test_balanced_preset_uses_balanced_mode(self):
        from app.services.strategy_service import StrategyService
        cfg = StrategyService._PRESETS["credit_spread"]["balanced"]
        assert cfg["data_quality_mode"] == "balanced"

    def test_wide_preset_uses_lenient_mode(self):
        from app.services.strategy_service import StrategyService
        cfg = StrategyService._PRESETS["credit_spread"]["wide"]
        assert cfg["data_quality_mode"] == "lenient"


# ===========================================================================
# 7. Gate groups include new DQ and QUOTE_INVALID codes
# ===========================================================================

class TestGateGroupsDQ:
    def test_data_quality_gate_group_exists(self):
        from app.services.strategy_service import StrategyService
        assert "data_quality" in StrategyService._GATE_GROUPS

    def test_data_quality_group_contains_dq_missing_codes(self):
        from app.services.strategy_service import StrategyService
        dq_group = StrategyService._GATE_GROUPS["data_quality"]
        assert "DQ_MISSING:open_interest" in dq_group
        assert "DQ_MISSING:volume" in dq_group

    def test_quote_validation_group_contains_quote_invalid_codes(self):
        from app.services.strategy_service import StrategyService
        qv_group = StrategyService._GATE_GROUPS["quote_validation"]
        # Check a sample of the 12 codes
        assert "QUOTE_INVALID:short_leg:missing_bid" in qv_group
        assert "QUOTE_INVALID:long_leg:missing_ask" in qv_group
        assert "QUOTE_INVALID:short_leg:inverted_market" in qv_group


# ===========================================================================
# 8. Filter trace includes missing_field_counts & data_quality_mode
# ===========================================================================

class TestFilterTraceMissingFieldCounts:
    @pytest.mark.anyio
    async def test_filter_trace_has_missing_field_counts(self, tmp_path):
        from app.services.strategy_service import StrategyService
        mock_bds = MagicMock()
        mock_bds.get_source_health_snapshot.return_value = {"sources": []}
        svc = StrategyService(base_data_service=mock_bds, results_dir=tmp_path)

        mock_contracts = [
            FakeContract(strike=595.0, bid=3.00, ask=3.20, delta=-0.30, open_interest=1000, volume=100),
            FakeContract(strike=590.0, bid=1.50, ask=1.80, delta=-0.20, open_interest=None, volume=None),
            FakeContract(strike=585.0, bid=0.80, ask=1.00, delta=-0.12, open_interest=500, volume=50),
            FakeContract(strike=580.0, bid=0.40, ask=0.55, delta=-0.08, open_interest=None, volume=None),
        ]

        async def mock_get_inputs(sym, exp):
            return {
                "symbol": sym, "expiration": exp,
                "underlying_price": 600.0, "vix": 18.0,
                "contracts": mock_contracts,
                "prices_history": [595.0, 596.0, 597.0, 598.0, 599.0, 600.0],
            }

        async def mock_get_expirations(sym):
            return ["2025-03-21"]

        svc.base_data_service.get_analysis_inputs = mock_get_inputs
        svc.base_data_service.tradier_client = MagicMock()
        svc.base_data_service.tradier_client.get_expirations = mock_get_expirations

        result = await svc.generate("credit_spread", {"preset": "wide", "symbols": ["SPY"]})
        ft = result["filter_trace"]

        assert "missing_field_counts" in ft
        mfc = ft["missing_field_counts"]
        assert isinstance(mfc, dict)
        assert "open_interest" in mfc
        assert "volume" in mfc
        assert "bid" in mfc
        assert "ask" in mfc
        assert "quote_rejected" in mfc
        assert "dq_waived" in mfc
        assert "total_enriched" in mfc
        assert mfc["total_enriched"] >= 0

    @pytest.mark.anyio
    async def test_filter_trace_has_data_quality_mode(self, tmp_path):
        from app.services.strategy_service import StrategyService
        mock_bds = MagicMock()
        mock_bds.get_source_health_snapshot.return_value = {"sources": []}
        svc = StrategyService(base_data_service=mock_bds, results_dir=tmp_path)

        mock_contracts = [
            FakeContract(strike=595.0, bid=3.00, ask=3.20, delta=-0.30),
            FakeContract(strike=590.0, bid=1.50, ask=1.80, delta=-0.20),
        ]

        async def mock_get_inputs(sym, exp):
            return {
                "symbol": sym, "expiration": exp,
                "underlying_price": 600.0, "vix": 18.0,
                "contracts": mock_contracts,
                "prices_history": [595.0, 596.0, 597.0, 598.0, 599.0, 600.0],
            }

        async def mock_get_expirations(sym):
            return ["2025-03-21"]

        svc.base_data_service.get_analysis_inputs = mock_get_inputs
        svc.base_data_service.tradier_client = MagicMock()
        svc.base_data_service.tradier_client.get_expirations = mock_get_expirations

        result = await svc.generate("credit_spread", {"preset": "strict", "symbols": ["SPY"]})
        ft = result["filter_trace"]

        assert "data_quality_mode" in ft
        assert ft["data_quality_mode"] == "strict"


# ===========================================================================
# 9. enrich() centralised quote validation
# ===========================================================================

class TestEnrichQuoteValidation:
    """Verify enrich() populates _quote_rejection using validate_spread_quotes()."""

    def test_valid_quotes_no_rejection(self):
        plugin = CreditSpreadStrategyPlugin()
        contracts = [
            FakeContract(strike=595.0, bid=3.00, ask=3.20, delta=-0.30),
            FakeContract(strike=590.0, bid=1.50, ask=1.80, delta=-0.20),
        ]
        candidates = [{
            "short_leg": contracts[0],
            "long_leg": contracts[1],
            "strategy": "put_credit_spread",
            "width": 5.0,
            "snapshot": {
                "symbol": "SPY", "expiration": "2025-03-21",
                "underlying_price": 600.0, "vix": 18.0,
            },
        }]
        inputs = {
            "symbol": "SPY", "expiration": "2025-03-21",
            "underlying_price": 600.0, "vix": 18.0,
            "prices_history": [595.0, 596.0, 597.0, 598.0, 599.0, 600.0],
        }
        enriched = plugin.enrich(candidates, inputs)
        assert len(enriched) >= 1
        for row in enriched:
            assert row.get("_quote_rejection") is None

    def test_missing_short_bid_sets_rejection(self):
        plugin = CreditSpreadStrategyPlugin()
        contracts = [
            FakeContract(strike=595.0, bid=None, ask=3.20, delta=-0.30),
            FakeContract(strike=590.0, bid=1.50, ask=1.80, delta=-0.20),
        ]
        candidates = [{
            "short_leg": contracts[0],
            "long_leg": contracts[1],
            "strategy": "put_credit_spread",
            "width": 5.0,
            "snapshot": {
                "symbol": "SPY", "expiration": "2025-03-21",
                "underlying_price": 600.0, "vix": 18.0,
            },
        }]
        inputs = {
            "symbol": "SPY", "expiration": "2025-03-21",
            "underlying_price": 600.0, "vix": 18.0,
            "prices_history": [595.0, 596.0, 597.0, 598.0, 599.0, 600.0],
        }
        enriched = plugin.enrich(candidates, inputs)
        assert len(enriched) >= 1
        assert enriched[0]["_quote_rejection"] == "QUOTE_INVALID:short_leg:missing_bid"

    def test_inverted_long_leg_sets_rejection(self):
        plugin = CreditSpreadStrategyPlugin()
        contracts = [
            FakeContract(strike=595.0, bid=3.00, ask=3.20, delta=-0.30),
            FakeContract(strike=590.0, bid=2.00, ask=1.50, delta=-0.20),  # inverted
        ]
        candidates = [{
            "short_leg": contracts[0],
            "long_leg": contracts[1],
            "strategy": "put_credit_spread",
            "width": 5.0,
            "snapshot": {
                "symbol": "SPY", "expiration": "2025-03-21",
                "underlying_price": 600.0, "vix": 18.0,
            },
        }]
        inputs = {
            "symbol": "SPY", "expiration": "2025-03-21",
            "underlying_price": 600.0, "vix": 18.0,
            "prices_history": [595.0, 596.0, 597.0, 598.0, 599.0, 600.0],
        }
        enriched = plugin.enrich(candidates, inputs)
        assert len(enriched) >= 1
        assert enriched[0]["_quote_rejection"] == "QUOTE_INVALID:long_leg:inverted_market"


# ===========================================================================
# 10. _FILTER_TRACE_SKIP_KEYS includes new keys
# ===========================================================================

class TestFilterTraceSkipKeys:
    def test_data_quality_mode_in_skip_keys(self):
        from app.services.strategy_service import StrategyService
        assert "data_quality_mode" in StrategyService._FILTER_TRACE_SKIP_KEYS

    def test_spread_type_in_skip_keys(self):
        from app.services.strategy_service import StrategyService
        assert "spread_type" in StrategyService._FILTER_TRACE_SKIP_KEYS

    def test_min_credit_waiver_in_skip_keys(self):
        from app.services.strategy_service import StrategyService
        assert "min_credit_for_dq_waiver" in StrategyService._FILTER_TRACE_SKIP_KEYS
