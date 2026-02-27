"""Tests for iron-condor multi-leg model analysis support.

Covers:
  1. TradeContract Pydantic validation for IC payloads (4 numeric strikes)
  2. TradeContract string-strike normalization (legacy "P649.0|C702.0" encoding)
  3. Regression: 2-leg credit spread still passes TradeContract
  4. build_analysis_facts for iron condor (4-strike structure, width derivation)
  5. compute_trade_metrics for iron condor (breakeven_low/high, max_loss)
"""

from app.models.trade_contract import TradeContract
from common.trade_analysis_engine import build_analysis_facts, compute_trade_metrics


# ── Fixtures ───────────────────────────────────────────────────────────────

def _ic_payload_clean() -> dict:
    """Iron condor payload with 4 numeric strikes (no string encoding)."""
    return {
        "spread_type": "iron_condor",
        "symbol": "SPY",
        "underlying": "SPY",
        "expiration": "2026-03-13",
        "dte": 14,
        "short_put_strike": 649.0,
        "long_put_strike": 644.0,
        "short_call_strike": 702.0,
        "long_call_strike": 707.0,
        "net_credit": 1.245,
        "width": 5.0,
        "max_loss": 375.5,
        "return_on_risk": 0.3316,
        "pop_approx": 0.5362,
        "iv": 0.19,
        "legs": [
            {"right": "put",  "side": "sell", "strike": 649.0, "qty": -1},
            {"right": "put",  "side": "buy",  "strike": 644.0, "qty": 1},
            {"right": "call", "side": "sell", "strike": 702.0, "qty": -1},
            {"right": "call", "side": "buy",  "strike": 707.0, "qty": 1},
        ],
    }


def _ic_payload_legacy_strings() -> dict:
    """Iron condor payload with legacy string-encoded strikes."""
    return {
        "spread_type": "iron_condor",
        "symbol": "SPY",
        "underlying": "SPY",
        "expiration": "2026-03-13",
        "dte": 14,
        "short_strike": "P649.0|C702.0",
        "long_strike": "P644.0|C707.0",
        "net_credit": 1.245,
        "width": 5.0,
    }


def _credit_spread_payload() -> dict:
    """Standard 2-leg put credit spread payload."""
    return {
        "spread_type": "put_credit",
        "underlying": "SPY",
        "short_strike": 580.0,
        "long_strike": 575.0,
        "dte": 7,
        "net_credit": 1.12,
        "width": 5.0,
        "return_on_risk": 0.288,
    }


# ── 1. TradeContract: IC with 4 numeric strikes passes validation ──────────

def test_trade_contract_iron_condor_clean_payload():
    """IC trade with 4 numeric leg-strike fields should validate cleanly."""
    contract = TradeContract.from_dict(_ic_payload_clean())
    d = contract.to_dict()

    # 4-leg fields present as floats
    assert d["short_put_strike"] == 649.0
    assert d["long_put_strike"] == 644.0
    assert d["short_call_strike"] == 702.0
    assert d["long_call_strike"] == 707.0

    # short_strike/long_strike should be None (not set for IC)
    assert d["short_strike"] is None
    assert d["long_strike"] is None

    # legs preserved
    assert isinstance(d["legs"], list)
    assert len(d["legs"]) == 4

    # Identity fields
    assert d["spread_type"] == "iron_condor"
    assert d["net_credit"] == 1.245


# ── 2. TradeContract: legacy string strikes auto-parsed ────────────────────

def test_trade_contract_iron_condor_string_strike_normalization():
    """Legacy 'P649.0|C702.0' strings should be parsed into 4 numeric fields."""
    contract = TradeContract.from_dict(_ic_payload_legacy_strings())
    d = contract.to_dict()

    # String strikes should have been cleared to None
    assert d["short_strike"] is None
    assert d["long_strike"] is None

    # 4 numeric strike fields populated from parsed strings
    assert d["short_put_strike"] == 649.0
    assert d["short_call_strike"] == 702.0
    assert d["long_put_strike"] == 644.0
    assert d["long_call_strike"] == 707.0


def test_trade_contract_iron_condor_string_strikes_dont_override_explicit():
    """If both string and explicit numeric strikes are present, explicit wins."""
    payload = _ic_payload_legacy_strings()
    payload["short_put_strike"] = 650.0  # explicit takes precedence
    payload["short_call_strike"] = 700.0

    contract = TradeContract.from_dict(payload)
    d = contract.to_dict()

    assert d["short_put_strike"] == 650.0   # kept explicit
    assert d["short_call_strike"] == 700.0  # kept explicit
    assert d["long_put_strike"] == 644.0    # parsed from string
    assert d["long_call_strike"] == 707.0   # parsed from string


# ── 3. Regression: 2-leg credit spread unchanged ──────────────────────────

def test_trade_contract_credit_spread_unchanged():
    """2-leg credit spread should still work exactly as before."""
    contract = TradeContract.from_dict(_credit_spread_payload())
    d = contract.to_dict()

    assert d["short_strike"] == 580.0
    assert d["long_strike"] == 575.0
    assert d["spread_type"] == "put_credit"
    assert d["net_credit"] == 1.12

    # IC fields should be None
    assert d["short_put_strike"] is None
    assert d["long_put_strike"] is None
    assert d["short_call_strike"] is None
    assert d["long_call_strike"] is None


