"""Breadth & Participation — Data Quality Diagnostics Framework.

Provides structured, severity-classified warnings and data quality
assessment for institutional-grade breadth analysis.

Warning severity tiers:
  critical — directly undermines historical validity or core score trustworthiness
  high     — materially reduces confidence or coverage
  medium   — weakens the engine but does not invalidate it
  low      — minor degradation or nonessential gap
  info     — known unimplemented enhancement (scaffold), not a production defect

Warning categories:
  data_integrity   — structural risks to data validity (e.g., survivorship bias)
  completeness     — missing data fields or insufficient coverage
  methodology      — benchmark or scoring methodology gaps
  disagreement     — cross-pillar or cross-signal conflict
  scaffold         — intentionally deferred enhancement
  coverage         — universe representation gaps

Each warning object:
  {
    "severity": "critical|high|medium|low|info",
    "category": "data_integrity|completeness|methodology|disagreement|scaffold|coverage",
    "code": "stable_machine_readable_code",
    "message": "human-readable text",
    "impact": "what this affects",
    "recommended_action": "what should be done next"
  }
"""

from __future__ import annotations

import logging
import statistics
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# SEVERITY CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
SEVERITY_INFO = "info"

# Severity ordering for sorting (lower = more severe)
SEVERITY_ORDER = {
    SEVERITY_CRITICAL: 0,
    SEVERITY_HIGH: 1,
    SEVERITY_MEDIUM: 2,
    SEVERITY_LOW: 3,
    SEVERITY_INFO: 4,
}

# ═══════════════════════════════════════════════════════════════════════
# CATEGORY CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

CAT_DATA_INTEGRITY = "data_integrity"
CAT_COMPLETENESS = "completeness"
CAT_METHODOLOGY = "methodology"
CAT_DISAGREEMENT = "disagreement"
CAT_SCAFFOLD = "scaffold"
CAT_COVERAGE = "coverage"


# ═══════════════════════════════════════════════════════════════════════
# WARNING BUILDER
# ═══════════════════════════════════════════════════════════════════════

def build_warning(
    *,
    severity: str,
    category: str,
    code: str,
    message: str,
    impact: str,
    recommended_action: str,
) -> dict[str, str]:
    """Build a single structured warning object.

    All fields are required. Code must be stable and machine-readable.
    """
    return {
        "severity": severity,
        "category": category,
        "code": code,
        "message": message,
        "impact": impact,
        "recommended_action": recommended_action,
    }


# ═══════════════════════════════════════════════════════════════════════
# SCAFFOLDED METRIC REGISTRY
# ═══════════════════════════════════════════════════════════════════════

# Metrics intentionally deferred. These generate info-level warnings,
# not production defect alerts.
SCAFFOLDED_METRICS: dict[str, dict[str, str]] = {
    "accumulation_distribution_bias": {
        "pillar": "volume_breadth",
        "description": "Accumulation/distribution bias scoring",
        "impact": "Volume pillar uses 3 of 5 planned submetrics",
    },
    "volume_thrust_signal": {
        "pillar": "volume_breadth",
        "description": "Volume thrust signal detection",
        "impact": "Cannot detect extreme volume thrust events",
    },
    "thrust_followthrough": {
        "pillar": "participation_stability",
        "description": "Breadth thrust follow-through measurement",
        "impact": "Stability pillar uses 3 of 5 planned submetrics",
    },
    "breadth_reversal_frequency": {
        "pillar": "participation_stability",
        "description": "Breadth reversal frequency tracking",
        "impact": "Cannot measure oscillation / reversal rate",
    },
    "trend_momentum_long": {
        "pillar": "trend_breadth",
        "description": "20D change in pct_above_200dma",
        "impact": "Long-term trend momentum not tracked; does not invalidate trend score",
    },
}


def is_scaffolded_metric(metric_name: str) -> bool:
    """Check if a metric is in the scaffolded (deferred) registry."""
    return metric_name in SCAFFOLDED_METRICS


# ═══════════════════════════════════════════════════════════════════════
# SURVIVORSHIP BIAS / POINT-IN-TIME ANALYSIS
# ═══════════════════════════════════════════════════════════════════════

