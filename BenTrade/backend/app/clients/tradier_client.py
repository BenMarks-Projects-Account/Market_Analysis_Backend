from typing import Any
import logging
import re
from datetime import datetime, timezone

import httpx

from app.config import Settings
from app.utils.cache import TTLCache
from app.utils.http import request_json


logger = logging.getLogger(__name__)
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.\-]{1,10}$")


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

    @staticmethod
    def _normalize_symbol(symbol: Any) -> str | None:
        value = str(symbol or "").strip().upper()
        if not value:
            return None
        if not _SYMBOL_PATTERN.fullmatch(value):
            return None
        return value

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed != parsed or parsed in (float("inf"), float("-inf")):
            return None
        return parsed

    def _sanitize_quote(self, quote_obj: dict[str, Any], *, symbol: str) -> dict[str, Any]:
        if not isinstance(quote_obj, dict):
            return {}

        cleaned = dict(quote_obj)
        bid = self._to_float(cleaned.get("bid"))
        ask = self._to_float(cleaned.get("ask"))

        if bid is not None and bid < 0:
            logger.warning("event=tradier_quote_validation symbol=%s reason=negative_bid", symbol)
            bid = None
        if ask is not None and ask < 0:
            logger.warning("event=tradier_quote_validation symbol=%s reason=negative_ask", symbol)
            ask = None
        if bid is not None and ask is not None and ask < bid:
            logger.warning("event=tradier_quote_validation symbol=%s reason=ask_below_bid", symbol)
            bid = None
            ask = None

        cleaned["bid"] = bid
        cleaned["ask"] = ask
        return cleaned

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            logger.warning("event=tradier_quote_validation reason=invalid_symbol symbol=%s", symbol)
            return {}

        key = f"tradier:quote:{normalized_symbol}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/quotes"

        async def _load() -> dict[str, Any]:
            payload = await request_json(
                self.http_client,
                "GET",
                url,
                params={"symbols": normalized_symbol},
                headers=self._headers,
            )
            quote_obj = (payload.get("quotes") or {}).get("quote")
            if isinstance(quote_obj, list):
                quote_obj = quote_obj[0] if quote_obj else {}
            return self._sanitize_quote(quote_obj or {}, symbol=normalized_symbol)

        return await self.cache.get_or_set(key, self.settings.QUOTE_CACHE_TTL_SECONDS, _load)

    async def get_expirations(self, symbol: str) -> list[str]:
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            logger.warning("event=tradier_expirations_validation reason=invalid_symbol symbol=%s", symbol)
            return []

        url = f"{self.settings.TRADIER_BASE_URL}/markets/options/expirations"
        payload = await request_json(
            self.http_client,
            "GET",
            url,
            params={"symbol": normalized_symbol, "includeAllRoots": "true"},
            headers=self._headers,
        )
        dates = ((payload.get("expirations") or {}).get("date")) or []
        if isinstance(dates, str):
            dates = [dates]

        valid: list[str] = []
        for item in dates:
            value = str(item or "").strip()
            if not value:
                continue
            try:
                exp_date = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                logger.warning("event=tradier_expirations_validation symbol=%s reason=invalid_date value=%s", normalized_symbol, value)
                continue
            dte = (exp_date - datetime.now(timezone.utc).date()).days
            if dte < 0:
                logger.warning("event=tradier_expirations_validation symbol=%s reason=past_expiration value=%s", normalized_symbol, value)
                continue
            valid.append(value)
        return valid

    async def get_chain(self, symbol: str, expiration: str, greeks: bool = True) -> list[dict[str, Any]]:
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            logger.warning("event=tradier_chain_validation reason=invalid_symbol symbol=%s", symbol)
            return []

        key = f"tradier:chain:{normalized_symbol}:{expiration}:{int(greeks)}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/options/chains"

        async def _load() -> list[dict[str, Any]]:
            payload = await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "symbol": normalized_symbol,
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
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            logger.warning("event=tradier_history_validation reason=invalid_symbol symbol=%s", symbol)
            return []

        key = f"tradier:history:{normalized_symbol}:{start_date}:{end_date}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/history"

        async def _load() -> list[float]:
            payload = await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "symbol": normalized_symbol,
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
        normalized = [self._normalize_symbol(symbol) for symbol in (symbols or [])]
        normalized = [symbol for symbol in normalized if symbol]
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
                symbol = self._normalize_symbol(item.get("symbol"))
                if symbol:
                    out[symbol] = self._sanitize_quote(item, symbol=symbol)
            return out

        return await self.cache.get_or_set(key, self.settings.QUOTE_CACHE_TTL_SECONDS, _load)
