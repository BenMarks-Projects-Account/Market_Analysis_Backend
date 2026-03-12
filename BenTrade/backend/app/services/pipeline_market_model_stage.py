"""Pipeline Market Model-Analysis Stage v1.0 — per-engine model enrichment.

Implements the ``market_model_analysis`` stage handler for the BenTrade
pipeline orchestrator (Step 5).  Reads Step 4 engine output artifacts,
normalizes them into model-ready input, runs model analysis per eligible
engine, and persists distinct model-analysis artifacts.

Public API
──────────
    market_model_stage_handler(...)             Orchestrator-compatible handler.
    build_model_analysis_record(...)            Per-engine execution record.
    build_model_stage_summary(...)              Stage-level summary.
    normalize_engine_for_model(...)             Engine output → model input.
    DEFAULT_MODEL_MAX_WORKERS                   Default concurrency limit.

Role boundary
─────────────
This module owns the *model-enrichment pass* over market-picture
outputs created in Step 4.

It does NOT:
- re-run market engines (Step 4's job)
- build final market composites (later stage)
- execute scanners or per-candidate logic
- make final trade decisions
- persist to disk / database (artifact store handles that)
"""

from __future__ import annotations

import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.engine_output_contract import (
    ENGINE_METADATA,
    normalize_engine_output,
)
from app.services.pipeline_artifact_store import (
    build_artifact_record,
    get_artifact_by_key,
    put_artifact,
)
from app.services.pipeline_run_contract import (
    build_log_event,
    build_run_error,
)

logger = logging.getLogger("bentrade.pipeline_market_model_stage")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "market_model_analysis"
_SOURCE_STAGE_KEY = "market_data"

# ── Concurrency ─────────────────────────────────────────────────
DEFAULT_MODEL_MAX_WORKERS: int = 1
"""Default concurrency limit for parallel model-analysis calls.

Set to 1 — local LLM endpoints are single-capacity and share context
window memory across concurrent requests, leading to "Context size
exceeded" errors when parallelized.  Override via kwargs['max_workers'].
"""

# ── Analysis status vocabulary ──────────────────────────────────
ANALYSIS_STATUSES = frozenset({
    "analyzed",
    "skipped_not_eligible",
    "skipped_missing_artifact",
    "skipped_invalid_payload",
    "skipped_disabled",
    "failed",
})

# ── Stage outcome thresholds ────────────────────────────────────
_MIN_ANALYSES_FOR_SUCCESS: int = 1
"""Minimum number of analyses that must succeed for the stage to
complete.  0 successes → stage fails."""


# =====================================================================
#  Timestamp helper
# =====================================================================

def _now_iso() -> str:
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


# =====================================================================
#  Per-engine model-analysis record
# =====================================================================

def build_model_analysis_record(
    *,
    engine_key: str,
    status: str,
    source_artifact_ref: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    elapsed_ms: int | None = None,
    model_provider: str | None = None,
    model_name: str | None = None,
    normalized_input_ref: str | None = None,
    normalized_output_ref: str | None = None,
    error: dict[str, Any] | None = None,
    downstream_usable: bool = False,
) -> dict[str, Any]:
    """Build a normalized per-engine model-analysis execution record.

    Parameters
    ----------
    engine_key : str
        Stable engine identifier matching ENGINE_METADATA keys.
    status : str
        One of ANALYSIS_STATUSES.
    source_artifact_ref : str | None
        artifact_id of the Step 4 engine output used as input.
    started_at / completed_at : str | None
        ISO timestamps.
    elapsed_ms : int | None
        Wall-clock time in ms.
    model_provider / model_name : str | None
        Model identification metadata when available.
    normalized_input_ref : str | None
        artifact_id of the persisted normalized model input, if any.
    normalized_output_ref : str | None
        artifact_id of the persisted model output, if any.
    error : dict | None
        Structured error if failed.
    downstream_usable : bool
        Whether this result is usable by later stages.
    """
    return {
        "engine_key": engine_key,
        "status": status,
        "source_artifact_ref": source_artifact_ref,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_ms": elapsed_ms,
        "model_provider": model_provider,
        "model_name": model_name,
        "normalized_input_ref": normalized_input_ref,
        "normalized_output_ref": normalized_output_ref,
        "error": error,
        "downstream_usable": downstream_usable,
    }