# Expected schema for future point-in-time constituent provider.
# Logging this makes future integration straightforward.
POINT_IN_TIME_PROVIDER_SCHEMA = {
    "description": "Interface for historical constituent membership data",
    "methods": {
        "get_constituents_at_date": {
            "params": {"index": "str", "date": "str (YYYY-MM-DD)"},
            "returns": "list[str] — ticker symbols that were index members on that date",
        },
        "get_membership_changes": {
            "params": {"index": "str", "start_date": "str", "end_date": "str"},
            "returns": "list[dict] — additions/deletions with effective dates",
        },
    },
    "notes": "When available, enables survivorship-bias-free historical breadth.",
}


def assess_survivorship_risk(
    universe_meta: dict[str, Any],
    *,
    is_historical_mode: bool = False,
) -> dict[str, Any]:
    """Assess survivorship bias risk and produce structured warning.

    Parameters
    ----------
    universe_meta : dict
        Universe metadata from data provider.
    is_historical_mode : bool
        True if engine is evaluating historical/backfilled data.
        False for live/current snapshot analysis.

    Returns
    -------
    dict with:
      warning: structured warning object (or None if no risk)
      confidence_penalty: float — penalty to apply to confidence score
      historical_validity_penalty: float — penalty for historical validity
      point_in_time_available: bool
      survivorship_bias_risk: bool
      historical_validity_degraded: bool
    """
    pit_available = not universe_meta.get("survivorship_bias_risk", True)

    if pit_available:
        logger.info("event=survivorship_check result=clean pit_available=true")
        return {
            "warning": None,
            "confidence_penalty": 0.0,
            "historical_validity_penalty": 0.0,
            "point_in_time_available": True,
            "survivorship_bias_risk": False,
            "historical_validity_degraded": False,
        }

    # Point-in-time NOT available — assess severity based on mode
    if is_historical_mode:
        severity = SEVERITY_CRITICAL
        confidence_penalty = 15.0
        historical_validity_penalty = 35.0
        message = (
            "Historical breadth may be distorted because point-in-time "
            "constituent membership is unavailable. Current constituents are "
            "being used for historical calculations, introducing survivorship bias."
        )
        impact = (
            "Historical trend scoring, backtest-based breadth analysis, and "
            "regime comparisons may materially overstate past breadth quality "
            "because failed/delisted constituents are excluded."
        )
        recommended_action = (
            "Integrate a point-in-time constituent provider (e.g., index "
            "membership history API) to eliminate survivorship bias in "
            "historical analysis."
        )
    else:
        # Current/live snapshot mode — survivorship is a known permanent
        # limitation (no point-in-time constituent provider).  Apply a
        # small penalty for awareness but don't let it push confidence
        # into degraded territory by itself.
        severity = SEVERITY_HIGH
        confidence_penalty = 2.0
        historical_validity_penalty = 25.0
        message = (
            "Survivorship bias risk: no point-in-time constituent data. "
            "Current snapshot scoring is reasonable, but historical trend "
            "interpretation may be less reliable."
        )
        impact = (
            "Current breadth snapshot uses today's constituent list, which "
            "is acceptable for live analysis. Historical comparisons and "
            "trend persistence metrics may be slightly inflated."
        )
        recommended_action = (
            "Add point-in-time constituent provider for improved historical "
            "validity. Current snapshot analysis remains usable."
        )

    warning = build_warning(
        severity=severity,
        category=CAT_DATA_INTEGRITY,
        code="NO_POINT_IN_TIME_CONSTITUENTS",
        message=message,
        impact=impact,
        recommended_action=recommended_action,
    )

    logger.info(
        "event=survivorship_check result=risk_detected severity=%s "
        "is_historical=%s confidence_penalty=%.1f "
        "historical_validity_penalty=%.1f",
        severity, is_historical_mode, confidence_penalty,
        historical_validity_penalty,
    )

    return {
        "warning": warning,
        "confidence_penalty": confidence_penalty,
        "historical_validity_penalty": historical_validity_penalty,
        "point_in_time_available": False,
        "survivorship_bias_risk": True,
        "historical_validity_degraded": True,
    }


