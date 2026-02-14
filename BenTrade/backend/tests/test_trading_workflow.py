import unittest
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import Settings
from app.models.schemas import OptionContract
from app.storage.repository import InMemoryTradingRepository
from app.trading.models import OrderLeg, OrderTicket, ProfitLossEstimate, TradingPreviewRequest, TradingSubmitRequest
from app.trading.paper_broker import PaperBroker
from app.trading.service import TradingService
from app.trading.tradier_broker import TradierBroker


class _FakeTradierClient:
    async def get_quote(self, symbol: str):
        return {"last": 681.25, "symbol": symbol}

    async def get_chain(self, symbol: str, expiration: str, greeks: bool = True):
        return [
            {
                "option_type": "put",
                "strike": 665,
                "expiration_date": expiration,
                "bid": 1.10,
                "ask": 1.20,
                "symbol": f"{symbol}PUT665",
            },
            {
                "option_type": "put",
                "strike": 660,
                "expiration_date": expiration,
                "bid": 0.25,
                "ask": 0.35,
                "symbol": f"{symbol}PUT660",
            },
        ]


class _FakeBaseDataService:
    def __init__(self):
        self.tradier_client = _FakeTradierClient()

    def normalize_chain(self, contracts):
        out = []
        for c in contracts:
            out.append(
                OptionContract(
                    option_type=c["option_type"],
                    strike=float(c["strike"]),
                    expiration=c["expiration_date"],
                    bid=float(c["bid"]),
                    ask=float(c["ask"]),
                    symbol=c["symbol"],
                )
            )
        return out


def _make_service() -> TradingService:
    settings = Settings(
        TRADING_CONFIRMATION_SECRET="test-secret",
        ENABLE_LIVE_TRADING=False,
        LIVE_TRADING_RUNTIME_ENABLED=False,
        MAX_WIDTH_DEFAULT=10,
        MAX_LOSS_PER_SPREAD_DEFAULT=500,
        MIN_CREDIT_DEFAULT=0.2,
    )
    repo = InMemoryTradingRepository()
    paper = PaperBroker()
    live = TradierBroker(settings=settings, http_client=None, dry_run=True)  # type: ignore[arg-type]
    return TradingService(
        settings=settings,
        base_data_service=_FakeBaseDataService(),
        repository=repo,
        paper_broker=paper,
        live_broker=live,
    )


class TradingWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_builds_multileg_ticket_and_checks(self):
        service = _make_service()
        req = TradingPreviewRequest(
            symbol="SPY",
            strategy="put_credit",
            expiration="2026-03-20",
            short_strike=665,
            long_strike=660,
            quantity=1,
            limit_price=0.92,
            mode="paper",
        )

        preview = await service.preview(req)

        self.assertEqual(preview.ticket.strategy, "put_credit")
        self.assertEqual(len(preview.ticket.legs), 2)
        self.assertEqual({leg.side for leg in preview.ticket.legs}, {"SELL_TO_OPEN", "BUY_TO_OPEN"})
        self.assertIn("width_ok", preview.checks)
        self.assertTrue(preview.confirmation_token)

    async def test_preview_hard_rejects_when_bid_ask_missing(self):
        class _MissingAskBaseDataService(_FakeBaseDataService):
            def normalize_chain(self, contracts):
                rows = super().normalize_chain(contracts)
                rows[1].ask = None
                return rows

        settings = Settings(
            TRADING_CONFIRMATION_SECRET="test-secret",
            ENABLE_LIVE_TRADING=False,
            LIVE_TRADING_RUNTIME_ENABLED=False,
        )
        service = TradingService(
            settings=settings,
            base_data_service=_MissingAskBaseDataService(),
            repository=InMemoryTradingRepository(),
            paper_broker=PaperBroker(),
            live_broker=TradierBroker(settings=settings, http_client=None, dry_run=True),  # type: ignore[arg-type]
        )

        req = TradingPreviewRequest(
            symbol="SPY",
            strategy="put_credit",
            expiration="2026-03-20",
            short_strike=665,
            long_strike=660,
            quantity=1,
            limit_price=0.92,
            mode="paper",
        )

        with self.assertRaises(HTTPException):
            await service.preview(req)

    async def test_submit_is_idempotent_for_same_ticket_and_key(self):
        service = _make_service()
        req = TradingPreviewRequest(
            symbol="SPY",
            strategy="put_credit",
            expiration="2026-03-20",
            short_strike=665,
            long_strike=660,
            quantity=1,
            limit_price=0.92,
            mode="paper",
        )
        preview = await service.preview(req)

        submit_req = TradingSubmitRequest(
            ticket_id=preview.ticket.id,
            confirmation_token=preview.confirmation_token,
            idempotency_key="idem-abc-123",
            mode="paper",
        )

        first = await service.submit(submit_req)
        second = await service.submit(submit_req)

        self.assertEqual(first.broker_order_id, second.broker_order_id)
        self.assertEqual(first.status, second.status)

    async def test_paper_broker_fill_simulation_credit(self):
        broker = PaperBroker()
        ticket = OrderTicket(
            id="ticket-1",
            mode="paper",
            strategy="put_credit",
            underlying="SPY",
            expiration="2026-03-20",
            quantity=1,
            limit_price=0.75,
            price_effect="CREDIT",
            time_in_force="DAY",
            legs=[
                OrderLeg(
                    option_type="put",
                    expiration="2026-03-20",
                    strike=665,
                    side="SELL_TO_OPEN",
                    quantity=1,
                    bid=1.00,
                    ask=1.10,
                    mid=1.05,
                ),
                OrderLeg(
                    option_type="put",
                    expiration="2026-03-20",
                    strike=660,
                    side="BUY_TO_OPEN",
                    quantity=1,
                    bid=0.20,
                    ask=0.30,
                    mid=0.25,
                ),
            ],
            estimated_max_profit=ProfitLossEstimate(per_spread=0.9, total=90),
            estimated_max_loss=ProfitLossEstimate(per_spread=4.1, total=410),
            created_at=datetime.now(timezone.utc),
            asof_quote_ts=datetime.now(timezone.utc),
            asof_chain_ts=datetime.now(timezone.utc),
        )

        result = await broker.place_order(ticket)
        self.assertEqual(result.status, "FILLED")


if __name__ == "__main__":
    unittest.main()
