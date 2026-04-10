"""API routes for pre-market intelligence and futures data."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pre-market", tags=["pre-market"])


@router.get("/briefing")
async def get_briefing(request: Request) -> dict:
    """Full pre-market intelligence briefing.

    Includes gap analysis, VIX term structure, overnight regime signal,
    cross-asset confirmation, and position exposure alerts.
    """
    service = request.app.state.pre_market_service
    return await service.build_briefing()


@router.get("/snapshots")
async def get_snapshots(request: Request) -> dict:
    """All futures/index snapshots (latest price, change, etc.)."""
    client = request.app.state.futures_client
    return await client.get_all_snapshots()


@router.get("/snapshot/{instrument}")
async def get_snapshot(request: Request, instrument: str) -> dict:
    """Single futures/index snapshot."""
    client = request.app.state.futures_client
    return await client.get_snapshot(instrument)


@router.get("/bars/{instrument}")
async def get_bars(
    request: Request,
    instrument: str,
    timeframe: str = Query("1h", pattern="^(1min|5min|15min|30min|1hour|1day|1m|5m|15m|30m|1h|1d)$"),
    days: int = Query(5, ge=1, le=30),
) -> dict:
    """Historical bars for a single futures/index instrument."""
    client = request.app.state.futures_client
    bars = await client.get_bars(instrument, timeframe=timeframe, days=days)
    # Include prior session close for baseline normalisation (e.g. 48h charts)
    snap = await client.get_snapshot(instrument)
    prior_close = snap.get("prev_close") if snap else None
    return {"instrument": instrument, "timeframe": timeframe, "days": days,
            "bars": bars, "prior_close": prior_close}


@router.get("/vix-term-structure")
async def get_vix_term_structure(request: Request) -> dict:
    """VIX spot vs. VXX-implied term structure."""
    client = request.app.state.futures_client
    return await client.get_vix_term_structure()


@router.get("/health")
async def get_health(request: Request) -> dict:
    """Futures data health check."""
    client = request.app.state.futures_client
    healthy = await client.health()
    return {"healthy": healthy}
