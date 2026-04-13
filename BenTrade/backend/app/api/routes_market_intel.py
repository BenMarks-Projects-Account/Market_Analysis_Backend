"""Market Intelligence API routes (FMP + Polygon powered).

Endpoints:
  GET  /api/market/movers              → top gainers, losers, most active
  GET  /api/market/sectors             → sector rotation heatmap data
  GET  /api/market/pre-market-movers   → pre-market equity gappers
  GET  /api/market/upgrades-downgrades → analyst rating changes
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)
router = APIRouter(tags=["market-intel"])


# ── Sector ETF mapping ────────────────────────────────────────

SECTOR_ETFS = {
    "Financials": "XLF",
    "Technology": "XLK",
    "Energy": "XLE",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}


# ── Helpers ────────────────────────────────────────────────────

async def _build_sector_rotation_from_etfs(polygon_client) -> list[dict]:
    """Compute sector rotation table from Polygon daily bars for 11 sector ETFs.

    Returns [{"sector": str, "etf": str, "1d": float|None, "1w": float|None,
              "1m": float|None, "3m": float|None}, ...] sorted by 1M desc.
    """
    today = date.today()
    start = today - timedelta(days=120)  # buffer for 90+ trading days

    async def _fetch_bars(sector: str, etf: str):
        try:
            bars = await polygon_client.get_aggregates_ohlc(
                etf, start_date=start, end_date=today,
            )
            return (sector, etf, bars)
        except Exception as e:
            logger.warning("Sector rotation: failed to fetch %s (%s): %s", etf, sector, e)
            return (sector, etf, [])

    results = await asyncio.gather(
        *[_fetch_bars(sector, etf) for sector, etf in SECTOR_ETFS.items()]
    )

    rotation: list[dict] = []
    for sector, etf, bars in results:
        if not bars or len(bars) < 2:
            continue

        latest_close = bars[-1].get("close")
        if not latest_close:
            continue

        def _pct_change(idx: int) -> float | None:
            """Compute % change from bars[-idx] to latest."""
            if len(bars) < idx:
                return None
            prev = bars[-idx].get("close")
            if not prev:
                return None
            return round((latest_close - prev) / prev * 100, 2)

        rotation.append({
            "sector": sector,
            "etf": etf,
            "1d": _pct_change(2),    # 1 trading day back
            "1w": _pct_change(6),    # ~5 trading days
            "1m": _pct_change(22),   # ~21 trading days
            "3m": _pct_change(64),   # ~63 trading days
        })

    rotation.sort(key=lambda r: r.get("1m") or -999, reverse=True)
    return rotation


# ── Routes ─────────────────────────────────────────────────────

@router.get("/api/market/movers")
async def get_market_movers(request: Request) -> dict:
    """Top gainers, losers, and most active stocks for the day."""
    fmp = request.app.state.fmp_client

    if not fmp.is_available():
        raise HTTPException(status_code=503, detail="FMP not available")

    gainers = await fmp.get_market_gainers()
    losers = await fmp.get_market_losers()
    actives = await fmp.get_market_actives()

    return {
        "gainers": (gainers or [])[:10],
        "losers": (losers or [])[:10],
        "actives": (actives or [])[:10],
    }


@router.get("/api/market/sectors")
async def get_market_sectors(request: Request) -> dict:
    """Sector rotation heatmap — 1d / 1w / 1m / 3m changes from sector ETFs.

    Uses Polygon daily OHLC bars for 11 sector ETFs (XLF, XLK, etc.)
    to compute accurate multi-timeframe rotation data.
    FMP current snapshot kept for supplementary metadata when available.
    """
    polygon = request.app.state.polygon_client
    fmp = request.app.state.fmp_client

    # Primary: Polygon ETF-based rotation (accurate multi-timeframe)
    rotation = await _build_sector_rotation_from_etfs(polygon)

    # Supplementary: FMP current-day snapshot (used for 'current' field only)
    current = []
    if fmp.is_available():
        try:
            current = await fmp.get_sector_performance() or []
        except Exception as e:
            logger.debug("FMP sector snapshot unavailable: %s", e)

    return {
        "current": current,
        "rotation": rotation,
    }


# High-volume stocks scanned for pre-market movers via Polygon snapshots.
_PREMARKET_SCAN_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD",
    "NFLX", "JPM", "V", "MA", "BAC", "WMT", "UNH", "JNJ", "PG",
    "XOM", "CVX", "HD", "DIS", "COST", "AVGO", "CRM", "ADBE",
    "INTC", "PYPL", "ORCL", "CSCO", "MRK", "PFE", "ABBV", "LLY",
    "MCD", "NKE", "LOW", "GS", "MS", "COIN", "SQ", "SHOP", "PLTR",
    "RIVN", "LCID", "SOFI", "MARA", "RIOT", "SMCI", "ARM", "MSTR",
    "SPY", "QQQ", "IWM", "DIA",
]


async def _get_premarket_from_polygon(polygon_client) -> dict | None:
    """Compute pre-market movers from Polygon batch snapshots.

    Uses todaysChange/todaysChangePerc from Polygon snapshots,
    which during pre-market reflect movement vs previous close.
    """
    try:
        snapshots = await polygon_client.get_snapshots(_PREMARKET_SCAN_SYMBOLS)
    except Exception as e:
        logger.info("Polygon snapshot batch failed: %s", e)
        return None

    if not snapshots:
        return None

    movers = []
    for sym, snap in snapshots.items():
        change_pct = snap.get("change_percentage")
        price = snap.get("price") or snap.get("last")
        prev_close = snap.get("prev_close")

        if change_pct is None or price is None or prev_close is None:
            continue
        if abs(change_pct) < 0.5:  # Filter to >0.5% moves
            continue

        movers.append({
            "symbol": sym,
            "name": sym,  # Polygon snapshots don't include company name
            "price": price,
            "change": round(price - prev_close, 2) if prev_close else None,
            "changesPercentage": round(change_pct, 2),
            "volume": snap.get("volume") or 0,
        })

    movers.sort(key=lambda m: m["changesPercentage"], reverse=True)

    gainers = [m for m in movers if m["changesPercentage"] > 0][:10]
    losers = [m for m in movers if m["changesPercentage"] < 0]
    losers = sorted(losers, key=lambda m: m["changesPercentage"])[:10]

    return {"gainers": gainers, "losers": losers}


@router.get("/api/market/pre-market-movers")
async def get_pre_market_movers(request: Request) -> dict:
    """Pre-market gappers (top moving stocks before market open).

    Tries sources in order: FMP → Polygon snapshots.
    """
    fmp = request.app.state.fmp_client
    polygon = request.app.state.polygon_client

    # Source 1: FMP pre-market (may be plan-blocked)
    if fmp.is_available():
        try:
            quotes = await fmp.get_pre_market_quotes()
            if quotes:
                sorted_quotes = sorted(
                    quotes,
                    key=lambda q: q.get("changesPercentage") or 0,
                    reverse=True,
                )
                return {
                    "gainers": sorted_quotes[:10],
                    "losers": sorted_quotes[-10:][::-1],
                    "source": "fmp",
                }
        except Exception as e:
            logger.info("FMP pre-market unavailable: %s", e)

    # Source 2: Polygon snapshots (15-min delayed, but includes pre-market)
    try:
        data = await _get_premarket_from_polygon(polygon)
        if data and (data.get("gainers") or data.get("losers")):
            data["source"] = "polygon"
            return data
    except Exception as e:
        logger.warning("Polygon pre-market failed: %s", e)

    return {
        "gainers": [],
        "losers": [],
        "error": "Pre-market data not available from current sources",
    }


@router.get("/api/market/upgrades-downgrades")
async def get_market_upgrades_downgrades(
    request: Request,
    limit: int = Query(30, ge=1, le=100),
) -> dict:
    """Recent analyst upgrades and downgrades."""
    fmp = request.app.state.fmp_client

    if not fmp.is_available():
        raise HTTPException(status_code=503, detail="FMP not available")

    grades = await fmp.get_upgrades_downgrades(limit=limit)
    if not grades:
        return {"upgrades": [], "downgrades": [], "all": []}

    upgrades = []
    downgrades = []

    for g in grades:
        action = (g.get("action") or "").lower()
        if "upgrade" in action or "raise" in action or "buy" in action:
            upgrades.append(g)
        elif "downgrade" in action or "lower" in action or "sell" in action:
            downgrades.append(g)

    return {
        "upgrades": upgrades[:15],
        "downgrades": downgrades[:15],
        "all": grades,
    }
