from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.services.data_workbench_service import DataWorkbenchService
from app.services.platform_settings import PlatformSettings
from app.services.validation_events import ValidationEventsService, build_rollups
from app.utils.trade_key import canonicalize_trade_key

logger = logging.getLogger(__name__)

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

    # Include model endpoint health
    from app.services.model_health_service import check_model_health
    model_health = check_model_health()
    model_models = model_health.get("models_loaded") or []
    source_health["model_endpoint"] = {
        "status": "green" if model_health["status"] == "healthy" else "red",
        "message": (
            (model_models[0] if model_models else "No models loaded")
            + f" · {model_health.get('latency_ms', 0)} ms"
            + (f" · {model_health.get('source_name', '')}" if model_health.get("source_name") else "")
        ),
        "last_http": 200 if model_health["status"] == "healthy" else None,
    }

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


# ── Platform Data Source ──────────────────────────────────────────────────


def _get_platform_settings(request: Request) -> PlatformSettings:
    ps = getattr(request.app.state, "platform_settings", None)
    if isinstance(ps, PlatformSettings):
        return ps
    raise RuntimeError("platform_settings not initialised on app.state")


@router.get("/platform/data-source")
async def get_platform_data_source(request: Request) -> dict:
    """Return current platform data-source mode + metadata."""
    ps = _get_platform_settings(request)
    state = ps.get_state()
    # Include snapshot availability info
    snapshot_dir = getattr(request.app.state, "snapshot_dir", None)
    has_snapshots = False
    available_snapshots: list[dict] = []
    if snapshot_dir:
        snapshot_path = Path(snapshot_dir)
        if snapshot_path.is_dir():
            has_snapshots = any(snapshot_path.rglob("chain_*.json"))
            # List manifest-based snapshots
            try:
                from app.services.snapshot_capture_service import SnapshotCaptureService
                available_snapshots = SnapshotCaptureService.list_snapshots(snapshot_path)
            except Exception:
                pass
    state["has_snapshots"] = has_snapshots
    state["available_snapshots"] = available_snapshots
    # Include default scanner symbols so the frontend capture button knows what to request
    from app.services.strategy_service import DEFAULT_SCANNER_SYMBOLS
    state["scanner_symbols"] = list(DEFAULT_SCANNER_SYMBOLS)
    return state


@router.put("/platform/data-source")
async def set_platform_data_source(request: Request) -> dict:
    """Set platform data-source mode (``live`` | ``snapshot``).

    Body: ``{"data_source_mode": "live" | "snapshot"}``
    """
    ps = _get_platform_settings(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_JSON", "message": "Request body must be valid JSON"}},
        )
    mode = body.get("data_source_mode") if isinstance(body, dict) else None
    if not mode:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "MISSING_FIELD", "message": "data_source_mode is required"}},
        )
    try:
        result = ps.set_data_source_mode(mode)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_VALUE", "message": str(exc)}},
        )
    return result


@router.post("/platform/snapshot-cleanup")
async def trigger_snapshot_cleanup(request: Request) -> dict:
    """Manually trigger snapshot retention cleanup."""
    from app.utils.snapshot import run_snapshot_cleanup

    snapshot_dir = getattr(request.app.state, "snapshot_dir", None)
    if not snapshot_dir:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "NO_SNAPSHOT_DIR", "message": "Snapshot directory not configured"}},
        )
    try:
        from app.config import get_settings
        settings = get_settings()
        retention_days = int(getattr(settings, "SNAPSHOT_RETENTION_DAYS", 7))
        removed = run_snapshot_cleanup(Path(snapshot_dir), retention_days=retention_days)
        return {"status": "ok", "removed_directories": removed}
    except Exception as exc:
        logger.error("event=snapshot_cleanup_error error=%s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "CLEANUP_ERROR", "message": str(exc)}},
        )


# ── Model Source ──────────────────────────────────────────────────────────


@router.get("/platform/model-source")
async def get_model_source_endpoint(request: Request) -> dict:
    """Return current model source and all available options."""
    from app.model_sources import MODEL_SOURCES
    from app.services.model_state import get_model_source

    active = get_model_source()
    cfg = MODEL_SOURCES.get(active, {})
    return {
        "active_source": active,
        "active_name": cfg.get("name", active),
        "active_endpoint": cfg.get("endpoint"),
        "sources": {
            key: {"name": src["name"], "enabled": src["enabled"], "endpoint": src.get("endpoint")}
            for key, src in MODEL_SOURCES.items()
        },
    }


@router.post("/platform/model-source")
async def set_model_source_endpoint(request: Request) -> dict:
    """Switch the active model source.

    Body: ``{"source": "local" | "model_machine" | "premium_online"}``

    After switching, the model health cache is reset and a fresh probe
    is performed so the caller gets the real health of the new source.
    """
    from app.services.model_state import set_model_source
    from app.model_sources import MODEL_SOURCES
    from app.services.model_health_service import check_model_health, reset_cache

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_JSON", "message": "Request body must be valid JSON"}},
        )
    source = body.get("source") if isinstance(body, dict) else None
    if not source:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "MISSING_FIELD", "message": "source is required"}},
        )
    try:
        active = set_model_source(source)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_VALUE", "message": str(exc)}},
        )
    cfg = MODEL_SOURCES.get(active, {})

    # Reset cache and run a live probe against the new source
    reset_cache()
    model_health = check_model_health(force=True)

    return {
        "success": True,
        "active_source": active,
        "active_name": cfg.get("name", active),
        "active_endpoint": cfg.get("endpoint"),
        "model_health": {
            "status": model_health.get("status"),
            "error": model_health.get("error"),
            "latency_ms": model_health.get("latency_ms"),
            "models_loaded": model_health.get("models_loaded", []),
            "checked_at": model_health.get("checked_at"),
        },
    }
