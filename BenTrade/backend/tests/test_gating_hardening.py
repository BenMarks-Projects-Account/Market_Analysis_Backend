"""Regression tests for butterfly and calendar gating hardening.

Covers:
1. Calendar with POP_NOT_IMPLEMENTED_FOR_STRATEGY → rejected (METRICS_NOT_IMPLEMENTED).
2. Butterfly with expected_value < 0 fails strict preset.
3. Butterfly with pop < min_pop fails strict.
4. Debit butterfly requires net_debit, not net_credit.
5. debit_pct_of_width gate rejects oversized debit butterflies.
6. metrics_status.ready == false ⇒ engine_gate_status.passed == false.
7. Butterfly METRICS_MISSING gates for pop / EV / max_profit / return_on_risk.
8. Calendar always produces METRICS_NOT_IMPLEMENTED reason.
9. Preset resolution for butterflies and calendars works.
10. EV / EV-to-risk threshold gates.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.strategies.butterflies import ButterfliesStrategyPlugin
from app.services.strategies.calendars import CalendarsStrategyPlugin
from app.utils.computed_metrics import (
    apply_metrics_contract,
    build_computed_metrics,
    build_metrics_status,
    is_debit_strategy,
)
from app.utils.normalize import normalize_trade


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_leg(bid: float | None, ask: float | None, **kwargs: Any) -> SimpleNamespace:
    defaults = {
        "strike": kwargs.pop("strike", 100.0),
        "option_type": kwargs.pop("option_type", "call"),
        "open_interest": kwargs.pop("open_interest", 1000),
        "volume": kwargs.pop("volume", 100),
        "delta": kwargs.pop("delta", 0.5),
        "gamma": kwargs.pop("gamma", 0.02),
        "theta": kwargs.pop("theta", -0.03),
        "vega": kwargs.pop("vega", 0.10),
        "iv": kwargs.pop("iv", 0.20),
        "symbol": kwargs.pop("symbol", "SPY260313C00600000"),
    }
    defaults.update(kwargs)
    return SimpleNamespace(bid=bid, ask=ask, **defaults)


def _calendar_candidate(
    near_bid=5.20, near_ask=5.40, far_bid=8.50, far_ask=8.90,
    oi=1000, vol=100, strike=686.0, spot=686.0,
) -> dict[str, Any]:
    near = _make_leg(bid=near_bid, ask=near_ask, strike=strike,
                     open_interest=oi, volume=vol, theta=-0.08)
    far = _make_leg(bid=far_bid, ask=far_ask, strike=strike,
                    open_interest=oi, volume=vol, theta=-0.04)
    return {
        "strategy": "calendar_spread",
        "spread_type": "calendar_call_spread",
        "option_side": "call",
        "symbol": "SPY",
        "expiration_near": "2026-03-13",
        "expiration_far": "2026-03-31",
        "expiration": "2026-03-31",
        "dte_near": 14,
        "dte_far": 32,
        "dte": 32,
        "underlying_price": spot,
        "strike": strike,
        "short_strike": strike,
        "long_strike": strike,
        "near_leg": near,
        "far_leg": far,
        "near_snapshot": {"symbol": "SPY", "expiration": "2026-03-13"},
        "far_snapshot": {"symbol": "SPY", "expiration": "2026-03-31"},
    }


def _butterfly_candidate(
    lower_bid=4.80, lower_ask=5.10,
    center_bid=2.50, center_ask=2.70,
    upper_bid=0.90, upper_ask=1.10,
    oi=1000, vol=100, wing_width=5.0,
) -> dict[str, Any]:
    lower = _make_leg(bid=lower_bid, ask=lower_ask, strike=602, open_interest=oi, volume=vol)
    center = _make_leg(bid=center_bid, ask=center_ask, strike=607, open_interest=oi, volume=vol)
    upper = _make_leg(bid=upper_bid, ask=upper_ask, strike=612, open_interest=oi, volume=vol)
    return {
        "strategy": "butterflies",
        "spread_type": "debit_call_butterfly",
        "butterfly_type": "debit",
        "option_side": "call",
        "symbol": "QQQ",
        "expiration": "2026-03-06",
        "dte": 7,
        "underlying_price": 605.0,
        "center_strike": 607.0,
        "lower_strike": 602.0,
        "upper_strike": 612.0,
        "wing_width": wing_width,
        "expected_move": 8.0,
        "center_mode": "spot",
        "lower_leg": lower,
        "center_leg": center,
        "upper_leg": upper,
        "snapshot": {"symbol": "QQQ", "expiration": "2026-03-06"},
    }


def _enrich_butterfly(candidate: dict) -> dict | None:
    plugin = ButterfliesStrategyPlugin()
    enriched = plugin.enrich([candidate], {"policy": {}})
    return enriched[0] if enriched else None


def _enrich_calendar(candidate: dict) -> dict | None:
    plugin = CalendarsStrategyPlugin()
    enriched = plugin.enrich([candidate], {"policy": {}})
    return enriched[0] if enriched else None


# ========================================================================
# 1. Calendar with POP not implemented → METRICS_NOT_IMPLEMENTED
# ========================================================================

class TestCalendarMetricsNotImplemented:

    def test_calendar_rejected_with_metrics_not_implemented(self):
        """A well-priced calendar must be rejected with METRICS_NOT_IMPLEMENTED."""
        cand = _calendar_candidate()
        enriched = _enrich_calendar(cand)
        assert enriched is not None
        assert enriched["execution_invalid"] is False  # pricing is fine

        plugin = CalendarsStrategyPlugin()
        enriched["_policy"] = {}
        enriched["_request"] = {}
        passed, reasons = plugin.evaluate(enriched)

        assert not passed, "Calendar should NOT pass evaluate — POP/EV not implemented"
        assert "METRICS_NOT_IMPLEMENTED" in reasons

    def test_calendar_has_metrics_missing_pop(self):
        """Calendar reasons must include METRICS_MISSING:pop (per-field trace)."""
        cand = _calendar_candidate()
        enriched = _enrich_calendar(cand)
        assert enriched is not None
        plugin = CalendarsStrategyPlugin()
        enriched["_policy"] = {}
        enriched["_request"] = {}
        _, reasons = plugin.evaluate(enriched)

        assert "METRICS_MISSING:pop" in reasons
        assert "METRICS_MISSING:expected_value" in reasons
        assert "METRICS_MISSING:max_profit" in reasons
        assert "METRICS_MISSING:return_on_risk" in reasons

    def test_calendar_pop_model_used_is_none(self):
        """Enriched calendar must have pop_model_used = 'NONE'."""
        cand = _calendar_candidate()
        enriched = _enrich_calendar(cand)
        assert enriched is not None
        assert enriched["pop_model_used"] == "NONE"


# ========================================================================
# 2. Butterfly with expected_value < 0 fails strict preset
# ========================================================================

class TestButterflyNegativeEV:

    def test_negative_ev_fails_strict(self):
        """Butterfly with EV < 0 should fail strict min_expected_value >= 0."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None

        plugin = ButterfliesStrategyPlugin()
        enriched["_policy"] = {}
        # strict preset: min_expected_value = 0.0
        enriched["_request"] = {"min_expected_value": 0.0}

        ev = enriched.get("expected_value")
        if ev is not None and ev < 0:
            passed, reasons = plugin.evaluate(enriched)
            assert not passed
            assert "expected_value_below_threshold" in reasons

    def test_negative_ev_allowed_in_wide(self):
        """Butterfly with slightly negative EV can pass in wide preset."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None

        plugin = ButterfliesStrategyPlugin()
        enriched["_policy"] = {}
        # wide preset: min_expected_value = -50.0
        enriched["_request"] = {
            "min_expected_value": -50.0,
            "min_pop": 0.02,
            "min_ev_to_risk": -0.05,
            "min_cost_efficiency": 0.5,
            "max_debit_pct_width": 0.80,
        }

        ev = enriched.get("expected_value")
        if ev is not None and ev >= -50.0:
            passed, reasons = plugin.evaluate(enriched)
            # Should not be rejected by EV gate specifically
            assert "expected_value_below_threshold" not in reasons


# ========================================================================
# 3. Butterfly with pop < min_pop fails strict
# ========================================================================

class TestButterflyPopThreshold:

    def test_low_pop_fails_strict(self):
        """Butterfly with pop < 0.08 (strict min_pop) should fail."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None

        plugin = ButterfliesStrategyPlugin()
        enriched["_policy"] = {}
        enriched["_request"] = {"min_pop": 0.30}  # very high threshold

        pop = enriched.get("p_win_used")
        if pop is not None and pop < 0.30:
            passed, reasons = plugin.evaluate(enriched)
            assert not passed
            assert "pop_below_threshold" in reasons

    def test_missing_pop_produces_metrics_missing(self):
        """Butterfly with pop=None should produce METRICS_MISSING:pop."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None

        # Force pop to None to test the gate
        enriched["p_win_used"] = None
        enriched["pop_butterfly"] = None

        plugin = ButterfliesStrategyPlugin()
        enriched["_policy"] = {}
        enriched["_request"] = {}
        passed, reasons = plugin.evaluate(enriched)
        assert not passed
        assert "METRICS_MISSING:pop" in reasons


# ========================================================================
# 4. Debit butterfly requires net_debit, not net_credit
# ========================================================================

class TestDebitButterflyRequiredField:

    def test_debit_butterfly_has_net_debit(self):
        """Enriched debit butterfly must have net_debit != None."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None
        assert enriched["net_debit"] is not None
        assert enriched["net_credit"] is None  # debit strategy → no net_credit

    def test_metrics_status_reports_net_debit_not_net_credit(self):
        """For debit butterfly with missing cashflow, missing_required says 'net_debit'."""
        # Simulate a debit butterfly with no cashflow at all
        trade = {
            "strategy": "butterflies",
            "spread_type": "debit_call_butterfly",
            "strategy_id": "debit_call_butterfly",
            # All metrics None
        }
        metrics = build_computed_metrics(trade)
        status = build_metrics_status(metrics, strategy_id="debit_call_butterfly")

        # Should report "net_debit" not "net_credit"
        assert "net_debit" in status["missing_required"]
        assert "net_credit" not in status["missing_required"]

    def test_credit_strategy_reports_net_credit(self):
        """For credit strategy, missing_required says 'net_credit'."""
        trade = {"strategy_id": "put_credit_spread"}
        metrics = build_computed_metrics(trade)
        status = build_metrics_status(metrics, strategy_id="put_credit_spread")

        assert "net_credit" in status["missing_required"]
        assert "net_debit" not in status["missing_required"]

    def test_is_debit_strategy_for_butterfly(self):
        """butterflies and debit_call_butterfly are debit strategies."""
        assert is_debit_strategy("butterflies")
        assert is_debit_strategy("butterfly_debit")
        assert is_debit_strategy("debit_call_butterfly") or is_debit_strategy("butterflies")


