"""Options Scanner V2 — Iron Condors family (skeleton).

Handles:
- iron_condor

Implementation status: SKELETON — Phase B not yet implemented.

Construction logic (Phase B)
----------------------------
For each eligible expiration:
    For each (put_short, put_long, call_short, call_long) combo:
        - put_long < put_short < call_short < call_long
        - All same expiration
        - Net credit = (put_short.bid - put_long.ask) + (call_short.bid - call_long.ask)

Family-specific structural checks:
- Exactly 4 legs (2 puts, 2 calls).
- Strike ordering: put_long < put_short < call_short < call_long.
- All same expiration.

Family math override:
- Net credit = put_side_credit + call_side_credit.
- Max profit = net_credit × 100.
- Max loss = (wider_wing_width - net_credit) × 100.
- Width = max(put_width, call_width).
"""

from __future__ import annotations

from typing import Any

from app.services.scanner_v2.base_scanner import BaseV2Scanner
from app.services.scanner_v2.contracts import (
    V2Candidate,
    V2CheckResult,
    V2RecomputedMath,
)
from app.services.scanner_v2.data import V2NarrowedUniverse


class IronCondorsV2Scanner(BaseV2Scanner):
    """V2 scanner for iron condors."""

    family_key = "iron_condors"
    scanner_version = "2.0.0-skeleton"
    dte_min = 7
    dte_max = 60

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
        narrowed_universe: V2NarrowedUniverse | None = None,
    ) -> list[V2Candidate]:
        """Phase B — construct all iron condor candidates.

        TODO: Implement in later prompt.
        """
        raise NotImplementedError(
            "IronCondorsV2Scanner.construct_candidates() not yet implemented."
        )

    def family_structural_checks(
        self, candidate: V2Candidate,
    ) -> list[V2CheckResult]:
        """Iron-condor-specific structural checks.

        - Exactly 4 legs.
        - 2 puts + 2 calls.
        - Strike ordering: put_long < put_short < call_short < call_long.
        """
        checks: list[V2CheckResult] = []

        if len(candidate.legs) != 4:
            checks.append(V2CheckResult(
                "ic_leg_count", False,
                f"expected 4 legs, got {len(candidate.legs)}",
            ))
            candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
            return checks
        checks.append(V2CheckResult("ic_leg_count", True, "4 legs"))

        puts = sorted(
            [l for l in candidate.legs if l.option_type == "put"],
            key=lambda l: l.strike,
        )
        calls = sorted(
            [l for l in candidate.legs if l.option_type == "call"],
            key=lambda l: l.strike,
        )

        if len(puts) != 2 or len(calls) != 2:
            checks.append(V2CheckResult(
                "ic_put_call_balance", False,
                f"expected 2P+2C, got {len(puts)}P+{len(calls)}C",
            ))
            candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
            return checks
        checks.append(V2CheckResult("ic_put_call_balance", True, "2P+2C"))

        # Strike ordering
        pl, ps = puts[0], puts[1]   # put_long (lower), put_short (higher)
        cs, cl = calls[0], calls[1]  # call_short (lower), call_long (higher)

        if ps.strike >= cs.strike:
            checks.append(V2CheckResult(
                "ic_strike_ordering", False,
                f"put_short={ps.strike} >= call_short={cs.strike}",
            ))
            candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
        else:
            checks.append(V2CheckResult("ic_strike_ordering", True, ""))

        return checks

    def family_math(
        self, candidate: V2Candidate,
    ) -> V2RecomputedMath:
        """Iron condor–specific math.

        TODO: Full implementation in later prompt.
        Returns a minimal V2RecomputedMath placeholder.
        """
        return V2RecomputedMath(notes={"status": "skeleton — not yet implemented"})
