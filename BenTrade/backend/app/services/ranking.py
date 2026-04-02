from __future__ import annotations

import logging
from functools import cmp_to_key
from typing import Any

_log = logging.getLogger(__name__)


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

    edge = minmax_norm(ev_to_risk, 0.00, 0.30)
    ror = minmax_norm(return_on_risk, 0.05, 2.00)
    pop_norm = minmax_norm(pop, 0.25, 0.95)
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
    """Compute a 0–100 rank score from weighted components.

    Components and weights:
      edge (EV/risk)    0.30
      ror               0.22
      pop               0.20
      liquidity         0.18   (OI + volume + spread tightness)
      tqs               0.10   (omitted & re-weighted when absent)

    Liquidity impact comes ONLY from the weighted component.
    No additional multiplicative penalty is applied — that was removed
    to eliminate double-penalization (see scoring audit Finding #2).

    Returns: float in [0, 100] (rounded to 3 decimal places).
    """
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

    # Scale to 0–100 and clamp.
    return round(clamp(score * 100.0, 0.0, 100.0), 3)


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


# ═══════════════════════════════════════════════════════════════════════
# STRATEGY-AWARE RANKING v2
# ═══════════════════════════════════════════════════════════════════════
# Scores each candidate against strategy-appropriate criteria so that
# income (credit) and directional (debit) strategies compete on a
# universal composite score without hardcoded slot allocation.
# ═══════════════════════════════════════════════════════════════════════


# === STRATEGY CLASSIFICATION ===

INCOME_STRATEGIES = frozenset({
    "put_credit_spread", "call_credit_spread",
    "iron_condor", "iron_butterfly",
})
BUTTERFLY_STRATEGIES = frozenset({
    "butterfly_debit", "iron_butterfly",
})
DIRECTIONAL_STRATEGIES = frozenset({
    "put_debit", "call_debit",
})
CALENDAR_STRATEGIES = frozenset({
    "calendar_call_spread", "calendar_put_spread",
    "diagonal_call_spread", "diagonal_put_spread",
})


def classify_strategy(scanner_key: str) -> str:
    """Classify strategy into income, butterfly, directional, or calendar."""
    if scanner_key in INCOME_STRATEGIES:
        return "income"
    if scanner_key in BUTTERFLY_STRATEGIES:
        return "butterfly"
    if scanner_key in DIRECTIONAL_STRATEGIES:
        return "directional"
    if scanner_key in CALENDAR_STRATEGIES:
        return "calendar"
    return "unknown"


# === NORMALIZATION RANGES BY STRATEGY CLASS ===
# Each range defines [poor, excellent] — values at "poor" score 0,
# values at "excellent" score 100.

