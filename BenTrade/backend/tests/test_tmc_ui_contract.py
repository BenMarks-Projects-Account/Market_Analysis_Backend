"""Prompt 9 — TMC UI integration contract tests.

Validates that:
1. Backend TMC responses match the field shapes the new JS expects.
2. Status vocabulary is stable (completed/degraded/failed/no_output/unavailable).
3. Stock/options opportunity read models expose the fields the cards render.
4. Trigger responses have the fields the status badge reads.

Run with:
    cd BenTrade/backend
    python -m pytest tests/test_tmc_ui_contract.py -v --tb=short
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_tmc import router
from app.workflows.tmc_service import TMCStatus


# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def data_dir(tmp_path):
    """Create a data directory inside tmp_path/backend/data."""
    d = tmp_path / "backend" / "data"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def app(data_dir):
    """FastAPI app with TMC router and tmp data dir."""
    app = FastAPI()
    app.include_router(router)
    app.state.backend_dir = data_dir.parent  # backend_dir/data = data_dir
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _write_pointer(data_dir: Path, workflow_id: str, run_id: str) -> None:
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
    run_dir = data_dir / "workflows" / "stock_opportunity" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "output.json").write_text(
        json.dumps({
            "run_id": run_id,
            "workflow_id": "stock_opportunity",
            "generated_at": "2025-01-15T18:00:00+00:00",
            "market_state_ref": "ms_abc123",
            "publication_status": "completed",
            "total_candidates": 3,
            "selected_count": 2,
            "quality_level": "full",
            "candidates": [
                {
                    "symbol": "SPY",
                    "action": "buy",
                    "conviction": 0.82,
                    "rationale_summary": "Strong momentum",
                    "key_supporting_points": ["Trend up", "Volume high"],
                    "key_risks": ["Overextended"],
                    "strategy_type": "momentum",
                    "scanner_key": "top_momentum",
                },
                {
                    "symbol": "QQQ",
                    "action": "hold",
                    "conviction": 0.55,
                    "rationale_summary": "Neutral outlook",
                    "key_supporting_points": [],
                    "key_risks": [],
                    "strategy_type": "value",
                },
            ],
            "warnings": ["Market hours limited"],
        }),
        encoding="utf-8",
    )


def _write_options_output(data_dir: Path, run_id: str) -> None:
    run_dir = data_dir / "workflows" / "options_opportunity" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "output.json").write_text(
        json.dumps({
            "run_id": run_id,
            "workflow_id": "options_opportunity",
            "generated_at": "2025-01-15T18:00:00+00:00",
            "market_state_ref": "ms_abc123",
            "publication_status": "completed",
            "total_candidates": 5,
            "selected_count": 2,
            "quality_level": "full",
            "candidates": [
                {
                    "underlying": "SPY",
                    "strategy_id": "bull_put_spread",
                    "ev": 12.50,
                    "pop": 0.72,
                    "max_loss": -88.00,
                    "credit": 0.45,
                    "dte": 21,
                    "width": 5.00,
                    "legs": [
                        {"side": "sell", "strike": 540, "option_type": "put", "expiration": "2025-02-07"},
                        {"side": "buy", "strike": 535, "option_type": "put", "expiration": "2025-02-07"},
                    ],
                },
                {
                    "underlying": "QQQ",
                    "strategy_id": "iron_condor",
                    "ev": 8.30,
                    "pop": 0.65,
                    "max_loss": -200.00,
                    "credit": 1.20,
                    "dte": 30,
                    "width": 10.00,
                    "legs": [],
                },
            ],
            "scan_diagnostics": {"total_scanned": 120, "passed": 2},
            "validation_summary": {"valid": 2, "invalid": 0},
            "warnings": [],
        }),
        encoding="utf-8",
    )


def _write_stock_summary(data_dir: Path, run_id: str) -> None:
    run_dir = data_dir / "workflows" / "stock_opportunity" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "run_id": run_id,
            "workflow_id": "stock_opportunity",
            "status": "completed",
            "started_at": "2025-01-15T17:55:00+00:00",
            "completed_at": "2025-01-15T18:00:00+00:00",
            "market_state_ref": "ms_abc123",
            "total_candidates": 3,
            "selected_count": 2,
            "quality_level": "full",
            "stage_count": 4,
            "warnings_count": 1,
        }),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════════
# 1. TMC STATUS VOCABULARY
# ═══════════════════════════════════════════════════════════════════


class TestTMCStatusVocabulary:
    """The JS tmcStatusClass() maps these exact strings to CSS classes."""

    def test_all_statuses_are_strings(self):
        expected = {"completed", "degraded", "failed", "no_output", "unavailable"}
        actual = {s.value for s in TMCStatus}
        assert actual == expected

    def test_no_output_when_no_pointer(self, client):
        resp = client.get("/api/tmc/workflows/stock/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "no_output"
        assert body["data"] is None

    def test_no_output_options_when_no_pointer(self, client):
        resp = client.get("/api/tmc/workflows/options/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "no_output"
        assert body["data"] is None


# ═══════════════════════════════════════════════════════════════════
# 2. STOCK OPPORTUNITIES — UI FIELD CONTRACT
# ═══════════════════════════════════════════════════════════════════


class TestStockOpportunitiesContract:
    """Fields the JS buildStockCard() reads from resp.data.candidates[*]."""

    def test_stock_response_shape(self, client, data_dir):
        run_id = "run_stock_001"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_stock_output(data_dir, run_id)

        resp = client.get("/api/tmc/workflows/stock/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"

        data = body["data"]
        assert data is not None
        # Top-level fields the JS reads
        assert "run_id" in data
        assert "quality_level" in data
        assert "candidates" in data
        assert isinstance(data["candidates"], list)
        assert len(data["candidates"]) == 2

    def test_stock_candidate_fields(self, client, data_dir):
        run_id = "run_stock_002"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_stock_output(data_dir, run_id)

        resp = client.get("/api/tmc/workflows/stock/latest")
        c = resp.json()["data"]["candidates"][0]

        # Fields buildStockCard reads
        assert c["symbol"] == "SPY"
        assert c["action"] == "buy"
        assert c["conviction"] == 0.82
        assert c["rationale_summary"] == "Strong momentum"
        assert isinstance(c["key_supporting_points"], list)
        assert isinstance(c["key_risks"], list)
        assert "strategy_type" in c

    def test_stock_warnings_are_list(self, client, data_dir):
        run_id = "run_stock_003"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_stock_output(data_dir, run_id)

        data = client.get("/api/tmc/workflows/stock/latest").json()["data"]
        assert isinstance(data["warnings"], list)


# ═══════════════════════════════════════════════════════════════════
# 3. OPTIONS OPPORTUNITIES — UI FIELD CONTRACT
# ═══════════════════════════════════════════════════════════════════


class TestOptionsOpportunitiesContract:
    """Fields the JS buildOptionsCard() reads from resp.data.candidates[*]."""

    def test_options_response_shape(self, client, data_dir):
        run_id = "run_opts_001"
        _write_pointer(data_dir, "options_opportunity", run_id)
        _write_options_output(data_dir, run_id)

        resp = client.get("/api/tmc/workflows/options/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"

        data = body["data"]
        assert data is not None
        assert "run_id" in data
        assert "quality_level" in data
        assert "candidates" in data
        assert "scan_diagnostics" in data
        assert "validation_summary" in data
        assert len(data["candidates"]) == 2

    def test_options_candidate_quantitative_fields(self, client, data_dir):
        run_id = "run_opts_002"
        _write_pointer(data_dir, "options_opportunity", run_id)
        _write_options_output(data_dir, run_id)

        resp = client.get("/api/tmc/workflows/options/latest")
        c = resp.json()["data"]["candidates"][0]

        # Fields buildOptionsCard reads
        assert c["underlying"] == "SPY"
        assert c["strategy_id"] == "bull_put_spread"
        assert c["ev"] == 12.50
        assert c["pop"] == 0.72
        assert c["max_loss"] == -88.00
        assert c["credit"] == 0.45
        assert c["dte"] == 21
        assert c["width"] == 5.00

    def test_options_legs_are_list(self, client, data_dir):
        run_id = "run_opts_003"
        _write_pointer(data_dir, "options_opportunity", run_id)
        _write_options_output(data_dir, run_id)

        c = client.get("/api/tmc/workflows/options/latest").json()["data"]["candidates"][0]
        legs = c["legs"]
        assert isinstance(legs, list)
        assert len(legs) == 2
        leg = legs[0]
        assert "side" in leg
        assert "strike" in leg
        assert "option_type" in leg

    def test_scan_diagnostics_fields(self, client, data_dir):
        run_id = "run_opts_004"
        _write_pointer(data_dir, "options_opportunity", run_id)
        _write_options_output(data_dir, run_id)

        diag = client.get("/api/tmc/workflows/options/latest").json()["data"]["scan_diagnostics"]
        assert diag["total_scanned"] == 120
        assert diag["passed"] == 2


# ═══════════════════════════════════════════════════════════════════
# 4. SUMMARY ENDPOINT — UI FIELD CONTRACT
# ═══════════════════════════════════════════════════════════════════


class TestSummaryContract:
    """Fields the JS could read from summary responses."""

    def test_stock_summary_shape(self, client, data_dir):
        run_id = "run_sum_001"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_stock_summary(data_dir, run_id)

        resp = client.get("/api/tmc/workflows/stock/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in {"completed", "degraded", "failed"}

        data = body["data"]
        assert data is not None
        assert data["run_id"] == run_id
        assert data["started_at"] is not None
        assert data["completed_at"] is not None
        assert isinstance(data["total_candidates"], int)
        assert isinstance(data["selected_count"], int)
        assert isinstance(data["stage_count"], int)
        assert isinstance(data["warnings_count"], int)

    def test_summary_no_output_when_no_pointer(self, client):
        resp = client.get("/api/tmc/workflows/options/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "no_output"
        assert body["data"] is None


# ═══════════════════════════════════════════════════════════════════
# 5. TRIGGER RESPONSE CONTRACT
# ═══════════════════════════════════════════════════════════════════


class TestTriggerResponseContract:
    """Trigger endpoints return the right shape even when deps are absent."""

    def test_stock_trigger_without_deps_returns_error_shape(self, client):
        """When tmc_stock_deps is not wired, the run should fail gracefully."""
        resp = client.post("/api/tmc/workflows/stock/run", json={})
        # Without runner deps the backend should return a valid response
        # structure (may have status=failed or raise)
        assert resp.status_code in (200, 500, 422)
        if resp.status_code == 200:
            body = resp.json()
            assert "status" in body
            assert "run_id" in body

    def test_options_trigger_without_deps_returns_error_shape(self, client):
        resp = client.post("/api/tmc/workflows/options/run", json={})
        assert resp.status_code in (200, 500, 422)
        if resp.status_code == 200:
            body = resp.json()
            assert "status" in body
            assert "run_id" in body
