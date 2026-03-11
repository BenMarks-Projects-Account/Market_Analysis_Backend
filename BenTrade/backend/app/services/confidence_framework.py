"""Uncertainty / Confidence Framework v1.1

Centralises confidence normalisation, label derivation, uncertainty
assessment, and impact-based penalty handling for every BenTrade
decision layer.

Design principles
-----------------
1. **One canonical scale** — 0.0–1.0 float (0 = no confidence, 1 = full).
   Legacy 0–100 inputs are accepted and converted automatically.
2. **Deterministic labels** from score thresholds — no magic.
3. **Structured impacts** — freshness, quality, conflict, coverage,
   fallback each contribute named penalty records with reasons.
4. **Uncertainty ≠ confidence ≠ conviction** — three distinct dimensions.
   - *confidence* = how trustworthy / well-supported an assessment is
   - *uncertainty* = why confidence is limited or what weakens it
   - *conviction* = action-oriented strength of the final decision output
5. **Backward-compatible** — layers can adopt incrementally; legacy
   fields remain readable.

Changelog
---------
v1.1 – expanded downstream adoption
    - PENALTY_TABLES consolidated export for inspectability.
    - market_composite now imports penalty tables from this module.
    - decision_prompt_payload fallback path gets structured assessment.
    - Version bump from 1.0 → 1.1.

Public API
----------
normalize_confidence(raw)
    Convert any numeric confidence value to canonical 0.0–1.0.

confidence_label(score)
    Derive "high" / "moderate" / "low" / "none" from a 0.0–1.0 score.

signal_quality_label(score)
    Derive "high" / "medium" / "low" from a 0.0–1.0 score.

uncertainty_level(score)
    Derive "low" / "moderate" / "high" / "very_high" from a 0.0–1.0
    uncertainty score (inverse of confidence, with impacts).

build_confidence_assessment(...)
    Full builder: score + impacts → structured dict.

aggregate_impacts(impacts)
    Sum impacts → total penalty + reasons list.

apply_impacts(base_score, impacts)
    Reduce base score by impact penalties → clamped result.

build_uncertainty_summary(assessment)
    Derive uncertainty section from a completed confidence assessment.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ── Version lock ─────────────────────────────────────────────────────────
_FRAMEWORK_VERSION = "1.1"

# ── Confidence label thresholds (applied to 0.0–1.0 normalised score) ───
# ≥ 0.80 → high
# ≥ 0.60 → moderate
# ≥ 0.30 → low
# < 0.30 → none
_LABEL_THRESHOLDS: list[tuple[float, str]] = [
    (0.80, "high"),
    (0.60, "moderate"),
    (0.30, "low"),
    (0.00, "none"),
]

# ── Signal-quality label thresholds (matches engine convention) ──────────
# ≥ 0.80 → high
# ≥ 0.60 → medium
# < 0.60 → low
_SIGNAL_QUALITY_THRESHOLDS: list[tuple[float, str]] = [
    (0.80, "high"),
    (0.60, "medium"),
    (0.00, "low"),
]

# ── Uncertainty level thresholds (applied to uncertainty score) ──────────
# uncertainty = 1.0 - adjusted_confidence + impact_contribution
# ≤ 0.20 → low
# ≤ 0.40 → moderate
# ≤ 0.65 → high
# > 0.65 → very_high
_UNCERTAINTY_THRESHOLDS: list[tuple[float, str]] = [
    (0.65, "very_high"),
    (0.40, "high"),
    (0.20, "moderate"),
    (0.00, "low"),
]

# ── Valid impact categories ──────────────────────────────────────────────
VALID_IMPACT_CATEGORIES = frozenset({
    "freshness",
    "quality",
    "conflict",
    "coverage",
    "fallback",
    "proxy",
    "data_gap",
    "model_recovery",
    "stale_source",
    "readiness",
})

# ── Preset penalty tables ───────────────────────────────────────────────
# These centralise the per-status penalties that were scattered across
# market_composite, context_assembler, engines, etc.

QUALITY_PENALTIES: dict[str, float] = {
    "good":        0.00,
    "acceptable":  0.00,
    "degraded":    0.15,
    "poor":        0.30,
    "unavailable": 0.40,
    "unknown":     0.10,
}

FRESHNESS_PENALTIES: dict[str, float] = {
    "live":       0.00,
    "recent":     0.00,
    "stale":      0.10,
    "very_stale": 0.25,
    "unknown":    0.05,
}

CONFLICT_PENALTIES: dict[str, float] = {
    "none": 0.00,
    "low":  0.05,
    "moderate": 0.15,
    "high": 0.30,
}

COVERAGE_PENALTIES: dict[str, float] = {
    "full":    0.00,
    "high":    0.02,
    "partial": 0.10,
    "minimal": 0.25,
    "none":    0.40,
    "sparse":  0.15,
}

# Consolidated export for external inspectability / calibration audit.
PENALTY_TABLES: dict[str, dict[str, float]] = {
    "quality":   QUALITY_PENALTIES,
    "freshness": FRESHNESS_PENALTIES,
    "conflict":  CONFLICT_PENALTIES,
    "coverage":  COVERAGE_PENALTIES,
}


# =====================================================================
#  normalize_confidence
# =====================================================================

def normalize_confidence(raw: Any) -> float | None:
    """Convert any numeric confidence value to canonical 0.0–1.0.

    Accepts:
    - 0.0–1.0 float → pass through
    - 0–100 int/float → divide by 100
    - None / non-numeric → return None
    - Negative → clamp to 0.0; >1.0 and ≤100 → divide by 100; >100 → clamp 1.0

    Returns
    -------
    float in [0.0, 1.0] or None if input is unusable.
    """
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None

    if val != val:  # NaN check
        return None

    if val < 0.0:
        return 0.0
    if val <= 1.0:
        return round(val, 4)
    if val <= 100.0:
        return round(val / 100.0, 4)
    return 1.0


# =====================================================================
#  Label derivation
# =====================================================================

def confidence_label(score: float | None) -> str:
    """Derive a human label from a 0.0–1.0 confidence score.

    Returns one of: "high", "moderate", "low", "none".
    """
    if score is None or not _is_finite(score):
        return "none"
    for threshold, label in _LABEL_THRESHOLDS:
        if score >= threshold:
            return label
    return "none"


def signal_quality_label(score: float | None) -> str:
    """Derive signal quality from a 0.0–1.0 confidence score.

    Returns one of: "high", "medium", "low".
    Matches the existing engine convention.
    """
    if score is None or not _is_finite(score):
        return "low"
    for threshold, label in _SIGNAL_QUALITY_THRESHOLDS:
        if score >= threshold:
            return label
    return "low"


def uncertainty_level(uncertainty_score: float | None) -> str:
    """Derive an uncertainty level from a 0.0–1.0 uncertainty score.

    Returns one of: "low", "moderate", "high", "very_high".
    """
    if uncertainty_score is None or not _is_finite(uncertainty_score):
        return "very_high"
    for threshold, label in _UNCERTAINTY_THRESHOLDS:
        if uncertainty_score >= threshold:
            return label
    return "low"


# =====================================================================
#  Impact records
# =====================================================================

def make_impact(
    category: str,
    penalty: float,
    reason: str,
    *,
    source: str = "",
) -> dict[str, Any]:
    """Create a structured confidence impact record.

    Parameters
    ----------
    category : str
        One of VALID_IMPACT_CATEGORIES (e.g. "freshness", "quality").
    penalty : float
        Penalty magnitude in 0.0–1.0 range to subtract from confidence.
    reason : str
        Human-readable explanation of why confidence is reduced.
    source : str
        Optional originating module/engine name.
    """
    return {
        "category": str(category),
        "penalty": max(0.0, min(1.0, float(penalty))),
        "reason": str(reason),
        "source": str(source) if source else "",
    }


def impact_from_quality(quality_status: str, *, source: str = "") -> dict[str, Any] | None:
    """Create a quality impact from a data_quality_status string."""
    status = str(quality_status).lower().strip() if quality_status else "unknown"
    penalty = QUALITY_PENALTIES.get(status, QUALITY_PENALTIES.get("unknown", 0.10))
    if penalty <= 0.0:
        return None
    return make_impact("quality", penalty, f"data quality: {status}", source=source)


def impact_from_freshness(freshness_status: str, *, source: str = "") -> dict[str, Any] | None:
    """Create a freshness impact from a freshness_status string."""
    status = str(freshness_status).lower().strip() if freshness_status else "unknown"
    penalty = FRESHNESS_PENALTIES.get(status, FRESHNESS_PENALTIES.get("unknown", 0.05))
    if penalty <= 0.0:
        return None
    return make_impact("freshness", penalty, f"freshness: {status}", source=source)


def impact_from_conflict(conflict_severity: str, *, source: str = "") -> dict[str, Any] | None:
    """Create a conflict impact from a conflict_severity string."""
    sev = str(conflict_severity).lower().strip() if conflict_severity else "none"
    penalty = CONFLICT_PENALTIES.get(sev, 0.0)
    if penalty <= 0.0:
        return None
    return make_impact("conflict", penalty, f"conflict severity: {sev}", source=source)


def impact_from_coverage(coverage_level: str, *, source: str = "") -> dict[str, Any] | None:
    """Create a coverage impact from a coverage_level string."""
    lvl = str(coverage_level).lower().strip() if coverage_level else "none"
    penalty = COVERAGE_PENALTIES.get(lvl, 0.0)
    if penalty <= 0.0:
        return None
    return make_impact("coverage", penalty, f"coverage: {lvl}", source=source)


# =====================================================================
#  Impact aggregation
# =====================================================================

def aggregate_impacts(
    impacts: list[dict[str, Any]] | None,
) -> tuple[float, list[str]]:
    """Sum impact penalties and collect reasons.

    Returns
    -------
    (total_penalty, reasons) : tuple[float, list[str]]
        total_penalty clamped to [0.0, 1.0].
    """
    if not impacts:
        return 0.0, []
    total = 0.0
    reasons: list[str] = []
    for imp in impacts:
        if not isinstance(imp, dict):
            continue
        p = imp.get("penalty", 0.0)
        if isinstance(p, (int, float)) and p > 0:
            total += p
        r = imp.get("reason", "")
        if r:
            reasons.append(str(r))
    return min(total, 1.0), reasons


def apply_impacts(
    base_score: float,
    impacts: list[dict[str, Any]] | None,
) -> float:
    """Subtract aggregated impact penalties from base_score.

    Returns clamped 0.0–1.0 result.
    """
    if base_score is None or not _is_finite(base_score):
        base_score = 0.0
    total, _ = aggregate_impacts(impacts)
    return max(0.0, min(1.0, round(base_score - total, 4)))


# =====================================================================
#  build_confidence_assessment
# =====================================================================

def build_confidence_assessment(
    *,
    raw_confidence: Any = None,
    base_score: float | None = None,
    quality_status: str | None = None,
    freshness_status: str | None = None,
    conflict_severity: str | None = None,
    coverage_level: str | None = None,
    extra_impacts: list[dict[str, Any]] | None = None,
    source: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete confidence assessment with impacts and reasons.

    This is the primary entry point for layers that want a structured
    confidence/uncertainty block.

    Parameters
    ----------
    raw_confidence : numeric | None
        Incoming confidence (any scale).  Normalised to 0.0–1.0.
    base_score : float | None
        Explicit 0.0–1.0 base score.  Overrides raw_confidence if given.
    quality_status : str | None
        Data quality status (good/acceptable/degraded/poor/unavailable).
    freshness_status : str | None
        Freshness status (live/recent/stale/very_stale/unknown).
    conflict_severity : str | None
        Conflict severity (none/low/moderate/high).
    coverage_level : str | None
        Coverage level (full/high/partial/minimal/none/sparse).
    extra_impacts : list | None
        Additional impact dicts (e.g. fallback, proxy, model_recovery).
    source : str
        Originating module name for traceability.
    context : dict | None
        Extra context to embed in assessment metadata.

    Returns
    -------
    dict — structured confidence assessment with:
        framework_version, generated_at, source,
        raw_input, base_score, adjusted_score,
        confidence_label, signal_quality,
        impacts (list), total_penalty,
        confidence_reasons, uncertainty_reasons,
        uncertainty_score, uncertainty_level,
        context
    """
    # Resolve base score
    if base_score is not None:
        bs = normalize_confidence(base_score)
    else:
        bs = normalize_confidence(raw_confidence)
    if bs is None:
        bs = 0.0

    # Collect impacts
    impacts: list[dict[str, Any]] = []

    imp = impact_from_quality(quality_status, source=source) if quality_status else None
    if imp:
        impacts.append(imp)

    imp = impact_from_freshness(freshness_status, source=source) if freshness_status else None
    if imp:
        impacts.append(imp)

    imp = impact_from_conflict(conflict_severity, source=source) if conflict_severity else None
    if imp:
        impacts.append(imp)

    imp = impact_from_coverage(coverage_level, source=source) if coverage_level else None
    if imp:
        impacts.append(imp)

    if extra_impacts:
        for ei in extra_impacts:
            if isinstance(ei, dict) and ei.get("penalty", 0) > 0:
                impacts.append(ei)

    # Compute adjusted score
    adjusted = apply_impacts(bs, impacts)
    total_penalty, all_reasons = aggregate_impacts(impacts)

    # Derive labels
    c_label = confidence_label(adjusted)
    sq_label = signal_quality_label(adjusted)

    # Derive uncertainty
    u_score = round(1.0 - adjusted, 4)
    u_level = uncertainty_level(u_score)

    # Build confidence reasons (why confidence IS at this level)
    confidence_reasons: list[str] = []
    if bs >= 0.80 and adjusted >= 0.80:
        confidence_reasons.append("strong base confidence with minimal degradation")
    elif bs >= 0.60:
        confidence_reasons.append(f"moderate base confidence ({bs:.2f})")
    else:
        confidence_reasons.append(f"weak base confidence ({bs:.2f})")

    if total_penalty > 0:
        confidence_reasons.append(
            f"total penalty of {total_penalty:.2f} applied from {len(impacts)} impact(s)"
        )
    if total_penalty == 0 and bs >= 0.60:
        confidence_reasons.append("no degradation impacts detected")

    # Build uncertainty reasons (why uncertainty IS at this level)
    uncertainty_reasons: list[str] = []
    if len(all_reasons) == 0 and adjusted >= 0.60:
        uncertainty_reasons.append("all inputs healthy — low uncertainty")
    else:
        uncertainty_reasons.extend(all_reasons)
    if adjusted < 0.30:
        uncertainty_reasons.append("adjusted confidence below minimum threshold")

    now_iso = datetime.now(timezone.utc).isoformat()

    return {
        "framework_version": _FRAMEWORK_VERSION,
        "generated_at": now_iso,
        "source": str(source) if source else "",
        "raw_input": raw_confidence,
        "base_score": bs,
        "adjusted_score": adjusted,
        "confidence_label": c_label,
        "signal_quality": sq_label,
        "impacts": impacts,
        "total_penalty": round(total_penalty, 4),
        "confidence_reasons": confidence_reasons,
        "uncertainty_reasons": uncertainty_reasons,
        "uncertainty_score": u_score,
        "uncertainty_level": u_level,
        "context": dict(context) if isinstance(context, dict) else {},
    }


