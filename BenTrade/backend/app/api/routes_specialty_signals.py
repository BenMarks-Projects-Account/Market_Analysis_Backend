"""Specialty signal routes — congressional trading, insider clusters, unusual options.

Endpoints:
  GET  /api/signals/congress          → recent STOCK Act disclosures
  GET  /api/signals/insider-clusters  → companies with cluster insider buying
       BLOCKED: Requires FMP higher tier for /insider-trading-latest.
       Section removed from UI. Route kept for future alternative sources (SEC EDGAR).
  GET  /api/signals/unusual-options   → high vol/OI ratio contracts
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)
router = APIRouter(tags=["specialty-signals"])


# ─── Congressional Trading ──────────────────────────────────────

@router.get("/api/signals/congress")
async def get_congressional_trading(
    request: Request,
    limit: int = Query(30, ge=1, le=100),
) -> dict:
    """Latest congressional trading disclosures (Senate + House).

    Combines both chambers, sorted by disclosure date descending.
    Aggregates by ticker to surface trending tickers.
    """
    fmp = request.app.state.fmp_client
    if not fmp.is_available():
        raise HTTPException(status_code=503, detail="FMP not available")

    senate_task = fmp.get_senate_latest()
    house_task = fmp.get_house_latest()
    senate, house = await asyncio.gather(senate_task, house_task, return_exceptions=True)

    if isinstance(senate, Exception):
        logger.warning("Senate fetch error: %s", senate)
        senate = None
    if isinstance(house, Exception):
        logger.warning("House fetch error: %s", house)
        house = None

    all_trades: list[dict] = []

    if senate:
        for trade in senate[:limit]:
            all_trades.append({
                "chamber": "Senate",
                "name": _safe_name(trade),
                "symbol": trade.get("symbol") or trade.get("ticker"),
                "asset": trade.get("assetDescription") or trade.get("assetName"),
                "type": trade.get("type"),
                "amount": trade.get("amount"),
                "date_traded": trade.get("transactionDate"),
                "date_disclosed": trade.get("disclosureDate"),
            })

    if house:
        for trade in house[:limit]:
            all_trades.append({
                "chamber": "House",
                "name": _safe_name(trade),
                "symbol": trade.get("ticker") or trade.get("symbol"),
                "asset": trade.get("assetDescription"),
                "type": trade.get("type"),
                "amount": trade.get("amount"),
                "date_traded": trade.get("transactionDate"),
                "date_disclosed": trade.get("disclosureDate"),
            })

    all_trades.sort(
        key=lambda t: t.get("date_disclosed") or "",
        reverse=True,
    )

    ticker_counts = Counter(
        t["symbol"]
        for t in all_trades
        if t.get("symbol") and t["symbol"] != "N/A"
    )
    trending = [
        {"symbol": sym, "trade_count": count}
        for sym, count in ticker_counts.most_common(10)
    ]

    return {
        "trades": all_trades[:limit],
        "trending_tickers": trending,
        "total_count": len(all_trades),
    }


# ─── Insider Cluster Buying ────────────────────────────────────

@router.get("/api/signals/insider-clusters")
async def get_insider_clusters(
    request: Request,
    days: int = Query(30, ge=7, le=90),
) -> dict:
    """Find companies with cluster insider buying (3+ unique buyers).

    A "cluster" = 3+ unique insiders purchasing the same stock
    within the lookback window.
    """
    fmp = request.app.state.fmp_client
    if not fmp.is_available():
        raise HTTPException(status_code=503, detail="FMP not available")

    raw = await fmp.get_insider_trading_latest(limit=500)
    if not raw:
        return {"clusters": [], "lookback_days": days}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for tx in raw:
        tx_type = tx.get("transactionType", "")
        if "P" not in tx_type and "Purchase" not in tx_type:
            continue
        try:
            date_str = (tx.get("transactionDate") or "").split("T")[0]
            tx_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if tx_date < cutoff:
                continue
        except (ValueError, AttributeError):
            continue

        symbol = tx.get("symbol")
        if symbol:
            by_symbol[symbol].append(tx)

    clusters: list[dict] = []
    for symbol, transactions in by_symbol.items():
        unique_buyers = set(
            tx.get("reportingName") for tx in transactions if tx.get("reportingName")
        )
        if len(unique_buyers) >= 3:
            total_shares = sum(
                tx.get("securitiesTransacted", 0) or 0
                for tx in transactions
            )
            total_value = sum(
                (tx.get("securitiesTransacted", 0) or 0)
                * (tx.get("price", 0) or 0)
                for tx in transactions
            )
            clusters.append({
                "symbol": symbol,
                "company_name": transactions[0].get("companyName"),
                "unique_buyers": len(unique_buyers),
                "transaction_count": len(transactions),
                "total_shares": total_shares,
                "total_value": round(total_value, 2),
                "buyers": sorted(unique_buyers)[:5],
            })

    clusters.sort(
        key=lambda c: (c["unique_buyers"], c["total_value"]),
        reverse=True,
    )

    return {
        "clusters": clusters[:15],
        "lookback_days": days,
    }


# ─── Unusual Options Activity ──────────────────────────────────

@router.get("/api/signals/unusual-options")
async def get_unusual_options(request: Request) -> dict:
    """Find unusual options activity on today's most active stocks.

    Heuristic: contract volume > 2× its open interest = unusual.
    Only considers contracts with volume >= 500.
    Fetches chains in parallel to reduce latency.
    """
    fmp = request.app.state.fmp_client
    tradier = request.app.state.tradier_client

    # Get today's most active stocks from FMP
    actives_raw = await fmp.get_market_actives() if fmp.is_available() else None
    if not actives_raw:
        return {"unusual": []}

    actives = actives_raw[:5]  # Reduced from 10 → 5 for speed

    async def _fetch_chain(stock: dict) -> tuple[str, dict, list[dict]] | None:
        """Fetch nearest-expiry chain for one stock. Returns None on failure."""
        symbol = stock.get("symbol")
        if not symbol:
            return None
        try:
            expirations = await tradier.get_expirations(symbol)
            if not expirations:
                logger.debug("UOA: no expirations for %s", symbol)
                return None
            chain = await tradier.get_chain(symbol, expirations[0], greeks=False)
            return (symbol, stock, chain) if chain else None
        except Exception as e:
            logger.warning("UOA: chain fetch failed for %s: %s", symbol, e)
            return None

    # Run all chain fetches in parallel
    results = await asyncio.gather(
        *[_fetch_chain(stock) for stock in actives],
        return_exceptions=False,
    )

    unusual_contracts: list[dict] = []

    for result in results:
        if not result:
            continue
        symbol, stock, chain = result
        underlying_price = stock.get("price")

        for contract in chain:
            volume = contract.get("volume", 0) or 0
            oi = contract.get("open_interest", 0) or 0

            if volume < 500:
                continue

            if oi > 0 and volume > oi * 2:
                unusual_contracts.append({
                    "symbol": symbol,
                    "type": contract.get("option_type"),
                    "strike": contract.get("strike"),
                    "expiration": contract.get("expiration_date"),
                    "volume": volume,
                    "open_interest": oi,
                    "vol_oi_ratio": round(volume / oi, 2),
                    "last_price": contract.get("last"),
                    "underlying_price": underlying_price,
                })

    unusual_contracts.sort(key=lambda c: c["vol_oi_ratio"], reverse=True)

    return {
        "unusual": unusual_contracts[:20],
    }


# ─── Helpers ────────────────────────────────────────────────────

def _safe_name(trade: dict) -> str:
    first = (trade.get("firstName") or "").strip()
    last = (trade.get("lastName") or "").strip()
    return f"{first} {last}".strip() or "Unknown"
