"""Tests for butterfly spread pricing, payoff, probabilities, and execution gates.

Covers:
- spread_mid / spread_natural / spread_mark computation
- net_debit populated and consistent with max_profit / max_loss
- prob_touch_center is None (not hardcoded 1.0)
- execution_invalid when pricing is missing
- rank_score = 0 when execution_invalid
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_leg(bid: float | None, ask: float | None, **kwargs: Any) -> SimpleNamespace:
    """Create a minimal option leg stub with bid/ask and optional greeks."""
    defaults = {
        "strike": kwargs.pop("strike", 100.0),
        "option_type": kwargs.pop("option_type", "call"),
        "open_interest": kwargs.pop("open_interest", 500),
        "volume": kwargs.pop("volume", 50),
        "delta": kwargs.pop("delta", 0.5),
        "gamma": kwargs.pop("gamma", 0.02),
        "theta": kwargs.pop("theta", -0.03),
        "vega": kwargs.pop("vega", 0.10),
        "iv": kwargs.pop("iv", 0.20),
        "symbol": kwargs.pop("symbol", "QQQ260306C00602000"),
    }
    defaults.update(kwargs)
    return SimpleNamespace(bid=bid, ask=ask, **defaults)


def _build_debit_butterfly_candidate(
    lower_leg: SimpleNamespace,
    center_leg: SimpleNamespace,
    upper_leg: SimpleNamespace,
    *,
    spot: float = 605.0,
    dte: int = 7,
    wing_width: float = 5.0,
    spread_type: str = "debit_call_butterfly",
) -> dict[str, Any]:
    """Build a single debit butterfly candidate dict (as build_candidates would)."""
    return {
        "strategy": "butterflies",
        "spread_type": spread_type,
        "butterfly_type": "debit",
        "option_side": "call",
        "symbol": "QQQ",
        "expiration": "2026-03-06",
        "dte": dte,
        "underlying_price": spot,
        "center_strike": 607.0,
        "lower_strike": 602.0,
        "upper_strike": 612.0,
        "wing_width": wing_width,
        "expected_move": 8.0,
        "center_mode": "spot",
        "lower_leg": lower_leg,
        "center_leg": center_leg,
        "upper_leg": upper_leg,
        "snapshot": {"symbol": "QQQ", "expiration": "2026-03-06"},
    }


def _enrich_one(candidate: dict[str, Any]) -> dict[str, Any] | None:
    """Run enrich on a single candidate and return the enriched row (or None)."""
    plugin = ButterfliesStrategyPlugin()
    enriched = plugin.enrich([candidate], {"policy": {}})
    return enriched[0] if enriched else None


# ---------------------------------------------------------------------------
# Test: spread_mid computed correctly for the example trade
# ---------------------------------------------------------------------------

class TestDebitButterflyPricing:
    """QQQ 602/607/612 debit call butterfly, 7 DTE."""

    @pytest.fixture()
    def example_legs(self):
        # lower strike 602: buy 1
        lower = _make_leg(bid=4.80, ask=5.10, strike=602, option_type="call")
        # center strike 607: sell 2
        center = _make_leg(bid=2.50, ask=2.70, strike=607, option_type="call")
        # upper strike 612: buy 1
        upper = _make_leg(bid=0.90, ask=1.10, strike=612, option_type="call")
        return lower, center, upper

    @pytest.fixture()
    def enriched(self, example_legs):
        lower, center, upper = example_legs
        cand = _build_debit_butterfly_candidate(lower, center, upper)
        result = _enrich_one(cand)
        assert result is not None, "enrich dropped the candidate unexpectedly"
        return result

    def test_spread_mid(self, enriched):
        # spread_mid = mid(lower) + mid(upper) - 2*mid(center)
        # mid(lower) = (4.80+5.10)/2 = 4.95
        # mid(center) = (2.50+2.70)/2 = 2.60
        # mid(upper) = (0.90+1.10)/2 = 1.00
        # spread_mid = 4.95 + 1.00 - 2*2.60 = 0.75
        assert enriched["spread_mid"] == pytest.approx(0.75, abs=0.01)

    def test_spread_natural(self, enriched):
        # spread_natural = ask(lower) + ask(upper) - 2*bid(center)
        # = 5.10 + 1.10 - 2*2.50 = 1.20
        assert enriched["spread_natural"] == pytest.approx(1.20, abs=0.01)

    def test_spread_mark(self, enriched):
        # spread_mark mirrors spread_mid
        assert enriched["spread_mark"] == enriched["spread_mid"]

    def test_net_debit_populated(self, enriched):
        # net_debit should equal spread_mid
        assert enriched["net_debit"] is not None
        assert enriched["net_debit"] == enriched["spread_mid"]

    def test_net_credit_null(self, enriched):
        # debit strategy → net_credit must be None
        assert enriched["net_credit"] is None

    def test_max_profit_max_loss_consistent(self, enriched):
        # wing_width = 5, net_debit = spread_mid ≈ 0.75
        debit = enriched["net_debit"]
        assert enriched["max_loss"] == pytest.approx(debit * 100.0, abs=0.01)
        assert enriched["max_profit"] == pytest.approx((5.0 - debit) * 100.0, abs=0.01)

    def test_payoff_example_values(self, enriched):
        # Confirm the example: wing_width=5, debit≈0.75
        # max_profit ≈ 425, max_loss ≈ 75
        assert enriched["max_profit"] > 0
        assert enriched["max_loss"] > 0
        assert enriched["max_profit"] + enriched["max_loss"] == pytest.approx(500.0, abs=0.01)


# ---------------------------------------------------------------------------
# Test: probability_of_touch_center is None (not 1.0)
# ---------------------------------------------------------------------------

class TestProbabilityFields:

    @pytest.fixture()
    def enriched(self):
        lower = _make_leg(bid=4.80, ask=5.10, strike=602)
        center = _make_leg(bid=2.50, ask=2.70, strike=607)
        upper = _make_leg(bid=0.90, ask=1.10, strike=612)
        cand = _build_debit_butterfly_candidate(lower, center, upper)
        result = _enrich_one(cand)
        assert result is not None
        return result

    def test_prob_touch_center_is_null(self, enriched):
        """prob_touch_center must be None until a real touch model is built."""
        assert enriched["probability_of_touch_center"] is None

    def test_pop_butterfly_is_valid(self, enriched):
        """pop_butterfly should be a probability in (0, 1), not 1.0."""
        pop = enriched["pop_butterfly"]
        assert pop is not None
        assert 0.0 < pop < 1.0

    def test_p_win_used_equals_pop(self, enriched):
        """p_win_used should match pop_butterfly."""
        assert enriched["p_win_used"] == enriched["pop_butterfly"]

    def test_pop_model_used_is_normal_cdf(self, enriched):
        assert enriched["pop_model_used"] == "normal_cdf"


# ---------------------------------------------------------------------------
# Test: execution_invalid when pricing is missing
# ---------------------------------------------------------------------------

class TestExecutionInvalid:

    def test_missing_all_quotes(self):
        """All leg quotes = None → execution_invalid=True."""
        lower = _make_leg(bid=None, ask=None, strike=602)
        center = _make_leg(bid=None, ask=None, strike=607)
        upper = _make_leg(bid=None, ask=None, strike=612)
        cand = _build_debit_butterfly_candidate(lower, center, upper)
        result = _enrich_one(cand)
        assert result is not None
        assert result["execution_invalid"] is True
        assert result["spread_mid"] is None
        assert result["net_debit"] is None

    def test_missing_center_bid(self):
        """Missing center bid → spread_natural unavailable."""
        lower = _make_leg(bid=4.80, ask=5.10, strike=602)
        center = _make_leg(bid=None, ask=2.70, strike=607)
        upper = _make_leg(bid=0.90, ask=1.10, strike=612)
        cand = _build_debit_butterfly_candidate(lower, center, upper)
        result = _enrich_one(cand)
        assert result is not None
        # spread_mid should be None (center_mid = None)
        assert result["execution_invalid"] is True

    def test_rank_score_zero_when_invalid(self):
        """rank_score must be 0 for execution-invalid trades."""
        lower = _make_leg(bid=None, ask=None, strike=602)
        center = _make_leg(bid=None, ask=None, strike=607)
        upper = _make_leg(bid=None, ask=None, strike=612)
        cand = _build_debit_butterfly_candidate(lower, center, upper)
        result = _enrich_one(cand)
        assert result is not None
        assert result["rank_score"] == 0.0

    def test_evaluate_rejects_pricing_unavailable(self):
        """evaluate() should reject trades with missing pricing."""
        lower = _make_leg(bid=None, ask=None, strike=602)
        center = _make_leg(bid=None, ask=None, strike=607)
        upper = _make_leg(bid=None, ask=None, strike=612)
        cand = _build_debit_butterfly_candidate(lower, center, upper)
        result = _enrich_one(cand)
        assert result is not None

        plugin = ButterfliesStrategyPlugin()
        passed, reasons = plugin.evaluate(result)
        assert not passed
        # Must contain an execution_invalid or pricing_unavailable reason
        has_pricing_rejection = any(
            "execution_invalid" in r or "pricing_unavailable" in r
            for r in reasons
        )
        assert has_pricing_rejection, f"Expected pricing rejection, got: {reasons}"


# ---------------------------------------------------------------------------
# Test: valid trade passes evaluate
# ---------------------------------------------------------------------------

class TestValidTradePassesEvaluate:

    def test_good_trade_passes(self):
        """A well-priced butterfly should pass evaluate."""
        lower = _make_leg(bid=4.80, ask=5.10, strike=602, open_interest=1000, volume=100)
        center = _make_leg(bid=2.50, ask=2.70, strike=607, open_interest=1000, volume=100)
        upper = _make_leg(bid=0.90, ask=1.10, strike=612, open_interest=1000, volume=100)
        cand = _build_debit_butterfly_candidate(lower, center, upper)
        result = _enrich_one(cand)
        assert result is not None
        assert result["execution_invalid"] is False
        assert result["net_debit"] is not None
        assert result["spread_mid"] is not None

        # rank_score should be positive
        assert result["rank_score"] > 0


# ---------------------------------------------------------------------------
# Test: computed_metrics pop fallback does NOT use probability_of_touch_center
# ---------------------------------------------------------------------------

class TestComputedMetricsPopFallback:

    def test_touch_center_not_used_for_pop(self):
        """computed_metrics 'pop' must not fall back to probability_of_touch_center."""
        from app.utils.computed_metrics import build_computed_metrics  # noqa: E402

        trade = {
            "probability_of_touch_center": 1.0,  # bogus value
            # No pop, p_win_used, pop_delta_approx, pop_approx, pop_butterfly
        }
        metrics = build_computed_metrics(trade)
        # pop should be None since the only available field is touch_center
        # which is no longer in the fallback chain
        assert metrics["pop"] is None

    def test_pop_butterfly_used_for_pop(self):
        """computed_metrics 'pop' should fall back to pop_butterfly."""
        from app.utils.computed_metrics import build_computed_metrics  # noqa: E402

        trade = {
            "pop_butterfly": 0.1036,
            "probability_of_touch_center": None,
        }
        metrics = build_computed_metrics(trade)
        assert metrics["pop"] == pytest.approx(0.1036, abs=0.001)

    def test_p_win_used_preferred_over_pop_butterfly(self):
        """p_win_used should be preferred over pop_butterfly."""
        from app.utils.computed_metrics import build_computed_metrics  # noqa: E402

        trade = {
            "p_win_used": 0.25,
            "pop_butterfly": 0.10,
        }
        metrics = build_computed_metrics(trade)
        assert metrics["pop"] == pytest.approx(0.25, abs=0.001)


# ---------------------------------------------------------------------------
# Test: debit sanity — net_debit <= 0 or >= wing_width is invalid
# ---------------------------------------------------------------------------

class TestDebitSanity:

    def test_negative_debit_is_invalid(self):
        """If pricing results in net_debit <= 0 → execution_invalid."""
        # Center bid very high, so debit goes negative
        lower = _make_leg(bid=2.00, ask=2.10, strike=602)
        center = _make_leg(bid=5.00, ask=5.10, strike=607)
        upper = _make_leg(bid=2.00, ask=2.10, strike=612)
        cand = _build_debit_butterfly_candidate(lower, center, upper)
        result = _enrich_one(cand)
        assert result is not None
        assert result["execution_invalid"] is True

    def test_debit_ge_wing_width_is_invalid(self):
        """If net_debit >= wing_width → execution_invalid."""
        # Very expensive wings, cheap center
        lower = _make_leg(bid=10.00, ask=10.20, strike=602)
        center = _make_leg(bid=0.10, ask=0.20, strike=607)
        upper = _make_leg(bid=10.00, ask=10.20, strike=612)
        cand = _build_debit_butterfly_candidate(lower, center, upper, wing_width=5.0)
        result = _enrich_one(cand)
        assert result is not None
        assert result["execution_invalid"] is True
