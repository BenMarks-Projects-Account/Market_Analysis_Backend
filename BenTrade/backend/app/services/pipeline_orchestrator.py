"""Pipeline Orchestrator v1.0 — runtime execution conductor.

Coordinates ordered stage execution for one BenTrade pipeline run
using the Step 1 run contract and Step 2 artifact store.

Public API
──────────
    create_orchestrator(...)         Build an orchestrator state dict.
    run_pipeline(...)                Execute full pipeline with defaults.
    run_pipeline_with_handlers(...)  Execute with custom handler registry.
    execute_stage(...)               Run a single stage through the wrapper.
    build_stage_result(...)          Build a normalized stage result dict.
    get_default_handlers(...)        Return the default stub handler registry.
    get_default_dependency_map(...)  Return canonical dependency graph.
    get_stop_policy(...)             Return the stop/continue policy.
    summarize_pipeline_result(...)   Compact digest of a completed run.

Role boundary
─────────────
This module owns the *execution flow* — stage ordering, dependency
gating, timing, error capture, and stop/continue semantics.

It does NOT:
- implement real business logic for any stage
- persist runs / artifacts to disk / database
- stream events over SSE / WebSocket (seam only)
- manage candidate-level iteration (future)
- duplicate Step 1 status semantics or Step 2 storage logic
"""

from __future__ import annotations

import copy
import logging
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.pipeline_artifact_store import (
    build_artifact_record,
    create_artifact_store,
    put_artifact,
    summarize_artifact_store,
)
from app.services.pipeline_market_stage import market_stage_handler
from app.services.pipeline_market_model_stage import market_model_stage_handler
from app.services.pipeline_scanner_stage import scanner_stage_handler
from app.services.pipeline_candidate_selection_stage import candidate_selection_handler
from app.services.pipeline_context_assembly_stage import context_assembly_handler
from app.services.pipeline_candidate_enrichment_stage import candidate_enrichment_handler
from app.services.pipeline_event_context_stage import event_context_handler
from app.services.pipeline_portfolio_policy_stage import portfolio_policy_handler
from app.services.pipeline_trade_decision_packet_stage import decision_packet_handler
from app.services.pipeline_decision_prompt_payload_stage import prompt_payload_handler
from app.services.pipeline_final_recommendation_stage import final_recommendation_handler
from app.services.pipeline_final_response_stage import final_response_handler
from app.services.pipeline_run_contract import (
    PIPELINE_STAGES,
    build_log_event,
    build_run_error,
    compute_run_status,
    create_pipeline_run,
    finalize_run,
    mark_stage_completed,
    mark_stage_failed,
    mark_stage_running,
    mark_stage_skipped,
    run_summary,
)

logger = logging.getLogger("bentrade.pipeline_orchestrator")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "orchestrator"
_ORCHESTRATOR_VERSION = "1.0"
_COMPATIBLE_VERSIONS = frozenset({"1.0"})


# =====================================================================
#  Dependency map — canonical defaults
# =====================================================================

_DEFAULT_DEPENDENCY_MAP: dict[str, list[str]] = {
    "market_data":                  [],
    "market_model_analysis":        ["market_data"],
    "scanners":                     ["market_data"],
    "candidate_selection":          ["scanners"],
    "shared_context":               ["market_model_analysis"],
    "candidate_enrichment":         ["candidate_selection", "shared_context"],
    "policy":                       ["candidate_enrichment"],
    "events":                       ["candidate_enrichment"],
    "orchestration":                ["candidate_enrichment", "policy", "events"],
    "prompt_payload":               ["orchestration"],
    "final_model_decision":         ["prompt_payload"],
    "final_response_normalization": ["final_model_decision"],
}
"""Default dependency graph for the pipeline.

Each key maps to the list of stage_keys that must complete
successfully before it can execute.  Empty list = no prereqs.
"""


# =====================================================================
#  Stop / continue policy
# =====================================================================

# Stages whose failure is fatal — halts the pipeline immediately.
# All stages are fatal by default; only stages listed in
# _CONTINUABLE_STAGES are allowed to fail without halting.
_CONTINUABLE_STAGES: frozenset[str] = frozenset({
    "events",
})
"""Stages whose failure does NOT halt the pipeline.

Failures in these stages are recorded normally but downstream
stages (that do not depend on them) may still execute.
"""


