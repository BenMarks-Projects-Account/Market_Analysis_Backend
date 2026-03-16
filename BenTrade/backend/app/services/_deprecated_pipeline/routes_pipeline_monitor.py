"""Pipeline Monitor API routes.

Exposes the in-memory pipeline run store to the frontend Pipeline Monitor
dashboard and provides run-control endpoints (start, pause, resume, cancel).

Prefix: ``/api/pipeline`` (set in this file, not in ``main.py``).
"""

from __future__ import annotations

import copy
import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.services import pipeline_run_store
from app.services.pipeline_run_contract import PIPELINE_STAGES, STAGE_LABELS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline-monitor"])

# Guard against concurrent pipeline runs.
_active_run_lock = threading.Lock()
_active_run_id: str | None = None


# ---------------------------------------------------------------------------
# List runs
# ---------------------------------------------------------------------------


@router.get("/runs")
async def list_pipeline_runs(request: Request) -> dict[str, Any]:
    """Return compact summaries of all stored pipeline runs (newest first)."""
    runs = pipeline_run_store.list_runs()
    return {
        "runs": runs,
        "count": len(runs),
        "stage_order": list(PIPELINE_STAGES),
    }


# ---------------------------------------------------------------------------
# Run control — start / pause / resume / cancel
# ---------------------------------------------------------------------------
# IMPORTANT: These routes MUST be declared BEFORE /runs/{run_id} below.
# Starlette matches paths in declaration order; if /runs/{run_id} comes
# first it swallows /runs/start as run_id="start" and returns 405.
# ---------------------------------------------------------------------------
# NOTE: pause and resume are scaffolded stubs. The pipeline orchestrator
# currently runs synchronously; true pause/resume requires an async
# execution model. The API contracts are defined here so the frontend
# can wire up controls now and the backend can be upgraded later.
# ---------------------------------------------------------------------------


