"""
Decision Policy Framework v1.1
================================

Reusable guardrail layer that evaluates whether a proposed candidate
trade should be **allowed**, **cautioned**, **restricted**, or **blocked**
based on structured context already available in BenTrade.

This module makes **deterministic, auditable** checks — no LLM calls,
no opaque weighting, no final trade decisions.  It answers:

    "Given this candidate, market composite, conflict state, and portfolio
     exposure, what guardrails should be applied?"

Inputs (all optional and partial-data-safe)
-------------------------------------------
- ``candidate``     – Normalized candidate dict from scanner_candidate_contract
- ``market``        – Market composite dict from build_market_composite()
- ``conflicts``     – Conflict report dict from detect_conflicts()
- ``portfolio``     – Portfolio exposure dict from build_portfolio_exposure()
- ``assembled``     – Assembled context dict from assemble_context() (optional)

Output
------
``evaluate_policy(...)`` returns a structured policy result with::

    {
        "policy_version":     "1.0",
        "evaluated_at":       ISO-8601,
        "status":             "evaluated" | "insufficient_data",
        "policy_decision":    "allow" | "caution" | "restrict" | "block" | "insufficient_data",
        "decision_severity":  "none" | "low" | "moderate" | "high" | "critical",
        "summary":            str,
        "triggered_checks":   list[PolicyCheck],
        "blocking_checks":    list[PolicyCheck],
        "caution_checks":     list[PolicyCheck],
        "restrictive_checks": list[PolicyCheck],
        "size_guidance":      "normal" | "reduced" | "minimal" | "none",
        "eligibility_flags":  list[str],
        "warning_flags":      list[str],
        "evidence":           dict,
        "metadata":           dict,
    }

Each ``PolicyCheck`` has::

    {
        "check_code":         str,  # stable machine identifier
        "severity":           str,  # "low" | "moderate" | "high" | "critical"
        "category":           str,  # guardrail category
        "title":              str,  # short human label
        "description":        str,  # explanation of why it fired
        "entities":           list[str],
        "evidence":           dict,
        "recommended_effect": str,  # "caution" | "restrict" | "block"
        "confidence_impact":  str,  # "none" | "minor" | "moderate" | "major"
    }
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from app.utils.strategy_constants import SYMBOL_TO_CLUSTER
from app.utils.time_horizon import horizon_rank, ALLOWED_HORIZONS


# ── Constants ────────────────────────────────────────────────────────

_POLICY_VERSION = "1.1"

# Categories
_CAT_PORTFOLIO = "portfolio_concentration"
_CAT_MARKET    = "market_conflict"
_CAT_QUALITY   = "data_quality"
_CAT_HORIZON   = "time_horizon"
_CAT_RISK_PKG  = "risk_packaging"

# Severity ranking: higher = worse
_SEVERITY_RANK = {"none": 0, "low": 1, "moderate": 2, "high": 3, "critical": 4}

# Decision ranking: higher = more restrictive
_DECISION_RANK = {"allow": 0, "caution": 1, "restrict": 2, "block": 3, "insufficient_data": 4}

# Recommended-effect → decision mapping
_EFFECT_TO_DECISION = {"caution": "caution", "restrict": "restrict", "block": "block"}

# ── Tunable thresholds ───────────────────────────────────────────────
# All policy-check numeric thresholds live here for visibility.
# Adjust these to tighten or relax guardrails.

# Horizon gap thresholds (rank units)
_HORIZON_GAP_CAUTION = 2
_HORIZON_GAP_RESTRICT = 4

# Portfolio concentration thresholds (share / HHI values)
_SYMBOL_SHARE_RESTRICT = 0.30         # single-symbol share ≥ this → restrict
_HHI_OVERALL_CAUTION = 0.50           # portfolio HHI > this → caution
_STRATEGY_SHARE_CAUTION = 0.50        # same-strategy share ≥ this → caution
_EXPIRATION_SHARE_CAUTION = 0.50      # DTE bucket share ≥ this → caution
_CLUSTER_SHARE_RESTRICT = 0.50        # correlated cluster share ≥ this → restrict
_UTILIZATION_CAUTION = 0.60           # capital utilization > this → caution
_UTILIZATION_RESTRICT = 0.80          # capital utilization > this → restrict

# Market / data quality thresholds
_MARKET_CONFIDENCE_CAUTION = 0.35     # market confidence < this → caution
_CANDIDATE_MISSING_FIELDS_CAUTION = 3 # candidate missing ≥ this many fields → caution
_CANDIDATE_CONFIDENCE_CAUTION = 0.30  # candidate confidence < this → caution

# ── Short-premium strategy identification ────────────────────────────
# Strategies that sell option premium.  When the market is unstable
# these face outsized adverse-move risk.
_SHORT_PREMIUM_STRATEGIES: frozenset[str] = frozenset({
    "put_credit_spread", "call_credit_spread", "iron_condor",
    "csp", "cash_secured_put", "covered_call",
    "credit_put", "credit_call",
    "credit_put_spread", "credit_call_spread",
    "income",
})


# ── Public API ───────────────────────────────────────────────────────


def evaluate_policy(
    *,
    candidate: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
    conflicts: dict[str, Any] | None = None,
    portfolio: dict[str, Any] | None = None,
    assembled: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate deterministic guardrail policy checks.

    All inputs are optional.  When critical inputs are missing the
    engine returns ``policy_decision = "insufficient_data"`` instead
    of fabricating certainty.

    Parameters
    ----------
    candidate : dict | None
        Normalized candidate from ``normalize_candidate_output()``.
    market : dict | None
        Market composite from ``build_market_composite()``.
    conflicts : dict | None
        Conflict report from ``detect_conflicts()``.
    portfolio : dict | None
        Portfolio exposure from ``build_portfolio_exposure()``.
    assembled : dict | None
        Assembled context from ``assemble_context()`` (optional enrichment).

    Returns
    -------
    dict – structured policy result conforming to the output contract.
    """
    checks: list[dict[str, Any]] = []
    warning_flags: list[str] = []
    eligibility_flags: list[str] = []

    # ── Insufficient-data pre-check ──────────────────────────────
    insufficient, insuff_warnings = _check_insufficient_data(
        candidate, market, conflicts, portfolio,
    )
    warning_flags.extend(insuff_warnings)

    if insufficient:
        return _build_result(
            checks=[],
            warning_flags=sorted(set(warning_flags)),
            eligibility_flags=[],
            policy_decision="insufficient_data",
            decision_severity="critical",
            summary="Cannot evaluate policy: critical context inputs are missing.",
            candidate=candidate,
            market=market,
            conflicts=conflicts,
            portfolio=portfolio,
        )

    # ── Run guardrail categories ─────────────────────────────────

    # A. Portfolio concentration / clustering
    if portfolio is not None:
        checks.extend(_check_portfolio_concentration(candidate, portfolio))
    else:
        warning_flags.append("portfolio_unavailable")

    # B. Market and conflict conditions
    if market is not None:
        checks.extend(_check_market_conditions(candidate, market))
    if conflicts is not None:
        checks.extend(_check_conflict_conditions(candidate, conflicts))

    # C. Data quality / coverage
    checks.extend(_check_data_quality(candidate, market, conflicts, portfolio, assembled))

    # D. Time-horizon alignment
    if candidate is not None and market is not None:
        checks.extend(_check_time_horizon(candidate, market, assembled))

    # E. Risk packaging / structure
    if candidate is not None:
        checks.extend(_check_risk_packaging(candidate))

    # ── Derive policy decision ───────────────────────────────────
    blocking = [c for c in checks if c["recommended_effect"] == "block"]
    restrictive = [c for c in checks if c["recommended_effect"] == "restrict"]
    cautionary = [c for c in checks if c["recommended_effect"] == "caution"]

    policy_decision = _derive_decision(blocking, restrictive, cautionary)
    decision_severity = _derive_severity(checks)
    size_guidance = _derive_size_guidance(policy_decision, checks)

    # Eligibility flags
    if not checks:
        eligibility_flags.append("clean_evaluation")
    if policy_decision == "allow":
        eligibility_flags.append("eligible")
    if policy_decision in ("caution", "restrict"):
        eligibility_flags.append("conditionally_eligible")
    if policy_decision == "block":
        eligibility_flags.append("ineligible")

    summary = _build_summary(
        policy_decision, blocking, restrictive, cautionary, candidate,
    )

    return _build_result(
        checks=checks,
        warning_flags=sorted(set(warning_flags)),
        eligibility_flags=sorted(set(eligibility_flags)),
        policy_decision=policy_decision,
        decision_severity=decision_severity,
        summary=summary,
        size_guidance=size_guidance,
        candidate=candidate,
        market=market,
        conflicts=conflicts,
        portfolio=portfolio,
    )