def get_stop_policy() -> dict[str, Any]:
    """Return the current stop/continue policy as a dict.

    Returns
    -------
    dict[str, Any]
        - default_behavior: "stop" — most failures halt the pipeline
        - continuable_stages: list of stage keys that can fail softly
        - description: human-readable summary
    """
    return {
        "default_behavior": "stop",
        "continuable_stages": sorted(_CONTINUABLE_STAGES),
        "description": (
            "Stage failures halt the pipeline unless the stage "
            "is listed in continuable_stages."
        ),
    }


def _is_fatal_failure(stage_key: str) -> bool:
    """Return True if a failure in this stage should halt the pipeline."""
    return stage_key not in _CONTINUABLE_STAGES


# =====================================================================
#  Stage handler type and default stubs
# =====================================================================

# Handler signature:
#   (run, artifact_store, stage_key, **kwargs) -> dict[str, Any]
#
# Expected return shape:
#   {
#       "outcome": "completed" | "failed" | "skipped",
#       "summary_counts": dict,
#       "artifacts": list[dict],   # artifact records to write
#       "metadata": dict,
#       "error": dict | None,      # structured error if failed
#   }

StageHandler = Callable[..., dict[str, Any]]


def _stub_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Default stub handler — returns a clean completed result.

    Placeholder for real stage business logic to be plugged in later.
    """
    return {
        "outcome": "completed",
        "summary_counts": {"items_processed": 0},
        "artifacts": [],
        "metadata": {"stub": True},
        "error": None,
    }


def get_default_handlers() -> dict[str, StageHandler]:
    """Return the default handler registry — real handlers where
    available, stubs for the rest.

    Returns
    -------
    dict[str, StageHandler]
        stage_key → handler callable, one entry per canonical stage.
    """
    handlers = {stage: _stub_handler for stage in PIPELINE_STAGES}
    handlers["market_data"] = market_stage_handler
    handlers["market_model_analysis"] = market_model_stage_handler
    handlers["scanners"] = scanner_stage_handler
    handlers["candidate_selection"] = candidate_selection_handler
    handlers["shared_context"] = context_assembly_handler
    handlers["candidate_enrichment"] = candidate_enrichment_handler
    handlers["events"] = event_context_handler
    handlers["policy"] = portfolio_policy_handler
    handlers["orchestration"] = decision_packet_handler
    handlers["prompt_payload"] = prompt_payload_handler
    handlers["final_model_decision"] = final_recommendation_handler
    handlers["final_response_normalization"] = final_response_handler
    return handlers


# =====================================================================
#  Stage result builder
# =====================================================================

def build_stage_result(
    *,
    stage_key: str,
    handler_invoked: bool,
    outcome: str,
    artifact_count: int = 0,
    error_count: int = 0,
    skipped_reason: str = "",
    dependency_status: str = "satisfied",
    timing_ms: int | None = None,
    summary_counts: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a normalized per-stage result summary.

    Parameters
    ----------
    stage_key : str
        Which stage this result is for.
    handler_invoked : bool
        Whether the handler was actually called.
    outcome : str
        "completed" | "failed" | "skipped"
    artifact_count : int
        Number of artifacts written during this stage.
    error_count : int
        Number of errors encountered.
    skipped_reason : str
        Why the stage was skipped (empty if it ran).
    dependency_status : str
        "satisfied" | "unsatisfied" | "not_applicable"
    timing_ms : int | None
        Wall-clock time for the stage handler in milliseconds.
    summary_counts : dict | None
        Domain-specific counts from the handler.
    metadata : dict | None
        Additional context.

    Returns
    -------
    dict[str, Any]
        Compact, machine-usable stage result.
    """
    return {
        "stage_key": stage_key,
        "handler_invoked": handler_invoked,
        "outcome": outcome,
        "artifact_count": artifact_count,
        "error_count": error_count,
        "skipped_reason": skipped_reason,
        "dependency_status": dependency_status,
        "timing_ms": timing_ms,
        "summary_counts": summary_counts or {},
        "metadata": metadata or {},
    }


# =====================================================================
#  Dependency gating
# =====================================================================

