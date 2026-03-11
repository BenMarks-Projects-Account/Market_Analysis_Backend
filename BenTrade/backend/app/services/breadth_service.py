"""Breadth & Participation Service — orchestrator layer.

Coordinates data fetching, engine computation, and caching.
Follows the same layered pattern as NewsSentimentService:
  1. Fetch raw data (via BreadthDataProvider)
  2. Invoke engine (via breadth_engine.compute_breadth_scores)
  3. Cache result (via TTLCache)
  4. Return structured payload

Caching:
  - Engine result cached for BREADTH_CACHE_TTL seconds (default 120)
  - Cache key: 'breadth_participation'
  - Forced refresh via `force=True` parameter
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.services.breadth_data_provider import BreadthDataProvider
from app.services.breadth_engine import compute_breadth_scores
from app.services.dashboard_metadata_contract import build_dashboard_metadata
from app.services.engine_output_contract import normalize_engine_output
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)

# Cache TTL in seconds
BREADTH_CACHE_TTL = 120


class BreadthService:
    """Service layer for Breadth & Participation engine.

    Orchestrates data fetch → engine computation → cache.
    """

    def __init__(
        self,
        data_provider: BreadthDataProvider,
        cache: TTLCache,
        *,
        ttl_seconds: int = BREADTH_CACHE_TTL,
    ) -> None:
        self.data_provider = data_provider
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    async def get_breadth_analysis(
        self, *, force: bool = False
    ) -> dict[str, Any]:
        """Return full breadth & participation analysis.

        Parameters
        ----------
        force : bool
            If True, bypass cache and recompute.

        Returns
        -------
        dict with:
          engine_result: full engine output
          data_quality: summary of data coverage
          as_of: ISO timestamp
        """
        cache_key = "breadth_participation"

        if not force:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                logger.info("event=breadth_cache_hit")
                return cached

        logger.info("event=breadth_compute_start force=%s", force)
        start = datetime.now(timezone.utc)

        try:
            # Step 1: Fetch raw data
            raw_data = await self.data_provider.fetch_breadth_data()

            # Step 2: Invoke engine
            engine_result = compute_breadth_scores(
                participation_data=raw_data["participation_data"],
                trend_data=raw_data["trend_data"],
                volume_data=raw_data["volume_data"],
                leadership_data=raw_data["leadership_data"],
                stability_data=raw_data["stability_data"],
                universe_meta=raw_data["universe_meta"],
            )

            # Step 3: Build response payload
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            diag = engine_result.get("diagnostics", {})
            quality_scores = diag.get("quality_scores", {})
            payload = {
                "engine_result": engine_result,
                "data_quality": {
                    "universe_coverage_pct": engine_result.get("universe", {}).get("coverage_pct", 0),
                    "signal_quality": engine_result.get("signal_quality", "low"),
                    "confidence_score": engine_result.get("confidence_score", 0),
                    "data_quality_score": quality_scores.get("data_quality_score", 0),
                    "historical_validity_score": quality_scores.get("historical_validity_score", 0),
                    "point_in_time_available": engine_result.get("point_in_time_constituents_available", False),
                    "survivorship_bias_risk": engine_result.get("survivorship_bias_risk", True),
                    "missing_inputs_count": len(engine_result.get("missing_inputs", [])),
                    "warning_count": len(engine_result.get("warnings", [])),
                    "grouped_warnings": diag.get("grouped_warnings", {}),
                    "structured_warnings": diag.get("structured_warnings", []),
                },
                "compute_duration_s": round(duration, 2),
                "as_of": engine_result.get("as_of"),
            }
            payload["normalized"] = normalize_engine_output(
                "breadth_participation", payload
            )
            payload["dashboard_metadata"] = build_dashboard_metadata(
                "breadth_participation",
                engine_result=engine_result,
                compute_duration_s=round(duration, 2),
            )

            # Step 4: Cache
            await self.cache.set(cache_key, payload, self.ttl_seconds)
            logger.info(
                "event=breadth_compute_complete score=%.2f label=%s "
                "duration_s=%.1f cached_ttl=%d",
                engine_result.get("score", 0),
                engine_result.get("label", "unknown"),
                duration,
                self.ttl_seconds,
            )

            return payload

        except Exception as exc:
            logger.error(
                "event=breadth_compute_failed error=%s", exc, exc_info=True
            )
            # Return a degraded response rather than crashing
            return {
                "engine_result": {
                    "engine": "breadth_participation",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "score": None,
                    "label": "Unavailable",
                    "short_label": "Unavailable",
                    "confidence_score": 0,
                    "signal_quality": "low",
                    "summary": f"Engine computation failed: {exc}",
                    "pillar_scores": {},
                    "pillar_explanations": {},
                    "positive_contributors": [],
                    "negative_contributors": [],
                    "conflicting_signals": [],
                    "trader_takeaway": "Breadth data is currently unavailable.",
                    "warnings": [f"Engine error: {exc}"],
                    "missing_inputs": [],
                    "diagnostics": {},
                    "raw_inputs": {},
                },
                "data_quality": {
                    "universe_coverage_pct": 0,
                    "signal_quality": "low",
                    "missing_inputs_count": 0,
                    "warning_count": 1,
                },
                "compute_duration_s": 0,
                "as_of": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "dashboard_metadata": build_dashboard_metadata(
                    "breadth_participation",
                    is_error_payload=True,
                    error_stage="compute",
                ),
            }

    # ── Model (LLM) Analysis ────────────────────────────────────

    def _run_model_analysis(
        self, engine_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Attempt LLM-based breadth analysis.

        Returns dict with model_analysis (or None) and error info if failed.
        Attaches ``normalized`` key via model_analysis_contract.
        """
        from common.model_sanitize import classify_model_error, user_facing_error_message
        from app.services.model_analysis_contract import wrap_service_model_response
        import time as _time

        t0 = _time.monotonic()
        requested_at = datetime.now(timezone.utc).isoformat()

        try:
            from common.model_analysis import analyze_breadth_participation
            result = analyze_breadth_participation(
                engine_result=engine_result,
                timeout=180,
                retries=0,
            )
            duration_ms = int((_time.monotonic() - t0) * 1000)
            logger.info(
                "event=breadth_model_analysis_ok score=%s label=%s",
                result.get("score"),
                result.get("label"),
            )
            outcome = {"model_analysis": result}
            return wrap_service_model_response(
                "breadth_participation", outcome,
                requested_at=requested_at, duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((_time.monotonic() - t0) * 1000)
            error_kind = classify_model_error(exc)
            error_msg = user_facing_error_message(error_kind)
            logger.warning(
                "event=breadth_model_analysis_failed error_kind=%s error=%s",
                error_kind, exc,
            )
            outcome = {
                "model_analysis": None,
                "error": {"kind": error_kind, "message": error_msg},
            }
            return wrap_service_model_response(
                "breadth_participation", outcome,
                requested_at=requested_at, duration_ms=duration_ms,
            )

    async def run_model_analysis(
        self, *, force: bool = False
    ) -> dict[str, Any]:
        """Run LLM model analysis on breadth data.

        Uses the base engine result as input (raw_inputs + pillar_scores).
        Caches result independently from engine cache.

        Returns
        -------
        dict with:
          model_analysis: LLM output or None
          error: dict | None — { kind, message } if model failed
          as_of: ISO timestamp
        """
        import asyncio

        model_cache_key = "breadth_participation:model"

        if not force:
            cached = await self.cache.get(model_cache_key)
            if cached is not None:
                logger.info("event=breadth_model_cache_hit")
                return cached

        logger.info("event=breadth_model_analysis_start force=%s", force)

        # Get base engine data (don't force — use cached if available)
        base = await self.get_breadth_analysis(force=False)
        engine_result = base.get("engine_result", {})

        # Run blocking model call in executor to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        model_outcome = await loop.run_in_executor(
            None, self._run_model_analysis, engine_result
        )

        result: dict[str, Any] = {
            "model_analysis": model_outcome.get("model_analysis"),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

        # Pass through error info if model failed
        if model_outcome.get("error"):
            result["error"] = model_outcome["error"]

        # Carry normalized contract through for downstream consumers
        if "normalized" in model_outcome:
            result["normalized"] = model_outcome["normalized"]

        # Only cache successful results — don't cache failures
        if result["model_analysis"] is not None:
            await self.cache.set(model_cache_key, result, self.ttl_seconds)

        has_model = result["model_analysis"] is not None
        logger.info(
            "event=breadth_model_analysis_complete has_model=%s cached_ttl=%d",
            has_model,
            self.ttl_seconds if has_model else 0,
        )

        return result