# ── Result assembly ──────────────────────────────────────────────────

def _build_result(
    *,
    checks: list[dict],
    warning_flags: list[str],
    eligibility_flags: list[str],
    policy_decision: str,
    decision_severity: str,
    summary: str,
    size_guidance: str = "none",
    candidate: dict | None,
    market: dict | None,
    conflicts: dict | None,
    portfolio: dict | None,
) -> dict[str, Any]:
    blocking = [c for c in checks if c["recommended_effect"] == "block"]
    restrictive = [c for c in checks if c["recommended_effect"] == "restrict"]
    cautionary = [c for c in checks if c["recommended_effect"] == "caution"]

    return {
        "policy_version": _POLICY_VERSION,
        "evaluated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "status": "evaluated" if policy_decision != "insufficient_data" else "insufficient_data",
        "policy_decision": policy_decision,
        "decision_severity": decision_severity,
        "summary": summary,
        "triggered_checks": checks,
        "blocking_checks": blocking,
        "caution_checks": cautionary,
        "restrictive_checks": restrictive,
        "size_guidance": size_guidance,
        "eligibility_flags": eligibility_flags,
        "warning_flags": warning_flags,
        "evidence": {
            "candidate_symbol": _safe_str(candidate, "symbol") if candidate else None,
            "candidate_strategy": _safe_str(candidate, "setup_type", "scanner_key") if candidate else None,
            "market_status": _safe_str(market, "status") if market else None,
            "market_state": _safe_str(market, "market_state") if market else None,
            "conflict_severity": _safe_str(conflicts, "conflict_severity") if conflicts else None,
            "portfolio_status": _safe_str(portfolio, "status") if portfolio else None,
            "checks_triggered": len(checks),
            "blocking_count": len(blocking),
            "restrictive_count": len(restrictive),
            "caution_count": len(cautionary),
        },
        "metadata": {
            "policy_version": _POLICY_VERSION,
            "candidate_provided": candidate is not None,
            "market_provided": market is not None,
            "conflicts_provided": conflicts is not None,
            "portfolio_provided": portfolio is not None,
            "checks_evaluated": len(checks),
        },
    }


