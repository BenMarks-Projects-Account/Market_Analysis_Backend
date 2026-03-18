"""Volatility & Options Structure API routes.

Endpoints:
  GET  /api/volatility-options          → full engine payload
  GET  /api/volatility-options/engine   → engine result only (lighter)
  POST /api/volatility-options/model    → LLM model analysis (manual trigger)
"""

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/volatility-options", tags=["volatility-options"]
)


@router.get("")
async def get_volatility_options(
    request: Request, force: bool = False
) -> dict:
    """Return full volatility & options structure analysis.

    Response contains:
      engine_result: composite score, pillar scores, strategy suitability,
                     confidence, diagnostics, raw inputs, explanations
      data_quality: signal quality and data source summary
      compute_duration_s: wall-clock time
      as_of: ISO timestamp
    """
    service = request.app.state.volatility_options_service
    return await service.get_volatility_analysis(force=force)


@router.get("/engine")
async def get_engine_only(
    request: Request, force: bool = False
) -> dict:
    """Return engine result only (lighter endpoint)."""
    service = request.app.state.volatility_options_service
    payload = await service.get_volatility_analysis(force=force)
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

    Defaults to force=True since this is an explicit user action.
    """
    logger.info(
        "[VOL_MODEL] request_start method=POST "
        "path=/api/volatility-options/model force=%s",
        force,
    )
    service = request.app.state.volatility_options_service
    result = await service.run_model_analysis(force=force)
    has_model = result.get("model_analysis") is not None
    if has_model:
        try:
            from app.services.model_score_store import save_model_score
            data_dir = str(request.app.state.backend_dir / "data")
            save_model_score(data_dir, "volatility_options", result["model_analysis"], result.get("as_of"))
        except Exception:
            pass
    logger.info("[VOL_MODEL] response status=200 has_model=%s", has_model)
    return result
