"""Flows & Positioning Engine — Phase 1 rebuild.

Thin adapter around the new pillar-based implementation in
``app.services.flows``:

  * Pillar 1 — Positioning (COT z-scores on ES/NQ/VX/ZN/ZB)
  * Pillar 2 — Flows (sector RS vs SPY + HYG/TLT credit + NAV overlay)
  * Pillar 3 — Dealer Hedging — DEFERRED until Tradier OPRA access restored

The deterministic composite + LLM interpretation are produced by
``flows.flows_composite.build_flows_composite``. This module exposes a
single async public entry point ``compute_flows_positioning_scores``
used by ``FlowsPositioningService``.

Public contract (what the returned dict looks like) is fully documented
in ``flows.contracts.translate_to_legacy_output`` and is compatible with
``engine_output_contract._normalize_pillar_engine``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.services.flows.flows_composite import (
    LLMInterpretFn,
    build_flows_composite,
)
from app.services.flows.flows_llm_interpretation import interpret_flows_composite

logger = logging.getLogger(__name__)


async def compute_flows_positioning_scores(
    fmp_client: Any,
    *,
    llm_interpret_fn: LLMInterpretFn | None = interpret_flows_composite,
    execution_mode: str | None = None,
) -> dict[str, Any]:
    """Produce the full engine result dict for the Flows & Positioning engine.

    Parameters
    ----------
    fmp_client
        Shared FMPClient. Both pillars fetch their own data via this
        client (COT reports and daily OHLCV bars).
    llm_interpret_fn
        Optional async LLM interpreter. Defaults to the routed-model
        interpreter in ``flows_llm_interpretation``. Pass ``None`` to
        disable the LLM call entirely (useful for tests and offline
        smoke runs).
    execution_mode
        Optional explicit routing execution mode forwarded to the LLM
        interpreter.

    Returns
    -------
    dict
        The engine-facing dict (legacy 0-100 shape) including
        ``pillar_status``, contributor lists, conflict list, and
        optional LLM-sourced fields (``narrative``, ``llm_risks``,
        ``confidence_qualifier``).

    Failure mode
    ------------
    On total failure (both pillars raise) the engine returns a
    neutral-unavailable payload rather than raising, so the service
    layer can always return something to downstream consumers.
    """
    try:
        return await build_flows_composite(
            fmp_client,
            llm_interpret_fn=llm_interpret_fn,
            execution_mode=execution_mode,
        )
    except Exception as exc:  # noqa: BLE001 — top-level safety net
        logger.error(
            "event=flows_positioning_engine_failed error=%s",
            exc,
            exc_info=True,
        )
        as_of = datetime.now(timezone.utc).isoformat()
        return {
            "engine": "flows_positioning",
            "as_of": as_of,
            "score": None,
            "label": "Unavailable",
            "short_label": "Unavailable",
            "confidence_score": 0.0,
            "signal_quality": "low",
            "summary": "Flows & positioning engine failed to compute.",
            "trader_takeaway": "Insufficient data for flows & positioning read.",
            "pillar_scores": {
                "positioning": None,
                "flows": None,
                "dealer_hedging": None,
            },
            "pillar_weights": {
                "positioning": 1.0 / 3.0,
                "flows": 1.0 / 3.0,
                "dealer_hedging": 1.0 / 3.0,
            },
            "pillar_explanations": {},
            "pillar_status": {
                "positioning": "unavailable",
                "flows": "unavailable",
                "dealer_hedging": "deferred",
            },
            "positive_contributors": [],
            "negative_contributors": [],
            "conflicting_signals": [],
            "warnings": [f"Engine error: {exc}"],
            "missing_inputs": ["engine:ENGINE_EXCEPTION"],
            "diagnostics": {"pillar_details": {}, "engine_error": str(exc)},
            "narrative": None,
            "llm_risks": [],
            "confidence_qualifier": None,
        }


__all__ = ["compute_flows_positioning_scores"]
