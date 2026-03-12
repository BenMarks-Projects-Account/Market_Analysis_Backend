"""Options Scanner V2 — Vertical Spreads family (skeleton).

Handles:
- put_credit_spread
- call_credit_spread
- put_debit
- call_debit

Implementation status: SKELETON — Phase B not yet implemented.
Full implementation comes in a later prompt.

Construction logic (Phase B)
----------------------------
For each eligible expiration:
    For each (short_strike, long_strike) pair:
        - Credit spreads: short is closer to ATM, long is further OTM.
          Put credit: short_strike > long_strike.
          Call credit: short_strike < long_strike.
        - Debit spreads: long is closer to ATM, short is further OTM.
          Put debit: long_strike > short_strike.
          Call debit: long_strike < short_strike.

Width = |short_strike - long_strike|

Family-specific structural checks:
- Exactly 2 legs (one short, one long).
- Both legs same option_type (put or call).
- Both legs same expiration.

Family math:
- Uses default vertical math (credit or debit).  No override needed.
"""

from __future__ import annotations

from typing import Any

from app.services.scanner_v2.base_scanner import BaseV2Scanner
from app.services.scanner_v2.contracts import V2Candidate, V2CheckResult


class VerticalSpreadsV2Scanner(BaseV2Scanner):
    """V2 scanner for vertical spread families.

    Supports put_credit_spread, call_credit_spread, put_debit, call_debit.
    """

    family_key = "vertical_spreads"
    scanner_version = "2.0.0-skeleton"
    dte_min = 1
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
        """Phase B — construct all vertical spread candidates.

        TODO: Implement in later prompt.
        """
        raise NotImplementedError(
            "VerticalSpreadsV2Scanner.construct_candidates() not yet implemented. "
            "This is a skeleton for the V2 architecture foundation."
        )

    def family_structural_checks(
        self, candidate: V2Candidate,
    ) -> list[V2CheckResult]:
        """Vertical-spread-specific structural checks.

        - Exactly 2 legs.
        - One short, one long.
        - Same option_type on both legs.
        """
        checks: list[V2CheckResult] = []

        if len(candidate.legs) != 2:
            checks.append(V2CheckResult(
                "vertical_leg_count", False,
                f"expected 2 legs, got {len(candidate.legs)}",
            ))
            candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
            return checks
        checks.append(V2CheckResult("vertical_leg_count", True, "2 legs"))

        sides = {leg.side for leg in candidate.legs}
        if sides != {"long", "short"}:
            checks.append(V2CheckResult(
                "vertical_sides", False,
                f"expected one long + one short, got {sides}",
            ))
            candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
        else:
            checks.append(V2CheckResult("vertical_sides", True, ""))

        types = {leg.option_type for leg in candidate.legs}
        if len(types) != 1:
            checks.append(V2CheckResult(
                "vertical_same_type", False,
                f"mixed option types: {types}",
            ))
            candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
        else:
            checks.append(V2CheckResult("vertical_same_type", True, ""))

        return checks
