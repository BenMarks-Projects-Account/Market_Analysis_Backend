from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any


@dataclass
class IdempotencyEntry:
    ticket_id: str
    idempotency_key: str
    response: dict[str, Any]


class InMemoryTradingRepository:
    def __init__(self) -> None:
        self._tickets: dict[str, dict[str, Any]] = {}
        self._orders: dict[str, dict[str, Any]] = {}
        self._idempotency: dict[tuple[str, str], IdempotencyEntry] = {}
        self._lock = RLock()

    def save_ticket(self, ticket: dict[str, Any]) -> None:
        with self._lock:
            self._tickets[ticket["id"]] = ticket

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._tickets.get(ticket_id)

    def save_order(self, order: dict[str, Any]) -> None:
        with self._lock:
            self._orders[order["id"]] = order

    def list_orders(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._orders.values())

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._orders.get(order_id)

    def get_idempotent(self, ticket_id: str, idempotency_key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._idempotency.get((ticket_id, idempotency_key))
            return entry.response if entry else None

    def save_idempotent(self, ticket_id: str, idempotency_key: str, response: dict[str, Any]) -> None:
        with self._lock:
            self._idempotency[(ticket_id, idempotency_key)] = IdempotencyEntry(
                ticket_id=ticket_id,
                idempotency_key=idempotency_key,
                response=response,
            )