SCORING_PROFILES: dict[str, dict[str, Any]] = {
    "income": {
        # Weights — edge is primary signal
        "edge_weight": 0.35,
        "pop_weight": 0.25,
        "structure_weight": 0.15,
        "market_fit_weight": 0.15,
        "execution_weight": 0.10,

        # POP band (replaces linear pop_range normalization)
        "pop_ideal_low": 0.65,
        "pop_ideal_high": 0.85,
        "pop_below_penalty": 200.0,
        "pop_above_penalty": 150.0,

        # Edge — managed EV parameters
        "use_managed_ev": True,
        "managed_ror_positive_scale": 700.0,
        "managed_ror_negative_scale": 300.0,
        "managed_ev_base_score": 30.0,

        # Structure
        "short_delta_range": (0.08, 0.35),
        "short_delta_ideal": (0.15, 0.30),
        "dte_ideal": (21, 45),

        # Legacy ranges kept for fallback when managed EV is None
        "pop_range": (0.45, 0.85),
        "ev_to_risk_range": (0.02, 0.15),
        "credit_to_width_range": (0.05, 0.30),
    },
    "directional": {
        # Weights
        "edge_weight": 0.35,
        "pop_weight": 0.20,
        "structure_weight": 0.20,
        "market_fit_weight": 0.15,
        "execution_weight": 0.10,

        # POP band
        "pop_ideal_low": 0.35,
        "pop_ideal_high": 0.55,
        "pop_below_penalty": 200.0,
        "pop_above_penalty": 150.0,

        # Edge — managed EV
        "use_managed_ev": True,
        "managed_ror_positive_scale": 140.0,
        "managed_ror_negative_scale": 150.0,
        "managed_ev_base_score": 30.0,

        # Structure
        "breakeven_proximity_range": (0.05, 0.00),
        "dte_ideal": (14, 60),

        # Legacy (unused when managed EV active)
        "pop_range": (0.30, 0.60),
        "ev_to_risk_range": (0.20, 1.00),
        "credit_to_width_range": None,
    },
    "calendar": {
        # Calendar retains old scoring — managed EV is None for calendars
        "edge_weight": 0.25,
        "pop_weight": 0.20,
        "structure_weight": 0.20,
        "market_fit_weight": 0.20,
        "execution_weight": 0.15,

        # POP band
        "pop_ideal_low": 0.30,
        "pop_ideal_high": 0.60,
        "pop_below_penalty": 200.0,
        "pop_above_penalty": 150.0,

        # Calendar has no managed EV
        "use_managed_ev": False,

        # Legacy scoring still used
        "pop_range": (0.30, 0.60),
        "ev_to_risk_range": (0.05, 0.40),
        "credit_to_width_range": None,
        "dte_ideal": (21, 60),
    },
    "butterfly": {
        # Weights — structure matters more for butterflies
        "edge_weight": 0.30,
        "pop_weight": 0.20,
        "structure_weight": 0.25,
        "market_fit_weight": 0.15,
        "execution_weight": 0.10,

        # POP band — butterflies have low POP by nature
        "pop_ideal_low": 0.15,
        "pop_ideal_high": 0.45,
        "pop_below_penalty": 200.0,
        "pop_above_penalty": 150.0,

        # Edge — use managed EV but with butterfly adjustments
        "use_managed_ev": True,
        "managed_ror_positive_scale": 200.0,
        "managed_ror_negative_scale": 150.0,
        "managed_ev_base_score": 30.0,

        # Structure
        "dte_ideal": (14, 45),

        # Legacy fallback
        "pop_range": (0.15, 0.45),
        "ev_to_risk_range": (0.05, 0.40),
        "credit_to_width_range": None,
    },
    "unknown": {
        "edge_weight": 0.25,
        "pop_weight": 0.20,
        "structure_weight": 0.20,
        "market_fit_weight": 0.20,
        "execution_weight": 0.15,

        "pop_ideal_low": 0.30,
        "pop_ideal_high": 0.60,
        "pop_below_penalty": 200.0,
        "pop_above_penalty": 150.0,

        "use_managed_ev": False,

        "pop_range": (0.30, 0.60),
        "ev_to_risk_range": (0.20, 1.00),
        "credit_to_width_range": None,
        "dte_ideal": (14, 60),
    },
}


# === SCORING FUNCTIONS ===

def _norm_v2(value: float | None, low: float, high: float) -> float | None:
    """Normalize value to 0-100 between low and high bounds."""
    if value is None:
        return None
    if high == low:
        return 50.0
    score = ((value - low) / (high - low)) * 100
    return max(0.0, min(100.0, round(score, 1)))


def _compute_pop_band_score(
    pop: float | None,
    profile: dict[str, Any],
) -> float | None:
    """Score POP within an ideal band. Peak = 100 inside band, penalty outside.

    Unlike linear normalization (higher POP = higher score), band scoring
    penalizes BOTH too-low and too-high POP:
    - Too low: not enough wins to converge
    - Too high: credit too thin relative to risk (income) or
                too expensive / limited upside (directional)
    """
    if pop is None:
        return None

    ideal_low = profile.get("pop_ideal_low", 0.30)
    ideal_high = profile.get("pop_ideal_high", 0.60)
    below_penalty = profile.get("pop_below_penalty", 200.0)
    above_penalty = profile.get("pop_above_penalty", 150.0)

    if ideal_low <= pop <= ideal_high:
        return 100.0

    band_width = ideal_high - ideal_low
    if band_width <= 0:
        return 50.0  # safety fallback

    if pop < ideal_low:
        distance = ideal_low - pop
        penalty = (distance / band_width) * below_penalty
        return max(0.0, 100.0 - penalty)
    else:
        distance = pop - ideal_high
        penalty = (distance / band_width) * above_penalty
        return max(0.0, 100.0 - penalty)


