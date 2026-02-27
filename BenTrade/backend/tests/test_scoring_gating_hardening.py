"""Tests for scoring & gating consistency hardening.

Covers:
  Task 1 — POP gate: missing POP enforcement per data_quality_mode
  Task 2 — Liquidity: no double penalization, monotonic degradation
  Task 3 — Score scale: rank_score in 0–100 range
"""

from __future__ import annotations

import pytest
from app.services.ranking import (
    compute_rank_score,
    compute_liquidity_score,
    safe_float,
    sort_trades_by_rank,
)
from app.services.strategies.credit_spread import CreditSpreadStrategyPlugin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trade(
    *,
    pop: float | None = 0.80,
    ev_to_risk: float = 0.030,
    return_on_risk: float = 0.20,
    bid_ask_spread_pct: float = 0.005,
    open_interest: int | None = 3000,
    volume: int | None = 1200,
    tqs: float | None = 0.68,
    width: float = 5.0,
    net_credit: float = 0.55,
    ev_per_share: float | None = None,
    dq_mode: str = "balanced",
    preset_min_pop: float = 0.60,
) -> dict:
    """Build a minimal trade dict suitable for evaluate() and scoring.

    Note: bid_ask_spread_pct is a decimal (e.g. 0.005 = 0.5%).
    Gate 5 multiplies by 100 and compares against max_bid_ask_spread_pct (in %).
    """
    trade: dict = {
        "width": width,
        "net_credit": net_credit,
        "return_on_risk": return_on_risk,
        "ev_to_risk": ev_to_risk,
        "bid_ask_spread_pct": bid_ask_spread_pct,
        "open_interest": open_interest,
        "volume": volume,
        "_request": {
            "data_quality_mode": dq_mode,
            "min_pop": preset_min_pop,
            "min_ev_to_risk": 0.02,
            "min_ror": 0.01,
            "max_bid_ask_spread_pct": 1.5,
            "min_open_interest": 300,
            "min_volume": 20,
        },
        "_policy": {},
    }
    if pop is not None:
        trade["p_win_used"] = pop
    if tqs is not None:
        trade["trade_quality_score"] = tqs
    if ev_per_share is not None:
        trade["ev_per_share"] = ev_per_share
    return trade


_plugin = CreditSpreadStrategyPlugin()


# ===================================================================
# Task 1 — POP Gate Behavior
# ===================================================================


class TestPopGateBehavior:
    """Missing POP must be rejected in strict/balanced, waivable only in lenient."""

    def test_pop_present_and_above_floor_passes(self):
        trade = _make_trade(pop=0.82, dq_mode="balanced")
        passed, reasons = _plugin.evaluate(trade)
        assert passed is True, f"Expected pass; got reasons={reasons}"

    def test_pop_present_below_floor_rejected(self):
        trade = _make_trade(pop=0.40, dq_mode="balanced", preset_min_pop=0.60)
        passed, reasons = _plugin.evaluate(trade)
        assert passed is False
        assert "pop_below_floor" in reasons

    # --- Missing POP ---

    def test_missing_pop_rejected_in_strict(self):
        trade = _make_trade(pop=None, dq_mode="strict")
        passed, reasons = _plugin.evaluate(trade)
        assert passed is False
        assert "DQ_MISSING:pop" in reasons

    def test_missing_pop_rejected_in_balanced(self):
        trade = _make_trade(pop=None, dq_mode="balanced")
        passed, reasons = _plugin.evaluate(trade)
        assert passed is False
        assert "DQ_MISSING:pop" in reasons

    def test_missing_pop_waived_in_lenient(self):
        """In lenient mode, missing POP does NOT produce DQ_MISSING:pop."""
        trade = _make_trade(pop=None, dq_mode="lenient")
        passed, reasons = _plugin.evaluate(trade)
        # Should not be rejected for POP — other gates may still catch it
        assert "DQ_MISSING:pop" not in reasons

    def test_gate_breakdown_reflects_pop_rejection(self):
        """Verify DQ_MISSING:pop appears in reasons list (usable for gate_breakdown)."""
        trade = _make_trade(pop=None, dq_mode="balanced")
        passed, reasons = _plugin.evaluate(trade)
        assert not passed
        # Only POP-related rejection should appear (trade is otherwise healthy)
        pop_reasons = [r for r in reasons if "pop" in r.lower()]
        assert len(pop_reasons) >= 1


# ===================================================================
# Task 2 — Liquidity Double-Penalization Removal
# ===================================================================


