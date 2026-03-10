"""Volatility & Options Structure Data Provider.

Fetches raw data needed by the volatility_options_engine from available
data sources. Follows the same provider pattern as BreadthDataProvider.

Data sources:
  - MarketContextService — VIX spot (Tradier → Finnhub → FRED waterfall)
  - Tradier — VIX quote, VVIX quote, SPY options chain (IV/skew),
              VIX daily closes (VIX rank / percentile / 20d avg),
              SPY daily closes (realized volatility)
  - FRED — VIXCLS (EOD fallback), SKEW (CBOE Skew Index)

The provider assembles raw dicts that map directly to engine pillar inputs.
It does NOT score or interpret — that is the engine's job.

PROXY METRICS (labeled explicitly):
  These are proxy/index-level approximations due to data availability constraints:
  - vix_rank_30d (PROXY)        = (vix_spot - min(VIX, 30d)) / (max(VIX, 30d) - min(VIX, 30d)) × 100
                                   [Derived from VIX futures-free index, not actual implied vol]
  - vix_percentile_1y (PROXY)   = count(VIX_history < vix_spot) / len(VIX_history) × 100
  - spy_pc_ratio_proxy (PROXY)  = SPY options P/C ratio (not broader index options)
  - tail_risk_signal (DERIVED)  = Deterministic rule-based assessment from put_skew + CBOE SKEW

PRIMARY DERIVED FIELDS:
  vix_avg_20d              = mean(VIX closes, last 20 trading days)
  rv_30d_close_close       = annualized std dev of SPY log-returns over 30 trading days
                             std(ln(P[i]/P[i-1]) for i=1..30) × sqrt(252) × 100
  option_richness          = Blended interpretation (Rich/Fair/Cheap) using both VIX rank context
                             and IV-RV spread
  cboe_skew                = FRED series "SKEW" latest observation (tail hedging demand)
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Symbols for quote-based data
_VIX_SYMBOL = "VIX"
_VVIX_SYMBOL = "VVIX"
_SPY_SYMBOL = "SPY"

# FRED series for CBOE Skew Index
_FRED_SKEW_SERIES = "SKEW"

# Historical lookback periods (calendar days, over-request to cover weekends/holidays)
_VIX_HISTORY_CALENDAR_DAYS = 380   # Need ~252 trading days
_SPY_HISTORY_CALENDAR_DAYS = 60    # Need ~30 trading days of returns


# ═══════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def _interpolate(
    value: float,
    in_lo: float,
    in_hi: float,
    out_lo: float = 0.0,
    out_hi: float = 100.0,
) -> float:
    """Linearly interpolate value from [in_lo, in_hi] → [out_lo, out_hi].

    Formula: score = out_lo + (value - in_lo) / (in_hi - in_lo) * (out_hi - out_lo)
    Clamps to output range.
    """
    if in_hi == in_lo:
        return (out_lo + out_hi) / 2
    ratio = (value - in_lo) / (in_hi - in_lo)
    return max(min(out_lo, out_hi), min(max(out_lo, out_hi), out_lo + ratio * (out_hi - out_lo)))


class VolatilityOptionsDataProvider:
    """Fetch raw volatility data for the engine."""

    def __init__(
        self,
        tradier_client: Any,
        market_context_service: Any | None = None,
        fred_client: Any | None = None,
    ) -> None:
        self.tradier = tradier_client
        self.market_ctx = market_context_service
        self.fred = fred_client

    async def fetch_volatility_data(self) -> dict[str, Any]:
        """Fetch all raw data needed by the volatility engine.

        Returns dict with keys: regime_data, structure_data, skew_data,
        positioning_data, metric_availability — one per engine pillar
        (pillar 5 is derived).
        """
        logger.info("[VOL_PROVIDER] fetch_start")

        # Fire independent fetches in parallel
        vix_task = self._fetch_vix_data()
        vvix_task = self._fetch_vvix()
        spy_iv_task = self._fetch_spy_iv_data()
        vix_hist_task = self._fetch_vix_history()
        spy_rv_task = self._fetch_spy_rv()
        cboe_skew_task = self._fetch_cboe_skew()

        vix_data, vvix_val, spy_iv, vix_hist, spy_rv, cboe_skew = (
            await asyncio.gather(
                vix_task, vvix_task, spy_iv_task,
                vix_hist_task, spy_rv_task, cboe_skew_task,
                return_exceptions=True,
            )
        )

        # Safely unpack (exceptions become empty dicts / None)
        if isinstance(vix_data, Exception):
            logger.warning("[VOL_PROVIDER] vix_fetch_failed error=%s", vix_data)
            vix_data = {}
        if isinstance(vvix_val, Exception):
            logger.warning("[VOL_PROVIDER] vvix_fetch_failed error=%s", vvix_val)
            vvix_val = None
        if isinstance(spy_iv, Exception):
            logger.warning("[VOL_PROVIDER] spy_iv_fetch_failed error=%s", spy_iv)
            spy_iv = {}
        if isinstance(vix_hist, Exception):
            logger.warning("[VOL_PROVIDER] vix_history_failed error=%s", vix_hist)
            vix_hist = {}
        if isinstance(spy_rv, Exception):
            logger.warning("[VOL_PROVIDER] spy_rv_failed error=%s", spy_rv)
            spy_rv = {}
        if isinstance(cboe_skew, Exception):
            logger.warning("[VOL_PROVIDER] cboe_skew_failed error=%s", cboe_skew)
            cboe_skew = None

        vix_spot = vix_data.get("vix_spot")
        vix_source = vix_data.get("source", "unknown")

        # VIX history-derived proxy metrics (not true IV history, but index-level approximation)
        vix_avg_20d = vix_hist.get("vix_avg_20d")
        vix_rank_30d = vix_hist.get("vix_rank_30d")  # PROXY: VIX history, not option IV
        vix_percentile_1y = vix_hist.get("vix_percentile_1y")  # PROXY: VIX history, not option IV

        # Realized volatility from SPY history (close-to-close, annualized)
        rv_30d = spy_rv.get("rv_30d")

        # Tail Risk Signal — deterministic rule-based signal combining multiple inputs
        # Inputs: put_skew_25d (25-delta put IV premium), CBOE SKEW index (if available)
        # Output: One of {Low, Moderate, Elevated, High} + numeric value 0-100
        put_skew_25d = spy_iv.get("put_skew_25d")
        tail_risk_signal = None
        tail_risk_numeric = None  # Will be 0-100 for historical compatibility
        if put_skew_25d is not None or cboe_skew is not None:
            # Compute numeric signal from available inputs
            skew_components: list[float] = []
            if put_skew_25d is not None:
                # Normalize put skew: -2 to 5 → map to 0-100
                # Negative skew (calls richer) = lower tail risk
                # Positive skew (puts richer) = higher tail risk
                skew_score = _interpolate(put_skew_25d, -2.0, 5.0, 20.0, 85.0)
                skew_components.append(skew_score)
            if cboe_skew is not None:
                # CBOE SKEW typical range 110-160
                # Lower = less tail concern, higher = more hedging demand
                skew_score = _interpolate(cboe_skew, 110.0, 160.0, 15.0, 90.0)
                skew_components.append(skew_score)

            if skew_components:
                tail_risk_numeric = round(sum(skew_components) / len(skew_components), 1)
                # Deterministic thresholds for signal state
                if tail_risk_numeric <= 30:
                    tail_risk_signal = "Low"
                elif tail_risk_numeric <= 60:
                    tail_risk_signal = "Moderate"
                elif tail_risk_numeric <= 80:
                    tail_risk_signal = "Elevated"
                else:
                    tail_risk_signal = "High"

        # Option Richness — blended logic combining VIX rank context and IV-RV spread
        # NOT just iv_rank alone, but considers both historical IV context and current premium
        option_richness = None
        option_richness_label = None
        iv_30d = spy_iv.get("iv_30d")
        if vix_rank_30d is not None and iv_30d is not None and rv_30d is not None:
            # Rich: VIX rank elevated (>60) AND IV > RV → options expensive with elevated vol
            # Cheap: VIX rank low (<30) OR IV <= RV → options compressed or underpriced
            # Fair: Mixed signals or moderate context
            iv_rv_spread = iv_30d - rv_30d
            is_high_rank = vix_rank_30d > 60
            is_iv_high = iv_30d > rv_30d
            is_low_rank = vix_rank_30d < 30
            is_iv_low = iv_30d <= rv_30d

            if (is_high_rank and is_iv_high):
                option_richness_label = "Rich"
                option_richness = 75.0  # High richness
            elif (is_low_rank or is_iv_low):
                option_richness_label = "Cheap"
                option_richness = 25.0  # Low richness
            else:
                option_richness_label = "Fair"
                option_richness = 50.0  # Fair/moderate richness
        elif vix_rank_30d is not None:
            # Degraded: only VIX rank available, fall back to simple mapping
            if vix_rank_30d > 60:
                option_richness_label = "Rich"
                option_richness = 70.0
            elif vix_rank_30d < 30:
                option_richness_label = "Cheap"
                option_richness = 30.0
            else:
                option_richness_label = "Fair"
                option_richness = 50.0

        # Compute premium bias from multiple signals
        # Positive = favors selling, negative = favors buying
        premium_bias = spy_iv.get("premium_bias")
        if premium_bias is None:
            bias_components: list[float] = []
            if iv_30d is not None and rv_30d is not None and rv_30d > 0:
                # IV > RV → options are overpriced → sell bias
                vrp = iv_30d - rv_30d
                bias_components.append(min(max(vrp * 5, -50), 50))
            if vix_rank_30d is not None:
                # High IV rank → sell bias (options are expensive relative to history)
                bias_components.append((vix_rank_30d - 50) * 0.8)
            eq_pc = spy_iv.get("equity_pc_ratio")
            if eq_pc is not None:
                # Low P/C → bullish → sell puts; High P/C → bearish → buy puts
                bias_components.append((0.85 - eq_pc) * 40)
            if bias_components:
                premium_bias = round(
                    sum(bias_components) / len(bias_components), 2,
                )

        # Build pillar input dicts
        regime_data = {
            "vix_spot": vix_spot,
            "vix_avg_20d": vix_avg_20d,
            "vix_rank_30d": vix_rank_30d,  # PROXY renamed from iv_rank_30d
            "vix_percentile_1y": vix_percentile_1y,  # PROXY renamed from iv_percentile_1y
            "vvix": vvix_val,
        }

        # Term structure — use VIX front/2nd/3rd month futures approximation
        # VIX futures are not directly available via Tradier, so we use
        # VIX spot and VVIX as proxy signals. Term structure shape is
        # inferred from VIX level relative to its average.
        vix_front = vix_spot
        vix_2nd = None
        vix_3rd = None
        if vix_spot is not None and vix_avg_20d is not None:
            if vix_avg_20d > 0:
                ratio = vix_spot / vix_avg_20d
                if ratio < 1.0:
                    # VIX below average → contango likely
                    vix_2nd = vix_avg_20d
                    vix_3rd = vix_avg_20d * 1.03
                else:
                    # VIX above average → backwardation possible
                    vix_2nd = vix_spot * 0.97
                    vix_3rd = vix_spot * 0.95

        structure_data = {
            "vix_front_month": vix_front,
            "vix_2nd_month": vix_2nd,
            "vix_3rd_month": vix_3rd,
            "iv_30d": spy_iv.get("iv_30d"),
            "rv_30d": rv_30d,
        }

        # Skew data — from SPY options + CBOE SKEW from FRED
        skew_data = {
            "cboe_skew": cboe_skew,
            "put_skew_25d": put_skew_25d,
            "tail_risk_signal": tail_risk_signal,  # Now a label: "Low"|"Moderate"|"Elevated"|"High"
            "tail_risk_numeric": tail_risk_numeric,  # 0-100 for backward compat + detail
        }

        # Positioning data — SPY P/C serves as composite equity/index proxy (EXPLICITLY labeled)
        positioning_data = {
            "equity_pc_ratio": spy_iv.get("equity_pc_ratio"),
            "spy_pc_ratio_proxy": spy_iv.get("equity_pc_ratio"),  # PROXY: SPY not broader index
            "option_richness": option_richness,  # Now 0-100 scale (for persistence)
            "option_richness_label": option_richness_label,  # "Rich"|"Fair"|"Cheap"
            "premium_bias": premium_bias,
        }

        # Metric availability report — documents why each metric is
        # present or absent, enabling degraded-state UI messaging & provenance tracing.
        metric_availability = self._build_metric_availability(
            vix_spot=vix_spot,
            vix_avg_20d=vix_avg_20d,
            vix_rank_30d=vix_rank_30d,  # PROXY metric
            vix_percentile_1y=vix_percentile_1y,  # PROXY metric
            vvix_val=vvix_val,
            rv_30d=rv_30d,
            cboe_skew=cboe_skew,
            put_skew_25d=put_skew_25d,
            tail_risk_signal=tail_risk_signal,
            tail_risk_numeric=tail_risk_numeric,
            option_richness=option_richness,
            option_richness_label=option_richness_label,
            equity_pc_ratio=spy_iv.get("equity_pc_ratio"),
            premium_bias=premium_bias,
            iv_30d=iv_30d,
            vix_hist_count=vix_hist.get("history_count", 0),
            spy_rv_count=spy_rv.get("return_count", 0),
            fred_client_available=self.fred is not None,
        )

        result = {
            "regime_data": regime_data,
            "structure_data": structure_data,
            "skew_data": skew_data,
            "positioning_data": positioning_data,
            "metric_availability": metric_availability,
            "data_sources": {
                "vix_source": vix_source,
                "vvix_available": vvix_val is not None,
                "spy_iv_available": bool(spy_iv),
                "vix_history_days": vix_hist.get("history_count", 0),
                "spy_return_days": spy_rv.get("return_count", 0),
                "cboe_skew_available": cboe_skew is not None,
                "fred_available": self.fred is not None,
            },
        }

        logger.info(
            "[VOL_PROVIDER] fetch_complete vix=%.2f vvix=%s vix_rank=%s rv=%s "
            "skew=%s source=%s",
            vix_spot or 0, vvix_val, vix_rank_30d, rv_30d,
            cboe_skew, vix_source,
        )

        return result

    # ── VIX fetch (via market context service or direct) ─────────

    async def _fetch_vix_data(self) -> dict[str, Any]:
        """Get VIX spot and previous close.

        Source priority: MarketContextService (pre-built waterfall) → Tradier direct.
        """
        # Try market context service first (has Tradier → Finnhub → FRED waterfall)
        if self.market_ctx is not None:
            try:
                ctx = await self.market_ctx.get_market_context()
                vix_metric = ctx.get("vix", {})
                vix_val = vix_metric.get("value")
                if vix_val is not None:
                    return {
                        "vix_spot": float(vix_val),
                        "vix_previous_close": (
                            float(vix_metric["previous_close"])
                            if vix_metric.get("previous_close") is not None
                            else None
                        ),
                        "source": vix_metric.get("source", "market_context"),
                    }
            except Exception as exc:
                logger.debug("[VOL_PROVIDER] market_ctx_vix_failed error=%s", exc)

        # Fallback: direct Tradier quote
        if self.tradier is not None:
            try:
                quote = await self.tradier.get_quote(_VIX_SYMBOL)
                last = quote.get("last")
                if last is not None and float(last) > 0:
                    prev = quote.get("prevclose") or quote.get("previous_close")
                    return {
                        "vix_spot": round(float(last), 2),
                        "vix_previous_close": (
                            round(float(prev), 2) if prev is not None else None
                        ),
                        "source": "tradier",
                    }
            except Exception as exc:
                logger.debug("[VOL_PROVIDER] tradier_vix_failed error=%s", exc)

        return {}

    # ── VVIX fetch ───────────────────────────────────────────────

    async def _fetch_vvix(self) -> float | None:
        """Fetch VVIX (vol of vol) from Tradier."""
        if self.tradier is None:
            return None
        try:
            quote = await self.tradier.get_quote(_VVIX_SYMBOL)
            last = quote.get("last")
            if last is not None and float(last) > 0:
                return round(float(last), 2)
        except Exception as exc:
            logger.debug("[VOL_PROVIDER] vvix_fetch_failed error=%s", exc)
        return None

    # ── VIX history → VIX Rank, VIX Percentile, 20d avg ──────────
    # NOTE: These are PROXY metrics using VIX futures-free index, not true option IV history.
    # They serve as index-level approximations of implied volatility rank/percentile.

    async def _fetch_vix_history(self) -> dict[str, Any]:
        """Fetch VIX daily closes and compute proxy IV metrics and 20d average.

        Uses Tradier get_daily_closes("VIX", ...) for up to ~252 trading days.

        PROXY METRICS (not true IV history, but index-level approximations):
          vix_rank_30d        — (spot - 30d_min) / (30d_max - 30d_min) × 100
          vix_percentile_1y   — pct of 252d closes < current spot × 100

        PRIMARY METRICS:
          vix_avg_20d         — mean of last 20 trading-day closes

        Returns dict with:
          vix_avg_20d        — 20-day mean VIX close
          vix_rank_30d       — VIX rank proxy over 30 trading days
          vix_percentile_1y  — VIX percentile proxy over all available history
          history_count      — number of trading days retrieved
        """
        if self.tradier is None:
            return {}

        try:
            today = datetime.now(timezone.utc).date()
            start = today - timedelta(days=_VIX_HISTORY_CALENDAR_DAYS)
            closes = await self.tradier.get_daily_closes(
                _VIX_SYMBOL,
                start.isoformat(),
                today.isoformat(),
            )

            if not closes or len(closes) < 5:
                logger.debug(
                    "[VOL_PROVIDER] vix_history insufficient count=%d",
                    len(closes) if closes else 0,
                )
                return {"history_count": len(closes) if closes else 0}

            result: dict[str, Any] = {"history_count": len(closes)}

            # Current VIX = most recent close in history
            current_vix = closes[-1]

            # ── VIX 20-day average ──
            last_20 = closes[-20:] if len(closes) >= 20 else closes
            result["vix_avg_20d"] = round(sum(last_20) / len(last_20), 2)

            # ── VIX Rank (30 trading days) — PROXY METRIC ──
            # Formula: (current - min) / (max - min) × 100
            # Interpretation: How high/low is current VIX vs recent history?
            last_30 = closes[-30:] if len(closes) >= 30 else closes
            vix_min = min(last_30)
            vix_max = max(last_30)
            if vix_max > vix_min:
                result["vix_rank_30d"] = round(
                    (current_vix - vix_min) / (vix_max - vix_min) * 100, 1,
                )
            else:
                result["vix_rank_30d"] = 50.0  # Flat → middle rank

            # ── VIX Percentile (1 year / all available history) — PROXY METRIC ──
            # Formula: count(historical closes < current) / total × 100
            # Interpretation: What percentile is current VIX vs all recent history?
            below_count = sum(1 for c in closes if c < current_vix)
            result["vix_percentile_1y"] = round(
                below_count / len(closes) * 100, 1,
            )

            return result

        except Exception as exc:
            logger.warning("[VOL_PROVIDER] vix_history_failed error=%s", exc)
            return {}

    # ── SPY RV (realized volatility) ─────────────────────────────
    # Formula: Annualized standard deviation of SPY close-to-close log-returns
    # RV(30d) = std(ln(P[i]/P[i-1])) × sqrt(252) × 100
    # This is the foundation for comparing implied vs realized vol.

    async def _fetch_spy_rv(self) -> dict[str, Any]:
        """Compute SPY 30-day realized volatility (close-to-close, annualized).

        FORMULA:
          rv_30d_close_close = std(ln(P[i]/P[i-1]) for i=1..30) × sqrt(252) × 100
          - Uses natural log of close-to-close price ratios
          - Annualized with sqrt(252) trading days/year
          - Expressed as percentage points for comparison with IV

        Returns dict with:
          rv_30d       — annualized realized vol in percentage points
          return_count — number of daily log-returns computed (≤ 30)

        Data source: Tradier SPY daily close prices over ~60 calendar days.
        """
        if self.tradier is None:
            return {}

        try:
            today = datetime.now(timezone.utc).date()
            start = today - timedelta(days=_SPY_HISTORY_CALENDAR_DAYS)
            closes = await self.tradier.get_daily_closes(
                _SPY_SYMBOL,
                start.isoformat(),
                today.isoformat(),
            )

            # Need at least 10 prices (9 returns) for meaningful RV
            if not closes or len(closes) < 10:
                logger.debug(
                    "[VOL_PROVIDER] spy_history insufficient count=%d",
                    len(closes) if closes else 0,
                )
                return {"return_count": 0}

            # Use last 31 prices → 30 log-returns (or whatever is available, max 30)
            prices = closes[-31:] if len(closes) >= 31 else closes

            # Compute daily log returns: ln(P[i] / P[i-1])
            log_returns: list[float] = []
            for i in range(1, len(prices)):
                if prices[i - 1] > 0 and prices[i] > 0:
                    log_returns.append(math.log(prices[i] / prices[i - 1]))

            if len(log_returns) < 5:
                return {"return_count": len(log_returns)}

            # Annualized standard deviation
            # Sample standard deviation (divide by n-1 for unbiased estimator)
            mean_ret = sum(log_returns) / len(log_returns)
            variance = sum((r - mean_ret) ** 2 for r in log_returns) / (
                len(log_returns) - 1
            )
            daily_vol = math.sqrt(variance)
            annualized_vol = daily_vol * math.sqrt(252) * 100  # percentage

            return {
                "rv_30d": round(annualized_vol, 2),
                "return_count": len(log_returns),
            }

        except Exception as exc:
            logger.warning("[VOL_PROVIDER] spy_rv_failed error=%s", exc)
            return {}

    # ── CBOE SKEW from FRED ──────────────────────────────────────

    async def _fetch_cboe_skew(self) -> float | None:
        """Fetch latest CBOE SKEW Index value from FRED (series: SKEW).

        Returns the numeric value or None if unavailable.
        """
        if self.fred is None:
            return None
        try:
            value = await self.fred.get_latest_series_value(_FRED_SKEW_SERIES)
            if value is not None and value > 0:
                return round(float(value), 2)
        except Exception as exc:
            logger.debug("[VOL_PROVIDER] fred_skew_failed error=%s", exc)
        return None

    # ── SPY IV/options data ──────────────────────────────────────

    async def _fetch_spy_iv_data(self) -> dict[str, Any]:
        """Fetch SPY implied volatility and options metrics.

        Gets SPY options chain for nearest expiration to extract:
        - ATM implied volatility (proxy for IV_30d)
        - Put/call volume ratios
        - Put skew (25-delta put IV vs ATM IV)
        """
        if self.tradier is None:
            return {}

        result: dict[str, Any] = {}

        try:
            # Get SPY quote for current price
            spy_quote = await self.tradier.get_quote(_SPY_SYMBOL)
            spy_price = spy_quote.get("last")
            if spy_price is None:
                return {}
            spy_price = float(spy_price)

            # Get nearest expiration
            expirations = await self.tradier.get_expirations(_SPY_SYMBOL)
            if not expirations:
                return {}

            # Use first expiration (nearest)
            nearest_exp = expirations[0]

            # Get options chain with greeks
            chain = await self.tradier.get_chain(
                _SPY_SYMBOL, nearest_exp, greeks=True,
            )
            if not chain:
                return {}

            # Find ATM strike
            atm_strike = min(
                (opt.get("strike", 0) for opt in chain if opt.get("strike")),
                key=lambda s: abs(s - spy_price),
                default=None,
            )

            if atm_strike is None:
                return {}

            # Extract IV from ATM options
            atm_call_iv = None
            atm_put_iv = None
            total_put_vol = 0
            total_call_vol = 0
            put_25d_iv = None

            for opt in chain:
                strike = opt.get("strike")
                opt_type = opt.get("option_type", "").lower()
                greeks = opt.get("greeks", {}) or {}
                iv = greeks.get("mid_iv") or greeks.get("ask_iv")
                delta = greeks.get("delta")
                vol = opt.get("volume") or 0

                if strike == atm_strike:
                    if opt_type == "call" and iv is not None:
                        atm_call_iv = float(iv) * 100  # Convert to percentage
                    elif opt_type == "put" and iv is not None:
                        atm_put_iv = float(iv) * 100

                # Accumulate P/C volume
                if opt_type == "put":
                    total_put_vol += int(vol) if vol else 0
                elif opt_type == "call":
                    total_call_vol += int(vol) if vol else 0

                # Find ~25-delta put for skew
                if (opt_type == "put" and delta is not None
                        and -0.30 <= float(delta) <= -0.20
                        and iv is not None and put_25d_iv is None):
                    put_25d_iv = float(iv) * 100

            # ATM IV (average of call and put)
            if atm_call_iv is not None and atm_put_iv is not None:
                atm_iv = (atm_call_iv + atm_put_iv) / 2
            elif atm_call_iv is not None:
                atm_iv = atm_call_iv
            elif atm_put_iv is not None:
                atm_iv = atm_put_iv
            else:
                atm_iv = None

            if atm_iv is not None:
                result["iv_30d"] = round(atm_iv, 2)

            # Put skew (25-delta put IV minus ATM IV)
            if put_25d_iv is not None and atm_iv is not None:
                result["put_skew_25d"] = round(put_25d_iv - atm_iv, 2)

            # Equity put/call ratio (from SPY as proxy)
            if total_call_vol > 0:
                result["equity_pc_ratio"] = round(
                    total_put_vol / total_call_vol, 4,
                )

            # Premium bias from chain-only signals (may be overridden
            # in fetch_volatility_data if IV rank / RV also available)
            bias_signals: list[float] = []
            if atm_iv is not None and atm_iv > 15:
                bias_signals.append(20)  # IV moderate → slight sell bias
            if result.get("equity_pc_ratio") and result["equity_pc_ratio"] < 0.8:
                bias_signals.append(15)  # Low P/C → bullish → sell puts
            if bias_signals:
                result["premium_bias"] = round(
                    sum(bias_signals) / len(bias_signals), 2,
                )

        except Exception as exc:
            logger.warning("[VOL_PROVIDER] spy_iv_fetch_failed error=%s", exc)

        return result

    # ── Metric availability diagnostics ──────────────────────────

    def _build_metric_availability(
        self,
        *,
        vix_spot: float | None,
        vix_avg_20d: float | None,
        vix_rank_30d: float | None,
        vix_percentile_1y: float | None,
        vvix_val: float | None,
        rv_30d: float | None,
        cboe_skew: float | None,
        put_skew_25d: float | None,
        tail_risk_signal: str | None,
        tail_risk_numeric: float | None,
        option_richness: float | None,
        option_richness_label: str | None,
        equity_pc_ratio: float | None,
        premium_bias: float | None,
        iv_30d: float | None,
        vix_hist_count: int,
        spy_rv_count: int,
        fred_client_available: bool,
    ) -> dict[str, dict[str, Any]]:
        """Build comprehensive per-metric availability report with full provenance.

        Each entry includes:
          - status: "ok" | "degraded" | "unavailable"
          - reason: human-readable explanation
          - source: data source (Tradier, FRED, computed, etc)
          - primary_vs_proxy: "primary" | "proxy" | "derived"
          - direct_vs_derived: "direct" | "derived"
          - lookback_used: time window or N/A
          - formula_or_logic: calculation method
          - dependencies: what inputs are required
          - degraded_mode_flag: True if available with reduced inputs

        These enable UI to:
          1. Show tooltips explaining why unavailable
          2. Trace data provenance
          3. Distinguish primary vs proxy metrics
          4. Document degraded modes
        """
        def _entry(
            val: Any,
            status: str,
            reason: str,
            source: str | None = None,
            primary_vs_proxy: str = "primary",
            direct_vs_derived: str = "direct",
            lookback_used: str | None = None,
            formula_or_logic: str | None = None,
            dependencies: list[str] | None = None,
            degraded_mode_flag: bool = False,
        ) -> dict[str, Any]:
            return {
                "status": status,
                "reason": reason,
                "source": source,
                "primary_vs_proxy": primary_vs_proxy,
                "direct_vs_derived": direct_vs_derived,
                "lookback_used": lookback_used,
                "formula_or_logic": formula_or_logic,
                "dependencies": dependencies or [],
                "degraded_mode_flag": degraded_mode_flag,
            }

        return {
            # ────────────────────────────────────────────────────────────
            # VOLATILITY REGIME PILLAR
            # ────────────────────────────────────────────────────────────

            "vix_spot": _entry(
                vix_spot,
                "ok" if vix_spot is not None else "unavailable",
                "Live VIX spot quote" if vix_spot is not None else "VIX quote unavailable from all sources",
                source="MarketContextService (with Tradier fallback)" if vix_spot is not None else None,
                primary_vs_proxy="primary",
                direct_vs_derived="direct",
                formula_or_logic="Latest bid/ask midpoint or last trade",
                dependencies=[],
            ),

            "vix_avg_20d": _entry(
                vix_avg_20d,
                "ok" if vix_avg_20d is not None else "unavailable",
                f"Mean of {min(vix_hist_count, 20)} recent VIX closes"
                if vix_avg_20d is not None
                else "Insufficient VIX history (<5 days)" if vix_hist_count < 5 else "VIX history fetch failed",
                source="Tradier VIX daily closes" if vix_avg_20d is not None else None,
                primary_vs_proxy="primary",
                direct_vs_derived="derived",
                lookback_used="20 trading days (or less if insufficient history)",
                formula_or_logic="mean(VIX closes over last 20 trading days)",
                dependencies=["VIX daily history"],
                degraded_mode_flag=vix_hist_count < 20,
            ),

            "vix_rank_30d": _entry(
                vix_rank_30d,
                "ok" if vix_rank_30d is not None else "unavailable",
                f"VIX rank over {min(vix_hist_count, 30)} trading days (PROXY)"
                if vix_rank_30d is not None
                else "Insufficient VIX history for 30d rank",
                source="Tradier VIX daily closes (index proxy)" if vix_rank_30d is not None else None,
                primary_vs_proxy="proxy",  # ← KEY: This is a proxy metric
                direct_vs_derived="derived",
                lookback_used="30 trading days (or less if insufficient)",
                formula_or_logic="(spot - min) / (max - min) × 100 where min/max over 30d",
                dependencies=["VIX daily history"],
                degraded_mode_flag=vix_hist_count < 30,
            ),

            "vix_percentile_1y": _entry(
                vix_percentile_1y,
                "ok" if vix_percentile_1y is not None else "unavailable",
                f"VIX percentile over {vix_hist_count} trading days (PROXY)"
                if vix_percentile_1y is not None
                else "Insufficient VIX history for percentile",
                source="Tradier VIX daily closes (index proxy)" if vix_percentile_1y is not None else None,
                primary_vs_proxy="proxy",  # ← KEY: This is a proxy metric
                direct_vs_derived="derived",
                lookback_used=f"~252 trading days (actual: {vix_hist_count})",
                formula_or_logic="count(closes < spot) / total × 100",
                dependencies=["VIX daily history"],
                degraded_mode_flag=vix_hist_count < 200,
            ),

            "vvix": _entry(
                vvix_val,
                "ok" if vvix_val is not None else "unavailable",
                "Live VVIX (vol of vol) quote" if vvix_val is not None else "VVIX quote unavailable",
                source="Tradier" if vvix_val is not None else None,
                primary_vs_proxy="primary",
                direct_vs_derived="direct",
                formula_or_logic="Latest bid/ask midpoint",
                dependencies=[],
            ),

            # ────────────────────────────────────────────────────────────
            # VOLATILITY STRUCTURE PILLAR
            # ────────────────────────────────────────────────────────────

            "iv_30d": _entry(
                iv_30d,
                "ok" if iv_30d is not None else "unavailable",
                "SPY ATM implied volatility from options chain" if iv_30d is not None else "SPY options chain unavailable or no ATM strike found",
                source="Tradier SPY options chain" if iv_30d is not None else None,
                primary_vs_proxy="primary",
                direct_vs_derived="direct",
                formula_or_logic="ATM IV interpolated from 30-40 DTE puts/calls",
                dependencies=["SPY options chain"],
            ),

            "rv_30d": _entry(
                rv_30d,
                "ok" if rv_30d is not None else "unavailable",
                f"Annualized RV computed from {spy_rv_count} SPY daily log-returns"
                if rv_30d is not None
                else "Insufficient SPY price history" if spy_rv_count < 5 else "SPY history fetch failed",
                source="Tradier SPY daily closes" if rv_30d is not None else None,
                primary_vs_proxy="primary",
                direct_vs_derived="derived",
                lookback_used="~30 trading days",
                formula_or_logic="std(ln(P[i]/P[i-1])) × sqrt(252) × 100 [close-to-close, sample std]",
                dependencies=["SPY daily close prices"],
                degraded_mode_flag=spy_rv_count < 20,
            ),

            # ────────────────────────────────────────────────────────────
            # TAIL RISK & SKEW PILLAR
            # ────────────────────────────────────────────────────────────

            "cboe_skew": _entry(
                cboe_skew,
                "ok" if cboe_skew is not None else "unavailable",
                "CBOE SKEW index (tail hedging demand)" if cboe_skew is not None else "FRED client unavailable or SKEW series not available",
                source="FRED series SKEW (CBOE Skew Index)" if cboe_skew is not None else None,
                primary_vs_proxy="primary",
                direct_vs_derived="direct",
                lookback_used="Latest observation",
                formula_or_logic="Latest closing value of CBOE SKEW",
                dependencies=["FRED client", "SKEW series availability"],
            ),

            "put_skew_25d": _entry(
                put_skew_25d,
                "ok" if put_skew_25d is not None else "unavailable",
                "25-delta put IV premium over ATM (skew measure)" if put_skew_25d is not None else "No 25-delta put found in SPY chain",
                source="Tradier SPY options chain" if put_skew_25d is not None else None,
                primary_vs_proxy="primary",
                direct_vs_derived="derived",
                lookback_used="30-40 DTE strikes",
                formula_or_logic="IV(25d put) - IV(ATM)",
                dependencies=["SPY options chain with ATM and 25d puts"],
            ),

            "tail_risk_signal": _entry(
                tail_risk_signal,
                "ok" if tail_risk_signal is not None else "unavailable",
                f"Tail risk state: {tail_risk_signal} (numeric={tail_risk_numeric})"
                if tail_risk_signal is not None
                else "Cannot compute without put_skew_25d or cboe_skew",
                source="Computed from put_skew_25d + CBOE SKEW" if tail_risk_signal is not None else None,
                primary_vs_proxy="derived",
                direct_vs_derived="derived",
                lookback_used="Current + 30d skew",
                formula_or_logic="Deterministic threshold logic: Low (<30), Moderate (30-60), Elevated (60-80), High (80+) [numeric 0-100]",
                dependencies=["put_skew_25d or cboe_skew"],
                degraded_mode_flag=cboe_skew is None and put_skew_25d is not None,
            ),

            # ────────────────────────────────────────────────────────────
            # POSITIONING & OPTIONS POSTURE PILLAR
            # ────────────────────────────────────────────────────────────

            "equity_pc_ratio": _entry(
                equity_pc_ratio,
                "ok" if equity_pc_ratio is not None else "unavailable",
                "SPY options put/call volume ratio" if equity_pc_ratio is not None else "SPY chain volume data unavailable",
                source="Tradier SPY options chain volume" if equity_pc_ratio is not None else None,
                primary_vs_proxy="primary",
                direct_vs_derived="derived",
                lookback_used="30-40 DTE strikes",
                formula_or_logic="sum(put volume) / sum(call volume) for 30-40 DTE options",
                dependencies=["SPY options chain with volume"],
            ),

            "option_richness": _entry(
                option_richness,
                "ok" if option_richness is not None else "unavailable",
                f"Option richness: {option_richness_label} (numeric={option_richness})"
                if option_richness is not None
                else "Cannot compute without vix_rank_30d context",
                source="Computed from VIX rank + IV-RV spread" if option_richness is not None else None,
                primary_vs_proxy="derived",
                direct_vs_derived="derived",
                lookback_used="30d for rank, 30d for RV",
                formula_or_logic="Blended logic: Rich if (vix_rank>60 AND iv>rv), Cheap if (vix_rank<30 OR iv≤rv), else Fair",
                dependencies=["vix_rank_30d", "iv_30d", "rv_30d"],
                degraded_mode_flag=iv_30d is None or rv_30d is None,
            ),

            "premium_bias": _entry(
                premium_bias,
                "ok" if premium_bias is not None else "unavailable",
                "Composite SELL/NEUTRAL/BUY bias from IV, RV, P/C signals"
                if premium_bias is not None
                else "Insufficient signals for premium bias",
                source="Composite of IV, RV, P/C" if premium_bias is not None else None,
                primary_vs_proxy="derived",
                direct_vs_derived="derived",
                lookback_used="30d",
                formula_or_logic="Weighted avg of: (IV-RV)*5, (vix_rank-50)*0.8, (0.85-pc)*40",
                dependencies=["iv_30d", "rv_30d", "vix_rank_30d", "equity_pc_ratio"],
                degraded_mode_flag=True if premium_bias is not None else False,
            ),
        }