# ── Insufficient data check ─────────────────────────────────────────

def _check_insufficient_data(
    candidate: dict | None,
    market: dict | None,
    conflicts: dict | None,
    portfolio: dict | None,
) -> tuple[bool, list[str]]:
    """Determine if data is too sparse for meaningful evaluation.

    Returns (is_insufficient, warning_list).
    Insufficient when candidate is missing entirely — cannot evaluate
    a policy without knowing what trade is proposed.
    """
    warnings: list[str] = []

    if candidate is None:
        warnings.append("candidate_missing")
        return True, warnings

    # Candidate must have symbol
    sym = candidate.get("symbol") or candidate.get("underlying") or ""
    if not str(sym).strip():
        warnings.append("candidate_symbol_missing")
        return True, warnings

    # Track which optional inputs are missing
    if market is None:
        warnings.append("market_composite_unavailable")
    if conflicts is None:
        warnings.append("conflict_report_unavailable")
    if portfolio is None:
        warnings.append("portfolio_exposure_unavailable")

    # If we have a candidate, we can run *some* checks even without other inputs
    return False, warnings


# ══════════════════════════════════════════════════════════════════
#  A. PORTFOLIO CONCENTRATION CHECKS
# ══════════════════════════════════════════════════════════════════

def _check_portfolio_concentration(
    candidate: dict | None,
    portfolio: dict,
) -> list[dict[str, Any]]:
    """Evaluate portfolio concentration guardrails."""
    checks: list[dict] = []
    if candidate is None:
        return checks

    cand_sym = str(candidate.get("symbol") or "").upper()

    # ── A1. Same-underlying concentration ────────────────────────
    under_conc = portfolio.get("underlying_concentration", {})
    if under_conc.get("concentrated"):
        top = under_conc.get("top_symbols", [])
        # Check if candidate symbol already in concentrated list
        for entry in top:
            if entry.get("symbol") == cand_sym and entry.get("share", 0) >= _SYMBOL_SHARE_RESTRICT:
                checks.append(_make_check(
                    code="portfolio_underlying_concentrated",
                    severity="high",
                    category=_CAT_PORTFOLIO,
                    title="Candidate symbol already heavily concentrated",
                    description=(
                        f"{cand_sym} already represents "
                        f"{entry['share']:.0%} of portfolio risk. "
                        f"Adding more increases single-name concentration."
                    ),
                    entities=[cand_sym],
                    evidence={"symbol": cand_sym, "share": entry.get("share"), "hhi": under_conc.get("hhi")},
                    effect="restrict",
                    impact="major",
                ))
                break
        else:
            # Even if concentrated on another symbol, general concentration warning
            if under_conc.get("hhi", 0) > _HHI_OVERALL_CAUTION:
                checks.append(_make_check(
                    code="portfolio_overall_concentrated",
                    severity="moderate",
                    category=_CAT_PORTFOLIO,
                    title="Portfolio overall highly concentrated",
                    description=(
                        f"Portfolio HHI is {under_conc.get('hhi', 0):.2f} "
                        f"(above 0.50). Adding new positions increases risk."
                    ),
                    entities=[cand_sym],
                    evidence={"hhi": under_conc.get("hhi"), "total_symbols": under_conc.get("total_symbols")},
                    effect="caution",
                    impact="moderate",
                ))

    # ── A2. Strategy concentration ───────────────────────────────
    strat_conc = portfolio.get("strategy_concentration", {})
    if strat_conc.get("concentrated"):
        cand_strat = str(candidate.get("setup_type") or candidate.get("scanner_key") or "").lower()
        top_strats = strat_conc.get("top_strategies", [])
        for entry in top_strats:
            if entry.get("strategy") == cand_strat and entry.get("share", 0) >= _STRATEGY_SHARE_CAUTION:
                checks.append(_make_check(
                    code="portfolio_strategy_concentrated",
                    severity="moderate",
                    category=_CAT_PORTFOLIO,
                    title="Same strategy type already concentrated",
                    description=(
                        f"Strategy '{cand_strat}' already represents "
                        f"{entry['share']:.0%} of positions. "
                        f"Adding more concentrates strategy risk."
                    ),
                    entities=[cand_strat],
                    evidence={"strategy": cand_strat, "share": entry.get("share")},
                    effect="caution",
                    impact="moderate",
                ))
                break

    # ── A3. Expiration clustering ────────────────────────────────
    exp_conc = portfolio.get("expiration_concentration", {})
    if exp_conc.get("concentrated"):
        cand_dte = candidate.get("entry_context", {}).get("dte")
        if cand_dte is not None:
            bucket = _dte_to_bucket(cand_dte)
            buckets = exp_conc.get("buckets", {})
            bucket_info = buckets.get(bucket, {})
            if bucket_info.get("share", 0) >= _EXPIRATION_SHARE_CAUTION and bucket_info.get("count", 0) > 1:
                checks.append(_make_check(
                    code="portfolio_expiration_clustered",
                    severity="moderate",
                    category=_CAT_PORTFOLIO,
                    title="Expiration clustering in candidate's DTE bucket",
                    description=(
                        f"DTE bucket '{bucket}' already holds "
                        f"{bucket_info['share']:.0%} of portfolio risk. "
                        f"Candidate at {cand_dte} DTE would add to this cluster."
                    ),
                    entities=[bucket],
                    evidence={"bucket": bucket, "share": bucket_info.get("share"), "dte": cand_dte},
                    effect="caution",
                    impact="minor",
                ))

    # ── A4. Correlated cluster risk ──────────────────────────────
    corr = portfolio.get("correlation_exposure", {})
    if corr.get("concentrated"):
        # Check if candidate's symbol falls into a concentrated cluster
        cluster = SYMBOL_TO_CLUSTER.get(cand_sym)
        if cluster:
            cluster_info = corr.get("clusters", {}).get(cluster, {})
            if cluster_info.get("share", 0) >= _CLUSTER_SHARE_RESTRICT:
                checks.append(_make_check(
                    code="portfolio_correlated_cluster",
                    severity="high",
                    category=_CAT_PORTFOLIO,
                    title="Correlated asset cluster already concentrated",
                    description=(
                        f"{cand_sym} belongs to the '{cluster}' cluster, "
                        f"which already represents {cluster_info['share']:.0%} of risk. "
                        f"Adding more concentrates correlated exposure."
                    ),
                    entities=[cand_sym, cluster],
                    evidence={
                        "cluster": cluster, "share": cluster_info.get("share"),
                        "cluster_symbols": cluster_info.get("symbols", []),
                    },
                    effect="restrict",
                    impact="major",
                ))

    # ── A5. Directional stacking ─────────────────────────────────
    dir_exp = portfolio.get("directional_exposure", {})
    port_bias = dir_exp.get("bias", "neutral")
    cand_dir = str(candidate.get("direction") or "").lower()

    if port_bias in ("bullish", "bearish") and cand_dir:
        # Same direction stacking
        cand_is_bullish = cand_dir in ("long", "bullish")
        cand_is_bearish = cand_dir in ("short", "bearish")

        if (port_bias == "bullish" and cand_is_bullish) or \
           (port_bias == "bearish" and cand_is_bearish):
            # Check if lean is already heavy
            risk_flags = portfolio.get("risk_flags", [])
            if "heavy_bullish_lean" in risk_flags or "heavy_bearish_lean" in risk_flags:
                checks.append(_make_check(
                    code="portfolio_directional_stacking",
                    severity="moderate",
                    category=_CAT_PORTFOLIO,
                    title="Adding to an already heavy directional lean",
                    description=(
                        f"Portfolio is already heavily {port_bias}. "
                        f"Candidate direction '{cand_dir}' would increase directional risk."
                    ),
                    entities=[cand_sym],
                    evidence={"portfolio_bias": port_bias, "candidate_direction": cand_dir},
                    effect="caution",
                    impact="moderate",
                ))

    # ── A6. High utilization ─────────────────────────────────────
    capital = portfolio.get("capital_at_risk", {})
    util = capital.get("utilization_pct")
    if util is not None and util > _UTILIZATION_CAUTION:
        severity = "high" if util > _UTILIZATION_RESTRICT else "moderate"
        effect = "restrict" if util > _UTILIZATION_RESTRICT else "caution"
        checks.append(_make_check(
            code="portfolio_high_utilization",
            severity=severity,
            category=_CAT_PORTFOLIO,
            title="Portfolio capital utilization is elevated",
            description=(
                f"Current utilization is {util:.0%}. "
                f"Adding new risk may exceed prudent allocation limits."
            ),
            entities=[],
            evidence={"utilization_pct": util, "total_risk": capital.get("total_risk")},
            effect=effect,
            impact="moderate",
        ))

    return checks