# ═══════════════════════════════════════════════════════════════════════
# CROSS-PILLAR DISAGREEMENT ANALYSIS
# ═══════════════════════════════════════════════════════════════════════

def analyze_disagreement(
    pillars: dict[str, dict[str, Any]],
    universe_meta: dict[str, Any],
) -> dict[str, Any]:
    """Analyze cross-pillar disagreement and classify cause.

    Determines whether disagreement is:
      - expected/healthy signal conflict (genuine mixed market conditions)
      - suspicious inconsistency possibly caused by data problems

    Checks for data-driven disagreement:
      - large sample size differences across pillars
      - sector mapping incompleteness driving one pillar
      - benchmark data missing for some pillars but present for others

    Returns
    -------
    dict with:
      disagreement_level: float (std of pillar scores)
      severity: str
      is_data_driven: bool — True if disagreement may be caused by data issues
      suspected_causes: list[str]
      warning: structured warning object (or None)
      confidence_penalty: float
    """
    valid_scores = {
        k: v["score"] for k, v in pillars.items()
        if v.get("score") is not None
    }

    if len(valid_scores) < 3:
        logger.debug("event=disagreement_check result=insufficient_pillars count=%d",
                      len(valid_scores))
        return {
            "disagreement_level": 0.0,
            "severity": SEVERITY_LOW,
            "is_data_driven": False,
            "suspected_causes": [],
            "warning": None,
            "confidence_penalty": 0.0,
        }

    scores = list(valid_scores.values())
    score_std = statistics.stdev(scores)
    score_range = max(scores) - min(scores)

    # ── Check for data-driven disagreement causes ────────────────
    suspected_causes: list[str] = []
    is_data_driven = False

    # Check 1: observation count disparity across pillars
    obs_counts = []
    for pname, pdata in pillars.items():
        total_obs = 0
        for sm in pdata.get("submetrics", []):
            total_obs += sm.get("observations", 0)
        if total_obs > 0:
            obs_counts.append((pname, total_obs))

    if len(obs_counts) >= 2:
        min_obs = min(c for _, c in obs_counts)
        max_obs = max(c for _, c in obs_counts)
        if max_obs > 0 and min_obs / max_obs < 0.3:
            suspected_causes.append(
                f"Large observation disparity across pillars "
                f"(min={min_obs}, max={max_obs})"
            )
            is_data_driven = True

    # Check 2: many missing submetrics in one pillar but not others
    missing_counts = {
        pname: pdata.get("missing_count", 0)
        for pname, pdata in pillars.items()
    }
    if missing_counts:
        max_missing = max(missing_counts.values())
        min_missing = min(missing_counts.values())
        if max_missing >= 3 and min_missing == 0:
            worst_pillar = [k for k, v in missing_counts.items() if v == max_missing][0]
            suspected_causes.append(
                f"Pillar '{worst_pillar}' has {max_missing} missing submetrics "
                f"while others have {min_missing} — potential data feed issue"
            )
            is_data_driven = True

    # Check 3: benchmark data missing for some pillars
    participation = pillars.get("participation_breadth", {})
    leadership = pillars.get("leadership_quality", {})
    part_has_ew = any(
        sm.get("name") == "equal_weight_confirmation" and sm.get("status") == "valid"
        for sm in participation.get("submetrics", [])
    )
    lead_has_ew = any(
        sm.get("name") == "ew_vs_cw_relative" and sm.get("status") == "valid"
        for sm in leadership.get("submetrics", [])
    )
    if part_has_ew != lead_has_ew:
        suspected_causes.append(
            "Benchmark data inconsistency: EW data available for "
            f"{'participation' if part_has_ew else 'leadership'} "
            f"but not {'leadership' if part_has_ew else 'participation'}"
        )
        is_data_driven = True

    # ── Classify severity ────────────────────────────────────────
    if score_std <= 15:
        # Mild disagreement — normal
        severity = SEVERITY_LOW
        confidence_penalty = 0.0
        warning = None
    elif is_data_driven:
        # Disagreement looks data-driven — escalate
        severity = SEVERITY_HIGH
        confidence_penalty = min((score_std - 15) * 1.5, 20)
        warning = build_warning(
            severity=SEVERITY_HIGH,
            category=CAT_DISAGREEMENT,
            code="CROSS_PILLAR_DISAGREEMENT_DATA_DRIVEN",
            message=(
                f"Cross-pillar disagreement (std={score_std:.1f}) appears "
                f"data-driven rather than market-driven. "
                f"Suspected causes: {'; '.join(suspected_causes)}"
            ),
            impact=(
                "Composite score may be unreliable because pillar "
                "disagreement stems from data quality issues rather "
                "than genuine market conditions."
            ),
            recommended_action=(
                "Investigate data feed completeness and freshness. "
                "Resolve observation count disparities before trusting composite."
            ),
        )
    else:
        # Genuine market signal conflict
        severity = SEVERITY_MEDIUM
        confidence_penalty = min((score_std - 15) * 1.0, 15)
        warning = build_warning(
            severity=SEVERITY_MEDIUM,
            category=CAT_DISAGREEMENT,
            code="CROSS_PILLAR_DISAGREEMENT_SIGNAL",
            message=(
                f"Pillars disagree materially (std={score_std:.1f}, "
                f"range={score_range:.0f}), which lowers confidence but "
                f"may reflect genuine mixed internal market conditions "
                f"rather than bad data."
            ),
            impact=(
                "Composite score blends conflicting signals. Individual "
                "pillar scores may be more informative than the composite."
            ),
            recommended_action=(
                "Review individual pillar scores. Consider which pillar "
                "context is most relevant to your trading thesis."
            ),
        )

    logger.info(
        "event=disagreement_analysis std=%.1f range=%.0f severity=%s "
        "is_data_driven=%s causes=%d penalty=%.1f",
        score_std, score_range, severity, is_data_driven,
        len(suspected_causes), confidence_penalty,
    )

    return {
        "disagreement_level": round(score_std, 2),
        "severity": severity,
        "is_data_driven": is_data_driven,
        "suspected_causes": suspected_causes,
        "warning": warning,
        "confidence_penalty": confidence_penalty,
    }


