from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["regime"])

# Broad-market proxy symbols for regime context charts.
# VTI  — total US stock market
# VXUS — ex-US / international equity
# EFA  — developed international markets
# BND  — broad US bond aggregate (fixed-income context)
# TLT  — 20+ year treasury bonds (long-duration rates sensitivity)
# UUP  — US Dollar Index (dollar pressure / trade-weighted strength)
# HYG  — high-yield corporate bonds (credit risk appetite)
# LQD  — investment-grade corporate bonds (credit quality / spread proxy)
_PROXY_SYMBOLS: list[str] = ["VTI", "VXUS", "EFA", "BND", "TLT", "UUP", "HYG", "LQD"]
_PROXY_LOOKBACK_DAYS = 14  # ~2 weeks — short-term regime context
_PROXY_CACHE_KEY = "regime_proxies:v6"
_PROXY_CACHE_TTL = 120  # 2 minutes — fresher for short-term view
_PROXY_MAX_POINTS = 30  # max data points per chart after downsampling


@router.get("/regime")
async def get_regime(request: Request) -> dict:
    return await request.app.state.regime_service.get_regime()


@router.get("/regime/proxies")
async def get_regime_proxies(request: Request) -> dict[str, Any]:
    """Return ~2-week proxy chart data for broad-market ETFs.

    Tries 15-min intraday bars first (Polygon → Tradier) for denser,
    more-live charts.  Falls back to daily close bars if intraday data
    is unavailable for a symbol.

    Response shape:
      {
        "as_of": "ISO timestamp",
        "proxies": {
          "VTI":  { "symbol": "VTI", "history": [{"date":"…","close":…}, …],
                    "change_pct": …, "bar_size": "15min" | "daily" },
          …
        }
      }
    """
    cache = getattr(request.app.state, "cache", None)
    if cache is not None:
        cached = await cache.get(_PROXY_CACHE_KEY)
        if cached is not None:
            return cached

    bds = request.app.state.base_data_service

    async def _fetch_one(symbol: str) -> dict[str, Any]:
        try:
            # Try intraday hourly bars first (~65 bars over 14 days)
            bars = await bds.get_intraday_bars(symbol, lookback_days=_PROXY_LOOKBACK_DAYS)
            bar_size = "1h"

            # Fallback to daily if intraday returned nothing
            if not bars:
                daily = await bds.get_prices_history_dated(symbol, lookback_days=_PROXY_LOOKBACK_DAYS)
                bars = [b for b in (daily or []) if b.get("close") is not None]
                bar_size = "daily"
            else:
                bars = [b for b in bars if b.get("close") is not None]

            change_pct = None
            if len(bars) >= 2:
                first_close = float(bars[0]["close"])
                last_close = float(bars[-1]["close"])
                if first_close:
                    change_pct = round((last_close - first_close) / first_close, 4)

            # Downsample to _PROXY_MAX_POINTS via stride (keep first + last)
            if len(bars) > _PROXY_MAX_POINTS:
                stride = max(1, len(bars) // _PROXY_MAX_POINTS)
                sampled = bars[::stride]
                if sampled[-1] is not bars[-1]:
                    sampled.append(bars[-1])
                bars = sampled

            return {
                "symbol": symbol,
                "history": [{"date": b.get("date"), "close": float(b["close"])} for b in bars],
                "change_pct": change_pct,
                "bar_size": bar_size,
            }
        except Exception as exc:
            logger.warning("regime proxy fetch failed symbol=%s error=%s", symbol, exc)
            return {"symbol": symbol, "history": [], "change_pct": None, "bar_size": "unknown"}

    results = await asyncio.gather(*[_fetch_one(s) for s in _PROXY_SYMBOLS])
    proxies = {r["symbol"]: r for r in results}

    payload: dict[str, Any] = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "proxies": proxies,
    }

    if cache is not None:
        await cache.set(_PROXY_CACHE_KEY, payload, _PROXY_CACHE_TTL)

    return payload