# =====================================================================
#  build_uncertainty_summary (compact extract)
# =====================================================================

def build_uncertainty_summary(
    assessment: dict[str, Any] | None,
) -> dict[str, Any]:
    """Extract a compact uncertainty summary from a confidence assessment.

    Useful when layers want to embed just the uncertainty portion
    without the full assessment.
    """
    if not isinstance(assessment, dict):
        return {
            "uncertainty_score": 1.0,
            "uncertainty_level": "very_high",
            "uncertainty_reasons": ["assessment unavailable"],
            "confidence_label": "none",
            "adjusted_score": 0.0,
        }
    return {
        "uncertainty_score": assessment.get("uncertainty_score", 1.0),
        "uncertainty_level": assessment.get("uncertainty_level", "very_high"),
        "uncertainty_reasons": assessment.get("uncertainty_reasons", []),
        "confidence_label": assessment.get("confidence_label", "none"),
        "adjusted_score": assessment.get("adjusted_score", 0.0),
    }


# =====================================================================
#  Convenience: quick_assess (one-liner for common case)
# =====================================================================

def quick_assess(
    raw_confidence: Any,
    *,
    quality: str | None = None,
    freshness: str | None = None,
    conflict: str | None = None,
    coverage: str | None = None,
    source: str = "",
) -> dict[str, Any]:
    """Shorthand for build_confidence_assessment with keyword args."""
    return build_confidence_assessment(
        raw_confidence=raw_confidence,
        quality_status=quality,
        freshness_status=freshness,
        conflict_severity=conflict,
        coverage_level=coverage,
        source=source,
    )


# =====================================================================
#  Internal helpers
# =====================================================================

def _is_finite(val: Any) -> bool:
    """Return True if val is a finite number."""
    try:
        f = float(val)
        return f == f and f != float("inf") and f != float("-inf")
    except (TypeError, ValueError):
        return False
