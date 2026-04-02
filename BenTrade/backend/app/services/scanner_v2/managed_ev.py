"""Managed expected value model for options scanner Phase E.

Computes a three-outcome EV that accounts for profit targets and stop
losses, running in parallel with the existing binary (hold-to-expiration)
EV calculation.  Called from ``phase_e_recomputed_math()`` after the
binary EV is already set.

Three outcomes
--------------
1. **Profit target hit** — trade closed at a fraction of max profit.
2. **Stop loss hit** — trade closed at a managed loss level.
3. **Expiration reached** — trade reaches expiry without hitting either
   level; falls back to the binary POP model for the residual outcome.

Touch probability (zero-drift lognormal)
-----------------------------------------
P_touch(barrier, S, T, σ) = 2 × N(−|ln(barrier/S)| / (σ√T))

This is the simplified closed-form assuming μ = 0 (same assumption the
existing POP model uses).
"""

from __future__ import annotations

import math

# ── Strategy classification (duplicated to avoid circular import) ───
# ranking.py owns the canonical classify_strategy(). We duplicate the
# simple dict lookup here rather than importing from ranking to prevent
# a circular dependency path (scanner_v2 → ranking → scanner_v2).

_INCOME_KEYS = frozenset({
    "put_credit_spread", "call_credit_spread",
    "iron_condor", "iron_butterfly",
})
_BUTTERFLY_KEYS = frozenset({
    "butterfly_debit", "iron_butterfly",
})
_DIRECTIONAL_KEYS = frozenset({
    "put_debit", "call_debit",
})
_CALENDAR_KEYS = frozenset({
    "calendar_call_spread", "calendar_put_spread",
    "diagonal_call_spread", "diagonal_put_spread",
})


def _classify_strategy(scanner_key: str) -> str:
    if scanner_key in _INCOME_KEYS:
        return "income"
    if scanner_key in _BUTTERFLY_KEYS:
        return "butterfly"
    if scanner_key in _DIRECTIONAL_KEYS:
        return "directional"
    if scanner_key in _CALENDAR_KEYS:
        return "calendar"
    return "unknown"


# ── Management policy defaults ──────────────────────────────────────

DEFAULT_MANAGEMENT_POLICIES: dict[str, dict | None] = {
    "income": {
        "profit_target_pct": 0.50,      # close at 50% of max profit
        "stop_loss_multiplier": 2.0,    # cut at 2× credit received
        "stop_loss_basis": "credit",    # "credit" or "width"
        "min_dte_to_manage": 7,         # don't exit inside 7 DTE
    },
    "directional": {
        "profit_target_pct": 0.75,      # close at 75% of max profit
        "stop_loss_multiplier": 1.0,    # cut at 1× debit paid (100% loss)
        "stop_loss_basis": "debit",     # "debit" or "width"
        "min_dte_to_manage": 5,
    },
    "butterfly": {
        "profit_target_pct": 0.50,      # 50% — butterflies rarely reach 75%
        "stop_loss_multiplier": 1.0,    # cut at 1× debit (standard)
        "stop_loss_basis": "debit",
        "min_dte_to_manage": 5,
    },
    "calendar": None,   # max_profit is None — no managed EV yet
    "unknown": None,    # fall back to binary EV
}


# ── Normal CDF (dependency-free) ────────────────────────────────────

