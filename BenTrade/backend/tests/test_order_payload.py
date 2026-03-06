"""Unit tests for multi-leg order payload correctness.

Tests verify that:
  - TradierBroker.build_payload() produces correct Tradier form-encoded fields
  - tradier_order_builder.build_multileg_order() handles 2-leg and 4-leg orders
  - Short put credit spread, short call credit spread, iron condor payloads
    are complete and correctly structured
"""

import unittest
from datetime import datetime, timezone

from app.trading.models import OrderLeg, OrderTicket, ProfitLossEstimate
from app.trading.tradier_order_builder import build_multileg_order


def _make_ticket(
    strategy="put_credit",
    underlying="SPY",
    price_effect="CREDIT",
    legs=None,
    limit_price=0.85,
    quantity=1,
    mode="paper",
) -> OrderTicket:
    """Build a minimal OrderTicket for testing."""
    if legs is None:
        legs = [
            OrderLeg(
                option_type="put",
                expiration="2026-03-20",
                strike=560.0,
                side="SELL_TO_OPEN",
                quantity=quantity,
                occ_symbol="SPY260320P00560000",
                bid=1.10,
                ask=1.20,
                mid=1.15,
            ),
            OrderLeg(
                option_type="put",
                expiration="2026-03-20",
                strike=555.0,
                side="BUY_TO_OPEN",
                quantity=quantity,
                occ_symbol="SPY260320P00555000",
                bid=0.25,
                ask=0.35,
                mid=0.30,
            ),
        ]
    now = datetime.now(timezone.utc)
    return OrderTicket(
        id="test-ticket-001",
        mode=mode,
        strategy=strategy,
        underlying=underlying,
        expiration="2026-03-20",
        quantity=quantity,
        limit_price=limit_price,
        price_effect=price_effect,
        time_in_force="DAY",
        legs=legs,
        estimated_max_profit=ProfitLossEstimate(per_spread=0.85, total=85.0),
        estimated_max_loss=ProfitLossEstimate(per_spread=4.15, total=415.0),
        created_at=now,
        asof_quote_ts=now,
        asof_chain_ts=now,
    )


