"""
Stock Execution Models
======================

Pydantic request / response contracts for equity (stock) order execution.
Separate from the option-spread models in trading.models so the two
order flows remain independently evolvable.

Trade type:
  stock_long  — buy equity shares

Broker:
  Tradier  (equity class order)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ── Allowed stock strategy IDs ────────────────────────────────────
_VALID_STOCK_STRATEGIES = frozenset([
    "stock_pullback_swing",
    "stock_momentum_breakout",
    "stock_mean_reversion",
    "stock_volatility_expansion",
    "company_evaluator_buy",
])

_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,7}$")

# Default maximum single-order equity quantity.
# Can be overridden via env STOCK_MAX_QTY.
STOCK_MAX_QTY_DEFAULT = 500


# ── Request ───────────────────────────────────────────────────────

class StockExecutionRequest(BaseModel):
    """Payload sent by the frontend when the user confirms a stock trade."""

    trade_key: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1, max_length=8)
    strategy_id: str
    trade_type: Literal["stock_long"] = "stock_long"
    qty: int = Field(..., ge=1)
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None
    time_in_force: Literal["day", "gtc"] = "day"
    account_mode: Literal["paper", "live"] = "paper"

    # Reference / audit fields
    price_reference: float | None = None  # last price shown on card
    as_of: str | None = None              # timestamp from frontend
    engine: dict[str, Any] | None = None  # composite_score, thesis[], etc.
    metrics: dict[str, Any] | None = None # strategy scanner metrics
    client_request_id: str | None = None  # optional idempotency key

    # Explicit live-mode confirmation gate
    confirm_live: bool = False

    # ── Validators ────────────────────────────────────────────

    @field_validator("symbol")
    @classmethod
    def _validate_symbol(cls, v: str) -> str:
        v = v.strip().upper()
        if not _SYMBOL_PATTERN.match(v):
            raise ValueError(
                f"Invalid symbol '{v}' — must be 1-8 uppercase alpha-numeric chars"
            )
        return v

    @field_validator("strategy_id")
    @classmethod
    def _validate_strategy(cls, v: str) -> str:
        if v not in _VALID_STOCK_STRATEGIES:
            raise ValueError(
                f"Unknown strategy '{v}'. Valid: {sorted(_VALID_STOCK_STRATEGIES)}"
            )
        return v

    @field_validator("limit_price")
    @classmethod
    def _validate_limit_price(cls, v: float | None, info) -> float | None:
        ot = info.data.get("order_type", "market")
        if ot == "limit":
            if v is None or v <= 0:
                raise ValueError("limit_price must be > 0 for limit orders")
        elif ot == "market":
            if v is not None:
                raise ValueError("limit_price must be null for market orders")
        return v


# ── Response ──────────────────────────────────────────────────────

class StockExecutionResponse(BaseModel):
    """Normalised response returned to the frontend after order submission."""

    status: Literal["submitted", "filled", "rejected", "error"]
    broker: Literal["tradier", "paper"] = "tradier"
    account_mode: Literal["paper", "live"]
    order_id: str | None = None
    symbol: str
    qty: int
    order_type: Literal["market", "limit"]
    limit_price: float | None = None
    submitted_at: str  # ISO-8601 timestamp
    message: str
    raw_broker_response: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    trade_key: str | None = None
    client_request_id: str | None = None
