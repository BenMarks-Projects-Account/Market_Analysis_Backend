"""Deterministic trade-analysis engine.

This module provides:

1. ``build_analysis_facts(trade)`` — Normalizes a raw trade dict into a
   structured ``AnalysisFacts`` dict with explicit null tracking and
   data-quality flags for missing/invalid fields.

2. ``compute_trade_metrics(facts)`` — Pure-math engine calculations that
   are deterministic and reproducible.  These are computed *before* the LLM
   is called so the model can cross-check its own independent calculations.

3. ``validate_model_schema(model_eval)`` — Validates LLM output against
   required schema fields.  Returns a list of violations (empty = valid).

All derived fields document their formula inline (data-integrity rule §1).
"""
from __future__ import annotations

import math
from typing import Any


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _to_float(x: Any) -> float | None:
    """Safely coerce *x* to float.  Returns ``None`` on failure."""
    if x is None:
        return None
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 1.  AnalysisFacts builder
# ---------------------------------------------------------------------------

def build_analysis_facts(trade: dict) -> dict:
    """Normalize a raw trade dict into a structured facts contract.

    Returns a dict with sections: ``underlying``, ``structure``, ``pricing``,
    ``volatility``, ``liquidity``, ``market_context``, and
    ``data_quality_flags`` (list of strings naming missing/invalid fields).

    Input fields → output mapping documented inline for traceability (§1).
    """
    dq_flags: list[str] = []

    def _require(label: str, val: float | None) -> float | None:
        """Track *label* in ``dq_flags`` when the value is None."""
        if val is None:
            dq_flags.append(label)
        return val

    # -- Underlying ----------------------------------------------------------
    symbol = (
        trade.get("symbol")
        or trade.get("underlying")
        or trade.get("underlying_symbol")
    )
    underlying_price = _require(
        "underlying_price",
        _to_float(trade.get("price") or trade.get("underlying_price")),
    )
    underlying_bid  = _to_float(trade.get("underlying_bid"))
    underlying_ask  = _to_float(trade.get("underlying_ask"))

    # -- Structure -----------------------------------------------------------
    spread_type = trade.get("spread_type") or trade.get("strategy_id") or trade.get("type")
    _is_condor = "iron_condor" in str(spread_type or "").lower() or "condor" in str(spread_type or "").lower()

    # 4-leg strike fields (iron condor)
    short_put_strike  = _to_float(trade.get("short_put_strike"))
    long_put_strike   = _to_float(trade.get("long_put_strike"))
    short_call_strike = _to_float(trade.get("short_call_strike"))
    long_call_strike  = _to_float(trade.get("long_call_strike"))

    if _is_condor:
        # For iron condors: short_strike/long_strike may be None or string-derived;
        # the 4 explicit leg-strike fields are authoritative.
        if short_put_strike is None:
            dq_flags.append("short_put_strike")
        if long_put_strike is None:
            dq_flags.append("long_put_strike")
        if short_call_strike is None:
            dq_flags.append("short_call_strike")
        if long_call_strike is None:
            dq_flags.append("long_call_strike")
        # Don't require 2-leg short_strike/long_strike for condors
        short_strike = _to_float(trade.get("short_strike"))
        long_strike  = _to_float(trade.get("long_strike"))
    else:
        short_strike = _require("short_strike", _to_float(trade.get("short_strike")))
        long_strike  = _require("long_strike",  _to_float(trade.get("long_strike")))

    expiration = trade.get("expiration")
    dte        = _require("dte", _to_float(trade.get("dte")))

    # Derive width
    # For iron condors: wing_width = max(|short_put - long_put|, |short_call - long_call|)
    # For 2-leg spreads: width = |short_strike - long_strike|
    raw_width = _to_float(trade.get("width"))
    if raw_width is None:
        if _is_condor:
            put_wing = (abs(short_put_strike - long_put_strike)
                        if short_put_strike is not None and long_put_strike is not None
                        else None)
            call_wing = (abs(short_call_strike - long_call_strike)
                         if short_call_strike is not None and long_call_strike is not None
                         else None)
            # Use max wing width (typically equal)
            if put_wing is not None and call_wing is not None:
                raw_width = max(put_wing, call_wing)
            elif put_wing is not None:
                raw_width = put_wing
            elif call_wing is not None:
                raw_width = call_wing
        elif short_strike is not None and long_strike is not None:
            raw_width = abs(short_strike - long_strike)
    width = _require("width", raw_width)

    # -- Pricing -------------------------------------------------------------
    net_credit = _require(
        "net_credit",
        _to_float(trade.get("net_credit") or trade.get("credit")),
    )
    # contract_multiplier defaults to 100 for equity options
    contract_multiplier = _to_float(trade.get("contract_multiplier")) or 100.0

    # -- Volatility ----------------------------------------------------------
    iv = _to_float(trade.get("iv") or trade.get("implied_vol"))
    if iv is None:
        dq_flags.append("iv")
    realized_vol = _to_float(
        trade.get("realized_vol") or trade.get("realized_vol_20d")
    )
    iv_rv_ratio = _to_float(trade.get("iv_rv_ratio"))
    iv_rank     = _to_float(trade.get("iv_rank"))
    vix         = _to_float(trade.get("vix"))

    # -- Liquidity -----------------------------------------------------------
    bid = _to_float(trade.get("bid"))
    ask = _to_float(trade.get("ask"))
    bid_ask_spread_pct = _to_float(trade.get("bid_ask_spread_pct"))
    if bid_ask_spread_pct is None and bid is not None and ask is not None and ask > 0:
        # Formula: bid_ask_spread_pct = (ask - bid) / ask
        mid = (bid + ask) / 2.0
        if mid > 0:
            bid_ask_spread_pct = (ask - bid) / mid
    open_interest = _to_float(trade.get("open_interest"))
    volume        = _to_float(trade.get("volume"))
    if open_interest is None:
        dq_flags.append("open_interest")
    if volume is None:
        dq_flags.append("volume")

    # -- Market context (raw numbers only, no regime labels) -----------------
    sma20  = _to_float(trade.get("sma20"))
    sma50  = _to_float(trade.get("sma50"))
    sma200 = _to_float(trade.get("sma200"))
    ema20  = _to_float(trade.get("ema20"))
    ema50  = _to_float(trade.get("ema50"))
    rsi14  = _to_float(trade.get("rsi14"))

    trend_facts: list[str] = []
    if underlying_price is not None and ema20 is not None:
        trend_facts.append(
            f"Close {'>' if underlying_price >= ema20 else '<'} EMA20"
        )
    if ema50 is not None and sma200 is not None:
        trend_facts.append(
            f"EMA50 {'>' if ema50 >= sma200 else '<'} SMA200"
        )
    if underlying_price is not None and sma50 is not None:
        trend_facts.append(
            f"Close {'>' if underlying_price >= sma50 else '<'} SMA50"
        )

    momentum_facts: list[str] = []
    if rsi14 is not None:
        if rsi14 < 30:
            momentum_facts.append(f"RSI14 = {rsi14:.1f} (oversold zone)")
        elif rsi14 > 70:
            momentum_facts.append(f"RSI14 = {rsi14:.1f} (overbought zone)")
        else:
            momentum_facts.append(f"RSI14 = {rsi14:.1f}")

    short_strike_z     = _to_float(trade.get("short_strike_z"))
    strike_distance_pct = _to_float(trade.get("strike_distance_pct"))
    distance_facts: list[str] = []
    if short_strike_z is not None:
        distance_facts.append(
            f"Short strike is {short_strike_z:.2f} sigma from spot"
        )
    if strike_distance_pct is not None:
        distance_facts.append(
            f"Short strike is {strike_distance_pct * 100:.1f}% from spot"
        )

    # -- Short-leg greeks (optional, used for POP proxy) ---------------------
    short_delta_abs = _to_float(
        trade.get("short_delta_abs") or trade.get("pop_delta_approx")
    )
    pop = _to_float(trade.get("pop") or trade.get("p_win_used"))

    return {
        "underlying": {
            "symbol": symbol,
            "price": underlying_price,
            "bid": underlying_bid,
            "ask": underlying_ask,
        },
        "structure": {
            "spread_type": spread_type,
            "short_strike": short_strike,
            "long_strike": long_strike,
            # Iron-condor 4-strike fields (None for 2-leg spreads)
            "short_put_strike": short_put_strike,
            "long_put_strike": long_put_strike,
            "short_call_strike": short_call_strike,
            "long_call_strike": long_call_strike,
            "is_condor": _is_condor,
            "expiration": expiration,
            "dte": dte,
            "width": width,
        },
        "pricing": {
            "net_credit": net_credit,
            "contract_multiplier": contract_multiplier,
        },
        "volatility": {
            "iv": iv,
            "realized_vol_20d": realized_vol,
            "iv_rv_ratio": iv_rv_ratio,
            "iv_rank": iv_rank,
            "vix": vix,
        },
        "liquidity": {
            "bid": bid,
            "ask": ask,
            "bid_ask_spread_pct": bid_ask_spread_pct,
            "open_interest": open_interest,
            "volume": volume,
        },
        "market_context": {
            "underlying_price": underlying_price,
            "vix": vix,
            "trend_facts": trend_facts,
            "momentum_facts": momentum_facts,
            "distance_facts": distance_facts,
        },
        "probability": {
            "short_delta_abs": short_delta_abs,
            "pop": pop,
            "short_strike_z": short_strike_z,
        },
        "data_quality_flags": dq_flags,
    }


