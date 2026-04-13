"""FMP (Financial Modeling Prep) client for market intelligence data.

Provides: market movers, sector rotation, pre-market quotes,
analyst upgrades/downgrades.

Uses the ``/stable`` API base (paid Starter plan, 300 calls/min).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import Settings
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)


class FMPClient:
    """Async FMP client with TTL caching and 402/401 gating."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
        cache: TTLCache,
    ) -> None:
        self.settings = settings
        self.http_client = http_client
        self.cache = cache
        self._disabled_paths: dict[str, float] = {}  # path → expiry timestamp

    # TTL for disabled paths (retry after 1 hour)
    _DISABLE_TTL = 3600

    def is_available(self) -> bool:
        return bool(self.settings.FMP_API_KEY)

    # ── Public data methods ────────────────────────────────────

    async def get_market_gainers(self) -> list[dict[str, Any]] | None:
        return await self._fetch("/biggest-gainers", ttl=60)

    async def get_market_losers(self) -> list[dict[str, Any]] | None:
        return await self._fetch("/biggest-losers", ttl=60)

    async def get_market_actives(self) -> list[dict[str, Any]] | None:
        return await self._fetch("/most-actives", ttl=60)

    async def get_sector_performance(self, date: str | None = None) -> list[dict[str, Any]] | None:
        from datetime import datetime, timezone
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return await self._fetch(
            "/sector-performance-snapshot",
            params={"date": date},
            ttl=60,
        )

    async def get_sector_performance_historical(self, sector: str, days: int = 63) -> list[dict[str, Any]] | None:
        return await self._fetch(
            "/historical-sector-performance",
            params={"sector": sector, "limit": days},
            ttl=300,
        )

    async def get_pre_market_quotes(self) -> list[dict[str, Any]] | None:
        return await self._fetch("/pre-post-market", ttl=60)

    async def get_upgrades_downgrades(self, limit: int = 50) -> list[dict[str, Any]] | None:
        return await self._fetch(
            "/grades-latest-news",
            params={"limit": limit},
            ttl=60,
        )

    # ── Congressional / Insider trading methods ──────────────────

    async def get_senate_latest(self) -> list[dict[str, Any]] | None:
        """Latest Senate STOCK Act disclosures."""
        return await self._fetch("/senate-latest", ttl=300)

    async def get_house_latest(self) -> list[dict[str, Any]] | None:
        """Latest House STOCK Act disclosures."""
        return await self._fetch("/house-latest", ttl=300)

    async def get_insider_trading_latest(self, limit: int = 100) -> list[dict[str, Any]] | None:
        """Latest insider trades across all companies."""
        return await self._fetch(
            "/insider-trading-latest",
            params={"limit": limit},
            ttl=300,
        )

    # ── Economic calendar ───────────────────────────────────────

    async def get_economic_calendar(
        self, from_date: str, to_date: str,
    ) -> list[dict[str, Any]] | None:
        """Fetch economic calendar events from FMP.

        Returns list of events with: event, date, country, actual, estimate,
        previous, impact, etc.  Returns None if plan-blocked or unavailable.
        """
        return await self._fetch(
            "/economic-calendar",
            params={"from": from_date, "to": to_date},
            ttl=300,
        )

    # ── Breadth / screener methods ─────────────────────────────

    async def get_stock_screener(
        self,
        *,
        market_cap_min: int = 100_000_000,
        exchange: str = "nyse,nasdaq",
        limit: int = 5000,
    ) -> list[dict[str, Any]] | None:
        """Fetch broad stock screener for breadth calculations.

        Cached for 120s since this is a heavy call returning thousands of rows.
        """
        return await self._fetch(
            "/stock-screener",
            params={
                "marketCapMoreThan": market_cap_min,
                "isActivelyTrading": "true",
                "exchange": exchange,
                "limit": limit,
            },
            ttl=120,
        )

    # ── Internal fetch with caching + 402 gating ──────────────

    async def _fetch(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        ttl: int = 60,
    ) -> list[dict[str, Any]] | None:
        if not self.is_available():
            return None

        if path in self._disabled_paths:
            if time.time() < self._disabled_paths[path]:
                return None
            # TTL expired — retry this path
            logger.info("FMP re-enabling previously disabled path %s", path)
            del self._disabled_paths[path]

        cache_key = f"fmp:{path}:{params or ''}"

        async def _load() -> list[dict[str, Any]] | None:
            url = f"{self.settings.FMP_BASE_URL}{path}"
            full_params: dict[str, Any] = {"apikey": self.settings.FMP_API_KEY}
            if params:
                full_params.update(params)

            try:
                resp = await self.http_client.get(url, params=full_params, timeout=10.0)
            except httpx.HTTPError as exc:
                logger.warning("FMP network error on %s: %s", path, exc)
                return None

            if resp.status_code == 402:
                logger.warning("FMP 402 (plan limit) on %s — disabling for 1 hour", path)
                self._disabled_paths[path] = time.time() + self._DISABLE_TTL
                return None

            if resp.status_code == 401:
                logger.error("FMP 401 on %s — check FMP_API_KEY", path)
                return None

            if resp.status_code >= 400:
                logger.warning("FMP HTTP %d on %s: %s", resp.status_code, path, resp.text[:200])
                return None

            try:
                return resp.json()
            except Exception:
                logger.warning("FMP bad JSON on %s", path)
                return None

        return await self.cache.get_or_set(cache_key, ttl, _load)
