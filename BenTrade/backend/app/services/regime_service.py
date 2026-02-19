from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base_data_service import BaseDataService
from app.utils.cache import TTLCache
from app.utils.http import request_json
from common.quant_analysis import rsi, simple_moving_average


class RegimeService:
    def __init__(
        self,
        base_data_service: BaseDataService,
        cache: TTLCache,
        *,
        ttl_seconds: int = 45,
    ) -> None:
        self.base_data_service = base_data_service
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _ema(prices: list[float], period: int) -> float | None:
        if period <= 0 or len(prices) < period:
            return None
        k = 2.0 / (period + 1.0)
        value = sum(prices[:period]) / period
        for price in prices[period:]:
            value = (price * k) + (value * (1.0 - k))
        return value

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, "", "."):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _mark_fred_success(self, message: str) -> None:
        self.base_data_service._mark_success("fred", http_status=200, message=message)

    def _mark_fred_failure(self, err: Exception) -> None:
        self.base_data_service._mark_failure("fred", err)

    async def _fred_recent_values(self, series_id: str, count: int) -> list[float]:
        fred = self.base_data_service.fred_client
        try:
            payload = await request_json(
                fred.http_client,
                "GET",
                f"{fred.settings.FRED_BASE_URL}/series/observations",
                params={
                    "series_id": series_id,
                    "sort_order": "desc",
                    "limit": max(2, int(count)),
                    "api_key": fred.settings.FRED_KEY,
                    "file_type": "json",
                },
            )
            obs = payload.get("observations") or []
            out: list[float] = []
            for row in obs:
                value = self._safe_float(row.get("value"))
                if value is None:
                    continue
                out.append(value)
            self._mark_fred_success(f"series {series_id} ok")
            return out
        except Exception as exc:
            self._mark_fred_failure(exc)
            return []

    @staticmethod
    def _bounded(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _normalize_component(points: float, max_points: float) -> float:
        if max_points <= 0:
            return 0.0
        return max(0.0, min(100.0, (points / max_points) * 100.0))

    async def _compute(self) -> dict[str, Any]:
        notes: list[str] = []

        spy_snapshot = await self.base_data_service.get_snapshot("SPY")
        spy_history = []
        try:
            history_full = await self.base_data_service.get_prices_history("SPY", lookback_days=365)
            spy_history = [float(x) for x in (history_full or []) if x is not None]
        except Exception:
            spy_history = [float(x) for x in (spy_snapshot.get("prices_history") or []) if x is not None]
            notes.append("Trend: SPY full history unavailable; using snapshot history")

        if len(spy_history) < 200:
            notes.append("Insufficient history for SMA200")

        spy_last = self._safe_float(spy_snapshot.get("underlying_price"))
        if spy_last is None and spy_history:
            spy_last = spy_history[-1]

        ema20 = self._ema(spy_history, 20)
        ema50 = self._ema(spy_history, 50)
        sma50 = simple_moving_average(spy_history, 50)
        sma200 = simple_moving_average(spy_history, 200)
        rsi14 = rsi(spy_history, 14)

        trend_points = 0.0
        trend_signals: list[str] = []
        trend_max = 25.0
        trend_available_points = 0.0

        close_gt_ema20: bool | None = None
        close_gt_ema50: bool | None = None
        sma50_gt_sma200: bool | None = None

        if spy_last is not None and ema20 is not None:
            trend_available_points += 10.0
            close_gt_ema20 = spy_last > ema20
            if close_gt_ema20:
                trend_points += 10.0
            trend_signals.append(f"Close {spy_last:.2f} {'>' if close_gt_ema20 else '<='} EMA20 {ema20:.2f}")
        else:
            notes.append("Trend: EMA20 unavailable")
            trend_signals.append("Close/EMA20 unavailable")

        if spy_last is not None and ema50 is not None:
            trend_available_points += 5.0
            close_gt_ema50 = spy_last > ema50
            if close_gt_ema50:
                trend_points += 5.0
            trend_signals.append(f"Close {spy_last:.2f} {'>' if close_gt_ema50 else '<='} EMA50 {ema50:.2f}")
        else:
            notes.append("Trend: EMA50 unavailable")
            trend_signals.append("Close/EMA50 unavailable")

        if sma50 is not None and sma200 is not None:
            trend_available_points += 10.0
            sma50_gt_sma200 = sma50 > sma200
            if sma50_gt_sma200:
                trend_points += 10.0
            trend_signals.append(f"SMA50 {sma50:.2f} {'>' if sma50_gt_sma200 else '<='} SMA200 {sma200:.2f}")
        else:
            notes.append("Trend: SMA50/SMA200 unavailable")
            trend_signals.append("SMA50/SMA200 unavailable")

        trend_available = trend_available_points > 0
        trend_score = self._normalize_component(trend_points, trend_available_points if trend_available_points > 0 else trend_max)

        vix_recent = await self._fred_recent_values(self.base_data_service.fred_client.settings.FRED_VIX_SERIES_ID, 6)
        vix_now = vix_recent[0] if vix_recent else self._safe_float(spy_snapshot.get("vix"))
        vix_5d_prev = vix_recent[5] if len(vix_recent) > 5 else None
        vix_5d_change = ((vix_now - vix_5d_prev) / vix_5d_prev) if (vix_now is not None and vix_5d_prev not in (None, 0)) else None

        vol_points = 0.0
        vol_max = 25.0
        vol_signals: list[str] = []
        vol_available = vix_now is not None

        if vix_now is None:
            notes.append("Volatility: VIX unavailable")
        else:
            if vix_now < 18:
                vol_points += 25.0
                vol_signals.append("VIX < 18 (+25)")
            elif vix_now <= 25:
                vol_points += 12.0
                vol_signals.append("VIX 18-25 (+12)")
            else:
                vol_signals.append("VIX > 25 (+0)")

            if vix_5d_change is not None and vix_5d_change > 0.10:
                vol_points -= 5.0
                vol_signals.append("VIX up >10% in 5D (-5)")

        vol_points = self._bounded(vol_points, 0.0, vol_max)

        sector_symbols = ["XLF", "XLK", "XLE", "XLY", "XLP", "XLV", "XLI", "XLB", "XLRE", "XLU", "XLC"]
        sector_above = 0
        sector_valid = 0
        breadth_signals: list[str] = []

        for symbol in sector_symbols:
            history = await self.base_data_service.get_prices_history(symbol, lookback_days=365)
            prices = [float(x) for x in (history or []) if x is not None]
            if not prices:
                continue
            last = prices[-1]
            sector_ema20 = self._ema(prices, 20)
            if sector_ema20 is None:
                continue
            sector_valid += 1
            if last > sector_ema20:
                sector_above += 1

        breadth_max = 25.0
        breadth_available = sector_valid > 0
        pct_above = (sector_above / sector_valid) if sector_valid else 0.0
        breadth_points = pct_above * breadth_max
        breadth_signals.append(f"{sector_above}/{sector_valid} sectors above EMA20")
        if not breadth_available:
            notes.append("Breadth: sector EMA data unavailable")

        ten_year_recent = await self._fred_recent_values("DGS10", 6)
        ten_year_now = ten_year_recent[0] if ten_year_recent else None
        ten_year_5d_prev = ten_year_recent[5] if len(ten_year_recent) > 5 else None
        ten_year_delta_bps = ((ten_year_now - ten_year_5d_prev) * 100.0) if (ten_year_now is not None and ten_year_5d_prev is not None) else None

        rates_points = 15.0
        rates_max = 15.0
        rates_available = ten_year_now is not None
        rates_signals: list[str] = []

        if ten_year_now is None:
            rates_points = 0.0
            notes.append("Rates: 10Y yield unavailable")
        else:
            rates_signals.append(f"10Y now {ten_year_now:.2f}%")
            if ten_year_delta_bps is not None and ten_year_delta_bps > 15:
                penalty = 10.0 if ten_year_delta_bps > 25 else 7.0
                rates_points -= penalty
                rates_signals.append(f"10Y +{ten_year_delta_bps:.1f}bps in 5D (-{penalty:.0f})")
            elif ten_year_delta_bps is not None and ten_year_delta_bps > 8:
                rates_points -= 5.0
                rates_signals.append(f"10Y +{ten_year_delta_bps:.1f}bps in 5D (-5)")

        rates_points = self._bounded(rates_points, 0.0, rates_max)

        momentum_max = 10.0
        momentum_available = rsi14 is not None
        momentum_signals: list[str] = []
        momentum_points = 0.0

        if rsi14 is None:
            notes.append("Momentum: RSI14 unavailable")
        else:
            if 45 <= rsi14 <= 65:
                momentum_points = momentum_max
                momentum_signals.append("RSI in ideal band 45-65 (+10)")
            else:
                distance = min(abs(rsi14 - 45), abs(rsi14 - 65))
                scale = max(0.0, 1.0 - min(distance, 25.0) / 25.0)
                momentum_points = momentum_max * scale
                momentum_signals.append(f"RSI outside ideal band ({rsi14:.1f})")

        components = {
            "trend": {
                "score": trend_score,
                "raw_points": trend_points,
                "signals": trend_signals,
                "inputs": {
                    "close": spy_last,
                    "ema20": ema20,
                    "ema50": ema50,
                    "sma50": sma50,
                    "sma200": sma200,
                    "close_gt_ema20": close_gt_ema20,
                    "close_gt_ema50": close_gt_ema50,
                    "sma50_gt_sma200": sma50_gt_sma200,
                },
            },
            "volatility": {
                "score": self._normalize_component(vol_points, vol_max),
                "signals": vol_signals,
                "inputs": {
                    "vix": vix_now,
                    "vix_5d_change": vix_5d_change,
                },
            },
            "breadth": {
                "score": self._normalize_component(breadth_points, breadth_max),
                "signals": breadth_signals,
                "inputs": {
                    "sectors_above_ema20": sector_above,
                    "sectors_total": sector_valid,
                    "pct_above_ema20": pct_above,
                },
            },
            "rates": {
                "score": self._normalize_component(rates_points, rates_max),
                "signals": rates_signals,
                "inputs": {
                    "ten_year_yield": ten_year_now,
                    "ten_year_5d_change_bps": ten_year_delta_bps,
                },
            },
            "momentum": {
                "score": self._normalize_component(momentum_points, momentum_max),
                "signals": momentum_signals,
                "inputs": {
                    "rsi14": rsi14,
                },
            },
        }

        available_max = 0.0
        raw_points = 0.0

        if trend_available:
            available_max += trend_available_points
            raw_points += trend_points
        if vol_available:
            available_max += vol_max
            raw_points += vol_points
        if breadth_available:
            available_max += breadth_max
            raw_points += breadth_points
        if rates_available:
            available_max += rates_max
            raw_points += rates_points
        if momentum_available:
            available_max += momentum_max
            raw_points += momentum_points

        if available_max <= 0:
            regime_score = 50.0
            notes.append("No regime inputs available; defaulting score to neutral baseline")
        else:
            regime_score = self._bounded((raw_points / available_max) * 100.0, 0.0, 100.0)
            if available_max < 100.0:
                notes.append(f"Partial data coverage: {available_max:.0f}/100 points available")

        if regime_score >= 65:
            label = "RISK_ON"
            playbook = {
                "primary": ["put_credit_spread", "covered_call", "call_debit"],
                "avoid": ["short_gamma", "debit_butterfly"],
                "notes": [
                    "Favor bullish premium-selling structures with defined risk",
                    "Use selective directional long-premium only with strong trend continuation",
                ],
            }
        elif regime_score >= 40:
            label = "NEUTRAL"
            playbook = {
                "primary": ["iron_condor", "credit_spread_wider_distance", "calendar"],
                "avoid": ["high_conviction_directional_bets"],
                "notes": [
                    "Favor range-aware structures and balanced risk",
                    "Widen short strikes and tighten entry quality filters",
                ],
            }
        else:
            label = "RISK_OFF"
            playbook = {
                "primary": ["put_debit", "cash", "hedges"],
                "avoid": ["short_puts_near_spot", "short_gamma"],
                "notes": [
                    "Reduce net short downside exposure",
                    "Prioritize convex downside protection and smaller risk units",
                ],
            }

        if notes:
            playbook["notes"] = playbook.get("notes", []) + notes

        return {
            "as_of": self._now_iso(),
            "regime_label": label,
            "regime_score": round(regime_score, 2),
            "components": components,
            "suggested_playbook": playbook,
            "source_health": self.base_data_service.get_source_health_snapshot(),
        }

    async def get_regime(self) -> dict[str, Any]:
        return await self.cache.get_or_set("regime:v1", self.ttl_seconds, self._compute)