class TestBrokerBuildPayload(unittest.TestCase):
    """Test TradierBroker.build_payload() via OrderTicket."""

    def test_short_put_credit_spread(self):
        """2-leg short put credit spread produces correct Tradier payload."""
        # Arrange: Sell 560P, Buy 555P for $0.85 credit
        from app.trading.tradier_broker import TradierBroker
        from app.config import Settings
        import httpx

        settings = Settings()
        broker = TradierBroker(settings=settings, http_client=httpx.AsyncClient(), dry_run=True)
        ticket = _make_ticket(strategy="put_credit", price_effect="CREDIT", limit_price=0.85)

        # Act
        payload = broker.build_payload(ticket)

        # Assert: required Tradier multileg fields
        self.assertEqual(payload["class"], "multileg")
        self.assertEqual(payload["symbol"], "SPY")
        self.assertEqual(payload["type"], "credit")  # Tradier multileg uses credit/debit, not limit
        self.assertEqual(payload["duration"], "day")
        self.assertEqual(payload["price"], "0.85")
        self.assertEqual(payload["tag"], "test-ticket-001")

        # Bracket-indexed leg fields (form-encoded format)
        self.assertEqual(payload["option_symbol[0]"], "SPY260320P00560000")
        self.assertEqual(payload["side[0]"], "sell_to_open")
        self.assertEqual(payload["quantity[0]"], "1")

        self.assertEqual(payload["option_symbol[1]"], "SPY260320P00555000")
        self.assertEqual(payload["side[1]"], "buy_to_open")
        self.assertEqual(payload["quantity[1]"], "1")

        # Must NOT have JSON legs array
        self.assertNotIn("legs", payload)
        self.assertNotIn("option_symbol[2]", payload)

    def test_short_call_credit_spread(self):
        """2-leg short call credit spread produces correct Tradier payload."""
        from app.trading.tradier_broker import TradierBroker
        from app.config import Settings
        import httpx

        settings = Settings()
        broker = TradierBroker(settings=settings, http_client=httpx.AsyncClient(), dry_run=True)

        legs = [
            OrderLeg(
                option_type="call", expiration="2026-03-20", strike=590.0,
                side="SELL_TO_OPEN", quantity=1,
                occ_symbol="SPY260320C00590000", bid=1.50, ask=1.65, mid=1.575,
            ),
            OrderLeg(
                option_type="call", expiration="2026-03-20", strike=595.0,
                side="BUY_TO_OPEN", quantity=1,
                occ_symbol="SPY260320C00595000", bid=0.40, ask=0.55, mid=0.475,
            ),
        ]
        ticket = _make_ticket(
            strategy="call_credit", price_effect="CREDIT",
            limit_price=1.10, legs=legs,
        )

        payload = broker.build_payload(ticket)

        self.assertEqual(payload["class"], "multileg")
        self.assertEqual(payload["symbol"], "SPY")
        self.assertEqual(payload["price"], "1.1")
        self.assertEqual(payload["option_symbol[0]"], "SPY260320C00590000")
        self.assertEqual(payload["side[0]"], "sell_to_open")
        self.assertEqual(payload["option_symbol[1]"], "SPY260320C00595000")
        self.assertEqual(payload["side[1]"], "buy_to_open")

    def test_iron_condor_4_legs(self):
        """4-leg iron condor produces correct Tradier payload."""
        from app.trading.tradier_broker import TradierBroker
        from app.config import Settings
        import httpx

        settings = Settings()
        broker = TradierBroker(settings=settings, http_client=httpx.AsyncClient(), dry_run=True)

        legs = [
            # Put credit side: sell 560P, buy 555P
            OrderLeg(
                option_type="put", expiration="2026-03-20", strike=560.0,
                side="SELL_TO_OPEN", quantity=1,
                occ_symbol="SPY260320P00560000", bid=1.10, ask=1.20, mid=1.15,
            ),
            OrderLeg(
                option_type="put", expiration="2026-03-20", strike=555.0,
                side="BUY_TO_OPEN", quantity=1,
                occ_symbol="SPY260320P00555000", bid=0.25, ask=0.35, mid=0.30,
            ),
            # Call credit side: sell 590C, buy 595C
            OrderLeg(
                option_type="call", expiration="2026-03-20", strike=590.0,
                side="SELL_TO_OPEN", quantity=1,
                occ_symbol="SPY260320C00590000", bid=1.50, ask=1.65, mid=1.575,
            ),
            OrderLeg(
                option_type="call", expiration="2026-03-20", strike=595.0,
                side="BUY_TO_OPEN", quantity=1,
                occ_symbol="SPY260320C00595000", bid=0.40, ask=0.55, mid=0.475,
            ),
        ]
        ticket = _make_ticket(
            strategy="put_credit",  # iron condor uses same structure
            price_effect="CREDIT",
            limit_price=1.95,
            legs=legs,
        )

        payload = broker.build_payload(ticket)

        # All 4 legs present
        self.assertEqual(payload["class"], "multileg")
        self.assertEqual(payload["symbol"], "SPY")
        self.assertEqual(payload["price"], "1.95")

        # Verify bracket-indexed leg fields
        self.assertEqual(payload["option_symbol[0]"], "SPY260320P00560000")
        self.assertEqual(payload["side[0]"], "sell_to_open")
        self.assertEqual(payload["option_symbol[2]"], "SPY260320C00590000")
        self.assertEqual(payload["side[2]"], "sell_to_open")
        self.assertEqual(payload["option_symbol[3]"], "SPY260320C00595000")
        self.assertEqual(payload["side[3]"], "buy_to_open")

        # No JSON legs array
        self.assertNotIn("legs", payload)