# ═══════════════════════════════════════════════════════════════════════
# COMPLETENESS WARNINGS
# ═══════════════════════════════════════════════════════════════════════

def assess_data_completeness(
    pillars: dict[str, dict[str, Any]],
    universe_meta: dict[str, Any],
) -> list[dict[str, str]]:
    """Generate structured warnings for data completeness issues.

    Separates real completeness gaps from scaffolded/deferred metrics.
    """
    warnings: list[dict[str, str]] = []

    # ── Universe coverage ────────────────────────────────────────
    expected = universe_meta.get("expected_count", 0)
    actual = universe_meta.get("actual_count", 0)
    if expected > 0:
        coverage = actual / expected
        if coverage < 0.50:
            warnings.append(build_warning(
                severity=SEVERITY_HIGH,
                category=CAT_COVERAGE,
                code="SEVERE_UNIVERSE_COVERAGE_GAP",
                message=f"Universe coverage critically low: {actual}/{expected} ({coverage:.0%})",
                impact="Breadth scores may not represent the true market",
                recommended_action="Verify data feed connectivity and ticker availability",
            ))
        elif coverage < 0.75:
            warnings.append(build_warning(
                severity=SEVERITY_HIGH,
                category=CAT_COVERAGE,
                code="LOW_UNIVERSE_COVERAGE",
                message=f"Universe coverage below target: {actual}/{expected} ({coverage:.0%})",
                impact="Reduced representativeness of breadth measurements",
                recommended_action="Check for failed quote batches or stale tickers",
            ))
        elif coverage < 0.90:
            warnings.append(build_warning(
                severity=SEVERITY_MEDIUM,
                category=CAT_COVERAGE,
                code="MODERATE_UNIVERSE_COVERAGE_GAP",
                message=f"Universe coverage: {actual}/{expected} ({coverage:.0%})",
                impact="Minor reduction in breadth measurement precision",
                recommended_action="Acceptable for v1; monitor for degradation",
            ))

    # ── Per-pillar completeness ──────────────────────────────────
    for pname, pdata in pillars.items():
        readable = pname.replace("_", " ").title()
        missing_count = pdata.get("missing_count", 0)

        for sm in pdata.get("submetrics", []):
            sm_name = sm.get("name", "")
            if sm.get("status") != "unavailable":
                continue

            # Check if this is a scaffolded metric
            if is_scaffolded_metric(sm_name):
                scaffold_info = SCAFFOLDED_METRICS[sm_name]
                warnings.append(build_warning(
                    severity=SEVERITY_INFO,
                    category=CAT_SCAFFOLD,
                    code=f"SCAFFOLD_{sm_name.upper()}",
                    message=f"{scaffold_info['description']} — not yet implemented",
                    impact=scaffold_info["impact"],
                    recommended_action="Planned enhancement; does not affect current scoring weights",
                ))
            else:
                # Real missing data — classify severity
                if "benchmark" in sm_name or "equal_weight" in sm_name or "ew_" in sm_name:
                    sev = SEVERITY_HIGH
                    code_prefix = "MISSING_BENCHMARK"
                elif missing_count >= 3:
                    sev = SEVERITY_HIGH
                    code_prefix = "MAJOR_PILLAR_INCOMPLETENESS"
                else:
                    sev = SEVERITY_MEDIUM
                    code_prefix = "MISSING_SUBMETRIC"

                warnings.append(build_warning(
                    severity=sev,
                    category=CAT_COMPLETENESS,
                    code=f"{code_prefix}_{sm_name.upper()}",
                    message=f"{readable}: {sm_name} data unavailable",
                    impact=f"Pillar '{readable}' computed from fewer submetrics, reducing precision",
                    recommended_action="Check data feed for missing inputs",
                ))

    # ── Benchmark availability ───────────────────────────────────
    participation = pillars.get("participation_breadth", {})
    has_ew = any(
        sm.get("name") == "equal_weight_confirmation" and sm.get("status") == "valid"
        for sm in participation.get("submetrics", [])
    )
    if not has_ew:
        warnings.append(build_warning(
            severity=SEVERITY_HIGH,
            category=CAT_METHODOLOGY,
            code="MISSING_EW_BENCHMARK",
            message="Equal-weight benchmark data unavailable",
            impact="Cannot confirm whether rally is broad or narrow via EW/CW comparison",
            recommended_action="Ensure RSP (equal-weight ETF) quote data is available",
        ))

    return warnings


