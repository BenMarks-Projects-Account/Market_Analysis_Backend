"""Shared structural validation — composable checks for V2 candidates.

This module provides reusable structural validation functions that all
V2 scanner families can call.  Each function validates one aspect of
candidate structure and returns a ``V2ValidationResult``.

Layer boundary
──────────────
Structural validation asks:
    "Is this trade structure valid enough to even discuss?"

It does NOT ask:
    "Is this a good trade?"  (That's downstream.)
    "Does the math check out?"  (That's math_checks.py.)

Usage
─────
Family builders call individual checks or the shared runner::

    from app.services.scanner_v2.validation.structural import (
        run_shared_structural_checks,
        validate_same_expiry,
    )

    # Full shared checks
    summary = run_shared_structural_checks(candidate)

    # Or individual
    result = validate_same_expiry(candidate.legs)

Shared vs family-specific
─────────────────────────
Shared checks (this module):
    leg count, required fields, sides, option types, same expiry,
    width, pricing sanity, no duplicate legs.

Family-specific checks (family modules via hooks):
    vertical ordering, condor geometry, butterfly symmetry,
    calendar expiry alignment.
"""

from __future__ import annotations

import math
from typing import Any

from app.services.scanner_v2.contracts import V2Candidate, V2Leg, V2RecomputedMath
from app.services.scanner_v2.validation.contracts import (
    V2ValidationResult,
    V2ValidationSummary,
)


# ═══════════════════════════════════════════════════════════════════
#  Shared structural runner
# ═══════════════════════════════════════════════════════════════════

def run_shared_structural_checks(
    candidate: V2Candidate,
    *,
    expected_leg_count: int | tuple[int, ...] | None = None,
    require_same_expiry: bool = True,
) -> V2ValidationSummary:
    """Run all shared structural checks on a candidate.

    Parameters
    ----------
    candidate
        V2Candidate to validate.
    expected_leg_count
        If set, validate exact leg count.  Can be an int or tuple
        of allowed counts (e.g. ``(3, 4)`` for butterflies).
    require_same_expiry
        If True, validate all legs share the same expiration.
        Set False for calendars/diagonals.

    Returns
    -------
    V2ValidationSummary
        Aggregated results — check ``summary.all_passed`` and
        ``summary.fail_codes`` to decide rejection.
    """
    results: list[V2ValidationResult] = []

    # 1. Required fields
    results.append(validate_required_fields(candidate))

    # 2. Leg count
    if expected_leg_count is not None:
        results.append(validate_leg_count(candidate.legs, expected_leg_count))
    else:
        results.append(validate_has_legs(candidate.legs))

    # Stop here if legs are missing or badly broken
    if any(r.is_failure for r in results):
        return V2ValidationSummary(results=results)

    # 3. Sides (long/short)
    results.append(validate_sides(candidate.legs))

    # 4. Option types (put/call)
    results.append(validate_option_types(candidate.legs))

    # 5. Expiry consistency
    if require_same_expiry:
        results.append(validate_same_expiry(candidate.legs))

    # 6. No duplicate legs
    results.append(validate_no_duplicate_legs(candidate.legs))

    # 7. Width (if set)
    if candidate.math.width is not None:
        results.append(validate_width(candidate.math))

    # 8. Pricing sanity
    results.append(validate_pricing_sanity(candidate.math))

    return V2ValidationSummary(results=results)


# ═══════════════════════════════════════════════════════════════════
#  Individual structural checks
# ═══════════════════════════════════════════════════════════════════

def validate_has_legs(legs: list[V2Leg]) -> V2ValidationResult:
    """Check that at least one leg exists."""
    if not legs:
        return V2ValidationResult.make_fail(
            "has_legs",
            "v2_malformed_legs",
            message="no legs present",
            actual=0,
        )
    return V2ValidationResult.make_pass(
        "has_legs",
        message=f"{len(legs)} leg(s)",
        actual=len(legs),
    )


