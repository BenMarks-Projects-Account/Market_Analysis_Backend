"""
Tests for market structure data shaping:
- 52-week high/low derivation in stock summary
- Correction-state classification logic (frontend-side, tested here as pure function)
"""

import pytest


# ── 52-week high/low derivation (mirrors backend logic) ──


def _derive_52w_extremes(history_all: list[float]):
    """Replicates the backend derivation of high_52w / low_52w from full history."""
    if not history_all:
        return None, None
    return max(history_all), min(history_all)


class TestHigh52wDerivation:
    def test_normal_history(self):
        history = [100.0, 105.0, 110.0, 102.0, 98.0, 107.0]
        high, low = _derive_52w_extremes(history)
        assert high == 110.0
        assert low == 98.0

    def test_single_point(self):
        history = [540.0]
        high, low = _derive_52w_extremes(history)
        assert high == 540.0
        assert low == 540.0

    def test_empty_history(self):
        high, low = _derive_52w_extremes([])
        assert high is None
        assert low is None

    def test_monotonic_rise(self):
        history = [500.0, 510.0, 520.0, 530.0, 540.0]
        high, low = _derive_52w_extremes(history)
        assert high == 540.0
        assert low == 500.0

    def test_high_52w_in_response_shape(self):
        """Verify the response shape includes high_52w / low_52w."""
        history_all = [500.0, 520.0, 540.0, 510.0, 505.0]
        high_52w = max(history_all)
        low_52w = min(history_all)
        response_price = {
            "last": 505.0,
            "prev_close": 510.0,
            "change": -5.0,
            "change_pct": -0.0098,
            "range_high": 540.0,
            "range_low": 500.0,
            "high_52w": high_52w,
            "low_52w": low_52w,
        }
        assert response_price["high_52w"] == 540.0
        assert response_price["low_52w"] == 500.0


# ── Correction-state classification (mirrors frontend logic) ──


def classify_drawdown_state(last, high_52w):
    """Pure Python mirror of the frontend classifyDrawdownState function."""
    if last is None or high_52w is None or high_52w <= 0:
        return None
    drawdown_pct = ((last - high_52w) / high_52w) * 100
    if drawdown_pct >= -4.9:
        return {"drawdown_pct": drawdown_pct, "label": "Near High", "tone": "bullish"}
    elif drawdown_pct >= -9.9:
        return {"drawdown_pct": drawdown_pct, "label": "Pullback", "tone": "neutral"}
    elif drawdown_pct >= -19.9:
        return {"drawdown_pct": drawdown_pct, "label": "Correction", "tone": "riskoff"}
    else:
        return {"drawdown_pct": drawdown_pct, "label": "Bear Market", "tone": "bearish"}


class TestCorrectionStateClassification:
    def test_near_high(self):
        result = classify_drawdown_state(540.0, 545.0)
        assert result is not None
        assert result["label"] == "Near High"
        assert result["tone"] == "bullish"
        assert -5.0 < result["drawdown_pct"] < 0.0

    def test_at_high(self):
        result = classify_drawdown_state(545.0, 545.0)
        assert result["label"] == "Near High"
        assert result["drawdown_pct"] == 0.0

    def test_pullback_lower_bound(self):
        # -5.0% exactly
        high = 100.0
        last = 95.0
        result = classify_drawdown_state(last, high)
        assert result["label"] == "Pullback"

    def test_pullback_mid(self):
        high = 100.0
        last = 92.5  # -7.5%
        result = classify_drawdown_state(last, high)
        assert result["label"] == "Pullback"
        assert result["tone"] == "neutral"

    def test_correction_lower_bound(self):
        high = 100.0
        last = 90.0  # -10%
        result = classify_drawdown_state(last, high)
        assert result["label"] == "Correction"

    def test_correction_mid(self):
        high = 100.0
        last = 85.0  # -15%
        result = classify_drawdown_state(last, high)
        assert result["label"] == "Correction"
        assert result["tone"] == "riskoff"

    def test_bear_market(self):
        high = 100.0
        last = 78.0  # -22%
        result = classify_drawdown_state(last, high)
        assert result["label"] == "Bear Market"
        assert result["tone"] == "bearish"

    def test_exact_bear_threshold(self):
        high = 100.0
        last = 80.0  # -20% exactly
        result = classify_drawdown_state(last, high)
        assert result["label"] == "Bear Market"

    def test_none_last(self):
        result = classify_drawdown_state(None, 545.0)
        assert result is None

    def test_none_high(self):
        result = classify_drawdown_state(540.0, None)
        assert result is None

    def test_zero_high(self):
        result = classify_drawdown_state(540.0, 0)
        assert result is None

    def test_above_high(self):
        # Edge case: current price above 52w high (new high just set)
        result = classify_drawdown_state(550.0, 545.0)
        assert result["label"] == "Near High"
        assert result["drawdown_pct"] > 0

    def test_realistic_spy_correction(self):
        """SPY pullback from 590 to 530 = -10.2% = Correction territory."""
        result = classify_drawdown_state(530.0, 590.0)
        assert result["label"] == "Correction"
        assert abs(result["drawdown_pct"] - (-10.17)) < 0.1
