from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from app.services.base_data_service import BaseDataService
from common.quant_analysis import expected_move, realized_vol_annualized, rsi, simple_moving_average


DEFAULT_SCANNER_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "NFLX", "JPM", "XLF", "XLK", "XLE", "XLV",
]


LIQUIDITY_BONUS = {
    "SPY": 2.0,
    "QQQ": 2.0,
    "IWM": 1.6,
    "DIA": 1.4,
    "AAPL": 1.8,
    "MSFT": 1.8,
    "NVDA": 1.7,
    "AMZN": 1.5,
    "META": 1.5,
    "GOOGL": 1.4,
    "TSLA": 1.4,
    "AMD": 1.3,
    "NFLX": 1.1,
    "JPM": 1.2,
    "XLF": 1.2,
    "XLK": 1.2,
    "XLE": 1.0,
    "XLV": 1.0,
}


class StockAnalysisService:
    def __init__(self, base_data_service: BaseDataService, results_dir: Path | None = None, signal_service: Any | None = None) -> None:
        self.base_data_service = base_data_service
        self.results_dir = results_dir
        self.signal_service = signal_service
        self._lock = RLock()
        self.watchlist_path = (results_dir / "stock_watchlist.json") if results_dir else None

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _range_to_points(range_key: str) -> int:
        mapping = {
            "1mo": 22,
            "3mo": 66,
            "6mo": 132,
            "1y": 252,
        }
        return mapping.get(str(range_key or "").lower(), 132)

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, ""):
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
        for px in values[period:]:
            ema_val = (px * k) + (ema_val * (1 - k))
        return ema_val

    @staticmethod
    def _select_expiration(expirations: list[str]) -> str | None:
        if not expirations:
            return None
        today = datetime.now(timezone.utc).date()

        parsed: list[tuple[str, Any]] = []
        for exp in expirations:
            try:
                parsed.append((exp, datetime.strptime(exp, "%Y-%m-%d").date()))
            except Exception:
                continue

        if not parsed:
            return expirations[0]

        parsed.sort(key=lambda item: item[1])
        future = [item for item in parsed if item[1] >= today]
        return (future[0] if future else parsed[0])[0]

    @staticmethod
    def _trend(last: float | None, sma20: float | None, sma50: float | None) -> str:
        if last is None or sma20 is None or sma50 is None:
            return "range"
        if sma20 > sma50 and last >= sma20:
            return "up"
        if sma20 < sma50 and last <= sma20:
            return "down"
        return "range"

    @staticmethod
    def _normalize_symbol(symbol: Any) -> str:
        value = str(symbol or "").strip().upper()
        cleaned = "".join(ch for ch in value if ch.isalnum() or ch in (".", "-"))
        return cleaned[:12]

    @staticmethod
    def _default_watchlist() -> list[str]:
        return ["SPY", "QQQ", "IWM", "AAPL", "MSFT"]

    def get_watchlist(self) -> dict[str, Any]:
        defaults = self._default_watchlist()
        path = self.watchlist_path
        if path is None:
            return {
                "symbols": defaults,
                "source": "memory",
                "path": None,
            }

        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                payload = {"symbols": defaults}
                path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                return {
                    "symbols": defaults,
                    "source": "file",
                    "path": str(path),
                }

            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                loaded = {}

            raw_symbols = loaded.get("symbols") if isinstance(loaded, dict) else []
            if not isinstance(raw_symbols, list):
                raw_symbols = []

            normalized: list[str] = []
            seen: set[str] = set()
            for item in raw_symbols:
                symbol = self._normalize_symbol(item)
                if not symbol or symbol in seen:
                    continue
                normalized.append(symbol)
                seen.add(symbol)

            merged = []
            for symbol in defaults + normalized:
                if symbol and symbol not in merged:
                    merged.append(symbol)

            if merged != raw_symbols:
                path.write_text(json.dumps({"symbols": merged}, indent=2), encoding="utf-8")

            return {
                "symbols": merged,
                "source": "file",
                "path": str(path),
            }

    def add_to_watchlist(self, symbol: str) -> dict[str, Any]:
        normalized = self._normalize_symbol(symbol)
        if not normalized:
            return {
                "ok": False,
                "added": False,
                "symbols": self.get_watchlist().get("symbols", []),
                "message": "symbol is required",
            }

        current = self.get_watchlist()
        symbols = list(current.get("symbols") or [])
        added = normalized not in symbols
        if added:
            symbols.append(normalized)

        path = self.watchlist_path
        if path is not None:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"symbols": symbols}, indent=2), encoding="utf-8")

        return {
            "ok": True,
            "added": added,
            "symbol": normalized,
            "symbols": symbols,
            "message": ("added" if added else "already exists"),
        }

    async def _estimate_iv(self, symbol: str, underlying_price: float | None) -> tuple[float | None, str | None]:
        if underlying_price is None:
            return None, "last price unavailable for IV estimate"

        try:
            expirations = await self.base_data_service.tradier_client.get_expirations(symbol)
        except Exception as exc:
            return None, f"options expirations unavailable: {exc}"

        selected_exp = self._select_expiration(expirations)
        if not selected_exp:
            return None, "no option expirations available"

        try:
            chain_raw = await self.base_data_service.tradier_client.get_chain(symbol, selected_exp, greeks=True)
            contracts = self.base_data_service.normalize_chain(chain_raw)
        except Exception as exc:
            return None, f"options chain unavailable: {exc}"

        iv_candidates: list[tuple[float, float]] = []
        for contract in contracts:
            strike = self._safe_float(getattr(contract, "strike", None))
            iv_value = self._safe_float(getattr(contract, "iv", None))
            if strike is None or iv_value is None:
                continue
            iv_candidates.append((abs(strike - underlying_price), iv_value))

        if not iv_candidates:
            return None, "no IV values available"

        iv_candidates.sort(key=lambda item: item[0])
        nearest = [iv for _, iv in iv_candidates[:6]]
        if not nearest:
            return None, "no near-ATM IV values available"
        return sum(nearest) / len(nearest), None

    def _score_scan_row(self, symbol: str, trend: str, rsi14: float | None, rv20: float | None, iv: float | None, iv_rv_ratio: float | None) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []

        liquidity_bonus = LIQUIDITY_BONUS.get(symbol, 0.4)
        score += liquidity_bonus
        reasons.append(f"liquidity bonus +{liquidity_bonus:.1f}")

        if iv_rv_ratio is not None and iv_rv_ratio > 1.2:
            score += 2.0
            reasons.append(f"IV rich (IV/RV {iv_rv_ratio:.2f}) +2.0")
        elif iv_rv_ratio is not None:
            reasons.append(f"IV/RV neutral ({iv_rv_ratio:.2f})")
        else:
            reasons.append("IV/RV unavailable")

        if trend == "up":
            score += 1.2
            reasons.append("uptrend alignment for put-credit/covered-call +1.2")
        elif trend == "down":
            score += 0.5
            reasons.append("downtrend alignment for call-credit +0.5")
        else:
            reasons.append("range trend (neutral)")

        if rsi14 is None:
            reasons.append("RSI unavailable")
        elif rsi14 < 70:
            score += 1.0
            reasons.append(f"RSI not overheated ({rsi14:.1f}) +1.0")
        elif rsi14 >= 75:
            score -= 1.0
            reasons.append(f"RSI overheated ({rsi14:.1f}) -1.0")
        else:
            reasons.append(f"RSI elevated ({rsi14:.1f})")

        if rv20 is None:
            reasons.append("20d realized vol unavailable")

        return round(score, 3), reasons

    @staticmethod
    def _source_status(snapshot: dict[str, dict[str, Any]]) -> str:
        statuses = [str((row or {}).get("status") or "").lower() for row in (snapshot or {}).values()]
        if any(state == "red" for state in statuses):
            return "down"
        if any(state == "yellow" for state in statuses):
            return "degraded"
        if any(state == "green" for state in statuses):
            return "ok"
        return "degraded"

    @staticmethod
    def _score_label(score: float) -> str:
        if score >= 85.0:
            return "strong"
        if score >= 70.0:
            return "constructive"
        if score >= 55.0:
            return "neutral_plus"
        return "weak"

    async def _scan_symbol(self, symbol: str) -> tuple[dict[str, Any], list[str]]:
        ticker = str(symbol or "").strip().upper()
        notes: list[str] = []

        history = await self.base_data_service.get_prices_history(ticker, lookback_days=180)
        closes = [float(value) for value in (history or []) if value is not None]

        last = closes[-1] if closes else None
        rsi14 = rsi(closes, 14) if closes else None
        sma20 = simple_moving_average(closes, 20) if closes else None
        sma50 = simple_moving_average(closes, 50) if closes else None
        rv20 = realized_vol_annualized(closes[-21:]) if len(closes) >= 21 else None
        trend = self._trend(last, sma20, sma50)

        if not closes:
            notes.append(f"{ticker}: missing price history")

        iv, iv_note = await self._estimate_iv(ticker, last)
        if iv_note:
            notes.append(f"{ticker}: {iv_note}")

        iv_rv_ratio = None
        if iv is not None and rv20 not in (None, 0):
            iv_rv_ratio = iv / rv20

        scanner_score, reasons = self._score_scan_row(
            ticker,
            trend,
            self._safe_float(rsi14),
            self._safe_float(rv20),
            self._safe_float(iv),
            self._safe_float(iv_rv_ratio),
        )

        row = {
            "symbol": ticker,
            "scanner_score": scanner_score,
            "signals": {
                "trend": trend,
                "rsi_14": self._safe_float(rsi14),
                "rv_20d": self._safe_float(rv20),
                "iv": self._safe_float(iv),
                "iv_rv_ratio": self._safe_float(iv_rv_ratio),
            },
            "reasons": reasons,
        }
        return row, notes

    async def scan_universe(self, universe: str = "default") -> dict[str, Any]:
        universe_key = str(universe or "default").strip().lower()
        symbols = DEFAULT_SCANNER_UNIVERSE

        notes: list[str] = []
        if universe_key == "watchlist":
            symbols = list(self.get_watchlist().get("symbols") or self._default_watchlist())
            notes.append("scanner using persisted watchlist symbols")
        elif universe_key != "default":
            notes.append(f"universe '{universe_key}' not configured; using default")
        semaphore = asyncio.Semaphore(5)

        async def _scan(symbol: str) -> tuple[dict[str, Any], list[str]]:
            async with semaphore:
                return await self._scan_symbol(symbol)

        scans = await asyncio.gather(*[_scan(symbol) for symbol in symbols], return_exceptions=True)

        results: list[dict[str, Any]] = []
        for item in scans:
            if isinstance(item, Exception):
                notes.append(f"scanner item failed: {item}")
                continue
            row, row_notes = item
            results.append(row)
            notes.extend(row_notes)

        results.sort(key=lambda row: float(row.get("scanner_score") or 0.0), reverse=True)

        return {
            "as_of": self._utc_now_iso(),
            "universe": universe_key,
            "results": results,
            "notes": notes,
            "source_health": self.base_data_service.get_source_health_snapshot(),
        }

    async def stock_scanner(self, max_candidates: int = 15) -> dict[str, Any]:
        if self.signal_service and hasattr(self.signal_service, "get_symbol_signals"):
            return await self._stock_scanner_via_signal_hub(max_candidates=max_candidates)

        notes: list[str] = []
        max_count = max(10, min(int(max_candidates or 15), 20))
        source_health_snapshot = self.base_data_service.get_source_health_snapshot()
        source_status = self._source_status(source_health_snapshot)

        configured_symbols = list(self.get_watchlist().get("symbols") or [])
        symbols = list(DEFAULT_SCANNER_UNIVERSE) + configured_symbols
        normalized_symbols: list[str] = []
        seen: set[str] = set()
        for raw_symbol in symbols:
            symbol = self._normalize_symbol(raw_symbol)
            if not symbol or symbol in seen:
                continue
            normalized_symbols.append(symbol)
            seen.add(symbol)

        semaphore = asyncio.Semaphore(6)

        async def _scan_symbol(symbol: str) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    history = await self.base_data_service.get_prices_history(symbol, lookback_days=280)
                except Exception as exc:
                    notes.append(f"{symbol}: history unavailable ({exc})")
                    return None

                closes = [float(value) for value in (history or []) if value is not None]
                if len(closes) < 60:
                    notes.append(f"{symbol}: insufficient history")
                    return None

                price = closes[-1]
                lookback_252 = closes[-252:] if len(closes) >= 252 else closes
                ema20 = self._ema(closes, 20)
                sma50 = simple_moving_average(closes, 50)
                sma200 = simple_moving_average(closes, 200)
                rsi14 = self._safe_float(rsi(closes, 14))
                rv20 = self._safe_float(realized_vol_annualized(closes[-21:])) if len(closes) >= 21 else None

                trend_score = 0.0
                momentum_score = 0.0
                volatility_score = 0.0
                signals: list[str] = []

                if ema20 is not None and price > ema20:
                    trend_score += 45.0
                    signals.append("above_ema20")

                if sma50 is not None and sma200 is not None and sma50 > sma200:
                    trend_score += 35.0
                    signals.append("trend_up")

                if rsi14 is not None:
                    if 50.0 <= rsi14 <= 70.0:
                        momentum_score = 15.0
                        signals.append("strong_rsi")
                    elif 45.0 <= rsi14 < 50.0 or 70.0 < rsi14 <= 75.0:
                        momentum_score = 10.0
                    elif 40.0 <= rsi14 < 45.0 or 75.0 < rsi14 <= 80.0:
                        momentum_score = 6.0
                    else:
                        momentum_score = 2.0

                if rv20 is None:
                    volatility_score = 6.0
                elif 0.12 <= rv20 <= 0.40:
                    volatility_score = 10.0
                    signals.append("volatility_suitable")
                elif 0.08 <= rv20 < 0.12 or 0.40 < rv20 <= 0.55:
                    volatility_score = 7.0
                elif rv20 < 0.08:
                    volatility_score = 4.0
                    signals.append("volatility_low")
                else:
                    volatility_score = 2.0
                    signals.append("volatility_high")

                if sma50 is not None and price > sma50:
                    signals.append("above_sma50")

                if sma200 is not None and price > sma200:
                    signals.append("above_sma200")

                composite_score = round(trend_score + momentum_score + volatility_score, 3)
                strategy = "credit_put_spread" if "trend_up" in signals else "credit_call_spread"

                trend = "up" if "trend_up" in signals else ("down" if rv20 is not None and rv20 > 0.55 else "range")
                score_label = self._score_label(composite_score)

                iv_estimate = None
                iv_rv_ratio = None
                try:
                    iv_estimate, _ = await self._estimate_iv(symbol, price)
                    if iv_estimate is not None and rv20 not in (None, 0):
                        iv_rv_ratio = iv_estimate / rv20
                except Exception as exc:
                    notes.append(f"{symbol}: IV estimate unavailable ({exc})")

                price_change_1d = None
                if len(closes) >= 2 and closes[-2] not in (None, 0):
                    price_change_1d = (closes[-1] / closes[-2]) - 1.0

                price_change_20d = None
                if len(closes) >= 21 and closes[-21] not in (None, 0):
                    price_change_20d = (closes[-1] / closes[-21]) - 1.0

                sparkline_source = closes[-24:]
                sparkline: list[float] = []
                if sparkline_source and sparkline_source[0] not in (None, 0):
                    base = sparkline_source[0]
                    sparkline = [round(((point / base) - 1.0) * 100.0, 3) for point in sparkline_source]

                thesis: list[str] = []
                if "trend_up" in signals:
                    thesis.append("Trend is constructive with 50/200-day alignment")
                else:
                    thesis.append("Trend is mixed; prefer defined-risk setups")

                if rsi14 is not None:
                    if 50.0 <= rsi14 <= 70.0:
                        thesis.append("Momentum remains healthy without overbought extremes")
                    elif rsi14 > 75.0:
                        thesis.append("Momentum is stretched; mean reversion risk elevated")

                if rv20 is not None:
                    if 0.12 <= rv20 <= 0.40:
                        thesis.append("Realized volatility is in a favorable range for spreads")
                    elif rv20 > 0.55:
                        thesis.append("Realized volatility is elevated; manage width and sizing")

                if iv_rv_ratio is not None:
                    if iv_rv_ratio > 1.2:
                        thesis.append("Implied volatility is rich versus realized volatility")
                    elif iv_rv_ratio < 0.9:
                        thesis.append("Implied volatility is not rich; edge may rely on directional thesis")

                return {
                    "symbol": symbol,
                    "idea_key": f"{symbol}|stock_scanner",
                    "price": round(price, 4),
                    "trend_score": round(trend_score, 3),
                    "momentum_score": round(momentum_score, 3),
                    "volatility_score": round(volatility_score, 3),
                    "composite_score": composite_score,
                    "score_label": score_label,
                    "signals": signals,
                    "trend": trend,
                    "recommended_strategy": strategy,
                    "thesis": thesis,
                    "source_health": {
                        "status": source_status,
                        "providers": {name: (state or {}).get("status") for name, state in source_health_snapshot.items()},
                    },
                    "metrics": {
                        "rsi14": rsi14,
                        "rv20": rv20,
                        "iv": iv_estimate,
                        "iv_rv_ratio": iv_rv_ratio,
                        "ema20": self._safe_float(ema20),
                        "sma50": self._safe_float(sma50),
                        "sma200": self._safe_float(sma200),
                        "high_52w": max(lookback_252) if lookback_252 else None,
                        "low_52w": min(lookback_252) if lookback_252 else None,
                        "price_change_1d": price_change_1d,
                        "price_change_20d": price_change_20d,
                    },
                    "sparkline": sparkline,
                }

        scan_results = await asyncio.gather(*[_scan_symbol(symbol) for symbol in normalized_symbols], return_exceptions=True)

        candidates: list[dict[str, Any]] = []
        for result in scan_results:
            if isinstance(result, Exception):
                notes.append(f"scanner item failed: {result}")
                continue
            if not result:
                continue
            candidates.append(result)

        candidates.sort(key=lambda row: float(row.get("composite_score") or 0.0), reverse=True)

        return {
            "as_of": self._utc_now_iso(),
            "candidates": candidates[:max_count],
            "notes": notes,
            "source_health": source_health_snapshot,
            "source_status": source_status,
        }

    async def _stock_scanner_via_signal_hub(self, max_candidates: int = 15) -> dict[str, Any]:
        notes: list[str] = []
        max_count = max(10, min(int(max_candidates or 15), 20))
        source_health_snapshot = self.base_data_service.get_source_health_snapshot()
        source_status = self._source_status(source_health_snapshot)

        configured_symbols = list(self.get_watchlist().get("symbols") or [])
        symbols = list(DEFAULT_SCANNER_UNIVERSE) + configured_symbols
        normalized_symbols: list[str] = []
        seen: set[str] = set()
        for raw_symbol in symbols:
            symbol = self._normalize_symbol(raw_symbol)
            if not symbol or symbol in seen:
                continue
            normalized_symbols.append(symbol)
            seen.add(symbol)

        semaphore = asyncio.Semaphore(6)

        async def _scan_symbol(symbol: str) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    sig = await self.signal_service.get_symbol_signals(symbol=symbol, range_key="6mo")
                except Exception as exc:
                    notes.append(f"{symbol}: signal hub unavailable ({exc})")
                    return None

                metrics = sig.get("metrics") if isinstance(sig.get("metrics"), dict) else {}
                signals = sig.get("signals") if isinstance(sig.get("signals"), list) else []
                signal_ids = {str(item.get("id") or "") for item in signals if item.get("value")}

                trend = "up" if "trend_up" in signal_ids else ("down" if "trend_down" in signal_ids else "range")
                composite_score = float((sig.get("composite") or {}).get("score") or 0.0)
                strategy = "credit_put_spread" if trend == "up" else ("credit_call_spread" if trend == "down" else "iron_condor")

                rsi14 = self._safe_float(metrics.get("rsi14"))
                rv20 = self._safe_float(metrics.get("rv20d"))
                iv_estimate = self._safe_float(metrics.get("iv"))
                iv_rv_ratio = self._safe_float(metrics.get("iv_rv_ratio"))

                thesis = [str(item.get("why") or "") for item in signals if item.get("value") and str(item.get("why") or "").strip()]
                thesis = thesis[:4]
                reasons = [str(item.get("id") or "") for item in signals if item.get("value")]

                history = await self.base_data_service.get_prices_history(symbol, lookback_days=90)
                closes = [float(value) for value in (history or []) if value is not None]
                price = closes[-1] if closes else None
                sparkline_source = closes[-24:]
                sparkline: list[float] = []
                if sparkline_source and sparkline_source[0] not in (None, 0):
                    base = sparkline_source[0]
                    sparkline = [round(((point / base) - 1.0) * 100.0, 3) for point in sparkline_source]

                return {
                    "symbol": symbol,
                    "idea_key": f"{symbol}|stock_scanner",
                    "price": round(float(price), 4) if price is not None else None,
                    "trend_score": round(composite_score * 0.4, 3),
                    "momentum_score": round(composite_score * 0.3, 3),
                    "volatility_score": round(composite_score * 0.3, 3),
                    "composite_score": round(composite_score, 3),
                    "score_label": self._score_label(composite_score),
                    "signals": reasons,
                    "trend": trend,
                    "recommended_strategy": strategy,
                    "thesis": thesis or ["Signal Hub composite synthesis"],
                    "source_health": {
                        "status": source_status,
                        "providers": {name: (state or {}).get("status") for name, state in source_health_snapshot.items()},
                    },
                    "metrics": {
                        "rsi14": rsi14,
                        "rv20": rv20,
                        "iv": iv_estimate,
                        "iv_rv_ratio": iv_rv_ratio,
                        "ema20": self._safe_float(metrics.get("ema20")),
                        "sma50": self._safe_float(metrics.get("sma50")),
                        "sma200": self._safe_float(metrics.get("sma200")),
                        "high_52w": max(closes[-252:]) if closes else None,
                        "low_52w": min(closes[-252:]) if closes else None,
                        "price_change_1d": ((closes[-1] / closes[-2]) - 1.0) if len(closes) >= 2 and closes[-2] not in (None, 0) else None,
                        "price_change_20d": ((closes[-1] / closes[-21]) - 1.0) if len(closes) >= 21 and closes[-21] not in (None, 0) else None,
                    },
                    "sparkline": sparkline,
                }

        scan_results = await asyncio.gather(*[_scan_symbol(symbol) for symbol in normalized_symbols], return_exceptions=True)

        candidates: list[dict[str, Any]] = []
        for result in scan_results:
            if isinstance(result, Exception):
                notes.append(f"scanner item failed: {result}")
                continue
            if not result:
                continue
            candidates.append(result)

        candidates.sort(key=lambda row: float(row.get("composite_score") or 0.0), reverse=True)

        return {
            "as_of": self._utc_now_iso(),
            "candidates": candidates[:max_count],
            "notes": notes,
            "source_health": source_health_snapshot,
            "source_status": source_status,
        }

    async def get_summary(self, symbol: str, range_key: str = "6mo") -> dict[str, Any]:
        ticker = str(symbol or "SPY").strip().upper() or "SPY"
        notes: list[str] = []

        history_all = await self.base_data_service.get_prices_history(ticker, lookback_days=365)
        history_all = [float(x) for x in (history_all or []) if x is not None]

        if not history_all:
            notes.append("Price history unavailable from primary/fallback providers.")

        points = self._range_to_points(range_key)
        history = history_all[-points:] if history_all else []

        last = history[-1] if history else None
        prev_close = history[-2] if len(history) > 1 else None
        change = (last - prev_close) if (last is not None and prev_close is not None) else None
        change_pct = (change / prev_close) if (change is not None and prev_close not in (None, 0)) else None

        sma20 = simple_moving_average(history, 20) if history else None
        sma50 = simple_moving_average(history, 50) if history else None
        ema20 = self._ema(history, 20) if history else None
        rsi14 = rsi(history, 14) if history else None
        rv20 = realized_vol_annualized(history[-21:]) if len(history) >= 21 else (realized_vol_annualized(history) if history else None)

        options_context: dict[str, Any] = {
            "expiration": None,
            "iv": None,
            "expected_move": None,
            "iv_rv": None,
            "dte": None,
            "vix": None,
        }

        try:
            expirations = await self.base_data_service.tradier_client.get_expirations(ticker)
        except Exception as exc:
            expirations = []
            notes.append(f"Options expiration lookup failed: {exc}")

        selected_exp = self._select_expiration(expirations)
        if selected_exp:
            try:
                inputs = await self.base_data_service.get_analysis_inputs(
                    ticker,
                    selected_exp,
                    include_prices_history=False,
                )
                chain = inputs.get("contracts") or []
                underlying_price = self._safe_float(inputs.get("underlying_price"))
                vix = self._safe_float(inputs.get("vix"))

                if underlying_price is None and last is not None:
                    underlying_price = last

                iv_candidates: list[tuple[float, float]] = []
                if underlying_price is not None:
                    for contract in chain:
                        strike = self._safe_float(getattr(contract, "strike", None))
                        iv_val = self._safe_float(getattr(contract, "iv", None))
                        if strike is None or iv_val is None:
                            continue
                        iv_candidates.append((abs(strike - underlying_price), iv_val))

                iv_atm = None
                if iv_candidates:
                    iv_candidates.sort(key=lambda item: item[0])
                    nearest = [iv for _, iv in iv_candidates[:6]]
                    if nearest:
                        iv_atm = sum(nearest) / len(nearest)

                dte = None
                try:
                    exp_date = datetime.strptime(selected_exp, "%Y-%m-%d").date()
                    dte = (exp_date - datetime.now(timezone.utc).date()).days
                except Exception:
                    dte = None

                em = None
                if underlying_price is not None and iv_atm is not None and dte is not None and dte > 0:
                    try:
                        em = expected_move(underlying_price, iv_atm, dte)
                    except Exception:
                        em = None

                iv_rv = None
                if iv_atm is not None and rv20 not in (None, 0):
                    iv_rv = iv_atm / rv20

                options_context = {
                    "expiration": selected_exp,
                    "iv": iv_atm,
                    "expected_move": em,
                    "iv_rv": iv_rv,
                    "dte": dte,
                    "vix": vix,
                }
            except Exception as exc:
                notes.append(f"Options context unavailable: {exc}")
        else:
            notes.append("No option expirations available for symbol.")

        source_health = self.base_data_service.get_source_health_snapshot()

        return {
            "symbol": ticker,
            "as_of": self._utc_now_iso(),
            "price": {
                "last": last,
                "prev_close": prev_close,
                "change": change,
                "change_pct": change_pct,
                "range_high": max(history) if history else None,
                "range_low": min(history) if history else None,
            },
            "history": [{"idx": idx, "close": value} for idx, value in enumerate(history)],
            "indicators": {
                "rsi14": rsi14,
                "sma20": sma20,
                "sma50": sma50,
                "ema20": ema20,
                "realized_vol": rv20,
            },
            "options_context": options_context,
            "source_health": source_health,
            "notes": notes,
        }
