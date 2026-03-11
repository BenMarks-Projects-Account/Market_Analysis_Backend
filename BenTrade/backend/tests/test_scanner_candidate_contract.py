"""Tests for scanner_candidate_contract.py — normalized scanner candidate output.

Covers:
  1. Contract shape validation (all required fields present)
  2. Stock candidate normalization (all 4 scanner families)
  3. Options candidate normalization (credit spread, debit, condor, butterfly)
  4. Backward compatibility (existing fields not mutated)
  5. Sorting/filtering safety (composite_score preserved)
  6. Edge cases (missing fields, empty candidates, unknown scanners)
  7. SCANNER_METADATA completeness
  8. REQUIRED_FIELDS coverage
"""

from __future__ import annotations

import copy
import pytest

from app.services.scanner_candidate_contract import (
    REQUIRED_FIELDS,
    SCANNER_METADATA,
    normalize_candidate_output,
)


# ── Fixtures: stock candidate shapes ─────────────────────────────────

def _make_stock_candidate(
    scanner_key: str = "stock_pullback_swing",
    symbol: str = "AAPL",
    price: float = 185.50,
    composite_score: float = 72.3,
    confidence: float | None = None,
) -> dict:
    """Build a minimal stock candidate matching the stock scanner output shape."""
    base = {
        "symbol": symbol,
        "strategy_id": scanner_key,
        "trade_type": "stock_long",
        "trade_key": f"{symbol}|STOCK|{scanner_key}|NA|NA|NA",
        "idea_key": f"{symbol}|{scanner_key}",
        "price": price,
        "underlying_price": price,
        "entry_reference": price,
        "composite_score": composite_score,
        "score_breakdown": {"trend": 25.0, "pullback": 20.0, "reset": 15.0, "liquidity": 12.3},
        "metrics": {
            "sma20": 183.0,
            "sma50": 178.5,
            "atr_pct": 0.025,
            "rsi14": 42.5,
            "avg_dollar_volume": 15_000_000,
        },
        "thesis": ["Healthy pullback to rising 20-SMA", "RSI reset from overbought"],
        "risk_notes": [],
        "as_of": "2026-03-15T14:30:00+00:00",
        "data_source": {
            "history": "tradier",
            "confidence": 1.0,
        },
    }
    # Add scanner-specific fields
    if scanner_key == "stock_pullback_swing":
        base["trend_score"] = 25.0
        base["pullback_score"] = 20.0
        base["reset_score"] = 15.0
        base["liquidity_score"] = 12.3
        base["trend_state"] = "uptrend"
    elif scanner_key == "stock_momentum_breakout":
        base["breakout_score"] = 28.0
        base["volume_score"] = 22.0
        base["trend_score"] = 18.0
        base["base_quality_score"] = 4.3
        base["breakout_state"] = "confirmed_breakout"
    elif scanner_key == "stock_mean_reversion":
        base["oversold_score"] = 30.0
        base["stabilization_score"] = 18.0
        base["room_score"] = 14.0
        base["liquidity_score"] = 10.3
        base["reversion_state"] = "oversold_stabilizing"
        base["confidence"] = confidence or 0.85
    elif scanner_key == "stock_volatility_expansion":
        base["expansion_score"] = 26.0
        base["compression_score"] = 20.0
        base["confirmation_score"] = 16.0
        base["risk_score"] = 10.3
        base["expansion_state"] = "expanding"
        base["confidence"] = confidence or 0.90
    return base


# ── Fixtures: options candidate shapes ───────────────────────────────

