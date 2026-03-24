"""Unit tests for _identify_strategy – multi-leg option strategy detection."""

from __future__ import annotations

import pytest

from app.api.routes_active_trades import _identify_strategy


def _leg(option_type: str, strike: float, quantity: int, **extra) -> dict:
    """Helper to build a minimal position dict for testing."""
    d = {
        "option_type": option_type,
        "strike": strike,
        "quantity": quantity,
        "symbol": f"SPY250718{option_type[0].upper()}{int(strike * 1000):08d}",
        "underlying": "SPY",
        "expiration": "2025-07-18",
        "position_key": f"{option_type}_{strike}_{quantity}",
        "avg_open_price": 1.50,
        "mark_price": 1.20,
        "unrealized_pnl": 30.0,
    }
    d.update(extra)
    return d


# ── Iron Condor ─────────────────────────────────────────────────────────


class TestIronCondor:
    def test_basic_iron_condor(self):
        legs = [
            _leg("put", 540, 1),     # long put (lower)
            _leg("put", 550, -1),    # short put
            _leg("call", 580, -1),   # short call
            _leg("call", 590, 1),    # long call (higher)
        ]
        result = _identify_strategy(legs)
        assert len(result) == 1
        assert result[0]["strategy"] == "iron_condor"
        assert len(result[0]["legs"]) == 4

    def test_iron_condor_quantity_mismatch_falls_through(self):
        legs = [
            _leg("put", 540, 1),
            _leg("put", 550, -2),    # different qty
            _leg("call", 580, -1),
            _leg("call", 590, 1),
        ]
        result = _identify_strategy(legs)
        strategies = [r["strategy"] for r in result]
        assert "iron_condor" not in strategies

    def test_iron_condor_multi_lot(self):
        legs = [
            _leg("put", 540, 3),
            _leg("put", 550, -3),
            _leg("call", 580, -3),
            _leg("call", 590, 3),
        ]
        result = _identify_strategy(legs)
        assert len(result) == 1
        assert result[0]["strategy"] == "iron_condor"

    def test_iron_condor_legs_sorted_by_strike(self):
        legs = [
            _leg("call", 590, 1),
            _leg("put", 540, 1),
            _leg("call", 580, -1),
            _leg("put", 550, -1),
        ]
        result = _identify_strategy(legs)
        strikes = [l["strike"] for l in result[0]["legs"]]
        assert strikes == [540, 550, 580, 590]


# ── Butterfly ───────────────────────────────────────────────────────────


class TestButterfly:
    def test_basic_call_butterfly(self):
        legs = [
            _leg("call", 570, 1),    # long lower wing
            _leg("call", 580, -2),   # short center (2x)
            _leg("call", 590, 1),    # long upper wing
        ]
        result = _identify_strategy(legs)
        assert len(result) == 1
        assert result[0]["strategy"] == "butterfly_debit"
        assert len(result[0]["legs"]) == 3

    def test_basic_put_butterfly(self):
        legs = [
            _leg("put", 540, 1),
            _leg("put", 550, -2),
            _leg("put", 560, 1),
        ]
        result = _identify_strategy(legs)
        assert len(result) == 1
        assert result[0]["strategy"] == "butterfly_debit"

    def test_asymmetric_wings_not_butterfly(self):
        legs = [
            _leg("call", 570, 1),
            _leg("call", 580, -2),
            _leg("call", 600, 1),   # asymmetric: 10 vs 20
        ]
        result = _identify_strategy(legs)
        strategies = [r["strategy"] for r in result]
        assert "butterfly_debit" not in strategies

    def test_butterfly_wrong_qty_ratio_falls_through(self):
        legs = [
            _leg("call", 570, 1),
            _leg("call", 580, -3),   # not 2x
            _leg("call", 590, 1),
        ]
        result = _identify_strategy(legs)
        strategies = [r["strategy"] for r in result]
        assert "butterfly_debit" not in strategies


# ── Vertical Spread ─────────────────────────────────────────────────────


class TestVerticalSpread:
    def test_put_credit_spread(self):
        legs = [
            _leg("put", 540, 1),     # long lower
            _leg("put", 550, -1),    # short higher
        ]
        result = _identify_strategy(legs)
        assert len(result) == 1
        assert result[0]["strategy"] == "put_credit_spread"

    def test_call_credit_spread(self):
        legs = [
            _leg("call", 580, -1),   # short lower
            _leg("call", 590, 1),    # long higher
        ]
        result = _identify_strategy(legs)
        assert len(result) == 1
        assert result[0]["strategy"] == "call_credit_spread"

    def test_put_debit_spread(self):
        legs = [
            _leg("put", 540, -1),    # short lower
            _leg("put", 550, 1),     # long higher
        ]
        result = _identify_strategy(legs)
        assert len(result) == 1
        assert result[0]["strategy"] == "put_debit"

    def test_call_debit_spread(self):
        legs = [
            _leg("call", 580, 1),    # long lower
            _leg("call", 590, -1),   # short higher
        ]
        result = _identify_strategy(legs)
        assert len(result) == 1
        assert result[0]["strategy"] == "call_debit"


# ── Singles ─────────────────────────────────────────────────────────────


class TestSingles:
    def test_single_long_call(self):
        legs = [_leg("call", 580, 1)]
        result = _identify_strategy(legs)
        assert len(result) == 1
        assert result[0]["strategy"] == "single"

    def test_single_short_put(self):
        legs = [_leg("put", 540, -1)]
        result = _identify_strategy(legs)
        assert len(result) == 1
        assert result[0]["strategy"] == "single"


# ── Priority / Mixed ───────────────────────────────────────────────────


class TestPriority:
    def test_ic_detected_before_verticals(self):
        """4 legs that form an IC should not be split into 2 verticals."""
        legs = [
            _leg("put", 540, 1),
            _leg("put", 550, -1),
            _leg("call", 580, -1),
            _leg("call", 590, 1),
        ]
        result = _identify_strategy(legs)
        assert len(result) == 1
        assert result[0]["strategy"] == "iron_condor"

    def test_ic_plus_extra_single(self):
        legs = [
            _leg("put", 540, 1),
            _leg("put", 550, -1),
            _leg("call", 580, -1),
            _leg("call", 590, 1),
            _leg("call", 600, -2),   # extra unmatched
        ]
        result = _identify_strategy(legs)
        strategies = sorted(r["strategy"] for r in result)
        assert strategies == ["iron_condor", "single"]

    def test_vertical_plus_single(self):
        legs = [
            _leg("put", 540, 1),
            _leg("put", 550, -1),
            _leg("call", 600, -3),   # unmatched
        ]
        result = _identify_strategy(legs)
        strategies = sorted(r["strategy"] for r in result)
        assert strategies == ["put_credit_spread", "single"]

    def test_empty_input(self):
        assert _identify_strategy([]) == []
