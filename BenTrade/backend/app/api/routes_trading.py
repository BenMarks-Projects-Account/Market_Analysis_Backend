from fastapi import APIRouter, HTTPException, Request

from app.config import set_live_runtime_enabled
from app.trading.models import OrderPreviewResponse, OrderSubmitResponse, TradingPreviewRequest, TradingSubmitRequest
from app.utils.http import UpstreamError

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.get("/status")
async def trading_status(request: Request) -> dict:
    """Return current trading capability flags for the frontend."""
    settings = request.app.state.trading_service.settings
    return {
        "enable_live_trading": settings.ENABLE_LIVE_TRADING,
        "live_runtime_enabled": settings.LIVE_TRADING_RUNTIME_ENABLED,
        "trading_live_enabled": settings.TRADING_LIVE_ENABLED,
        "dry_run": settings.TRADIER_DRY_RUN_LIVE,
        "environment": settings.TRADIER_ENV,
        "data_source": "LIVE",
        "paper_configured": bool(settings.TRADIER_API_KEY_PAPER and settings.TRADIER_ACCOUNT_ID_PAPER),
        "trade_capability_enabled": settings.ENABLE_LIVE_TRADING and settings.LIVE_TRADING_RUNTIME_ENABLED,
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
    }


@router.post("/kill-switch/off")
async def kill_switch_off() -> dict:
    settings = set_live_runtime_enabled(False)
    return {
        "ok": True,
        "live_runtime_enabled": settings.LIVE_TRADING_RUNTIME_ENABLED,
        "enable_live_trading": settings.ENABLE_LIVE_TRADING,
    }
