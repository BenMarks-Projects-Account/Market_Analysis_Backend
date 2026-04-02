"""Polygon.io client – price-history (OHLC), snapshots, and technical indicators.

Primary source for daily close data used by SMA / RSI / realized-vol / regime
calculations, and now also the primary source for bulk quote snapshots
(15-min delayed, Stocks Starter plan) and pre-computed technical indicators.

Uses the same httpx.AsyncClient + TTLCache pattern as the other clients.

Rate-limit resilience
---------------------
* An ``asyncio.Semaphore`` caps concurrent in-flight requests (default 15,
  safe on the unlimited-calls Stocks Starter plan).
* HTTP 429 responses trigger exponential back-off with jitter (up to 4
  retries, max 8 s delay).  ``Retry-After`` headers are respected.
* A session-level in-memory de-dupe dict prevents duplicate requests for
  the same ticker+range during a single scan.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.utils.cache import TTLCache
from app.utils.http import UpstreamError

logger = logging.getLogger(__name__)

# -- Rate-limit defaults (not user-configurable, keep simple) --
# Paid Stocks Starter plan: unlimited calls, so higher concurrency is safe.
_MAX_CONCURRENT = 15
_MAX_RETRIES = 4
_BASE_DELAY = 0.5        # seconds
_MAX_DELAY = 8.0          # seconds


class PolygonClient:
    """Thin async wrapper around the Polygon.io REST v2 API."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient, cache: TTLCache) -> None:
        self.settings = settings
        self.http_client = http_client
        self.cache = cache
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
        # Session-level dedup: cleared on each health() call (i.e. at startup)
        self._inflight: dict[str, asyncio.Future[Any]] = {}

    # ------------------------------------------------------------------
    # Internal: rate-limited request with 429 retry
    # ------------------------------------------------------------------

    async def _request_with_retry(self, method: str, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send an HTTP request with semaphore gating and 429 back-off."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            async with self._semaphore:
                try:
                    response = await self.http_client.request(method, url, params=params)
                except httpx.HTTPError as exc:
                    raise UpstreamError(
                        f"Network error calling upstream: {url}",
                        details={"url": url, "exception": str(exc)},
                    ) from exc

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = min(float(retry_after), _MAX_DELAY)
                        except (TypeError, ValueError):
                            delay = _BASE_DELAY * (2 ** attempt)
                    else:
                        delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                    jitter = random.uniform(0, delay * 0.25)
                    total_delay = delay + jitter
                    logger.warning(
                        "event=polygon_429_retry attempt=%d delay=%.2fs url=%s",
                        attempt + 1, total_delay, url,
                    )
                    last_exc = UpstreamError(
                        f"Upstream returned HTTP 429",
                        details={"url": str(response.url), "status_code": 429, "attempt": attempt + 1},
                    )
                    await asyncio.sleep(total_delay)
                    continue

                if response.status_code >= 400:
                    raise UpstreamError(
                        f"Upstream returned HTTP {response.status_code}",
                        details={
                            "url": str(response.url),
                            "status_code": response.status_code,
                            "body": response.text,
                        },
                    )

                try:
                    return response.json()
                except ValueError as exc:
                    raise UpstreamError(
                        "Upstream returned invalid JSON",
                        details={"url": str(response.url), "body": response.text[:1000]},
                    ) from exc

        # Exhausted retries on 429
        raise last_exc or UpstreamError("Polygon rate-limit retries exhausted", details={"url": url})

    async def _dedup_request(self, dedup_key: str, method: str, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """De-duplicate concurrent identical requests within a single scan.

        If a request for *dedup_key* is already in-flight, await its result
        instead of firing a second HTTP call.  Completed futures are removed
        so subsequent scans re-fetch.
        """
        existing = self._inflight.get(dedup_key)
        if existing is not None and not existing.done():
            logger.debug("event=polygon_dedup_hit key=%s", dedup_key)
            return await existing

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._inflight[dedup_key] = fut
        try:
            result = await self._request_with_retry(method, url, params=params)
            fut.set_result(result)
            return result
        except BaseException as exc:
            fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(dedup_key, None)

    # ------------------------------------------------------------------
    # OHLC aggregates
    # ------------------------------------------------------------------

    async def get_aggregates_ohlc(
        self,
        ticker: str,
        start_date: str | date,
        end_date: str | date,
        timespan: str = "day",
        multiplier: int = 1,
        adjusted: bool = True,
    ) -> list[dict[str, Any]]:
        """Return daily OHLC bars from the Polygon aggregates endpoint.

        Returns a list of dicts compatible with the existing ``prices_history``
        consumer::

            [{"date": "2026-02-10", "open": 511.2, "high": 515.0,
              "low": 509.8, "close": 513.4, "volume": 12345678}, ...]

        Empty results (weekends, holidays, bad ticker) return ``[]``.
        """
        ticker = str(ticker).upper().strip()
        if not ticker:
            return []

        from_str = start_date if isinstance(start_date, str) else start_date.isoformat()
        to_str = end_date if isinstance(end_date, str) else end_date.isoformat()

        cache_key = f"polygon:aggs:{ticker}:{from_str}:{to_str}:{timespan}:{multiplier}"

        async def _load() -> list[dict[str, Any]]:
            url = (
                f"{self.settings.POLYGON_BASE_URL}/v2/aggs/ticker/{ticker}"
                f"/range/{multiplier}/{timespan}/{from_str}/{to_str}"
            )
            params: dict[str, Any] = {
                "adjusted": str(adjusted).lower(),
                "sort": "asc",
                "limit": 5000,
                "apiKey": self.settings.POLYGON_API_KEY,
            }

            payload = await self._dedup_request(cache_key, "GET", url, params=params)
            return self._parse_aggs(payload)

        return await self.cache.get_or_set(cache_key, self.settings.CANDLES_CACHE_TTL_SECONDS, _load)

    # ------------------------------------------------------------------
    # Snapshot / last price
    # ------------------------------------------------------------------

    async def get_last_price(self, ticker: str) -> dict[str, Any]:
        """Return a structure compatible with the existing quote expectations.

        Returns ``{"price": float, "source": "polygon"}`` or ``{}`` on failure.

        Uses Polygon's *previous close* endpoint which is available on all
        plans (the snapshot endpoint requires a premium plan).
        """
        ticker = str(ticker).upper().strip()
        if not ticker:
            return {}

        cache_key = f"polygon:prev_close:{ticker}"

        async def _load() -> dict[str, Any]:
            url = f"{self.settings.POLYGON_BASE_URL}/v2/aggs/ticker/{ticker}/prev"
            params: dict[str, Any] = {
                "adjusted": "true",
                "apiKey": self.settings.POLYGON_API_KEY,
            }

            payload = await self._request_with_retry("GET", url, params=params)
            results = payload.get("results") or []
            if not results:
                return {}
            bar = results[0]
            close = bar.get("c")
            if close is None:
                return {}
            try:
                return {"price": float(close), "source": "polygon"}
            except (TypeError, ValueError):
                return {}

        return await self.cache.get_or_set(cache_key, self.settings.QUOTE_CACHE_TTL_SECONDS, _load)

    # ------------------------------------------------------------------
    # Health / canary
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """Canary check – fetch 5 recent SPY bars.  Returns True if ≥1 bar."""
        if not self.settings.POLYGON_API_KEY:
            return False
        try:
            today = date.today()
            start = date(today.year, today.month, max(today.day - 7, 1))
            bars = await self.get_aggregates_ohlc("SPY", start_date=start, end_date=today)
            return len(bars) > 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_aggs(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalise Polygon aggregates response into flat OHLCV dicts."""
        results = payload.get("results") or []
        if not results:
            logger.warning(
                "event=polygon_empty_results ticker=%s status=%s",
                payload.get("ticker", "?"),
                payload.get("status", "?"),
            )
            return []

        bars: list[dict[str, Any]] = []
        for r in results:
            ts_ms = r.get("t")
            if ts_ms is None:
                continue
            try:
                dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
                iso_date = dt.strftime("%Y-%m-%d")
            except (OSError, ValueError, OverflowError):
                continue

            try:
                bar: dict[str, Any] = {
                    "date": iso_date,
                    "open": float(r["o"]),
                    "high": float(r["h"]),
                    "low": float(r["l"]),
                    "close": float(r["c"]),
                    "volume": int(r.get("v", 0)),
                }
            except (KeyError, TypeError, ValueError):
                continue
            bars.append(bar)

        return bars

    async def get_daily_closes(self, ticker: str, lookback_days: int = 365) -> list[float]:
        """Convenience wrapper – returns a flat list of close prices (newest last).

        Drop-in replacement for ``YahooClient.get_daily_closes``.
        """
        today = datetime.now(timezone.utc).date()
        start = date.fromordinal(max(today.toordinal() - lookback_days, 1))

        bars = await self.get_aggregates_ohlc(ticker, start_date=start, end_date=today)
        return [b["close"] for b in bars]

    async def get_daily_closes_dated(self, ticker: str, lookback_days: int = 365) -> list[dict[str, Any]]:
        """Return daily bars as ``[{"date": "YYYY-MM-DD", "close": float}, ...]``."""
        today = datetime.now(timezone.utc).date()
        start = date.fromordinal(max(today.toordinal() - lookback_days, 1))

        bars = await self.get_aggregates_ohlc(ticker, start_date=start, end_date=today)
        return [{"date": b["date"], "close": b["close"]} for b in bars]

    async def get_intraday_bars(
        self,
        ticker: str,
        lookback_days: int = 14,
        multiplier: int = 15,
        timespan: str = "minute",
    ) -> list[dict[str, Any]]:
        """Return intraday bars with ISO-datetime timestamps.

        Returns ``[{"date": "2026-03-18T10:30:00+00:00", "close": float}, ...]``.
        Uses Polygon aggregates with sub-daily granularity (default 15-min bars).
        """
        ticker = str(ticker).upper().strip()
        if not ticker:
            return []

        today = datetime.now(timezone.utc).date()
        start = date.fromordinal(max(today.toordinal() - lookback_days, 1))
        from_str = start.isoformat()
        to_str = today.isoformat()

        cache_key = f"polygon:intraday:{ticker}:{from_str}:{to_str}:{timespan}:{multiplier}"

        async def _load() -> list[dict[str, Any]]:
            url = (
                f"{self.settings.POLYGON_BASE_URL}/v2/aggs/ticker/{ticker}"
                f"/range/{multiplier}/{timespan}/{from_str}/{to_str}"
            )
            params: dict[str, Any] = {
                "adjusted": "true",
                "sort": "asc",
                "limit": 5000,
                "apiKey": self.settings.POLYGON_API_KEY,
            }
            payload = await self._dedup_request(cache_key, "GET", url, params=params)
            return self._parse_intraday(payload)

        return await self.cache.get_or_set(cache_key, self.settings.CANDLES_CACHE_TTL_SECONDS, _load)

    @staticmethod
    def _parse_intraday(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse Polygon aggregates into bars with full ISO datetime strings."""
        results = payload.get("results") or []
        if not results:
            return []
        bars: list[dict[str, Any]] = []
        for r in results:
            ts_ms = r.get("t")
            if ts_ms is None:
                continue
            try:
                dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
                iso_dt = dt.isoformat()
            except (OSError, ValueError, OverflowError):
                continue
            try:
                bars.append({"date": iso_dt, "close": float(r["c"])})
            except (KeyError, TypeError, ValueError):
                continue
        return bars

    # ------------------------------------------------------------------
    # Snapshot — 15-min delayed quote (Stocks Starter plan)
    # ------------------------------------------------------------------

    async def get_snapshot(self, ticker: str) -> dict[str, Any]:
        """15-min delayed quote snapshot for a single ticker.

        Returns a dict with keys: price, open, high, low, close, volume,
        prev_close, change, change_pct, updated, source.
        Returns ``{}`` on failure.
        """
        ticker = str(ticker).upper().strip()
        if not ticker:
            return {}

        cache_key = f"polygon:snapshot:{ticker}"

        async def _load() -> dict[str, Any]:
            url = (
                f"{self.settings.POLYGON_BASE_URL}"
                f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
            )
            params: dict[str, Any] = {"apiKey": self.settings.POLYGON_API_KEY}
            payload = await self._request_with_retry("GET", url, params=params)
            return self._parse_snapshot(payload)

        return await self.cache.get_or_set(cache_key, self.settings.QUOTE_CACHE_TTL_SECONDS, _load)

    async def get_snapshots(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        """Batch snapshot for multiple tickers.

        Returns ``{TICKER: {price, open, high, low, ...}, ...}``.
        Tickers with no data are omitted from the result.
        """
        normalized = sorted({str(t).upper().strip() for t in tickers if str(t).strip()})
        if not normalized:
            return {}

        cache_key = f"polygon:snapshots:{','.join(normalized)}"

        async def _load() -> dict[str, dict[str, Any]]:
            url = (
                f"{self.settings.POLYGON_BASE_URL}"
                f"/v2/snapshot/locale/us/markets/stocks/tickers"
            )
            params: dict[str, Any] = {
                "tickers": ",".join(normalized),
                "apiKey": self.settings.POLYGON_API_KEY,
            }
            payload = await self._request_with_retry("GET", url, params=params)
            result: dict[str, dict[str, Any]] = {}
            for item in payload.get("tickers") or []:
                parsed = self._parse_snapshot_item(item)
                sym = parsed.get("symbol")
                if sym and parsed.get("price") is not None:
                    result[sym] = parsed
            return result

        return await self.cache.get_or_set(cache_key, self.settings.QUOTE_CACHE_TTL_SECONDS, _load)

    @staticmethod
    def _parse_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
        """Parse a single-ticker snapshot response."""
        ticker_data = payload.get("ticker")
        if not ticker_data:
            return {}
        return PolygonClient._parse_snapshot_item(ticker_data)

    @staticmethod
    def _parse_snapshot_item(item: dict[str, Any]) -> dict[str, Any]:
        """Parse one ticker entry from a snapshot response.

        Input fields: ticker, day{o,h,l,c,v}, prevDay{c}, todaysChange, todaysChangePerc, updated
        Output: normalized dict with price, open, high, low, close, volume,
                prev_close, change, change_pct, symbol, source.
        """
        day = item.get("day") or {}
        prev = item.get("prevDay") or {}
        try:
            price = float(day.get("c") or 0) or None
        except (TypeError, ValueError):
            price = None

        return {
            "symbol": str(item.get("ticker", "")).upper(),
            "price": price,
            "last": price,
            "open": _safe_num(day.get("o")),
            "high": _safe_num(day.get("h")),
            "low": _safe_num(day.get("l")),
            "close": price,
            "volume": _safe_num(day.get("v")),
            "prev_close": _safe_num(prev.get("c")),
            "change": _safe_num(item.get("todaysChange")),
            "change_percentage": _safe_num(item.get("todaysChangePerc")),
            "updated": item.get("updated"),
            "source": "polygon",
        }

    # ------------------------------------------------------------------
    # Technical indicators (Stocks Starter plan)
    # ------------------------------------------------------------------

    async def get_rsi(
        self, symbol: str, *, timespan: str = "day", window: int = 14, limit: int = 1,
    ) -> dict[str, Any]:
        """Fetch RSI from Polygon ``/v1/indicators/rsi/{symbol}``.

        Returns ``{"value": float, "timestamp": str}`` or ``{}``.
        """
        return await self._fetch_indicator("rsi", symbol, timespan=timespan, window=window, limit=limit)

    async def get_sma(
        self, symbol: str, *, timespan: str = "day", window: int = 50, limit: int = 1,
    ) -> dict[str, Any]:
        """Fetch SMA from Polygon ``/v1/indicators/sma/{symbol}``."""
        return await self._fetch_indicator("sma", symbol, timespan=timespan, window=window, limit=limit)

    async def get_ema(
        self, symbol: str, *, timespan: str = "day", window: int = 20, limit: int = 1,
    ) -> dict[str, Any]:
        """Fetch EMA from Polygon ``/v1/indicators/ema/{symbol}``."""
        return await self._fetch_indicator("ema", symbol, timespan=timespan, window=window, limit=limit)

    async def get_macd(
        self, symbol: str, *, timespan: str = "day", limit: int = 1,
    ) -> dict[str, Any]:
        """Fetch MACD from Polygon ``/v1/indicators/macd/{symbol}``.

        Returns ``{"value": float, "signal": float, "histogram": float, "timestamp": str}``
        or ``{}``.
        """
        return await self._fetch_indicator("macd", symbol, timespan=timespan, limit=limit)

    async def _fetch_indicator(
        self, indicator: str, symbol: str, *, timespan: str = "day",
        window: int | None = None, limit: int = 1,
    ) -> dict[str, Any]:
        """Shared implementation for technical indicator endpoints."""
        symbol = str(symbol).upper().strip()
        if not symbol:
            return {}

        params: dict[str, Any] = {
            "timespan": timespan,
            "limit": limit,
            "apiKey": self.settings.POLYGON_API_KEY,
        }
        if window is not None:
            params["window"] = window

        cache_key = f"polygon:{indicator}:{symbol}:{timespan}:{window}:{limit}"

        async def _load() -> dict[str, Any]:
            url = f"{self.settings.POLYGON_BASE_URL}/v1/indicators/{indicator}/{symbol}"
            payload = await self._request_with_retry("GET", url, params=params)
            values = (payload.get("results") or {}).get("values") or []
            if not values:
                return {}
            latest = values[0]
            if indicator == "macd":
                return {
                    "value": _safe_num(latest.get("value")),
                    "signal": _safe_num(latest.get("signal")),
                    "histogram": _safe_num(latest.get("histogram")),
                    "timestamp": str(latest.get("timestamp", "")),
                }
            return {
                "value": _safe_num(latest.get("value")),
                "timestamp": str(latest.get("timestamp", "")),
            }

        return await self.cache.get_or_set(cache_key, self.settings.CANDLES_CACHE_TTL_SECONDS, _load)

    # ------------------------------------------------------------------
    # Full OHLCV bars (alias matching Tradier get_daily_bars interface)
    # ------------------------------------------------------------------

    async def get_daily_bars(
        self, ticker: str, start_date: str, end_date: str,
    ) -> list[dict[str, Any]]:
        """Return full OHLCV daily bars, compatible with Tradier ``get_daily_bars`` shape.

        Returns ``[{"date", "open", "high", "low", "close", "volume"}, ...]``.
        """
        return await self.get_aggregates_ohlc(ticker, start_date=start_date, end_date=end_date)


def _safe_num(value: Any) -> float | None:
    """Coerce to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
