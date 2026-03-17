"""Focused tests for DataPopulationService.

Coverage:
    - Status reports correctly through lifecycle
    - trigger() deduplicates when already running
    - start()/stop() manage the background loop
    - Failed MI run sets phase to 'failed'
    - Successful MI run proceeds to model analysis phase
    - 6 per-engine model analysis calls are invoked
    - Partial model failures still complete with error info
    - model_progress dict tracks per-engine status
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_population_service import (
    DataPopulationService,
    PopulationStatus,
    _ENGINE_MODEL_CALLS,
)
from app.workflows.market_intelligence_runner import MarketIntelligenceDeps


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════


def _make_mock_service() -> MagicMock:
    """Create a mock engine service with an async run_model_analysis."""
    svc = MagicMock()
    svc.run_model_analysis = AsyncMock(return_value={"model_analysis": {"ok": True}})
    return svc


def _stub_mi_deps(with_services: bool = False) -> MarketIntelligenceDeps:
    """Create MI deps. When with_services=True, populate with mock services."""
    if with_services:
        return MarketIntelligenceDeps(
            market_context_service=None,
            breadth_service=_make_mock_service(),
            volatility_options_service=_make_mock_service(),
            cross_asset_macro_service=_make_mock_service(),
            flows_positioning_service=_make_mock_service(),
            liquidity_conditions_service=_make_mock_service(),
            news_sentiment_service=_make_mock_service(),
        )
    return MarketIntelligenceDeps(
        market_context_service=None,
        breadth_service=None,
        volatility_options_service=None,
        cross_asset_macro_service=None,
        flows_positioning_service=None,
        liquidity_conditions_service=None,
        news_sentiment_service=None,
    )


@dataclass
class _FakeRunResult:
    status: str = "completed"
    error: str | None = None


# ══════════════════════════════════════════════════════════════════════
# TESTS
# ══════════════════════════════════════════════════════════════════════


class TestPopulationStatus:
    def test_default_status(self):
        s = PopulationStatus()
        assert s.phase == "idle"
        assert s.run_count == 0

    def test_to_dict_includes_model_progress(self):
        s = PopulationStatus(phase="model_analysis", run_count=3, model_progress={"breadth_participation": "done"})
        d = s.to_dict()
        assert d["phase"] == "model_analysis"
        assert d["model_progress"]["breadth_participation"] == "done"


class TestDataPopulationService:
    @pytest.mark.asyncio
    async def test_initial_status_is_idle(self, tmp_path: Path):
        svc = DataPopulationService(data_dir=tmp_path, mi_deps=_stub_mi_deps())
        assert svc.status.phase == "idle"

    @pytest.mark.asyncio
    async def test_successful_run_calls_all_6_model_analyses(self, tmp_path: Path):
        """After MI completes, all 6 per-engine model analysis calls should run."""
        deps = _stub_mi_deps(with_services=True)
        svc = DataPopulationService(data_dir=tmp_path, mi_deps=deps)
        fake_result = _FakeRunResult(status="completed")

        with patch(
            "app.services.data_population_service.run_scheduled_market_intelligence",
            new_callable=AsyncMock,
            return_value=fake_result,
        ):
            await svc._run_once()

        assert svc.status.phase == "completed"
        assert svc.status.run_count == 1
        assert svc.status.last_result_status == "completed"
        # All 6 engines should have been called
        for attr, label in _ENGINE_MODEL_CALLS:
            engine_svc = getattr(deps, attr)
            engine_svc.run_model_analysis.assert_awaited_once_with(force=True)
            assert svc.status.model_progress[label] == "done"

    @pytest.mark.asyncio
    async def test_model_analysis_partial_failure(self, tmp_path: Path):
        """One engine failing model analysis should not prevent others from running."""
        deps = _stub_mi_deps(with_services=True)
        # Make breadth service fail
        deps.breadth_service.run_model_analysis = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        svc = DataPopulationService(data_dir=tmp_path, mi_deps=deps)
        fake_result = _FakeRunResult(status="completed")

        with patch(
            "app.services.data_population_service.run_scheduled_market_intelligence",
            new_callable=AsyncMock,
            return_value=fake_result,
        ):
            await svc._run_once()

        assert svc.status.phase == "completed"
        assert svc.status.last_result_status == "partial"
        assert "breadth_participation" in (svc.status.error or "")
        assert svc.status.model_progress["breadth_participation"] == "failed"
        # Other engines should still have succeeded
        assert svc.status.model_progress["volatility_options"] == "done"
        assert svc.status.model_progress["news_sentiment"] == "done"

    @pytest.mark.asyncio
    async def test_null_services_are_skipped(self, tmp_path: Path):
        """When engine services are None, model analysis should be skipped (not crash)."""
        svc = DataPopulationService(data_dir=tmp_path, mi_deps=_stub_mi_deps())
        fake_result = _FakeRunResult(status="completed")

        with patch(
            "app.services.data_population_service.run_scheduled_market_intelligence",
            new_callable=AsyncMock,
            return_value=fake_result,
        ):
            await svc._run_once()

        assert svc.status.phase == "completed"
        for _, label in _ENGINE_MODEL_CALLS:
            assert svc.status.model_progress[label] == "skipped"

    @pytest.mark.asyncio
    async def test_failed_run_sets_failed(self, tmp_path: Path):
        svc = DataPopulationService(data_dir=tmp_path, mi_deps=_stub_mi_deps())
        fake_result = _FakeRunResult(status="failed", error="Engine timeout")

        with patch(
            "app.services.data_population_service.run_scheduled_market_intelligence",
            new_callable=AsyncMock,
            return_value=fake_result,
        ):
            await svc._run_once()

        assert svc.status.phase == "failed"
        assert svc.status.run_count == 1
        assert "Engine timeout" in (svc.status.error or "")

    @pytest.mark.asyncio
    async def test_exception_sets_failed(self, tmp_path: Path):
        svc = DataPopulationService(data_dir=tmp_path, mi_deps=_stub_mi_deps())

        with patch(
            "app.services.data_population_service.run_scheduled_market_intelligence",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection refused"),
        ):
            await svc._run_once()

        assert svc.status.phase == "failed"
        assert "connection refused" in (svc.status.error or "")

    @pytest.mark.asyncio
    async def test_trigger_deduplicates_when_running(self, tmp_path: Path):
        svc = DataPopulationService(data_dir=tmp_path, mi_deps=_stub_mi_deps())
        # Simulate already running
        svc._status.phase = "market_data"
        status = await svc.trigger()
        assert status.phase == "market_data"

    @pytest.mark.asyncio
    async def test_start_and_stop(self, tmp_path: Path):
        svc = DataPopulationService(data_dir=tmp_path, mi_deps=_stub_mi_deps(with_services=True))
        fake_result = _FakeRunResult(status="completed")

        with patch(
            "app.services.data_population_service.run_scheduled_market_intelligence",
            new_callable=AsyncMock,
            return_value=fake_result,
        ):
            await svc.start()
            # Let first cycle run
            await asyncio.sleep(0.2)
            await svc.stop()

        assert svc.status.run_count >= 1
        assert svc.status.phase == "completed"

    @pytest.mark.asyncio
    async def test_run_count_increments(self, tmp_path: Path):
        svc = DataPopulationService(data_dir=tmp_path, mi_deps=_stub_mi_deps())
        fake_result = _FakeRunResult(status="completed")

        with patch(
            "app.services.data_population_service.run_scheduled_market_intelligence",
            new_callable=AsyncMock,
            return_value=fake_result,
        ):
            await svc._run_once()
            await svc._run_once()

        assert svc.status.run_count == 2
