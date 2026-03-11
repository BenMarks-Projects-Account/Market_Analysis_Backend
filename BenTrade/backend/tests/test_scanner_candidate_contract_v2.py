"""Tests for scanner_candidate_contract.py — Second Pass (Step 2).

Covers:
  1. Options confidence derivation — multi-factor behavior
  2. Direct field-mapping for remaining options strategy variants
  3. Normalized shape consistency across ALL stock + options scanners
  4. Required-field presence for ALL supported normalized candidates
  5. Incomplete/weak candidate data handling
  6. Backward compatibility for downstream consumers
  7. Cross-family contract alignment
"""

from __future__ import annotations

import pytest

from app.services.scanner_candidate_contract import (
    REQUIRED_FIELDS,
    SCANNER_METADATA,
    normalize_candidate_output,
    _derive_options_confidence,
)


# ── Shared fixture builders ──────────────────────────────────────────

def _make_stock_candidate(
    scanner_key: str = "stock_pullback_swing",
    symbol: str = "AAPL",
    price: float = 185.50,
    composite_score: float = 72.3,
) -> dict:
    """Minimal stock candidate matching scanner output shape."""
    base = {
        "symbol": symbol,
        "strategy_id": scanner_key,
        "trade_type": "stock_long",
        "trade_key": f"{symbol}|STOCK|{scanner_key}|NA|NA|NA",
        "price": price,
        "underlying_price": price,
        "entry_reference": price,
        "composite_score": composite_score,
        "score_breakdown": {"trend": 25.0, "pullback": 20.0},
        "metrics": {"sma20": 183.0, "atr_pct": 0.025, "rsi14": 42.5},
        "thesis": ["Healthy pullback to rising 20-SMA"],
        "risk_notes": [],
        "as_of": "2026-03-15T14:30:00+00:00",
        "data_source": {"history": "tradier", "confidence": 1.0},
    }
    if scanner_key == "stock_pullback_swing":
        base["trend_state"] = "uptrend"
    elif scanner_key == "stock_momentum_breakout":
        base["breakout_state"] = "confirmed_breakout"
    elif scanner_key == "stock_mean_reversion":
        base["reversion_state"] = "oversold_stabilizing"
    elif scanner_key == "stock_volatility_expansion":
        base["expansion_state"] = "expanding"
    return base


def _make_options_candidate(
    strategy_id: str = "put_credit_spread",
    symbol: str = "SPY",
    *,
    composite_score: float = 85.5,
    pop: float = 0.72,
    max_profit: float = 125.0,
    max_loss: float = -375.0,
    ev: float = 22.50,
    ror: float = 0.333,
    iv_rank: float = 45.0,
    bid_ask_pct: float = 0.08,
    metrics_ready: bool = True,
    missing_fields: list | None = None,
    validation_warnings: list | None = None,
    legs: list | None = None,
) -> dict:
    """Build a configurable options candidate for testing.

    All computed values can be overridden to test specific scenarios.
    """
    return {
        "symbol": symbol,
        "underlying": symbol,
        "underlying_symbol": symbol,
        "strategy_id": strategy_id,
        "trade_key": f"{symbol}|{strategy_id}|2026-04-17|540|535|33",
        "trade_id": f"{symbol}|{strategy_id}|2026-04-17|540|535|33",
        "expiration": "2026-04-17",
        "dte": 33,
        "short_strike": 540.0,
        "long_strike": 535.0,
        "price": 590.0,
        "underlying_price": 590.0,
        "composite_score": composite_score,
        "rank_score": composite_score,
        "computed": {
            "max_profit": max_profit,
            "max_loss": max_loss,
            "pop": pop,
            "return_on_risk": ror,
            "expected_value": ev,
            "kelly_fraction": 0.12,
            "iv_rank": iv_rank,
            "bid_ask_pct": bid_ask_pct,
            "ev_to_risk": 0.06,
        },
        "details": {
            "break_even": 538.75,
            "dte": 33.0,
            "market_regime": "neutral",
            "trade_quality_score": 78.0,
        },
        "pills": {
            "strategy_label": SCANNER_METADATA.get(strategy_id, {}).get("name", strategy_id),
            "dte": 33,
            "pop": pop,
        },
        "pricing": {
            "spread_mid": 1.25,
            "spread_natural": 1.10,
            "spread_mark": 1.175,
        },
        "computed_metrics": {
            "max_profit": max_profit,
            "max_loss": max_loss,
            "pop": pop,
            "net_credit": 1.25,
        },
        "metrics_status": {
            "ready": metrics_ready,
            "missing_fields": missing_fields or [],
        },
        "validation_warnings": validation_warnings or [],
        "engine_gate_status": {"passed": True, "failed_reasons": []},
        "legs": legs if legs is not None else [
            {
                "name": "short_put", "right": "put", "side": "sell",
                "strike": 540.0, "bid": 3.20, "ask": 3.50, "mid": 3.35,
                "delta": -0.28, "iv": 0.22,
            },
            {
                "name": "long_put", "right": "put", "side": "buy",
                "strike": 535.0, "bid": 2.00, "ask": 2.30, "mid": 2.15,
                "delta": -0.22, "iv": 0.23,
            },
        ],
        "tie_breaks": {"ev_to_risk": 0.06, "pop": pop},
        "as_of": "2026-03-15T14:30:00+00:00",
    }


