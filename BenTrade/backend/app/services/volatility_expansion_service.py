"""
BenTrade — Volatility Expansion Stock Strategy Service
strategy_id: stock_volatility_expansion

End-to-end scanner pipeline:
  1) Universe assembly (balanced: ~150-400 liquid stocks, ETFs excluded)
  2) Per-symbol OHLCV fetch (Tradier primary, async semaphore-limited)
  3) Volatility-expansion-specific enrichment metrics
  4) Balanced filters: expansion FROM compression, long bias, risk sanity
  5) Composite scoring: expansion(0-40) + compression(0-25) + confirmation(0-20)
     + risk(0-15) = 0-100
  6) Trade shape construction with canonical trade_key

Data source policy:
  - Tradier is authoritative for universe + pricing
  - Fallback to BaseDataService history only if Tradier fails,
    with confidence downgrade

Scoring formula breakdown:
  expansion_score    (0-40): ATR ratio, RV ratio, range ratio
  compression_score  (0-25): prior low volatility/range, BB width percentile
  confirmation_score (0-20): volume spike, direction confirmation
  risk_score         (0-15): ATR% reasonableness, liquidity, gap penalty

TODO:
  - 4-level gate system (future phase)
  - Presets framework (Strict / Balanced / Wide)
  - IV-based expansion overlay when option chain IV available
  - SPY market context / regime overlay
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base_data_service import BaseDataService
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
    "min_history_bars": 120,
    "min_price": 7.0,
    "min_avg_dollar_vol": 20_000_000,   # $20M avg daily dollar volume
    "lookback_days": 280,               # calendar days → ~190 trading days
    "concurrency": 8,
    "per_symbol_timeout": 12.0,
    # Filter thresholds
    "atr_pct_max": 0.12,                # max ATR% for risk sanity
    # Expansion thresholds (meet ONE)
    "atr_ratio_min": 1.25,
    "rv_ratio_min": 1.25,
    "range_ratio_min": 1.35,
    # Compression thresholds (meet ONE)
    "bb_width_percentile_max": 35,
    "prior_range_20_max": 0.14,
    "prior_atr_pct_max": 0.045,
}


# ────────────────────────────────────────────────────────────────────────────
# Service
# ────────────────────────────────────────────────────────────────────────────

class VolatilityExpansionService:
    """Volatility Expansion stock strategy scanner."""

    STRATEGY_ID = "stock_volatility_expansion"

    def __init__(self, base_data_service: BaseDataService) -> None:
        self.bds = base_data_service

    # ── Public entry point ──────────────────────────────────────────────────

    async def scan(self, *, max_candidates: int = 30) -> dict[str, Any]:
        """Run the full volatility expansion scan and return the payload."""
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
                    rejections.append({"symbol": sym, "reason_code": "TIMEOUT",
                                       "detail": f">{cfg['per_symbol_timeout']}s"})
                    return None
                except Exception as exc:
                    rejections.append({"symbol": sym, "reason_code": "ERROR",
                                       "detail": str(exc)[:120]})
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
            "candidates_count": len(candidates),
            "rejections": rejections,
            "rejections_count": len(rejections),
            "notes": notes,
            "source_health": source_health,
            "scan_time_seconds": round(elapsed, 2),
            "debug": {
                "universe_count": universe_count,
                "scanned_count": universe_count,
                "rejected_count": len(rejections),
                "candidate_count": len(candidates),
            },
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
            logger.warning("event=vol_exp_bars_fail symbol=%s error=%s", symbol, exc)

        # Fallback to BaseDataService history (close-only)
        if not bars:
            data_source = "fallback"
            try:
                fb_closes = await self.bds.get_prices_history(
                    symbol, lookback_days=cfg["lookback_days"],
                )
                if fb_closes:
                    bars = [
                        {"date": None, "open": None, "high": None,
                         "low": None, "close": c, "volume": None}
                        for c in fb_closes
                    ]
            except Exception as exc:
                logger.warning("event=vol_exp_fallback_fail symbol=%s error=%s",
                               symbol, exc)

        if not bars:
            rejections.append({"symbol": symbol, "reason_code": "NO_DATA",
                               "detail": "No price history available"})
            return None

        # -- Extract series --
        closes = [b["close"] for b in bars if b.get("close") is not None]
        volumes = [b["volume"] for b in bars if b.get("volume") is not None]
        highs = [b["high"] for b in bars if b.get("high") is not None]
        lows = [b["low"] for b in bars if b.get("low") is not None]
        opens = [b["open"] for b in bars if b.get("open") is not None]

        # -- Hard requirements --
        if len(closes) < cfg["min_history_bars"]:
            rejections.append({"symbol": symbol, "reason_code": "INSUFFICIENT_HISTORY",
                               "detail": f"{len(closes)} bars < {cfg['min_history_bars']}"})
            return None

        price = closes[-1]
        if price < cfg["min_price"]:
            rejections.append({"symbol": symbol, "reason_code": "PRICE_TOO_LOW",
                               "detail": f"${price:.2f} < ${cfg['min_price']}"})
            return None

        # Volume check
        avg_vol_20 = (sum(volumes[-20:]) / min(len(volumes[-20:]), 20)
                      if len(volumes) >= 20 else None)
        avg_dollar_vol_20 = (avg_vol_20 * price) if avg_vol_20 is not None else None

        if avg_dollar_vol_20 is not None and avg_dollar_vol_20 < cfg["min_avg_dollar_vol"]:
            rejections.append({"symbol": symbol, "reason_code": "LOW_LIQUIDITY",
                               "detail": f"${avg_dollar_vol_20:,.0f} < ${cfg['min_avg_dollar_vol']:,}"})
            return None

        # -- Compute volatility expansion metrics --
        metrics = self._compute_metrics(closes, highs, lows, opens, volumes, price)

        # -- Apply filters --
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
        if atr_pct is not None and atr_pct > 0.08:
            confidence = max(0.5, confidence - 0.1)
        # Penalize if volume spike absent
        vsr = metrics.get("vol_spike_ratio")
        if vsr is not None and vsr < 1.2:
            confidence = max(0.5, confidence - 0.05)

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
            "expansion_score": round(score_breakdown.get("expansion", 0), 1),
            "compression_score": round(score_breakdown.get("compression", 0), 1),
            "confirmation_score": round(score_breakdown.get("confirmation", 0), 1),
            "risk_score": round(score_breakdown.get("risk", 0), 1),
            "metrics": {k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in metrics.items()},
            "thesis": thesis,
            "expansion_state": metrics.get("expansion_state", "unknown"),
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
        """Compute all volatility-expansion enrichment metrics.

        Derived fields & formulas documented inline per copilot-instructions.
        """
        m: dict[str, Any] = {}

        # ── A) ATR / True Range regime ──────────────────────────────

        def _atr(h: list[float], l: list[float], c: list[float],
                 period: int = 14) -> float | None:
            """Average True Range over *period* bars ending at the series tail.
            Inputs: highs, lows, closes (need period+1 bars)
            Formula: mean of max(H-L, |H-prevC|, |L-prevC|) over *period*.
            """
            if len(h) < period + 1 or len(l) < period + 1 or len(c) < period + 1:
                return None
            trs: list[float] = []
            for i in range(-period, 0):
                tr = max(h[i] - l[i],
                         abs(h[i] - c[i - 1]),
                         abs(l[i] - c[i - 1]))
                trs.append(tr)
            return sum(trs) / len(trs)

        # Derived field: atr_14
        # Inputs: highs, lows, closes (last 15 bars)
        # Formula: see _atr helper
        atr_14 = _atr(highs, lows, closes, 14)
        m["atr_14"] = atr_14

        # Derived field: atr_14_prev
        # Inputs: highs[:-10], lows[:-10], closes[:-10]
        # Formula: ATR-14 ending 10 bars ago
        atr_14_prev = _atr(highs[:-10], lows[:-10], closes[:-10], 14) \
            if len(closes) >= 25 else None
        m["atr_14_prev"] = atr_14_prev

        # Derived field: atr_pct
        # Inputs: atr_14, price
        # Formula: atr_14 / price
        m["atr_pct"] = (atr_14 / price) if (atr_14 is not None and price > 0) else None

        # Derived field: prior_atr_pct
        # Inputs: atr_14_prev, closes[-11] (price 10 bars ago)
        # Formula: atr_14_prev / closes[-11]
        if atr_14_prev is not None and len(closes) >= 11 and closes[-11] > 0:
            m["prior_atr_pct"] = atr_14_prev / closes[-11]
        else:
            m["prior_atr_pct"] = None

        # Derived field: atr_ratio_10
        # Inputs: atr_14, atr_14_prev
        # Formula: atr_14 / atr_14_prev
        if atr_14 is not None and atr_14_prev is not None and atr_14_prev > 0:
            m["atr_ratio_10"] = atr_14 / atr_14_prev
        else:
            m["atr_ratio_10"] = None

        # ── B) Realized volatility expansion ────────────────────────

        # Derived field: rv_20
        # Inputs: closes[-21:] (21 prices for 20 log returns)
        # Formula: annualized stddev of log returns
        if len(closes) >= 21:
            m["rv_20"] = realized_vol_annualized(closes[-21:], trading_days=252)
        else:
            m["rv_20"] = None

        # Derived field: rv_20_prev
        # Inputs: closes[-31:-10] (21 prices ending 10d ago)
        # Formula: annualized stddev of log returns
        if len(closes) >= 31:
            m["rv_20_prev"] = realized_vol_annualized(closes[-31:-10], trading_days=252)
        else:
            m["rv_20_prev"] = None

        # Derived field: rv_ratio
        # Inputs: rv_20, rv_20_prev
        # Formula: rv_20 / rv_20_prev
        rv = m["rv_20"]
        rvp = m["rv_20_prev"]
        if rv is not None and rvp is not None and rvp > 0:
            m["rv_ratio"] = rv / rvp
        else:
            m["rv_ratio"] = None

        # ── C) Range expansion / breakout ───────────────────────────

        # Derived field: range_10_pct
        # Inputs: highs[-10:], lows[-10:], price
        # Formula: (max(highs[-10:]) - min(lows[-10:])) / price
        if len(highs) >= 10 and len(lows) >= 10:
            m["range_10_pct"] = (max(highs[-10:]) - min(lows[-10:])) / price if price > 0 else None
        else:
            m["range_10_pct"] = None

        # Derived field: range_20_pct
        # Inputs: highs[-20:], lows[-20:], price
        # Formula: (max(highs[-20:]) - min(lows[-20:])) / price
        if len(highs) >= 20 and len(lows) >= 20:
            m["range_20_pct"] = (max(highs[-20:]) - min(lows[-20:])) / price if price > 0 else None
        else:
            m["range_20_pct"] = None

        # Derived field: prior_range_20_pct
        # Inputs: highs[-30:-10], lows[-30:-10], closes[-11]
        # Formula: (max(highs[-30:-10]) - min(lows[-30:-10])) / closes[-11]
        if len(highs) >= 30 and len(lows) >= 30 and len(closes) >= 11:
            ref = closes[-11]
            if ref > 0:
                m["prior_range_20_pct"] = (max(highs[-30:-10]) - min(lows[-30:-10])) / ref
            else:
                m["prior_range_20_pct"] = None
        else:
            m["prior_range_20_pct"] = None

        # Derived field: range_ratio
        # Inputs: range_20_pct, prior_range_20_pct
        # Formula: range_20_pct / prior_range_20_pct
        r20 = m["range_20_pct"]
        pr20 = m["prior_range_20_pct"]
        if r20 is not None and pr20 is not None and pr20 > 0:
            m["range_ratio"] = r20 / pr20
        else:
            m["range_ratio"] = None

        # ── D) Compression indicator (Bollinger width) ──────────────

        # Derived field: bb_width_20
        # Inputs: closes[-20:], sma20
        # Formula: (upperBB - lowerBB) / SMA20 = 4 * stddev / SMA20
        sma20 = simple_moving_average(closes, 20)
        sma50 = simple_moving_average(closes, 50)
        m["sma20"] = sma20
        m["sma50"] = sma50

        bb_width_20: float | None = None
        if sma20 is not None and len(closes) >= 20 and sma20 > 0:
            window = closes[-20:]
            mean_w = sum(window) / len(window)
            var_w = sum((x - mean_w) ** 2 for x in window) / len(window)
            std_w = math.sqrt(var_w)
            bb_width_20 = (4.0 * std_w) / sma20
        m["bb_width_20"] = bb_width_20

        # Derived field: bb_width_prev
        # Inputs: closes[:-10], SMA20 of closes[:-10]
        # Formula: same as bb_width_20 but ending 10 bars ago
        bb_width_prev: float | None = None
        if len(closes) >= 30:
            sma20_prev = simple_moving_average(closes[:-10], 20)
            if sma20_prev is not None and sma20_prev > 0:
                w2 = closes[-30:-10]
                m2 = sum(w2) / len(w2)
                v2 = sum((x - m2) ** 2 for x in w2) / len(w2)
                s2 = math.sqrt(v2)
                bb_width_prev = (4.0 * s2) / sma20_prev
        m["bb_width_prev"] = bb_width_prev

        # Derived field: bb_width_rising
        # Inputs: bb_width_20, bb_width_prev
        # Formula: bb_width_20 > bb_width_prev
        if bb_width_20 is not None and bb_width_prev is not None:
            m["bb_width_rising"] = bb_width_20 > bb_width_prev
        else:
            m["bb_width_rising"] = None

        # Derived field: bb_width_percentile_180
        # Inputs: rolling BB widths over last 180 bars
        # Formula: percentile rank of current bb_width_20 among last 180 values
        bb_pctile: float | None = None
        if len(closes) >= 40 and bb_width_20 is not None:
            # Compute rolling bb_widths for available history up to 180
            lookback = min(len(closes) - 20, 180)
            widths: list[float] = []
            for offset in range(lookback):
                idx_end = len(closes) - offset
                if idx_end < 20:
                    break
                seg = closes[idx_end - 20: idx_end]
                s_mean = sum(seg) / len(seg)
                if s_mean <= 0:
                    continue
                s_var = sum((x - s_mean) ** 2 for x in seg) / len(seg)
                s_std = math.sqrt(s_var)
                widths.append((4.0 * s_std) / s_mean)
            if widths:
                below = sum(1 for w in widths if w < bb_width_20)
                bb_pctile = (below / len(widths)) * 100.0
        m["bb_width_percentile_180"] = bb_pctile

        # ── E) Direction bias (long-only) ───────────────────────────

        # Derived field: return_1d
        # Inputs: closes[-1], closes[-2]
        # Formula: (closes[-1] - closes[-2]) / closes[-2]
        m["return_1d"] = ((closes[-1] - closes[-2]) / closes[-2]
                          if len(closes) >= 2 and closes[-2] > 0 else None)

        # Derived field: return_2d
        # Inputs: closes[-1], closes[-3]
        # Formula: (closes[-1] - closes[-3]) / closes[-3]
        m["return_2d"] = ((closes[-1] - closes[-3]) / closes[-3]
                          if len(closes) >= 3 and closes[-3] > 0 else None)

        # Derived field: return_5d
        # Inputs: closes[-1], closes[-6]
        # Formula: (closes[-1] - closes[-6]) / closes[-6]
        m["return_5d"] = ((closes[-1] - closes[-6]) / closes[-6]
                          if len(closes) >= 6 and closes[-6] > 0 else None)

        # Derived field: close_vs_sma20
        # Inputs: price, sma20
        # Formula: (price - sma20) / sma20
        m["close_vs_sma20"] = ((price - sma20) / sma20
                               if sma20 is not None and sma20 > 0 else None)

        # Derived field: close_vs_sma50
        # Inputs: price, sma50
        # Formula: (price - sma50) / sma50
        m["close_vs_sma50"] = ((price - sma50) / sma50
                               if sma50 is not None and sma50 > 0 else None)

        # Derived field: bullish_bias
        # Inputs: price, sma20, return_2d, closes[-1], closes[-2]
        # Formula: True if (close >= SMA20) OR (2d_return >= 0) OR (close[-1] > close[-2])
        bullish = False
        if sma20 is not None and price >= sma20:
            bullish = True
        elif m["return_2d"] is not None and m["return_2d"] >= 0:
            bullish = True
        elif len(closes) >= 2 and closes[-1] > closes[-2]:
            bullish = True
        m["bullish_bias"] = bullish

        # ── F) Volume confirmation ──────────────────────────────────

        if volumes and len(volumes) >= 20:
            avg_v = sum(volumes[-20:]) / 20
            m["avg_vol_20"] = round(avg_v)
            m["avg_dollar_vol_20"] = round(avg_v * price)
            today_vol = volumes[-1] if volumes else None
            m["vol_spike_ratio"] = (today_vol / avg_v
                                    if today_vol is not None and avg_v > 0 else None)
            m["today_vol"] = today_vol
        else:
            m["avg_vol_20"] = None
            m["avg_dollar_vol_20"] = None
            m["vol_spike_ratio"] = None
            m["today_vol"] = None

        # ── G) Risk / gap metrics ───────────────────────────────────

        # Derived field: gap_pct
        # Inputs: opens[-1], closes[-2]
        # Formula: (opens[-1] - closes[-2]) / closes[-2]
        if opens and len(opens) >= 1 and len(closes) >= 2 and closes[-2] > 0:
            m["gap_pct"] = (opens[-1] - closes[-2]) / closes[-2]
        else:
            m["gap_pct"] = None

        # RSI for context
        m["rsi14"] = rsi(closes, 14) if len(closes) >= 15 else None

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

        # SMA200 for context
        m["sma200"] = simple_moving_average(closes, 200) if len(closes) >= 200 else None

        # Realized vol 40D (baseline for context)
        if len(closes) >= 41:
            m["rv_40"] = realized_vol_annualized(closes[-41:], trading_days=252)
        else:
            m["rv_40"] = None

        # ── H) Expansion state classification ──────────────────────

        # Derived field: expansion_state
        # Inputs: atr_ratio_10, rv_ratio, range_ratio, bb_width_percentile_180,
        #         bullish_bias
        # Formula:
        #   "expanding_bullish" if expansion + bullish_bias
        #   "expanding" if expansion but neutral/mixed direction
        #   "compressed" if compression detected but no expansion yet
        #   else "neutral"
        atr_r = m.get("atr_ratio_10")
        rv_r = m.get("rv_ratio")
        rng_r = m.get("range_ratio")
        bb_pct = m.get("bb_width_percentile_180")

        is_expanding = False
        if atr_r is not None and atr_r >= 1.25:
            is_expanding = True
        if rv_r is not None and rv_r >= 1.25:
            is_expanding = True
        if rng_r is not None and rng_r >= 1.35:
            is_expanding = True

        is_compressed_prior = False
        if bb_pct is not None and bb_pct <= 35:
            is_compressed_prior = True
        if m.get("prior_range_20_pct") is not None and m["prior_range_20_pct"] <= 0.14:
            is_compressed_prior = True
        if m.get("prior_atr_pct") is not None and m["prior_atr_pct"] <= 0.045:
            is_compressed_prior = True

        if is_expanding and bullish:
            m["expansion_state"] = "expanding_bullish"
        elif is_expanding:
            m["expansion_state"] = "expanding"
        elif is_compressed_prior:
            m["expansion_state"] = "compressed"
        else:
            m["expansion_state"] = "neutral"

        return m

    # ── Filters ─────────────────────────────────────────────────────────────

    @staticmethod
    def _apply_filters(
        symbol: str,
        metrics: dict[str, Any],
        cfg: dict[str, Any],
    ) -> dict[str, str] | None:
        """Apply balanced volatility expansion filters.
        Returns rejection dict or None.

        A candidate qualifies if:
          1) Evidence of expansion (must meet ONE)
          2) Evidence it expanded FROM compression (must meet ONE)
          3) Long bias sanity (must meet ONE)
          4) Tradability / risk sanity

        Filter order documented per scanner-contract requirement.
        """
        atr_ratio = metrics.get("atr_ratio_10")
        rv_ratio = metrics.get("rv_ratio")
        range_ratio = metrics.get("range_ratio")
        bb_pctile = metrics.get("bb_width_percentile_180")
        bb_rising = metrics.get("bb_width_rising")
        prior_range = metrics.get("prior_range_20_pct")
        prior_atr = metrics.get("prior_atr_pct")
        bullish = metrics.get("bullish_bias", False)
        close_vs_sma20 = metrics.get("close_vs_sma20")
        return_2d = metrics.get("return_2d")
        return_1d = metrics.get("return_1d")
        vol_spike = metrics.get("vol_spike_ratio")
        atr_pct = metrics.get("atr_pct")
        avg_dv = metrics.get("avg_dollar_vol_20")
        price = metrics.get("sma20")  # use a reasonable ref

        # 1) Expansion gate (must meet ONE)
        expansion_met = False
        if atr_ratio is not None and atr_ratio >= cfg["atr_ratio_min"]:
            expansion_met = True
        if rv_ratio is not None and rv_ratio >= cfg["rv_ratio_min"]:
            expansion_met = True
        if range_ratio is not None and range_ratio >= cfg["range_ratio_min"]:
            expansion_met = True

        if not expansion_met:
            parts = []
            if atr_ratio is not None:
                parts.append(f"ATRr={atr_ratio:.2f}")
            if rv_ratio is not None:
                parts.append(f"RVr={rv_ratio:.2f}")
            if range_ratio is not None:
                parts.append(f"RngR={range_ratio:.2f}")
            return {"symbol": symbol, "reason_code": "NO_EXPANSION",
                    "detail": f"No expansion signal: {', '.join(parts)}"}

        # 2) Prior compression gate (must meet ONE)
        compression_met = False
        if (bb_pctile is not None and bb_pctile <= cfg["bb_width_percentile_max"]
                and bb_rising is True):
            compression_met = True
        if prior_range is not None and prior_range <= cfg["prior_range_20_max"]:
            compression_met = True
        if prior_atr is not None and prior_atr <= cfg["prior_atr_pct_max"]:
            compression_met = True

        if not compression_met:
            parts = []
            if bb_pctile is not None:
                parts.append(f"BBpct={bb_pctile:.0f}")
            if prior_range is not None:
                parts.append(f"prRng={prior_range * 100:.1f}%")
            if prior_atr is not None:
                parts.append(f"prATR={prior_atr * 100:.2f}%")
            return {"symbol": symbol, "reason_code": "NO_PRIOR_COMPRESSION",
                    "detail": f"Expanding but no prior compression: {', '.join(parts)}"}

        # 3) Long bias sanity (must meet ONE)
        long_met = False
        if close_vs_sma20 is not None and close_vs_sma20 >= 0:
            long_met = True
        if return_2d is not None and return_2d >= 0:
            long_met = True
        if (return_1d is not None and return_1d > 0
                and vol_spike is not None and vol_spike >= 1.2):
            long_met = True

        if not long_met:
            return {"symbol": symbol, "reason_code": "NO_LONG_BIAS",
                    "detail": f"Expansion from compression but bearish direction "
                              f"(vs_sma20={close_vs_sma20}, 2d_ret={return_2d})"}

        # 4) Tradability / risk sanity
        if atr_pct is not None and atr_pct > cfg["atr_pct_max"]:
            return {"symbol": symbol, "reason_code": "TOO_VOLATILE",
                    "detail": f"ATR% {atr_pct * 100:.1f}% > {cfg['atr_pct_max'] * 100:.0f}%"}

        return None  # passed all filters

    # ── Scoring ─────────────────────────────────────────────────────────────

    @staticmethod
    def _score(metrics: dict[str, Any]) -> tuple[dict[str, float], float]:
        """Score a candidate. Returns (breakdown_dict, composite).

        Score components (0-100 total):
          - expansion_score    (0-40): ATR ratio, RV ratio, range ratio
          - compression_score  (0-25): prior low vol/range, BB width percentile
          - confirmation_score (0-20): volume spike, direction confirmation
          - risk_score         (0-15): ATR% reasonableness, liquidity, gap
        """

        # ── Expansion score (0-40) ──
        expansion = 0.0
        atr_r = metrics.get("atr_ratio_10")
        rv_r = metrics.get("rv_ratio")
        rng_r = metrics.get("range_ratio")

        # Score based on the strongest expansion signal
        best_ratio = 0.0
        if atr_r is not None:
            best_ratio = max(best_ratio, atr_r)
        if rv_r is not None:
            best_ratio = max(best_ratio, rv_r)
        if rng_r is not None:
            # range ratio uses 1.35 threshold, scale to comparable range
            best_ratio = max(best_ratio, rng_r * 0.93)

        if best_ratio >= 2.0:
            expansion += 30
        elif best_ratio >= 1.7:
            expansion += 25
        elif best_ratio >= 1.5:
            expansion += 20
        elif best_ratio >= 1.35:
            expansion += 15
        elif best_ratio >= 1.25:
            expansion += 10

        # Bonus for multiple expansion signals
        signals_count = 0
        if atr_r is not None and atr_r >= 1.25:
            signals_count += 1
        if rv_r is not None and rv_r >= 1.25:
            signals_count += 1
        if rng_r is not None and rng_r >= 1.35:
            signals_count += 1

        if signals_count >= 3:
            expansion += 10
        elif signals_count >= 2:
            expansion += 6

        expansion = min(expansion, 40.0)

        # ── Compression score (0-25) ──
        compression = 0.0
        bb_pct = metrics.get("bb_width_percentile_180")
        prior_range = metrics.get("prior_range_20_pct")
        prior_atr = metrics.get("prior_atr_pct")

        if bb_pct is not None:
            if bb_pct <= 15:
                compression += 14  # very tight compression
            elif bb_pct <= 25:
                compression += 11
            elif bb_pct <= 35:
                compression += 7
            elif bb_pct <= 50:
                compression += 3

        if prior_range is not None:
            if prior_range <= 0.08:
                compression += 7
            elif prior_range <= 0.12:
                compression += 5
            elif prior_range <= 0.14:
                compression += 3

        if prior_atr is not None:
            if prior_atr <= 0.025:
                compression += 4
            elif prior_atr <= 0.035:
                compression += 3
            elif prior_atr <= 0.045:
                compression += 2

        compression = min(compression, 25.0)

        # ── Confirmation score (0-20) ──
        confirm = 0.0
        vsr = metrics.get("vol_spike_ratio")
        r1d = metrics.get("return_1d")
        r2d = metrics.get("return_2d")
        close_vs = metrics.get("close_vs_sma20")
        bullish = metrics.get("bullish_bias", False)

        # Volume spike
        if vsr is not None:
            if vsr >= 2.5:
                confirm += 8
            elif vsr >= 1.8:
                confirm += 7
            elif vsr >= 1.3:
                confirm += 5
            elif vsr >= 1.0:
                confirm += 2

        # Direction confirmation
        if close_vs is not None and close_vs >= 0:
            confirm += 4
        if r2d is not None and r2d > 0:
            confirm += 3
        elif r1d is not None and r1d > 0:
            confirm += 2

        if bullish:
            confirm += 3

        confirm = min(confirm, 20.0)

        # ── Risk score (0-15) ──
        risk = 0.0
        atr_pct = metrics.get("atr_pct")
        adv = metrics.get("avg_dollar_vol_20")
        gap = metrics.get("gap_pct")

        # ATR% reasonableness
        if atr_pct is not None:
            if atr_pct <= 0.03:
                risk += 5
            elif atr_pct <= 0.05:
                risk += 4
            elif atr_pct <= 0.08:
                risk += 3
            elif atr_pct <= 0.12:
                risk += 1

        # Liquidity
        if adv is not None:
            if adv >= 500_000_000:
                risk += 6
            elif adv >= 200_000_000:
                risk += 5
            elif adv >= 100_000_000:
                risk += 4
            elif adv >= 50_000_000:
                risk += 3
            elif adv >= 20_000_000:
                risk += 1

        # Gap penalty
        if gap is not None:
            abs_gap = abs(gap)
            if abs_gap <= 0.01:
                risk += 2  # minimal gap = bonus
            elif abs_gap >= 0.05:
                risk = max(0, risk - 2)  # big gap = penalty

        risk = min(risk, 15.0)

        breakdown = {
            "expansion": expansion,
            "compression": compression,
            "confirmation": confirm,
            "risk": risk,
        }
        composite = expansion + compression + confirm + risk
        return breakdown, min(composite, 100.0)

    # ── Thesis bullets ──────────────────────────────────────────────────────

    @staticmethod
    def _build_thesis(metrics: dict[str, Any], scores: dict[str, float],
                      price: float) -> list[str]:
        """Generate 3-6 human-readable thesis bullets."""
        bullets: list[str] = []

        atr_r = metrics.get("atr_ratio_10")
        if atr_r is not None and atr_r >= 1.15:
            bullets.append(f"ATR expansion {atr_r:.2f}× vs 10D baseline")

        rv_r = metrics.get("rv_ratio")
        if rv_r is not None and rv_r >= 1.15:
            bullets.append(f"RV20 expansion {rv_r:.2f}×")

        rng_r = metrics.get("range_ratio")
        if rng_r is not None and rng_r >= 1.2:
            bullets.append(f"20D range expansion {rng_r:.2f}× vs prior")

        prior_range = metrics.get("prior_range_20_pct")
        if prior_range is not None and prior_range <= 0.14:
            bullets.append(f"Prior 20D range compressed ({prior_range * 100:.1f}%)")

        bb_pct = metrics.get("bb_width_percentile_180")
        if bb_pct is not None and bb_pct <= 40:
            bullets.append(f"BB width percentile {bb_pct:.0f} (compressed)")

        vsr = metrics.get("vol_spike_ratio")
        if vsr is not None and vsr >= 1.2:
            bullets.append(f"Volume {vsr:.1f}× avg confirms expansion")

        close_vs = metrics.get("close_vs_sma20")
        if close_vs is not None and close_vs >= 0:
            bullets.append("Close above SMA-20 (bullish bias)")

        atr_pct = metrics.get("atr_pct")
        if atr_pct is not None and atr_pct > 0.08:
            bullets.append(f"Note: ATR% {atr_pct * 100:.1f}% (elevated volatility)")

        gap = metrics.get("gap_pct")
        if gap is not None and abs(gap) >= 0.03:
            direction = "up" if gap > 0 else "down"
            bullets.append(f"Gap {direction} {abs(gap) * 100:.1f}% on last open")

        return bullets[:6]