def validate_leg_count(
    legs: list[V2Leg],
    expected: int | tuple[int, ...],
) -> V2ValidationResult:
    """Check exact leg count matches expected."""
    count = len(legs)
    allowed = (expected,) if isinstance(expected, int) else expected

    if count not in allowed:
        return V2ValidationResult.make_fail(
            "leg_count",
            "v2_malformed_legs",
            message=f"expected {expected} legs, got {count}",
            expected=expected,
            actual=count,
        )
    return V2ValidationResult.make_pass(
        "leg_count",
        message=f"{count} legs",
        expected=expected,
        actual=count,
    )


def validate_required_fields(candidate: V2Candidate) -> V2ValidationResult:
    """Check that critical identity and leg fields are present."""
    missing: list[str] = []

    if not candidate.symbol:
        missing.append("symbol")
    if not candidate.strategy_id:
        missing.append("strategy_id")

    for leg in candidate.legs:
        prefix = f"leg[{leg.index}]"
        if not _is_finite(leg.strike):
            missing.append(f"{prefix}.strike")
        if leg.option_type not in ("put", "call"):
            missing.append(f"{prefix}.option_type")
        if leg.side not in ("long", "short"):
            missing.append(f"{prefix}.side")
        if not leg.expiration:
            missing.append(f"{prefix}.expiration")

    if missing:
        return V2ValidationResult.make_fail(
            "required_fields",
            "v2_malformed_legs",
            message=f"missing: {', '.join(missing)}",
            metadata={"missing_fields": missing},
        )
    return V2ValidationResult.make_pass("required_fields")


def validate_sides(legs: list[V2Leg]) -> V2ValidationResult:
    """Check that every leg has a valid side (long/short)."""
    bad = [leg.index for leg in legs if leg.side not in ("long", "short")]
    if bad:
        return V2ValidationResult.make_fail(
            "valid_sides",
            "v2_malformed_legs",
            message=f"invalid side on legs {bad}",
            metadata={"bad_leg_indices": bad},
        )
    return V2ValidationResult.make_pass("valid_sides")


def validate_option_types(legs: list[V2Leg]) -> V2ValidationResult:
    """Check that every leg has a valid option type (put/call)."""
    bad = [leg.index for leg in legs if leg.option_type not in ("put", "call")]
    if bad:
        return V2ValidationResult.make_fail(
            "valid_option_types",
            "v2_malformed_legs",
            message=f"invalid option_type on legs {bad}",
            metadata={"bad_leg_indices": bad},
        )
    return V2ValidationResult.make_pass("valid_option_types")


def validate_same_expiry(legs: list[V2Leg]) -> V2ValidationResult:
    """Check that all legs share the same expiration date."""
    expirations = {leg.expiration for leg in legs}
    if len(expirations) > 1:
        return V2ValidationResult.make_fail(
            "same_expiry",
            "v2_mismatched_expiry",
            message=f"found {len(expirations)} distinct expirations: {sorted(expirations)}",
            expected=1,
            actual=len(expirations),
            metadata={"expirations": sorted(expirations)},
        )
    return V2ValidationResult.make_pass(
        "same_expiry",
        message=f"all legs expire {next(iter(expirations))}" if expirations else "",
    )


def validate_multi_expiry(
    legs: list[V2Leg],
    min_expirations: int = 2,
) -> V2ValidationResult:
    """Check that legs span at least ``min_expirations`` distinct dates.

    For calendars/diagonals that require different expirations.
    """
    expirations = {leg.expiration for leg in legs}
    if len(expirations) < min_expirations:
        return V2ValidationResult.make_fail(
            "multi_expiry",
            "v2_mismatched_expiry",
            message=f"expected >= {min_expirations} expirations, got {len(expirations)}",
            expected=min_expirations,
            actual=len(expirations),
        )
    return V2ValidationResult.make_pass(
        "multi_expiry",
        message=f"{len(expirations)} expirations: {sorted(expirations)}",
    )


