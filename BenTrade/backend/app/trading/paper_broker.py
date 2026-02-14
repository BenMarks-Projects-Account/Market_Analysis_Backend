from __future__ import annotations

import uuid

from app.trading.broker_base import BrokerBase
from app.trading.models import BrokerResult, OrderTicket


def _leg_mid(leg) -> float:
    if leg.mid is not None:
        return leg.mid
    if leg.bid is not None and leg.ask is not None:
        return (leg.bid + leg.ask) / 2.0
    return 0.0


class PaperBroker(BrokerBase):
    async def place_order(self, ticket: OrderTicket) -> BrokerResult:
        short_leg = next((l for l in ticket.legs if l.side == "SELL_TO_OPEN"), None)
        long_leg = next((l for l in ticket.legs if l.side == "BUY_TO_OPEN"), None)

        spread_mid = 0.0
        if short_leg and long_leg:
            if ticket.price_effect == "CREDIT":
                spread_mid = _leg_mid(short_leg) - _leg_mid(long_leg)
            else:
                spread_mid = _leg_mid(long_leg) - _leg_mid(short_leg)

        if ticket.price_effect == "CREDIT":
            status = "FILLED" if ticket.limit_price <= spread_mid else "WORKING"
        else:
            status = "FILLED" if ticket.limit_price >= spread_mid else "WORKING"

        return BrokerResult(
            broker="paper",
            status=status,
            broker_order_id=f"paper-{uuid.uuid4().hex[:12]}",
            message=f"Paper multi-leg order {status.lower()} (spread_mid={spread_mid:.4f})",
            raw={"spread_mid": spread_mid},
        )

    async def get_order(self, broker_order_id: str) -> dict:
        return {"broker": "paper", "broker_order_id": broker_order_id}

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {"broker": "paper", "broker_order_id": broker_order_id, "status": "CANCELLED"}