# ---------------------------------------------------------------------------
# 2.  Deterministic engine metrics
# ---------------------------------------------------------------------------

def compute_trade_metrics(facts: dict) -> dict:
    """Compute deterministic trade metrics from *facts* (output of
    ``build_analysis_facts``).

    All formulas are documented inline for traceability (§1).

    Returns a dict with keys:
      max_profit_per_share, max_loss_per_share, breakeven, return_on_risk,
      net_credit_per_share, width, premium_to_risk_ratio, pop_proxy,
      ev_per_share, kelly_fraction.
    """
    structure = facts.get("structure") or {}
    pricing   = facts.get("pricing") or {}
    prob      = facts.get("probability") or {}

    net_credit = _to_float(pricing.get("net_credit"))
    width      = _to_float(structure.get("width"))
    spread_type = structure.get("spread_type") or ""
    short_strike = _to_float(structure.get("short_strike"))

    # -- max_profit_per_share = net_credit  (credit spreads)
    # For debit spreads this would be width - debit, but the platform focuses
    # on credit strategies.
    max_profit = net_credit

    # -- max_loss_per_share = width - net_credit  (credit spreads)
    max_loss: float | None = None
    if width is not None and net_credit is not None:
        max_loss = width - net_credit
        if max_loss < 0:
            max_loss = 0.0  # shouldn't happen for valid trades

    # -- breakeven
    is_condor = structure.get("is_condor", False)
    if is_condor:
        # Iron condor has two break-even points:
        #   BEL = short_put_strike  - net_credit
        #   BEH = short_call_strike + net_credit
        sp = _to_float(structure.get("short_put_strike"))
        sc = _to_float(structure.get("short_call_strike"))
        breakeven: float | None = None  # single BE not meaningful for IC
        breakeven_low: float | None = None
        breakeven_high: float | None = None
        if sp is not None and net_credit is not None:
            breakeven_low = sp - net_credit
        if sc is not None and net_credit is not None:
            breakeven_high = sc + net_credit
    else:
        # Formula (put credit): breakeven = short_strike - net_credit
        # Formula (call credit): breakeven = short_strike + net_credit
        breakeven: float | None = None
        breakeven_low = None
        breakeven_high = None
        if short_strike is not None and net_credit is not None:
            st = spread_type.lower()
            if "put" in st:
                breakeven = short_strike - net_credit
            elif "call" in st:
                breakeven = short_strike + net_credit
            else:
                # Default to put credit convention
                breakeven = short_strike - net_credit

    # -- return_on_risk = max_profit / max_loss
    return_on_risk: float | None = None
    if max_profit is not None and max_loss is not None and max_loss > 0:
        return_on_risk = max_profit / max_loss

    # -- premium_to_risk_ratio = net_credit / width
    premium_to_risk: float | None = None
    if net_credit is not None and width is not None and width > 0:
        premium_to_risk = net_credit / width

    # -- POP proxy
    # Prefer explicit POP from scanner / delta.
    # Fallback: 1 - |short_delta_abs| for credit spreads (OTM delta ≈ P(ITM)).
    pop_proxy: float | None = _to_float(prob.get("pop"))
    if pop_proxy is None:
        delta = _to_float(prob.get("short_delta_abs"))
        if delta is not None:
            # For OTM credit spreads, POP ≈ 1 - |delta|
            pop_proxy = 1.0 - abs(delta)

    # -- EV per share = POP * max_profit - (1 - POP) * max_loss
    ev_per_share: float | None = None
    if pop_proxy is not None and max_profit is not None and max_loss is not None:
        ev_per_share = (pop_proxy * max_profit) - ((1.0 - pop_proxy) * max_loss)

    # -- Kelly fraction = (POP * (1 + ROR) - 1) / ROR   (simplified)
    kelly_fraction: float | None = None
    if pop_proxy is not None and return_on_risk is not None and return_on_risk > 0:
        # Kelly = p - q/b where p=POP, q=1-POP, b=max_profit/max_loss=ROR
        kelly_fraction = pop_proxy - (1.0 - pop_proxy) / return_on_risk

    return {
        "max_profit_per_share": max_profit,
        "max_loss_per_share": max_loss,
        "breakeven": breakeven,
        "breakeven_low": breakeven_low,    # IC: short_put - net_credit
        "breakeven_high": breakeven_high,  # IC: short_call + net_credit
        "return_on_risk": return_on_risk,
        "net_credit_per_share": net_credit,
        "width": width,
        "premium_to_risk_ratio": premium_to_risk,
        "pop_proxy": pop_proxy,
        "ev_per_share": ev_per_share,
        "kelly_fraction": kelly_fraction,
    }


