"""Tests for the expected_fill pricing model.

Covers:
  1. compute_expected_fill — core blending, clamping, strategy resolution
  2. recompute_fill_economics — credit + debit fill-based metrics
  3. apply_expected_fill — convenience integration with trade dicts
  4. build_fill_trace — trace-level aggregation
  5. Edge cases — None inputs, zero spreads, boundary weights
"""

from __future__ import annotations

import pytest

from app.utils.expected_fill import (
    FILL_STRATEGY_DEFAULTS,
    _is_credit_strategy,
    _resolve_strategy_key,
    apply_expected_fill,
    build_fill_trace,
    compute_expected_fill,
    recompute_fill_economics,
)


# ─────────────────────────────────────────────────────────────────────────
# 1. compute_expected_fill
# ─────────────────────────────────────────────────────────────────────────

class TestComputeExpectedFill:
    """Core fill computation tests."""

    def test_credit_spread_basic(self):
        """2-leg credit spread: fill should be between natural and mid."""
        result = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.80,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
        )
        assert result is not None
        fp = result["expected_fill_price"]
        # Fill should be between natural (0.80) and mid (1.00)
        assert 0.80 <= fp <= 1.00
        # Weight should be in the credit_spread range
        assert result["expected_fill_weight_w"] > 0
        assert result["expected_fill_basis"] == "expected_fill"

    def test_debit_spread_basic(self):
        """2-leg debit spread: fill should be between mid and natural (ask)."""
        result = compute_expected_fill(
            spread_mid=2.00,
            spread_natural=2.40,
            strategy="call_debit",
            is_credit=False,
            leg_count=2,
        )
        assert result is not None
        fp = result["expected_fill_price"]
        # For debit: fill should be between mid (2.00) and natural (2.40)
        assert 2.00 <= fp <= 2.40

    def test_iron_condor_4_leg(self):
        """4-leg IC: larger leg penalty → lower w → fill closer to natural."""
        result = compute_expected_fill(
            spread_mid=1.50,
            spread_natural=1.20,
            strategy="iron_condor",
            is_credit=True,
            leg_count=4,
        )
        assert result is not None
        fp = result["expected_fill_price"]
        assert 1.20 <= fp <= 1.50
        # IC w should be lower than credit spread w (more legs)
        assert result["expected_fill_weight_w"] <= FILL_STRATEGY_DEFAULTS["credit_spread"]["base_w"]

    def test_butterfly_3_leg(self):
        """3-leg debit butterfly."""
        result = compute_expected_fill(
            spread_mid=0.50,
            spread_natural=0.70,
            strategy="butterflies",
            spread_type="debit_call_butterfly",
            is_credit=False,
            leg_count=3,
        )
        assert result is not None
        fp = result["expected_fill_price"]
        # Debit: fill ≥ mid
        assert fp >= 0.50
        assert fp <= 0.70

    def test_iron_butterfly_credit(self):
        """4-leg iron butterfly (credit)."""
        result = compute_expected_fill(
            spread_mid=3.00,
            spread_natural=2.60,
            strategy="butterflies",
            spread_type="iron_butterfly",
            is_credit=True,
            leg_count=4,
        )
        assert result is not None
        fp = result["expected_fill_price"]
        # Credit: fill ≤ mid
        assert fp <= 3.00
        assert fp >= 2.60

    def test_credit_clamp_never_better_than_mid(self):
        """Credit fill must never exceed mid."""
        result = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.80,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
        )
        assert result is not None
        assert result["expected_fill_price"] <= 1.00

    def test_debit_clamp_never_better_than_mid(self):
        """Debit fill must never go below mid."""
        result = compute_expected_fill(
            spread_mid=2.00,
            spread_natural=2.50,
            strategy="call_debit",
            is_credit=False,
            leg_count=2,
        )
        assert result is not None
        assert result["expected_fill_price"] >= 2.00

    def test_returns_none_when_mid_missing(self):
        """Should return None when mid is missing."""
        result = compute_expected_fill(
            spread_mid=None,
            spread_natural=0.80,
            strategy="put_credit_spread",
            is_credit=True,
        )
        assert result is None

    def test_returns_none_when_natural_missing(self):
        """Should return None when natural is missing."""
        result = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=None,
            strategy="put_credit_spread",
            is_credit=True,
        )
        assert result is None

    def test_slippage_metrics(self):
        """Slippage fields should be correctly computed."""
        result = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.80,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
        )
        assert result is not None
        assert result["slippage_vs_mid"] >= 0
        assert result["slippage_pct"] >= 0

    def test_fill_confidence_high(self):
        """High w → high confidence."""
        result = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.95,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
            liquidity_score=0.9,
            bid_ask_spread_pct=0.05,
        )
        assert result is not None
        assert result["fill_confidence"] == "high"

    def test_fill_confidence_low(self):
        """Very wide spread → low confidence."""
        result = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.30,
            strategy="iron_condor",
            is_credit=True,
            leg_count=4,
            bid_ask_spread_pct=5.0,
        )
        assert result is not None
        assert result["fill_confidence"] in ("low", "medium")

    def test_liquidity_boost(self):
        """High liquidity_score should give slightly better fill."""
        result_low = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.80,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
            liquidity_score=0.3,
        )
        result_high = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.80,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
            liquidity_score=0.9,
        )
        assert result_low is not None and result_high is not None
        # Higher liquidity → higher w → fill closer to mid for credits
        assert result_high["expected_fill_weight_w"] >= result_low["expected_fill_weight_w"]

    def test_spread_pct_penalty(self):
        """Higher bid_ask_spread_pct should degrade fill quality."""
        result_tight = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.80,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
            bid_ask_spread_pct=0.1,
        )
        result_wide = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.80,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
            bid_ask_spread_pct=3.0,
        )
        assert result_tight is not None and result_wide is not None
        assert result_tight["expected_fill_weight_w"] > result_wide["expected_fill_weight_w"]

    def test_detail_trace_present(self):
        """Debug trace should include all intermediate values."""
        result = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.80,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
        )
        assert result is not None
        detail = result["_fill_detail"]
        assert "strategy_key" in detail
        assert "base_w" in detail
        assert "mid" in detail
        assert "natural" in detail
        assert "raw_fill" in detail
        assert "is_credit" in detail