class TestOrderBuilderMultileg(unittest.TestCase):
    """Test tradier_order_builder.build_multileg_order()."""

    def test_credit_spread_from_trade_dict(self):
        """build_multileg_order produces correct payload from a trade dict."""
        trade = {
            "underlying": "QQQ",
            "strategy_id": "put_credit_spread",
            "expiration": "2026-04-17",
            "net_credit": 0.92,
            "legs": [
                {"occ_symbol": "QQQ260417P00480000", "side": "sell_to_open", "qty": 1, "strike": 480, "right": "put"},
                {"occ_symbol": "QQQ260417P00475000", "side": "buy_to_open", "qty": 1, "strike": 475, "right": "put"},
            ],
        }

        result = build_multileg_order(trade, account_mode="paper", quantity=2)

        payload = result["payload"]
        self.assertEqual(payload["class"], "multileg")
        self.assertEqual(payload["symbol"], "QQQ")
        self.assertEqual(payload["type"], "credit")  # Tradier multileg uses credit/debit, not limit
        self.assertEqual(payload["price"], "0.92")
        self.assertEqual(payload["duration"], "day")

        # Bracket-indexed leg fields
        self.assertEqual(payload["quantity[0]"], "2")
        self.assertEqual(payload["quantity[1]"], "2")

        self.assertEqual(payload["option_symbol[0]"], "QQQ260417P00480000")
        self.assertEqual(payload["side[0]"], "sell_to_open")
        self.assertEqual(payload["option_symbol[1]"], "QQQ260417P00475000")
        self.assertEqual(payload["side[1]"], "buy_to_open")

        # No JSON legs array
        self.assertNotIn("legs", payload)

    def test_missing_occ_raises_error(self):
        """build_multileg_order raises ValueError if any leg is missing OCC symbol."""
        trade = {
            "underlying": "SPY",
            "strategy_id": "put_credit_spread",
            "legs": [
                {"occ_symbol": "SPY260320P00560000", "side": "sell", "qty": 1},
                {"occ_symbol": "", "side": "buy", "qty": 1},  # missing OCC
            ],
        }

        with self.assertRaises(ValueError) as ctx:
            build_multileg_order(trade)
        self.assertIn("OCC symbol is missing", str(ctx.exception))

    def test_missing_legs_raises_error(self):
        """build_multileg_order raises ValueError if legs list is empty."""
        trade = {"underlying": "SPY", "strategy_id": "put_credit_spread", "legs": []}

        with self.assertRaises(ValueError) as ctx:
            build_multileg_order(trade)
        self.assertIn("no legs", str(ctx.exception))

    def test_iron_condor_4_legs_from_dict(self):
        """build_multileg_order handles 4-leg iron condor."""
        trade = {
            "underlying": "IWM",
            "strategy_id": "iron_condor",
            "price_effect": "CREDIT",
            "net_credit": 2.10,
            "legs": [
                {"occ_symbol": "IWM260320P00200000", "side": "sell_to_open", "qty": 1, "strike": 200, "right": "put"},
                {"occ_symbol": "IWM260320P00195000", "side": "buy_to_open", "qty": 1, "strike": 195, "right": "put"},
                {"occ_symbol": "IWM260320C00220000", "side": "sell_to_open", "qty": 1, "strike": 220, "right": "call"},
                {"occ_symbol": "IWM260320C00225000", "side": "buy_to_open", "qty": 1, "strike": 225, "right": "call"},
            ],
        }

        result = build_multileg_order(trade)
        payload = result["payload"]

        self.assertEqual(payload["class"], "multileg")
        self.assertEqual(payload["symbol"], "IWM")
        self.assertEqual(payload["price"], "2.1")
        self.assertEqual(len(result["legs_used"]), 4)

        # All 4 legs present as bracket-indexed fields
        self.assertEqual(payload["option_symbol[0]"], "IWM260320P00200000")
        self.assertEqual(payload["option_symbol[3]"], "IWM260320C00225000")

        # No JSON legs array
        self.assertNotIn("legs", payload)


class TestDryRunPayload(unittest.TestCase):
    """Test that dry_run=True logs the payload without sending."""

    def test_dry_run_returns_dry_run_status(self):
        """TradierBroker with dry_run=True returns DRY_RUN without HTTP call."""
        import asyncio
        from app.trading.tradier_broker import TradierBroker
        from app.config import Settings
        import httpx

        settings = Settings()
        broker = TradierBroker(settings=settings, http_client=httpx.AsyncClient(), dry_run=True)
        ticket = _make_ticket()

        result = asyncio.run(
            broker.place_order(ticket, trace_id="test-trace-123")
        )

        self.assertEqual(result.broker, "tradier")
        self.assertEqual(result.status, "DRY_RUN")
        self.assertTrue(result.broker_order_id.startswith("dryrun-"))
        self.assertIn("dry run", result.message.lower())
        # Payload should be in raw
        self.assertIn("payload", result.raw)
        self.assertEqual(result.raw["trace_id"], "test-trace-123")
        self.assertTrue(result.raw["dry_run"])


