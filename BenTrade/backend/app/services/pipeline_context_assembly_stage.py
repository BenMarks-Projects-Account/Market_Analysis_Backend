"""Pipeline Context Assembly Stage — Step 8.

Assembles one reusable run-level shared context package from
upstream market, model-analysis, and candidate-selection artifacts.
The shared context is consumed by downstream candidate-enrichment
stages so each candidate sees the same market/model backdrop
without re-fetching or re-computing anything.

Public API
──────────
    context_assembly_handler(run, artifact_store, stage_key, **kwargs)
        Stage handler compatible with the Step 3 orchestrator.

Role boundary
─────────────
This module:
- Retrieves upstream artifacts (Steps 4, 5, 7) from the store.
- Normalises each upstream source into a context module dict.
- Tracks per-module assembly quality and degradation.
- Writes "shared_context" and "context_assembly_summary" artifacts.
- Emits structured events via event_callback.

This module does NOT:
- Re-run any earlier stage.
- Perform candidate-specific enrichment.
- Apply policy or scoring logic.
- Make network or API calls.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.pipeline_artifact_store import (
    build_artifact_record,
    get_artifact_by_key,
    put_artifact,
)
from app.services.pipeline_run_contract import (
    build_log_event,
    build_run_error,
)

logger = logging.getLogger("bentrade.pipeline_context_assembly_stage")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "shared_context"

# ── Context module names (stable identifiers) ──────────────────
_MODULE_MARKET = "market_data"
_MODULE_MODEL = "model_analysis"
_MODULE_SELECTION = "candidate_selection"

_CONTEXT_MODULES: tuple[str, ...] = (
    _MODULE_MARKET,
    _MODULE_MODEL,
    _MODULE_SELECTION,
)
"""Ordered context module names.  Each maps to one upstream stage."""


# =====================================================================
#  Assembly-quality constants
# =====================================================================

ASSEMBLY_STATUS_FULL = "full"
ASSEMBLY_STATUS_DEGRADED = "degraded"
ASSEMBLY_STATUS_FAILED = "failed"

_VALID_ASSEMBLY_STATUSES = frozenset({
    ASSEMBLY_STATUS_FULL,
    ASSEMBLY_STATUS_DEGRADED,
    ASSEMBLY_STATUS_FAILED,
})

# =====================================================================
#  Event emission helper
# =====================================================================

def _make_event_emitter(
    run: dict[str, Any],
    event_callback: Callable[..., None] | None,
) -> Callable[..., None] | None:
    """Build an event emitter closure for context assembly events.

    Returns None if no callback is configured.
    """
    if event_callback is None:
        return None

    run_id = run["run_id"]

    def _emit(
        event_type: str,
        level: str = "info",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        merged_meta = {"stage_key": _STAGE_KEY}
        if metadata:
            merged_meta.update(metadata)

        event = build_log_event(
            run_id=run_id,
            stage_key=_STAGE_KEY,
            event_type=event_type,
            level=level,
            message=message,
            metadata=merged_meta,
        )

        # Update run log counts
        counts = run.get("log_event_counts", {})
        counts["total"] = counts.get("total", 0) + 1
        by_level = counts.get("by_level", {})
        by_level[level] = by_level.get(level, 0) + 1

        try:
            event_callback(event)
        except Exception:
            logger.warning(
                "Event callback raised during context assembly event '%s'",
                event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Upstream artifact retrieval
# =====================================================================

def _retrieve_market_module(
    artifact_store: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve market-data artifacts from Step 4.

    Returns
    -------
    dict with keys:
        available: bool — True if the market stage summary exists.
        stage_status: str | None — e.g. "success", "degraded", "failed".
        summary: dict | None — the market_stage_summary data.
        engine_keys: list[str] — engine keys that succeeded.
        engines: dict[str, Any] — engine_key → engine artifact data.
        degraded_reasons: list[str] — reasons for degradation.
    """
    summary_art = get_artifact_by_key(
        artifact_store, "market_data", "market_stage_summary",
    )

    if summary_art is None:
        return {
            "available": False,
            "stage_status": None,
            "summary": None,
            "engine_keys": [],
            "engines": {},
            "degraded_reasons": ["market_stage_summary artifact missing"],
        }

    summary_data = summary_art.get("data") or {}
    engine_keys = summary_data.get("engines_succeeded", [])

    # Retrieve per-engine artifacts
    engines: dict[str, Any] = {}
    for ek in engine_keys:
        eng_art = get_artifact_by_key(
            artifact_store, "market_data", f"engine_{ek}",
        )
        if eng_art is not None:
            engines[ek] = eng_art.get("data")

    degraded = list(summary_data.get("degraded_reasons", []))
    missing_engines = [ek for ek in engine_keys if ek not in engines]
    if missing_engines:
        degraded.append(
            f"Missing engine artifacts: {', '.join(missing_engines)}"
        )

    return {
        "available": True,
        "stage_status": summary_data.get("stage_status"),
        "summary": summary_data,
        "engine_keys": engine_keys,
        "engines": engines,
        "degraded_reasons": degraded,
    }


