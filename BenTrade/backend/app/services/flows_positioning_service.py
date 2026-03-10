"""Flows & Positioning Service — orchestrator layer.

Coordinates data fetching, engine computation, and caching.
Pattern mirrors CrossAssetMacroService:
  1. Fetch raw data (via FlowsPositioningDataProvider)
  2. Invoke engine (via flows_positioning_engine.compute_flows_positioning_scores)
  3. Cache result (via TTLCache)
  4. Return structured payload

Caching:
  - Engine result cached for FLOWS_POSITIONING_CACHE_TTL seconds (default 90)
  - Cache key: 'flows_positioning'
  - Forced refresh via `force=True` parameter
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.services.flows_positioning_data_provider import FlowsPositioningDataProvider
from app.services.flows_positioning_engine import compute_flows_positioning_scores
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)

FLOWS_POSITIONING_CACHE_TTL = 90


class FlowsPositioningService:
    """Service layer for the Flows & Positioning engine."""

    def __init__(
        self,
        data_provider: FlowsPositioningDataProvider,
        cache: TTLCache,
        *,
        ttl_seconds: int = FLOWS_POSITIONING_CACHE_TTL,
    ) -> None:
        self.data_provider = data_provider
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    async def get_flows_positioning_analysis(
        self, *, force: bool = False
    ) -> dict[str, Any]:
        """Return full flows & positioning analysis.

        Parameters
        ----------
        force : bool
            If True, bypass cache and recompute.

        Returns
        -------
        dict with:
          engine_result: full engine output
          data_quality: summary of data coverage
          compute_duration_s: wall-clock time
          as_of: ISO timestamp
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
            raw_data = await self.data_provider.fetch_flows_positioning_data()

            engine_result = compute_flows_positioning_scores(
                positioning_data=raw_data["positioning_data"],
                crowding_data=raw_data["crowding_data"],
                squeeze_data=raw_data["squeeze_data"],
                flow_data=raw_data["flow_data"],
                stability_data=raw_data["stability_data"],
                source_meta=raw_data["source_meta"],
            )

            duration = (datetime.now(timezone.utc) - start).total_seconds()
            computed_at = datetime.now(timezone.utc).isoformat()

            # Surface per-source errors so frontend can show degraded state
            source_errors = raw_data.get("source_errors", {})
            if source_errors:
                for src, err_msg in source_errors.items():
                    engine_result.setdefault("warnings", []).append(
                        f"Source '{src}' failed: {err_msg}"
                    )

            payload = {
                "engine_result": engine_result,
                "data_quality": {
                    "signal_quality": engine_result.get("signal_quality", "low"),
                    "confidence_score": engine_result.get("confidence_score", 0),
                    "missing_inputs_count": len(engine_result.get("missing_inputs", [])),
                    "warning_count": len(engine_result.get("warnings", [])),
                    "source_errors": source_errors,
                },
                "cache_info": {
                    "cache_hit": False,
                    "engine_run_at": computed_at,
                    "cache_ttl_s": self.ttl_seconds,
                },
                "compute_duration_s": round(duration, 2),
                "as_of": engine_result.get("as_of"),
            }

            await self.cache.set(cache_key, payload, self.ttl_seconds)
            logger.info(
                "event=flows_positioning_compute_complete score=%.2f label=%s "
                "duration_s=%.1f cached_ttl=%d",
                engine_result.get("score", 0),
                engine_result.get("label", "unknown"),
                duration,
                self.ttl_seconds,
            )

            return payload

        except Exception as exc:
            logger.error(
                "event=flows_positioning_compute_failed error=%s",
                exc, exc_info=True,
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
                    "strategy_bias": {},
                    "positive_contributors": [],
                    "negative_contributors": [],
                    "conflicting_signals": [],
                    "trader_takeaway": "Flows & positioning data is currently unavailable.",
                    "warnings": [f"Engine error: {exc}"],
                    "missing_inputs": [],
                    "diagnostics": {},
                    "raw_inputs": {},
                },
                "data_quality": {
                    "signal_quality": "low",
                    "missing_inputs_count": 0,
                    "warning_count": 1,
                },
                "compute_duration_s": 0,
                "as_of": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            }

    # ── Model (LLM) Analysis ────────────────────────────────────

    def _run_model_analysis(
        self, engine_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Attempt LLM-based flows & positioning analysis."""
        from common.model_sanitize import classify_model_error, user_facing_error_message

        try:
            from common.model_analysis import analyze_flows_positioning
            result = analyze_flows_positioning(
                engine_result=engine_result,
                timeout=180,
                retries=0,
            )
            logger.info(
                "event=flows_positioning_model_analysis_ok score=%s label=%s",
                result.get("score"),
                result.get("label"),
            )
            return {"model_analysis": result}
        except Exception as exc:
            error_kind = classify_model_error(exc)
            error_msg = user_facing_error_message(error_kind)
            logger.warning(
                "event=flows_positioning_model_analysis_failed error_kind=%s error=%s",
                error_kind, exc,
            )
            return {
                "model_analysis": None,
                "error": {"kind": error_kind, "message": error_msg},
            }

    async def run_model_analysis(
        self, *, force: bool = False
    ) -> dict[str, Any]:
        """Run LLM model analysis on flows & positioning data.

        Returns
        -------
        dict with:
          model_analysis: LLM output or None
          error: dict | None — { kind, message } if model failed
          as_of: ISO timestamp
        """
        import asyncio

        model_cache_key = "flows_positioning:model"

        if not force:
            cached = await self.cache.get(model_cache_key)
            if cached is not None:
                logger.info("event=flows_positioning_model_cache_hit")
                return cached

        logger.info("event=flows_positioning_model_analysis_start force=%s", force)

        base = await self.get_flows_positioning_analysis(force=False)
        engine_result = base.get("engine_result", {})

        loop = asyncio.get_running_loop()
        model_outcome = await loop.run_in_executor(
            None, self._run_model_analysis, engine_result
        )

        result: dict[str, Any] = {
            "model_analysis": model_outcome.get("model_analysis"),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

        if model_outcome.get("error"):
            result["error"] = model_outcome["error"]

        if result["model_analysis"] is not None:
            await self.cache.set(model_cache_key, result, self.ttl_seconds)

        has_model = result["model_analysis"] is not None
        logger.info(
            "event=flows_positioning_model_analysis_complete has_model=%s cached_ttl=%d",
            has_model,
            self.ttl_seconds if has_model else 0,
        )

        return result
