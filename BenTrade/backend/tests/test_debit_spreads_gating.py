"""Tests for debit-spreads data/quote enrichment + gate/score consistency.

Covers:
  T1  — Quote enrichment: validate_spread_quotes, rejection codes
  T2  — Pricing: net_debit, max_profit/loss, p_win_used populated
  T3  — OI/Volume mapping: None vs 0 distinction, DQ flags
  T4  — POP handling: missing POP rejection per data_quality_mode
  T5  — Gate breakdown: unique rejection reasons, no overcounting
  T6  — Ranking: compute_rank_score delegation (no custom inline formula)
  T7  — Score scale: rank_score 0–100
  T8  — Presets: conservative/balanced/wide resolve via strategy_service
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.ranking import compute_rank_score, safe_float
from app.services.strategies.debit_spreads import (
    DebitSpreadsStrategyPlugin,
    validate_quote,
    validate_spread_quotes,
)
from app.services.strategy_service import StrategyService


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
    """Build a fake contract (SimpleNamespace mimics Pydantic OptionContract)."""
    return SimpleNamespace(
        strike=strike,
        bid=bid,
        ask=ask,
        open_interest=open_interest,
        volume=volume,
        iv=iv,
        theta=theta,
        option_type=option_type,
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
    strategy: str = "call_debit",
    underlying_price: float = 102.0,
    dte: int = 30,
    width: float | None = None,
) -> dict[str, Any]:
    """Build a fake candidate dict as returned by build_candidates."""
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
            strike=long_strike,
            bid=long_bid,
            ask=long_ask,
            open_interest=long_oi,
            volume=long_vol,
            iv=long_iv,
        ),
        "short_leg": _leg(
            strike=short_strike,
            bid=short_bid,
            ask=short_ask,
            open_interest=short_oi,
            volume=short_vol,
            iv=short_iv,
        ),
        "snapshot": {"symbol": "SPY", "prices_history": []},
    }


def _enrich_one(candidate: dict, request: dict | None = None) -> dict[str, Any]:
    """Enrich a single candidate and return the enriched trade dict."""
    plugin = DebitSpreadsStrategyPlugin()
    inputs = {
        "request": request or {},
        "policy": {},
    }
    results = plugin.enrich([candidate], inputs)
    assert len(results) == 1, f"Expected 1 enriched trade, got {len(results)}"
    return results[0]


def _make_enriched_trade(
    *,
    pop: float | None = 0.60,
    ev_to_risk: float | None = 0.02,
    return_on_risk: float | None = 0.30,
    bid_ask_spread_pct: float | None = 0.005,
    open_interest: int | None = 500,
    volume: int | None = 100,
    net_debit: float | None = 2.0,
    width: float | None = 5.0,
    debit_as_pct_of_width: float | None = 0.40,
    dq_mode: str = "balanced",
    rejection_codes: list[str] | None = None,
    quote_rejection: str | None = None,
) -> dict[str, Any]:
    """Build a minimal enriched trade dict for evaluate() testing."""
    return {
        "p_win_used": pop,
        "ev_to_risk": ev_to_risk,
        "return_on_risk": return_on_risk,
        "bid_ask_spread_pct": bid_ask_spread_pct,
        "open_interest": open_interest,
        "volume": volume,
        "net_debit": net_debit,
        "width": width,
        "debit_as_pct_of_width": debit_as_pct_of_width,
        "_rejection_codes": rejection_codes or [],
        "_quote_rejection": quote_rejection,
        "_request": {
            "data_quality_mode": dq_mode,
            "min_pop": 0.50,
            "min_ev_to_risk": 0.01,
            "max_bid_ask_spread_pct": 2.0,
            "min_open_interest": 300,
            "min_volume": 20,
            "max_debit_pct_width": 0.50,
        },
        "_policy": {},
    }


# =========================================================================
# T1 — Quote validation
# =========================================================================

class TestQuoteValidation:
    """Validate centralised quote checking for debit-spread legs."""

    def test_valid_quote(self):
        ok, reason = validate_quote(2.0, 2.5)
        assert ok is True
        assert reason is None

    def test_missing_bid(self):
        ok, reason = validate_quote(None, 2.5)
        assert ok is False
        assert reason == "missing_bid"

    def test_missing_ask(self):
        ok, reason = validate_quote(2.0, None)
        assert ok is False
        assert reason == "missing_ask"

    def test_negative_bid(self):
        ok, reason = validate_quote(-0.1, 2.5)
        assert ok is False
        assert reason == "negative_bid"

    def test_zero_ask(self):
        ok, reason = validate_quote(0.0, 0.0)
        assert ok is False
        assert reason == "zero_or_negative_ask"

    def test_inverted_market(self):
        ok, reason = validate_quote(3.0, 2.0)
        assert ok is False
        assert reason == "inverted_market"

    def test_spread_quotes_both_valid(self):
        ok, reason = validate_spread_quotes(2.0, 2.5, 1.0, 1.3)
        assert ok is True
        assert reason is None

    def test_spread_quotes_long_leg_invalid(self):
        ok, reason = validate_spread_quotes(None, 2.5, 1.0, 1.3)
        assert ok is False
        assert "QUOTE_INVALID:long_leg:missing_bid" in reason

    def test_spread_quotes_short_leg_invalid(self):
        ok, reason = validate_spread_quotes(2.0, 2.5, None, 1.3)
        assert ok is False
        assert "QUOTE_INVALID:short_leg:missing_bid" in reason


# =========================================================================
# T2 — enrich() produces correct pricing + populates p_win_used
# =========================================================================

class TestEnrichPricing:
    """Verify enrich() sets net_debit, max_profit/loss, p_win_used."""

    def test_basic_call_debit_metrics(self):
        cand = _candidate(
            long_strike=100.0,
            short_strike=105.0,
            long_ask=5.5,
            short_bid=2.0,
        )
        trade = _enrich_one(cand)

        # net_debit = long_ask - short_bid = 5.5 - 2.0 = 3.5
        assert trade["net_debit"] == pytest.approx(3.5, abs=0.01)
        # width = 5.0
        assert trade["width"] == pytest.approx(5.0, abs=0.01)
        # max_profit = (width - debit) * 100 = (5.0 - 3.5) * 100 = 150
        assert trade["max_profit"] == pytest.approx(150.0, abs=1.0)
        assert trade["max_profit_per_contract"] == trade["max_profit"]
        # max_loss = debit * 100 = 350
        assert trade["max_loss"] == pytest.approx(350.0, abs=1.0)
        assert trade["max_loss_per_contract"] == trade["max_loss"]
        # return_on_risk = max_profit / max_loss = 150 / 350 ≈ 0.4286
        assert trade["return_on_risk"] == pytest.approx(150.0 / 350.0, abs=0.01)

    def test_p_win_used_populated(self):
        """p_win_used must be set to pop_refined (refined POP) for debit spreads."""
        cand = _candidate(long_bid=3.5, long_ask=4.0, short_bid=1.5, short_ask=2.0)
        trade = _enrich_one(cand)

        assert trade["p_win_used"] is not None
        # p_win_used is the best available POP model result, within [0, 1]
        assert 0.0 <= trade["p_win_used"] <= 1.0
        assert trade["p_win_used"] == trade["implied_prob_profit"]
        # pop_delta_approx still stored as baseline
        assert trade["pop_delta_approx"] == pytest.approx(0.50, abs=0.01)

    def test_quote_failure_sets_rejection_codes(self):
        """When quotes are invalid, enrich still produces a dict with rejection codes."""
        cand = _candidate(long_bid=None, long_ask=None, short_bid=2.0, short_ask=2.5)
        trade = _enrich_one(cand, request={"_skip_quote_integrity": True})

        assert trade["_rejection_codes"]
        assert any("QUOTE_INVALID" in code for code in trade["_rejection_codes"])
        # Metrics should be None (cannot compute)
        assert trade["net_debit"] is None
        assert trade["max_profit"] is None

    def test_per_leg_raw_fields_stored(self):
        """Enriched trade stores _long_bid, _short_ask, etc. for diagnostics."""
        cand = _candidate(long_bid=5.0, long_ask=5.5, short_bid=2.0, short_ask=2.5)
        trade = _enrich_one(cand)

        assert trade["_long_bid"] == pytest.approx(5.0)
        assert trade["_long_ask"] == pytest.approx(5.5)
        assert trade["_short_bid"] == pytest.approx(2.0)
        assert trade["_short_ask"] == pytest.approx(2.5)


# =========================================================================
# T3 — OI/Volume: None vs 0 distinction
# =========================================================================

class TestOIVolumeMapping:
    """OI/volume must preserve None (missing) vs 0 (zero) vs positive."""

    def test_both_legs_have_oi(self):
        cand = _candidate(long_oi=500, short_oi=300)
        trade = _enrich_one(cand)
        assert trade["open_interest"] == 300  # min of both legs

    def test_missing_oi_is_none(self):
        cand = _candidate(long_oi=None, short_oi=300)
        trade = _enrich_one(cand)
        assert trade["open_interest"] is None  # missing, not 0

    def test_zero_oi_is_zero(self):
        cand = _candidate(long_oi=0, short_oi=300)
        trade = _enrich_one(cand)
        assert trade["open_interest"] == 0  # zero, not None

    def test_missing_volume_is_none(self):
        cand = _candidate(long_vol=None, short_vol=100)
        trade = _enrich_one(cand)
        assert trade["volume"] is None

    def test_per_leg_raw_oi_vol_stored(self):
        cand = _candidate(long_oi=500, short_oi=None, long_vol=100, short_vol=50)
        trade = _enrich_one(cand)
        assert trade["_long_oi"] == 500
        assert trade["_short_oi"] is None
        assert trade["_long_vol"] == 100
        assert trade["_short_vol"] == 50


# =========================================================================
# T4 — POP gate: missing POP per data_quality_mode
# =========================================================================

class TestPOPGate:
    """Missing POP must reject in strict/balanced, waive in lenient."""

    plugin = DebitSpreadsStrategyPlugin()

    def test_missing_pop_rejects_balanced(self):
        trade = _make_enriched_trade(pop=None, dq_mode="balanced")
        passed, reasons = self.plugin.evaluate(trade)
        assert not passed
        assert "DQ_MISSING:pop" in reasons

    def test_missing_pop_rejects_strict(self):
        trade = _make_enriched_trade(pop=None, dq_mode="strict")
        passed, reasons = self.plugin.evaluate(trade)
        assert not passed
        assert "DQ_MISSING:pop" in reasons

    def test_missing_pop_waived_lenient(self):
        trade = _make_enriched_trade(pop=None, dq_mode="lenient")
        passed, reasons = self.plugin.evaluate(trade)
        # Should NOT contain DQ_MISSING:pop
        assert "DQ_MISSING:pop" not in reasons

    def test_low_pop_rejects(self):
        trade = _make_enriched_trade(pop=0.30, dq_mode="balanced")
        passed, reasons = self.plugin.evaluate(trade)
        assert not passed
        assert "pop_below_floor" in reasons

    def test_good_pop_passes(self):
        trade = _make_enriched_trade(pop=0.65, dq_mode="balanced")
        passed, reasons = self.plugin.evaluate(trade)
        assert passed or "pop_below_floor" not in reasons


# =========================================================================
# T5 — Gate breakdown: OI/vol DQ codes are distinct from threshold failures
# =========================================================================

class TestGateBreakdownClarity:
    """Verify each trade gets ONE set of rejection codes, not duplicated."""

    plugin = DebitSpreadsStrategyPlugin()

    def test_missing_oi_gives_dq_code_not_threshold(self):
        trade = _make_enriched_trade(open_interest=None, dq_mode="balanced")
        passed, reasons = self.plugin.evaluate(trade)
        assert "DQ_MISSING:open_interest" in reasons
        assert "open_interest_below_min" not in reasons

    def test_zero_oi_gives_dq_zero_code(self):
        trade = _make_enriched_trade(open_interest=0, dq_mode="balanced")
        passed, reasons = self.plugin.evaluate(trade)
        assert "DQ_ZERO:open_interest" in reasons
        assert "open_interest_below_min" not in reasons

    def test_low_oi_gives_threshold_code(self):
        trade = _make_enriched_trade(open_interest=10, dq_mode="balanced")
        passed, reasons = self.plugin.evaluate(trade)
        assert "open_interest_below_min" in reasons
        assert "DQ_MISSING:open_interest" not in reasons

    def test_missing_vol_gives_dq_code(self):
        trade = _make_enriched_trade(volume=None, dq_mode="balanced")
        passed, reasons = self.plugin.evaluate(trade)
        assert "DQ_MISSING:volume" in reasons

    def test_zero_vol_gives_dq_zero_code(self):
        trade = _make_enriched_trade(volume=0, dq_mode="balanced")
        passed, reasons = self.plugin.evaluate(trade)
        assert "DQ_ZERO:volume" in reasons

    def test_quote_rejection_returns_immediately(self):
        """Pre-enrichment rejection codes should be returned as-is without additional gates."""
        trade = _make_enriched_trade(
            rejection_codes=["QUOTE_INVALID:long_leg:missing_bid"],
        )
        passed, reasons = self.plugin.evaluate(trade)
        assert not passed
        assert reasons == ["QUOTE_INVALID:long_leg:missing_bid"]

    def test_no_duplicate_reasons(self):
        """Each reason in the list should be unique."""
        trade = _make_enriched_trade(
            open_interest=None,
            volume=None,
            pop=None,
            dq_mode="balanced",
        )
        _, reasons = self.plugin.evaluate(trade)
        # Should have no duplicates
        assert len(reasons) == len(set(reasons)), f"Duplicate reasons: {reasons}"


# =========================================================================
# T6 — Ranking: compute_rank_score delegation
# =========================================================================

class TestRankingDelegation:
    """score() must delegate to compute_rank_score, not use custom formula."""

    plugin = DebitSpreadsStrategyPlugin()

    def test_score_uses_compute_rank_score(self):
        """Verify score() returns the same value as compute_rank_score()."""
        cand = _candidate()
        trade = _enrich_one(cand)
        score_val, _ = self.plugin.score(trade)
        expected = compute_rank_score(trade)
        assert score_val == pytest.approx(expected, abs=0.001)


# =========================================================================
# T7 — Score scale: 0–100
# =========================================================================

class TestScoreScale:
    """rank_score must be on 0–100 scale."""

    plugin = DebitSpreadsStrategyPlugin()

    def test_score_in_0_100_range(self):
        cand = _candidate()
        trade = _enrich_one(cand)
        score_val, _ = self.plugin.score(trade)
        assert 0.0 <= score_val <= 100.0, f"Score {score_val} not in [0, 100]"

    def test_score_not_in_0_1_range(self):
        """Score should NOT be in [0, 1] (old scale). Even a bad trade gets > 1."""
        cand = _candidate()
        trade = _enrich_one(cand)
        score_val, _ = self.plugin.score(trade)
        # A healthy debit spread should score well above 1.0
        # (unless all metrics are zero, which our test candidate isn't)
        # This is a canary: if score is < 1.0, the old 0–1 formula leaked back.
        assert score_val > 1.0, f"Score {score_val} looks like old 0–1 scale"


# =========================================================================
# T8 — Presets: verify strategy_service has debit_spreads presets
# =========================================================================

class TestDebitSpreadPresets:
    """_PRESETS['debit_spreads'] must exist with strict, conservative, balanced, wide."""

    def test_presets_exist(self):
        presets = StrategyService._PRESETS.get("debit_spreads", {})
        assert "strict" in presets
        assert "conservative" in presets
        assert "balanced" in presets
        assert "wide" in presets

    def test_widening_thresholds(self):
        """Strict thresholds must be tighter than balanced, which must be tighter than wide."""
        presets = StrategyService._PRESETS["debit_spreads"]
        strict = presets["strict"]
        balanced = presets["balanced"]
        wide = presets["wide"]

        # min_pop: strict > balanced > wide
        assert strict["min_pop"] > balanced["min_pop"] > wide["min_pop"]
        # min_open_interest: strict > balanced > wide
        assert strict["min_open_interest"] > balanced["min_open_interest"] > wide["min_open_interest"]
        # max_debit_pct_width: strict < balanced < wide (tighter = lower)
        assert strict["max_debit_pct_width"] < balanced["max_debit_pct_width"] < wide["max_debit_pct_width"]

    def test_data_quality_mode_per_preset(self):
        presets = StrategyService._PRESETS["debit_spreads"]
        assert presets["strict"]["data_quality_mode"] == "strict"
        assert presets["conservative"]["data_quality_mode"] == "balanced"
        assert presets["balanced"]["data_quality_mode"] == "balanced"
        assert presets["wide"]["data_quality_mode"] == "lenient"

    def test_resolve_thresholds(self):
        """resolve_thresholds() should return a populated dict for debit_spreads."""
        result = StrategyService.resolve_thresholds("debit_spreads", "balanced")
        assert "min_pop" in result
        assert "max_debit_pct_width" in result
        assert "data_quality_mode" in result

    def test_apply_request_defaults_stamps_preset(self):
        """_apply_request_defaults should stamp _preset_name and _requested_preset_name."""
        svc = StrategyService.__new__(StrategyService)
        req = svc._apply_request_defaults("debit_spreads", {"preset": "wide"})
        assert req.get("_preset_name") == "wide"
        assert req.get("_requested_preset_name") == "wide"
        assert req.get("_requested_data_quality_mode") is None  # not explicitly set


# =========================================================================
# T-extra — DQ lenient waiver logic
# =========================================================================

class TestLenientDQWaiver:
    """In lenient mode, missing OI/vol should be waived when pricing is healthy."""

    plugin = DebitSpreadsStrategyPlugin()

    def test_missing_oi_waived_lenient_healthy_pricing(self):
        trade = _make_enriched_trade(
            open_interest=None,
            volume=100,
            dq_mode="lenient",
            net_debit=2.0,
            bid_ask_spread_pct=0.005,
        )
        passed, reasons = self.plugin.evaluate(trade)
        assert "DQ_MISSING:open_interest" not in reasons

    def test_missing_oi_not_waived_lenient_bad_pricing(self):
        trade = _make_enriched_trade(
            open_interest=None,
            volume=100,
            dq_mode="lenient",
            net_debit=0.01,  # too low for waiver
            bid_ask_spread_pct=0.005,
        )
        passed, reasons = self.plugin.evaluate(trade)
        assert "DQ_MISSING:open_interest" in reasons


# =========================================================================
# T-extra — evaluate(): debit_too_close_to_width gate
# =========================================================================

class TestDebitCapGate:
    """The debit-as-pct-of-width gate should respect thresholds."""

    plugin = DebitSpreadsStrategyPlugin()

    def test_debit_under_cap_passes(self):
        trade = _make_enriched_trade(debit_as_pct_of_width=0.40)
        passed, reasons = self.plugin.evaluate(trade)
        assert "debit_too_close_to_width" not in reasons

    def test_debit_over_cap_rejected(self):
        trade = _make_enriched_trade(debit_as_pct_of_width=0.80)
        passed, reasons = self.plugin.evaluate(trade)
        assert "debit_too_close_to_width" in reasons


# =========================================================================
# T-extra — Gate groups include debit-spread codes
# =========================================================================

class TestGateGroupsCoverage:
    """Verify _GATE_GROUPS includes debit-spread-specific rejection codes."""

    def test_debit_codes_in_gate_groups(self):
        all_codes = []
        for codes in StrategyService._GATE_GROUPS.values():
            all_codes.extend(codes)
        assert "non_positive_debit" in all_codes
        assert "debit_ge_width" in all_codes
        assert "debit_too_close_to_width" in all_codes

    def test_quote_rejected_debit_exceeds_width_in_gate_groups(self):
        """QUOTE_REJECTED:debit_exceeds_width is in quote_validation, not spread_structure."""
        quote_codes = StrategyService._GATE_GROUPS["quote_validation"]
        assert "QUOTE_REJECTED:debit_exceeds_width" in quote_codes
        structure_codes = StrategyService._GATE_GROUPS["spread_structure"]
        assert "QUOTE_REJECTED:debit_exceeds_width" not in structure_codes


# =========================================================================
# T-extra — Quote failure isolation (debit_ge_width artifact prevention)
# =========================================================================

class TestQuoteFailureIsolation:
    """When quote is suspect (short_bid=0), reject as QUOTE_REJECTED, not debit_ge_width.

    Bug: zero short-leg bid inflates debit to long_ask, causing debit >= width.
    The candidate was mis-attributed to 'debit_ge_width' (spread_structure)
    when the root cause was data quality.
    """

    def test_zero_short_bid_produces_quote_rejected_not_debit_ge_width(self):
        """short_bid=0 → QUOTE_REJECTED:debit_exceeds_width, NOT debit_ge_width."""
        cand = _candidate(
            long_strike=100.0, short_strike=105.0,
            long_bid=5.0, long_ask=5.5,
            short_bid=0.0, short_ask=0.01,  # no real market on short leg
        )
        trade = _enrich_one(cand)
        codes = trade.get("_rejection_codes", [])
        # Must be classified as quote quality, not spread structure
        assert "QUOTE_REJECTED:debit_exceeds_width" in codes
        assert "debit_ge_width" not in codes
        # DQ flag should indicate root cause
        assert any("QUOTE_REJECTED:short_bid_zero" in f for f in trade.get("_dq_flags", []))

    def test_zero_short_bid_pricing_fields_reflect_suspect_data(self):
        """When short_bid=0 triggers QUOTE_REJECTED, net_debit is still computed
        (for diagnostic trace), but candidate is rejected before gates run."""
        cand = _candidate(
            long_strike=100.0, short_strike=105.0,
            long_bid=5.0, long_ask=5.5,
            short_bid=0.0, short_ask=0.01,
        )
        trade = _enrich_one(cand)
        # net_debit = long_ask - short_bid = 5.5 - 0.0 = 5.5, width = 5.0
        assert trade["net_debit"] == pytest.approx(5.5, abs=0.01)
        # But _quote_rejection should be set (first rejection code)
        assert trade["_quote_rejection"] == "QUOTE_REJECTED:debit_exceeds_width"

    def test_zero_short_bid_rejected_by_evaluate_gate1(self):
        """Evaluate returns QUOTE_REJECTED before any structural gate fires."""
        cand = _candidate(
            long_strike=100.0, short_strike=105.0,
            long_bid=5.0, long_ask=5.5,
            short_bid=0.0, short_ask=0.01,
        )
        trade = _enrich_one(cand)
        trade["_policy"] = {}
        trade["_request"] = {"data_quality_mode": "balanced"}
        plugin = DebitSpreadsStrategyPlugin()
        passed, reasons = plugin.evaluate(trade)
        assert not passed
        assert "QUOTE_REJECTED:debit_exceeds_width" in reasons
        assert "debit_ge_width" not in reasons
        assert "debit_too_close_to_width" not in reasons

    def test_valid_debit_ge_width_still_works(self):
        """When short_bid > 0 but debit legitimately >= width, debit_ge_width fires."""
        # long_ask=5.5, short_bid=0.40, width=5.0 → debit=5.10 >= 5.0
        cand = _candidate(
            long_strike=100.0, short_strike=105.0,
            long_bid=5.0, long_ask=5.5,
            short_bid=0.40, short_ask=0.50,
        )
        trade = _enrich_one(cand)
        codes = trade.get("_rejection_codes", [])
        assert "debit_ge_width" in codes
        assert "QUOTE_REJECTED:debit_exceeds_width" not in codes

    def test_valid_quotes_structural_checks_run_normally(self):
        """With healthy quotes and debit < width, no structural rejection."""
        cand = _candidate(
            long_bid=4.0, long_ask=4.5,
            short_bid=2.0, short_ask=2.5,
        )
        trade = _enrich_one(cand)
        codes = trade.get("_rejection_codes", [])
        assert not codes, f"Expected no rejections, got: {codes}"
        assert trade["net_debit"] is not None
        assert trade["max_profit"] is not None
        assert trade["max_loss"] is not None

    def test_missing_quotes_produce_quote_invalid_not_structural(self):
        """When leg quotes are None, reject as QUOTE_INVALID, not structural."""
        cand = _candidate(
            long_bid=None, long_ask=None,
            short_bid=2.0, short_ask=2.5,
        )
        trade = _enrich_one(cand, request={"_skip_quote_integrity": True})
        codes = trade.get("_rejection_codes", [])
        assert any("QUOTE_INVALID" in c for c in codes)
        assert "debit_ge_width" not in codes
        # Pricing fields should be None (no valid quotes to derive from)
        assert trade["net_debit"] is None
        assert trade["max_profit"] is None
        assert trade["max_loss"] is None
