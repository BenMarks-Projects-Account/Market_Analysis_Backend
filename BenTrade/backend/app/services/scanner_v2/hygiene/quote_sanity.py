"""V2 Scanner — shared quote sanity checks.

Goes beyond Phase D's quote-presence checks to detect structurally
broken quote conditions that make a candidate untrustworthy even
though individual leg quotes exist.

Philosophy
----------
- REJECT obvious quote garbage that cannot produce reliable pricing.
- WARN on marginal quote conditions that downstream may still accept.
- Do NOT rank or score — leave desirability to downstream.

Checks performed
----------------
Candidate-level (across legs):
- Negative bid: any leg with bid < 0.
- Negative ask: any leg with ask < 0.
- Spread pricing impossible: for credit spreads, computed credit ≤ 0
  when a positive credit is structurally required (and vice versa for
  debit spreads).  This catches cases where legs exist and have quotes
  but the combination is economically nonsensical.
- Bid-ask width ratio: warn when bid/ask spread on any leg is
  excessively wide relative to the mid price.

These run on candidates that PASSED Phase D (quote presence already
confirmed).  We do NOT re-check presence here.
"""

from __future__ import annotations

from typing import Any

from app.services.scanner_v2.contracts import V2Candidate, V2CheckResult
from app.services.scanner_v2.diagnostics.builder import DiagnosticsBuilder
from app.services.scanner_v2.diagnostics.reason_codes import (
    REJECT_NEGATIVE_BID,
    REJECT_NEGATIVE_ASK,
    REJECT_SPREAD_PRICING_IMPOSSIBLE,
    WARN_WIDE_LEG_SPREAD,
)

# Leg-level bid/ask spread threshold for warning.
# If (ask - bid) / mid > this ratio, warn.  Conservative default: 100%.
# This catches truly absurd spreads (ask = 10x bid) without
# over-filtering normal option spreads.
_WIDE_LEG_SPREAD_WARN_RATIO = 1.0


def run_quote_sanity(
    candidates: list[V2Candidate],
    *,
    wide_leg_spread_ratio: float = _WIDE_LEG_SPREAD_WARN_RATIO,
) -> list[V2Candidate]:
    """Run candidate-level quote sanity checks.

    Skips candidates already rejected by prior phases.

    Parameters
    ----------
    candidates
        List of V2Candidate objects (mutated in place).
    wide_leg_spread_ratio
        Warn if any leg's (ask-bid)/mid exceeds this ratio.

    Returns
    -------
    The same list, with diagnostics updated on each candidate.
    """
    for cand in candidates:
        if cand.diagnostics.reject_reasons:
            continue

        builder = DiagnosticsBuilder(source_phase="D2")
        checks: list[V2CheckResult] = []

        # ── Per-leg checks ──────────────────────────────────────
        for leg in cand.legs:
            prefix = f"leg[{leg.index}] {leg.side} {leg.option_type} {leg.strike}"

            # Negative bid
            if leg.bid is not None and leg.bid < 0:
                checks.append(V2CheckResult(
                    "non_negative_bid", False,
                    f"{prefix}: bid={leg.bid}",
                ))
                builder.add_reject(
                    REJECT_NEGATIVE_BID,
                    source_check="non_negative_bid",
                    message=f"{prefix}: bid={leg.bid}",
                    leg_index=leg.index,
                )
            else:
                checks.append(V2CheckResult("non_negative_bid", True, prefix))

            # Negative ask
            if leg.ask is not None and leg.ask < 0:
                checks.append(V2CheckResult(
                    "non_negative_ask", False,
                    f"{prefix}: ask={leg.ask}",
                ))
                builder.add_reject(
                    REJECT_NEGATIVE_ASK,
                    source_check="non_negative_ask",
                    message=f"{prefix}: ask={leg.ask}",
                    leg_index=leg.index,
                )
            else:
                checks.append(V2CheckResult("non_negative_ask", True, prefix))

            # Wide leg spread warning
            if (
                leg.bid is not None
                and leg.ask is not None
                and leg.bid >= 0
                and leg.ask > leg.bid
            ):
                mid = (leg.bid + leg.ask) / 2.0
                if mid > 0:
                    spread_ratio = (leg.ask - leg.bid) / mid
                    if spread_ratio > wide_leg_spread_ratio:
                        checks.append(V2CheckResult(
                            "leg_spread_reasonable", False,
                            f"{prefix}: spread_ratio={spread_ratio:.2f}",
                        ))
                        builder.add_warning(
                            WARN_WIDE_LEG_SPREAD,
                            source_check="leg_spread_reasonable",
                            message=(
                                f"{prefix}: (ask-bid)/mid = "
                                f"{spread_ratio:.2f} > {wide_leg_spread_ratio}"
                            ),
                            leg_index=leg.index,
                            spread_ratio=round(spread_ratio, 4),
                            threshold=wide_leg_spread_ratio,
                        )
                    else:
                        checks.append(V2CheckResult(
                            "leg_spread_reasonable", True, prefix,
                        ))

        # ── Candidate-level: spread pricing sanity ───────────────
        _check_spread_pricing(cand, builder, checks)

        # Store check results under "quote_sanity" subsection
        # (Phase D already owns "quote"; we use a distinct key)
        builder.set_check_results("quote_sanity", checks)
        builder.apply(cand.diagnostics)

    return candidates


