"""FMP (Financial Modeling Prep) client for market intelligence data.

Provides: market movers, sector rotation, pre-market quotes,
analyst upgrades/downgrades, historical price data, quotes,
technical indicators, and MACD.

Uses the ``/stable`` API base.  Rate limit is configurable via
``Settings.FMP_MAX_RPM`` (default 3 000 for Ultimate tier).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.config import Settings
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)


# ── Insider data normalization helpers ─────────────────────────────────

def _normalize_insider_role(raw: str) -> str:
    """Map FMP insider role/title to canonical set.

    Canonical roles: CEO, CFO, COO, CTO, CHIEF_OTHER, DIRECTOR,
    OWNER_10PCT, OTHER
    """
    if not raw:
        return "OTHER"
    upper = raw.upper()
    if "CEO" in upper or "CHIEF EXECUTIVE" in upper:
        return "CEO"
    if "CFO" in upper or "CHIEF FINANCIAL" in upper:
        return "CFO"
    if "COO" in upper or "CHIEF OPERATING" in upper:
        return "COO"
    if "DIRECTOR" in upper:
        return "DIRECTOR"
    if "CTO" in upper or "CHIEF TECHNOLOGY" in upper or "CHIEF TECH" in upper:
        return "CTO"
    if "CHIEF" in upper:
        return "CHIEF_OTHER"
    if "10%" in upper or "OWNER" in upper:
        return "OWNER_10PCT"
    if "PRESIDENT" in upper or "SVP" in upper or "VP" in upper or "OFFICER" in upper:
        return "OTHER"
    return "OTHER"


def _normalize_transaction_type(raw: str) -> str:
    """Map FMP transaction type codes to canonical set.

    FMP codes: P-Purchase, S-Sale, M-Option Exercise, A-Award/Grant, etc.
    Canonical: buy, sell, option_exercise, grant, other
    """
    if not raw:
        return "other"
    upper = raw.upper().strip()
    # Single-char codes (most common)
    if upper == "P" or upper.startswith("P-") or "PURCHASE" in upper:
        return "buy"
    if upper == "S" or upper.startswith("S-") or "SALE" in upper or "SELL" in upper:
        return "sell"
    if upper.startswith("M") or "EXERCISE" in upper or "CONVERSION" in upper:
        return "option_exercise"
    if upper.startswith("A") or "AWARD" in upper or "GRANT" in upper:
        return "grant"
    return "other"


class TokenBucketRateLimiter:
    """Async token-bucket rate limiter for FMP API calls.

    Default limit is derived from ``Settings.FMP_MAX_RPM`` (3 000 for
    Ultimate tier).  An 80 % safety margin gives 2 400 effective
    requests/min by default.

    When the bucket is empty, ``acquire()`` sleeps until a token is
    available rather than raising an exception.
    """

    def __init__(
        self,
        max_per_minute: int = 3000,
        safety_pct: float = 0.80,
    ) -> None:
        effective = int(max_per_minute * safety_pct)
        self._capacity = effective
        self._tokens = float(effective)
        self._refill_rate = effective / 60.0  # tokens per second
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume one."""
        async with self._lock:
            self._refill()
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._refill_rate
                logger.warning(
                    "FMP rate limiter throttling — sleeping %.2fs", wait,
                )
                await asyncio.sleep(wait)
                self._refill()
            self._tokens -= 1.0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now


