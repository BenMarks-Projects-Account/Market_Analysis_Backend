"""Breadth & Participation Data Provider.

Responsible for fetching and assembling raw breadth data from market data
sources. This layer transforms raw API responses into the normalized input
dicts expected by the breadth engine.

Supports multiple universes (v1: single universe via Tradier bulk quotes).
Calculates breadth statistics from constituent OHLCV and moving averages.

Data source hierarchy:
  - Tradier: bulk quotes, daily bars, OHLCV (source of truth for pricing)
  - Future: exchange-level breadth feeds, constituent membership APIs

Each method logs:
  - data source used
  - observation counts
  - missing/excluded tickers
  - staleness indicators
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ── Default universe constituents ────────────────────────────────────
# Phase 1: Use a curated subset of major ETF/index representatives.
# Future: fetch live constituent lists from data provider.

# SP500 top holdings and sector representatives (v1 proxy universe)
# This is NOT the full S&P 500 — it's a representative sample.
# Point-in-time constituency is NOT available in v1. Survivorship bias risk is logged.
SP500_PROXY: list[str] = [
    # Technology
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AVGO", "ADBE", "CRM", "AMD", "INTC",
    "CSCO", "ORCL", "ACN", "TXN", "QCOM", "AMAT", "INTU", "MU", "NOW", "LRCX",
    # Healthcare
    "UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "MDT", "ISRG", "GILD", "CVS", "CI", "SYK", "ZTS", "REGN", "VRTX",
    # Financials
    "BRK.B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "BLK",
    "SPGI", "C", "SCHW", "CB", "PGR", "MMC", "ICE", "AON", "CME", "USB",
    # Consumer Discretionary
    "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG",
    # Consumer Staples
    "PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "CL", "MDLZ", "KHC",
    # Energy
    "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "WMB",
    # Industrials
    "GE", "CAT", "HON", "UNP", "UPS", "BA", "RTX", "LMT", "DE", "MMM",
    # Materials
    "LIN", "APD", "SHW", "ECL", "FCX", "NEM", "DOW", "DD", "NUE", "VMC",
    # Utilities
    "NEE", "DUK", "SO", "D", "AEP", "SRE", "EXC", "XEL", "ES", "WEC",
    # REITs
    "PLD", "AMT", "CCI", "EQIX", "SPG", "PSA", "O", "WELL", "DLR", "AVB",
    # Communication Services
    "GOOG", "META", "DIS", "CMCSA", "NFLX", "VZ", "T", "TMUS", "CHTR", "EA",
]

# Sector classification map (ticker → GICS sector name)
SECTOR_MAP: dict[str, str] = {}
_SECTOR_ASSIGNMENTS = {
    "Technology": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AVGO", "ADBE", "CRM", "AMD", "INTC",
        "CSCO", "ORCL", "ACN", "TXN", "QCOM", "AMAT", "INTU", "MU", "NOW", "LRCX",
    ],
    "Healthcare": [
        "UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY",
        "AMGN", "MDT", "ISRG", "GILD", "CVS", "CI", "SYK", "ZTS", "REGN", "VRTX",
    ],
    "Financials": [
        "BRK.B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "BLK",
        "SPGI", "C", "SCHW", "CB", "PGR", "MMC", "ICE", "AON", "CME", "USB",
    ],
    "Consumer Discretionary": [
        "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG",
    ],
    "Consumer Staples": [
        "PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "CL", "MDLZ", "KHC",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "WMB",
    ],
    "Industrials": [
        "GE", "CAT", "HON", "UNP", "UPS", "BA", "RTX", "LMT", "DE", "MMM",
    ],
    "Materials": [
        "LIN", "APD", "SHW", "ECL", "FCX", "NEM", "DOW", "DD", "NUE", "VMC",
    ],
    "Utilities": [
        "NEE", "DUK", "SO", "D", "AEP", "SRE", "EXC", "XEL", "ES", "WEC",
    ],
    "REITs": [
        "PLD", "AMT", "CCI", "EQIX", "SPG", "PSA", "O", "WELL", "DLR", "AVB",
    ],
    "Communication Services": [
        "GOOG", "DIS", "CMCSA", "NFLX", "VZ", "T", "TMUS", "CHTR", "EA",
    ],
}
for _sector, _tickers in _SECTOR_ASSIGNMENTS.items():
    for _t in _tickers:
        SECTOR_MAP[_t] = _sector

# Deduplicated ticker list (some appear in multiple lists above)
_ALL_TICKERS = sorted(set(SP500_PROXY))

# Benchmark symbols
_INDEX_SYMBOL = "SPY"    # cap-weighted S&P 500 proxy
_EW_SYMBOL = "RSP"      # equal-weight S&P 500 ETF


def _safe_float(value: Any, default: float | None = None) -> float | None:
    """Safely coerce to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sma(prices: list[float], period: int) -> float | None:
    """Simple moving average of last `period` values.

    Formula: sum(prices[-period:]) / period
    """
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