# ══════════════════════════════════════════════════════════════════
#  B. MARKET & CONFLICT CHECKS
# ══════════════════════════════════════════════════════════════════

def _check_market_conditions(
    candidate: dict | None,
    market: dict,
) -> list[dict[str, Any]]:
    """Evaluate market composite guardrails."""
    checks: list[dict] = []

    market_status = market.get("status", "")
    market_state = market.get("market_state", "")
    support_state = market.get("support_state", "")
    stability_state = market.get("stability_state", "")
    confidence = market.get("confidence", 1.0)

    # ── B1. Unstable market state ────────────────────────────────
    if stability_state == "unstable":
        checks.append(_make_check(
            code="market_unstable",
            severity="high",
            category=_CAT_MARKET,
            title="Market environment is unstable",
            description=(
                "Market composite stability is 'unstable'. "
                "New option positions carry elevated regime-change risk."
            ),
            entities=[],
            evidence={"stability_state": stability_state, "confidence": confidence},
            effect="restrict",
            impact="major",
        ))
    elif stability_state == "noisy":
        checks.append(_make_check(
            code="market_noisy",
            severity="low",
            category=_CAT_MARKET,
            title="Market environment is noisy",
            description=(
                "Market composite stability is 'noisy'. "
                "Signals are less reliable, warranting caution."
            ),
            entities=[],
            evidence={"stability_state": stability_state, "confidence": confidence},
            effect="caution",
            impact="minor",
        ))

    # ── B2. Fragile support state ────────────────────────────────
    if support_state == "fragile":
        checks.append(_make_check(
            code="market_fragile_support",
            severity="moderate",
            category=_CAT_MARKET,
            title="Market support is fragile",
            description=(
                "Market composite support state is 'fragile'. "
                "Underlying conditions may not sustain new positions well."
            ),
            entities=[],
            evidence={"support_state": support_state},
            effect="caution",
            impact="moderate",
        ))

    # ── B3. Candidate direction vs market state ──────────────────
    if candidate is not None:
        cand_dir = str(candidate.get("direction") or "").lower()
        if cand_dir and market_state:
            # Bullish candidate in risk_off market
            if cand_dir in ("long", "bullish") and market_state == "risk_off":
                checks.append(_make_check(
                    code="candidate_vs_market_direction",
                    severity="high",
                    category=_CAT_MARKET,
                    title="Candidate direction conflicts with market state",
                    description=(
                        f"Candidate is directionally '{cand_dir}' but "
                        f"market composite is 'risk_off'. "
                        f"Entering a bullish trade against a risk-off backdrop is risky."
                    ),
                    entities=[str(candidate.get("symbol", ""))],
                    evidence={"candidate_direction": cand_dir, "market_state": market_state},
                    effect="restrict",
                    impact="major",
                ))
            # Bearish candidate in risk_on market
            elif cand_dir in ("short", "bearish") and market_state == "risk_on":
                checks.append(_make_check(
                    code="candidate_vs_market_direction",
                    severity="moderate",
                    category=_CAT_MARKET,
                    title="Candidate direction conflicts with market state",
                    description=(
                        f"Candidate is directionally '{cand_dir}' but "
                        f"market composite is 'risk_on'. "
                        f"Entering a bearish trade against a risk-on backdrop warrants caution."
                    ),
                    entities=[str(candidate.get("symbol", ""))],
                    evidence={"candidate_direction": cand_dir, "market_state": market_state},
                    effect="caution",
                    impact="moderate",
                ))

    # ── B4. Short premium in unstable market ─────────────────────
    if candidate is not None and stability_state in ("unstable", "noisy"):
        cand_setup = str(candidate.get("setup_type") or candidate.get("scanner_key") or "").lower()

        if cand_setup in _SHORT_PREMIUM_STRATEGIES and stability_state == "unstable":
            checks.append(_make_check(
                code="short_premium_unstable_market",
                severity="high",
                category=_CAT_MARKET,
                title="Short-premium strategy in unstable market",
                description=(
                    f"Selling premium via '{cand_setup}' when market stability "
                    f"is 'unstable' exposes the position to outsized adverse moves."
                ),
                entities=[cand_setup],
                evidence={"strategy": cand_setup, "stability_state": stability_state},
                effect="restrict",
                impact="major",
            ))

    # ── B5. Low market composite confidence ──────────────────────
    if confidence < _MARKET_CONFIDENCE_CAUTION:
        checks.append(_make_check(
            code="market_low_confidence",
            severity="moderate",
            category=_CAT_MARKET,
            title="Market composite confidence is very low",
            description=(
                f"Market composite confidence is {confidence:.0%}. "
                f"This suggests degraded or conflicting market context."
            ),
            entities=[],
            evidence={"confidence": confidence},
            effect="caution",
            impact="moderate",
        ))

    # ── B6. Degraded market composite ────────────────────────────
    if market_status == "insufficient_data":
        checks.append(_make_check(
            code="market_insufficient_data",
            severity="high",
            category=_CAT_QUALITY,
            title="Market composite has insufficient data",
            description="Market composite status is 'insufficient_data'. Context is unreliable.",
            entities=[],
            evidence={"market_status": market_status},
            effect="restrict",
            impact="major",
        ))
    elif market_status == "degraded":
        checks.append(_make_check(
            code="market_degraded",
            severity="moderate",
            category=_CAT_QUALITY,
            title="Market composite is degraded",
            description="Market composite status is 'degraded'. Some modules may be on fallback data.",
            entities=[],
            evidence={"market_status": market_status},
            effect="caution",
            impact="moderate",
        ))

    return checks


