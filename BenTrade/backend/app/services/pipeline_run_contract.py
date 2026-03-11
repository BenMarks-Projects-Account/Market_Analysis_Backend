"""Pipeline Run Contract v1.0 — runtime execution container.

Defines the canonical run/state model for one full BenTrade pipeline
execution.  Every later workflow stage (market engines, scanners,
context assembly, trade review, final decisioning) runs inside
this container.

Public API
──────────
    create_pipeline_run(...)      Build a new PipelineRun dict.
    initialize_stage_states(...)  Populate the stage state map.
    mark_stage_running(...)       Transition a stage to running.
    mark_stage_completed(...)     Transition a stage to completed.
    mark_stage_failed(...)        Transition a stage to failed.
    mark_stage_skipped(...)       Transition a stage to skipped.
    compute_run_status(...)       Deterministic status rollup.
    finalize_run(...)             Seal a run (set end time, final status).
    build_run_error(...)          Create a structured error object.
    build_log_event(...)          Create a structured log event.
    validate_pipeline_run(...)    Validate a PipelineRun dict.
    run_summary(...)              Compact digest for UI / logging.

Role boundary
─────────────
This module owns the *shape* of the run container and the state-
transition rules.  It does NOT:
- execute any pipeline stages
- persist artifacts to disk / DB
- stream events over SSE / WebSocket
- implement retry / cancellation logic (future)
- manage candidate-level state (future seam only)

Designed for inspectability, serialization, and replay.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("bentrade.pipeline_run_contract")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "contract"
_PIPELINE_VERSION = "1.0"
_COMPATIBLE_VERSIONS = frozenset({"1.0"})


# =====================================================================
#  Stage registry — canonical order
# =====================================================================

PIPELINE_STAGES: tuple[str, ...] = (
    "market_data",
    "market_model_analysis",
    "scanners",
    "candidate_selection",
    "shared_context",
    "candidate_enrichment",
    "events",
    "policy",
    "orchestration",
    "prompt_payload",
    "final_model_decision",
    "final_response_normalization",
)
"""Canonical stage keys in execution order.

