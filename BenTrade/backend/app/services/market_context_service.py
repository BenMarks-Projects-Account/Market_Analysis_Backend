"""Centralized market context — single source of truth for macro metrics.

Provides intraday-aware values for VIX, yields, oil, etc.  Tries the
best-available source for each metric and returns freshness metadata so
the UI can show exactly how stale a value is.

Data source hierarchy (priority order):
  VIX       → Tradier quote (intraday) → Finnhub quote (intraday) → FRED VIXCLS (EOD fallback)
  SPY/QQQ/IWM/DIA → Tradier quote (intraday, via get_flat_macro enrichment)
  10Y Yield → FRED DGS10 (EOD)
  2Y Yield  → FRED DGS2 (EOD)
  Fed Funds → FRED DFF (EOD)
  Oil WTI   → FRED DCOILWTICO (EOD)
  USD Index → FRED DTWEXBGS (EOD / weekly)

Normalized metric envelope — every metric is wrapped:
  {
    "value": float | None,         # current_value for display
    "previous_close": float | None,# prior session close (when available)
    "source": str,                 # "tradier" | "finnhub" | "fred"
    "freshness": str,              # "intraday" | "delayed" | "eod"
    "is_intraday": bool,
    "observation_date": str | None,# YYYY-MM-DD for EOD series
    "fetched_at": str,             # ISO timestamp of fetch
    "source_timestamp": str | None # exchange/provider timestamp when available
  }
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.clients.finnhub_client import FinnhubClient
from app.clients.fred_client import FredClient
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)

# Cache key / TTL for the full market context object
_CONTEXT_CACHE_KEY = "market_context:latest"
_CONTEXT_CACHE_TTL = 30  # seconds — short so intraday values stay fresh

# Tradier client is injected optionally for intraday quote capability
_TradierClient = None  # resolved at import time below
try:
    from app.clients.tradier_client import TradierClient as _TradierClient  # type: ignore[assignment]
except ImportError:
    pass


def _metric(
    value: float | None,
    source: str,
    observation_date: str | None = None,
    is_intraday: bool = False,
    previous_close: float | None = None,
    source_timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a single normalized metric envelope."""
    if is_intraday:
        freshness = "intraday"
    elif observation_date:
        freshness = "eod"
    else:
        freshness = "delayed"
    return {
        "value": value,
        "previous_close": previous_close,
        "source": source,
        "freshness": freshness,
        "is_intraday": is_intraday,
        "observation_date": observation_date,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_timestamp": source_timestamp,
    }