def _check_conflict_conditions(
    candidate: dict | None,
    conflicts: dict,
) -> list[dict[str, Any]]:
    """Evaluate conflict severity guardrails."""
    checks: list[dict] = []

    severity = conflicts.get("conflict_severity", "none")
    count = conflicts.get("conflict_count", 0)
    flags = conflicts.get("conflict_flags", [])

    # ── B7. High conflict severity ───────────────────────────────
    if severity == "high":
        checks.append(_make_check(
            code="conflict_severity_high",
            severity="high",
            category=_CAT_MARKET,
            title="Conflict severity is high",
            description=(
                f"Conflict detector reports '{severity}' severity "
                f"with {count} conflicts. Strong disagreement across "
                f"market modules reduces decision confidence."
            ),
            entities=flags[:5],
            evidence={"conflict_severity": severity, "conflict_count": count, "flags": flags[:5]},
            effect="restrict",
            impact="major",
        ))
    elif severity == "moderate":
        checks.append(_make_check(
            code="conflict_severity_moderate",
            severity="moderate",
            category=_CAT_MARKET,
            title="Conflict severity is moderate",
            description=(
                f"Conflict detector reports '{severity}' severity "
                f"with {count} conflicts. Some module disagreement is present."
            ),
            entities=flags[:5],
            evidence={"conflict_severity": severity, "conflict_count": count, "flags": flags[:3]},
            effect="caution",
            impact="moderate",
        ))

    return checks


