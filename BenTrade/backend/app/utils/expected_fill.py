"""Expected Fill pricing model for BenTrade scanner pipeline.

Computes a realistic fill price by blending mid and natural (worst-case)
prices with strategy-aware weights.  Provides fill-based economics
(max_profit_fill, max_loss_fill, ror_fill, ev_fill, ev_to_risk_fill) that
sit alongside the existing mid-based metrics.

Formula
-------
    expected_fill_price = w × mid + (1 − w) × natural

where *w* is a base weight that degrades with:
  • leg count (more legs → harder to fill at mid)
  • illiquidity (wider spreads or lower liquidity_score → worse fills)

Conservative clamp: the expected fill is NEVER better than mid.
  • For credits: fill ≤ mid  (you can't receive more than mid)
  • For debits:  fill ≥ mid  (you can't pay less than mid)

All input fields and formulae are documented inline per copilot-instructions
non-negotiable #1 (traceability).
"""

from __future__ import annotations

import math
from typing import Any

# ── Strategy-aware defaults ──────────────────────────────────────────────
# Keys: strategy_id tokens that appear in trade["strategy"] or
# trade["spread_type"].
#
# base_w:          starting mid-weight before penalties
# leg_penalty:     w reduction per leg above 2
# min_w:           absolute floor for w (never below this)
# spread_pct_k:    sensitivity to bid_ask_spread_pct degradation
#                  w_adj = w − spread_pct_k × bid_ask_spread_pct
# liq_boost:       bonus w when liquidity_score > 0.6

FILL_STRATEGY_DEFAULTS: dict[str, dict[str, float]] = {
    # 2-leg credit spread (put credit spread)
    "credit_spread": {
        "base_w": 0.70,
        "leg_penalty": 0.00,
        "min_w": 0.30,
        "spread_pct_k": 0.10,
        "liq_boost": 0.05,
    },
    # 2-leg debit spreads (call/put debit)
    "debit_spread": {
        "base_w": 0.65,
        "leg_penalty": 0.00,
        "min_w": 0.30,
        "spread_pct_k": 0.12,
        "liq_boost": 0.05,
    },
    # 4-leg iron condor
    "iron_condor": {
        "base_w": 0.55,
        "leg_penalty": 0.05,
        "min_w": 0.25,
        "spread_pct_k": 0.08,
        "liq_boost": 0.05,
    },
    # 3-leg debit butterfly
    "debit_butterfly": {
        "base_w": 0.50,
        "leg_penalty": 0.05,
        "min_w": 0.25,
        "spread_pct_k": 0.10,
        "liq_boost": 0.05,
    },
    # 4-leg iron butterfly
    "iron_butterfly": {
        "base_w": 0.50,
        "leg_penalty": 0.05,
        "min_w": 0.20,
        "spread_pct_k": 0.10,
        "liq_boost": 0.05,
    },
}

# Fallback defaults when strategy_id doesn't match any key above.
_FALLBACK_DEFAULTS: dict[str, float] = {
    "base_w": 0.60,
    "leg_penalty": 0.05,
    "min_w": 0.25,
    "spread_pct_k": 0.10,
    "liq_boost": 0.05,
}


def _resolve_strategy_key(strategy: str | None, spread_type: str | None) -> str:
    """Map a trade's strategy/spread_type to a FILL_STRATEGY_DEFAULTS key.

    Returns the best-matching key, or 'fallback' if none match.
    """
    candidates = [
        s for s in (strategy, spread_type) if s
    ]
    for c in candidates:
        cl = c.lower()
        if "iron_condor" in cl:
            return "iron_condor"
        if "iron_butterfly" in cl:
            return "iron_butterfly"
        if "debit" in cl and "butterfly" in cl:
            return "debit_butterfly"
        if "butterfly" in cl:
            # Generic butterfly → debit butterfly default
            return "debit_butterfly"
        if "credit" in cl:
            return "credit_spread"
        if "debit" in cl:
            return "debit_spread"
    return "fallback"


def _safe_float(v: Any) -> float | None:
    """Convert to float; return None on failure."""
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────
# Core fill computation
# ─────────────────────────────────────────────────────────────────────────

