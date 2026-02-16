from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base_data_service import BaseDataService
from app.utils.cache import TTLCache
from common.quant_analysis import expected_move, realized_vol_annualized, rsi, simple_moving_average


DEFAULT_SIGNAL_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "NFLX", "JPM", "XLF", "XLK", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLRE", "XLU", "XLC",
]


class SignalService:
    def __init__(self, base_data_service: BaseDataService, cache: TTLCache, *, ttl_seconds: int = 45) -> None:
        self.base_data_service = base_data_service
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, "", "."):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _ema(values: list[float], period: int) -> float | None:
        if period <= 0 or len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period
        for value in values[period:]:
            ema_val = (value * k) + (ema_val * (1 - k))
        return ema_val

    @staticmethod
    def _range_to_points(range_key: str) -> int:
        mapping = {"1mo": 22, "3mo": 66, "6mo": 132, "1y": 252}
        return mapping.get(str(range_key or "").lower(), 132)

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    async def _estimate_iv_and_move(self, symbol: str, spot: float | None, rv20d: float | None) -> tuple[float | None, float | None, float | None, int | None]:
        if spot is None:
            return None, None, None, None

        try:
            expirations = await self.base_data_service.tradier_client.get_expirations(symbol)
            expiration = str(expirations[0]) if expirations else None
        except Exception:
            expiration = None

        if not expiration:
            return None, None, None, None

        try:
            inputs = await self.base_data_service.get_analysis_inputs(symbol, expiration, include_prices_history=False)
            contracts = inputs.get("contracts") or []
            underlying = self._safe_float(inputs.get("underlying_price")) or spot

            iv_candidates: list[tuple[float, float]] = []
            if underlying is not None:
                for contract in contracts:
                    strike = self._safe_float(getattr(contract, "strike", None))
                    iv_val = self._safe_float(getattr(contract, "iv", None))
                    if strike is None or iv_val is None:
                        continue
                    iv_candidates.append((abs(strike - underlying), iv_val))

            if not iv_candidates:
                return None, None, None, None

            iv_candidates.sort(key=lambda x: x[0])
            nearest = [iv for _, iv in iv_candidates[:6]]
            iv = (sum(nearest) / len(nearest)) if nearest else None

            dte = None
            try:
                exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
                dte = (exp_date - datetime.now(timezone.utc).date()).days
            except Exception:
                dte = None

            em = None
            if iv is not None and underlying is not None and dte is not None and dte > 0:
                try:
                    em = expected_move(underlying, iv, dte)
                except Exception:
                    em = None

            iv_rv_ratio = None
            if iv is not None and rv20d not in (None, 0):
                iv_rv_ratio = iv / rv20d

            return iv, iv_rv_ratio, em, dte
        except Exception:
            return None, None, None, None

    async def _compute_symbol(self, symbol: str, range_key: str = "6mo") -> dict[str, Any]:
        ticker = str(symbol or "SPY").strip().upper() or "SPY"
        points = self._range_to_points(range_key)

        history_full = await self.base_data_service.get_prices_history(ticker, lookback_days=365)
        history_full = [float(x) for x in (history_full or []) if x is not None]
        history = history_full[-points:] if history_full else []

        last = history[-1] if history else None
        ema20 = self._ema(history, 20) if history else None
        sma50 = simple_moving_average(history, 50) if history else None
        sma200 = simple_moving_average(history, 200) if history else None
        rsi14 = rsi(history, 14) if history else None
        rv20d = realized_vol_annualized(history[-21:]) if len(history) >= 21 else None

        iv, iv_rv_ratio, em, dte = await self._estimate_iv_and_move(ticker, last, rv20d)

        high_252 = max(history_full[-252:]) if len(history_full) >= 1 else None
        drawdown = ((last / high_252) - 1.0) if (last not in (None, 0) and high_252 not in (None, 0)) else None

        signals: list[dict[str, Any]] = []

        trend_up = bool(last is not None and ema20 is not None and sma50 is not None and sma200 is not None and last > ema20 and sma50 > sma200)
        trend_down = bool(last is not None and ema20 is not None and sma50 is not None and sma200 is not None and last < ema20 and sma50 < sma200)
        trend_strength = 0.0
        if last is not None and ema20 not in (None, 0):
            trend_strength = self._clamp(abs((last - ema20) / ema20), 0.0, 0.12) / 0.12

        signals.append({"id": "trend_up", "value": trend_up, "strength": round(trend_strength if trend_up else 1.0 - trend_strength, 3), "why": "Price vs EMA20 and SMA50 vs SMA200"})
        signals.append({"id": "trend_down", "value": trend_down, "strength": round(trend_strength if trend_down else 1.0 - trend_strength, 3), "why": "Price below EMA20 and SMA50 below SMA200"})

        momentum_strong = bool(rsi14 is not None and 50 <= rsi14 <= 65)
        momentum_weak = bool(rsi14 is not None and (rsi14 < 40 or rsi14 > 75))
        momentum_strength = 0.0 if rsi14 is None else self._clamp(1.0 - (abs(rsi14 - 57.5) / 35.0), 0.0, 1.0)

        signals.append({"id": "momentum_strong", "value": momentum_strong, "strength": round(momentum_strength, 3), "why": "RSI in constructive band"})
        signals.append({"id": "momentum_weak", "value": momentum_weak, "strength": round(1.0 - momentum_strength, 3), "why": "RSI outside constructive band"})

        vol_high = bool(rv20d is not None and rv20d > 0.45)
        vol_low = bool(rv20d is not None and rv20d < 0.15)
        vol_strength = 0.0 if rv20d is None else self._clamp(rv20d / 0.6, 0.0, 1.0)

        signals.append({"id": "vol_high", "value": vol_high, "strength": round(vol_strength, 3), "why": "RV20d elevated"})
        signals.append({"id": "vol_low", "value": vol_low, "strength": round(1.0 - vol_strength, 3), "why": "RV20d subdued"})

        iv_rv_rich = bool(iv_rv_ratio is not None and iv_rv_ratio > 1.2)
        iv_rv_cheap = bool(iv_rv_ratio is not None and iv_rv_ratio < 0.9)
        ivrv_strength = 0.0 if iv_rv_ratio is None else self._clamp(abs(iv_rv_ratio - 1.0) / 0.6, 0.0, 1.0)

        signals.append({"id": "iv_rv_rich", "value": iv_rv_rich, "strength": round(ivrv_strength if iv_rv_rich else 1.0 - ivrv_strength, 3), "why": "IV/RV ratio above rich threshold"})
        signals.append({"id": "iv_rv_cheap", "value": iv_rv_cheap, "strength": round(ivrv_strength if iv_rv_cheap else 1.0 - ivrv_strength, 3), "why": "IV/RV ratio below cheap threshold"})

        stretch = None
        if last is not None and ema20 not in (None, 0):
            stretch = (last - ema20) / ema20
        mean_reversion_zone = bool(stretch is not None and abs(stretch) > 0.04)
        mr_strength = 0.0 if stretch is None else self._clamp(abs(stretch) / 0.1, 0.0, 1.0)
        signals.append({"id": "mean_reversion_zone", "value": mean_reversion_zone, "strength": round(mr_strength, 3), "why": "Price stretched from EMA20"})

        drawdown_warning = bool((last is not None and sma200 is not None and last < sma200) or (drawdown is not None and drawdown < -0.12))
        dd_strength = 0.0 if drawdown is None else self._clamp(abs(min(drawdown, 0.0)) / 0.25, 0.0, 1.0)
        signals.append({"id": "drawdown_warning", "value": drawdown_warning, "strength": round(dd_strength, 3), "why": "Price below long trend or deep drawdown"})

        trend_score = 0.0
        if last is not None and ema20 is not None:
            trend_score += 50.0 if last > ema20 else 10.0
        if sma50 is not None and sma200 is not None:
            trend_score += 50.0 if sma50 > sma200 else 15.0
        trend_score = self._clamp(trend_score, 0.0, 100.0)

        momentum_score = 50.0
        if rsi14 is not None:
            if 45 <= rsi14 <= 65:
                momentum_score = 100.0
            elif 40 <= rsi14 <= 75:
                momentum_score = 65.0
            else:
                momentum_score = 25.0

        vol_score = 50.0
        if rv20d is not None:
            if 0.15 <= rv20d <= 0.40:
                vol_score = 100.0
            elif rv20d < 0.10 or rv20d > 0.55:
                vol_score = 30.0
            else:
                vol_score = 65.0

        ivrv_score = 50.0
        if iv_rv_ratio is not None:
            if 1.1 <= iv_rv_ratio <= 1.8:
                ivrv_score = 100.0
            elif 0.9 <= iv_rv_ratio < 1.1:
                ivrv_score = 70.0
            else:
                ivrv_score = 35.0

        composite_score = (0.35 * trend_score) + (0.25 * momentum_score) + (0.20 * vol_score) + (0.20 * ivrv_score)
        composite_score = self._clamp(composite_score, 0.0, 100.0)

        label = "Neutral"
        if composite_score >= 70:
            label = "Strong"
        elif composite_score < 45:
            label = "Weak"

        return {
            "as_of": self._now_iso(),
            "symbol": ticker,
            "signals": signals,
            "metrics": {
                "ema20": ema20,
                "sma50": sma50,
                "sma200": sma200,
                "rsi14": rsi14,
                "rv20d": rv20d,
                "iv": iv,
                "iv_rv_ratio": iv_rv_ratio,
                "expected_move": em,
                "dte": dte,
                "drawdown": drawdown,
            },
            "composite": {
                "score": round(composite_score, 2),
                "label": label,
            },
        }

    async def get_symbol_signals(self, symbol: str, range_key: str = "6mo") -> dict[str, Any]:
        key = f"signals:v1:{str(symbol or 'SPY').upper()}:{str(range_key or '6mo').lower()}"
        return await self.cache.get_or_set(key, self.ttl_seconds, lambda: self._compute_symbol(symbol, range_key))

    async def get_universe_signals(self, universe: str = "default", range_key: str = "6mo") -> dict[str, Any]:
        universe_key = str(universe or "default").strip().lower()
        symbols = list(DEFAULT_SIGNAL_UNIVERSE)

        seen: set[str] = set()
        normalized: list[str] = []
        for item in symbols:
            sym = str(item or "").strip().upper()
            if not sym or sym in seen:
                continue
            normalized.append(sym)
            seen.add(sym)

        rows = await self._gather_universe(normalized, range_key)
        rows.sort(key=lambda row: float((row.get("composite") or {}).get("score") or 0.0), reverse=True)

        return {
            "as_of": self._now_iso(),
            "universe": universe_key,
            "items": rows,
        }

    async def _gather_universe(self, symbols: list[str], range_key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for symbol in symbols:
            try:
                out.append(await self.get_symbol_signals(symbol, range_key))
            except Exception:
                continue
        return out