# =====================================================================
#  Step 4 artifact retrieval
# =====================================================================

def _get_market_stage_summary(
    artifact_store: dict[str, Any],
) -> dict[str, Any] | None:
    """Retrieve the Step 4 market_stage_summary artifact."""
    art = get_artifact_by_key(
        artifact_store, _SOURCE_STAGE_KEY, "market_stage_summary",
    )
    if art is None:
        return None
    return art


def _get_engine_artifact(
    artifact_store: dict[str, Any],
    engine_key: str,
) -> dict[str, Any] | None:
    """Retrieve a per-engine output artifact from Step 4."""
    return get_artifact_by_key(
        artifact_store, _SOURCE_STAGE_KEY, f"engine_{engine_key}",
    )


# =====================================================================
#  Eligibility logic
# =====================================================================

def _determine_eligibility(
    engine_key: str,
    summary_entry: dict[str, Any] | None,
    engine_artifact: dict[str, Any] | None,
    *,
    disabled_engines: set[str] | None = None,
) -> tuple[str, str]:
    """Determine whether an engine output should receive model analysis.

    Returns
    -------
    (status, reason)
        status: one of ANALYSIS_STATUSES skip variants or "eligible"
        reason: human-readable explanation
    """
    if disabled_engines and engine_key in disabled_engines:
        return "skipped_disabled", f"Engine '{engine_key}' disabled for model analysis"

    if summary_entry is None:
        return "skipped_not_eligible", (
            f"Engine '{engine_key}' not found in Step 4 summary"
        )

    if not summary_entry.get("eligible_for_model_analysis", False):
        return "skipped_not_eligible", (
            f"Engine '{engine_key}' not marked eligible (status={summary_entry.get('status', 'unknown')})"
        )

    if engine_artifact is None:
        return "skipped_missing_artifact", (
            f"Engine '{engine_key}' artifact missing from store"
        )

    # Validate payload has actual data
    artifact_data = engine_artifact.get("data")
    if artifact_data is None or (isinstance(artifact_data, dict) and not artifact_data):
        return "skipped_invalid_payload", (
            f"Engine '{engine_key}' artifact has empty/null data"
        )

    return "eligible", ""


# =====================================================================
#  Engine output normalization (model-ready input)
# =====================================================================

def normalize_engine_for_model(
    engine_key: str,
    engine_artifact: dict[str, Any],
) -> dict[str, Any]:
    """Convert a Step 4 engine output artifact into a model-ready input.

    Uses engine_output_contract.normalize_engine_output() for the
    heavy lifting, then wraps the result into a compact model-input
    structure.

    Parameters
    ----------
    engine_key : str
        Stable engine identifier.
    engine_artifact : dict
        The full artifact record from the artifact store.

    Returns
    -------
    dict[str, Any]
        Stable model-input payload with:
        - engine_key
        - engine_name
        - source_artifact_ref
        - normalized_data (from engine_output_contract)
        - compact_summary (extracted key metrics)
        - warnings
    """
    raw_data = engine_artifact.get("data", {})
    source_ref = engine_artifact.get("artifact_id")
    meta = ENGINE_METADATA.get(engine_key, {})

    # Normalize through the engine output contract
    normalized = normalize_engine_output(engine_key, raw_data or {})

    # Extract compact summary for model context
    compact_summary = {
        "score": normalized.get("score"),
        "label": normalized.get("label"),
        "confidence": normalized.get("confidence"),
        "signal_quality": normalized.get("signal_quality"),
        "engine_status": normalized.get("engine_status"),
        "summary": normalized.get("summary"),
        "trader_takeaway": normalized.get("trader_takeaway"),
    }

    warnings = list(normalized.get("warnings") or [])
    status_detail = normalized.get("status_detail", {})
    if status_detail.get("degraded_reasons"):
        warnings.extend(status_detail["degraded_reasons"])

    return {
        "engine_key": engine_key,
        "engine_name": meta.get("name", engine_key),
        "source_artifact_ref": source_ref,
        "normalized_data": normalized,
        "compact_summary": compact_summary,
        "warnings": warnings,
    }


# =====================================================================
#  Model execution seam
# =====================================================================