def _check_dependencies(
    run: dict[str, Any],
    stage_key: str,
    dependency_map: dict[str, list[str]],
) -> tuple[bool, str]:
    """Check whether all dependencies for a stage are satisfied.

    A dependency is satisfied if its stage status is "completed".

    Parameters
    ----------
    run : dict
        The pipeline run dict.
    stage_key : str
        Stage to check.
    dependency_map : dict
        stage_key → list of prerequisite stage_keys.

    Returns
    -------
    (satisfied, reason)
        satisfied: True if all deps are completed.
        reason: empty string if satisfied, otherwise describes
                which deps are unsatisfied and their statuses.
    """
    deps = dependency_map.get(stage_key, [])
    if not deps:
        return True, ""

    stages = run.get("stages", {})
    unsatisfied: list[str] = []
    for dep in deps:
        dep_stage = stages.get(dep)
        if dep_stage is None:
            unsatisfied.append(f"{dep}=missing")
        elif dep_stage["status"] != "completed":
            unsatisfied.append(f"{dep}={dep_stage['status']}")

    if unsatisfied:
        reason = (
            f"Unsatisfied dependencies for '{stage_key}': "
            + ", ".join(unsatisfied)
        )
        return False, reason

    return True, ""


# =====================================================================
#  Event emission seam
# =====================================================================