# ─────────────────────────────────────────────────────────────────────────
# 2. recompute_fill_economics
# ─────────────────────────────────────────────────────────────────────────

class TestRecomputeFillEconomics:

    def test_credit_fill_economics(self):
        """Credit strategy fill economics."""
        trade = {"width": 5.0, "p_win_used": 0.80}
        result = recompute_fill_economics(trade, expected_fill_price=0.90, is_credit=True)

        # max_profit_fill = 0.90 * 100 = 90
        assert result["max_profit_fill"] == 90.0
        # max_loss_fill = (5.0 - 0.90) * 100 = 410
        assert result["max_loss_fill"] == 410.0
        # ror_fill = 90 / 410
        assert result["ror_fill"] is not None and result["ror_fill"] > 0
        # ev_fill = 0.80 * 90 - 0.20 * 410 = 72 - 82 = -10
        assert result["ev_fill"] is not None
        # ev_to_risk_fill = ev_fill / max_loss_fill
        assert result["ev_to_risk_fill"] is not None

    def test_debit_fill_economics(self):
        """Debit strategy fill economics."""
        trade = {"width": 5.0, "p_win_used": 0.55}
        result = recompute_fill_economics(trade, expected_fill_price=2.20, is_credit=False)

        # max_profit_fill = (5.0 - 2.20) * 100 = 280
        assert result["max_profit_fill"] == 280.0
        # max_loss_fill = 2.20 * 100 = 220
        assert result["max_loss_fill"] == 220.0
        assert result["ror_fill"] is not None
        assert result["ev_fill"] is not None
        assert result["ev_to_risk_fill"] is not None

    def test_missing_width(self):
        """No width → all fill economics None."""
        trade = {"p_win_used": 0.80}
        result = recompute_fill_economics(trade, expected_fill_price=0.90, is_credit=True)
        assert result["max_profit_fill"] is None
        assert result["max_loss_fill"] is None

    def test_missing_pop(self):
        """Missing POP → EV fields None but max_profit/loss still computed."""
        trade = {"width": 5.0}
        result = recompute_fill_economics(trade, expected_fill_price=0.90, is_credit=True)
        assert result["max_profit_fill"] is not None
        assert result["ev_fill"] is None
        assert result["ev_to_risk_fill"] is None

    def test_butterfly_wing_width(self):
        """Butterflies use wing_width instead of width."""
        trade = {"wing_width": 5.0, "p_win_used": 0.10}
        result = recompute_fill_economics(trade, expected_fill_price=0.50, is_credit=False)
        assert result["max_profit_fill"] == 450.0  # (5.0 - 0.50) * 100
        assert result["max_loss_fill"] == 50.0  # 0.50 * 100


