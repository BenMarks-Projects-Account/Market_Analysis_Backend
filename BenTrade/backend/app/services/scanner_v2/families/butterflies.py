"""Options Scanner V2 — Butterflies family (skeleton).

Handles:
- butterfly_debit
- iron_butterfly

Implementation status: SKELETON — Phase B not yet implemented.

Construction logic (Phase B)
----------------------------
Debit butterfly (3 legs):
    For each eligible expiration:
        long 1× lower wing, short 2× body, long 1× upper wing
        All same option_type (put or call).
        body = (lower + upper) / 2.
        Width = body − lower = upper − body (symmetric).

Iron butterfly (4 legs):
    For each eligible expiration:
        short 1× put at body, short 1× call at body,
        long 1× put (lower wing), long 1× call (upper wing).
        Net credit received.

Family-specific structural checks:
- Correct leg count (3 for debit, 4 for iron).
- Body strike is midpoint of wings (symmetric).
- All same expiration.

Family math override:
- Debit butterfly: max_loss = net_debit × 100.
  max_profit = (width − net_debit) × 100.
- Iron butterfly: max_profit = net_credit × 100.
  max_loss = (width − net_credit) × 100.
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


class ButterfliesV2Scanner(BaseV2Scanner):
    """V2 scanner for butterfly families."""

    family_key = "butterflies"
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
        """Phase B — construct all butterfly candidates.

        TODO: Implement in later prompt.
        """
        raise NotImplementedError(
            "ButterfliesV2Scanner.construct_candidates() not yet implemented."
        )

    def family_structural_checks(
        self, candidate: V2Candidate,
    ) -> list[V2CheckResult]:
        """Butterfly-specific structural checks.

        - 3 legs (debit butterfly) or 4 legs (iron butterfly).
        - Symmetric wing structure.
        """
        checks: list[V2CheckResult] = []

        n = len(candidate.legs)
        if n not in (3, 4):
            checks.append(V2CheckResult(
                "butterfly_leg_count", False,
                f"expected 3 or 4 legs, got {n}",
            ))
            candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
            return checks
        checks.append(V2CheckResult("butterfly_leg_count", True, f"{n} legs"))

        return checks

    def family_math(
        self, candidate: V2Candidate,
    ) -> V2RecomputedMath:
        """Butterfly-specific math.

        TODO: Full implementation in later prompt.
        """
        return V2RecomputedMath(notes={"status": "skeleton — not yet implemented"})
