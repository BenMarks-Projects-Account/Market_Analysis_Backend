"""Options Scanner V2 — shared phase implementations.

Phases C through F are common across all strategy families.  Each phase
function takes a list of candidates (in progress) and returns them with
diagnostics / recomputed fields attached.

Phase A (data loading) is handled by the runner.
Phase B (candidate construction) is family-specific.

Phase summary
-------------
C — structural_validation   Reject malformed candidates.
D — quote_liquidity_sanity  Reject broken/missing quotes and missing OI/volume.
E — recomputed_math         Recompute core pricing from leg quotes.
F — normalize_and_package   Assign IDs, timestamps, set passed/downstream_usable.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from app.services.scanner_v2.contracts import (
    V2Candidate,
    V2CheckResult,
    V2Diagnostics,
    V2Leg,
    V2RecomputedMath,
)

_log = logging.getLogger("bentrade.scanner_v2.phases")


# =====================================================================
#  Phase C — Structural Validation
# =====================================================================

def phase_c_structural_validation(
    candidates: list[V2Candidate],
    *,
    family_checks: Any | None = None,
) -> list[V2Candidate]:
    """Run shared + family-specific structural checks.

    Shared checks (all families):
    - valid_leg_count: at least 1 leg exists.
    - legs_have_strikes: every leg has a finite strike.
    - legs_have_option_type: every leg has "put" or "call".
    - legs_have_sides: every leg has "long" or "short".
    - valid_width: width > 0 (for families that have width).
    - non_degenerate_pricing: credit < width (credit strategies) or
      debit < width (debit strategies).

    Family-specific checks are appended by calling
    ``family_checks(candidate) → list[V2CheckResult]`` if provided.

    Rejected candidates get reason codes added to
    ``candidate.diagnostics.reject_reasons``.
    """
    for cand in candidates:
        checks: list[V2CheckResult] = []

        # ── Leg count ───────────────────────────────────────────
        if not cand.legs:
            checks.append(V2CheckResult("valid_leg_count", False, "no legs"))
            cand.diagnostics.reject_reasons.append("v2_malformed_legs")
        else:
            checks.append(V2CheckResult("valid_leg_count", True,
                                        f"{len(cand.legs)} legs"))

        # ── Strikes present ─────────────────────────────────────
        missing_strikes = [
            leg.index for leg in cand.legs
            if not _is_finite(leg.strike)
        ]
        if missing_strikes:
            checks.append(V2CheckResult("legs_have_strikes", False,
                                        f"missing on legs {missing_strikes}"))
            if "v2_malformed_legs" not in cand.diagnostics.reject_reasons:
                cand.diagnostics.reject_reasons.append("v2_malformed_legs")
        elif cand.legs:
            checks.append(V2CheckResult("legs_have_strikes", True, ""))

        # ── Option type present ─────────────────────────────────
        bad_type = [
            leg.index for leg in cand.legs
            if leg.option_type not in ("put", "call")
        ]
        if bad_type:
            checks.append(V2CheckResult("legs_have_option_type", False,
                                        f"invalid on legs {bad_type}"))
            if "v2_malformed_legs" not in cand.diagnostics.reject_reasons:
                cand.diagnostics.reject_reasons.append("v2_malformed_legs")
        elif cand.legs:
            checks.append(V2CheckResult("legs_have_option_type", True, ""))

        # ── Side present ────────────────────────────────────────
        bad_side = [
            leg.index for leg in cand.legs
            if leg.side not in ("long", "short")
        ]
        if bad_side:
            checks.append(V2CheckResult("legs_have_sides", False,
                                        f"invalid on legs {bad_side}"))
            if "v2_malformed_legs" not in cand.diagnostics.reject_reasons:
                cand.diagnostics.reject_reasons.append("v2_malformed_legs")
        elif cand.legs:
            checks.append(V2CheckResult("legs_have_sides", True, ""))

        # ── Expiration consistency (non-calendar families) ──────
        if cand.expiration_back is None and len(cand.legs) >= 2:
            expirations = {leg.expiration for leg in cand.legs}
            if len(expirations) > 1:
                checks.append(V2CheckResult(
                    "matched_expiry", False,
                    f"found {len(expirations)} distinct expirations",
                ))
                cand.diagnostics.reject_reasons.append("v2_mismatched_expiry")
            else:
                checks.append(V2CheckResult("matched_expiry", True, ""))

        # ── Width check (if applicable) ─────────────────────────
        if cand.math.width is not None:
            if cand.math.width <= 0:
                checks.append(V2CheckResult("valid_width", False,
                                            f"width={cand.math.width}"))
                cand.diagnostics.reject_reasons.append("v2_invalid_width")
            else:
                checks.append(V2CheckResult("valid_width", True,
                                            f"width={cand.math.width}"))

        # ── Degenerate pricing ──────────────────────────────────
        if cand.math.width is not None and cand.math.width > 0:
            if cand.math.net_credit is not None:
                if cand.math.net_credit <= 0:
                    checks.append(V2CheckResult(
                        "non_positive_credit", False,
                        f"credit={cand.math.net_credit}",
                    ))
                    cand.diagnostics.reject_reasons.append("v2_non_positive_credit")
                elif cand.math.net_credit >= cand.math.width:
                    checks.append(V2CheckResult(
                        "non_degenerate_pricing", False,
                        f"credit={cand.math.net_credit} >= width={cand.math.width}",
                    ))
                    cand.diagnostics.reject_reasons.append("v2_impossible_pricing")
                else:
                    checks.append(V2CheckResult("non_degenerate_pricing", True, ""))
            elif cand.math.net_debit is not None:
                if cand.math.net_debit <= 0:
                    checks.append(V2CheckResult(
                        "non_positive_debit", False,
                        f"debit={cand.math.net_debit}",
                    ))
                    cand.diagnostics.reject_reasons.append("v2_non_positive_credit")
                elif cand.math.net_debit >= cand.math.width:
                    checks.append(V2CheckResult(
                        "non_degenerate_pricing", False,
                        f"debit={cand.math.net_debit} >= width={cand.math.width}",
                    ))
                    cand.diagnostics.reject_reasons.append("v2_impossible_pricing")
                else:
                    checks.append(V2CheckResult("non_degenerate_pricing", True, ""))

        # ── Family-specific checks ──────────────────────────────
        if family_checks is not None:
            extra = family_checks(cand)
            checks.extend(extra)

        cand.diagnostics.structural_checks = checks

    return candidates


# =====================================================================
#  Phase D — Quote & Liquidity Sanity
# =====================================================================

def phase_d_quote_liquidity_sanity(
    candidates: list[V2Candidate],
) -> list[V2Candidate]:
    """Reject candidates with broken quotes or missing liquidity data.

    Quote checks (per leg):
    - bid and ask present (not None)
    - ask >= bid (not inverted)
    - mid > 0

    Liquidity checks (per leg):
    - open_interest is not None
    - volume is not None

    Already-rejected candidates (from Phase C) are skipped — they
    already have reject reasons.
    """
    for cand in candidates:
        if cand.diagnostics.reject_reasons:
            # Already rejected in Phase C — skip to avoid stacking
            continue

        q_checks: list[V2CheckResult] = []
        l_checks: list[V2CheckResult] = []

        for leg in cand.legs:
            prefix = f"leg[{leg.index}] {leg.side} {leg.option_type} {leg.strike}"

            # ── Quote presence ──────────────────────────────────
            if leg.bid is None or leg.ask is None:
                q_checks.append(V2CheckResult(
                    "quote_present", False, f"{prefix}: bid={leg.bid} ask={leg.ask}",
                ))
                if "v2_missing_quote" not in cand.diagnostics.reject_reasons:
                    cand.diagnostics.reject_reasons.append("v2_missing_quote")
                continue  # Skip further quote checks on this leg

            # ── Inverted ────────────────────────────────────────
            if leg.ask < leg.bid:
                q_checks.append(V2CheckResult(
                    "not_inverted", False,
                    f"{prefix}: ask={leg.ask} < bid={leg.bid}",
                ))
                if "v2_inverted_quote" not in cand.diagnostics.reject_reasons:
                    cand.diagnostics.reject_reasons.append("v2_inverted_quote")
            else:
                q_checks.append(V2CheckResult("not_inverted", True, prefix))

            # ── Zero mid ────────────────────────────────────────
            mid = (leg.bid + leg.ask) / 2.0
            if mid <= 0:
                q_checks.append(V2CheckResult(
                    "positive_mid", False, f"{prefix}: mid={mid}",
                ))
                if "v2_zero_mid" not in cand.diagnostics.reject_reasons:
                    cand.diagnostics.reject_reasons.append("v2_zero_mid")
            else:
                q_checks.append(V2CheckResult("positive_mid", True, prefix))

            # ── Liquidity: OI ───────────────────────────────────
            if leg.open_interest is None:
                l_checks.append(V2CheckResult(
                    "oi_present", False, f"{prefix}: OI=None",
                ))
                if "v2_missing_oi" not in cand.diagnostics.reject_reasons:
                    cand.diagnostics.reject_reasons.append("v2_missing_oi")
            else:
                l_checks.append(V2CheckResult("oi_present", True, prefix))

            # ── Liquidity: volume ───────────────────────────────
            if leg.volume is None:
                l_checks.append(V2CheckResult(
                    "volume_present", False, f"{prefix}: volume=None",
                ))
                if "v2_missing_volume" not in cand.diagnostics.reject_reasons:
                    cand.diagnostics.reject_reasons.append("v2_missing_volume")
            else:
                l_checks.append(V2CheckResult("volume_present", True, prefix))

        cand.diagnostics.quote_checks = q_checks
        cand.diagnostics.liquidity_checks = l_checks

    return candidates


# =====================================================================
#  Phase E — Recomputed Math
# =====================================================================

def phase_e_recomputed_math(
    candidates: list[V2Candidate],
    *,
    family_math: Any | None = None,
) -> list[V2Candidate]:
    """Recompute core pricing from leg quotes.

    Default implementation handles 2-leg vertical spreads (credit and
    debit).  Families with different math (iron condors, butterflies,
    calendars) provide ``family_math(candidate) → V2RecomputedMath``
    to override.

    This phase does NOT reject candidates for unfavorable POP/EV/RoR.
    It only rejects for structurally impossible math results
    (max_loss ≤ 0, max_profit ≤ 0 when they must be positive).
    """
    for cand in candidates:
        if cand.diagnostics.reject_reasons:
            # Already rejected — skip
            continue

        math_checks: list[V2CheckResult] = []

        if family_math is not None:
            cand.math = family_math(cand)
        else:
            _recompute_vertical_math(cand)

        # ── Validate recomputed results ─────────────────────────
        m = cand.math

        if m.max_loss is not None:
            if not _is_finite(m.max_loss) or m.max_loss <= 0:
                math_checks.append(V2CheckResult(
                    "valid_max_loss", False, f"max_loss={m.max_loss}",
                ))
                cand.diagnostics.reject_reasons.append("v2_impossible_max_loss")
            else:
                math_checks.append(V2CheckResult(
                    "valid_max_loss", True, f"max_loss={m.max_loss:.2f}",
                ))

        if m.max_profit is not None:
            if not _is_finite(m.max_profit) or m.max_profit <= 0:
                math_checks.append(V2CheckResult(
                    "valid_max_profit", False, f"max_profit={m.max_profit}",
                ))
                cand.diagnostics.reject_reasons.append("v2_impossible_max_profit")
            else:
                math_checks.append(V2CheckResult(
                    "valid_max_profit", True, f"max_profit={m.max_profit:.2f}",
                ))

        # ── POP / EV / RoR: compute if possible, warn if not ───
        if m.pop is not None:
            math_checks.append(V2CheckResult(
                "pop_computed", True, f"pop={m.pop:.4f} source={m.pop_source}",
            ))
        else:
            math_checks.append(V2CheckResult(
                "pop_computed", False, "could not compute POP",
            ))
            cand.diagnostics.warnings.append(
                "POP could not be computed — downstream stages may need to estimate it",
            )

        if m.ev is not None:
            math_checks.append(V2CheckResult(
                "ev_computed", True, f"ev={m.ev:.2f}",
            ))
        else:
            math_checks.append(V2CheckResult(
                "ev_computed", False, "could not compute EV (missing POP or max_loss)",
            ))

        if m.ror is not None:
            math_checks.append(V2CheckResult(
                "ror_computed", True, f"ror={m.ror:.4f}",
            ))

        cand.diagnostics.math_checks = math_checks

    return candidates


def _recompute_vertical_math(cand: V2Candidate) -> None:
    """Default math for 2-leg vertical spreads.

    Credit spread:  net_credit = short.bid − long.ask
    Debit spread:   net_debit  = long.ask  − short.bid

    Input:  net_credit or net_debit already set from Phase B construction.
    """
    m = cand.math
    notes: dict[str, str] = {}

    if len(cand.legs) != 2:
        notes["skipped"] = f"expected 2 legs, got {len(cand.legs)}"
        m.notes = notes
        return

    short_legs = [l for l in cand.legs if l.side == "short"]
    long_legs = [l for l in cand.legs if l.side == "long"]
    if not short_legs or not long_legs:
        notes["skipped"] = "missing short or long leg"
        m.notes = notes
        return

    short = short_legs[0]
    long = long_legs[0]

    # Width
    m.width = abs(short.strike - long.strike)
    notes["width"] = f"|{short.strike} - {long.strike}| = {m.width}"

    if m.width <= 0:
        m.notes = notes
        return

    # Net credit / debit
    if short.bid is not None and long.ask is not None:
        credit = short.bid - long.ask
        if credit > 0:
            m.net_credit = round(credit, 4)
            m.max_profit = round(credit * 100, 2)
            m.max_loss = round((m.width - credit) * 100, 2)
            notes["net_credit"] = f"short.bid({short.bid}) - long.ask({long.ask}) = {credit}"
        else:
            debit = long.ask - short.bid
            m.net_debit = round(debit, 4)
            m.max_profit = round((m.width - debit) * 100, 2)
            m.max_loss = round(debit * 100, 2)
            notes["net_debit"] = f"long.ask({long.ask}) - short.bid({short.bid}) = {debit}"
    else:
        notes["pricing"] = "missing bid/ask on short or long leg"
        m.notes = notes
        return

    # POP — delta approximation
    if short.delta is not None:
        m.pop = round(1.0 - abs(short.delta), 4)
        m.pop_source = "delta_approx"
        notes["pop"] = f"1 - |short.delta({short.delta})| = {m.pop}"

    # EV
    if m.pop is not None and m.max_profit is not None and m.max_loss is not None:
        m.ev = round(m.pop * m.max_profit - (1.0 - m.pop) * m.max_loss, 2)
        notes["ev"] = f"pop*max_profit - (1-pop)*max_loss = {m.ev}"

        if cand.dte and cand.dte > 0:
            m.ev_per_day = round(m.ev / cand.dte, 4)

    # RoR
    if m.max_profit is not None and m.max_loss is not None and m.max_loss > 0:
        m.ror = round(m.max_profit / m.max_loss, 4)
        notes["ror"] = f"max_profit / max_loss = {m.ror}"

    # Kelly
    if m.pop is not None and m.ror is not None:
        # Kelly = p - q/b  where p=pop, q=1-pop, b=ror
        q = 1.0 - m.pop
        if m.ror > 0:
            kelly = m.pop - q / m.ror
            m.kelly = round(kelly, 4)

    # Breakeven
    if m.net_credit is not None:
        if short.option_type == "put":
            be = short.strike - m.net_credit
        else:
            be = short.strike + m.net_credit
        m.breakeven = [round(be, 2)]
        notes["breakeven"] = f"{be:.2f}"
    elif m.net_debit is not None:
        if long.option_type == "call":
            be = long.strike + m.net_debit
        else:
            be = long.strike - m.net_debit
        m.breakeven = [round(be, 2)]
        notes["breakeven"] = f"{be:.2f}"

    m.notes = notes


# =====================================================================
#  Phase F — Normalization & Packaging
# =====================================================================

def phase_f_normalize(
    candidates: list[V2Candidate],
    *,
    scanner_version: str = "",
) -> list[V2Candidate]:
    """Final packaging: set status flags, timestamps, pass/reject reasons.

    - Candidates with no reject_reasons → passed=True, downstream_usable=True
    - Candidates with reject_reasons → passed=False, downstream_usable=False
    - All candidates get generated_at timestamp
    - All candidates get scanner_version
    """
    now = datetime.now(timezone.utc).isoformat()

    for cand in candidates:
        cand.generated_at = now
        cand.scanner_version = scanner_version

        if not cand.diagnostics.reject_reasons:
            cand.passed = True
            cand.downstream_usable = True
            cand.diagnostics.pass_reasons = _collect_pass_reasons(cand)
        else:
            cand.passed = False
            cand.downstream_usable = False

    return candidates


def _collect_pass_reasons(cand: V2Candidate) -> list[str]:
    """Build human-readable pass reasons from check results."""
    reasons: list[str] = []

    s_pass = sum(1 for c in cand.diagnostics.structural_checks if c.passed)
    s_total = len(cand.diagnostics.structural_checks)
    if s_total > 0:
        reasons.append(f"structural: {s_pass}/{s_total} passed")

    q_pass = sum(1 for c in cand.diagnostics.quote_checks if c.passed)
    q_total = len(cand.diagnostics.quote_checks)
    if q_total > 0:
        reasons.append(f"quotes: {q_pass}/{q_total} passed")

    l_pass = sum(1 for c in cand.diagnostics.liquidity_checks if c.passed)
    l_total = len(cand.diagnostics.liquidity_checks)
    if l_total > 0:
        reasons.append(f"liquidity: {l_pass}/{l_total} passed")

    m_pass = sum(1 for c in cand.diagnostics.math_checks if c.passed)
    m_total = len(cand.diagnostics.math_checks)
    if m_total > 0:
        reasons.append(f"math: {m_pass}/{m_total} passed")

    return reasons


# =====================================================================
#  Helpers
# =====================================================================

def _is_finite(value: Any) -> bool:
    """True if value is a finite number."""
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