def _make_options_candidate(
    strategy_id: str = "put_credit_spread",
    symbol: str = "SPY",
) -> dict:
    """Build a minimal options candidate matching post-normalize_trade() shape."""
    return {
        "symbol": symbol,
        "underlying": symbol,
        "underlying_symbol": symbol,
        "strategy_id": strategy_id,
        "spread_type": strategy_id,
        "strategy": strategy_id,
        "trade_key": f"{symbol}|{strategy_id}|2026-04-17|540|535|33",
        "trade_id": f"{symbol}|{strategy_id}|2026-04-17|540|535|33",
        "expiration": "2026-04-17",
        "dte": 33,
        "short_strike": 540.0,
        "long_strike": 535.0,
        "price": 590.0,
        "underlying_price": 590.0,
        "composite_score": 85.5,
        "rank_score": 85.5,
        "computed": {
            "max_profit": 125.0,
            "max_loss": -375.0,
            "pop": 0.72,
            "return_on_risk": 0.333,
            "expected_value": 22.50,
            "kelly_fraction": 0.12,
            "iv_rank": 45.0,
            "short_strike_z": -1.8,
            "bid_ask_pct": 0.08,
            "strike_dist_pct": 0.085,
            "rsi14": 48.0,
            "rv_20d": 0.18,
            "open_interest": 5200.0,
            "volume": 1500.0,
            "ev_to_risk": 0.06,
        },
        "details": {
            "break_even": 538.75,
            "dte": 33.0,
            "expected_move": 15.0,
            "iv_rv_ratio": 1.2,
            "trade_quality_score": 78.0,
            "market_regime": "neutral",
        },
        "pills": {
            "strategy_label": "Put Credit Spread",
            "dte": 33,
            "pop": 0.72,
            "oi": 5200.0,
            "vol": 1500.0,
            "regime_label": "neutral",
        },
        "pricing": {
            "spread_mid": 1.25,
            "spread_natural": 1.10,
            "spread_mark": 1.175,
        },
        "computed_metrics": {
            "max_profit": 125.0,
            "max_loss": -375.0,
            "pop": 0.72,
            "net_credit": 1.25,
        },
        "metrics_status": {
            "ready": True,
            "missing_fields": [],
        },
        "validation_warnings": [],
        "engine_gate_status": {
            "passed": True,
            "failed_reasons": [],
        },
        "legs": [
            {
                "name": "short_put",
                "right": "put",
                "side": "sell",
                "strike": 540.0,
                "qty": 1,
                "bid": 3.20,
                "ask": 3.50,
                "mid": 3.35,
                "delta": -0.28,
                "iv": 0.22,
                "open_interest": 5200,
                "volume": 1500,
                "occ_symbol": "SPY260417P00540000",
            },
            {
                "name": "long_put",
                "right": "put",
                "side": "buy",
                "strike": 535.0,
                "qty": 1,
                "bid": 2.00,
                "ask": 2.30,
                "mid": 2.15,
                "delta": -0.22,
                "iv": 0.23,
                "open_interest": 3800,
                "volume": 900,
                "occ_symbol": "SPY260417P00535000",
            },
        ],
        "tie_breaks": {"ev_to_risk": 0.06, "pop": 0.72},
        "as_of": "2026-03-15T14:30:00+00:00",
    }


# ═════════════════════════════════════════════════════════════════════
#  1. Contract shape validation — all REQUIRED_FIELDS present
# ═════════════════════════════════════════════════════════════════════

class TestContractShapeValidation:
    """Every normalized output must contain all REQUIRED_FIELDS."""

    @pytest.mark.parametrize("scanner_key", [
        "stock_pullback_swing",
        "stock_momentum_breakout",
        "stock_mean_reversion",
        "stock_volatility_expansion",
    ])
    def test_stock_candidate_has_all_required_fields(self, scanner_key: str):
        candidate = _make_stock_candidate(scanner_key=scanner_key)
        result = normalize_candidate_output(scanner_key, candidate)
        missing = REQUIRED_FIELDS - set(result.keys())
        assert not missing, f"Missing required fields for {scanner_key}: {missing}"

    @pytest.mark.parametrize("strategy_id", [
        "put_credit_spread",
        "call_credit_spread",
        "put_debit",
        "call_debit",
        "iron_condor",
        "butterfly_debit",
        "calendar_spread",
    ])
    def test_options_candidate_has_all_required_fields(self, strategy_id: str):
        candidate = _make_options_candidate(strategy_id=strategy_id)
        result = normalize_candidate_output(strategy_id, candidate)
        missing = REQUIRED_FIELDS - set(result.keys())
        assert not missing, f"Missing required fields for {strategy_id}: {missing}"