# ═════════════════════════════════════════════════════════════════════
#  1. Options confidence derivation — multi-factor behavior
# ═════════════════════════════════════════════════════════════════════

class TestOptionsConfidenceDerivation:
    """Confidence should reflect data quality and completeness, not trade quality."""

    def test_full_candidate_high_confidence(self):
        """All fields present, consistent, ready → high confidence."""
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["confidence"] >= 0.90

    def test_missing_metrics_lowers_confidence(self):
        """Missing computed fields lower data_completeness factor."""
        c = _make_options_candidate()
        c["computed"] = {"pop": 0.72, "max_loss": -375.0}  # only 2 of 7
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["confidence"] < 0.80

    def test_no_legs_lowers_confidence(self):
        """No legs penalises structure_quality factor."""
        c = _make_options_candidate(legs=[])
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["confidence"] < 1.0

    def test_wide_bid_ask_lowers_confidence(self):
        """Wide spread penalises structure_quality factor."""
        c = _make_options_candidate(bid_ask_pct=0.30)
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["confidence"] < 1.0

    def test_validation_warnings_lower_confidence(self):
        """Validation warnings penalise consistency factor."""
        c = _make_options_candidate(
            validation_warnings=["MISSING:pop", "STALE:iv", "LOW_OI"],
        )
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["confidence"] < 1.0

    def test_not_ready_lowers_confidence(self):
        """metrics_status.ready=False penalises consistency."""
        c = _make_options_candidate(metrics_ready=False)
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["confidence"] < 1.0

    def test_ev_pop_mismatch_lowers_confidence(self):
        """Positive POP + negative EV → suspicious data → lower consistency."""
        c = _make_options_candidate(pop=0.72, ev=-5.0)
        n = normalize_candidate_output("put_credit_spread", c)
        full = normalize_candidate_output(
            "put_credit_spread", _make_options_candidate(),
        )
        assert n["confidence"] < full["confidence"]

    def test_gutted_candidate_very_low_confidence(self):
        """No computed, no legs, no pricing → near-zero confidence."""
        c = _make_options_candidate()
        c["computed"] = {}
        c["legs"] = []
        c["pricing"] = {}
        c["metrics_status"] = {"ready": False, "missing_fields": []}
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["confidence"] < 0.30

    def test_confidence_always_in_range(self):
        """Confidence stays within [0.0, 1.0] even with extreme inputs."""
        for pop, ev, ready, legs in [
            (0.99, 100, True, True),
            (0.10, -50, False, False),
            (None, None, False, False),
        ]:
            c = _make_options_candidate(
                pop=pop or 0.5, ev=ev or 0, metrics_ready=ready,
                legs=[] if not legs else None,
            )
            if pop is None:
                c["computed"]["pop"] = None
            if ev is None:
                c["computed"]["expected_value"] = None
            n = normalize_candidate_output("put_credit_spread", c)
            assert 0.0 <= n["confidence"] <= 1.0

    def test_confidence_distinct_from_setup_quality(self):
        """A low-ranked trade can have high confidence if data is complete."""
        c = _make_options_candidate(composite_score=20.0)  # bad trade
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["setup_quality"] == 20.0  # trade quality is low
        assert n["confidence"] >= 0.90     # but data confidence is high

    def test_confidence_not_a_pass_fail_gate(self):
        """Confidence has granularity — not just 1.0 vs 0.7."""
        scores = set()
        for bap in [0.02, 0.10, 0.20, 0.30]:
            c = _make_options_candidate(bid_ask_pct=bap)
            n = normalize_candidate_output("put_credit_spread", c)
            scores.add(n["confidence"])
        # Should produce at least 2 distinct values across the range
        assert len(scores) >= 2

    def test_no_pricing_reduces_structure_quality(self):
        """Missing spread_mid pricing lowers structure_quality factor."""
        c = _make_options_candidate()
        c["pricing"] = {}
        n = normalize_candidate_output("put_credit_spread", c)
        full = normalize_candidate_output(
            "put_credit_spread", _make_options_candidate(),
        )
        assert n["confidence"] <= full["confidence"]