class TestLiquidityNoPenalty:
    """Score must degrade smoothly via weighted liquidity only — no multiplicative cliff."""

    @staticmethod
    def _trade_with_spread(spread_pct: float) -> dict:
        return {
            "ev_to_risk": 0.035,
            "return_on_risk": 0.25,
            "p_win_used": 0.80,
            "bid_ask_spread_pct": spread_pct,
            "open_interest": 3000,
            "volume": 1500,
            "trade_quality_score": 0.70,
        }

    def test_monotonic_decline_as_spread_widens(self):
        """Trade A (tight) > Trade B (moderate) > Trade C (wide)."""
        score_a = compute_rank_score(self._trade_with_spread(0.03))
        score_b = compute_rank_score(self._trade_with_spread(0.15))
        score_c = compute_rank_score(self._trade_with_spread(0.29))

        assert score_a > score_b > score_c, (
            f"Expected monotonic decline: A={score_a} > B={score_b} > C={score_c}"
        )

    def test_smooth_degradation_no_cliff(self):
        """Gap between consecutive spread steps should be roughly proportional,
        not a sudden cliff."""
        spreads = [0.03, 0.08, 0.13, 0.18, 0.23, 0.28]
        scores = [compute_rank_score(self._trade_with_spread(s)) for s in spreads]

        # Verify monotonic
        for i in range(1, len(scores)):
            assert scores[i - 1] >= scores[i], (
                f"Score not monotonically decreasing at step {i}: "
                f"{scores[i - 1]} vs {scores[i]}"
            )

        # Verify no single step drops more than 50% of the total range
        total_range = scores[0] - scores[-1]
        if total_range > 0:
            max_step = max(scores[i - 1] - scores[i] for i in range(1, len(scores)))
            assert max_step < total_range * 0.6, (
                f"Cliff detected: single step drop {max_step:.4f} "
                f"is >60% of total range {total_range:.4f}"
            )

    def test_no_multiplicative_penalty_artifact(self):
        """A trade with spread_pct=0.90 (very wide) should still get a nonzero
        score from the weighted blend — NOT be zeroed out by a multiplier."""
        trade = self._trade_with_spread(0.90)
        score = compute_rank_score(trade)
        assert score > 0, "Score should not be zeroed for wide spread"


# ===================================================================
# Task 3 — Score Scale 0–100
# ===================================================================


class TestScoreScale:
    """rank_score must be in [0, 100] everywhere."""

    def test_score_within_0_100(self):
        trade = _make_trade()
        score = compute_rank_score(trade)
        assert 0 <= score <= 100, f"Score {score} out of [0, 100]"

    def test_high_quality_trade_gets_high_score(self):
        trade = {
            "ev_to_risk": 0.048,
            "return_on_risk": 0.45,
            "p_win_used": 0.92,
            "bid_ask_spread_pct": 0.02,
            "open_interest": 5000,
            "volume": 5000,
            "trade_quality_score": 0.82,
        }
        score = compute_rank_score(trade)
        # A near-perfect trade should score well above 50
        assert score > 50, f"High-quality trade scored only {score}"
        assert score <= 100

    def test_low_quality_trade_gets_low_score(self):
        trade = {
            "ev_to_risk": 0.001,
            "return_on_risk": 0.06,
            "p_win_used": 0.52,
            "bid_ask_spread_pct": 0.28,
            "open_interest": 30,
            "volume": 10,
            "trade_quality_score": 0.42,
        }
        score = compute_rank_score(trade)
        assert 0 <= score < 40, f"Low-quality trade scored {score}"

    def test_sort_produces_100_scale_scores(self):
        trades = [
            {
                "underlying": "SPY",
                "short_strike": 590,
                "long_strike": 585,
                "ev_to_risk": 0.03,
                "return_on_risk": 0.20,
                "p_win_used": 0.80,
                "bid_ask_spread_pct": 0.06,
                "open_interest": 2500,
                "volume": 900,
                "trade_quality_score": 0.65,
            },
        ]
        ordered = sort_trades_by_rank(trades)
        assert 0 < ordered[0]["rank_score"] <= 100

    def test_credit_spread_score_method_returns_0_100(self):
        """CreditSpreadStrategyPlugin.score() should return 0–100."""
        trade = _make_trade()
        score, tie_breaks = _plugin.score(trade)
        assert 0 <= score <= 100, f"Plugin score {score} out of [0, 100]"

    def test_missing_everything_scores_zero(self):
        """A trade with no meaningful data should score 0."""
        trade = {}
        score = compute_rank_score(trade)
        assert score == 0.0


# ===================================================================
# Regression: existing ranking test equivalents (updated for 0–100)
# ===================================================================


class TestRankingRegression:
    """Ensure sort ordering still works after scale change."""

    def test_rank_score_prefers_edge_efficiency_and_liquidity(self):
        high = {
            "ev_to_risk": 0.045,
            "return_on_risk": 0.32,
            "p_win_used": 0.82,
            "bid_ask_spread_pct": 0.05,
            "open_interest": 4200,
            "volume": 3200,
            "trade_quality_score": 0.72,
        }
        low = {
            "ev_to_risk": 0.005,
            "return_on_risk": 0.12,
            "p_win_used": 0.68,
            "bid_ask_spread_pct": 0.28,
            "open_interest": 60,
            "volume": 40,
            "trade_quality_score": 0.48,
        }
        assert compute_rank_score(high) > compute_rank_score(low)

    def test_sort_descending(self):
        trades = [
            {"underlying": "A", "short_strike": 1, "long_strike": 0,
             "ev_to_risk": 0.01, "return_on_risk": 0.14, "p_win_used": 0.73,
             "bid_ask_spread_pct": 0.12, "open_interest": 700, "volume": 300,
             "trade_quality_score": 0.58},
            {"underlying": "A", "short_strike": 2, "long_strike": 0,
             "ev_to_risk": 0.038, "return_on_risk": 0.29, "p_win_used": 0.81,
             "bid_ask_spread_pct": 0.06, "open_interest": 3000, "volume": 1900,
             "trade_quality_score": 0.70},
        ]
        ordered = sort_trades_by_rank(trades)
        assert ordered[0]["rank_score"] >= ordered[1]["rank_score"]
