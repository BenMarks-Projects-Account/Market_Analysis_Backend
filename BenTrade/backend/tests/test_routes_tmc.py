"""Tests for TMC API routes — Prompt 8.

Run with:
    cd BenTrade/backend
    python -m pytest tests/test_routes_tmc.py -v --tb=short
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_tmc import router


# ═══════════════════════════════════════════════════════════════════
# FIXTURES / HELPERS
# ═══════════════════════════════════════════════════════════════════


def _create_app(data_dir: Path) -> FastAPI:
    """Build a minimal FastAPI app with the TMC router and a tmp data dir."""
    app = FastAPI()
    app.include_router(router)

    # Simulate app.state.backend_dir — routes derive data_dir from this
    # data_dir IS the "data" folder, backend_dir is its parent
    backend_dir = data_dir.parent
    (backend_dir / "data").mkdir(parents=True, exist_ok=True)
    app.state.backend_dir = backend_dir

    return app


def _write_pointer(data_dir: Path, workflow_id: str, run_id: str) -> None:
    """Write a minimal valid workflow pointer file."""
    pointer_dir = data_dir / "workflows" / workflow_id
    pointer_dir.mkdir(parents=True, exist_ok=True)
    (pointer_dir / "latest.json").write_text(
        json.dumps({
            "run_id": run_id,
            "workflow_id": workflow_id,
            "completed_at": "2025-01-15T18:00:00+00:00",
            "status": "completed",
            "output_filename": "output.json",
            "contract_version": "1.0",
        }),
        encoding="utf-8",
    )


def _write_stock_output(data_dir: Path, run_id: str) -> None:
    """Write a stock-opportunity output.json."""
    run_dir = data_dir / "workflows" / "stock_opportunity" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "output.json").write_text(
        json.dumps({
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
        }),
        encoding="utf-8",
    )


def _write_options_output(data_dir: Path, run_id: str) -> None:
    """Write an options-opportunity output.json with diagnostics."""
    run_dir = data_dir / "workflows" / "options_opportunity" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "output.json").write_text(
        json.dumps({
            "contract_version": "1.0",
            "workflow_id": "options_opportunity",
            "run_id": run_id,
            "generated_at": "2025-01-15T18:00:05+00:00",
            "market_state_ref": "ms_abc123",
            "publication": {"status": "degraded"},
            "candidates": [
                {"strategy_id": "bull_put_spread", "symbol": "SPY", "ev": 12.5},
            ],
            "quality": {
                "total_candidates_found": 200,
                "selected_count": 1,
                "level": "fair",
            },
            "scan_diagnostics": {"scanners_run": 11},
            "validation_summary": {"passed": 1},
        }),
        encoding="utf-8",
    )


def _write_summary(data_dir: Path, workflow_id: str, run_id: str) -> None:
    """Write a summary.json for a given workflow run."""
    run_dir = data_dir / "workflows" / workflow_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "run_id": run_id,
            "workflow_id": workflow_id,
            "status": "completed",
            "started_at": "2025-01-15T17:59:50+00:00",
            "completed_at": "2025-01-15T18:00:00+00:00",
            "quality_level": "good",
            "market_state_ref": "ms_abc123",
            "total_candidates": 10,
            "selected_count": 2,
            "stages": [{"stage_key": "load_market_state"}, {"stage_key": "scan"}],
            "warnings": ["partial fallback"],
        }),
        encoding="utf-8",
    )


def _write_market_state_fixture(data_dir: Path) -> None:
    """Write a valid market_state pointer + artifact for runner tests."""
    ms_dir = data_dir / "market_state"
    ms_dir.mkdir(parents=True, exist_ok=True)
    artifact_filename = "market_state_20250115_175500.json"
    (ms_dir / artifact_filename).write_text(
        json.dumps({
            "contract_version": "1.0",
            "workflow_id": "market_intelligence",
            "run_id": "ms_test001",
            "generated_at": "2025-01-15T17:55:00+00:00",
            "publication": {"status": "completed"},
            "market_state": {
                "spy_price": 580.0, "spy_change_pct": 0.15,
                "qqq_price": 490.0, "qqq_change_pct": 0.12,
                "iwm_price": 220.0, "iwm_change_pct": -0.05,
                "dia_price": 420.0, "dia_change_pct": 0.08,
                "vix_level": 16.5, "vix_change_pct": -0.30,
                "market_regime": "bullish", "volatility_regime": "low",
                "breadth_reading": "positive", "composite_score": 72.0,
                "tone_classification": "risk_on",
            },
        }),
        encoding="utf-8",
    )
    (ms_dir / "latest.json").write_text(
        json.dumps({
            "artifact_filename": artifact_filename,
            "artifact_id": "ms_test001",
            "published_at": "2025-01-15T17:55:00+00:00",
            "status": "valid",
            "contract_version": "1.0",
        }),
        encoding="utf-8",
    )


# Stub services for trigger tests
class _StubStockService:
    async def scan(self) -> dict[str, Any]:
        return {"scan_results": [{"symbol": "AAPL", "score": 85}]}


class _StubOptionsService:
    async def scan(
        self, symbols: list[str], scanner_keys: list[str],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "scan_results": [{
                "scanner_key": "bull_put_spread_spy",
                "strategy_id": "bull_put_spread",
                "family_key": "vertical_spreads",
                "symbol": "SPY",
                "candidates": [{"strategy_id": "bull_put_spread", "ev": 12.5}],
                "rejected": [],
                "total_constructed": 50, "total_passed": 1, "total_rejected": 49,
                "reject_reason_counts": {}, "warning_counts": {},
                "phase_counts": [], "elapsed_ms": 100,
            }],
        }


# ═══════════════════════════════════════════════════════════════════
# 1. LATEST STOCK OPPORTUNITIES
# ═══════════════════════════════════════════════════════════════════


class TestGetLatestStockOpportunities:

    def test_no_output_returns_no_output_status(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.get("/api/tmc/workflows/stock/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "no_output"
        assert body["data"] is None

    def test_with_output_returns_read_model(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_stock_001"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_stock_output(data_dir, run_id)

        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.get("/api/tmc/workflows/stock/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["data"] is not None
        assert body["data"]["run_id"] == run_id
        assert body["data"]["market_state_ref"] == "ms_abc123"
        assert len(body["data"]["candidates"]) == 2

    def test_lineage_preserved(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_lin_stock"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_stock_output(data_dir, run_id)

        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/stock/latest").json()
        assert body["data"]["run_id"] == run_id
        assert body["data"]["generated_at"] == "2025-01-15T18:00:00+00:00"
        assert body["data"]["market_state_ref"] == "ms_abc123"


# ═══════════════════════════════════════════════════════════════════
# 2. LATEST OPTIONS OPPORTUNITIES
# ═══════════════════════════════════════════════════════════════════


class TestGetLatestOptionsOpportunities:

    def test_no_output_returns_no_output_status(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.get("/api/tmc/workflows/options/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "no_output"
        assert body["data"] is None

    def test_with_output_returns_read_model(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_opts_001"
        _write_pointer(data_dir, "options_opportunity", run_id)
        _write_options_output(data_dir, run_id)

        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.get("/api/tmc/workflows/options/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["data"]["selected_count"] == 1
        assert body["data"]["scan_diagnostics"]["scanners_run"] == 11

    def test_quant_fields_preserved(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_quant_opts"
        _write_pointer(data_dir, "options_opportunity", run_id)
        _write_options_output(data_dir, run_id)

        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/options/latest").json()
        assert body["data"]["candidates"][0]["ev"] == 12.5
        assert body["data"]["candidates"][0]["strategy_id"] == "bull_put_spread"


# ═══════════════════════════════════════════════════════════════════
# 3. LATEST RUN SUMMARIES
# ═══════════════════════════════════════════════════════════════════


class TestGetRunSummary:

    def test_stock_summary_no_output(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.get("/api/tmc/workflows/stock/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "no_output"
        assert body["data"] is None

    def test_options_summary_no_output(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.get("/api/tmc/workflows/options/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "no_output"

    def test_stock_summary_with_data(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_sum_stock"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_summary(data_dir, "stock_opportunity", run_id)

        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.get("/api/tmc/workflows/stock/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["data"]["run_id"] == run_id
        assert body["data"]["stage_count"] == 2
        assert body["data"]["warnings_count"] == 1

    def test_options_summary_with_data(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_sum_opts"
        _write_pointer(data_dir, "options_opportunity", run_id)
        _write_summary(data_dir, "options_opportunity", run_id)

        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.get("/api/tmc/workflows/options/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["run_id"] == run_id


# ═══════════════════════════════════════════════════════════════════
# 4. TRIGGER ENDPOINTS
# ═══════════════════════════════════════════════════════════════════


class TestTriggerEndpoints:

    def test_stock_trigger_no_deps_returns_failed(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.post("/api/tmc/workflows/stock/run")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert "not configured" in body["error"]

    def test_options_trigger_no_deps_returns_failed(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.post("/api/tmc/workflows/options/run")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert "not configured" in body["error"]

    def test_stock_trigger_with_deps(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write_market_state_fixture(data_dir)

        from app.workflows.stock_opportunity_runner import StockOpportunityDeps
        app = _create_app(data_dir)
        app.state.tmc_stock_deps = StockOpportunityDeps(
            stock_engine_service=_StubStockService(),
        )
        client = TestClient(app)

        resp = client.post(
            "/api/tmc/workflows/stock/run",
            json={"top_n": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["workflow_id"] == "stock_opportunity"
        assert body["run_id"] != ""
        assert body["status"] in ("completed", "degraded")

    def test_options_trigger_with_deps(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write_market_state_fixture(data_dir)

        from app.workflows.options_opportunity_runner import OptionsOpportunityDeps
        app = _create_app(data_dir)
        app.state.tmc_options_deps = OptionsOpportunityDeps(
            options_scanner_service=_StubOptionsService(),
        )
        client = TestClient(app)

        resp = client.post(
            "/api/tmc/workflows/options/run",
            json={"top_n": 3, "symbols": ["SPY"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["workflow_id"] == "options_opportunity"
        assert body["status"] in ("completed", "degraded")

    def test_stock_trigger_empty_body(self, tmp_path: Path) -> None:
        """POST with no body should still work (defaults)."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.post("/api/tmc/workflows/stock/run")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# 5. RESPONSE MODEL SHAPE STABILITY
