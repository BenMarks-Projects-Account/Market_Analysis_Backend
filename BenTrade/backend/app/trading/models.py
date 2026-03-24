from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


STRATEGY_LITERAL = Literal[
    "put_credit", "call_credit", "put_debit", "call_debit",
    "iron_condor", "butterfly_debit",
]


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
    strategy: STRATEGY_LITERAL
    underlying: str
    expiration: str
    quantity: int
    order_type: Literal["LIMIT"] = "LIMIT"
    limit_price: float
    price_effect: Literal["CREDIT", "DEBIT"]
    time_in_force: Literal["DAY", "GTC"]
    # Supports 2-leg spreads AND 4-leg condors/butterflies
    legs: list[OrderLeg] = Field(min_length=2, max_length=4)
    estimated_max_profit: ProfitLossEstimate
    estimated_max_loss: ProfitLossEstimate
    created_at: datetime
    asof_quote_ts: datetime
    asof_chain_ts: datetime


class PreviewLeg(BaseModel):
    """Lightweight leg input for multi-leg preview requests."""
    strike: float
    side: Literal["BUY_TO_OPEN", "SELL_TO_OPEN", "buy_to_open", "sell_to_open",
                   "buy", "sell"]
    option_type: Literal["put", "call"]
    quantity: int = 1
    # Exact OCC symbol from option chain; preferred over reconstructed symbol
    option_symbol: str | None = None


class TradingPreviewRequest(BaseModel):
    symbol: str
    strategy: STRATEGY_LITERAL
    expiration: str
    # 2-leg spreads: short_strike/long_strike (required)
    # Multi-leg (iron_condor, butterfly): legs array (short/long optional)
    short_strike: float | None = None
    long_strike: float | None = None
    legs: list[PreviewLeg] | None = None
    quantity: int = Field(ge=1)
    limit_price: float = Field(gt=0)
    time_in_force: Literal["DAY", "GTC"] = "DAY"
    mode: Literal["paper", "live"] = "paper"
    trace_id: str | None = None


class OrderPreviewResponse(BaseModel):
    ticket: OrderTicket
    checks: dict[str, Any]
    warnings: list[str]
    confirmation_token: str
    expires_at: datetime
    trace_id: str | None = None
    # Tradier preview response (from POST /orders with preview=true)
    tradier_preview: dict[str, Any] | None = None
    tradier_preview_error: str | None = None
    payload_sent: dict[str, Any] | None = None
    # Risk policy check (Phase 1 — warnings only)
    policy_warnings: list[dict[str, str]] = []
    policy_status: str = "clear"


class TradingSubmitRequest(BaseModel):
    ticket_id: str
    confirmation_token: str
    idempotency_key: str = Field(min_length=6)
    mode: Literal["paper", "live"] = "paper"
    trace_id: str | None = None


class OrderSubmitResponse(BaseModel):
    broker: Literal["paper", "tradier"]
    status: Literal["ACCEPTED", "REJECTED", "WORKING", "FILLED", "DRY_RUN"]
    broker_order_id: str
    message: str
    created_at: datetime
    account_mode_used: Literal["paper", "live"] | None = None
    trace_id: str | None = None
    tradier_raw_status: str | None = None
    dry_run: bool = False
    # ── Destination metadata (NEW) ─────────────────────────────
    destination: Literal["paper", "live"] | None = None
    destination_label: str | None = None
    dev_mode_forced_paper: bool = False


class BrokerResult(BaseModel):
    broker: Literal["paper", "tradier"]
    status: Literal["ACCEPTED", "REJECTED", "WORKING", "FILLED", "DRY_RUN"]
    broker_order_id: str
    message: str
    raw: dict[str, Any] | None = None


# ── Close-order models ───────────────────────────────────────────────

class CloseOrderLeg(BaseModel):
    """Leg from the close_order_builder output."""
    option_symbol: str
    side: Literal[
        "buy_to_close", "sell_to_close",
        "BUY_TO_CLOSE", "SELL_TO_CLOSE",
    ]
    quantity: int = Field(ge=1)
    strike: float | None = None
    option_type: Literal["put", "call"] | None = None


class CloseOrderPreviewRequest(BaseModel):
    """Preview a close order built by the active trade pipeline."""
    order_type: Literal["multileg", "equity"]
    symbol: str
    legs: list[CloseOrderLeg] | None = None
    limit_price: float | None = None
    price_effect: Literal["credit", "debit", "CREDIT", "DEBIT"] | None = None
    time_in_force: Literal["DAY", "GTC", "day", "gtc"] = "DAY"
    # Equity-only fields
    side: Literal["sell"] | None = None
    quantity: int | None = None
    # Context
    mode: Literal["paper", "live"] = "paper"
    trace_id: str | None = None


class CloseOrderPreviewResponse(BaseModel):
    """Preview result for a close order."""
    ok: bool
    payload_sent: dict[str, Any] | None = None
    tradier_preview: dict[str, Any] | None = None
    tradier_preview_error: str | None = None
    description: str | None = None
    trace_id: str | None = None


class CloseOrderSubmitRequest(BaseModel):
    """Submit a close order (the same payload that was previewed)."""
    order_type: Literal["multileg", "equity"]
    symbol: str
    legs: list[CloseOrderLeg] | None = None
    limit_price: float | None = None
    price_effect: Literal["credit", "debit", "CREDIT", "DEBIT"] | None = None
    time_in_force: Literal["DAY", "GTC", "day", "gtc"] = "DAY"
    side: Literal["sell"] | None = None
    quantity: int | None = None
    mode: Literal["paper", "live"] = "paper"
    trace_id: str | None = None


class CloseOrderSubmitResponse(BaseModel):
    """Execution result for a close order."""
    ok: bool
    broker: str | None = None
    status: str | None = None
    broker_order_id: str | None = None
    message: str | None = None
    dry_run: bool = False
    trace_id: str | None = None
