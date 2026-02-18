from __future__ import annotations

import json
import logging
import inspect
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from collections import Counter, defaultdict
from typing import Any

from app.utils.http import UpstreamError
from app.models.trade_contract import TradeContract
from app.services.base_data_service import BaseDataService
from app.services.evaluation.gates import evaluate_trade as evaluate_trade_contract
from app.services.evaluation.ranking import sort_trades_by_rank as sort_trades_by_rank_contracts
from app.services.evaluation.scoring import compute_composite_score as compute_composite_score_contract
from app.services.evaluation.types import EvaluationContext
from app.services.ranking import safe_float as rank_safe_float
from app.services.validation_events import emit_validation_event
from app.utils.dates import dte_ceil
from app.utils.trade_key import canonicalize_strategy_id, canonicalize_trade_key, trade_key

try:
    from common.quant_analysis import CreditSpread, enrich_trades_batch
except Exception:
    from quant_analysis import CreditSpread, enrich_trades_batch


SUPPORTED_UNDERLYINGS = ("SPY", "QQQ", "IWM", "XSP", "SPX", "NDX")

SYMBOL_ALIASES: dict[str, str] = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "IWM": "IWM",
    "XSP": "XSP",
    "SPX": "SPX",
    "NDX": "NDX",
}

INDEX_RULES: dict[str, dict[str, float]] = {
    "SPY": {"min_pop": 0.70, "min_ror": 0.12, "max_width": 10, "max_delta": 0.30, "min_iv_rv": 0.75},
    "SPX": {"min_pop": 0.70, "min_ror": 0.12, "max_width": 50, "max_delta": 0.30},
    "XSP": {"min_pop": 0.70, "min_ror": 0.12, "max_width": 10, "max_delta": 0.30},
    "QQQ": {"min_pop": 0.72, "min_ror": 0.15, "max_width": 5, "max_delta": 0.25},
    "NDX": {"min_pop": 0.72, "min_ror": 0.15, "max_width": 50, "max_delta": 0.25},
    "IWM": {"min_pop": 0.75, "min_ror": 0.18, "max_width": 5, "max_delta": 0.22},
}

for _sym in INDEX_RULES:
    INDEX_RULES[_sym].setdefault("min_iv_rv", 0.75)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _symbol_alias(symbol: str) -> str:
    sym = str(symbol or "").upper()
    return SYMBOL_ALIASES.get(sym, sym)


def _rules_with_validation_adjustment(rules: dict[str, float], validation_mode: bool) -> dict[str, float]:
    if not validation_mode:
        return dict(rules)

    adjusted = dict(rules)
    adjusted["min_pop"] = max(0.0, float(adjusted.get("min_pop", 0.0)) - 0.03)
    adjusted["min_ror"] = max(0.0, float(adjusted.get("min_ror", 0.0)) - 0.03)
    adjusted["min_iv_rv"] = max(0.0, float(adjusted.get("min_iv_rv", 0.0)) - 0.05)
    return adjusted


def evaluate_underlying_tradeable(base_metrics: dict, validation_mode: bool) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    underlying_price = _to_float(base_metrics.get("underlying_price"))
    open_interest = _to_float(base_metrics.get("option_open_interest"))
    bid_ask_spread_pct = _to_float(base_metrics.get("bid_ask_spread_pct"))
    iv_rank = _to_float(base_metrics.get("iv_rank"))
    dte = _to_float(base_metrics.get("dte"))

    if underlying_price is None:
        reasons.append("missing_underlying_price")
    elif underlying_price < 20:
        reasons.append("underlying_price_too_low")

    min_open_interest = 300.0 if validation_mode else 1000.0
    if open_interest is None:
        reasons.append("missing_open_interest")
    elif open_interest < min_open_interest:
        reasons.append("open_interest_below_min")

    max_spread = 0.15 if validation_mode else 0.10
    if bid_ask_spread_pct is None:
        reasons.append("missing_bid_ask_spread_pct")
    elif bid_ask_spread_pct > max_spread:
        reasons.append("liquidity_spread_too_wide")

    if iv_rank is None:
        reasons.append("missing_iv_rank")
    elif iv_rank < 0.15:
        reasons.append("iv_rank_below_min")

    if dte is None:
        reasons.append("missing_dte")
    elif dte < 3 or dte > 21:
        reasons.append("dte_out_of_range")

    return len(reasons) == 0, reasons


