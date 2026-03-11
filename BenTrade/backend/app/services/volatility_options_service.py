"""Volatility & Options Structure Service — orchestrator layer.

Coordinates data fetching, engine computation, and caching.
Follows the same pattern as BreadthService:
  1. Fetch raw data (via VolatilityOptionsDataProvider)
  2. Invoke engine (compute_volatility_scores)
  3. Cache result (via TTLCache)
  4. Return structured payload

Caching:
  - Engine result cached for VOL_CACHE_TTL seconds (default 120)
  - Cache key: 'volatility_options'
  - Model analysis cached separately: 'volatility_options:model'
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.services.volatility_options_data_provider import VolatilityOptionsDataProvider
from app.services.volatility_options_engine import compute_volatility_scores
from app.services.dashboard_metadata_contract import build_dashboard_metadata
from app.services.engine_output_contract import normalize_engine_output
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)

VOL_CACHE_TTL = 120


class VolatilityOptionsService:
    """Service layer for Volatility & Options Structure engine."""

    def __init__(
        self,
        data_provider: VolatilityOptionsDataProvider,
        cache: TTLCache,
        *,
        ttl_seconds: int = VOL_CACHE_TTL,
    ) -> None:
        self.data_provider = data_provider
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    async def get_volatility_analysis(
        self, *, force: bool = False
    ) -> dict[str, Any]:
        """Return full volatility & options structure analysis.

        Parameters
        ----------
        force : bool
            If True, bypass cache and recompute.
        """
        cache_key = "volatility_options"

        if not force:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                logger.info("event=vol_cache_hit")
                return cached

        logger.info("event=vol_compute_start force=%s", force)
        start = datetime.now(timezone.utc)

        try:
            # Step 1: Fetch raw data
            raw_data = await self.data_provider.fetch_volatility_data()

            # Step 2: Invoke engine
            engine_result = compute_volatility_scores(
                regime_data=raw_data["regime_data"],
                structure_data=raw_data["structure_data"],
                skew_data=raw_data["skew_data"],
                positioning_data=raw_data["positioning_data"],
            )

            # Step 3: Build response payload
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            payload = {
                "engine_result": engine_result,
                "data_quality": {
                    "signal_quality": engine_result.get("signal_quality", "low"),
                    "confidence_score": engine_result.get("confidence_score", 0),
                    "missing_inputs_count": len(engine_result.get("missing_inputs", [])),
                    "warning_count": len(engine_result.get("warnings", [])),
                    "data_sources": raw_data.get("data_sources", {}),
                    "metric_availability": raw_data.get("metric_availability", {}),
                },
                "compute_duration_s": round(duration, 2),
                "as_of": engine_result.get("as_of"),
            }
            payload["normalized"] = normalize_engine_output(
                "volatility_options", payload
            )
            payload["dashboard_metadata"] = build_dashboard_metadata(
                "volatility_options",
                engine_result=engine_result,
                compute_duration_s=round(duration, 2),
            )

            # Step 4: Cache
            await self.cache.set(cache_key, payload, self.ttl_seconds)
            logger.info(
                "event=vol_compute_complete score=%.2f label=%s "
                "duration_s=%.1f cached_ttl=%d",
                engine_result.get("score", 0),
                engine_result.get("label", "unknown"),
                duration,
                self.ttl_seconds,
            )

            return payload

        except Exception as exc:
            logger.error(
                "event=vol_compute_failed error=%s", exc, exc_info=True
            )
            return {
                "engine_result": {
                    "engine": "volatility_options",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "score": None,
                    "label": "Unavailable",
                    "short_label": "Unavailable",
                    "confidence_score": 0,
                    "signal_quality": "low",
                    "summary": f"Engine computation failed: {exc}",
                    "pillar_scores": {},
                    "pillar_explanations": {},
                    "strategy_scores": {},
                    "positive_contributors": [],
                    "negative_contributors": [],
                    "conflicting_signals": [],
                    "trader_takeaway": "Volatility data is currently unavailable.",
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
                "dashboard_metadata": build_dashboard_metadata(
                    "volatility_options",
                    is_error_payload=True,
                    error_stage="compute",
                ),
            }

    # ── Model (LLM) Analysis ────────────────────────────────────

    def _run_model_analysis(
        self, engine_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Blocking LLM-based volatility analysis.

        Attaches ``normalized`` key via model_analysis_contract.
        """
        from common.model_sanitize import classify_model_error, user_facing_error_message
        from app.services.model_analysis_contract import wrap_service_model_response
        import time as _time

        t0 = _time.monotonic()
        requested_at = datetime.now(timezone.utc).isoformat()

        try:
            from common.model_analysis import analyze_volatility_options
            result = analyze_volatility_options(
                engine_result=engine_result,
                timeout=180,
                retries=0,
            )
            duration_ms = int((_time.monotonic() - t0) * 1000)
            logger.info(
                "event=vol_model_analysis_ok score=%s label=%s",
                result.get("score"),
                result.get("label"),
            )
            outcome = {"model_analysis": result}
            return wrap_service_model_response(
                "volatility_options", outcome,
                requested_at=requested_at, duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((_time.monotonic() - t0) * 1000)
            error_kind = classify_model_error(exc)
            error_msg = user_facing_error_message(error_kind)
            logger.warning(
                "event=vol_model_analysis_failed error_kind=%s error=%s",
                error_kind, exc,
            )
            outcome = {
                "model_analysis": None,
                "error": {"kind": error_kind, "message": error_msg},
            }
            return wrap_service_model_response(
                "volatility_options", outcome,
                requested_at=requested_at, duration_ms=duration_ms,
            )

    async def run_model_analysis(
        self, *, force: bool = False
    ) -> dict[str, Any]:
        """Run LLM model analysis on volatility data."""
        import asyncio

        model_cache_key = "volatility_options:model"

        if not force:
            cached = await self.cache.get(model_cache_key)
            if cached is not None:
                logger.info("event=vol_model_cache_hit")
                return cached

        logger.info("event=vol_model_analysis_start force=%s", force)

        # Get base engine data (use cached if available)
        base = await self.get_volatility_analysis(force=False)
        engine_result = base.get("engine_result", {})

        # Run blocking model call in executor
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

        # Carry normalized contract through for downstream consumers
        if "normalized" in model_outcome:
            result["normalized"] = model_outcome["normalized"]

        # Only cache successful results
        if result["model_analysis"] is not None:
            await self.cache.set(model_cache_key, result, self.ttl_seconds)

        has_model = result["model_analysis"] is not None
        logger.info(
            "event=vol_model_analysis_complete has_model=%s cached_ttl=%d",
            has_model,
            self.ttl_seconds if has_model else 0,
        )

        return result