# ─────────────────────────────────────────────────────────────────────────
# 3. apply_expected_fill (convenience integration)
# ─────────────────────────────────────────────────────────────────────────

class TestApplyExpectedFill:

    def test_credit_spread_trade(self):
        """Full integration test for a credit spread trade dict."""
        trade = {
            "strategy": "put_credit_spread",
            "spread_type": "put_credit_spread",
            "net_credit": 1.00,
            "spread_bid": 0.85,
            "spread_ask": 1.15,
            "spread_mid": 1.00,
            "width": 5.0,
            "p_win_used": 0.82,
            "ev_to_risk": 0.05,
            "return_on_risk": 0.25,
            "max_profit": 100.0,
            "max_loss": 400.0,
            "ev_per_contract": 20.0,
            "legs": [
                {"name": "short_put", "bid": 1.50, "ask": 1.60},
                {"name": "long_put", "bid": 0.50, "ask": 0.60},
            ],
        }
        result = apply_expected_fill(trade)
        assert result is not None

        # Check fields were added to trade
        assert trade["expected_fill_price"] is not None
        assert trade["expected_fill_basis"] == "expected_fill"
        assert trade["fill_confidence"] in ("high", "medium", "low")

        # Check _mid aliases
        assert trade["max_profit_mid"] == 100.0
        assert trade["max_loss_mid"] == 400.0
        assert trade["ror_mid"] == 0.25
        assert trade["ev_to_risk_mid"] == 0.05

        # Check fill economics
        assert trade["max_profit_fill"] is not None
        assert trade["max_loss_fill"] is not None
        assert trade["ror_fill"] is not None
        assert trade["ev_fill"] is not None
        assert trade["ev_to_risk_fill"] is not None

    def test_iron_condor_trade(self):
        """IC trade: spread_mid derived from net_credit, natural from spread_bid."""
        trade = {
            "strategy": "iron_condor",
            "spread_type": "iron_condor",
            "net_credit": 1.50,
            "spread_bid": 1.20,
            "spread_ask": 1.80,
            "width": 5.0,
            "p_win_used": 0.75,
            "ev_to_risk": 0.03,
            "return_on_risk": 0.42,
            "max_profit": 150.0,
            "max_loss": 350.0,
            "liquidity_score": 0.65,
            "bid_ask_spread_pct": 0.40,
            "legs": [
                {"name": "long_put"},
                {"name": "short_put"},
                {"name": "short_call"},
                {"name": "long_call"},
            ],
        }
        result = apply_expected_fill(trade)
        assert result is not None
        assert trade["expected_fill_price"] <= 1.50  # credit clamp
        assert trade["expected_fill_price"] >= 1.20  # at least natural

    def test_debit_spread_trade(self):
        """Debit spread: spread_natural derived from spread_ask."""
        trade = {
            "strategy": "call_debit",
            "spread_type": "call_debit",
            "net_debit": 2.00,
            "spread_bid": 1.80,
            "spread_ask": 2.20,
            "spread_mid": 2.00,
            "width": 5.0,
            "p_win_used": 0.55,
            "ev_to_risk": 0.01,
            "return_on_risk": 1.50,
            "max_profit": 300.0,
            "max_loss": 200.0,
            "legs": [
                {"name": "long_call"},
                {"name": "short_call"},
            ],
        }
        result = apply_expected_fill(trade)
        assert result is not None
        assert trade["expected_fill_price"] >= 2.00  # debit clamp

    def test_butterfly_trade(self):
        """Butterfly with explicit spread_mid and spread_natural."""
        trade = {
            "strategy": "butterflies",
            "spread_type": "debit_call_butterfly",
            "spread_mid": 0.50,
            "spread_natural": 0.70,
            "net_debit": 0.50,
            "wing_width": 5.0,
            "p_win_used": 0.08,
            "ev_to_risk": 0.15,
            "return_on_risk": 9.0,
            "max_profit": 450.0,
            "max_loss": 50.0,
            "expected_value": 36.0,
            "legs": [
                {"name": "lower"},
                {"name": "center"},
                {"name": "upper"},
            ],
        }
        result = apply_expected_fill(trade)
        assert result is not None
        assert trade["expected_fill_price"] >= 0.50  # debit clamp

    def test_missing_pricing_returns_none(self):
        """Trade without spread pricing → fill unavailable."""
        trade = {
            "strategy": "put_credit_spread",
            "spread_type": "put_credit_spread",
            "width": 5.0,
            "p_win_used": 0.80,
        }
        result = apply_expected_fill(trade)
        assert result is None
        assert trade["expected_fill_price"] is None
        assert trade["_fill_unavailable"] is True

    def test_idempotent_mid_aliases(self):
        """Calling apply_expected_fill twice should not overwrite _mid aliases."""
        trade = {
            "strategy": "put_credit_spread",
            "spread_mid": 1.00,
            "spread_bid": 0.85,
            "width": 5.0,
            "p_win_used": 0.80,
            "max_profit": 100.0,
            "max_loss": 400.0,
            "return_on_risk": 0.25,
            "ev_to_risk": 0.05,
            "ev_per_contract": 20.0,
            "legs": [{"name": "short_put"}, {"name": "long_put"}],
        }
        apply_expected_fill(trade)
        first_mid = trade["max_profit_mid"]

        # Calling again should not change _mid values
        trade["max_profit"] = 999.0  # corrupt the original field
        apply_expected_fill(trade)
        assert trade["max_profit_mid"] == first_mid  # not overwritten


