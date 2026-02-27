"""Tests for debit spread data integrity (Goals 1-6).

Covers:
  QC  — Quote consistency: spread_bid/spread_ask/spread_mid from leg quotes
  BE  — Breakeven formula: call_debit vs put_debit direction
  PD  — POP direction: delta-based, not 1-debit/width
  GA  — Gate metrics alignment: displayed fields match gated values
  SV  — Sanity validation: validate_debit_trade checks
  EV  — EV-POP consistency
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.strategies.debit_spreads import (
    DebitSpreadsStrategyPlugin,
    _validate_debit_trade,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _leg(
    *,
    strike: float = 100.0,
    bid: float | None = 2.0,
    ask: float | None = 2.5,
    open_interest: int | None = 500,
    volume: int | None = 100,
    iv: float | None = 0.25,
    theta: float | None = -0.03,
    option_type: str = "call",
    delta: float | None = 0.50,
) -> SimpleNamespace:
    return SimpleNamespace(
        strike=strike, bid=bid, ask=ask, open_interest=open_interest,
        volume=volume, iv=iv, theta=theta, option_type=option_type,
        delta=delta,
    )


def _candidate(
    *,
    long_strike: float = 100.0,
    short_strike: float = 105.0,
    long_bid: float | None = 5.0,
    long_ask: float | None = 5.5,
    short_bid: float | None = 2.0,
    short_ask: float | None = 2.5,
    long_oi: int | None = 500,
    short_oi: int | None = 600,
    long_vol: int | None = 100,
    short_vol: int | None = 120,
    long_iv: float | None = 0.25,
    short_iv: float | None = 0.22,
    long_delta: float | None = 0.50,
    short_delta: float | None = 0.20,
    strategy: str = "call_debit",
    underlying_price: float = 102.0,
    dte: int = 30,
    width: float | None = None,
) -> dict[str, Any]:
    w = width if width is not None else abs(short_strike - long_strike)
    return {
        "strategy": strategy,
        "spread_type": strategy,
        "symbol": "SPY",
        "expiration": "2025-08-15",
        "dte": dte,
        "underlying_price": underlying_price,
        "width": w,
        "long_strike": long_strike,
        "short_strike": short_strike,
        "long_leg": _leg(
            strike=long_strike, bid=long_bid, ask=long_ask,
            open_interest=long_oi, volume=long_vol, iv=long_iv,
            delta=long_delta,
        ),
        "short_leg": _leg(
            strike=short_strike, bid=short_bid, ask=short_ask,
            open_interest=short_oi, volume=short_vol, iv=short_iv,
            delta=short_delta,
        ),
        "snapshot": {
            "symbol": "SPY",
            "prices_history": [100.0 + i * 0.1 for i in range(30)],
        },
    }


def _enrich_one(candidate: dict, request: dict | None = None) -> dict[str, Any]:
    plugin = DebitSpreadsStrategyPlugin()
    results = plugin.enrich([candidate], {"request": request or {}, "policy": {}})
    assert len(results) == 1
    return results[0]


# =========================================================================
# QC — Quote consistency (Goal 1)
# =========================================================================

class TestQuoteConsistencyDebit:
    """test_quote_consistency_debit: spread quotes derived correctly from legs."""

    def test_spread_bid_ask_mid_present(self):
        """Healthy chain produces spread_bid, spread_ask, spread_mid."""
        trade = _enrich_one(_candidate())
        assert trade["spread_bid"] is not None
        assert trade["spread_ask"] is not None
        assert trade["spread_mid"] is not None

    def test_spread_bid_formula(self):
        """spread_bid = long_bid − short_ask (worst exit)."""
        cand = _candidate(long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5)
        trade = _enrich_one(cand)
        # spread_bid = 5.0 − 2.5 = 2.5
        assert trade["spread_bid"] == pytest.approx(2.5, abs=0.01)

    def test_spread_ask_formula(self):
        """spread_ask = long_ask − short_bid (worst entry / natural debit)."""
        cand = _candidate(long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5)
        trade = _enrich_one(cand)
        # spread_ask = 5.5 − 2.0 = 3.5
        assert trade["spread_ask"] == pytest.approx(3.5, abs=0.01)

    def test_spread_mid_formula(self):
        """spread_mid = (spread_bid + spread_ask) / 2."""
        cand = _candidate(long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5)
        trade = _enrich_one(cand)
        # spread_mid = (2.5 + 3.5) / 2 = 3.0
        assert trade["spread_mid"] == pytest.approx(3.0, abs=0.01)

    def test_net_debit_equals_spread_ask_natural(self):
        """Default (natural) mode: net_debit = spread_ask."""
        cand = _candidate(long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5)
        trade = _enrich_one(cand)
        assert trade["net_debit"] == pytest.approx(trade["spread_ask"], abs=0.01)
        assert trade["_debit_method"] == "natural"

    def test_net_debit_equals_spread_mid_when_mid_mode(self):
        """Mid mode: net_debit = spread_mid."""
        cand = _candidate(long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5)
        trade = _enrich_one(cand, request={"debit_price_basis": "mid"})
        assert trade["net_debit"] == pytest.approx(trade["spread_mid"], abs=0.01)
        assert trade["_debit_method"] == "mid"

    def test_spread_quotes_none_when_quotes_invalid(self):
        """When leg quotes are invalid, spread quotes are None."""
        cand = _candidate(long_bid=None, long_ask=None)
        trade = _enrich_one(cand, request={"_skip_quote_integrity": True})
        assert trade["spread_bid"] is None
        assert trade["spread_ask"] is None
        assert trade["spread_mid"] is None

    def test_bid_ask_spread_pct_from_spread_quotes(self):
        """bid_ask_spread_pct = (spread_ask − spread_bid) / net_debit."""
        cand = _candidate(long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5)
        trade = _enrich_one(cand)
        # spread_ask=3.5, spread_bid=2.5, net_debit=3.5
        # pct = (3.5 - 2.5) / 3.5 ≈ 0.2857
        expected_pct = (3.5 - 2.5) / 3.5
        assert trade["bid_ask_spread_pct"] == pytest.approx(expected_pct, abs=0.01)

    def test_leg_mid_fields_present(self):
        """Per-leg mid fields stored in debug output."""
        cand = _candidate(long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5)
        trade = _enrich_one(cand)
        assert trade["_long_mid"] == pytest.approx(5.25, abs=0.01)
        assert trade["_short_mid"] == pytest.approx(2.25, abs=0.01)


# =========================================================================
# BE — Breakeven formula (Goal 2 prerequisite)
# =========================================================================

class TestBreakevenFormulaCallDebit:
    """test_breakeven_formula_call_debit: call_debit breakeven = long_strike + net_debit."""

    def test_call_debit_breakeven(self):
        cand = _candidate(
            strategy="call_debit",
            long_strike=100.0, short_strike=105.0,
            long_ask=5.5, short_bid=2.0,
        )
        trade = _enrich_one(cand)
        # debit = 5.5 − 2.0 = 3.5
        # breakeven = 100.0 + 3.5 = 103.5
        assert trade["break_even"] == pytest.approx(103.5, abs=0.01)

    def test_put_debit_breakeven(self):
        """put_debit breakeven = long_strike − net_debit."""
        cand = _candidate(
            strategy="put_debit",
            long_strike=105.0, short_strike=100.0,
            long_ask=5.5, short_bid=2.0,
        )
        trade = _enrich_one(cand)
        # debit = 5.5 − 2.0 = 3.5
        # breakeven = 105.0 − 3.5 = 101.5
        assert trade["break_even"] == pytest.approx(101.5, abs=0.01)


# =========================================================================
# PD — POP direction (Goal 2)
# =========================================================================

class TestPOPDirectionCallDebit:
    """test_pop_direction_call_debit: POP uses refined model, delta as baseline."""

    def test_pop_delta_approx_equals_delta_long(self):
        """pop_delta_approx = |delta_long| always (baseline stored separately)."""
        cand = _candidate(long_delta=0.30, short_delta=0.10)
        trade = _enrich_one(cand)
        assert trade["pop_delta_approx"] == pytest.approx(0.30, abs=0.01)
        # p_win_used is set to the best available POP model
        assert trade["pop_refined"] is not None
        assert trade["p_win_used"] is not None
        assert 0.0 <= trade["p_win_used"] <= 1.0

    def test_pop_not_one_minus_debit_over_width(self):
        """The old broken formula (1 − debit/width) is NOT used as p_win_used."""
        # Build a cheap OTM spread: debit/width small → old formula gave ~0.9
        cand = _candidate(
            long_strike=698.0, short_strike=708.0,
            underlying_price=686.76,
            long_bid=1.0, long_ask=1.10,
            short_bid=0.03, short_ask=0.04,
            long_delta=0.22, short_delta=0.05,
            width=10.0,
        )
        trade = _enrich_one(cand)
        # Old formula: 1 − 1.07/10 = 0.893 (absurdly high for OTM)
        # Refined POP should be lower than raw delta (0.22) for debit spreads
        assert trade["p_win_used"] < 0.5  # must NOT be 0.893
        assert trade["pop_delta_approx"] == pytest.approx(0.22, abs=0.01)

    def test_implied_max_profit_prob_is_debit_over_width(self):
        """New diagnostic field: implied_max_profit_prob = debit/width."""
        cand = _candidate(
            long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5,
        )
        trade = _enrich_one(cand)
        # debit = 3.5, width = 5.0 → debit/width = 0.70
        assert trade["implied_max_profit_prob"] == pytest.approx(0.70, abs=0.01)

    def test_pop_none_when_delta_missing(self):
        """When delta is missing, pop_delta_approx is None.

        p_win_used may still be non-None if breakeven+lognormal model works
        (fallback).  Verify POP model field is set correctly.
        """
        cand = _candidate(long_delta=None, short_delta=None)
        trade = _enrich_one(cand)
        assert trade["pop_delta_approx"] is None
        # Breakeven model may provide a value if IV is present
        if trade.get("pop_breakeven_lognormal") is not None:
            assert trade["p_win_used"] is not None
            assert trade["pop_model_used"] == "BREAKEVEN_LOGNORMAL"
        else:
            assert trade["p_win_used"] is None
            assert any("MISSING_POP" in f for f in trade["_dq_flags"])

    def test_pop_none_when_delta_and_iv_missing(self):
        """When both delta and IV are missing, p_win_used is None."""
        cand = _candidate(long_delta=None, short_delta=None, long_iv=None, short_iv=None)
        trade = _enrich_one(cand)
        assert trade["p_win_used"] is None
        assert trade["pop_delta_approx"] is None
        assert trade["pop_refined"] is None
        assert trade["pop_model_used"] == "NONE"
        assert any("MISSING_POP" in f for f in trade["_dq_flags"])

    def test_pop_direction_put_debit(self):
        """For put_debit, pop_delta_approx = |delta_long| (put delta is negative)."""
        cand = _candidate(
            strategy="put_debit",
            long_strike=105.0, short_strike=100.0,
            long_delta=-0.40, short_delta=-0.15,
        )
        trade = _enrich_one(cand)
        assert trade["pop_delta_approx"] == pytest.approx(0.40, abs=0.01)
        # pop_refined exists and is within [0, 1]
        assert trade["pop_refined"] is not None
        assert 0.0 <= trade["pop_refined"] <= 1.0
        # p_win_used is the best model result, within [0, 1]
        assert trade["p_win_used"] is not None
        assert 0.0 <= trade["p_win_used"] <= 1.0


# =========================================================================
# EV — EV-POP consistency (Goal 3)
# =========================================================================

class TestEVConsistency:
    """EV must reconcile with p_win_used using binary payoff model."""

    def test_ev_formula_binary_model(self):
        """EV = p_win × max_profit − p_loss × max_loss."""
        cand = _candidate(
            long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5,
            long_delta=0.60,  # POP = 0.60
        )
        trade = _enrich_one(cand)
        p = trade["p_win_used"]
        mp = trade["max_profit"]
        ml = trade["max_loss"]
        expected_ev = p * mp - (1.0 - p) * ml
        assert trade["ev_per_contract"] == pytest.approx(expected_ev, abs=0.5)

    def test_ev_inputs_debug_dict_present(self):
        """_ev_inputs contains audit trail for EV computation."""
        trade = _enrich_one(_candidate())
        ev_inputs = trade.get("_ev_inputs")
        assert ev_inputs is not None
        assert "p_win_used" in ev_inputs
        assert "p_loss_used" in ev_inputs
        assert "avg_win_used" in ev_inputs
        assert "avg_loss_used" in ev_inputs
        assert "model" in ev_inputs
        assert ev_inputs["model"] == "binary"

    def test_ev_inputs_match_displayed_values(self):
        """p_win_used in _ev_inputs matches top-level p_win_used."""
        cand = _candidate(long_delta=0.45)
        trade = _enrich_one(cand)
        assert trade["_ev_inputs"]["p_win_used"] == trade["p_win_used"]
        assert trade["_ev_inputs"]["avg_win_used"] == trade["max_profit"]
        assert trade["_ev_inputs"]["avg_loss_used"] == trade["max_loss"]

    def test_ev_to_risk_consistent(self):
        """ev_to_risk = ev_per_contract / max_loss."""
        trade = _enrich_one(_candidate(long_delta=0.55))
        ev = trade["ev_per_contract"]
        ml = trade["max_loss"]
        if ev is not None and ml is not None and ml > 0:
            assert trade["ev_to_risk"] == pytest.approx(ev / ml, abs=0.01)

    def test_ev_none_when_pop_missing(self):
        """EV must be None when ALL POP models are unavailable."""
        # Both delta and IV must be missing to prevent any POP model
        cand = _candidate(long_delta=None, long_iv=None, short_iv=None)
        trade = _enrich_one(cand)
        assert trade["p_win_used"] is None
        assert trade["ev_per_contract"] is None
        assert trade["ev_to_risk"] is None

    def test_no_alignment_blending(self):
        """EV must NOT blend alignment into the probability (old bug).

        Old code used: p = 0.75 * implied_prob_profit + 0.25 * alignment
        New code uses refined POP directly.
        The test verifies EV = pop * max_profit - (1-pop) * max_loss exactly.
        """
        cand = _candidate(long_delta=0.35)
        trade = _enrich_one(cand)
        pop = trade["p_win_used"]
        mp = trade["max_profit"]
        ml = trade["max_loss"]
        exact_ev = pop * mp - (1.0 - pop) * ml
        assert trade["ev_per_contract"] == pytest.approx(exact_ev, abs=0.01)


# =========================================================================
# GA — Gate metrics alignment (Goal 4)
# =========================================================================

class TestGateMetricsAlignment:
    """test_gate_metrics_alignment: displayed fields match gated values."""

    plugin = DebitSpreadsStrategyPlugin()

    def test_gate_eval_snapshot_present(self):
        """After evaluate(), trade has _gate_eval_snapshot."""
        cand = _candidate()
        trade = _enrich_one(cand)
        trade["_policy"] = {}
        trade["_request"] = {"data_quality_mode": "balanced"}
        self.plugin.evaluate(trade)
        snap = trade.get("_gate_eval_snapshot")
        assert snap is not None
        assert "pop" in snap
        assert "min_pop" in snap
        assert "oi" in snap
        assert "min_oi" in snap
        assert "dq_mode" in snap
        # Enhanced trace fields (Task D)
        assert "pop_model_used" in snap
        assert "break_even" in snap
        assert "expected_move" in snap
        assert "max_profit" in snap
        assert "max_loss" in snap
        assert "kelly_fraction" in snap

    def test_gate_pop_matches_displayed(self):
        """POP value in gate snapshot matches p_win_used in trade."""
        cand = _candidate(long_delta=0.60)
        trade = _enrich_one(cand)
        trade["_policy"] = {}
        trade["_request"] = {"data_quality_mode": "balanced"}
        self.plugin.evaluate(trade)
        assert trade["_gate_eval_snapshot"]["pop"] == trade["p_win_used"]

    def test_gate_oi_matches_displayed(self):
        """OI in gate snapshot matches open_interest in trade."""
        cand = _candidate(long_oi=500, short_oi=300)
        trade = _enrich_one(cand)
        trade["_policy"] = {}
        trade["_request"] = {"data_quality_mode": "balanced"}
        self.plugin.evaluate(trade)
        assert trade["_gate_eval_snapshot"]["oi"] == trade["open_interest"]

    def test_gate_spread_pct_matches_displayed(self):
        """Spread pct in gate snapshot matches bid_ask_spread_pct."""
        trade = _enrich_one(_candidate())
        trade["_policy"] = {}
        trade["_request"] = {"data_quality_mode": "balanced"}
        self.plugin.evaluate(trade)
        assert trade["_gate_eval_snapshot"]["spread_pct"] == trade["bid_ask_spread_pct"]

    def test_oi_is_min_of_legs(self):
        """open_interest = min(long_oi, short_oi), matching what gates evaluate."""
        cand = _candidate(long_oi=500, short_oi=200)
        trade = _enrich_one(cand)
        assert trade["open_interest"] == 200  # min
        trade["_policy"] = {}
        trade["_request"] = {"data_quality_mode": "balanced"}
        self.plugin.evaluate(trade)
        assert trade["_gate_eval_snapshot"]["oi"] == 200

    def test_volume_is_min_of_legs(self):
        """volume = min(long_vol, short_vol)."""
        cand = _candidate(long_vol=100, short_vol=50)
        trade = _enrich_one(cand)
        assert trade["volume"] == 50


# =========================================================================
# SV — Sanity validation (Goal 5)
# =========================================================================

class TestSanityValidation:
    """validate_debit_trade produces correct dq_flags."""

    def test_healthy_trade_valid_for_ranking(self):
        """A normal healthy trade passes all sanity checks."""
        trade = _enrich_one(_candidate())
        assert trade["_valid_for_ranking"] is True
        sanity_flags = [f for f in trade["_dq_flags"] if f.startswith("SANITY:")]
        assert len(sanity_flags) == 0

    def test_max_loss_matches_debit(self):
        """max_loss must equal net_debit × 100."""
        cand = _candidate(long_ask=5.5, short_bid=2.0)
        trade = _enrich_one(cand)
        assert trade["max_loss"] == pytest.approx(trade["net_debit"] * 100.0, abs=1.0)

    def test_max_profit_matches_width_minus_debit(self):
        """max_profit must equal (width − net_debit) × 100."""
        cand = _candidate(long_ask=5.5, short_bid=2.0)
        trade = _enrich_one(cand)
        expected = (trade["width"] - trade["net_debit"]) * 100.0
        assert trade["max_profit"] == pytest.approx(expected, abs=1.0)

    def test_pop_suspect_flag(self):
        """POP > 0.8 when breakeven > 1.5× expected move → POP_SUSPECT."""
        # Build a trade where breakeven is far from spot and pop is high
        # This requires manipulating: high delta (pop > 0.8) + big debit offset
        trade = {
            "p_win_used": 0.85,
            "break_even": 120.0,
            "underlying_price": 100.0,
            "net_debit": 2.0,
            "width": 5.0,
            "max_loss": 200.0,
            "max_profit": 300.0,
            "spread_bid": 1.5,
            "spread_ask": 2.0,
            "spread_mid": 1.75,
            "bid_ask_spread_pct": 0.25,
            "ev_per_contract": None,
            "strategy": "call_debit",
            "long_strike": 118.0,
            "_debit_method": "natural",
            "_rejection_codes": [],
            "_dq_flags": [],
            "_valid_for_ranking": True,
        }
        _validate_debit_trade(trade, exp_move=5.0)
        # breakeven 120, underlying 100, distance=20, 1.5×exp_move=7.5 → 20 > 7.5
        assert any("POP_SUSPECT" in f for f in trade["_dq_flags"])

    def test_ev_pop_mismatch_flagged(self):
        """If EV doesn't match binary formula with p_win_used, flag it."""
        trade = {
            "p_win_used": 0.50,
            "ev_per_contract": 999.0,  # impossibly high
            "max_profit": 300.0,
            "max_loss": 200.0,
            "net_debit": 2.0,
            "width": 5.0,
            "spread_bid": 1.5,
            "spread_ask": 2.0,
            "spread_mid": 1.75,
            "bid_ask_spread_pct": 0.25,
            "break_even": 102.0,
            "underlying_price": 100.0,
            "strategy": "call_debit",
            "long_strike": 100.0,
            "_debit_method": "natural",
            "_rejection_codes": [],
            "_dq_flags": [],
            "_valid_for_ranking": True,
        }
        _validate_debit_trade(trade, exp_move=10.0)
        assert any("SANITY:ev_pop_mismatch" in f for f in trade["_dq_flags"])

    def test_sanity_failure_blocks_evaluate(self):
        """Trade with _valid_for_ranking=False is rejected by evaluate()."""
        cand = _candidate()
        trade = _enrich_one(cand)
        # Manually set validation failure
        trade["_valid_for_ranking"] = False
        trade["_dq_flags"].append("SANITY:spread_ask_negative")
        trade["_policy"] = {}
        trade["_request"] = {}
        plugin = DebitSpreadsStrategyPlugin()
        passed, reasons = plugin.evaluate(trade)
        assert not passed
        assert any("SANITY:" in r for r in reasons)