def _default_model_executor(
    engine_key: str,
    model_input: dict[str, Any],
) -> dict[str, Any]:
    """Default model execution function using common.model_analysis.

    Dispatches to the appropriate analyze_* function based on
    engine_key.  Returns the raw model analysis result dict.

    This is the seam that later model-routing work can integrate
    with cleanly — replace this callable via kwargs['model_executor'].
    """
    from common.model_analysis import (
        analyze_breadth_participation,
        analyze_cross_asset_macro,
        analyze_flows_positioning,
        analyze_liquidity_conditions,
        analyze_news_sentiment,
        analyze_volatility_options,
    )

    # The analyze_* functions expect engine_result dict
    engine_data = model_input.get("normalized_data", {})

    dispatch: dict[str, Callable] = {
        "breadth_participation": lambda: analyze_breadth_participation(
            engine_result=engine_data,
        ),
        "volatility_options": lambda: analyze_volatility_options(
            engine_result=engine_data,
        ),
        "liquidity_financial_conditions": lambda: analyze_liquidity_conditions(
            engine_result=engine_data,
        ),
        "cross_asset_macro": lambda: analyze_cross_asset_macro(
            engine_result=engine_data,
        ),
        "flows_positioning": lambda: analyze_flows_positioning(
            engine_result=engine_data,
        ),
        "news_sentiment": lambda: analyze_news_sentiment(
            # items and macro_context live under detail_sections after
            # normalize_engine_output(); fall back to top-level keys for
            # callers that pass raw (un-normalized) engine payloads.
            items=(
                engine_data.get("detail_sections", {}).get("items")
                or engine_data.get("items", [])
            ),
            macro_context=(
                engine_data.get("detail_sections", {}).get("macro_context")
                or engine_data.get("macro_context", {})
            ),
        ),
    }

    fn = dispatch.get(engine_key)
    if fn is None:
        raise ValueError(f"No model analysis function for engine '{engine_key}'")

    return fn()


# =====================================================================
#  Single-engine model analysis execution
# =====================================================================