@router.post("/runs/start")
async def start_pipeline_run(request: Request) -> dict[str, Any]:
    """Trigger a new pipeline run and return its run_id immediately.

    The pipeline executes in a background thread so the frontend can poll
    ``GET /runs/{run_id}`` for live stage-by-stage progress.
    """
    global _active_run_id

    from app.services.pipeline_orchestrator import (
        create_orchestrator,
        _execute_pipeline,
    )

    with _active_run_lock:
        if _active_run_id is not None:
            return {
                "ok": False,
                "message": "A pipeline run is already in progress.",
                "run_id": _active_run_id,
                "status": "running",
            }

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        pass

    trigger = body.get("trigger_source", "trade-building-pipeline")
    scope = body.get("scope", {"mode": "full"})

    events: list[dict[str, Any]] = []

    # Build orchestrator so we can snapshot the run object up front.
    orch = create_orchestrator(
        trigger_source=trigger,
        requested_scope=scope,
    )
    run = orch["run"]
    run_id = run["run_id"]

    # Capture events AND incrementally update the store on every stage.
    # Serialise callbacks so concurrent stage threads don't race on
    # copy.deepcopy(run) / list mutations (see Root Cause note below).
    _cb_lock = threading.Lock()

    # Import the orchestrator's run-mutation lock so we can hold it
    # during the deepcopy of `run`, preventing mark_stage_* races.
    from app.services.pipeline_orchestrator import get_run_lock
    _rl = get_run_lock()

    # Also capture the artifact store reference so stage-completion
    # callbacks can propagate artifacts to the snapshot mid-run.
    _artifact_store = orch["artifact_store"]

    def _live_callback(event: dict[str, Any]) -> None:
        # ROOT CAUSE NOTE  (parallel-stage stall):
        # Without _cb_lock, multiple stage threads call deepcopy(run)
        # concurrently.  Without _rl, mark_stage_* can mutate the run
        # dict while deepcopy iterates it, causing RuntimeError.  And
        # prior to adding __deepcopy__ on ScannerLivenessTracker, the
        # tracker's threading.Lock raised TypeError on every deepcopy.
        with _cb_lock:
            events.append(event)
            # Extract per-candidate progress from Step 14 events.
            candidate_progress = None
            event_type = event.get("event_type", "")
            if event_type == "candidate_execution_completed":
                meta = event.get("metadata") or {}
                candidate_progress = {
                    "candidate_id": meta.get("candidate_id"),
                    "symbol": meta.get("symbol"),
                    "queue_position": meta.get("queue_position"),
                    "candidate_status": meta.get("candidate_status"),
                    "completed_count": meta.get("completed_count"),
                    "remaining_count": meta.get("remaining_count"),
                    "elapsed_ms": meta.get("elapsed_ms"),
                }
            # Hold the run-mutation lock during deepcopy to prevent
            # mark_stage_* from modifying run mid-copy.
            with _rl:
                run_copy = copy.deepcopy(run)
            events_copy = list(events)  # shallow; events are immutable dicts

            # Propagate artifact store on stage completion/failure so
            # endpoints like Scanner Review see artifacts mid-run.
            artifact_store_copy = None
            if event_type in ("stage_completed", "stage_failed"):
                artifact_store_copy = copy.deepcopy(_artifact_store)

            pipeline_run_store.update_active_run_precopied(
                run_id, run_copy, events_copy,
                candidate_progress=candidate_progress,
                artifact_store_copy=artifact_store_copy,
            )

    orch["event_callback"] = _live_callback

    # Store an initial "pending" snapshot so the UI can poll immediately.
    pipeline_run_store.store_active_run(run_id, run)

    with _active_run_lock:
        _active_run_id = run_id

    def _background() -> None:
        global _active_run_id
        try:
            result = _execute_pipeline(orch)
            result["events"] = events
            pipeline_run_store.store_pipeline_result(result)
            logger.info("event=pipeline_run_completed run_id=%s", run_id)
        except Exception as exc:
            logger.exception("event=pipeline_run_failed run_id=%s error=%s", run_id, exc)
            # Ensure the snapshot reflects failure — use the safe
            # precopied path with the run-lock held during deepcopy.
            try:
                run["status"] = "failed"
                with _rl:
                    run_copy = copy.deepcopy(run)
                pipeline_run_store.update_active_run_precopied(
                    run_id, run_copy, list(events),
                )
            except Exception:
                logger.exception(
                    "event=pipeline_snapshot_failed run_id=%s "
                    "(could not persist failure snapshot)", run_id,
                )
        finally:
            with _active_run_lock:
                _active_run_id = None

    thread = threading.Thread(target=_background, name=f"pipeline-{run_id}", daemon=True)
    thread.start()
    logger.info("event=pipeline_run_started run_id=%s", run_id)

    return {
        "ok": True,
        "run_id": run_id,
        "status": "running",
    }


@router.post("/runs/{run_id}/pause")
async def pause_pipeline_run(run_id: str, request: Request) -> dict[str, Any]:
    """Pause a running pipeline (stub — not yet implemented)."""
    snap = pipeline_run_store.get_run(run_id)
    if snap is None:
        raise HTTPException(status_code=404, detail={"message": f"Run '{run_id}' not found"})
    return {
        "ok": False,
        "run_id": run_id,
        "implemented": False,
        "message": "Pause is scaffolded but not yet backed by async run control. Pipeline runs synchronously.",
    }


@router.post("/runs/{run_id}/resume")
async def resume_pipeline_run(run_id: str, request: Request) -> dict[str, Any]:
    """Resume a paused pipeline (stub — not yet implemented)."""
    snap = pipeline_run_store.get_run(run_id)
    if snap is None:
        raise HTTPException(status_code=404, detail={"message": f"Run '{run_id}' not found"})
    return {
        "ok": False,
        "run_id": run_id,
        "implemented": False,
        "message": "Resume is scaffolded but not yet backed by async run control. Pipeline runs synchronously.",
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_pipeline_run(run_id: str, request: Request) -> dict[str, Any]:
    """Cancel a running pipeline (stub — not yet implemented)."""
    snap = pipeline_run_store.get_run(run_id)
    if snap is None:
        raise HTTPException(status_code=404, detail={"message": f"Run '{run_id}' not found"})
    return {
        "ok": False,
        "run_id": run_id,
        "implemented": False,
        "message": "Cancel is scaffolded but not yet backed by async run control. Pipeline runs synchronously.",
    }


# ---------------------------------------------------------------------------
# Run detail  (MUST be after /runs/start to avoid path collision)
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}")
async def get_pipeline_run(run_id: str, request: Request) -> dict[str, Any]:
    """Return the full UI-ready detail payload for a single run."""
    detail = pipeline_run_store.get_run_detail(run_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail={"message": f"Pipeline run '{run_id}' not found", "run_id": run_id},
        )
    return detail


