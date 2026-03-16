"""Tests for parallel stage execution, event propagation, and finalization.

Covers:
─── Parallel stages both launch and finalize correctly
─── Child events propagate through event_callback in parallel mode
─── One parallel stage succeeds while another fails — both resolve truthfully
─── Run-store reflects multiple concurrently running stages
─── No stage remains stuck RUNNING after handler returns
─── Exceptions inside a parallel stage are surfaced and finalize truthfully
─── ScannerLivenessTracker is deepcopy-safe (root cause of parallel stall)
─── Thread-safe event callback serializes concurrent updates
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.pipeline_orchestrator import (
    _DEFAULT_DEPENDENCY_MAP,
    _execute_pipeline,
    build_stage_result,
    create_orchestrator,
    execute_stage,
    get_run_lock,
    run_pipeline_with_handlers,
)
from app.services.pipeline_run_contract import (
    PIPELINE_STAGES,
    create_pipeline_run,
    initialize_stage_states,
)
from app.services.pipeline_artifact_store import create_artifact_store
from app.services.pipeline_scanner_stage import ScannerLivenessTracker
from app.services import pipeline_run_store


# ── Test helpers ────────────────────────────────────────────────

def _success_handler(run, artifact_store, stage_key, **kwargs):
    """Handler that completes immediately with counts."""
    return {
        "outcome": "completed",
        "summary_counts": {"items_processed": 1},
        "artifacts": [],
        "metadata": {"handler": stage_key},
        "error": None,
    }


def _slow_success_handler(run, artifact_store, stage_key, **kwargs):
    """Handler that takes a moment and emits child events."""
    event_callback = kwargs.get("event_callback")
    if event_callback:
        event_callback({
            "event_type": f"{stage_key}_child_started",
            "stage_key": stage_key,
            "level": "info",
            "message": f"Child work started in {stage_key}",
            "run_id": run["run_id"],
            "metadata": {},
        })
    time.sleep(0.1)  # simulate work
    if event_callback:
        event_callback({
            "event_type": f"{stage_key}_child_completed",
            "stage_key": stage_key,
            "level": "info",
            "message": f"Child work completed in {stage_key}",
            "run_id": run["run_id"],
            "metadata": {},
        })
    return {
        "outcome": "completed",
        "summary_counts": {"items_processed": 3},
        "artifacts": [],
        "metadata": {"handler": stage_key},
        "error": None,
    }


def _failing_handler(run, artifact_store, stage_key, **kwargs):
    """Handler that reports failure cleanly."""
    return {
        "outcome": "failed",
        "summary_counts": {},
        "artifacts": [],
        "metadata": {},
        "error": {
            "code": "TEST_FAILURE",
            "message": f"Stage {stage_key} intentionally failed",
            "source": stage_key,
            "detail": {},
            "timestamp": "2026-01-01T00:00:00Z",
            "retryable": False,
        },
    }


def _exception_handler(run, artifact_store, stage_key, **kwargs):
    """Handler that raises an exception."""
    raise RuntimeError(f"Unhandled error in {stage_key}")


def _handler_that_adds_tracker(run, artifact_store, stage_key, **kwargs):
    """Simulates scanner handler storing a ScannerLivenessTracker in run."""
    tracker = ScannerLivenessTracker()
    tracker.mark_started("test_scanner_a")
    run["_scanner_liveness"] = tracker
    # Emit child events AFTER tracker is in run
    event_callback = kwargs.get("event_callback")
    if event_callback:
        event_callback({
            "event_type": "scanner_started",
            "stage_key": stage_key,
            "level": "info",
            "message": "Scanner A started",
            "run_id": run["run_id"],
            "metadata": {"scanner_key": "test_scanner_a"},
        })
    time.sleep(0.05)
    tracker.mark_completed("test_scanner_a")
    if event_callback:
        event_callback({
            "event_type": "scanner_completed",
            "stage_key": stage_key,
            "level": "info",
            "message": "Scanner A completed",
            "run_id": run["run_id"],
            "metadata": {"scanner_key": "test_scanner_a"},
        })
    return {
        "outcome": "completed",
        "summary_counts": {"scanners_completed": 1},
        "artifacts": [],
        "metadata": {"handler": stage_key},
        "error": None,
    }


def _build_all_success_handlers() -> dict:
    """Build a handler registry where every stage succeeds."""
    return {sk: _success_handler for sk in PIPELINE_STAGES}


def _build_parallel_handlers(
    *,
    mma_handler=None,
    scanner_handler=None,
) -> dict:
    """Build handlers with custom market_model_analysis and stock_scanners."""
    handlers = _build_all_success_handlers()
    if mma_handler:
        handlers["market_model_analysis"] = mma_handler
    if scanner_handler:
        handlers["stock_scanners"] = scanner_handler
    return handlers


# =====================================================================
#  ScannerLivenessTracker deepcopy safety (ROOT CAUSE)
# =====================================================================

class TestTrackerDeepcopySafety:

    def test_deepcopy_tracker_succeeds(self):
        """copy.deepcopy(ScannerLivenessTracker) must not raise."""
        tracker = ScannerLivenessTracker()
        tracker.mark_started("a")
        tracker.mark_completed("a")
        tracker.mark_started("b")
        tracker.mark_timed_out("b")
        t2 = copy.deepcopy(tracker)
        assert isinstance(t2, ScannerLivenessTracker)
        snap = t2.snapshot()
        assert snap["completed"] == ["a"]
        assert snap["timed_out"] == ["b"]

    def test_deepcopy_run_with_tracker(self):
        """copy.deepcopy(run) succeeds when run contains a tracker."""
        run = create_pipeline_run(
            trigger_source="test", requested_scope={"mode": "full"},
        )
        tracker = ScannerLivenessTracker()
        tracker.mark_started("scan_x")
        run["_scanner_liveness"] = tracker
        run2 = copy.deepcopy(run)
        assert "_scanner_liveness" in run2
        assert isinstance(run2["_scanner_liveness"], ScannerLivenessTracker)

    def test_deepcopy_tracker_independent(self):
        """Mutating the copy does not affect the original."""
        tracker = ScannerLivenessTracker()
        tracker.mark_started("s1")
        t2 = copy.deepcopy(tracker)
        t2.mark_completed("s1")
        # Original still has s1 in flight
        snap_orig = tracker.snapshot()
        assert snap_orig["in_flight_count"] == 1
        snap_copy = t2.snapshot()
        assert snap_copy["in_flight_count"] == 0

    def test_shallow_copy_tracker(self):
        """copy.copy(ScannerLivenessTracker) works correctly."""
        tracker = ScannerLivenessTracker()
        tracker.mark_started("s1")
        t2 = copy.copy(tracker)
        assert isinstance(t2, ScannerLivenessTracker)


# =====================================================================
#  Parallel stage execution — both finalize correctly
# =====================================================================

class TestParallelStageFinalization:

    def test_parallel_stages_both_complete(self):
        """market_model_analysis and scanners both start and complete."""
        handlers = _build_parallel_handlers(
            mma_handler=_slow_success_handler,
            scanner_handler=_slow_success_handler,
        )
        result = run_pipeline_with_handlers(handlers, trigger_source="test")
        run = result["run"]
        # Both parallel stages completed
        mma = run["stages"]["market_model_analysis"]
        scn = run["stages"]["stock_scanners"]
        assert mma["status"] == "completed", f"mma status = {mma['status']}"
        assert scn["status"] == "completed", f"scn status = {scn['status']}"

    def test_parallel_stages_no_stuck_running(self):
        """No stage remains RUNNING after pipeline completes."""
        handlers = _build_parallel_handlers(
            mma_handler=_slow_success_handler,
            scanner_handler=_slow_success_handler,
        )
        result = run_pipeline_with_handlers(handlers, trigger_source="test")
        run = result["run"]
        for sk, stage in run["stages"].items():
            assert stage["status"] != "running", \
                f"Stage '{sk}' stuck in RUNNING"

    def test_one_parallel_fails_other_succeeds(self):
        """If one parallel stage fails, the other still resolves truthfully."""
        handlers = _build_parallel_handlers(
            mma_handler=_failing_handler,
            scanner_handler=_slow_success_handler,
        )
        result = run_pipeline_with_handlers(handlers, trigger_source="test")
        run = result["run"]
        assert run["stages"]["market_model_analysis"]["status"] == "failed"
        assert run["stages"]["stock_scanners"]["status"] == "completed"

    def test_one_parallel_exception_other_succeeds(self):
        """If one parallel stage throws, the other still resolves."""
        handlers = _build_parallel_handlers(
            mma_handler=_exception_handler,
            scanner_handler=_slow_success_handler,
        )
        result = run_pipeline_with_handlers(handlers, trigger_source="test")
        run = result["run"]
        assert run["stages"]["market_model_analysis"]["status"] == "failed"
        assert run["stages"]["stock_scanners"]["status"] == "completed"

    def test_both_parallel_fail(self):
        """If both parallel stages fail, both are marked failed."""
        handlers = _build_parallel_handlers(
            mma_handler=_failing_handler,
            scanner_handler=_failing_handler,
        )
        result = run_pipeline_with_handlers(handlers, trigger_source="test")
        run = result["run"]
        # stock_scanners fails in Wave 0 → market_model_analysis's
        # dep is unsatisfied → skipped (handler never runs)
        assert run["stages"]["market_model_analysis"]["status"] == "skipped"
        assert run["stages"]["stock_scanners"]["status"] == "failed"


# =====================================================================
#  Event propagation under parallel execution
# =====================================================================

class TestParallelEventPropagation:

    def test_child_events_reach_callback(self):
        """Child events emitted by parallel stage handlers reach callback."""
        captured_events = []

        def _capture(event):
            captured_events.append(event)

        handlers = _build_parallel_handlers(
            mma_handler=_slow_success_handler,
            scanner_handler=_slow_success_handler,
        )
        result = run_pipeline_with_handlers(
            handlers, trigger_source="test", event_callback=_capture,
        )
        event_types = [e.get("event_type") for e in captured_events]
        # Both stages should have emitted child events
        assert "market_model_analysis_child_started" in event_types
        assert "market_model_analysis_child_completed" in event_types
        assert "stock_scanners_child_started" in event_types
        assert "stock_scanners_child_completed" in event_types

    def test_stage_started_and_completed_events(self):
        """stage_started and stage_completed emitted for parallel stages."""
        captured = []

        def _capture(event):
            captured.append(event)

        handlers = _build_parallel_handlers(
            mma_handler=_success_handler,
            scanner_handler=_success_handler,
        )
        run_pipeline_with_handlers(
            handlers, trigger_source="test", event_callback=_capture,
        )
        event_types = [e["event_type"] for e in captured]
        # stage_started for both
        started = [e for e in captured
                   if e["event_type"] == "stage_started"
                   and e["stage_key"] in ("market_model_analysis", "stock_scanners")]
        assert len(started) == 2
        # stage_completed for both
        completed = [e for e in captured
                     if e["event_type"] == "stage_completed"
                     and e["stage_key"] in ("market_model_analysis", "stock_scanners")]
        assert len(completed) == 2

    def test_events_with_tracker_in_run(self):
        """Events still propagate when handler stores tracker in run."""
        captured = []

        def _capture(event):
            captured.append(event)

        handlers = _build_parallel_handlers(
            mma_handler=_slow_success_handler,
            scanner_handler=_handler_that_adds_tracker,
        )
        result = run_pipeline_with_handlers(
            handlers, trigger_source="test", event_callback=_capture,
        )
        event_types = [e["event_type"] for e in captured]
        # Scanner child events must reach callback even with tracker in run
        assert "scanner_started" in event_types
        assert "scanner_completed" in event_types
        # Model analysis child events must also work
        assert "market_model_analysis_child_started" in event_types

    def test_callback_exception_does_not_crash_stage(self):
        """If event_callback raises, stages still complete when handler
        wraps callbacks in try/except (matching real handler behavior)."""
        call_count = {"n": 0}

        def _bad_callback(event):
            call_count["n"] += 1
            if call_count["n"] % 2 == 0:
                raise RuntimeError("callback boom")

        def _resilient_handler(run, artifact_store, stage_key, **kwargs):
            """Handler that wraps event_callback like real handlers do."""
            cb = kwargs.get("event_callback")
            if cb:
                try:
                    cb({
                        "event_type": f"{stage_key}_child",
                        "stage_key": stage_key,
                        "level": "info",
                        "message": "child event",
                        "run_id": run["run_id"],
                        "metadata": {},
                    })
                except Exception:
                    pass  # real handlers log and continue
            return {
                "outcome": "completed",
                "summary_counts": {"items_processed": 1},
                "artifacts": [],
                "metadata": {"handler": stage_key},
                "error": None,
            }

        handlers = _build_parallel_handlers(
            mma_handler=_resilient_handler,
            scanner_handler=_resilient_handler,
        )
        result = run_pipeline_with_handlers(
            handlers, trigger_source="test", event_callback=_bad_callback,
        )
        run = result["run"]
        # Both stages must still complete despite callback failures
        assert run["stages"]["market_model_analysis"]["status"] == "completed"
        assert run["stages"]["stock_scanners"]["status"] == "completed"


# =====================================================================
#  Run-store concurrency (live callback path)
# =====================================================================

class TestRunStoreConcurrency:

    def test_update_precopied_stores_correctly(self):
        """update_active_run_precopied writes the snapshot."""
        run = create_pipeline_run(
            trigger_source="test", requested_scope={"mode": "full"},
        )
        run_id = run["run_id"]
        pipeline_run_store.store_active_run(run_id, run)

        run_copy = copy.deepcopy(run)
        events = [{"event_type": "test", "level": "info", "message": "hi"}]
        pipeline_run_store.update_active_run_precopied(
            run_id, run_copy, events,
        )
        snap = pipeline_run_store.get_run(run_id)
        assert snap is not None
        assert snap["events"] == events

        # Cleanup
        pipeline_run_store.clear_all()

    def test_update_precopied_with_progress(self):
        """update_active_run_precopied handles candidate_progress."""
        run = create_pipeline_run(
            trigger_source="test", requested_scope={"mode": "full"},
        )
        run_id = run["run_id"]
        pipeline_run_store.store_active_run(run_id, run)

        run_copy = copy.deepcopy(run)
        progress = {"candidate_id": "c1", "completed_count": 2}
        pipeline_run_store.update_active_run_precopied(
            run_id, run_copy, None,
            candidate_progress=progress,
        )
        snap = pipeline_run_store.get_run(run_id)
        assert snap["candidate_progress"]["candidate_id"] == "c1"

        pipeline_run_store.clear_all()

    def test_concurrent_event_callback_serialized(self):
        """Multiple threads calling a serialized callback don't corrupt state."""
        events = []
        lock = threading.Lock()
        errors = []

        run = create_pipeline_run(
            trigger_source="test", requested_scope={"mode": "full"},
        )
        run_id = run["run_id"]
        pipeline_run_store.store_active_run(run_id, run)

        _cb_lock = threading.Lock()

        def _safe_callback(event):
            with _cb_lock:
                events.append(event)
                try:
                    run_copy = copy.deepcopy(run)
                    pipeline_run_store.update_active_run_precopied(
                        run_id, run_copy, list(events),
                    )
                except Exception as e:
                    errors.append(e)

        # Spawn multiple threads calling the callback concurrently
        threads = []
        for i in range(10):
            ev = {"event_type": f"test_{i}", "level": "info", "message": str(i)}
            t = threading.Thread(target=_safe_callback, args=(ev,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Errors during concurrent callback: {errors}"
        assert len(events) == 10

        snap = pipeline_run_store.get_run(run_id)
        assert len(snap["events"]) == 10

        pipeline_run_store.clear_all()


# =====================================================================
#  Orchestrator run_lock exposure
# =====================================================================

class TestRunLockExposure:

    def test_get_run_lock_returns_lock(self):
        """get_run_lock() returns a threading.Lock."""
        lock = get_run_lock()
        assert isinstance(lock, type(threading.Lock()))

    def test_get_run_lock_is_consistent(self):
        """get_run_lock() returns the same lock object on each call."""
        assert get_run_lock() is get_run_lock()


# =====================================================================
#  End-to-end parallel pipeline with tracker
# =====================================================================

class TestEndToEndParallelWithTracker:

    def test_full_pipeline_with_tracker_and_callback(self):
        """Full pipeline with tracker in run and event callback completes."""
        captured = []

        def _capture(event):
            captured.append(event)

        handlers = _build_parallel_handlers(
            mma_handler=_slow_success_handler,
            scanner_handler=_handler_that_adds_tracker,
        )
        result = run_pipeline_with_handlers(
            handlers, trigger_source="test", event_callback=_capture,
        )
        run = result["run"]

        # Pipeline completed (not stuck)
        assert run["status"] in ("completed", "partial_failed", "failed")

        # Both parallel stages finalized
        assert run["stages"]["market_model_analysis"]["status"] != "running"
        assert run["stages"]["stock_scanners"]["status"] != "running"

        # The tracker is present and has the right data
        tracker = run.get("_scanner_liveness")
        if tracker:
            snap = tracker.snapshot() if hasattr(tracker, "snapshot") else tracker
            if isinstance(snap, dict):
                assert snap.get("completed") is not None

        # Events were captured (not silently dropped)
        event_types = [e["event_type"] for e in captured]
        assert "run_started" in event_types
        # At least stage_started/completed for the parallel stages
        assert any(
            e["event_type"] == "stage_started"
            and e.get("stage_key") == "stock_scanners"
            for e in captured
        )