def compute_expected_fill(
    spread_mid: float | None,
    spread_natural: float | None,
    *,
    strategy: str | None = None,
    spread_type: str | None = None,
    is_credit: bool = True,
    leg_count: int = 2,
    liquidity_score: float | None = None,
    bid_ask_spread_pct: float | None = None,
) -> dict[str, Any] | None:
    """Compute expected fill price and metadata.

    Parameters
    ----------
    spread_mid : mid-based spread price (best estimate)
    spread_natural : worst-case fill price (credits: bid-side; debits: ask-side)
    strategy : trade["strategy"] value
    spread_type : trade["spread_type"] value
    is_credit : True for credit strategies, False for debit
    leg_count : number of legs (derived from len(trade["legs"]))
    liquidity_score : 0-1 composite liquidity score (if available)
    bid_ask_spread_pct : spread-level bid/ask spread pct (if available)

    Returns
    -------
    dict with:
        expected_fill_price : float  — the blended fill price
        expected_fill_weight_w : float  — the weight used (0-1)
        expected_fill_basis : str  — "expected_fill"
        slippage_vs_mid : float  — |fill − mid|
        slippage_pct : float  — slippage_vs_mid / |mid| (0 if mid is 0)
        fill_confidence : str  — "high" / "medium" / "low"
        _fill_detail : dict  — debug trace with all inputs and intermediates
    Returns None when insufficient data to compute a fill.
    """
    mid = _safe_float(spread_mid)
    natural = _safe_float(spread_natural)

    # Both mid and natural are required for blending
    if mid is None or natural is None:
        return None

    # Resolve strategy defaults
    key = _resolve_strategy_key(strategy, spread_type)
    defaults = FILL_STRATEGY_DEFAULTS.get(key, _FALLBACK_DEFAULTS)

    base_w = defaults["base_w"]
    leg_pen = defaults["leg_penalty"]
    min_w = defaults["min_w"]
    spread_k = defaults["spread_pct_k"]
    liq_boost = defaults["liq_boost"]

    # ── Step 1: leg penalty ─────────────────────────────────────────────
    # Extra legs beyond 2 degrade fill quality
    extra_legs = max(0, leg_count - 2)
    w = base_w - (leg_pen * extra_legs)

    # ── Step 2: bid-ask spread penalty ──────────────────────────────────
    # Higher spread % → worse expected fill
    bap = _safe_float(bid_ask_spread_pct)
    if bap is not None and bap > 0:
        w -= spread_k * bap

    # ── Step 3: liquidity boost ─────────────────────────────────────────
    # Good liquidity → slightly better fills
    liq = _safe_float(liquidity_score)
    if liq is not None and liq > 0.6:
        w += liq_boost * (liq - 0.6) / 0.4  # scale 0→liq_boost over 0.6→1.0

    # ── Step 4: clamp to [min_w, 1.0] ───────────────────────────────────
    w = max(min_w, min(1.0, w))

    # ── Step 5: compute blended fill ────────────────────────────────────
    # expected_fill_price = w × mid + (1 − w) × natural
    raw_fill = w * mid + (1.0 - w) * natural

    # ── Step 6: conservative clamp ──────────────────────────────────────
    # Credits: fill ≤ mid  (natural ≤ mid for a credit; fill between them)
    # Debits:  fill ≥ mid  (natural ≥ mid for a debit; fill between them)
    if is_credit:
        expected_fill = min(raw_fill, mid)
    else:
        expected_fill = max(raw_fill, mid)

    expected_fill = round(expected_fill, 6)

    # ── Slippage metrics ────────────────────────────────────────────────
    slippage_vs_mid = round(abs(expected_fill - mid), 6)
    slippage_pct = round(slippage_vs_mid / abs(mid), 6) if abs(mid) > 1e-9 else 0.0

    # ── Fill confidence ─────────────────────────────────────────────────
    # Classification based on w value
    if w >= 0.60:
        fill_confidence = "high"
    elif w >= 0.40:
        fill_confidence = "medium"
    else:
        fill_confidence = "low"

    return {
        "expected_fill_price": expected_fill,
        "expected_fill_weight_w": round(w, 4),
        "expected_fill_basis": "expected_fill",
        "slippage_vs_mid": slippage_vs_mid,
        "slippage_pct": slippage_pct,
        "fill_confidence": fill_confidence,
        "_fill_detail": {
            "strategy_key": key,
            "base_w": base_w,
            "leg_count": leg_count,
            "leg_penalty_applied": round(leg_pen * extra_legs, 4),
            "spread_pct_input": bap,
            "spread_pct_penalty": round(spread_k * (bap or 0.0), 4),
            "liquidity_score_input": liq,
            "liquidity_boost_applied": round(
                liq_boost * max(0.0, ((liq or 0.0) - 0.6)) / 0.4, 4
            ) if liq is not None and liq > 0.6 else 0.0,
            "w_before_clamp": round(w, 6),  # Already clamped above but store pre-round
            "w_final": round(w, 4),
            "mid": mid,
            "natural": natural,
            "raw_fill": round(raw_fill, 6),
            "is_credit": is_credit,
            "clamped": raw_fill != expected_fill,
        },
    }