def _compute_edge_score(
    m: dict[str, Any],
    legs: list[dict[str, Any]],
    strategy_class: str,
    profile: dict[str, Any],
    underlying_price: float | None = None,
) -> float:
    """Score edge quality. Uses managed EV when available, falls back to legacy."""
    use_managed = profile.get("use_managed_ev", False)

    if use_managed:
        ev_managed = safe_float(m.get("ev_managed"))
        managed_ror = safe_float(m.get("managed_expected_ror"))

        # If managed EV was not computed (None), fall back to legacy
        if ev_managed is None or managed_ror is None:
            return _compute_edge_score_legacy(m, legs, strategy_class, profile)

        base_score = profile.get("managed_ev_base_score", 30.0)

        if ev_managed < 0:
            # Negative managed EV: score 0-30 range
            neg_scale = profile.get("managed_ror_negative_scale", 300.0)
            raw = max(0.0, base_score + managed_ror * neg_scale)
        else:
            # Positive managed EV: score 30-100 range
            pos_scale = profile.get("managed_ror_positive_scale", 700.0)
            raw = min(100.0, base_score + managed_ror * pos_scale)

        # Width normalization: penalize extreme payoff asymmetry.
        # Ideal width range: ≤3% of underlying → no penalty.
        # Derived field: width_factor = f(width / underlying_price)
        width = safe_float(m.get("width"))
        up = underlying_price if underlying_price and underlying_price > 0 else None
        if width and up:
            width_pct = width / up
            if width_pct <= 0.03:
                width_factor = 1.0
            elif width_pct <= 0.05:
                # Linear: 3% → 1.0, 5% → 0.85
                width_factor = 1.0 - (width_pct - 0.03) * 7.5
            else:
                # Stronger: 5% → 0.85, 10% → 0.60
                width_factor = 0.85 - (width_pct - 0.05) * 5.0
            width_factor = max(0.50, min(1.0, width_factor))
            raw *= width_factor

        return raw

    return _compute_edge_score_legacy(m, legs, strategy_class, profile)


def _compute_edge_score_legacy(
    m: dict[str, Any],
    legs: list[dict[str, Any]],
    strategy_class: str,
    profile: dict[str, Any],
) -> float:
    """Legacy edge scoring using credit-to-width and binary ev_to_risk."""
    if strategy_class == "income":
        credit = safe_float(m.get("net_credit"), 0.0)
        width = safe_float(m.get("width"), 0.0)
        credit_to_width = credit / width if width > 0 else 0

        ctw_range = profile.get("credit_to_width_range", (0.10, 0.35))
        ctw_score = _norm_v2(credit_to_width, ctw_range[0], ctw_range[1])

        ev = safe_float(m.get("ev"), 0.0)
        max_loss = abs(safe_float(m.get("max_loss"), 0.0))
        ev_to_risk = ev / max_loss if max_loss > 0 else 0
        evr_range = profile.get("ev_to_risk_range", (0.02, 0.15))
        evr_score = _norm_v2(ev_to_risk, evr_range[0], evr_range[1])

        if ctw_score is not None and evr_score is not None:
            return ctw_score * 0.6 + evr_score * 0.4
        return evr_score if evr_score is not None else 0.0

    # Directional / calendar: expected_ror is the key metric
    expected_ror = safe_float(m.get("expected_ror"), 0.0)
    if expected_ror == 0:
        ev = safe_float(m.get("ev"), 0.0)
        max_loss = abs(safe_float(m.get("max_loss"), 0.0))
        expected_ror = ev / max_loss if max_loss > 0 else 0

    evr_range = profile.get("ev_to_risk_range", (0.10, 0.60))
    return _norm_v2(expected_ror, evr_range[0], evr_range[1]) or 0.0


