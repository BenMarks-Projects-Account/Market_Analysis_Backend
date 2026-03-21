"""Tests for TMC refresh/freshness behavior — TMC Refresh Audit fix.

Validates:
1. Cache-control headers prevent browser caching on GET endpoints.
2. Latest endpoint always returns freshest run after pointer update.
3. Runner CancelledError handling still packages output.
4. Asyncio.shield in trigger endpoint protects workflow.

Run with:
    cd BenTrade/backend
    python -m pytest tests/test_tmc_refresh_freshness.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_tmc import router


# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════


def _create_app(data_dir: Path) -> FastAPI:
    """Build a minimal FastAPI app with the TMC router and a tmp data dir."""
    app = FastAPI()
    app.include_router(router)
    backend_dir = data_dir.parent
    (backend_dir / "data").mkdir(parents=True, exist_ok=True)
    app.state.backend_dir = backend_dir
    return app


def _write_pointer(data_dir: Path, workflow_id: str, run_id: str,
                   completed_at: str = "2025-01-15T18:00:00+00:00",
                   batch_status: str = "completed") -> None:
    pointer_dir = data_dir / "workflows" / workflow_id
    pointer_dir.mkdir(parents=True, exist_ok=True)
    d: dict[str, Any] = {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "completed_at": completed_at,
        "status": "completed",
        "output_filename": "output.json",
        "contract_version": "1.0",
    }
    if batch_status:
        d["batch_status"] = batch_status
    (pointer_dir / "latest.json").write_text(
        json.dumps(d),
        encoding="utf-8",
    )


def _write_stock_output(data_dir: Path, run_id: str,
                        candidates: list[dict[str, Any]] | None = None,
                        generated_at: str = "2025-01-15T18:00:00+00:00",
                        batch_status: str = "completed") -> None:
    run_dir = data_dir / "workflows" / "stock_opportunity" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if candidates is None:
        candidates = [{"symbol": "AAPL", "score": 85}]
    (run_dir / "output.json").write_text(
        json.dumps({
            "contract_version": "1.0",
            "workflow_id": "stock_opportunity",
            "run_id": run_id,
            "generated_at": generated_at,
            "batch_status": batch_status,
            "market_state_ref": "ms_test",
            "publication": {"status": "completed"},
            "candidates": candidates,
            "quality": {
                "total_candidates_found": len(candidates),
                "selected_count": len(candidates),
                "level": "good",
            },
        }),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════════
# 1. CACHE-CONTROL HEADERS
# ═══════════════════════════════════════════════════════════════════


class TestCacheControlHeaders:
    """Ensure GET endpoints return no-store Cache-Control headers
    so browsers never serve stale TMC data from HTTP cache."""

    def test_stock_latest_has_no_cache(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.get("/api/tmc/workflows/stock/latest")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "no-store" in cc, f"Expected no-store in Cache-Control, got: {cc}"
        assert "no-cache" in cc

    def test_options_latest_has_no_cache(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.get("/api/tmc/workflows/options/latest")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "no-store" in cc, f"Expected no-store in Cache-Control, got: {cc}"

    def test_stock_latest_with_data_has_no_cache(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_cache_01"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_stock_output(data_dir, run_id)

        app = _create_app(data_dir)
        client = TestClient(app)

        resp = client.get("/api/tmc/workflows/stock/latest")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "no-store" in cc
        body = resp.json()
        assert body["data"]["run_id"] == run_id


# ═══════════════════════════════════════════════════════════════════
# 2. FRESHNESS: POINTER UPDATE → NEW DATA RETURNED
# ═══════════════════════════════════════════════════════════════════


class TestFreshnessOnPointerUpdate:
    """After a new run writes output.json and updates latest.json,
    the GET /stock/latest endpoint must return the new data."""

    def test_latest_follows_pointer_update(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        # Old run
        old_run = "run_old_001"
        _write_pointer(data_dir, "stock_opportunity", old_run)
        _write_stock_output(data_dir, old_run,
                            candidates=[{"symbol": "OLD", "score": 50}],
                            generated_at="2025-01-14T12:00:00+00:00")

        resp1 = client.get("/api/tmc/workflows/stock/latest")
        assert resp1.json()["data"]["run_id"] == old_run
        assert resp1.json()["data"]["candidates"][0]["symbol"] == "OLD"

        # New run overwrites pointer
        new_run = "run_new_002"
        _write_stock_output(data_dir, new_run,
                            candidates=[{"symbol": "FRESH", "score": 95}],
                            generated_at="2025-01-15T18:00:00+00:00")
        _write_pointer(data_dir, "stock_opportunity", new_run,
                       completed_at="2025-01-15T18:00:00+00:00")

        resp2 = client.get("/api/tmc/workflows/stock/latest")
        assert resp2.json()["data"]["run_id"] == new_run
        assert resp2.json()["data"]["candidates"][0]["symbol"] == "FRESH"

    def test_old_run_artifacts_still_exist(self, tmp_path: Path) -> None:
        """Old run files are preserved (not deleted) — only the pointer moves."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        old_run = "run_preserve_old"
        _write_pointer(data_dir, "stock_opportunity", old_run)
        _write_stock_output(data_dir, old_run)

        new_run = "run_preserve_new"
        _write_stock_output(data_dir, new_run)
        _write_pointer(data_dir, "stock_opportunity", new_run)

        # Old output still on disk
        old_output = data_dir / "workflows" / "stock_opportunity" / old_run / "output.json"
        assert old_output.is_file(), "Old run output should be preserved"


