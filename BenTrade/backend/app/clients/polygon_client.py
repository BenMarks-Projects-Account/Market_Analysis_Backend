"""Polygon.io client – price-history (OHLC) and last-price snapshot.

Replaces Yahoo Finance as the primary source for daily close data used by
SMA / RSI / realized-vol / regime calculations.

Uses the same httpx.AsyncClient + TTLCache pattern as the other clients.

Rate-limit resilience
---------------------
* An ``asyncio.Semaphore`` caps concurrent in-flight requests (default 5).
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
_MAX_CONCURRENT = 5
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
        today = date.today()
        start = date.fromordinal(max(today.toordinal() - lookback_days, 1))

        bars = await self.get_aggregates_ohlc(ticker, start_date=start, end_date=today)
        return [b["close"] for b in bars]

    async def get_daily_closes_dated(self, ticker: str, lookback_days: int = 365) -> list[dict[str, Any]]:
        """Return daily bars as ``[{"date": "YYYY-MM-DD", "close": float}, ...]``."""
        today = date.today()
        start = date.fromordinal(max(today.toordinal() - lookback_days, 1))

        bars = await self.get_aggregates_ohlc(ticker, start_date=start, end_date=today)
        return [{"date": b["date"], "close": b["close"]} for b in bars]
