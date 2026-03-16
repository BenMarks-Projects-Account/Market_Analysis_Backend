"""Pipeline Final Response Normalization / Candidate Decision Ledger — Step 15.

Consumes per-candidate final recommendation artifacts (Step 14),
normalizes them into a stable downstream-facing response contract,
builds a run-level candidate decision ledger, and persists both
per-candidate response artifacts and ledger/summary artifacts.

Public API
──────────
    final_response_handler(run, artifact_store, stage_key, **kwargs)
        Stage handler compatible with the Step 3 orchestrator.
    normalize_final_response(final_output, run_id)
        Convert a Step 14 normalized recommendation into the downstream
        final response contract.
    build_ledger_row(response)
        Build a compact ledger row from a normalized final response.

Role boundary
─────────────
This module:
- Retrieves per-candidate final recommendation artifacts from Step 14.
- Normalizes them into a stable downstream response contract.
- Preserves policy/model consistency signals and candidate lineage.
- Builds a run-level candidate decision ledger.
- Writes per-candidate normalized response artifacts keyed response_{cid}.
- Writes a candidate_decision_ledger artifact.
- Writes a final_response_summary artifact.
- Emits structured final-response events via event_callback.

This module does NOT:
- Re-run the model or make new decisions.
- Mutate Step 14 artifacts in place.
- Parse raw model output when Step 14 normalized output exists.
- Render HTML/markdown or produce presentational output.
- Build cross-run dashboards or comparative reports.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.pipeline_artifact_store import (
    build_artifact_record,
    get_artifact_by_key,
    list_stage_artifacts,
    put_artifact,
)
from app.services.pipeline_run_contract import (
    build_log_event,
    build_run_error,
)

logger = logging.getLogger("bentrade.pipeline_final_response_stage")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "final_response_normalization"
_FINAL_RESPONSE_VERSION = "1.0"

# ── Upstream stage key ──────────────────────────────────────────
_UPSTREAM_STAGE_KEY = "final_model_decision"


# =====================================================================
#  Response status vocabulary
# =====================================================================

STATUS_READY = "ready"
STATUS_READY_DEGRADED = "ready_degraded"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

VALID_RESPONSE_STATUSES = frozenset({
    STATUS_READY,
    STATUS_READY_DEGRADED,
    STATUS_SKIPPED,
    STATUS_FAILED,
})


# =====================================================================
#  Recommendation bucket mapping
# =====================================================================

_DECISION_TO_BUCKET: dict[str | None, str] = {
    "buy": "buy",
    "sell": "sell",
    "hold": "hold",
    "pass": "pass",
    None: "unknown",
}

_BUCKET_PRIORITY: dict[str, int] = {
    "buy": 1,
    "sell": 2,
    "hold": 3,
    "pass": 4,
    "unknown": 5,
}


# =====================================================================
#  Per-candidate response normalization
# =====================================================================

def normalize_final_response(
    final_output: dict[str, Any],
    run_id: str,
    *,
    source_artifact_ref: str | None = None,
) -> dict[str, Any]:
    """Normalize a Step 14 recommendation into the downstream response contract.

    Parameters
    ----------
    final_output : dict
        The normalized recommendation from Step 14 (data of a
        ``final_model_output`` artifact).
    run_id : str
        Pipeline run identifier.
    source_artifact_ref : str | None
        The artifact_id of the Step 14 artifact (for lineage).

    Returns
    -------
    dict
        Normalized final response following the downstream contract.
    """
    candidate_id = final_output.get("candidate_id")
    symbol = final_output.get("symbol")

    # ── Map Step 14 final_status → response_status ──────────────
    response_status = _map_response_status(final_output)

    # ── Candidate identity ──────────────────────────────────────
    # Step 14 carries forward the compact_candidate_block fields
    # through the recommendation and metadata.
    metadata = final_output.get("metadata", {})
    rec = final_output.get("recommendation", {})

    candidate_identity = {
        "symbol": symbol,
        "scanner_key": metadata.get("scanner_key"),
        "strategy_type": metadata.get("strategy_type"),
        "opportunity_type": metadata.get("opportunity_type"),
        "direction": metadata.get("direction"),
        "rank_position": metadata.get("rank_position"),
        "rank_score": metadata.get("rank_score"),
    }

    # ── Recommendation summary ──────────────────────────────────
    recommendation_summary = {
        "action": rec.get("decision"),
        "conviction": rec.get("conviction"),
        "rationale_summary": rec.get("rationale_summary"),
        "key_supporting_points": rec.get("key_supporting_points", []),
        "key_risks": rec.get("key_risks", []),
        "event_sensitivity": rec.get("event_sensitivity"),
        "portfolio_fit": rec.get("portfolio_fit"),
        "sizing_guidance": rec.get("sizing_guidance"),
    }

    # ── Policy summary ──────────────────────────────────────────
    guardrail = final_output.get("policy_guardrail_echo", {})
    warnings_list = final_output.get("warnings", [])
    consistency_warning = None
    for w in warnings_list:
        if "despite" in str(w):
            consistency_warning = w
            break

    policy_summary = {
        "overall_outcome": guardrail.get("overall_outcome"),
        "blockers": guardrail.get("blockers", []),
        "cautions": guardrail.get("cautions", []),
        "restrictions": guardrail.get("restrictions", []),
        "consistency_warning": consistency_warning,
    }

    # ── Execution summary ───────────────────────────────────────
    model_meta = final_output.get("model_metadata", {})
    execution_summary = {
        "provider": model_meta.get("provider"),
        "model_name": model_meta.get("model_name"),
        "input_mode": model_meta.get("input_mode"),
        "override_used": model_meta.get("override_used", False),
        "latency_ms": model_meta.get("latency_ms"),
    }

    # ── Quality summary ─────────────────────────────────────────
    quality = final_output.get("quality", {})
    quality_summary = {
        "response_quality": quality.get("response_quality"),
        "degraded_reasons": quality.get("degraded_reasons", []),
        "downstream_usable": quality.get("downstream_usable", False),
        "warnings": warnings_list,
    }

    # ── Source refs ──────────────────────────────────────────────
    source_refs = {
        "final_model_artifact_ref": source_artifact_ref,
        "prompt_payload_ref": final_output.get("source_prompt_payload_ref"),
    }

    # ── UI hints ────────────────────────────────────────────────
    action = rec.get("decision")
    bucket = _DECISION_TO_BUCKET.get(action, "unknown")
    ui_hints = {
        "display_title": _build_display_title(symbol, action),
        "display_symbol": symbol or "???",
        "recommendation_bucket": bucket,
        "review_priority": _BUCKET_PRIORITY.get(bucket, 5),
    }

    # ── Ledger metadata ─────────────────────────────────────────
    ledger_metadata = {
        "normalization_timestamp": datetime.now(timezone.utc).isoformat(),
        "response_version": _FINAL_RESPONSE_VERSION,
        "stage_key": _STAGE_KEY,
    }

    return {
        "final_response_version": _FINAL_RESPONSE_VERSION,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "source_final_model_ref": source_artifact_ref,
        "response_status": response_status,
        "candidate_identity": candidate_identity,
        "recommendation_summary": recommendation_summary,
        "policy_summary": policy_summary,
        "execution_summary": execution_summary,
        "quality_summary": quality_summary,
        "source_refs": source_refs,
        "ui_hints": ui_hints,
        "ledger_metadata": ledger_metadata,
    }


def _map_response_status(final_output: dict[str, Any]) -> str:
    """Map a Step 14 final_status to the Step 15 response_status."""
    step14_status = final_output.get("final_status", "")
    quality = final_output.get("quality", {})
    downstream = quality.get("downstream_usable", False)

    if step14_status == "skipped_not_runnable":
        return STATUS_SKIPPED
    if step14_status == "failed":
        return STATUS_FAILED
    if step14_status == "completed_degraded":
        return STATUS_READY_DEGRADED
    if step14_status == "completed" and downstream:
        return STATUS_READY
    if step14_status == "completed":
        return STATUS_READY
    # Unknown status from Step 14 → degraded
    if downstream:
        return STATUS_READY_DEGRADED
    return STATUS_FAILED


def _build_display_title(
    symbol: str | None,
    action: str | None,
) -> str:
    """Build a compact display title for UI hints."""
    sym = symbol or "???"
    act = (action or "unknown").upper()
    return f"{sym} — {act}"


# =====================================================================
#  Ledger row builder
# =====================================================================

def build_ledger_row(response: dict[str, Any]) -> dict[str, Any]:
    """Build a compact ledger row from a normalized final response.

    Ledger rows are designed for audit tables, sorting, and
    filtering — they carry the minimum needed for run-level views.
    """
    rec = response.get("recommendation_summary", {})
    policy = response.get("policy_summary", {})
    exec_sum = response.get("execution_summary", {})
    quality = response.get("quality_summary", {})
    identity = response.get("candidate_identity", {})

    return {
        "candidate_id": response.get("candidate_id"),
        "symbol": identity.get("symbol"),
        "action": rec.get("action"),
        "conviction": rec.get("conviction"),
        "policy_outcome": policy.get("overall_outcome"),
        "response_status": response.get("response_status"),
        "consistency_flag": policy.get("consistency_warning"),
        "provider": exec_sum.get("provider"),
        "model_name": exec_sum.get("model_name"),
        "downstream_usable": quality.get("downstream_usable", False),
        "source_response_ref": response.get("source_final_model_ref"),
        "rank_position": identity.get("rank_position"),
        "scanner_key": identity.get("scanner_key"),
    }


# =====================================================================
#  Run-level candidate decision ledger builder
# =====================================================================

def _build_candidate_decision_ledger(
    *,
    run_id: str,
    ledger_rows: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    """Build the run-level candidate decision ledger."""
    cids_processed = []
    cids_ready = []
    cids_degraded = []
    cids_failed = []
    cids_skipped = []
    counts_by_action: dict[str, int] = {}
    counts_by_policy: dict[str, int] = {}
    counts_by_consistency: dict[str, int] = {}
    provider_usage: dict[str, int] = {}
    model_usage: dict[str, int] = {}

    for resp in responses:
        cid = resp.get("candidate_id")
        status = resp.get("response_status")
        cids_processed.append(cid)

        if status == STATUS_READY:
            cids_ready.append(cid)
        elif status == STATUS_READY_DEGRADED:
            cids_degraded.append(cid)
        elif status == STATUS_SKIPPED:
            cids_skipped.append(cid)
        elif status == STATUS_FAILED:
            cids_failed.append(cid)

        # Action counts
        action = (
            resp.get("recommendation_summary", {}).get("action") or "unknown"
        )
        counts_by_action[action] = counts_by_action.get(action, 0) + 1

        # Policy counts
        policy_outcome = (
            resp.get("policy_summary", {}).get("overall_outcome") or "unknown"
        )
        counts_by_policy[policy_outcome] = (
            counts_by_policy.get(policy_outcome, 0) + 1
        )

        # Consistency warning counts
        cw = resp.get("policy_summary", {}).get("consistency_warning")
        if cw:
            counts_by_consistency[cw] = (
                counts_by_consistency.get(cw, 0) + 1
            )

        # Provider usage
        provider = resp.get("execution_summary", {}).get("provider")
        if provider:
            provider_usage[provider] = provider_usage.get(provider, 0) + 1

        # Model usage
        model_name = resp.get("execution_summary", {}).get("model_name")
        if model_name:
            model_usage[model_name] = model_usage.get(model_name, 0) + 1

    # Determine stage status rollup
    if cids_failed and not cids_ready and not cids_degraded:
        stage_status_rollup = "failed"
    elif cids_failed or cids_degraded:
        stage_status_rollup = "degraded"
    else:
        stage_status_rollup = "success"

    return {
        "ledger_version": _FINAL_RESPONSE_VERSION,
        "run_id": run_id,
        "stage_key": _STAGE_KEY,
        "candidate_ids_processed": cids_processed,
        "candidate_ids_ready": cids_ready,
        "candidate_ids_degraded": cids_degraded,
        "candidate_ids_failed": cids_failed,
        "candidate_ids_skipped": cids_skipped,
        "ledger_rows": ledger_rows,
        "counts_by_action": counts_by_action,
        "counts_by_policy_outcome": counts_by_policy,
        "counts_by_consistency_warning": counts_by_consistency,
        "provider_usage": provider_usage,
        "model_usage": model_usage,
        "stage_status_rollup": stage_status_rollup,
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
#  Stage summary builder
# =====================================================================

def _build_stage_summary(
    *,
    stage_status: str,
    total_candidates_loaded: int,
    total_ready: int,
    total_degraded: int,
    total_skipped: int,
    total_failed: int,
    output_artifact_refs: dict[str, str],
    ledger_artifact_ref: str | None,
    counts_by_action: dict[str, int],
    counts_by_policy_outcome: dict[str, int],
    provider_usage_counts: dict[str, int],
    model_usage_counts: dict[str, int],
    warnings: list[str],
    degraded_reasons: list[str],
    elapsed_ms: int,
) -> dict[str, Any]:
    """Build the final response stage summary dict."""
    return {
        "stage_key": _STAGE_KEY,
        "stage_status": stage_status,
        "total_candidates_loaded": total_candidates_loaded,
        "total_ready": total_ready,
        "total_degraded": total_degraded,
        "total_skipped": total_skipped,
        "total_failed": total_failed,
        "output_artifact_refs": output_artifact_refs,
        "ledger_artifact_ref": ledger_artifact_ref,
        "counts_by_action": counts_by_action,
        "counts_by_policy_outcome": counts_by_policy_outcome,
        "provider_usage_counts": provider_usage_counts,
        "model_usage_counts": model_usage_counts,
        "warnings": warnings,
        "degraded_reasons": degraded_reasons,
        "summary_artifact_ref": None,  # filled after write
        "elapsed_ms": elapsed_ms,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
#  Event emission helper
# =====================================================================

def _make_event_emitter(
    run: dict[str, Any],
    event_callback: Callable[..., None] | None,
) -> Callable[..., None] | None:
    """Build an event emitter closure for final response stage events."""
    if event_callback is None:
        return None

    run_id = run["run_id"]

    def _emit(
        event_type: str,
        level: str = "info",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        merged_meta: dict[str, Any] = {"stage_key": _STAGE_KEY}
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

        counts = run.get("log_event_counts", {})
        counts["total"] = counts.get("total", 0) + 1
        by_level = counts.get("by_level", {})
        by_level[level] = by_level.get(level, 0) + 1

        try:
            event_callback(event)
        except Exception:
            logger.warning(
                "Event callback raised during final response event '%s'",
                event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Upstream artifact retrieval
# =====================================================================

def _retrieve_final_model_summary(
    artifact_store: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Retrieve the final_model_summary from Step 14.

    Returns ``(summary_data, artifact_id)`` or ``(None, None)``.
    """
    art = get_artifact_by_key(
        artifact_store, _UPSTREAM_STAGE_KEY, "final_model_summary",
    )
    if art is None:
        return None, None
    return art.get("data") or {}, art.get("artifact_id")


