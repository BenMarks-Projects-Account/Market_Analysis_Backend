"""Recomputed-math verification — independent checks of derived pricing.

This module verifies that recomputed math fields are internally
consistent and within tolerance of independently recomputed values.

It does NOT recompute the math itself — that's Phase E's job.
It only VERIFIES the results.

Layer boundary
──────────────
Math verification asks:
    "Given the leg quotes and recomputed math, are the numbers correct?"

It does NOT ask:
    "Is this trade structure valid?"  (That's structural.py.)
    "Is this a good trade?"  (That's downstream scoring.)

Usage
─────
    from app.services.scanner_v2.validation.math_checks import (
        run_math_verification,
    )

    summary = run_math_verification(candidate, family_key="vertical_spreads")

Tolerance policy
────────────────
Tolerances come from ``tolerances.py``.  Math checks use
``V2ToleranceSpec.classify()`` to produce pass/warn/fail on each
metric comparison.
"""

from __future__ import annotations

import math
from typing import Any

from app.services.scanner_v2.contracts import V2Candidate, V2Leg, V2RecomputedMath
from app.services.scanner_v2.validation.contracts import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    STATUS_WARN,
    V2ValidationResult,
    V2ValidationSummary,
)
from app.services.scanner_v2.validation.tolerances import (
    V2ToleranceSpec,
    get_tolerance,
    get_tolerances,
)


# ═══════════════════════════════════════════════════════════════════
#  Main runner
# ═══════════════════════════════════════════════════════════════════

def run_math_verification(
    candidate: V2Candidate,
    *,
    family_key: str | None = None,
) -> V2ValidationSummary:
    """Run all math verification checks on a candidate.

    Parameters
    ----------
    candidate
        V2Candidate whose ``math`` field has already been recomputed
        by Phase E.
    family_key
        Strategy family key for tolerance lookup.

    Returns
    -------
    V2ValidationSummary
        Aggregated verification results.
    """
    results: list[V2ValidationResult] = []
    m = candidate.math

    # 1. Positivity and finiteness checks (hard failures)
    results.append(verify_positive_max_loss(m))
    results.append(verify_positive_max_profit(m))
    results.append(verify_finite_values(m))

    # 2. Width verification
    results.append(verify_width(candidate, family_key=family_key))

    # 3. Net credit / debit verification
    results.append(verify_net_credit_or_debit(candidate, family_key=family_key))

    # 4. Max profit / max loss verification
    results.append(verify_max_profit(candidate, family_key=family_key))
    results.append(verify_max_loss(candidate, family_key=family_key))

    # 5. Breakeven verification
    results.append(verify_breakeven(candidate, family_key=family_key))

    # 6. RoR verification
    results.append(verify_ror(candidate, family_key=family_key))

    # 7. POP / EV presence checks (non-fatal)
    results.append(check_pop_computed(m))
    results.append(check_ev_computed(m))

    return V2ValidationSummary(results=results)


# ═══════════════════════════════════════════════════════════════════
#  Positivity / finiteness checks
# ═══════════════════════════════════════════════════════════════════

def verify_positive_max_loss(math: V2RecomputedMath) -> V2ValidationResult:
    """max_loss must be positive if set."""
    if math.max_loss is None:
        return V2ValidationResult.make_skipped(
            "positive_max_loss", message="max_loss not set",
        )
    if not _is_finite(math.max_loss) or math.max_loss <= 0:
        return V2ValidationResult.make_fail(
            "positive_max_loss",
            "v2_impossible_max_loss",
            message=f"max_loss={math.max_loss}",
            actual=math.max_loss,
        )
    return V2ValidationResult.make_pass(
        "positive_max_loss",
        message=f"max_loss={math.max_loss:.2f}",
        actual=math.max_loss,
    )


