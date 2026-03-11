"""
Market Composite Summary v1.1
==============================

Reusable synthesis-layer module that distils assembled market context
(from ``context_assembler.assemble_context``) plus an optional conflict
report (from ``conflict_detector.detect_conflicts``) into a compact
world-state assessment.

Output dimensions
-----------------
1. **market_state**    – ``risk_on`` | ``neutral`` | ``risk_off``
2. **support_state**   – ``supportive`` | ``mixed`` | ``fragile``
3. **stability_state** – ``orderly`` | ``noisy`` | ``unstable``

Each dimension includes a confidence score (0.0–1.0) and an evidence
dict listing the inputs that drove the conclusion.

Output contract
---------------
``build_market_composite(assembled, conflict_report=None)`` returns::

    {
        "composite_version":   "1.1",
        "computed_at":         ISO-8601 str,
        "status":              "ok" | "degraded" | "insufficient_data",

        "market_state":        "risk_on" | "neutral" | "risk_off",
        "support_state":       "supportive" | "mixed" | "fragile",
        "stability_state":     "orderly" | "noisy" | "unstable",

        "confidence":          float (0.0 – 1.0),

        "evidence": {
            "market_state":    {...},
            "support_state":   {...},
            "stability_state": {...},
        },

        "adjustments": {
            "conflict_adjustment":  {...} | None,
            "quality_adjustment":   {...} | None,
            "horizon_adjustment":   {...} | None,
        },

        "summary":             str,   # 1-2 sentence human-readable summary

        "metadata": {
            "composite_version":      "1.0",
            "engines_used":           int,
            "conflict_count":         int,
            "conflict_severity":      str,
            "overall_quality":        str,
            "overall_freshness":      str,
            "horizon_span":           str | None,
        },
    }
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from app.services.confidence_framework import (
    CONFLICT_PENALTIES,
    FRESHNESS_PENALTIES,
    QUALITY_PENALTIES,
    build_confidence_assessment,
    make_impact,
)
from app.utils.time_horizon import HORIZON_ORDER, horizon_rank
from app.utils.tone_classification import (
    classify_label as _classify_label,
    classify_score as _classify_score,
    engine_tone as _engine_tone,
)

# ── Constants ────────────────────────────────────────────────────────

_COMPOSITE_VERSION = "1.1"

# Vocabulary
MARKET_STATES = frozenset({"risk_on", "neutral", "risk_off"})
SUPPORT_STATES = frozenset({"supportive", "mixed", "fragile"})
STABILITY_STATES = frozenset({"orderly", "noisy", "unstable"})

# Tone classification — imported from app.utils.tone_classification
# (helpers available as _classify_label, _classify_score, _engine_tone)

# Quality priority (worst wins), mirrors context_assembler logic
_QUALITY_RANK = {
    "unavailable": 0, "poor": 1, "degraded": 2,
    "acceptable": 3, "good": 4, "unknown": -1,
}

# Confidence penalties sourced from the shared confidence_framework.
# Aliases preserve existing lookup patterns throughout this module.
_QUALITY_PENALTY = QUALITY_PENALTIES
_FRESHNESS_PENALTY = FRESHNESS_PENALTIES
_CONFLICT_SEVERITY_PENALTY = CONFLICT_PENALTIES


# ── Public API ───────────────────────────────────────────────────────


def build_market_composite(
    assembled: dict[str, Any],
    conflict_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact market composite summary.

    Parameters
    ----------
    assembled : dict
        Output of ``context_assembler.assemble_context()``.
    conflict_report : dict | None
        Output of ``conflict_detector.detect_conflicts(assembled)``.
        When *None* the composite is built without conflict awareness.

    Returns
    -------
    dict – composite summary conforming to the output contract above.
    """
    market_ctx = assembled.get("market_context") or {}
    quality_sum = assembled.get("quality_summary") or {}
    freshness_sum = assembled.get("freshness_summary") or {}
    horizon_sum = assembled.get("horizon_summary") or {}

    engines_used = len(market_ctx)

    # ── Bail early when there is nothing to work with ────────────
    if engines_used == 0:
        return _empty_output()

    # ── Step 1 — derive per-engine tones ─────────────────────────
    tones = _collect_engine_tones(market_ctx)
    tone_counts = _count_tones(tones)

    # ── Step 2 — determine raw market_state ──────────────────────
    market_state, market_evidence = _derive_market_state(tone_counts, tones)

    # ── Step 3 — determine raw support_state ─────────────────────
    support_state, support_evidence = _derive_support_state(
        tone_counts, quality_sum, market_ctx,
    )

    # ── Step 4 — determine raw stability_state ───────────────────
    stability_state, stability_evidence = _derive_stability_state(
        tone_counts, conflict_report,
    )

    # ── Step 5 — base confidence ─────────────────────────────────
    confidence = _compute_base_confidence(tone_counts, engines_used)

    # ── Step 6 — adjustments ─────────────────────────────────────
    conflict_adj = _apply_conflict_adjustment(
        conflict_report, market_state, support_state, stability_state,
    )
    quality_adj = _apply_quality_adjustment(quality_sum, freshness_sum)
    horizon_adj = _apply_horizon_adjustment(horizon_sum)

    # Apply confidence penalties from adjustments
    if conflict_adj:
        confidence -= conflict_adj.get("confidence_penalty", 0.0)
    if quality_adj:
        confidence -= quality_adj.get("confidence_penalty", 0.0)
    if horizon_adj:
        confidence -= horizon_adj.get("confidence_penalty", 0.0)

    confidence = round(max(0.0, min(1.0, confidence)), 2)

    # ── Step 6b — structured confidence assessment (framework v1.1) ──
    # Build from the pre-penalty base confidence + the same inputs the
    # adjustments used, so the assessment captures reasons/uncertainty.
    _extra_impacts: list[dict[str, Any]] = []
    if quality_adj:
        d_pen = quality_adj.get("degraded_penalty", 0.0)
        if d_pen > 0:
            _extra_impacts.append(make_impact(
                "coverage", d_pen,
                f"{quality_adj.get('degraded_count', 0)} engines degraded",
                source="market_composite",
            ))
    if horizon_adj:
        h_pen = horizon_adj.get("confidence_penalty", 0.0)
        if h_pen > 0:
            _extra_impacts.append(make_impact(
                "data_gap", h_pen,
                "wide horizon span across engines",
                source="market_composite",
            ))

    _base_confidence = _compute_base_confidence(tone_counts, engines_used)
    _conf_assessment = build_confidence_assessment(
        base_score=_base_confidence,
        quality_status=quality_sum.get("overall_quality", "unknown"),
        freshness_status=freshness_sum.get("overall_freshness", "unknown"),
        conflict_severity=(
            conflict_report.get("conflict_severity", "none")
            if conflict_report else "none"
        ),
        extra_impacts=_extra_impacts or None,
        source="market_composite",
    )

    # Apply state downgrades from adjustments
    if conflict_adj and conflict_adj.get("stability_downgrade"):
        stability_state = conflict_adj["stability_downgrade"]
    if conflict_adj and conflict_adj.get("support_downgrade"):
        support_state = conflict_adj["support_downgrade"]
    if quality_adj and quality_adj.get("support_downgrade"):
        support_state = quality_adj["support_downgrade"]

    # ── Step 7 — conflict metadata (needed for status) ────────────
    conflict_count = 0
    conflict_severity = "none"
    if conflict_report:
        conflict_count = conflict_report.get("conflict_count", 0)
        conflict_severity = conflict_report.get("conflict_severity", "none")

    # ── Step 8 — assemble status ─────────────────────────────────
    overall_quality = quality_sum.get("overall_quality", "unknown")
    overall_freshness = freshness_sum.get("overall_freshness", "unknown")
    status = _determine_status(
        overall_quality, overall_freshness, engines_used, conflict_severity,
    )

    # ── Step 9 — horizon span label ──────────────────────────────
    shortest = horizon_sum.get("shortest")
    longest = horizon_sum.get("longest")
    if shortest and longest and shortest != longest:
        horizon_span = f"{shortest} → {longest}"
    elif shortest:
        horizon_span = shortest
    else:
        horizon_span = None

    # ── Build output ─────────────────────────────────────────────
    return {
        "composite_version": _COMPOSITE_VERSION,
        "computed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "status": status,
        "market_state": market_state,
        "support_state": support_state,
        "stability_state": stability_state,
        "confidence": confidence,
        "confidence_assessment": _conf_assessment,
        "evidence": {
            "market_state": market_evidence,
            "support_state": support_evidence,
            "stability_state": stability_evidence,
        },
        "adjustments": {
            "conflict_adjustment": conflict_adj,
            "quality_adjustment": quality_adj,
            "horizon_adjustment": horizon_adj,
        },
        "summary": _build_human_summary(
            market_state, support_state, stability_state, confidence, status,
        ),
        "metadata": {
            "composite_version": _COMPOSITE_VERSION,
            "engines_used": engines_used,
            "conflict_count": conflict_count,
            "conflict_severity": conflict_severity,
            "overall_quality": overall_quality,
            "overall_freshness": overall_freshness,
            "horizon_span": horizon_span,
        },
    }