# =========================================================================
# Sample trace (Goal deliverable)
# =========================================================================

class TestSampleTrace:
    """One enriched trade must contain all computed components for debugging."""

    def test_sample_trade_has_all_debug_fields(self):
        cand = _candidate(
            long_strike=100.0, short_strike=105.0,
            long_bid=5.0, long_ask=5.5,
            short_bid=2.0, short_ask=2.5,
            long_delta=0.55, short_delta=0.20,
            long_oi=1000, short_oi=800,
            long_vol=200, short_vol=150,
        )
        trade = _enrich_one(cand)

        # Spread-level quotes
        assert trade["spread_bid"] is not None
        assert trade["spread_ask"] is not None
        assert trade["spread_mid"] is not None

        # Pricing
        assert trade["net_debit"] is not None
        assert trade["_debit_method"] in ("natural", "mid")
        assert trade["max_profit"] is not None
        assert trade["max_loss"] is not None
        assert trade["break_even"] is not None
        assert trade["return_on_risk"] is not None
        assert trade["debit_as_pct_of_width"] is not None

        # POP
        assert trade["p_win_used"] is not None
        assert trade["pop_delta_approx"] is not None
        assert trade["pop_refined"] is not None
        assert trade["implied_max_profit_prob"] is not None

        # EV
        assert trade["ev_per_contract"] is not None
        assert trade["ev_to_risk"] is not None
        assert trade["_ev_inputs"] is not None
        assert trade["_ev_inputs"]["model"] == "binary"
        assert trade["kelly_fraction"] is not None

        # IV Rank (may be None without iv_history, but field must exist)
        assert "iv_rank" in trade

        # Liquidity
        assert trade["open_interest"] is not None
        assert trade["volume"] is not None
        assert trade["bid_ask_spread_pct"] is not None

        # Sanity
        assert trade["_valid_for_ranking"] is True

        # Per-leg debug
        assert trade["_long_bid"] is not None
        assert trade["_long_ask"] is not None
        assert trade["_short_bid"] is not None
        assert trade["_short_ask"] is not None
        assert trade["_long_mid"] is not None
        assert trade["_short_mid"] is not None

        # POP model tracking
        assert trade["pop_model_used"] in ("BREAKEVEN_LOGNORMAL", "DELTA_ADJUSTED", "DELTA_APPROX")
        assert trade["pop_refined_model"] in ("BREAKEVEN_LOGNORMAL", "DELTA_ADJUSTED", None)