def _retrieve_model_module(
    artifact_store: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve model-analysis artifacts from Step 5.

    Returns
    -------
    dict with keys:
        available: bool — True if the model stage summary exists.
        stage_status: str | None
        summary: dict | None — the model_stage_summary data.
        engine_keys: list[str] — engines that were analysed.
        models: dict[str, Any] — engine_key → model artifact data.
        degraded_reasons: list[str]
    """
    summary_art = get_artifact_by_key(
        artifact_store, "market_model_analysis", "model_stage_summary",
    )

    if summary_art is None:
        return {
            "available": False,
            "stage_status": None,
            "summary": None,
            "engine_keys": [],
            "models": {},
            "degraded_reasons": ["model_stage_summary artifact missing"],
        }

    summary_data = summary_art.get("data") or {}
    engine_keys = summary_data.get("engines_analyzed", [])

    # Retrieve per-engine model artifacts
    models: dict[str, Any] = {}
    for ek in engine_keys:
        model_art = get_artifact_by_key(
            artifact_store, "market_model_analysis", f"model_{ek}",
        )
        if model_art is not None:
            models[ek] = model_art.get("data")

    degraded = list(summary_data.get("degraded_reasons", []))
    missing_models = [ek for ek in engine_keys if ek not in models]
    if missing_models:
        degraded.append(
            f"Missing model artifacts: {', '.join(missing_models)}"
        )

    return {
        "available": True,
        "stage_status": summary_data.get("stage_status"),
        "summary": summary_data,
        "engine_keys": engine_keys,
        "models": models,
        "degraded_reasons": degraded,
    }


def _retrieve_selection_module(
    artifact_store: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve candidate-selection artifacts from Step 7.

    Returns
    -------
    dict with keys:
        available: bool — True if selected_candidates exists.
        stage_status: str | None
        summary: dict | None — candidate_selection_summary data.
        selected_candidates: list[dict] — ranked/selected candidates.
        selected_count: int
        degraded_reasons: list[str]
    """
    candidates_art = get_artifact_by_key(
        artifact_store, "candidate_selection", "selected_candidates",
    )

    summary_art = get_artifact_by_key(
        artifact_store, "candidate_selection", "candidate_selection_summary",
    )

    if candidates_art is None:
        return {
            "available": False,
            "stage_status": None,
            "summary": None,
            "selected_candidates": [],
            "selected_count": 0,
            "degraded_reasons": ["selected_candidates artifact missing"],
        }

    candidates_data = candidates_art.get("data") or []
    summary_data = (summary_art.get("data") or {}) if summary_art else {}

    degraded: list[str] = []
    if summary_art is None:
        degraded.append("candidate_selection_summary artifact missing")
    degraded.extend(summary_data.get("degraded_reasons", []))

    return {
        "available": True,
        "stage_status": summary_data.get("stage_status"),
        "summary": summary_data,
        "selected_candidates": candidates_data,
        "selected_count": len(candidates_data),
        "degraded_reasons": degraded,
    }


# =====================================================================
#  Per-module assembly record builder
# =====================================================================

def _build_module_record(
    module_name: str,
    retrieval_result: dict[str, Any],
    *,
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    """Build a per-module assembly record for the assembly ledger.

    Parameters
    ----------
    module_name : str
        One of _CONTEXT_MODULES.
    retrieval_result : dict
        Output from _retrieve_*_module().
    elapsed_ms : int
        Wall-clock time for the retrieval.

    Returns
    -------
    dict with keys:
        module_name, available, stage_status, degraded_reasons,
        assembly_status, elapsed_ms
    """
    available = retrieval_result.get("available", False)
    degraded_reasons = retrieval_result.get("degraded_reasons", [])
    stage_status = retrieval_result.get("stage_status")

    if not available:
        assembly_status = ASSEMBLY_STATUS_FAILED
    elif degraded_reasons:
        assembly_status = ASSEMBLY_STATUS_DEGRADED
    else:
        assembly_status = ASSEMBLY_STATUS_FULL

    return {
        "module_name": module_name,
        "available": available,
        "stage_status": stage_status,
        "degraded_reasons": degraded_reasons,
        "assembly_status": assembly_status,
        "elapsed_ms": elapsed_ms,
    }


# =====================================================================
#  Shared context assembly
# =====================================================================

def _compute_overall_assembly_status(
    module_records: list[dict[str, Any]],
) -> str:
    """Derive the overall assembly status from per-module records.

    - "full" if all modules are "full".
    - "degraded" if any module is "degraded" (but none failed).
    - "failed" if any module is "failed".

    Input: market_data and model_analysis are required;
           candidate_selection is required for downstream work.
    """
    statuses = {r["module_name"]: r["assembly_status"] for r in module_records}

    if any(s == ASSEMBLY_STATUS_FAILED for s in statuses.values()):
        return ASSEMBLY_STATUS_FAILED
    if any(s == ASSEMBLY_STATUS_DEGRADED for s in statuses.values()):
        return ASSEMBLY_STATUS_DEGRADED
    return ASSEMBLY_STATUS_FULL


def assemble_shared_context(
    artifact_store: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the shared context package from upstream artifacts.

    This is the core assembly function.  It retrieves each upstream
    module, builds per-module assembly records, and produces the
    unified shared-context dict.

    Returns
    -------
    dict with keys:
        context_modules: dict — module_name → normalised module data
        module_records: list[dict] — per-module assembly records
        overall_status: str — "full" | "degraded" | "failed"
        degraded_reasons: list[str] — aggregated degradation reasons
        assembled_at: str — ISO-8601 timestamp
    """
    module_records: list[dict[str, Any]] = []
    context_modules: dict[str, Any] = {}
    all_degraded: list[str] = []

    # ── Market data module ──────────────────────────────────────
    t0 = time.monotonic()
    market_result = _retrieve_market_module(artifact_store)
    market_ms = int((time.monotonic() - t0) * 1000)
    module_records.append(
        _build_module_record(_MODULE_MARKET, market_result, elapsed_ms=market_ms)
    )
    context_modules[_MODULE_MARKET] = {
        "available": market_result["available"],
        "stage_status": market_result["stage_status"],
        "summary": market_result["summary"],
        "engine_keys": market_result["engine_keys"],
        "engines": market_result["engines"],
    }
    all_degraded.extend(
        f"[{_MODULE_MARKET}] {r}" for r in market_result["degraded_reasons"]
    )

    # ── Model analysis module ───────────────────────────────────
    t0 = time.monotonic()
    model_result = _retrieve_model_module(artifact_store)
    model_ms = int((time.monotonic() - t0) * 1000)
    module_records.append(
        _build_module_record(_MODULE_MODEL, model_result, elapsed_ms=model_ms)
    )
    context_modules[_MODULE_MODEL] = {
        "available": model_result["available"],
        "stage_status": model_result["stage_status"],
        "summary": model_result["summary"],
        "engine_keys": model_result["engine_keys"],
        "models": model_result["models"],
    }
    all_degraded.extend(
        f"[{_MODULE_MODEL}] {r}" for r in model_result["degraded_reasons"]
    )

    # ── Candidate selection module ──────────────────────────────
    t0 = time.monotonic()
    selection_result = _retrieve_selection_module(artifact_store)
    selection_ms = int((time.monotonic() - t0) * 1000)
    module_records.append(
        _build_module_record(
            _MODULE_SELECTION, selection_result, elapsed_ms=selection_ms,
        )
    )
    context_modules[_MODULE_SELECTION] = {
        "available": selection_result["available"],
        "stage_status": selection_result["stage_status"],
        "summary": selection_result["summary"],
        "selected_candidates": selection_result["selected_candidates"],
        "selected_count": selection_result["selected_count"],
    }
    all_degraded.extend(
        f"[{_MODULE_SELECTION}] {r}" for r in selection_result["degraded_reasons"]
    )

    # ── Compute overall status ──────────────────────────────────
    overall_status = _compute_overall_assembly_status(module_records)

    return {
        "context_modules": context_modules,
        "module_records": module_records,
        "overall_status": overall_status,
        "degraded_reasons": all_degraded,
        "assembled_at": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
#  Artifact writers
# =====================================================================

def _write_shared_context_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    shared_context: dict[str, Any],
) -> str:
    """Write the shared_context artifact.  Returns artifact_id."""
    modules = shared_context.get("context_modules", {})
    module_statuses = {
        name: mod.get("stage_status")
        for name, mod in modules.items()
    }

    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="shared_context",
        artifact_type="shared_context",
        data=shared_context,
        summary={
            "overall_status": shared_context.get("overall_status"),
            "module_count": len(modules),
            "module_statuses": module_statuses,
            "degraded_reason_count": len(
                shared_context.get("degraded_reasons", [])
            ),
            "assembled_at": shared_context.get("assembled_at"),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_context_assembly_summary_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    summary: dict[str, Any],
) -> str:
    """Write the context_assembly_summary artifact.  Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="context_assembly_summary",
        artifact_type="context_assembly_summary",
        data=summary,
        summary={
            "overall_status": summary.get("overall_status"),
            "modules_assembled": summary.get("modules_assembled"),
            "modules_degraded": summary.get("modules_degraded"),
            "modules_failed": summary.get("modules_failed"),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


# =====================================================================
#  Stage handler — public entry point
# =====================================================================

def context_assembly_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Shared context assembly stage handler (Step 8).

    Retrieves upstream artifacts from Steps 4 (market), 5 (model),
    and 7 (candidate selection), assembles a normalised shared context
    package, writes artifacts, and emits structured events.

    Parameters
    ----------
    run : dict
        The pipeline run dict (passed by orchestrator).
    artifact_store : dict
        The artifact store (passed by orchestrator).
    stage_key : str
        Expected to be "shared_context".
    **kwargs
        event_callback : callable | None
            Optional event callback for structured events.

    Returns
    -------
    dict[str, Any]
        Handler result compatible with Step 3 orchestrator:
        { outcome, summary_counts, artifacts, metadata, error }
    """
    t0 = time.monotonic()
    run_id = run["run_id"]

    # ── 1. Resolve parameters ───────────────────────────────────
    event_callback = kwargs.get("event_callback")
    emit = _make_event_emitter(run, event_callback)

    # ── 2. Emit context_assembly_started ────────────────────────
    if emit:
        emit(
            "context_assembly_started",
            message="Shared context assembly started",
        )

    # ── 3. Assemble shared context ──────────────────────────────
    try:
        shared_context = assemble_shared_context(artifact_store)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "Context assembly failed: %s", exc, exc_info=True,
        )
        if emit:
            emit(
                "context_assembly_failed",
                level="error",
                message=f"Context assembly failed: {exc}",
            )
        return {
            "outcome": "failed",
            "summary_counts": {
                "modules_assembled": 0,
                "modules_degraded": 0,
                "modules_failed": len(_CONTEXT_MODULES),
            },
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="CONTEXT_ASSEMBLY_EXCEPTION",
                message=f"Context assembly raised {type(exc).__name__}: {exc}",
                source=_STAGE_KEY,
            ),
        }

    # ── 4. Evaluate assembly outcome ────────────────────────────
    overall_status = shared_context["overall_status"]
    module_records = shared_context["module_records"]

    modules_full = sum(
        1 for r in module_records
        if r["assembly_status"] == ASSEMBLY_STATUS_FULL
    )
    modules_degraded = sum(
        1 for r in module_records
        if r["assembly_status"] == ASSEMBLY_STATUS_DEGRADED
    )
    modules_failed = sum(
        1 for r in module_records
        if r["assembly_status"] == ASSEMBLY_STATUS_FAILED
    )

    # ── 5. Build stage summary ──────────────────────────────────
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    generated_at = datetime.now(timezone.utc).isoformat()

    stage_summary = {
        "stage_key": _STAGE_KEY,
        "overall_status": overall_status,
        "modules_assembled": modules_full + modules_degraded,
        "modules_full": modules_full,
        "modules_degraded": modules_degraded,
        "modules_failed": modules_failed,
        "module_records": module_records,
        "degraded_reasons": shared_context["degraded_reasons"],
        "shared_context_artifact_ref": None,  # filled after write
        "summary_artifact_ref": None,         # filled after write
        "elapsed_ms": elapsed_ms,
        "generated_at": generated_at,
    }

    # ── 6. Write artifacts ──────────────────────────────────────
    shared_context_art_id = _write_shared_context_artifact(
        artifact_store, run_id, shared_context,
    )
    stage_summary["shared_context_artifact_ref"] = shared_context_art_id

    summary_art_id = _write_context_assembly_summary_artifact(
        artifact_store, run_id, stage_summary,
    )
    stage_summary["summary_artifact_ref"] = summary_art_id

    # Build artifact dicts for the handler return (orchestrator will
    # also write these, but the artifact store already has them via
    # put_artifact above).  We return empty artifacts list to avoid
    # double-write by the orchestrator's _write_handler_artifacts.
    artifacts_written = 2

    # ── 7. Determine outcome ────────────────────────────────────
    # The handler succeeds even when degraded — degradation is
    # informational.  Outcome is "failed" only if ALL modules
    # failed (no usable context at all).
    if overall_status == ASSEMBLY_STATUS_FAILED:
        outcome = "failed"
        if emit:
            emit(
                "context_assembly_failed",
                level="error",
                message=(
                    f"Context assembly failed: "
                    f"{modules_failed}/{len(_CONTEXT_MODULES)} modules failed"
                ),
                metadata={
                    "modules_failed": modules_failed,
                    "degraded_reasons": shared_context["degraded_reasons"],
                },
            )
        return {
            "outcome": "failed",
            "summary_counts": {
                "modules_assembled": modules_full + modules_degraded,
                "modules_degraded": modules_degraded,
                "modules_failed": modules_failed,
            },
            "artifacts": [],
            "metadata": {
                "overall_status": overall_status,
                "module_records": module_records,
                "stage_summary": stage_summary,
                "elapsed_ms": elapsed_ms,
            },
            "error": build_run_error(
                code="CONTEXT_ASSEMBLY_FAILED",
                message=(
                    f"All {modules_failed} context modules failed assembly"
                    if modules_failed == len(_CONTEXT_MODULES)
                    else f"{modules_failed}/{len(_CONTEXT_MODULES)} modules failed"
                ),
                source=_STAGE_KEY,
            ),
        }

    # ── 8. Emit context_assembly_completed ──────────────────────
    if emit:
        emit(
            "context_assembly_completed",
            message=(
                f"Context assembly completed ({overall_status}): "
                f"{modules_full} full, {modules_degraded} degraded, "
                f"{modules_failed} failed"
            ),
            metadata={
                "overall_status": overall_status,
                "modules_full": modules_full,
                "modules_degraded": modules_degraded,
                "modules_failed": modules_failed,
                "artifacts_written": artifacts_written,
                "elapsed_ms": elapsed_ms,
            },
        )

    # ── 9. Return handler result ────────────────────────────────
    return {
        "outcome": "completed",
        "summary_counts": {
            "modules_assembled": modules_full + modules_degraded,
            "modules_degraded": modules_degraded,
            "modules_failed": modules_failed,
        },
        "artifacts": [],  # Already written directly via put_artifact
        "metadata": {
            "overall_status": overall_status,
            "module_records": module_records,
            "stage_summary": stage_summary,
            "shared_context_artifact_id": shared_context_art_id,
            "summary_artifact_id": summary_art_id,
            "elapsed_ms": elapsed_ms,
        },
        "error": None,
    }
