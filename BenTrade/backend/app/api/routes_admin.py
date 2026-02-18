from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.services.data_workbench_service import DataWorkbenchService
from app.services.validation_events import ValidationEventsService, build_rollups
from app.utils.trade_key import canonicalize_trade_key

router = APIRouter()


def _get_data_workbench_service(request: Request) -> DataWorkbenchService:
    service = getattr(request.app.state, "data_workbench_service", None)
    if isinstance(service, DataWorkbenchService):
        return service

    results_dir = Path(getattr(request.app.state, "results_dir"))
    events_service = getattr(request.app.state, "validation_events", None)
    if not isinstance(events_service, ValidationEventsService):
        events_service = ValidationEventsService(results_dir=results_dir)

    service = DataWorkbenchService(results_dir=results_dir, validation_events=events_service)
    request.app.state.data_workbench_service = service
    return service


def _workbench_payload_v2(payload: dict) -> dict:
    trade = payload.get("trade") if isinstance(payload.get("trade"), dict) else {}
    trade_json = payload.get("trade_json") if isinstance(payload.get("trade_json"), dict) else {}
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    warnings = trade_json.get("validation_warnings") if isinstance(trade_json.get("validation_warnings"), list) else []

    return {
        "trade_key": payload.get("trade_key") or trade.get("trade_key") or "",
        "strategy_id": str(trade.get("strategy_id") or trade.get("spread_type") or trade.get("strategy") or "").strip().lower() or None,
        "input_snapshot": trade_json.get("input_snapshot") if isinstance(trade_json.get("input_snapshot"), dict) else None,
        "trade_output": trade,
        "validation_warnings": warnings,
        "sources": sources,
        "trade": trade,
        "trade_json": trade_json,
    }


@router.get("/data-health")
async def get_data_health(request: Request) -> dict:
    source_health: dict = {}
    try:
        source_health = request.app.state.base_data_service.get_source_health_snapshot()
    except Exception:
        source_health = {}

    events_service = getattr(request.app.state, "validation_events", None)
    if not isinstance(events_service, ValidationEventsService):
        events_service = ValidationEventsService(results_dir=request.app.state.results_dir)

    events = events_service.read_recent(limit=200)
    rollups = build_rollups(events)

    return {
        "source_health": source_health,
        "validation_events": events,
        "rollups": rollups,
    }


@router.get("/data-workbench/trade/{trade_key}")
async def get_data_workbench_trade(trade_key: str, request: Request):
    service = _get_data_workbench_service(request)
    lookup = service.resolve_trade_with_trace(trade_key)
    payload = lookup.get("record")
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "DATA_WORKBENCH_TRADE_NOT_FOUND",
                    "message": "Trade not found in latest scanner reports or trade ledger",
                    "details": {
                        "trade_key": lookup.get("normalized_key") or canonicalize_trade_key(trade_key) or str(trade_key or "").strip(),
                        "original_key": lookup.get("original_key") or str(trade_key or "").strip(),
                        "normalized_key": lookup.get("normalized_key") or "",
                        "attempted_keys": lookup.get("attempted_keys") or [],
                        "closest_matches": lookup.get("closest_matches") or [],
                    },
                }
            },
        )
    return _workbench_payload_v2(payload)


@router.get("/data-workbench/trade")
async def get_data_workbench_trade_query(trade_key: str = Query(...), request: Request = None):
    service = _get_data_workbench_service(request)
    lookup = service.resolve_trade_with_trace(trade_key)
    payload = lookup.get("record")
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "DATA_WORKBENCH_TRADE_NOT_FOUND",
                    "message": "Trade not found in latest scanner reports or trade ledger",
                    "details": {
                        "trade_key": lookup.get("normalized_key") or canonicalize_trade_key(trade_key) or str(trade_key or "").strip(),
                        "original_key": lookup.get("original_key") or str(trade_key or "").strip(),
                        "normalized_key": lookup.get("normalized_key") or "",
                        "attempted_keys": lookup.get("attempted_keys") or [],
                        "closest_matches": lookup.get("closest_matches") or [],
                    },
                }
            },
        )
    return _workbench_payload_v2(payload)


@router.get("/data-workbench/search")
async def search_data_workbench_trades(
    request: Request,
    underlying: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    service = _get_data_workbench_service(request)
    rows = service.search_recent(underlying=underlying, limit=limit)
    return {
        "items": rows,
        "count": len(rows),
    }
