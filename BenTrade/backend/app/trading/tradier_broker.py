from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from app.config import Settings
from app.trading.broker_base import BrokerBase
from app.trading.models import BrokerResult, OrderTicket
from app.utils.http import request_json

logger = logging.getLogger(__name__)


class TradierBroker(BrokerBase):
    def __init__(self, *, settings: Settings, http_client: httpx.AsyncClient, dry_run: bool = True) -> None:
        self.settings = settings
        self.http_client = http_client
        self.dry_run = dry_run

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.TRADIER_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def build_payload(self, ticket: OrderTicket) -> dict[str, Any]:
        # TODO: Confirm exact Tradier advanced/multileg field names for your account permissions.
        # This payload is intentionally centralized here so broker-specific field changes
        # are isolated from the rest of the app.
        payload: dict[str, Any] = {
            "class": "multileg",
            "symbol": ticket.underlying,
            "type": "limit",
            "duration": ticket.time_in_force.lower(),
            "price": f"{ticket.limit_price:.2f}",
            "tag": ticket.id,
            "option_symbol[0]": ticket.legs[0].occ_symbol or "",
            "side[0]": ticket.legs[0].side.lower(),
            "quantity[0]": str(ticket.legs[0].quantity),
            "option_symbol[1]": ticket.legs[1].occ_symbol or "",
            "side[1]": ticket.legs[1].side.lower(),
            "quantity[1]": str(ticket.legs[1].quantity),
        }
        return payload

    async def place_multileg_order(self, ticket: OrderTicket) -> BrokerResult:
        payload = self.build_payload(ticket)

        if self.dry_run:
            logger.warning("event=tradier_dry_run ticket_id=%s payload=%s", ticket.id, payload)
            return BrokerResult(
                broker="tradier",
                status="ACCEPTED",
                broker_order_id=f"dryrun-{uuid.uuid4().hex[:10]}",
                message="Tradier dry-run enabled; payload logged, no live order submitted",
                raw={"payload": payload},
            )

        url = f"{self.settings.TRADIER_BASE_URL}/accounts/{self.settings.TRADIER_ACCOUNT_ID}/orders"
        result = await request_json(
            self.http_client,
            "POST",
            url,
            params=payload,
            headers=self._headers,
        )
        order_obj = result.get("order") or {}
        broker_order_id = str(order_obj.get("id") or order_obj.get("order_id") or uuid.uuid4().hex)
        status = str(order_obj.get("status") or "ACCEPTED").upper()

        normalized = "ACCEPTED"
        if status in ("FILLED", "WORKING", "REJECTED", "ACCEPTED"):
            normalized = status

        return BrokerResult(
            broker="tradier",
            status=normalized,
            broker_order_id=broker_order_id,
            message=f"Tradier multi-leg order status: {normalized}",
            raw=result,
        )

    async def place_order(self, ticket: OrderTicket) -> BrokerResult:
        return await self.place_multileg_order(ticket)

    async def get_order(self, broker_order_id: str) -> dict:
        if not self.settings.TRADIER_ACCOUNT_ID:
            return {"broker": "tradier", "broker_order_id": broker_order_id, "status": "UNKNOWN"}

        url = f"{self.settings.TRADIER_BASE_URL}/accounts/{self.settings.TRADIER_ACCOUNT_ID}/orders/{broker_order_id}"
        payload = await request_json(self.http_client, "GET", url, headers=self._headers)
        return payload

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {
            "broker": "tradier",
            "broker_order_id": broker_order_id,
            "status": "NOT_IMPLEMENTED",
            "message": "Cancel endpoint stubbed; implement if needed",
        }
