from typing import Any
import asyncio
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from app.config import Settings
from app.utils.cache import TTLCache
from app.utils.http import request_json, UpstreamError


logger = logging.getLogger(__name__)
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.\-]{1,10}$")
# OCC option symbol: root(1-6 upper) + date(6 digits) + P/C + strike(8 digits)
_OCC_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[PC]\d{8}$")

# Default: 2 req/sec = 120/minute (conservative for Tradier's limit)
_DEFAULT_MAX_PER_SECOND = 2.0
_MAX_429_RETRIES = 3


class _AsyncRateLimiter:
    """Leaky-bucket rate limiter with async support.

    Ensures requests are spaced at least ``1 / max_per_second`` seconds apart.
    Thread-safe via asyncio.Lock.
    """

    def __init__(self, max_per_second: float = _DEFAULT_MAX_PER_SECOND) -> None:
        self._min_interval = 1.0 / max_per_second
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def acquire(self) -> None:
        """Wait until a request slot is available."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request
            if elapsed < self._min_interval:
                delay = self._min_interval - elapsed
                logger.debug("event=tradier_rate_limiter_wait delay=%.3fs", delay)
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()


class TradierClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient, cache: TTLCache) -> None:
        self.settings = settings
        self.http_client = http_client
        self.cache = cache
        self._rate_limiter = _AsyncRateLimiter(max_per_second=_DEFAULT_MAX_PER_SECOND)

    async def _rate_limited_request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Wrap ``request_json`` with rate limiting and 429 retry.

        Acquires a rate-limiter slot before each attempt.  On HTTP 429,
        retries up to ``_MAX_429_RETRIES`` times with exponential backoff
        (respecting the ``Retry-After`` header when present).
        """
        for attempt in range(_MAX_429_RETRIES + 1):
            await self._rate_limiter.acquire()
            try:
                return await request_json(self.http_client, method, url, **kwargs)
            except UpstreamError as exc:
                status = (exc.details or {}).get("status_code")
                if status == 429 and attempt < _MAX_429_RETRIES:
                    body = (exc.details or {}).get("body", "")
                    # Respect Retry-After header if the upstream error preserved it
                    retry_after = 2 ** (attempt + 1)
                    delay = min(retry_after, 30)
                    logger.warning(
                        "event=tradier_rate_limited url=%s attempt=%d/%d delay=%ds body=%s",
                        url, attempt + 1, _MAX_429_RETRIES, delay, (body or "")[:200],
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

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
            payload = await self._rate_limited_request(
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

        key = f"tradier:expirations:{normalized_symbol}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/options/expirations"

        async def _load() -> list[str]:
            payload = await self._rate_limited_request(
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

        return await self.cache.get_or_set(key, self.settings.EXPIRATIONS_CACHE_TTL_SECONDS, _load)

    async def get_chain(self, symbol: str, expiration: str, greeks: bool = True) -> list[dict[str, Any]]:
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            logger.warning("event=tradier_chain_validation reason=invalid_symbol symbol=%s", symbol)
            return []

        key = f"tradier:chain:{normalized_symbol}:{expiration}:{int(greeks)}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/options/chains"

        async def _load() -> list[dict[str, Any]]:
            payload = await self._rate_limited_request(
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

    async def fetch_chain_raw_payload(
        self, symbol: str, expiration: str, greeks: bool = True,
    ) -> dict[str, Any]:
        """Fetch the full raw JSON payload from ``/markets/options/chains``.

        Bypasses cache.  Used by the snapshot-capture endpoint to save the
        complete, unextracted response body for later replay.
        """
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            return {}
        url = f"{self.settings.TRADIER_BASE_URL}/markets/options/chains"
        return await self._rate_limited_request(
            "GET",
            url,
            params={
                "symbol": normalized_symbol,
                "expiration": expiration,
                "greeks": str(greeks).lower(),
            },
            headers=self._headers,
        )

    async def get_daily_closes(self, symbol: str, start_date: str, end_date: str) -> list[float]:
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            logger.warning("event=tradier_history_validation reason=invalid_symbol symbol=%s", symbol)
            return []

        key = f"tradier:history:{normalized_symbol}:{start_date}:{end_date}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/history"

        async def _load() -> list[float]:
            payload = await self._rate_limited_request(
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

    async def get_daily_closes_dated(self, symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """Return daily bars as ``[{"date": "YYYY-MM-DD", "close": float}, ...]``."""
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            logger.warning("event=tradier_history_dated_validation reason=invalid_symbol symbol=%s", symbol)
            return []

        key = f"tradier:history_dated:{normalized_symbol}:{start_date}:{end_date}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/history"

        async def _load() -> list[dict[str, Any]]:
            payload = await self._rate_limited_request(
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

            result: list[dict[str, Any]] = []
            for day in days:
                if not isinstance(day, dict):
                    continue
                close = day.get("close")
                day_date = day.get("date")
                if close is None or day_date is None:
                    continue
                try:
                    result.append({"date": str(day_date), "close": float(close)})
                except (TypeError, ValueError):
                    continue
            return result

        return await self.cache.get_or_set(key, self.settings.CANDLES_CACHE_TTL_SECONDS, _load)

    async def get_daily_bars(self, symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """Return full OHLCV daily bars: ``[{"date", "open", "high", "low", "close", "volume"}, ...]``."""
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            return []

        key = f"tradier:bars:{normalized_symbol}:{start_date}:{end_date}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/history"

        async def _load() -> list[dict[str, Any]]:
            payload = await self._rate_limited_request(
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

            result: list[dict[str, Any]] = []
            for day in days:
                if not isinstance(day, dict):
                    continue
                close = day.get("close")
                day_date = day.get("date")
                if close is None or day_date is None:
                    continue
                try:
                    result.append({
                        "date": str(day_date),
                        "open": float(day["open"]) if day.get("open") is not None else None,
                        "high": float(day["high"]) if day.get("high") is not None else None,
                        "low": float(day["low"]) if day.get("low") is not None else None,
                        "close": float(close),
                        "volume": int(day["volume"]) if day.get("volume") is not None else None,
                    })
                except (TypeError, ValueError, KeyError):
                    continue
            return result

        return await self.cache.get_or_set(key, self.settings.CANDLES_CACHE_TTL_SECONDS, _load)

    async def get_intraday_bars(
        self, symbol: str, start_date: str, end_date: str, interval: str = "15min"
    ) -> list[dict[str, Any]]:
        """Return intraday bars with ISO-datetime timestamps.

        Tradier ``/markets/history`` with ``interval=15min`` returns bars keyed
        under ``history.day`` with ``date`` + ``time`` fields (or just ``date``
        as an ISO-like datetime).  Normalised to::

            [{"date": "2026-03-18T10:30:00", "close": float}, ...]
        """
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            return []

        key = f"tradier:intraday:{normalized_symbol}:{start_date}:{end_date}:{interval}"
        url = f"{self.settings.TRADIER_BASE_URL}/markets/history"

        async def _load() -> list[dict[str, Any]]:
            payload = await self._rate_limited_request(
                "GET",
                url,
                params={
                    "symbol": normalized_symbol,
                    "interval": interval,
                    "start": start_date,
                    "end": end_date,
                },
                headers=self._headers,
            )

            days = ((payload.get("history") or {}).get("day")) or []
            if isinstance(days, dict):
                days = [days]

            result: list[dict[str, Any]] = []
            for bar in days:
                if not isinstance(bar, dict):
                    continue
                close = bar.get("close")
                bar_date = bar.get("date")
                if close is None or bar_date is None:
                    continue
                # Tradier may return datetime or date+time fields
                bar_time = bar.get("time")
                iso_dt = str(bar_date)
                if bar_time and "T" not in iso_dt:
                    iso_dt = f"{iso_dt}T{bar_time}"
                try:
                    result.append({"date": iso_dt, "close": float(close)})
                except (TypeError, ValueError):
                    continue
            return result

        return await self.cache.get_or_set(key, self.settings.CANDLES_CACHE_TTL_SECONDS, _load)

    async def health(self) -> bool:
        try:
            await self.get_quote("SPY")
            return True
        except Exception:
            return False

    async def get_balances(self) -> dict[str, Any]:
        url = self.account_endpoint("balances")
        return await self._rate_limited_request("GET", url, headers=self._headers)

    async def get_positions(self) -> dict[str, Any]:
        url = self.account_endpoint("positions")
        return await self._rate_limited_request("GET", url, headers=self._headers)

    async def get_orders(self, status: str | None = None) -> dict[str, Any]:
        url = self.account_endpoint("orders")
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        return await self._rate_limited_request("GET", url, headers=self._headers, params=params or None)

    async def get_quotes(self, symbols: list[str]) -> dict[str, Any]:
        normalized = [self._normalize_symbol(symbol) for symbol in (symbols or [])]
        normalized = [symbol for symbol in normalized if symbol]
        if not normalized:
            return {}

        key = "tradier:quotes:" + ",".join(sorted(set(normalized)))
        url = f"{self.settings.TRADIER_BASE_URL}/markets/quotes"

        async def _load() -> dict[str, Any]:
            payload = await self._rate_limited_request(
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

    # ------------------------------------------------------------------
    # Option-symbol-aware quote lookup (accepts OCC symbols)
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_option_symbol(symbol: Any) -> str | None:
        """Validate an OCC option symbol (e.g. SPY260320P00500000)."""
        value = str(symbol or "").strip().upper()
        if not value:
            return None
        if _OCC_SYMBOL_PATTERN.fullmatch(value):
            return value
        return None

    async def get_option_quotes(self, option_symbols: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch quotes for OCC option symbols via /markets/quotes.

        Unlike get_quotes(), accepts long-form OCC symbols (e.g.
        SPY260320P00500000).  Uses the same Tradier /markets/quotes
        endpoint which supports both equity and option symbols.

        Returns ``{occ_symbol: {bid, ask, last, ...}, ...}``.
        """
        validated: list[str] = []
        for sym in option_symbols or []:
            norm = self._normalize_option_symbol(sym)
            if norm:
                validated.append(norm)
            else:
                logger.warning(
                    "event=tradier_option_quote_validation reason=invalid_occ_symbol symbol=%s",
                    sym,
                )
        if not validated:
            return {}

        key = "tradier:option_quotes:" + ",".join(sorted(set(validated)))
        url = f"{self.settings.TRADIER_BASE_URL}/markets/quotes"

        async def _load() -> dict[str, dict[str, Any]]:
            payload = await self._rate_limited_request(
                "GET",
                url,
                params={"symbols": ",".join(sorted(set(validated)))},
                headers=self._headers,
            )
            quote_obj = (payload.get("quotes") or {}).get("quote")
            if isinstance(quote_obj, dict):
                quote_obj = [quote_obj]

            out: dict[str, dict[str, Any]] = {}
            for item in quote_obj or []:
                if not isinstance(item, dict):
                    continue
                raw_sym = str(item.get("symbol") or "").strip().upper()
                if raw_sym:
                    out[raw_sym] = self._sanitize_quote(item, symbol=raw_sym)
            return out

        return await self.cache.get_or_set(key, self.settings.QUOTE_CACHE_TTL_SECONDS, _load)