def verify_positive_max_profit(math: V2RecomputedMath) -> V2ValidationResult:
    """max_profit must be positive if set."""
    if math.max_profit is None:
        return V2ValidationResult.make_skipped(
            "positive_max_profit", message="max_profit not set",
        )
    if not _is_finite(math.max_profit) or math.max_profit <= 0:
        return V2ValidationResult.make_fail(
            "positive_max_profit",
            "v2_impossible_max_profit",
            message=f"max_profit={math.max_profit}",
            actual=math.max_profit,
        )
    return V2ValidationResult.make_pass(
        "positive_max_profit",
        message=f"max_profit={math.max_profit:.2f}",
        actual=math.max_profit,
    )


def verify_finite_values(math: V2RecomputedMath) -> V2ValidationResult:
    """All set numeric fields must be finite (not NaN, not inf)."""
    non_finite: list[str] = []

    for field_name in (
        "net_credit", "net_debit", "max_profit", "max_loss",
        "width", "pop", "ev", "ror", "kelly",
    ):
        val = getattr(math, field_name, None)
        if val is not None and not _is_finite(val):
            non_finite.append(f"{field_name}={val}")

    # Check breakeven list
    for i, be in enumerate(math.breakeven):
        if not _is_finite(be):
            non_finite.append(f"breakeven[{i}]={be}")

    if non_finite:
        return V2ValidationResult.make_fail(
            "finite_values",
            "v2_non_finite_math",
            message=f"non-finite: {', '.join(non_finite)}",
            metadata={"non_finite_fields": non_finite},
        )
    return V2ValidationResult.make_pass("finite_values")


# ═══════════════════════════════════════════════════════════════════
#  Tolerance-based verification checks
# ═══════════════════════════════════════════════════════════════════

def verify_width(
    candidate: V2Candidate,
    *,
    family_key: str | None = None,
) -> V2ValidationResult:
    """Verify width matches independent computation from leg strikes.

    Formula: width = |short.strike − long.strike|  (2-leg)
             width = center − lower                 (3-leg debit butterfly)
             width = max(put_width, call_width)     (4-leg iron condor / iron butterfly)
             width = max(strike) - min(strike)       (multi-leg fallback)
    """
    m = candidate.math
    if m.width is None:
        return V2ValidationResult.make_skipped(
            "verify_width", message="width not set",
        )

    if len(candidate.legs) < 2:
        return V2ValidationResult.make_skipped(
            "verify_width", message="fewer than 2 legs",
        )

    # Recompute independently
    short_legs = [l for l in candidate.legs if l.side == "short"]
    long_legs = [l for l in candidate.legs if l.side == "long"]

    if len(candidate.legs) == 2 and short_legs and long_legs:
        expected_width = abs(short_legs[0].strike - long_legs[0].strike)
    elif family_key == "butterflies" and len(candidate.legs) == 3:
        # Debit butterfly: width = center − lower (symmetric)
        legs_sorted = sorted(candidate.legs, key=lambda l: l.strike)
        expected_width = legs_sorted[1].strike - legs_sorted[0].strike
    elif family_key in ("iron_condors", "butterflies") and len(candidate.legs) == 4:
        # Iron condor: width = max(put_side_width, call_side_width)
        put_legs = sorted(
            [l for l in candidate.legs if l.option_type == "put"],
            key=lambda l: l.strike,
        )
        call_legs = sorted(
            [l for l in candidate.legs if l.option_type == "call"],
            key=lambda l: l.strike,
        )
        if len(put_legs) == 2 and len(call_legs) == 2:
            put_width = put_legs[1].strike - put_legs[0].strike
            call_width = call_legs[1].strike - call_legs[0].strike
            expected_width = max(put_width, call_width)
        else:
            strikes = [l.strike for l in candidate.legs]
            expected_width = max(strikes) - min(strikes)
    else:
        # Multi-leg: use max minus min strike as width (family hook may differ)
        strikes = [l.strike for l in candidate.legs]
        expected_width = max(strikes) - min(strikes)

    tol = get_tolerance("width", family_key)
    return _compare_metric(
        "verify_width", expected_width, m.width, tol,
        fail_code="v2_width_mismatch",
        warn_code="v2_warn_width_mismatch",
    )


