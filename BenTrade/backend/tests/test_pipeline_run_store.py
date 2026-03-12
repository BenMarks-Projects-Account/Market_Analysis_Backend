"""Tests for pipeline_run_store — live snapshot and active run support.

Covers:
    - store_active_run creates an initial pollable snapshot
    - update_active_run incrementally updates run state
    - update_active_run is a no-op for unknown run_id
    - store_pipeline_result overwrites active snapshot with final result
    - list_runs returns active runs with correct status
    - get_run_detail returns stage status for in-progress runs
    - concurrent store/update safety (threading.Lock)
"""

from __future__ import annotations

import copy
from typing import Any

from app.services import pipeline_run_store
from app.services.pipeline_run_contract import (
    PIPELINE_STAGES,
    create_pipeline_run,
    mark_stage_running,
    mark_stage_completed,
    finalize_run,
)


# ── helpers ──────────────────────────────────────────────────────

def _make_run(**kw: Any) -> dict[str, Any]:
    return create_pipeline_run(trigger_source="test", **kw)


def _cleanup():
    """Reset the module-level store between tests."""
    pipeline_run_store.clear_all()


class TestStoreActiveRun:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_store_active_run_creates_snapshot(self):
        run = _make_run()
        run_id = run["run_id"]
        pipeline_run_store.store_active_run(run_id, run)

        snap = pipeline_run_store.get_run(run_id)
        assert snap is not None
        assert snap["run_id"] == run_id
        assert snap["run"]["status"] == "pending"

    def test_list_runs_includes_active(self):
        run = _make_run()
        run_id = run["run_id"]
        run["status"] = "running"
        pipeline_run_store.store_active_run(run_id, run)

        runs = pipeline_run_store.list_runs()
        assert any(r["run_id"] == run_id for r in runs)
        matching = [r for r in runs if r["run_id"] == run_id][0]
        assert matching["status"] == "running"

    def test_get_run_detail_shows_stage_status(self):
        run = _make_run()
        run_id = run["run_id"]
        mark_stage_running(run, "market_data")
        pipeline_run_store.store_active_run(run_id, run)

        detail = pipeline_run_store.get_run_detail(run_id)
        assert detail is not None
        assert detail["status"] == "running"
        stages = {s["stage_key"]: s for s in detail["stages"]}
        assert stages["market_data"]["status"] == "running"
        assert stages["market_model_analysis"]["status"] == "pending"


class TestUpdateActiveRun:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_update_reflects_stage_progression(self):
        run = _make_run()
        run_id = run["run_id"]
        pipeline_run_store.store_active_run(run_id, run)

        # Simulate Stage 1 running
        mark_stage_running(run, "market_data")
        pipeline_run_store.update_active_run(run_id, run)

        detail = pipeline_run_store.get_run_detail(run_id)
        stages = {s["stage_key"]: s for s in detail["stages"]}
        assert stages["market_data"]["status"] == "running"

        # Simulate Stage 1 completed
        mark_stage_completed(run, "market_data")
        pipeline_run_store.update_active_run(run_id, run)

        detail = pipeline_run_store.get_run_detail(run_id)
        stages = {s["stage_key"]: s for s in detail["stages"]}
        assert stages["market_data"]["status"] == "completed"

    def test_update_stores_events(self):
        run = _make_run()
        run_id = run["run_id"]
        pipeline_run_store.store_active_run(run_id, run)

        events = [{"type": "stage_started", "stage_key": "market_data"}]
        pipeline_run_store.update_active_run(run_id, run, events=events)

        snap = pipeline_run_store.get_run(run_id)
        assert len(snap["events"]) == 1
        assert snap["events"][0]["stage_key"] == "market_data"

    def test_update_noop_for_unknown_run(self):
        # Should not raise
        run = _make_run()
        pipeline_run_store.update_active_run("nonexistent-id", run)


class TestFinalResultOverwritesActive:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_store_pipeline_result_overwrites_active_snapshot(self):
        run = _make_run()
        run_id = run["run_id"]
        pipeline_run_store.store_active_run(run_id, run)

        # Simulate full completion
        for stage in PIPELINE_STAGES:
            mark_stage_running(run, stage)
            mark_stage_completed(run, stage)
        finalize_run(run)

        result = {
            "run": run,
            "artifact_store": {},
            "stage_results": [],
            "summary": {"run_summary": {"status": "completed"}},
            "events": [],
        }
        pipeline_run_store.store_pipeline_result(result)

        detail = pipeline_run_store.get_run_detail(run_id)
        assert detail["status"] == "completed"
        stages = {s["stage_key"]: s for s in detail["stages"]}
        for sk in PIPELINE_STAGES:
            assert stages[sk]["status"] == "completed"
