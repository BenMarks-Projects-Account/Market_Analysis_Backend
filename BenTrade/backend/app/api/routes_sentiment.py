"""Sentiment API routes — crypto risk sentiment.

Endpoints:
  GET  /api/sentiment/crypto     → crypto risk sentiment (BTC/ETH/SOL)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(tags=["sentiment"])


# ── Helpers ────────────────────────────────────────────────────

def _format_crypto(data: dict | None) -> dict:
    if not data:
        return {"price": None, "change_24h": None, "volume_24h": None, "market_cap": None}
    return {
        "price": data.get("usd"),
        "change_24h": data.get("usd_24h_change"),
        "volume_24h": data.get("usd_24h_vol"),
        "market_cap": data.get("usd_market_cap"),
    }


# ── Routes ─────────────────────────────────────────────────────

@router.get("/api/sentiment/crypto")
async def get_crypto_sentiment(request: Request) -> dict:
    """Crypto risk sentiment indicators (BTC, ETH, SOL)."""
    coingecko = request.app.state.coingecko_client

    prices = await coingecko.get_simple_prices(["bitcoin", "ethereum", "solana"])
    dominance = await coingecko.get_market_dominance()

    if not prices:
        raise HTTPException(status_code=503, detail="Crypto data unavailable")

    return {
        "btc": _format_crypto(prices.get("bitcoin")),
        "eth": _format_crypto(prices.get("ethereum")),
        "sol": _format_crypto(prices.get("solana")),
        "btc_dominance": (
            dominance.get("market_cap_percentage", {}).get("btc")
            if dominance else None
        ),
        "total_market_cap_usd": (
            dominance.get("total_market_cap", {}).get("usd")
            if dominance else None
        ),
    }
