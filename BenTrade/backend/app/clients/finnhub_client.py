from typing import Any

import httpx

from app.config import Settings
from app.utils.cache import TTLCache
from app.utils.http import request_json


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
