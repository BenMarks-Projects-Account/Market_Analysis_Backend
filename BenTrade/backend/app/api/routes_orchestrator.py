"""Orchestrator API routes — control the continuous MI → TMC loop.

Endpoints
─────────
    GET  /api/orchestrator/status     — current orchestrator state
    POST /api/orchestrator/start      — start the continuous loop
    POST /api/orchestrator/stop       — stop the loop
    POST /api/orchestrator/pause      — pause (finish current stage, don't start next)
    POST /api/orchestrator/resume     — resume after pause
    POST /api/orchestrator/delay      — set inter-cycle delay seconds
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from app.workflows.continuous_workflow_orchestrator import get_orchestrator

logger = logging.getLogger("bentrade.routes_orchestrator")

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


@router.get("/status")
async def get_orchestrator_status(request: Request) -> dict[str, Any]:
    """Return the current orchestrator state."""
    orch = get_orchestrator(request.app)
    return orch.status


@router.post("/start")
async def start_orchestrator(
    request: Request,
    account_mode: str = Query("paper", pattern="^(live|paper)$"),
) -> dict[str, Any]:
    """Start the continuous workflow loop."""
    orch = get_orchestrator(request.app)
    await orch.start(account_mode=account_mode)
    logger.info("event=orchestrator_api_start account_mode=%s", account_mode)
    return {"action": "started", **orch.status}


@router.post("/stop")
async def stop_orchestrator(request: Request) -> dict[str, Any]:
    """Stop the continuous workflow loop."""
    orch = get_orchestrator(request.app)
    await orch.stop()
    logger.info("event=orchestrator_api_stop")
    return {"action": "stopped", **orch.status}


@router.post("/pause")
async def pause_orchestrator(request: Request) -> dict[str, Any]:
    """Pause the loop (current stage finishes, next cycle doesn't start)."""
    orch = get_orchestrator(request.app)
    orch.pause()
    return {"action": "paused", **orch.status}


@router.post("/resume")
async def resume_orchestrator(request: Request) -> dict[str, Any]:
    """Resume the loop after a pause."""
    orch = get_orchestrator(request.app)
    orch.resume()
    return {"action": "resumed", **orch.status}


@router.post("/delay")
async def set_orchestrator_delay(
    request: Request,
    seconds: float = Query(0.0, ge=0.0, le=3600.0),
) -> dict[str, Any]:
    """Set the delay between cycles (0 = no delay, continuous)."""
    orch = get_orchestrator(request.app)
    orch.set_delay(seconds)
    return {"action": "delay_set", **orch.status}