def _retrieve_final_model_output(
    artifact_store: dict[str, Any],
    candidate_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Retrieve a per-candidate final model output from Step 14.

    Returns ``(output_data, artifact_id)`` or ``(None, None)``.
    """
    art = get_artifact_by_key(
        artifact_store, _UPSTREAM_STAGE_KEY, f"final_{candidate_id}",
    )
    if art is None:
        return None, None
    return art.get("data") or {}, art.get("artifact_id")


# =====================================================================
#  Artifact writers
# =====================================================================

def _write_response_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    candidate_id: str | None,
    response: dict[str, Any],
) -> str:
    """Write one per-candidate final response artifact.  Returns artifact_id."""
    artifact_key = (
        f"response_{candidate_id}" if candidate_id
        else "response_unknown"
    )

    rec_summary = response.get("recommendation_summary", {})
    policy = response.get("policy_summary", {})
    quality = response.get("quality_summary", {})

    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key=artifact_key,
        artifact_type="final_decision_response",
        data=response,
        candidate_id=candidate_id,
        summary={
            "candidate_id": candidate_id,
            "symbol": response.get("candidate_identity", {}).get("symbol"),
            "response_status": response.get("response_status"),
            "action": rec_summary.get("action"),
            "conviction": rec_summary.get("conviction"),
            "policy_outcome": policy.get("overall_outcome"),
            "downstream_usable": quality.get("downstream_usable"),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_ledger_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    ledger: dict[str, Any],
) -> str:
    """Write the candidate_decision_ledger artifact.  Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="candidate_decision_ledger",
        artifact_type="final_response_ledger",
        data=ledger,
        summary={
            "total_processed": len(ledger.get("candidate_ids_processed", [])),
            "total_ready": len(ledger.get("candidate_ids_ready", [])),
            "total_degraded": len(ledger.get("candidate_ids_degraded", [])),
            "total_failed": len(ledger.get("candidate_ids_failed", [])),
            "total_skipped": len(ledger.get("candidate_ids_skipped", [])),
            "stage_status_rollup": ledger.get("stage_status_rollup"),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_summary_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    summary: dict[str, Any],
) -> str:
    """Write the final_response_summary artifact.  Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="final_response_summary",
        artifact_type="final_response_summary",
        data=summary,
        summary={
            "stage_status": summary.get("stage_status"),
            "total_ready": summary.get("total_ready"),
            "total_degraded": summary.get("total_degraded"),
            "total_skipped": summary.get("total_skipped"),
            "total_failed": summary.get("total_failed"),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


# =====================================================================
#  Vacuous completion helper
# =====================================================================

def _vacuous_completion(
    artifact_store: dict[str, Any],
    run_id: str,
    emit: Callable[..., None] | None,
    elapsed_ms: int,
    note: str,
    status: str = "no_candidates_to_process",
) -> dict[str, Any]:
    """Return a vacuous completion when there are no candidates."""
    empty_ledger = _build_candidate_decision_ledger(
        run_id=run_id,
        ledger_rows=[],
        responses=[],
        warnings=[note],
    )
    ledger_art_id = _write_ledger_artifact(
        artifact_store, run_id, empty_ledger,
    )

    summary = _build_stage_summary(
        stage_status=status,
        total_candidates_loaded=0,
        total_ready=0,
        total_degraded=0,
        total_skipped=0,
        total_failed=0,
        output_artifact_refs={},
        ledger_artifact_ref=ledger_art_id,
        counts_by_action={},
        counts_by_policy_outcome={},
        provider_usage_counts={},
        model_usage_counts={},
        warnings=[note],
        degraded_reasons=[],
        elapsed_ms=elapsed_ms,
    )
    summary_art_id = _write_summary_artifact(
        artifact_store, run_id, summary,
    )
    summary["summary_artifact_ref"] = summary_art_id

    if emit:
        emit(
            "final_response_completed",
            message=f"Final response normalization vacuous: {note}",
            metadata={"note": note},
        )

    return {
        "outcome": "completed",
        "summary_counts": _empty_summary_counts(),
        "artifacts": [],
        "metadata": {
            "stage_status": status,
            "note": note,
            "elapsed_ms": elapsed_ms,
        },
        "error": None,
    }


def _empty_summary_counts() -> dict[str, int]:
    return {
        "total_ready": 0,
        "total_degraded": 0,
        "total_skipped": 0,
        "total_failed": 0,
    }


# =====================================================================
#  Stage handler — public entry point
# =====================================================================

def final_response_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Final response normalization / candidate decision ledger handler (Step 15).

    Retrieves per-candidate final recommendation artifacts from
    Step 14, normalizes them into a stable downstream response
    contract, builds a run-level candidate decision ledger, and
    persists per-candidate response artifacts plus ledger/summary
    artifacts.

    Parameters
    ----------
    run : dict
        The pipeline run dict (passed by orchestrator).
    artifact_store : dict
        The artifact store (passed by orchestrator).
    stage_key : str
        Expected to be "final_response_normalization".
    **kwargs
        event_callback : callable | None
            Optional event callback for structured events.

    Returns
    -------
    dict[str, Any]
        Handler result: { outcome, summary_counts, artifacts,
        metadata, error }
    """
    t0 = time.monotonic()
    run_id = run["run_id"]

    # ── 1. Resolve parameters ───────────────────────────────────
    event_callback = kwargs.get("event_callback")
    emit = _make_event_emitter(run, event_callback)

    # ── 2. Emit final_response_started ──────────────────────────
    if emit:
        emit(
            "final_response_started",
            message="Final response normalization stage started",
        )

    # ── 3. Retrieve final model summary (required) ──────────────
    try:
        fm_summary, fm_summary_art_id = _retrieve_final_model_summary(
            artifact_store,
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "Final response stage failed during upstream summary "
            "retrieval: %s", exc, exc_info=True,
        )
        if emit:
            emit(
                "final_response_failed",
                level="error",
                message=(
                    f"Final model summary retrieval failed: {exc}"
                ),
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="FINAL_RESPONSE_UPSTREAM_ERROR",
                message=(
                    f"Failed to retrieve final model summary: {exc}"
                ),
                source=_STAGE_KEY,
            ),
        }

    if fm_summary is None:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("No final_model_summary found")
        if emit:
            emit(
                "final_response_failed",
                level="error",
                message="No final model summary found",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="NO_FINAL_MODEL_SOURCE",
                message="final_model_summary not found",
                source=_STAGE_KEY,
            ),
        }

    # ── 4. Extract candidate IDs from summary ──────────────────
    candidate_ids = fm_summary.get("candidate_ids_processed", [])
    if not candidate_ids:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return _vacuous_completion(
            artifact_store, run_id, emit, elapsed_ms,
            note="Zero candidates in final model summary",
        )

    # ── 5. Retrieve and normalize per-candidate outputs ─────────
    responses: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    output_artifact_refs: dict[str, str] = {}
    warnings: list[str] = []
    degraded_reasons: list[str] = []

    total_ready = 0
    total_degraded = 0
    total_skipped = 0
    total_failed = 0

    counts_by_action: dict[str, int] = {}
    counts_by_policy: dict[str, int] = {}
    provider_usage: dict[str, int] = {}
    model_usage: dict[str, int] = {}

    for cid in candidate_ids:
        output_data, output_art_id = _retrieve_final_model_output(
            artifact_store, cid,
        )

        if output_data is None:
            # Step 14 produced no artifact for this candidate —
            # record as failed normalization
            failed_resp = _build_failed_response(cid, run_id)
            responses.append(failed_resp)
            ledger_rows.append(build_ledger_row(failed_resp))
            total_failed += 1
            counts_by_action["unknown"] = (
                counts_by_action.get("unknown", 0) + 1
            )
            warnings.append(
                f"[{cid}] final model output artifact missing"
            )
            continue

        # Normalize into downstream response contract
        try:
            resp = normalize_final_response(
                output_data, run_id,
                source_artifact_ref=output_art_id,
            )
        except Exception as exc:
            logger.warning(
                "Normalization failed for candidate %s: %s",
                cid, exc, exc_info=True,
            )
            failed_resp = _build_failed_response(
                cid, run_id, reason=str(exc),
            )
            responses.append(failed_resp)
            ledger_rows.append(build_ledger_row(failed_resp))
            total_failed += 1
            counts_by_action["unknown"] = (
                counts_by_action.get("unknown", 0) + 1
            )
            warnings.append(f"[{cid}] normalization error: {exc}")
            continue

        # ── Write per-candidate response artifact ───────────────
        resp_art_id = _write_response_artifact(
            artifact_store, run_id, cid, resp,
        )
        output_artifact_refs[cid] = resp_art_id

        # ── Classify and count ──────────────────────────────────
        status = resp.get("response_status")
        if status == STATUS_READY:
            total_ready += 1
        elif status == STATUS_READY_DEGRADED:
            total_degraded += 1
            deg = resp.get("quality_summary", {}).get("degraded_reasons", [])
            for d in deg:
                degraded_reasons.append(f"[{cid}] {d}")
        elif status == STATUS_SKIPPED:
            total_skipped += 1
        elif status == STATUS_FAILED:
            total_failed += 1

        # Action counts
        action = (
            resp.get("recommendation_summary", {}).get("action") or "unknown"
        )
        counts_by_action[action] = counts_by_action.get(action, 0) + 1

        # Policy outcome counts
        policy_outcome = (
            resp.get("policy_summary", {}).get("overall_outcome") or "unknown"
        )
        counts_by_policy[policy_outcome] = (
            counts_by_policy.get(policy_outcome, 0) + 1
        )

        # Provider/model usage
        exec_sum = resp.get("execution_summary", {})
        prov = exec_sum.get("provider")
        if prov:
            provider_usage[prov] = provider_usage.get(prov, 0) + 1
        model = exec_sum.get("model_name")
        if model:
            model_usage[model] = model_usage.get(model, 0) + 1

        # Collect warnings from response
        resp_warnings = resp.get("quality_summary", {}).get("warnings", [])
        for w in resp_warnings:
            warnings.append(f"[{cid}] {w}")

        responses.append(resp)
        ledger_rows.append(build_ledger_row(resp))

    total_loaded = len(candidate_ids)

    # ── 6. Determine stage status ───────────────────────────────
    # Skipped candidates are not failures
    effective_ready = total_ready + total_degraded
    if total_failed > 0 and effective_ready == 0:
        stage_status = "failed"
    elif total_failed > 0 or total_degraded > 0:
        stage_status = "degraded"
    else:
        stage_status = "success"

    # ── 7. Build and write ledger ───────────────────────────────
    ledger = _build_candidate_decision_ledger(
        run_id=run_id,
        ledger_rows=ledger_rows,
        responses=responses,
        warnings=warnings,
    )
    ledger_art_id = _write_ledger_artifact(
        artifact_store, run_id, ledger,
    )

    # ── 8. Build and write summary ──────────────────────────────
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    summary = _build_stage_summary(
        stage_status=stage_status,
        total_candidates_loaded=total_loaded,
        total_ready=total_ready,
        total_degraded=total_degraded,
        total_skipped=total_skipped,
        total_failed=total_failed,
        output_artifact_refs=output_artifact_refs,
        ledger_artifact_ref=ledger_art_id,
        counts_by_action=counts_by_action,
        counts_by_policy_outcome=counts_by_policy,
        provider_usage_counts=provider_usage,
        model_usage_counts=model_usage,
        warnings=warnings,
        degraded_reasons=degraded_reasons,
        elapsed_ms=elapsed_ms,
    )
    summary_art_id = _write_summary_artifact(
        artifact_store, run_id, summary,
    )
    summary["summary_artifact_ref"] = summary_art_id

    # ── 9. Handle all-failed case ───────────────────────────────
    if stage_status == "failed":
        if emit:
            emit(
                "final_response_failed",
                level="error",
                message=(
                    f"Final response normalization failed: "
                    f"{total_failed}/{total_loaded} normalizations failed"
                ),
                metadata={
                    "total_failed": total_failed,
                    "total_ready": total_ready,
                },
            )
        return {
            "outcome": "failed",
            "summary_counts": {
                "total_ready": total_ready,
                "total_degraded": total_degraded,
                "total_skipped": total_skipped,
                "total_failed": total_failed,
            },
            "artifacts": list(output_artifact_refs.values()),
            "metadata": {
                "stage_status": stage_status,
                "stage_summary": summary,
                "elapsed_ms": elapsed_ms,
            },
            "error": build_run_error(
                code="FINAL_RESPONSE_ALL_FAILED",
                message=(
                    f"All {total_failed} response normalizations failed"
                ),
                source=_STAGE_KEY,
            ),
        }

    # ── 10. Emit success / degraded ─────────────────────────────
    if emit:
        emit(
            "final_response_completed",
            message=(
                f"Final response normalization completed: "
                f"{total_ready}/{total_loaded} ready"
                + (f" ({total_degraded} degraded)" if total_degraded else "")
                + (f" ({total_skipped} skipped)" if total_skipped else "")
            ),
            metadata={
                "total_ready": total_ready,
                "total_degraded": total_degraded,
                "total_skipped": total_skipped,
                "total_failed": total_failed,
                "counts_by_action": counts_by_action,
            },
        )

    return {
        "outcome": "completed",
        "summary_counts": {
            "total_ready": total_ready,
            "total_degraded": total_degraded,
            "total_skipped": total_skipped,
            "total_failed": total_failed,
        },
        "artifacts": list(output_artifact_refs.values()),
        "metadata": {
            "stage_status": stage_status,
            "stage_summary": summary,
            "elapsed_ms": elapsed_ms,
        },
        "error": None,
    }