def validate_width(math: V2RecomputedMath) -> V2ValidationResult:
    """Check that width is positive and finite."""
    w = math.width
    if w is None:
        return V2ValidationResult.make_skipped(
            "width_positive",
            message="width not set",
        )
    if not _is_finite(w) or w <= 0:
        return V2ValidationResult.make_fail(
            "width_positive",
            "v2_invalid_width",
            message=f"width={w}",
            actual=w,
        )
    return V2ValidationResult.make_pass(
        "width_positive",
        message=f"width={w}",
        actual=w,
    )


def validate_pricing_sanity(math: V2RecomputedMath) -> V2ValidationResult:
    """Check that credit/debit are sane relative to width.

    For credit strategies: 0 < credit < width.
    For debit strategies:  0 < debit  < width.
    """
    w = math.width

    if math.net_credit is not None:
        if math.net_credit <= 0:
            return V2ValidationResult.make_fail(
                "pricing_sanity",
                "v2_non_positive_credit",
                message=f"credit={math.net_credit} is non-positive",
                actual=math.net_credit,
            )
        if w is not None and w > 0 and math.net_credit >= w:
            return V2ValidationResult.make_fail(
                "pricing_sanity",
                "v2_impossible_pricing",
                message=f"credit={math.net_credit} >= width={w}",
                expected=f"< {w}",
                actual=math.net_credit,
            )
        return V2ValidationResult.make_pass(
            "pricing_sanity",
            message=f"credit={math.net_credit}",
        )

    if math.net_debit is not None:
        if math.net_debit <= 0:
            return V2ValidationResult.make_fail(
                "pricing_sanity",
                "v2_non_positive_credit",
                message=f"debit={math.net_debit} is non-positive",
                actual=math.net_debit,
            )
        if w is not None and w > 0 and math.net_debit >= w:
            return V2ValidationResult.make_fail(
                "pricing_sanity",
                "v2_impossible_pricing",
                message=f"debit={math.net_debit} >= width={w}",
                expected=f"< {w}",
                actual=math.net_debit,
            )
        return V2ValidationResult.make_pass(
            "pricing_sanity",
            message=f"debit={math.net_debit}",
        )

    # Neither credit nor debit set — skip
    return V2ValidationResult.make_skipped(
        "pricing_sanity",
        message="neither net_credit nor net_debit set",
    )


def validate_no_duplicate_legs(legs: list[V2Leg]) -> V2ValidationResult:
    """Check for duplicate legs (same strike + type + side + expiration)."""
    seen: set[tuple[float, str, str, str]] = set()
    dupes: list[int] = []
    for leg in legs:
        key = (leg.strike, leg.option_type, leg.side, leg.expiration)
        if key in seen:
            dupes.append(leg.index)
        seen.add(key)

    if dupes:
        return V2ValidationResult.make_fail(
            "no_duplicate_legs",
            "v2_malformed_legs",
            message=f"duplicate legs at indices {dupes}",
            metadata={"duplicate_indices": dupes},
        )
    return V2ValidationResult.make_pass("no_duplicate_legs")


def validate_strike_ordering(
    legs: list[V2Leg],
    *,
    ascending: bool = True,
) -> V2ValidationResult:
    """Check that leg strikes are in the specified order.

    Legs are compared in their list order (by index).

    Parameters
    ----------
    ascending
        If True, verify strikes increase with leg index.
        If False, verify strikes decrease.
    """
    strikes = [leg.strike for leg in sorted(legs, key=lambda l: l.index)]

    for i in range(len(strikes) - 1):
        if ascending and strikes[i] >= strikes[i + 1]:
            return V2ValidationResult.make_fail(
                "strike_ordering",
                "v2_malformed_legs",
                message=f"strike[{i}]={strikes[i]} >= strike[{i+1}]={strikes[i+1]} (expected ascending)",
                metadata={"strikes": strikes},
            )
        if not ascending and strikes[i] <= strikes[i + 1]:
            return V2ValidationResult.make_fail(
                "strike_ordering",
                "v2_malformed_legs",
                message=f"strike[{i}]={strikes[i]} <= strike[{i+1}]={strikes[i+1]} (expected descending)",
                metadata={"strikes": strikes},
            )

    return V2ValidationResult.make_pass(
        "strike_ordering",
        message=f"{'ascending' if ascending else 'descending'}: {strikes}",
    )


