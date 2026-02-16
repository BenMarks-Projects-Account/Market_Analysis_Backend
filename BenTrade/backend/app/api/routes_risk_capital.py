from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/risk", tags=["risk-capital"])


class RiskPolicyUpdateRequest(BaseModel):
    portfolio_size: float | None = None
    max_total_risk_pct: float | None = None
    max_symbol_risk_pct: float | None = None
    max_trade_risk_pct: float | None = None
    max_dte: int | None = None
    min_cash_reserve_pct: float | None = None
    max_position_size_pct: float | None = None
    default_contracts_cap: int | None = None
    max_risk_per_trade: float | None = None
    max_risk_total: float | None = None
    max_concurrent_trades: int | None = None
    max_risk_per_underlying: float | None = None
    max_same_expiration_risk: float | None = None
    max_short_strike_distance_sigma: float | None = None
    min_open_interest: int | None = None
    min_volume: int | None = None
    max_bid_ask_spread_pct: float | None = None
    min_pop: float | None = None
    min_ev_to_risk: float | None = None
    min_return_on_risk: float | None = None
    max_iv_rv_ratio_for_buying: float | None = None
    min_iv_rv_ratio_for_selling: float | None = None
    notes: str | None = None


@router.get("/policy")
async def get_risk_policy(request: Request) -> dict[str, Any]:
    policy = request.app.state.risk_policy_service.get_policy()
    return {"policy": policy}


@router.put("/policy")
async def put_risk_policy(payload: RiskPolicyUpdateRequest, request: Request) -> dict[str, Any]:
    policy = request.app.state.risk_policy_service.save_policy(payload.model_dump(exclude_none=False))
    return {"ok": True, "policy": policy}


@router.get("/snapshot")
async def get_risk_snapshot(request: Request) -> dict[str, Any]:
    return await request.app.state.risk_policy_service.build_snapshot(request)
