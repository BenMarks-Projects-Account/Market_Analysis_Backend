"""Portfolio & Market fix routes — equity curve, VIX pulse.

Endpoints:
  GET   /api/portfolio/equity-curve   → historical equity curve data
  POST  /api/portfolio/equity-snapshot → take today's equity snapshot
  GET   /api/market/vix-pulse         → VIX with context (percentile, classification, trend)
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(tags=["portfolio", "market"])

# FRED series for VIX daily close — used for 1-year percentile calculation.
_FRED_VIX_SERIES = "VIXCLS"


# ── Equity Curve ───────────────────────────────────────────────

@router.get("/api/portfolio/equity-curve")
async def get_equity_curve(request: Request, days: int = 90) -> dict:
    """Get historical equity curve data from local snapshots.

    Auto-takes a snapshot for today if one doesn't exist yet.
    """
    tracker = request.app.state.equity_tracker
    tradier = request.app.state.tradier_client

    # Auto-snapshot: if no record for today, take one now
    history = tracker.get_history(days=days)
    today_str = date.today().isoformat()
    has_today = any(r.get("date") == today_str for r in history)
    if not has_today:
        try:
            balances = await tradier.get_balances()
            await tracker.snapshot_from_balances(balances)
            history = tracker.get_history(days=days)
        except Exception as exc:
            logger.debug("event=equity_auto_snapshot_skip error=%s", exc)
    if not history:
        return {"data": [], "total_days": 0, "message": "No history yet"}

    start_val = history[0].get("total_value")
    end_val = history[-1].get("total_value")
    total_return = None
    if start_val and end_val and len(history) >= 2:
        total_return = round((end_val - start_val) / start_val * 100, 2)

    return {
        "data": history,
        "total_days": len(history),
        "start_value": start_val,
        "end_value": end_val,
        "total_return_pct": total_return,
    }


@router.post("/api/portfolio/equity-snapshot")
async def take_equity_snapshot(request: Request) -> dict:
    """Take a snapshot of current account equity.

    Fetches balances from Tradier and stores today's value.
    Called automatically during data population, or manually.
    """
    tradier = request.app.state.tradier_client
    tracker = request.app.state.equity_tracker

    try:
        balances = await tradier.get_balances()
    except Exception as exc:
        logger.warning("event=equity_snapshot_error reason=tradier_fetch error=%s", exc)
        raise HTTPException(status_code=503, detail="Could not fetch account balances") from exc

    record = await tracker.snapshot_from_balances(balances)
    if not record:
        raise HTTPException(status_code=422, detail="Balance data unusable for snapshot")

    return {"ok": True, "snapshot": record}


# ── VIX Pulse ──────────────────────────────────────────────────

def _classify_vix(value: float) -> str:
    """Classify VIX level into a named regime.

    Thresholds:
    - < 12: calm
    - < 17: normal
    - < 25: elevated
    - < 35: fear
    - >= 35: panic
    """
    if value < 12:
        return "calm"
    if value < 17:
        return "normal"
    if value < 25:
        return "elevated"
    if value < 35:
        return "fear"
    return "panic"


@router.get("/api/market/vix-pulse")
async def get_vix_pulse(request: Request) -> dict:
    """VIX with context: current level, 1-year percentile, classification, daily change."""
    tradier = request.app.state.tradier_client
    fred = request.app.state.fred_client

    # 1. Current VIX — real-time from Tradier quote
    vix_quote = await tradier.get_quote("VIX")
    current = None
    if vix_quote:
        current = vix_quote.get("last") or vix_quote.get("close")

    # 2. 1-year history from FRED VIXCLS for percentile
    series = await fred.get_observation_series(_FRED_VIX_SERIES, limit=300)

    if current is None and series:
        # Fallback: use latest FRED value
        current = series[-1]["value"]

    if current is None:
        raise HTTPException(status_code=503, detail="VIX data unavailable")

    current = round(float(current), 2)

    # 3. Daily change — current (Tradier real-time) vs yesterday's close (FRED).
    #    series[-1] may be today's FRED close (same as current), so use series[-2]
    #    as the baseline to get a meaningful intraday change.
    change = 0.0
    change_pct = 0.0
    if series and len(series) >= 2:
        day_before = series[-2]["value"]
        change = round(current - day_before, 2)
        change_pct = round((current - day_before) / day_before * 100, 2) if day_before else 0.0

    # 4. Percentile within 1-year window
    values = [s["value"] for s in series] if series else []
    if values:
        below = sum(1 for v in values if v < current)
        percentile_1y = round(100 * below / len(values), 0)
    else:
        percentile_1y = None

    classification = _classify_vix(current)

    return {
        "current": current,
        "change": change,
        "change_pct": change_pct,
        "percentile_1y": percentile_1y,
        "classification": classification,
    }