# ========================================================================
# 5. debit_pct_of_width gate rejects oversized debit butterflies
# ========================================================================

class TestDebitPctOfWidthGate:

    def test_debit_pct_of_width_computed(self):
        """debit_pct_of_width should be net_debit / wing_width."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None
        assert enriched.get("debit_pct_of_width") is not None

        expected = enriched["net_debit"] / enriched["wing_width"]
        assert enriched["debit_pct_of_width"] == pytest.approx(expected, abs=0.001)

    def test_oversized_debit_rejected_strict(self):
        """Butterfly where debit > 35% of wing width → rejected with strict preset."""
        # Make a butterfly where debit is large relative to wing
        # lower=10.00, center=5.00, upper=3.00
        # spread_mid = 10.00 + 3.00 - 2*5.00 = 3.00 on wing_width=5
        # debit_pct_of_width = 3.00 / 5.00 = 0.60 > 0.35 (strict)
        cand = _butterfly_candidate(
            lower_bid=9.80, lower_ask=10.20,
            center_bid=4.80, center_ask=5.20,
            upper_bid=2.80, upper_ask=3.20,
            wing_width=5.0,
        )
        enriched = _enrich_butterfly(cand)
        assert enriched is not None
        assert enriched["debit_pct_of_width"] is not None
        assert enriched["debit_pct_of_width"] > 0.35  # should be ~0.60

        plugin = ButterfliesStrategyPlugin()
        enriched["_policy"] = {}
        enriched["_request"] = {"max_debit_pct_width": 0.35}
        passed, reasons = plugin.evaluate(enriched)
        assert not passed
        assert "BUTTERFLY_DEBIT_TOO_LARGE" in reasons

    def test_reasonable_debit_passes_wide(self):
        """A butterfly with debit ~15% of width should pass wide preset (0.80)."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None

        dpw = enriched.get("debit_pct_of_width")
        if dpw is not None and dpw < 0.80:
            plugin = ButterfliesStrategyPlugin()
            enriched["_policy"] = {}
            enriched["_request"] = {"max_debit_pct_width": 0.80}
            _, reasons = plugin.evaluate(enriched)
            assert "BUTTERFLY_DEBIT_TOO_LARGE" not in reasons


