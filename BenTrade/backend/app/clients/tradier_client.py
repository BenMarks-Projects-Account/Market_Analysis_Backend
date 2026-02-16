from typing import Any

import httpx

from app.config import Settings
from app.utils.cache import TTLCache
from app.utils.http import request_json


class TradierClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient, cache: TTLCache) -> None:
        self.settings = settings
        self.http_client = http_client
        self.cache = cache

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.TRADIER_TOKEN}",
            "Accept": "application/json",
        }

    def account_endpoint(self, path: str) -> str:
        clean_path = path.lstrip("/")
        return f"{self.settings.TRADIER_BASE_URL}/accounts/{self.settings.TRADIER_ACCOUNT_ID}/{clean_path}"

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        key = f"tradier:quote:{symbol.upper()}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/quotes"

        async def _load() -> dict[str, Any]:
            payload = await request_json(
                self.http_client,
                "GET",
                url,
                params={"symbols": symbol.upper()},
                headers=self._headers,
            )
            quote_obj = (payload.get("quotes") or {}).get("quote")
            if isinstance(quote_obj, list):
                quote_obj = quote_obj[0] if quote_obj else {}
            return quote_obj or {}

        return await self.cache.get_or_set(key, self.settings.QUOTE_CACHE_TTL_SECONDS, _load)

    async def get_expirations(self, symbol: str) -> list[str]:
        url = f"{self.settings.TRADIER_BASE_URL}/markets/options/expirations"
        payload = await request_json(
            self.http_client,
            "GET",
            url,
            params={"symbol": symbol.upper(), "includeAllRoots": "true"},
            headers=self._headers,
        )
        dates = ((payload.get("expirations") or {}).get("date")) or []
        if isinstance(dates, str):
            return [dates]
        return [str(x) for x in dates]

    async def get_chain(self, symbol: str, expiration: str, greeks: bool = True) -> list[dict[str, Any]]:
        key = f"tradier:chain:{symbol.upper()}:{expiration}:{int(greeks)}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/options/chains"

        async def _load() -> list[dict[str, Any]]:
            payload = await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "symbol": symbol.upper(),
                    "expiration": expiration,
                    "greeks": str(greeks).lower(),
                },
                headers=self._headers,
            )
            options = ((payload.get("options") or {}).get("option")) or []
            if isinstance(options, dict):
                return [options]
            return options

        return await self.cache.get_or_set(key, self.settings.CHAIN_CACHE_TTL_SECONDS, _load)

    async def get_daily_closes(self, symbol: str, start_date: str, end_date: str) -> list[float]:
        key = f"tradier:history:{symbol.upper()}:{start_date}:{end_date}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/history"

        async def _load() -> list[float]:
            payload = await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "symbol": symbol.upper(),
                    "interval": "daily",
                    "start": start_date,
                    "end": end_date,
                },
                headers=self._headers,
            )

            days = ((payload.get("history") or {}).get("day")) or []
            if isinstance(days, dict):
                days = [days]

            closes: list[float] = []
            for day in days:
                if not isinstance(day, dict):
                    continue
                close = day.get("close")
                if close is None:
                    continue
                try:
                    closes.append(float(close))
                except (TypeError, ValueError):
                    continue
            return closes

        return await self.cache.get_or_set(key, self.settings.CANDLES_CACHE_TTL_SECONDS, _load)

    async def health(self) -> bool:
        try:
            await self.get_quote("SPY")
            return True
        except Exception:
            return False

    async def get_balances(self) -> dict[str, Any]:
        url = self.account_endpoint("balances")
        return await request_json(self.http_client, "GET", url, headers=self._headers)

    async def get_positions(self) -> dict[str, Any]:
        url = self.account_endpoint("positions")
        return await request_json(self.http_client, "GET", url, headers=self._headers)

    async def get_orders(self, status: str | None = None) -> dict[str, Any]:
        url = self.account_endpoint("orders")
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        return await request_json(self.http_client, "GET", url, headers=self._headers, params=params or None)

    async def get_quotes(self, symbols: list[str]) -> dict[str, Any]:
        normalized = [str(symbol or "").upper().strip() for symbol in (symbols or []) if str(symbol or "").strip()]
        if not normalized:
            return {}

        key = "tradier:quotes:" + ",".join(sorted(set(normalized)))
        url = f"{self.settings.TRADIER_BASE_URL}/markets/quotes"

        async def _load() -> dict[str, Any]:
            payload = await request_json(
                self.http_client,
                "GET",
                url,
                params={"symbols": ",".join(sorted(set(normalized)))},
                headers=self._headers,
            )
            quote_obj = (payload.get("quotes") or {}).get("quote")
            if isinstance(quote_obj, dict):
                quote_obj = [quote_obj]

            out: dict[str, Any] = {}
            for item in quote_obj or []:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or "").upper()
                if symbol:
                    out[symbol] = item
            return out

        return await self.cache.get_or_set(key, self.settings.QUOTE_CACHE_TTL_SECONDS, _load)
