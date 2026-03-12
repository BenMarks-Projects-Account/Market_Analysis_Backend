"""In-memory pipeline run store — holds recent pipeline results for the monitor UI.

Keeps a bounded ring of completed (or failed) pipeline run snapshots so the
frontend Pipeline Monitor can inspect run status, stage progression, artifacts,
and candidate ledgers without needing persistent storage.

Module role: ``service`` — state container for pipeline run results.
"""

from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from app.services.pipeline_artifact_store import (
    list_artifacts,
    list_stage_artifacts,
    summarize_artifact_store,
)
from app.services.pipeline_run_contract import (
    PIPELINE_STAGES,
    run_summary,
)

_MODULE_ROLE = "service"
_MAX_RUNS = 50  # bounded ring size

# ---------------------------------------------------------------------------
# Store singleton
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_runs: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Write API
# ---------------------------------------------------------------------------


def store_pipeline_result(result: dict[str, Any]) -> str:
    """Persist a pipeline result snapshot (from ``run_pipeline`` / ``run_pipeline_with_handlers``).

    Parameters
    ----------
    result : dict
        The orchestrator result dict containing ``run``, ``artifact_store``,
        ``stage_results``, and ``summary`` keys.

    Returns
    -------
    str
        The ``run_id`` of the stored snapshot.
    """
    run = result.get("run") or {}
    run_id = run.get("run_id", "")
    if not run_id:
        return ""

    snapshot: dict[str, Any] = {
        "run_id": run_id,
        "stored_at": _now_iso(),
        "run": copy.deepcopy(run),
        "artifact_store": copy.deepcopy(result.get("artifact_store", {})),
        "stage_results": copy.deepcopy(result.get("stage_results", [])),
        "summary": copy.deepcopy(result.get("summary", {})),
        "events": copy.deepcopy(result.get("events", [])),
    }

    with _lock:
        _runs[run_id] = snapshot
        # Evict oldest if over capacity
        while len(_runs) > _MAX_RUNS:
            _runs.popitem(last=False)

    return run_id


def store_active_run(run_id: str, run: dict[str, Any]) -> None:
    """Create an initial in-progress snapshot so the UI can poll immediately.

    Called once at the start of a background pipeline run, before any stages
    execute.  Subsequent stage transitions call ``update_active_run``.
    """
    if not run_id:
        return
    snapshot: dict[str, Any] = {
        "run_id": run_id,
        "stored_at": _now_iso(),
        "run": copy.deepcopy(run),
        "artifact_store": {},
        "stage_results": [],
        "summary": {},
        "events": [],
    }
    with _lock:
        _runs[run_id] = snapshot
        while len(_runs) > _MAX_RUNS:
            _runs.popitem(last=False)


def update_active_run(
    run_id: str,
    run: dict[str, Any],
    *,
    events: list[dict[str, Any]] | None = None,
    candidate_progress: dict[str, Any] | None = None,
) -> None:
    """Incrementally update the snapshot for an in-progress run.

    Called by the event callback on every stage transition so the polling
    endpoint returns current state.

    Parameters
    ----------
    run_id : str
        The run to update.
    run : dict
        Current run state (deep-copied into snapshot).
    events : list | None
        Latest events list.
    candidate_progress : dict | None
        Per-candidate execution progress from Step 14 sequential queue.
        Contains: current_candidate_id, current_candidate_symbol,
        completed_count, remaining_count, total_runnable,
        queue_position, candidate_status, elapsed_ms, timestamp.
    """
    with _lock:
        snap = _runs.get(run_id)
        if snap is None:
            return
        snap["run"] = copy.deepcopy(run)
        snap["stored_at"] = _now_iso()
        if events is not None:
            snap["events"] = copy.deepcopy(events)
        if candidate_progress is not None:
            snap["candidate_progress"] = copy.deepcopy(candidate_progress)


def clear_all() -> int:
    """Remove all stored runs. Returns count removed."""
    with _lock:
        count = len(_runs)
        _runs.clear()
        return count


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def list_runs() -> list[dict[str, Any]]:
    """Return compact summaries of all stored runs, newest first."""
    with _lock:
        snapshots = list(_runs.values())

    rows: list[dict[str, Any]] = []
    for snap in reversed(snapshots):
        run = snap.get("run", {})
        summary = snap.get("summary", {})
        run_sum = summary.get("run_summary", {}) or run_summary(run)
        rows.append({
            "run_id": snap["run_id"],
            "stored_at": snap.get("stored_at"),
            "status": run_sum.get("status", run.get("status", "unknown")),
            "trigger_source": run_sum.get("trigger_source", run.get("trigger_source", "")),
            "started_at": run_sum.get("started_at", run.get("started_at")),
            "ended_at": run_sum.get("ended_at", run.get("ended_at")),
            "duration_ms": run_sum.get("duration_ms", run.get("duration_ms")),
            "completed_stages": run_sum.get("completed_stages", 0),
            "failed_stages": run_sum.get("failed_stages", 0),
            "pending_stages": run_sum.get("pending_stages", 0),
            "error_count": run_sum.get("error_count", 0),
        })
    return rows


