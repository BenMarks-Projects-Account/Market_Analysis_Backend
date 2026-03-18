"""Breadth & Participation API routes.

Endpoints:
  GET  /api/breadth-participation          → full engine payload
  GET  /api/breadth-participation/engine   → engine result only (lighter)
"""

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/breadth-participation", tags=["breadth-participation"]
)


@router.get("")
async def get_breadth_participation(
    request: Request, force: bool = False
) -> dict:
    """Return full breadth & participation analysis.

    Response contains:
      engine_result: composite score, pillar scores, confidence,
                     diagnostics, raw inputs, explanations
      data_quality: coverage and signal quality summary
      compute_duration_s: wall-clock time
      as_of: ISO timestamp
    """
    service = request.app.state.breadth_service
    return await service.get_breadth_analysis(force=force)


@router.get("/engine")
async def get_engine_only(
    request: Request, force: bool = False
) -> dict:
    """Return engine result only (lighter endpoint)."""
    service = request.app.state.breadth_service
    payload = await service.get_breadth_analysis(force=force)
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
        "[BREADTH_MODEL] request_start method=POST "
        "path=/api/breadth-participation/model force=%s",
        force,
    )
    service = request.app.state.breadth_service
    result = await service.run_model_analysis(force=force)
    has_model = result.get("model_analysis") is not None
    if has_model:
        try:
            from app.services.model_score_store import save_model_score
            data_dir = str(request.app.state.backend_dir / "data")
            save_model_score(data_dir, "breadth_participation", result["model_analysis"], result.get("as_of"))
        except Exception:
            pass
    logger.info(
        "[BREADTH_MODEL] response status=200 has_model=%s", has_model
    )
    return result