def validate_same_option_type(legs: list[V2Leg]) -> V2ValidationResult:
    """Check that all legs have the same option type (put or call)."""
    types = {leg.option_type for leg in legs}
    if len(types) > 1:
        return V2ValidationResult.make_fail(
            "same_option_type",
            "v2_malformed_legs",
            message=f"mixed option types: {types}",
            metadata={"types": sorted(types)},
        )
    return V2ValidationResult.make_pass(
        "same_option_type",
        message=f"all {next(iter(types))}" if types else "",
    )


def validate_has_short_and_long(legs: list[V2Leg]) -> V2ValidationResult:
    """Check that both short and long sides are present."""
    sides = {leg.side for leg in legs}
    if "short" not in sides or "long" not in sides:
        return V2ValidationResult.make_fail(
            "has_short_and_long",
            "v2_malformed_legs",
            message=f"expected both short and long, got {sides}",
            metadata={"sides": sorted(sides)},
        )
    return V2ValidationResult.make_pass("has_short_and_long")


# ═══════════════════════════════════════════════════════════════════
#  Family-specific structural check runners
# ═══════════════════════════════════════════════════════════════════

def run_vertical_structural_checks(
    candidate: V2Candidate,
) -> V2ValidationSummary:
    """Structural checks specific to vertical spreads.

    - Exactly 2 legs.
    - One short, one long.
    - Same option type.
    - Same expiry.
    - Valid width.

    Returns combined shared + family-specific results.
    """
    shared = run_shared_structural_checks(
        candidate,
        expected_leg_count=2,
        require_same_expiry=True,
    )
    if shared.has_failures:
        return shared

    family_results: list[V2ValidationResult] = [
        validate_has_short_and_long(candidate.legs),
        validate_same_option_type(candidate.legs),
    ]
    return V2ValidationSummary(results=shared.results + family_results)


def run_iron_condor_structural_checks(
    candidate: V2Candidate,
) -> V2ValidationSummary:
    """Structural checks specific to iron condors.

    - Exactly 4 legs.
    - Same expiry.
    - 2 puts + 2 calls.
    - Strike ordering: put_long < put_short < call_short < call_long.
    """
    shared = run_shared_structural_checks(
        candidate,
        expected_leg_count=4,
        require_same_expiry=True,
    )
    if shared.has_failures:
        return shared

    family_results: list[V2ValidationResult] = []

    # 2 puts + 2 calls
    puts = [l for l in candidate.legs if l.option_type == "put"]
    calls = [l for l in candidate.legs if l.option_type == "call"]
    if len(puts) != 2 or len(calls) != 2:
        family_results.append(V2ValidationResult.make_fail(
            "ic_put_call_balance",
            "v2_malformed_legs",
            message=f"expected 2P+2C, got {len(puts)}P+{len(calls)}C",
        ))
        return V2ValidationSummary(results=shared.results + family_results)

    family_results.append(V2ValidationResult.make_pass("ic_put_call_balance"))

    # Strike ordering
    puts_sorted = sorted(puts, key=lambda l: l.strike)
    calls_sorted = sorted(calls, key=lambda l: l.strike)
    pl, ps = puts_sorted[0], puts_sorted[1]
    cs, cl = calls_sorted[0], calls_sorted[1]

    if not (pl.strike < ps.strike < cs.strike < cl.strike):
        family_results.append(V2ValidationResult.make_fail(
            "ic_strike_ordering",
            "v2_malformed_legs",
            message=(
                f"expected PL < PS < CS < CL, got "
                f"{pl.strike} < {ps.strike} < {cs.strike} < {cl.strike}"
            ),
            metadata={
                "put_long": pl.strike,
                "put_short": ps.strike,
                "call_short": cs.strike,
                "call_long": cl.strike,
            },
        ))
    else:
        family_results.append(V2ValidationResult.make_pass(
            "ic_strike_ordering",
            message=f"PL={pl.strike} < PS={ps.strike} < CS={cs.strike} < CL={cl.strike}",
        ))

    return V2ValidationSummary(results=shared.results + family_results)