def _run_single_model_analysis(
    engine_key: str,
    model_input: dict[str, Any],
    source_artifact_ref: str | None,
    model_executor: Callable[[str, dict[str, Any]], dict[str, Any]],
    event_emitter: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Execute model analysis for a single engine output.

    Returns a per-engine model-analysis record.
    """
    started_at = _now_iso()
    t0 = time.monotonic()

    if event_emitter:
        event_emitter(
            "model_analysis_started",
            engine_key=engine_key,
            message=f"Model analysis starting for '{engine_key}'",
            metadata={"source_artifact_ref": source_artifact_ref},
        )

    try:
        result = model_executor(engine_key, model_input)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        completed_at = _now_iso()

        # Extract model/provider metadata if available
        model_provider = None
        model_name = None
        if isinstance(result, dict):
            model_provider = result.get("model_source") or result.get("provider")
            model_name = result.get("model") or result.get("model_name")

        if event_emitter:
            event_emitter(
                "model_analysis_completed",
                engine_key=engine_key,
                message=f"Model analysis completed for '{engine_key}' in {elapsed_ms}ms",
                metadata={
                    "elapsed_ms": elapsed_ms,
                    "source_artifact_ref": source_artifact_ref,
                },
            )

        return {
            "record": build_model_analysis_record(
                engine_key=engine_key,
                status="analyzed",
                source_artifact_ref=source_artifact_ref,
                started_at=started_at,
                completed_at=completed_at,
                elapsed_ms=elapsed_ms,
                model_provider=model_provider,
                model_name=model_name,
                downstream_usable=True,
            ),
            "model_output": result,
        }

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        completed_at = _now_iso()
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)

        logger.error(
            "Model analysis for '%s' failed: %s: %s",
            engine_key, type(exc).__name__, exc, exc_info=True,
        )

        if event_emitter:
            event_emitter(
                "model_analysis_failed",
                engine_key=engine_key,
                level="error",
                message=f"Model analysis failed for '{engine_key}': {type(exc).__name__}: {exc}",
                metadata={"source_artifact_ref": source_artifact_ref},
            )

        return {
            "record": build_model_analysis_record(
                engine_key=engine_key,
                status="failed",
                source_artifact_ref=source_artifact_ref,
                started_at=started_at,
                completed_at=completed_at,
                elapsed_ms=elapsed_ms,
                error=build_run_error(
                    code="MODEL_ANALYSIS_EXCEPTION",
                    message=f"{type(exc).__name__}: {exc}",
                    source=f"model_analysis.{engine_key}",
                    detail={"traceback": tb},
                ),
            ),
            "model_output": None,
        }


# =====================================================================
#  Bounded parallel execution
# =====================================================================

def _execute_analyses_parallel(
    work_items: list[dict[str, Any]],
    model_executor: Callable[[str, dict[str, Any]], dict[str, Any]],
    max_workers: int,
    event_emitter: Callable[..., None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run model analyses with bounded parallelism.

    Parameters
    ----------
    work_items : list
        Each item: {engine_key, model_input, source_artifact_ref}
    model_executor : callable
        (engine_key, model_input) -> dict
    max_workers : int
        Concurrency limit.
    event_emitter : callable | None
        Event callback for progress events.

    Returns
    -------
    dict[str, dict[str, Any]]
        engine_key → {record, model_output}
    """
    results: dict[str, dict[str, Any]] = {}
    if not work_items:
        return results

    actual_workers = min(max_workers, len(work_items))

    with ThreadPoolExecutor(max_workers=actual_workers) as pool:
        futures = {
            pool.submit(
                _run_single_model_analysis,
                item["engine_key"],
                item["model_input"],
                item["source_artifact_ref"],
                model_executor,
                event_emitter,
            ): item["engine_key"]
            for item in work_items
        }

        for future in futures:
            engine_key = futures[future]
            try:
                result = future.result()
                results[engine_key] = result
            except Exception as exc:
                logger.error(
                    "Unexpected executor error for model analysis '%s': %s",
                    engine_key, exc, exc_info=True,
                )
                results[engine_key] = {
                    "record": build_model_analysis_record(
                        engine_key=engine_key,
                        status="failed",
                        error=build_run_error(
                            code="MODEL_EXECUTOR_ERROR",
                            message=f"Executor error: {type(exc).__name__}: {exc}",
                            source=f"model_analysis.{engine_key}",
                        ),
                    ),
                    "model_output": None,
                }

    return results


# =====================================================================
#  Stage summary builder
# =====================================================================

def build_model_stage_summary(
    analysis_records: dict[str, dict[str, Any]],
    skipped_records: dict[str, dict[str, Any]],
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    """Build the model-analysis stage summary artifact payload.

    Parameters
    ----------
    analysis_records : dict
        engine_key → {record, model_output} for engines that were
        attempted (analyzed or failed).
    skipped_records : dict
        engine_key → analysis record for engines skipped by design.
    elapsed_ms : int | None
        Total wall-clock time for the stage.
    """
    analyzed = []
    failed = []
    skipped_by_reason: dict[str, list[str]] = {}
    artifact_refs: dict[str, str | None] = {}
    engine_summaries: dict[str, dict[str, Any]] = {}

    # Process attempted analyses
    for key, entry in analysis_records.items():
        rec = entry.get("record", {})
        status = rec.get("status", "failed")
        artifact_refs[key] = rec.get("normalized_output_ref")

        if status == "analyzed":
            analyzed.append(key)
        else:
            failed.append(key)

        engine_summaries[key] = {
            "status": status,
            "elapsed_ms": rec.get("elapsed_ms"),
            "model_provider": rec.get("model_provider"),
            "model_name": rec.get("model_name"),
            "source_artifact_ref": rec.get("source_artifact_ref"),
            "output_artifact_ref": rec.get("normalized_output_ref"),
            "downstream_usable": rec.get("downstream_usable", False),
        }

    # Process skipped engines
    for key, rec in skipped_records.items():
        status = rec.get("status", "skipped_not_eligible")
        bucket = skipped_by_reason.setdefault(status, [])
        bucket.append(key)

        engine_summaries[key] = {
            "status": status,
            "elapsed_ms": None,
            "model_provider": None,
            "model_name": None,
            "source_artifact_ref": rec.get("source_artifact_ref"),
            "output_artifact_ref": None,
            "downstream_usable": False,
        }

    total_considered = len(analysis_records) + len(skipped_records)
    total_attempted = len(analysis_records)
    analyzed_count = len(analyzed)
    failed_count = len(failed)
    skipped_count = len(skipped_records)

    # Stage-level status rollup
    if total_attempted == 0:
        stage_status = "no_eligible_inputs"
    elif analyzed_count == 0:
        stage_status = "failed"
    elif failed_count > 0:
        stage_status = "degraded"
    else:
        stage_status = "success"

    # Degraded reasoning
    degraded_reasons = []
    if failed:
        degraded_reasons.append(f"analyses_failed: {failed}")

    return {
        "stage_key": _STAGE_KEY,
        "stage_status": stage_status,
        "total_considered": total_considered,
        "total_attempted": total_attempted,
        "engines_analyzed": analyzed,
        "engines_failed": failed,
        "engines_skipped": skipped_by_reason,
        "analyzed_count": analyzed_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "artifact_refs": artifact_refs,
        "degraded_reasons": degraded_reasons,
        "engine_summaries": engine_summaries,
        "elapsed_ms": elapsed_ms,
        "generated_at": _now_iso(),
    }


# =====================================================================
#  Artifact writing helpers
# =====================================================================

def _write_model_output_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    engine_key: str,
    model_output: Any,
    record: dict[str, Any],
) -> str | None:
    """Write a per-engine model-analysis output artifact.

    Returns the artifact_id, or None if nothing to write.
    """
    if model_output is None:
        return None

    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key=f"model_{engine_key}",
        artifact_type="market_model_output",
        data=model_output,
        summary={
            "engine_key": engine_key,
            "status": record.get("status"),
            "model_provider": record.get("model_provider"),
        },
        metadata={
            "engine_key": engine_key,
            "source_artifact_ref": record.get("source_artifact_ref"),
            "elapsed_ms": record.get("elapsed_ms"),
            "model_provider": record.get("model_provider"),
            "model_name": record.get("model_name"),
        },
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_model_stage_summary_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    summary: dict[str, Any],
) -> str:
    """Write the model-analysis stage summary artifact. Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="model_stage_summary",
        artifact_type="market_model_output",
        data=summary,
        summary={
            "stage_status": summary.get("stage_status"),
            "analyzed_count": summary.get("analyzed_count"),
            "failed_count": summary.get("failed_count"),
            "skipped_count": summary.get("skipped_count"),
        },
        metadata={
            "engine_keys": list(summary.get("engine_summaries", {}).keys()),
        },
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


# =====================================================================
#  Event emission helper (within stage)
# =====================================================================

def _make_event_emitter(
    run: dict[str, Any],
    event_callback: Callable[..., None] | None,
) -> Callable[..., None] | None:
    """Build a thread-safe event emitter closure for model-analysis events.

    Returns None if no callback is configured.
    """
    if event_callback is None:
        return None

    run_id = run["run_id"]

    def _emit(
        event_type: str,
        engine_key: str = "",
        level: str = "info",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        merged_meta = {"engine_key": engine_key}
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

        # Update run log counts (thread-safe enough for counters)
        counts = run.get("log_event_counts", {})
        counts["total"] = counts.get("total", 0) + 1
        by_level = counts.get("by_level", {})
        by_level[level] = by_level.get(level, 0) + 1

        try:
            event_callback(event)
        except Exception:
            logger.warning(
                "Event callback raised during model analysis '%s' event '%s'",
                engine_key, event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Stage handler (orchestrator-compatible)
# =====================================================================

def market_model_stage_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Orchestrator-compatible handler for the market_model_analysis stage.

    Sequence:
    1. Retrieve Step 4 market_stage_summary.
    2. Identify eligible engines from the summary.
    3. Retrieve per-engine artifacts from Step 4.
    4. Determine eligibility per engine.
    5. Normalize eligible engine outputs into model-ready inputs.
    6. Execute model analysis per eligible engine (bounded parallel).
    7. Write per-engine model-output artifacts.
    8. Build and write stage summary artifact.
    9. Determine stage outcome.
    10. Return handler result dict.

    Handler kwargs (passed via orchestrator handler_kwargs)
    ──────────────────────────────────────────────────────
    model_executor : callable | None
        Override the model execution function (for testing).
        Signature: (engine_key, model_input) -> dict
    max_workers : int
        Concurrency limit for parallel model calls.
    event_callback : callable | None
        Event callback (fallback if orchestrator doesn't inject one).
    disabled_engines : set[str] | None
        Engine keys to skip for this run.
    model_results_override : dict | None
        Pre-computed model results keyed by engine_key (for testing/replay).
        Skips actual model execution.

    Returns
    -------
    dict[str, Any]
        Handler result compatible with Step 3 orchestrator:
        { outcome, summary_counts, artifacts, metadata, error }
    """
    t0 = time.monotonic()
    run_id = run["run_id"]

    # ── Resolve parameters ──────────────────────────────────────
    model_executor = kwargs.get("model_executor", _default_model_executor)
    max_workers = kwargs.get("max_workers", DEFAULT_MODEL_MAX_WORKERS)
    event_callback = kwargs.get("event_callback")
    event_emitter = _make_event_emitter(run, event_callback)
    disabled_engines: set[str] = set(kwargs.get("disabled_engines") or [])
    model_results_override: dict[str, Any] | None = kwargs.get("model_results_override")

    # ── 1. Retrieve Step 4 summary ──────────────────────────────
    summary_artifact = _get_market_stage_summary(artifact_store)
    if summary_artifact is None:
        return _build_no_summary_result(t0)

    summary_data = summary_artifact.get("data", {})
    engine_summaries = summary_data.get("engine_summaries", {})

    if not engine_summaries:
        return _build_no_engines_result(
            artifact_store, run_id, t0,
            reason="Step 4 summary has no engine entries",
        )

    # ── 2-4. Evaluate eligibility per engine ────────────────────
    work_items: list[dict[str, Any]] = []
    skipped_records: dict[str, dict[str, Any]] = {}

    for engine_key, summary_entry in engine_summaries.items():
        # Retrieve Step 4 artifact
        engine_artifact = _get_engine_artifact(artifact_store, engine_key)

        # Determine eligibility
        elig_status, elig_reason = _determine_eligibility(
            engine_key, summary_entry, engine_artifact,
            disabled_engines=disabled_engines,
        )

        if elig_status != "eligible":
            skipped_records[engine_key] = build_model_analysis_record(
                engine_key=engine_key,
                status=elig_status,
                source_artifact_ref=(
                    engine_artifact.get("artifact_id") if engine_artifact else None
                ),
            )
            continue

        # ── 5. Normalize into model-ready input ─────────────────
        try:
            model_input = normalize_engine_for_model(engine_key, engine_artifact)
        except Exception as exc:
            logger.warning(
                "Normalization failed for '%s': %s", engine_key, exc,
            )
            skipped_records[engine_key] = build_model_analysis_record(
                engine_key=engine_key,
                status="skipped_invalid_payload",
                source_artifact_ref=engine_artifact.get("artifact_id"),
                error=build_run_error(
                    code="NORMALIZATION_FAILED",
                    message=f"Normalization failed: {type(exc).__name__}: {exc}",
                    source=f"model_analysis.{engine_key}",
                ),
            )
            continue

        work_items.append({
            "engine_key": engine_key,
            "model_input": model_input,
            "source_artifact_ref": engine_artifact.get("artifact_id"),
        })

    # ── Handle zero eligible engines ────────────────────────────
    if not work_items and model_results_override is None:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        summary = build_model_stage_summary({}, skipped_records, elapsed_ms)
        summary_art_id = _write_model_stage_summary_artifact(
            artifact_store, run_id, summary,
        )
        return {
            "outcome": "completed",
            "summary_counts": {
                "analyses_attempted": 0,
                "analyses_succeeded": 0,
                "analyses_failed": 0,
                "analyses_skipped": len(skipped_records),
            },
            "artifacts": [],
            "metadata": {
                "stage_summary_artifact_id": summary_art_id,
                "stage_status": summary["stage_status"],
                "elapsed_ms": elapsed_ms,
            },
            "error": None,
        }

    # ── 6. Execute model analyses ───────────────────────────────
    analysis_results: dict[str, dict[str, Any]]

    if model_results_override is not None:
        # Test/replay mode: use pre-supplied results
        analysis_results = {}
        for item in work_items:
            key = item["engine_key"]
            if key in model_results_override:
                analysis_results[key] = {
                    "record": build_model_analysis_record(
                        engine_key=key,
                        status="analyzed",
                        source_artifact_ref=item["source_artifact_ref"],
                        started_at=_now_iso(),
                        completed_at=_now_iso(),
                        elapsed_ms=0,
                        downstream_usable=True,
                    ),
                    "model_output": model_results_override[key],
                }
            else:
                skipped_records[key] = build_model_analysis_record(
                    engine_key=key,
                    status="skipped_not_eligible",
                    source_artifact_ref=item["source_artifact_ref"],
                )
        # Also handle override keys for engines not in work_items
        for key, override_result in model_results_override.items():
            if key not in analysis_results and key not in skipped_records:
                analysis_results[key] = {
                    "record": build_model_analysis_record(
                        engine_key=key,
                        status="analyzed",
                        started_at=_now_iso(),
                        completed_at=_now_iso(),
                        elapsed_ms=0,
                        downstream_usable=True,
                    ),
                    "model_output": override_result,
                }
    else:
        analysis_results = _execute_analyses_parallel(
            work_items, model_executor, max_workers, event_emitter,
        )

    # ── 7. Write per-engine model-output artifacts ──────────────
    artifact_ids: list[str] = []
    for key, entry in analysis_results.items():
        rec = entry.get("record", {})
        model_output = entry.get("model_output")

        art_id = _write_model_output_artifact(
            artifact_store, run_id, key, model_output, rec,
        )
        if art_id:
            rec["normalized_output_ref"] = art_id
            artifact_ids.append(art_id)

    # ── 8. Build and write stage summary ────────────────────────
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    summary = build_model_stage_summary(
        analysis_results, skipped_records, elapsed_ms,
    )
    summary_art_id = _write_model_stage_summary_artifact(
        artifact_store, run_id, summary,
    )
    artifact_ids.append(summary_art_id)

    # ── 9. Determine stage outcome ──────────────────────────────
    analyzed_count = summary["analyzed_count"]
    failed_count = summary["failed_count"]
    total_attempted = summary["total_attempted"]

    if total_attempted > 0 and analyzed_count == 0:
        outcome = "failed"
        error = build_run_error(
            code="ALL_ANALYSES_FAILED",
            message=f"All {total_attempted} model analyses failed",
            source=_STAGE_KEY,
            detail={"failed_count": failed_count},
        )
    else:
        outcome = "completed"
        error = None

    # ── 10. Return handler result ───────────────────────────────
    return {
        "outcome": outcome,
        "summary_counts": {
            "analyses_attempted": total_attempted,
            "analyses_succeeded": analyzed_count,
            "analyses_failed": failed_count,
            "analyses_skipped": len(skipped_records),
        },
        "artifacts": [],  # artifacts already written directly
        "metadata": {
            "stage_summary_artifact_id": summary_art_id,
            "model_artifact_ids": {
                k: entry.get("record", {}).get("normalized_output_ref")
                for k, entry in analysis_results.items()
                if entry.get("record", {}).get("normalized_output_ref")
            },
            "stage_status": summary["stage_status"],
            "elapsed_ms": elapsed_ms,
            "analysis_records": {
                k: entry.get("record", {}) for k, entry in analysis_results.items()
            },
            "degraded_reasons": summary.get("degraded_reasons", []),
        },
        "error": error,
    }


