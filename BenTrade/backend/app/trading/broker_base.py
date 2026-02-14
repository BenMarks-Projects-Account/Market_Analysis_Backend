from __future__ import annotations

from abc import ABC, abstractmethod

from app.trading.models import BrokerResult, OrderTicket


class BrokerBase(ABC):
    @abstractmethod
    async def place_order(self, ticket: OrderTicket) -> BrokerResult:
        raise NotImplementedError

    @abstractmethod
    async def get_order(self, broker_order_id: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> dict:
        raise NotImplementedError
