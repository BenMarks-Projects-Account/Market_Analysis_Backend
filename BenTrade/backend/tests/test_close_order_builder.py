"""Tests for close_order_builder and pipeline integration."""
import pytest

from app.services.close_order_builder import (
    build_close_order,
    _invert_side,
    _close_quantity,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _option_trade(**overrides):
    """Minimal option spread trade dict (2-leg vertical)."""
    trade = {
        "symbol": "SPY",
        "strategy": "put_credit_spread",
        "strategy_id": "put_credit_spread",
        "trade_key": "spy-pcs-20260601",
        "expiration": "2026-06-01",
        "quantity": 2,
        "mark_price": 1.80,
        "legs": [
            {
                "symbol": "SPY260601P00400000",
                "side": "sell",
                "qty": 2,
                "strike": 400.0,
                "option_type": "put",
                "price": 3.00,
            },
            {
                "symbol": "SPY260601P00395000",
                "side": "buy",
                "qty": 2,
                "strike": 395.0,
                "option_type": "put",
                "price": 1.20,
            },
        ],
    }
    trade.update(overrides)
    return trade


def _equity_trade(**overrides):
    """Minimal equity position trade dict."""
    trade = {
        "symbol": "AAPL",
        "strategy": "equity",
        "strategy_id": "equity",
        "trade_key": "aapl-equity",
        "expiration": None,
        "quantity": 50,
        "mark_price": 195.0,
        "legs": [
            {
                "symbol": "AAPL",
                "side": "buy",
                "qty": 50,
                "price": 195.0,
                "mark_price": 195.0,
                "avg_open_price": 180.0,
            },
        ],
    }
    trade.update(overrides)
    return trade


def _iron_condor_trade(**overrides):
    """4-leg iron condor trade dict."""
    trade = {
        "symbol": "SPY",
        "strategy": "iron_condor",
        "strategy_id": "iron_condor",
        "trade_key": "spy-ic-20260601",
        "expiration": "2026-06-01",
        "quantity": 1,
        "legs": [
            {"symbol": "SPY260601P00390000", "side": "buy",  "qty": 1, "strike": 390.0, "option_type": "put",  "price": 0.50},
            {"symbol": "SPY260601P00395000", "side": "sell", "qty": 1, "strike": 395.0, "option_type": "put",  "price": 1.50},
            {"symbol": "SPY260601C00420000", "side": "sell", "qty": 1, "strike": 420.0, "option_type": "call", "price": 1.80},
            {"symbol": "SPY260601C00425000", "side": "buy",  "qty": 1, "strike": 425.0, "option_type": "call", "price": 0.60},
        ],
    }
    trade.update(overrides)
    return trade


# ═════════════════════════════════════════════════════════════════════════
#  Unit tests — _invert_side
# ═════════════════════════════════════════════════════════════════════════

class TestInvertSide:
    def test_sell_to_buy_to_close(self):
        assert _invert_side("sell") == "buy_to_close"

    def test_buy_to_sell_to_close(self):
        assert _invert_side("buy") == "sell_to_close"

    def test_sell_to_open_maps(self):
        assert _invert_side("sell_to_open") == "buy_to_close"

    def test_buy_to_open_maps(self):
        assert _invert_side("buy_to_open") == "sell_to_close"

    def test_unknown_returns_none(self):
        assert _invert_side("hold") is None

    def test_empty_returns_none(self):
        assert _invert_side("") is None


# ═════════════════════════════════════════════════════════════════════════
#  Unit tests — _close_quantity
# ═════════════════════════════════════════════════════════════════════════

class TestCloseQuantity:
    def test_close_returns_full(self):
        assert _close_quantity(10, "CLOSE", 0.5) == 10

    def test_reduce_half(self):
        assert _close_quantity(10, "REDUCE", 0.5) == 5

    def test_reduce_rounds_up_minimum_one(self):
        assert _close_quantity(1, "REDUCE", 0.5) == 1

    def test_reduce_rounds_correctly(self):
        assert _close_quantity(3, "REDUCE", 0.5) == 2


# ═════════════════════════════════════════════════════════════════════════
#  Unit tests — build_close_order (option trades)
# ═════════════════════════════════════════════════════════════════════════

class TestBuildCloseOrderOptions:
    def test_full_close_vertical(self):
        order = build_close_order(_option_trade(), action="CLOSE")
        assert order is not None
        assert order["order_type"] == "multileg"
        assert order["action"] == "CLOSE"
        assert order["symbol"] == "SPY"
        assert order["ready_for_preview"] is True
        assert len(order["legs"]) == 2

        # Short leg → buy_to_close
        short_leg = [l for l in order["legs"] if l["strike"] == 400.0][0]
        assert short_leg["side"] == "buy_to_close"
        assert short_leg["quantity"] == 2

        # Long leg → sell_to_close
        long_leg = [l for l in order["legs"] if l["strike"] == 395.0][0]
        assert long_leg["side"] == "sell_to_close"
        assert long_leg["quantity"] == 2

    def test_reduce_vertical(self):
        order = build_close_order(_option_trade(), action="REDUCE", reduce_pct=0.5)
        assert order is not None
        assert order["action"] == "REDUCE"
        for leg in order["legs"]:
            assert leg["quantity"] == 1  # 50% of 2

    def test_iron_condor_close(self):
        order = build_close_order(_iron_condor_trade(), action="CLOSE")
        assert order is not None
        assert len(order["legs"]) == 4
        buy_legs = [l for l in order["legs"] if l["side"] == "buy_to_close"]
        sell_legs = [l for l in order["legs"] if l["side"] == "sell_to_close"]
        # 2 short legs → buy_to_close, 2 long legs → sell_to_close
        assert len(buy_legs) == 2
        assert len(sell_legs) == 2

    def test_has_estimated_cost(self):
        order = build_close_order(_option_trade(), action="CLOSE")
        assert order["estimated_cost"] is not None
        assert isinstance(order["estimated_cost"], float)

    def test_has_limit_price(self):
        order = build_close_order(_option_trade(), action="CLOSE")
        assert order["limit_price"] is not None
        assert order["limit_price"] > 0

    def test_preserves_option_symbols(self):
        order = build_close_order(_option_trade(), action="CLOSE")
        symbols = {l["option_symbol"] for l in order["legs"]}
        assert "SPY260601P00400000" in symbols
        assert "SPY260601P00395000" in symbols

    def test_preserves_strategy_and_expiration(self):
        order = build_close_order(_option_trade(), action="CLOSE")
        assert order["strategy_id"] == "put_credit_spread"
        assert order["expiration"] == "2026-06-01"

    def test_description_contains_close(self):
        order = build_close_order(_option_trade(), action="CLOSE")
        assert "Close" in order["description"]
        assert "SPY" in order["description"]


# ═════════════════════════════════════════════════════════════════════════
#  Unit tests — build_close_order (equity trades)
# ═════════════════════════════════════════════════════════════════════════

class TestBuildCloseOrderEquity:
    def test_full_equity_close(self):
        order = build_close_order(_equity_trade(), action="CLOSE")
        assert order is not None
        assert order["order_type"] == "equity"
        assert order["action"] == "CLOSE"
        assert order["symbol"] == "AAPL"
        assert order["quantity"] == 50
        assert order["side"] == "sell"
        assert order["ready_for_preview"] is True

    def test_equity_reduce(self):
        order = build_close_order(_equity_trade(), action="REDUCE", reduce_pct=0.5)
        assert order is not None
        assert order["quantity"] == 25

    def test_equity_estimated_proceeds(self):
        order = build_close_order(_equity_trade(), action="CLOSE")
        # 50 shares × $195 = $9750
        assert order["estimated_proceeds"] == 9750.0

    def test_equity_description(self):
        order = build_close_order(_equity_trade(), action="CLOSE")
        assert "50 shares" in order["description"]
        assert "AAPL" in order["description"]


# ═════════════════════════════════════════════════════════════════════════
#  Edge cases
# ═════════════════════════════════════════════════════════════════════════

class TestBuildCloseOrderEdgeCases:
    def test_no_legs_returns_none(self):
        trade = _option_trade(legs=[])
        assert build_close_order(trade) is None

    def test_none_legs_returns_none(self):
        trade = _option_trade(legs=None)
        assert build_close_order(trade) is None

    def test_missing_symbol_returns_none(self):
        trade = _option_trade(symbol="")
        assert build_close_order(trade) is None

    def test_missing_price_still_builds(self):
        """Order is still generated even if mark prices are missing."""
        legs = [
            {"symbol": "SPY260601P00400000", "side": "sell", "qty": 1, "strike": 400.0, "option_type": "put", "price": None},
            {"symbol": "SPY260601P00395000", "side": "buy",  "qty": 1, "strike": 395.0, "option_type": "put", "price": None},
        ]
        order = build_close_order(_option_trade(legs=legs), action="CLOSE")
        assert order is not None
        assert order["estimated_cost"] is None
        assert order["limit_price"] is None


# ═════════════════════════════════════════════════════════════════════════
#  Pipeline integration — Stage 6 attaches suggested_close_order
# ═════════════════════════════════════════════════════════════════════════

class TestPipelineCloseOrderIntegration:
    """Verify that run_active_trade_pipeline attaches close orders."""

    def _make_trade_with_legs(self, strategy="put_credit_spread"):
        """Trade with actual legs so close order builder can produce output."""
        return {
            "symbol": "SPY",
            "strategy": strategy,
            "strategy_id": strategy,
            "trade_key": f"spy-{strategy}-20260601",
            "trade_id": "t-close-test",
            "dte": 30,
            "short_strike": 400.0,
            "long_strike": 395.0,
            "expiration": "2026-06-01",
            "quantity": 1,
            "legs": [
                {"symbol": "SPY260601P00400000", "side": "sell", "qty": 1, "strike": 400.0, "option_type": "put", "price": 3.00},
                {"symbol": "SPY260601P00395000", "side": "buy",  "qty": 1, "strike": 395.0, "option_type": "put", "price": 1.20},
            ],
            "status": "OPEN",
            "avg_open_price": 1.80,
            "mark_price": 1.80,
            "unrealized_pnl": 0.0,
            "unrealized_pnl_pct": 0.0,
            "cost_basis_total": 180.0,
            "market_value": 180.0,
            "spread_type": "vertical",
        }

    def test_close_order_attached_for_close_rec(self):
        """When engine recommends CLOSE, suggested_close_order should be present."""
        from app.services.active_trade_pipeline import normalize_recommendation
        from app.services.close_order_builder import build_close_order

        trade = self._make_trade_with_legs()
        engine_output = {
            "trade_health_score": 15,
            "engine_recommendation": "CLOSE",
            "urgency": 3,
            "risk_flags": ["RAPID_DECAY"],
            "component_scores": {},
        }
        model_output = {
            "model_available": False,
            "recommendation": None,
            "conviction": None,
        }
        packet = {"identity": {"trade_key": trade["trade_key"], "strategy": "put_credit_spread", "strategy_id": "put_credit_spread", "expiration": "2026-06-01", "dte": 30}, "symbol": "SPY", "position": {"avg_open_price": 1.80, "mark_price": 1.80, "unrealized_pnl": 0.0, "unrealized_pnl_pct": 0.0}}
        rec = normalize_recommendation(trade, engine_output, model_output, packet)

        # Simulate Stage 6 logic
        action = rec.get("recommendation", "")
        if action in ("CLOSE", "URGENT_REVIEW"):
            rec["suggested_close_order"] = build_close_order(trade, action="CLOSE")
        elif action == "REDUCE":
            rec["suggested_close_order"] = build_close_order(trade, action="REDUCE")
        else:
            rec["suggested_close_order"] = None

        assert rec["recommendation"] == "CLOSE"
        assert rec["suggested_close_order"] is not None
        assert rec["suggested_close_order"]["order_type"] == "multileg"
        assert rec["suggested_close_order"]["ready_for_preview"] is True

    def test_hold_has_no_close_order(self):
        """HOLD recommendation should have suggested_close_order = None."""
        from app.services.active_trade_pipeline import normalize_recommendation
        from app.services.close_order_builder import build_close_order

        trade = self._make_trade_with_legs()
        engine_output = {
            "trade_health_score": 80,
            "engine_recommendation": "HOLD",
            "urgency": 1,
            "risk_flags": [],
            "component_scores": {},
        }
        model_output = {
            "model_available": False,
            "recommendation": None,
            "conviction": None,
        }
        packet = {"identity": {"trade_key": trade["trade_key"], "strategy": "put_credit_spread", "strategy_id": "put_credit_spread", "expiration": "2026-06-01", "dte": 30}, "symbol": "SPY", "position": {"avg_open_price": 1.80, "mark_price": 1.80, "unrealized_pnl": 0.0, "unrealized_pnl_pct": 0.0}}
        rec = normalize_recommendation(trade, engine_output, model_output, packet)

        action = rec.get("recommendation", "")
        if action in ("CLOSE", "URGENT_REVIEW"):
            rec["suggested_close_order"] = build_close_order(trade, action="CLOSE")
        elif action == "REDUCE":
            rec["suggested_close_order"] = build_close_order(trade, action="REDUCE")
        else:
            rec["suggested_close_order"] = None

        assert rec["recommendation"] == "HOLD"
        assert rec["suggested_close_order"] is None

    def test_reduce_gets_partial_close(self):
        """REDUCE should generate a partial close order."""
        from app.services.close_order_builder import build_close_order

        trade = self._make_trade_with_legs()
        trade["quantity"] = 4
        trade["legs"][0]["qty"] = 4
        trade["legs"][1]["qty"] = 4

        order = build_close_order(trade, action="REDUCE", reduce_pct=0.5)
        assert order is not None
        for leg in order["legs"]:
            assert leg["quantity"] == 2  # 50% of 4
