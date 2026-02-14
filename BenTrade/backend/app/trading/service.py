from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from app.config import Settings
from app.services.base_data_service import BaseDataService
from app.storage.repository import InMemoryTradingRepository
from app.trading.broker_base import BrokerBase
from app.trading.models import (
    BrokerResult,
    OrderLeg,
    OrderPreviewResponse,
    OrderSubmitResponse,
    OrderTicket,
    ProfitLossEstimate,
    TradingPreviewRequest,
    TradingSubmitRequest,
)
from app.trading.risk import evaluate_preview_risk, evaluate_submit_freshness


class TradingService:
    def __init__(
        self,
        *,
        settings: Settings,
        base_data_service: BaseDataService,
        repository: InMemoryTradingRepository,
        paper_broker: BrokerBase,
        live_broker: BrokerBase,
    ) -> None:
        self.settings = settings
        self.base_data_service = base_data_service
        self.repository = repository
        self.paper_broker = paper_broker
        self.live_broker = live_broker

    def _secret(self) -> bytes:
        secret = self.settings.TRADING_CONFIRMATION_SECRET or "unsafe-dev-secret"
        return secret.encode("utf-8")

    @staticmethod
    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")

    @staticmethod
    def _b64d(data: str) -> bytes:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded.encode("utf-8"))

    def _ticket_hash(self, ticket: OrderTicket) -> str:
        serialized = json.dumps(ticket.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _make_confirmation_token(self, ticket: OrderTicket, expires_at: datetime) -> str:
        payload = {
            "ticket_id": ticket.id,
            "ticket_hash": self._ticket_hash(ticket),
            "exp": int(expires_at.timestamp()),
            "mode": ticket.mode,
        }
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(self._secret(), payload_bytes, hashlib.sha256).digest()
        return f"{self._b64(payload_bytes)}.{self._b64(sig)}"

    def _validate_confirmation_token(self, token: str, ticket: OrderTicket) -> None:
        try:
            payload_b64, sig_b64 = token.split(".", 1)
            payload_raw = self._b64d(payload_b64)
            expected_sig = hmac.new(self._secret(), payload_raw, hashlib.sha256).digest()
            provided_sig = self._b64d(sig_b64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid confirmation token format") from exc

        if not hmac.compare_digest(expected_sig, provided_sig):
            raise HTTPException(status_code=400, detail="Invalid confirmation token signature")

        payload = json.loads(payload_raw.decode("utf-8"))
        if payload.get("ticket_id") != ticket.id:
            raise HTTPException(status_code=400, detail="Confirmation token ticket mismatch")
        if payload.get("ticket_hash") != self._ticket_hash(ticket):
            raise HTTPException(status_code=400, detail="Confirmation token hash mismatch")
        exp = int(payload.get("exp", 0))
        if datetime.now(timezone.utc).timestamp() > exp:
            raise HTTPException(status_code=400, detail="Confirmation token expired")

    @staticmethod
    def _mid(bid: float | None, ask: float | None) -> float | None:
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _build_legs(
        self,
        *,
        req: TradingPreviewRequest,
        short_contract,
        long_contract,
    ) -> tuple[OrderLeg, OrderLeg, str]:
        qty = req.quantity
        if req.strategy == "put_credit":
            short_side, long_side, price_effect = "SELL_TO_OPEN", "BUY_TO_OPEN", "CREDIT"
            option_type = "put"
        elif req.strategy == "call_credit":
            short_side, long_side, price_effect = "SELL_TO_OPEN", "BUY_TO_OPEN", "CREDIT"
            option_type = "call"
        elif req.strategy == "put_debit":
            short_side, long_side, price_effect = "SELL_TO_OPEN", "BUY_TO_OPEN", "DEBIT"
            option_type = "put"
        else:
            short_side, long_side, price_effect = "SELL_TO_OPEN", "BUY_TO_OPEN", "DEBIT"
            option_type = "call"

        short_leg = OrderLeg(
            option_type=option_type,
            expiration=req.expiration,
            strike=req.short_strike,
            side=short_side,
            quantity=qty,
            occ_symbol=short_contract.symbol,
            bid=short_contract.bid,
            ask=short_contract.ask,
            mid=self._mid(short_contract.bid, short_contract.ask),
        )
        long_leg = OrderLeg(
            option_type=option_type,
            expiration=req.expiration,
            strike=req.long_strike,
            side=long_side,
            quantity=qty,
            occ_symbol=long_contract.symbol,
            bid=long_contract.bid,
            ask=long_contract.ask,
            mid=self._mid(long_contract.bid, long_contract.ask),
        )
        return short_leg, long_leg, price_effect

    def _estimate_max_pnl(
        self,
        *,
        width: float,
        limit_price: float,
        quantity: int,
        price_effect: str,
    ) -> tuple[ProfitLossEstimate, ProfitLossEstimate]:
        multiplier = self.settings.TRADING_CONTRACT_MULTIPLIER
        if price_effect == "CREDIT":
            max_profit_per = max(0.0, limit_price)
            max_loss_per = max(0.0, width - limit_price)
        else:
            max_profit_per = max(0.0, width - limit_price)
            max_loss_per = max(0.0, limit_price)

        max_profit = ProfitLossEstimate(
            per_spread=max_profit_per,
            total=max_profit_per * quantity * multiplier,
        )
        max_loss = ProfitLossEstimate(
            per_spread=max_loss_per,
            total=max_loss_per * quantity * multiplier,
        )
        return max_profit, max_loss

    async def preview(self, req: TradingPreviewRequest) -> OrderPreviewResponse:
        symbol = req.symbol.upper()
        option_type = "put" if "put" in req.strategy else "call"

        quote = await self.base_data_service.tradier_client.get_quote(symbol)
        quote_ts = datetime.now(timezone.utc)
        raw_chain = await self.base_data_service.tradier_client.get_chain(symbol, req.expiration, greeks=True)
        chain_ts = datetime.now(timezone.utc)

        contracts = self.base_data_service.normalize_chain(raw_chain)
        filtered = [c for c in contracts if c.option_type == option_type and c.expiration == req.expiration]
        contract_map = {f"{c.strike:.8f}": c for c in filtered}

        short_contract = contract_map.get(f"{req.short_strike:.8f}")
        long_contract = contract_map.get(f"{req.long_strike:.8f}")
        if not short_contract or not long_contract:
            raise HTTPException(status_code=404, detail="One or both spread legs were not found in option chain")

        short_leg, long_leg, price_effect = self._build_legs(
            req=req,
            short_contract=short_contract,
            long_contract=long_contract,
        )

        width = abs(req.short_strike - req.long_strike)
        spread_mid = 0.0
        if short_leg.mid is not None and long_leg.mid is not None:
            if price_effect == "CREDIT":
                spread_mid = short_leg.mid - long_leg.mid
            else:
                spread_mid = long_leg.mid - short_leg.mid

        max_profit, max_loss = self._estimate_max_pnl(
            width=width,
            limit_price=req.limit_price,
            quantity=req.quantity,
            price_effect=price_effect,
        )

        risk = evaluate_preview_risk(
            settings=self.settings,
            strategy=req.strategy,
            width=width,
            max_loss_per_spread=max_loss.per_spread * self.settings.TRADING_CONTRACT_MULTIPLIER,
            net_credit_or_debit=max(0.0, spread_mid),
            short_leg=short_leg,
            long_leg=long_leg,
            limit_price=req.limit_price,
        )

        checks = dict(risk.checks)
        checks.update(
            {
                "legs_found": True,
                "spread_mid": round(spread_mid, 6),
                "underlying_price": quote.get("last") or quote.get("close") or quote.get("mark"),
            }
        )

        hard_reject_keys = ["width_ok", "max_loss_ok", "credit_floor_ok", "legs_have_bid_ask"]
        hard_failures = [k for k in hard_reject_keys if checks.get(k) is False]
        if hard_failures:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Preview failed risk hard checks",
                    "failed_checks": hard_failures,
                    "checks": checks,
                    "warnings": risk.warnings,
                },
            )

        ticket = OrderTicket(
            id=str(uuid.uuid4()),
            mode=req.mode,
            strategy=req.strategy,
            underlying=symbol,
            expiration=req.expiration,
            quantity=req.quantity,
            limit_price=req.limit_price,
            price_effect=price_effect,
            time_in_force=req.time_in_force,
            legs=[short_leg, long_leg],
            estimated_max_profit=max_profit,
            estimated_max_loss=max_loss,
            created_at=datetime.now(timezone.utc),
            asof_quote_ts=quote_ts,
            asof_chain_ts=chain_ts,
        )
        self.repository.save_ticket(ticket.model_dump(mode="json"))

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.settings.TRADING_CONFIRMATION_TTL_SECONDS)
        token = self._make_confirmation_token(ticket, expires_at)
        return OrderPreviewResponse(
            ticket=ticket,
            checks=checks,
            warnings=risk.warnings,
            confirmation_token=token,
            expires_at=expires_at,
        )

    async def submit(self, req: TradingSubmitRequest) -> OrderSubmitResponse:
        ticket_raw = self.repository.get_ticket(req.ticket_id)
        if not ticket_raw:
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket = OrderTicket.model_validate(ticket_raw)
        if ticket.mode != req.mode:
            raise HTTPException(status_code=400, detail="Submit mode does not match preview mode")

        self._validate_confirmation_token(req.confirmation_token, ticket)

        cached = self.repository.get_idempotent(req.ticket_id, req.idempotency_key)
        if cached:
            return OrderSubmitResponse.model_validate(cached)

        if req.mode == "live":
            if not self.settings.ENABLE_LIVE_TRADING or not self.settings.LIVE_TRADING_RUNTIME_ENABLED:
                raise HTTPException(status_code=403, detail="Live trading is disabled")

            freshness = evaluate_submit_freshness(ticket, max_age_seconds=self.settings.LIVE_DATA_MAX_AGE_SECONDS)
            if not freshness["data_fresh"]:
                raise HTTPException(status_code=400, detail=f"Live submit rejected: stale market data ({freshness})")

            result = await self.live_broker.place_order(ticket)
        else:
            result = await self.paper_broker.place_order(ticket)

        response = OrderSubmitResponse(
            broker=result.broker,
            status=result.status,
            broker_order_id=result.broker_order_id,
            message=result.message,
            created_at=datetime.now(timezone.utc),
        )

        order_record = {
            "id": response.broker_order_id,
            "ticket_id": ticket.id,
            "idempotency_key": req.idempotency_key,
            "request_mode": req.mode,
            "ticket": ticket.model_dump(mode="json"),
            "result": response.model_dump(mode="json"),
            "raw": result.raw,
        }
        self.repository.save_order(order_record)
        self.repository.save_idempotent(req.ticket_id, req.idempotency_key, response.model_dump(mode="json"))
        return response