# ---------------------------------------------------------------------------
# Artifact detail
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/artifacts/{artifact_id}")
async def get_pipeline_artifact(
    run_id: str,
    artifact_id: str,
    request: Request,
) -> dict[str, Any]:
    """Return the full artifact record (including raw data) for inspection."""
    art = pipeline_run_store.get_run_artifact(run_id, artifact_id)
    if art is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Artifact '{artifact_id}' not found in run '{run_id}'",
                "run_id": run_id,
                "artifact_id": artifact_id,
            },
        )
    return art


# ---------------------------------------------------------------------------
# Events / logs
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/events")
async def get_pipeline_events(
    run_id: str,
    request: Request,
    level: str | None = Query(default=None, description="Filter by level (info, warning, error)"),
    stage_key: str | None = Query(default=None, description="Filter by stage"),
) -> dict[str, Any]:
    """Return log events for a pipeline run with optional filters."""
    snap = pipeline_run_store.get_run(run_id)
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail={"message": f"Pipeline run '{run_id}' not found", "run_id": run_id},
        )
    events = snap.get("events", [])
    if level:
        events = [e for e in events if e.get("level") == level]
    if stage_key:
        events = [e for e in events if e.get("stage_key") == stage_key]
    return {"run_id": run_id, "events": events, "count": len(events)}


# ---------------------------------------------------------------------------
# Demo run (creates a synthetic pipeline result for UI testing)
# ---------------------------------------------------------------------------


@router.post("/demo-run")
async def create_demo_run(request: Request) -> dict[str, Any]:
    """Execute a full pipeline with default stub handlers and store the result.

    This is a convenience endpoint for testing the Pipeline Monitor UI
    without requiring real market data or model calls.
    """
    from app.services.pipeline_orchestrator import run_pipeline

    events: list[dict[str, Any]] = []

    def _capture_event(event: dict[str, Any]) -> None:
        events.append(event)

    result = run_pipeline(
        trigger_source="demo",
        requested_scope={"mode": "demo", "note": "Pipeline Monitor UI test run"},
        event_callback=_capture_event,
    )

    # Attach captured events to result before storing
    result["events"] = events

    run_id = pipeline_run_store.store_pipeline_result(result)
    logger.info("event=demo_pipeline_run_stored run_id=%s", run_id)

    return {
        "ok": True,
        "run_id": run_id,
        "status": result.get("run", {}).get("status", "unknown"),
        "message": "Demo pipeline run created and stored for monitor inspection.",
    }


# ---------------------------------------------------------------------------
# Store management
# ---------------------------------------------------------------------------


@router.get("/status")
async def pipeline_store_status(request: Request) -> dict[str, Any]:
    """Return store health / counts."""
    return {
        "stored_runs": pipeline_run_store.run_count(),
        "max_runs": pipeline_run_store._MAX_RUNS,
        "stage_count": len(PIPELINE_STAGES),
        "stages": list(PIPELINE_STAGES),
    }


# ---------------------------------------------------------------------------
# Dependency map (for graph visualisation)
# ---------------------------------------------------------------------------


@router.get("/dependency-map")
async def get_dependency_map(request: Request) -> dict[str, Any]:
    """Return the canonical pipeline dependency graph and stage labels."""
    from app.services.pipeline_orchestrator import get_default_dependency_map

    dep_map = get_default_dependency_map()
    return {
        "dependency_map": dep_map,
        "stage_order": list(PIPELINE_STAGES),
        "stage_labels": dict(STAGE_LABELS),
    }


# (Run control routes moved above /runs/{run_id} — see top of file)
