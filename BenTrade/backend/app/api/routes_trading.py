from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any

from app.config import set_live_runtime_enabled
from app.trading.execution_validator import validate_trade_for_execution
from app.trading.models import OrderPreviewResponse, OrderSubmitResponse, TradingPreviewRequest, TradingSubmitRequest
from app.trading.tradier_order_builder import build_multileg_order
from app.utils.http import UpstreamError

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.get("/status")
async def trading_status(request: Request) -> dict:
    """Return current trading capability flags for the frontend.

    ``trade_capability_enabled`` reflects the runtime toggle only so the
    UI kill-switch works regardless of the env-var master gate.  The
    backend enforces ``ENABLE_LIVE_TRADING`` separately at order
    submission time (see ``TradingService.submit``).
    """
    settings = request.app.state.trading_service.settings
    return {
        "enable_live_trading": settings.ENABLE_LIVE_TRADING,
        "live_runtime_enabled": settings.LIVE_TRADING_RUNTIME_ENABLED,
        "trading_live_enabled": settings.TRADING_LIVE_ENABLED,
        "dry_run": settings.TRADIER_DRY_RUN_LIVE,
        "environment": settings.TRADIER_ENV,
        "data_source": "LIVE",
        "paper_configured": bool(settings.TRADIER_API_KEY_PAPER and settings.TRADIER_ACCOUNT_ID_PAPER),
        # Runtime toggle — controls the UI trade-cap switch.
        # ENABLE_LIVE_TRADING is enforced at submission, not here.
        "trade_capability_enabled": settings.LIVE_TRADING_RUNTIME_ENABLED,
    }


@router.get("/test-connection")
async def test_connection(request: Request) -> dict:
    settings = request.app.state.trading_service.settings
    try:
        await request.app.state.tradier_client.get_balances()
        return {
            "status": "ok",
            "account_id": settings.TRADIER_ACCOUNT_ID,
            "environment": settings.TRADIER_ENV,
        }
    except (UpstreamError, Exception) as exc:
        return {
            "status": "error",
            "message": str(exc),
            "environment": settings.TRADIER_ENV,
        }


@router.post("/preview", response_model=OrderPreviewResponse)
async def preview(payload: TradingPreviewRequest, request: Request) -> OrderPreviewResponse:
    return await request.app.state.trading_service.preview(payload)


@router.post("/submit", response_model=OrderSubmitResponse)
async def submit(payload: TradingSubmitRequest, request: Request) -> OrderSubmitResponse:
    return await request.app.state.trading_service.submit(payload)


@router.get("/orders")
async def list_orders(request: Request) -> list[dict]:
    return request.app.state.trading_repository.list_orders()


@router.get("/orders/{order_id}")
async def get_order(order_id: str, request: Request) -> dict:
    order = request.app.state.trading_repository.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/kill-switch/on")
async def kill_switch_on() -> dict:
    settings = set_live_runtime_enabled(True)
    return {
        "ok": True,
        "live_runtime_enabled": settings.LIVE_TRADING_RUNTIME_ENABLED,
        "enable_live_trading": settings.ENABLE_LIVE_TRADING,
        "trade_capability_enabled": settings.LIVE_TRADING_RUNTIME_ENABLED,
    }


@router.post("/kill-switch/off")
async def kill_switch_off() -> dict:
    settings = set_live_runtime_enabled(False)
    return {
        "ok": True,
        "live_runtime_enabled": settings.LIVE_TRADING_RUNTIME_ENABLED,
        "enable_live_trading": settings.ENABLE_LIVE_TRADING,
        "trade_capability_enabled": settings.LIVE_TRADING_RUNTIME_ENABLED,
    }


# ── Pre-flight validation endpoint ──────────────────────────────────


@router.post("/validate")
async def validate_trade(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the execution pre-flight validator against a trade payload.

    Returns { valid, blocking_errors, warnings }.
    The frontend uses this to gate the Execute / Preview buttons.
    """
    result = validate_trade_for_execution(payload)
    return result


# ── Tradier payload builder (inspect only, no submission) ────────────


class BuildPayloadRequest(BaseModel):
    trade: dict[str, Any]
    account_mode: str = "paper"
    limit_price: float | None = None
    quantity: int = 1
    time_in_force: str = "day"


@router.post("/build-payload")
async def build_payload(req: BuildPayloadRequest) -> dict[str, Any]:
    """Build a Tradier multi-leg order payload for inspection.

    Does NOT submit the order.  Returns the structured payload,
    metadata, and leg details for audit purposes.
    """
    # Validate first
    validation = validate_trade_for_execution(req.trade)
    if not validation["valid"]:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Trade failed pre-flight validation — cannot build payload",
                "blocking_errors": validation["blocking_errors"],
                "warnings": validation["warnings"],
            },
        )

    try:
        result = build_multileg_order(
            req.trade,
            account_mode=req.account_mode,
            limit_price=req.limit_price,
            quantity=req.quantity,
            time_in_force=req.time_in_force,
            tag=req.trade.get("trade_key"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "payload": result["payload"],
        "metadata": result["metadata"],
        "legs_used": result["legs_used"],
        "validation": validation,
    }
