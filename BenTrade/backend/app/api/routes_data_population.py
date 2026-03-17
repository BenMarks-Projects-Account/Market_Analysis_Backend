"""Data Population API routes.

Provides status polling and manual trigger for the data-population pipeline.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/data-population", tags=["data-population"])


@router.get("/status")
async def get_status(request: Request) -> JSONResponse:
    """Return current data-population status for frontend polling."""
    svc = getattr(request.app.state, "data_population_service", None)
    if svc is None:
        return JSONResponse(
            status_code=503,
            content={"phase": "unavailable", "error": "Data population service not configured"},
        )
    return JSONResponse(content=svc.status.to_dict())


@router.post("/trigger")
async def trigger_run(request: Request) -> JSONResponse:
    """Manually trigger a data-population run (deduped if already running)."""
    svc = getattr(request.app.state, "data_population_service", None)
    if svc is None:
        return JSONResponse(
            status_code=503,
            content={"phase": "unavailable", "error": "Data population service not configured"},
        )
    status = await svc.trigger()
    return JSONResponse(content=status.to_dict())