# ═══════════════════════════════════════════════════════════════════════
# CONFIDENCE / VALIDITY SCORING
# ═══════════════════════════════════════════════════════════════════════

def compute_quality_scores(
    pillars: dict[str, dict[str, Any]],
    universe_meta: dict[str, Any],
    *,
    is_historical_mode: bool = False,
) -> dict[str, Any]:
    """Compute separated confidence, data quality, and historical validity scores.

    Returns
    -------
    dict with:
      confidence_score: float (0-100) — trust in current signal quality
      data_quality_score: float (0-100) — trust in data completeness/consistency
      historical_validity_score: float (0-100) — trust in historical/backtest integrity
      signal_quality: str — "high" | "medium" | "low"
      penalties: list[dict] — detailed penalty breakdown
      survivorship: dict — survivorship risk assessment
      disagreement: dict — disagreement analysis
      structured_warnings: list[dict] — all structured warnings sorted by severity
    """
    confidence = 100.0
    data_quality = 100.0
    historical_validity = 100.0
    penalties: list[dict[str, Any]] = []
    all_warnings: list[dict[str, str]] = []

    # ── 1. Data completeness ─────────────────────────────────────
    total_submetrics = 0
    missing_submetrics = 0
    scaffolded_count = 0

    for pname, pdata in pillars.items():
        for sm in pdata.get("submetrics", []):
            total_submetrics += 1
            if sm.get("status") in ("unavailable", None):
                if is_scaffolded_metric(sm.get("name", "")):
                    scaffolded_count += 1
                else:
                    missing_submetrics += 1

    # Only penalize real missing data, not scaffolded metrics
    if total_submetrics > 0:
        real_total = total_submetrics - scaffolded_count
        if real_total > 0:
            missing_pct = missing_submetrics / real_total
            data_penalty = missing_pct * 30
            if data_penalty > 0:
                confidence -= data_penalty
                data_quality -= data_penalty
                penalties.append({
                    "factor": "data_completeness",
                    "confidence_penalty": round(data_penalty, 1),
                    "data_quality_penalty": round(data_penalty, 1),
                    "detail": (
                        f"{missing_submetrics}/{real_total} active submetrics "
                        f"unavailable (excludes {scaffolded_count} scaffolded)"
                    ),
                })
                logger.info(
                    "event=quality_penalty factor=data_completeness "
                    "missing=%d real_total=%d scaffolded=%d penalty=%.1f",
                    missing_submetrics, real_total, scaffolded_count, data_penalty,
                )

    # ── 2. Universe coverage ─────────────────────────────────────
    expected = universe_meta.get("expected_count", 0)
    actual = universe_meta.get("actual_count", 0)
    if expected > 0:
        coverage = actual / expected
        if coverage < 0.90:
            cov_penalty = (1 - coverage) * 40
            confidence -= cov_penalty
            data_quality -= cov_penalty
            historical_validity -= cov_penalty * 0.5
            penalties.append({
                "factor": "universe_coverage",
                "confidence_penalty": round(cov_penalty, 1),
                "data_quality_penalty": round(cov_penalty, 1),
                "detail": f"Universe coverage: {actual}/{expected} ({coverage:.0%})",
            })
            logger.info(
                "event=quality_penalty factor=universe_coverage "
                "actual=%d expected=%d coverage=%.2f penalty=%.1f",
                actual, expected, coverage, cov_penalty,
            )

    # ── 3. Cross-pillar disagreement ─────────────────────────────
    disagreement = analyze_disagreement(pillars, universe_meta)
    if disagreement["warning"]:
        all_warnings.append(disagreement["warning"])
    if disagreement["confidence_penalty"] > 0:
        confidence -= disagreement["confidence_penalty"]
        penalties.append({
            "factor": "cross_pillar_disagreement",
            "confidence_penalty": round(disagreement["confidence_penalty"], 1),
            "data_quality_penalty": round(
                disagreement["confidence_penalty"] * 0.5
                if disagreement["is_data_driven"] else 0, 1
            ),
            "detail": (
                f"Pillar std={disagreement['disagreement_level']:.1f}, "
                f"data_driven={disagreement['is_data_driven']}"
            ),
        })

    # ── 4. Survivorship bias ─────────────────────────────────────
    survivorship = assess_survivorship_risk(
        universe_meta, is_historical_mode=is_historical_mode
    )
    if survivorship["warning"]:
        all_warnings.append(survivorship["warning"])
    if survivorship["confidence_penalty"] > 0:
        confidence -= survivorship["confidence_penalty"]
        penalties.append({
            "factor": "survivorship_bias",
            "confidence_penalty": round(survivorship["confidence_penalty"], 1),
            "historical_validity_penalty": round(
                survivorship["historical_validity_penalty"], 1
            ),
            "detail": (
                f"Point-in-time={survivorship['point_in_time_available']}, "
                f"historical_mode={is_historical_mode}"
            ),
        })
    historical_validity -= survivorship["historical_validity_penalty"]

    # ── 5. Pillar unavailability ─────────────────────────────────
    unavailable_pillars = [
        pname for pname, pdata in pillars.items()
        if pdata.get("score") is None
    ]
    if unavailable_pillars:
        pillar_penalty = len(unavailable_pillars) * 10
        confidence -= pillar_penalty
        data_quality -= pillar_penalty * 0.8
        historical_validity -= pillar_penalty * 0.5
        penalties.append({
            "factor": "pillar_unavailability",
            "confidence_penalty": round(pillar_penalty, 1),
            "data_quality_penalty": round(pillar_penalty * 0.8, 1),
            "detail": f"Unavailable pillars: {', '.join(unavailable_pillars)}",
        })
        for pname in unavailable_pillars:
            all_warnings.append(build_warning(
                severity=SEVERITY_HIGH,
                category=CAT_COMPLETENESS,
                code=f"PILLAR_UNAVAILABLE_{pname.upper()}",
                message=f"Pillar '{pname.replace('_', ' ').title()}' fully unavailable",
                impact="Composite score computed from fewer pillars, reducing reliability",
                recommended_action="Check data inputs for this pillar",
            ))

    # ── 6. Completeness warnings ─────────────────────────────────
    completeness_warnings = assess_data_completeness(pillars, universe_meta)
    all_warnings.extend(completeness_warnings)

    # ── Clamp scores ─────────────────────────────────────────────
    confidence = round(max(0, min(100, confidence)), 2)
    data_quality = round(max(0, min(100, data_quality)), 2)
    historical_validity = round(max(0, min(100, historical_validity)), 2)

    # ── Signal quality label ─────────────────────────────────────
    if confidence >= 80:
        signal_quality = "high"
    elif confidence >= 60:
        signal_quality = "medium"
    else:
        signal_quality = "low"

    # ── Sort warnings by severity ────────────────────────────────
    all_warnings.sort(key=lambda w: SEVERITY_ORDER.get(w.get("severity", "info"), 99))

    logger.info(
        "event=quality_scores_computed confidence=%.1f data_quality=%.1f "
        "historical_validity=%.1f signal_quality=%s warnings=%d penalties=%d",
        confidence, data_quality, historical_validity, signal_quality,
        len(all_warnings), len(penalties),
    )

    return {
        "confidence_score": confidence,
        "data_quality_score": data_quality,
        "historical_validity_score": historical_validity,
        "signal_quality": signal_quality,
        "penalties": penalties,
        "survivorship": {
            "point_in_time_available": survivorship["point_in_time_available"],
            "survivorship_bias_risk": survivorship["survivorship_bias_risk"],
            "historical_validity_degraded": survivorship["historical_validity_degraded"],
        },
        "disagreement": {
            "level": disagreement["disagreement_level"],
            "severity": disagreement["severity"],
            "is_data_driven": disagreement["is_data_driven"],
            "suspected_causes": disagreement["suspected_causes"],
        },
        "structured_warnings": all_warnings,
    }