# ═════════════════════════════════════════════════════════════════════
#  2. Stock candidate normalization — field mapping accuracy
# ═════════════════════════════════════════════════════════════════════

class TestStockCandidateNormalization:

    def test_pullback_swing_identity(self):
        c = _make_stock_candidate("stock_pullback_swing", symbol="MSFT", price=420.0)
        n = normalize_candidate_output("stock_pullback_swing", c)
        assert n["scanner_key"] == "stock_pullback_swing"
        assert n["scanner_name"] == "Pullback Swing"
        assert n["strategy_family"] == "stock"
        assert n["asset_class"] == "equity"
        assert n["symbol"] == "MSFT"
        assert n["underlying"] == "MSFT"
        assert n["direction"] == "long"

    def test_momentum_breakout_identity(self):
        c = _make_stock_candidate("stock_momentum_breakout", symbol="NVDA")
        n = normalize_candidate_output("stock_momentum_breakout", c)
        assert n["scanner_key"] == "stock_momentum_breakout"
        assert n["scanner_name"] == "Momentum Breakout"
        assert n["setup_type"] == "momentum_breakout"

    def test_mean_reversion_identity(self):
        c = _make_stock_candidate("stock_mean_reversion")
        n = normalize_candidate_output("stock_mean_reversion", c)
        assert n["scanner_key"] == "stock_mean_reversion"
        assert n["scanner_name"] == "Mean Reversion"

    def test_volatility_expansion_identity(self):
        c = _make_stock_candidate("stock_volatility_expansion")
        n = normalize_candidate_output("stock_volatility_expansion", c)
        assert n["scanner_key"] == "stock_volatility_expansion"
        assert n["scanner_name"] == "Volatility Expansion"

    def test_stock_setup_quality_maps_from_composite_score(self):
        c = _make_stock_candidate(composite_score=78.5)
        n = normalize_candidate_output("stock_pullback_swing", c)
        assert n["setup_quality"] == 78.5

    def test_stock_confidence_from_data_source(self):
        c = _make_stock_candidate()
        c["data_source"]["confidence"] = 0.85
        n = normalize_candidate_output("stock_pullback_swing", c)
        assert n["confidence"] == 0.85

    def test_stock_confidence_from_top_level(self):
        """Mean reversion / vol expansion have top-level confidence"""
        c = _make_stock_candidate("stock_mean_reversion", confidence=0.92)
        n = normalize_candidate_output("stock_mean_reversion", c)
        assert n["confidence"] == 0.92

    def test_stock_thesis_summary_is_list(self):
        c = _make_stock_candidate()
        n = normalize_candidate_output("stock_pullback_swing", c)
        assert isinstance(n["thesis_summary"], list)
        assert len(n["thesis_summary"]) == 2
        assert "Healthy pullback" in n["thesis_summary"][0]

    def test_stock_pricing_snapshot(self):
        c = _make_stock_candidate(price=185.50)
        n = normalize_candidate_output("stock_pullback_swing", c)
        assert n["pricing_snapshot"]["price"] == 185.50
        assert n["pricing_snapshot"]["underlying_price"] == 185.50

    def test_stock_candidate_metrics_includes_scores(self):
        c = _make_stock_candidate(composite_score=72.3)
        n = normalize_candidate_output("stock_pullback_swing", c)
        cm = n["candidate_metrics"]
        assert cm["composite_score"] == 72.3
        assert "score_breakdown" in cm
        assert cm["score_breakdown"]["trend"] == 25.0

    def test_stock_candidate_metrics_includes_enrichment(self):
        c = _make_stock_candidate()
        n = normalize_candidate_output("stock_pullback_swing", c)
        cm = n["candidate_metrics"]
        assert cm["sma20"] == 183.0
        assert cm["rsi14"] == 42.5

    def test_stock_detail_sections_sub_scores(self):
        c = _make_stock_candidate("stock_pullback_swing")
        n = normalize_candidate_output("stock_pullback_swing", c)
        ds = n["detail_sections"]
        assert "sub_scores" in ds
        assert ds["sub_scores"]["trend_score"] == 25.0

    def test_stock_detail_sections_state(self):
        c = _make_stock_candidate("stock_pullback_swing")
        n = normalize_candidate_output("stock_pullback_swing", c)
        ds = n["detail_sections"]
        assert "state" in ds
        assert ds["state"]["trend_state"] == "uptrend"

    def test_stock_entry_context(self):
        c = _make_stock_candidate(price=185.50)
        c["trend_state"] = "uptrend"
        n = normalize_candidate_output("stock_pullback_swing", c)
        ec = n["entry_context"]
        assert ec["price"] == 185.50
        assert ec["state"] == "uptrend"

    def test_stock_time_horizon(self):
        c = _make_stock_candidate()
        n = normalize_candidate_output("stock_pullback_swing", c)
        assert n["time_horizon"] == "swing"


