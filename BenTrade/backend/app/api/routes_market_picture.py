"""Routes for the Market Picture Scoreboard and History.

Serves a slim view of the market-state artifact suitable for
the home dashboard scoreboard cards.  Each card exposes paired
engine vs model fields so the UI can render them side-by-side.

Per-engine model scores are hydrated from the durable model score
store (data/market_state/model_scores_latest.json) which is
populated by engine model endpoints.  Each card includes freshness
metadata (model_captured_at, model_fresh, model_status) so the
frontend can show stale/missing status honestly.

Card normalisation is handled by the shared contract module
(app.services.market_picture_contract) — see normalize_engine_card().

History: each successful scoreboard load also appends a compact
snapshot to market_picture_history.jsonl for overtime charting.
Model scores are enriched from the same durable store.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.services.market_picture_contract import ENGINE_DISPLAY, build_engine_cards

logger = logging.getLogger(__name__)

router = APIRouter(tags=["market-picture"])

# Route-level cache for history endpoint (avoids re-reading JSONL every request)
_history_cache: dict[str, tuple[float, Any]] = {}
_HISTORY_CACHE_TTL = 60  # seconds


def _get_data_dir(request: Request) -> str:
    return str(request.app.state.backend_dir / "data")


@router.get("/api/market-picture/scoreboard")
async def get_scoreboard(request: Request) -> JSONResponse:
    """Return scoreboard-ready engine summaries from the latest artifact.

    Card shape per engine:
      key, name, engine_score, engine_label, engine_summary,
      model_score, model_summary, confidence, status
    """
    try:
        data_dir = _get_data_dir(request)
        from app.workflows.market_state_consumer import load_market_state_for_consumer

        consumer = load_market_state_for_consumer(data_dir)
    except Exception as exc:
        logger.warning("[MarketPictureScoreboard] load failed: %s", exc)
        return JSONResponse(
            content={"ok": False, "error": str(exc), "engines": [], "composite": None, "model_status": None},
            status_code=200,
        )

    if not consumer.loaded or not consumer.artifact:
        return JSONResponse(
            content={"ok": False, "error": consumer.error or "Market state not available", "engines": [], "composite": None, "model_status": None},
            status_code=200,
        )

    artifact: dict[str, Any] = consumer.artifact
    raw_engines: dict[str, Any] = artifact.get("engines") or {}

    # Load durable model scores for hydration into engine cards
    from app.services.model_score_store import load_all_scores
    all_model_scores = load_all_scores(data_dir)

    # Build normalised engine cards via shared contract
    engine_cards = build_engine_cards(raw_engines, all_model_scores)

    # Composite overview
    composite_raw = artifact.get("composite") or {}
    composite = {
        "market_state": composite_raw.get("market_state"),
        "support_state": composite_raw.get("support_state"),
        "stability_state": composite_raw.get("stability_state"),
        "confidence": composite_raw.get("confidence"),
        "summary": composite_raw.get("summary"),
    }

    # Model interpretation status
    mi = artifact.get("model_interpretation") or {}
    model_status = mi.get("status")

    generated_at = artifact.get("generated_at")

    # ── Capture overtime snapshot (fire-and-forget, never blocks response) ──
    try:
        from app.services.market_picture_history import build_snapshot, append_snapshot
        from app.services.model_score_store import load_fresh_scores

        fresh_model_scores = load_fresh_scores(data_dir)

        snapshot = build_snapshot(
            artifact=artifact,
            engine_cards=engine_cards,
            composite=composite,
            model_status=model_status,
            generated_at=generated_at,
            model_scores=fresh_model_scores,
        )
        append_snapshot(data_dir, snapshot)
    except Exception as exc:
        logger.debug("[MarketPictureScoreboard] history capture skipped: %s", exc)

    return JSONResponse(content={
        "ok": True,
        "engines": engine_cards,
        "composite": composite,
        "model_status": model_status,
        "generated_at": generated_at,
    })


@router.get("/api/market-picture/history")
async def get_history(
    request: Request,
    limit: int = Query(default=200, ge=1, le=2000),
) -> JSONResponse:
    """Return historical market-picture snapshots for overtime charting.

    Query params:
        limit — max number of most-recent entries to return (default 200)

    Response shape:
        { ok, entries: [...], count }

    Route-level cache: results are cached for 60s to avoid re-reading the
    JSONL file on every dashboard poll.
    """
    cache_key = f"history_{limit}"
    now = time.monotonic()
    cached = _history_cache.get(cache_key)
    if cached:
        cached_time, cached_response = cached
        if (now - cached_time) < _HISTORY_CACHE_TTL:
            return cached_response

    try:
        data_dir = _get_data_dir(request)
        from app.services.market_picture_history import load_history

        entries = load_history(data_dir, limit=limit)
    except Exception as exc:
        logger.warning("[MarketPictureHistory] load failed: %s", exc)
        return JSONResponse(
            content={"ok": False, "error": str(exc), "entries": [], "count": 0},
            status_code=200,
        )

    response = JSONResponse(content={
        "ok": True,
        "entries": entries,
        "count": len(entries),
    })
    _history_cache[cache_key] = (now, response)
    return response


@router.get("/api/market-picture/model-scores")
async def get_model_scores(request: Request) -> JSONResponse:
    """Return latest durable model scores for all engines with freshness metadata.

    Response shape:
        { ok, scores: { engine_key: { model_score, model_label, confidence,
                                      captured_at, age_seconds, is_fresh } } }
    """
    try:
        data_dir = _get_data_dir(request)
        from app.services.model_score_store import load_all_scores

        scores = load_all_scores(data_dir)
    except Exception as exc:
        logger.warning("[MarketPictureModelScores] load failed: %s", exc)
        return JSONResponse(
            content={"ok": False, "error": str(exc), "scores": {}},
            status_code=200,
        )

    return JSONResponse(content={
        "ok": True,
        "scores": scores,
    })
