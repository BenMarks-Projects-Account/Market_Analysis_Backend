"""BenTrade — Stock Strategy Routes

Dedicated routes for the four stock strategy scanners and equity execution.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.trading.stock_models import StockExecutionRequest, StockExecutionResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stocks", tags=["stock-strategies"])

_STRATEGIES = [
    "stock_pullback_swing",
    "stock_momentum_breakout",
    "stock_mean_reversion",
    "stock_volatility_expansion",
]

# ── Default top-N for stock engine ─────────────────────────────────
_STOCK_ENGINE_TOP_N = 9


def _stub(strategy_id: str) -> dict:
    return {
        "strategy_id": strategy_id,
        "status": "not_implemented",
        "candidates": [],
    }


@router.get("/pullback-swing")
async def get_pullback_swing(request: Request) -> dict:
    """Run the Pullback Swing scanner and return scored candidates."""
    svc = getattr(request.app.state, "pullback_swing_service", None)
    if svc is None:
        return _stub("stock_pullback_swing")
    try:
        return await svc.scan()
    except Exception as exc:
        logger.exception("event=pullback_swing_scan_error error=%s", exc)
        return {
            "strategy_id": "stock_pullback_swing",
            "status": "error",
            "error": str(exc)[:200],
            "candidates": [],
        }


@router.get("/momentum-breakout")
async def get_momentum_breakout(request: Request) -> dict:
    """Run the Momentum Breakout scanner and return scored candidates."""
    svc = getattr(request.app.state, "momentum_breakout_service", None)
    if svc is None:
        return _stub("stock_momentum_breakout")
    try:
        return await svc.scan()
    except Exception as exc:
        logger.exception("event=momentum_breakout_scan_error error=%s", exc)
        return {
            "strategy_id": "stock_momentum_breakout",
            "status": "error",
            "error": str(exc)[:200],
            "candidates": [],
        }


@router.get("/mean-reversion")
async def get_mean_reversion(request: Request) -> dict:
    """Run the Mean Reversion Bounce scanner and return scored candidates."""
    svc = getattr(request.app.state, "mean_reversion_service", None)
    if svc is None:
        return _stub("stock_mean_reversion")
    try:
        return await svc.scan()
    except Exception as exc:
        logger.exception("event=mean_reversion_scan_error error=%s", exc)
        return {
            "strategy_id": "stock_mean_reversion",
            "status": "error",
            "error": str(exc)[:200],
            "candidates": [],
        }


@router.get("/volatility-expansion")
async def get_volatility_expansion(request: Request) -> dict:
    """Run the Volatility Expansion scanner and return scored candidates."""
    svc = getattr(request.app.state, "volatility_expansion_service", None)
    if svc is None:
        return _stub("stock_volatility_expansion")
    try:
        return await svc.scan()
    except Exception as exc:
        logger.exception("event=volatility_expansion_scan_error error=%s", exc)
        return {
            "strategy_id": "stock_volatility_expansion",
            "status": "error",
            "error": str(exc)[:200],
            "candidates": [],
        }


# ── Stock Engine — aggregate all scanners ─────────────────────────

@router.get("/engine")
async def get_stock_engine(request: Request) -> dict:
    """Run ALL stock scanners, aggregate and rank results, return top 9.

    Ranking order (descending):
      1. composite_score (0-100)
      2. model recommendation strength (BUY > HOLD > PASS)
      3. avg_dollar_volume (liquidity proxy)
      4. symbol + strategy_id (deterministic tie-breaker)

    Partial failures: if one scanner errors, the others still contribute.
    Warnings are included in the response.
    """
    svc = getattr(request.app.state, "stock_engine_service", None)
    if svc is None:
        return {
            "engine": "stock_engine",
            "status": "error",
            "error": "Stock engine service not initialised",
            "candidates": [],
            "scanners": [],
            "warnings": ["stock_engine_service not initialised"],
            "top_n": _STOCK_ENGINE_TOP_N,
            "total_candidates": 0,
            "scan_time_seconds": 0,
        }
    try:
        return await svc.scan(top_n=_STOCK_ENGINE_TOP_N)
    except Exception as exc:
        logger.exception("event=stock_engine_error error=%s", exc)
        return {
            "engine": "stock_engine",
            "status": "error",
            "error": str(exc)[:200],
            "candidates": [],
            "scanners": [],
            "warnings": [str(exc)[:200]],
            "top_n": _STOCK_ENGINE_TOP_N,
            "total_candidates": 0,
            "scan_time_seconds": 0,
        }


# ── Stock execution endpoint ──────────────────────────────────────

@router.post("/execute", response_model=StockExecutionResponse)
async def execute_stock_trade(
    req: StockExecutionRequest, request: Request,
) -> StockExecutionResponse:
    """Execute an equity (stock_long) order via Tradier.

    Default account_mode is "paper".  Live requires
    TRADIER_EXECUTION_ENABLED=true and confirm_live=true.
    """
    svc = getattr(request.app.state, "stock_execution_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="Stock execution service not initialised",
        )
    try:
        return await svc.execute(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("event=stock_execute_error symbol=%s error=%s", req.symbol, exc)
        raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc


@router.get("/execute/status")
async def stock_execution_status(request: Request) -> dict:
    """Return execution capability flags for stock trading UI."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return {"stock_execution_enabled": False}
    return {
        "stock_execution_enabled": True,
        "account_mode_default": "paper",
        "tradier_execution_enabled": settings.TRADIER_EXECUTION_ENABLED,
        "dry_run": not settings.TRADIER_EXECUTION_ENABLED,
        "paper_configured": bool(
            settings.TRADIER_API_KEY_PAPER and settings.TRADIER_ACCOUNT_ID_PAPER
        ),
    }
