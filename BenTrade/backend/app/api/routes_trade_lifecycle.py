from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/lifecycle", tags=["trade-lifecycle"])


class LifecycleEventRequest(BaseModel):
    event: str
    trade_key: str | None = None
    source: str | None = "unknown"
    reason: str | None = None
    note: str | None = None
    trade: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None


@router.post("/event")
async def post_lifecycle_event(payload: LifecycleEventRequest, request: Request) -> dict[str, Any]:
    body = payload.model_dump(exclude_none=True)
    event_payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}

    trade_payload = body.get("trade") if isinstance(body.get("trade"), dict) else {}
    merged_payload = dict(trade_payload)
    merged_payload.update(event_payload)

    try:
        event = request.app.state.trade_lifecycle_service.append_event(
            event=payload.event,
            trade_key_value=payload.trade_key,
            source=payload.source,
            payload=merged_payload,
            reason=payload.reason,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist lifecycle event: {exc}") from exc

    latest = request.app.state.trade_lifecycle_service.get_trade_history(str(event.get("trade_key") or ""))
    return {"ok": True, "event": event, "trade": latest}


@router.get("/trades")
async def get_lifecycle_trades(
    request: Request,
    state: str | None = Query(default=None, description="Lifecycle state filter"),
) -> dict[str, Any]:
    rows = request.app.state.trade_lifecycle_service.get_trades(state=state)
    return {"state": state, "trades": rows}


@router.get("/trades/{trade_key}")
async def get_lifecycle_trade_detail(trade_key: str, request: Request) -> dict[str, Any]:
    detail = request.app.state.trade_lifecycle_service.get_trade_history(trade_key)
    if not detail.get("history"):
        raise HTTPException(status_code=404, detail="trade not found")
    return detail