def run_butterfly_structural_checks(
    candidate: V2Candidate,
) -> V2ValidationSummary:
    """Structural checks specific to butterflies.

    - 3 legs (debit butterfly) or 4 legs (iron butterfly).
    - Same expiry.
    - Symmetric wing structure: body = (lower + upper) / 2.
    """
    shared = run_shared_structural_checks(
        candidate,
        expected_leg_count=(3, 4),
        require_same_expiry=True,
    )
    if shared.has_failures:
        return shared

    family_results: list[V2ValidationResult] = []

    # Sort legs by strike
    legs_sorted = sorted(candidate.legs, key=lambda l: l.strike)
    strikes = [l.strike for l in legs_sorted]

    # Symmetric wing check
    if len(strikes) >= 3:
        lower = strikes[0]
        upper = strikes[-1]
        expected_body = (lower + upper) / 2

        # Find body strikes (middle leg(s))
        body_strikes = strikes[1:-1]
        if body_strikes:
            actual_body = body_strikes[0]
            delta = abs(expected_body - actual_body)
            if delta > 0.01:
                family_results.append(V2ValidationResult.make_fail(
                    "butterfly_symmetry",
                    "v2_malformed_legs",
                    message=(
                        f"body strike {actual_body} != midpoint "
                        f"({lower} + {upper}) / 2 = {expected_body}"
                    ),
                    expected=expected_body,
                    actual=actual_body,
                    delta=delta,
                ))
            else:
                family_results.append(V2ValidationResult.make_pass(
                    "butterfly_symmetry",
                    message=f"body={actual_body}, wings=({lower}, {upper})",
                ))

    return V2ValidationSummary(results=shared.results + family_results)


def run_calendar_structural_checks(
    candidate: V2Candidate,
) -> V2ValidationSummary:
    """Structural checks specific to calendar/diagonal spreads.

    - Exactly 2 legs.
    - Different expirations (at least 2).
    - Same strike (pure calendar) or allowed strike relationship.
    - One short, one long.
    - Same option type.
    """
    shared = run_shared_structural_checks(
        candidate,
        expected_leg_count=2,
        require_same_expiry=False,
    )
    if shared.has_failures:
        return shared

    family_results: list[V2ValidationResult] = [
        validate_multi_expiry(candidate.legs, min_expirations=2),
        validate_has_short_and_long(candidate.legs),
        validate_same_option_type(candidate.legs),
    ]

    # Same strike check (pure calendar)
    strikes = {l.strike for l in candidate.legs}
    if len(strikes) == 1:
        family_results.append(V2ValidationResult.make_pass(
            "calendar_same_strike",
            message=f"both legs at strike={next(iter(strikes))}",
        ))
    else:
        # Diagonal — not a failure, but note it
        family_results.append(V2ValidationResult.make_pass(
            "calendar_strike_relationship",
            message=f"diagonal: strikes={sorted(strikes)}",
            metadata={"is_diagonal": True},
        ))

    return V2ValidationSummary(results=shared.results + family_results)


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _is_finite(value: object) -> bool:
    """True if value is a finite number."""
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
