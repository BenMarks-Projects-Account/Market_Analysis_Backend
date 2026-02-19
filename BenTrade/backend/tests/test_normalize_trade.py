"""Regression tests for the unified ``normalize_trade()`` builder.

Coverage targets:
 1. Per-share → per-contract scaling (EV, max_profit, max_loss)
 2. Null handling — blanks stay null, no invented values
 3. Composite-strike keys (iron_condor, butterfly)
 4. Simple strike fallback to ``strike`` field
 5. Strategy alias canonicalization
 6. Symbol triple-write
 7. Validation warnings for missing key metrics
 8. Pills sub-dict shape
 9. DTE derivation toggle
10. Homepage pick reads same per-contract values as scanner
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.utils.normalize import normalize_trade, strategy_label


# ── 1. Per-share → per-contract scaling ──────────────────────────────


def test_per_share_to_per_contract_scaling():
    """EV, max_profit, max_loss scale from per-share to per-contract via multiplier."""
    trade = {
        "underlying": "SPY",
        "spread_type": "put_credit_spread",
        "expiration": "2026-03-20",
        "short_strike": 550,
        "long_strike": 545,
        "dte": 30,
        "ev_per_share": 0.25,
        "max_profit_per_share": 1.10,
        "max_loss_per_share": 3.90,
        "contractsMultiplier": 100,
    }
    result = normalize_trade(trade)

    assert result["computed"]["expected_value"] == 25.0
    assert result["computed"]["max_profit"] == pytest.approx(110.0)
    assert result["computed"]["max_loss"] == pytest.approx(390.0)


def test_per_contract_fields_preferred_over_per_share():
    """When per-contract values already exist, they take priority over per-share."""
    trade = {
        "underlying": "AAPL",
        "spread_type": "put_credit_spread",
        "expiration": "2026-03-20",
        "short_strike": 220,
        "long_strike": 215,
        "dte": 30,
        "ev_per_contract": 42.0,
        "ev_per_share": 0.25,
        "max_profit_per_contract": 130.0,
        "max_profit_per_share": 1.10,
        "max_loss_per_contract": 370.0,
        "max_loss_per_share": 3.90,
    }
    result = normalize_trade(trade)

    assert result["computed"]["expected_value"] == 42.0
    assert result["computed"]["max_profit"] == 130.0
    assert result["computed"]["max_loss"] == 370.0


# ── 2. Null handling ────────────────────────────────────────────────


def test_null_fields_stay_null():
    """Missing metrics must remain None — never zero or invented values."""
    trade = {
        "underlying": "TSLA",
        "spread_type": "put_credit_spread",
        "expiration": "2026-04-17",
        "short_strike": 300,
        "long_strike": 295,
        "dte": 45,
    }
    result = normalize_trade(trade)

    assert result["computed"]["expected_value"] is None
    assert result["computed"]["max_profit"] is None
    assert result["computed"]["max_loss"] is None
    assert result["computed"]["pop"] is None
    assert result["computed"]["return_on_risk"] is None
    # computed_metrics mirrors
    cm = result["computed_metrics"]
    assert cm["expected_value"] is None
    assert cm["pop"] is None


# ── 3. Composite-strike keys — iron condor ───────────────────────────


def test_iron_condor_composite_trade_key():
    """Iron condor trade key includes P/C composite strike format."""
    trade = {
        "underlying": "SPY",
        "spread_type": "iron_condor",
        "expiration": "2026-03-20",
        "dte": 30,
        "put_short_strike": 540,
        "put_long_strike": 535,
        "call_short_strike": 570,
        "call_long_strike": 575,
    }
    result = normalize_trade(trade)

    assert result["trade_key"] == "SPY|2026-03-20|iron_condor|P540|C570|P535|C575|30"
    assert result["short_strike"] == "P540|C570"
    assert result["long_strike"] == "P535|C575"


# ── 4. Simple strike fallback ────────────────────────────────────────


def test_single_leg_strike_fallback():
    """When only 'strike' is provided (no short_strike/long_strike), use it."""
    trade = {
        "underlying": "AAPL",
        "spread_type": "csp",
        "expiration": "2026-03-20",
        "strike": 215,
        "dte": 30,
    }
    result = normalize_trade(trade)

    assert "215" in result["trade_key"]
    assert result["short_strike"] == 215


def test_generic_strategy_strike_fallback():
    """For non-single-leg strategies with only 'strike', still use it."""
    trade = {
        "underlying": "QQQ",
        "spread_type": "put_credit_spread",
        "expiration": "2026-03-20",
        "strike": 500,
        "dte": 14,
    }
    result = normalize_trade(trade)

    # strike should map to short_strike via generic fallback
    assert result["short_strike"] == 500


# ── 5. Strategy alias canonicalization ───────────────────────────────


@pytest.mark.parametrize(
    ("input_strategy", "expected_canonical"),
    [
        ("credit_put_spread", "put_credit_spread"),
        ("debit_call_butterfly", "butterfly_debit"),
        ("butterflies", "butterfly_debit"),
        ("put_credit", "put_credit_spread"),
        ("call_debit", "call_debit"),
    ],
)
def test_strategy_alias_canonicalization(input_strategy, expected_canonical):
    """All strategy aliases must canonicalize to their standard form."""
    trade = {
        "underlying": "SPY",
        "spread_type": input_strategy,
        "expiration": "2026-03-20",
        "short_strike": 550,
        "long_strike": 545,
        "dte": 30,
    }
    result = normalize_trade(trade)

    assert result["strategy_id"] == expected_canonical
    assert result["spread_type"] == expected_canonical
    assert result["strategy"] == expected_canonical


# ── 6. Symbol triple-write ───────────────────────────────────────────


def test_symbol_triple_write():
    """All three symbol fields must be uppercased and in sync."""
    trade = {
        "underlying": "aapl",
        "spread_type": "put_credit_spread",
        "expiration": "2026-03-20",
        "short_strike": 220,
        "long_strike": 215,
        "dte": 30,
    }
    result = normalize_trade(trade)

    assert result["underlying"] == "AAPL"
    assert result["underlying_symbol"] == "AAPL"
    assert result["symbol"] == "AAPL"


# ── 7. Validation warnings ──────────────────────────────────────────


def test_validation_warnings_for_missing_metrics():
    """Missing key metrics must produce the correct validation warnings."""
    trade = {
        "underlying": "MSFT",
        "spread_type": "put_credit_spread",
        "expiration": "2026-03-20",
        "short_strike": 400,
        "long_strike": 395,
        "dte": 30,
    }
    result = normalize_trade(trade)
    warnings = result.get("validation_warnings", [])

    assert "POP_NOT_IMPLEMENTED_FOR_STRATEGY" in warnings
    assert "REGIME_UNAVAILABLE" in warnings
    assert "MAX_PROFIT_UNAVAILABLE" in warnings
    assert "MAX_LOSS_UNAVAILABLE" in warnings
    assert "EXPECTED_VALUE_UNAVAILABLE" in warnings
    assert "RETURN_ON_RISK_UNAVAILABLE" in warnings


def test_no_duplicate_warnings():
    """Calling normalize_trade twice must not duplicate warnings."""
    trade = {
        "underlying": "MSFT",
        "spread_type": "put_credit_spread",
        "expiration": "2026-03-20",
        "short_strike": 400,
        "long_strike": 395,
        "dte": 30,
    }
    first = normalize_trade(trade)
    second = normalize_trade(first)
    warnings = second.get("validation_warnings", [])

    # Each warning code should appear exactly once
    for code in ("POP_NOT_IMPLEMENTED_FOR_STRATEGY", "REGIME_UNAVAILABLE"):
        assert warnings.count(code) == 1


# ── 8. Pills sub-dict shape ─────────────────────────────────────────


def test_pills_shape():
    """The pills sub-dict must include required keys with correct values."""
    trade = {
        "underlying": "SPY",
        "spread_type": "put_credit_spread",
        "expiration": "2026-03-20",
        "short_strike": 550,
        "long_strike": 545,
        "dte": 30,
        "p_win_used": 0.72,
        "open_interest": 5000,
        "volume": 1200,
        "market_regime": "NEUTRAL",
    }
    result = normalize_trade(trade)
    pills = result["pills"]

    assert pills["strategy_label"] == "Put Credit Spread"
    assert pills["dte"] == 30.0
    assert pills["pop"] == 0.72
    assert pills["oi"] == 5000.0
    assert pills["vol"] == 1200.0
    assert pills["regime_label"] == "NEUTRAL"


# ── 9. DTE derivation toggle ────────────────────────────────────────


def test_dte_not_derived_by_default():
    """When derive_dte=False, missing DTE stays as-is."""
    trade = {
        "underlying": "SPY",
        "spread_type": "put_credit_spread",
        "expiration": "2026-03-20",
        "short_strike": 550,
        "long_strike": 545,
    }
    result = normalize_trade(trade, derive_dte=False)
    assert result["dte"] is None


# ── 10. Homepage pick reads same per-contract values as scanner ──────


def test_homepage_scanner_parity():
    """The computed sub-dict must carry the SAME per-contract values that
    the homepage recommendation engine reads via computed.expected_value,
    computed.max_profit, computed.max_loss."""
    trade = {
        "underlying": "AAPL",
        "spread_type": "put_credit_spread",
        "expiration": "2026-03-20",
        "short_strike": 220,
        "long_strike": 215,
        "dte": 33,
        "ev_per_share": 0.35,
        "max_profit_per_share": 1.25,
        "max_loss_per_share": 3.75,
        "return_on_risk": 0.3333,
        "p_win_used": 0.72,
    }
    result = normalize_trade(trade)

    # Scanner path values (in computed dict)
    computed = result["computed"]
    assert computed["expected_value"] == 35.0
    assert computed["max_profit"] == 125.0
    assert computed["max_loss"] == 375.0
    assert computed["pop"] == 0.72
    assert computed["return_on_risk"] == 0.3333

    # Homepage path: reads from computed_metrics (via apply_metrics_contract)
    cm = result["computed_metrics"]
    assert cm["expected_value"] == 35.0
    assert cm["max_profit"] == 125.0
    assert cm["max_loss"] == 375.0
    assert cm["pop"] == 0.72

    # Legacy backfill removed — values only live in computed/computed_metrics


# ── strategy_label helper ────────────────────────────────────────────


def test_strategy_label_known():
    assert strategy_label("put_credit_spread") == "Put Credit Spread"
    assert strategy_label("butterfly_debit") == "Debit Butterfly"
    assert strategy_label("csp") == "Cash Secured Put"


def test_strategy_label_unknown_fallback():
    assert strategy_label("exotic_straddle") == "Exotic Straddle"
