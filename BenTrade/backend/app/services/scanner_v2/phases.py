"""Options Scanner V2 — shared phase implementations.

Phases C through F are common across all strategy families.  Each phase
function takes a list of candidates (in progress) and returns them with
diagnostics / recomputed fields attached.

Phase A (data loading) is handled by the runner.
Phase B (candidate construction) is family-specific.

Phase summary
-------------
C  — structural_validation   Reject malformed candidates.
D  — quote_liquidity_sanity  Reject broken/missing quotes and missing OI/volume.
D2 — trust_hygiene           Quote sanity, liquidity sanity, duplicate suppression.
E  — recomputed_math         Recompute core pricing from leg quotes.
F  — normalize_and_package   Assign IDs, timestamps, set passed/downstream_usable.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any


def _normal_cdf(x: float) -> float:
    """Standard normal CDF using math.erf (no scipy needed)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pop_breakeven_lognormal(
    underlying_price: float,
    breakeven: float,
    iv: float,
    dte: int,
) -> float | None:
    """P(S_T < breakeven) under Black-Scholes lognormal model.

    For put debit spreads this IS the POP (profit when stock falls below breakeven).
    For call debit spreads the caller should use 1 - result.

    Formula: d2 = [ln(S/K) + (-0.5 * sigma^2 * T)] / (sigma * sqrt(T))
             P(S_T < K) = N(-d2)
    """
    if underlying_price <= 0 or breakeven <= 0 or iv <= 0 or dte <= 0:
        return None
    t = dte / 365.0
    sigma_sqrt_t = iv * math.sqrt(t)
    if sigma_sqrt_t <= 0:
        return 1.0 if underlying_price < breakeven else 0.0
    d2 = (math.log(underlying_price / breakeven) + (-0.5 * iv * iv * t)) / sigma_sqrt_t
    return round(_normal_cdf(-d2), 4)

from app.services.scanner_v2.contracts import (
    V2Candidate,
    V2CheckResult,
    V2Diagnostics,
    V2Leg,
    V2RecomputedMath,
)
from app.services.scanner_v2.diagnostics.builder import (
    DiagnosticsBuilder,
    collect_pass_reasons,
)
from app.services.scanner_v2.diagnostics.reason_codes import (
    REJECT_CREDIT_SPREAD_NO_CREDIT,
    REJECT_INVERTED_QUOTE,
    REJECT_MISSING_OI,
    REJECT_MISSING_QUOTE,
    REJECT_MISSING_SHORT_DELTA,
    REJECT_MISSING_VOLUME,
    REJECT_WIDE_SPREAD_SHORT_LEG,
    REJECT_ZERO_BID_SHORT_LEG,
    REJECT_ZERO_MID,
)
from app.services.scanner_v2.validation.math_checks import run_math_verification
from app.services.scanner_v2.validation.structural import (
    run_shared_structural_checks,
)

_log = logging.getLogger("bentrade.scanner_v2.phases")


# =====================================================================
#  Phase C — Structural Validation
# =====================================================================