# ══════════════════════════════════════════════════════════════════
#  C. DATA QUALITY CHECKS
# ══════════════════════════════════════════════════════════════════

def _check_data_quality(
    candidate: dict | None,
    market: dict | None,
    conflicts: dict | None,
    portfolio: dict | None,
    assembled: dict | None,
) -> list[dict[str, Any]]:
    """Evaluate data-quality and coverage guardrails."""
    checks: list[dict] = []

    # ── C1. Candidate data quality ───────────────────────────────
    if candidate is not None:
        dq = candidate.get("data_quality", {})
        missing = dq.get("missing_fields", [])
        confidence = candidate.get("confidence")

        if missing and len(missing) >= _CANDIDATE_MISSING_FIELDS_CAUTION:
            checks.append(_make_check(
                code="candidate_many_missing_fields",
                severity="moderate",
                category=_CAT_QUALITY,
                title="Candidate has many missing data fields",
                description=(
                    f"Candidate is missing {len(missing)} fields: "
                    f"{', '.join(missing[:5])}. "
                    f"Evaluation reliability is reduced."
                ),
                entities=missing[:5],
                evidence={"missing_fields": missing, "count": len(missing)},
                effect="caution",
                impact="moderate",
            ))

        if confidence is not None and confidence < _CANDIDATE_CONFIDENCE_CAUTION:
            checks.append(_make_check(
                code="candidate_low_confidence",
                severity="moderate",
                category=_CAT_QUALITY,
                title="Candidate data confidence is very low",
                description=(
                    f"Candidate confidence is {confidence:.2f}. "
                    f"Trade metrics may be unreliable."
                ),
                entities=[str(candidate.get("symbol", ""))],
                evidence={"confidence": confidence},
                effect="caution",
                impact="moderate",
            ))

    # ── C2. Portfolio data quality ───────────────────────────────
    if portfolio is not None:
        port_status = portfolio.get("status", "")
        port_warnings = portfolio.get("warning_flags", [])

        if port_status == "partial":
            greeks_cov = portfolio.get("greeks_exposure", {}).get("coverage", "none")
            risk_partial = "risk_data_partial" in port_warnings or "risk_data_unavailable" in port_warnings
            if greeks_cov == "none" and risk_partial:
                checks.append(_make_check(
                    code="portfolio_data_very_sparse",
                    severity="moderate",
                    category=_CAT_QUALITY,
                    title="Portfolio data is very sparse",
                    description=(
                        "Portfolio exposure data is partial with no Greeks "
                        "and missing risk data. Concentration checks may be unreliable."
                    ),
                    entities=[],
                    evidence={"portfolio_status": port_status, "greeks_coverage": greeks_cov, "warnings": port_warnings},
                    effect="caution",
                    impact="moderate",
                ))

    # ── C3. Assembled context quality ────────────────────────────
    if assembled is not None:
        quality_summary = assembled.get("quality_summary", {})
        overall_quality = quality_summary.get("overall_quality", "unknown")
        degraded_modules = assembled.get("degraded_modules", [])
        missing_modules = assembled.get("missing_modules", [])

        if overall_quality in ("poor", "unavailable"):
            checks.append(_make_check(
                code="context_quality_poor",
                severity="high",
                category=_CAT_QUALITY,
                title="Overall context quality is poor",
                description=(
                    f"Assembled context overall quality is '{overall_quality}'. "
                    f"Decision inputs are unreliable."
                ),
                entities=degraded_modules[:3],
                evidence={
                    "overall_quality": overall_quality,
                    "degraded_modules": degraded_modules,
                    "missing_modules": missing_modules,
                },
                effect="restrict",
                impact="major",
            ))
        elif overall_quality == "degraded":
            checks.append(_make_check(
                code="context_quality_degraded",
                severity="moderate",
                category=_CAT_QUALITY,
                title="Context quality is degraded",
                description=(
                    f"Assembled context quality is '{overall_quality}'. "
                    f"Some inputs are on fallback or missing."
                ),
                entities=degraded_modules[:3],
                evidence={
                    "overall_quality": overall_quality,
                    "degraded_count": len(degraded_modules),
                    "missing_count": len(missing_modules),
                },
                effect="caution",
                impact="minor",
            ))

        # Too many missing market modules
        if len(missing_modules) >= 3:
            checks.append(_make_check(
                code="context_many_missing_modules",
                severity="high",
                category=_CAT_QUALITY,
                title="Too many market modules missing",
                description=(
                    f"{len(missing_modules)} market modules are missing: "
                    f"{', '.join(missing_modules[:5])}. "
                    f"Market picture is incomplete."
                ),
                entities=missing_modules[:5],
                evidence={"missing_modules": missing_modules, "count": len(missing_modules)},
                effect="restrict",
                impact="major",
            ))

    return checks


