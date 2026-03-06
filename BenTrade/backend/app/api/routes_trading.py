from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any
import logging

from app.config import get_settings, set_tradier_execution_enabled
from app.trading.execution_validator import validate_trade_for_execution
from app.trading.models import OrderPreviewResponse, OrderSubmitResponse, TradingPreviewRequest, TradingSubmitRequest
from app.trading.tradier_order_builder import build_multileg_order
from app.utils.http import UpstreamError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.get("/status")
async def trading_status(request: Request) -> dict:
    """Return current trading capability flags for the frontend.

    Single source of truth: ``tradier_execution_enabled``.
    dry_run is its inverse.
    """
    settings = request.app.state.trading_service.settings
    exec_enabled = settings.TRADIER_EXECUTION_ENABLED
    is_dev = settings.ENVIRONMENT == "development"

    # Credential diagnostics (redacted — last 4 chars only)
    live_key_last4 = (settings.TRADIER_API_KEY_LIVE or "")[-4:] or None
    live_acct_last4 = (settings.TRADIER_ACCOUNT_ID_LIVE or "")[-4:] or None
    paper_key_last4 = (settings.TRADIER_API_KEY_PAPER or "")[-4:] or None
    paper_acct_last4 = (settings.TRADIER_ACCOUNT_ID_PAPER or "")[-4:] or None

    from app.trading.tradier_credentials import get_tradier_base_url
    paper_base_url = get_tradier_base_url(settings.TRADIER_ENV_PAPER)
    live_base_url = get_tradier_base_url(settings.TRADIER_ENV_LIVE)

    return {
        "tradier_execution_enabled": exec_enabled,
        "dry_run": not exec_enabled,
        "environment": settings.TRADIER_ENV,
        "data_source": "LIVE",
        "paper_configured": bool(settings.TRADIER_API_KEY_PAPER and settings.TRADIER_ACCOUNT_ID_PAPER),
        "development_mode": is_dev,
        "bentrade_environment": settings.ENVIRONMENT,
        "live_blocked": is_dev,
        "credentials": {
            "live_key_last4": live_key_last4,
            "live_acct_last4": live_acct_last4,
            "live_env": settings.TRADIER_ENV_LIVE,
            "live_base_url": live_base_url,
            "paper_key_last4": paper_key_last4,
            "paper_acct_last4": paper_acct_last4,
            "paper_env": settings.TRADIER_ENV_PAPER,
            "paper_base_url": paper_base_url,
        },
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
    print("=== PREVIEW ROUTE HIT ===")  # DIAGNOSTIC
    settings = request.app.state.trading_service.settings
    trace_id = payload.trace_id or "unknown"
    logger.info(
        "event=preview_request trace_id=%s symbol=%s strategy=%s "
        "expiration=%s short_strike=%s long_strike=%s legs=%d mode=%s "
        "execution_enabled=%s",
        trace_id, payload.symbol, payload.strategy,
        payload.expiration, payload.short_strike, payload.long_strike,
        len(payload.legs) if payload.legs else 0,
        payload.mode, settings.TRADIER_EXECUTION_ENABLED,
    )
    try:
        result = await request.app.state.trading_service.preview(payload)
        logger.info(
            "event=preview_ok trace_id=%s ticket_id=%s",
            trace_id, result.ticket.id,
        )
        return result
    except HTTPException:
        raise  # let FastAPI handle these normally
    except UpstreamError as exc:
        # Tradier or other upstream service returned an error —
        # surface the FULL response body so the UI can show it.
        logger.exception(
            "event=preview_upstream_error trace_id=%s error=%s details=%s",
            trace_id, exc, exc.details,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"Tradier API error: {exc}",
                "trace_id": trace_id,
                "upstream_status": exc.details.get("status_code"),
                "upstream_body": exc.details.get("body", ""),
                "upstream_url": exc.details.get("url", ""),
            },
        ) from exc
    except Exception as exc:
        logger.exception(
            "event=preview_unhandled_error trace_id=%s error=%s", trace_id, exc,
        )
        # Include exception class name for debugging
        raise HTTPException(
            status_code=500,
            detail={
                "message": f"Preview failed: {type(exc).__name__}: {exc}",
                "trace_id": trace_id,
                "error_type": type(exc).__name__,
            },
        ) from exc


@router.post("/submit", response_model=OrderSubmitResponse)
async def submit(payload: TradingSubmitRequest, request: Request) -> OrderSubmitResponse:
    trace_id = payload.trace_id or "unknown"
    try:
        return await request.app.state.trading_service.submit(payload)
    except HTTPException:
        raise
    except UpstreamError as exc:
        logger.exception(
            "event=submit_upstream_error trace_id=%s error=%s details=%s",
            trace_id, exc, exc.details,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"Tradier API error: {exc}",
                "trace_id": trace_id,
                "upstream_status": exc.details.get("status_code"),
                "upstream_body": exc.details.get("body", ""),
            },
        ) from exc
    except Exception as exc:
        logger.exception(
            "event=submit_unhandled_error trace_id=%s error=%s", trace_id, exc,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "message": f"Submit failed: {type(exc).__name__}: {exc}",
                "trace_id": trace_id,
                "error_type": type(exc).__name__,
            },
        ) from exc


@router.get("/orders")
async def list_orders(request: Request) -> list[dict]:
    return request.app.state.trading_repository.list_orders()


@router.get("/orders/{order_id}")
async def get_order(order_id: str, request: Request) -> dict:
    order = request.app.state.trading_repository.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.get("/orders/{order_id}/tradier-status")
async def get_tradier_order_status(order_id: str, request: Request) -> dict:
    """Fetch real-time order status from Tradier for a submitted order.

    Used for reconciliation — checks Tradier's actual order state.
    """
    trading_service = request.app.state.trading_service
    broker = trading_service.live_broker

    # Try to find the order record to get the mode/creds context
    order_record = request.app.state.trading_repository.get_order(order_id)
    mode = "paper"
    if order_record:
        mode = order_record.get("request_mode", "paper")

    try:
        from app.trading.tradier_credentials import resolve_tradier_credentials
        settings = trading_service.settings
        creds = resolve_tradier_credentials(
            purpose="EXECUTION",
            account_mode=mode,
            live_api_key=settings.TRADIER_API_KEY_LIVE,
            live_account_id=settings.TRADIER_ACCOUNT_ID_LIVE,
            live_env=settings.TRADIER_ENV_LIVE,
            paper_api_key=settings.TRADIER_API_KEY_PAPER,
            paper_account_id=settings.TRADIER_ACCOUNT_ID_PAPER,
            paper_env=settings.TRADIER_ENV_PAPER,
        )
        result = await broker.get_order_status(order_id, creds=creds)
        return {"ok": True, "order": result.get("order", result), "mode": mode}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "mode": mode}


@router.get("/runtime-config")
async def get_runtime_config() -> dict:
    """GET current runtime config — single execution flag."""
    settings = get_settings()
    return {
        "tradier_execution_enabled": settings.TRADIER_EXECUTION_ENABLED,
        "source": "runtime",
    }


class RuntimeConfigPatch(BaseModel):
    tradier_execution_enabled: bool


@router.patch("/runtime-config")
async def patch_runtime_config(body: RuntimeConfigPatch) -> dict:
    """PATCH runtime config — toggle execution on/off, persisted to disk."""
    settings = set_tradier_execution_enabled(body.tradier_execution_enabled)
    return {
        "ok": True,
        "tradier_execution_enabled": settings.TRADIER_EXECUTION_ENABLED,
        "dry_run": not settings.TRADIER_EXECUTION_ENABLED,
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
