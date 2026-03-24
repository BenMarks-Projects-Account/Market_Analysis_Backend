"""Active Trade Pipeline API routes.

Endpoints
─────────
    POST /api/active-trade-pipeline/run
        Run the Active Trade Pipeline on all current positions.
        Query params: account_mode (live|paper), skip_model (bool)

    GET  /api/active-trade-pipeline/results
        Get the most recent pipeline run result.

    GET  /api/active-trade-pipeline/results/{run_id}
        Get a specific pipeline run result by ID.

All endpoints follow the BenTrade error-propagation pattern:
upstream errors are preserved, not masked.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(
    prefix="/api/active-trade-pipeline",
    tags=["active-trade-pipeline"],
)
logger = logging.getLogger("bentrade.routes_active_trade_pipeline")

# In-memory result store — most recent N runs.
# Bounded to prevent unbounded memory growth.
_MAX_STORED_RUNS = 10
_run_results: list[dict[str, Any]] = []


def _store_result(result: dict[str, Any]) -> None:
    """Store pipeline result, evicting oldest if at capacity."""
    _run_results.append(result)
    while len(_run_results) > _MAX_STORED_RUNS:
        _run_results.pop(0)


def _get_latest() -> dict[str, Any] | None:
    """Return the most recent pipeline result, or None."""
    return _run_results[-1] if _run_results else None


def _get_by_id(run_id: str) -> dict[str, Any] | None:
    """Find a stored pipeline result by run_id."""
    for r in reversed(_run_results):
        if r.get("run_id") == run_id:
            return r
    return None


@router.post("/run")
async def run_pipeline(
    request: Request,
    account_mode: str = Query("live", pattern="^(live|paper)$"),
    skip_model: bool = Query(False),
) -> dict[str, Any]:
    """Run the Active Trade Pipeline on all current broker positions.

    1. Fetches active trades from Tradier (via existing active trades route logic)
    2. Runs the full pipeline (engine + model) on each trade
    3. Returns normalized recommendations for all positions

    Query Parameters:
        account_mode: "live" or "paper" (which Tradier account to use)
        skip_model: if true, skip LLM analysis (engine-only mode)
    """
    from app.api.routes_active_trades import _build_active_payload
    from app.services.active_trade_pipeline import run_active_trade_pipeline
    from app.trading.tradier_credentials import get_tradier_context

    # ── 0. Pre-check: credentials available for requested mode ──
    settings = request.app.state.trading_service.settings
    try:
        creds = get_tradier_context(settings, account_type=account_mode)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{account_mode.upper()} trading credentials not configured. "
                f"Set TRADIER_API_KEY_{account_mode.upper()} and "
                f"TRADIER_ACCOUNT_ID_{account_mode.upper()} in .env."
            ),
        ) from exc

    logger.info(
        "event=pipeline_run account_mode=%s account_id=%s base_url=%s skip_model=%s",
        account_mode,
        creds.account_id[-4:] if creds.account_id else "none",
        creds.base_url,
        skip_model,
    )

    # ── 1. Fetch active trades ──────────────────────────────────
    try:
        payload = await _build_active_payload(request, account_mode=account_mode)
    except Exception as exc:
        logger.error("[active_trade_pipeline] Failed to fetch active trades: %s", exc)
        return {
            "ok": False,
            "error": {
                "message": f"Failed to fetch active trades: {exc}",
                "type": type(exc).__name__,
            },
        }

    if not payload.get("ok"):
        return {
            "ok": False,
            "error": payload.get("error") or {"message": "Failed to load positions"},
            "positions_payload": {
                "account_mode": payload.get("account_mode"),
                "source": payload.get("source"),
            },
        }

    trades = payload.get("active_trades") or []

    # ── 2. Resolve services ─────────────────────────────────────
    monitor_service = getattr(request.app.state, "active_trade_monitor_service", None)
    regime_service = getattr(request.app.state, "regime_service", None)
    base_data_service = getattr(request.app.state, "base_data_service", None)

    if not monitor_service or not regime_service or not base_data_service:
        missing = []
        if not monitor_service:
            missing.append("active_trade_monitor_service")
        if not regime_service:
            missing.append("regime_service")
        if not base_data_service:
            missing.append("base_data_service")
        return {
            "ok": False,
            "error": {
                "message": f"Required services not available: {', '.join(missing)}",
                "type": "ServiceUnavailable",
            },
        }

    # ── 3. Run pipeline ─────────────────────────────────────────
    positions_metadata = {
        "source": payload.get("source", "tradier"),
        "account_mode": account_mode,
        "positions_fetched": len(trades),
    }

    try:
        result = await run_active_trade_pipeline(
            trades,
            monitor_service,
            regime_service,
            base_data_service,
            skip_model=skip_model,
            positions_metadata=positions_metadata,
        )
    except Exception as exc:
        logger.error(
            "[active_trade_pipeline] Pipeline execution failed: %s",
            exc, exc_info=True,
        )
        return {
            "ok": False,
            "error": {
                "message": f"Pipeline execution failed: {exc}",
                "type": type(exc).__name__,
            },
        }

    # ── 4. Store and return ─────────────────────────────────────
    result["ok"] = True
    result["account_mode"] = account_mode
    _store_result(result)

    return result


@router.get("/results")
async def get_latest_results() -> dict[str, Any]:
    """Get the most recent Active Trade Pipeline result."""
    latest = _get_latest()
    if latest is None:
        return {
            "ok": False,
            "error": {"message": "No pipeline results available. Run the pipeline first."},
        }
    return latest


@router.get("/results/{run_id}")
async def get_results_by_id(run_id: str) -> dict[str, Any]:
    """Get a specific Active Trade Pipeline result by run ID."""
    result = _get_by_id(run_id)
    if result is None:
        return {
            "ok": False,
            "error": {"message": f"Run {run_id} not found"},
        }
    return result


@router.get("/runs")
async def list_runs() -> dict[str, Any]:
    """List all stored pipeline runs (summary only, no full recommendations)."""
    runs = []
    for r in reversed(_run_results):
        runs.append({
            "run_id": r.get("run_id"),
            "started_at": r.get("started_at"),
            "ended_at": r.get("ended_at"),
            "duration_ms": r.get("duration_ms"),
            "status": r.get("status"),
            "trade_count": r.get("trade_count"),
            "recommendation_counts": r.get("recommendation_counts"),
            "account_mode": r.get("account_mode"),
        })
    return {"ok": True, "runs": runs}
