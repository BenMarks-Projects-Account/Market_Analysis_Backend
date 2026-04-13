from typing import Any

import httpx

from app.config import Settings
from app.utils.cache import TTLCache
from app.utils.http import request_json


# Symbols where per-symbol earnings checks are skipped (ETFs don't report earnings).
_ETF_SYMBOLS = frozenset({
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "XLB",
    "XLI", "XLP", "XLU", "XLC", "XLRE", "XLY", "GLD", "SLV", "TLT",
    "HYG", "LQD", "EEM", "EFA", "VXX", "UVXY", "SQQQ", "TQQQ",
})


class FinnhubClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient, cache: TTLCache) -> None:
        self.settings = settings
        self.http_client = http_client
        self.cache = cache

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        url = f"{self.settings.FINNHUB_BASE_URL}/quote"
        return await request_json(
            self.http_client,
            "GET",
            url,
            params={"symbol": symbol.upper(), "token": self.settings.FINNHUB_KEY},
        )

    async def get_daily_candles(self, symbol: str, from_unix: int, to_unix: int) -> dict[str, Any]:
        key = f"finnhub:candles:{symbol.upper()}:{from_unix}:{to_unix}"
        url = f"{self.settings.FINNHUB_BASE_URL}/stock/candle"

        async def _load() -> dict[str, Any]:
            return await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "symbol": symbol.upper(),
                    "resolution": "D",
                    "from": from_unix,
                    "to": to_unix,
                    "token": self.settings.FINNHUB_KEY,
                },
            )

        return await self.cache.get_or_set(key, self.settings.CANDLES_CACHE_TTL_SECONDS, _load)

    async def health(self) -> bool:
        try:
            payload = await self.get_quote("SPY")
            return payload is not None
        except Exception:
            return False

    async def get_economic_calendar(
        self,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        """Fetch economic calendar events (CPI, NFP, FOMC, etc.).

        Parameters
        ----------
        from_date : str
            ISO date string for range start (e.g. "2026-04-10").
        to_date : str
            ISO date string for range end (e.g. "2026-04-11").

        Returns
        -------
        dict – Finnhub response with ``economicCalendar`` list.
        """
        cache_key = f"finnhub:economic_cal:{from_date}:{to_date}"

        async def _load() -> dict[str, Any]:
            url = f"{self.settings.FINNHUB_BASE_URL}/calendar/economic"
            return await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "from": from_date,
                    "to": to_date,
                    "token": self.settings.FINNHUB_KEY,
                },
            )

        return await self.cache.get_or_set(cache_key, 300, _load)

    async def get_market_news(self, category: str = "general") -> list[dict[str, Any]]:
        """Fetch general market news headlines.

        Parameters
        ----------
        category : str
            One of: general, forex, crypto, merger.

        Returns
        -------
        list[dict] – News items with headline, summary, source, url, datetime, etc.
        """
        cache_key = f"finnhub:market_news:{category}"

        async def _load() -> list[dict[str, Any]]:
            url = f"{self.settings.FINNHUB_BASE_URL}/news"
            resp = await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "category": category,
                    "token": self.settings.FINNHUB_KEY,
                },
            )
            return resp if isinstance(resp, list) else []

        return await self.cache.get_or_set(cache_key, 60, _load)

    async def get_earnings_calendar_range(
        self,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """Fetch earnings calendar for a date range (all symbols).

        Parameters
        ----------
        from_date : str
            ISO date string for range start.
        to_date : str
            ISO date string for range end.

        Returns
        -------
        list[dict] – Earnings entries with symbol, date, hour, eps/revenue estimates.
        """
        cache_key = f"finnhub:earnings_range:{from_date}:{to_date}"

        async def _load() -> list[dict[str, Any]]:
            url = f"{self.settings.FINNHUB_BASE_URL}/calendar/earnings"
            resp = await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "from": from_date,
                    "to": to_date,
                    "token": self.settings.FINNHUB_KEY,
                },
            )
            return resp.get("earningsCalendar", []) if resp else []

        return await self.cache.get_or_set(cache_key, 3600, _load)

    async def get_earnings_calendar(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """Fetch upcoming earnings dates for a symbol.

        Parameters
        ----------
        symbol : str
            Ticker symbol (e.g. "AAPL").
        from_date : str
            ISO date string for range start (e.g. "2026-03-27").
        to_date : str
            ISO date string for range end (e.g. "2026-04-10").

        Returns
        -------
        list[dict] – List of earnings calendar entries from Finnhub.
            Each dict typically has: date, epsActual, epsEstimate,
            hour, quarter, revenueActual, revenueEstimate, symbol, year.
        """
        symbol_upper = symbol.upper()
        if symbol_upper in _ETF_SYMBOLS:
            return []

        cache_key = f"finnhub:earnings:{symbol_upper}:{from_date}:{to_date}"

        async def _load() -> list[dict[str, Any]]:
            url = f"{self.settings.FINNHUB_BASE_URL}/calendar/earnings"
            resp = await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "symbol": symbol_upper,
                    "from": from_date,
                    "to": to_date,
                    "token": self.settings.FINNHUB_KEY,
                },
            )
            # Finnhub returns {"earningsCalendar": [...]}
            return resp.get("earningsCalendar", []) if resp else []

        return await self.cache.get_or_set(cache_key, 3600, _load)
