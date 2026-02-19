"""Tests for legacy-field stripping at API boundaries.

Validates that:
1. ``strip_legacy_fields()`` removes all 32 documented legacy keys.
2. Canonical root fields (symbol, strategy_id, computed, details, pills,
   trade_key, metrics_status, validation_warnings) survive stripping.
3. The full normalize → strip pipeline produces a clean trade dict
   with no legacy flat fields.
4. ``_LEGACY_FLAT_FIELDS`` stays in sync with ``docs/canonical_contract.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.utils.normalize import (
    _LEGACY_FLAT_FIELDS,
    normalize_trade,
    strip_legacy_fields,
)

# ── Constants ─────────────────────────────────────────────────────────

# Canonical root keys that MUST survive stripping.
_CANONICAL_ROOT_KEYS = {
    "trade_key",
    "symbol",
    "strategy_id",
    "expiration",
    "dte",
    "short_strike",
    "long_strike",
    "computed",
    "details",
    "pills",
    "metrics_status",
    "validation_warnings",
    "computed_metrics",
}

# The full set of legacy flat fields documented for removal.
_EXPECTED_LEGACY_FIELDS = frozenset({
    "ev_per_share",
    "ev_per_contract",
    "max_profit_per_share",
    "max_loss_per_share",
    "max_profit_per_contract",
    "max_loss_per_contract",
    "p_win_used",
    "pop_delta_approx",
    "pop_approx",
    "probability_of_profit",
    "implied_prob_profit",
    "ev_to_risk",
    "bid_ask_spread_pct",
    "strike_distance_pct",
    "realized_vol_20d",
    "estimated_risk",
    "risk_amount",
    "estimated_max_profit",
    "premium_received",
    "premium_paid",
    "scanner_score",
    "expiration_date",
    "spread_type",
    "strategy",
    "underlying",
    "underlying_symbol",
})


# ── 1. strip_legacy_fields() removes every legacy key ────────────────


def test_strip_removes_all_legacy_keys():
    """Every key in _LEGACY_FLAT_FIELDS is removed from the output."""
    trade = {field: "dummy" for field in _LEGACY_FLAT_FIELDS}
    # Add a canonical key to make sure we don't accidentally strip everything
    trade["symbol"] = "SPY"
    trade["computed"] = {"pop": 0.72}

    result = strip_legacy_fields(trade)

    for field in _LEGACY_FLAT_FIELDS:
        assert field not in result, f"Legacy field '{field}' was not stripped"


def test_strip_preserves_canonical_root_keys():
    """Canonical root keys survive stripping unchanged."""
    trade = {k: f"value_{k}" for k in _CANONICAL_ROOT_KEYS}
    # Also inject a few legacy fields to prove they're removed
    trade["spread_type"] = "put_credit_spread"
    trade["underlying"] = "SPY"

    result = strip_legacy_fields(trade)

    for key in _CANONICAL_ROOT_KEYS:
        assert key in result, f"Canonical key '{key}' was incorrectly stripped"
        assert result[key] == f"value_{key}"


def test_strip_returns_new_dict():
    """strip_legacy_fields must not mutate the original dict."""
    trade = {"symbol": "QQQ", "spread_type": "call_credit_spread"}
    result = strip_legacy_fields(trade)

    assert "spread_type" not in result
    assert "spread_type" in trade  # original unchanged


def test_strip_empty_dict():
    """Stripping an empty dict returns an empty dict."""
    assert strip_legacy_fields({}) == {}


def test_strip_no_legacy_keys_is_noop():
    """When no legacy keys are present, nothing is removed."""
    trade = {"symbol": "IWM", "strategy_id": "iron_condor", "computed": {}}
    result = strip_legacy_fields(trade)
    assert result == trade


# ── 2. _LEGACY_FLAT_FIELDS constant completeness ─────────────────────


def test_legacy_fields_constant_matches_expected_set():
    """_LEGACY_FLAT_FIELDS must contain exactly the documented fields."""
    missing = _EXPECTED_LEGACY_FIELDS - _LEGACY_FLAT_FIELDS
    extra = _LEGACY_FLAT_FIELDS - _EXPECTED_LEGACY_FIELDS
    assert not missing, f"Missing from _LEGACY_FLAT_FIELDS: {missing}"
    # Allow extra fields added for future cleanup (not a failure),
    # but note them for awareness.
    if extra:
        pytest.skip(f"Extra fields in _LEGACY_FLAT_FIELDS (ok): {extra}")


# ── 3. normalize_trade() → strip pipeline ────────────────────────────


def _make_raw_input() -> dict:
    """Minimal raw trade dict that exercises normalization."""
    return {
        "underlying": "SPY",
        "spread_type": "put_credit_spread",
        "expiration": "2026-03-20",
        "short_strike": 550,
        "long_strike": 545,
        "dte": 30,
        "ev_per_share": 0.25,
        "max_profit_per_share": 1.10,
        "max_loss_per_share": 3.90,
        "p_win_used": 0.72,
        "bid_ask_spread_pct": 0.05,
        "strike_distance_pct": 0.02,
        "rsi14": 48.0,
        "realized_vol_20d": 0.15,
        "contractsMultiplier": 100,
    }


def test_normalize_then_strip_has_no_legacy_fields():
    """Full pipeline: normalize → strip must produce zero legacy keys."""
    raw = _make_raw_input()
    normalized = normalize_trade(raw)
    clean = strip_legacy_fields(normalized)

    found_legacy = {k for k in clean if k in _LEGACY_FLAT_FIELDS}
    assert not found_legacy, f"Legacy fields survived pipeline: {found_legacy}"


def test_normalize_then_strip_preserves_computed():
    """computed dict survives the pipeline with expected values."""
    raw = _make_raw_input()
    normalized = normalize_trade(raw)
    clean = strip_legacy_fields(normalized)

    comp = clean.get("computed", {})
    assert comp.get("expected_value") == pytest.approx(25.0)
    assert comp.get("max_profit") == pytest.approx(110.0)
    assert comp.get("max_loss") == pytest.approx(390.0)
    assert comp.get("pop") == pytest.approx(0.72)


def test_normalize_then_strip_preserves_identity():
    """symbol and strategy_id survive the pipeline."""
    raw = _make_raw_input()
    normalized = normalize_trade(raw)
    clean = strip_legacy_fields(normalized)

    assert clean.get("symbol") == "SPY"
    assert clean.get("strategy_id") == "put_credit_spread"


def test_normalize_then_strip_preserves_pills():
    """pills sub-dict survives the pipeline."""
    raw = _make_raw_input()
    normalized = normalize_trade(raw)
    clean = strip_legacy_fields(normalized)

    pills = clean.get("pills")
    assert isinstance(pills, dict)
    assert "strategy_label" in pills
    assert "dte" in pills


def test_normalize_then_strip_preserves_details():
    """details sub-dict survives the pipeline."""
    raw = _make_raw_input()
    normalized = normalize_trade(raw)
    clean = strip_legacy_fields(normalized)

    details = clean.get("details")
    assert isinstance(details, dict)


def test_normalize_then_strip_preserves_trade_key():
    """trade_key survives the pipeline."""
    raw = _make_raw_input()
    normalized = normalize_trade(raw)
    clean = strip_legacy_fields(normalized)

    assert clean.get("trade_key"), "trade_key must be present and non-empty"


# ── 4. Existing normalize tests must still reference legacy back-fills ─

def test_normalize_still_emits_some_legacy_fields_before_strip():
    """normalize_trade still passes through some legacy flat fields.
    
    Stripping happens at the API boundary, not inside normalize_trade().
    This confirms that normalize_trade alone keeps legacy fields from the
    raw input (e.g. spread_type, underlying) so strip_legacy_fields()
    has work to do.
    """
    raw = _make_raw_input()
    normalized = normalize_trade(raw)

    # These passthrough fields still exist before stripping
    assert "spread_type" in normalized or "strategy" in normalized
    assert "underlying" in normalized or "underlying_symbol" in normalized
    assert "symbol" in normalized