def phase_c_structural_validation(
    candidates: list[V2Candidate],
    *,
    family_checks: Any | None = None,
    expected_leg_count: int | tuple[int, ...] | None = None,
    require_same_expiry: bool = True,
) -> list[V2Candidate]:
    """Run shared + family-specific structural checks.

    Delegates to the validation.structural module for composable checks.
    Family-specific checks can be provided via ``family_checks``
    callback (returns ``list[V2CheckResult]``) or by passing a
    ``V2ValidationSummary``-returning callable.

    Rejected candidates get reason codes added to
    ``candidate.diagnostics.reject_reasons``.
    """
    for cand in candidates:
        builder = DiagnosticsBuilder(source_phase="C")

        # Run shared structural checks via validation framework
        summary = run_shared_structural_checks(
            cand,
            expected_leg_count=expected_leg_count,
            require_same_expiry=require_same_expiry,
        )

        # Import fail codes and check results via builder
        builder.merge_validation_summary(summary, check_section="structural")

        # Family-specific checks (legacy callback interface)
        if family_checks is not None:
            extra = family_checks(cand)
            if "structural" in builder._check_results:
                builder._check_results["structural"].extend(extra)
            else:
                builder.set_check_results("structural", extra)

        builder.apply(cand.diagnostics)

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

        builder = DiagnosticsBuilder(source_phase="D")
        q_checks: list[V2CheckResult] = []
        l_checks: list[V2CheckResult] = []

        for leg in cand.legs:
            prefix = f"leg[{leg.index}] {leg.side} {leg.option_type} {leg.strike}"

            # ── Quote presence ──────────────────────────────────
            if leg.bid is None or leg.ask is None:
                q_checks.append(V2CheckResult(
                    "quote_present", False, f"{prefix}: bid={leg.bid} ask={leg.ask}",
                ))
                builder.add_reject(
                    REJECT_MISSING_QUOTE,
                    source_check="quote_present",
                    message=f"{prefix}: bid={leg.bid} ask={leg.ask}",
                    leg_index=leg.index,
                )
                continue  # Skip further quote checks on this leg

            # ── Inverted ────────────────────────────────────────
            if leg.ask < leg.bid:
                q_checks.append(V2CheckResult(
                    "not_inverted", False,
                    f"{prefix}: ask={leg.ask} < bid={leg.bid}",
                ))
                builder.add_reject(
                    REJECT_INVERTED_QUOTE,
                    source_check="not_inverted",
                    message=f"{prefix}: ask={leg.ask} < bid={leg.bid}",
                    leg_index=leg.index,
                )
            else:
                q_checks.append(V2CheckResult("not_inverted", True, prefix))

            # ── Zero mid ────────────────────────────────────────
            mid = (leg.bid + leg.ask) / 2.0
            if mid <= 0:
                q_checks.append(V2CheckResult(
                    "positive_mid", False, f"{prefix}: mid={mid}",
                ))
                builder.add_reject(
                    REJECT_ZERO_MID,
                    source_check="positive_mid",
                    message=f"{prefix}: mid={mid}",
                    leg_index=leg.index,
                )
            else:
                q_checks.append(V2CheckResult("positive_mid", True, prefix))

            # ── Liquidity: OI ───────────────────────────────────
            if leg.open_interest is None:
                l_checks.append(V2CheckResult(
                    "oi_present", False, f"{prefix}: OI=None",
                ))
                builder.add_reject(
                    REJECT_MISSING_OI,
                    source_check="oi_present",
                    message=f"{prefix}: OI=None",
                    leg_index=leg.index,
                )
            else:
                l_checks.append(V2CheckResult("oi_present", True, prefix))

            # ── Liquidity: volume ───────────────────────────────
            if leg.volume is None:
                l_checks.append(V2CheckResult(
                    "volume_present", False, f"{prefix}: volume=None",
                ))
                builder.add_reject(
                    REJECT_MISSING_VOLUME,
                    source_check="volume_present",
                    message=f"{prefix}: volume=None",
                    leg_index=leg.index,
                )
            else:
                l_checks.append(V2CheckResult("volume_present", True, prefix))

            # ── Delta: short legs (except calendars) ────────────
            # Short legs need delta for POP computation. Calendars
            # set POP=None by design and are exempt.
            if (
                leg.side == "short"
                and leg.delta is None
                and cand.family_key != "calendars"
            ):
                q_checks.append(V2CheckResult(
                    "short_delta_present", False,
                    f"{prefix}: delta=None",
                ))
                builder.add_reject(
                    REJECT_MISSING_SHORT_DELTA,
                    source_check="short_delta_present",
                    message=f"{prefix}: delta=None",
                    leg_index=leg.index,
                )
            elif leg.side == "short" and cand.family_key != "calendars":
                q_checks.append(V2CheckResult(
                    "short_delta_present", True, prefix,
                ))

        # ── Zero-bid short leg (credit strategies only) ─────
        _CREDIT_SCANNER_KEYS = frozenset({
            "put_credit_spread", "call_credit_spread",
            "iron_condor", "iron_butterfly",
        })
        if cand.scanner_key in _CREDIT_SCANNER_KEYS:
            for leg in cand.legs:
                if (
                    leg.side == "short"
                    and leg.bid is not None
                    and leg.bid <= 0
                ):
                    prefix = f"leg[{leg.index}] {leg.side} {leg.option_type} {leg.strike}"
                    q_checks.append(V2CheckResult(
                        "short_leg_has_bid", False,
                        f"{prefix}: bid={leg.bid}",
                    ))
                    builder.add_reject(
                        REJECT_ZERO_BID_SHORT_LEG,
                        source_check="short_leg_has_bid",
                        message=f"{prefix}: bid={leg.bid} (no premium)",
                        leg_index=leg.index,
                    )
                    break  # One zero-bid short is enough to reject

        # ── Wide bid-ask spread on short leg (credit strategies only) ─
        # A short leg with >20% spread often produces a negative actual
        # credit even when mid-price construction looks viable.
        if cand.scanner_key in _CREDIT_SCANNER_KEYS and not builder._reject_codes:
            for leg in cand.legs:
                if leg.side == "short" and leg.bid is not None and leg.ask is not None and leg.bid > 0:
                    spread_pct = (leg.ask - leg.bid) / leg.bid
                    if spread_pct > 0.20:
                        prefix = f"leg[{leg.index}] {leg.side} {leg.option_type} {leg.strike}"
                        q_checks.append(V2CheckResult(
                            "short_leg_spread_ok", False,
                            f"{prefix}: spread_pct={spread_pct:.2%}",
                        ))
                        builder.add_reject(
                            REJECT_WIDE_SPREAD_SHORT_LEG,
                            source_check="short_leg_spread_ok",
                            message=f"{prefix}: bid={leg.bid} ask={leg.ask} spread={spread_pct:.2%}",
                            leg_index=leg.index,
                        )
                        break  # One wide-spread short is enough to reject

        builder.set_check_results("quote", q_checks)
        builder.set_check_results("liquidity", l_checks)
        builder.apply(cand.diagnostics)

    return candidates