def get_run(run_id: str) -> dict[str, Any] | None:
    """Return the full snapshot for a run, or ``None``."""
    with _lock:
        snap = _runs.get(run_id)
    if snap is None:
        return None
    return copy.deepcopy(snap)


def get_run_detail(run_id: str) -> dict[str, Any] | None:
    """Return a UI-ready detail payload for a single run."""
    snap = get_run(run_id)
    if snap is None:
        return None

    run = snap.get("run", {})
    artifact_store = snap.get("artifact_store", {})
    stage_results = snap.get("stage_results", [])
    summary = snap.get("summary", {})

    # Build per-stage detail list
    stages_detail: list[dict[str, Any]] = []
    stage_states = run.get("stages", {})
    for stage_key in PIPELINE_STAGES:
        state = stage_states.get(stage_key, {})
        # Find matching stage_result
        sr = next((r for r in stage_results if r.get("stage_key") == stage_key), {})
        stage_artifacts = list_stage_artifacts(artifact_store, stage_key) if artifact_store.get("artifacts") else []

        stages_detail.append({
            "stage_key": stage_key,
            "label": state.get("label", stage_key.replace("_", " ").title()),
            "status": state.get("status", sr.get("outcome", "pending")),
            "started_at": state.get("started_at"),
            "ended_at": state.get("ended_at"),
            "duration_ms": state.get("duration_ms"),
            "summary_counts": state.get("summary_counts", sr.get("summary_counts", {})),
            "artifact_count": len(stage_artifacts),
            "artifact_refs": [a.get("artifact_id", "") for a in stage_artifacts],
            "error": state.get("error") or sr.get("error"),
            "log_event_count": state.get("log_event_count", 0),
        })

    # Build artifact index
    all_artifacts = list_artifacts(artifact_store) if artifact_store.get("artifacts") else []
    artifact_summaries = []
    for art in all_artifacts:
        artifact_summaries.append({
            "artifact_id": art.get("artifact_id", ""),
            "stage_key": art.get("stage_key", ""),
            "artifact_key": art.get("artifact_key", ""),
            "artifact_type": art.get("artifact_type", ""),
            "candidate_id": art.get("candidate_id"),
            "status": art.get("status", "active"),
            "created_at": art.get("created_at", ""),
            "summary": art.get("summary", {}),
        })

    # Extract ledger if present
    ledger = None
    for art in all_artifacts:
        if art.get("artifact_type") == "final_response_ledger":
            ledger = art.get("data")
            break

    return {
        "run_id": run.get("run_id", ""),
        "status": run.get("status", "unknown"),
        "pipeline_version": run.get("pipeline_version", ""),
        "trigger_source": run.get("trigger_source", ""),
        "started_at": run.get("started_at"),
        "ended_at": run.get("ended_at"),
        "duration_ms": run.get("duration_ms"),
        "candidate_counters": run.get("candidate_counters", {}),
        "error_count": len(run.get("errors", [])),
        "errors": run.get("errors", []),
        "stages": stages_detail,
        "stage_order": list(PIPELINE_STAGES),
        "artifacts": artifact_summaries,
        "artifact_store_summary": summarize_artifact_store(artifact_store) if artifact_store.get("artifacts") else {},
        "ledger": ledger,
        "events": snap.get("events", []),
        "candidate_progress": snap.get("candidate_progress"),
        "summary": summary,
        "stored_at": snap.get("stored_at"),
        "module_role": _MODULE_ROLE,
    }


def get_run_artifact(run_id: str, artifact_id: str) -> dict[str, Any] | None:
    """Return the full artifact record (including ``data``) for inspection."""
    snap = get_run(run_id)
    if snap is None:
        return None
    store = snap.get("artifact_store", {})
    artifacts = store.get("artifacts", {})
    art = artifacts.get(artifact_id)
    if art is None:
        return None
    return copy.deepcopy(art)


def get_run_events(run_id: str) -> list[dict[str, Any]]:
    """Return all log events for a run."""
    snap = get_run(run_id)
    if snap is None:
        return []
    return snap.get("events", [])


def run_count() -> int:
    """Current number of stored runs."""
    with _lock:
        return len(_runs)
