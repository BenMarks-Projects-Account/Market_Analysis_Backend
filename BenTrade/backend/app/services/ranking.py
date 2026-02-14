from __future__ import annotations

from functools import cmp_to_key
from typing import Any


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def safe_float(x, default=None):
    if x in (None, ""):
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def minmax_norm(x, lo, hi):
    value = safe_float(x)
    if value is None:
        return 0.0
    lo_v = safe_float(lo, 0.0)
    hi_v = safe_float(hi, 1.0)
    if hi_v <= lo_v:
        return 0.0
    clipped = clamp(value, lo_v, hi_v)
    return (clipped - lo_v) / (hi_v - lo_v)


def _get_pop(trade: dict[str, Any]) -> float:
    pop = safe_float(trade.get("p_win_used"))
    if pop is None:
        pop = safe_float(trade.get("pop_delta_approx"))
    return pop if pop is not None else 0.0


def _get_ev_to_risk(trade: dict[str, Any]) -> float:
    direct = safe_float(trade.get("ev_to_risk"))
    if direct is not None:
        return direct

    ev_per_share = safe_float(trade.get("ev_per_share"))
    if ev_per_share is None:
        ev_per_share = safe_float(trade.get("expected_value"))
    max_loss_per_share = safe_float(trade.get("max_loss_per_share"))
    if max_loss_per_share is None:
        max_loss_per_share = safe_float(trade.get("max_loss"))

    if ev_per_share is None or max_loss_per_share is None or max_loss_per_share <= 0:
        return 0.0
    return ev_per_share / max_loss_per_share


def compute_liquidity_score(trade: dict[str, Any]) -> float:
    open_interest = safe_float(trade.get("open_interest"))
    volume = safe_float(trade.get("volume"))
    bid_ask_spread_pct = safe_float(trade.get("bid_ask_spread_pct"))

    oi_score = clamp((open_interest if open_interest is not None else 0.0) / 5000.0)
    vol_score = clamp((volume if volume is not None else 0.0) / 5000.0)
    spread_penalty = clamp((bid_ask_spread_pct if bid_ask_spread_pct is not None else 0.30) / 0.30)

    return clamp(0.45 * oi_score + 0.35 * vol_score + 0.20 * (1.0 - spread_penalty))


def _compute_rank_components(trade: dict[str, Any]) -> dict[str, float | None]:
    ev_to_risk = _get_ev_to_risk(trade)
    return_on_risk = safe_float(trade.get("return_on_risk"), 0.0)
    pop = _get_pop(trade)
    tqs_raw = safe_float(trade.get("trade_quality_score"))

    edge = minmax_norm(ev_to_risk, 0.00, 0.05)
    ror = minmax_norm(return_on_risk, 0.05, 0.50)
    pop_norm = minmax_norm(pop, 0.50, 0.95)
    liquidity = compute_liquidity_score(trade)
    tqs_norm = minmax_norm(tqs_raw, 0.40, 0.85) if tqs_raw is not None else None

    return {
        "edge": edge,
        "ror": ror,
        "pop": pop_norm,
        "liquidity": liquidity,
        "tqs": tqs_norm,
        "raw_edge": ev_to_risk,
        "raw_pop": pop,
    }


def compute_rank_score(trade: dict[str, Any]) -> float:
    comps = _compute_rank_components(trade)
    weighted_terms: list[tuple[float, float]] = [
        (0.30, float(comps["edge"])),
        (0.22, float(comps["ror"])),
        (0.20, float(comps["pop"])),
        (0.18, float(comps["liquidity"])),
    ]
    if comps["tqs"] is not None:
        weighted_terms.append((0.10, float(comps["tqs"])))

    total_weight = sum(weight for weight, _ in weighted_terms)
    if total_weight <= 0:
        return 0.0

    score = sum(weight * value for weight, value in weighted_terms) / total_weight

    spread_pct = safe_float(trade.get("bid_ask_spread_pct"), 9.99)
    liquidity_penalty = clamp(((spread_pct - 0.30) / 0.70), 0.0, 1.0)
    score = score * (1.0 - 0.75 * liquidity_penalty)

    return round(clamp(score), 6)


def _trade_tie_break_tuple(trade: dict[str, Any]) -> tuple[float, float, float, float, str, float, float]:
    comps = _compute_rank_components(trade)
    edge = float(comps["edge"])
    pop = float(comps["pop"])
    spread = safe_float(trade.get("bid_ask_spread_pct"), 1.0)
    open_interest = safe_float(trade.get("open_interest"), 0.0)
    symbol = str(trade.get("underlying") or trade.get("underlying_symbol") or "").upper()
    short_strike = safe_float(trade.get("short_strike"), 0.0)
    long_strike = safe_float(trade.get("long_strike"), 0.0)

    return (edge, pop, -spread, open_interest, symbol, short_strike, long_strike)


def compare_trades_for_rank(a: dict[str, Any], b: dict[str, Any], eps: float = 1e-9) -> int:
    a_rank = safe_float(a.get("rank_score"), 0.0)
    b_rank = safe_float(b.get("rank_score"), 0.0)
    if abs(a_rank - b_rank) > eps:
        return -1 if a_rank > b_rank else 1

    a_tb = _trade_tie_break_tuple(a)
    b_tb = _trade_tie_break_tuple(b)
    if a_tb > b_tb:
        return -1
    if a_tb < b_tb:
        return 1
    return 0


def sort_trades_by_rank(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for trade in trades:
        trade["rank_score"] = compute_rank_score(trade)
    return sorted(trades, key=cmp_to_key(compare_trades_for_rank))
