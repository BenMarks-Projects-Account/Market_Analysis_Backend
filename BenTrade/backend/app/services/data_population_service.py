"""Data Population Service — manages the MI → model analysis pipeline.

Runs on startup and every 5 minutes via an asyncio background loop.

Pipeline order:
  Phase 1 (market_data) — MI workflow: collect data, run engines, assemble + publish market state.
  Phase 2 (model_analysis) — 6 per-engine LLM model analysis calls
       (breadth, volatility, cross-asset, flows, liquidity, news).
  These artefacts are prerequisites for the trade-building workflow.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.utils.market_hours import market_status
from app.workflows.market_intelligence_runner import (
    MarketIntelligenceDeps,
    run_scheduled_market_intelligence,
)

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300  # 5 minutes — regular session
_INTERVAL_EXTENDED = 600  # 10 minutes — pre/post-market
_INTERVAL_CLOSED = 1800  # 30 minutes — weekends, holidays, overnight

# Each entry: (attr on MarketIntelligenceDeps, label used for model-score persistence)
# Labels MUST match the canonical engine keys in market_state_contract.ENGINE_KEYS.
_ENGINE_MODEL_CALLS: list[tuple[str, str]] = [
    ("breadth_service", "breadth_participation"),
    ("volatility_options_service", "volatility_options"),
    ("cross_asset_macro_service", "cross_asset_macro"),
    ("flows_positioning_service", "flows_positioning"),
    ("liquidity_conditions_service", "liquidity_financial_conditions"),
    ("news_sentiment_service", "news_sentiment"),
]


@dataclass
class PopulationStatus:
    """Snapshot of the current data-population state."""

    phase: str = "idle"  # idle | market_data | model_analysis | completed | failed
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    run_count: int = 0
    last_result_status: str | None = None
    # Per-engine model analysis progress (populated during phase 2)
    model_progress: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "run_count": self.run_count,
            "last_result_status": self.last_result_status,
            "model_progress": self.model_progress,
        }


class DataPopulationService:
    """Orchestrates data population: market data collection + model analysis.

    Lifecycle:
    1. ``start()`` — kicks off the first run and starts the repeating loop.
    2. ``trigger()`` — manually starts a run (deduped if already running).
    3. ``stop()`` — cancels the background loop.
    """

    def __init__(
        self,
        data_dir: Path,
        mi_deps: MarketIntelligenceDeps,
    ) -> None:
        self._data_dir = data_dir
        self._mi_deps = mi_deps
        self._status = PopulationStatus()
        self._lock = asyncio.Lock()
        self._loop_task: asyncio.Task | None = None
        self._stopped = False

    @property
    def status(self) -> PopulationStatus:
        return self._status

    async def start(self) -> None:
        """Start the background scheduler. Runs first cycle immediately."""
        self._stopped = False
        self._loop_task = asyncio.create_task(self._run_loop())
        logger.info("event=data_population_scheduler_started interval_s=%d", INTERVAL_SECONDS)

    async def stop(self) -> None:
        """Stop the background scheduler."""
        self._stopped = True
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        logger.info("event=data_population_scheduler_stopped")

    async def trigger(self) -> PopulationStatus:
        """Manually trigger a run. Returns immediately if already running."""
        if self._status.phase in ("market_data", "model_analysis"):
            logger.info("event=data_population_trigger_skipped reason=already_running")
            return self._status
        asyncio.create_task(self._run_once())
        return self._status

    @staticmethod
    def _cycle_interval() -> int:
        """Pick sleep duration based on current market status."""
        status = market_status()
        if status == "open":
            return INTERVAL_SECONDS
        if status == "extended":
            return _INTERVAL_EXTENDED
        return _INTERVAL_CLOSED

    async def _run_loop(self) -> None:
        """Background loop: run immediately, then at adaptive intervals."""
        # First run on startup
        await self._run_once()
        while not self._stopped:
            try:
                interval = self._cycle_interval()
                logger.debug("event=data_population_sleep interval_s=%d", interval)
                await asyncio.sleep(interval)
                if not self._stopped:
                    await self._run_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("event=data_population_loop_error error=%s", exc, exc_info=True)
                interval = self._cycle_interval()
                await asyncio.sleep(interval)

    async def _run_once(self) -> None:
        """Execute one full data-population cycle.

        Phase 1 (market_data): MI workflow — collect, engines, assemble, publish.
        Phase 2 (model_analysis): 6 per-engine LLM model analysis calls.
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            self._status.started_at = now.isoformat()
            self._status.error = None
            self._status.completed_at = None
            self._status.model_progress = {}

            try:
                # ── Phase 1: Market data (MI workflow) ───────────────────
                self._status.phase = "market_data"
                logger.info("event=data_population_phase phase=market_data run=%d", self._status.run_count + 1)

                result = await run_scheduled_market_intelligence(
                    data_dir=self._data_dir,
                    deps=self._mi_deps,
                )
                mi_status = result.status if result else "unknown"
                logger.info("event=data_population_mi_complete status=%s", mi_status)

                if mi_status == "failed":
                    self._status.phase = "failed"
                    self._status.error = f"Market Intelligence failed: {result.error}"
                    self._status.last_result_status = "failed"
                    self._status.completed_at = datetime.now(timezone.utc).isoformat()
                    self._status.run_count += 1
                    return

                # ── Phase 2: Per-engine model analysis (concurrent dispatch) ──
                self._status.phase = "model_analysis"
                logger.info("event=data_population_phase phase=model_analysis run=%d", self._status.run_count + 1)

                # Initialize all engines to "pending" so the UI shows labels immediately.
                for _, label in _ENGINE_MODEL_CALLS:
                    self._status.model_progress[label] = "pending"

                # Build list of runnable engine tasks.
                runnable: list[tuple[str, str, object]] = []
                for attr, label in _ENGINE_MODEL_CALLS:
                    svc = getattr(self._mi_deps, attr, None)
                    if svc is None or not hasattr(svc, "run_model_analysis"):
                        self._status.model_progress[label] = "skipped"
                        logger.warning("event=model_analysis_skip engine=%s reason=no_service", label)
                        continue
                    runnable.append((attr, label, svc))

                model_errors: list[str] = []

                async def _run_engine(label: str, svc: object) -> None:
                    """Run a single engine's model analysis, updating status."""
                    self._status.model_progress[label] = "running"
                    logger.info("event=model_analysis_start engine=%s", label)
                    try:
                        model_result = await svc.run_model_analysis(force=True)  # type: ignore[union-attr]
                        if model_result.get("model_analysis"):
                            try:
                                from app.services.model_score_store import save_model_score
                                save_model_score(
                                    str(self._data_dir),
                                    label,
                                    model_result["model_analysis"],
                                    model_result.get("as_of"),
                                )
                            except Exception:
                                pass
                        self._status.model_progress[label] = "done"
                        logger.info("event=model_analysis_done engine=%s", label)
                    except Exception as exc:
                        self._status.model_progress[label] = "failed"
                        model_errors.append(f"{label}: {exc}")
                        logger.error("event=model_analysis_error engine=%s error=%s", label, exc, exc_info=True)

                # Dispatch all engines concurrently. The per-provider execution
                # gate ensures only one prompt is sent to each model endpoint at
                # a time, so this effectively feeds the next prompt to the next
                # available provider as soon as the previous one completes.
                tasks = [_run_engine(label, svc) for _, label, svc in runnable]
                await asyncio.gather(*tasks, return_exceptions=False)

                # ── Finalize ─────────────────────────────────────────────
                if model_errors:
                    self._status.phase = "completed"
                    self._status.error = f"Model analysis partial: {'; '.join(model_errors)}"
                    self._status.last_result_status = "partial"
                else:
                    self._status.phase = "completed"
                    self._status.last_result_status = mi_status

                self._status.completed_at = datetime.now(timezone.utc).isoformat()
                self._status.run_count += 1
                logger.info(
                    "event=data_population_complete run=%d status=%s",
                    self._status.run_count,
                    self._status.last_result_status,
                )

            except Exception as exc:
                self._status.phase = "failed"
                self._status.error = str(exc)
                self._status.completed_at = datetime.now(timezone.utc).isoformat()
                self._status.run_count += 1
                logger.error("event=data_population_error error=%s", exc, exc_info=True)