# ---------------------------------------------------------------------------
# 3.  Schema validation for LLM output
# ---------------------------------------------------------------------------

# Tier-1 required fields that the model MUST return for a valid evaluation
_REQUIRED_TIER1 = frozenset({
    "recommendation",
    "score_0_100",
    "confidence_0_1",
    "thesis",
    "model_calculations",
    "key_drivers",
    "risk_review",
})

# Required sub-fields inside model_calculations
_REQUIRED_MODEL_CALC = frozenset({
    "expected_value_est",
    "return_on_risk_est",
    "probability_est",
})


def validate_model_schema(model_eval: dict) -> list[str]:
    """Validate *model_eval* against the required schema.

    Returns a list of violation strings.  Empty list = valid.
    """
    violations: list[str] = []

    if not isinstance(model_eval, dict):
        return ["model_eval is not a dict"]

    # -- Tier-1 top-level keys
    for key in _REQUIRED_TIER1:
        if key not in model_eval or model_eval[key] is None:
            violations.append(f"missing_required_field:{key}")

    # -- recommendation value check
    rec = model_eval.get("recommendation", "")
    if isinstance(rec, str) and rec.upper() not in (
        "TAKE", "PASS", "WATCH", "ACCEPT", "REJECT", "NEUTRAL",
    ):
        violations.append(f"invalid_recommendation:{rec}")

    # -- score_0_100 range
    score = model_eval.get("score_0_100")
    if score is not None:
        try:
            s = int(score)
            if s < 0 or s > 100:
                violations.append(f"score_out_of_range:{s}")
        except (TypeError, ValueError):
            violations.append(f"score_not_integer:{score}")

    # -- confidence_0_1 range
    conf = model_eval.get("confidence_0_1")
    if conf is not None:
        try:
            c = float(conf)
            if c < 0.0 or c > 1.0:
                violations.append(f"confidence_out_of_range:{c}")
        except (TypeError, ValueError):
            violations.append(f"confidence_not_float:{conf}")

    # -- model_calculations sub-fields
    mc = model_eval.get("model_calculations")
    if isinstance(mc, dict):
        for key in _REQUIRED_MODEL_CALC:
            if key not in mc or mc[key] is None:
                violations.append(f"missing_model_calc:{key}")
    elif mc is not None:
        violations.append("model_calculations_not_dict")

    # -- key_drivers minimum count
    kd = model_eval.get("key_drivers")
    if isinstance(kd, list) and len(kd) < 3:
        violations.append(f"key_drivers_count:{len(kd)}_min:3")
    elif kd is not None and not isinstance(kd, list):
        violations.append("key_drivers_not_list")

    # -- thesis minimum length (2 sentences ≈ at least 1 period inside)
    thesis = model_eval.get("thesis")
    if isinstance(thesis, str) and thesis.count(".") < 1:
        violations.append("thesis_too_short")

    return violations
