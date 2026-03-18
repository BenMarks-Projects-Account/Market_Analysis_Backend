"""News & Sentiment API routes (layered architecture).

Endpoints:
  GET  /api/news-sentiment             → base payload (engine + items + macro, no model)
  GET  /api/news-sentiment/headlines   → headlines only (lighter)
  GET  /api/news-sentiment/macro       → FRED macro context only
  GET  /api/news-sentiment/engine      → engine result only
  POST /api/news-sentiment/model       → trigger model analysis (manual, on demand)
"""

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/news-sentiment", tags=["news-sentiment"])


@router.get("")
async def get_news_sentiment(request: Request, force: bool = False) -> dict:
    """Return base news-sentiment payload (engine + items + macro).

    Model analysis is NOT included — trigger it separately via POST /model.

    Response contains:
      internal_engine: deterministic engine result (always present)
      items: normalized headline items
      macro_context: FRED macro snapshot
      source_freshness: per-provider status
    """
    service = request.app.state.news_sentiment_service
    return await service.get_news_sentiment(force=force)


@router.get("/headlines")
async def get_headlines(request: Request, force: bool = False) -> dict:
    """Return headlines subset with basic metadata (lighter endpoint)."""
    service = request.app.state.news_sentiment_service
    payload = await service.get_news_sentiment(force=force)
    return {
        "items": payload.get("items", []),
        "item_count": payload.get("item_count", 0),
        "as_of": payload.get("as_of"),
        "source_freshness": payload.get("source_freshness", []),
    }


@router.get("/macro")
async def get_macro_context(request: Request) -> dict:
    """Return FRED macro context layer only."""
    service = request.app.state.news_sentiment_service
    payload = await service.get_news_sentiment()
    return {
        "macro_context": payload.get("macro_context", {}),
        "as_of": payload.get("as_of"),
    }


@router.get("/engine")
async def get_engine_result(request: Request, force: bool = False) -> dict:
    """Return internal engine result only."""
    service = request.app.state.news_sentiment_service
    payload = await service.get_news_sentiment(force=force)
    return {
        "internal_engine": payload.get("internal_engine", {}),
        "as_of": payload.get("as_of"),
    }


@router.post("/model")
@router.post("/model/")  # accept trailing slash to avoid 405
async def run_model_analysis(request: Request, force: bool = True) -> dict:
    """Trigger LLM model analysis on demand (manual, user-initiated).

    Returns model_analysis dict or null if model is unavailable.
    Defaults to force=True since this is an explicit user action.
    """
    logger.info("[NEWS_MODEL] request_start method=POST path=/api/news-sentiment/model force=%s", force)
    service = request.app.state.news_sentiment_service
    result = await service.run_model_analysis(force=force)
    has_model = result.get("model_analysis") is not None
    if has_model:
        try:
            from app.services.model_score_store import save_model_score
            data_dir = str(request.app.state.backend_dir / "data")
            save_model_score(data_dir, "news_sentiment", result["model_analysis"], result.get("as_of"))
        except Exception:
            pass
    error_info = result.get("error")
    if error_info:
        logger.warning(
            "[NEWS_MODEL] response status=200 has_model=%s error_kind=%s error_msg=%s",
            has_model, error_info.get("kind"), error_info.get("message"),
        )
    else:
        logger.info("[NEWS_MODEL] response status=200 has_model=%s", has_model)
    return result