class TestBuildOccSymbol(unittest.TestCase):
    """Test build_occ_symbol() OCC construction."""

    def test_put_occ_symbol(self):
        from app.services.trading.order_builder import build_occ_symbol
        result = build_occ_symbol("IWM", "2026-03-09", 255.0, "put")
        self.assertEqual(result, "IWM260309P00255000")

    def test_call_occ_symbol(self):
        from app.services.trading.order_builder import build_occ_symbol
        result = build_occ_symbol("SPY", "2026-03-20", 665.0, "call")
        self.assertEqual(result, "SPY260320C00665000")

    def test_fractional_strike(self):
        from app.services.trading.order_builder import build_occ_symbol
        result = build_occ_symbol("SPY", "2026-04-17", 552.50, "put")
        self.assertEqual(result, "SPY260417P00552500")

    def test_small_strike(self):
        from app.services.trading.order_builder import build_occ_symbol
        result = build_occ_symbol("XSP", "2026-06-19", 55.0, "call")
        self.assertEqual(result, "XSP260619C00055000")

    def test_invalid_expiration_raises(self):
        from app.services.trading.order_builder import build_occ_symbol
        with self.assertRaises(ValueError):
            build_occ_symbol("SPY", "03-20-2026", 665.0, "put")

    def test_invalid_option_type_raises(self):
        from app.services.trading.order_builder import build_occ_symbol
        with self.assertRaises(ValueError):
            build_occ_symbol("SPY", "2026-03-20", 665.0, "straddle")


class TestBuildMultilegCreditSpread(unittest.TestCase):
    """Test build_multileg_credit_spread() end-to-end payload construction."""

    def test_put_credit_spread_payload(self):
        """IWM put credit spread matches exact Tradier format."""
        from app.services.trading.order_builder import build_multileg_credit_spread
        payload = build_multileg_credit_spread({
            "symbol": "IWM",
            "strategy": "put_credit",
            "expiration": "2026-03-09",
            "short_strike": 255,
            "long_strike": 254,
            "quantity": 1,
            "limit_price": 0.25,
        })
        self.assertEqual(payload["class"], "multileg")
        self.assertEqual(payload["symbol"], "IWM")
        self.assertEqual(payload["type"], "credit")  # Tradier multileg uses credit/debit
        self.assertEqual(payload["duration"], "day")
        # Bracket-indexed legs
        self.assertIn("side[0]", payload)
        self.assertIn("side[1]", payload)

    def test_call_credit_spread_payload(self):
        from app.services.trading.order_builder import build_multileg_credit_spread
        payload = build_multileg_credit_spread({
            "symbol": "SPY",
            "strategy": "call_credit",
            "expiration": "2026-03-20",
            "short_strike": 590,
            "long_strike": 595,
            "quantity": 2,
            "limit_price": 1.10,
        })
        self.assertEqual(payload["option_symbol[0]"], "SPY260320C00590000")
        self.assertEqual(payload["side[0]"], "sell_to_open")
        self.assertEqual(payload["option_symbol[1]"], "SPY260320C00595000")
        self.assertEqual(payload["side[1]"], "buy_to_open")
        self.assertEqual(payload["quantity[0]"], "2")

    def test_preview_flag(self):
        """preview=True should add preview=true to the payload.

        Tradier previews use POST /orders with preview=true in payload.
        """
        from app.services.trading.order_builder import build_multileg_credit_spread
        payload = build_multileg_credit_spread(
            {
                "symbol": "IWM",
                "strategy": "put_credit",
                "expiration": "2026-03-09",
                "short_strike": 255,
                "long_strike": 254,
                "quantity": 1,
                "limit_price": 0.25,
            },
            preview=True,
        )
        self.assertEqual(payload["preview"], "true")

    def test_no_preview_flag_when_false(self):
        """preview=False should NOT include preview in the payload."""
        from app.services.trading.order_builder import build_multileg_credit_spread
        payload = build_multileg_credit_spread(
            {
                "symbol": "IWM",
                "strategy": "put_credit",
                "expiration": "2026-03-09",
                "short_strike": 255,
                "long_strike": 254,
                "quantity": 1,
                "limit_price": 0.25,
            },
            preview=False,
        )
        self.assertNotIn("preview", payload)

    def test_missing_symbol_raises(self):
        from app.services.trading.order_builder import build_multileg_credit_spread
        with self.assertRaises(ValueError):
            build_multileg_credit_spread({
                "strategy": "put_credit",
                "expiration": "2026-03-09",
                "short_strike": 255,
                "long_strike": 254,
                "quantity": 1,
                "limit_price": 0.25,
            })

    def test_unknown_strategy_raises(self):
        from app.services.trading.order_builder import build_multileg_credit_spread
        with self.assertRaises(ValueError):
            build_multileg_credit_spread({
                "symbol": "SPY",
                "strategy": "iron_butterfly",
                "expiration": "2026-03-20",
                "short_strike": 665,
                "long_strike": 660,
                "quantity": 1,
                "limit_price": 0.50,
            })


if __name__ == "__main__":
    unittest.main()