def verify_net_credit_or_debit(
    candidate: V2Candidate,
    *,
    family_key: str | None = None,
) -> V2ValidationResult:
    """Verify net_credit or net_debit from leg quotes.

    Credit: short.bid − long.ask  (2-leg vertical)
    Debit:  long.ask  − short.bid (2-leg vertical)
    3-leg debit butterfly: ask(lower) + ask(upper) − 2×bid(center)
    4-leg iron condor/butterfly: (put_short.bid - put_long.ask)
                               + (call_short.bid - call_long.ask)
    """
    m = candidate.math

    # ── 3-leg debit butterfly path ─────────────────────────
    if family_key == "butterflies" and len(candidate.legs) == 3:
        legs_sorted = sorted(candidate.legs, key=lambda l: l.strike)
        lower, center, upper = legs_sorted
        if (lower.ask is not None and upper.ask is not None
                and center.bid is not None):
            # net_debit = ask(lower) + ask(upper) - 2×bid(center)
            expected = round(lower.ask + upper.ask - 2 * center.bid, 4)
            if m.net_debit is not None:
                tol = get_tolerance("net_debit", family_key)
                return _compare_metric(
                    "verify_net_debit", expected, m.net_debit, tol,
                    fail_code="v2_debit_mismatch",
                    warn_code="v2_warn_debit_mismatch",
                )
            return V2ValidationResult.make_skipped(
                "verify_net_credit_debit",
                message="net_debit not set for debit butterfly",
            )
        return V2ValidationResult.make_skipped(
            "verify_net_credit_debit",
            message="missing bid/ask on debit butterfly legs",
        )

    # ── 4-leg iron condor / iron butterfly path ───────────────
    if family_key in ("iron_condors", "butterflies") and len(candidate.legs) == 4:
        put_legs = sorted(
            [l for l in candidate.legs if l.option_type == "put"],
            key=lambda l: l.strike,
        )
        call_legs = sorted(
            [l for l in candidate.legs if l.option_type == "call"],
            key=lambda l: l.strike,
        )
        if len(put_legs) == 2 and len(call_legs) == 2:
            pl, ps = put_legs[0], put_legs[1]
            cs, cl = call_legs[0], call_legs[1]
            if (ps.bid is not None and pl.ask is not None
                    and cs.bid is not None and cl.ask is not None):
                expected = round(
                    (ps.bid - pl.ask) + (cs.bid - cl.ask), 4,
                )
                if m.net_credit is not None:
                    tol = get_tolerance("net_credit", family_key)
                    return _compare_metric(
                        "verify_net_credit", expected, m.net_credit, tol,
                        fail_code="v2_credit_mismatch",
                        warn_code="v2_warn_credit_mismatch",
                    )
                return V2ValidationResult.make_skipped(
                    "verify_net_credit_debit",
                    message="net_credit not set for iron condor",
                )
        return V2ValidationResult.make_skipped(
            "verify_net_credit_debit",
            message="iron condor leg structure invalid for credit verification",
        )

    # ── Standard 2-leg path ─────────────────────────────────
    if len(candidate.legs) != 2:
        return V2ValidationResult.make_skipped(
            "verify_net_credit_debit",
            message="non-2-leg strategy — family hook required",
        )

    short_legs = [l for l in candidate.legs if l.side == "short"]
    long_legs = [l for l in candidate.legs if l.side == "long"]
    if not short_legs or not long_legs:
        return V2ValidationResult.make_skipped(
            "verify_net_credit_debit",
            message="missing short or long leg",
        )

    short = short_legs[0]
    long = long_legs[0]

    if short.bid is None or long.ask is None:
        return V2ValidationResult.make_skipped(
            "verify_net_credit_debit",
            message="missing bid/ask",
        )

    raw_credit = short.bid - long.ask

    if m.net_credit is not None:
        expected = round(raw_credit, 4)
        tol = get_tolerance("net_credit", family_key)
        return _compare_metric(
            "verify_net_credit", expected, m.net_credit, tol,
            fail_code="v2_credit_mismatch",
            warn_code="v2_warn_credit_mismatch",
        )

    if m.net_debit is not None:
        expected = round(long.ask - short.bid, 4)
        tol = get_tolerance("net_debit", family_key)
        return _compare_metric(
            "verify_net_debit", expected, m.net_debit, tol,
            fail_code="v2_debit_mismatch",
            warn_code="v2_warn_debit_mismatch",
        )

    return V2ValidationResult.make_skipped(
        "verify_net_credit_debit",
        message="neither net_credit nor net_debit set",
    )