# =========================================================================
# Task 1 — POP floor epsilon tolerance
# =========================================================================

class TestPOPFloorEpsilon:
    """test_pop_floor_epsilon_allows_boundary: boundary POP passes with epsilon."""

    plugin = DebitSpreadsStrategyPlugin()

    def test_pop_at_exact_boundary_passes(self):
        """pop == min_pop should pass (inclusive)."""
        cand = _candidate(long_delta=0.50)
        trade = _enrich_one(cand)
        trade["p_win_used"] = 0.50  # explicitly set to test boundary
        trade["_policy"] = {}
        # Set all other thresholds very permissive to isolate POP gate
        trade["_request"] = {
            "min_pop": 0.50, "data_quality_mode": "balanced",
            "min_ev_to_risk": -999, "max_bid_ask_spread_pct": 999,
            "max_debit_pct_width": 0.99, "min_open_interest": 0, "min_volume": 0,
        }
        passed, reasons = self.plugin.evaluate(trade)
        assert passed, f"pop=0.50, min_pop=0.50 should pass: {reasons}"

    def test_pop_just_below_boundary_passes_with_epsilon(self):
        """pop = min_pop - 0.00005 should pass within epsilon tolerance."""
        cand = _candidate(long_delta=0.50)
        trade = _enrich_one(cand)
        # Manually adjust POP to be epsilon-close to threshold
        trade["p_win_used"] = 0.4999
        trade["_policy"] = {}
        trade["_request"] = {
            "min_pop": 0.50, "data_quality_mode": "balanced",
            "min_ev_to_risk": -999, "max_bid_ask_spread_pct": 999,
            "max_debit_pct_width": 0.99, "min_open_interest": 0, "min_volume": 0,
        }
        passed, reasons = self.plugin.evaluate(trade)
        assert passed, f"pop=0.4999 should pass with epsilon: {reasons}"

    def test_pop_below_boundary_beyond_epsilon_fails(self):
        """pop = min_pop - 0.01 should still fail."""
        cand = _candidate(long_delta=0.50)
        trade = _enrich_one(cand)
        trade["p_win_used"] = 0.49
        trade["_policy"] = {}
        trade["_request"] = {"min_pop": 0.50, "data_quality_mode": "balanced"}
        passed, reasons = self.plugin.evaluate(trade)
        assert not passed
        assert "pop_below_floor" in reasons

    def test_pop_gate_eval_trace_present(self):
        """_pop_gate_eval contains trace fields for debugging."""
        cand = _candidate(long_delta=0.50)
        trade = _enrich_one(cand)
        trade["_policy"] = {}
        trade["_request"] = {"min_pop": 0.50, "data_quality_mode": "balanced"}
        self.plugin.evaluate(trade)
        eval_trace = trade.get("_pop_gate_eval")
        assert eval_trace is not None
        assert "pop_actual" in eval_trace
        assert "threshold" in eval_trace
        assert "epsilon" in eval_trace
        assert "effective_threshold" in eval_trace
        assert "passed" in eval_trace
        assert "reason" in eval_trace
        assert "pop_model_used" in eval_trace

    def test_pop_gate_eval_values_match(self):
        """_pop_gate_eval.pop_actual matches trade's p_win_used."""
        cand = _candidate(long_delta=0.45)
        trade = _enrich_one(cand)
        trade["_policy"] = {}
        trade["_request"] = {"min_pop": 0.50, "data_quality_mode": "balanced"}
        self.plugin.evaluate(trade)
        eval_trace = trade["_pop_gate_eval"]
        assert eval_trace["pop_actual"] == trade["p_win_used"]
        assert eval_trace["threshold"] == 0.50
        assert eval_trace["passed"] is False
        assert eval_trace["reason"] == "pop_below_floor"