# =====================================================================
#  Fallback result builders
# =====================================================================

def _build_no_summary_result(t0: float) -> dict[str, Any]:
    """Build handler result when Step 4 summary artifact is missing."""
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return {
        "outcome": "failed",
        "summary_counts": {
            "analyses_attempted": 0,
            "analyses_succeeded": 0,
            "analyses_failed": 0,
            "analyses_skipped": 0,
        },
        "artifacts": [],
        "metadata": {
            "stage_status": "no_source_summary",
            "elapsed_ms": elapsed_ms,
        },
        "error": build_run_error(
            code="NO_SOURCE_SUMMARY",
            message="Step 4 market_stage_summary artifact not found",
            source=_STAGE_KEY,
        ),
    }


def _build_no_engines_result(
    artifact_store: dict[str, Any],
    run_id: str,
    t0: float,
    *,
    reason: str = "No eligible engines for model analysis",
) -> dict[str, Any]:
    """Build handler result when no engines are available for analysis."""
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    summary = build_model_stage_summary({}, {}, elapsed_ms)
    summary_art_id = _write_model_stage_summary_artifact(
        artifact_store, run_id, summary,
    )
    return {
        "outcome": "completed",
        "summary_counts": {
            "analyses_attempted": 0,
            "analyses_succeeded": 0,
            "analyses_failed": 0,
            "analyses_skipped": 0,
        },
        "artifacts": [],
        "metadata": {
            "stage_summary_artifact_id": summary_art_id,
            "stage_status": "no_eligible_inputs",
            "elapsed_ms": elapsed_ms,
            "reason": reason,
        },
        "error": None,
    }