# ═══════════════════════════════════════════════════════════════════════
# WARNING GROUPING (for UI consumption)
# ═══════════════════════════════════════════════════════════════════════

def group_warnings_for_ui(
    structured_warnings: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    """Group structured warnings into UI sections.

    Returns dict with keys:
      structural_risks — critical + high data_integrity/coverage/methodology
      completeness_issues — medium+ completeness warnings
      signal_notes — disagreement and interpretation warnings
      deferred_enhancements — info/scaffold items
    """
    structural: list[dict[str, str]] = []
    completeness: list[dict[str, str]] = []
    signal_notes: list[dict[str, str]] = []
    deferred: list[dict[str, str]] = []

    for w in structured_warnings:
        sev = w.get("severity", "info")
        cat = w.get("category", "")

        if cat == CAT_SCAFFOLD or sev == SEVERITY_INFO:
            deferred.append(w)
        elif cat == CAT_DISAGREEMENT:
            signal_notes.append(w)
        elif sev in (SEVERITY_CRITICAL, SEVERITY_HIGH) and cat in (
            CAT_DATA_INTEGRITY, CAT_COVERAGE, CAT_METHODOLOGY
        ):
            structural.append(w)
        elif cat in (CAT_COMPLETENESS, CAT_COVERAGE):
            completeness.append(w)
        else:
            # Default: treat as signal note
            signal_notes.append(w)

    logger.debug(
        "event=warnings_grouped structural=%d completeness=%d "
        "signal=%d deferred=%d",
        len(structural), len(completeness),
        len(signal_notes), len(deferred),
    )

    return {
        "structural_risks": structural,
        "completeness_issues": completeness,
        "signal_notes": signal_notes,
        "deferred_enhancements": deferred,
    }
