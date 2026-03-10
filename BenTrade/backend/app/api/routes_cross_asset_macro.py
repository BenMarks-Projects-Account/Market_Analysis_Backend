"""Cross-Asset / Macro Confirmation API routes.

Endpoints:
  GET  /api/cross-asset-macro          → full engine payload
  GET  /api/cross-asset-macro/engine   → engine result only (lighter)
  POST /api/cross-asset-macro/model    → trigger LLM model analysis
"""

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/cross-asset-macro", tags=["cross-asset-macro"]
)


@router.get("")
async def get_cross_asset_macro(
    request: Request, force: bool = False
) -> dict:
    """Return full cross-asset / macro confirmation analysis.

    Response contains:
      engine_result: composite score, pillar scores, confidence,
                     diagnostics, raw inputs, explanations
      data_quality: coverage and signal quality summary
      compute_duration_s: wall-clock time
      as_of: ISO timestamp
    """
    service = request.app.state.cross_asset_macro_service
    return await service.get_cross_asset_analysis(force=force)


@router.get("/engine")
async def get_engine_only(
    request: Request, force: bool = False
) -> dict:
    """Return engine result only (lighter endpoint)."""
    service = request.app.state.cross_asset_macro_service
    payload = await service.get_cross_asset_analysis(force=force)
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
        "[CROSS_ASSET_MODEL] request_start method=POST "
        "path=/api/cross-asset-macro/model force=%s",
        force,
    )
    service = request.app.state.cross_asset_macro_service
    result = await service.run_model_analysis(force=force)
    has_model = result.get("model_analysis") is not None
    logger.info(
        "[CROSS_ASSET_MODEL] response status=200 has_model=%s", has_model
    )
    return result