# ═══════════════════════════════════════════════════════════════════


class TestResponseModelShapes:

    def test_trigger_response_keys(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.post("/api/tmc/workflows/stock/run").json()
        expected_keys = {
            "workflow_id", "run_id", "status", "started_at",
            "completed_at", "candidate_count", "warnings_count",
            "market_state_ref", "error",
        }
        assert set(body.keys()) == expected_keys

    def test_latest_response_keys(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/stock/latest").json()
        assert set(body.keys()) == {"status", "data"}

    def test_summary_response_keys(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/stock/summary").json()
        assert set(body.keys()) == {"status", "data"}

    def test_stock_data_keys_when_present(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_shape_stock"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_stock_output(data_dir, run_id)

        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/stock/latest").json()
        data = body["data"]
        expected_data_keys = {
            "run_id", "workflow_id", "generated_at", "market_state_ref",
            "status", "batch_status", "total_candidates", "selected_count",
            "quality_level", "candidates", "warnings",
        }
        assert set(data.keys()) == expected_data_keys

    def test_options_data_keys_when_present(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_shape_opts"
        _write_pointer(data_dir, "options_opportunity", run_id)
        _write_options_output(data_dir, run_id)

        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/options/latest").json()
        data = body["data"]
        expected_data_keys = {
            "run_id", "workflow_id", "generated_at", "market_state_ref",
            "status", "batch_status", "total_candidates", "selected_count",
            "quality_level", "candidates", "warnings",
            "scan_diagnostics", "validation_summary",
        }
        assert set(data.keys()) == expected_data_keys

    def test_summary_data_keys_when_present(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_shape_sum"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_summary(data_dir, "stock_opportunity", run_id)

        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/stock/summary").json()
        data = body["data"]
        expected_data_keys = {
            "run_id", "workflow_id", "status", "started_at",
            "completed_at", "market_state_ref", "total_candidates",
            "selected_count", "quality_level", "stage_count",
            "warnings_count",
        }
        assert set(data.keys()) == expected_data_keys
