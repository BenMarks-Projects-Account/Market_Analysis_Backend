"""
Tests for Active Trade card math — per-share vs total value consistency.

Verifies that _normalize_positions correctly derives:
  - avg_open_price (per-share) from cost_basis (total) / quantity
  - market_value (total) from mark_price (per-share) * quantity
  - unrealized_pnl = (current_price - avg_entry) * quantity
  - unrealized_pnl_pct = unrealized_pnl / cost_basis_total

Fixture based on real broker data:
  WMT  Qty 10  cost_basis=1278.30  current_price=127.73
  Expected: avg_entry=127.83  market_value=1277.30  pnl=-1.00  pnl_pct≈-0.0782%
"""

from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── ensure importable ────────────────────────────────────────────────
_backend = Path(__file__).resolve().parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from app.api.routes_active_trades import _normalize_positions


# ── Fixtures ─────────────────────────────────────────────────────────

# Tradier position data (as returned by the API)
WMT_POSITION = {
    "symbol": "WMT",
    "quantity": 10,
    "cost_basis": 1278.30,          # TOTAL cost, not per-share
    # Tradier does NOT provide average_open_price for equities
    "last": 127.73,                 # per-share last price
}

QUOTE_MAP = {}  # no quote enrichment needed; last is on the position


class TestNormalizePositionsCardMath:
    """Verify per-share / total consistency in _normalize_positions output."""

    def _norm(self, position: dict, quote_map: dict | None = None) -> dict:
        results = _normalize_positions([position], quote_map or {})
        assert len(results) == 1
        return results[0]

    # ── Core fixture ─────────────────────────────────────────────────

    def test_wmt_avg_entry_price_is_per_share(self):
        """avg_open_price = cost_basis / qty = 1278.30 / 10 = 127.83"""
        out = self._norm(WMT_POSITION)
        assert out["avg_open_price"] == pytest.approx(127.83, abs=0.01)

    def test_wmt_mark_price_is_per_share(self):
        """mark_price should be the per-share current price (127.73)."""
        out = self._norm(WMT_POSITION)
        assert out["mark_price"] == pytest.approx(127.73)

    def test_wmt_cost_basis_total(self):
        """cost_basis_total = total cost from Tradier (1278.30)."""
        out = self._norm(WMT_POSITION)
        assert out["cost_basis_total"] == pytest.approx(1278.30)

    def test_wmt_market_value(self):
        """market_value = mark_price * qty = 127.73 * 10 = 1277.30"""
        out = self._norm(WMT_POSITION)
        assert out["market_value"] == pytest.approx(1277.30, abs=0.01)

    def test_wmt_unrealized_pnl(self):
        """unrealized_pnl = (current - avg_entry) * qty = (127.73 - 127.83) * 10 = -1.00"""
        out = self._norm(WMT_POSITION)
        assert out["unrealized_pnl"] == pytest.approx(-1.00, abs=0.05)

    def test_wmt_unrealized_pnl_pct(self):
        """unrealized_pnl_pct = pnl / cost_basis_total = -1.00 / 1278.30 ≈ -0.000782"""
        out = self._norm(WMT_POSITION)
        expected_pct = -1.00 / 1278.30  # ≈ -0.000782
        assert out["unrealized_pnl_pct"] == pytest.approx(expected_pct, abs=1e-4)

    # ── Edge cases ───────────────────────────────────────────────────

    def test_single_share(self):
        """With qty=1, avg_open_price should equal cost_basis."""
        pos = {"symbol": "AAPL", "quantity": 1, "cost_basis": 189.50, "last": 191.20}
        out = self._norm(pos)
        assert out["avg_open_price"] == pytest.approx(189.50)
        assert out["cost_basis_total"] == pytest.approx(189.50)
        assert out["market_value"] == pytest.approx(191.20)
        assert out["unrealized_pnl"] == pytest.approx(1.70, abs=0.01)

    def test_no_cost_basis_uses_avg_open_price(self):
        """If Tradier provides average_open_price (no cost_basis), use it directly."""
        pos = {"symbol": "SPY", "quantity": 5, "average_open_price": 450.25, "last": 452.10}
        out = self._norm(pos)
        assert out["avg_open_price"] == pytest.approx(450.25)
        assert out["cost_basis_total"] == pytest.approx(450.25 * 5)
        assert out["market_value"] == pytest.approx(452.10 * 5)

    def test_average_price_field(self):
        """Tradier equity per-share field 'average_price' should be used."""
        pos = {"symbol": "MSFT", "quantity": 3, "average_price": 400.50, "last": 405.00}
        out = self._norm(pos)
        assert out["avg_open_price"] == pytest.approx(400.50)
        assert out["cost_basis_total"] == pytest.approx(400.50 * 3)

    def test_avg_cost_field(self):
        """Common broker alias 'avg_cost' should be used as per-share price."""
        pos = {"symbol": "GOOG", "quantity": 2, "avg_cost": 175.00, "last": 180.00}
        out = self._norm(pos)
        assert out["avg_open_price"] == pytest.approx(175.00)
        assert out["unrealized_pnl"] == pytest.approx(10.00, abs=0.01)

    def test_per_share_preferred_over_cost_basis(self):
        """When both average_open_price and cost_basis are present, prefer per-share."""
        pos = {"symbol": "META", "quantity": 10, "average_open_price": 50.00, "cost_basis": 500.00, "last": 55.00}
        out = self._norm(pos)
        # Should use average_open_price directly, not cost_basis / qty
        assert out["avg_open_price"] == pytest.approx(50.00)
        assert out["cost_basis_total"] == pytest.approx(500.00)

    def test_zero_quantity_no_crash(self):
        """quantity=0 should not produce division-by-zero."""
        pos = {"symbol": "BAD", "quantity": 0, "cost_basis": 500.00, "last": 50.00}
        out = self._norm(pos)
        # avg_open_price should be None (can't divide by 0)
        assert out["avg_open_price"] is None

    def test_missing_cost_basis_and_price(self):
        """If no pricing data at all, fields should be None, not fabricated."""
        pos = {"symbol": "EMPTY", "quantity": 10}
        out = self._norm(pos)
        assert out["avg_open_price"] is None
        assert out["mark_price"] is None
        assert out["unrealized_pnl"] is None
        assert out["unrealized_pnl_pct"] is None

    def test_negative_quantity_short_position(self):
        """Short position: quantity is negative. avg_open_price still per-share."""
        pos = {"symbol": "TSLA", "quantity": -5, "cost_basis": 1250.00, "last": 248.00}
        out = self._norm(pos)
        # avg_open_price = 1250 / abs(-5) = 250.00
        assert out["avg_open_price"] == pytest.approx(250.00)
        # unrealized = (248 - 250) * (-5) = +10.00  (short profits when price drops)
        assert out["unrealized_pnl"] == pytest.approx(10.00, abs=0.01)

    def test_quote_map_fallback_for_mark(self):
        """If position has no price, fall back to quote_map."""
        pos = {"symbol": "QQQ", "quantity": 3, "cost_basis": 1500.00}
        quotes = {"QQQ": {"last": 502.00}}
        out = self._norm(pos, quotes)
        assert out["mark_price"] == pytest.approx(502.00)
        assert out["market_value"] == pytest.approx(502.00 * 3)

    # ── NO double-multiply verification ──────────────────────────────

    def test_no_double_multiply(self):
        """
        Regression: old code used total cost_basis as avg_open_price,
        then computed basis = avg_open_price * qty (multiplying qty twice).
        Verify the math is now clean.
        """
        # 50 shares @ $20/share = $1000 total cost basis
        pos = {"symbol": "TEST", "quantity": 50, "cost_basis": 1000.00, "last": 21.00}
        out = self._norm(pos)
        assert out["avg_open_price"] == pytest.approx(20.00)
        assert out["cost_basis_total"] == pytest.approx(1000.00)
        assert out["market_value"] == pytest.approx(1050.00)
        # P&L = (21 - 20) * 50 = 50.00
        assert out["unrealized_pnl"] == pytest.approx(50.00, abs=0.01)
        # P&L% = 50 / 1000 = 0.05 (5%)
        assert out["unrealized_pnl_pct"] == pytest.approx(0.05, abs=1e-4)