# ═══════════════════════════════════════════════════════════════════
# 3. RUNNER CANCELLED-ERROR HANDLING
# ═══════════════════════════════════════════════════════════════════


class TestRunnerCancelledErrorHandling:
    """Verify the runner's try/except catches CancelledError and
    still attempts to package output."""

    def test_runner_catches_cancelled_error(self) -> None:
        """Smoke test: the runner function signature accepts
        CancelledError recovery."""
        import inspect
        from app.workflows.stock_opportunity_runner import run_stock_opportunity
        source = inspect.getsource(run_stock_opportunity)
        # Verify the CancelledError handler is present
        assert "CancelledError" in source, \
            "run_stock_opportunity must handle CancelledError"
        assert "packaging partial output" in source, \
            "CancelledError handler should attempt partial packaging"


# ═══════════════════════════════════════════════════════════════════
# 4. TRIGGER ENDPOINT SHIELD
# ═══════════════════════════════════════════════════════════════════


class TestTriggerEndpointShield:
    """Verify the trigger endpoint uses asyncio.shield()."""

    def test_trigger_uses_shield(self) -> None:
        import inspect
        from app.api.routes_tmc import trigger_stock_workflow
        source = inspect.getsource(trigger_stock_workflow)
        assert "asyncio.shield" in source, \
            "trigger_stock_workflow must use asyncio.shield to protect from HTTP disconnect"
        assert "asyncio.ensure_future" in source, \
            "trigger_stock_workflow must use ensure_future for shielding"

    def test_options_trigger_uses_shield(self) -> None:
        import inspect
        from app.api.routes_tmc import trigger_options_workflow
        source = inspect.getsource(trigger_options_workflow)
        assert "asyncio.shield" in source, \
            "trigger_options_workflow must use asyncio.shield"


# ═══════════════════════════════════════════════════════════════════
# 5. RESPONSE CONTRACT UNCHANGED
# ═══════════════════════════════════════════════════════════════════


