from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from app.config import Settings
from app.trading.broker_base import BrokerBase
from app.trading.models import BrokerResult, OrderTicket
from app.trading.tradier_credentials import TradierCredentials
from app.utils.http import request_json

logger = logging.getLogger(__name__)


class TradierBroker(BrokerBase):
    """Tradier multi-leg order broker.

    Credentials can be supplied per-call via ``place_order(ticket, creds=...)``.
    If *creds* is ``None``, the broker falls back to the legacy Settings fields
    (``TRADIER_TOKEN``, ``TRADIER_BASE_URL``, ``TRADIER_ACCOUNT_ID``).
    """

    def __init__(self, *, settings: Settings, http_client: httpx.AsyncClient, dry_run: bool = True) -> None:
        self.settings = settings
        self.http_client = http_client
        self.dry_run = dry_run
        # Resolved credentials — set per-call via place_order(creds=...)
        self._runtime_creds: TradierCredentials | None = None

    # ── Credential accessors ─────────────────────────────────
    def _api_key(self) -> str:
        if self._runtime_creds:
            return self._runtime_creds.api_key
        return self.settings.TRADIER_TOKEN

    def _base_url(self) -> str:
        if self._runtime_creds:
            return self._runtime_creds.base_url
        return self.settings.TRADIER_BASE_URL

    def _account_id(self) -> str:
        if self._runtime_creds:
            return self._runtime_creds.account_id
        return self.settings.TRADIER_ACCOUNT_ID

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key()}",
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

    async def place_multileg_order(self, ticket: OrderTicket, *, creds: TradierCredentials | None = None) -> BrokerResult:
        self._runtime_creds = creds
        try:
            return await self._place_multileg_order_inner(ticket)
        finally:
            self._runtime_creds = None

    async def _place_multileg_order_inner(self, ticket: OrderTicket) -> BrokerResult:
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

        url = f"{self._base_url()}/accounts/{self._account_id()}/orders"
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

    async def place_order(self, ticket: OrderTicket, *, creds: TradierCredentials | None = None) -> BrokerResult:
        return await self.place_multileg_order(ticket, creds=creds)

    async def get_order(self, broker_order_id: str) -> dict:
        if not self._account_id():
            return {"broker": "tradier", "broker_order_id": broker_order_id, "status": "UNKNOWN"}

        url = f"{self._base_url()}/accounts/{self._account_id()}/orders/{broker_order_id}"
        payload = await request_json(self.http_client, "GET", url, headers=self._headers)
        return payload

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {
            "broker": "tradier",
            "broker_order_id": broker_order_id,
            "status": "NOT_IMPLEMENTED",
            "message": "Cancel endpoint stubbed; implement if needed",
        }
