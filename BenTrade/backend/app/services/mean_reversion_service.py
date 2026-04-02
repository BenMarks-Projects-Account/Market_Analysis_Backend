"""
BenTrade — Mean Reversion Bounce Stock Strategy Service
strategy_id: stock_mean_reversion

End-to-end scanner pipeline:
  1) Universe assembly (balanced: ~150-400 liquid stocks, ETFs excluded)
  2) Per-symbol OHLCV fetch (Tradier primary, async semaphore-limited)
  3) Mean-reversion-specific enrichment metrics (oversold + stabilization)
  4) Balanced filters: must be oversold/stretched AND showing stabilization
  5) Composite scoring: oversold(0-40) + stabilization(0-25) + room(0-20) + liquidity(0-15) = 0-100
  6) Trade shape construction with canonical trade_key

Data source policy:
  - Tradier is authoritative for universe + pricing
  - Fallback to BaseDataService history only if Tradier fails, with confidence downgrade

Scoring formula breakdown:
  oversold_score       (0-40): RSI14 in 25-35 sweet spot, zscore_20 penalty for extremes
  stabilization_score  (0-25): positive 1d/2d returns, no new lows, volume on green day
  room_score           (0-20): distance below SMA20 (snapback potential), penalize structural damage
  liquidity_score      (0-15): avg dollar volume, reasonable ATR%

TODO:
  - Multi-level gates / filter stack (future phase)
  - Presets framework (Strict / Balanced / Wide)
  - SPY market context overlay (regime-aware gating)
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base_data_service import BaseDataService
from app.services.scanner_candidate_contract import normalize_candidate_output
from app.utils.trade_key import stock_trade_key, stock_idea_key
from common.quant_analysis import rsi, simple_moving_average, realized_vol_annualized

logger = logging.getLogger(__name__)

# ── ETF / Index exclusion set ───────────────────────────────────────────────
_ETF_EXCLUSIONS: frozenset[str] = frozenset({
    # Broad market
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "IVV", "RSP",
    "MDY", "IJR", "IJH", "VB", "VO", "VV",
    # Sector SPDR
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLU", "XLP", "XLY", "XLB", "XLRE", "XLC",
    # Other sector / thematic
    "ARKK", "ARKW", "ARKF", "ARKG", "ARKQ",
    "SMH", "SOXX", "XBI", "IBB", "XOP", "OIH", "GDX", "GDXJ",
    "KRE", "KBE", "XHB", "ITB", "XRT", "HACK", "CIBR", "BOTZ",
    "TAN", "ICLN", "LIT", "REMX", "JETS", "PBW",
    # Bond / commodity / volatility
    "TLT", "IEF", "SHY", "AGG", "BND", "HYG", "JNK", "LQD", "TIP",
    "GLD", "SLV", "USO", "UNG", "DBA", "DBC",
    "VXX", "UVXY", "SVXY", "VIXY",
    # Leveraged / inverse
    "TQQQ", "SQQQ", "SPXL", "SPXS", "UPRO", "SDS", "SH",
    "QLD", "QID", "TNA", "TZA", "FAS", "FAZ",
    "LABU", "LABD", "SOXL", "SOXS", "TECL", "TECS",
    "NUGT", "DUST", "JNUG", "JDST", "ERX", "ERY",
    # Index symbols
    "SPX", "NDX", "RUT", "DJX", "XSP",
})

# ── Balanced universe: curated liquid large/mid-cap stocks ──────────────────
_BALANCED_UNIVERSE: list[str] = [
    # Technology
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD", "CRM", "AVGO",
    "ADBE", "INTC", "CSCO", "ORCL", "NOW", "SHOP", "SNOW", "PANW", "CRWD", "NET",
    "PLTR", "MDB", "DDOG", "ZS", "FTNT", "MRVL", "ANET", "TEAM", "WDAY", "TTD",
    "UBER", "DASH", "COIN", "SQ", "PYPL", "INTU", "SNPS", "CDNS", "KLAC", "LRCX",
    "AMAT", "MU", "ON", "MCHP", "TXN", "QCOM", "ARM", "SMCI",
    # Healthcare
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "GILD", "VRTX", "REGN", "ISRG", "MDT", "SYK", "BSX", "EW", "ZTS",
    "DXCM", "MRNA", "BIIB", "GEHC", "HCA", "IDXX",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "V",
    "MA", "COF", "USB", "PNC", "CME", "ICE", "MCO", "SPGI", "CB", "MMC",
    "AIG", "MET", "PRU", "ALL", "TRV",
    # Consumer discretionary
    "HD", "LOW", "NKE", "SBUX", "MCD", "TGT", "COST", "WMT", "TJX", "ROST",
    "LULU", "YUM", "DPZ", "CMG", "BKNG", "MAR", "HLT", "ABNB", "RCL", "NCLH",
    "F", "GM", "RIVN", "LCID",
    # Consumer staples
    "PG", "KO", "PEP", "PM", "MO", "CL", "EL", "KMB", "GIS", "K",
    "MDLZ", "HSY", "SJM", "STZ", "SAM",
    # Industrials
    "CAT", "DE", "HON", "UNP", "UPS", "FDX", "BA", "RTX", "LMT", "GD",
    "NOC", "GE", "MMM", "EMR", "ITW", "PH", "ROK", "ETN", "WM", "RSG",
    # Energy
    "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "VLO", "PSX", "OXY", "DVN",
    "HAL", "BKR", "FANG", "PXD",
    # Communication
    "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR",
    # Materials
    "LIN", "APD", "SHW", "ECL", "DD", "DOW", "NEM", "FCX", "STLD", "NUE",
    # Real estate (a few liquid REITs)
    "AMT", "PLD", "CCI", "EQIX", "SPG", "O", "PSA",
    # Utilities (a handful)
    "NEE", "DUK", "SO", "AEP", "D",
]

# ── Balanced config ─────────────────────────────────────────────────────────
_BALANCED_CONFIG: dict[str, Any] = {
    "min_history_bars": 120,           # mean reversion needs less history than SMA200 strategies
    "min_price": 5.0,
    "min_avg_dollar_vol": 15_000_000,  # $15M avg daily dollar volume
    "lookback_days": 300,              # calendar days → ~200 trading days
    "concurrency": 8,
    "per_symbol_timeout": 12.0,        # seconds
    # Filter thresholds
    "atr_pct_max": 0.10,               # max ATR% to avoid extremely wild names
    "dist_sma50_floor": -0.18,         # avoid deep structural breakdowns
}


# ────────────────────────────────────────────────────────────────────────────
# Service
# ────────────────────────────────────────────────────────────────────────────

class MeanReversionService:
    """Mean Reversion Bounce stock strategy scanner."""

    STRATEGY_ID = "stock_mean_reversion"

    def __init__(self, base_data_service: BaseDataService) -> None:
        self.bds = base_data_service

    # ── Public entry point ──────────────────────────────────────────────────

    async def scan(self, *, max_candidates: int = 30) -> dict[str, Any]:
        """Run the full mean reversion scan and return the payload."""
        cfg = _BALANCED_CONFIG
        t0 = datetime.now(timezone.utc)
        notes: list[str] = []
        rejections: list[dict[str, str]] = []

        # 1) Build universe
        symbols = self._build_universe()
        universe_count = len(symbols)
        notes.append(f"Universe: {universe_count} symbols (balanced, ETFs excluded)")

        # 2) Per-symbol scan (async, semaphore-limited)
        sem = asyncio.Semaphore(cfg["concurrency"])

        async def _scan_one(sym: str) -> dict[str, Any] | None:
            async with sem:
                try:
                    return await asyncio.wait_for(
                        self._scan_symbol(sym, cfg, rejections),
                        timeout=cfg["per_symbol_timeout"],
                    )
                except asyncio.TimeoutError:
                    rejections.append({"symbol": sym, "reason_code": "TIMEOUT", "detail": f">{cfg['per_symbol_timeout']}s"})
                    return None
                except Exception as exc:
                    rejections.append({"symbol": sym, "reason_code": "ERROR", "detail": str(exc)[:120]})
                    return None

        raw = await asyncio.gather(*[_scan_one(s) for s in symbols])
        candidates = [r for r in raw if r is not None]

        # 3) Sort by composite_score descending, trim
        candidates.sort(key=lambda c: c.get("composite_score", 0), reverse=True)
        candidates = candidates[:max_candidates]

        # 4) Add rank
        for i, c in enumerate(candidates, 1):
            c["rank"] = i

        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        notes.append(f"Scan completed in {elapsed:.1f}s — {len(candidates)} candidates from {universe_count} symbols")
        notes.append(f"Rejections: {len(rejections)}")

        source_health = {}
        try:
            source_health = self.bds.get_source_health_snapshot()
        except Exception:
            pass

        # Attach normalized candidate contract to each candidate
        for c in candidates:
            c["normalized"] = normalize_candidate_output(self.STRATEGY_ID, c)

        return {
            "strategy_id": self.STRATEGY_ID,
            "status": "ok",
            "mode": "balanced",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "universe": {
                "mode": "balanced",
                "symbols_count": universe_count,
                "symbols_sample": symbols[:20],
            },
            "candidates": candidates,
            "rejections": rejections,
            "notes": notes,
            "source_health": source_health,
            "scan_time_seconds": round(elapsed, 2),
        }

    # ── Universe builder ────────────────────────────────────────────────────

    def _build_universe(self) -> list[str]:
        """Return deduplicated, ETF-excluded symbol list."""
        seen: set[str] = set()
        result: list[str] = []
        for sym in _BALANCED_UNIVERSE:
            s = sym.strip().upper()
            if not s or s in seen or s in _ETF_EXCLUSIONS:
                continue
            seen.add(s)
            result.append(s)
        return result

    # ── Per-symbol scan ─────────────────────────────────────────────────────

    async def _scan_symbol(
        self,
        symbol: str,
        cfg: dict[str, Any],
        rejections: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        """Fetch data, compute metrics, apply filters, score, build trade shape."""

        # -- Fetch OHLCV bars: Polygon primary, Tradier fallback --
        data_source = "polygon"
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=cfg["lookback_days"])

        bars: list[dict[str, Any]] = []
        if self.bds.polygon_client is not None:
            try:
                bars = await self.bds.polygon_client.get_daily_bars(
                    symbol,
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                )
            except Exception as exc:
                logger.warning("event=mean_rev_bars_fail symbol=%s source=polygon error=%s", symbol, exc)

        # Fallback 1: Tradier full OHLCV bars
        if not bars:
            data_source = "tradier"
            try:
                bars = await self.bds.tradier_client.get_daily_bars(
                    symbol,
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                )
            except Exception as exc:
                logger.warning("event=mean_rev_bars_fail symbol=%s source=tradier error=%s", symbol, exc)

        # Fallback 2: BaseDataService history (close-only)
        if not bars:
            data_source = "fallback"
            try:
                closes = await self.bds.get_prices_history(symbol, lookback_days=cfg["lookback_days"])
                if closes:
                    bars = [{"date": None, "open": None, "high": None, "low": None, "close": c, "volume": None} for c in closes]
            except Exception as exc:
                logger.warning("event=mean_rev_fallback_fail symbol=%s error=%s", symbol, exc)

        if not bars:
            rejections.append({"symbol": symbol, "reason_code": "NO_DATA", "detail": "No price history available"})
            return None

        # -- Extract series --
        closes = [b["close"] for b in bars if b.get("close") is not None]
        volumes = [b["volume"] for b in bars if b.get("volume") is not None]
        highs = [b["high"] for b in bars if b.get("high") is not None]
        lows = [b["low"] for b in bars if b.get("low") is not None]
        opens = [b["open"] for b in bars if b.get("open") is not None]

        # -- Hard requirements --
        if len(closes) < cfg["min_history_bars"]:
            rejections.append({"symbol": symbol, "reason_code": "INSUFFICIENT_HISTORY", "detail": f"{len(closes)} bars < {cfg['min_history_bars']}"})
            return None

        price = closes[-1]
        if price < cfg["min_price"]:
            rejections.append({"symbol": symbol, "reason_code": "PRICE_TOO_LOW", "detail": f"${price:.2f} < ${cfg['min_price']}"})
            return None

        # Volume check
        avg_vol_20 = sum(volumes[-20:]) / min(len(volumes[-20:]), 20) if len(volumes) >= 20 else None
        avg_dollar_vol_20 = (avg_vol_20 * price) if avg_vol_20 is not None else None

        if avg_dollar_vol_20 is not None and avg_dollar_vol_20 < cfg["min_avg_dollar_vol"]:
            rejections.append({"symbol": symbol, "reason_code": "LOW_LIQUIDITY", "detail": f"${avg_dollar_vol_20:,.0f} < ${cfg['min_avg_dollar_vol']:,}"})
            return None

        if avg_dollar_vol_20 is None:
            avg_vol_20 = None

        # -- Compute mean reversion metrics --
        metrics = self._compute_metrics(closes, highs, lows, opens, volumes, price)

        # -- Apply mean reversion filters --
        reject = self._apply_filters(symbol, metrics, cfg)
        if reject:
            rejections.append(reject)
            return None

        # -- Score --
        score_breakdown, composite_score = self._score(metrics)

        # -- Thesis bullets --
        thesis = self._build_thesis(metrics, score_breakdown, price)

        # -- Confidence --
        confidence = 1.0 if data_source == "tradier" else 0.7
        atr_pct = metrics.get("atr_pct")
        if atr_pct is not None and atr_pct > 0.06:
            confidence = max(0.5, confidence - 0.15)

        # -- Build trade shape --
        t_key = stock_trade_key(symbol, self.STRATEGY_ID)
        i_key = stock_idea_key(symbol, self.STRATEGY_ID)

        return {
            "symbol": symbol,
            "strategy_id": self.STRATEGY_ID,
            "trade_type": "stock_long",
            "trade_key": t_key,
            "idea_key": i_key,
            "price": round(price, 2),
            "underlying_price": round(price, 2),
            "entry_reference": round(price, 2),
            "composite_score": round(composite_score, 1),
            "score_breakdown": {k: round(v, 1) for k, v in score_breakdown.items()},
            "oversold_score": round(score_breakdown.get("oversold", 0), 1),
            "stabilization_score": round(score_breakdown.get("stabilization", 0), 1),
            "room_score": round(score_breakdown.get("room", 0), 1),
            "liquidity_score": round(score_breakdown.get("liquidity", 0), 1),
            "metrics": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in metrics.items()},
            "thesis": thesis,
            "reversion_state": metrics.get("reversion_state", "unknown"),
            "risk_notes": [],
            "as_of": datetime.now(timezone.utc).isoformat(),
            "confidence": round(confidence, 2),
            "data_source": {
                "history": data_source,
                "confidence": confidence,
            },
        }

    # ── Metrics computation ─────────────────────────────────────────────────

    @staticmethod
    def _compute_metrics(
        closes: list[float],
        highs: list[float],
        lows: list[float],
        opens: list[float],
        volumes: list[int],
        price: float,
    ) -> dict[str, Any]:
        """Compute all mean-reversion enrichment metrics.

        Derived fields & formulas documented inline per copilot-instructions requirement.
        """
        m: dict[str, Any] = {}

        # ── A) Oversold / stretch indicators ────────────────────────

        # Derived field: rsi14
        # Inputs: closes, period=14
        # Formula: standard RSI
        m["rsi14"] = rsi(closes, 14)

        # Derived field: rsi2
        # Inputs: closes, period=2
        # Formula: standard RSI with 2-period (very short-term oversold)
        m["rsi2"] = rsi(closes, 2)

        # Moving averages
        sma20 = simple_moving_average(closes, 20)
        sma50 = simple_moving_average(closes, 50)
        sma200 = simple_moving_average(closes, 200) if len(closes) >= 200 else None
        m["sma20"] = sma20
        m["sma50"] = sma50
        m["sma200"] = sma200

        # Derived field: zscore_20
        # Inputs: price, closes[-20:], SMA20
        # Formula: (price - SMA20) / stddev(closes[-20:])
        if sma20 is not None and len(closes) >= 20:
            window = closes[-20:]
            mean = sum(window) / len(window)
            variance = sum((x - mean) ** 2 for x in window) / len(window)
            stddev = math.sqrt(variance) if variance > 0 else 0.0
            m["zscore_20"] = (price - mean) / stddev if stddev > 0 else 0.0
            m["stddev_20"] = round(stddev, 4)
        else:
            m["zscore_20"] = None
            m["stddev_20"] = None

        # Derived field: dist_sma20
        # Inputs: price, sma20
        # Formula: (price - sma20) / sma20
        m["dist_sma20"] = (price - sma20) / sma20 if (sma20 is not None and sma20 > 0) else None

        # Derived field: dist_sma50
        # Inputs: price, sma50
        # Formula: (price - sma50) / sma50
        m["dist_sma50"] = (price - sma50) / sma50 if (sma50 is not None and sma50 > 0) else None

        # Derived field: drawdown_20
        # Inputs: price, max(highs[-20:])
        # Formula: (price - high_20) / high_20
        high_20 = max(highs[-20:]) if len(highs) >= 20 else (max(closes[-20:]) if len(closes) >= 20 else price)
        m["high_20"] = high_20
        m["drawdown_20"] = (price - high_20) / high_20 if high_20 > 0 else 0.0

        # Derived field: drawdown_55
        # Inputs: price, max(highs[-55:])
        # Formula: (price - high_55) / high_55
        high_55 = max(highs[-55:]) if len(highs) >= 55 else (max(closes[-55:]) if len(closes) >= 55 else price)
        m["high_55"] = high_55
        m["drawdown_55"] = (price - high_55) / high_55 if high_55 > 0 else 0.0

        # ── B) Volatility / risk context ────────────────────────────

        # Derived field: atr14
        # Inputs: highs, lows, closes (14 bars)
        # Formula: avg of max(H-L, |H-prevC|, |L-prevC|) over 14 periods
        atr14 = None
        if len(highs) >= 15 and len(lows) >= 15 and len(closes) >= 15:
            true_ranges: list[float] = []
            for i in range(-14, 0):
                h = highs[i]
                l = lows[i]
                prev_c = closes[i - 1]
                tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                true_ranges.append(tr)
            atr14 = sum(true_ranges) / len(true_ranges)
        m["atr14"] = atr14

        # Derived field: atr_pct
        # Inputs: atr14, price
        # Formula: atr14 / price
        m["atr_pct"] = (atr14 / price) if (atr14 is not None and price > 0) else None

        # Derived field: range_10_pct
        # Inputs: highs[-10:], lows[-10:], price
        # Formula: (max(highs[-10:]) - min(lows[-10:])) / price
        if len(highs) >= 10 and len(lows) >= 10:
            m["range_10_pct"] = (max(highs[-10:]) - min(lows[-10:])) / price if price > 0 else None
        else:
            m["range_10_pct"] = None

        # Derived field: realized_vol_20
        # Inputs: closes[-21:] (need 21 prices for 20 returns)
        # Formula: annualized std of log returns (uses quant_analysis helper)
        if len(closes) >= 21:
            m["realized_vol_20"] = realized_vol_annualized(closes[-21:], trading_days=252)
        else:
            m["realized_vol_20"] = None

        # ── C) Stabilization / bounce signals ───────────────────────

        # Derived field: return_1d
        # Inputs: closes[-1], closes[-2]
        # Formula: (closes[-1] - closes[-2]) / closes[-2]
        m["return_1d"] = (closes[-1] - closes[-2]) / closes[-2] if len(closes) >= 2 and closes[-2] > 0 else None

        # Derived field: return_2d
        # Inputs: closes[-1], closes[-3]
        # Formula: (closes[-1] - closes[-3]) / closes[-3]
        m["return_2d"] = (closes[-1] - closes[-3]) / closes[-3] if len(closes) >= 3 and closes[-3] > 0 else None

        # Derived field: return_5d
        # Inputs: closes[-1], closes[-6]
        # Formula: (closes[-1] - closes[-6]) / closes[-6]
        m["return_5d"] = (closes[-1] - closes[-6]) / closes[-6] if len(closes) >= 6 and closes[-6] > 0 else None

        # Derived field: bounce_hint
        # Inputs: closes[-1], closes[-2], closes[-3], lows[-2]
        # Formula: True if (close > prev_close) OR (close > prev_low) OR (2d_return >= 0.5%)
        bounce_hint = False
        if len(closes) >= 3:
            r1 = m.get("return_1d")
            r2 = m.get("return_2d")
            if r1 is not None and r1 > 0:
                bounce_hint = True
            elif len(lows) >= 2 and closes[-1] > lows[-2]:
                bounce_hint = True  # higher-low hint
            elif r2 is not None and r2 >= 0.005:
                bounce_hint = True
            # Not making new lows in last 3 bars
            if not bounce_hint and closes[-1] >= closes[-3]:
                bounce_hint = True
        m["bounce_hint"] = bounce_hint

        # SMA20 slope (downtrend pressure indicator)
        if len(closes) > 30 and sma20 is not None:
            sma20_lag = simple_moving_average(closes[:-10], 20)
            m["slope_sma20"] = ((sma20 - sma20_lag) / price) if (sma20_lag is not None and price > 0) else None
        else:
            m["slope_sma20"] = None

        # Derived field: downtrend_pressure
        # Inputs: slope_sma20
        # Formula: True if slope_sma20 < -0.005 (SMA20 falling meaningfully)
        slope = m.get("slope_sma20")
        m["downtrend_pressure"] = slope is not None and slope < -0.005

        # ── D) Liquidity / tradability ──────────────────────────────

        if volumes and len(volumes) >= 20:
            avg_v = sum(volumes[-20:]) / 20
            m["avg_vol_20"] = round(avg_v)
            m["avg_dollar_vol_20"] = round(avg_v * price)
            today_vol = volumes[-1] if volumes else None
            m["vol_spike_ratio"] = today_vol / avg_v if (today_vol is not None and avg_v > 0) else None
            m["today_vol"] = today_vol
        else:
            m["avg_vol_20"] = None
            m["avg_dollar_vol_20"] = None
            m["vol_spike_ratio"] = None
            m["today_vol"] = None

        # 52-week range
        if len(closes) >= 252:
            m["high_52w"] = max(closes[-252:])
            m["low_52w"] = min(closes[-252:])
        elif len(closes) >= 120:
            m["high_52w"] = max(closes)
            m["low_52w"] = min(closes)
        else:
            m["high_52w"] = None
            m["low_52w"] = None

        # ── E) Reversion state classification ───────────────────────

        # Derived field: reversion_state
        # Inputs: rsi14, zscore_20, dist_sma20, bounce_hint
        # Formula:
        #   "bounce_starting" if oversold AND bounce_hint
        #   "oversold" if oversold conditions met but no bounce yet
        #   "stretched" if mildly extended (zscore <= -1.0 or dist_sma20 <= -0.03)
        #   else "neutral"
        rsi14 = m.get("rsi14")
        rsi2 = m.get("rsi2")
        zscore = m.get("zscore_20")
        dist20 = m.get("dist_sma20")

        is_oversold = False
        if rsi14 is not None and rsi14 <= 35:
            is_oversold = True
        elif rsi2 is not None and rsi2 <= 10:
            is_oversold = True
        elif zscore is not None and zscore <= -1.5:
            is_oversold = True
        elif dist20 is not None and dist20 <= -0.05:
            is_oversold = True

        if is_oversold and bounce_hint:
            m["reversion_state"] = "bounce_starting"
        elif is_oversold:
            m["reversion_state"] = "oversold"
        elif (zscore is not None and zscore <= -1.0) or (dist20 is not None and dist20 <= -0.03):
            m["reversion_state"] = "stretched"
        else:
            m["reversion_state"] = "neutral"

        return m

    # ── Filters ─────────────────────────────────────────────────────────────

    @staticmethod
    def _apply_filters(
        symbol: str,
        metrics: dict[str, Any],
        cfg: dict[str, Any],
    ) -> dict[str, str] | None:
        """Apply balanced mean reversion filters. Returns rejection dict or None.

        A candidate qualifies if it meets:
          1) At least ONE oversold/stretched condition
          2) At least ONE stabilization hint
          3) Risk sanity checks

        Filter order:
          1. Oversold gate (must meet one)
          2. Stabilization gate (must meet one)
          3. ATR% sanity (not too wild)
          4. Structural damage check (not too far below SMA50)
        """
        rsi14 = metrics.get("rsi14")
        rsi2 = metrics.get("rsi2")
        zscore = metrics.get("zscore_20")
        dist_sma20 = metrics.get("dist_sma20")
        dist_sma50 = metrics.get("dist_sma50")
        atr_pct = metrics.get("atr_pct")
        bounce_hint = metrics.get("bounce_hint", False)
        return_1d = metrics.get("return_1d")
        return_2d = metrics.get("return_2d")

        # 1) Oversold / stretched gate (must meet ONE)
        oversold_met = False
        if rsi14 is not None and rsi14 <= 35:
            oversold_met = True
        if rsi2 is not None and rsi2 <= 10:
            oversold_met = True
        if zscore is not None and zscore <= -1.5:
            oversold_met = True
        if dist_sma20 is not None and dist_sma20 <= -0.05:
            oversold_met = True

        if not oversold_met:
            detail_parts = []
            if rsi14 is not None:
                detail_parts.append(f"RSI14={rsi14:.1f}")
            if zscore is not None:
                detail_parts.append(f"z={zscore:.2f}")
            if dist_sma20 is not None:
                detail_parts.append(f"distSMA20={dist_sma20*100:.1f}%")
            return {"symbol": symbol, "reason_code": "NOT_OVERSOLD",
                    "detail": f"No oversold condition met: {', '.join(detail_parts)}"}

        # 2) Stabilization gate (must meet ONE)
        stab_met = False
        if return_1d is not None and return_1d >= 0:
            stab_met = True
        if return_2d is not None and return_2d >= 0.005:
            stab_met = True
        if bounce_hint:
            stab_met = True  # bounce_hint already checks higher-low + no new lows

        if not stab_met:
            return {"symbol": symbol, "reason_code": "NO_STABILIZATION",
                    "detail": f"Oversold but no bounce signal (1d={return_1d:.3f if return_1d else 'N/A'}, 2d={return_2d:.3f if return_2d else 'N/A'})"}

        # 3) ATR% sanity
        if atr_pct is not None and atr_pct > cfg["atr_pct_max"]:
            return {"symbol": symbol, "reason_code": "TOO_VOLATILE",
                    "detail": f"ATR% {atr_pct*100:.1f}% > {cfg['atr_pct_max']*100:.0f}%"}

        # 4) Structural damage check
        if dist_sma50 is not None and dist_sma50 < cfg["dist_sma50_floor"]:
            return {"symbol": symbol, "reason_code": "STRUCTURAL_DAMAGE",
                    "detail": f"Dist SMA50 {dist_sma50*100:.1f}% < {cfg['dist_sma50_floor']*100:.0f}% floor"}

        return None  # passed all filters

    # ── Scoring ─────────────────────────────────────────────────────────────

    @staticmethod
    def _score(metrics: dict[str, Any]) -> tuple[dict[str, float], float]:
        """Score a candidate. Returns (breakdown_dict, composite).

        Score components (0–100 total):
          - oversold_score       (0–40): RSI14 sweet spot 25-35, zscore bonus, RSI2 bonus
          - stabilization_score  (0–25): positive returns, no new lows, volume on green day
          - room_score           (0–20): distance below SMA20 = snapback potential
          - liquidity_score      (0–15): avg dollar volume, reasonable ATR%
        """

        # ── Oversold score (0–40) ──
        oversold = 0.0
        rsi14 = metrics.get("rsi14")
        rsi2 = metrics.get("rsi2")
        zscore = metrics.get("zscore_20")

        if rsi14 is not None:
            if 25 <= rsi14 <= 30:
                oversold += 22  # core sweet spot — full points
            elif 20 <= rsi14 < 25:
                oversold += 18  # very oversold, but approaching panic
            elif 30 < rsi14 <= 40:
                # Smooth transition from sweet spot (22) to mildly oversold (6)
                oversold += round(22 - (rsi14 - 30) * (22 - 6) / (40 - 30))
            elif 15 <= rsi14 < 20:
                oversold += 12  # deep oversold — knife risk but still scored
            elif rsi14 < 15:
                oversold += 6   # extreme panic — penalized

        # Zscore bonus
        if zscore is not None:
            if -2.5 <= zscore <= -1.5:
                oversold += 10  # good stretch level
            elif -3.0 <= zscore < -2.5:
                oversold += 7   # very stretched — approaching extreme
            elif -1.5 < zscore <= -1.0:
                oversold += 4   # mild stretch
            elif zscore < -3.0:
                oversold += 3   # extreme — diminishing returns

        # RSI2 bonus (short-term oversold signal)
        if rsi2 is not None:
            if rsi2 <= 5:
                oversold += 6   # very short-term crushed
            elif rsi2 <= 10:
                oversold += 4
            elif rsi2 <= 20:
                oversold += 2

        oversold = min(oversold, 40.0)

        # ── Stabilization score (0–25) ──
        stab = 0.0
        r1d = metrics.get("return_1d")
        r2d = metrics.get("return_2d")
        bounce = metrics.get("bounce_hint", False)
        vsr = metrics.get("vol_spike_ratio")

        # Positive 1d return
        if r1d is not None and r1d > 0:
            if r1d >= 0.02:
                stab += 8   # strong green day
            elif r1d >= 0.005:
                stab += 6   # decent bounce
            else:
                stab += 3   # flat-to-green

        # Positive 2d return
        if r2d is not None and r2d >= 0.005:
            stab += 4
        elif r2d is not None and r2d >= 0:
            stab += 2

        # Bounce hint (covers higher-low, no-new-lows checks)
        if bounce:
            stab += 4

        # Volume spike on green day = buyers stepping in
        if vsr is not None and r1d is not None and r1d > 0:
            if vsr >= 2.0:
                stab += 6
            elif vsr >= 1.5:
                stab += 4
            elif vsr >= 1.3:
                stab += 3

        stab = min(stab, 25.0)

        # ── Room score (0–20) — snapback potential ──
        room = 0.0
        dist20 = metrics.get("dist_sma20")
        dist50 = metrics.get("dist_sma50")

        # Distance below SMA20 = more room to revert
        if dist20 is not None:
            if dist20 <= -0.08:
                room += 12  # 8%+ below SMA20 — lots of room
            elif dist20 <= -0.05:
                room += 10  # 5-8% below
            elif dist20 <= -0.03:
                room += 7   # 3-5% below
            elif dist20 <= -0.01:
                room += 3   # slightly below

        # Penalty for too far below SMA50 (structural damage caps room)
        if dist50 is not None:
            if dist50 < -0.15:
                room = max(0, room - 6)
            elif dist50 < -0.10:
                room = max(0, room - 3)

        # Bonus if drawdown from 20D high is moderate (not catastrophic)
        dd20 = metrics.get("drawdown_20")
        if dd20 is not None:
            if -0.12 <= dd20 <= -0.04:
                room += 5  # healthy pullback range
            elif -0.20 <= dd20 < -0.12:
                room += 3  # deeper but still recoverable
            elif dd20 < -0.20:
                room += 0  # crash territory — no bonus

        room = min(room, 20.0)

        # ── Liquidity score (0–15) ──
        liq = 0.0
        adv = metrics.get("avg_dollar_vol_20")
        atr_pct = metrics.get("atr_pct")

        if adv is not None:
            if adv >= 500_000_000:
                liq += 8
            elif adv >= 200_000_000:
                liq += 7
            elif adv >= 100_000_000:
                liq += 5
            elif adv >= 50_000_000:
                liq += 4
            elif adv >= 15_000_000:
                liq += 2

        # ATR% reasonableness bonus
        if atr_pct is not None:
            if atr_pct <= 0.03:
                liq += 5   # low volatility, easier to trade
            elif atr_pct <= 0.05:
                liq += 4
            elif atr_pct <= 0.07:
                liq += 3
            elif atr_pct <= 0.10:
                liq += 1

        liq = min(liq, 15.0)

        breakdown = {
            "oversold": oversold,
            "stabilization": stab,
            "room": room,
            "liquidity": liq,
        }
        composite = oversold + stab + room + liq
        return breakdown, composite

    # ── Thesis bullets ──────────────────────────────────────────────────────

    @staticmethod
    def _build_thesis(metrics: dict[str, Any], scores: dict[str, float], price: float) -> list[str]:
        """Generate 3–6 human-readable thesis bullets."""
        bullets: list[str] = []

        rsi14 = metrics.get("rsi14")
        if rsi14 is not None:
            if rsi14 <= 25:
                bullets.append(f"RSI {rsi14:.0f} (deeply oversold)")
            elif rsi14 <= 35:
                bullets.append(f"RSI {rsi14:.0f} (oversold)")
            elif rsi14 <= 40:
                bullets.append(f"RSI {rsi14:.0f} (approaching oversold)")

        zscore = metrics.get("zscore_20")
        if zscore is not None and zscore <= -1.0:
            bullets.append(f"Z-score {zscore:.1f} vs 20D mean (stretched)")

        dist20 = metrics.get("dist_sma20")
        if dist20 is not None and dist20 <= -0.02:
            bullets.append(f"{abs(dist20 * 100):.1f}% below SMA-20 (snapback room)")

        r1d = metrics.get("return_1d")
        rs = metrics.get("reversion_state")
        if rs == "bounce_starting" and r1d is not None and r1d > 0:
            bullets.append(f"Green day after selloff (+{r1d * 100:.1f}%)")
        elif rs == "bounce_starting":
            bullets.append("Early stabilization signal detected")

        vsr = metrics.get("vol_spike_ratio")
        if vsr is not None and vsr >= 1.3 and r1d is not None and r1d > 0:
            bullets.append(f"Volume {vsr:.1f}× avg on up day (buyers stepping in)")

        dd20 = metrics.get("drawdown_20")
        if dd20 is not None and dd20 <= -0.05:
            bullets.append(f"Down {abs(dd20 * 100):.1f}% from 20D high")

        adv = metrics.get("avg_dollar_vol_20")
        if adv is not None and adv >= 100_000_000:
            bullets.append(f"High liquidity (${adv / 1_000_000:.0f}M avg daily $vol)")

        dp = metrics.get("downtrend_pressure")
        if dp:
            bullets.append("Note: SMA-20 slope negative (downtrend pressure)")

        return bullets[:6]
