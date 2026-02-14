from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class OrderLeg(BaseModel):
    option_type: Literal["put", "call"]
    expiration: str
    strike: float
    side: Literal["BUY_TO_OPEN", "SELL_TO_OPEN"]
    quantity: int
    occ_symbol: str | None = None
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None


class ProfitLossEstimate(BaseModel):
    per_spread: float
    total: float


class OrderTicket(BaseModel):
    id: str
    mode: Literal["paper", "live"]
    strategy: Literal["put_credit", "call_credit", "put_debit", "call_debit"]
    underlying: str
    expiration: str
    quantity: int
    order_type: Literal["LIMIT"] = "LIMIT"
    limit_price: float
    price_effect: Literal["CREDIT", "DEBIT"]
    time_in_force: Literal["DAY", "GTC"]
    legs: list[OrderLeg] = Field(min_length=2, max_length=2)
    estimated_max_profit: ProfitLossEstimate
    estimated_max_loss: ProfitLossEstimate
    created_at: datetime
    asof_quote_ts: datetime
    asof_chain_ts: datetime


class TradingPreviewRequest(BaseModel):
    symbol: str
    strategy: Literal["put_credit", "call_credit", "put_debit", "call_debit"]
    expiration: str
    short_strike: float
    long_strike: float
    quantity: int = Field(ge=1)
    limit_price: float = Field(gt=0)
    time_in_force: Literal["DAY", "GTC"] = "DAY"
    mode: Literal["paper", "live"] = "paper"


class OrderPreviewResponse(BaseModel):
    ticket: OrderTicket
    checks: dict[str, Any]
    warnings: list[str]
    confirmation_token: str
    expires_at: datetime


class TradingSubmitRequest(BaseModel):
    ticket_id: str
    confirmation_token: str
    idempotency_key: str = Field(min_length=6)
    mode: Literal["paper", "live"] = "paper"


class OrderSubmitResponse(BaseModel):
    broker: Literal["paper", "tradier"]
    status: Literal["ACCEPTED", "REJECTED", "WORKING", "FILLED"]
    broker_order_id: str
    message: str
    created_at: datetime


class BrokerResult(BaseModel):
    broker: Literal["paper", "tradier"]
    status: Literal["ACCEPTED", "REJECTED", "WORKING", "FILLED"]
    broker_order_id: str
    message: str
    raw: dict[str, Any] | None = None