def verify_max_profit(
    candidate: V2Candidate,
    *,
    family_key: str | None = None,
) -> V2ValidationResult:
    """Verify max_profit from credit/debit and width.

    Credit: max_profit = net_credit × 100
    Debit:  max_profit = (width − net_debit) × 100
    """
    m = candidate.math

    if m.max_profit is None:
        return V2ValidationResult.make_skipped(
            "verify_max_profit", message="max_profit not set",
        )

    expected: float | None = None

    if m.net_credit is not None:
        # Credit spread: max_profit = credit × 100
        expected = round(m.net_credit * 100, 2)
    elif m.net_debit is not None and m.width is not None:
        # Debit spread: max_profit = (width - debit) × 100
        expected = round((m.width - m.net_debit) * 100, 2)

    if expected is None:
        return V2ValidationResult.make_skipped(
            "verify_max_profit",
            message="cannot recompute (missing credit/debit or width)",
        )

    tol = get_tolerance("max_profit", family_key)
    return _compare_metric(
        "verify_max_profit", expected, m.max_profit, tol,
        fail_code="v2_max_profit_mismatch",
        warn_code="v2_warn_max_profit_mismatch",
    )


def verify_max_loss(
    candidate: V2Candidate,
    *,
    family_key: str | None = None,
) -> V2ValidationResult:
    """Verify max_loss from credit/debit and width.

    Credit: max_loss = (width − net_credit) × 100
    Debit:  max_loss = net_debit × 100
    """
    m = candidate.math

    if m.max_loss is None:
        return V2ValidationResult.make_skipped(
            "verify_max_loss", message="max_loss not set",
        )

    expected: float | None = None

    if m.net_credit is not None and m.width is not None:
        expected = round((m.width - m.net_credit) * 100, 2)
    elif m.net_debit is not None:
        expected = round(m.net_debit * 100, 2)

    if expected is None:
        return V2ValidationResult.make_skipped(
            "verify_max_loss",
            message="cannot recompute (missing credit/debit or width)",
        )

    tol = get_tolerance("max_loss", family_key)
    return _compare_metric(
        "verify_max_loss", expected, m.max_loss, tol,
        fail_code="v2_max_loss_mismatch",
        warn_code="v2_warn_max_loss_mismatch",
    )