class TestDeriveOptionsConfidenceDirect:
    """Direct unit tests for the _derive_options_confidence function."""

    def test_perfect_inputs(self):
        computed = {
            "max_profit": 125, "max_loss": -375, "pop": 0.72,
            "expected_value": 22.5, "return_on_risk": 0.33,
            "iv_rank": 45, "bid_ask_pct": 0.05,
        }
        ms = {"ready": True, "missing_fields": []}
        legs = [{"strike": 540}]
        pricing = {"spread_mid": 1.25}
        result = _derive_options_confidence(computed, ms, [], legs, pricing)
        assert result == 1.0

    def test_empty_computed(self):
        result = _derive_options_confidence({}, {"ready": False}, [], [], {})
        assert result < 0.30

    def test_partial_computed(self):
        computed = {"max_loss": -375, "pop": 0.72, "max_profit": 125}
        ms = {"ready": True, "missing_fields": []}
        result = _derive_options_confidence(computed, ms, [], [{"s": 1}], {"spread_mid": 1.0})
        assert 0.50 < result < 1.0

    def test_many_warnings_reduce_consistency(self):
        computed = {
            "max_profit": 125, "max_loss": -375, "pop": 0.72,
            "expected_value": 22.5, "return_on_risk": 0.33,
            "iv_rank": 45, "bid_ask_pct": 0.05,
        }
        ms = {"ready": True}
        warns = ["w1", "w2", "w3", "w4", "w5"]
        result = _derive_options_confidence(computed, ms, warns, [{"s": 1}], {"spread_mid": 1.0})
        perfect = _derive_options_confidence(computed, ms, [], [{"s": 1}], {"spread_mid": 1.0})
        assert result < perfect


# ═════════════════════════════════════════════════════════════════════
#  2. Direct field-mapping for remaining options strategy variants
# ═════════════════════════════════════════════════════════════════════