# ─────────────────────────────────────────────────────────────────────────
# 4. build_fill_trace
# ─────────────────────────────────────────────────────────────────────────

class TestBuildFillTrace:

    def _make_trade(self, **overrides):
        base = {
            "strategy": "put_credit_spread",
            "spread_mid": 1.00,
            "spread_bid": 0.85,
            "width": 5.0,
            "p_win_used": 0.80,
            "max_profit": 100.0,
            "max_loss": 400.0,
            "return_on_risk": 0.25,
            "ev_to_risk": 0.05,
            "ev_per_contract": 20.0,
            "trade_key": "SPY|2025-03-21|put_credit_spread|550/545|30",
            "legs": [{"name": "short_put"}, {"name": "long_put"}],
        }
        base.update(overrides)
        apply_expected_fill(base)
        return base

    def test_trace_structure(self):
        """Trace should have all three sections."""
        trades = [self._make_trade(), self._make_trade(spread_bid=0.90)]
        passed = [trades[0]]
        trace = build_fill_trace(trades, passed)

        assert "fill_model_summary" in trace
        assert "fill_impact" in trace
        assert "fill_samples" in trace

    def test_fill_model_summary_counts(self):
        """Summary should count fill-available vs unavailable."""
        trades = [
            self._make_trade(),
            self._make_trade(),
            {"strategy": "put_credit_spread", "expected_fill_price": None},
        ]
        trace = build_fill_trace(trades, [])
        summary = trace["fill_model_summary"]
        assert summary["total_enriched"] == 3
        assert summary["fill_computed"] == 2
        assert summary["fill_unavailable"] == 1

    def test_fill_samples_top_slippage(self):
        """Top slippage sample should be included."""
        trades = [
            self._make_trade(spread_bid=0.40),  # large slippage
            self._make_trade(spread_bid=0.95),  # small slippage
        ]
        trace = build_fill_trace(trades, trades)
        samples = trace["fill_samples"]
        assert "top_slippage" in samples

    def test_empty_trades(self):
        """Empty trade list should not crash."""
        trace = build_fill_trace([], [])
        assert trace["fill_model_summary"]["total_enriched"] == 0


