"""Tests for TMC execution service and compact read models — Prompt 7.

Run with:
    cd BenTrade/backend
    python -m pytest tests/test_tmc_service.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.workflows.tmc_service import (
    TMCExecutionResult,
    TMCExecutionService,
    TMCStatus,
    OptionsOpportunityReadModel,
    StockOpportunityReadModel,
    WorkflowRunSummaryReadModel,
    load_latest_options_output,
    load_latest_stock_output,
    load_latest_run_summary,
    _run_status_to_tmc,
)


# ═══════════════════════════════════════════════════════════════════
# FIXTURES / HELPERS
# ═══════════════════════════════════════════════════════════════════


def _write_pointer(data_dir: Path, workflow_id: str, run_id: str) -> Path:
    """Write a minimal valid pointer file."""
    pointer_dir = data_dir / "workflows" / workflow_id
    pointer_dir.mkdir(parents=True, exist_ok=True)
    pointer_path = pointer_dir / "latest.json"
    pointer_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "workflow_id": workflow_id,
                "completed_at": "2025-01-15T18:00:00+00:00",
                "status": "completed",
                "output_filename": "output.json",
                "contract_version": "1.0",
            }
        ),
        encoding="utf-8",
    )
    return pointer_path


def _write_stock_output(data_dir: Path, run_id: str) -> Path:
    """Write a stock-opportunity output.json with two candidates."""
    run_dir = data_dir / "workflows" / "stock_opportunity" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    output = {
        "contract_version": "1.0",
        "workflow_id": "stock_opportunity",
        "run_id": run_id,
        "generated_at": "2025-01-15T18:00:00+00:00",
        "market_state_ref": "ms_abc123",
        "publication": {"status": "completed"},
        "candidates": [
            {"symbol": "AAPL", "score": 85},
            {"symbol": "MSFT", "score": 78},
        ],
        "quality": {
            "total_candidates_found": 10,
            "selected_count": 2,
            "level": "good",
        },
    }
    path = run_dir / "output.json"
    path.write_text(json.dumps(output), encoding="utf-8")
    return path


def _write_options_output(data_dir: Path, run_id: str) -> Path:
    """Write an options-opportunity output.json with scan diagnostics."""
    run_dir = data_dir / "workflows" / "options_opportunity" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    output = {
        "contract_version": "1.0",
        "workflow_id": "options_opportunity",
        "run_id": run_id,
        "generated_at": "2025-01-15T18:00:05+00:00",
        "market_state_ref": "ms_abc123",
        "publication": {"status": "degraded"},
        "candidates": [
            {"strategy_id": "bull_put_spread", "symbol": "SPY", "ev": 12.5},
            {"strategy_id": "iron_condor", "symbol": "QQQ", "ev": 8.3},
            {"strategy_id": "calendar", "symbol": "IWM", "ev": 5.1},
        ],
        "quality": {
            "total_candidates_found": 200,
            "selected_count": 3,
            "level": "fair",
        },
        "scan_diagnostics": {"scanners_run": 11, "scanners_ok": 9},
        "validation_summary": {"passed": 3, "failed": 0},
    }
    path = run_dir / "output.json"
    path.write_text(json.dumps(output), encoding="utf-8")
    return path


def _write_summary(
    data_dir: Path,
    workflow_id: str,
    run_id: str,
    *,
    status: str = "completed",
) -> Path:
    """Write a summary.json for a given workflow run."""
    run_dir = data_dir / "workflows" / workflow_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "status": status,
        "started_at": "2025-01-15T17:59:50+00:00",
        "completed_at": "2025-01-15T18:00:00+00:00",
        "quality_level": "good",
        "market_state_ref": "ms_abc123",
        "total_candidates": 10,
        "selected_count": 2,
        "stages": [{"stage_key": "load_market_state"}, {"stage_key": "scan"}],
        "warnings": ["partial scanner fallback"],
    }
    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    return path


# Stub services for execution tests
class _StubStockService:
    """Stub stock engine service that returns canned results."""

    async def scan(self) -> dict[str, Any]:
        return {
            "scan_results": [
                {"symbol": "AAPL", "score": 85, "source": "stub"},
            ],
        }


class _StubOptionsService:
    """Stub options scanner service that returns canned results."""

    async def scan(
        self,
        symbols: list[str],
        scanner_keys: list[str],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "scan_results": [
                {
                    "scanner_key": "bull_put_spread_spy",
                    "strategy_id": "bull_put_spread",
                    "family_key": "vertical_spreads",
                    "symbol": "SPY",
                    "candidates": [
                        {"strategy_id": "bull_put_spread", "symbol": "SPY", "ev": 12.5},
                    ],
                    "rejected": [],
                    "total_constructed": 50,
                    "total_passed": 1,
                    "total_rejected": 49,
                    "reject_reason_counts": {},
                    "warning_counts": {},
                    "phase_counts": [],
                    "elapsed_ms": 100,
                },
            ],
        }


def _write_market_state_fixture(data_dir: Path) -> str:
    """Write a valid market_state latest.json + artifact for runners.

    Uses the market_state_discovery PointerData shape:
        artifact_filename, artifact_id, published_at, status, contract_version
    Artifact is stored flat in data/market_state/ (no run subfolders).
    """
    ms_dir = data_dir / "market_state"
    ms_dir.mkdir(parents=True, exist_ok=True)
    artifact_id = "ms_test001"
    artifact_filename = "market_state_20250115_175500.json"

    # Minimal market state shape (15 required keys)
    market_state = {
        "contract_version": "1.0",
        "workflow_id": "market_intelligence",
        "run_id": artifact_id,
        "generated_at": "2025-01-15T17:55:00+00:00",
        "publication": {"status": "completed"},
        "market_state": {
            "spy_price": 580.0,
            "spy_change_pct": 0.15,
            "qqq_price": 490.0,
            "qqq_change_pct": 0.12,
            "iwm_price": 220.0,
            "iwm_change_pct": -0.05,
            "dia_price": 420.0,
            "dia_change_pct": 0.08,
            "vix_level": 16.5,
            "vix_change_pct": -0.30,
            "market_regime": "bullish",
            "volatility_regime": "low",
            "breadth_reading": "positive",
            "composite_score": 72.0,
            "tone_classification": "risk_on",
        },
    }
    (ms_dir / artifact_filename).write_text(
        json.dumps(market_state), encoding="utf-8"
    )
    # Pointer (market_state_discovery.PointerData shape)
    (ms_dir / "latest.json").write_text(
        json.dumps(
            {
                "artifact_filename": artifact_filename,
                "artifact_id": artifact_id,
                "published_at": "2025-01-15T17:55:00+00:00",
                "status": "valid",
                "contract_version": "1.0",
            }
        ),
        encoding="utf-8",
    )
    return artifact_id


# ═══════════════════════════════════════════════════════════════════
# 1. TMC STATUS VOCABULARY
# ═══════════════════════════════════════════════════════════════════


class TestTMCStatus:
    """Tests for the TMCStatus enum."""

    def test_all_values_are_strings(self) -> None:
        for member in TMCStatus:
            assert isinstance(member.value, str)

    def test_expected_members(self) -> None:
        expected = {"completed", "degraded", "failed", "no_output", "unavailable"}
        assert {m.value for m in TMCStatus} == expected

    def test_string_enum_identity(self) -> None:
        assert TMCStatus.COMPLETED == "completed"
        assert TMCStatus.DEGRADED == "degraded"
        assert TMCStatus.FAILED == "failed"


# ═══════════════════════════════════════════════════════════════════
# 2. STATUS MAPPING
# ═══════════════════════════════════════════════════════════════════


class TestStatusMapping:
    """Tests for _run_status_to_tmc()."""

    def test_failed_maps_to_failed(self) -> None:
        assert _run_status_to_tmc("failed", None) == TMCStatus.FAILED
        assert _run_status_to_tmc("failed", "completed") == TMCStatus.FAILED

    def test_degraded_publication_maps_to_degraded(self) -> None:
        assert _run_status_to_tmc("completed", "degraded") == TMCStatus.DEGRADED

    def test_completed_maps_to_completed(self) -> None:
        assert _run_status_to_tmc("completed", "completed") == TMCStatus.COMPLETED
        assert _run_status_to_tmc("completed", None) == TMCStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════
# 3. TMC EXECUTION RESULT
# ═══════════════════════════════════════════════════════════════════


class TestTMCExecutionResult:
    """Tests for TMCExecutionResult data class."""

    def test_to_dict_completed(self) -> None:
        r = TMCExecutionResult(
            workflow_id="stock_opportunity",
            run_id="r1",
            status=TMCStatus.COMPLETED,
            started_at="2025-01-15T17:59:50+00:00",
            completed_at="2025-01-15T18:00:00+00:00",
            candidate_count=5,
            warnings_count=0,
            market_state_ref="ms_abc123",
        )
        d = r.to_dict()
        assert d["workflow_id"] == "stock_opportunity"
        assert d["candidate_count"] == 5
        assert d["market_state_ref"] == "ms_abc123"
        assert "error" not in d

    def test_to_dict_failed_includes_error(self) -> None:
        r = TMCExecutionResult(
            workflow_id="options_opportunity",
            run_id="r2",
            status=TMCStatus.FAILED,
            started_at="2025-01-15T17:59:50+00:00",
            completed_at="2025-01-15T18:00:00+00:00",
            error="Scanner timeout",
        )
        d = r.to_dict()
        assert d["error"] == "Scanner timeout"
        assert d["status"] == "failed"

    def test_frozen(self) -> None:
        r = TMCExecutionResult(
            workflow_id="x", run_id="r", status="ok",
            started_at="a", completed_at="b",
        )
        with pytest.raises(AttributeError):
            r.status = "changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════
# 4. LATEST STOCK OUTPUT READER
# ═══════════════════════════════════════════════════════════════════


class TestLoadLatestStockOutput:
    """Tests for load_latest_stock_output()."""

    def test_returns_none_when_no_pointer(self, tmp_path: Path) -> None:
        result = load_latest_stock_output(tmp_path)
        assert result is None

    def test_returns_none_when_pointer_but_no_output(self, tmp_path: Path) -> None:
        _write_pointer(tmp_path, "stock_opportunity", "r1")
        result = load_latest_stock_output(tmp_path)
        assert result is None

    def test_loads_valid_stock_output(self, tmp_path: Path) -> None:
        run_id = "run_001"
        _write_pointer(tmp_path, "stock_opportunity", run_id)
        _write_stock_output(tmp_path, run_id)

        result = load_latest_stock_output(tmp_path)
        assert result is not None
        assert isinstance(result, StockOpportunityReadModel)
        assert result.run_id == run_id
        assert result.workflow_id == "stock_opportunity"
        assert result.total_candidates == 10
        assert result.selected_count == 2
        assert result.quality_level == "good"
        assert len(result.candidates) == 2
        assert result.market_state_ref == "ms_abc123"
        assert result.status == TMCStatus.COMPLETED

    def test_lineage_preserved(self, tmp_path: Path) -> None:
        """market_state_ref and run_id must survive into read model."""
        run_id = "run_lin"
        _write_pointer(tmp_path, "stock_opportunity", run_id)
        _write_stock_output(tmp_path, run_id)
        result = load_latest_stock_output(tmp_path)
        assert result is not None
        assert result.run_id == run_id
        assert result.market_state_ref == "ms_abc123"

    def test_to_dict_roundtrip(self, tmp_path: Path) -> None:
        run_id = "run_rt"
        _write_pointer(tmp_path, "stock_opportunity", run_id)
        _write_stock_output(tmp_path, run_id)
        result = load_latest_stock_output(tmp_path)
        assert result is not None
        d = result.to_dict()
        assert d["run_id"] == run_id
        assert isinstance(d["candidates"], list)
        assert len(d["candidates"]) == 2

    def test_corrupt_pointer_returns_none(self, tmp_path: Path) -> None:
        ptr_dir = tmp_path / "workflows" / "stock_opportunity"
        ptr_dir.mkdir(parents=True)
        (ptr_dir / "latest.json").write_text("not json", encoding="utf-8")
        result = load_latest_stock_output(tmp_path)
        assert result is None


# ═══════════════════════════════════════════════════════════════════
# 5. LATEST OPTIONS OUTPUT READER
# ═══════════════════════════════════════════════════════════════════


class TestLoadLatestOptionsOutput:
    """Tests for load_latest_options_output()."""

    def test_returns_none_when_no_pointer(self, tmp_path: Path) -> None:
        result = load_latest_options_output(tmp_path)
        assert result is None

    def test_loads_valid_options_output(self, tmp_path: Path) -> None:
        run_id = "run_opt_001"
        _write_pointer(tmp_path, "options_opportunity", run_id)
        _write_options_output(tmp_path, run_id)

        result = load_latest_options_output(tmp_path)
        assert result is not None
        assert isinstance(result, OptionsOpportunityReadModel)
        assert result.run_id == run_id
        assert result.total_candidates == 200
        assert result.selected_count == 3
        assert len(result.candidates) == 3
        assert result.status == TMCStatus.DEGRADED  # publication was degraded
        assert result.scan_diagnostics["scanners_run"] == 11
        assert result.validation_summary["passed"] == 3

    def test_quant_fields_preserved(self, tmp_path: Path) -> None:
        """Options candidates must retain EV and strategy_id."""
        run_id = "run_quant"
        _write_pointer(tmp_path, "options_opportunity", run_id)
        _write_options_output(tmp_path, run_id)
        result = load_latest_options_output(tmp_path)
        assert result is not None
        assert result.candidates[0]["ev"] == 12.5
        assert result.candidates[0]["strategy_id"] == "bull_put_spread"

    def test_to_dict_includes_diagnostics(self, tmp_path: Path) -> None:
        run_id = "run_diag"
        _write_pointer(tmp_path, "options_opportunity", run_id)
        _write_options_output(tmp_path, run_id)
        result = load_latest_options_output(tmp_path)
        assert result is not None
        d = result.to_dict()
        assert "scan_diagnostics" in d
        assert "validation_summary" in d

    def test_corrupt_output_returns_none(self, tmp_path: Path) -> None:
        run_id = "run_bad"
        _write_pointer(tmp_path, "options_opportunity", run_id)
        run_dir = tmp_path / "workflows" / "options_opportunity" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "output.json").write_text("{{broken", encoding="utf-8")
        result = load_latest_options_output(tmp_path)
        assert result is None


# ═══════════════════════════════════════════════════════════════════
# 6. LATEST RUN SUMMARY READER
# ═══════════════════════════════════════════════════════════════════


class TestLoadLatestRunSummary:
    """Tests for load_latest_run_summary()."""

    def test_returns_none_when_no_pointer(self, tmp_path: Path) -> None:
        result = load_latest_run_summary(tmp_path, "stock_opportunity")
        assert result is None

    def test_returns_none_when_pointer_but_no_summary(self, tmp_path: Path) -> None:
        _write_pointer(tmp_path, "stock_opportunity", "r1")
        result = load_latest_run_summary(tmp_path, "stock_opportunity")
        assert result is None

    def test_loads_valid_summary(self, tmp_path: Path) -> None:
        run_id = "run_sum01"
        _write_pointer(tmp_path, "stock_opportunity", run_id)
        _write_summary(tmp_path, "stock_opportunity", run_id)

        result = load_latest_run_summary(tmp_path, "stock_opportunity")
        assert result is not None
        assert isinstance(result, WorkflowRunSummaryReadModel)
        assert result.run_id == run_id
        assert result.stage_count == 2
        assert result.warnings_count == 1
        assert result.total_candidates == 10
        assert result.quality_level == "good"

    def test_to_dict(self, tmp_path: Path) -> None:
        run_id = "run_sdict"
        _write_pointer(tmp_path, "stock_opportunity", run_id)
        _write_summary(tmp_path, "stock_opportunity", run_id)
        result = load_latest_run_summary(tmp_path, "stock_opportunity")
        assert result is not None
        d = result.to_dict()
        assert d["stage_count"] == 2
        assert d["warnings_count"] == 1


# ═══════════════════════════════════════════════════════════════════
# 7. TMC EXECUTION SERVICE — READ METHODS
# ═══════════════════════════════════════════════════════════════════


class TestTMCExecutionServiceReads:
    """Tests for TMCExecutionService read model methods."""

    def test_get_latest_stock_none(self, tmp_path: Path) -> None:
        tmc = TMCExecutionService(data_dir=tmp_path)
        assert tmc.get_latest_stock_opportunities() is None

    def test_get_latest_options_none(self, tmp_path: Path) -> None:
        tmc = TMCExecutionService(data_dir=tmp_path)
        assert tmc.get_latest_options_opportunities() is None

    def test_get_latest_stock_with_output(self, tmp_path: Path) -> None:
        run_id = "r_stock"
        _write_pointer(tmp_path, "stock_opportunity", run_id)
        _write_stock_output(tmp_path, run_id)

        tmc = TMCExecutionService(data_dir=tmp_path)
        result = tmc.get_latest_stock_opportunities()
        assert result is not None
        assert result.run_id == run_id

    def test_get_latest_options_with_output(self, tmp_path: Path) -> None:
        run_id = "r_opts"
        _write_pointer(tmp_path, "options_opportunity", run_id)
        _write_options_output(tmp_path, run_id)

        tmc = TMCExecutionService(data_dir=tmp_path)
        result = tmc.get_latest_options_opportunities()
        assert result is not None
        assert result.run_id == run_id

    def test_get_latest_run_summary(self, tmp_path: Path) -> None:
        run_id = "r_sum"
        _write_pointer(tmp_path, "stock_opportunity", run_id)
        _write_summary(tmp_path, "stock_opportunity", run_id)

        tmc = TMCExecutionService(data_dir=tmp_path)
        result = tmc.get_latest_run_summary("stock_opportunity")
        assert result is not None
        assert result.run_id == run_id


# ═══════════════════════════════════════════════════════════════════
# 8. TMC EXECUTION SERVICE — EXECUTION TRIGGERS
# ═══════════════════════════════════════════════════════════════════


class TestTMCExecutionServiceTriggers:
    """Tests for TMCExecutionService run triggers."""

    @pytest.mark.asyncio
    async def test_stock_trigger_no_deps_fails(self, tmp_path: Path) -> None:
        tmc = TMCExecutionService(data_dir=tmp_path)
        result = await tmc.run_stock_opportunities()
        assert result.status == TMCStatus.FAILED
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_options_trigger_no_deps_fails(self, tmp_path: Path) -> None:
        tmc = TMCExecutionService(data_dir=tmp_path)
        result = await tmc.run_options_opportunities()
        assert result.status == TMCStatus.FAILED
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_stock_trigger_with_deps(self, tmp_path: Path) -> None:
        """Stock run with a stub service — produces a completed result."""
        _write_market_state_fixture(tmp_path)

        from app.workflows.stock_opportunity_runner import StockOpportunityDeps

        deps = StockOpportunityDeps(stock_engine_service=_StubStockService())
        tmc = TMCExecutionService(data_dir=tmp_path, stock_deps=deps)
        result = await tmc.run_stock_opportunities(top_n=5)

        assert result.workflow_id == "stock_opportunity"
        assert result.run_id != ""
        assert result.status in (TMCStatus.COMPLETED, TMCStatus.DEGRADED)
        assert isinstance(result.to_dict(), dict)

    @pytest.mark.asyncio
    async def test_options_trigger_with_deps(self, tmp_path: Path) -> None:
        """Options run with a stub service — produces a completed result."""
        _write_market_state_fixture(tmp_path)

        from app.workflows.options_opportunity_runner import OptionsOpportunityDeps

        deps = OptionsOpportunityDeps(options_scanner_service=_StubOptionsService())
        tmc = TMCExecutionService(data_dir=tmp_path, options_deps=deps)
        result = await tmc.run_options_opportunities(
            top_n=5,
            symbols=["SPY"],
        )

        assert result.workflow_id == "options_opportunity"
        assert result.run_id != ""
        assert result.status in (TMCStatus.COMPLETED, TMCStatus.DEGRADED)


# ═══════════════════════════════════════════════════════════════════
# 9. READ MODEL FROZEN INVARIANT
# ═══════════════════════════════════════════════════════════════════


class TestReadModelFrozen:
    """Ensure all read models are immutable."""

    def test_stock_read_model_frozen(self, tmp_path: Path) -> None:
        run_id = "r_frz_s"
        _write_pointer(tmp_path, "stock_opportunity", run_id)
        _write_stock_output(tmp_path, run_id)
        model = load_latest_stock_output(tmp_path)
        assert model is not None
        with pytest.raises(AttributeError):
            model.run_id = "changed"  # type: ignore[misc]

    def test_options_read_model_frozen(self, tmp_path: Path) -> None:
        run_id = "r_frz_o"
        _write_pointer(tmp_path, "options_opportunity", run_id)
        _write_options_output(tmp_path, run_id)
        model = load_latest_options_output(tmp_path)
        assert model is not None
        with pytest.raises(AttributeError):
            model.run_id = "changed"  # type: ignore[misc]

    def test_summary_read_model_frozen(self, tmp_path: Path) -> None:
        run_id = "r_frz_sum"
        _write_pointer(tmp_path, "stock_opportunity", run_id)
        _write_summary(tmp_path, "stock_opportunity", run_id)
        model = load_latest_run_summary(tmp_path, "stock_opportunity")
        assert model is not None
        with pytest.raises(AttributeError):
            model.run_id = "changed"  # type: ignore[misc]