def verify_breakeven(
    candidate: V2Candidate,
    *,
    family_key: str | None = None,
) -> V2ValidationResult:
    """Verify breakeven price(s) for 2-leg verticals.

    Credit put:  breakeven = short.strike − net_credit
    Credit call: breakeven = short.strike + net_credit
    Debit call:  breakeven = long.strike  + net_debit
    Debit put:   breakeven = long.strike  − net_debit
    """
    m = candidate.math

    if not m.breakeven:
        return V2ValidationResult.make_skipped(
            "verify_breakeven", message="no breakeven values set",
        )

    # ── 3-leg debit butterfly breakeven path ──────────────
    if (family_key == "butterflies" and len(candidate.legs) == 3
            and len(m.breakeven) == 2 and m.net_debit is not None):
        legs_sorted = sorted(candidate.legs, key=lambda l: l.strike)
        lower, center, upper = legs_sorted
        expected_low = round(lower.strike + m.net_debit, 2)
        expected_high = round(upper.strike - m.net_debit, 2)
        tol = get_tolerance("breakeven", family_key)
        status_low, delta_low = tol.classify(expected_low, m.breakeven[0])
        status_high, delta_high = tol.classify(expected_high, m.breakeven[1])
        _rank = {STATUS_FAIL: 0, STATUS_WARN: 1, STATUS_PASS: 2,
                 STATUS_SKIPPED: 3}
        worst = min(
            [(status_low, delta_low, expected_low, m.breakeven[0]),
             (status_high, delta_high, expected_high, m.breakeven[1])],
            key=lambda t: _rank.get(t[0], 99),
        )
        w_status, w_delta, w_expected, w_actual = worst
        msg = (
            f"be_low: exp={expected_low} act={m.breakeven[0]}, "
            f"be_high: exp={expected_high} act={m.breakeven[1]}"
        )
        if w_status == STATUS_PASS:
            return V2ValidationResult.make_pass(
                "verify_breakeven", message=msg,
                expected=w_expected, actual=w_actual, delta=w_delta,
            )
        if w_status == STATUS_WARN:
            return V2ValidationResult.make_warn(
                "verify_breakeven", "v2_warn_breakeven_mismatch",
                message=msg, expected=w_expected, actual=w_actual,
                delta=w_delta,
            )
        return V2ValidationResult.make_fail(
            "verify_breakeven", "v2_breakeven_mismatch",
            message=msg, expected=w_expected, actual=w_actual,
            delta=w_delta,
        )

    # ── 4-leg iron condor / iron butterfly path ───────────────
    if (family_key in ("iron_condors", "butterflies") and len(candidate.legs) == 4
            and len(m.breakeven) == 2 and m.net_credit is not None):
        put_legs = sorted(
            [l for l in candidate.legs if l.option_type == "put"],
            key=lambda l: l.strike,
        )
        call_legs = sorted(
            [l for l in candidate.legs if l.option_type == "call"],
            key=lambda l: l.strike,
        )
        if len(put_legs) == 2 and len(call_legs) == 2:
            put_short = put_legs[1]   # higher put strike
            call_short = call_legs[0]  # lower call strike
            expected_low = round(put_short.strike - m.net_credit, 2)
            expected_high = round(call_short.strike + m.net_credit, 2)
            tol = get_tolerance("breakeven", family_key)
            # Verify both breakevens — worst result wins
            status_low, delta_low = tol.classify(expected_low, m.breakeven[0])
            status_high, delta_high = tol.classify(expected_high, m.breakeven[1])
            # Determine worst status
            _rank = {STATUS_FAIL: 0, STATUS_WARN: 1, STATUS_PASS: 2,
                     STATUS_SKIPPED: 3}
            worst = min(
                [(status_low, delta_low, expected_low, m.breakeven[0]),
                 (status_high, delta_high, expected_high, m.breakeven[1])],
                key=lambda t: _rank.get(t[0], 99),
            )
            w_status, w_delta, w_expected, w_actual = worst
            msg = (
                f"be_low: exp={expected_low} act={m.breakeven[0]}, "
                f"be_high: exp={expected_high} act={m.breakeven[1]}"
            )
            if w_status == STATUS_PASS:
                return V2ValidationResult.make_pass(
                    "verify_breakeven", message=msg,
                    expected=w_expected, actual=w_actual, delta=w_delta,
                )
            if w_status == STATUS_WARN:
                return V2ValidationResult.make_warn(
                    "verify_breakeven", "v2_warn_breakeven_mismatch",
                    message=msg, expected=w_expected, actual=w_actual,
                    delta=w_delta,
                )
            return V2ValidationResult.make_fail(
                "verify_breakeven", "v2_breakeven_mismatch",
                message=msg, expected=w_expected, actual=w_actual,
                delta=w_delta,
            )

    # ── Standard 2-leg path ───────────────────────────────
    if len(candidate.legs) != 2:
        return V2ValidationResult.make_skipped(
            "verify_breakeven",
            message="non-2-leg strategy — family hook required",
        )

    short_legs = [l for l in candidate.legs if l.side == "short"]
    long_legs = [l for l in candidate.legs if l.side == "long"]
    if not short_legs or not long_legs:
        return V2ValidationResult.make_skipped(
            "verify_breakeven", message="missing short or long leg",
        )

    short = short_legs[0]
    long = long_legs[0]
    expected_be: float | None = None

    if m.net_credit is not None:
        if short.option_type == "put":
            expected_be = round(short.strike - m.net_credit, 2)
        else:
            expected_be = round(short.strike + m.net_credit, 2)
    elif m.net_debit is not None:
        if long.option_type == "call":
            expected_be = round(long.strike + m.net_debit, 2)
        else:
            expected_be = round(long.strike - m.net_debit, 2)

    if expected_be is None:
        return V2ValidationResult.make_skipped(
            "verify_breakeven",
            message="cannot recompute breakeven",
        )

    actual_be = m.breakeven[0]
    tol = get_tolerance("breakeven", family_key)
    return _compare_metric(
        "verify_breakeven", expected_be, actual_be, tol,
        fail_code="v2_breakeven_mismatch",
        warn_code="v2_warn_breakeven_mismatch",
    )