# ═════════════════════════════════════════════════════════════════════
#  3. Options candidate normalization — field mapping accuracy
# ═════════════════════════════════════════════════════════════════════

class TestOptionsCandidateNormalization:

    def test_credit_spread_identity(self):
        c = _make_options_candidate("put_credit_spread")
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["scanner_key"] == "put_credit_spread"
        assert n["scanner_name"] == "Put Credit Spread"
        assert n["strategy_family"] == "options"
        assert n["asset_class"] == "option"
        assert n["symbol"] == "SPY"
        assert n["direction"] == "short"

    def test_debit_spread_direction(self):
        c = _make_options_candidate("put_debit")
        n = normalize_candidate_output("put_debit", c)
        assert n["direction"] == "long"

    def test_iron_condor_direction(self):
        c = _make_options_candidate("iron_condor")
        n = normalize_candidate_output("iron_condor", c)
        assert n["direction"] == "neutral"

    def test_options_setup_quality_from_composite(self):
        c = _make_options_candidate()
        c["composite_score"] = 85.5
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["setup_quality"] == 85.5

    def test_options_risk_definition(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        rd = n["risk_definition"]
        assert rd["type"] == "defined_risk_spread"
        assert rd["max_loss_per_contract"] == -375.0
        assert rd["pop"] == 0.72

    def test_options_reward_profile(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        rp = n["reward_profile"]
        assert rp["max_profit_per_contract"] == 125.0
        assert rp["expected_value_per_contract"] == 22.50
        assert rp["return_on_risk"] == 0.333

    def test_options_entry_context(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        ec = n["entry_context"]
        assert ec["spread_mid"] == 1.25
        assert ec["short_strike"] == 540.0
        assert ec["long_strike"] == 535.0
        assert ec["expiration"] == "2026-04-17"
        assert ec["dte"] == 33

    def test_options_pricing_snapshot(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        ps = n["pricing_snapshot"]
        assert ps["spread_mid"] == 1.25
        assert ps["spread_natural"] == 1.10
        assert ps["underlying_price"] == 590.0

    def test_options_strategy_structure_has_legs(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        ss = n["strategy_structure"]
        assert ss is not None
        assert len(ss["legs"]) == 2
        assert ss["short_strike"] == 540.0
        assert ss["long_strike"] == 535.0

    def test_options_candidate_metrics(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        cm = n["candidate_metrics"]
        assert cm["pop"] == 0.72
        assert cm["max_profit"] == 125.0
        assert cm["max_loss"] == -375.0
        assert cm["expected_value"] == 22.50
        assert cm["iv_rank"] == 45.0
        assert cm["break_even"] == 538.75

    def test_options_supporting_signals(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        signals = n["supporting_signals"]
        assert any("POP=72%" in s for s in signals)
        assert any("EV=$22.50" in s for s in signals)

    def test_options_thesis_summary(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        assert isinstance(n["thesis_summary"], list)
        assert any("Put Credit Spread" in t for t in n["thesis_summary"])

    def test_options_market_context_tags(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        tags = n["market_context_tags"]
        assert "put_credit_spread" in tags
        assert "neutral" in tags  # from market_regime
        assert "long_dte" in tags  # 33 DTE > 30

    def test_options_data_quality(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        dq = n["data_quality"]
        assert dq["metrics_ready"] is True
        assert dq["missing_fields"] == []

    def test_options_confidence_high_when_metrics_ready(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["confidence"] == 1.0

    def test_options_confidence_lower_when_metrics_not_ready(self):
        c = _make_options_candidate()
        c["metrics_status"]["ready"] = False
        c["metrics_status"]["missing_fields"] = ["max_profit", "max_loss"]
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["confidence"] < 1.0

    def test_options_detail_sections(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        ds = n["detail_sections"]
        assert "tie_breaks" in ds
        assert "computed_metrics" in ds
        assert "engine_gate_status" in ds
        assert "pills" in ds

    def test_options_time_horizon(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["time_horizon"] == "days_to_expiry"


# ═════════════════════════════════════════════════════════════════════
#  4. Backward compatibility — existing fields not mutated
# ═════════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:

    def test_stock_candidate_original_fields_preserved(self):
        """normalize_candidate_output() returns a NEW dict; original is untouched."""
        original = _make_stock_candidate()
        original_copy = copy.deepcopy(original)
        result = normalize_candidate_output("stock_pullback_swing", original)

        # The result is a separate dict
        assert result is not original

        # Original candidate fields are not mutated
        assert original["symbol"] == original_copy["symbol"]
        assert original["composite_score"] == original_copy["composite_score"]
        assert original["metrics"] == original_copy["metrics"]
        assert original["thesis"] == original_copy["thesis"]

    def test_options_candidate_original_fields_preserved(self):
        original = _make_options_candidate()
        original_copy = copy.deepcopy(original)
        result = normalize_candidate_output("put_credit_spread", original)

        assert result is not original
        assert original["computed"] == original_copy["computed"]
        assert original["legs"] == original_copy["legs"]

    def test_normalized_key_attachment_pattern(self):
        """The integration pattern: candidate['normalized'] = normalize_candidate_output(...)"""
        candidate = _make_stock_candidate()
        candidate["normalized"] = normalize_candidate_output("stock_pullback_swing", candidate)

        # Original fields still there
        assert candidate["symbol"] == "AAPL"
        assert candidate["composite_score"] == 72.3

        # Normalized contract also present
        assert "normalized" in candidate
        assert candidate["normalized"]["strategy_family"] == "stock"
        assert candidate["normalized"]["setup_quality"] == 72.3


# ═════════════════════════════════════════════════════════════════════
#  5. Sorting/filtering safety — composite_score preserved
# ═════════════════════════════════════════════════════════════════════

class TestSortingFilteringSafety:

    def test_stock_composite_score_in_candidate_metrics(self):
        c = _make_stock_candidate(composite_score=72.3)
        n = normalize_candidate_output("stock_pullback_swing", c)
        assert n["candidate_metrics"]["composite_score"] == 72.3
        assert n["setup_quality"] == 72.3

    def test_options_composite_score_in_candidate_metrics(self):
        c = _make_options_candidate()
        c["composite_score"] = 85.5
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["candidate_metrics"]["composite_score"] == 85.5
        assert n["setup_quality"] == 85.5

    def test_stock_ranking_order_preserved(self):
        """Multiple stock candidates should maintain sort order by setup_quality."""
        candidates = [
            _make_stock_candidate(symbol="AAPL", composite_score=72.3),
            _make_stock_candidate(symbol="MSFT", composite_score=85.0),
            _make_stock_candidate(symbol="GOOG", composite_score=60.1),
        ]
        normalized = [
            normalize_candidate_output("stock_pullback_swing", c)
            for c in candidates
        ]
        # Sort by setup_quality descending (like stock engine does with composite_score)
        sorted_norm = sorted(normalized, key=lambda x: -(x["setup_quality"] or 0))
        assert sorted_norm[0]["symbol"] == "MSFT"
        assert sorted_norm[1]["symbol"] == "AAPL"
        assert sorted_norm[2]["symbol"] == "GOOG"


# ═════════════════════════════════════════════════════════════════════
#  6. Edge cases
# ═════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_empty_candidate(self):
        """Empty dict should produce a valid contract with None/empty values."""
        result = normalize_candidate_output("stock_pullback_swing", {})
        missing = REQUIRED_FIELDS - set(result.keys())
        assert not missing, f"Missing fields: {missing}"
        assert result["symbol"] == "UNKNOWN"
        assert result["setup_quality"] is None

    def test_unknown_scanner_key(self):
        """Unknown scanner key should still produce a valid contract."""
        c = _make_stock_candidate()
        c["trade_type"] = "stock_long"
        result = normalize_candidate_output("stock_unknown_scanner", c)
        missing = REQUIRED_FIELDS - set(result.keys())
        assert not missing
        assert result["scanner_key"] == "stock_unknown_scanner"
        assert result["strategy_family"] == "stock"

    def test_options_unknown_strategy(self):
        """Unknown options strategy should still produce valid contract."""
        c = _make_options_candidate("exotic_strategy")
        result = normalize_candidate_output("exotic_strategy", c)
        missing = REQUIRED_FIELDS - set(result.keys())
        assert not missing
        assert result["scanner_key"] == "exotic_strategy"

    def test_missing_computed_on_options(self):
        """Options candidate with missing computed sub-dict."""
        c = _make_options_candidate()
        del c["computed"]
        result = normalize_candidate_output("put_credit_spread", c)
        missing = REQUIRED_FIELDS - set(result.keys())
        assert not missing
        assert result["candidate_metrics"]["pop"] is None

    def test_missing_thesis_on_stock(self):
        """Stock candidate with no thesis."""
        c = _make_stock_candidate()
        del c["thesis"]
        result = normalize_candidate_output("stock_pullback_swing", c)
        assert result["thesis_summary"] == []

    def test_none_composite_score(self):
        """Candidate with None composite_score."""
        c = _make_stock_candidate(composite_score=None)
        c["composite_score"] = None
        result = normalize_candidate_output("stock_pullback_swing", c)
        assert result["setup_quality"] is None
        assert result["candidate_metrics"]["composite_score"] is None

    def test_stock_candidate_with_rank(self):
        """rank field should appear in detail_sections."""
        c = _make_stock_candidate()
        c["rank"] = 3
        result = normalize_candidate_output("stock_pullback_swing", c)
        assert result["detail_sections"]["rank"] == 3

    def test_options_no_legs(self):
        """Options candidate with empty legs list."""
        c = _make_options_candidate()
        c["legs"] = []
        result = normalize_candidate_output("put_credit_spread", c)
        assert result["strategy_structure"] is None  # No legs = no structure

    def test_options_gate_failed_adds_risk_flags(self):
        """Failed engine gate reasons should appear in risk_flags."""
        c = _make_options_candidate()
        c["engine_gate_status"] = {
            "passed": False,
            "failed_reasons": ["METRICS_MISSING:pop", "METRICS_MISSING:max_loss"],
        }
        result = normalize_candidate_output("put_credit_spread", c)
        assert any("Gate: METRICS_MISSING:pop" in f for f in result["risk_flags"])


# ═════════════════════════════════════════════════════════════════════
#  7. SCANNER_METADATA completeness
# ═════════════════════════════════════════════════════════════════════

class TestScannerMetadata:

    def test_all_stock_scanners_in_metadata(self):
        for key in [
            "stock_pullback_swing",
            "stock_momentum_breakout",
            "stock_mean_reversion",
            "stock_volatility_expansion",
        ]:
            assert key in SCANNER_METADATA, f"Missing metadata for {key}"
            meta = SCANNER_METADATA[key]
            assert meta["strategy_family"] == "stock"
            assert meta["asset_class"] == "equity"

    def test_all_options_strategies_in_metadata(self):
        for key in [
            "put_credit_spread",
            "call_credit_spread",
            "put_debit",
            "call_debit",
            "iron_condor",
            "butterfly_debit",
            "calendar_spread",
            "calendar_call_spread",
            "calendar_put_spread",
            "csp",
            "covered_call",
            "income",
        ]:
            assert key in SCANNER_METADATA, f"Missing metadata for {key}"
            meta = SCANNER_METADATA[key]
            assert meta["strategy_family"] == "options"
            assert meta["asset_class"] == "option"

    def test_metadata_has_required_fields(self):
        for key, meta in SCANNER_METADATA.items():
            assert "name" in meta, f"{key}: missing 'name'"
            assert "strategy_family" in meta, f"{key}: missing 'strategy_family'"
            assert "asset_class" in meta, f"{key}: missing 'asset_class'"
            assert "setup_type" in meta, f"{key}: missing 'setup_type'"
            assert "direction" in meta, f"{key}: missing 'direction'"
            assert "time_horizon" in meta, f"{key}: missing 'time_horizon'"


# ═════════════════════════════════════════════════════════════════════
#  8. REQUIRED_FIELDS coverage
# ═════════════════════════════════════════════════════════════════════

class TestRequiredFieldsCoverage:

    def test_required_fields_count(self):
        """Contract should have exactly 28 required fields."""
        assert len(REQUIRED_FIELDS) == 28

    def test_required_fields_match_docstring(self):
        """All fields listed in module docstring should be in REQUIRED_FIELDS."""
        expected = {
            "candidate_id", "scanner_key", "scanner_name", "strategy_family",
            "setup_type", "asset_class", "symbol", "underlying", "direction",
            "thesis_summary", "entry_context", "time_horizon", "setup_quality",
            "confidence", "risk_definition", "reward_profile",
            "supporting_signals", "risk_flags", "invalidation_signals",
            "market_context_tags", "position_sizing_notes", "data_quality",
            "source_status", "pricing_snapshot", "strategy_structure",
            "candidate_metrics", "detail_sections", "generated_at",
        }
        assert REQUIRED_FIELDS == expected


# ═════════════════════════════════════════════════════════════════════
#  9. Cross-family consistency
# ═════════════════════════════════════════════════════════════════════

class TestCrossFamilyConsistency:
    """Stock and options normalized outputs should share the same field set."""

    def test_stock_and_options_same_keys(self):
        stock = normalize_candidate_output(
            "stock_pullback_swing",
            _make_stock_candidate(),
        )
        options = normalize_candidate_output(
            "put_credit_spread",
            _make_options_candidate(),
        )
        stock_keys = set(stock.keys())
        options_keys = set(options.keys())
        assert stock_keys == options_keys, (
            f"Key diff: stock_only={stock_keys - options_keys}, "
            f"options_only={options_keys - stock_keys}"
        )

    def test_all_scanners_produce_identical_key_sets(self):
        """Every scanner family produces the same top-level key set."""
        results = {}
        for scanner_key in [
            "stock_pullback_swing",
            "stock_momentum_breakout",
            "stock_mean_reversion",
            "stock_volatility_expansion",
        ]:
            c = _make_stock_candidate(scanner_key=scanner_key)
            results[scanner_key] = set(normalize_candidate_output(scanner_key, c).keys())

        for strategy_id in [
            "put_credit_spread",
            "call_credit_spread",
            "iron_condor",
            "butterfly_debit",
        ]:
            c = _make_options_candidate(strategy_id=strategy_id)
            results[strategy_id] = set(normalize_candidate_output(strategy_id, c).keys())

        key_sets = list(results.values())
        for i, ks in enumerate(key_sets[1:], 1):
            assert ks == key_sets[0], (
                f"Key set mismatch at index {i}: "
                f"extra={ks - key_sets[0]}, missing={key_sets[0] - ks}"
            )