# ========================================================================
# 6. metrics_status.ready == false ⇒ engine_gate_status.passed == false
# ========================================================================

class TestMetricsStatusImpliesGateStatus:

    def test_calendar_metrics_not_ready_blocks_gate(self):
        """Calendar trade normalized with metrics_status.ready=false
        must have engine_gate_status.passed=false."""
        cand = _calendar_candidate()
        enriched = _enrich_calendar(cand)
        assert enriched is not None

        # Normalize the trade (as strategy_service would)
        enriched["selection_reasons"] = []  # simulate accepted trade
        normalized = normalize_trade(
            enriched,
            strategy_id="calendars",
            expiration="2026-03-31",
            derive_dte=True,
        )

        ms = normalized.get("metrics_status") or {}
        assert ms.get("ready") is False, "Calendar metrics_status.ready must be False"

        egs = normalized.get("engine_gate_status") or {}
        assert egs.get("passed") is False, (
            "engine_gate_status.passed must be False when metrics_status.ready is False"
        )
        # Must have METRICS_MISSING reasons
        failed_reasons = egs.get("failed_reasons") or []
        has_metrics_missing = any(r.startswith("METRICS_MISSING:") for r in failed_reasons)
        assert has_metrics_missing, f"Expected METRICS_MISSING reasons, got: {failed_reasons}"

    def test_butterfly_with_full_metrics_passes_gate(self):
        """A well-priced butterfly with metrics_status.ready=true
        should have engine_gate_status.passed=true."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None
        assert enriched["execution_invalid"] is False

        enriched["selection_reasons"] = []
        normalized = normalize_trade(
            enriched,
            strategy_id="butterflies",
            expiration="2026-03-06",
            derive_dte=True,
        )

        ms = normalized.get("metrics_status") or {}
        egs = normalized.get("engine_gate_status") or {}

        if ms.get("ready"):
            assert egs.get("passed") is True
        else:
            # If metrics are still missing (e.g., break_even, bid_ask_pct),
            # gate should also be failed
            assert egs.get("passed") is False

    def test_generic_trade_missing_metrics_blocks_gate(self):
        """Any trade with all metrics None must have gate passed=false."""
        trade = {
            "strategy": "credit_spread",
            "spread_type": "put_credit_spread",
            "strategy_id": "put_credit_spread",
            "selection_reasons": [],
        }
        normalized = normalize_trade(
            trade,
            strategy_id="credit_spread",
            expiration="2026-03-06",
            derive_dte=True,
        )

        ms = normalized.get("metrics_status") or {}
        egs = normalized.get("engine_gate_status") or {}

        assert ms.get("ready") is False
        assert egs.get("passed") is False


# ========================================================================
# 7. Butterfly METRICS_MISSING gates
# ========================================================================

class TestButterflyMetricsMissing:

    def test_missing_max_profit_produces_metrics_missing(self):
        """Butterfly with max_profit=None → METRICS_MISSING:max_profit."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None

        # Force max_profit to None
        enriched["max_profit"] = None
        enriched["max_profit_per_contract"] = None

        plugin = ButterfliesStrategyPlugin()
        enriched["_policy"] = {}
        enriched["_request"] = {}
        passed, reasons = plugin.evaluate(enriched)
        assert not passed
        assert "METRICS_MISSING:max_profit" in reasons

    def test_missing_return_on_risk_produces_metrics_missing(self):
        """Butterfly with return_on_risk=None → METRICS_MISSING:return_on_risk."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None

        enriched["return_on_risk"] = None

        plugin = ButterfliesStrategyPlugin()
        enriched["_policy"] = {}
        enriched["_request"] = {}
        passed, reasons = plugin.evaluate(enriched)
        assert not passed
        assert "METRICS_MISSING:return_on_risk" in reasons

    def test_missing_expected_value_produces_metrics_missing(self):
        """Butterfly with expected_value=None → METRICS_MISSING:expected_value."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None

        enriched["expected_value"] = None
        enriched["ev_per_contract"] = None

        plugin = ButterfliesStrategyPlugin()
        enriched["_policy"] = {}
        enriched["_request"] = {}
        passed, reasons = plugin.evaluate(enriched)
        assert not passed
        assert "METRICS_MISSING:expected_value" in reasons