# ── 4. build_analysis_facts: iron condor ──────────────────────────────────

def test_build_analysis_facts_iron_condor():
    """build_analysis_facts should populate 4-strike structure for IC trades."""
    trade = _ic_payload_clean()
    trade["price"] = 684.52  # underlying price
    facts = build_analysis_facts(trade)

    structure = facts["structure"]
    assert structure["is_condor"] is True
    assert structure["short_put_strike"] == 649.0
    assert structure["long_put_strike"] == 644.0
    assert structure["short_call_strike"] == 702.0
    assert structure["long_call_strike"] == 707.0
    assert structure["width"] == 5.0

    # No data quality flags for missing short_strike/long_strike
    # (those are not required for condors)
    assert "short_strike" not in facts["data_quality_flags"]
    assert "long_strike" not in facts["data_quality_flags"]


def test_build_analysis_facts_iron_condor_derives_width():
    """Width should be derived from leg strikes if not explicitly provided."""
    trade = _ic_payload_clean()
    trade["price"] = 684.52
    del trade["width"]  # remove explicit width
    facts = build_analysis_facts(trade)

    # Width derived from max(|649-644|, |702-707|) = 5.0
    assert facts["structure"]["width"] == 5.0


def test_build_analysis_facts_credit_spread_unchanged():
    """2-leg credit spread facts building should be unchanged."""
    trade = _credit_spread_payload()
    trade["price"] = 590.0
    facts = build_analysis_facts(trade)

    structure = facts["structure"]
    assert structure["is_condor"] is False
    assert structure["short_strike"] == 580.0
    assert structure["long_strike"] == 575.0
    assert structure["width"] == 5.0
    assert structure["short_put_strike"] is None
    assert structure["long_put_strike"] is None


# ── 5. compute_trade_metrics: iron condor breakevens ──────────────────────

def test_compute_trade_metrics_iron_condor():
    """IC metrics should include breakeven_low and breakeven_high."""
    trade = _ic_payload_clean()
    trade["price"] = 684.52
    trade["pop"] = 0.5362
    facts = build_analysis_facts(trade)
    metrics = compute_trade_metrics(facts)

    # max_profit = net_credit = 1.245
    assert metrics["max_profit_per_share"] == 1.245

    # max_loss = width - net_credit = 5.0 - 1.245 = 3.755
    assert abs(metrics["max_loss_per_share"] - 3.755) < 0.001

    # Breakeven low  = short_put  - net_credit = 649.0 - 1.245 = 647.755
    assert abs(metrics["breakeven_low"] - 647.755) < 0.001

    # Breakeven high = short_call + net_credit = 702.0 + 1.245 = 703.245
    assert abs(metrics["breakeven_high"] - 703.245) < 0.001

    # Single breakeven is None for IC
    assert metrics["breakeven"] is None

    # RoR = 1.245 / 3.755 ≈ 0.3316
    assert abs(metrics["return_on_risk"] - 0.3316) < 0.01

    # POP proxy from explicit pop
    assert abs(metrics["pop_proxy"] - 0.5362) < 0.001


def test_compute_trade_metrics_credit_spread_regression():
    """Credit spread metrics should be unchanged (breakeven_low/high = None)."""
    trade = _credit_spread_payload()
    trade["price"] = 590.0
    facts = build_analysis_facts(trade)
    metrics = compute_trade_metrics(facts)

    assert metrics["max_profit_per_share"] == 1.12
    assert abs(metrics["max_loss_per_share"] - 3.88) < 0.001

    # Single breakeven for put credit: 580 - 1.12 = 578.88
    assert abs(metrics["breakeven"] - 578.88) < 0.001

    # IC-specific fields should be None
    assert metrics["breakeven_low"] is None
    assert metrics["breakeven_high"] is None


# ── 6. End-to-end: TradeContract → build_analysis_facts roundtrip ─────────

def test_iron_condor_contract_to_facts_roundtrip():
    """Full path: legacy string payload → TradeContract → to_dict → facts."""
    # Simulate what the API endpoint does
    raw_payload = _ic_payload_legacy_strings()
    raw_payload["price"] = 684.52
    raw_payload["pop"] = 0.54

    contract = TradeContract.from_dict(raw_payload)
    trade_dict = contract.to_dict()

    # build_analysis_facts should work without errors
    facts = build_analysis_facts(trade_dict)

    assert facts["structure"]["is_condor"] is True
    assert facts["structure"]["short_put_strike"] == 649.0
    assert facts["structure"]["short_call_strike"] == 702.0
    assert facts["structure"]["long_put_strike"] == 644.0
    assert facts["structure"]["long_call_strike"] == 707.0

    # Metrics should compute cleanly
    metrics = compute_trade_metrics(facts)
    assert metrics["max_profit_per_share"] is not None
    assert metrics["max_loss_per_share"] is not None
    assert metrics["breakeven_low"] is not None
    assert metrics["breakeven_high"] is not None
    assert metrics["return_on_risk"] is not None