# =====================================================================
#  Phase D2 — Trust Hygiene (Quote Sanity + Liquidity Sanity + Dedup)
# =====================================================================

def phase_d2_trust_hygiene(
    candidates: list[V2Candidate],
    *,
    dedup_key_fn: Any | None = None,
) -> tuple[list[V2Candidate], dict[str, Any]]:
    """Run quote sanity, liquidity sanity, and duplicate suppression.

    This phase sits between Phase D (quote/liquidity presence) and
    Phase E (recomputed math).  It catches candidates that have valid
    but broken/unusable quotes or liquidity, and suppresses duplicates.

    Parameters
    ----------
    candidates
        Full list including any already-rejected candidates.
    dedup_key_fn
        Optional custom dedup key function for family-specific
        duplicate detection.

    Returns
    -------
    (candidates, hygiene_summary)
        candidates with diagnostics updated; hygiene_summary dict
        with quote_sanity, liquidity_sanity, and dedup stats.
    """
    from app.services.scanner_v2.hygiene.quote_sanity import run_quote_sanity
    from app.services.scanner_v2.hygiene.liquidity_sanity import run_liquidity_sanity
    from app.services.scanner_v2.hygiene.dedup import run_dedup

    # Step 1: Quote sanity
    candidates = run_quote_sanity(candidates)

    # Step 2: Liquidity sanity
    candidates = run_liquidity_sanity(candidates)

    # Step 3: Duplicate suppression
    candidates, dedup_result = run_dedup(candidates, key_fn=dedup_key_fn)

    hygiene_summary = {
        "dedup": dedup_result.to_dict(),
    }

    return candidates, hygiene_summary


# =====================================================================
#  Phase E — Recomputed Math
# =====================================================================