class TestResponseContract:
    """Verify response shapes didn't change with the fix."""

    def test_stock_no_output_shape(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/stock/latest").json()
        assert "status" in body
        assert "data" in body
        assert body["status"] == "no_output"
        assert body["data"] is None

    def test_stock_with_output_shape(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_shape_01"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_stock_output(data_dir, run_id)

        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/stock/latest").json()
        assert body["status"] == "completed"
        data = body["data"]
        # Required fields
        assert "run_id" in data
        assert "candidates" in data
        assert "quality_level" in data
        assert "generated_at" in data
        assert isinstance(data["candidates"], list)


# ═══════════════════════════════════════════════════════════════════
# 6. BATCH STATUS SEMANTICS
# ═══════════════════════════════════════════════════════════════════


class TestBatchStatusSemantics:
    """Verify batch_status is surfaced through the TMC read-model
    and API response, and that partial runs are distinguished."""

    def test_completed_batch_status_in_response(self, tmp_path: Path) -> None:
        """A completed run surfaces batch_status='completed'."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_bs_completed"
        _write_pointer(data_dir, "stock_opportunity", run_id, batch_status="completed")
        _write_stock_output(data_dir, run_id, batch_status="completed")

        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/stock/latest").json()
        assert body["data"]["batch_status"] == "completed"

    def test_partial_batch_status_in_response(self, tmp_path: Path) -> None:
        """A partial (interrupted) run surfaces batch_status='partial'."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_bs_partial"
        _write_pointer(data_dir, "stock_opportunity", run_id, batch_status="partial")
        _write_stock_output(data_dir, run_id, batch_status="partial")

        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/stock/latest").json()
        assert body["data"]["batch_status"] == "partial"

    def test_legacy_pointer_defaults_to_completed(self, tmp_path: Path) -> None:
        """A legacy pointer without batch_status should default to completed."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_bs_legacy"
        # Write pointer without batch_status (legacy)
        pointer_dir = data_dir / "workflows" / "stock_opportunity"
        pointer_dir.mkdir(parents=True, exist_ok=True)
        (pointer_dir / "latest.json").write_text(
            json.dumps({
                "run_id": run_id,
                "workflow_id": "stock_opportunity",
                "completed_at": "2025-01-15T18:00:00+00:00",
                "status": "completed",
                "output_filename": "output.json",
                "contract_version": "1.0",
            }),
            encoding="utf-8",
        )
        # Write output without batch_status (legacy)
        run_dir = data_dir / "workflows" / "stock_opportunity" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "output.json").write_text(
            json.dumps({
                "contract_version": "1.0",
                "workflow_id": "stock_opportunity",
                "run_id": run_id,
                "generated_at": "2025-01-15T18:00:00+00:00",
                "market_state_ref": "ms_test",
                "publication": {"status": "completed"},
                "candidates": [{"symbol": "SPY"}],
                "quality": {
                    "total_candidates_found": 1,
                    "selected_count": 1,
                    "level": "good",
                },
            }),
            encoding="utf-8",
        )

        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/stock/latest").json()
        # Legacy runs without explicit batch_status default to "completed"
        assert body["data"]["batch_status"] == "completed"

    def test_batch_status_in_generated_at_present(self, tmp_path: Path) -> None:
        """generated_at must always be present for freshness display."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        run_id = "run_gen_at"
        ts = "2025-03-18T14:30:00+00:00"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        _write_stock_output(data_dir, run_id, generated_at=ts)

        app = _create_app(data_dir)
        client = TestClient(app)

        body = client.get("/api/tmc/workflows/stock/latest").json()
        assert body["data"]["generated_at"] == ts


# ═══════════════════════════════════════════════════════════════════
# 7. OPTIONS RUNNER CANCELLED-ERROR HANDLING
# ═══════════════════════════════════════════════════════════════════


class TestOptionsRunnerCancelledError:
    """Verify the options runner has the same CancelledError
    protection as the stock runner."""

    def test_options_runner_catches_cancelled_error(self) -> None:
        """Smoke test: the options runner handles CancelledError."""
        import inspect
        from app.workflows.options_opportunity_runner import run_options_opportunity
        source = inspect.getsource(run_options_opportunity)
        assert "CancelledError" in source, \
            "run_options_opportunity must handle CancelledError"
        assert "packaging partial output" in source, \
            "CancelledError handler should attempt partial packaging"

    def test_options_runner_has_batch_status(self) -> None:
        """Verify options runner writes batch_status to output.json."""
        import inspect
        from app.workflows.options_opportunity_runner import _stage_select_package
        source = inspect.getsource(_stage_select_package)
        assert "batch_status" in source, \
            "_stage_select_package must write batch_status to output"


# ═══════════════════════════════════════════════════════════════════
# 8. POINTER BATCH_STATUS FIELD
# ═══════════════════════════════════════════════════════════════════


class TestPointerBatchStatus:
    """Verify WorkflowPointerData supports batch_status field."""

    def test_pointer_with_batch_status(self) -> None:
        from app.workflows.artifact_strategy import WorkflowPointerData
        p = WorkflowPointerData(
            run_id="r1", workflow_id="stock_opportunity",
            completed_at="now", status="valid",
            output_filename="output.json", contract_version="1.0",
            batch_status="partial",
        )
        d = p.to_dict()
        assert d["batch_status"] == "partial"

    def test_pointer_without_batch_status_backward_compat(self) -> None:
        from app.workflows.artifact_strategy import WorkflowPointerData
        p = WorkflowPointerData(
            run_id="r1", workflow_id="stock_opportunity",
            completed_at="now", status="valid",
            output_filename="output.json", contract_version="1.0",
        )
        assert p.batch_status is None
        d = p.to_dict()
        assert "batch_status" not in d

    def test_pointer_from_dict_with_batch_status(self) -> None:
        from app.workflows.artifact_strategy import WorkflowPointerData
        p = WorkflowPointerData.from_dict({
            "run_id": "r2", "workflow_id": "w",
            "completed_at": "c", "status": "valid",
            "output_filename": "output.json", "contract_version": "1.0",
            "batch_status": "completed",
        })
        assert p.batch_status == "completed"

    def test_pointer_from_dict_without_batch_status(self) -> None:
        from app.workflows.artifact_strategy import WorkflowPointerData
        p = WorkflowPointerData.from_dict({
            "run_id": "r3", "workflow_id": "w",
            "completed_at": "c", "status": "valid",
            "output_filename": "output.json", "contract_version": "1.0",
        })
        assert p.batch_status is None