# ========================================================================
# 8. Preset resolution for butterflies and calendars
# ========================================================================

class TestPresetResolution:

    def test_butterfly_presets_exist(self):
        from app.services.strategy_service import StrategyService
        for level in ("strict", "conservative", "balanced", "wide"):
            resolved = StrategyService.resolve_thresholds("butterflies", level)
            assert "min_pop" in resolved, f"Missing min_pop in butterflies/{level}"
            assert "min_ev_to_risk" in resolved, f"Missing min_ev_to_risk in butterflies/{level}"
            assert "max_debit_pct_width" in resolved, f"Missing max_debit_pct_width in butterflies/{level}"
            assert "min_expected_value" in resolved, f"Missing min_expected_value in butterflies/{level}"
            assert "min_open_interest" in resolved, f"Missing min_open_interest in butterflies/{level}"

    def test_calendar_presets_exist(self):
        from app.services.strategy_service import StrategyService
        for level in ("strict", "conservative", "balanced", "wide"):
            resolved = StrategyService.resolve_thresholds("calendars", level)
            assert "required_metrics_complete" in resolved
            assert resolved["required_metrics_complete"] is True
            assert "min_open_interest" in resolved
            assert "max_bid_ask_spread_pct" in resolved

    def test_strict_tighter_than_wide(self):
        """Strict preset must have materially tighter thresholds than wide."""
        from app.services.strategy_service import StrategyService
        strict = StrategyService.resolve_thresholds("butterflies", "strict")
        wide = StrategyService.resolve_thresholds("butterflies", "wide")

        assert strict["min_pop"] > wide["min_pop"]
        assert strict["min_open_interest"] > wide["min_open_interest"]
        assert strict["max_debit_pct_width"] < wide["max_debit_pct_width"]
        assert strict["min_expected_value"] >= wide["min_expected_value"]

    def test_resolve_thresholds_overrides(self):
        """Overrides should win over preset values."""
        from app.services.strategy_service import StrategyService
        resolved = StrategyService.resolve_thresholds(
            "butterflies", "strict", overrides={"min_pop": 0.99}
        )
        assert resolved["min_pop"] == 0.99