# =====================================================================
#  Failed response helper
# =====================================================================

def _build_failed_response(
    candidate_id: str,
    run_id: str,
    *,
    reason: str = "upstream_artifact_missing",
) -> dict[str, Any]:
    """Build a failed response entry when upstream artifact is missing."""
    return {
        "final_response_version": _FINAL_RESPONSE_VERSION,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "source_final_model_ref": None,
        "response_status": STATUS_FAILED,
        "candidate_identity": {
            "symbol": None,
            "scanner_key": None,
            "strategy_type": None,
            "opportunity_type": None,
            "direction": None,
            "rank_position": None,
            "rank_score": None,
        },
        "recommendation_summary": {
            "action": None,
            "conviction": None,
            "rationale_summary": None,
            "key_supporting_points": [],
            "key_risks": [],
            "event_sensitivity": None,
            "portfolio_fit": None,
            "sizing_guidance": None,
        },
        "policy_summary": {
            "overall_outcome": None,
            "blockers": [],
            "cautions": [],
            "restrictions": [],
            "consistency_warning": None,
        },
        "execution_summary": {
            "provider": None,
            "model_name": None,
            "input_mode": None,
            "override_used": False,
            "latency_ms": None,
        },
        "quality_summary": {
            "response_quality": None,
            "degraded_reasons": [reason],
            "downstream_usable": False,
            "warnings": [reason],
        },
        "source_refs": {
            "final_model_artifact_ref": None,
            "prompt_payload_ref": None,
        },
        "ui_hints": {
            "display_title": "??? — UNKNOWN",
            "display_symbol": "???",
            "recommendation_bucket": "unknown",
            "review_priority": 5,
        },
        "ledger_metadata": {
            "normalization_timestamp": datetime.now(
                timezone.utc,
            ).isoformat(),
            "response_version": _FINAL_RESPONSE_VERSION,
            "stage_key": _STAGE_KEY,
        },
    }
