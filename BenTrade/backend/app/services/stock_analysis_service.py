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
    def __init__(self, base_data_service: BaseDataService, results_dir: Path | None = None) -> None:
        self.base_data_service = base_data_service
        self.results_dir = results_dir
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
