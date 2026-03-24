"""Tests for the close-order preview/submit endpoints.

POST /api/trading/close-preview
POST /api/trading/close-submit
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes_trading import _build_close_tradier_payload
from app.trading.models import (
    CloseOrderLeg,
    CloseOrderPreviewRequest,
    CloseOrderSubmitRequest,
)


# ── Unit tests for payload builder ────────────────────────────────────

class TestBuildCloseTradierPayload:
    """Test the _build_close_tradier_payload helper."""

    def test_multileg_close_builds_payload(self):
        req = CloseOrderPreviewRequest(
            order_type="multileg",
            symbol="SPY",
            legs=[
                CloseOrderLeg(option_symbol="SPY260320P00500000", side="buy_to_close", quantity=1, strike=500, option_type="put"),
                CloseOrderLeg(option_symbol="SPY260320P00495000", side="sell_to_close", quantity=1, strike=495, option_type="put"),
            ],
            limit_price=0.45,
            price_effect="debit",
            mode="paper",
        )
        payload = _build_close_tradier_payload(req)
        assert payload["class"] == "multileg"
        assert payload["symbol"] == "SPY"
        assert payload["type"] == "debit"
        assert payload["price"] == "0.45"
        assert payload["side[0]"] == "buy_to_close"
        assert payload["side[1]"] == "sell_to_close"
        assert payload["option_symbol[0]"] == "SPY260320P00500000"
        assert payload["option_symbol[1]"] == "SPY260320P00495000"
        assert payload["quantity[0]"] == "1"
        assert payload["quantity[1]"] == "1"

    def test_equity_close_builds_payload(self):
        req = CloseOrderPreviewRequest(
            order_type="equity",
            symbol="AAPL",
            side="sell",
            quantity=100,
            limit_price=190.50,
            mode="paper",
        )
        payload = _build_close_tradier_payload(req)
        assert payload["class"] == "equity"
        assert payload["symbol"] == "AAPL"
        assert payload["side"] == "sell"
        assert payload["quantity"] == "100"
        assert payload["type"] == "limit"
        assert payload["price"] == "190.5"

    def test_equity_market_order_when_no_limit(self):
        req = CloseOrderPreviewRequest(
            order_type="equity",
            symbol="AAPL",
            side="sell",
            quantity=50,
            limit_price=None,
            mode="paper",
        )
        payload = _build_close_tradier_payload(req)
        assert payload["type"] == "market"
        assert "price" not in payload

    def test_multileg_rejects_single_leg(self):
        req = CloseOrderPreviewRequest(
            order_type="multileg",
            symbol="SPY",
            legs=[
                CloseOrderLeg(option_symbol="SPY260320P00500000", side="buy_to_close", quantity=1),
            ],
            limit_price=0.45,
            price_effect="debit",
        )
        with pytest.raises(ValueError, match="at least 2 legs"):
            _build_close_tradier_payload(req)

    def test_multileg_rejects_missing_limit(self):
        req = CloseOrderPreviewRequest(
            order_type="multileg",
            symbol="SPY",
            legs=[
                CloseOrderLeg(option_symbol="SPY260320P00500000", side="buy_to_close", quantity=1),
                CloseOrderLeg(option_symbol="SPY260320P00495000", side="sell_to_close", quantity=1),
            ],
            limit_price=None,
            price_effect="debit",
        )
        with pytest.raises(ValueError, match="limit_price"):
            _build_close_tradier_payload(req)

    def test_equity_rejects_zero_quantity(self):
        req = CloseOrderPreviewRequest(
            order_type="equity",
            symbol="AAPL",
            side="sell",
            quantity=0,
            limit_price=190.0,
        )
        with pytest.raises(ValueError, match="quantity"):
            _build_close_tradier_payload(req)

    def test_price_effect_defaults_debit(self):
        req = CloseOrderPreviewRequest(
            order_type="multileg",
            symbol="IWM",
            legs=[
                CloseOrderLeg(option_symbol="IWM260320P00200000", side="buy_to_close", quantity=2),
                CloseOrderLeg(option_symbol="IWM260320P00195000", side="sell_to_close", quantity=2),
            ],
            limit_price=0.30,
            price_effect=None,
        )
        payload = _build_close_tradier_payload(req)
        assert payload["type"] == "debit"

    def test_credit_price_effect(self):
        req = CloseOrderPreviewRequest(
            order_type="multileg",
            symbol="QQQ",
            legs=[
                CloseOrderLeg(option_symbol="QQQ260320C00500000", side="sell_to_close", quantity=1),
                CloseOrderLeg(option_symbol="QQQ260320C00505000", side="buy_to_close", quantity=1),
            ],
            limit_price=0.60,
            price_effect="credit",
        )
        payload = _build_close_tradier_payload(req)
        assert payload["type"] == "credit"


# ── Model validation tests ────────────────────────────────────────────

class TestCloseOrderModels:
    """Test Pydantic model validation."""

    def test_close_order_leg_valid(self):
        leg = CloseOrderLeg(option_symbol="SPY260320P00500000", side="buy_to_close", quantity=1)
        assert leg.side == "buy_to_close"

    def test_close_order_leg_uppercase_side(self):
        leg = CloseOrderLeg(option_symbol="SPY260320P00500000", side="SELL_TO_CLOSE", quantity=1)
        assert leg.side == "SELL_TO_CLOSE"

    def test_preview_request_defaults_paper(self):
        req = CloseOrderPreviewRequest(
            order_type="multileg",
            symbol="SPY",
            legs=[
                CloseOrderLeg(option_symbol="A" * 18, side="buy_to_close", quantity=1),
                CloseOrderLeg(option_symbol="B" * 18, side="sell_to_close", quantity=1),
            ],
            limit_price=0.50,
            price_effect="debit",
        )
        assert req.mode == "paper"

    def test_submit_request_valid(self):
        req = CloseOrderSubmitRequest(
            order_type="equity",
            symbol="AAPL",
            side="sell",
            quantity=100,
            limit_price=190.0,
            mode="paper",
        )
        assert req.mode == "paper"
        assert req.order_type == "equity"