def phase_e_recomputed_math(
    candidates: list[V2Candidate],
    *,
    family_math: Any | None = None,
    family_key: str | None = None,
) -> list[V2Candidate]:
    """Recompute core pricing from leg quotes, then verify the results.

    Default implementation handles 2-leg vertical spreads (credit and
    debit).  Families with different math (iron condors, butterflies,
    calendars) provide ``family_math(candidate) → V2RecomputedMath``
    to override.

    After recomputation, delegates to ``math_checks.run_math_verification()``
    for independent verification of all derived values.

    This phase does NOT reject candidates for unfavorable POP/EV/RoR.
    It only rejects for structurally impossible math results
    (max_loss ≤ 0, max_profit ≤ 0, mismatches beyond tolerance).
    """
    for cand in candidates:
        if cand.diagnostics.reject_reasons:
            # Already rejected — skip
            continue

        # Step 1: Recompute math
        if family_math is not None:
            cand.math = family_math(cand)
        else:
            _recompute_vertical_math(cand)

        # Step 2: Verify recomputed results via validation framework
        summary = run_math_verification(cand, family_key=family_key)

        # Step 3: Use builder to import results into diagnostics
        builder = DiagnosticsBuilder(source_phase="E")
        builder.merge_validation_summary(summary, check_section="math")

        # Deep ITM long-leg rejection for debit spreads
        if not cand.diagnostics.reject_reasons and not builder._reject_codes:
            m = cand.math
            is_debit = m.net_debit is not None and m.net_debit > 0
            if is_debit and len(cand.legs) >= 2:
                long_legs = [l for l in cand.legs if l.side == "long"]
                if long_legs and long_legs[0].delta is not None:
                    if abs(long_legs[0].delta) > 0.85:
                        builder.add_reject("v2_deep_itm_long_leg")

        # Credit strategy must actually produce a credit after recomputation.
        # Phase C checks mid-price credit; this catches cases where bid/ask
        # spread makes the actual credit (short.bid - long.ask) non-positive.
        _CREDIT_SCANNER_KEYS_E = frozenset({
            "put_credit_spread", "call_credit_spread",
            "iron_condor", "iron_butterfly",
        })
        if not cand.diagnostics.reject_reasons and not builder._reject_codes:
            m = cand.math
            if cand.scanner_key in _CREDIT_SCANNER_KEYS_E:
                if m.net_credit is None or m.net_credit <= 0:
                    builder.add_reject(
                        REJECT_CREDIT_SPREAD_NO_CREDIT,
                        source_check="credit_positive_after_recompute",
                        message=(
                            f"{cand.symbol} {cand.scanner_key}: "
                            f"net_credit={m.net_credit} net_debit={m.net_debit}"
                        ),
                    )
                    _log.debug(
                        "event=credit_no_credit scanner_key=%s symbol=%s "
                        "net_credit=%s net_debit=%s",
                        cand.scanner_key, cand.symbol,
                        m.net_credit, m.net_debit,
                    )

        # Expected RoR: probability-weighted return = EV / |max_loss|
        # More realistic than raw RoR (max_profit / max_loss) for
        # strategies with low POP (butterflies, debit spreads).
        # Derived field: expected_ror = ev / abs(max_loss)
        m = cand.math
        if m.ev is not None and m.max_loss is not None and m.max_loss != 0:
            m.expected_ror = round(m.ev / abs(m.max_loss), 4)

        builder.apply(cand.diagnostics)

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

    # POP — delta approximation for credits, breakeven lognormal for debits
    # Credit spread: profits when short leg expires OTM → POP = 1 - |delta_short|
    # Debit spread: profits when stock moves past breakeven → lognormal model
    is_credit = m.net_credit is not None and m.net_credit > 0
    if is_credit and short.delta is not None:
        m.pop = round(1.0 - abs(short.delta), 4)
        m.pop_source = "delta_approx"
        notes["pop"] = f"credit: 1 - |short.delta({short.delta})| = {m.pop}"
    elif not is_credit and long.delta is not None:
        # Debit spread: use breakeven lognormal POP when possible
        pop_set = False
        if m.net_debit is not None and cand.underlying_price and cand.dte and cand.dte > 0:
            iv = long.iv or short.iv or None
            if iv and iv > 0:
                if long.option_type == "call":
                    be = long.strike + m.net_debit
                else:
                    be = long.strike - m.net_debit
                pop_be = _pop_breakeven_lognormal(cand.underlying_price, be, iv, cand.dte)
                if pop_be is not None:
                    # For call debit: profit when S_T > breakeven → 1 - P(S_T < breakeven)
                    if long.option_type == "call":
                        pop_be = round(1.0 - pop_be, 4)
                    m.pop = pop_be
                    m.pop_source = "breakeven_lognormal"
                    notes["pop"] = f"debit breakeven_lognormal: be={be:.2f}, iv={iv:.4f}, dte={cand.dte} → {m.pop}"
                    pop_set = True
        if not pop_set:
            m.pop = round(abs(long.delta), 4)
            m.pop_source = "delta_approx_fallback" if (m.net_debit is not None) else "delta_approx"
            notes["pop"] = f"debit: |long.delta({long.delta})| = {m.pop}"

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
            cand.diagnostics.pass_reasons = collect_pass_reasons(cand.diagnostics)
        else:
            cand.passed = False
            cand.downstream_usable = False

    return candidates


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
