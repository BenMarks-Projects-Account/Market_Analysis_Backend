"""API routes for routing visibility / dashboard / operator control.

Endpoints (mounted under /api/admin prefix):
    GET  /routing/health    — provider health summaries
    GET  /routing/system    — global routing config/state
    GET  /routing/recent    — recent routing trace summaries
    GET  /routing/dashboard — composite payload (all three above)
    GET  /routing/execution-mode   — current execution mode + options (Step 17)
    POST /routing/execution-mode   — update execution mode (Step 17)
    POST /routing/refresh-config    — reload config from env (Step 14)
    POST /routing/refresh-providers — live-probe all providers (Step 14)
    POST /routing/refresh-runtime   — full coherent refresh (Step 14)
    POST /routing/circuit-breaker/reset  — reset circuit breaker (one or all)
    GET  /routing/circuit-breaker/status — circuit breaker status for all providers

Steps 13–17 — Distributed Model Routing / UI visibility + runtime control
               + control-plane hardening + health semantics + execution mode.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.services.routing_dashboard_service import (
    build_dashboard_payload,
    build_provider_health_summaries,
    build_routing_system_summary,
    get_recent_traces,
    refresh_routing_runtime,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/routing", tags=["routing"])

# ---------------------------------------------------------------------------
# Lightweight cooldown gate (Step 15)
# ---------------------------------------------------------------------------
# Per-endpoint minimum interval between successive calls.
# Uses monotonic clock so wall-clock adjustments can't bypass the gate.

COOLDOWN_SECONDS: float = 10.0  # minimum interval per endpoint
_last_call: dict[str, float] = {}  # endpoint key → monotonic timestamp


def _check_cooldown(endpoint_key: str) -> float | None:
    """Return remaining cooldown seconds, or *None* if clear to proceed."""
    now = time.monotonic()
    last = _last_call.get(endpoint_key)
    if last is not None:
        elapsed = now - last
        if elapsed < COOLDOWN_SECONDS:
            return round(COOLDOWN_SECONDS - elapsed, 1)
    return None


def _record_call(endpoint_key: str) -> None:
    _last_call[endpoint_key] = time.monotonic()


def _cooldown_response(remaining: float) -> JSONResponse:
    """HTTP 429 with a human-readable message and Retry-After header."""
    return JSONResponse(
        status_code=429,
        content={
            "error": "cooldown_active",
            "message": f"Please wait {remaining}s before retrying.",
            "retry_after": remaining,
        },
        headers={"Retry-After": str(int(remaining) + 1)},
    )


@router.get("/health")
async def get_routing_health(
    refresh: bool = Query(default=False, description="Live-probe providers if true"),
) -> dict:
    """Return per-provider routing health summaries."""
    providers = build_provider_health_summaries(refresh=refresh)
    return {"providers": [p.to_dict() for p in providers]}


@router.get("/system")
async def get_routing_system() -> dict:
    """Return global routing system configuration and state."""
    summary = build_routing_system_summary()
    return {"system": summary.to_dict()}


@router.get("/recent")
async def get_recent_routing_traces(
    limit: int = Query(default=20, ge=1, le=50, description="Max traces to return"),
) -> dict:
    """Return recent routing trace summaries (newest first)."""
    traces = get_recent_traces(limit=limit)
    return {"traces": traces, "count": len(traces)}


@router.get("/dashboard")
async def get_routing_dashboard(
    refresh: bool = Query(default=False, description="Live-probe providers if true"),
    recent_limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    """Return complete routing dashboard payload.

    Combines system summary, provider health, and recent traces
    in a single response for the Data Health UI.
    """
    return build_dashboard_payload(
        refresh_providers=refresh,
        recent_limit=recent_limit,
    )


# ---------------------------------------------------------------------------
# Execution mode read/write (Step 17)
# ---------------------------------------------------------------------------


@router.get("/execution-mode")
async def get_execution_mode_endpoint() -> dict:
    """Return the current execution mode, its display metadata, and all options.

    Response includes:
        selected_mode, display_label, description, routing_enabled, options[]
    """
    from app.services.execution_mode_state import get_execution_mode
    from app.services.model_routing_config import get_routing_config
    from app.services.routing_dashboard_contract import (
        build_execution_mode_options,
        execution_mode_description,
        execution_mode_display_label,
    )

    mode = get_execution_mode()
    config = get_routing_config()

    return {
        "selected_mode": mode,
        "display_label": execution_mode_display_label(mode),
        "description": execution_mode_description(mode),
        "routing_enabled": config.routing_enabled,
        "options": build_execution_mode_options(),
    }


@router.post("/execution-mode")
async def set_execution_mode_endpoint(request: Request) -> JSONResponse:
    """Update the active execution mode.

    Body: ``{"mode": "local_distributed" | "online_distributed" | ...}``

    Validates against the canonical execution mode set.
    Returns the new state on success.
    """
    remaining = _check_cooldown("execution-mode")
    if remaining is not None:
        return _cooldown_response(remaining)

    from app.services.execution_mode_state import set_execution_mode
    from app.services.model_routing_config import get_routing_config
    from app.services.routing_dashboard_contract import (
        execution_mode_description,
        execution_mode_display_label,
    )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "INVALID_JSON", "message": "Request body must be valid JSON"},
        )

    mode = body.get("mode") if isinstance(body, dict) else None
    if not mode or not isinstance(mode, str):
        return JSONResponse(
            status_code=400,
            content={"error": "MISSING_FIELD", "message": "mode is required"},
        )

    try:
        active = set_execution_mode(mode)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": "INVALID_MODE", "message": str(exc)},
        )

    _record_call("execution-mode")
    logger.info("[control] execution mode changed to '%s'", active)

    config = get_routing_config()
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "selected_mode": active,
            "display_label": execution_mode_display_label(active),
            "description": execution_mode_description(active),
            "routing_enabled": config.routing_enabled,
        },
    )


# ---------------------------------------------------------------------------
# Operator control actions (Step 14)
# ---------------------------------------------------------------------------


@router.post("/refresh-config")
async def post_refresh_config():
    """Reload routing config from environment variables.

    Re-reads all ROUTING_* env vars and replaces the cached config.
    Returns a diff summary showing which fields changed.

    Does NOT rebuild the execution gate or refresh providers.
    Use /refresh-runtime for a full coherent refresh.
    """
    remaining = _check_cooldown("refresh-config")
    if remaining is not None:
        return _cooldown_response(remaining)

    from app.services.model_routing_config import refresh_routing_config
    logger.info("[control] operator requested config refresh")
    _record_call("refresh-config")
    diff = refresh_routing_config()
    return {"action": "config_refreshed", "result": diff}


@router.post("/refresh-providers")
async def post_refresh_providers():
    """Live-probe all registered providers and return updated statuses.

    This refreshes provider health state without changing config or gate.
    Also resets circuit breaker state so probed providers get a clean slate.
    """
    remaining = _check_cooldown("refresh-providers")
    if remaining is not None:
        return _cooldown_response(remaining)

    from app.services.model_provider_registry import get_registry
    from app.services.model_router_policy import get_circuit_breaker
    logger.info("[control] operator requested provider refresh (+ circuit breaker reset)")
    _record_call("refresh-providers")
    get_circuit_breaker().reset()
    registry = get_registry()
    statuses = registry.all_statuses(refresh=True)
    return {
        "action": "providers_refreshed",
        "providers": [
            {
                "provider_id": s.provider_id,
                "state": s.state,
                "probe_success": s.probe_success,
                "timing_ms": s.timing_ms,
            }
            for s in statuses
        ],
    }


@router.post("/refresh-runtime")
async def post_refresh_runtime():
    """Full coherent runtime refresh: config → gate → providers.

    This is the safest way to apply runtime changes.  Order:
        1. Config — reload from environment (source of truth)
        2. Execution gate — rebuild from new config (concurrency limits)
        3. Provider statuses — live-probe with new probe parameters

    Returns composite audit payload with before/after summaries.
    """
    remaining = _check_cooldown("refresh-runtime")
    if remaining is not None:
        return _cooldown_response(remaining)

    logger.info("[control] operator requested full runtime refresh")
    _record_call("refresh-runtime")
    result = refresh_routing_runtime()
    return {"action": "runtime_refreshed", "result": result}


# ---------------------------------------------------------------------------
# Circuit breaker reset
# ---------------------------------------------------------------------------

@router.post("/circuit-breaker/reset")
async def post_circuit_breaker_reset(request: Request):
    """Reset circuit breaker state for one or all providers.

    Body (optional):
        { "provider_id": "openai" }  — reset a single provider
        {} or omitted               — reset ALL providers

    Returns the circuit breaker status after reset.
    """
    remaining = _check_cooldown("circuit-breaker-reset")
    if remaining is not None:
        return _cooldown_response(remaining)

    from app.services.model_router_policy import get_circuit_breaker
    from app.services.model_provider_registry import get_registry

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    cb = get_circuit_breaker()
    provider_id = body.get("provider_id") if isinstance(body, dict) else None

    if provider_id:
        logger.info("[control] operator reset circuit breaker for provider=%s", provider_id)
        cb.reset(provider_id)
    else:
        logger.info("[control] operator reset ALL circuit breakers")
        cb.reset()

    _record_call("circuit-breaker-reset")

    # Return updated status for all providers
    registry = get_registry()
    statuses = registry.all_statuses(refresh=False)
    result = {}
    for s in statuses:
        result[s.provider_id] = cb.status(s.provider_id)

    return {"action": "circuit_breaker_reset", "provider_id": provider_id or "ALL", "statuses": result}


@router.get("/circuit-breaker/status")
async def get_circuit_breaker_status():
    """Return circuit breaker status for all registered providers."""
    from app.services.model_router_policy import get_circuit_breaker
    from app.services.model_provider_registry import get_registry

    cb = get_circuit_breaker()
    registry = get_registry()
    statuses = registry.all_statuses(refresh=False)
    result = {}
    for s in statuses:
        result[s.provider_id] = cb.status(s.provider_id)

    return {"statuses": result}
