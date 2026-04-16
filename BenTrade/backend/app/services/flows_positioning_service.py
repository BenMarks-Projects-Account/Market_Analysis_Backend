"""Flows & Positioning Service — Phase 1 rebuild.

Orchestrator layer around the new ``flows_positioning_engine``:
  1. Invoke async engine (which fetches COT + RS bars via FMPClient and
     runs LLM interpretation inline).
  2. Cache result.
  3. Attach ``normalized`` and ``dashboard_metadata``.
  4. Expose ``get_flows_positioning_analysis`` and ``run_model_analysis``.

Public interface is unchanged from the pre-Phase-1 service so
``MarketIntelligenceRunner``, ``DataPopulationService``, API route
handlers, and ``RegimeService`` remain wiring-compatible. The only
change is the constructor — it now takes an ``FMPClient`` instead of a
``FlowsPositioningDataProvider``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.services.dashboard_metadata_contract import build_dashboard_metadata
from app.services.engine_output_contract import normalize_engine_output
from app.services.flows_positioning_engine import compute_flows_positioning_scores
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)

FLOWS_POSITIONING_CACHE_TTL = 90


class FlowsPositioningService:
    """Service layer for the Flows & Positioning engine (Phase 1)."""

    def __init__(
        self,
        fmp_client: Any,
        cache: TTLCache,
        *,
        ttl_seconds: int = FLOWS_POSITIONING_CACHE_TTL,
    ) -> None:
        self.fmp_client = fmp_client
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    async def get_flows_positioning_analysis(
        self, *, force: bool = False
    ) -> dict[str, Any]:
        """Return the full flows & positioning analysis.

        Always hits the deterministic + LLM path unless served from cache.
        The engine's internal LLM interpretation is the single source of
        narrative / risks / confidence_qualifier — no separate LLM call
        is made in ``run_model_analysis``.
        """
        cache_key = "flows_positioning"

        if not force:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                logger.info("event=flows_positioning_cache_hit")
                cached.setdefault("cache_info", {})["cache_hit"] = True
                return cached

        logger.info("event=flows_positioning_compute_start force=%s", force)
        start = datetime.now(timezone.utc)

        try:
            engine_result = await compute_flows_positioning_scores(self.fmp_client)
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            computed_at = datetime.now(timezone.utc).isoformat()

            payload = {
                "engine_result": engine_result,
                "data_quality": {
                    "signal_quality": engine_result.get("signal_quality", "low"),
                    "confidence_score": engine_result.get("confidence_score", 0),
                    "missing_inputs_count": len(engine_result.get("missing_inputs", [])),
                    "warning_count": len(engine_result.get("warnings", [])),
                },
                "cache_info": {
                    "cache_hit": False,
                    "engine_run_at": computed_at,
                    "cache_ttl_s": self.ttl_seconds,
                },
                "compute_duration_s": round(duration, 2),
                "as_of": engine_result.get("as_of"),
            }
            payload["normalized"] = normalize_engine_output(
                "flows_positioning", payload
            )
            payload["dashboard_metadata"] = build_dashboard_metadata(
                "flows_positioning",
                engine_result=engine_result,
                source_errors={},
                compute_duration_s=round(duration, 2),
            )

            await self.cache.set(cache_key, payload, self.ttl_seconds)
            logger.info(
                "event=flows_positioning_compute_complete score=%s label=%s "
                "duration_s=%.1f cached_ttl=%d",
                engine_result.get("score"),
                engine_result.get("label", "unknown"),
                duration,
                self.ttl_seconds,
            )
            return payload

        except Exception as exc:
            logger.error(
                "event=flows_positioning_compute_failed error=%s",
                exc,
                exc_info=True,
            )
            return {
                "engine_result": {
                    "engine": "flows_positioning",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "score": None,
                    "label": "Unavailable",
                    "short_label": "Unavailable",
                    "confidence_score": 0,
                    "signal_quality": "low",
                    "summary": f"Engine computation failed: {exc}",
                    "pillar_scores": {},
                    "pillar_explanations": {},
                    "pillar_status": {
                        "positioning": "unavailable",
                        "flows": "unavailable",
                        "dealer_hedging": "deferred",
                    },
                    "positive_contributors": [],
                    "negative_contributors": [],
                    "conflicting_signals": [],
                    "trader_takeaway": "Flows & positioning data is currently unavailable.",
                    "warnings": [f"Engine error: {exc}"],
                    "missing_inputs": [],
                    "diagnostics": {},
                },
                "data_quality": {
                    "signal_quality": "low",
                    "missing_inputs_count": 0,
                    "warning_count": 1,
                },
                "compute_duration_s": 0,
                "as_of": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "dashboard_metadata": build_dashboard_metadata(
                    "flows_positioning",
                    is_error_payload=True,
                    error_stage="compute",
                ),
            }

    # ── Model (LLM) Analysis ────────────────────────────────────

    async def run_model_analysis(
        self, *, force: bool = False
    ) -> dict[str, Any]:
        """Return the LLM-sourced fields already computed inside the engine.

        In the Phase 1 architecture the LLM interpretation runs *inside*
        the engine (``flows_llm_interpretation``), so this method is a
        read-through of the cached engine result rather than a second
        model call.
        """
        from app.services.model_analysis_contract import wrap_service_model_response
        from common.model_sanitize import classify_model_error, user_facing_error_message
        import time as _time

        model_cache_key = "flows_positioning:model"

        if not force:
            cached = await self.cache.get(model_cache_key)
            if cached is not None:
                logger.info("event=flows_positioning_model_cache_hit")
                return cached

        logger.info("event=flows_positioning_model_analysis_start force=%s", force)
        t0 = _time.monotonic()
        requested_at = datetime.now(timezone.utc).isoformat()

        try:
            base = await self.get_flows_positioning_analysis(force=False)
            engine_result = base.get("engine_result", {}) or {}

            # Lift LLM-sourced fields produced inside the engine. If the
            # engine's LLM path failed, narrative will be None and
            # llm_risks empty — propagate that honestly rather than
            # fabricating a filler narrative.
            narrative = engine_result.get("narrative")
            llm_risks = engine_result.get("llm_risks", []) or []
            qualifier = engine_result.get("confidence_qualifier")

            if narrative is None:
                outcome = {
                    "model_analysis": None,
                    "error": {
                        "kind": "UNAVAILABLE",
                        "message": "LLM interpretation was skipped or failed for this engine run.",
                    },
                }
            else:
                outcome = {
                    "model_analysis": {
                        "narrative": narrative,
                        "risks": llm_risks,
                        "confidence_qualifier": qualifier,
                        "score": engine_result.get("score"),
                        "label": engine_result.get("label"),
                        "summary": engine_result.get("summary"),
                        "trader_takeaway": engine_result.get("trader_takeaway"),
                    },
                }

            duration_ms = int((_time.monotonic() - t0) * 1000)
            wrapped = wrap_service_model_response(
                "flows_positioning", outcome,
                requested_at=requested_at, duration_ms=duration_ms,
            )
            wrapped["as_of"] = datetime.now(timezone.utc).isoformat()

            await self.cache.set(model_cache_key, wrapped, self.ttl_seconds)
            return wrapped

        except Exception as exc:
            duration_ms = int((_time.monotonic() - t0) * 1000)
            error_kind = classify_model_error(exc)
            error_msg = user_facing_error_message(error_kind)
            logger.warning(
                "event=flows_positioning_model_analysis_failed error_kind=%s error=%s",
                error_kind, exc,
            )
            outcome = {
                "model_analysis": None,
                "error": {"kind": error_kind, "message": error_msg},
            }
            return wrap_service_model_response(
                "flows_positioning", outcome,
                requested_at=requested_at, duration_ms=duration_ms,
            )