def _compute_structure_score(
    candidate: dict[str, Any],
    m: dict[str, Any],
    legs: list[dict[str, Any]],
    strategy_class: str,
    profile: dict[str, Any],
) -> float:
    """Score the structural quality of the trade construction.

    Input fields:
      candidate.dte, math.dte, legs[].side, legs[].delta,
      candidate.underlying_price, math.breakeven, math.width
    Formula:
      average of applicable sub-scores (DTE, delta/breakeven, width)
    """
    scores: list[float] = []

    # DTE appropriateness
    dte = safe_float(candidate.get("dte")) or safe_float(m.get("dte"))
    dte_ideal = profile.get("dte_ideal", (21, 45))
    if dte is not None:
        if dte_ideal[0] <= dte <= dte_ideal[1]:
            scores.append(100.0)
        elif dte < dte_ideal[0]:
            scores.append(max(0.0, 100 - (dte_ideal[0] - dte) * 5))
        else:
            scores.append(max(0.0, 100 - (dte - dte_ideal[1]) * 2))

    if strategy_class == "income":
        # Short leg delta — ideal is 0.15-0.30
        short_legs = [
            l for l in legs
            if l.get("side") in ("short", "sell_to_open")
        ]
        if short_legs:
            short_delta = abs(safe_float(short_legs[0].get("delta"), 0.0))
            ideal = profile.get("short_delta_ideal", (0.15, 0.30))
            if ideal[0] <= short_delta <= ideal[1]:
                scores.append(100.0)
            elif short_delta < ideal[0]:
                scores.append(max(0.0, 100 - (ideal[0] - short_delta) * 500))
            else:
                scores.append(max(0.0, 100 - (short_delta - ideal[1]) * 300))

    elif strategy_class == "directional":
        # Breakeven proximity to current price
        underlying = safe_float(candidate.get("underlying_price"), 0.0)
        breakeven = m.get("breakeven")
        if underlying > 0 and breakeven:
            be = breakeven[0] if isinstance(breakeven, list) else breakeven
            be = safe_float(be)
            if be is not None:
                proximity = abs(be - underlying) / underlying
                prox_score = _norm_v2(1 - proximity, 0.95, 1.00)
                scores.append(prox_score if prox_score is not None else 50.0)

    # Width reasonableness (not too narrow, not too wide)
    width = safe_float(m.get("width"), 0.0)
    underlying = safe_float(candidate.get("underlying_price"), 100.0)
    width_pct = width / underlying if underlying > 0 else 0
    if 0.01 <= width_pct <= 0.10:
        scores.append(80.0)
    elif width_pct > 0.10:
        scores.append(50.0)
    elif width > 0:
        scores.append(30.0)

    return sum(scores) / len(scores) if scores else 50.0


# Strategy-regime compatibility matrix
# (strategy_class, regime) → base score
# Income is BenTrade's core strategy: it scores highest in NEUTRAL.
# Directional is a complement for trending regimes (RISK_ON / RISK_OFF).
_REGIME_FIT: dict[tuple[str, str], float] = {
    ("income", "NEUTRAL"): 80,
    ("income", "RISK_ON"): 65,
    ("income", "RISK_OFF"): 60,
    ("directional", "NEUTRAL"): 55,
    ("directional", "RISK_ON"): 80,
    ("directional", "RISK_OFF"): 75,
    ("calendar", "NEUTRAL"): 80,
    ("calendar", "RISK_ON"): 60,
    ("calendar", "RISK_OFF"): 50,
    ("butterfly", "NEUTRAL"): 70,
    ("butterfly", "RISK_ON"): 45,
    ("butterfly", "RISK_OFF"): 45,
}


def _compute_market_fit(
    candidate: dict[str, Any],
    strategy_class: str,
    regime_label: str,
) -> float:
    """Score how well this strategy fits the current market regime.

    Input fields:
      candidate.regime_alignment, candidate.event_risk
    Formula:
      base from _REGIME_FIT matrix, adjusted by alignment and event risk
    """
    regime = (regime_label or "NEUTRAL").upper()
    alignment = (candidate.get("regime_alignment") or "neutral").lower()

    base = _REGIME_FIT.get((strategy_class, regime), 60.0)

    if alignment == "aligned":
        base = min(100.0, base + 10)
    elif alignment == "misaligned":
        base = max(0.0, base - 15)

    event_risk = candidate.get("event_risk", "unknown")
    if event_risk == "high":
        base = max(0.0, base - 20)
    elif event_risk == "elevated":
        base = max(0.0, base - 10)

    return base


