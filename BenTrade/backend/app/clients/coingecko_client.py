"""CoinGecko client for crypto risk sentiment data.

Uses the free /api/v3 endpoints (no API key required).
Provides: simple prices (BTC, ETH, SOL) and global market dominance.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"


class CoinGeckoClient:
    """Async CoinGecko client with TTL caching."""

    def __init__(self, http_client: httpx.AsyncClient, cache: TTLCache) -> None:
        self.http_client = http_client
        self.cache = cache

    async def get_simple_prices(
        self, ids: list[str] | None = None
    ) -> dict[str, Any] | None:
        """Get simple price data for crypto symbols.

        Default: BTC, ETH, SOL.
        Returns dict keyed by coin id, e.g. {"bitcoin": {"usd": 65000, ...}}.
        """
        ids = ids or ["bitcoin", "ethereum", "solana"]
        cache_key = f"coingecko:prices:{','.join(sorted(ids))}"

        async def _load() -> dict[str, Any] | None:
            url = f"{BASE_URL}/simple/price"
            params = {
                "ids": ",".join(ids),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
                "include_market_cap": "true",
            }
            try:
                resp = await self.http_client.get(url, params=params, timeout=10.0)
            except httpx.HTTPError as exc:
                logger.warning("CoinGecko network error (prices): %s", exc)
                return None

            if resp.status_code == 429:
                logger.warning("CoinGecko 429 rate-limited on /simple/price")
                return None
            if resp.status_code >= 400:
                logger.warning("CoinGecko HTTP %d on /simple/price: %s", resp.status_code, resp.text[:200])
                return None

            try:
                return resp.json()
            except Exception:
                logger.warning("CoinGecko bad JSON on /simple/price")
                return None

        return await self.cache.get_or_set(cache_key, 60, _load)

    async def get_market_dominance(self) -> dict[str, Any] | None:
        """Get BTC dominance and total market cap from /global."""
        cache_key = "coingecko:global"

        async def _load() -> dict[str, Any] | None:
            url = f"{BASE_URL}/global"
            try:
                resp = await self.http_client.get(url, timeout=10.0)
            except httpx.HTTPError as exc:
                logger.warning("CoinGecko network error (global): %s", exc)
                return None

            if resp.status_code == 429:
                logger.warning("CoinGecko 429 rate-limited on /global")
                return None
            if resp.status_code >= 400:
                logger.warning("CoinGecko HTTP %d on /global: %s", resp.status_code, resp.text[:200])
                return None

            try:
                data = resp.json()
                return data.get("data", {})
            except Exception:
                logger.warning("CoinGecko bad JSON on /global")
                return None

        return await self.cache.get_or_set(cache_key, 60, _load)