# ─────────────────────────────────────────────────────────────────────────
# Fill-based economics recomputation
# ─────────────────────────────────────────────────────────────────────────

def recompute_fill_economics(
    trade: dict[str, Any],
    expected_fill_price: float,
    is_credit: bool,
) -> dict[str, Any]:
    """Recompute economics using the expected fill price.

    For credit strategies:
        max_profit_fill = expected_fill_price × 100
        max_loss_fill   = (width − expected_fill_price) × 100
        ror_fill        = max_profit_fill / max_loss_fill
        ev_fill         = pop × max_profit_fill − (1−pop) × max_loss_fill
        ev_to_risk_fill = ev_fill / max_loss_fill

    For debit strategies:
        max_profit_fill = (width − expected_fill_price) × 100
        max_loss_fill   = expected_fill_price × 100
        ror_fill        = max_profit_fill / max_loss_fill
        ev_fill         = pop × max_profit_fill − (1−pop) × max_loss_fill
        ev_to_risk_fill = ev_fill / max_loss_fill

    Parameters
    ----------
    trade : enriched trade dict (must have 'width' and 'p_win_used')
    expected_fill_price : the fill price from compute_expected_fill()
    is_credit : True for credit strategies

    Returns
    -------
    dict of fill-based metrics to merge into the trade dict.
    """
    width = _safe_float(trade.get("width") or trade.get("wing_width"))
    pop = _safe_float(trade.get("p_win_used"))
    fill = expected_fill_price

    result: dict[str, Any] = {}

    if width is None or width <= 0 or fill is None:
        # Can't compute fill economics without width
        result["max_profit_fill"] = None
        result["max_loss_fill"] = None
        result["ror_fill"] = None
        result["ev_fill"] = None
        result["ev_to_risk_fill"] = None
        return result

    if is_credit:
        # Credit strategy: we receive the fill as premium
        max_profit_fill = fill * 100.0
        max_loss_fill = max((width - fill), 0.0) * 100.0
    else:
        # Debit strategy: we pay the fill
        max_profit_fill = max((width - fill), 0.0) * 100.0
        max_loss_fill = fill * 100.0

    ror_fill = (max_profit_fill / max_loss_fill) if max_loss_fill > 0 else None

    if pop is not None and max_profit_fill is not None and max_loss_fill is not None:
        p_loss = 1.0 - pop
        ev_fill = (pop * max_profit_fill) - (p_loss * max_loss_fill)
        ev_to_risk_fill = (ev_fill / max_loss_fill) if max_loss_fill > 0 else None
    else:
        ev_fill = None
        ev_to_risk_fill = None

    result["max_profit_fill"] = round(max_profit_fill, 2) if max_profit_fill is not None else None
    result["max_loss_fill"] = round(max_loss_fill, 2) if max_loss_fill is not None else None
    result["ror_fill"] = round(ror_fill, 6) if ror_fill is not None else None
    result["ev_fill"] = round(ev_fill, 2) if ev_fill is not None else None
    result["ev_to_risk_fill"] = round(ev_to_risk_fill, 6) if ev_to_risk_fill is not None else None

    return result


# ─────────────────────────────────────────────────────────────────────────
# Convenience: apply fill model to a trade dict in-place
# ─────────────────────────────────────────────────────────────────────────