class TestCallCreditSpreadFieldMapping:
    """call_credit_spread — direct field-mapping assertions."""

    def test_identity_fields(self):
        c = _make_options_candidate("call_credit_spread", "QQQ")
        n = normalize_candidate_output("call_credit_spread", c)
        assert n["scanner_key"] == "call_credit_spread"
        assert n["scanner_name"] == "Call Credit Spread"
        assert n["strategy_family"] == "options"
        assert n["asset_class"] == "option"
        assert n["symbol"] == "QQQ"
        assert n["direction"] == "short"
        assert n["setup_type"] == "call_credit_spread"

    def test_risk_reward(self):
        c = _make_options_candidate("call_credit_spread")
        n = normalize_candidate_output("call_credit_spread", c)
        assert n["risk_definition"]["max_loss_per_contract"] == -375.0
        assert n["reward_profile"]["max_profit_per_contract"] == 125.0
        assert n["reward_profile"]["expected_value_per_contract"] == 22.50

    def test_entry_context(self):
        c = _make_options_candidate("call_credit_spread")
        n = normalize_candidate_output("call_credit_spread", c)
        ec = n["entry_context"]
        assert ec["spread_mid"] == 1.25
        assert ec["short_strike"] == 540.0
        assert ec["dte"] == 33

    def test_all_required_fields(self):
        c = _make_options_candidate("call_credit_spread")
        n = normalize_candidate_output("call_credit_spread", c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing


class TestCallDebitFieldMapping:
    """call_debit — direct field-mapping assertions."""

    def test_identity_fields(self):
        c = _make_options_candidate("call_debit", "AAPL")
        n = normalize_candidate_output("call_debit", c)
        assert n["scanner_key"] == "call_debit"
        assert n["scanner_name"] == "Call Debit Spread"
        assert n["direction"] == "long"
        assert n["setup_type"] == "call_debit"

    def test_setup_quality_maps_from_composite(self):
        c = _make_options_candidate("call_debit", composite_score=62.3)
        n = normalize_candidate_output("call_debit", c)
        assert n["setup_quality"] == 62.3

    def test_supporting_signals_generated(self):
        c = _make_options_candidate("call_debit", pop=0.68, ev=15.0, iv_rank=55)
        n = normalize_candidate_output("call_debit", c)
        assert any("POP" in s for s in n["supporting_signals"])
        assert any("EV" in s for s in n["supporting_signals"])

    def test_all_required_fields(self):
        c = _make_options_candidate("call_debit")
        n = normalize_candidate_output("call_debit", c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing


class TestPutDebitFieldMapping:
    """put_debit — extended field-mapping beyond direction."""

    def test_identity_fields(self):
        c = _make_options_candidate("put_debit")
        n = normalize_candidate_output("put_debit", c)
        assert n["scanner_key"] == "put_debit"
        assert n["scanner_name"] == "Put Debit Spread"
        assert n["direction"] == "long"

    def test_strategy_structure(self):
        c = _make_options_candidate("put_debit")
        n = normalize_candidate_output("put_debit", c)
        ss = n["strategy_structure"]
        assert ss is not None
        assert len(ss["legs"]) == 2

    def test_candidate_metrics(self):
        c = _make_options_candidate("put_debit", pop=0.60, ror=0.50)
        n = normalize_candidate_output("put_debit", c)
        cm = n["candidate_metrics"]
        assert cm["pop"] == 0.60
        assert cm["return_on_risk"] == 0.50

    def test_all_required_fields(self):
        c = _make_options_candidate("put_debit")
        n = normalize_candidate_output("put_debit", c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing


class TestIronCondorFieldMapping:
    """iron_condor — direct field-mapping assertions."""

    def test_identity_fields(self):
        c = _make_options_candidate("iron_condor", "IWM")
        n = normalize_candidate_output("iron_condor", c)
        assert n["scanner_key"] == "iron_condor"
        assert n["scanner_name"] == "Iron Condor"
        assert n["direction"] == "neutral"
        assert n["setup_type"] == "iron_condor"

    def test_risk_definition(self):
        c = _make_options_candidate("iron_condor", max_loss=-400.0, pop=0.75)
        n = normalize_candidate_output("iron_condor", c)
        assert n["risk_definition"]["max_loss_per_contract"] == -400.0
        assert n["risk_definition"]["pop"] == 0.75

    def test_market_context_tags(self):
        c = _make_options_candidate("iron_condor")
        n = normalize_candidate_output("iron_condor", c)
        assert "iron_condor" in n["market_context_tags"]

    def test_thesis_includes_strategy_label(self):
        c = _make_options_candidate("iron_condor")
        n = normalize_candidate_output("iron_condor", c)
        assert any("Iron Condor" in t for t in n["thesis_summary"])

    def test_all_required_fields(self):
        c = _make_options_candidate("iron_condor")
        n = normalize_candidate_output("iron_condor", c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing


class TestButterflyDebitFieldMapping:
    """butterfly_debit — direct field-mapping assertions."""

    def test_identity_fields(self):
        c = _make_options_candidate("butterfly_debit", "SPY")
        n = normalize_candidate_output("butterfly_debit", c)
        assert n["scanner_key"] == "butterfly_debit"
        assert n["scanner_name"] == "Debit Butterfly"
        assert n["direction"] == "neutral"
        assert n["setup_type"] == "butterfly_debit"

    def test_entry_context_has_strikes(self):
        c = _make_options_candidate("butterfly_debit")
        n = normalize_candidate_output("butterfly_debit", c)
        ec = n["entry_context"]
        assert ec["short_strike"] == 540.0
        assert ec["long_strike"] == 535.0
        assert ec["expiration"] == "2026-04-17"

    def test_pricing_snapshot(self):
        c = _make_options_candidate("butterfly_debit")
        n = normalize_candidate_output("butterfly_debit", c)
        ps = n["pricing_snapshot"]
        assert ps["spread_mid"] == 1.25
        assert ps["underlying_price"] == 590.0

    def test_all_required_fields(self):
        c = _make_options_candidate("butterfly_debit")
        n = normalize_candidate_output("butterfly_debit", c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing


class TestCalendarSpreadFieldMapping:
    """calendar_spread — direct field-mapping assertions."""

    def test_identity_fields(self):
        c = _make_options_candidate("calendar_spread")
        n = normalize_candidate_output("calendar_spread", c)
        assert n["scanner_key"] == "calendar_spread"
        assert n["scanner_name"] == "Calendar Spread"
        assert n["direction"] == "neutral"

    def test_time_horizon(self):
        c = _make_options_candidate("calendar_spread")
        n = normalize_candidate_output("calendar_spread", c)
        assert n["time_horizon"] == "days_to_expiry"

    def test_all_required_fields(self):
        c = _make_options_candidate("calendar_spread")
        n = normalize_candidate_output("calendar_spread", c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing


class TestCalendarCallSpreadFieldMapping:
    """calendar_call_spread — direct field-mapping assertions."""

    def test_identity_fields(self):
        c = _make_options_candidate("calendar_call_spread")
        n = normalize_candidate_output("calendar_call_spread", c)
        assert n["scanner_key"] == "calendar_call_spread"
        assert n["scanner_name"] == "Call Calendar Spread"
        assert n["direction"] == "neutral"
        assert n["setup_type"] == "calendar_call_spread"

    def test_risk_reward(self):
        c = _make_options_candidate("calendar_call_spread", max_profit=80.0, max_loss=-200.0)
        n = normalize_candidate_output("calendar_call_spread", c)
        assert n["risk_definition"]["max_loss_per_contract"] == -200.0
        assert n["reward_profile"]["max_profit_per_contract"] == 80.0

    def test_all_required_fields(self):
        c = _make_options_candidate("calendar_call_spread")
        n = normalize_candidate_output("calendar_call_spread", c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing


class TestCalendarPutSpreadFieldMapping:
    """calendar_put_spread — direct field-mapping assertions."""

    def test_identity_fields(self):
        c = _make_options_candidate("calendar_put_spread")
        n = normalize_candidate_output("calendar_put_spread", c)
        assert n["scanner_key"] == "calendar_put_spread"
        assert n["scanner_name"] == "Put Calendar Spread"
        assert n["direction"] == "neutral"

    def test_candidate_metrics(self):
        c = _make_options_candidate("calendar_put_spread", iv_rank=60.0)
        n = normalize_candidate_output("calendar_put_spread", c)
        assert n["candidate_metrics"]["iv_rank"] == 60.0

    def test_all_required_fields(self):
        c = _make_options_candidate("calendar_put_spread")
        n = normalize_candidate_output("calendar_put_spread", c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing


class TestCspFieldMapping:
    """csp (Cash Secured Put) — direct field-mapping assertions."""

    def test_identity_fields(self):
        c = _make_options_candidate("csp", "AAPL")
        n = normalize_candidate_output("csp", c)
        assert n["scanner_key"] == "csp"
        assert n["scanner_name"] == "Cash Secured Put"
        assert n["direction"] == "short"
        assert n["setup_type"] == "csp"

    def test_risk_definition_type(self):
        c = _make_options_candidate("csp")
        n = normalize_candidate_output("csp", c)
        assert n["risk_definition"]["type"] == "defined_risk_spread"

    def test_supporting_signals(self):
        c = _make_options_candidate("csp", pop=0.80, ev=30.0, iv_rank=50)
        n = normalize_candidate_output("csp", c)
        signals = n["supporting_signals"]
        assert any("POP=80%" in s for s in signals)
        assert any("EV" in s for s in signals)

    def test_all_required_fields(self):
        c = _make_options_candidate("csp")
        n = normalize_candidate_output("csp", c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing


class TestCoveredCallFieldMapping:
    """covered_call — direct field-mapping assertions."""

    def test_identity_fields(self):
        c = _make_options_candidate("covered_call", "MSFT")
        n = normalize_candidate_output("covered_call", c)
        assert n["scanner_key"] == "covered_call"
        assert n["scanner_name"] == "Covered Call"
        assert n["direction"] == "short"

    def test_entry_context(self):
        c = _make_options_candidate("covered_call")
        n = normalize_candidate_output("covered_call", c)
        ec = n["entry_context"]
        assert ec["spread_mid"] == 1.25
        assert ec["dte"] == 33

    def test_invalidation_signals_low_pop(self):
        """Low POP should produce invalidation signal."""
        c = _make_options_candidate("covered_call", pop=0.40)
        n = normalize_candidate_output("covered_call", c)
        assert any("Low POP" in s for s in n["invalidation_signals"])

    def test_all_required_fields(self):
        c = _make_options_candidate("covered_call")
        n = normalize_candidate_output("covered_call", c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing


class TestIncomeFieldMapping:
    """income — direct field-mapping assertions."""

    def test_identity_fields(self):
        c = _make_options_candidate("income")
        n = normalize_candidate_output("income", c)
        assert n["scanner_key"] == "income"
        assert n["scanner_name"] == "Income Strategy"
        assert n["direction"] == "short"
        assert n["setup_type"] == "income"

    def test_data_quality(self):
        c = _make_options_candidate("income")
        n = normalize_candidate_output("income", c)
        dq = n["data_quality"]
        assert dq["metrics_ready"] is True
        assert isinstance(dq["missing_fields"], list)

    def test_detail_sections_has_pills(self):
        c = _make_options_candidate("income")
        n = normalize_candidate_output("income", c)
        assert "pills" in n["detail_sections"]

    def test_all_required_fields(self):
        c = _make_options_candidate("income")
        n = normalize_candidate_output("income", c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing


# ═════════════════════════════════════════════════════════════════════
#  3. Normalized shape consistency across ALL scanners
# ═════════════════════════════════════════════════════════════════════

_ALL_OPTIONS_KEYS = [
    "put_credit_spread", "call_credit_spread",
    "put_debit", "call_debit",
    "iron_condor", "butterfly_debit",
    "calendar_spread", "calendar_call_spread", "calendar_put_spread",
    "csp", "covered_call", "income",
]

_ALL_STOCK_KEYS = [
    "stock_pullback_swing", "stock_momentum_breakout",
    "stock_mean_reversion", "stock_volatility_expansion",
]


class TestFullContractShapeConsistency:
    """Every scanner produces the exact same top-level key set."""

    @pytest.mark.parametrize("strategy_id", _ALL_OPTIONS_KEYS)
    def test_options_variant_has_all_required_fields(self, strategy_id: str):
        c = _make_options_candidate(strategy_id)
        n = normalize_candidate_output(strategy_id, c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing, f"{strategy_id}: missing {missing}"

    @pytest.mark.parametrize("scanner_key", _ALL_STOCK_KEYS)
    def test_stock_variant_has_all_required_fields(self, scanner_key: str):
        c = _make_stock_candidate(scanner_key)
        n = normalize_candidate_output(scanner_key, c)
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing, f"{scanner_key}: missing {missing}"

    def test_all_scanners_produce_identical_key_sets(self):
        """Every supported scanner produces the same top-level keys."""
        results = {}
        for sk in _ALL_STOCK_KEYS:
            results[sk] = set(normalize_candidate_output(
                sk, _make_stock_candidate(sk)).keys())
        for sid in _ALL_OPTIONS_KEYS:
            results[sid] = set(normalize_candidate_output(
                sid, _make_options_candidate(sid)).keys())

        ref_keys = results[_ALL_STOCK_KEYS[0]]
        for key, ks in results.items():
            assert ks == ref_keys, (
                f"{key}: extra={ks - ref_keys}, missing={ref_keys - ks}"
            )


# ═════════════════════════════════════════════════════════════════════
#  4. Incomplete / weak candidate data handling
# ═════════════════════════════════════════════════════════════════════

class TestWeakCandidateHandling:
    """Normalization must not crash on incomplete data."""

    def test_empty_options_candidate(self):
        n = normalize_candidate_output("put_credit_spread", {})
        missing = REQUIRED_FIELDS - set(n.keys())
        assert not missing
        assert n["setup_quality"] is None
        assert n["confidence"] is not None
        assert n["confidence"] < 0.50

    def test_options_no_computed_no_legs(self):
        c = {"symbol": "SPY", "underlying": "SPY"}
        n = normalize_candidate_output("iron_condor", c)
        assert n["strategy_structure"] is None
        assert n["candidate_metrics"]["pop"] is None
        assert n["risk_definition"]["max_loss_per_contract"] is None

    def test_options_none_pop_does_not_crash(self):
        c = _make_options_candidate(pop=0.72)
        c["computed"]["pop"] = None
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["candidate_metrics"]["pop"] is None
        assert n["risk_definition"]["pop"] is None

    def test_options_negative_ev_creates_invalidation(self):
        c = _make_options_candidate(ev=-10.0)
        n = normalize_candidate_output("put_credit_spread", c)
        assert any("Negative EV" in s for s in n["invalidation_signals"])

    def test_options_wide_spread_creates_invalidation(self):
        c = _make_options_candidate(bid_ask_pct=0.25)
        n = normalize_candidate_output("put_credit_spread", c)
        assert any("bid-ask" in s.lower() for s in n["invalidation_signals"])

    def test_string_composite_score_handled(self):
        """Non-numeric composite_score should not crash."""
        c = _make_options_candidate()
        c["composite_score"] = "bad"
        c["rank_score"] = "also_bad"
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["setup_quality"] is None

    def test_missing_pricing_dict(self):
        c = _make_options_candidate()
        del c["pricing"]
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["pricing_snapshot"]["spread_mid"] is None

    def test_missing_details_dict(self):
        c = _make_options_candidate()
        del c["details"]
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["candidate_metrics"]["break_even"] is None


# ═════════════════════════════════════════════════════════════════════
#  5. Backward compatibility for downstream consumers
# ═════════════════════════════════════════════════════════════════════

class TestBackwardCompatibilityV2:
    """Confidence changes should not break existing consumption patterns."""

    def test_confidence_still_numeric(self):
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        assert isinstance(n["confidence"], float)

    def test_confidence_still_high_for_complete_candidate(self):
        """Existing fully-populated candidates still get high confidence."""
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["confidence"] >= 0.90

    def test_confidence_still_lower_for_incomplete(self):
        """Incomplete candidates still get lower confidence (directional compat)."""
        c = _make_options_candidate(metrics_ready=False,
                                     missing_fields=["a", "b", "c", "d"])
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["confidence"] < 1.0

    def test_stock_confidence_unchanged(self):
        """Stock confidence derivation was not changed."""
        c = _make_stock_candidate()
        n = normalize_candidate_output("stock_pullback_swing", c)
        assert n["confidence"] == 1.0  # from data_source.confidence

    def test_setup_quality_still_from_rank_score(self):
        c = _make_options_candidate(composite_score=77.0)
        n = normalize_candidate_output("put_credit_spread", c)
        assert n["setup_quality"] == 77.0

    def test_candidate_metrics_shape_unchanged(self):
        """All expected keys in candidate_metrics still present."""
        c = _make_options_candidate()
        n = normalize_candidate_output("put_credit_spread", c)
        expected_keys = {
            "composite_score", "max_profit", "max_loss", "pop",
            "expected_value", "return_on_risk", "kelly_fraction",
            "iv_rank", "ev_to_risk", "bid_ask_pct", "break_even",
        }
        assert expected_keys <= set(n["candidate_metrics"].keys())


# ═════════════════════════════════════════════════════════════════════
#  6. Cross-family contract alignment
# ═════════════════════════════════════════════════════════════════════

class TestCrossFamilyAlignment:
    """Stock and options candidates should share the same contract schema."""

    def test_shared_fields_present_both_families(self):
        """Core shared fields are populated in both stock and options."""
        stock = normalize_candidate_output(
            "stock_pullback_swing", _make_stock_candidate(),
        )
        options = normalize_candidate_output(
            "put_credit_spread", _make_options_candidate(),
        )
        shared = [
            "strategy_family", "setup_type", "symbol", "underlying",
            "direction", "thesis_summary", "entry_context",
            "time_horizon", "setup_quality", "confidence",
            "supporting_signals", "risk_flags", "risk_definition",
            "reward_profile",
        ]
        for field in shared:
            assert field in stock, f"Stock missing {field}"
            assert field in options, f"Options missing {field}"
            # Neither should be the default "UNKNOWN" for well-defined candidates
            if field in ("symbol", "underlying"):
                assert stock[field] not in ("", "UNKNOWN")
                assert options[field] not in ("", "UNKNOWN")

    def test_both_have_numeric_quality_scores(self):
        stock = normalize_candidate_output(
            "stock_pullback_swing", _make_stock_candidate(),
        )
        options = normalize_candidate_output(
            "put_credit_spread", _make_options_candidate(),
        )
        assert isinstance(stock["setup_quality"], float)
        assert isinstance(options["setup_quality"], float)
        assert isinstance(stock["confidence"], float)
        assert isinstance(options["confidence"], float)

    def test_options_family_strategy_structure_not_none(self):
        """Options with legs should have strategy_structure populated."""
        n = normalize_candidate_output(
            "put_credit_spread", _make_options_candidate(),
        )
        assert n["strategy_structure"] is not None
        assert "legs" in n["strategy_structure"]

    def test_stock_family_strategy_structure_is_none(self):
        """Stock candidates should have strategy_structure = None."""
        n = normalize_candidate_output(
            "stock_pullback_swing", _make_stock_candidate(),
        )
        assert n["strategy_structure"] is None


# ═════════════════════════════════════════════════════════════════════
#  7. SCANNER_METADATA coverage
# ═════════════════════════════════════════════════════════════════════

class TestScannerMetadataV2:
    """Verify all strategy variants have complete metadata."""

    @pytest.mark.parametrize("strategy_id", _ALL_OPTIONS_KEYS)
    def test_options_metadata_complete(self, strategy_id: str):
        assert strategy_id in SCANNER_METADATA
        meta = SCANNER_METADATA[strategy_id]
        assert meta["strategy_family"] == "options"
        assert meta["asset_class"] == "option"
        assert "direction" in meta
        assert "time_horizon" in meta
        assert "setup_type" in meta
        assert "name" in meta

    @pytest.mark.parametrize("scanner_key", _ALL_STOCK_KEYS)
    def test_stock_metadata_complete(self, scanner_key: str):
        assert scanner_key in SCANNER_METADATA
        meta = SCANNER_METADATA[scanner_key]
        assert meta["strategy_family"] == "stock"
        assert meta["asset_class"] == "equity"
