"""Shared contracts for the Flows & Positioning engine (Phase 1 rebuild).

All internal pillar math operates in the range [-1, 1] with neutral = 0.
The `run()` method of the engine translates this into the legacy 0-100
output dict via `translate_to_legacy_output()` below. Internal dataclasses
must never leak past that boundary.

Scope:
- Phase 1 pillars: positioning (COT), flows (sector RS + NAV overlay).
- Pillar 3 (dealer_hedging) is DEFERRED — emitted via PillarResult.deferred()
  with reason code DEFERRED_PILLAR_3 until Tradier access is restored.

The constant EXPECTED_PILLAR_COUNT = 3 is deliberately unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

# Architectural invariant — do NOT change in Phase 1.
EXPECTED_PILLAR_COUNT: int = 3


# ═══════════════════════════════════════════════════════════════════════
# Sub-signal and pillar result shapes (internal, [-1, 1] scores)
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SubSignal:
    """A single observable input to a pillar.

    score: in [-1, 1] or None when the sub-signal could not be computed.
    raw_value: the underlying observable (z-score, ratio, etc.) — kept
        for the legacy `diagnostics.pillar_details.<pillar>.submetrics[]`
        contract.
    reason_code: stable machine code when score is None (e.g.
        "NAV_HISTORY_BUILDING", "COT_STALE", "PUT_CALL_DEFERRED").
    """

    name: str
    score: float | None
    raw_value: float | None = None
    reason_code: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class PillarResult:
    """A pillar aggregates sub-signals into a single score in [-1, 1].

    score: mean of available sub-signal scores, or None when
        `len(available) < min_subsignals`.
    confidence: ratio of available sub-signals to expected, in [0, 1].
    status: "active" | "partial" | "deferred" | "unavailable".
    sub_signals: ALL attempted sub-signals, including failed ones
        (for diagnostics and pillar_details.submetrics).
    """

    name: str
    score: float | None
    confidence: float
    status: str
    sub_signals: tuple[SubSignal, ...] = ()
    available_count: int = 0
    expected_count: int = 0
    explanation: str = ""
    reason_code: str | None = None

    @classmethod
    def deferred(cls, name: str, reason_code: str, explanation: str = "") -> "PillarResult":
        return cls(
            name=name,
            score=None,
            confidence=0.0,
            status="deferred",
            sub_signals=(),
            available_count=0,
            expected_count=0,
            explanation=explanation or "Deferred until dependent data source is available.",
            reason_code=reason_code,
        )

    @classmethod
    def unavailable(cls, name: str, reason_code: str, explanation: str = "") -> "PillarResult":
        return cls(
            name=name,
            score=None,
            confidence=0.0,
            status="unavailable",
            sub_signals=(),
            available_count=0,
            expected_count=0,
            explanation=explanation,
            reason_code=reason_code,
        )

    @classmethod
    def from_subsignals(
        cls,
        name: str,
        sub_signals: list[SubSignal],
        *,
        expected_count: int,
        min_subsignals: int = 3,
        explanation: str = "",
    ) -> "PillarResult":
        """Build a PillarResult from a list of attempted sub-signals.

        Mean-based scoring on the sub-signals that produced a value.
        If fewer than `min_subsignals` are available, the pillar score
        is None and status = "unavailable".
        """
        available = [s for s in sub_signals if s.score is not None]
        n = len(available)
        status: str
        score: float | None
        if n >= min_subsignals:
            score = sum(s.score or 0.0 for s in available) / n
            status = "active" if n == expected_count else "partial"
        else:
            score = None
            status = "unavailable"
        confidence = min(1.0, n / expected_count) if expected_count > 0 else 0.0
        return cls(
            name=name,
            score=score,
            confidence=confidence,
            status=status,
            sub_signals=tuple(sub_signals),
            available_count=n,
            expected_count=expected_count,
            explanation=explanation,
            reason_code=None if n >= min_subsignals else "INSUFFICIENT_SUBSIGNALS",
        )


# Contributor threshold — a pillar's |score| must exceed this to enter
# positive_contributors / negative_contributors lists. Single tuning
# point for future calibration work.
CONTRIBUTOR_THRESHOLD: float = 0.3


@dataclass(frozen=True)
class FlowsComposite:
    """Deterministic composite across the three pillars (internal shape).

    score: equal-weight mean of pillar scores that are not None, in [-1, 1].
    confidence: mean pillar confidence multiplied by a presence_factor
        equal to (pillars_present / EXPECTED_PILLAR_COUNT). A single
        active pillar therefore caps confidence at 1/3 even if that
        pillar's own confidence is 1.0.
    pillars: ordered dict-like mapping, keys = "positioning", "flows",
        "dealer_hedging".
    conflicts: strings describing cross-pillar disagreement detected by
        `build()` — sign flip with both magnitudes > CONTRIBUTOR_THRESHOLD.
    """

    score: float | None
    confidence: float
    pillars: dict[str, PillarResult]
    warnings: tuple[str, ...] = ()
    missing_inputs: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    @classmethod
    def build(
        cls,
        pillars: dict[str, PillarResult],
        *,
        warnings: list[str] | None = None,
        missing_inputs: list[str] | None = None,
    ) -> "FlowsComposite":
        present = [p for p in pillars.values() if p.score is not None]
        if present:
            score = sum(p.score or 0.0 for p in present) / len(present)
        else:
            score = None
        presence_factor = len(present) / EXPECTED_PILLAR_COUNT if EXPECTED_PILLAR_COUNT > 0 else 0.0
        mean_conf = (
            sum(p.confidence for p in present) / len(present)
            if present else 0.0
        )
        confidence = mean_conf * presence_factor

        # Cross-pillar conflict detection: opposite signs, both magnitudes
        # above CONTRIBUTOR_THRESHOLD.
        conflicts: list[str] = []
        named_present = [
            (name, p) for name, p in pillars.items() if p.score is not None
        ]
        for i in range(len(named_present)):
            for j in range(i + 1, len(named_present)):
                n1, p1 = named_present[i]
                n2, p2 = named_present[j]
                s1 = p1.score or 0.0
                s2 = p2.score or 0.0
                if (
                    s1 * s2 < 0
                    and abs(s1) >= CONTRIBUTOR_THRESHOLD
                    and abs(s2) >= CONTRIBUTOR_THRESHOLD
                ):
                    conflicts.append(
                        f"{n1} ({s1:+.2f}) disagrees with {n2} ({s2:+.2f})"
                    )
        return cls(
            score=score,
            confidence=confidence,
            pillars=pillars,
            warnings=tuple(warnings or ()),
            missing_inputs=tuple(missing_inputs or ()),
            conflicts=tuple(conflicts),
        )


# ═══════════════════════════════════════════════════════════════════════
# Label bands — EXACT copy of the old engine's _LABEL_BANDS
# Source: BenTrade/backend/app/services/flows_positioning_engine.py:55-62
# Values operate on the legacy 0-100 score space (post-translation).
# Downstream consumers (regime, TMC, model_analysis) are calibrated to
# these bands — do NOT invent new ones in Phase 1.
# ═══════════════════════════════════════════════════════════════════════

LABEL_BANDS: list[tuple[float, float, str, str]] = [
    (85.0, 100.0, "Strongly Supportive Flows", "Strongly Supportive"),
    (70.0, 84.99, "Supportive Positioning", "Supportive"),
    (55.0, 69.99, "Mixed but Tradable", "Mixed"),
    (45.0, 54.99, "Fragile / Crowded", "Fragile"),
    (30.0, 44.99, "Reversal Risk Elevated", "Reversal Risk"),
    (0.0, 29.99, "Unstable / Unwind Risk", "Unstable"),
]

# Old engine: _CONFIDENCE_HIGH = 80, _CONFIDENCE_MEDIUM = 60 (0-100 scale).
CONFIDENCE_HIGH: float = 80.0
CONFIDENCE_MEDIUM: float = 60.0


def label_from_legacy_score(score: float | None) -> tuple[str, str]:
    """Map a legacy 0-100 score to (full_label, short_label)."""
    if score is None:
        return "Unknown", "Unknown"
    for lo, hi, full, short in LABEL_BANDS:
        if lo <= score <= hi:
            return full, short
    return "Unknown", "Unknown"


def signal_quality_from_legacy_confidence(confidence_score: float) -> str:
    if confidence_score >= CONFIDENCE_HIGH:
        return "high"
    if confidence_score >= CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


# ═══════════════════════════════════════════════════════════════════════
# Translation boundary — internal [-1, 1] → legacy 0-100 wrapper
# ═══════════════════════════════════════════════════════════════════════

# Fixed weights shown to downstream consumers. Even when dealer_hedging
# is deferred, we display the intended equal weighting; the composite
# already reflects the missing pillar via `presence_factor` on confidence.
LEGACY_PILLAR_WEIGHTS: dict[str, float] = {
    "positioning": 1.0 / 3.0,
    "flows": 1.0 / 3.0,
    "dealer_hedging": 1.0 / 3.0,
}


def _score_to_legacy(score: float | None) -> float | None:
    """Map internal [-1, 1] score to legacy [0, 100]. Neutral 0 → 50."""
    if score is None:
        return None
    clamped = max(-1.0, min(1.0, score))
    return (clamped + 1.0) * 50.0


def _build_summary(composite: FlowsComposite, legacy_score: float | None, label: str) -> str:
    """Deterministic short summary. Never blocks on LLM."""
    if legacy_score is None:
        return "Insufficient data for flows & positioning read."
    pos = composite.pillars.get("positioning")
    flo = composite.pillars.get("flows")
    dea = composite.pillars.get("dealer_hedging")
    parts: list[str] = []
    if pos and pos.score is not None:
        if pos.score >= 0.4:
            parts.append("positioning supportive")
        elif pos.score <= -0.4:
            parts.append("positioning stretched")
        else:
            parts.append("positioning neutral")
    if flo and flo.score is not None:
        if flo.score >= 0.4:
            parts.append("flows risk-on")
        elif flo.score <= -0.4:
            parts.append("flows risk-off")
        else:
            parts.append("flows mixed")
    if dea and dea.status == "deferred":
        parts.append("dealer hedging deferred")
    head = "; ".join(parts) if parts else "mixed signals"
    return f"{head.capitalize()}. Composite: {label} ({legacy_score:.0f}/100)."


def _build_trader_takeaway(composite: FlowsComposite, legacy_score: float | None) -> str:
    if legacy_score is None:
        return "Insufficient data for flows & positioning read."
    notes: list[str] = []
    pos = composite.pillars.get("positioning")
    flo = composite.pillars.get("flows")
    if pos and pos.score is not None:
        if pos.score >= 0.6:
            notes.append("Crowded long positioning — lean contrarian on further upside.")
        elif pos.score <= -0.6:
            notes.append("Crowded short positioning — watch for squeeze risk.")
    if pos and flo and pos.score is not None and flo.score is not None:
        if pos.score * flo.score < -0.2:
            notes.append("Positioning and flows disagree — reduce conviction.")
        elif pos.score > 0.2 and flo.score > 0.2:
            notes.append("Positioning and flows both constructive — trend-follow bias.")
        elif pos.score < -0.2 and flo.score < -0.2:
            notes.append("Positioning and flows both defensive — risk-off bias.")
    if not notes:
        notes.append("Neutral flows & positioning — no directional edge from this engine.")
    return " ".join(notes)


def _build_diagnostics(composite: FlowsComposite) -> dict[str, Any]:
    """Populate diagnostics.pillar_details.{pillar}.submetrics[] per the
    legacy contract consumed by `_extract_supporting_metrics()`.
    """
    pillar_details: dict[str, Any] = {}
    for name, pillar in composite.pillars.items():
        submetrics: list[dict[str, Any]] = []
        for ss in pillar.sub_signals:
            submetrics.append({
                "name": ss.name,
                "raw_value": ss.raw_value,
                "score": _score_to_legacy(ss.score),
                "reason_code": ss.reason_code,
                "detail": ss.detail,
            })
        pillar_details[name] = {
            "status": pillar.status,
            "available_count": pillar.available_count,
            "expected_count": pillar.expected_count,
            "reason_code": pillar.reason_code,
            "submetrics": submetrics,
        }
    return {"pillar_details": pillar_details}


def _build_missing_inputs(composite: FlowsComposite) -> list[str]:
    missing = list(composite.missing_inputs)
    for name, pillar in composite.pillars.items():
        if pillar.status in ("unavailable", "deferred"):
            tag = f"{name}:{pillar.reason_code or pillar.status.upper()}"
            if tag not in missing:
                missing.append(tag)
        else:
            for ss in pillar.sub_signals:
                if ss.score is None and ss.reason_code:
                    tag = f"{name}.{ss.name}:{ss.reason_code}"
                    if tag not in missing:
                        missing.append(tag)
    return missing


def translate_to_legacy_output(
    composite: FlowsComposite,
    *,
    as_of: str,
    llm_narrative: str | None = None,
) -> dict[str, Any]:
    """Translate internal FlowsComposite to the legacy engine_result dict
    expected by `engine_output_contract._normalize_pillar_engine()`.

    This is the ONE AND ONLY place internal [-1, 1] math crosses the
    boundary into the legacy 0-100 contract. Everything downstream sees
    only legacy shape.
    """
    legacy_score = _score_to_legacy(composite.score)
    legacy_confidence = composite.confidence * 100.0
    full_label, short_label = label_from_legacy_score(legacy_score)
    sig_qual = signal_quality_from_legacy_confidence(legacy_confidence)

    # Soft single-pillar cap — aggressive labels require cross-pillar
    # confirmation. If fewer than 2 pillars contributed a non-None score,
    # cap the label at "Mixed but Tradable" / "Fragile / Crowded" band.
    warnings_out = list(composite.warnings)
    active_pillars = sum(1 for p in composite.pillars.values() if p.score is not None)
    if active_pillars < 2 and legacy_score is not None:
        capped = False
        if legacy_score >= 70.0:
            full_label, short_label = "Mixed but Tradable", "Mixed"
            capped = True
        elif legacy_score < 45.0:
            full_label, short_label = "Fragile / Crowded", "Fragile"
            capped = True
        if capped:
            warnings_out.append("Label capped due to single-pillar evidence.")

    pillar_scores: dict[str, float | None] = {
        name: _score_to_legacy(p.score) for name, p in composite.pillars.items()
    }
    pillar_explanations: dict[str, str] = {}
    for name, p in composite.pillars.items():
        if p.status == "deferred":
            pillar_explanations[name] = "Deferred until Tradier access restored (Phase 2)."
        else:
            pillar_explanations[name] = p.explanation

    summary = _build_summary(composite, legacy_score, full_label)
    trader_takeaway = _build_trader_takeaway(composite, legacy_score)

    # Contributor lists — sort by |score| descending, only pillars above
    # CONTRIBUTOR_THRESHOLD. Phrased "<pillar>: <explanation>".
    positive_ranked: list[tuple[float, str]] = []
    negative_ranked: list[tuple[float, str]] = []
    for name, p in composite.pillars.items():
        if p.score is None:
            continue
        if abs(p.score) < CONTRIBUTOR_THRESHOLD:
            continue
        phrase = f"{name}: {p.explanation or 'directional'}"
        if p.score >= CONTRIBUTOR_THRESHOLD:
            positive_ranked.append((abs(p.score), phrase))
        elif p.score <= -CONTRIBUTOR_THRESHOLD:
            negative_ranked.append((abs(p.score), phrase))
    positive_ranked.sort(key=lambda t: t[0], reverse=True)
    negative_ranked.sort(key=lambda t: t[0], reverse=True)
    positive = [phrase for _, phrase in positive_ranked]
    negative = [phrase for _, phrase in negative_ranked]

    diagnostics = _build_diagnostics(composite)
    if llm_narrative:
        # `_normalize_pillar_engine` strips unknown top-level keys but
        # the raw `engine_result` is preserved in the service payload
        # (`dashboard_metadata`). Stash LLM narrative under diagnostics
        # so downstream raw consumers can read it without changing the
        # normalizer contract.
        diagnostics["llm_narrative"] = llm_narrative

    return {
        "score": legacy_score,
        "label": full_label,
        "short_label": short_label,
        "confidence_score": legacy_confidence,
        "signal_quality": sig_qual,
        "summary": summary,
        "trader_takeaway": trader_takeaway,
        "pillar_scores": pillar_scores,
        "pillar_weights": dict(LEGACY_PILLAR_WEIGHTS),
        "pillar_explanations": pillar_explanations,
        "positive_contributors": positive,
        "negative_contributors": negative,
        "conflicting_signals": list(composite.conflicts),
        "warnings": warnings_out,
        "missing_inputs": _build_missing_inputs(composite),
        "diagnostics": diagnostics,
        "as_of": as_of,
        "engine": "flows_positioning",
    }


__all__ = [
    "EXPECTED_PILLAR_COUNT",
    "CONTRIBUTOR_THRESHOLD",
    "CONTRIBUTOR_THRESHOLD",
    "SubSignal",
    "PillarResult",
    "FlowsComposite",
    "LABEL_BANDS",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MEDIUM",
    "LEGACY_PILLAR_WEIGHTS",
    "label_from_legacy_score",
    "signal_quality_from_legacy_confidence",
    "translate_to_legacy_output",
]