def apply_expected_fill(trade: dict[str, Any]) -> dict[str, Any] | None:
    """Apply expected fill computation to an enriched trade dict.

    Reads spread_mid, spread_natural (or derives them from legs),
    computes fill + economics, and merges fields into the trade dict.

    Returns the fill result dict (or None if fill couldn't be computed).
    The trade dict is mutated in-place with all fill fields.
    """
    # ── Determine credit vs debit ───────────────────────────────────────
    strategy = trade.get("strategy") or ""
    spread_type = trade.get("spread_type") or ""

    is_credit = _is_credit_strategy(strategy, spread_type)

    # ── Resolve spread_mid and spread_natural ───────────────────────────
    # Different strategies store these differently:
    #   credit_spread:   spread_mid, spread_bid (=natural for credit)
    #   iron_condor:     net_credit (mid), spread_bid (natural)
    #   debit_spreads:   spread_mid, spread_ask (=natural for debit)
    #   butterflies:     spread_mid, spread_natural (explicit)
    spread_mid = _safe_float(trade.get("spread_mid"))
    spread_natural = _safe_float(trade.get("spread_natural"))

    # For strategies that don't have explicit spread_natural, derive it:
    if spread_natural is None:
        if is_credit:
            # For credits, natural = spread_bid (what you'd receive at worst)
            spread_natural = _safe_float(trade.get("spread_bid"))
        else:
            # For debits, natural = spread_ask (what you'd pay at worst)
            spread_natural = _safe_float(trade.get("spread_ask"))

    # For credit_spread and iron_condor, spread_mid may not be an explicit
    # field; derive from net_credit if needed (since net_credit is mid-based
    # for credit_spread with default basis, and always mid for IC).
    if spread_mid is None and is_credit:
        spread_mid = _safe_float(trade.get("net_credit"))

    # For debit_spreads, spread_mid is always present.  But as fallback:
    if spread_mid is None and not is_credit:
        spread_mid = _safe_float(trade.get("spread_mid"))
        if spread_mid is None:
            spread_mid = _safe_float(trade.get("net_debit"))

    # ── Determine leg count ─────────────────────────────────────────────
    legs = trade.get("legs")
    if isinstance(legs, list):
        leg_count = len(legs)
    else:
        # Fallback: infer from strategy
        key = _resolve_strategy_key(strategy, spread_type)
        if key in ("iron_condor", "iron_butterfly"):
            leg_count = 4
        elif key == "debit_butterfly":
            leg_count = 3
        else:
            leg_count = 2

    # ── Liquidity inputs ────────────────────────────────────────────────
    liquidity_score = _safe_float(trade.get("liquidity_score"))
    bid_ask_spread_pct = _safe_float(trade.get("bid_ask_spread_pct"))

    # ── Compute fill ────────────────────────────────────────────────────
    fill_result = compute_expected_fill(
        spread_mid=spread_mid,
        spread_natural=spread_natural,
        strategy=strategy,
        spread_type=spread_type,
        is_credit=is_credit,
        leg_count=leg_count,
        liquidity_score=liquidity_score,
        bid_ask_spread_pct=bid_ask_spread_pct,
    )

    if fill_result is None:
        # Couldn't compute fill — leave trade unchanged, add sentinel
        trade["expected_fill_price"] = None
        trade["expected_fill_basis"] = None
        trade["_fill_unavailable"] = True
        return None

    # ── Merge fill fields into trade ────────────────────────────────────
    trade["expected_fill_price"] = fill_result["expected_fill_price"]
    trade["expected_fill_weight_w"] = fill_result["expected_fill_weight_w"]
    trade["expected_fill_basis"] = fill_result["expected_fill_basis"]
    trade["slippage_vs_mid"] = fill_result["slippage_vs_mid"]
    trade["slippage_pct"] = fill_result["slippage_pct"]
    trade["fill_confidence"] = fill_result["fill_confidence"]
    trade["_fill_detail"] = fill_result["_fill_detail"]

    # ── Preserve mid-based metrics with _mid suffix ─────────────────────
    # Only set *_mid aliases if not already present (idempotent)
    if "max_profit_mid" not in trade:
        trade["max_profit_mid"] = trade.get("max_profit")
    if "max_loss_mid" not in trade:
        trade["max_loss_mid"] = trade.get("max_loss")
    if "ror_mid" not in trade:
        trade["ror_mid"] = trade.get("return_on_risk")
    if "ev_mid" not in trade:
        _ev = trade.get("ev_per_contract") or trade.get("expected_value")
        trade["ev_mid"] = _ev
    if "ev_to_risk_mid" not in trade:
        trade["ev_to_risk_mid"] = trade.get("ev_to_risk")

    # ── Compute and merge fill-based economics ──────────────────────────
    fill_econ = recompute_fill_economics(
        trade,
        fill_result["expected_fill_price"],
        is_credit=is_credit,
    )
    trade.update(fill_econ)

    return fill_result