# ─────────────────────────────────────────────────────────────────────────
# 5. Strategy resolution helpers
# ─────────────────────────────────────────────────────────────────────────

class TestStrategyResolution:

    def test_credit_spread_resolution(self):
        assert _resolve_strategy_key("put_credit_spread", None) == "credit_spread"

    def test_iron_condor_resolution(self):
        assert _resolve_strategy_key("iron_condor", "iron_condor") == "iron_condor"

    def test_debit_spread_resolution(self):
        assert _resolve_strategy_key("call_debit", "call_debit") == "debit_spread"

    def test_debit_butterfly_resolution(self):
        assert _resolve_strategy_key("butterflies", "debit_call_butterfly") == "debit_butterfly"

    def test_iron_butterfly_resolution(self):
        assert _resolve_strategy_key("butterflies", "iron_butterfly") == "iron_butterfly"

    def test_fallback_resolution(self):
        assert _resolve_strategy_key("unknown_strategy", None) == "fallback"

    def test_is_credit_credit_spread(self):
        assert _is_credit_strategy("put_credit_spread", "put_credit_spread") is True

    def test_is_credit_iron_condor(self):
        assert _is_credit_strategy("iron_condor", "iron_condor") is True

    def test_is_debit(self):
        assert _is_credit_strategy("call_debit", "call_debit") is False

    def test_is_debit_butterfly(self):
        assert _is_credit_strategy("butterflies", "debit_call_butterfly") is False

    def test_is_credit_iron_butterfly(self):
        assert _is_credit_strategy("butterflies", "iron_butterfly") is True


# ─────────────────────────────────────────────────────────────────────────
# 6. Edge cases
# ─────────────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_mid_equals_natural(self):
        """When mid == natural, fill should equal mid."""
        result = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=1.00,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
        )
        assert result is not None
        assert result["expected_fill_price"] == 1.00
        assert result["slippage_vs_mid"] == 0.0

    def test_zero_spread_mid(self):
        """Zero mid should still compute (edge case)."""
        result = compute_expected_fill(
            spread_mid=0.0,
            spread_natural=0.0,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
        )
        assert result is not None
        assert result["expected_fill_price"] == 0.0

    def test_negative_leg_count(self):
        """Negative leg_count should be treated as 0 extra legs."""
        result = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.80,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=0,  # unusual but shouldn't crash
        )
        assert result is not None

    def test_extreme_spread_pct(self):
        """Extreme bid_ask_spread_pct should clamp w to min."""
        result = compute_expected_fill(
            spread_mid=1.00,
            spread_natural=0.20,
            strategy="put_credit_spread",
            is_credit=True,
            leg_count=2,
            bid_ask_spread_pct=100.0,
        )
        assert result is not None
        # Should be clamped to min_w
        assert result["expected_fill_weight_w"] == FILL_STRATEGY_DEFAULTS["credit_spread"]["min_w"]