def _normal_cdf(x: float) -> float:
    """Standard normal CDF via math.erf (no scipy needed)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ── Touch probability ──────────────────────────────────────────────

def _touch_probability(
    barrier: float,
    spot: float,
    t_years: float,
    sigma: float,
) -> float | None:
    """P(lognormal process touches *barrier* before time T).

    Zero-drift (μ = 0) closed form:
        P_touch = 2 × N(−|ln(barrier/S)| / (σ√T))

    Returns None if inputs are invalid.
    """
    if barrier <= 0 or spot <= 0 or t_years <= 0 or sigma <= 0:
        return None
    sigma_sqrt_t = sigma * math.sqrt(t_years)
    if sigma_sqrt_t <= 0:
        return None
    log_ratio = abs(math.log(barrier / spot))
    return 2.0 * _normal_cdf(-log_ratio / sigma_sqrt_t)


# ── Income profit target probability (POP-decay model) ─────────────

def _income_profit_target_probability(
    pop: float,
    profit_target_pct: float,
    dte: int,
    iv: float | None,
) -> float:
    """Estimate probability of hitting profit target for income strategies.

    Income profit targets are theta-driven events.  The spread's value
    decays over time; you close when it reaches a fraction of the
    original credit.  This is fundamentally different from a price
    barrier event.

    Empirical calibration (tastytrade/CBOE studies):
    - 16-delta credit spread at 45 DTE with 50% target: ~73% hit rate
    - Higher POP → higher hit rate
    - More DTE → higher hit rate (more time for decay)
    - Lower profit target pct → easier to hit

    Model: p_target = POP × hit_rate
    where hit_rate captures the fraction of ultimately-profitable trades
    that reach the profit target before expiration.

    Calibration points:
        POP=0.84, DTE=45, target=50%: p ≈ 0.73 → factor = 0.869
        POP=0.70, DTE=30, target=50%: p ≈ 0.58 → factor = 0.829
        POP=0.85, DTE=21, target=50%: p ≈ 0.68 → factor = 0.800
    """
    # More DTE = more time for theta to work, normalized to 45 DTE
    dte_factor = min(1.0, dte / 45.0)

    # Lower profit target = easier to hit (50% → 0.50 factor, 75% → 0.25)
    target_factor = 1.0 - profit_target_pct

    # Higher IV → faster premium decay → slightly higher hit rate,
    # with diminishing returns.  Peaks around IV=0.30.
    iv_factor = min(1.0, (iv or 0.20) / 0.30)

    # Combined hit rate: fraction of POP-favorable trades that actually
    # reach the profit target before expiration
    # Base 0.75 + up to 0.15 from DTE + up to 0.10 from target ease × IV
    hit_rate = 0.75 + 0.15 * dte_factor + 0.10 * target_factor * iv_factor
    hit_rate = min(0.95, max(0.60, hit_rate))

    return pop * hit_rate


# ── Directional profit target probability ──────────────────────────

def _directional_profit_target_probability(
    pop: float,
    profit_target_pct: float,
    dte: int,
    iv: float | None,
) -> float:
    """Estimate probability of hitting profit target for directional trades.

    Directional trades profit from price movement (delta/gamma).
    Unlike income, theta works AGAINST the position.

    Calibrated against empirical debit spread management studies:
    - 50-delta, 30 DTE, 50% target: ~35-40% hit rate
    - 50-delta, 30 DTE, 75% target: ~20-25% hit rate
    - 40-delta, 21 DTE, 50% target: ~30-35% hit rate
    - 30-delta, 45 DTE, 50% target: ~20-25% hit rate

    Args:
        pop: probability of profit (0-1), typically equals long leg delta
        profit_target_pct: fraction of max profit to target (e.g. 0.75)
        dte: days to expiration
        iv: implied volatility (annualized, e.g. 0.25 = 25%)

    Returns:
        Estimated probability of hitting profit target (0.05 to 0.80)
    """
    # Target difficulty: higher profit target % = harder to achieve
    # Calibrated: 50% target -> 0.75, 75% target -> 0.45, 100% -> 0.15
    target_difficulty = max(0.10, 1.35 - 1.20 * profit_target_pct)

    # DTE factor: more time = more chances for favorable move
    # Linear scaling with compression -- caps at 1.15
    # Unlike income where DTE helps via theta, directional DTE helps
    # by giving more time for the move to happen
    dte_norm = dte / 30.0
    dte_factor = min(1.15, 0.85 + 0.30 * min(1.0, dte_norm))

    # IV factor: higher vol = bigger moves possible
    # Moderate linear scaling, clamped
    iv_factor = min(1.20, max(0.80, (iv or 0.25) / 0.25))

    # Combined probability
    p_target = pop * target_difficulty * dte_factor * iv_factor

    return min(0.80, max(0.05, p_target))


# ── Butterfly profit target probability ──────────────────────────────────

def _butterfly_profit_target_probability(
    pop: float,
    profit_target_pct: float,
    dte: int,
    iv: float | None,
) -> float:
    """Butterfly profit target probability.

    Butterflies profit from price PINNING near the center strike.
    This is harder to achieve than directional movement — the price
    must move to the right zone AND stay there.

    The existing butterfly POP already accounts for the range
    probability (breakeven_range_lognormal). The profit target
    probability is lower than POP because you need the spread to
    reach a specific value level, not just be profitable.
    """
    # Butterflies have lower profit target hit rates than verticals
    # because the profit zone is narrow
    target_difficulty = max(0.05, 1.00 - 1.40 * profit_target_pct)
    # At 50% target: 0.30, at 75% target: -0.05 → clamped to 0.05

    # DTE helps — more time for price to visit the zone
    dte_factor = min(1.10, 0.80 + 0.20 * min(1.0, dte / 30.0))

    # High IV hurts butterflies (more movement = less pinning)
    iv_penalty = max(0.70, min(1.0, 0.25 / max(iv or 0.25, 0.10)))

    p_target = pop * target_difficulty * dte_factor * iv_penalty
    return min(0.50, max(0.02, p_target))


# ── Directional stop loss probability (POP-based) ─────────────────

def _directional_stop_loss_probability(
    pop: float,
    profit_target_pct: float,
    dte: int,
    iv: float | None,
) -> float:
    """POP-based stop loss probability for directional trades.

    Debit spreads lose value from BOTH adverse price movement AND
    theta decay.  Touch probability only captures the price component.
    This model captures both by anchoring to (1 - POP).

    Key insight: (1 - POP) is the probability of being unprofitable
    at expiration.  Most of those losing trades will trigger the stop
    loss BEFORE expiration because theta accelerates the loss.

    Args:
        pop: probability of profit (0-1)
        profit_target_pct: used to avoid double-counting with p_target
        dte: days to expiration
        iv: implied volatility

    Returns:
        Raw (unconditioned) stop loss probability
    """
    # Base: (1 - POP) = probability of loss at expiration
    base_loss_prob = 1.0 - pop

    # Theta factor: shorter DTE → theta hits harder → more stops triggered early
    # At 30 DTE (reference): 75% of losing trades hit stop before expiry
    # At 14 DTE: 85% (theta accelerates in final 2 weeks)
    # At 45 DTE: 65% (more time for recovery before theta kicks in)
    if dte <= 14:
        theta_factor = 0.85
    elif dte <= 30:
        # Linear interpolation: 14→0.85, 30→0.75
        theta_factor = 0.85 - (dte - 14) * (0.10 / 16)
    elif dte <= 60:
        # Linear interpolation: 30→0.75, 60→0.60
        theta_factor = 0.75 - (dte - 30) * (0.15 / 30)
    else:
        theta_factor = 0.60

    # IV adjustment: higher IV → bigger moves → slightly more stops
    iv_adj = min(1.1, max(0.9, (iv or 0.25) / 0.25))

    p_stop = base_loss_prob * theta_factor * iv_adj
    return min(0.85, max(0.05, p_stop))


# ── Helpers for leg extraction ──────────────────────────────────────

def get_iv_from_legs(
    legs: list,
    strategy_class: str,
) -> float | None:
    """Extract representative IV from candidate legs.

    Income strategies → short leg IV (the sold premium drives risk).
    Directional strategies → long leg IV.
    Fallback → average of all legs' IV values.
    """
    if not legs:
        return None

    target_side = "short" if strategy_class == "income" else "long"
    for leg in legs:
        side = leg.side if hasattr(leg, "side") else leg.get("side")
        iv = leg.iv if hasattr(leg, "iv") else leg.get("iv")
        if side == target_side and iv is not None and iv > 0:
            return float(iv)

    # Fallback: average of all available IVs
    ivs = []
    for leg in legs:
        iv = leg.iv if hasattr(leg, "iv") else leg.get("iv")
        if iv is not None and iv > 0:
            ivs.append(float(iv))
    return sum(ivs) / len(ivs) if ivs else None


def get_short_strike(legs: list) -> float | None:
    """First short-side strike price."""
    for leg in legs:
        side = leg.side if hasattr(leg, "side") else leg.get("side")
        strike = leg.strike if hasattr(leg, "strike") else leg.get("strike")
        if side == "short" and strike is not None:
            return float(strike)
    return None


def get_long_strike(legs: list) -> float | None:
    """First long-side strike price."""
    for leg in legs:
        side = leg.side if hasattr(leg, "side") else leg.get("side")
        strike = leg.strike if hasattr(leg, "strike") else leg.get("strike")
        if side == "long" and strike is not None:
            return float(strike)
    return None


# ── Null result helper ──────────────────────────────────────────────

def _null_result(reason: str, policy: dict | None = None) -> dict:
    return {
        "ev_managed": None,
        "ev_managed_per_day": None,
        "managed_profit_target": None,
        "managed_stop_loss": None,
        "p_profit_target": None,
        "p_stop_loss": None,
        "p_expiration": None,
        "management_policy_used": policy,
        "ev_model": "three_outcome_v1",
        "managed_expected_ror": None,
        "managed_ev_note": reason,
    }


# ── Main entry point ───────────────────────────────────────────────

def compute_managed_ev(
    strategy_class: str,
    pop: float | None,
    max_profit: float | None,
    max_loss: float | None,
    net_credit: float | None,
    net_debit: float | None,
    width: float | None,
    dte: int | None,
    iv: float | None,
    underlying_price: float | None,
    short_strike: float | None,
    long_strike: float | None,
    scanner_key: str = "",
    management_policy: dict | None = None,
) -> dict:
    """Compute three-outcome managed expected value.

    Returns a dict of managed EV fields to merge into candidate math.
    If any required input is None or the strategy class has no policy,
    returns a dict with ev_managed=None and a note explaining why.
    """
    # ── Resolve management policy ───────────────────────────────
    default_policy = DEFAULT_MANAGEMENT_POLICIES.get(strategy_class)
    if default_policy is None:
        return _null_result(
            f"no management policy for strategy_class={strategy_class}"
        )

    policy = dict(default_policy)
    if management_policy:
        policy.update(management_policy)

    # ── Validate required inputs ────────────────────────────────
    if pop is None:
        return _null_result("pop is None", policy)
    if max_profit is None or max_profit <= 0:
        return _null_result("max_profit is None or <= 0", policy)
    if max_loss is None or max_loss <= 0:
        return _null_result("max_loss is None or <= 0", policy)
    if dte is None or dte <= 0:
        return _null_result("dte is None or <= 0", policy)
    if iv is None or iv <= 0:
        return _null_result("iv is None or <= 0", policy)
    if underlying_price is None or underlying_price <= 0:
        return _null_result("underlying_price is None or <= 0", policy)

    # ── Step 1: Compute management levels (dollar amounts) ──────
    profit_target_pct = policy["profit_target_pct"]
    stop_loss_multiplier = policy["stop_loss_multiplier"]
    stop_loss_basis = policy["stop_loss_basis"]

    profit_target_amount = max_profit * profit_target_pct

    if stop_loss_basis == "credit" and net_credit is not None and net_credit > 0:
        stop_loss_amount = net_credit * 100 * stop_loss_multiplier
    elif stop_loss_basis == "debit" and net_debit is not None and net_debit > 0:
        stop_loss_amount = net_debit * 100 * stop_loss_multiplier
    elif width is not None and width > 0:
        # Fallback to width-based stop
        stop_loss_amount = width * 100 * stop_loss_multiplier
    else:
        return _null_result(
            f"cannot compute stop_loss: basis={stop_loss_basis}, "
            f"net_credit={net_credit}, net_debit={net_debit}, width={width}",
            policy,
        )

    # Cap stop loss at max_loss (can't lose more than max)
    stop_loss_amount = min(stop_loss_amount, max_loss)

    # ── Step 2: Convert to price levels ─────────────────────────
    # Determine option type from scanner_key to orient price direction
    is_put_spread = scanner_key in (
        "put_credit_spread", "put_debit",
    )
    is_call_spread = scanner_key in (
        "call_credit_spread", "call_debit",
    )
    is_iron_condor = scanner_key in ("iron_condor", "iron_butterfly")
    is_butterfly = scanner_key == "butterfly_debit"

    if short_strike is not None and (is_put_spread or is_call_spread):
        # PUT CREDIT SPREAD (bullish): loses when price drops below short
        # CALL CREDIT SPREAD (bearish): loses when price rises above short
        if strategy_class == "income":
            if is_put_spread:
                # Bullish: profit target = price stays above short strike
                profit_target_price = short_strike
                stop_loss_price = short_strike - (stop_loss_amount / 100)
            else:
                # Bearish: profit target = price stays below short strike
                profit_target_price = short_strike
                stop_loss_price = short_strike + (stop_loss_amount / 100)
        else:
            # Directional (debit): profit when price moves toward long strike
            if long_strike is None:
                return _null_result("long_strike is None for directional", policy)
            if is_call_spread or scanner_key == "call_debit":
                # Call debit: profit when price rises
                profit_target_price = long_strike + (profit_target_amount / 100)
                stop_loss_price = long_strike - (stop_loss_amount / 100)
            else:
                # Put debit: profit when price falls
                profit_target_price = long_strike - (profit_target_amount / 100)
                stop_loss_price = long_strike + (stop_loss_amount / 100)
    elif is_iron_condor and short_strike is not None:
        # Iron condor / iron butterfly: use short put strike for stop
        # (simplification — the put side is typically the higher-risk side)
        profit_target_price = short_strike
        stop_loss_price = short_strike - (stop_loss_amount / 100)
    elif is_butterfly and long_strike is not None:
        # Butterfly: center strike is the target
        profit_target_price = long_strike + (profit_target_amount / 100)
        stop_loss_price = long_strike - (stop_loss_amount / 100)
    else:
        return _null_result(
            f"cannot determine price levels: scanner_key={scanner_key}, "
            f"short_strike={short_strike}, long_strike={long_strike}",
            policy,
        )

    # Ensure price levels are positive (only needed for directional
    # where price levels drive probability; income stop still needs this)
    if stop_loss_price <= 0:
        return _null_result(
            f"non-positive stop loss price: stop={stop_loss_price}",
            policy,
        )

    # ── Step 3: Estimate outcome probabilities ──────────────────
    t_years = dte / 365.0

    if strategy_class == "income":
        # ── Income: POP-decay model for profit target ───────────
        # Income profit targets are theta-driven, not price-driven.
        # Use empirically-grounded POP-decay model instead of touch
        # probability for profit target.
        p_target = _income_profit_target_probability(
            pop, profit_target_pct, dte, iv,
        )

        # Stop loss IS a price event — touch probability is correct
        p_stop_raw = _touch_probability(
            stop_loss_price, underlying_price, t_years, iv,
        )
        if p_stop_raw is None:
            return _null_result(
                f"touch probability returned None for stop: "
                f"stop_price={stop_loss_price}",
                policy,
            )

        # Race-to-barrier conditioning: if target is hit first,
        # stop can't be hit.  Since target is time-based and stop
        # is price-based they're somewhat independent, but once
        # target fires the trade is closed.
        p_stop = p_stop_raw * (1.0 - p_target)

    elif strategy_class == "butterfly":
        # ── Butterfly: pin-probability model ─────────────────────
        # Butterflies profit from price pinning near the center
        # strike.  Lower profit target (50%) because the triangular
        # payoff means even best-case realised profit is ~50% of max.
        p_target = _butterfly_profit_target_probability(
            pop, profit_target_pct, dte, iv,
        )

        # Stop loss — reuse POP-based directional model (debit-based)
        p_stop_raw = _directional_stop_loss_probability(
            pop, profit_target_pct, dte, iv,
        )

        # Race-to-barrier conditioning
        p_stop = p_stop_raw * (1.0 - p_target)

    else:
        # ── Directional: POP-based model for profit target ───────
        # Touching the target price doesn't mean the spread is
        # worth the profit target — the short leg retains extrinsic
        # value.  Use the empirically-grounded POP model instead.
        p_target = _directional_profit_target_probability(
            pop, profit_target_pct, dte, iv,
        )

        # Stop loss — POP-based model capturing theta + price decay.
        # Debit spread stop losses are TIME+PRICE events: theta erodes
        # spread value even without adverse price movement, so pure
        # touch probability underestimates stop-loss frequency.
        p_stop_raw = _directional_stop_loss_probability(
            pop, profit_target_pct, dte, iv,
        )

        # Race-to-barrier conditioning
        p_stop = p_stop_raw * (1.0 - p_target)

    # Expiration residual
    p_expiration = max(0.0, 1.0 - p_target - p_stop)

    # Normalize if sum exceeds 1.0 (safety — shouldn't happen with
    # conditioning, but floating point)
    total = p_target + p_stop + p_expiration
    if total > 1.0:
        p_target /= total
        p_stop /= total
        p_expiration /= total

    # Clamp all to [0, 1]
    p_target = max(0.0, min(1.0, p_target))
    p_stop = max(0.0, min(1.0, p_stop))
    p_expiration = max(0.0, min(1.0, p_expiration))

    # ── Step 4: Compute managed EV ──────────────────────────────
    # Expiration outcome uses the existing binary POP model
    p_expiry_win = pop
    p_expiry_loss = 1.0 - pop

    expiry_ev = p_expiration * (
        p_expiry_win * max_profit + p_expiry_loss * (-max_loss)
    )

    ev_managed = (
        p_target * profit_target_amount
        + p_stop * (-stop_loss_amount)
        + expiry_ev
    )

    # ── Step 5: Derived fields ──────────────────────────────────
    ev_managed_per_day = ev_managed / dte if dte > 0 else None
    managed_expected_ror = ev_managed / max_loss if max_loss > 0 else None

    return {
        "ev_managed": round(ev_managed, 2),
        "ev_managed_per_day": round(ev_managed_per_day, 4) if ev_managed_per_day is not None else None,
        "managed_profit_target": round(profit_target_amount, 2),
        "managed_stop_loss": round(stop_loss_amount, 2),
        "p_profit_target": round(p_target, 4),
        "p_stop_loss": round(p_stop, 4),
        "p_expiration": round(p_expiration, 4),
        "management_policy_used": policy,
        "ev_model": "three_outcome_v1",
        "managed_expected_ror": round(managed_expected_ror, 4) if managed_expected_ror is not None else None,
    }