def _is_credit_strategy(strategy: str, spread_type: str) -> bool:
    """Determine whether a strategy is credit or debit.

    Credit strategies: put_credit_spread, iron_condor, iron_butterfly
    Debit strategies: call_debit, put_debit, debit_call_butterfly, debit_put_butterfly
    """
    for s in (strategy, spread_type):
        sl = (s or "").lower()
        if "credit" in sl or "iron_condor" in sl or "iron_butterfly" in sl:
            return True
        if "debit" in sl:
            return False
    # Default to credit (conservative)
    return True


# ─────────────────────────────────────────────────────────────────────────
# Trace-level aggregation helpers (for strategy_service filter_trace)
# ─────────────────────────────────────────────────────────────────────────

def build_fill_trace(
    enriched_trades: list[dict[str, Any]],
    passed_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build fill_model_summary + fill_impact + fill_samples for filter trace.

    Parameters
    ----------
    enriched_trades : all trades AFTER enrichment + fill computation
    passed_trades : trades that passed evaluate gates

    Returns
    -------
    dict with keys:
        fill_model_summary : aggregate stats (w distribution, slippage, basis)
        fill_impact : comparison of mid vs fill gate outcomes
        fill_samples : representative sample trades for diagnostics
    """
    # -- fill_model_summary --
    w_values: list[float] = []
    slippage_values: list[float] = []
    slippage_pct_values: list[float] = []
    confidence_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "unavailable": 0}
    fill_available_count = 0

    for t in enriched_trades:
        fp = _safe_float(t.get("expected_fill_price"))
        if fp is not None:
            fill_available_count += 1
            w = _safe_float(t.get("expected_fill_weight_w"))
            if w is not None:
                w_values.append(w)
            slip = _safe_float(t.get("slippage_vs_mid"))
            if slip is not None:
                slippage_values.append(slip)
            slip_pct = _safe_float(t.get("slippage_pct"))
            if slip_pct is not None:
                slippage_pct_values.append(slip_pct)
            conf = t.get("fill_confidence", "unavailable")
            confidence_counts[conf] = confidence_counts.get(conf, 0) + 1
        else:
            confidence_counts["unavailable"] += 1

    def _stats(vals: list[float]) -> dict[str, float | None]:
        if not vals:
            return {"min": None, "max": None, "mean": None, "median": None}
        s = sorted(vals)
        n = len(s)
        return {
            "min": round(s[0], 6),
            "max": round(s[-1], 6),
            "mean": round(sum(s) / n, 6),
            "median": round(s[n // 2], 6),
        }

    fill_model_summary = {
        "total_enriched": len(enriched_trades),
        "fill_computed": fill_available_count,
        "fill_unavailable": len(enriched_trades) - fill_available_count,
        "w_stats": _stats(w_values),
        "slippage_stats": _stats(slippage_values),
        "slippage_pct_stats": _stats(slippage_pct_values),
        "confidence_distribution": confidence_counts,
    }

    # -- fill_impact: trades that would pass/fail differently on fill vs mid --
    fill_impact = _compute_fill_impact(enriched_trades)

    # -- fill_samples: representative trades for diagnostics --
    fill_samples = _build_fill_samples(enriched_trades, passed_trades)

    return {
        "fill_model_summary": fill_model_summary,
        "fill_impact": fill_impact,
        "fill_samples": fill_samples,
    }


def _compute_fill_impact(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare mid-based vs fill-based metrics for gate impact analysis.

    Identifies trades where fill-based ev_to_risk or ror crosses below
    common thresholds that mid-based values would have passed.
    """
    # Common thresholds for comparison
    ev_to_risk_thresholds = [0.01, 0.02, 0.03]
    ror_thresholds = [0.01, 0.05, 0.10]

    impact: dict[str, Any] = {
        "ev_to_risk_downgrades": {},  # threshold → count of trades that flip
        "ror_downgrades": {},
        "total_with_fill": 0,
        "total_where_fill_worse": 0,
    }

    for thr in ev_to_risk_thresholds:
        impact["ev_to_risk_downgrades"][str(thr)] = 0
    for thr in ror_thresholds:
        impact["ror_downgrades"][str(thr)] = 0

    for t in trades:
        ev_mid = _safe_float(t.get("ev_to_risk_mid") or t.get("ev_to_risk"))
        ev_fill = _safe_float(t.get("ev_to_risk_fill"))
        ror_mid = _safe_float(t.get("ror_mid") or t.get("return_on_risk"))
        ror_fill = _safe_float(t.get("ror_fill"))

        if ev_fill is None and ror_fill is None:
            continue

        impact["total_with_fill"] += 1

        worse = False
        if ev_mid is not None and ev_fill is not None and ev_fill < ev_mid:
            worse = True
            for thr in ev_to_risk_thresholds:
                if ev_mid >= thr > ev_fill:
                    impact["ev_to_risk_downgrades"][str(thr)] += 1

        if ror_mid is not None and ror_fill is not None and ror_fill < ror_mid:
            worse = True
            for thr in ror_thresholds:
                if ror_mid >= thr > ror_fill:
                    impact["ror_downgrades"][str(thr)] += 1

        if worse:
            impact["total_where_fill_worse"] += 1

    return impact


def _build_fill_samples(
    enriched: list[dict[str, Any]],
    passed: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build sample trades for fill diagnostics.

    Returns up to 3 categories:
      top_slippage: trade with largest slippage_pct (shows worst-case)
      closest_to_cutoff: trade where fill ev_to_risk is closest to 0
      random_sample: first passed trade with fill data (baseline)
    """
    samples: dict[str, Any] = {}

    # -- top_slippage --
    max_slip = -1.0
    max_slip_trade = None
    for t in enriched:
        slip = _safe_float(t.get("slippage_pct"))
        if slip is not None and slip > max_slip:
            max_slip = slip
            max_slip_trade = t

    if max_slip_trade is not None:
        samples["top_slippage"] = _sample_summary(max_slip_trade)

    # -- closest_to_cutoff --
    min_dist = float("inf")
    closest_trade = None
    for t in enriched:
        ev_fill = _safe_float(t.get("ev_to_risk_fill"))
        if ev_fill is not None:
            dist = abs(ev_fill)
            if dist < min_dist:
                min_dist = dist
                closest_trade = t

    if closest_trade is not None:
        samples["closest_to_cutoff"] = _sample_summary(closest_trade)

    # -- random_sample (first passed trade with fill) --
    for t in passed:
        if _safe_float(t.get("expected_fill_price")) is not None:
            samples["random_sample"] = _sample_summary(t)
            break

    return samples


def _sample_summary(trade: dict[str, Any]) -> dict[str, Any]:
    """Extract a compact summary of a trade for fill diagnostics."""
    return {
        "trade_key": trade.get("trade_key"),
        "strategy": trade.get("strategy"),
        "symbol": trade.get("underlying") or trade.get("symbol"),
        "spread_mid": trade.get("spread_mid"),
        "spread_natural": trade.get("spread_natural") or trade.get("spread_bid"),
        "expected_fill_price": trade.get("expected_fill_price"),
        "expected_fill_weight_w": trade.get("expected_fill_weight_w"),
        "fill_confidence": trade.get("fill_confidence"),
        "slippage_vs_mid": trade.get("slippage_vs_mid"),
        "slippage_pct": trade.get("slippage_pct"),
        "ev_to_risk_mid": trade.get("ev_to_risk_mid"),
        "ev_to_risk_fill": trade.get("ev_to_risk_fill"),
        "ror_mid": trade.get("ror_mid"),
        "ror_fill": trade.get("ror_fill"),
        "max_profit_mid": trade.get("max_profit_mid"),
        "max_profit_fill": trade.get("max_profit_fill"),
    }
