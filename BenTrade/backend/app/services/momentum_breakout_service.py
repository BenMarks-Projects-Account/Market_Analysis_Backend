"""
BenTrade — Momentum Breakout Stock Strategy Service
strategy_id: stock_momentum_breakout

End-to-end scanner pipeline:
  1) Universe assembly (balanced: ~150-400 liquid stocks, ETFs excluded)
  2) Per-symbol OHLCV fetch (Tradier primary, async semaphore-limited)
  3) Breakout-specific enrichment metrics
  4) Balanced filters (proximity, trend, RSI, compression, volume spike, extension)
  5) Composite scoring: breakout(0-35) + volume(0-25) + trend(0-20) + base_quality(0-20) = 0-100
  6) Trade shape construction with canonical trade_key

Data source policy:
  - Tradier is authoritative for universe + pricing
  - Fallback to BaseDataService history only if Tradier fails, with confidence downgrade

Scoring formula breakdown:
  breakout_score  (0-35): proximity to 55D high, breakout % through, ATR move quality
  volume_score    (0-25): volume spike ratio, dollar volume liquidity
  trend_score     (0-20): SMA50>SMA200, MA alignment, slope
  base_quality    (0-20): compression + range tightness + gap avoidance
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
from common.quant_analysis import rsi, simple_moving_average

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
    # Real estate (REITs — a few liquid ones)
    "AMT", "PLD", "CCI", "EQIX", "SPG", "O", "PSA",
    # Utilities (a handful)
    "NEE", "DUK", "SO", "AEP", "D",
]


# ── Balanced config ─────────────────────────────────────────────────────────
_BALANCED_CONFIG: dict[str, Any] = {
    "min_history_bars": 220,           # need SMA-200 + lookback stability
    "min_price": 7.0,                  # slightly higher floor than pullback ($7 vs $5)
    "min_avg_dollar_vol": 20_000_000,  # $20M avg daily dollar volume (higher bar)
    "lookback_days": 400,              # calendar days → ~280 trading days
    "concurrency": 8,
    "per_symbol_timeout": 12.0,        # seconds
    # Breakout filter thresholds
    "proximity_55d_pct": 0.03,         # within 3% of 55D high to qualify
    "breakout_min_pct": 0.003,         # 0.3% through high = breakout
    "breakout_max_pct": 0.03,          # >3% through = already extended (but still considered if vol OK)
    "trend_required": True,            # SMA50 > SMA200
    "rsi_min": 55,                     # minimum RSI for breakout (not oversold)
    "rsi_max": 78,                     # max RSI (not blow-off)
    "vol_spike_min": 1.2,              # today volume / avg20 >= 1.2x
    "extension_max_pct": 0.08,         # max distance above SMA20 (8%) — avoid chasing
    "compression_max": 0.15,           # 20D range / price <= 15% → tight base
}


# ────────────────────────────────────────────────────────────────────────────
# Service
# ────────────────────────────────────────────────────────────────────────────

class MomentumBreakoutService:
    """Momentum Breakout stock strategy scanner."""

    STRATEGY_ID = "stock_momentum_breakout"

    def __init__(self, base_data_service: BaseDataService) -> None:
        self.bds = base_data_service

    # ── Public entry point ──────────────────────────────────────────────────

    async def scan(self, *, max_candidates: int = 30) -> dict[str, Any]:
        """Run the full momentum breakout scan and return the payload."""
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
        """Fetch data, compute metrics, apply filters, score, and build trade shape."""

        # -- Fetch OHLCV bars from Tradier --
        data_source = "tradier"
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=cfg["lookback_days"])

        bars: list[dict[str, Any]] = []
        try:
            bars = await self.bds.tradier_client.get_daily_bars(
                symbol,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
        except Exception as exc:
            logger.warning("event=breakout_bars_fail symbol=%s error=%s", symbol, exc)

        # Fallback to BaseDataService history (close-only)
        if not bars:
            data_source = "fallback"
            try:
                closes = await self.bds.get_prices_history(symbol, lookback_days=cfg["lookback_days"])
                if closes:
                    bars = [{"date": None, "open": None, "high": None, "low": None, "close": c, "volume": None} for c in closes]
            except Exception as exc:
                logger.warning("event=breakout_fallback_fail symbol=%s error=%s", symbol, exc)

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

        # -- Compute breakout metrics --
        metrics = self._compute_metrics(closes, highs, lows, opens, volumes, price)

        # -- Apply breakout filters --
        reject = self._apply_filters(symbol, metrics, cfg)
        if reject:
            rejections.append(reject)
            return None

        # -- Score --
        score_breakdown, composite_score = self._score(metrics)

        # -- Thesis bullets --
        thesis = self._build_thesis(metrics, score_breakdown, price)

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
            "breakout_score": round(score_breakdown.get("breakout", 0), 1),
            "volume_score": round(score_breakdown.get("volume", 0), 1),
            "trend_score": round(score_breakdown.get("trend", 0), 1),
            "base_quality_score": round(score_breakdown.get("base_quality", 0), 1),
            "metrics": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in metrics.items()},
            "thesis": thesis,
            "breakout_state": metrics.get("breakout_state", "unknown"),
            "risk_notes": [],
            "as_of": datetime.now(timezone.utc).isoformat(),
            "data_source": {
                "history": data_source,
                "confidence": 1.0 if data_source == "tradier" else 0.7,
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
        """Compute all momentum-breakout enrichment metrics.

        Derived fields & formulas:
          high_20:     max(highs[-20:])
          high_55:     max(highs[-55:])
          high_252:    max(highs[-252:])  (52-week high)
          breakout_proximity_55: (price - high_55) / high_55
          atr14:       14-period average true range
          atr_pct:     atr14 / price
          range_20_pct: (max(highs[-20:]) - min(lows[-20:])) / price
          compression_score: 1 - (range_20_pct / range_55_pct) if range_55_pct > 0
          vol_spike_ratio: volumes[-1] / avg(volumes[-20:])
          roc_10:      (close[-1] - close[-11]) / close[-11]
          roc_20:      (close[-1] - close[-21]) / close[-21]
          gap_pct:     (open[-1] - close[-2]) / close[-2]
          dist_sma20:  (price - sma20) / sma20
          dist_sma50:  (price - sma50) / sma50
        """
        m: dict[str, Any] = {}

        # A) Multi-timeframe highs
        # Derived field: high_20
        # Inputs: highs[-20:]
        # Formula: max(highs[-20:])
        m["high_20"] = max(highs[-20:]) if len(highs) >= 20 else max(highs) if highs else price

        # Derived field: high_55
        # Inputs: highs[-55:]
        # Formula: max(highs[-55:])
        m["high_55"] = max(highs[-55:]) if len(highs) >= 55 else max(highs) if highs else price

        # Derived field: high_252
        # Inputs: highs[-252:]
        # Formula: max(highs[-252:])  (52-week high)
        m["high_252"] = max(highs[-252:]) if len(highs) >= 252 else max(highs) if highs else price

        # Derived field: low_252
        # Inputs: lows[-252:]
        # Formula: min(lows[-252:])  (52-week low)
        m["low_252"] = min(lows[-252:]) if len(lows) >= 252 else min(lows) if lows else price

        # B) Breakout proximity
        # Derived field: breakout_proximity_55
        # Inputs: price, high_55
        # Formula: (price - high_55) / high_55
        # Interpretation: 0 = at 55D high; >0 = broke through; <0 = below
        high_55 = m["high_55"]
        m["breakout_proximity_55"] = (price - high_55) / high_55 if high_55 > 0 else 0.0

        # Derived field: breakout_proximity_20
        # Inputs: price, high_20
        # Formula: (price - high_20) / high_20
        high_20 = m["high_20"]
        m["breakout_proximity_20"] = (price - high_20) / high_20 if high_20 > 0 else 0.0

        # Derived field: pct_from_52w_high
        # Inputs: price, high_252
        # Formula: (price - high_252) / high_252
        high_252 = m["high_252"]
        m["pct_from_52w_high"] = (price - high_252) / high_252 if high_252 > 0 else 0.0

        # C) ATR-14 (Average True Range)
        # Derived field: atr14
        # Inputs: highs, lows, closes over last 14 bars
        # Formula: avg of max(high-low, |high-prev_close|, |low-prev_close|) over 14 periods
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

        # D) Range & compression
        # Derived field: range_20_pct
        # Inputs: highs[-20:], lows[-20:], price
        # Formula: (max(highs[-20:]) - min(lows[-20:])) / price
        if len(highs) >= 20 and len(lows) >= 20:
            range_20 = max(highs[-20:]) - min(lows[-20:])
            m["range_20_pct"] = range_20 / price if price > 0 else None
        else:
            range_20 = None
            m["range_20_pct"] = None

        # Derived field: range_55_pct
        # Inputs: highs[-55:], lows[-55:], price
        # Formula: (max(highs[-55:]) - min(lows[-55:])) / price
        if len(highs) >= 55 and len(lows) >= 55:
            range_55 = max(highs[-55:]) - min(lows[-55:])
            m["range_55_pct"] = range_55 / price if price > 0 else None
        else:
            range_55 = None
            m["range_55_pct"] = None

        # Derived field: compression_score
        # Inputs: range_20_pct, range_55_pct
        # Formula: 1 - (range_20_pct / range_55_pct)
        # Interpretation: higher = tighter recent range relative to longer-term → base compression
        r20 = m["range_20_pct"]
        r55 = m["range_55_pct"]
        if r20 is not None and r55 is not None and r55 > 0:
            m["compression_score"] = max(0.0, 1.0 - (r20 / r55))
        else:
            m["compression_score"] = None

        # E) Volume metrics
        # Derived field: vol_spike_ratio
        # Inputs: volumes[-1], avg(volumes[-20:])
        # Formula: volumes[-1] / avg(volumes[-20:])
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

        # F) RSI-14
        rsi14 = rsi(closes, 14)
        m["rsi14"] = rsi14

        # G) Moving averages
        sma20 = simple_moving_average(closes, 20)
        sma50 = simple_moving_average(closes, 50)
        sma200 = simple_moving_average(closes, 200)
        m["sma20"] = sma20
        m["sma50"] = sma50
        m["sma200"] = sma200

        # Derived field: dist_sma20
        # Inputs: price, sma20
        # Formula: (price - sma20) / sma20
        m["dist_sma20"] = (price - sma20) / sma20 if (sma20 is not None and sma20 > 0) else None

        # Derived field: dist_sma50
        # Inputs: price, sma50
        # Formula: (price - sma50) / sma50
        m["dist_sma50"] = (price - sma50) / sma50 if (sma50 is not None and sma50 > 0) else None

        # Slopes
        if len(closes) > 30:
            sma20_lag = simple_moving_average(closes[:-10], 20)
            m["slope_20"] = ((sma20 - sma20_lag) / price) if (sma20 is not None and sma20_lag is not None and price > 0) else None
        else:
            m["slope_20"] = None

        if len(closes) > 60:
            sma50_lag = simple_moving_average(closes[:-10], 50)
            m["slope_50"] = ((sma50 - sma50_lag) / price) if (sma50 is not None and sma50_lag is not None and price > 0) else None
        else:
            m["slope_50"] = None

        # H) Rate of change
        # Derived field: roc_10
        # Inputs: closes[-1], closes[-11]
        # Formula: (closes[-1] - closes[-11]) / closes[-11]
        m["roc_10"] = (closes[-1] - closes[-11]) / closes[-11] if len(closes) >= 11 and closes[-11] > 0 else None

        # Derived field: roc_20
        # Inputs: closes[-1], closes[-21]
        # Formula: (closes[-1] - closes[-21]) / closes[-21]
        m["roc_20"] = (closes[-1] - closes[-21]) / closes[-21] if len(closes) >= 21 and closes[-21] > 0 else None

        # I) Gap detection
        # Derived field: gap_pct
        # Inputs: opens[-1], closes[-2]
        # Formula: (opens[-1] - closes[-2]) / closes[-2]
        if opens and len(opens) >= 1 and len(closes) >= 2 and closes[-2] > 0:
            m["gap_pct"] = (opens[-1] - closes[-2]) / closes[-2]
        else:
            m["gap_pct"] = None

        # J) Trend state classification
        # Derived field: trend_state
        # Inputs: price, sma50, sma200, slope_50
        # Formula:
        #   "strong_uptrend" if sma20 > sma50 > sma200 AND slope_50 > 0
        #   "uptrend" if sma50 > sma200 AND slope_50 > 0
        #   else "not_uptrend"
        trend_state = "not_uptrend"
        if sma20 is not None and sma50 is not None and sma200 is not None:
            slope_50 = m.get("slope_50")
            slope_ok = slope_50 is not None and slope_50 > 0
            if sma20 > sma50 > sma200 and slope_ok:
                trend_state = "strong_uptrend"
            elif sma50 > sma200 and slope_ok:
                trend_state = "uptrend"
        m["trend_state"] = trend_state

        # K) Breakout state classification
        # Derived field: breakout_state
        # Inputs: breakout_proximity_55, vol_spike_ratio
        # Formula:
        #   "breakout_confirmed" if proximity >= 0 AND vol_spike >= 1.5
        #   "breakout_attempt"   if proximity >= -0.01 AND vol_spike >= 1.0
        #   "near_breakout"      if proximity >= -0.03
        #   else "below_range"
        prox55 = m["breakout_proximity_55"]
        vsr = m.get("vol_spike_ratio")
        if prox55 >= 0 and vsr is not None and vsr >= 1.5:
            m["breakout_state"] = "breakout_confirmed"
        elif prox55 >= -0.01 and vsr is not None and vsr >= 1.0:
            m["breakout_state"] = "breakout_attempt"
        elif prox55 >= -0.03:
            m["breakout_state"] = "near_breakout"
        else:
            m["breakout_state"] = "below_range"

        return m

    # ── Filters ─────────────────────────────────────────────────────────────

    @staticmethod
    def _apply_filters(
        symbol: str,
        metrics: dict[str, Any],
        cfg: dict[str, Any],
    ) -> dict[str, str] | None:
        """Apply balanced breakout filters. Returns rejection dict or None if passed.

        Filter order:
          1. Proximity to 55D high (within cfg.proximity_55d_pct or already broke out)
          2. Trend check (SMA50 > SMA200 if required)
          3. RSI range (not oversold, not blow-off)
          4. Compression / base quality (20D range not too wide)
          5. Volume spike (today's vol vs avg)
          6. Extension limit (not too far above SMA20)
        """
        prox55 = metrics.get("breakout_proximity_55")
        rsi14 = metrics.get("rsi14")
        dist_sma20 = metrics.get("dist_sma20")
        vol_spike = metrics.get("vol_spike_ratio")
        range_20 = metrics.get("range_20_pct")
        sma50 = metrics.get("sma50")
        sma200 = metrics.get("sma200")

        # 1) Proximity: must be within 3% of 55D high (or above)
        if prox55 is not None and prox55 < -cfg["proximity_55d_pct"]:
            return {"symbol": symbol, "reason_code": "TOO_FAR_FROM_HIGH",
                    "detail": f"Proximity to 55D high: {prox55*100:.1f}% < -{cfg['proximity_55d_pct']*100:.0f}%"}

        # 2) Trend: SMA50 > SMA200
        if cfg.get("trend_required"):
            if sma50 is not None and sma200 is not None and sma50 <= sma200:
                return {"symbol": symbol, "reason_code": "TREND_FAILED",
                        "detail": f"SMA50 ${sma50:.2f} <= SMA200 ${sma200:.2f}"}

        # 3) RSI range
        if rsi14 is not None:
            if rsi14 < cfg["rsi_min"]:
                return {"symbol": symbol, "reason_code": "RSI_TOO_LOW",
                        "detail": f"RSI {rsi14:.1f} < {cfg['rsi_min']}"}
            if rsi14 > cfg["rsi_max"]:
                return {"symbol": symbol, "reason_code": "RSI_TOO_HIGH",
                        "detail": f"RSI {rsi14:.1f} > {cfg['rsi_max']}"}

        # 4) Base compression: 20D range not excessively wide
        if range_20 is not None and range_20 > cfg["compression_max"]:
            return {"symbol": symbol, "reason_code": "RANGE_TOO_WIDE",
                    "detail": f"20D range {range_20*100:.1f}% > {cfg['compression_max']*100:.0f}%"}

        # 5) Volume spike: today >= threshold * avg
        if vol_spike is not None and vol_spike < cfg["vol_spike_min"]:
            return {"symbol": symbol, "reason_code": "VOLUME_INSUFFICIENT",
                    "detail": f"Vol spike {vol_spike:.2f}x < {cfg['vol_spike_min']:.1f}x"}

        # 6) Extension: not too far above SMA20
        if dist_sma20 is not None and dist_sma20 > cfg["extension_max_pct"]:
            return {"symbol": symbol, "reason_code": "TOO_EXTENDED",
                    "detail": f"Dist above SMA20 {dist_sma20*100:.1f}% > {cfg['extension_max_pct']*100:.0f}%"}

        return None  # passed all filters

    # ── Scoring ─────────────────────────────────────────────────────────────

    @staticmethod
    def _score(metrics: dict[str, Any]) -> tuple[dict[str, float], float]:
        """Score a candidate. Returns (breakdown_dict, composite).

        Score components (0–100 total):
          - breakout_score   (0–35): proximity to 55D high, breakout %, ATR quality
          - volume_score     (0–25): vol spike ratio, dollar volume
          - trend_score      (0–20): trend_state, MA alignment, slope
          - base_quality     (0–20): compression, range tightness, gap avoidance

        Each sub-score is capped at its maximum.
        """

        # ── Breakout score (0–35) ──
        breakout = 0.0
        prox55 = metrics.get("breakout_proximity_55")
        prox20 = metrics.get("breakout_proximity_20")
        atr_pct = metrics.get("atr_pct")
        bs = metrics.get("breakout_state", "below_range")

        # Breakout state bonus
        if bs == "breakout_confirmed":
            breakout += 18
        elif bs == "breakout_attempt":
            breakout += 12
        elif bs == "near_breakout":
            breakout += 6

        # Proximity to 55D high: closer = better (above = bonus)
        if prox55 is not None:
            if prox55 >= 0:
                # Already through: bonus for 0-3% through, diminishing beyond
                pct_through = min(prox55, 0.05)
                breakout += 6 + (pct_through / 0.05) * 4  # 6-10 pts
            elif prox55 >= -0.01:
                breakout += 5  # within 1%
            elif prox55 >= -0.02:
                breakout += 3  # within 2%

        # ATR quality: moderate ATR is better for breakout (not too low = no vol, not too high = erratic)
        if atr_pct is not None:
            if 0.015 <= atr_pct <= 0.035:
                breakout += 5  # ideal ATR range
            elif 0.01 <= atr_pct < 0.015 or 0.035 < atr_pct <= 0.05:
                breakout += 3  # acceptable
            elif atr_pct > 0.05:
                breakout += 1  # too volatile

        breakout = min(breakout, 35.0)

        # ── Volume score (0–25) ──
        volume = 0.0
        vsr = metrics.get("vol_spike_ratio")
        adv = metrics.get("avg_dollar_vol_20")

        # Volume spike
        if vsr is not None:
            if vsr >= 3.0:
                volume += 15
            elif vsr >= 2.0:
                volume += 12
            elif vsr >= 1.5:
                volume += 9
            elif vsr >= 1.2:
                volume += 5
            elif vsr >= 1.0:
                volume += 2

        # Dollar volume liquidity
        if adv is not None:
            if adv >= 500_000_000:
                volume += 10
            elif adv >= 200_000_000:
                volume += 8
            elif adv >= 100_000_000:
                volume += 6
            elif adv >= 50_000_000:
                volume += 4
            elif adv >= 20_000_000:
                volume += 2

        volume = min(volume, 25.0)

        # ── Trend score (0–20) ──
        trend = 0.0
        ts = metrics.get("trend_state", "not_uptrend")

        if ts == "strong_uptrend":
            trend += 12
        elif ts == "uptrend":
            trend += 8

        # MA alignment
        sma20 = metrics.get("sma20")
        sma50 = metrics.get("sma50")
        sma200 = metrics.get("sma200")
        if sma20 is not None and sma50 is not None and sma20 > sma50:
            trend += 3
        if sma50 is not None and sma200 is not None and sma50 > sma200:
            trend += 2

        # Slope
        slope_50 = metrics.get("slope_50")
        if slope_50 is not None:
            if slope_50 > 0.01:
                trend += 3
            elif slope_50 > 0:
                trend += 1

        trend = min(trend, 20.0)

        # ── Base quality score (0–20) ──
        base = 0.0
        comp = metrics.get("compression_score")
        range_20 = metrics.get("range_20_pct")
        gap = metrics.get("gap_pct")

        # Compression (tight base relative to longer-term range)
        if comp is not None:
            if comp >= 0.6:
                base += 10
            elif comp >= 0.4:
                base += 7
            elif comp >= 0.2:
                base += 4
            else:
                base += 1

        # Tight 20D range bonus
        if range_20 is not None:
            if range_20 <= 0.06:
                base += 6  # very tight
            elif range_20 <= 0.10:
                base += 4
            elif range_20 <= 0.15:
                base += 2

        # Gap penalty: large gap-ups reduce quality (may be unsustainable)
        if gap is not None:
            if abs(gap) > 0.05:
                base = max(0, base - 3)  # large gap penalty
            elif abs(gap) > 0.03:
                base = max(0, base - 1)

        base = min(base, 20.0)

        breakdown = {
            "breakout": breakout,
            "volume": volume,
            "trend": trend,
            "base_quality": base,
        }
        composite = breakout + volume + trend + base
        return breakdown, composite

    # ── Thesis bullets ──────────────────────────────────────────────────────

    @staticmethod
    def _build_thesis(metrics: dict[str, Any], scores: dict[str, float], price: float) -> list[str]:
        """Generate 3–5 human-readable thesis bullets."""
        bullets: list[str] = []

        bs = metrics.get("breakout_state", "below_range")
        prox55 = metrics.get("breakout_proximity_55")
        if bs == "breakout_confirmed":
            bullets.append(f"Confirmed breakout above 55D high on strong volume")
        elif bs == "breakout_attempt":
            bullets.append(f"Breakout attempt near 55D high — watching for confirmation")
        elif bs == "near_breakout" and prox55 is not None:
            bullets.append(f"Within {abs(prox55 * 100):.1f}% of 55D high — potential breakout setup")

        vsr = metrics.get("vol_spike_ratio")
        if vsr is not None and vsr >= 1.5:
            bullets.append(f"Volume surge {vsr:.1f}x average — institutional interest")
        elif vsr is not None and vsr >= 1.2:
            bullets.append(f"Volume uptick {vsr:.1f}x average")

        ts = metrics.get("trend_state", "not_uptrend")
        if ts == "strong_uptrend":
            bullets.append("Strong uptrend: SMA-20 > SMA-50 > SMA-200 with rising slope")
        elif ts == "uptrend":
            bullets.append("Uptrend: SMA-50 above SMA-200 and rising")

        comp = metrics.get("compression_score")
        if comp is not None and comp >= 0.4:
            bullets.append(f"Tight base (compression {comp:.0%}) — coiled for expansion")

        roc_20 = metrics.get("roc_20")
        if roc_20 is not None and roc_20 > 0.05:
            bullets.append(f"Strong 20D momentum: +{roc_20*100:.1f}%")

        adv = metrics.get("avg_dollar_vol_20")
        if adv is not None and adv >= 100_000_000:
            bullets.append(f"High liquidity (${adv / 1_000_000:.0f}M avg daily $vol)")

        rsi14 = metrics.get("rsi14")
        if rsi14 is not None and 60 <= rsi14 <= 70:
            bullets.append(f"RSI {rsi14:.0f} — healthy momentum without overextension")

        return bullets[:5]