Future additions should be appended or inserted at the correct
position — never change the relative order of existing stages.
"""

_STAGE_INDEX: dict[str, int] = {s: i for i, s in enumerate(PIPELINE_STAGES)}

STAGE_LABELS: dict[str, str] = {
    "market_data":                   "Market Data Fetch",
    "market_model_analysis":         "Market Model Analysis",
    "scanners":                      "Scanner Execution",
    "candidate_selection":           "Candidate Selection",
    "shared_context":                "Shared Context Assembly",
    "candidate_enrichment":          "Candidate Enrichment",
    "policy":                        "Policy Evaluation",
    "events":                        "Event Context",
    "orchestration":                 "Decision Packet Assembly",
    "prompt_payload":                "Prompt Payload Build",
    "final_model_decision":          "Final Model Decision",
    "final_response_normalization":  "Response Normalization",
}


# =====================================================================
#  Status vocabularies
# =====================================================================

VALID_STAGE_STATUSES = frozenset({
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
})

VALID_RUN_STATUSES = frozenset({
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "partial_failed",
})

VALID_LOG_LEVELS = frozenset({
    "debug",
    "info",
    "warning",
    "error",
})

VALID_EVENT_TYPES = frozenset({
    "run_started",
    "run_completed",
    "run_failed",
    "stage_started",
    "stage_completed",
    "stage_failed",
    "stage_skipped",
    "engine_started",
    "engine_completed",
    "engine_failed",
    "model_analysis_started",
    "model_analysis_completed",
    "model_analysis_failed",
    "scanner_started",
    "scanner_completed",
    "scanner_failed",
    "selection_started",
    "selection_completed",
    "selection_failed",
    "context_assembly_started",
    "context_assembly_completed",
    "context_assembly_failed",
    "candidate_enrichment_started",
    "candidate_enrichment_completed",
    "candidate_enrichment_failed",
    "event_context_started",
    "event_context_completed",
    "event_context_failed",
    "policy_evaluation_started",
    "policy_evaluation_completed",
    "policy_evaluation_failed",
    "decision_packet_started",
    "decision_packet_completed",
    "decision_packet_failed",
    "prompt_payload_started",
    "prompt_payload_completed",
    "prompt_payload_failed",
    "final_model_started",
    "final_model_completed",
    "final_model_failed",
    "final_response_started",
    "final_response_completed",
    "final_response_failed",
    "candidate_started",
    "candidate_completed",
    "artifact_written",
    "model_call_started",
    "model_call_completed",
    "progress",
    "warning",
})


# =====================================================================
#  Required-key sets (for validation)
# =====================================================================

_REQUIRED_RUN_KEYS = frozenset({
    "run_id",
    "pipeline_version",
    "trigger_source",
    "requested_scope",
    "status",
    "started_at",
    "ended_at",
    "duration_ms",
    "stages",
    "stage_order",
    "candidate_counters",
    "errors",
    "log_event_counts",
    "metadata",
})

_REQUIRED_STAGE_KEYS = frozenset({
    "stage_key",
    "label",
    "status",
    "started_at",
    "ended_at",
    "duration_ms",
    "depends_on",
    "summary_counts",
    "error",
    "artifact_refs",
    "log_event_count",
})

_REQUIRED_ERROR_KEYS = frozenset({
    "code",
    "message",
    "source",
    "detail",
    "timestamp",
    "retryable",
})

_REQUIRED_LOG_EVENT_KEYS = frozenset({
    "run_id",
    "stage_key",
    "event_type",
    "timestamp",
    "level",
    "message",
    "metadata",
})


# =====================================================================
#  Stage state helpers (private)
# =====================================================================

def _now_iso() -> str:
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(start_iso: str | None, end_iso: str | None) -> int | None:
    """Compute millisecond duration between two ISO timestamps.

    Returns None if either timestamp is missing or unparseable.
    """
    if not start_iso or not end_iso:
        return None
    try:
        s = datetime.fromisoformat(start_iso)
        e = datetime.fromisoformat(end_iso)
        return max(0, int((e - s).total_seconds() * 1000))
    except (ValueError, TypeError):
        return None


def _build_stage_state(
    stage_key: str,
    *,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    """Create the initial state dict for a single stage.

    Derived fields
    ──────────────
    - label: from STAGE_LABELS lookup
    """
    return {
        "stage_key": stage_key,
        "label": STAGE_LABELS.get(stage_key, stage_key),
        "status": "pending",
        "started_at": None,
        "ended_at": None,
        "duration_ms": None,
        "depends_on": depends_on or [],
        "summary_counts": {},
        "error": None,
        "artifact_refs": [],
        "log_event_count": 0,
    }


# ── Stage transition guards ────────────────────────────────────

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending":   frozenset({"running", "skipped"}),
    "running":   frozenset({"completed", "failed"}),
    "completed": frozenset(),          # terminal
    "failed":    frozenset(),          # terminal
    "skipped":   frozenset(),          # terminal
}


def _assert_transition(current: str, target: str, stage_key: str) -> None:
    """Raise ValueError if transition is invalid."""
    allowed = _VALID_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise ValueError(
            f"Invalid stage transition for '{stage_key}': "
            f"'{current}' → '{target}' "
            f"(allowed: {sorted(allowed) if allowed else 'none — terminal state'})"
        )


# =====================================================================
#  Public API: create_pipeline_run
# =====================================================================

def create_pipeline_run(
    *,
    trigger_source: str = "manual",
    requested_scope: dict[str, Any] | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new PipelineRun dict in ``pending`` state.

    Parameters
    ----------
    trigger_source : str
        What triggered this run (e.g. "manual", "scheduled", "api").
    requested_scope : dict | None
        Execution scope — symbols, strategy families, or target
        identifiers.  Free-form dict; the pipeline interprets it.
    run_id : str | None
        Override the auto-generated run ID (useful for replays).
    metadata : dict | None
        Additional extensibility fields.

    Returns
    -------
    dict[str, Any]
        A fully initialized PipelineRun dict.
    """
    rid = run_id or f"run-{uuid.uuid4().hex[:12]}"
    now = _now_iso()

    stages = initialize_stage_states()

    return {
        "run_id": rid,
        "pipeline_version": _PIPELINE_VERSION,
        "trigger_source": trigger_source,
        "requested_scope": requested_scope or {},
        "status": "pending",
        "started_at": now,
        "ended_at": None,
        "duration_ms": None,
        "stages": stages,
        "stage_order": list(PIPELINE_STAGES),
        "candidate_counters": {
            "scanned": 0,
            "selected": 0,
            "enriched": 0,
            "policy_passed": 0,
            "submitted_to_model": 0,
            "approved": 0,
            "rejected": 0,
        },
        "errors": [],
        "log_event_counts": {
            "total": 0,
            "by_level": {"debug": 0, "info": 0, "warning": 0, "error": 0},
        },
        "metadata": metadata or {},
    }