class BreadthDataProvider:
    """Fetches and assembles breadth data from market sources.

    Designed for single-universe v1 with clear interfaces for
    multi-universe extension.
    """

    def __init__(
        self,
        tradier_client: Any,
        *,
        universe: list[str] | None = None,
        universe_name: str = "SP500_Proxy",
        history_days: int = 250,
    ) -> None:
        self.tradier_client = tradier_client
        self.universe = universe or _ALL_TICKERS
        self.universe_name = universe_name
        self.history_days = history_days

    async def fetch_breadth_data(self) -> dict[str, Any]:
        """Fetch all data needed for the breadth engine.

        Returns a dict with keys matching engine input structure:
          participation_data, trend_data, volume_data,
          leadership_data, stability_data, universe_meta

        Logs: universe size, fetch results, missing tickers, timing.
        """
        start_time = datetime.now(timezone.utc)
        universe = list(set(self.universe))  # deduplicate
        expected_count = len(universe)

        logger.info(
            "event=breadth_data_fetch_start universe=%s expected=%d",
            self.universe_name, expected_count,
        )

        # ── Fetch bulk quotes for all universe tickers ───────────
        # Tradier supports bulk quote API (comma-separated symbols)
        # We batch to avoid URL length limits
        all_quotes: dict[str, dict[str, Any]] = {}
        batch_size = 50
        for i in range(0, len(universe), batch_size):
            batch = universe[i:i + batch_size]
            try:
                batch_quotes = await self.tradier_client.get_quotes(batch)
                all_quotes.update(batch_quotes)
            except Exception as exc:
                logger.warning(
                    "event=breadth_quote_batch_failed batch_start=%d error=%s",
                    i, exc,
                )

        # ── Fetch index and EW benchmark quotes ──────────────────
        benchmark_quotes: dict[str, dict[str, Any]] = {}
        try:
            benchmark_quotes = await self.tradier_client.get_quotes(
                [_INDEX_SYMBOL, _EW_SYMBOL]
            )
        except Exception as exc:
            logger.warning("event=breadth_benchmark_fetch_failed error=%s", exc)

        # ── Fetch historical bars for trend/stability (index + sample) ──
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_date = (
            datetime.now(timezone.utc) - timedelta(days=self.history_days)
        ).strftime("%Y-%m-%d")

        # Fetch historical bars for all universe tickers for MA computation
        # This is expensive — in production, cache aggressively
        ticker_bars: dict[str, list[dict[str, Any]]] = {}
        # Fetch in parallel batches (use asyncio.gather with rate awareness)
        sem = asyncio.Semaphore(10)  # limit concurrent requests

        async def _fetch_bars(ticker: str) -> tuple[str, list[dict[str, Any]]]:
            async with sem:
                try:
                    bars = await self.tradier_client.get_daily_bars(
                        ticker, start_date, end_date
                    )
                    return ticker, bars
                except Exception as exc:
                    logger.debug(
                        "event=breadth_bar_fetch_failed ticker=%s error=%s",
                        ticker, exc,
                    )
                    return ticker, []

        bar_results = await asyncio.gather(
            *[_fetch_bars(t) for t in universe],
            return_exceptions=True,
        )
        for result in bar_results:
            if isinstance(result, Exception):
                continue
            ticker, bars = result
            if bars:
                ticker_bars[ticker] = bars

        # Also fetch benchmark bars
        for sym in [_INDEX_SYMBOL, _EW_SYMBOL]:
            try:
                bars = await self.tradier_client.get_daily_bars(
                    sym, start_date, end_date
                )
                ticker_bars[sym] = bars
            except Exception as exc:
                logger.warning(
                    "event=breadth_benchmark_bars_failed symbol=%s error=%s",
                    sym, exc,
                )

        # ── Compute breadth statistics ───────────────────────────
        fetch_duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        logger.info(
            "event=breadth_data_fetch_complete quotes=%d bars=%d "
            "duration_s=%.1f",
            len(all_quotes), len(ticker_bars), fetch_duration,
        )

        return self._assemble_breadth_data(
            all_quotes, benchmark_quotes, ticker_bars,
            universe, expected_count,
        )

    def _assemble_breadth_data(
        self,
        quotes: dict[str, dict[str, Any]],
        benchmark_quotes: dict[str, dict[str, Any]],
        ticker_bars: dict[str, list[dict[str, Any]]],
        universe: list[str],
        expected_count: int,
    ) -> dict[str, Any]:
        """Transform raw quotes and bars into engine input dicts.

        This is the core data transformation layer. All metric formulas are
        documented inline.
        """
        # ── Universe metadata ────────────────────────────────────
        valid_tickers = [t for t in universe if t in quotes]
        excluded_tickers = [t for t in universe if t not in quotes]
        actual_count = len(valid_tickers)

        universe_meta = {
            "name": self.universe_name,
            "expected_count": expected_count,
            "actual_count": actual_count,
            "coverage_pct": round(actual_count / max(expected_count, 1) * 100, 2),
            "excluded_count": len(excluded_tickers),
            "excluded_tickers_sample": excluded_tickers[:20],
            "stale_data_flag": False,
            "survivorship_bias_risk": True,  # v1 uses static list, not PIT
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "event=breadth_universe_assembled name=%s expected=%d actual=%d "
            "excluded=%d survivorship_bias=%s",
            self.universe_name, expected_count, actual_count,
            len(excluded_tickers), True,
        )

        # ── Participation data ───────────────────────────────────
        participation_data = self._compute_participation(
            quotes, valid_tickers, benchmark_quotes
        )

        # ── Trend data ───────────────────────────────────────────
        trend_data = self._compute_trend(ticker_bars, valid_tickers)

        # ── Volume data ──────────────────────────────────────────
        volume_data = self._compute_volume(quotes, valid_tickers)

        # ── Leadership data ──────────────────────────────────────
        leadership_data = self._compute_leadership(
            quotes, valid_tickers, benchmark_quotes
        )

        # ── Stability data ───────────────────────────────────────
        stability_data = self._compute_stability(ticker_bars, valid_tickers)

        return {
            "participation_data": participation_data,
            "trend_data": trend_data,
            "volume_data": volume_data,
            "leadership_data": leadership_data,
            "stability_data": stability_data,
            "universe_meta": universe_meta,
        }

    def _compute_participation(
        self,
        quotes: dict[str, dict[str, Any]],
        valid_tickers: list[str],
        benchmark_quotes: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute participation breadth metrics from quotes.

        Advancing: close > previous_close (or change > 0)
        Declining: close < previous_close (or change < 0)
        """
        advancing = 0
        declining = 0
        unchanged = 0
        new_highs = 0
        new_lows = 0

        for ticker in valid_tickers:
            q = quotes.get(ticker, {})
            change = _safe_float(q.get("change"))
            close = _safe_float(q.get("last") or q.get("close"))
            week_52_high = _safe_float(q.get("week_52_high"))
            week_52_low = _safe_float(q.get("week_52_low"))

            if change is not None:
                if change > 0:
                    advancing += 1
                elif change < 0:
                    declining += 1
                else:
                    unchanged += 1

            # New high/low detection (52-week)
            if close is not None and week_52_high is not None:
                if close >= week_52_high * 0.99:  # within 1% of 52w high
                    new_highs += 1
            if close is not None and week_52_low is not None:
                if close <= week_52_low * 1.01:  # within 1% of 52w low
                    new_lows += 1

        total_valid = advancing + declining + unchanged

        # ── Sector participation ─────────────────────────────────
        sector_changes: dict[str, list[float]] = {}
        for ticker in valid_tickers:
            sector = SECTOR_MAP.get(ticker)
            if not sector:
                continue
            q = quotes.get(ticker, {})
            change_pct = _safe_float(q.get("change_percentage"))
            if change_pct is not None:
                sector_changes.setdefault(sector, []).append(change_pct)

        sectors_positive = 0
        sectors_total = len(sector_changes)
        for sector, changes in sector_changes.items():
            avg = sum(changes) / len(changes) if changes else 0
            if avg > 0:
                sectors_positive += 1

        # ── Equal-weight confirmation ────────────────────────────
        spy_q = benchmark_quotes.get(_INDEX_SYMBOL, {})
        rsp_q = benchmark_quotes.get(_EW_SYMBOL, {})
        cw_return = _safe_float(spy_q.get("change_percentage"))
        ew_return = _safe_float(rsp_q.get("change_percentage"))

        # Convert percentage to decimal if needed
        if cw_return is not None and abs(cw_return) > 1:
            cw_return = cw_return / 100
        if ew_return is not None and abs(ew_return) > 1:
            ew_return = ew_return / 100

        return {
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
            "total_valid": total_valid,
            "new_highs": new_highs,
            "new_lows": new_lows,
            "sectors_positive": sectors_positive,
            "sectors_total": sectors_total,
            "ew_return": ew_return,
            "cw_return": cw_return,
        }

    def _compute_trend(
        self,
        ticker_bars: dict[str, list[dict[str, Any]]],
        valid_tickers: list[str],
    ) -> dict[str, Any]:
        """Compute trend breadth metrics from historical bars.

        For each ticker, compute:
          - whether close > 20/50/200 DMA
          - whether 20DMA > 50DMA, 50DMA > 200DMA
        """
        above_20 = 0
        above_50 = 0
        above_200 = 0
        ma20_over_50 = 0
        ma50_over_200 = 0
        counted = 0

        # For momentum: track current and prior pct_above values
        # (requires multi-day data which we have from bars)

        for ticker in valid_tickers:
            bars = ticker_bars.get(ticker, [])
            if not bars:
                continue

            closes = [_safe_float(b.get("close")) for b in bars]
            closes = [c for c in closes if c is not None]
            if len(closes) < 20:
                continue

            counted += 1
            current_close = closes[-1]

            sma_20 = _sma(closes, 20)
            sma_50 = _sma(closes, 50) if len(closes) >= 50 else None
            sma_200 = _sma(closes, 200) if len(closes) >= 200 else None

            if sma_20 is not None and current_close > sma_20:
                above_20 += 1
            if sma_50 is not None and current_close > sma_50:
                above_50 += 1
            if sma_200 is not None and current_close > sma_200:
                above_200 += 1
            if sma_20 is not None and sma_50 is not None and sma_20 > sma_50:
                ma20_over_50 += 1
            if sma_50 is not None and sma_200 is not None and sma_50 > sma_200:
                ma50_over_200 += 1

        total = max(counted, 1)

        # ── Trend momentum (change in breadth over rolling periods) ─
        # Compute pct_above_20dma for multiple lookback snapshots
        # This requires computing SMAs at prior dates from bars data
        mom_short = self._compute_trend_momentum(ticker_bars, valid_tickers, 20, 5)
        mom_int = self._compute_trend_momentum(ticker_bars, valid_tickers, 50, 10)
        mom_long = self._compute_trend_momentum(ticker_bars, valid_tickers, 200, 20)

        return {
            "total_valid": counted,
            "pct_above_20dma": above_20 / total,
            "pct_above_50dma": above_50 / total,
            "pct_above_200dma": above_200 / total,
            "pct_20_over_50": ma20_over_50 / total,
            "pct_50_over_200": ma50_over_200 / total,
            "trend_momentum_short": mom_short,
            "trend_momentum_intermediate": mom_int,
            "trend_momentum_long": mom_long,
        }

    def _compute_trend_momentum(
        self,
        ticker_bars: dict[str, list[dict[str, Any]]],
        valid_tickers: list[str],
        ma_period: int,
        lookback_days: int,
    ) -> float | None:
        """Compute change in pct_above_MA over `lookback_days`.

        Formula: pct_above_MA_today - pct_above_MA_{lookback_days_ago}

        Returns None if insufficient data.
        """
        above_now = 0
        above_prior = 0
        counted = 0

        for ticker in valid_tickers:
            bars = ticker_bars.get(ticker, [])
            if not bars:
                continue
            closes = [_safe_float(b.get("close")) for b in bars]
            closes = [c for c in closes if c is not None]
            if len(closes) < ma_period + lookback_days:
                continue

            counted += 1

            # Current snapshot
            current_close = closes[-1]
            current_sma = _sma(closes, ma_period)

            # Prior snapshot (lookback_days ago)
            prior_closes = closes[:-lookback_days]
            if len(prior_closes) < ma_period:
                continue
            prior_close = prior_closes[-1]
            prior_sma = _sma(prior_closes, ma_period)

            if current_sma is not None and current_close > current_sma:
                above_now += 1
            if prior_sma is not None and prior_close > prior_sma:
                above_prior += 1

        if counted < 10:
            return None

        pct_now = above_now / counted
        pct_prior = above_prior / counted
        return round(pct_now - pct_prior, 4)

    def _compute_volume(
        self,
        quotes: dict[str, dict[str, Any]],
        valid_tickers: list[str],
    ) -> dict[str, Any]:
        """Compute volume breadth metrics from quotes.

        Up volume: volume of stocks that advanced
        Down volume: volume of stocks that declined
        """
        up_volume = 0
        down_volume = 0
        total_volume = 0
        advancing = 0
        declining = 0

        for ticker in valid_tickers:
            q = quotes.get(ticker, {})
            change = _safe_float(q.get("change"))
            volume = _safe_float(q.get("volume"))

            if volume is None or volume <= 0:
                continue
            total_volume += volume

            if change is not None:
                if change > 0:
                    up_volume += volume
                    advancing += 1
                elif change < 0:
                    down_volume += volume
                    declining += 1

        return {
            "up_volume": up_volume,
            "down_volume": down_volume,
            "total_volume": total_volume,
            "advancing": advancing,
            "declining": declining,
        }

    def _compute_leadership(
        self,
        quotes: dict[str, dict[str, Any]],
        valid_tickers: list[str],
        benchmark_quotes: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute leadership quality metrics.

        Checks EW vs CW performance, sector breadth, median vs index.
        """
        spy_q = benchmark_quotes.get(_INDEX_SYMBOL, {})
        rsp_q = benchmark_quotes.get(_EW_SYMBOL, {})
        cw_return = _safe_float(spy_q.get("change_percentage"))
        ew_return = _safe_float(rsp_q.get("change_percentage"))

        # Normalize percentages to decimals
        if cw_return is not None and abs(cw_return) > 1:
            cw_return = cw_return / 100
        if ew_return is not None and abs(ew_return) > 1:
            ew_return = ew_return / 100

        index_return = cw_return

        # ── Per-stock returns ────────────────────────────────────
        stock_returns: list[float] = []
        outperforming = 0
        for ticker in valid_tickers:
            q = quotes.get(ticker, {})
            ret = _safe_float(q.get("change_percentage"))
            if ret is not None:
                # Normalize to decimal
                if abs(ret) > 1:
                    ret = ret / 100
                stock_returns.append(ret)
                if index_return is not None and ret > index_return:
                    outperforming += 1

        median_return = statistics.median(stock_returns) if stock_returns else None
        pct_outperf = outperforming / max(len(stock_returns), 1) if stock_returns else None

        # ── Sector returns ───────────────────────────────────────
        sector_returns: dict[str, float] = {}
        sector_ret_accum: dict[str, list[float]] = {}
        for ticker in valid_tickers:
            sector = SECTOR_MAP.get(ticker)
            if not sector:
                continue
            q = quotes.get(ticker, {})
            ret = _safe_float(q.get("change_percentage"))
            if ret is not None:
                if abs(ret) > 1:
                    ret = ret / 100
                sector_ret_accum.setdefault(sector, []).append(ret)

        for sector, rets in sector_ret_accum.items():
            sector_returns[sector] = sum(rets) / len(rets)

        return {
            "ew_return": ew_return,
            "cw_return": cw_return,
            "index_return": index_return,
            "median_return": median_return,
            "pct_outperforming_index": pct_outperf,
            "sector_returns": sector_returns,
        }

    def _compute_stability(
        self,
        ticker_bars: dict[str, list[dict[str, Any]]],
        valid_tickers: list[str],
    ) -> dict[str, Any]:
        """Compute participation stability metrics from historical bars.

        Requires multi-day breadth history to measure persistence/volatility.
        """
        # Compute daily A/D ratios and pct_above_20dma for recent sessions
        daily_ad_ratios: list[float] = []
        daily_pct_above_20: list[float] = []

        # We need to compute breadth for each of the last 10 trading days
        # Use the bars data to reconstruct daily snapshots
        # Find the common date range across tickers
        date_set: set[str] = set()
        for ticker in valid_tickers:
            bars = ticker_bars.get(ticker, [])
            for b in bars:
                d = b.get("date")
                if d:
                    date_set.add(d)

        sorted_dates = sorted(date_set)
        recent_dates = sorted_dates[-15:] if len(sorted_dates) >= 15 else sorted_dates

        for date in recent_dates:
            adv = 0
            dec = 0
            above_20 = 0
            counted_20 = 0
            total = 0

            for ticker in valid_tickers:
                bars = ticker_bars.get(ticker, [])
                if not bars:
                    continue

                # Find this date's bar and previous
                closes_up_to = []
                found_date = False
                prev_close = None

                for b in bars:
                    c = _safe_float(b.get("close"))
                    if c is None:
                        continue
                    closes_up_to.append(c)
                    if b.get("date") == date:
                        found_date = True
                        break
                    prev_close = c

                if not found_date or not closes_up_to:
                    continue

                current = closes_up_to[-1]
                total += 1

                if prev_close is not None:
                    if current > prev_close:
                        adv += 1
                    elif current < prev_close:
                        dec += 1

                # Check if above 20DMA
                if len(closes_up_to) >= 20:
                    sma20 = sum(closes_up_to[-20:]) / 20
                    counted_20 += 1
                    if current > sma20:
                        above_20 += 1

            if dec > 0:
                daily_ad_ratios.append(adv / dec)
            elif adv > 0:
                daily_ad_ratios.append(float(adv))

            if counted_20 > 0:
                daily_pct_above_20.append(above_20 / counted_20)

        # ── Compute stability metrics ────────────────────────────

        # breadth_persistence_10d: fraction of last 10 sessions with A/D > 1
        persistence_window = daily_ad_ratios[-10:] if len(daily_ad_ratios) >= 10 else daily_ad_ratios
        if persistence_window:
            persistence = sum(1 for r in persistence_window if r > 1) / len(persistence_window)
        else:
            persistence = None

        # ad_ratio_volatility_5d: std of last 5 A/D ratios
        vol_window = daily_ad_ratios[-5:] if len(daily_ad_ratios) >= 5 else daily_ad_ratios
        if len(vol_window) >= 2:
            ad_vol = statistics.stdev(vol_window)
        else:
            ad_vol = None

        # pct_above_20dma_volatility_5d: std of last 5 pct_above_20dma
        pct20_window = daily_pct_above_20[-5:] if len(daily_pct_above_20) >= 5 else daily_pct_above_20
        if len(pct20_window) >= 2:
            pct20_vol = statistics.stdev(pct20_window)
        else:
            pct20_vol = None

        return {
            "breadth_persistence_10d": persistence,
            "ad_ratio_volatility_5d": ad_vol,
            "pct_above_20dma_volatility_5d": pct20_vol,
        }
