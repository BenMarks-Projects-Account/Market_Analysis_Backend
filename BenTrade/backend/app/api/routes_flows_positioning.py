"""Flows & Positioning API routes.

Endpoints:
  GET  /api/flows-positioning          → full engine payload
  GET  /api/flows-positioning/engine   → engine result only (lighter)
  POST /api/flows-positioning/model    → trigger LLM model analysis
"""

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/flows-positioning", tags=["flows-positioning"]
)


@router.get("")
async def get_flows_positioning(
    request: Request, force: bool = False
) -> dict:
    """Return full flows & positioning analysis.

    Response contains:
      engine_result: composite score, pillar scores, confidence,
                     diagnostics, raw inputs, explanations, strategy_bias
      data_quality: coverage and signal quality summary
      compute_duration_s: wall-clock time
      as_of: ISO timestamp
    """
    service = request.app.state.flows_positioning_service
    return await service.get_flows_positioning_analysis(force=force)


@router.get("/engine")
async def get_engine_only(
    request: Request, force: bool = False
) -> dict:
    """Return engine result only (lighter endpoint)."""
    service = request.app.state.flows_positioning_service
    payload = await service.get_flows_positioning_analysis(force=force)
    return {
        "engine_result": payload.get("engine_result", {}),
        "as_of": payload.get("as_of"),
    }


@router.post("/model")
@router.post("/model/")
async def run_model_analysis(
    request: Request, force: bool = True
) -> dict:
    """Trigger LLM model analysis on demand (manual, user-initiated).

    Returns model_analysis dict or null if model is unavailable.
    Defaults to force=True since this is an explicit user action.
    """
    logger.info(
        "[FLOWS_POSITIONING_MODEL] request_start method=POST "
        "path=/api/flows-positioning/model force=%s",
        force,
    )
    service = request.app.state.flows_positioning_service
    result = await service.run_model_analysis(force=force)
    has_model = result.get("model_analysis") is not None
    logger.info(
        "[FLOWS_POSITIONING_MODEL] response status=200 has_model=%s",
        has_model,
    )
    return result