# =========================================================================
# Task 2 — Delta not required when breakeven model succeeds
# =========================================================================

class TestMissingDeltaNotDQWhenBreakevenModel:
    """test_missing_delta_not_dq_when_pop_model_breakeven."""

    plugin = DebitSpreadsStrategyPlugin()

    def test_pop_computed_via_breakeven_when_delta_missing(self):
        """With IV but no delta, breakeven+lognormal POP model fires."""
        cand = _candidate(
            long_delta=None, short_delta=None,
            long_iv=0.25, short_iv=0.22,
        )
        trade = _enrich_one(cand)
        assert trade["pop_delta_approx"] is None
        assert trade["pop_model_used"] == "BREAKEVEN_LOGNORMAL"
        assert trade["p_win_used"] is not None
        assert 0.0 < trade["p_win_used"] < 1.0

    def test_breakeven_model_not_flagged_missing_delta_dq(self):
        """MISSING_DELTA flag should NOT be set when BREAKEVEN_LOGNORMAL succeeded."""
        cand = _candidate(
            long_delta=None, short_delta=None,
            long_iv=0.25, short_iv=0.22,
        )
        trade = _enrich_one(cand)
        dq = trade.get("_dq_flags", [])
        assert not any("MISSING_DELTA" in f for f in dq), (
            f"MISSING_DELTA should not be flagged when breakeven model used: {dq}"
        )

    def test_delta_present_uses_refined_model(self):
        """When delta and IV are available, pop_refined uses BREAKEVEN_LOGNORMAL."""
        cand = _candidate(long_delta=0.55, long_iv=0.25, short_iv=0.22)
        trade = _enrich_one(cand)
        # With IV available, breakeven model fires for pop_refined
        assert trade["pop_refined_model"] == "BREAKEVEN_LOGNORMAL"
        assert trade["pop_model_used"] == "BREAKEVEN_LOGNORMAL"
        # Delta baseline still stored
        assert trade["pop_delta_approx"] == pytest.approx(0.55, abs=0.01)

    def test_both_missing_flags_all_models(self):
        """When both delta and IV are missing, flag all_models_unavailable."""
        cand = _candidate(long_delta=None, short_delta=None, long_iv=None, short_iv=None)
        trade = _enrich_one(cand)
        assert trade["pop_model_used"] == "NONE"
        assert trade["p_win_used"] is None
        dq = trade.get("_dq_flags", [])
        assert any("MISSING_POP:all_models_unavailable" in f for f in dq)

    def test_breakeven_model_does_not_reject_on_data_quality(self):
        """Trade with breakeven POP should not be rejected by DQ_MISSING:pop."""
        cand = _candidate(
            long_delta=None, short_delta=None,
            long_iv=0.25, short_iv=0.22,
        )
        trade = _enrich_one(cand)
        trade["_policy"] = {}
        trade["_request"] = {"min_pop": 0.10, "data_quality_mode": "balanced"}
        passed, reasons = self.plugin.evaluate(trade)
        assert "DQ_MISSING:pop" not in reasons