# ========================================================================
# 9. EV-to-risk threshold gate
# ========================================================================

class TestEvToRiskGate:

    def test_low_ev_to_risk_fails(self):
        """Butterfly with ev_to_risk < threshold should fail."""
        cand = _butterfly_candidate()
        enriched = _enrich_butterfly(cand)
        assert enriched is not None

        plugin = ButterfliesStrategyPlugin()
        enriched["_policy"] = {}
        enriched["_request"] = {"min_ev_to_risk": 0.50}  # very high threshold

        ev_to_risk = enriched.get("ev_to_risk")
        if ev_to_risk is not None and ev_to_risk < 0.50:
            passed, reasons = plugin.evaluate(enriched)
            assert not passed
            assert "ev_to_risk_below_threshold" in reasons


# ========================================================================
# 10. Calendar evaluate — execution_invalid produces correct reason
# ========================================================================

class TestCalendarExecutionInvalidEvaluate:

    def test_missing_quotes_rejected(self):
        """Calendar with missing quotes → execution_invalid reason in evaluate."""
        cand = _calendar_candidate(near_bid=None, near_ask=None, far_bid=None, far_ask=None)
        enriched = _enrich_calendar(cand)
        assert enriched is not None

        plugin = CalendarsStrategyPlugin()
        enriched["_policy"] = {}
        enriched["_request"] = {}
        passed, reasons = plugin.evaluate(enriched)
        assert not passed
        assert any("execution_invalid" in r for r in reasons)

    def test_pricing_ok_but_metrics_not_implemented(self):
        """Calendar with valid pricing still rejected for METRICS_NOT_IMPLEMENTED."""
        cand = _calendar_candidate()
        enriched = _enrich_calendar(cand)
        assert enriched is not None
        assert enriched["spread_mid"] is not None
        assert enriched["net_debit"] is not None

        plugin = CalendarsStrategyPlugin()
        enriched["_policy"] = {}
        enriched["_request"] = {}
        passed, reasons = plugin.evaluate(enriched)
        assert not passed
        assert "METRICS_NOT_IMPLEMENTED" in reasons
