import asyncio
from typing import Any

import yfinance as yf

from app.config import Settings
from app.utils.cache import TTLCache


class YahooClient:
    def __init__(self, settings: Settings, cache: TTLCache) -> None:
        self.settings = settings
        self.cache = cache

    async def get_daily_closes(self, symbol: str, period: str = "1y") -> list[float]:
        key = f"yahoo:daily_closes:{symbol.upper()}:{period}"

        async def _load() -> list[float]:
            def _fetch() -> list[float]:
                history = yf.Ticker(symbol.upper()).history(period=period, interval="1d", auto_adjust=False, actions=False)
                if history is None or history.empty or "Close" not in history:
                    return []

                closes: list[float] = []
                for value in history["Close"].tolist():
                    if value is None:
                        continue
                    try:
                        closes.append(float(value))
                    except (TypeError, ValueError):
                        continue
                return closes

            return await asyncio.to_thread(_fetch)

        return await self.cache.get_or_set(key, self.settings.CANDLES_CACHE_TTL_SECONDS, _load)

    async def health(self) -> bool:
        try:
            closes = await self.get_daily_closes("SPY")
            return len(closes) > 0
        except Exception:
            return False