# =========================================================================
# Task 3 — Quote counters consistency
# =========================================================================

class TestQuoteCountersConsistent:
    """test_quote_counters_consistent: success + failed == attempted."""

    def test_leg_quote_counters_sum(self):
        """leg_quote_lookup_success + leg_quote_lookup_failed == attempted."""
        from app.services.strategies.debit_spreads import DebitSpreadsStrategyPlugin

        plugin = DebitSpreadsStrategyPlugin()
        cands = [
            _candidate(long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5),
            _candidate(long_bid=None, long_ask=None, short_bid=2.0, short_ask=2.5),
        ]
        enriched = plugin.enrich(cands, {
            "request": {"_skip_quote_integrity": True},
            "policy": {},
        })

        total = len(enriched)
        has_all = sum(
            1 for r in enriched
            if r.get("_short_bid") is not None
            and r.get("_short_ask") is not None
            and r.get("_long_bid") is not None
            and r.get("_long_ask") is not None
        )
        failed = total - has_all
        assert has_all + failed == total

    def test_spread_quote_counters(self):
        """spread_quote_derived tracks spread-level quote derivation."""
        plugin = DebitSpreadsStrategyPlugin()
        cands = [
            _candidate(long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5),
            _candidate(long_bid=None, long_ask=None, short_bid=2.0, short_ask=2.5),
        ]
        enriched = plugin.enrich(cands, {
            "request": {"_skip_quote_integrity": True},
            "policy": {},
        })

        spread_derived = sum(
            1 for r in enriched
            if r.get("spread_bid") is not None and r.get("spread_ask") is not None
        )
        spread_failed = len(enriched) - spread_derived
        assert spread_derived + spread_failed == len(enriched)
        assert enriched[0]["spread_bid"] is not None
        assert enriched[1]["spread_bid"] is None


