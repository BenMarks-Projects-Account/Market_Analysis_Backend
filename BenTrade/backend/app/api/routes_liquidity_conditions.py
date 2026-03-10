"""Liquidity & Financial Conditions API routes.

Endpoints:
  GET  /api/liquidity-conditions          → full engine payload
  GET  /api/liquidity-conditions/engine   → engine result only (lighter)
  POST /api/liquidity-conditions/model    → trigger LLM model analysis
"""

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/liquidity-conditions", tags=["liquidity-conditions"]
)


@router.get("")
async def get_liquidity_conditions(
    request: Request, force: bool = False
) -> dict:
    """Return full liquidity & financial conditions analysis.

    Response contains:
      engine_result: composite score, pillar scores, confidence,
                     diagnostics, raw inputs, explanations, support_vs_stress
      data_quality: coverage and signal quality summary
      compute_duration_s: wall-clock time
      as_of: ISO timestamp
    """
    service = request.app.state.liquidity_conditions_service
    return await service.get_liquidity_conditions_analysis(force=force)


@router.get("/engine")
async def get_engine_only(
    request: Request, force: bool = False
) -> dict:
    """Return engine result only (lighter endpoint)."""
    service = request.app.state.liquidity_conditions_service
    payload = await service.get_liquidity_conditions_analysis(force=force)
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
        "[LIQUIDITY_CONDITIONS_MODEL] request_start method=POST "
        "path=/api/liquidity-conditions/model force=%s",
        force,
    )
    service = request.app.state.liquidity_conditions_service
    result = await service.run_model_analysis(force=force)
    has_model = result.get("model_analysis") is not None
    logger.info(
        "[LIQUIDITY_CONDITIONS_MODEL] response status=200 has_model=%s",
        has_model,
    )
    return result