def evaluate_trade(trade: dict, rules: dict, validation_mode: bool) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    effective_rules = _rules_with_validation_adjustment(rules, validation_mode)

    spread_bid = _to_float(trade.get("spread_bid"))
    spread_ask = _to_float(trade.get("spread_ask"))
    net_credit = _to_float(trade.get("net_credit"))
    short_bid = _to_float(trade.get("bid"))
    short_ask = _to_float(trade.get("ask"))

    if short_bid is None or short_ask is None or spread_bid is None or spread_ask is None:
        reasons.append("missing_quote")

    net_credit_check = spread_bid if spread_bid is not None else net_credit
    if net_credit_check is None or net_credit_check <= 0:
        reasons.append("non_positive_credit")

    p_win_used = _to_float(trade.get("p_win_used", trade.get("pop_delta_approx")))
    return_on_risk = _to_float(trade.get("return_on_risk"))
    short_delta_abs = _to_float(trade.get("short_delta_abs"))
    width = _to_float(trade.get("width"))
    iv_rv_ratio = _to_float(trade.get("iv_rv_ratio"))
    trade_quality_score = _to_float(trade.get("trade_quality_score"))
    bid_ask_spread_pct = _to_float(trade.get("bid_ask_spread_pct"))
    open_interest = _to_float(trade.get("open_interest"))
    volume = _to_float(trade.get("volume"))

    ev = _to_float(trade.get("ev_per_share", trade.get("expected_value")))
    kelly = _to_float(trade.get("kelly_fraction"))
    max_profit = _to_float(trade.get("max_profit_per_share", trade.get("max_profit")))
    max_loss = _to_float(trade.get("max_loss_per_share", trade.get("max_loss")))

    ev_floor = -0.50 if validation_mode else 0.0
    if ev is not None and ev < ev_floor:
        reasons.append("ev_negative")
    kelly_floor = -0.20 if validation_mode else 0.0
    if kelly is not None and kelly < kelly_floor:
        reasons.append("kelly_negative")
    hard_ror_floor = 0.05 if validation_mode else 0.10
    if return_on_risk is not None and return_on_risk < hard_ror_floor:
        reasons.append("ror_hard_floor")
    loss_profit_cap = 10.0 if validation_mode else 8.0
    if max_profit is not None and max_loss is not None and max_profit > 0:
        if (max_loss / max_profit) > loss_profit_cap:
            reasons.append("loss_profit_ratio_too_high")

    if p_win_used is None:
        reasons.append("missing_pop")
    elif p_win_used < float(effective_rules.get("min_pop", 0.0)):
        reasons.append("pop_below_min")

    if return_on_risk is None:
        reasons.append("missing_ror")
    elif return_on_risk < float(effective_rules.get("min_ror", 0.0)):
        reasons.append("ror_below_min")

    if short_delta_abs is None:
        reasons.append("missing_delta")
    elif abs(short_delta_abs) > float(effective_rules.get("max_delta", 1.0)):
        reasons.append("delta_above_max")

    if width is None:
        reasons.append("missing_width")
    elif width > float(effective_rules.get("max_width", 9999.0)):
        reasons.append("width_above_max")

    if iv_rv_ratio is None:
        if not validation_mode:
            reasons.append("missing_iv_rv")
    elif iv_rv_ratio < float(effective_rules.get("min_iv_rv", 0.0)):
        reasons.append("iv_rv_below_min")

    tqs_floor = 0.50 if validation_mode else 0.55
    if trade_quality_score is None:
        reasons.append("missing_trade_quality_score")
    elif trade_quality_score < tqs_floor:
        reasons.append("trade_quality_below_min")

    max_spread = 0.15 if validation_mode else 0.10
    if bid_ask_spread_pct is None:
        reasons.append("missing_bid_ask_spread_pct")
    elif bid_ask_spread_pct > max_spread:
        reasons.append("liquidity_spread_too_wide")

    min_open_interest = 100.0 if validation_mode else 1000.0
    if open_interest is None:
        reasons.append("missing_open_interest")
    elif open_interest < min_open_interest:
        reasons.append("open_interest_below_min")

    min_volume = 20.0 if validation_mode else 100.0
    if volume is None:
        reasons.append("missing_volume")
    elif volume < min_volume:
        reasons.append("volume_below_min")

    return len(reasons) == 0, reasons


def compute_composite_score(trade: dict) -> float:
    """Compute the weighted composite score for a trade using normalized components."""
    trade_quality_score = _clamp(_to_float(trade.get("trade_quality_score")) or 0.0)
    return_on_risk = _clamp((_to_float(trade.get("return_on_risk")) or 0.0) / 0.50)
    probability = _clamp(_to_float(trade.get("p_win_used", trade.get("pop_delta_approx"))) or 0.0)
    iv_rv_ratio_raw = _to_float(trade.get("iv_rv_ratio")) or 0.0
    iv_rv_ratio = _clamp((iv_rv_ratio_raw - 1.0) / 1.0)

    open_interest = _to_float(trade.get("open_interest")) or 0.0
    bid_ask_spread_pct = _to_float(trade.get("bid_ask_spread_pct"))
    spread_component = _clamp(1.0 - (bid_ask_spread_pct if bid_ask_spread_pct is not None else 1.0))
    liquidity_score = _clamp(open_interest / 5000.0) * spread_component

    score = (
        (0.30 * trade_quality_score)
        + (0.25 * return_on_risk)
        + (0.20 * probability)
        + (0.15 * iv_rv_ratio)
        + (0.10 * liquidity_score)
    )
    return round(_clamp(score), 6)