# ── Empty / insufficient data output ────────────────────────────────

def _empty_output() -> dict[str, Any]:
    """Return the contract shape when no market data is available."""
    return {
        "composite_version": _COMPOSITE_VERSION,
        "computed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "status": "insufficient_data",
        "market_state": "neutral",
        "support_state": "fragile",
        "stability_state": "unstable",
        "confidence": 0.0,
        "confidence_assessment": build_confidence_assessment(
            base_score=0.0,
            quality_status="unavailable",
            source="market_composite",
        ),
        "evidence": {
            "market_state": {},
            "support_state": {},
            "stability_state": {},
        },
        "adjustments": {
            "conflict_adjustment": None,
            "quality_adjustment": None,
            "horizon_adjustment": None,
        },
        "summary": "Insufficient market data to produce a composite assessment.",
        "metadata": {
            "composite_version": _COMPOSITE_VERSION,
            "engines_used": 0,
            "conflict_count": 0,
            "conflict_severity": "none",
            "overall_quality": "unknown",
            "overall_freshness": "unknown",
            "horizon_span": None,
        },
    }


# ── Tone helpers ─────────────────────────────────────────────────────
# _classify_label, _classify_score, _engine_tone imported from
# app.utils.tone_classification above.


def _get_normalized(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract normalized sub-dict from a market payload."""
    return payload.get("normalized", payload) if isinstance(payload, dict) else {}


def _collect_engine_tones(
    market_ctx: dict[str, Any],
) -> dict[str, str]:
    """Return {engine_key: tone} for every engine in market context."""
    result: dict[str, str] = {}
    for eng_key, payload in market_ctx.items():
        norm = _get_normalized(payload)
        result[eng_key] = _engine_tone(norm)
    return result


def _count_tones(tones: dict[str, str]) -> dict[str, int]:
    """Aggregate tone counts from engine tone map."""
    counts: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0, "unknown": 0}
    for tone in tones.values():
        counts[tone] = counts.get(tone, 0) + 1
    return counts


# ── Dimension 1: market_state ────────────────────────────────────────

def _derive_market_state(
    tone_counts: dict[str, int],
    tones: dict[str, str],
) -> tuple[str, dict[str, Any]]:
    """Determine market_state from engine tone consensus.

    Rules:
    - Majority bullish → risk_on
    - Majority bearish → risk_off
    - Otherwise → neutral

    Majority = strictly more than any other non-unknown tone.
    """
    bullish = tone_counts.get("bullish", 0)
    bearish = tone_counts.get("bearish", 0)
    neutral = tone_counts.get("neutral", 0)
    total = bullish + bearish + neutral  # exclude unknown

    evidence = {
        "tone_counts": {k: v for k, v in tone_counts.items() if k != "unknown"},
        "engine_tones": dict(tones),
    }

    if total == 0:
        return "neutral", evidence

    if bullish > bearish and bullish > neutral:
        return "risk_on", evidence
    if bearish > bullish and bearish > neutral:
        return "risk_off", evidence
    return "neutral", evidence


# ── Dimension 2: support_state ───────────────────────────────────────

def _derive_support_state(
    tone_counts: dict[str, int],
    quality_sum: dict[str, Any],
    market_ctx: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Determine support_state from alignment strength + data quality.

    Rules:
    - All engines agree (ignoring unknown) + quality good/acceptable → supportive
    - Moderate alignment or quality degraded → mixed
    - Engines split or quality poor/unavailable → fragile

    Formula inputs:
    - alignment_ratio = max_tone_count / total_non_unknown
    - overall_quality from quality_summary
    """
    bullish = tone_counts.get("bullish", 0)
    bearish = tone_counts.get("bearish", 0)
    neutral = tone_counts.get("neutral", 0)
    total = bullish + bearish + neutral

    overall_quality = quality_sum.get("overall_quality", "unknown")
    quality_rank = _QUALITY_RANK.get(overall_quality, -1)

    evidence: dict[str, Any] = {
        "overall_quality": overall_quality,
    }

    if total == 0:
        evidence["reason"] = "no_engine_tones"
        return "fragile", evidence

    max_tone_count = max(bullish, bearish, neutral)
    alignment_ratio = max_tone_count / total
    evidence["alignment_ratio"] = round(alignment_ratio, 2)

    # Strong alignment + acceptable+ quality → supportive
    if alignment_ratio >= 0.75 and quality_rank >= _QUALITY_RANK["acceptable"]:
        evidence["reason"] = "strong_alignment_good_quality"
        return "supportive", evidence

    # Split engines (≤50% alignment) or poor quality → fragile
    if alignment_ratio <= 0.5 or quality_rank <= _QUALITY_RANK["poor"]:
        evidence["reason"] = "split_or_poor_quality"
        return "fragile", evidence

    evidence["reason"] = "moderate_alignment_or_degraded_quality"
    return "mixed", evidence


# ── Dimension 3: stability_state ─────────────────────────────────────

def _derive_stability_state(
    tone_counts: dict[str, int],
    conflict_report: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Determine stability_state from tonal spread + conflict severity.

    Rules:
    - High conflict severity → unstable
    - Moderate conflict severity or engines split → noisy
    - Low/none conflict severity + engines aligned → orderly
    """
    conflict_severity = "none"
    conflict_count = 0
    if conflict_report:
        conflict_severity = conflict_report.get("conflict_severity", "none")
        conflict_count = conflict_report.get("conflict_count", 0)

    bullish = tone_counts.get("bullish", 0)
    bearish = tone_counts.get("bearish", 0)
    neutral = tone_counts.get("neutral", 0)
    total = bullish + bearish + neutral

    has_split = (bullish > 0 and bearish > 0)

    evidence: dict[str, Any] = {
        "conflict_severity": conflict_severity,
        "conflict_count": conflict_count,
        "has_bull_bear_split": has_split,
    }

    if conflict_severity == "high":
        evidence["reason"] = "high_conflict_severity"
        return "unstable", evidence

    if conflict_severity == "moderate" or has_split:
        evidence["reason"] = "moderate_conflicts_or_split"
        return "noisy", evidence

    evidence["reason"] = "low_conflicts_aligned"
    return "orderly", evidence


# ── Confidence ───────────────────────────────────────────────────────

def _compute_base_confidence(
    tone_counts: dict[str, int],
    engines_used: int,
) -> float:
    """Base confidence from engine agreement strength.

    Formula:
        base = agreement_ratio * coverage_factor

    - agreement_ratio = max_tone_count / total_non_unknown  (how aligned)
    - coverage_factor = engines_used / 6                     (how many engines)
    """
    bullish = tone_counts.get("bullish", 0)
    bearish = tone_counts.get("bearish", 0)
    neutral = tone_counts.get("neutral", 0)
    total = bullish + bearish + neutral

    if total == 0:
        return 0.3  # some engines present but all unknown

    agreement_ratio = max(bullish, bearish, neutral) / total
    coverage_factor = min(engines_used / 6, 1.0)

    return round(agreement_ratio * coverage_factor, 2)


# ── Adjustment: conflict ─────────────────────────────────────────────

def _apply_conflict_adjustment(
    conflict_report: dict[str, Any] | None,
    market_state: str,
    support_state: str,
    stability_state: str,
) -> dict[str, Any] | None:
    """Compute adjustments based on conflict report.

    Returns None when no conflict report is provided or no conflicts exist.
    """
    if not conflict_report:
        return None
    severity = conflict_report.get("conflict_severity", "none")
    count = conflict_report.get("conflict_count", 0)
    if count == 0:
        return None

    penalty = _CONFLICT_SEVERITY_PENALTY.get(severity, 0.0)

    result: dict[str, Any] = {
        "applied": True,
        "conflict_severity": severity,
        "conflict_count": count,
        "confidence_penalty": penalty,
    }

    # Downgrade stability if conflicts are moderate+ and current is orderly
    if severity == "high" and stability_state != "unstable":
        result["stability_downgrade"] = "unstable"
    elif severity == "moderate" and stability_state == "orderly":
        result["stability_downgrade"] = "noisy"

    # Downgrade support if conflicts are high and current is supportive
    if severity == "high" and support_state == "supportive":
        result["support_downgrade"] = "mixed"

    return result


# ── Adjustment: quality ──────────────────────────────────────────────

def _apply_quality_adjustment(
    quality_sum: dict[str, Any],
    freshness_sum: dict[str, Any],
) -> dict[str, Any] | None:
    """Compute adjustments based on data quality, freshness, and degraded-engine count.

    Inputs:
    - quality_sum.overall_quality  → _QUALITY_PENALTY lookup
    - quality_sum.degraded_count   → per-engine degradation factor
    - freshness_sum.overall_freshness → _FRESHNESS_PENALTY lookup

    Degraded-count penalty (additive):
    - 0–1 degraded engines: 0.00
    - 2 degraded engines:   0.05
    - 3 degraded engines:   0.10
    - 4+ degraded engines:  0.15
    """
    overall_quality = quality_sum.get("overall_quality", "unknown")
    overall_freshness = freshness_sum.get("overall_freshness", "unknown")
    degraded_count = quality_sum.get("degraded_count", 0) or 0

    q_penalty = _QUALITY_PENALTY.get(overall_quality, 0.0)
    f_penalty = _FRESHNESS_PENALTY.get(overall_freshness, 0.0)

    # Per-engine degradation factor: extra penalty when many engines degraded
    if degraded_count >= 4:
        d_penalty = 0.15
    elif degraded_count >= 3:
        d_penalty = 0.10
    elif degraded_count >= 2:
        d_penalty = 0.05
    else:
        d_penalty = 0.0

    total_penalty = q_penalty + f_penalty + d_penalty

    if total_penalty == 0.0:
        return None

    result: dict[str, Any] = {
        "applied": True,
        "overall_quality": overall_quality,
        "overall_freshness": overall_freshness,
        "degraded_count": degraded_count,
        "quality_penalty": q_penalty,
        "freshness_penalty": f_penalty,
        "degraded_penalty": d_penalty,
        "confidence_penalty": round(total_penalty, 2),
    }

    # Downgrade support to fragile when quality is poor or unavailable
    if _QUALITY_RANK.get(overall_quality, -1) <= _QUALITY_RANK["poor"]:
        result["support_downgrade"] = "fragile"

    return result


# ── Adjustment: horizon ──────────────────────────────────────────────

def _apply_horizon_adjustment(
    horizon_sum: dict[str, Any],
) -> dict[str, Any] | None:
    """Compute adjustments based on horizon span width.

    Wide horizon span (e.g. intraday + long_term) slightly reduces
    confidence because the engines are looking at different timeframes
    and their agreement may be coincidental.
    """
    shortest = horizon_sum.get("shortest")
    longest = horizon_sum.get("longest")

    if not shortest or not longest:
        return None

    short_rank = horizon_rank(shortest)
    long_rank = horizon_rank(longest)
    span = long_rank - short_rank

    # Span of 0-2 is normal; 3-4 is notable; 5+ is wide
    if span <= 2:
        return None

    if span <= 4:
        penalty = 0.05
    else:
        penalty = 0.10

    return {
        "applied": True,
        "shortest": shortest,
        "longest": longest,
        "span": span,
        "confidence_penalty": penalty,
    }


# ── Status determination ─────────────────────────────────────────────

def _determine_status(
    overall_quality: str,
    overall_freshness: str,
    engines_used: int,
    conflict_severity: str = "none",
) -> str:
    """Determine composite status: ok / degraded / insufficient_data.

    Formula inputs:
    - overall_quality from quality_summary
    - overall_freshness from freshness_summary
    - engines_used count
    - conflict_severity from conflict_report (high → degraded)
    """
    if engines_used == 0:
        return "insufficient_data"

    q_rank = _QUALITY_RANK.get(overall_quality, -1)
    if q_rank <= _QUALITY_RANK["poor"] or overall_freshness == "very_stale":
        return "degraded"

    if conflict_severity == "high":
        return "degraded"

    if engines_used <= 2 and q_rank <= _QUALITY_RANK["degraded"]:
        return "degraded"

    return "ok"


# ── Human summary ────────────────────────────────────────────────────

def _build_human_summary(
    market_state: str,
    support_state: str,
    stability_state: str,
    confidence: float,
    status: str,
) -> str:
    """Build 1-2 sentence human-readable composite summary."""
    state_label = {
        "risk_on": "Risk-On",
        "neutral": "Neutral",
        "risk_off": "Risk-Off",
    }.get(market_state, market_state)

    support_label = {
        "supportive": "signals are supportive",
        "mixed": "signals are mixed",
        "fragile": "signals are fragile",
    }.get(support_state, support_state)

    stability_label = {
        "orderly": "orderly conditions",
        "noisy": "noisy conditions",
        "unstable": "unstable conditions",
    }.get(stability_state, stability_state)

    conf_pct = int(confidence * 100)

    parts = [
        f"Market composite: {state_label} with {stability_label}; {support_label}.",
        f"Confidence: {conf_pct}%.",
    ]

    if status == "degraded":
        parts.append("Assessment degraded by data quality or freshness issues.")

    return " ".join(parts)