# =====================================================================
#  Public API: initialize_stage_states
# =====================================================================

def initialize_stage_states(
    stages: tuple[str, ...] | None = None,
    *,
    dependency_map: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the initial stage state map.

    Parameters
    ----------
    stages : tuple[str, ...] | None
        Stage keys in order.  Defaults to PIPELINE_STAGES.
    dependency_map : dict | None
        Optional mapping of stage_key → list of prerequisite stage_keys.

    Returns
    -------
    dict[str, dict[str, Any]]
        Keyed by stage_key, each value is a stage state dict.
    """
    stage_list = stages or PIPELINE_STAGES
    deps = dependency_map or {}
    return {
        key: _build_stage_state(key, depends_on=deps.get(key))
        for key in stage_list
    }


# =====================================================================
#  Public API: stage transitions
# =====================================================================

def mark_stage_running(
    run: dict[str, Any],
    stage_key: str,
) -> dict[str, Any]:
    """Transition a stage to ``running``.

    Side effects on *run*:
    - stage status → running
    - stage started_at → now
    - run status → running (if was pending)

    Raises ValueError if transition is invalid.
    """
    stage = _get_stage(run, stage_key)
    _assert_transition(stage["status"], "running", stage_key)

    stage["status"] = "running"
    stage["started_at"] = _now_iso()

    if run["status"] == "pending":
        run["status"] = "running"

    return run


def mark_stage_completed(
    run: dict[str, Any],
    stage_key: str,
    *,
    summary_counts: dict[str, Any] | None = None,
    artifact_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Transition a stage to ``completed``.

    Side effects on *run*:
    - stage status → completed
    - stage ended_at → now
    - stage duration_ms → computed
    - optional summary_counts / artifact_refs merged

    Raises ValueError if transition is invalid.
    """
    stage = _get_stage(run, stage_key)
    _assert_transition(stage["status"], "completed", stage_key)

    now = _now_iso()
    stage["status"] = "completed"
    stage["ended_at"] = now
    stage["duration_ms"] = _duration_ms(stage["started_at"], now)

    if summary_counts:
        stage["summary_counts"].update(summary_counts)
    if artifact_refs:
        stage["artifact_refs"].extend(artifact_refs)

    return run


def mark_stage_failed(
    run: dict[str, Any],
    stage_key: str,
    *,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transition a stage to ``failed``.

    Side effects on *run*:
    - stage status → failed
    - stage ended_at → now
    - stage duration_ms → computed
    - stage error → attached
    - error appended to run.errors

    Raises ValueError if transition is invalid.
    """
    stage = _get_stage(run, stage_key)
    _assert_transition(stage["status"], "failed", stage_key)

    now = _now_iso()
    stage["status"] = "failed"
    stage["ended_at"] = now
    stage["duration_ms"] = _duration_ms(stage["started_at"], now)
    stage["error"] = error

    if error:
        run["errors"].append(error)

    return run


def mark_stage_skipped(
    run: dict[str, Any],
    stage_key: str,
    *,
    reason: str = "",
) -> dict[str, Any]:
    """Transition a stage to ``skipped``.

    Side effects on *run*:
    - stage status → skipped

    Raises ValueError if transition is invalid.
    """
    stage = _get_stage(run, stage_key)
    _assert_transition(stage["status"], "skipped", stage_key)

    stage["status"] = "skipped"
    if reason:
        stage["summary_counts"]["skip_reason"] = reason

    return run


def _get_stage(run: dict[str, Any], stage_key: str) -> dict[str, Any]:
    """Look up a stage dict by key.  Raises KeyError if not found."""
    stages = run.get("stages") or {}
    if stage_key not in stages:
        raise KeyError(
            f"Stage '{stage_key}' not found in run '{run.get('run_id')}'. "
            f"Known stages: {sorted(stages.keys())}"
        )
    return stages[stage_key]


# =====================================================================
#  Public API: compute_run_status
# =====================================================================

def compute_run_status(run: dict[str, Any]) -> str:
    """Derive the top-level run status deterministically from stage states.

    Rollup rules (evaluated in order):
    ─────────────────────────────────
    1. If run is ``cancelled`` → ``cancelled``         (sticky)
    2. If *any* stage is ``running`` → ``running``
    3. If *all* stages are ``pending`` → ``pending``
    4. If *any* stage is ``failed``:
       a. If *all non-skipped* stages are terminal → ``failed``
       b. If some stages are still pending → ``partial_failed``
    5. If all stages are terminal (completed/failed/skipped)
       with at least one ``completed`` and no ``failed`` → ``completed``
    6. Otherwise → ``running``  (in-progress / mixed)

    Derived fields
    ──────────────
    - status: from the rules above
    """
    if run.get("status") == "cancelled":
        return "cancelled"

    stages = run.get("stages") or {}
    if not stages:
        return "pending"

    statuses = [s["status"] for s in stages.values()]

    if any(s == "running" for s in statuses):
        return "running"

    if all(s == "pending" for s in statuses):
        return "pending"

    terminal = {"completed", "failed", "skipped"}
    has_failed = any(s == "failed" for s in statuses)
    has_completed = any(s == "completed" for s in statuses)
    all_terminal = all(s in terminal for s in statuses)

    if has_failed:
        if all_terminal:
            return "failed"
        return "partial_failed"

    if all_terminal and has_completed:
        return "completed"

    # Fallback: some stages done, some pending, none running, none failed
    return "running"


# =====================================================================
#  Public API: finalize_run
# =====================================================================

def finalize_run(run: dict[str, Any]) -> dict[str, Any]:
    """Seal a run — compute final status, set end time and duration.

    This should be called once after all stages have reached a
    terminal state (completed / failed / skipped).

    Side effects on *run*:
    - status → computed via compute_run_status
    - ended_at → now
    - duration_ms → computed from started_at → ended_at
    """
    now = _now_iso()
    run["status"] = compute_run_status(run)
    run["ended_at"] = now
    run["duration_ms"] = _duration_ms(run["started_at"], now)
    return run


# =====================================================================
#  Public API: build_run_error
# =====================================================================

def build_run_error(
    *,
    code: str,
    message: str,
    source: str = "",
    detail: dict[str, Any] | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    """Create a structured error object.

    Reusable for stage-level, run-level, and future candidate-level
    errors.

    Parameters
    ----------
    code : str
        Machine-readable error code (e.g. "MARKET_DATA_TIMEOUT").
    message : str
        Human-readable description.
    source : str
        Module/stage that produced the error.
    detail : dict | None
        Arbitrary structured metadata about the failure.
    retryable : bool
        Whether the operation could succeed on retry.
    """
    return {
        "code": code,
        "message": message,
        "source": source,
        "detail": detail or {},
        "timestamp": _now_iso(),
        "retryable": retryable,
    }


# =====================================================================
#  Public API: build_log_event
# =====================================================================

def build_log_event(
    *,
    run_id: str,
    stage_key: str = "",
    event_type: str,
    level: str = "info",
    message: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a structured log / progress event.

    These events form the backend streaming payload (future SSE/WS).
    This function only creates the event dict — it does *not*
    emit, persist, or broadcast it.

    Parameters
    ----------
    run_id : str
        Pipeline run identifier.
    stage_key : str
        Which stage produced the event (empty for run-level events).
    event_type : str
        One of VALID_EVENT_TYPES.
    level : str
        Log level: debug / info / warning / error.
    message : str
        Human-readable event description.
    metadata : dict | None
        Additional structured context.
    """
    if event_type not in VALID_EVENT_TYPES:
        logger.warning(
            "Unknown event type '%s'; valid types: %s",
            event_type, sorted(VALID_EVENT_TYPES),
        )
    if level not in VALID_LOG_LEVELS:
        logger.warning(
            "Unknown log level '%s'; valid levels: %s",
            level, sorted(VALID_LOG_LEVELS),
        )

    return {
        "run_id": run_id,
        "stage_key": stage_key,
        "event_type": event_type,
        "timestamp": _now_iso(),
        "level": level,
        "message": message,
        "metadata": metadata or {},
    }


# =====================================================================
#  Public API: validate_pipeline_run
# =====================================================================

def validate_pipeline_run(
    run: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Validate a PipelineRun dict against the expected schema.

    Returns (ok, errors) where ok is True if report passes all checks.
    """
    errors: list[str] = []

    if not isinstance(run, dict):
        return False, ["run must be a dict"]

    # ── Required top-level keys ─────────────────────────────────
    for key in _REQUIRED_RUN_KEYS:
        if key not in run:
            errors.append(f"missing required key: {key}")

    # ── Version compatibility ───────────────────────────────────
    if run.get("pipeline_version") not in _COMPATIBLE_VERSIONS:
        errors.append(
            f"pipeline_version mismatch: expected one of "
            f"{sorted(_COMPATIBLE_VERSIONS)}, "
            f"got {run.get('pipeline_version')}"
        )

    # ── Run status ──────────────────────────────────────────────
    if run.get("status") not in VALID_RUN_STATUSES:
        errors.append(f"invalid run status: {run.get('status')}")

    # ── Stages ──────────────────────────────────────────────────
    stages = run.get("stages")
    if not isinstance(stages, dict):
        errors.append("stages must be a dict")
    else:
        for skey, sval in stages.items():
            if not isinstance(sval, dict):
                errors.append(f"stage '{skey}' must be a dict")
                continue
            for rk in _REQUIRED_STAGE_KEYS:
                if rk not in sval:
                    errors.append(f"stage '{skey}' missing key: {rk}")
            if sval.get("status") not in VALID_STAGE_STATUSES:
                errors.append(
                    f"stage '{skey}' invalid status: {sval.get('status')}"
                )

    # ── Stage order ─────────────────────────────────────────────
    if not isinstance(run.get("stage_order"), list):
        errors.append("stage_order must be a list")

    # ── Errors list ─────────────────────────────────────────────
    if not isinstance(run.get("errors"), list):
        errors.append("errors must be a list")

    # ── Candidate counters ──────────────────────────────────────
    if not isinstance(run.get("candidate_counters"), dict):
        errors.append("candidate_counters must be a dict")

    # ── Log event counts ────────────────────────────────────────
    if not isinstance(run.get("log_event_counts"), dict):
        errors.append("log_event_counts must be a dict")

    # ── Metadata ────────────────────────────────────────────────
    if not isinstance(run.get("metadata"), dict):
        errors.append("metadata must be a dict")

    return (len(errors) == 0, errors)


# =====================================================================
#  Public API: run_summary
# =====================================================================

def run_summary(run: dict[str, Any]) -> dict[str, Any]:
    """Return a compact overview of a pipeline run.

    Designed for UI dashboards and log outputs — read-only digest.

    Output keys:
    - run_id: str
    - pipeline_version: str
    - status: str
    - trigger_source: str
    - started_at: str | None
    - ended_at: str | None
    - duration_ms: int | None
    - stage_statuses: dict[str, str]
    - completed_stages: int
    - failed_stages: int
    - pending_stages: int
    - error_count: int
    - module_role: str
    """
    stages = run.get("stages") or {}
    stage_statuses = {k: v.get("status", "unknown") for k, v in stages.items()}

    return {
        "run_id": run.get("run_id", "unknown"),
        "pipeline_version": run.get("pipeline_version", "unknown"),
        "status": run.get("status", "unknown"),
        "trigger_source": run.get("trigger_source", "unknown"),
        "started_at": run.get("started_at"),
        "ended_at": run.get("ended_at"),
        "duration_ms": run.get("duration_ms"),
        "stage_statuses": stage_statuses,
        "completed_stages": sum(
            1 for s in stage_statuses.values() if s == "completed"
        ),
        "failed_stages": sum(
            1 for s in stage_statuses.values() if s == "failed"
        ),
        "pending_stages": sum(
            1 for s in stage_statuses.values() if s == "pending"
        ),
        "error_count": len(run.get("errors") or []),
        "module_role": _MODULE_ROLE,
    }