def select_expirations_in_window(expirations: list[str], now: datetime, dte_min: int, dte_max: int) -> list[str]:
    """
    expirations are 'YYYY-MM-DD'.
    Return expirations whose DTE is within [dte_min, dte_max], sorted by DTE ascending.
    """
    today = now.date()
    selected: list[tuple[int, str]] = []
    for exp in expirations:
        try:
            exp_date = datetime.strptime(str(exp), "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (exp_date - today).days
        if dte_min <= dte <= dte_max:
            selected.append((dte, str(exp)))
    selected.sort(key=lambda item: item[0])
    return [exp for _, exp in selected]


class ReportService:
    def __init__(self, base_data_service: BaseDataService, results_dir: Path) -> None:
        self.base_data_service = base_data_service
        self.results_dir = results_dir
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)

    async def _emit_progress(self, progress_callback: Any, payload: dict[str, Any]) -> None:
        if progress_callback is None:
            return
        try:
            result = progress_callback(payload)
            if inspect.isawaitable(result):
                await result
        except Exception:
            self.logger.debug("event=progress_callback_failed", exc_info=True)

    @staticmethod
    def _passes_first_gate(trade: dict[str, Any]) -> bool:
        ev = trade.get("ev_per_share", trade.get("expected_value"))
        kelly = trade.get("kelly_fraction")
        ror = trade.get("return_on_risk")
        max_profit = trade.get("max_profit_per_share", trade.get("max_profit"))
        max_loss = trade.get("max_loss_per_share", trade.get("max_loss"))

        try:
            if ev is not None and float(ev) < 0:
                return False
            if kelly is not None and float(kelly) < 0:
                return False
            if ror is not None and float(ror) < 0.10:
                return False
            if max_profit is not None and max_loss is not None and float(max_profit) > 0:
                if (float(max_loss) / float(max_profit)) > 8:
                    return False
        except Exception:
            return False

        return True

    @staticmethod
    def _safe_avg(values: list[float]) -> float | None:
        clean = [float(v) for v in values if v is not None]
        if not clean:
            return None
        return sum(clean) / len(clean)

    @staticmethod
    def _compute_iv_bounds(contracts: list) -> tuple[float | None, float | None]:
        iv_values = [float(c.iv) for c in contracts if c.iv is not None and float(c.iv) > 0]
        if not iv_values:
            return None, None
        return min(iv_values), max(iv_values)

    @staticmethod
    def _estimate_underlying_base_metrics(contracts: list, underlying_price: float, expiration: str) -> dict[str, Any]:
        dte = dte_ceil(expiration)
        liquid = [
            c for c in contracts
            if c.bid is not None and c.ask is not None and (c.ask + c.bid) > 0 and (c.open_interest or 0) > 0
        ]
        liquid = sorted(liquid, key=lambda c: (c.open_interest or 0), reverse=True)
        top_liquid = liquid[:20]

        spreads: list[float] = []
        open_interests: list[int] = []
        iv_values: list[float] = []

        for c in top_liquid:
            mid = ((c.bid or 0.0) + (c.ask or 0.0)) / 2.0
            if mid > 0:
                spreads.append(max(0.0, ((c.ask or 0.0) - (c.bid or 0.0)) / mid))
            open_interests.append(int(c.open_interest or 0))
            if c.iv is not None and float(c.iv) > 0:
                iv_values.append(float(c.iv))

        iv_rank = None
        if iv_values:
            iv_low = min(iv_values)
            iv_high = max(iv_values)
            iv_mid = median(iv_values)
            if iv_high > iv_low:
                iv_rank = _clamp((iv_mid - iv_low) / (iv_high - iv_low))
            else:
                iv_rank = 0.5

        return {
            "underlying_price": underlying_price,
            "option_open_interest": max(open_interests) if open_interests else None,
            "bid_ask_spread_pct": min(spreads) if spreads else None,
            "iv_rank": iv_rank,
            "dte": dte,
        }

    @classmethod
    def _build_report_stats(cls, all_candidates: list[dict[str, Any]], accepted: list[dict[str, Any]]) -> dict[str, Any]:
        total_candidates = len(all_candidates)
        accepted_trades = len(accepted)
        rejected_trades = max(total_candidates - accepted_trades, 0)
        acceptance_rate = (accepted_trades / total_candidates) if total_candidates > 0 else 0.0

        scores = [_to_float(t.get("composite_score")) for t in accepted]
        scores = [s for s in scores if s is not None]
        probabilities = [_to_float(t.get("p_win_used", t.get("pop_delta_approx"))) for t in accepted]
        probabilities = [p for p in probabilities if p is not None]
        ror_values = [_to_float(t.get("return_on_risk")) for t in accepted]
        ror_values = [r for r in ror_values if r is not None]
        rank_scores = [rank_safe_float(t.get("rank_score")) for t in accepted]
        rank_scores = [s for s in rank_scores if s is not None]

        best_underlying = None
        if accepted:
            best_trade = max(accepted, key=lambda t: _to_float(t.get("composite_score")) or -1.0)
            best_underlying = str(best_trade.get("underlying") or best_trade.get("underlying_symbol") or "").upper() or None

        dte_bucket_counts = {"3-5": 0, "6-10": 0, "11-14": 0}
        for trade in all_candidates:
            dte_val = _to_float(trade.get("dte"))
            if dte_val is None:
                continue
            dte_int = int(dte_val)
            if 3 <= dte_int <= 5:
                dte_bucket_counts["3-5"] += 1
            elif 6 <= dte_int <= 10:
                dte_bucket_counts["6-10"] += 1
            elif 11 <= dte_int <= 14:
                dte_bucket_counts["11-14"] += 1

        return {
            "total_candidates": total_candidates,
            "accepted_trades": accepted_trades,
            "rejected_trades": rejected_trades,
            "acceptance_rate": acceptance_rate,
            "best_trade_score": max(scores) if scores else None,
            "worst_accepted_score": min(scores) if scores else None,
            "avg_trade_score": cls._safe_avg(scores),
            "avg_probability": cls._safe_avg(probabilities),
            "avg_return_on_risk": cls._safe_avg(ror_values),
            "best_rank_score": max(rank_scores) if rank_scores else None,
            "avg_rank_score": cls._safe_avg(rank_scores),
            "best_underlying": best_underlying,
            "dte_bucket_counts": dte_bucket_counts,
        }

    @staticmethod
    def _safe_avg(values: list[float]) -> float | None:
        clean = [float(v) for v in values if v is not None]
        if not clean:
            return None
        return sum(clean) / len(clean)

    @classmethod
    def _build_diagnostics(cls, all_trades: list[dict[str, Any]], accepted_trades: list[dict[str, Any]]) -> dict[str, Any]:
        total_candidates = len(all_trades)
        accepted_count = len(accepted_trades)
        rejected_count = max(total_candidates - accepted_count, 0)
        acceptance_rate = (accepted_count / total_candidates) if total_candidates > 0 else 0.0

        quality_scores: list[float] = []
        pop_values: list[float] = []
        ror_values: list[float] = []
        for tr in accepted_trades:
            try:
                q = tr.get("trade_quality_score")
                if q is not None:
                    quality_scores.append(float(q))
            except Exception:
                pass
            try:
                p = tr.get("p_win_used", tr.get("pop_delta_approx"))
                if p is not None:
                    pop_values.append(float(p))
            except Exception:
                pass
            try:
                r = tr.get("return_on_risk")
                if r is not None:
                    ror_values.append(float(r))
            except Exception:
                pass

        return {
            "total_candidates": total_candidates,
            "accepted_trades": accepted_count,
            "rejected_trades": rejected_count,
            "acceptance_rate": acceptance_rate,
            "avg_quality_score": cls._safe_avg(quality_scores),
            "best_quality_score": max(quality_scores) if quality_scores else None,
            "worst_accepted_score": min(quality_scores) if quality_scores else None,
            "avg_pop": cls._safe_avg(pop_values),
            "avg_ror": cls._safe_avg(ror_values),
            "accepted_trades_list_count": accepted_count,
            "rejected_trades_list_count": rejected_count,
        }

    async def _choose_expiration(self, symbol: str) -> str:
        expirations = await self.base_data_service.tradier_client.get_expirations(symbol)
        if not expirations:
            raise ValueError("No expirations available")
        return expirations[0]

    def _build_candidates(self, *, contracts: list, underlying_price: float, expiration: str, symbol: str) -> list[dict[str, Any]]:
        put_contracts = [
            c
            for c in contracts
            if c.option_type == "put" and c.expiration == expiration and c.bid is not None and c.ask is not None
        ]
        call_contracts = [
            c
            for c in contracts
            if c.option_type == "call" and c.expiration == expiration and c.bid is not None and c.ask is not None
        ]

        put_shorts = [c for c in put_contracts if c.strike < underlying_price]
        call_shorts = [c for c in call_contracts if c.strike > underlying_price]

        def score_short(contract) -> float:
            delta_abs = abs(contract.delta) if contract.delta is not None else 0.25
            oi = contract.open_interest or 0
            return abs(delta_abs - 0.25) - (oi / 1_000_000)

        put_shorts = sorted(put_shorts, key=score_short)[:18]
        call_shorts = sorted(call_shorts, key=score_short)[:18]

        candidates: list[dict[str, Any]] = []

        def add_spreads(shorts, all_legs, spread_type: str, is_put: bool) -> None:
            for short_leg in shorts:
                if is_put:
                    long_legs = [l for l in all_legs if l.strike < short_leg.strike]
                    long_legs = sorted(long_legs, key=lambda x: abs((short_leg.strike - x.strike) - 5.0))
                else:
                    long_legs = [l for l in all_legs if l.strike > short_leg.strike]
                    long_legs = sorted(long_legs, key=lambda x: abs((x.strike - short_leg.strike) - 5.0))

                if not long_legs:
                    continue

                for long_leg in long_legs[:2]:
                    width = abs(short_leg.strike - long_leg.strike)
                    if width <= 0 or width > 10:
                        continue

                    short_bid = _to_float(short_leg.bid)
                    short_ask = _to_float(short_leg.ask)
                    long_bid = _to_float(long_leg.bid)
                    long_ask = _to_float(long_leg.ask)

                    if short_bid is None or short_ask is None or long_bid is None or long_ask is None:
                        continue

                    spread_bid = short_bid - long_ask
                    spread_ask = short_ask - long_bid
                    spread_mid = (spread_bid + spread_ask) / 2.0

                    bid_ask_spread_pct = 9.99
                    if spread_mid > 0:
                        try:
                            bid_ask_spread_pct = min(max((spread_ask - spread_bid) / spread_mid, 0.0), 9.99)
                        except Exception:
                            bid_ask_spread_pct = 9.99

                    net_credit = spread_bid
                    if net_credit <= 0:
                        continue
                    if net_credit >= width:
                        continue

                    candidates.append(
                        {
                            "spread_type": spread_type,
                            "underlying": symbol,
                            "underlying_symbol": symbol,
                            "expiration": expiration,
                            "short_strike": short_leg.strike,
                            "long_strike": long_leg.strike,
                            "dte": dte_ceil(expiration),
                            "underlying_price": underlying_price,
                            "price": underlying_price,
                            "bid": short_bid,
                            "ask": short_ask,
                            "open_interest": short_leg.open_interest,
                            "volume": short_leg.volume,
                            "short_delta_abs": abs(short_leg.delta) if short_leg.delta is not None else None,
                            "iv": short_leg.iv,
                            "implied_vol": short_leg.iv,
                            "width": width,
                            "net_credit": net_credit,
                            "spread_bid": spread_bid,
                            "spread_ask": spread_ask,
                            "spread_mid": spread_mid,
                            "bid_ask_spread_pct": bid_ask_spread_pct,
                            "pricing_source": "conservative_bid_ask",
                        }
                    )

        add_spreads(put_shorts, put_contracts, "put_credit", is_put=True)
        add_spreads(call_shorts, call_contracts, "call_credit", is_put=False)

        seen: set[tuple] = set()
        unique: list[dict[str, Any]] = []
        for c in candidates:
            key = (c["spread_type"], round(c["short_strike"], 6), round(c["long_strike"], 6), c["dte"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(c)

        validated: list[dict[str, Any]] = []
        for tr in unique:
            try:
                cs = CreditSpread(
                    spread_type=tr.get("spread_type"),
                    underlying_price=float(tr.get("underlying_price") or tr.get("price")),
                    short_strike=float(tr.get("short_strike")),
                    long_strike=float(tr.get("long_strike")),
                    net_credit=float(tr.get("net_credit")),
                    dte=int(tr.get("dte")),
                    short_delta_abs=tr.get("short_delta_abs"),
                    implied_vol=tr.get("iv") or tr.get("implied_vol"),
                    realized_vol=tr.get("realized_vol"),
                )
                cs.validate()
                validated.append(tr)
            except Exception:
                continue

        return validated[:60]

    async def generate_live_report(self, symbol: str = "SPY", progress_callback: Any = None) -> dict[str, Any]:
        requested = (symbol or "").upper()
        targets = list(SUPPORTED_UNDERLYINGS) if requested in ("", "ALL", "SPY") else ([requested] if requested in SUPPORTED_UNDERLYINGS else list(SUPPORTED_UNDERLYINGS))
        settings = self.base_data_service.tradier_client.settings
        dte_min = int(settings.DTE_MIN)
        dte_max = int(settings.DTE_MAX)
        max_expirations = max(1, int(settings.MAX_EXPIRATIONS_PER_SYMBOL))
        validation_mode = bool(getattr(settings, "VALIDATION_MODE", False))
        now = datetime.now(timezone.utc)

        await self._emit_progress(
            progress_callback,
            {
                "step": "pipeline_start",
                "message": f"Preparing symbols ({', '.join(targets)}) and DTE window {dte_min}-{dte_max}.",
            },
        )

        all_candidates: list[dict[str, Any]] = []
        accepted: list[dict[str, Any]] = []
        reject_reason_counts: Counter[str] = Counter()
        reject_reason_counts_by_symbol: dict[str, Counter[str]] = defaultdict(Counter)

        per_symbol: dict[str, dict[str, Any]] = {
            sym: {
                "symbol": sym,
                "provider_symbol": _symbol_alias(sym),
                "expirations": 0,
                "chains_fetched": 0,
                "candidates": 0,
                "accepted": 0,
                "rejected": 0,
                "reject_reason_counts": {},
            }
            for sym in targets
        }

        for current_symbol in targets:
            provider_symbol = _symbol_alias(current_symbol)
            symbol_diag = per_symbol[current_symbol]
            await self._emit_progress(
                progress_callback,
                {
                    "step": "symbol_start",
                    "symbol": current_symbol,
                    "message": f"Analyzing {current_symbol}: calling Tradier expirations API (symbol={provider_symbol}).",
                },
            )
            self.logger.info("event=underlying_analysis_start symbol=%s message=Analyzing underlying", current_symbol)
            try:
                available_expirations = await self.base_data_service.tradier_client.get_expirations(provider_symbol)
            except Exception as exc:
                status = (exc.details or {}).get("status_code") if isinstance(exc, UpstreamError) else None
                symbol_diag["error"] = "unsupported_by_provider" if status in (400, 404, 422) else "expirations_fetch_failed"
                await self._emit_progress(
                    progress_callback,
                    {
                        "step": "symbol_expirations_failed",
                        "symbol": current_symbol,
                        "message": f"{current_symbol}: failed fetching expirations ({str(exc)}).",
                    },
                )
                self.logger.warning(
                    "event=underlying_expirations_fetch_failed symbol=%s error=%s",
                    current_symbol,
                    str(exc),
                )
                continue

            expiration_dtes: list[dict[str, Any]] = []
            for exp in available_expirations:
                try:
                    exp_date = datetime.strptime(str(exp), "%Y-%m-%d").date()
                    exp_dte = (exp_date - now.date()).days
                except ValueError:
                    continue
                expiration_dtes.append({"expiration": str(exp), "dte": exp_dte})

            selected_expirations = select_expirations_in_window(available_expirations, now, dte_min, dte_max)[:max_expirations]
            self.logger.debug(
                "event=underlying_expirations_selected symbol=%s dte_min=%d dte_max=%d max_expirations=%d available=%s selected=%s",
                current_symbol,
                dte_min,
                dte_max,
                max_expirations,
                str(expiration_dtes),
                str(selected_expirations),
            )
            await self._emit_progress(
                progress_callback,
                {
                    "step": "symbol_expirations_selected",
                    "symbol": current_symbol,
                    "message": f"{current_symbol}: selected {len(selected_expirations)} expirations in DTE window.",
                },
            )
            symbol_diag["expirations"] = len(selected_expirations)
            if not selected_expirations:
                continue

            merged_symbol: list[dict[str, Any]] = []
            accepted_symbol_all: list[dict[str, Any]] = []

            for expiration in selected_expirations:
                await self._emit_progress(
                    progress_callback,
                    {
                        "step": "expiration_start",
                        "symbol": current_symbol,
                        "expiration": expiration,
                        "message": f"{current_symbol} {expiration}: calling Tradier quote/options chain + FRED VIX.",
                    },
                )
                self.logger.debug(
                    "event=underlying_expiration_start symbol=%s expiration=%s dte=%d",
                    current_symbol,
                    expiration,
                    dte_ceil(expiration),
                )
                try:
                    inputs = await self.base_data_service.get_analysis_inputs(
                        provider_symbol,
                        expiration,
                        include_prices_history=False,
                    )
                except Exception as exc:
                    status = (exc.details or {}).get("status_code") if isinstance(exc, UpstreamError) else None
                    if status in (400, 404, 422):
                        symbol_diag["error"] = "unsupported_by_provider"
                    await self._emit_progress(
                        progress_callback,
                        {
                            "step": "expiration_fetch_failed",
                            "symbol": current_symbol,
                            "expiration": expiration,
                            "message": f"{current_symbol} {expiration}: input fetch failed ({str(exc)}).",
                        },
                    )
                    self.logger.warning(
                        "event=underlying_analysis_fetch_failed symbol=%s expiration=%s error=%s",
                        current_symbol,
                        expiration,
                        str(exc),
                    )
                    continue

                symbol_diag["chains_fetched"] = int(symbol_diag["chains_fetched"] or 0) + 1
                underlying_price = inputs["underlying_price"]
                contracts = inputs["contracts"]
                vix = inputs["vix"]

                if underlying_price is None or not contracts:
                    await self._emit_progress(
                        progress_callback,
                        {
                            "step": "expiration_no_data",
                            "symbol": current_symbol,
                            "expiration": expiration,
                            "message": f"{current_symbol} {expiration}: no usable chain/price data.",
                        },
                    )
                    self.logger.debug(
                        "event=underlying_analysis_no_data symbol=%s expiration=%s contracts=%d underlying_price=%s",
                        current_symbol,
                        expiration,
                        len(contracts or []),
                        str(underlying_price),
                    )
                    continue

                self.logger.debug(
                    "event=underlying_chain_loaded symbol=%s expiration=%s contracts=%d",
                    current_symbol,
                    expiration,
                    len(contracts),
                )

                underlying_metrics = self._estimate_underlying_base_metrics(contracts, underlying_price, expiration)
                underlying_ok, underlying_reasons = evaluate_underlying_tradeable(underlying_metrics, validation_mode)
                if not underlying_ok:
                    reject_reason_counts.update(underlying_reasons)
                    reject_reason_counts_by_symbol[current_symbol].update(underlying_reasons)
                    await self._emit_progress(
                        progress_callback,
                        {
                            "step": "expiration_tradeability_rejected",
                            "symbol": current_symbol,
                            "expiration": expiration,
                            "message": f"{current_symbol} {expiration}: skipped by underlying tradeability checks ({', '.join(underlying_reasons)}).",
                        },
                    )
                    self.logger.debug(
                        "event=underlying_tradeability_rejected symbol=%s expiration=%s metrics=%s",
                        current_symbol,
                        expiration,
                        str(underlying_metrics),
                    )
                    continue

                iv_low, iv_high = self._compute_iv_bounds(contracts)

                base_trades = self._build_candidates(
                    contracts=contracts,
                    underlying_price=underlying_price,
                    expiration=expiration,
                    symbol=current_symbol,
                )
                if not base_trades:
                    await self._emit_progress(
                        progress_callback,
                        {
                            "step": "expiration_no_candidates",
                            "symbol": current_symbol,
                            "expiration": expiration,
                            "message": f"{current_symbol} {expiration}: no base spread candidates generated.",
                        },
                    )
                    self.logger.debug(
                        "event=symbol_candidates_generated symbol=%s expiration=%s count=0",
                        current_symbol,
                        expiration,
                    )
                    continue

                self.logger.debug(
                    "event=symbol_candidates_generated symbol=%s expiration=%s count=%d",
                    current_symbol,
                    expiration,
                    len(base_trades),
                )
                await self._emit_progress(
                    progress_callback,
                    {
                        "step": "expiration_quant_enrich",
                        "symbol": current_symbol,
                        "expiration": expiration,
                        "message": f"{current_symbol} {expiration}: calculating quantitative metrics for {len(base_trades)} candidates.",
                    },
                )

                enriched = enrich_trades_batch(
                    base_trades,
                    prices_history=[],
                    vix=vix,
                    iv_low=iv_low,
                    iv_high=iv_high,
                )

                merged: list[dict[str, Any]] = []
                for tr in enriched:
                    try:
                        cs = CreditSpread(
                            spread_type=tr.get("spread_type"),
                            underlying_price=float(tr.get("underlying_price") or tr.get("price")),
                            short_strike=float(tr.get("short_strike")),
                            long_strike=float(tr.get("long_strike")),
                            net_credit=float(tr.get("net_credit") or 0.0),
                            dte=int(tr.get("dte")),
                            short_delta_abs=tr.get("short_delta_abs"),
                            implied_vol=tr.get("iv") or tr.get("implied_vol"),
                            realized_vol=tr.get("realized_vol"),
                        )
                        summary = cs.summary(iv_rank_value=tr.get("iv_rank"))
                        combined = {**summary, **tr}
                        if combined.get("vix") is None:
                            combined["vix"] = vix
                        merged.append(combined)
                    except Exception:
                        fallback = dict(tr)
                        if fallback.get("vix") is None:
                            fallback["vix"] = vix
                        merged.append(fallback)

                symbol_diag["candidates"] = int(symbol_diag["candidates"] or 0) + len(merged)

                rules = INDEX_RULES.get(current_symbol, INDEX_RULES.get(provider_symbol, {}))
                if not rules:
                    symbol_diag["error"] = "chain_not_supported"
                    continue

                await self._emit_progress(
                    progress_callback,
                    {
                        "step": "expiration_history_fetch",
                        "symbol": current_symbol,
                        "expiration": expiration,
                        "message": f"{current_symbol} {expiration}: calling Yahoo history (Tradier/Finnhub fallback as needed).",
                    },
                )
                prices_history = await self.base_data_service.get_prices_history(provider_symbol, lookback_days=365)
                enriched_with_history = enrich_trades_batch(
                    merged,
                    prices_history=prices_history,
                    vix=vix,
                    iv_low=iv_low,
                    iv_high=iv_high,
                )

                merged_with_history: list[dict[str, Any]] = []
                for tr in enriched_with_history:
                    try:
                        cs = CreditSpread(
                            spread_type=tr.get("spread_type"),
                            underlying_price=float(tr.get("underlying_price") or tr.get("price")),
                            short_strike=float(tr.get("short_strike")),
                            long_strike=float(tr.get("long_strike")),
                            net_credit=float(tr.get("net_credit") or 0.0),
                            dte=int(tr.get("dte")),
                            short_delta_abs=tr.get("short_delta_abs"),
                            implied_vol=tr.get("iv") or tr.get("implied_vol"),
                            realized_vol=tr.get("realized_vol"),
                        )
                        summary = cs.summary(iv_rank_value=tr.get("iv_rank"))
                        combined = {**summary, **tr}
                        if combined.get("vix") is None:
                            combined["vix"] = vix
                        merged_with_history.append(combined)
                    except Exception:
                        fallback = dict(tr)
                        if fallback.get("vix") is None:
                            fallback["vix"] = vix
                        merged_with_history.append(fallback)

                accepted_symbol_exp: list[dict[str, Any]] = []
                for trade in merged_with_history:
                    contract = TradeContract.from_dict(trade)
                    result = evaluate_trade_contract(
                        contract,
                        EvaluationContext(rules=rules, validation_mode=validation_mode),
                        legacy_evaluator=evaluate_trade,
                    )
                    if result.accepted:
                        accepted_symbol_exp.append(contract.to_dict())
                    else:
                        reject_reason_counts.update(result.reasons)
                        reject_reason_counts_by_symbol[current_symbol].update(result.reasons)

                accepted_symbol_all.extend(accepted_symbol_exp)
                merged_symbol.extend(merged)
                self.logger.debug(
                    "event=expiration_filter_result symbol=%s expiration=%s generated=%d first_gate_kept=%d accepted=%d rejected=%d",
                    current_symbol,
                    expiration,
                    len(merged),
                    len(merged_with_history),
                    len(accepted_symbol_exp),
                    max(len(merged) - len(accepted_symbol_exp), 0),
                )
                await self._emit_progress(
                    progress_callback,
                    {
                        "step": "expiration_complete",
                        "symbol": current_symbol,
                        "expiration": expiration,
                        "message": f"{current_symbol} {expiration}: accepted {len(accepted_symbol_exp)} of {len(merged)} candidates.",
                    },
                )

            for tr in accepted_symbol_all:
                contract = TradeContract.from_dict(tr)
                tr["composite_score"] = compute_composite_score_contract(contract, legacy_scorer=compute_composite_score)

            all_candidates.extend(merged_symbol)
            accepted.extend(accepted_symbol_all)
            self.logger.info(
                "event=symbol_filter_result symbol=%s generated=%d first_gate_kept=%d accepted=%d rejected=%d",
                current_symbol,
                len(merged_symbol),
                len(accepted_symbol_all),
                len(accepted_symbol_all),
                max(len(merged_symbol) - len(accepted_symbol_all), 0),
            )
            symbol_diag["accepted"] = len(accepted_symbol_all)
            symbol_diag["rejected"] = max(len(merged_symbol) - len(accepted_symbol_all), 0)
            symbol_diag["reject_reason_counts"] = dict(reject_reason_counts_by_symbol[current_symbol])
            await self._emit_progress(
                progress_callback,
                {
                    "step": "symbol_complete",
                    "symbol": current_symbol,
                    "message": f"{current_symbol}: accepted {len(accepted_symbol_all)} of {len(merged_symbol)} candidates.",
                },
            )

        await self._emit_progress(
            progress_callback,
            {
                "step": "ranking_start",
                "message": "Scoring and ranking accepted trades across all symbols.",
            },
        )
        accepted_contracts = [TradeContract.from_dict(trade) for trade in accepted]
        accepted = [trade.to_dict() for trade in sort_trades_by_rank_contracts(accepted_contracts)]

        for tr in accepted:
            symbol = str(tr.get("underlying") or tr.get("underlying_symbol") or tr.get("symbol") or "").upper()
            if symbol:
                tr["underlying"] = symbol
                tr["underlying_symbol"] = symbol
                tr["symbol"] = symbol

            exp = str(tr.get("expiration") or "").strip() or "NA"
            tr["expiration"] = exp

            dte = tr.get("dte")
            if dte in (None, "") and exp not in ("", "NA"):
                try:
                    dte = dte_ceil(exp)
                except Exception:
                    dte = None
            tr["dte"] = dte

            strategy = tr.get("strategy_id") or tr.get("spread_type") or tr.get("strategy")
            canonical_strategy, alias_mapped, provided_strategy = canonicalize_strategy_id(strategy)
            canonical_strategy = canonical_strategy or str(strategy or "").strip().lower() or "NA"
            if alias_mapped:
                emit_validation_event(
                    severity="warn",
                    code="TRADE_STRATEGY_ALIAS_MAPPED",
                    message="Report trade strategy alias mapped to canonical strategy_id",
                    context={
                        "strategy_id": canonical_strategy,
                        "provided_strategy": provided_strategy,
                    },
                )
            tr["strategy_id"] = canonical_strategy
            tr["spread_type"] = canonical_strategy
            tr["strategy"] = canonical_strategy

            short_strike = tr.get("short_strike")
            long_strike = tr.get("long_strike")
            if canonical_strategy == "iron_condor" and short_strike in (None, "") and long_strike in (None, ""):
                short_strike = f"P{tr.get('put_short_strike') or 'NA'}|C{tr.get('call_short_strike') or 'NA'}"
                long_strike = f"P{tr.get('put_long_strike') or 'NA'}|C{tr.get('call_long_strike') or 'NA'}"
            elif canonical_strategy == "butterfly_debit" and short_strike in (None, "") and long_strike in (None, ""):
                short_strike = tr.get("center_strike") or tr.get("short_strike") or "NA"
                long_strike = f"L{tr.get('lower_strike') or 'NA'}|U{tr.get('upper_strike') or 'NA'}"
            elif canonical_strategy in {"csp", "covered_call", "single", "long_call", "long_put"} and short_strike in (None, ""):
                short_strike = tr.get("strike") or "NA"
                long_strike = long_strike if long_strike not in (None, "") else "NA"

            tr["short_strike"] = short_strike
            tr["long_strike"] = long_strike

            provided_key = str(tr.get("trade_key") or "").strip()
            generated_key = trade_key(
                underlying=symbol,
                expiration=exp,
                spread_type=canonical_strategy,
                short_strike=short_strike,
                long_strike=long_strike,
                dte=dte,
            )
            canonical_key = canonicalize_trade_key(provided_key) if provided_key else generated_key
            if provided_key and canonical_key != provided_key:
                emit_validation_event(
                    severity="warn",
                    code="TRADE_KEY_NON_CANONICAL",
                    message="Report trade_key was rewritten to canonical format",
                    context={
                        "trade_key": canonical_key,
                        "provided_trade_key": provided_key,
                    },
                )
            tr["trade_key"] = canonical_key

        for idx, tr in enumerate(accepted, start=1):
            tr["rank_in_report"] = idx

        report_stats = self._build_report_stats(all_candidates, accepted)
        source_health = self.base_data_service.get_source_health_snapshot()
        top_reject_reasons = [{"reason": reason, "count": count} for reason, count in reject_reason_counts.most_common(15)]
        high_spread_candidates = [
            tr for tr in all_candidates
            if (_to_float(tr.get("bid_ask_spread_pct")) is not None and float(_to_float(tr.get("bid_ask_spread_pct"))) > 0.30)
        ]
        worst_spread_candidates = sorted(
            [tr for tr in all_candidates if _to_float(tr.get("bid_ask_spread_pct")) is not None],
            key=lambda t: _to_float(t.get("bid_ask_spread_pct")) or 0.0,
            reverse=True,
        )[:10]
        worst_spreads = [
            {
                "symbol": str(tr.get("underlying") or tr.get("underlying_symbol") or "").upper(),
                "expiration": tr.get("expiration"),
                "spread_type": tr.get("spread_type"),
                "short_strike": tr.get("short_strike"),
                "long_strike": tr.get("long_strike"),
                "bid_ask_spread_pct": _to_float(tr.get("bid_ask_spread_pct")),
                "spread_bid": _to_float(tr.get("spread_bid")),
                "spread_ask": _to_float(tr.get("spread_ask")),
                "spread_mid": _to_float(tr.get("spread_mid")),
            }
            for tr in worst_spread_candidates
        ]
        diagnostics = {
            "symbols_requested": targets,
            "provider": "tradier",
            "symbol_aliases": SYMBOL_ALIASES,
            "per_symbol": per_symbol,
            "top_reject_reasons": top_reject_reasons,
            "reject_reason_counts_by_symbol": {k: dict(v) for k, v in reject_reason_counts_by_symbol.items()},
            "high_spread_trade_count": len(high_spread_candidates),
            "worst_bid_ask_spreads": worst_spreads,
        }

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"analysis_{ts}.json"
        file_path = self.results_dir / filename
        await self._emit_progress(
            progress_callback,
            {
                "step": "writing_report",
                "message": f"Writing report file {filename}.",
            },
        )
        payload = {
            "report_stats": report_stats,
            "trades": accepted,
            "source_health": source_health,
            "diagnostics": diagnostics,
            "validation_mode": validation_mode,
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)

        await self._emit_progress(
            progress_callback,
            {
                "step": "pipeline_complete",
                "message": f"Completed report generation with {report_stats['accepted_trades']} accepted trades.",
            },
        )

        return {
            "filename": filename,
            "count_total": report_stats["total_candidates"],
            "count_after_gate": report_stats["accepted_trades"],
            "symbol": "MULTI_INDEX",
        }
