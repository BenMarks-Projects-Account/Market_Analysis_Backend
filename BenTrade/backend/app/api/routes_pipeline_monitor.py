"""Pipeline Monitor API routes.

Exposes the in-memory pipeline run store to the frontend Pipeline Monitor
dashboard and provides run-control endpoints (start, pause, resume, cancel).

Prefix: ``/api/pipeline`` (set in this file, not in ``main.py``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.services import pipeline_run_store
from app.services.pipeline_run_contract import PIPELINE_STAGES, STAGE_LABELS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline-monitor"])


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
    """Trigger a new pipeline run and return its run_id.

    Executes the real pipeline with the default (real) stage handlers.
    """
    from app.services.pipeline_orchestrator import run_pipeline

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        pass

    events: list[dict[str, Any]] = []

    def _capture_event(event: dict[str, Any]) -> None:
        events.append(event)

    try:
        result = run_pipeline(
            trigger_source=body.get("trigger_source", "trade-building-pipeline"),
            requested_scope=body.get("scope", {"mode": "full"}),
            event_callback=_capture_event,
        )
    except Exception as exc:
        logger.exception("event=pipeline_run_failed error=%s", exc)
        raise HTTPException(
            status_code=500,
            detail={"message": f"Pipeline execution failed: {exc}", "error_type": type(exc).__name__},
        )

    result["events"] = events
    run_id = pipeline_run_store.store_pipeline_result(result)
    logger.info("event=pipeline_run_started run_id=%s", run_id)

    return {
        "ok": True,
        "run_id": run_id,
        "status": result.get("run", {}).get("status", "unknown"),
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
