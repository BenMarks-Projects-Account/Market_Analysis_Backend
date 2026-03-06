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
        }

    def build_payload(self, ticket: OrderTicket) -> dict[str, Any]:
        """Build Tradier multi-leg order form-encoded payload from an OrderTicket.

        Supports 2-leg spreads AND 4-leg condors (loops over all legs).
        Input fields: ticket.underlying, ticket.time_in_force, ticket.limit_price,
                      ticket.id (tag), ticket.price_effect,
                      ticket.legs[i].occ_symbol/side/quantity
        Formula: payload = { class: multileg, type: credit|debit,
                 side[i], option_symbol[i], quantity[i] }
        Tradier multileg orders use type="credit"/"debit"/"even", NOT "limit".
        """
        payload: dict[str, Any] = {
            "class": "multileg",
            "symbol": ticket.underlying,
            "type": ticket.price_effect.lower(),
            "duration": ticket.time_in_force.lower(),
            "price": str(round(ticket.limit_price, 2)),
            "tag": ticket.id,
        }
        for i, leg in enumerate(ticket.legs):
            payload[f"side[{i}]"] = leg.side.lower()
            payload[f"option_symbol[{i}]"] = leg.occ_symbol or ""
            payload[f"quantity[{i}]"] = str(leg.quantity)
        logger.debug("event=build_payload payload=%s", payload)
        return payload

    async def preview_multileg_order(
        self,
        ticket: OrderTicket,
        *,
        creds: TradierCredentials | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Call Tradier's order preview (POST /orders with preview=true)
        to get buying power effect and order validation without placing the order.

        Returns the full Tradier preview response dict.
        Raises UpstreamError if Tradier returns a non-2xx response.
        """
        self._runtime_creds = creds
        try:
            return await self._preview_multileg_order_inner(ticket, trace_id=trace_id)
        finally:
            self._runtime_creds = None

    async def _preview_multileg_order_inner(
        self,
        ticket: OrderTicket,
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        payload = self.build_payload(ticket)
        payload["preview"] = "true"
        tid = trace_id or ticket.id

        acct_id = self._account_id()
        if not acct_id:
            raise ValueError("Cannot preview order: account_id is not configured")

        url = f"{self._base_url()}/accounts/{acct_id}/orders"

        # === DIAGNOSTIC: Confirm real URL and payload leaving backend ===
        print(f"FINAL TRADIER URL: {url}")
        print(f"FINAL TRADIER PAYLOAD: {payload}")

        logger.info(
            "underlying=%s strategy=%s legs=%d limit_price=%s "
            "base_url=%s acct_last4=%s",
            tid, url, ticket.underlying, ticket.strategy,
            len(ticket.legs), ticket.limit_price,
            self._base_url(), (acct_id or "")[-4:],
        )
        logger.debug(
            "event=tradier_preview_payload trace_id=%s payload=%s",
            tid, payload,
        )

        result = await request_json(
            self.http_client,
            "POST",
            url,
            data=payload,
            headers=self._headers,
        )

        logger.info(
            "event=tradier_preview_result trace_id=%s response_keys=%s",
            tid, list(result.keys()),
        )
        logger.debug(
            "event=tradier_preview_response_body trace_id=%s body=%s",
            tid, result,
        )

        return result

    async def preview_raw_payload(
        self,
        payload: dict[str, str],
        *,
        creds: TradierCredentials | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit a pre-built Tradier payload for order preview.

        Unlike preview_multileg_order(), this accepts a raw payload dict
        (from build_tradier_multileg_order) and sends it directly.
        Sends to POST /orders with preview=true in the payload.
        """
        self._runtime_creds = creds
        try:
            # Ensure preview=true is in the payload
            payload["preview"] = "true"

            acct_id = self._account_id()
            if not acct_id:
                raise ValueError("Cannot preview order: account_id is not configured")

            url = f"{self._base_url()}/accounts/{acct_id}/orders"
            tid = trace_id or "raw-preview"

            # === DIAGNOSTIC: Confirm real URL and payload leaving backend ===
            print(f"FINAL TRADIER URL: {url}")
            print(f"FINAL TRADIER PAYLOAD: {payload}")

            logger.info(
                "event=tradier_raw_preview_submit trace_id=%s url=%s payload_keys=%s",
                tid, url, list(payload.keys()),
            )
            logger.debug(
                "event=tradier_raw_preview_payload trace_id=%s payload=%s",
                tid, payload,
            )

            result = await request_json(
                self.http_client,
                "POST",
                url,
                data=payload,
                headers=self._headers,
            )

            logger.info(
                "event=tradier_raw_preview_result trace_id=%s response_keys=%s",
                tid, list(result.keys()),
            )
            logger.debug(
                "event=tradier_raw_preview_response trace_id=%s body=%s",
                tid, result,
            )

            return result
        finally:
            self._runtime_creds = None

    async def place_multileg_order(
        self,
        ticket: OrderTicket,
        *,
        creds: TradierCredentials | None = None,
        trace_id: str | None = None,
        dry_run: bool | None = None,
    ) -> BrokerResult:
        self._runtime_creds = creds
        try:
            return await self._place_multileg_order_inner(
                ticket, trace_id=trace_id, dry_run=dry_run,
            )
        finally:
            self._runtime_creds = None

    async def _place_multileg_order_inner(
        self,
        ticket: OrderTicket,
        *,
        trace_id: str | None = None,
        dry_run: bool | None = None,
    ) -> BrokerResult:
        payload = self.build_payload(ticket)
        tid = trace_id or ticket.id

        # Per-call dry_run overrides instance default when provided
        effective_dry_run = dry_run if dry_run is not None else self.dry_run

        # ── Safe-redacted logging (no tokens/secrets) ──────────
        acct_last4 = (self._account_id() or "")[-4:] or "????"
        log_ctx = {
            "trace_id": tid,
            "mode": self._runtime_creds.mode_label if self._runtime_creds else "legacy",
            "account_last4": acct_last4,
            "base_url": self._base_url(),
            "underlying": ticket.underlying,
            "strategy": ticket.strategy,
            "legs": len(ticket.legs),
            "limit_price": ticket.limit_price,
            "dry_run": effective_dry_run,
        }

        if effective_dry_run:
            logger.warning(
                "event=tradier_dry_run trace_id=%s ticket_id=%s payload=%s",
                tid, ticket.id, payload,
            )
            return BrokerResult(
                broker="tradier",
                status="DRY_RUN",
                broker_order_id=f"dryrun-{uuid.uuid4().hex[:10]}",
                message="DRY RUN — payload logged, no broker order placed",
                raw={"payload": payload, "trace_id": tid, "dry_run": True},
            )

        url = f"{self._base_url()}/accounts/{self._account_id()}/orders"
        logger.info(
            "event=tradier_order_submit trace_id=%s url=%s ctx=%s",
            tid, url, log_ctx,
        )

        # Send as form-encoded body — Tradier multileg orders use bracket-indexed
        # form fields (side[0], option_symbol[0], etc.) with data= encoding.
        result = await request_json(
            self.http_client,
            "POST",
            url,
            data=payload,
            headers=self._headers,
        )

        order_obj = result.get("order") or {}
        broker_order_id = str(order_obj.get("id") or order_obj.get("order_id") or uuid.uuid4().hex)
        status = str(order_obj.get("status") or "ACCEPTED").upper()

        # Tradier returns: ok, pending, open, partially_filled, filled, expired, canceled, rejected
        _TRADIER_STATUS_MAP = {
            "OK": "ACCEPTED",
            "PENDING": "ACCEPTED",
            "OPEN": "WORKING",
            "PARTIALLY_FILLED": "WORKING",
            "FILLED": "FILLED",
            "EXPIRED": "REJECTED",
            "CANCELED": "REJECTED",
            "REJECTED": "REJECTED",
            # Fallbacks for legacy
            "ACCEPTED": "ACCEPTED",
            "WORKING": "WORKING",
        }
        normalized = _TRADIER_STATUS_MAP.get(status, "ACCEPTED")

        logger.info(
            "event=tradier_order_result trace_id=%s broker_order_id=%s "
            "tradier_status=%s normalized=%s response_keys=%s",
            tid, broker_order_id, status, normalized, list(result.keys()),
        )

        return BrokerResult(
            broker="tradier",
            status=normalized,
            broker_order_id=broker_order_id,
            message=f"Tradier order {normalized} (raw: {status})",
            raw={**result, "trace_id": tid},
        )

    async def place_order(
        self,
        ticket: OrderTicket,
        *,
        creds: TradierCredentials | None = None,
        trace_id: str | None = None,
        dry_run: bool | None = None,
    ) -> BrokerResult:
        return await self.place_multileg_order(
            ticket, creds=creds, trace_id=trace_id, dry_run=dry_run,
        )

    async def get_order_status(
        self,
        broker_order_id: str,
        *,
        creds: TradierCredentials | None = None,
    ) -> dict:
        """Fetch order status from Tradier by order ID.

        Used for reconciliation polling after submission.
        """
        self._runtime_creds = creds
        try:
            acct = self._account_id()
            if not acct:
                return {"broker": "tradier", "broker_order_id": broker_order_id, "status": "UNKNOWN"}

            url = f"{self._base_url()}/accounts/{acct}/orders/{broker_order_id}"
            result = await request_json(self.http_client, "GET", url, headers=self._headers)
            return result
        finally:
            self._runtime_creds = None

    async def get_order(self, broker_order_id: str) -> dict:
        return await self.get_order_status(broker_order_id)

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {
            "broker": "tradier",
            "broker_order_id": broker_order_id,
            "status": "NOT_IMPLEMENTED",
            "message": "Cancel endpoint stubbed; implement if needed",
        }