def verify_ror(
    candidate: V2Candidate,
    *,
    family_key: str | None = None,
) -> V2ValidationResult:
    """Verify return-on-risk = max_profit / max_loss."""
    m = candidate.math

    if m.ror is None:
        return V2ValidationResult.make_skipped(
            "verify_ror", message="ror not set",
        )

    if m.max_profit is None or m.max_loss is None or m.max_loss <= 0:
        return V2ValidationResult.make_skipped(
            "verify_ror",
            message="cannot recompute (missing max_profit/max_loss or max_loss<=0)",
        )

    expected = round(m.max_profit / m.max_loss, 4)
    tol = get_tolerance("ror", family_key)
    return _compare_metric(
        "verify_ror", expected, m.ror, tol,
        fail_code="v2_ror_mismatch",
        warn_code="v2_warn_ror_mismatch",
    )


# ═══════════════════════════════════════════════════════════════════
#  Presence checks (non-fatal)
# ═══════════════════════════════════════════════════════════════════

def check_pop_computed(math: V2RecomputedMath) -> V2ValidationResult:
    """Check whether POP was computed (warn if not)."""
    if math.pop is not None:
        return V2ValidationResult.make_pass(
            "pop_computed",
            message=f"pop={math.pop:.4f} source={math.pop_source}",
            actual=math.pop,
        )
    return V2ValidationResult.make_warn(
        "pop_computed",
        "v2_warn_pop_missing",
        message="POP could not be computed — missing delta",
    )


def check_ev_computed(math: V2RecomputedMath) -> V2ValidationResult:
    """Check whether EV was computed (warn if not)."""
    if math.ev is not None:
        return V2ValidationResult.make_pass(
            "ev_computed",
            message=f"ev={math.ev:.2f}",
            actual=math.ev,
        )
    return V2ValidationResult.make_warn(
        "ev_computed",
        "v2_warn_ev_missing",
        message="EV could not be computed (missing POP or max_loss)",
    )


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _compare_metric(
    check_key: str,
    expected: float,
    actual: float,
    tol: V2ToleranceSpec,
    *,
    fail_code: str,
    warn_code: str,
) -> V2ValidationResult:
    """Compare expected vs actual using tolerance classification.

    Returns pass/warn/fail V2ValidationResult with delta and codes.
    """
    status, delta = tol.classify(expected, actual)

    if status == STATUS_PASS:
        return V2ValidationResult.make_pass(
            check_key,
            message=f"expected={expected}, actual={actual}",
            expected=expected,
            actual=actual,
            delta=delta,
        )
    if status == STATUS_WARN:
        return V2ValidationResult.make_warn(
            check_key,
            warn_code,
            message=f"expected={expected}, actual={actual}, delta={delta}",
            expected=expected,
            actual=actual,
            delta=delta,
        )
    # FAIL
    return V2ValidationResult.make_fail(
        check_key,
        fail_code,
        message=f"expected={expected}, actual={actual}, delta={delta}",
        expected=expected,
        actual=actual,
        delta=delta,
    )


def _is_finite(value: object) -> bool:
    """True if value is a finite number."""
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
