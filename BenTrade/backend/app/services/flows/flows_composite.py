"""Flows & Positioning composite (Phase 1, Step 3).

Glues Pillar 1 (positioning) + Pillar 2 (flows) + deferred Pillar 3
(dealer_hedging) into a single engine-facing dict via
``translate_to_legacy_output()``.

Behaviors specified in the Step 3 prompt:
  * Partial pillars are full-weight participants. Their confidence
    already encodes the reduced sub-signal coverage; the composite
    applies no extra down-weighting.
  * Only pillars with ``score is None`` (unavailable / deferred) are
    excluded from the composite score.
  * Contributor lists use ``CONTRIBUTOR_THRESHOLD`` (0.3) on the
    internal [-1, 1] magnitude — same threshold drives cross-pillar
    conflict detection inside ``FlowsComposite.build()``.
  * LLM interpretation is non-critical path. Failures return ``None``
    and the deterministic fields are preserved.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .contracts import (
    CONTRIBUTOR_THRESHOLD,
    FlowsComposite,
    PillarResult,
    _score_to_legacy,
    translate_to_legacy_output,
)
from .pillar_flows import build_flows_pillar
from .pillar_positioning import build_positioning_pillar

logger = logging.getLogger(__name__)

# Signature: async fn taking the composite payload, returning one of:
#   (narrative_str, risks_list, qualifier_str) on success
#   None on any failure
LLMInterpretFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


def _deferred_pillar_3() -> PillarResult:
    """Pillar 3 (dealer hedging) is deferred until Tradier access is restored."""
    return PillarResult.deferred(
        name="dealer_hedging",
        reason_code="DEFERRED_PILLAR_3",
        explanation="Dealer hedging / gamma positioning deferred until Tradier OPRA access is restored (Phase 2).",
    )


def _contributor_entries(
    composite: FlowsComposite,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (positive, negative) contributor lists per Step 3 spec.

    Each entry: {"pillar": name, "score": legacy 0-100, "confidence": legacy 0-100}.
    Only pillars with |internal score| >= CONTRIBUTOR_THRESHOLD.
    Sorted by |internal score| descending.
    """
    positive: list[tuple[float, dict[str, Any]]] = []
    negative: list[tuple[float, dict[str, Any]]] = []
    for name, pillar in composite.pillars.items():
        if pillar.score is None:
            continue
        if abs(pillar.score) < CONTRIBUTOR_THRESHOLD:
            continue
        entry = {
            "pillar": name,
            "score": _score_to_legacy(pillar.score),
            "confidence": pillar.confidence * 100.0,
        }
        if pillar.score > 0:
            positive.append((abs(pillar.score), entry))
        else:
            negative.append((abs(pillar.score), entry))
    positive.sort(key=lambda t: t[0], reverse=True)
    negative.sort(key=lambda t: t[0], reverse=True)
    return [e for _, e in positive], [e for _, e in negative]


def _pillar_status_map(composite: FlowsComposite) -> dict[str, str]:
    return {name: p.status for name, p in composite.pillars.items()}


def _build_llm_payload(
    composite: FlowsComposite,
    legacy_engine_result: dict[str, Any],
) -> dict[str, Any]:
    """Shape the input the LLM interpreter gets.

    Keeps everything a single JSON-safe dict for easy serialization and
    stable snapshots.
    """
    per_pillar: dict[str, Any] = {}
    for name, pillar in composite.pillars.items():
        per_pillar[name] = {
            "status": pillar.status,
            "score_internal": pillar.score,  # [-1, 1] or None
            "confidence": pillar.confidence,
            "explanation": pillar.explanation,
            "reason_code": pillar.reason_code,
            "sub_signals": [
                {
                    "name": ss.name,
                    "score_internal": ss.score,
                    "raw_value": ss.raw_value,
                    "reason_code": ss.reason_code,
                    "detail": ss.detail,
                }
                for ss in pillar.sub_signals
            ],
        }
    return {
        "composite_score_legacy_0_100": legacy_engine_result.get("score"),
        "composite_confidence_legacy_0_100": legacy_engine_result.get("confidence_score"),
        "composite_label": legacy_engine_result.get("label"),
        "signal_quality": legacy_engine_result.get("signal_quality"),
        "pillars": per_pillar,
        "conflicts": list(composite.conflicts),
        "warnings": list(legacy_engine_result.get("warnings") or ()),
    }


async def build_flows_composite(
    fmp_client: Any,
    *,
    llm_interpret_fn: LLMInterpretFn | None = None,
    execution_mode: str | None = None,
) -> dict[str, Any]:
    """Run both pillars, compose, translate, optionally attach LLM fields.

    Args:
        fmp_client: shared FMPClient used by both pillars.
        llm_interpret_fn: optional async LLM interpreter. If None, no
            LLM call is attempted and the LLM fields are ``None``/``[]``.
            Pass ``flows_llm_interpretation.interpret_flows_composite``
            for the default implementation.
        execution_mode: forwarded to LLM interpreter for routing overrides.

    Returns the full engine-facing dict (see Step 3 STOP report spec).
    """
    # ── Pillars (Pillar 1 + Pillar 2 in parallel; P3 is a constant) ─────
    pos_pillar, flow_pillar = await asyncio.gather(
        build_positioning_pillar(fmp_client),
        build_flows_pillar(fmp_client),
    )
    dealer_pillar = _deferred_pillar_3()

    pillars: dict[str, PillarResult] = {
        "positioning": pos_pillar,
        "flows": flow_pillar,
        "dealer_hedging": dealer_pillar,
    }

    composite = FlowsComposite.build(pillars)
    as_of = datetime.now(timezone.utc).isoformat()

    legacy = translate_to_legacy_output(composite, as_of=as_of)

    # ── Overwrite contributor lists with the richer pillar-keyed shape ──
    positive, negative = _contributor_entries(composite)
    legacy["positive_contributors"] = positive
    legacy["negative_contributors"] = negative

    # ── Pillar status marker (top-level, per Step 3 spec) ───────────────
    legacy["pillar_status"] = _pillar_status_map(composite)

    # ── LLM interpretation (non-critical path) ──────────────────────────
    narrative: str | None = None
    risks: list[str] = []
    qualifier: str | None = None

    if llm_interpret_fn is not None:
        llm_payload = _build_llm_payload(composite, legacy)
        try:
            result = await llm_interpret_fn(llm_payload)
        except Exception as exc:  # noqa: BLE001 — non-critical path
            logger.warning(
                "event=flows_llm_interpretation_failed reason=%s",
                type(exc).__name__,
            )
            result = None

        if result is not None:
            narrative = result.get("narrative")
            risks = list(result.get("risks") or [])
            qualifier = result.get("confidence_qualifier")

    # Top-level LLM-sourced fields (may be None / empty on failure).
    legacy["narrative"] = narrative
    legacy["llm_risks"] = risks
    legacy["confidence_qualifier"] = qualifier

    # Mirror narrative into diagnostics so normalized consumers can still
    # see it (translate_to_legacy_output already honors llm_narrative
    # when passed, but we built legacy without it above to avoid a second
    # translation call — attach it here).
    if narrative:
        diagnostics = legacy.setdefault("diagnostics", {})
        diagnostics["llm_narrative"] = narrative

    return legacy


__all__ = [
    "build_flows_composite",
    "LLMInterpretFn",
]