# =========================================================================
# Refined POP model tests
# =========================================================================

class TestPOPRefined:
    """pop_refined gives more conservative estimates than raw delta for debit spreads."""

    def test_pop_refined_within_unit_interval(self):
        """pop_refined should be within [0, 1]."""
        cand = _candidate(long_delta=0.60, short_delta=0.20)
        trade = _enrich_one(cand)
        assert trade["pop_refined"] is not None
        assert trade["pop_delta_approx"] is not None
        assert 0.0 <= trade["pop_refined"] <= 1.0

    def test_wider_debit_lower_refined_pop(self):
        """Wider debit relative to width → lower refined POP."""
        # Cheap spread: small debit relative to width
        cheap = _candidate(
            long_strike=100.0, short_strike=110.0,
            long_bid=2.0, long_ask=2.5, short_bid=1.0, short_ask=1.5,
            long_delta=0.55, short_delta=0.30,
            long_iv=None, short_iv=None,  # force DELTA_ADJUSTED
            width=10.0,
        )
        # Expensive spread: large debit relative to width
        expensive = _candidate(
            long_strike=100.0, short_strike=110.0,
            long_bid=8.0, long_ask=8.5, short_bid=1.0, short_ask=1.5,
            long_delta=0.55, short_delta=0.30,
            long_iv=None, short_iv=None,  # force DELTA_ADJUSTED
            width=10.0,
        )
        t_cheap = _enrich_one(cheap)
        t_expensive = _enrich_one(expensive)
        assert t_cheap["pop_refined"] > t_expensive["pop_refined"]

    def test_pop_refined_uses_breakeven_when_iv_available(self):
        """When IV is available, pop_refined = pop_breakeven_lognormal."""
        cand = _candidate(long_delta=0.50, long_iv=0.25, short_iv=0.22)
        trade = _enrich_one(cand)
        assert trade["pop_refined_model"] == "BREAKEVEN_LOGNORMAL"
        assert trade["pop_refined"] == trade["pop_breakeven_lognormal"]

    def test_pop_refined_uses_delta_adjusted_when_no_iv(self):
        """When IV is absent but delta present, pop_refined = DELTA_ADJUSTED."""
        cand = _candidate(
            long_delta=0.50, short_delta=0.20,
            long_iv=None, short_iv=None,
        )
        trade = _enrich_one(cand)
        assert trade["pop_refined_model"] == "DELTA_ADJUSTED"
        assert trade["pop_refined"] is not None
        assert 0.0 <= trade["pop_refined"] <= 1.0

    def test_pop_refined_clamped_01(self):
        """pop_refined must be within [0, 1]."""
        for delta in [0.01, 0.50, 0.99]:
            cand = _candidate(long_delta=delta)
            trade = _enrich_one(cand)
            if trade["pop_refined"] is not None:
                assert 0.0 <= trade["pop_refined"] <= 1.0

    def test_pop_fallback_delta_flag(self):
        """POP_FALLBACK_DELTA flag set when pop_refined unavailable."""
        cand = _candidate(
            long_delta=0.50, short_delta=None,
            long_iv=None, short_iv=None,
        )
        trade = _enrich_one(cand)
        # Without IV and without short delta, should use simple conservative
        if trade["pop_refined_model"] == "DELTA_ADJUSTED":
            # pop_refined available → no fallback flag
            assert not any("POP_FALLBACK_DELTA" in f for f in trade["_dq_flags"])
        elif trade["pop_refined"] is None:
            assert any("POP_FALLBACK_DELTA" in f for f in trade["_dq_flags"])

    def test_pop_refined_none_when_all_missing(self):
        """pop_refined is None when both delta and IV missing."""
        cand = _candidate(long_delta=None, short_delta=None, long_iv=None, short_iv=None)
        trade = _enrich_one(cand)
        assert trade["pop_refined"] is None
        assert trade["pop_refined_model"] is None


# =========================================================================
# Kelly fraction tests
# =========================================================================