# ══════════════════════════════════════════════════════════════════
#  D. TIME-HORIZON CHECKS
# ══════════════════════════════════════════════════════════════════

def _check_time_horizon(
    candidate: dict,
    market: dict,
    assembled: dict | None,
) -> list[dict[str, Any]]:
    """Evaluate time-horizon alignment between candidate and market."""
    checks: list[dict] = []

    cand_horizon = str(candidate.get("time_horizon") or "unknown").lower()
    if cand_horizon not in ALLOWED_HORIZONS:
        cand_horizon = "unknown"

    if cand_horizon == "unknown":
        return checks  # can't evaluate alignment without candidate horizon

    cand_rank = horizon_rank(cand_horizon)

    # Get the market horizon from assembled context or market metadata
    market_horizons: list[str] = []
    if assembled is not None:
        hs = assembled.get("horizon_summary", {})
        market_horizons_dict = hs.get("market_horizons", {})
        market_horizons = [
            v for v in market_horizons_dict.values()
            if v and v != "unknown"
        ]

    if not market_horizons:
        # Try market metadata
        horizon_span = (market.get("metadata", {}).get("horizon_span") or "")
        if horizon_span:
            parts = [p.strip() for p in horizon_span.replace("→", " ").replace("->", " ").split()]
            market_horizons = [p for p in parts if p in ALLOWED_HORIZONS]

    if not market_horizons:
        return checks  # no horizon data to compare against

    market_ranks = [horizon_rank(h) for h in market_horizons if h != "unknown"]
    if not market_ranks:
        return checks

    # Closest market horizon to candidate
    min_gap = min(abs(cand_rank - mr) for mr in market_ranks)

    if min_gap >= _HORIZON_GAP_RESTRICT:
        checks.append(_make_check(
            code="horizon_severe_mismatch",
            severity="high",
            category=_CAT_HORIZON,
            title="Severe time-horizon mismatch",
            description=(
                f"Candidate horizon '{cand_horizon}' is far from the nearest "
                f"market horizon (gap={min_gap} ranks). Market evidence may not "
                f"apply to this trade's timeframe."
            ),
            entities=[cand_horizon],
            evidence={
                "candidate_horizon": cand_horizon,
                "market_horizons": market_horizons,
                "gap": min_gap,
            },
            effect="restrict",
            impact="major",
        ))
    elif min_gap >= _HORIZON_GAP_CAUTION:
        checks.append(_make_check(
            code="horizon_moderate_mismatch",
            severity="moderate",
            category=_CAT_HORIZON,
            title="Moderate time-horizon mismatch",
            description=(
                f"Candidate horizon '{cand_horizon}' is moderately distant from "
                f"market horizons (gap={min_gap} ranks). Supporting context "
                f"may be less relevant."
            ),
            entities=[cand_horizon],
            evidence={
                "candidate_horizon": cand_horizon,
                "market_horizons": market_horizons,
                "gap": min_gap,
            },
            effect="caution",
            impact="minor",
        ))

    return checks


# ══════════════════════════════════════════════════════════════════
#  E. RISK PACKAGING CHECKS
# ══════════════════════════════════════════════════════════════════

