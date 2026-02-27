"""Regression tests for iron condor EV/POP quality gates.

Validates:
- evaluate() rejects candidates with negative ev_to_risk
- evaluate() rejects candidates with ev_to_risk below preset threshold
- evaluate() rejects candidates with missing POP (non-lenient mode)
- evaluate() rejects candidates with POP below preset threshold
- evaluate() accepts candidates meeting all EV + POP thresholds
- Presets define min_ev_to_risk and min_pop for all tiers
- net_credit/net_debit semantics are correct after normalize_trade
- engine_gate_status is populated after normalize_trade

Reference trade that motivated these gates:
  SPY 2026-03-20. 21 DTE.  P625/620 + C708/713.
  expected_value = -120.42, ev_to_risk = -0.2912
  Previously passed STRICT because no EV gate existed.
"""

from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ic_candidate(**overrides: Any) -> dict[str, Any]:
    """Build a minimal IC candidate dict that passes all existing gates.

    Default values are set so the candidate passes sigma-distance,
    symmetry, credit, return-on-risk, and short-leg-bid gates.
    Only the EV/POP gate behaviour is under test.
    """
    base: dict[str, Any] = {
        "spread_type": "iron_condor",
        "strategy": "iron_condor",
        "symbol": "SPY",
        "underlying": "SPY",
        "expiration": "2026-06-01",
        "dte": 30,
        "underlying_price": 600.0,
        # Sigma / distance — passes min_sigma=1.10
        "min_sigma_dist": 1.30,
        "expected_move_ratio": 1.30,
        # Symmetry — passes target=0.70
        "symmetry_score": 0.85,
        # Credit — passes min_credit=0.10
        "total_credit": 2.50,
        "net_credit": 2.50,
        "net_debit": None,
        # Return-on-risk — passes min_ror=0.12
        "return_on_risk": 0.20,
        # Short-leg bids > 0
        "_short_put_bid": 1.50,
        "_short_call_bid": 1.50,
        # No penny-wing
        "_penny_wing": False,
        # ── Fields under test ──
        "p_win_used": 0.65,
        "pop_delta_approx": 0.65,
        "ev_per_share": 50.0,
        "expected_value": 50.0,
        "ev_to_risk": 0.10,
        # Readiness
        "readiness": True,
        # Request payload (preset resolved thresholds)
        "_request": {},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# evaluate() gate tests
# ---------------------------------------------------------------------------

class TestIronCondorEVGate:
    """evaluate() must reject candidates with negative or sub-threshold EV."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.iron_condor import IronCondorStrategyPlugin
        return IronCondorStrategyPlugin()

    def test_negative_ev_to_risk_rejected(self, plugin):
        """ev_to_risk = -0.29 must produce 'ev_to_risk_below_floor'."""
        candidate = _make_ic_candidate(ev_to_risk=-0.29, expected_value=-120.0)
        ok, reasons = plugin.evaluate(candidate)
        assert not ok, "Negative ev_to_risk must be rejected"
        assert "ev_to_risk_below_floor" in reasons

    def test_negative_ev_explicit_rejected(self, plugin):
        """ev_to_risk=None but ev < -0.05 must produce 'ev_negative'."""
        candidate = _make_ic_candidate(ev_to_risk=None, ev_per_share=-10.0, expected_value=-10.0)
        ok, reasons = plugin.evaluate(candidate)
        assert not ok, "Negative EV with missing ev_to_risk must be rejected"
        assert "ev_negative" in reasons

    def test_ev_to_risk_below_strict_threshold(self, plugin):
        """STRICT preset requires ev_to_risk >= 0.05."""
        candidate = _make_ic_candidate(
            ev_to_risk=0.03,  # below strict threshold of 0.05
            _request={"min_ev_to_risk": 0.05, "min_pop": 0.55},
        )
        ok, reasons = plugin.evaluate(candidate)
        assert not ok
        assert "ev_to_risk_below_floor" in reasons

    def test_ev_to_risk_passes_balanced(self, plugin):
        """BALANCED threshold (0.00): ev_to_risk=0.01 should pass."""
        candidate = _make_ic_candidate(
            ev_to_risk=0.01,
            _request={"min_ev_to_risk": 0.00, "min_pop": 0.45},
        )
        ok, reasons = plugin.evaluate(candidate)
        assert ok, f"Expected pass, got reasons: {reasons}"

    def test_ev_to_risk_passes_strict(self, plugin):
        """ev_to_risk=0.06 above STRICT threshold (0.05) should pass."""
        candidate = _make_ic_candidate(
            ev_to_risk=0.06,
            _request={"min_ev_to_risk": 0.05, "min_pop": 0.55},
        )
        ok, reasons = plugin.evaluate(candidate)
        assert ok, f"Expected pass, got reasons: {reasons}"


class TestIronCondorPOPGate:
    """evaluate() must reject candidates with missing or sub-threshold POP."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.iron_condor import IronCondorStrategyPlugin
        return IronCondorStrategyPlugin()

    def test_missing_pop_rejected_strict(self, plugin):
        """Missing POP in strict mode produces 'DQ_MISSING:pop'."""
        candidate = _make_ic_candidate(
            p_win_used=None,
            pop_delta_approx=None,
            _request={"data_quality_mode": "strict", "min_pop": 0.55},
        )
        ok, reasons = plugin.evaluate(candidate)
        assert not ok
        assert "DQ_MISSING:pop" in reasons

    def test_missing_pop_waived_lenient(self, plugin):
        """Missing POP in lenient mode is waived — no rejection."""
        candidate = _make_ic_candidate(
            p_win_used=None,
            pop_delta_approx=None,
            _request={"data_quality_mode": "lenient", "min_pop": 0.35},
        )
        ok, reasons = plugin.evaluate(candidate)
        # Should still pass (ev_to_risk is good, only POP is missing)
        assert "DQ_MISSING:pop" not in reasons

    def test_low_pop_rejected(self, plugin):
        """POP=0.40 below STRICT threshold (0.55) must produce 'pop_below_floor'."""
        candidate = _make_ic_candidate(
            p_win_used=0.40,
            _request={"min_pop": 0.55},
        )
        ok, reasons = plugin.evaluate(candidate)
        assert not ok
        assert "pop_below_floor" in reasons

    def test_pop_passes_threshold(self, plugin):
        """POP=0.60 above threshold (0.55) should pass."""
        candidate = _make_ic_candidate(
            p_win_used=0.60,
            _request={"min_pop": 0.55, "min_ev_to_risk": 0.05},
        )
        ok, reasons = plugin.evaluate(candidate)
        assert ok, f"Expected pass, got reasons: {reasons}"


# ---------------------------------------------------------------------------
# Preset threshold tests
# ---------------------------------------------------------------------------

class TestIronCondorPresets:
    """IC presets must define EV and POP thresholds for all tiers."""

    @pytest.fixture()
    def ic_presets(self) -> dict[str, dict[str, Any]]:
        from app.services.strategy_service import StrategyService
        return StrategyService._PRESETS["iron_condor"]

    @pytest.mark.parametrize("tier", ["strict", "conservative", "balanced", "wide"])
    def test_preset_has_ev_threshold(self, ic_presets, tier):
        preset = ic_presets[tier]
        assert "min_ev_to_risk" in preset, f"{tier} preset missing min_ev_to_risk"
        assert isinstance(preset["min_ev_to_risk"], (int, float))

    @pytest.mark.parametrize("tier", ["strict", "conservative", "balanced", "wide"])
    def test_preset_has_pop_threshold(self, ic_presets, tier):
        preset = ic_presets[tier]
        assert "min_pop" in preset, f"{tier} preset missing min_pop"
        assert isinstance(preset["min_pop"], (int, float))

    def test_strict_most_demanding(self, ic_presets):
        """Strict must have the highest ev_to_risk and pop thresholds."""
        strict = ic_presets["strict"]
        wide = ic_presets["wide"]
        assert strict["min_ev_to_risk"] > wide["min_ev_to_risk"]
        assert strict["min_pop"] > wide["min_pop"]


# ---------------------------------------------------------------------------
# net_credit/net_debit correctness after normalize_trade
# ---------------------------------------------------------------------------

class TestIronCondorCashflowNormalization:
    """normalize_trade must produce correct cashflow fields for IC (credit strategy)."""

    def test_credit_strategy_has_net_credit_not_debit(self):
        """After normalize, IC computed_metrics must have net_credit, not net_debit."""
        from app.utils.normalize import normalize_trade

        trade = {
            "spread_type": "iron_condor",
            "symbol": "SPY",
            "expiration": "2026-06-01",
            "net_credit": 2.50,
            "net_debit": None,
            "max_loss": 250.0,
            "max_profit": 250.0,
        }
        result = normalize_trade(trade, strategy_id="iron_condor")
        cm = result.get("computed_metrics", {})
        assert cm.get("net_credit") is not None, "IC must have net_credit in computed_metrics"
        assert cm.get("net_debit") is None, "IC must NOT have net_debit in computed_metrics"
        # Root must also be clean
        assert result.get("net_debit") is None, "IC root must NOT have net_debit"

    def test_corrupted_stored_report_corrected(self):
        """Re-normalizing a stored report with swapped cashflow should be corrected."""
        from app.utils.normalize import normalize_trade

        # Simulate a corrupted stored report: computed_metrics has wrong fields
        trade = {
            "spread_type": "iron_condor",
            "symbol": "SPY",
            "expiration": "2026-06-01",
            "net_credit": 2.50,
            "net_debit": None,
            "max_loss": 250.0,
            "max_profit": 250.0,
            # Corrupted computed_metrics from old version
            "computed_metrics": {
                "net_credit": None,
                "net_debit": 2.50,  # WRONG — should be net_credit
            },
        }
        result = normalize_trade(trade, strategy_id="iron_condor")
        cm = result.get("computed_metrics", {})
        assert cm.get("net_credit") == 2.50, "Corrupted net_debit should be moved to net_credit"
        assert cm.get("net_debit") is None, "net_debit must be nulled for credit strategy"


# ---------------------------------------------------------------------------
# engine_gate_status in normalized output
# ---------------------------------------------------------------------------

class TestEngineGateStatus:
    """normalize_trade must populate engine_gate_status from selection_reasons."""

    def test_accepted_trade_has_passed_status(self):
        from app.utils.normalize import normalize_trade

        trade = {
            "spread_type": "iron_condor",
            "symbol": "SPY",
            "expiration": "2026-06-01",
            "net_credit": 2.50,
            "selection_reasons": [],  # accepted — no rejections
        }
        result = normalize_trade(trade, strategy_id="iron_condor")
        egs = result.get("engine_gate_status")
        assert egs is not None, "engine_gate_status must be present"
        assert egs["passed"] is True
        assert egs["failed_reasons"] == []

    def test_rejected_trade_has_failed_status(self):
        from app.utils.normalize import normalize_trade

        trade = {
            "spread_type": "iron_condor",
            "symbol": "SPY",
            "expiration": "2026-06-01",
            "net_credit": 2.50,
            "selection_reasons": ["ev_to_risk_below_floor", "pop_below_floor"],
        }
        result = normalize_trade(trade, strategy_id="iron_condor")
        egs = result.get("engine_gate_status")
        assert egs is not None
        assert egs["passed"] is False
        assert "ev_to_risk_below_floor" in egs["failed_reasons"]


# ---------------------------------------------------------------------------
# Reference trade regression (the exact scenario that triggered this fix)
# ---------------------------------------------------------------------------

class TestReferenceTradeRegression:
    """The specific trade from the bug report must now be rejected."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.iron_condor import IronCondorStrategyPlugin
        return IronCondorStrategyPlugin()

    def test_spy_negative_ev_trade_rejected_strict(self, plugin):
        """SPY P625/620 + C708/713, ev_to_risk=-0.29, expected_value=-120.42.

        This trade previously passed STRICT because no EV gate existed.
        It must now be rejected with ev_to_risk_below_floor.
        """
        candidate = _make_ic_candidate(
            symbol="SPY",
            underlying_price=660.0,
            expiration="2026-03-20",
            dte=21,
            ev_to_risk=-0.2912,
            ev_per_share=-120.42,
            expected_value=-120.42,
            p_win_used=0.45,
            return_on_risk=0.21,
            total_credit=0.865,
            net_credit=0.865,
            _request={
                "min_ev_to_risk": 0.05,
                "min_pop": 0.55,
                "data_quality_mode": "strict",
            },
        )
        ok, reasons = plugin.evaluate(candidate)
        assert not ok, "Reference negative-EV trade must be rejected under STRICT"
        assert "ev_to_risk_below_floor" in reasons