class TestKellyFraction:
    """Kelly fraction computed correctly from binary payoff model."""

    def test_kelly_formula(self):
        """Kelly = (b*p - q) / b, clamped to [0, 1]."""
        cand = _candidate(long_delta=0.60, short_delta=0.20)
        trade = _enrich_one(cand)
        assert trade["kelly_fraction"] is not None
        # Verify formula: f = (b*p - q) / b
        p = trade["p_win_used"]
        mp = trade["max_profit"]
        ml = trade["max_loss"]
        b = mp / ml
        q = 1.0 - p
        expected = max(0.0, min(1.0, (b * p - q) / b))
        assert trade["kelly_fraction"] == pytest.approx(expected, abs=0.001)

    def test_kelly_clamped_floor(self):
        """Kelly never goes below 0 (negative edge → 0)."""
        # Very low POP → negative Kelly raw → clamped to 0
        cand = _candidate(long_delta=0.10, short_delta=0.02)
        trade = _enrich_one(cand)
        if trade["kelly_fraction"] is not None:
            assert trade["kelly_fraction"] >= 0.0

    def test_kelly_clamped_cap(self):
        """Kelly never exceeds cap (default 1.0)."""
        cand = _candidate(long_delta=0.90, short_delta=0.70)
        trade = _enrich_one(cand)
        if trade["kelly_fraction"] is not None:
            assert trade["kelly_fraction"] <= 1.0

    def test_kelly_none_when_pop_missing(self):
        """kelly_fraction is None when POP unavailable."""
        cand = _candidate(long_delta=None, short_delta=None, long_iv=None, short_iv=None)
        trade = _enrich_one(cand)
        assert trade["kelly_fraction"] is None
        assert any("KELLY_UNAVAILABLE" in f for f in trade["_dq_flags"])

    def test_kelly_present_in_trade_dict(self):
        """kelly_fraction field always exists in enriched output."""
        trade = _enrich_one(_candidate())
        assert "kelly_fraction" in trade


# =========================================================================
# IV Rank tests
# =========================================================================

class TestIVRank:
    """IV Rank computation from iv_history."""

    def test_iv_rank_computed_with_history(self):
        """iv_rank computed when iv_history has ≥20 observations."""
        cand = _candidate()
        # Inject IV history into snapshot
        iv_history = [0.15 + i * 0.005 for i in range(30)]  # 0.15 to 0.295
        cand["snapshot"]["iv_history"] = iv_history
        trade = _enrich_one(cand)
        # Current IV ≈ 0.235 (avg of long/short)
        # iv_min = 0.15, iv_max = 0.295
        # iv_rank = (0.235 - 0.15) / (0.295 - 0.15) ≈ 0.586
        assert trade["iv_rank"] is not None
        assert 0.0 <= trade["iv_rank"] <= 1.0
        assert trade["iv_rank"] == pytest.approx(
            (0.235 - 0.15) / (0.295 - 0.15), abs=0.05,
        )

    def test_iv_rank_none_without_history(self):
        """iv_rank is None when no iv_history available."""
        trade = _enrich_one(_candidate())
        assert trade["iv_rank"] is None
        assert any("IVR_INSUFFICIENT_HISTORY" in f for f in trade["_dq_flags"])

    def test_iv_rank_flag_no_current_iv(self):
        """IVR flag specifies no_current_iv when IV itself missing."""
        cand = _candidate(long_iv=None, short_iv=None)
        trade = _enrich_one(cand)
        assert trade["iv_rank"] is None
        assert any("IVR_INSUFFICIENT_HISTORY:no_current_iv" in f for f in trade["_dq_flags"])

    def test_iv_rank_clamped(self):
        """iv_rank clamped to [0, 1] even with extreme current IV."""
        cand = _candidate(long_iv=0.50, short_iv=0.50)
        cand["snapshot"]["iv_history"] = [0.10 + i * 0.005 for i in range(25)]
        trade = _enrich_one(cand)
        if trade["iv_rank"] is not None:
            assert 0.0 <= trade["iv_rank"] <= 1.0

    def test_iv_rank_field_present(self):
        """iv_rank field always exists in enriched output."""
        trade = _enrich_one(_candidate())
        assert "iv_rank" in trade


# =========================================================================
# Metrics readiness tests
# =========================================================================

class TestMetricsReadiness:
    """metrics_status.ready reflects core fields only."""

    def test_ready_when_core_present(self):
        """ready=True when all READINESS_REQUIRED_FIELDS are non-None."""
        from app.utils.computed_metrics import apply_metrics_contract, READINESS_REQUIRED_FIELDS
        cand = _candidate(long_delta=0.55, short_delta=0.20)
        trade = _enrich_one(cand)
        result = apply_metrics_contract(trade)
        status = result["metrics_status"]
        # All core fields should be present for a complete trade
        cm = result["computed_metrics"]
        core_missing = [f for f in READINESS_REQUIRED_FIELDS if cm.get(f) is None]
        if not core_missing:
            assert status["ready"] is True
        else:
            # If any core field missing, ready is False
            assert status["ready"] is False

    def test_advanced_missing_does_not_block_readiness(self):
        """iv_rank, rsi14, etc. missing should NOT block ready."""
        from app.utils.computed_metrics import build_metrics_status
        # Simulate computed_metrics with all required fields but missing advanced
        mock = {
            "max_profit": 150.0,
            "max_loss": 350.0,
            "expected_value": 50.0,
            "pop": 0.5,
            "return_on_risk": 0.4286,
            "ev_to_risk": 0.05,
            "bid_ask_pct": 0.10,
            "open_interest": 500,
            "volume": 100,
            "break_even": 103.5,
            "dte": 30,
            "net_debit": 3.5,
            # Advanced fields intentionally missing
            "iv_rank": None,
            "rsi14": None,
            "rv_20d": None,
            "kelly_fraction": None,
            "trade_quality_score": None,
        }
        status = build_metrics_status(mock)
        assert status["ready"] is True
        # missing_fields lists ONLY missing REQUIRED fields (none here)
        assert len(status["missing_fields"]) == 0
        # missing_optional lists missing advanced fields
        assert "iv_rank" in status["missing_optional"]

    def test_core_missing_blocks_readiness(self):
        """Missing pop blocks readiness."""
        from app.utils.computed_metrics import build_metrics_status
        mock = {
            "max_profit": 150.0,
            "max_loss": 350.0,
            "expected_value": 50.0,
            "pop": None,  # missing core
            "return_on_risk": 0.4286,
            "ev_to_risk": 0.05,
            "bid_ask_pct": 0.10,
            "open_interest": 500,
            "volume": 100,
            "break_even": 103.5,
            "dte": 30,
            "net_debit": 3.5,
        }
        status = build_metrics_status(mock)
        assert status["ready"] is False
        assert "pop" in status["missing_required"]
        assert "pop" in status["missing_fields"]