def _emit_event(
    run: dict[str, Any],
    event_type: str,
    stage_key: str = "",
    level: str = "info",
    message: str = "",
    metadata: dict[str, Any] | None = None,
    *,
    event_callback: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Build a log event and invoke the callback if provided.

    This is the seam for future SSE/WebSocket streaming.
    Currently just builds the event dict and optionally calls
    the callback.  Does NOT persist or broadcast.

    Returns the event dict for caller inspection.
    """
    event = build_log_event(
        run_id=run["run_id"],
        stage_key=stage_key,
        event_type=event_type,
        level=level,
        message=message,
        metadata=metadata,
    )

    # Update run log counts
    counts = run.get("log_event_counts", {})
    counts["total"] = counts.get("total", 0) + 1
    by_level = counts.get("by_level", {})
    by_level[level] = by_level.get(level, 0) + 1

    if event_callback is not None:
        try:
            event_callback(event)
        except Exception:
            logger.warning(
                "Event callback raised an exception for %s/%s",
                event_type, stage_key, exc_info=True,
            )

    return event


# =====================================================================
#  Stage execution wrapper
# =====================================================================

def execute_stage(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    *,
    handler: StageHandler | None = None,
    dependency_map: dict[str, list[str]] | None = None,
    event_callback: Callable[..., None] | None = None,
    handler_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a single stage through the standard wrapper.

    Sequence:
    1. Dependency check → skip if unsatisfied
    2. Transition stage to running
    3. Emit stage_started event
    4. Invoke handler (timed)
    5. Interpret handler result
    6. Write artifacts from handler
    7. Transition stage to completed / failed
    8. Emit stage_completed / stage_failed event
    9. Return normalized stage result

    Parameters
    ----------
    run : dict
        The pipeline run (mutated in place).
    artifact_store : dict
        The artifact store (mutated in place).
    stage_key : str
        Which stage to execute.
    handler : StageHandler | None
        The handler to invoke.  Falls back to _stub_handler.
    dependency_map : dict | None
        Dependency graph.  Defaults to _DEFAULT_DEPENDENCY_MAP.
    event_callback : callable | None
        Optional callback invoked for each event.
    handler_kwargs : dict | None
        Extra kwargs passed to the handler.

    Returns
    -------
    dict[str, Any]
        Normalized stage result from build_stage_result.
    """
    deps = dependency_map if dependency_map is not None else _DEFAULT_DEPENDENCY_MAP
    handler_fn = handler or _stub_handler
    extra_kwargs = handler_kwargs or {}

    # ── 1. Dependency check ─────────────────────────────────────
    satisfied, dep_reason = _check_dependencies(run, stage_key, deps)
    if not satisfied:
        logger.info("Skipping stage '%s': %s", stage_key, dep_reason)
        mark_stage_skipped(run, stage_key, reason=dep_reason)
        _emit_event(
            run, "stage_skipped", stage_key=stage_key,
            level="warning",
            message=dep_reason,
            event_callback=event_callback,
        )
        return build_stage_result(
            stage_key=stage_key,
            handler_invoked=False,
            outcome="skipped",
            skipped_reason=dep_reason,
            dependency_status="unsatisfied",
        )

    # ── 2. Transition to running ────────────────────────────────
    mark_stage_running(run, stage_key)
    _emit_event(
        run, "stage_started", stage_key=stage_key,
        message=f"Stage '{stage_key}' started",
        event_callback=event_callback,
    )

    # ── 3. Invoke handler (timed) ───────────────────────────────
    # Inject event_callback into handler kwargs so stage handlers
    # can emit fine-grained events (e.g. per-candidate progress).
    if event_callback is not None and "event_callback" not in extra_kwargs:
        extra_kwargs["event_callback"] = event_callback

    t0 = time.monotonic()
    try:
        handler_result = handler_fn(
            run, artifact_store, stage_key, **extra_kwargs
        )
    except Exception as exc:
        return _handle_stage_exception(
            run, artifact_store, stage_key, exc,
            t0=t0, event_callback=event_callback,
        )
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # ── 4. Interpret handler result ─────────────────────────────
    if not isinstance(handler_result, dict):
        return _handle_stage_exception(
            run, artifact_store, stage_key,
            TypeError(
                f"Handler for '{stage_key}' returned "
                f"{type(handler_result).__name__}, expected dict"
            ),
            t0=t0, event_callback=event_callback,
        )

    outcome = handler_result.get("outcome", "completed")

    # ── 5. Handle handler-reported failure ──────────────────────
    if outcome == "failed":
        return _handle_stage_failure(
            run, artifact_store, stage_key,
            handler_result=handler_result,
            elapsed_ms=elapsed_ms,
            event_callback=event_callback,
        )

    # ── 6. Write artifacts from handler ─────────────────────────
    artifact_count = _write_handler_artifacts(
        artifact_store, handler_result, run["run_id"], stage_key,
    )

    # ── 7. Transition to completed ──────────────────────────────
    summary_counts = handler_result.get("summary_counts", {})
    mark_stage_completed(run, stage_key, summary_counts=summary_counts)
    _emit_event(
        run, "stage_completed", stage_key=stage_key,
        message=f"Stage '{stage_key}' completed in {elapsed_ms}ms",
        metadata={"timing_ms": elapsed_ms},
        event_callback=event_callback,
    )

    return build_stage_result(
        stage_key=stage_key,
        handler_invoked=True,
        outcome="completed",
        artifact_count=artifact_count,
        timing_ms=elapsed_ms,
        summary_counts=summary_counts,
        metadata=handler_result.get("metadata", {}),
    )


# =====================================================================
#  Stage failure / exception helpers
# =====================================================================

def _handle_stage_failure(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    *,
    handler_result: dict[str, Any],
    elapsed_ms: int,
    event_callback: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Handle a handler that returned outcome=failed cleanly."""
    error_dict = handler_result.get("error")
    if error_dict is None:
        error_dict = build_run_error(
            code="STAGE_HANDLER_FAILED",
            message=f"Handler for '{stage_key}' reported failure",
            source=stage_key,
        )

    mark_stage_failed(run, stage_key, error=error_dict)
    _emit_event(
        run, "stage_failed", stage_key=stage_key,
        level="error",
        message=f"Stage '{stage_key}' failed: {error_dict.get('message', '')}",
        metadata={"timing_ms": elapsed_ms},
        event_callback=event_callback,
    )

    return build_stage_result(
        stage_key=stage_key,
        handler_invoked=True,
        outcome="failed",
        error_count=1,
        timing_ms=elapsed_ms,
        summary_counts=handler_result.get("summary_counts", {}),
        metadata=handler_result.get("metadata", {}),
    )


def _handle_stage_exception(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    exc: Exception,
    *,
    t0: float,
    event_callback: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Handle an unhandled exception from a stage handler."""
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)

    logger.error(
        "Stage '%s' raised %s: %s",
        stage_key, type(exc).__name__, exc, exc_info=True,
    )

    error_dict = build_run_error(
        code="STAGE_EXCEPTION",
        message=f"{type(exc).__name__}: {exc}",
        source=stage_key,
        detail={"traceback": tb},
    )

    mark_stage_failed(run, stage_key, error=error_dict)
    _emit_event(
        run, "stage_failed", stage_key=stage_key,
        level="error",
        message=f"Stage '{stage_key}' exception: {exc}",
        metadata={"timing_ms": elapsed_ms},
        event_callback=event_callback,
    )

    return build_stage_result(
        stage_key=stage_key,
        handler_invoked=True,
        outcome="failed",
        error_count=1,
        timing_ms=elapsed_ms,
    )


# =====================================================================
#  Artifact write helper
# =====================================================================

def _write_handler_artifacts(
    artifact_store: dict[str, Any],
    handler_result: dict[str, Any],
    run_id: str,
    stage_key: str,
) -> int:
    """Write artifacts declared by a handler result into the store.

    Returns the number of artifacts successfully written.
    """
    artifacts = handler_result.get("artifacts") or []
    written = 0
    for art_spec in artifacts:
        try:
            if "artifact_id" in art_spec and "artifact_type" in art_spec:
                # Already a full record — write directly
                put_artifact(artifact_store, art_spec, overwrite=True)
            else:
                # Build a record from spec
                record = build_artifact_record(
                    run_id=run_id,
                    stage_key=stage_key,
                    artifact_key=art_spec.get("artifact_key", f"{stage_key}_output"),
                    artifact_type=art_spec.get("artifact_type", f"{stage_key}_output"),
                    data=art_spec.get("data"),
                    summary=art_spec.get("summary"),
                    candidate_id=art_spec.get("candidate_id"),
                    metadata=art_spec.get("metadata"),
                )
                put_artifact(artifact_store, record, overwrite=True)
            written += 1
        except Exception:
            logger.warning(
                "Failed to write artifact for stage '%s': %s",
                stage_key, art_spec, exc_info=True,
            )
    return written


# =====================================================================
#  Public API: create_orchestrator
# =====================================================================

def create_orchestrator(
    *,
    trigger_source: str = "manual",
    requested_scope: dict[str, Any] | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    handlers: dict[str, StageHandler] | None = None,
    dependency_map: dict[str, list[str]] | None = None,
    event_callback: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Build an orchestrator state dict.

    Creates the run, artifact store, handler registry, and
    dependency map together.

    Parameters
    ----------
    trigger_source : str
        What triggered this run.
    requested_scope : dict | None
        Execution scope for the pipeline.
    run_id : str | None
        Override auto-generated run ID.
    metadata : dict | None
        Run-level metadata.
    handlers : dict | None
        Custom handler registry. Merged over defaults.
    dependency_map : dict | None
        Custom dependency graph. Defaults to canonical.
    event_callback : callable | None
        Event hook for streaming/logging.

    Returns
    -------
    dict[str, Any]
        Orchestrator state:
        - run: pipeline run dict
        - artifact_store: artifact store dict
        - handlers: resolved handler registry
        - dependency_map: resolved dependency graph
        - event_callback: event hook
        - stage_results: list (populated during execution)
        - orchestrator_version: version string
        - module_role: "orchestrator"
    """
    deps = dependency_map if dependency_map is not None else copy.deepcopy(_DEFAULT_DEPENDENCY_MAP)

    run = create_pipeline_run(
        trigger_source=trigger_source,
        requested_scope=requested_scope,
        run_id=run_id,
        metadata=metadata,
    )

    # Initialize stage states with dependency info
    stages = run["stages"]
    for stage_key, dep_list in deps.items():
        if stage_key in stages:
            stages[stage_key]["depends_on"] = dep_list

    store = create_artifact_store(run["run_id"])

    resolved_handlers = get_default_handlers()
    if handlers:
        resolved_handlers.update(handlers)

    return {
        "run": run,
        "artifact_store": store,
        "handlers": resolved_handlers,
        "dependency_map": deps,
        "event_callback": event_callback,
        "stage_results": [],
        "orchestrator_version": _ORCHESTRATOR_VERSION,
        "module_role": _MODULE_ROLE,
    }


# =====================================================================
#  Public API: run_pipeline
# =====================================================================

def run_pipeline(
    *,
    trigger_source: str = "manual",
    requested_scope: dict[str, Any] | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    event_callback: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Execute a full pipeline with default stub handlers.

    Convenience wrapper: creates orchestrator and executes all stages.

    Returns
    -------
    dict[str, Any]
        Pipeline result: run, artifact_store, stage_results, summary.
    """
    orch = create_orchestrator(
        trigger_source=trigger_source,
        requested_scope=requested_scope,
        run_id=run_id,
        metadata=metadata,
        event_callback=event_callback,
    )
    return _execute_pipeline(orch)


# =====================================================================
#  Public API: run_pipeline_with_handlers
# =====================================================================

def run_pipeline_with_handlers(
    handlers: dict[str, StageHandler],
    *,
    trigger_source: str = "manual",
    requested_scope: dict[str, Any] | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    dependency_map: dict[str, list[str]] | None = None,
    event_callback: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Execute a full pipeline with a custom handler registry.

    Parameters
    ----------
    handlers : dict
        Custom handlers keyed by stage_key.  Merged over defaults.
    (remaining parameters: same as run_pipeline)

    Returns
    -------
    dict[str, Any]
        Pipeline result: run, artifact_store, stage_results, summary.
    """
    orch = create_orchestrator(
        trigger_source=trigger_source,
        requested_scope=requested_scope,
        run_id=run_id,
        metadata=metadata,
        handlers=handlers,
        dependency_map=dependency_map,
        event_callback=event_callback,
    )
    return _execute_pipeline(orch)


# =====================================================================
#  Core execution loop (private)
# =====================================================================

def _execute_pipeline(orch: dict[str, Any]) -> dict[str, Any]:
    """Execute the full stage sequence and finalize.

    Iterates over PIPELINE_STAGES in canonical order, executing
    each stage through execute_stage.  Stops early if a fatal
    stage fails.

    Returns the final pipeline result dict.
    """
    run = orch["run"]
    store = orch["artifact_store"]
    handlers = orch["handlers"]
    deps = orch["dependency_map"]
    callback = orch.get("event_callback")
    stage_results: list[dict[str, Any]] = orch["stage_results"]

    halt = False  # set True by a fatal failure

    _emit_event(
        run, "run_started",
        message=f"Pipeline run '{run['run_id']}' started",
        event_callback=callback,
    )

    for stage_key in PIPELINE_STAGES:
        # ── Stop check ──────────────────────────────────────────
        if halt:
            reason = f"Pipeline halted due to prior fatal failure"
            mark_stage_skipped(run, stage_key, reason=reason)
            _emit_event(
                run, "stage_skipped", stage_key=stage_key,
                level="warning",
                message=reason,
                event_callback=callback,
            )
            stage_results.append(build_stage_result(
                stage_key=stage_key,
                handler_invoked=False,
                outcome="skipped",
                skipped_reason=reason,
                dependency_status="not_applicable",
            ))
            continue

        # ── Execute stage ───────────────────────────────────────
        handler = handlers.get(stage_key)
        if handler is None:
            logger.warning(
                "No handler registered for stage '%s'; using stub",
                stage_key,
            )
            handler = _stub_handler

        result = execute_stage(
            run, store, stage_key,
            handler=handler,
            dependency_map=deps,
            event_callback=callback,
        )
        stage_results.append(result)

        # ── Evaluate stop/continue ──────────────────────────────
        if result["outcome"] == "failed" and _is_fatal_failure(stage_key):
            halt = True
            logger.warning(
                "Fatal failure in stage '%s'; halting pipeline", stage_key,
            )

    # ── Finalize ────────────────────────────────────────────────
    finalize_run(run)

    final_status = run["status"]
    event_type = "run_completed" if final_status == "completed" else "run_failed"
    _emit_event(
        run, event_type,
        message=f"Pipeline run '{run['run_id']}' finished: {final_status}",
        event_callback=callback,
    )

    return _build_pipeline_result(run, store, stage_results)


# =====================================================================
#  Pipeline result construction
# =====================================================================

def _build_pipeline_result(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the final pipeline result dict."""
    return {
        "run": run,
        "artifact_store": artifact_store,
        "stage_results": stage_results,
        "summary": summarize_pipeline_result(run, artifact_store, stage_results),
    }


# =====================================================================
#  Public API: summarize_pipeline_result
# =====================================================================

def summarize_pipeline_result(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compact digest of a completed pipeline run.

    Returns
    -------
    dict[str, Any]
        - run_summary: from run_summary()
        - artifact_summary: from summarize_artifact_store()
        - stage_outcome_counts: {completed: n, failed: n, skipped: n}
        - total_timing_ms: sum of all stage timings
        - module_role: "orchestrator"
    """
    outcome_counts: dict[str, int] = {}
    total_timing = 0
    for sr in stage_results:
        o = sr.get("outcome", "unknown")
        outcome_counts[o] = outcome_counts.get(o, 0) + 1
        total_timing += sr.get("timing_ms") or 0

    return {
        "run_summary": run_summary(run),
        "artifact_summary": summarize_artifact_store(artifact_store),
        "stage_outcome_counts": outcome_counts,
        "total_timing_ms": total_timing,
        "module_role": _MODULE_ROLE,
    }


# =====================================================================
#  Public API: get_default_dependency_map
# =====================================================================

def get_default_dependency_map() -> dict[str, list[str]]:
    """Return a deep copy of the canonical dependency graph."""
    return copy.deepcopy(_DEFAULT_DEPENDENCY_MAP)
