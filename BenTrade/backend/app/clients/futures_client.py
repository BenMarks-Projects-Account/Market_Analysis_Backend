"""Futures and index data client for pre-market intelligence.

Fetches current prices, previous closes (for gap calculation), and historical
bars (for charting) for:
- Index futures: ES (SPY), NQ (QQQ), RTY (IWM), YM (DIA)
- VIX: spot index + ETF proxy (VXX) for term-structure estimation
- Macro futures: CL (crude oil), DX (dollar index), ZN (10Y treasury)

Data source policy (updated 2026-03-25):
  - yfinance library suffers from aggressive internal rate-limiting
    (YFRateLimitError) even though Yahoo's v8 chart API works fine.
  - All fetches now use direct httpx calls to the Yahoo Finance v8
    chart API, eliminating the yfinance dependency and rate-limit
    failures entirely.
  - Polygon (I:NDX) noted but not used — Stocks Basic plan lacks
    futures/index access.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.utils.cache import TTLCache

_log = logging.getLogger(__name__)

# ── Yahoo Finance v8 chart API ───────────────────────────────────────
_YAHOO_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}
_YAHOO_TIMEOUT = 15.0  # per-request timeout (seconds)

# ── Instrument registry ──────────────────────────────────────────────
# Each entry maps a canonical key to source-specific tickers.
# "yahoo"        – Yahoo Finance ticker (primary, via direct API)
# "polygon"      – Polygon I: ticker (secondary, most are 403 on our plan)
# "label"        – human-readable display name
# "underlying"   – ETF that tracks this futures contract (or None)
# "asset_class"  – grouping for UI display

_INSTRUMENTS: dict[str, dict[str, Any]] = {
    "es":  {"yahoo": "ES=F",     "polygon": None,    "label": "S&P 500 Futures",       "underlying": "SPY", "asset_class": "equity_index"},
    "nq":  {"yahoo": "NQ=F",     "polygon": "I:NDX", "label": "Nasdaq 100 Futures",    "underlying": "QQQ", "asset_class": "equity_index"},
    "rty": {"yahoo": "RTY=F",    "polygon": None,    "label": "Russell 2000 Futures",   "underlying": "IWM", "asset_class": "equity_index"},
    "ym":  {"yahoo": "YM=F",     "polygon": None,    "label": "Dow Futures",            "underlying": "DIA", "asset_class": "equity_index"},
    "vix": {"yahoo": "^VIX",     "polygon": None,    "label": "VIX Index",              "underlying": None,  "asset_class": "volatility"},
    "cl":  {"yahoo": "CL=F",     "polygon": None,    "label": "Crude Oil",              "underlying": None,  "asset_class": "commodity"},
    "dx":  {"yahoo": "DX-Y.NYB", "polygon": None,    "label": "Dollar Index",           "underlying": None,  "asset_class": "currency"},
    "zn":  {"yahoo": "ZN=F",     "polygon": None,    "label": "10Y Treasury Futures",   "underlying": None,  "asset_class": "fixed_income"},
    "tnx": {"yahoo": "^TNX",     "polygon": None,    "label": "10Y Treasury Yield",     "underlying": None,  "asset_class": "fixed_income"},
}

# VXX is used as a proxy for VIX futures term-structure estimation
_VIX_PROXY_TICKER = "VXX"

# Yahoo chart API interval mapping (from user-facing names → API values)
_INTERVAL_MAP = {
    "1min":  "1m",
    "5min":  "5m",
    "15min": "15m",
    "30min": "30m",
    "1hour": "1h",
    "1day":  "1d",
    # Short-form aliases (API route sends these directly)
    "1m":    "1m",
    "5m":    "5m",
    "15m":   "15m",
    "1h":    "1h",
    "1d":    "1d",
}

# Cache TTLs
_SNAPSHOT_CACHE_TTL = 30       # seconds – during market hours
_BARS_CACHE_TTL    = 300       # 5 min
_TERM_STRUCT_TTL   = 60        # 1 min


class FuturesClient:
    """Async client for futures/index data used by pre-market intelligence.

    Follows the same DI pattern as other BenTrade clients:
    ``(settings, http_client, cache)`` constructor with cache-through reads.

    Uses direct Yahoo Finance v8 chart API via httpx (no yfinance library).
    """

    # Expose the instrument registry for consumers
    INSTRUMENTS = _INSTRUMENTS

    def __init__(
        self,
        settings: Settings,
        cache: TTLCache,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self._http = http_client
        # Create a private httpx client if none provided (tests, standalone)
        self._owns_http = http_client is None
        if self._owns_http:
            self._http = httpx.AsyncClient(timeout=_YAHOO_TIMEOUT)

    # ------------------------------------------------------------------
    # Yahoo Finance v8 chart API — core fetch
    # ------------------------------------------------------------------

    async def _yahoo_chart(
        self, ticker: str, range_: str = "5d", interval: str = "1d",
    ) -> dict[str, Any] | None:
        """Fetch from Yahoo Finance ``/v8/finance/chart/{ticker}``.

        Returns the first element of ``chart.result`` or ``None``.
        """
        url = f"{_YAHOO_CHART_BASE}/{ticker}"
        params = {"range": range_, "interval": interval}
        try:
            resp = await self._http.get(
                url, params=params, headers=_YAHOO_HEADERS,
                timeout=_YAHOO_TIMEOUT,
            )
            if resp.status_code != 200:
                _log.warning(
                    "event=yahoo_chart_http_error ticker=%s status=%d body=%s",
                    ticker, resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            results = data.get("chart", {}).get("result")
            if not results:
                _log.warning("event=yahoo_chart_no_result ticker=%s", ticker)
                return None
            return results[0]
        except httpx.TimeoutException:
            _log.warning("event=yahoo_chart_timeout ticker=%s", ticker)
            return None
        except Exception as exc:
            _log.warning(
                "event=yahoo_chart_failed ticker=%s error=%s", ticker, exc,
            )
            return None

    # ------------------------------------------------------------------
    # Snapshot – single instrument
    # ------------------------------------------------------------------

    async def get_snapshot(self, instrument: str) -> dict[str, Any] | None:
        """Current price snapshot for one instrument.

        Returns a normalized dict::

            {
                "instrument": "es",
                "label": "S&P 500 Futures",
                "last": 5667.50,
                "prev_close": 5660.75,
                "change": 6.75,
                "change_pct": 0.0012,
                "open": 5658.00,
                "high": 5672.00,
                "low": 5645.25,
                "volume": 145000,
                "timestamp": "2026-03-24T10:30:00+00:00",
                "source": "yahoo_direct",
                "asset_class": "equity_index",
                "underlying": "SPY",
            }

        Returns ``None`` if the ticker is unknown or data is unavailable.
        """
        inst = _INSTRUMENTS.get(instrument)
        if inst is None:
            return None

        cache_key = f"futures:snap:{instrument}"

        async def _load() -> dict[str, Any] | None:
            chart = await self._yahoo_chart(inst["yahoo"], range_="5d", interval="1d")
            if chart is None:
                _log.info("event=snapshot_miss instrument=%s ticker=%s", instrument, inst["yahoo"])
                return None
            snap = _parse_snapshot(chart, inst, instrument)
            if snap:
                _log.info(
                    "event=snapshot_ok instrument=%s last=%s prev=%s source=yahoo_direct",
                    instrument, snap["last"], snap.get("prev_close"),
                )
            return snap

        return await self.cache.get_or_set(cache_key, _SNAPSHOT_CACHE_TTL, _load)

    # ------------------------------------------------------------------
    # Snapshot – all instruments in parallel
    # ------------------------------------------------------------------

    async def get_all_snapshots(self) -> dict[str, dict[str, Any] | None]:
        """Fetch snapshots for every instrument concurrently.

        Returns ``{instrument_key: snapshot_dict | None, ...}``.
        """
        async def _safe_get(key: str) -> tuple[str, dict[str, Any] | None]:
            try:
                return key, await self.get_snapshot(key)
            except Exception as exc:
                _log.warning("event=futures_snapshot_failed instrument=%s error=%s", key, exc)
                return key, None

        pairs = await asyncio.gather(*[_safe_get(k) for k in _INSTRUMENTS])
        result = dict(pairs)
        ok = sum(1 for v in result.values() if v is not None)
        _log.info("event=all_snapshots_done ok=%d total=%d", ok, len(result))
        return result

    # ------------------------------------------------------------------
    # Historical bars – for charting
    # ------------------------------------------------------------------

    async def get_bars(
        self,
        instrument: str,
        timeframe: str = "1hour",
        days: int = 5,
    ) -> list[dict[str, Any]]:
        """Historical OHLCV bars for charting.

        Args:
            instrument: One of the canonical keys (es, nq, rty, …).
            timeframe: ``"1min"`` | ``"5min"`` | ``"15min"`` | ``"30min"``
                       | ``"1hour"`` | ``"1day"``.
            days: Look-back window in calendar days.

        Returns a list of normalised bar dicts sorted oldest-first::

            [{"timestamp": "2026-03-24T09:30:00+00:00",
              "open": 5658.0, "high": 5672.0, "low": 5645.25,
              "close": 5667.5, "volume": 14500}, ...]
        """
        inst = _INSTRUMENTS.get(instrument)
        if inst is None:
            return []

        yf_interval = _INTERVAL_MAP.get(timeframe, "1h")
        cache_key = f"futures:bars:{instrument}:{yf_interval}:{days}d"

        async def _load() -> list[dict[str, Any]]:
            chart = await self._yahoo_chart(
                inst["yahoo"], range_=f"{days}d", interval=yf_interval,
            )
            if chart is None:
                _log.info(
                    "event=bars_miss instrument=%s ticker=%s interval=%s",
                    instrument, inst["yahoo"], yf_interval,
                )
                return []
            bars = _parse_bars(chart)
            _log.info(
                "event=bars_ok instrument=%s bars=%d interval=%s",
                instrument, len(bars), yf_interval,
            )
            return bars

        return await self.cache.get_or_set(cache_key, _BARS_CACHE_TTL, _load)

    # ------------------------------------------------------------------
    # VIX term-structure estimate
    # ------------------------------------------------------------------

    async def get_vix_term_structure(self) -> dict[str, Any]:
        """VIX spot + VXX-based term-structure estimate.

        True VIX futures (VX=F) are unavailable on both Polygon and Yahoo.
        We approximate term structure using:
          - ``^VIX``  → VIX spot index
          - ``VXX``   → iPath VIX Short-Term Futures ETN (tracks front/2nd
                         month blend)

        Returns::

            {
                "spot":          26.15,
                "vxx_price":     34.33,
                "structure":     "backwardation" | "contango" | "flat",
                "contango_pct":  -2.4,
                "source":        "yahoo_direct",
                "note":          "Estimated from VIX spot vs VXX proxy"
            }

        ``structure`` classification:
            contango_pct > 2   → contango   (normal, futures > spot)
            contango_pct < -2  → backwardation (fear, futures < spot)
            otherwise          → flat
        """
        cache_key = "futures:vix_term_structure"

        async def _load() -> dict[str, Any]:
            # Fetch VIX spot and VXX prices in parallel
            vix_task = self._fast_price("^VIX")
            vxx_task = self._fast_price(_VIX_PROXY_TICKER)
            vix_price, vxx_price = await asyncio.gather(vix_task, vxx_task)

            result: dict[str, Any] = {
                "spot": vix_price,
                "vxx_price": vxx_price,
                "structure": "unknown",
                "contango_pct": None,
                "source": "yahoo_direct",
                "note": "Estimated from VIX spot vs VXX proxy",
            }

            if vix_price is not None and vxx_price is not None and vix_price > 0:
                pct = ((vxx_price - vix_price) / vix_price) * 100
                result["contango_pct"] = round(pct, 2)
                if pct > 2:
                    result["structure"] = "contango"
                elif pct < -2:
                    result["structure"] = "backwardation"
                else:
                    result["structure"] = "flat"

            _log.info(
                "event=vix_term_structure spot=%s vxx=%s structure=%s",
                vix_price, vxx_price, result["structure"],
            )
            return result

        return await self.cache.get_or_set(cache_key, _TERM_STRUCT_TTL, _load)

    # ------------------------------------------------------------------
    # Health canary
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """Quick connectivity check — fetch ES=F snapshot."""
        try:
            snap = await self.get_snapshot("es")
            return snap is not None and snap.get("last") is not None
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fast_price(self, ticker: str) -> float | None:
        """Return the latest price for *ticker* via chart API, or None."""
        chart = await self._yahoo_chart(ticker, range_="5d", interval="1d")
        if chart is None:
            return None
        last = _safe_float(chart.get("meta", {}).get("regularMarketPrice"))
        if last is None:
            # Fallback: last non-None close from bar data
            closes = chart.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            for c in reversed(closes):
                if c is not None:
                    last = _safe_float(c)
                    break
        return last


# ── Module-level helpers ─────────────────────────────────────────────


def _parse_snapshot(
    chart: dict[str, Any], inst: dict[str, Any], instrument: str,
) -> dict[str, Any] | None:
    """Convert a Yahoo chart API result into a normalized snapshot dict."""
    meta = chart.get("meta", {})

    last = _safe_float(meta.get("regularMarketPrice"))
    prev_close = _safe_float(
        meta.get("chartPreviousClose") or meta.get("previousClose"),
    )

    if last is None:
        # Fallback: last non-None close from bar data
        closes = chart.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        for c in reversed(closes):
            if c is not None:
                last = _safe_float(c)
                break

    if last is None:
        _log.warning(
            "event=snapshot_no_price instrument=%s ticker=%s", instrument, inst["yahoo"],
        )
        return None

    change = round(last - prev_close, 4) if prev_close is not None else None
    change_pct = (
        round(change / prev_close, 6)
        if prev_close is not None and prev_close != 0 and change is not None
        else None
    )

    # Timestamp from regularMarketTime (unix epoch)
    ts_epoch = meta.get("regularMarketTime")
    ts_str = (
        datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat()
        if ts_epoch else None
    )

    return {
        "instrument": instrument,
        "label": inst["label"],
        "last": round(last, 2),
        "prev_close": round(prev_close, 2) if prev_close is not None else None,
        "change": round(change, 2) if change is not None else None,
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "open": _safe_float(meta.get("regularMarketOpen")),
        "high": _safe_float(meta.get("regularMarketDayHigh")),
        "low": _safe_float(meta.get("regularMarketDayLow")),
        "volume": _safe_int(meta.get("regularMarketVolume")),
        "timestamp": ts_str,
        "source": "yahoo_direct",
        "asset_class": inst.get("asset_class"),
        "underlying": inst.get("underlying"),
    }


def _parse_bars(chart: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a Yahoo chart API result into a list of OHLCV bar dicts."""
    timestamps = chart.get("timestamp") or []
    quotes = chart.get("indicators", {}).get("quote", [{}])[0]

    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    volumes = quotes.get("volume") or []

    bars: list[dict[str, Any]] = []
    for i, ts in enumerate(timestamps):
        c = _safe_float(closes[i]) if i < len(closes) else None
        if c is None:
            continue
        bars.append({
            "timestamp": (
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                if ts else None
            ),
            "open": _safe_float(opens[i]) if i < len(opens) else None,
            "high": _safe_float(highs[i]) if i < len(highs) else None,
            "low": _safe_float(lows[i]) if i < len(lows) else None,
            "close": c,
            "volume": _safe_int(volumes[i]) if i < len(volumes) else None,
        })
    return bars


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