def _compute_execution_quality(legs: list[dict[str, Any]]) -> float:
    """Score the liquidity and fillability of the trade.

    Input fields (per leg):
      bid, ask, volume, open_interest
    Formula:
      average of per-leg sub-scores (spread tightness, volume, OI)
    """
    if not legs:
        return 50.0

    scores: list[float] = []
    for leg in legs:
        bid = safe_float(leg.get("bid"), 0.0)
        ask = safe_float(leg.get("ask"), 0.0)
        volume = safe_float(leg.get("volume"), 0.0)
        oi = safe_float(leg.get("open_interest"), 0.0)

        # Bid-ask spread tightness
        if bid > 0 and ask > 0:
            spread_pct = (ask - bid) / ((bid + ask) / 2)
            s = _norm_v2(1 - spread_pct, 0.70, 0.95)
            scores.append(s if s is not None else 50.0)

        # Volume: >1000 = excellent, <10 = poor
        if volume > 0:
            scores.append(min(100.0, volume / 10))

        # Open interest: >5000 = excellent, <100 = poor
        if oi > 0:
            scores.append(min(100.0, oi / 50))

    return sum(scores) / len(scores) if scores else 30.0


def score_candidate(candidate: dict[str, Any], regime_label: str = "NEUTRAL") -> dict[str, Any]:
    """Score a candidate using strategy-appropriate criteria.

    Returns a dict with component scores (0-100), weights, and composite score.
    """
    scanner_key = candidate.get("scanner_key", "")
    strategy_class = classify_strategy(scanner_key)
    profile = SCORING_PROFILES[strategy_class]

    m = candidate.get("math") or {}
    legs = candidate.get("legs") or []

    # === Component 1: Probability Quality (band scoring) ===
    pop = safe_float(m.get("pop"))
    prob_score = _compute_pop_band_score(pop, profile)

    # === Component 2: Edge Quality ===
    edge_score = _compute_edge_score(
        m, legs, strategy_class, profile,
        underlying_price=safe_float(candidate.get("underlying_price")),
    )

    # === Component 3: Structure Quality ===
    structure_score = _compute_structure_score(candidate, m, legs, strategy_class, profile)

    # === Component 4: Market Fit ===
    market_score = _compute_market_fit(candidate, strategy_class, regime_label)

    # === Component 5: Execution Quality ===
    exec_score = _compute_execution_quality(legs)

    # === Composite Score (weighted average) ===
    components = {
        "probability": {"score": prob_score, "weight": profile["pop_weight"]},
        "edge": {"score": edge_score, "weight": profile["edge_weight"]},
        "structure": {"score": structure_score, "weight": profile["structure_weight"]},
        "market_fit": {"score": market_score, "weight": profile["market_fit_weight"]},
        "execution": {"score": exec_score, "weight": profile["execution_weight"]},
    }

    total_weight = 0.0
    total_score = 0.0
    for comp in components.values():
        s = comp["score"]
        w = comp["weight"]
        if s is not None:
            total_score += s * w
            total_weight += w

    composite = round(total_score / total_weight, 2) if total_weight > 0 else 0.0

    return {
        "composite_score": composite,
        "strategy_class": strategy_class,
        "components": components,
    }


def rank_candidates(
    candidates: list[dict[str, Any]],
    regime_label: str = "NEUTRAL",
) -> list[dict[str, Any]]:
    """Score and rank all candidates using strategy-aware criteria.

    Returns candidates sorted by composite_score descending.
    No hardcoded slot allocation — the best trades for the current
    conditions naturally rise to the top.
    """
    for cand in candidates:
        result = score_candidate(cand, regime_label)
        cand["ranking"] = result
        cand["composite_score"] = result["composite_score"]
        cand["strategy_class"] = result["strategy_class"]

    ranked = sorted(
        candidates,
        key=lambda c: (c.get("composite_score", 0), c.get("symbol", "")),
        reverse=True,
    )

    for i, cand in enumerate(ranked):
        cand["rank"] = i + 1

    # Log the distribution
    top10 = ranked[:10]
    income_count = sum(1 for c in top10 if c.get("strategy_class") == "income")
    directional_count = sum(1 for c in top10 if c.get("strategy_class") == "directional")
    calendar_count = sum(1 for c in top10 if c.get("strategy_class") == "calendar")
    _log.info(
        "event=ranking_complete total=%d top10_income=%d top10_directional=%d top10_calendar=%d "
        "top1_score=%.1f top1_strategy=%s top10_score=%.1f",
        len(ranked), income_count, directional_count, calendar_count,
        ranked[0]["composite_score"] if ranked else 0,
        ranked[0].get("scanner_key") if ranked else "none",
        ranked[9]["composite_score"] if len(ranked) >= 10 else 0,
    )

    return ranked