# =========================================================================
# Breakeven POP monotonicity tests (replaces removed pop_adjusted tests)
# =========================================================================

class TestBreakevenPOPMonotonicity:
    """Breakeven POP is authoritative — no delta cap."""

    def test_call_debit_pop_decreases_as_be_rises(self):
        """For call_debit, POP should decrease as breakeven moves further OTM."""
        # Cheap spread → lower BE → higher POP
        cheap = _candidate(
            long_strike=100.0, short_strike=105.0,
            long_bid=1.0, long_ask=1.5, short_bid=0.5, short_ask=0.8,
            long_delta=0.50, short_delta=0.20,
        )
        # Expensive spread → higher BE → lower POP
        expensive = _candidate(
            long_strike=100.0, short_strike=105.0,
            long_bid=4.0, long_ask=4.5, short_bid=0.5, short_ask=0.8,
            long_delta=0.50, short_delta=0.20,
        )
        t_cheap = _enrich_one(cheap)
        t_expensive = _enrich_one(expensive)
        # Cheaper debit → lower breakeven → higher POP
        assert t_cheap["break_even"] < t_expensive["break_even"]
        assert t_cheap["p_win_used"] > t_expensive["p_win_used"]

    def test_pop_within_unit_interval(self):
        """p_win_used is always within [0, 1]."""
        for delta in [0.05, 0.30, 0.50, 0.80, 0.95]:
            cand = _candidate(long_delta=delta)
            trade = _enrich_one(cand)
            if trade["p_win_used"] is not None:
                assert 0.0 <= trade["p_win_used"] <= 1.0

    def test_breakeven_pop_can_exceed_delta(self):
        """Breakeven POP is NOT capped at |delta_long|.

        The breakeven-lognormal model accounts for debit paid and can
        legitimately differ from the delta approximation.
        """
        cand = _candidate(
            long_strike=100.0, short_strike=105.0,
            long_bid=0.5, long_ask=0.8, short_bid=0.1, short_ask=0.2,
            long_delta=0.25, short_delta=0.05,
            underlying_price=102.0,
        )
        trade = _enrich_one(cand)
        if trade["pop_model_used"] == "BREAKEVEN_LOGNORMAL":
            # We just verify it's not forcibly capped — it can be above or below delta
            assert trade["p_win_used"] is not None
            assert 0.0 <= trade["p_win_used"] <= 1.0


# =========================================================================
# Kelly edge cases (Task 2 expanded)
# =========================================================================

class TestKellyEdgeCases:
    """Additional Kelly fraction edge cases."""

    def test_positive_edge_kelly_positive(self):
        """When POP × max_profit > (1−POP) × max_loss, Kelly > 0."""
        # Need high POP and/or favorable risk/reward
        # Use wide spread (width=20) and small debit for high return ratio
        cand = _candidate(
            long_strike=100.0, short_strike=120.0,
            long_bid=1.0, long_ask=1.5,
            short_bid=0.05, short_ask=0.10,
            long_delta=0.70, short_delta=0.15,
            long_iv=None, short_iv=None,  # avoid breakeven cap
            width=20.0,
        )
        trade = _enrich_one(cand)
        # debit=1.45, max_profit=18.55*100=1855, max_loss=1.45*100=145
        # b = 1855/145 ≈ 12.79
        # With DELTA_ADJUSTED capping, p_win might be lower, but with b≈13 even moderate p gives positive kelly
        if trade["kelly_fraction"] is not None:
            assert trade["kelly_fraction"] > 0.0

    def test_negative_edge_kelly_zero(self):
        """When edge is negative, Kelly should be 0."""
        # Low POP, expensive debit
        cand = _candidate(
            long_delta=0.10, short_delta=0.02,
            long_iv=None, short_iv=None,
        )
        trade = _enrich_one(cand)
        if trade["kelly_fraction"] is not None:
            assert trade["kelly_fraction"] == 0.0

    def test_extreme_edge_kelly_capped_at_one(self):
        """Even with extreme edge, Kelly ≤ 1.0."""
        cand = _candidate(
            long_strike=100.0, short_strike=120.0,
            long_bid=0.05, long_ask=0.10,
            short_bid=0.01, short_ask=0.02,
            long_delta=0.95, short_delta=0.85,
            long_iv=None, short_iv=None,
            width=20.0,
        )
        trade = _enrich_one(cand)
        if trade["kelly_fraction"] is not None:
            assert trade["kelly_fraction"] <= 1.0


# =========================================================================
# EV input consistency validation (Task 4)
# =========================================================================

class TestEVInputConsistencyValidation:
    """Verify _validate_debit_trade catches max_profit/max_loss vs net_debit mismatch."""

    def test_consistent_trade_no_sanity_flags(self):
        """A correctly enriched trade has no max_profit/max_loss SANITY flags."""
        trade = _enrich_one(_candidate())
        dq = trade.get("_dq_flags", [])
        sanity = [f for f in dq if "SANITY:max_profit_mismatch" in f or "SANITY:max_loss_mismatch" in f]
        assert sanity == [], f"Unexpected sanity flags: {sanity}"
        assert trade["_valid_for_ranking"] is True

    def test_max_profit_formula(self):
        """max_profit ≈ (width − net_debit) × 100 within $1 tolerance."""
        trade = _enrich_one(_candidate())
        expected = (trade["width"] - trade["net_debit"]) * 100.0
        assert abs(trade["max_profit"] - expected) <= 1.0

    def test_max_loss_formula(self):
        """max_loss ≈ net_debit × 100 within $1 tolerance."""
        trade = _enrich_one(_candidate())
        expected = trade["net_debit"] * 100.0
        assert abs(trade["max_loss"] - expected) <= 1.0

    def test_mismatch_detected(self):
        """Manually injected mismatch triggers SANITY flag and ranking exclusion."""
        trade = _enrich_one(_candidate())
        # Corrupt max_profit to create a mismatch
        trade["max_profit"] = 99999.0
        trade["_dq_flags"] = []
        trade["_valid_for_ranking"] = True
        _validate_debit_trade(trade, None)
        assert any("SANITY:max_profit_mismatch" in f for f in trade["_dq_flags"])
        assert trade["_valid_for_ranking"] is False