class MarketContextService:
    def __init__(
        self,
        fred_client: FredClient,
        finnhub_client: FinnhubClient | None,
        cache: TTLCache,
        tradier_client: Any | None = None,
    ) -> None:
        self.fred = fred_client
        self.finnhub = finnhub_client
        self.tradier = tradier_client
        self.cache = cache

    # ── VIX: Tradier intraday → Finnhub intraday → FRED EOD ─────

    async def _vix_from_tradier(self) -> dict[str, Any] | None:
        """Try Tradier for a live VIX quote. Returns metric dict or None."""
        if not self.tradier:
            return None
        try:
            quote = await self.tradier.get_quote("VIX")
            last = quote.get("last")
            if last is not None and float(last) > 0:
                prev_close = quote.get("prevclose") or quote.get("previous_close")
                prev_close = round(float(prev_close), 2) if prev_close is not None else None
                val = round(float(last), 2)
                logger.info(
                    "[MARKET_CONTEXT] metric_normalized symbol=VIX source=tradier"
                    " current_value=%.2f previous_close=%s freshness=intraday",
                    val, prev_close,
                )
                return _metric(
                    value=val,
                    source="tradier",
                    is_intraday=True,
                    previous_close=prev_close,
                )
        except Exception as exc:
            logger.debug("[MARKET_CONTEXT] tradier_vix_unavailable error=%s", exc)
        return None

    async def _vix_from_finnhub(self) -> dict[str, Any] | None:
        """Try Finnhub for a live VIX quote. Returns metric dict or None."""
        if not self.finnhub:
            return None
        try:
            quote = await self.finnhub.get_quote("VIX")
            current = quote.get("c")  # Finnhub 'c' = current price
            prev_close = quote.get("pc")  # Finnhub 'pc' = previous close
            ts = quote.get("t")  # Finnhub 't' = unix timestamp
            if current is not None and float(current) > 0:
                val = round(float(current), 2)
                pc = round(float(prev_close), 2) if prev_close else None
                src_ts = None
                if ts and int(ts) > 0:
                    src_ts = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
                logger.info(
                    "[MARKET_CONTEXT] metric_normalized symbol=VIX source=finnhub"
                    " current_value=%.2f previous_close=%s freshness=intraday",
                    val, pc,
                )
                return _metric(
                    value=val,
                    source="finnhub",
                    is_intraday=True,
                    previous_close=pc,
                    source_timestamp=src_ts,
                )
        except Exception as exc:
            logger.debug("[MARKET_CONTEXT] finnhub_vix_unavailable error=%s", exc)
        return None

    async def _vix_from_fred(self) -> dict[str, Any]:
        """FRED VIXCLS — always available but EOD only."""
        obs = await self.fred.get_series_with_date("VIXCLS")
        if obs:
            val = obs["value"]
            logger.info(
                "[MARKET_CONTEXT] metric_normalized symbol=VIX source=fred"
                " current_value=%s previous_close=None freshness=eod obs_date=%s",
                val, obs["observation_date"],
            )
            return _metric(
                value=val,
                source="fred",
                observation_date=obs["observation_date"],
                is_intraday=False,
            )
        return _metric(None, "fred", is_intraday=False)

    # ── Generic FRED helper ──────────────────────────────────────

    async def _fred_metric(self, series_id: str) -> dict[str, Any]:
        obs = await self.fred.get_series_with_date(series_id)
        if obs:
            return _metric(
                value=obs["value"],
                source="fred",
                observation_date=obs["observation_date"],
                is_intraday=False,
            )
        return _metric(None, "fred", is_intraday=False)

    # ── Public API ───────────────────────────────────────────────

    async def get_market_context(self) -> dict[str, Any]:
        """Return full market context — cached for _CONTEXT_CACHE_TTL seconds."""

        async def _build() -> dict[str, Any]:
            logger.info("[MARKET_CONTEXT] metric_fetch_start")

            # VIX: try Tradier first, then Finnhub, then FRED
            vix_metric = await self._vix_from_tradier()
            if vix_metric is None:
                vix_metric = await self._vix_from_finnhub()
            if vix_metric is None:
                vix_metric = await self._vix_from_fred()

            # Remaining metrics: all FRED (fire in parallel)
            ten_year, two_year, fed_funds, oil, usd = await asyncio.gather(
                self._fred_metric("DGS10"),
                self._fred_metric("DGS2"),
                self._fred_metric("DFF"),
                self._fred_metric("DCOILWTICO"),
                self._fred_metric("DTWEXBGS"),
            )

            # Derived: yield curve spread
            yield_spread = None
            if ten_year["value"] is not None and two_year["value"] is not None:
                yield_spread = round(ten_year["value"] - two_year["value"], 3)

            # CPI is monthly and slow-changing — fetch via FRED raw
            cpi_yoy = await self._compute_cpi_yoy()

            result = {
                "vix": vix_metric,
                "ten_year_yield": ten_year,
                "two_year_yield": two_year,
                "fed_funds_rate": fed_funds,
                "oil_wti": oil,
                "usd_index": usd,
                "yield_curve_spread": yield_spread,
                "cpi_yoy": cpi_yoy,
                "context_generated_at": datetime.now(timezone.utc).isoformat(),
            }

            logger.info(
                "[MARKET_CONTEXT] metric_fetch_success vix_source=%s vix_value=%s"
                " vix_freshness=%s ten_year=%s two_year=%s oil=%s",
                vix_metric.get("source"), vix_metric.get("value"),
                vix_metric.get("freshness"), ten_year.get("value"),
                two_year.get("value"), oil.get("value"),
            )
            return result

        return await self.cache.get_or_set(_CONTEXT_CACHE_KEY, _CONTEXT_CACHE_TTL, _build)

    async def _compute_cpi_yoy(self) -> dict[str, Any]:
        """CPI Year-over-Year from FRED CPIAUCSL (monthly, 13 observations)."""
        from app.utils.http import request_json

        try:
            payload = await request_json(
                self.fred.http_client,
                "GET",
                f"{self.fred.settings.FRED_BASE_URL}/series/observations",
                params={
                    "series_id": "CPIAUCSL",
                    "sort_order": "desc",
                    "limit": 13,
                    "api_key": self.fred.settings.FRED_KEY,
                    "file_type": "json",
                },
            )
            observations = payload.get("observations") or []
            values: list[float] = []
            obs_date = ""
            for i, row in enumerate(observations):
                raw = row.get("value")
                if raw in (None, "."):
                    continue
                try:
                    values.append(float(raw))
                except (TypeError, ValueError):
                    continue
                if i == 0:
                    obs_date = row.get("date", "")
            if len(values) >= 13 and values[12] != 0:
                yoy = (values[0] / values[12]) - 1.0
                return _metric(round(yoy, 4), "fred", observation_date=obs_date, is_intraday=False)
        except Exception as exc:
            logger.debug("event=cpi_yoy_error error=%s", exc)
        return _metric(None, "fred", is_intraday=False)

    # ── Convenience: flat dict for backward-compatible consumers ──

    async def get_flat_macro(self) -> dict[str, Any]:
        """Return a flat dict matching the old /api/stock/macro shape,
        plus freshness metadata under a `_freshness` key.

        Flat keys: vix, ten_year_yield, fed_funds_rate, cpi_yoy, notes
        """
        ctx = await self.get_market_context()
        notes: list[str] = []

        def _val(key: str) -> float | None:
            m = ctx.get(key)
            if not m or m.get("value") is None:
                notes.append(f"{key} unavailable")
                return None
            return m["value"]

        freshness = {}
        for key in ("vix", "ten_year_yield", "two_year_yield", "fed_funds_rate", "oil_wti", "usd_index", "cpi_yoy"):
            m = ctx.get(key)
            if m:
                freshness[key] = {
                    "source": m.get("source"),
                    "observation_date": m.get("observation_date"),
                    "is_intraday": m.get("is_intraday", False),
                    "freshness": m.get("freshness", "delayed"),
                    "fetched_at": m.get("fetched_at"),
                    "previous_close": m.get("previous_close"),
                    "source_timestamp": m.get("source_timestamp"),
                }

        return {
            "vix": _val("vix"),
            "ten_year_yield": _val("ten_year_yield"),
            "two_year_yield": _val("two_year_yield"),
            "fed_funds_rate": _val("fed_funds_rate"),
            "oil_wti": _val("oil_wti"),
            "usd_index": _val("usd_index"),
            "yield_curve_spread": ctx.get("yield_curve_spread"),
            "cpi_yoy": _val("cpi_yoy"),
            "notes": notes,
            "_freshness": freshness,
            "_generated_at": ctx.get("context_generated_at"),
        }
