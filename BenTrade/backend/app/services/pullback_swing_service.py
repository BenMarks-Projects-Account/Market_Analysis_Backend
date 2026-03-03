"""
BenTrade — Pullback Swing Stock Strategy Service
strategy_id: stock_pullback_swing

End-to-end scanner pipeline:
  1) Universe assembly (balanced: ~150-400 liquid stocks, ETFs excluded)
  2) Per-symbol OHLCV fetch (Tradier primary, async semaphore-limited)
  3) Pullback-swing enrichment metrics
  4) Simple composite scoring (trend + pullback + reset + liquidity)
  5) Trade shape construction with canonical trade_key

Data source policy:
  - Tradier is authoritative for universe + pricing
  - Fallback to BaseDataService history only if Tradier fails, with confidence downgrade

TODO:
  - Multi-level gates / filter stack (future phase)
  - Presets framework (Strict / Balanced / Wide)
  - SPY market context overlay
  - IV integration when cheap/cached
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base_data_service import BaseDataService
from app.utils.trade_key import stock_trade_key, stock_idea_key
from common.quant_analysis import rsi, simple_moving_average

logger = logging.getLogger(__name__)

# ── ETF / Index exclusion set ───────────────────────────────────────────────
# Comprehensive list of broad-market, sector, and leveraged ETFs to exclude.
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
# This is a stopgap until a Tradier screener endpoint is available.
# Covers major sectors; all are stocks (not ETFs).
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
    # Real estate (REITs are borderline; include a few liquid ones)
    "AMT", "PLD", "CCI", "EQIX", "SPG", "O", "PSA",
    # Utilities (a handful)
    "NEE", "DUK", "SO", "AEP", "D",
]


# ── Balanced config (single level — no presets system yet) ──────────────────
_BALANCED_CONFIG = {
    "min_history_bars": 220,       # need SMA-200 stability
    "min_price": 5.0,
    "min_avg_dollar_vol": 15_000_000,  # $15M avg daily dollar volume
    "lookback_days": 400,          # request enough calendar days for ~280 trading days
    "concurrency": 8,
    "per_symbol_timeout": 12.0,    # seconds
}


# ────────────────────────────────────────────────────────────────────────────
# Service
# ────────────────────────────────────────────────────────────────────────────

class PullbackSwingService:
    """Pullback Swing stock strategy scanner."""

    STRATEGY_ID = "stock_pullback_swing"

    def __init__(self, base_data_service: BaseDataService) -> None:
        self.bds = base_data_service

    # ── Public entry point ──────────────────────────────────────────────────

    async def scan(self, *, max_candidates: int = 30) -> dict[str, Any]:
        """Run the full pullback swing scan and return the payload."""
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
        results: list[dict[str, Any] | None] = []

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
        """Fetch data, compute metrics, score, and build trade shape for one symbol."""

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
            logger.warning("event=pullback_bars_fail symbol=%s error=%s", symbol, exc)

        # Fallback to BaseDataService history (close-only)
        if not bars:
            data_source = "fallback"
            try:
                closes = await self.bds.get_prices_history(symbol, lookback_days=cfg["lookback_days"])
                if closes:
                    bars = [{"date": None, "open": None, "high": None, "low": None, "close": c, "volume": None} for c in closes]
            except Exception as exc:
                logger.warning("event=pullback_fallback_fail symbol=%s error=%s", symbol, exc)

        if not bars:
            rejections.append({"symbol": symbol, "reason_code": "NO_DATA", "detail": "No price history available"})
            return None

        # -- Extract series --
        closes = [b["close"] for b in bars if b.get("close") is not None]
        volumes = [b["volume"] for b in bars if b.get("volume") is not None]
        highs = [b["high"] for b in bars if b.get("high") is not None]
        lows = [b["low"] for b in bars if b.get("low") is not None]

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

        # If volume data is missing (fallback source), allow but flag
        if avg_dollar_vol_20 is None:
            avg_vol_20 = None  # can't compute

        # -- Compute enrichment metrics --
        metrics = self._compute_metrics(closes, highs, lows, volumes, price)

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
            "trend_score": round(score_breakdown.get("trend", 0), 1),
            "pullback_score": round(score_breakdown.get("pullback", 0), 1),
            "reset_score": round(score_breakdown.get("reset", 0), 1),
            "liquidity_score": round(score_breakdown.get("liquidity", 0), 1),
            "metrics": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in metrics.items()},
            "thesis": thesis,
            "trend_state": metrics.get("trend_state", "unknown"),
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
        volumes: list[int],
        price: float,
    ) -> dict[str, Any]:
        """Compute all pullback-swing enrichment metrics."""
        m: dict[str, Any] = {}

        # A) Moving averages
        sma20 = simple_moving_average(closes, 20)
        sma50 = simple_moving_average(closes, 50)
        sma200 = simple_moving_average(closes, 200)
        m["sma20"] = sma20
        m["sma50"] = sma50
        m["sma200"] = sma200

        # Slopes (simple: change over last N bars / price, annualized-ish)
        # slope_20: (SMA20 now − SMA20 10 bars ago) / price
        if len(closes) > 30:
            sma20_lagged = simple_moving_average(closes[:-10], 20)
            m["slope_20"] = ((sma20 - sma20_lagged) / price) if (sma20 is not None and sma20_lagged is not None and price > 0) else None
        else:
            m["slope_20"] = None

        if len(closes) > 60:
            sma50_lagged = simple_moving_average(closes[:-10], 50)
            m["slope_50"] = ((sma50 - sma50_lagged) / price) if (sma50 is not None and sma50_lagged is not None and price > 0) else None
        else:
            m["slope_50"] = None

        # Trend state
        # Derived field: trend_state
        # Inputs: price, sma20, sma50, sma200, slope_50
        # Formula:
        #   "strong_uptrend" if price > sma20 > sma50 > sma200 AND slope_50 > 0
        #   "uptrend" if price > sma50 AND sma50 > sma200 AND slope_50 > 0
        #   else "not_uptrend"
        trend_state = "not_uptrend"
        if sma20 is not None and sma50 is not None and sma200 is not None:
            slope_50 = m.get("slope_50")
            slope_ok = slope_50 is not None and slope_50 > 0
            if price > sma20 > sma50 > sma200 and slope_ok:
                trend_state = "strong_uptrend"
            elif price > sma50 and sma50 > sma200 and slope_ok:
                trend_state = "uptrend"
        m["trend_state"] = trend_state

        # B) Pullback characterization
        high_20d = max(highs[-20:]) if len(highs) >= 20 else (max(closes[-20:]) if len(closes) >= 20 else price)
        high_50d = max(highs[-50:]) if len(highs) >= 50 else (max(closes[-50:]) if len(closes) >= 50 else price)

        # Derived field: pullback_from_20d_high
        # Inputs: price, high_20d
        # Formula: (price - high_20d) / high_20d  (negative = pullback)
        m["pullback_from_20d_high"] = (price - high_20d) / high_20d if high_20d > 0 else 0.0
        m["pullback_from_50d_high"] = (price - high_50d) / high_50d if high_50d > 0 else 0.0

        # Derived field: distance_to_sma20
        # Inputs: price, sma20
        # Formula: (price - sma20) / sma20
        m["distance_to_sma20"] = (price - sma20) / sma20 if sma20 and sma20 > 0 else None
        m["distance_to_sma50"] = (price - sma50) / sma50 if sma50 and sma50 > 0 else None

        # Returns
        m["return_1d"] = (closes[-1] - closes[-2]) / closes[-2] if len(closes) >= 2 and closes[-2] > 0 else None
        m["return_5d"] = (closes[-1] - closes[-6]) / closes[-6] if len(closes) >= 6 and closes[-6] > 0 else None
        m["return_20d"] = (closes[-1] - closes[-21]) / closes[-21] if len(closes) >= 21 and closes[-21] > 0 else None

        # C) Momentum reset
        rsi14 = rsi(closes, 14)
        m["rsi14"] = rsi14

        # RSI change over 5 days
        if len(closes) >= 20:
            rsi_5d_ago = rsi(closes[:-5], 14)
            m["rsi_change_5d"] = (rsi14 - rsi_5d_ago) if (rsi14 is not None and rsi_5d_ago is not None) else None
        else:
            m["rsi_change_5d"] = None

        # D) Liquidity
        if volumes and len(volumes) >= 20:
            avg_v = sum(volumes[-20:]) / 20
            m["avg_vol_20"] = round(avg_v)
            m["avg_dollar_vol_20"] = round(avg_v * price)
            today_vol = volumes[-1] if volumes else None
            m["today_vol_vs_avg"] = today_vol / avg_v if (today_vol is not None and avg_v > 0) else None
        else:
            m["avg_vol_20"] = None
            m["avg_dollar_vol_20"] = None
            m["today_vol_vs_avg"] = None

        # 52-week range
        if len(closes) >= 252:
            m["high_52w"] = max(closes[-252:])
            m["low_52w"] = min(closes[-252:])
        elif len(closes) >= 200:
            m["high_52w"] = max(closes)
            m["low_52w"] = min(closes)
        else:
            m["high_52w"] = None
            m["low_52w"] = None

        return m

    # ── Scoring ─────────────────────────────────────────────────────────────

    @staticmethod
    def _score(metrics: dict[str, Any]) -> tuple[dict[str, float], float]:
        """Score a candidate.  Returns (breakdown_dict, composite).

        Score components (0–100 total):
          - trend_score   (0–35): trend_state + MA alignment + slope_50
          - pullback_score (0–35): pullback zone + distance to SMA20 + not too far below SMA50
          - reset_score   (0–20): RSI in 40–60 zone, not collapsing
          - liquidity_score (0–10): avg dollar vol + today_vol_vs_avg sanity
        """
        # ── Trend score (0–35) ──
        trend = 0.0
        ts = metrics.get("trend_state", "not_uptrend")
        if ts == "strong_uptrend":
            trend += 20
        elif ts == "uptrend":
            trend += 12

        sma20 = metrics.get("sma20")
        sma50 = metrics.get("sma50")
        sma200 = metrics.get("sma200")
        price = (sma20 or 1)  # safe fallback; actual price is in the caller
        # MA alignment bonus
        if sma20 is not None and sma50 is not None and sma20 > sma50:
            trend += 5
        if sma50 is not None and sma200 is not None and sma50 > sma200:
            trend += 4

        slope_50 = metrics.get("slope_50")
        if slope_50 is not None:
            if slope_50 > 0.01:
                trend += 6
            elif slope_50 > 0:
                trend += 3

        trend = min(trend, 35.0)

        # ── Pullback score (0–35) ──
        pullback = 0.0
        pb20 = metrics.get("pullback_from_20d_high")
        dist_sma20 = metrics.get("distance_to_sma20")
        dist_sma50 = metrics.get("distance_to_sma50")

        # Best zone: pullback from 20d high between -1% and -6%
        if pb20 is not None:
            if -0.06 <= pb20 <= -0.01:
                pullback += 18  # sweet spot
            elif -0.10 <= pb20 < -0.06:
                pullback += 12  # deeper pullback, still ok
            elif -0.01 < pb20 <= 0:
                pullback += 6   # barely pulled back
            elif -0.15 <= pb20 < -0.10:
                pullback += 5   # getting extended
            else:
                pullback += 0   # too deep or no pullback

        # Near SMA20 (within ±1.5%) bonus
        if dist_sma20 is not None:
            if -0.015 <= dist_sma20 <= 0.015:
                pullback += 10  # right at support
            elif -0.03 <= dist_sma20 < -0.015:
                pullback += 5   # slightly below SMA20
            elif 0.015 < dist_sma20 <= 0.03:
                pullback += 4   # slightly above

        # Penalty: too far below SMA50
        if dist_sma50 is not None and dist_sma50 < -0.02:
            pullback = max(0, pullback - 8)

        pullback = min(pullback, 35.0)

        # ── Reset score (0–20) ──
        reset = 0.0
        rsi14 = metrics.get("rsi14")
        rsi_change = metrics.get("rsi_change_5d")

        if rsi14 is not None:
            if 40 <= rsi14 <= 60:
                reset += 14  # ideal reset zone
            elif 35 <= rsi14 < 40:
                reset += 10  # approaching oversold
            elif 60 < rsi14 <= 68:
                reset += 8   # slightly elevated
            elif 30 <= rsi14 < 35:
                reset += 5   # near oversold
            elif rsi14 < 30:
                reset += 2   # falling knife risk
            elif rsi14 > 68:
                reset += 2   # chasing risk

        # RSI stabilizing or rising from low = bonus
        if rsi_change is not None and rsi14 is not None:
            if rsi14 < 55 and rsi_change > 0:
                reset += 4  # recovering momentum
            elif rsi_change < -10:
                reset = max(0, reset - 3)  # collapsing

        reset = min(reset, 20.0)

        # ── Liquidity score (0–10) ──
        liq = 0.0
        adv = metrics.get("avg_dollar_vol_20")
        tvr = metrics.get("today_vol_vs_avg")

        if adv is not None:
            if adv >= 500_000_000:
                liq += 7
            elif adv >= 100_000_000:
                liq += 5
            elif adv >= 50_000_000:
                liq += 4
            elif adv >= 15_000_000:
                liq += 2

        # Volume sanity: today's volume not wildly abnormal
        if tvr is not None:
            if 0.3 <= tvr <= 3.0:
                liq += 3  # normal range
            elif 0.1 <= tvr < 0.3 or 3.0 < tvr <= 5.0:
                liq += 1  # slightly off

        liq = min(liq, 10.0)

        breakdown = {
            "trend": trend,
            "pullback": pullback,
            "reset": reset,
            "liquidity": liq,
        }
        composite = trend + pullback + reset + liq
        return breakdown, composite

    # ── Thesis bullets ──────────────────────────────────────────────────────

    @staticmethod
    def _build_thesis(metrics: dict[str, Any], scores: dict[str, float], price: float) -> list[str]:
        """Generate 3–5 human-readable thesis bullets."""
        bullets: list[str] = []

        ts = metrics.get("trend_state", "not_uptrend")
        if ts == "strong_uptrend":
            bullets.append("Price above rising SMA-20, SMA-50, and SMA-200 (strong uptrend)")
        elif ts == "uptrend":
            bullets.append("Price above rising SMA-50 (uptrend)")

        pb20 = metrics.get("pullback_from_20d_high")
        if pb20 is not None and pb20 < -0.005:
            bullets.append(f"Pullback {pb20 * 100:.1f}% from 20D high")

        dist20 = metrics.get("distance_to_sma20")
        if dist20 is not None and -0.02 <= dist20 <= 0.02:
            bullets.append(f"Near SMA-20 support ({dist20 * 100:+.1f}%)")

        rsi14 = metrics.get("rsi14")
        if rsi14 is not None:
            if 40 <= rsi14 <= 60:
                bullets.append(f"RSI {rsi14:.0f} (reset zone)")
            elif 30 <= rsi14 < 40:
                bullets.append(f"RSI {rsi14:.0f} (oversold — potential bounce)")
            elif 60 < rsi14 <= 70:
                bullets.append(f"RSI {rsi14:.0f} (moderate momentum)")

        adv = metrics.get("avg_dollar_vol_20")
        if adv is not None and adv >= 100_000_000:
            bullets.append(f"High liquidity (${adv / 1_000_000:.0f}M avg daily $vol)")

        slope50 = metrics.get("slope_50")
        if slope50 is not None and slope50 > 0.005:
            bullets.append("SMA-50 trending higher")

        return bullets[:5]