class FMPClient:
    """Async FMP client with TTL caching and 402/401 gating."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
        cache: TTLCache,
        rate_limiter: TokenBucketRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.http_client = http_client
        self.cache = cache
        self._rate_limiter = rate_limiter or TokenBucketRateLimiter(
            max_per_minute=getattr(settings, "FMP_MAX_RPM", 3000),
        )
        self._disabled_paths: dict[str, float] = {}  # path → expiry timestamp

    # TTL for disabled paths (retry after 1 hour)
    _DISABLE_TTL = 3600

    def is_available(self) -> bool:
        return bool(self.settings.FMP_API_KEY)

    async def health(self) -> bool:
        """Canary check — fetch a single SPY quote.  Returns True if successful."""
        if not self.is_available():
            return False
        try:
            quote = await self.get_quote("SPY")
            return quote is not None and quote.get("price") is not None
        except Exception:
            return False

    # ── Public data methods ────────────────────────────────────

    async def get_market_gainers(self) -> list[dict[str, Any]] | None:
        return await self._fetch("/biggest-gainers", ttl=60)

    async def get_market_losers(self) -> list[dict[str, Any]] | None:
        return await self._fetch("/biggest-losers", ttl=60)

    async def get_market_actives(self) -> list[dict[str, Any]] | None:
        return await self._fetch("/most-actives", ttl=60)

    async def get_sector_performance(self, date: str | None = None) -> list[dict[str, Any]] | None:
        from datetime import datetime, timezone
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return await self._fetch(
            "/sector-performance-snapshot",
            params={"date": date},
            ttl=60,
        )

    async def get_sector_performance_historical(self, sector: str, days: int = 63) -> list[dict[str, Any]] | None:
        return await self._fetch(
            "/historical-sector-performance",
            params={"sector": sector, "limit": days},
            ttl=300,
        )

    async def get_pre_market_quotes(self) -> list[dict[str, Any]] | None:
        return await self._fetch("/pre-post-market", ttl=60)

    async def get_upgrades_downgrades(self, limit: int = 50) -> list[dict[str, Any]] | None:
        return await self._fetch(
            "/grades-latest-news",
            params={"limit": limit},
            ttl=60,
        )

    # ── Congressional / Insider trading methods ──────────────────

    async def get_senate_latest(self) -> list[dict[str, Any]] | None:
        """Latest Senate STOCK Act disclosures."""
        return await self._fetch("/senate-latest", ttl=300)

    async def get_house_latest(self) -> list[dict[str, Any]] | None:
        """Latest House STOCK Act disclosures."""
        return await self._fetch("/house-latest", ttl=300)

    async def get_insider_trading_latest(self, limit: int = 100) -> list[dict[str, Any]] | None:
        """Latest insider trades across all companies."""
        return await self._fetch(
            "/insider-trading-latest",
            params={"limit": limit},
            ttl=300,
        )

    # ── Per-symbol smart money endpoints (Ultimate tier) ────────

    async def get_institutional_holders(
        self,
        symbol: str,
        year: int,
        quarter: int,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]] | None:
        """13F institutional holders with analytics for a symbol.

        FMP endpoint: /institutional-ownership/extract-analytics/holder
        Returns per-holder: investorName, shares, value, change, changePercentage, etc.
        Cached 6 hours — data updates quarterly.
        """
        return await self._fetch(
            "/institutional-ownership/extract-analytics/holder",
            params={
                "symbol": symbol.upper(),
                "year": year,
                "quarter": quarter,
                "page": 0,
                "limit": limit,
            },
            ttl=21600,  # 6 hours
        )

    async def get_institutional_positions_summary(
        self,
        symbol: str,
        year: int,
        quarter: int,
    ) -> list[dict[str, Any]] | None:
        """Institutional positions summary for a symbol.

        FMP endpoint: /institutional-ownership/symbol-positions-summary
        Returns: investors count, totalInvestedValue, ownership percentages, etc.
        Cached 6 hours.
        """
        return await self._fetch(
            "/institutional-ownership/symbol-positions-summary",
            params={
                "symbol": symbol.upper(),
                "year": year,
                "quarter": quarter,
            },
            ttl=21600,
        )

    async def get_insider_trading_by_symbol(
        self, symbol: str, *, limit: int = 200,
    ) -> list[dict[str, Any]] | None:
        """Per-symbol insider trading (Form 4).

        FMP endpoint: /insider-trading/search
        Returns: reportingName, transactionType, securitiesTransacted, price, etc.
        Cached 1 hour — Form 4 filings are timely.
        """
        return await self._fetch(
            "/insider-trading/search",
            params={"symbol": symbol.upper(), "limit": limit, "page": 0},
            ttl=3600,
        )

    async def get_insider_transactions(
        self,
        symbol: str,
        lookback_days: int = 30,
    ) -> list[dict[str, Any]]:
        """Fetch insider Form 4 transactions for a symbol, normalized.

        Returns normalized transactions with:
            - insider_name: str
            - insider_role: str (CEO, CFO, COO, CTO, CHIEF_OTHER, DIRECTOR,
              OWNER_10PCT, OTHER)
            - transaction_type: str (buy, sell, option_exercise, grant, other)
            - transaction_date: str (ISO date, YYYY-MM-DD)
            - shares: int
            - price_per_share: float
            - total_value: float (shares × price)
            - filing_date: str

        Filters to transactions within lookback_days of today.
        Skips malformed rows (missing date/name).
        Uses the raw /insider-trading/search endpoint with 1h cache.
        """
        from datetime import datetime, timedelta, timezone

        raw = await self.get_insider_trading_by_symbol(symbol, limit=200)
        if not raw:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        results: list[dict[str, Any]] = []
        for row in raw:
            # Parse date — skip malformed rows
            date_str = row.get("transactionDate") or row.get("filingDate")
            if not date_str:
                continue
            try:
                tx_date = datetime.strptime(
                    str(date_str).split("T")[0], "%Y-%m-%d",
                ).replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue

            if tx_date < cutoff:
                continue

            name = row.get("reportingName") or row.get("reportingCik")
            if not name:
                continue

            role = _normalize_insider_role(
                row.get("typeOfOwner") or row.get("reportingRelation") or "",
            )
            tx_type = _normalize_transaction_type(
                row.get("transactionType") or "",
            )
            shares = abs(int(row.get("securitiesTransacted", 0) or 0))
            price = float(row.get("price", 0) or 0)

            results.append({
                "insider_name": str(name).strip(),
                "insider_role": role,
                "transaction_type": tx_type,
                "transaction_date": tx_date.strftime("%Y-%m-%d"),
                "shares": shares,
                "price_per_share": round(price, 2),
                "total_value": round(shares * price, 2),
                "filing_date": str(
                    row.get("filingDate", ""),
                ).split("T")[0],
            })

        return results

    async def get_insider_trade_statistics(
        self, symbol: str,
    ) -> list[dict[str, Any]] | None:
        """Insider trade statistics for a symbol.

        FMP endpoint: /insider-trading/statistics
        Returns: total buys/sells, net, etc.
        Cached 1 hour.
        """
        return await self._fetch(
            "/insider-trading/statistics",
            params={"symbol": symbol.upper()},
            ttl=3600,
        )

    async def get_mutual_fund_holders(
        self, symbol: str,
    ) -> list[dict[str, Any]] | None:
        """Mutual fund & ETF disclosure holders for a symbol.

        FMP endpoint: /funds/disclosure-holders-latest
        Cached 6 hours — updates quarterly.
        """
        return await self._fetch(
            "/funds/disclosure-holders-latest",
            params={"symbol": symbol.upper()},
            ttl=21600,
        )

    async def get_senate_trades(
        self, symbol: str,
    ) -> list[dict[str, Any]] | None:
        """Senate trades for a specific symbol.

        FMP endpoint: /senate-trades
        Cached 5 min.
        """
        return await self._fetch(
            "/senate-trades",
            params={"symbol": symbol.upper()},
            ttl=300,
        )

    async def get_house_trades(
        self, symbol: str,
    ) -> list[dict[str, Any]] | None:
        """House trades for a specific symbol.

        FMP endpoint: /house-trades
        Cached 5 min.
        """
        return await self._fetch(
            "/house-trades",
            params={"symbol": symbol.upper()},
            ttl=300,
        )

    async def get_shares_float(
        self, symbol: str,
    ) -> list[dict[str, Any]] | None:
        """Shares float data for a symbol.

        FMP endpoint: /shares-float
        Returns: freeFloat, floatShares, outstandingShares, etc.
        Cached 6 hours.
        """
        return await self._fetch(
            "/shares-float",
            params={"symbol": symbol.upper()},
            ttl=21600,
        )

    # ── 13F pillar / institutional aggregate endpoints ─────────

    async def get_institutional_ownership_by_holder(
        self,
        cik: str,
        *,
        year: int | None = None,
        quarter: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]] | None:
        """Reverse lookup: all holdings for a specific 13F filer by CIK.

        FMP endpoint: /institutional-ownership/portfolio-holdings
        Returns per-holding: symbol, shares, value, changeInShares, etc.
        Cached 24 hours — 13F data is quarterly.
        """
        params: dict[str, Any] = {"cik": cik, "limit": limit, "page": 0}
        if year is not None:
            params["year"] = year
        if quarter is not None:
            params["quarter"] = quarter
        return await self._fetch(
            "/institutional-ownership/portfolio-holdings",
            params=params,
            ttl=86400,  # 24 hours — quarterly data
        )

    async def get_institutional_ownership_list(
        self,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]] | None:
        """List institutional 13F filers with AUM/holdings count.

        FMP endpoint: /institutional-ownership/list
        Returns: cik, investorName, totalInvestedValue, holdingsCount, etc.
        Cached 24 hours — stable list, used to build tier-2 filers.
        """
        return await self._fetch(
            "/institutional-ownership/list",
            params={"limit": limit},
            ttl=86400,
        )

    async def get_company_profile(
        self, symbol: str,
    ) -> dict[str, Any] | None:
        """Company profile including sector and industry (GICS).

        FMP endpoint: /profile?symbol={symbol}
        Returns: symbol, companyName, sector, industry, mktCap, etc.
        Cached 24 hours — profile data is stable.
        """
        raw = await self._fetch(
            "/profile",
            params={"symbol": symbol},
            ttl=86400,
        )
        if not raw:
            return None
        return raw[0] if isinstance(raw, list) and raw else raw

    async def get_13f_filing_dates(
        self,
        cik: str,
    ) -> list[dict[str, Any]] | None:
        """Get 13F filing dates for a specific filer.

        FMP endpoint: /institutional-ownership/portfolio-date
        Returns: [{"date": "2025-12-31"}, ...] — the reporting periods.
        Cached 1 hour during filing windows to detect new filings.
        """
        return await self._fetch(
            "/institutional-ownership/portfolio-date",
            params={"cik": cik},
            ttl=3600,
        )

    # ── Economic calendar ───────────────────────────────────────

    async def get_economic_calendar(
        self, from_date: str, to_date: str,
    ) -> list[dict[str, Any]] | None:
        """Fetch economic calendar events from FMP.

        Returns list of events with: event, date, country, actual, estimate,
        previous, impact, etc.  Returns None if plan-blocked or unavailable.
        """
        return await self._fetch(
            "/economic-calendar",
            params={"from": from_date, "to": to_date},
            ttl=300,
        )

    # ── Breadth / screener methods ─────────────────────────────

    async def get_stock_screener(
        self,
        *,
        market_cap_min: int = 100_000_000,
        exchange: str = "nyse,nasdaq",
        limit: int = 5000,
    ) -> list[dict[str, Any]] | None:
        """Fetch broad stock screener for breadth calculations.

        Cached for 120s since this is a heavy call returning thousands of rows.
        """
        return await self._fetch(
            "/stock-screener",
            params={
                "marketCapMoreThan": market_cap_min,
                "isActivelyTrading": "true",
                "exchange": exchange,
                "limit": limit,
            },
            ttl=120,
        )

    # ── Internal fetch with caching + 402 gating ──────────────

    async def _fetch(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        ttl: int = 60,
    ) -> list[dict[str, Any]] | None:
        if not self.is_available():
            return None

        if path in self._disabled_paths:
            if time.time() < self._disabled_paths[path]:
                return None
            # TTL expired — retry this path
            logger.info("FMP re-enabling previously disabled path %s", path)
            del self._disabled_paths[path]

        cache_key = f"fmp:{path}:{params or ''}"

        async def _load() -> list[dict[str, Any]] | None:
            url = f"{self.settings.FMP_BASE_URL}{path}"
            full_params: dict[str, Any] = {"apikey": self.settings.FMP_API_KEY}
            if params:
                full_params.update(params)

            await self._rate_limiter.acquire()
            try:
                resp = await self.http_client.get(url, params=full_params, timeout=10.0)
            except httpx.HTTPError as exc:
                logger.warning("FMP network error on %s: %s", path, exc)
                return None

            if resp.status_code == 402:
                logger.warning("FMP 402 (plan limit) on %s — disabling for 1 hour", path)
                self._disabled_paths[path] = time.time() + self._DISABLE_TTL
                return None

            if resp.status_code == 401:
                logger.error("FMP 401 on %s — check FMP_API_KEY", path)
                return None

            if resp.status_code >= 400:
                logger.warning("FMP HTTP %d on %s: %s", resp.status_code, path, resp.text[:200])
                return None

            try:
                return resp.json()
            except Exception:
                logger.warning("FMP bad JSON on %s", path)
                return None

        return await self.cache.get_or_set(cache_key, ttl, _load)

    # ── Historical price data (replaces Polygon get_aggregates_ohlc / get_daily_bars / get_daily_closes) ──

    async def get_historical_price_eod(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """Fetch end-of-day historical OHLCV bars from FMP.

        FMP endpoint: /historical-price-eod/full?symbol={symbol}
        Replaces: Polygon ``get_aggregates_ohlc()``, ``get_daily_bars()``,
                  ``get_daily_closes()``, ``get_daily_closes_dated()``.

        For VIX data, pass symbol="^VIX".

        FMP returns fields:  date, open, high, low, close, volume, adjClose,
        change, changePercent, vwap, label, changeOverTime.

        This method normalises each bar to the Polygon-compatible shape:
            {"date": str, "open": float, "high": float, "low": float,
             "close": float, "volume": int}
        so existing callers that consume Polygon bars can switch over
        without changes.

        Returns oldest-first order (FMP returns newest-first by default).
        Returns None on API error or unavailability.
        """
        params: dict[str, Any] = {"symbol": symbol}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        raw = await self._fetch(
            "/historical-price-eod/full",
            params=params,
            ttl=1800,  # 30 min — daily bars don't change intraday
        )
        if not raw:
            return None

        bars: list[dict[str, Any]] = []
        for row in raw:
            try:
                bars.append({
                    "date": row["date"],
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": int(row.get("volume", 0)),
                })
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug("FMP eod bar parse skip: %s", exc)
                continue

        # FMP returns newest-first; Polygon callers expect oldest-first
        bars.sort(key=lambda b: b["date"])
        return bars

    # ── Snapshot / quote (replaces Polygon get_snapshot) ─────────────────

    async def get_quote(self, symbol: str) -> dict[str, Any] | None:
        """Fetch current quote snapshot from FMP.

        FMP endpoint: /quote?symbol={symbol}
        Replaces: Polygon ``get_snapshot()``.

        For VIX data, pass symbol="^VIX".

        FMP returns (among others): symbol, price, changesPercentage, change,
        dayLow, dayHigh, open, previousClose, volume, avgVolume, timestamp.

        This method normalises to the Polygon-compatible shape:
            {"symbol": str, "price": float, "last": float,
             "open": float, "high": float, "low": float, "close": float,
             "volume": int, "prev_close": float, "change": float,
             "change_percentage": float, "updated": str, "source": "fmp"}
        so existing callers that consume Polygon snapshots can switch
        without changes.

        Returns None on API error or unavailability.
        """
        raw = await self._fetch(
            "/quote",
            params={"symbol": symbol},
            ttl=120,
        )
        if not raw:
            return None

        # /quote returns a list with one item
        row = raw[0] if isinstance(raw, list) and raw else raw
        if not isinstance(row, dict):
            return None

        try:
            price = float(row.get("price", 0))
            year_high = row.get("yearHigh")
            year_low = row.get("yearLow")
            return {
                "symbol": row.get("symbol", symbol),
                "price": price,
                "last": price,
                "open": float(row.get("open", 0)),
                "high": float(row.get("dayHigh", 0)),
                "low": float(row.get("dayLow", 0)),
                "close": price,
                "volume": int(row.get("volume", 0)),
                "prev_close": float(row.get("previousClose", 0)),
                "change": float(row.get("change", 0)),
                "change_percentage": float(row.get("changesPercentage", 0)),
                "week_52_high": float(year_high) if year_high is not None else None,
                "week_52_low": float(year_low) if year_low is not None else None,
                "updated": str(row.get("timestamp", "")),
                "source": "fmp",
            }
        except (TypeError, ValueError) as exc:
            logger.warning("FMP quote parse error for %s: %s", symbol, exc)
            return None

    # ── Intraday bars (replaces Polygon get_intraday_bars) ──────────────────

    async def get_intraday_bars(
        self,
        symbol: str,
        interval: str = "1hour",
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """Fetch intraday OHLCV bars at the specified interval.

        FMP endpoint: /historical-chart/{interval}?symbol={symbol}
        Replaces: Polygon ``get_intraday_bars()`` for chart rendering.
        FMP Ultimate tier is required for intraday endpoints.

        Supported intervals: 1min, 5min, 15min, 30min, 1hour, 4hour.
        Default is 1hour to match current Polygon usage pattern.

        Normalises each bar to the shape consumed by chart rendering:
            {"date": ISO-datetime str, "close": float}
        Callers only use date + close; full OHLCV is not propagated.

        Returns oldest-first order (FMP returns newest-first by default).
        Returns None on API error or unavailability.
        """
        params: dict[str, Any] = {"symbol": symbol}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        raw = await self._fetch(
            f"/historical-chart/{interval}",
            params=params,
            ttl=300,  # 5 min — intraday bars refresh more often than daily
        )
        if not raw:
            return None

        bars: list[dict[str, Any]] = []
        for row in raw:
            try:
                bars.append({
                    "date": row["date"],
                    "close": float(row["close"]),
                })
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug("FMP intraday bar parse skip: %s", exc)
                continue

        # FMP returns newest-first; callers expect oldest-first
        bars.sort(key=lambda b: b["date"])
        return bars

    # ── Technical indicators (replaces Polygon get_rsi / get_sma / get_ema) ──

    async def get_technical_indicator(
        self,
        symbol: str,
        indicator: str,
        period_length: int,
        timeframe: str = "1day",
    ) -> list[dict[str, Any]] | None:
        """Fetch a technical indicator time series from FMP.

        FMP endpoint: /technical-indicators/{indicator}?symbol={symbol}
                      &periodLength={period_length}&timeframe={timeframe}
        Replaces: Polygon ``get_rsi()``, ``get_sma()``, ``get_ema()``.

        Accepted ``indicator`` values: "rsi", "sma", "ema", "wma", "dema",
        "tema", "williams", "adx", "standardDeviation", and others supported
        by FMP's /stable/technical-indicators/ family.

        Returns oldest-first list of:
            {"date": str, "value": float}
        matching the Polygon indicator shape ("value" + "timestamp").

        Returns None on API error or unavailability.
        """
        raw = await self._fetch(
            f"/technical-indicators/{indicator}",
            params={
                "symbol": symbol,
                "periodLength": period_length,
                "timeframe": timeframe,
            },
            ttl=1800,
        )
        if not raw:
            return None

        points: list[dict[str, Any]] = []
        for row in raw:
            try:
                points.append({
                    "date": row["date"],
                    "value": float(row.get(indicator, row.get("value", 0))),
                })
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug("FMP indicator parse skip: %s", exc)
                continue

        points.sort(key=lambda p: p["date"])
        return points

    # ── MACD (locally computed from two EMA calls — replaces Polygon get_macd) ──

    async def get_macd(
        self,
        symbol: str,
        timeframe: str = "1day",
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> dict[str, Any] | None:
        """Compute MACD locally from FMP EMA data.

        This does NOT call a single FMP MACD endpoint.  Instead it:
          1. Fetches EMA(fast_period) via ``get_technical_indicator()``
          2. Fetches EMA(slow_period) via ``get_technical_indicator()``
          3. Computes macd_line = ema_fast − ema_slow (aligned by date)
          4. Computes signal_line = EMA(signal_period) of macd_line
             using the standard EMA formula:
               EMA_today = value × k + EMA_yesterday × (1 − k)
               where k = 2 / (period + 1)
          5. Computes histogram = macd_line − signal_line

        Replaces: Polygon ``get_macd()``.

        Returns::

            {
                "macd": [{"date": str, "value": float}, ...],
                "signal": [{"date": str, "value": float}, ...],
                "histogram": [{"date": str, "value": float}, ...],
            }

        All lists are oldest-first and aligned by date.
        Returns None if either EMA fetch fails.
        """
        ema_fast, ema_slow = await asyncio.gather(
            self.get_technical_indicator(symbol, "ema", fast_period, timeframe),
            self.get_technical_indicator(symbol, "ema", slow_period, timeframe),
        )
        if not ema_fast or not ema_slow:
            return None

        # Build date-keyed lookup for alignment
        fast_by_date = {p["date"]: p["value"] for p in ema_fast}
        slow_by_date = {p["date"]: p["value"] for p in ema_slow}

        common_dates = sorted(set(fast_by_date) & set(slow_by_date))
        if not common_dates:
            return None

        # MACD line = EMA(fast) − EMA(slow)
        macd_line = [
            {"date": d, "value": fast_by_date[d] - slow_by_date[d]}
            for d in common_dates
        ]

        # Signal line = EMA(signal_period) of MACD line values
        k = 2.0 / (signal_period + 1)
        signal_line: list[dict[str, Any]] = []
        ema_val: float | None = None
        for point in macd_line:
            if ema_val is None:
                ema_val = point["value"]  # seed with first value
            else:
                ema_val = point["value"] * k + ema_val * (1.0 - k)
            signal_line.append({"date": point["date"], "value": ema_val})

        # Histogram = MACD − Signal
        histogram = [
            {"date": m["date"], "value": m["value"] - s["value"]}
            for m, s in zip(macd_line, signal_line)
        ]

        return {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram,
        }
