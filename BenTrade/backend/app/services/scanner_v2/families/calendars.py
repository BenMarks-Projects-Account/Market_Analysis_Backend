"""Options Scanner V2 — Calendar Spreads family (skeleton).

Handles:
- calendar_spread
- calendar_call_spread
- calendar_put_spread

Implementation status: SKELETON — Phase B not yet implemented.

Construction logic (Phase B)
----------------------------
For each pair of eligible expirations (front, back) where front < back:
    For each strike available in both expirations:
        Short 1× front-month, Long 1× back-month.
        Same strike, same option_type.

Net debit = back_leg.ask − front_leg.bid (paying for time value).

Family-specific structural checks:
- Exactly 2 legs.
- Same strike on both legs.
- Same option_type on both legs.
- Front expiration < back expiration (NOT same expiry).

Family math override:
- Different from verticals: no fixed width, no simple max profit/loss.
- Max loss ≈ net debit paid.
- Max profit is path-dependent (estimated, not exact).
"""

from __future__ import annotations

from typing import Any

from app.services.scanner_v2.base_scanner import BaseV2Scanner
from app.services.scanner_v2.contracts import (
    V2Candidate,
    V2CheckResult,
    V2RecomputedMath,
)


class CalendarsV2Scanner(BaseV2Scanner):
    """V2 scanner for calendar spread families."""

    family_key = "calendars"
    scanner_version = "2.0.0-skeleton"
    dte_min = 7
    dte_max = 90

    def construct_candidates(
        self,
        *,
        chain: dict[str, Any],
        symbol: str,
        underlying_price: float | None,
        expirations: list[str],
        strategy_id: str,
        scanner_key: str,
        context: dict[str, Any],
    ) -> list[V2Candidate]:
        """Phase B — construct all calendar spread candidates.

        TODO: Implement in later prompt.
        """
        raise NotImplementedError(
            "CalendarsV2Scanner.construct_candidates() not yet implemented."
        )

    def family_structural_checks(
        self, candidate: V2Candidate,
    ) -> list[V2CheckResult]:
        """Calendar-specific structural checks.

        - Exactly 2 legs.
        - Same strike.
        - Same option_type.
        - Different expirations (front < back).
        """
        checks: list[V2CheckResult] = []

        if len(candidate.legs) != 2:
            checks.append(V2CheckResult(
                "calendar_leg_count", False,
                f"expected 2 legs, got {len(candidate.legs)}",
            ))
            candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
            return checks
        checks.append(V2CheckResult("calendar_leg_count", True, "2 legs"))

        leg_a, leg_b = candidate.legs

        # Same strike
        if leg_a.strike != leg_b.strike:
            checks.append(V2CheckResult(
                "calendar_same_strike", False,
                f"strikes differ: {leg_a.strike} vs {leg_b.strike}",
            ))
            candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
        else:
            checks.append(V2CheckResult("calendar_same_strike", True, ""))

        # Same option type
        if leg_a.option_type != leg_b.option_type:
            checks.append(V2CheckResult(
                "calendar_same_type", False,
                f"types differ: {leg_a.option_type} vs {leg_b.option_type}",
            ))
            candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
        else:
            checks.append(V2CheckResult("calendar_same_type", True, ""))

        # Different expirations
        if leg_a.expiration == leg_b.expiration:
            checks.append(V2CheckResult(
                "calendar_different_expiry", False,
                "both legs have same expiration",
            ))
            candidate.diagnostics.reject_reasons.append("v2_mismatched_expiry")
        else:
            checks.append(V2CheckResult("calendar_different_expiry", True, ""))

        return checks

    def family_math(
        self, candidate: V2Candidate,
    ) -> V2RecomputedMath:
        """Calendar-specific math.

        TODO: Full implementation in later prompt.
        """
        return V2RecomputedMath(notes={"status": "skeleton — not yet implemented"})
