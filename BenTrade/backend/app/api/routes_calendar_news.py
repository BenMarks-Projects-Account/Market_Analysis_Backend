"""Calendar & News API routes for home dashboard.

Endpoints:
  GET  /api/calendar/economic   → economic releases (CPI, NFP, FOMC, etc.)
  GET  /api/calendar/earnings   → earnings reports scheduled today
  GET  /api/news/market          → general market news headlines
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)
router = APIRouter(tags=["calendar-news"])


@router.get("/api/calendar/economic")
async def get_economic_calendar(
    request: Request,
    days_ahead: int = Query(1, ge=0, le=7),
) -> dict:
    """Economic calendar releases for today (+ optional lookahead).

    Tries sources in order: FMP → FRED release dates → Finnhub.
    Filters to US events where possible.
    """
    fmp = request.app.state.fmp_client
    fred = request.app.state.fred_client
    finnhub = request.app.state.finnhub_client

    today = datetime.now(timezone.utc).date()
    end_date = today + timedelta(days=days_ahead)

    # ── Source 1: FMP economic calendar ──
    if fmp.is_available():
        try:
            fmp_data = await fmp.get_economic_calendar(
                from_date=today.isoformat(),
                to_date=end_date.isoformat(),
            )
            if fmp_data and isinstance(fmp_data, list) and len(fmp_data) > 0:
                events = _format_fmp_economic(fmp_data)
                return {
                    "date": today.isoformat(),
                    "count": len(events),
                    "events": events,
                    "source": "fmp",
                }
        except Exception as e:
            logger.info("FMP economic calendar unavailable: %s", e)

    # ── Source 2: FRED release dates ──
    try:
        fred_dates = await fred.get_release_dates(
            realtime_start=today.isoformat(),
            realtime_end=end_date.isoformat(),
        )
        if fred_dates:
            events = await _format_fred_releases(fred, fred_dates)
            if events:
                return {
                    "date": today.isoformat(),
                    "count": len(events),
                    "events": events,
                    "source": "fred",
                }
    except Exception as e:
        logger.warning("FRED release dates failed: %s", e)

    # ── Source 3: Finnhub (may require premium) ──
    try:
        data = await finnhub.get_economic_calendar(
            from_date=today.isoformat(),
            to_date=end_date.isoformat(),
        )
        events = _format_finnhub_economic(data)
        return {
            "date": today.isoformat(),
            "count": len(events),
            "events": events,
            "source": "finnhub",
        }
    except Exception as exc:
        details = getattr(exc, "details", {}) or {}
        status_code = details.get("status_code", 0)
        if status_code == 403:
            logger.info("Finnhub economic calendar requires premium")
        else:
            logger.warning("Finnhub economic calendar failed: %s", exc)

    # All sources failed
    return {
        "date": today.isoformat(),
        "count": 0,
        "events": [],
        "error": "No economic calendar source available",
    }


def _format_fmp_economic(fmp_data: list[dict]) -> list[dict]:
    """Normalize FMP economic calendar entries to common schema."""
    events = []
    for item in fmp_data:
        country = item.get("country", "")
        if country and country != "US":
            continue
        events.append({
            "event": item.get("event"),
            "time": item.get("date", ""),  # FMP uses full datetime in 'date'
            "country": country or "US",
            "impact": item.get("impact", "low"),
            "actual": item.get("actual"),
            "forecast": item.get("estimate"),
            "previous": item.get("previous"),
            "unit": item.get("unit"),
        })
    events.sort(key=lambda e: e.get("time") or "")
    return events


async def _format_fred_releases(fred_client, release_dates: list[dict]) -> list[dict]:
    """Convert FRED release dates into economic calendar events.

    Maps release_id → human name via the /releases endpoint.
    """
    release_names = await fred_client.get_releases() or {}
    events = []
    for item in release_dates:
        release_id = item.get("release_id")
        release_date = item.get("date", "")
        name = release_names.get(release_id, f"FRED Release #{release_id}")
        events.append({
            "event": name,
            "time": release_date,
            "country": "US",
            "impact": "medium",  # FRED doesn't provide impact; default to medium
            "actual": None,
            "forecast": None,
            "previous": None,
            "unit": None,
        })
    events.sort(key=lambda e: e.get("time") or "")
    return events


def _format_finnhub_economic(data: dict) -> list[dict]:
    """Normalize Finnhub economic calendar to common schema."""
    events = []
    raw = (data.get("economicCalendar") or data.get("result") or []) if isinstance(data, dict) else []
    for event in raw:
        country = event.get("country", "")
        if country != "US":
            continue
        events.append({
            "event": event.get("event"),
            "time": event.get("time"),
            "country": country,
            "impact": event.get("impact", "low"),
            "actual": event.get("actual"),
            "forecast": event.get("estimate"),
            "previous": event.get("prev"),
            "unit": event.get("unit"),
        })
    events.sort(key=lambda e: e.get("time") or "")
    return events


@router.get("/api/calendar/earnings")
async def get_earnings_calendar(
    request: Request,
    days_ahead: int = Query(1, ge=0, le=7),
) -> dict:
    """Earnings calendar for today (+ optional lookahead).

    Returns all symbols with scheduled reports, sorted by hour then symbol.
    """
    finnhub = request.app.state.finnhub_client

    today = datetime.now(timezone.utc).date()
    end_date = today + timedelta(days=days_ahead)

    try:
        raw = await finnhub.get_earnings_calendar_range(
            from_date=today.isoformat(),
            to_date=end_date.isoformat(),
        )
    except Exception as exc:
        logger.error("Earnings calendar fetch failed: %s", exc)
        raise HTTPException(status_code=503, detail="Earnings calendar unavailable") from exc

    earnings = []
    for e in raw or []:
        earnings.append({
            "symbol": e.get("symbol"),
            "date": e.get("date"),
            "hour": e.get("hour"),
            "eps_estimate": e.get("epsEstimate"),
            "eps_actual": e.get("epsActual"),
            "revenue_estimate": e.get("revenueEstimate"),
            "revenue_actual": e.get("revenueActual"),
            "year": e.get("year"),
            "quarter": e.get("quarter"),
        })

    time_order = {"bmo": 0, "dmh": 1, "amc": 2}
    earnings.sort(key=lambda x: (
        x.get("date") or "",
        time_order.get(x.get("hour"), 3),
        x.get("symbol") or "",
    ))

    return {
        "date": today.isoformat(),
        "count": len(earnings),
        "earnings": earnings,
    }


@router.get("/api/news/market")
async def get_market_news(
    request: Request,
    category: str = Query("general"),
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    """Latest market news headlines from Finnhub."""
    finnhub = request.app.state.finnhub_client

    try:
        news = await finnhub.get_market_news(category=category)
    except Exception as exc:
        logger.error("Market news fetch failed: %s", exc)
        raise HTTPException(status_code=503, detail="Market news unavailable") from exc

    items = (news or [])[:limit]

    return {
        "category": category,
        "count": len(items),
        "news": [
            {
                "headline": n.get("headline"),
                "summary": n.get("summary"),
                "source": n.get("source"),
                "url": n.get("url"),
                "datetime": n.get("datetime"),
                "category": n.get("category"),
                "image": n.get("image"),
                "related": n.get("related"),
            }
            for n in items
        ],
    }