def _check_spread_pricing(
    cand: V2Candidate,
    builder: DiagnosticsBuilder,
    checks: list[V2CheckResult],
) -> None:
    """Verify that spread-level pricing is consistent with leg quotes.

    For a credit spread: short.bid - long.ask should be > 0.
    For a debit spread:  long.ask - short.bid should be > 0.

    If the spread pricing is impossible given the leg quotes, reject.
    This catches scenarios like: both legs have valid quotes, but the
    combination produces a nonsensical result.
    """
    if len(cand.legs) != 2:
        # Multi-leg families may need custom pricing checks.
        # For now, only validate 2-leg spreads.
        return

    short_legs = [l for l in cand.legs if l.side == "short"]
    long_legs = [l for l in cand.legs if l.side == "long"]

    if not short_legs or not long_legs:
        return

    short = short_legs[0]
    long = long_legs[0]

    # Need both bid/ask values to check spread pricing
    if any(v is None for v in (short.bid, short.ask, long.bid, long.ask)):
        return

    # Determine if credit or debit based on what Phase B set
    is_credit = cand.math.net_credit is not None and cand.math.net_credit > 0
    is_debit = cand.math.net_debit is not None and cand.math.net_debit > 0

    if is_credit:
        # Credit spread: short.bid - long.ask should be > 0
        actual_credit = short.bid - long.ask
        if actual_credit <= 0:
            checks.append(V2CheckResult(
                "spread_pricing_viable", False,
                f"credit spread but short.bid({short.bid}) - "
                f"long.ask({long.ask}) = {actual_credit:.4f} ≤ 0",
            ))
            builder.add_reject(
                REJECT_SPREAD_PRICING_IMPOSSIBLE,
                source_check="spread_pricing_viable",
                message=(
                    f"Credit spread impossible: short.bid({short.bid}) - "
                    f"long.ask({long.ask}) = {actual_credit:.4f}"
                ),
                expected_credit=cand.math.net_credit,
                actual_credit=round(actual_credit, 4),
            )
        else:
            checks.append(V2CheckResult(
                "spread_pricing_viable", True,
                f"credit={actual_credit:.4f}",
            ))
    elif is_debit:
        # Debit spread: long.ask - short.bid should be > 0
        actual_debit = long.ask - short.bid
        if actual_debit <= 0:
            checks.append(V2CheckResult(
                "spread_pricing_viable", False,
                f"debit spread but long.ask({long.ask}) - "
                f"short.bid({short.bid}) = {actual_debit:.4f} ≤ 0",
            ))
            builder.add_reject(
                REJECT_SPREAD_PRICING_IMPOSSIBLE,
                source_check="spread_pricing_viable",
                message=(
                    f"Debit spread impossible: long.ask({long.ask}) - "
                    f"short.bid({short.bid}) = {actual_debit:.4f}"
                ),
                expected_debit=cand.math.net_debit,
                actual_debit=round(actual_debit, 4),
            )
        else:
            checks.append(V2CheckResult(
                "spread_pricing_viable", True,
                f"debit={actual_debit:.4f}",
            ))