def _check_risk_packaging(
    candidate: dict,
) -> list[dict[str, Any]]:
    """Evaluate candidate risk definition completeness."""
    checks: list[dict] = []

    risk_def = candidate.get("risk_definition", {})
    risk_type = risk_def.get("type", "")
    reward = candidate.get("reward_profile", {})
    cand_sym = str(candidate.get("symbol") or "")

    # ── E1. Missing risk definition ──────────────────────────────
    if not risk_type:
        checks.append(_make_check(
            code="risk_definition_missing",
            severity="critical",
            category=_CAT_RISK_PKG,
            title="Risk definition is missing",
            description=(
                "Candidate has no risk definition type. "
                "Cannot evaluate max loss or position safety."
            ),
            entities=[cand_sym],
            evidence={"risk_definition": risk_def},
            effect="block",
            impact="major",
        ))
        return checks  # No point checking further

    # ── E2. Defined-risk spread with missing max loss ────────────
    if risk_type == "defined_risk_spread":
        max_loss = risk_def.get("max_loss_per_contract")
        if max_loss is None:
            checks.append(_make_check(
                code="risk_max_loss_missing",
                severity="high",
                category=_CAT_RISK_PKG,
                title="Max loss per contract is missing",
                description=(
                    "Defined-risk spread candidate is missing max_loss_per_contract. "
                    "Cannot compute position sizing or capital requirements."
                ),
                entities=[cand_sym],
                evidence={"risk_type": risk_type, "max_loss": max_loss},
                effect="restrict",
                impact="major",
            ))

        # Missing POP
        pop = risk_def.get("pop")
        if pop is None:
            checks.append(_make_check(
                code="risk_pop_missing",
                severity="low",
                category=_CAT_RISK_PKG,
                title="Probability of profit is missing",
                description="Candidate is missing POP. Expected-value assessment is incomplete.",
                entities=[cand_sym],
                evidence={"pop": pop},
                effect="caution",
                impact="minor",
            ))

    # ── E3. Missing reward profile metrics ───────────────────────
    reward_type = reward.get("type", "")
    if reward_type == "defined_reward_spread":
        ev = reward.get("expected_value_per_contract")
        ror = reward.get("return_on_risk")
        if ev is None and ror is None:
            checks.append(_make_check(
                code="reward_metrics_missing",
                severity="low",
                category=_CAT_RISK_PKG,
                title="Expected value and return-on-risk are both missing",
                description="Neither EV nor return-on-risk is available for this candidate.",
                entities=[cand_sym],
                evidence={"ev": ev, "return_on_risk": ror},
                effect="caution",
                impact="minor",
            ))

    # ── E4. Stock without stop-loss ──────────────────────────────
    if risk_type == "stop_loss_based":
        notes = risk_def.get("notes", [])
        if not notes:
            checks.append(_make_check(
                code="risk_stop_loss_undefined",
                severity="moderate",
                category=_CAT_RISK_PKG,
                title="Stop-loss level is undefined",
                description="Stock candidate uses stop-loss-based risk but no stop-loss notes are defined.",
                entities=[cand_sym],
                evidence={"risk_type": risk_type, "notes": notes},
                effect="caution",
                impact="moderate",
            ))

    return checks


# ── Decision derivation ──────────────────────────────────────────────

def _derive_decision(
    blocking: list[dict],
    restrictive: list[dict],
    cautionary: list[dict],
) -> str:
    """Derive overall policy decision from triggered checks.

    Priority: block > restrict > caution > allow.
    """
    if blocking:
        return "block"
    if restrictive:
        return "restrict"
    if cautionary:
        return "caution"
    return "allow"


def _derive_severity(checks: list[dict]) -> str:
    """Derive overall severity from triggered checks."""
    if not checks:
        return "none"
    max_rank = max(_SEVERITY_RANK.get(c.get("severity", "none"), 0) for c in checks)
    for name, rank in _SEVERITY_RANK.items():
        if rank == max_rank:
            return name
    return "none"


def _derive_size_guidance(
    decision: str,
    checks: list[dict],
) -> str:
    """Derive size guidance from policy decision.

    - allow → normal
    - caution → reduced
    - restrict → minimal
    - block / insufficient_data → none
    """
    if decision == "allow":
        return "normal"
    if decision == "caution":
        return "reduced"
    if decision == "restrict":
        return "minimal"
    return "none"  # block or insufficient_data


# ── Summary builder ──────────────────────────────────────────────────

def _build_summary(
    decision: str,
    blocking: list[dict],
    restrictive: list[dict],
    cautionary: list[dict],
    candidate: dict | None,
) -> str:
    """Build a concise human-readable summary."""
    sym = str(candidate.get("symbol", "candidate")) if candidate else "candidate"

    if decision == "allow":
        return f"No policy issues detected for {sym}. Candidate is eligible for review."

    parts = []
    if blocking:
        parts.append(f"{len(blocking)} blocking issue(s)")
    if restrictive:
        parts.append(f"{len(restrictive)} restrictive issue(s)")
    if cautionary:
        parts.append(f"{len(cautionary)} cautionary issue(s)")

    issues = ", ".join(parts)
    action = {
        "caution": "Proceed with caution",
        "restrict": "Significant restrictions apply",
        "block": "Trade is blocked by policy",
    }.get(decision, decision)

    return f"{action} for {sym}: {issues}."


# ── Helpers ──────────────────────────────────────────────────────────

def _make_check(
    *,
    code: str,
    severity: str,
    category: str,
    title: str,
    description: str,
    entities: list[str],
    evidence: dict,
    effect: str,
    impact: str,
) -> dict[str, Any]:
    """Create a standardised policy check item."""
    return {
        "check_code": code,
        "severity": severity,
        "category": category,
        "title": title,
        "description": description,
        "entities": entities,
        "evidence": evidence,
        "recommended_effect": effect,
        "confidence_impact": impact,
    }


def _safe_str(d: dict | None, *keys: str) -> str | None:
    """Safely extract first available string value from dict."""
    if d is None:
        return None
    for k in keys:
        v = d.get(k)
        if v is not None:
            return str(v)
    return None


def _dte_to_bucket(dte: int) -> str:
    """Map DTE value to portfolio bucket label."""
    if dte <= 7:
        return "0-7D"
    if dte <= 21:
        return "8-21D"
    if dte <= 45:
        return "22-45D"
    if dte <= 90:
        return "46-90D"
    return "90D+"
