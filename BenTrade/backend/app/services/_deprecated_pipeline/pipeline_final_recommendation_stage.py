"""Pipeline Final Recommendation / Model Execution Stage — Step 14.

Consumes per-candidate prompt payload artifacts (Step 13), executes
the final recommendation model through an injectable execution seam,
normalizes raw model output into stable per-candidate final
recommendation artifacts, and produces a stage summary.

Execution model — Sequential Execution Queue
─────────────────────────────────────────────
Candidates are processed **strictly one at a time**, in deterministic
order (input order from Step 13 summary).  For each candidate:

  1. Mark candidate execution as started
  2. Send exactly one prompt payload to the model executor
  3. Wait for the response to complete
  4. Normalize and store the result artifact
  5. Update per-candidate progress tracking
  6. Proceed to the next candidate

No concurrent model calls occur during a pipeline run.  This is
enforced by design (explicit sequential loop), not by accidental
thread-pool configuration.  The executor seam is the single insertion
point for future local / model-machine / Bedrock distribution logic.

Public API
──────────
    final_recommendation_handler(run, artifact_store, stage_key, **kwargs)
        Stage handler compatible with the Step 3 orchestrator.
    normalize_model_response(raw_result, prompt_payload, run_id)
        Convert raw model output into the stable recommendation contract.
    real_model_executor(payload, rendered_text)
        Live model executor — calls LLM via model_router.
    stub_model_executor(payload, rendered_text)
        Deterministic stub executor for testing / fallback (test-only).
    default_model_executor
        Legacy alias for stub_model_executor (backward compat).

Role boundary
─────────────
This module:
- Retrieves per-candidate prompt payloads from Step 13.
- Determines runnable vs skipped payloads via downstream_usable.
- Invokes the final model through a clean injectable executor seam.
- Executes candidates strictly one at a time (sequential queue).
- Normalizes model responses into stable recommendation artifacts.
- Preserves policy guardrail echo from the prompt payload.
- Writes per-candidate final recommendation artifacts keyed final_{cid}.
- Updates per-candidate execution progress during processing.
- Writes a final_model_summary artifact.
- Emits structured events via event_callback.

This module does NOT:
- Re-compress or re-assemble prompt payloads.
- Re-evaluate policy logic or override guardrails.
- Render final user-facing responses or trade cards.
- Perform cross-candidate ranking for presentation.
- Persist to disk/database (future layer).
- Execute multiple candidates concurrently.
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

logger = logging.getLogger("bentrade.pipeline_final_recommendation_stage")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "final_model_decision"
_FINAL_RECOMMENDATION_VERSION = "1.0"


# =====================================================================
#  Final recommendation status vocabulary
# =====================================================================

STATUS_COMPLETED = "completed"
STATUS_COMPLETED_DEGRADED = "completed_degraded"
STATUS_SKIPPED_NOT_RUNNABLE = "skipped_not_runnable"
STATUS_FAILED = "failed"

VALID_FINAL_STATUSES = frozenset({
    STATUS_COMPLETED,
    STATUS_COMPLETED_DEGRADED,
    STATUS_SKIPPED_NOT_RUNNABLE,
    STATUS_FAILED,
})

# ── Default execution configuration ────────────────────────────
_DEFAULT_INPUT_MODE = "structured"


# =====================================================================
#  Model executor type and default stub
# =====================================================================

# Executor signature:
#   (payload: dict, rendered_text: str | None) -> dict
#
# Expected return shape:
#   {
#       "status": "success" | "error",
#       "raw_response": <model output, any JSON-serialisable>,
#       "provider": str,
#       "model_name": str,
#       "latency_ms": int,
#       "metadata": dict,
#   }

ModelExecutor = Callable[[dict, str | None], dict[str, Any]]


# ── System prompt for final recommendation model ────────────────
_FINAL_RECOMMENDATION_SYSTEM_PROMPT = """\
You are BenTrade's final recommendation engine for options trades.
You will receive a structured candidate analysis containing:
- Candidate identity (symbol, strategy, strikes, expiration)
- Policy evaluation outcome (cleared / caution / restricted / blocked)
- Event context (upcoming earnings, dividends, macro events)
- Data quality notes

Analyze the candidate and return ONLY valid JSON (no markdown, no commentary) with exactly these keys:
{
  "decision": "buy" | "hold" | "pass",
  "conviction": <float 0.0 to 1.0>,
  "rationale_summary": "<1-3 sentence summary of your reasoning>",
  "key_supporting_points": ["<point1>", "<point2>", ...],
  "key_risks": ["<risk1>", "<risk2>", ...],
  "market_alignment": "bullish" | "bearish" | "neutral" | "uncertain",
  "portfolio_fit": "strong" | "acceptable" | "marginal" | "poor",
  "event_sensitivity": "high" | "moderate" | "low" | "none",
  "sizing_guidance": "full" | "standard" | "reduced" | "minimal"
}

Rules:
- If policy outcome is "blocked", decision MUST be "pass".
- If policy outcome is "restricted", decision should be "pass" or "hold".
- conviction must honestly reflect certainty; do not inflate.
- key_supporting_points and key_risks should each have 1-5 items.
- Do NOT wrap your response in markdown code fences.
"""


def real_model_executor(
    payload: dict[str, Any],
    rendered_text: str | None,
) -> dict[str, Any]:
    """Live model executor that calls the LLM via model_router.

    Sends the rendered prompt text (from Step 13) as user content and
    parses the JSON response via the json_repair pipeline.

    Input fields used from payload:
        - rendered_prompt_text (via rendered_text param)
        - compact_candidate_block, compact_policy_block (fallback context)
        - candidate_id, symbol (identity)

    Output fields:
        status, raw_response, provider, model_name, latency_ms, metadata
    """
    import json as _json

    from app.services.model_router import get_model_endpoint, model_request
    from common.json_repair import extract_and_repair_json

    candidate_id = payload.get("candidate_id", "unknown")
    symbol = payload.get("symbol", "unknown")

    # Build user message from rendered text or fallback to structured JSON
    if rendered_text:
        user_content = rendered_text
    else:
        # Fallback: serialize the structured blocks
        fallback_data = {
            "candidate_id": candidate_id,
            "symbol": symbol,
            "candidate": payload.get("compact_candidate_block", {}),
            "policy": payload.get("compact_policy_block", {}),
            "events": payload.get("compact_event_block", {}),
            "quality": payload.get("compact_quality_block", {}),
        }
        user_content = _json.dumps(fallback_data, ensure_ascii=False)

    messages_payload = {
        "messages": [
            {"role": "system", "content": _FINAL_RECOMMENDATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 1200,
        "temperature": 0.0,
    }

    t0 = time.monotonic()
    try:
        # Resolve provider info before the call for metadata
        endpoint = get_model_endpoint()
        from app.services.model_state import get_model_source
        source_key = get_model_source()

        raw_api_response = model_request(
            messages_payload, timeout=120, retries=1,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "[real_model_executor] Model call failed for %s (%s) "
            "after %dms: %s",
            symbol, candidate_id, latency_ms, exc,
        )
        return {
            "status": "error",
            "raw_response": {},
            "provider": "model_router",
            "model_name": "unavailable",
            "latency_ms": latency_ms,
            "error": str(exc),
            "metadata": {"source_key": "unknown"},
        }

    # Extract assistant content from OpenAI-compatible response
    assistant_text = ""
    choices = raw_api_response.get("choices", [])
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message", {})
        if isinstance(message, dict):
            assistant_text = message.get("content", "")

    if not assistant_text:
        logger.warning(
            "[real_model_executor] Empty assistant content for %s (%s)",
            symbol, candidate_id,
        )
        return {
            "status": "error",
            "raw_response": {},
            "provider": source_key,
            "model_name": raw_api_response.get("model", "unknown"),
            "latency_ms": latency_ms,
            "error": "empty_assistant_content",
            "metadata": {"raw_keys": list(raw_api_response.keys())},
        }

    # Strip <think> tags if present (some models emit reasoning traces)
    from common.model_sanitize import had_think_tags
    if had_think_tags(assistant_text):
        logger.info(
            "[real_model_executor] Stripping <think> tags for %s", symbol,
        )
        import re as _re
        assistant_text = _re.sub(
            r"<think>.*?</think>", "", assistant_text, flags=_re.DOTALL,
        ).strip()

    # Parse JSON from model output
    parsed, method = extract_and_repair_json(assistant_text)

    if parsed is None or not isinstance(parsed, dict):
        logger.warning(
            "[real_model_executor] JSON parse failed for %s (%s), "
            "method=%s, text[:200]=%s",
            symbol, candidate_id, method, assistant_text[:200],
        )
        return {
            "status": "error",
            "raw_response": {"raw_text": assistant_text[:500]},
            "provider": source_key,
            "model_name": raw_api_response.get("model", "unknown"),
            "latency_ms": latency_ms,
            "error": "json_parse_failure",
            "metadata": {"parse_method": method},
        }

    logger.info(
        "[real_model_executor] Success for %s (%s) — decision=%s, "
        "conviction=%s, parsed_via=%s, latency=%dms",
        symbol, candidate_id,
        parsed.get("decision"), parsed.get("conviction"),
        method, latency_ms,
    )

    return {
        "status": "success",
        "raw_response": parsed,
        "provider": source_key,
        "model_name": raw_api_response.get("model", "unknown"),
        "latency_ms": latency_ms,
        "metadata": {
            "parse_method": method,
            "finish_reason": (
                choices[0].get("finish_reason")
                if choices else None
            ),
        },
    }


def stub_model_executor(
    payload: dict[str, Any],
    rendered_text: str | None,
) -> dict[str, Any]:
    """Deterministic stub executor for testing and fallback.

    Returns a stub recommendation derived from the policy outcome
    without calling any model backend.
    """
    candidate_id = payload.get("candidate_id")
    symbol = payload.get("symbol")
    policy_block = payload.get("compact_policy_block", {})
    overall_outcome = policy_block.get("overall_outcome", "unknown")

    # Derive stub decision from policy outcome
    if overall_outcome == "blocked":
        decision = "pass"
        conviction = 0.0
    elif overall_outcome == "restricted":
        decision = "pass"
        conviction = 0.1
    elif overall_outcome == "caution":
        decision = "hold"
        conviction = 0.4
    else:
        decision = "buy"
        conviction = 0.7

    return {
        "status": "success",
        "raw_response": {
            "decision": decision,
            "conviction": conviction,
            "rationale_summary": (
                f"Stub recommendation for {symbol} ({candidate_id})"
            ),
            "key_supporting_points": [
                f"Policy outcome: {overall_outcome}",
            ],
            "key_risks": [],
            "market_alignment": "neutral",
            "portfolio_fit": "acceptable",
            "event_sensitivity": "low",
            "sizing_guidance": "standard",
        },
        "provider": "stub",
        "model_name": "stub_model_executor",
        "latency_ms": 0,
        "metadata": {"stub": True},
    }


# Keep legacy alias for backward compatibility
default_model_executor = stub_model_executor


# =====================================================================
#  Response normalization
# =====================================================================

def normalize_model_response(
    raw_result: dict[str, Any],
    prompt_payload: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    """Normalize raw model output into the stable recommendation contract.

    Handles missing or partial fields honestly — preserves
    degraded/unknown state when parsing is incomplete.

    Parameters
    ----------
    raw_result : dict
        Raw execution result from the model executor.
    prompt_payload : dict
        The Step 13 prompt payload that was used as model input.
    run_id : str
        Pipeline run identifier.

    Returns
    -------
    dict
        Normalized recommendation following the final recommendation
        contract.
    """
    candidate_id = prompt_payload.get("candidate_id")
    symbol = prompt_payload.get("symbol")

    raw_status = raw_result.get("status", "error")
    raw_body = raw_result.get("raw_response", {})
    if not isinstance(raw_body, dict):
        raw_body = {}

    # ── Determine execution & final status ──────────────────────
    if raw_status == "success":
        model_execution_status = "success"
        degraded_reasons: list[str] = []
        # Check if response body is suspiciously empty
        if not raw_body.get("decision"):
            model_execution_status = "success_partial"
            degraded_reasons.append("missing_decision_in_response")
    else:
        model_execution_status = "error"
        degraded_reasons = [raw_result.get("error", "model_execution_error")]

    # ── Build recommendation block ──────────────────────────────
    recommendation = {
        "decision": raw_body.get("decision"),
        "conviction": raw_body.get("conviction"),
        "rationale_summary": raw_body.get("rationale_summary"),
        "key_supporting_points": raw_body.get(
            "key_supporting_points", [],
        ),
        "key_risks": raw_body.get("key_risks", []),
        "market_alignment": raw_body.get("market_alignment"),
        "portfolio_fit": raw_body.get("portfolio_fit"),
        "event_sensitivity": raw_body.get("event_sensitivity"),
        "sizing_guidance": raw_body.get("sizing_guidance"),
    }

    # ── Policy guardrail echo ───────────────────────────────────
    policy_block = prompt_payload.get("compact_policy_block", {})
    policy_guardrail_echo = {
        "overall_outcome": policy_block.get("overall_outcome"),
        "blockers": policy_block.get("blocking_reasons", []),
        "cautions": policy_block.get("caution_reasons", []),
        "restrictions": policy_block.get("restriction_reasons", []),
    }

    # ── Quality assessment ──────────────────────────────────────
    quality_block = prompt_payload.get("compact_quality_block", {})
    response_quality = "full" if model_execution_status == "success" else "degraded"
    all_degraded = list(degraded_reasons)
    payload_degraded = prompt_payload.get("degraded_reasons", [])
    if payload_degraded:
        all_degraded.extend(payload_degraded)

    downstream_usable = (
        model_execution_status in ("success", "success_partial")
    )

    quality = {
        "response_quality": response_quality,
        "degraded_reasons": all_degraded,
        "downstream_usable": downstream_usable,
    }

    # ── Model metadata ──────────────────────────────────────────
    model_metadata = {
        "provider": raw_result.get("provider"),
        "model_name": raw_result.get("model_name"),
        "latency_ms": raw_result.get("latency_ms"),
        "override_used": raw_result.get("override_used", False),
        "routing_metadata": raw_result.get("routing_metadata"),
        "input_mode": raw_result.get("input_mode", _DEFAULT_INPUT_MODE),
    }

    # ── Determine final status ──────────────────────────────────
    if model_execution_status == "error":
        final_status = STATUS_FAILED
    elif all_degraded:
        final_status = STATUS_COMPLETED_DEGRADED
    else:
        final_status = STATUS_COMPLETED

    # ── Warnings ────────────────────────────────────────────────
    warnings: list[str] = []
    # Check guardrail consistency
    outcome = policy_block.get("overall_outcome")
    decision = recommendation.get("decision")
    if outcome == "blocked" and decision not in (None, "pass"):
        warnings.append(
            f"model_recommends_{decision}_despite_blocked_policy"
        )
    if outcome == "restricted" and decision not in (None, "pass", "hold"):
        warnings.append(
            f"model_recommends_{decision}_despite_restricted_policy"
        )

    return {
        "final_recommendation_version": _FINAL_RECOMMENDATION_VERSION,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "symbol": symbol,
        "source_prompt_payload_ref": prompt_payload.get(
            "source_decision_packet_ref",
        ),
        "final_status": final_status,
        "model_execution_status": model_execution_status,
        "recommendation": recommendation,
        "policy_guardrail_echo": policy_guardrail_echo,
        "quality": quality,
        "model_metadata": model_metadata,
        "raw_response_excerpt": _build_raw_excerpt(raw_body),
        "warnings": warnings,
        "notes": [],
        "metadata": {
            "normalization_timestamp": datetime.now(
                timezone.utc,
            ).isoformat(),
            "recommendation_version": _FINAL_RECOMMENDATION_VERSION,
            "stage_key": _STAGE_KEY,
            "policy_outcome": outcome,
            "downstream_usable": downstream_usable,
        },
    }


def _build_raw_excerpt(raw_body: dict[str, Any]) -> dict[str, Any]:
    """Build a compact excerpt of the raw model response.

    Keeps only the top-level keys and their types, plus the decision
    and conviction if present.  Full raw output is in the raw_response
    field of the executor result.
    """
    excerpt: dict[str, Any] = {
        "keys_present": sorted(raw_body.keys()) if raw_body else [],
    }
    if "decision" in raw_body:
        excerpt["decision"] = raw_body["decision"]
    if "conviction" in raw_body:
        excerpt["conviction"] = raw_body["conviction"]
    return excerpt


# =====================================================================
#  Per-candidate execution record builder
# =====================================================================

def _build_execution_record(
    *,
    candidate_id: str | None,
    symbol: str | None,
    payload_status: str | None,
    execution_status: str,
    source_prompt_payload_ref: str | None,
    provider: str | None,
    model_name: str | None,
    input_mode_used: str,
    override_used: bool,
    output_artifact_ref: str | None,
    downstream_usable: bool,
    degraded_reasons: list[str],
    elapsed_ms: int,
    error_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a per-candidate execution record for the stage summary."""
    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "payload_status": payload_status,
        "execution_status": execution_status,
        "source_prompt_payload_ref": source_prompt_payload_ref,
        "provider": provider,
        "model_name": model_name,
        "input_mode_used": input_mode_used,
        "override_used": override_used,
        "output_artifact_ref": output_artifact_ref,
        "downstream_usable": downstream_usable,
        "degraded_reasons": degraded_reasons,
        "elapsed_ms": elapsed_ms,
        "error": error_info,
    }


# =====================================================================
#  Stage summary builder
# =====================================================================

def _build_stage_summary(
    *,
    stage_status: str,
    total_candidates_loaded: int,
    total_runnable: int,
    total_completed: int,
    total_degraded: int,
    total_skipped: int,
    total_failed: int,
    execution_records: list[dict[str, Any]],
    output_artifact_refs: dict[str, str],
    provider_usage_counts: dict[str, int],
    override_usage_counts: dict[str, int],
    warnings: list[str],
    elapsed_ms: int,
) -> dict[str, Any]:
    """Build the final model execution stage summary dict."""
    return {
        "stage_key": _STAGE_KEY,
        "stage_status": stage_status,
        "total_candidates_loaded": total_candidates_loaded,
        "total_runnable": total_runnable,
        "total_completed": total_completed,
        "total_degraded": total_degraded,
        "total_skipped": total_skipped,
        "total_failed": total_failed,
        "candidate_ids_processed": [
            r.get("candidate_id") for r in execution_records
        ],
        "output_artifact_refs": output_artifact_refs,
        "provider_usage_counts": provider_usage_counts,
        "override_usage_counts": override_usage_counts,
        "execution_records": execution_records,
        "warnings": warnings,
        "summary_artifact_ref": None,  # filled after write
        "elapsed_ms": elapsed_ms,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
#  Execution queue progress tracker
# =====================================================================

# ProgressCallback signature:
#   (progress: dict[str, Any]) -> None
#
# Called after each candidate completes.  The dict contains:
#   current_candidate_id, current_candidate_symbol,
#   completed_count, remaining_count, total_runnable,
#   queue_position (1-based index of this candidate),
#   candidate_status ("completed" | "failed" | "degraded"),
#   elapsed_ms (for this candidate).
ProgressCallback = Callable[[dict[str, Any]], None]


def _build_candidate_progress(
    *,
    queue_position: int,
    candidate_id: str,
    symbol: str | None,
    candidate_status: str,
    completed_count: int,
    remaining_count: int,
    total_runnable: int,
    elapsed_ms: int,
) -> dict[str, Any]:
    """Build a per-candidate progress snapshot for live tracking."""
    return {
        "queue_position": queue_position,
        "current_candidate_id": candidate_id,
        "current_candidate_symbol": symbol,
        "candidate_status": candidate_status,
        "completed_count": completed_count,
        "remaining_count": remaining_count,
        "total_runnable": total_runnable,
        "elapsed_ms": elapsed_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
#  Event emission helper
# =====================================================================

def _make_event_emitter(
    run: dict[str, Any],
    event_callback: Callable[..., None] | None,
) -> Callable[..., None] | None:
    """Build an event emitter closure for final model stage events."""
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
                "Event callback raised during final model event '%s'",
                event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Upstream artifact retrieval
# =====================================================================

def _retrieve_prompt_payload_summary(
    artifact_store: dict[str, Any],
) -> dict[str, Any] | None:
    """Retrieve prompt_payload_summary from Step 13."""
    art = get_artifact_by_key(
        artifact_store, "prompt_payload", "prompt_payload_summary",
    )
    if art is None:
        return None
    return art.get("data") or {}


def _retrieve_prompt_payload(
    artifact_store: dict[str, Any],
    candidate_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Retrieve a per-candidate prompt payload from Step 13.

    Returns ``(payload_data, artifact_id)`` or ``(None, None)``.
    """
    art = get_artifact_by_key(
        artifact_store, "prompt_payload", f"prompt_{candidate_id}",
    )
    if art is None:
        return None, None
    return art.get("data") or {}, art.get("artifact_id")


# =====================================================================
#  Artifact writers
# =====================================================================

def _write_recommendation_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    candidate_id: str | None,
    recommendation: dict[str, Any],
) -> str:
    """Write one final recommendation artifact.  Returns artifact_id."""
    artifact_key = (
        f"final_{candidate_id}" if candidate_id
        else "final_unknown"
    )

    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key=artifact_key,
        artifact_type="final_model_output",
        data=recommendation,
        candidate_id=candidate_id,
        summary={
            "candidate_id": candidate_id,
            "symbol": recommendation.get("symbol"),
            "final_status": recommendation.get("final_status"),
            "decision": (
                recommendation.get("recommendation", {}).get("decision")
            ),
            "conviction": (
                recommendation.get("recommendation", {}).get("conviction")
            ),
            "policy_outcome": (
                recommendation
                .get("policy_guardrail_echo", {})
                .get("overall_outcome")
            ),
            "downstream_usable": (
                recommendation.get("quality", {}).get("downstream_usable")
            ),
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
    """Write the final_model_summary artifact.  Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="final_model_summary",
        artifact_type="final_model_summary",
        data=summary,
        summary={
            "stage_status": summary.get("stage_status"),
            "total_completed": summary.get("total_completed"),
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
    summary = _build_stage_summary(
        stage_status=status,
        total_candidates_loaded=0,
        total_runnable=0,
        total_completed=0,
        total_degraded=0,
        total_skipped=0,
        total_failed=0,
        execution_records=[],
        output_artifact_refs={},
        provider_usage_counts={},
        override_usage_counts={},
        warnings=[note],
        elapsed_ms=elapsed_ms,
    )
    summary_art_id = _write_summary_artifact(artifact_store, run_id, summary)
    summary["summary_artifact_ref"] = summary_art_id

    if emit:
        emit(
            "final_model_completed",
            message=f"Final model execution vacuous: {note}",
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
        "total_completed": 0,
        "total_degraded": 0,
        "total_skipped": 0,
        "total_failed": 0,
    }


# =====================================================================
#  Single-candidate execution
# =====================================================================

def _execute_single_candidate(
    *,
    candidate_id: str,
    payload_data: dict[str, Any],
    payload_art_id: str | None,
    run_id: str,
    executor: ModelExecutor,
    input_mode: str,
    override_used: bool,
) -> dict[str, Any]:
    """Execute the model for a single candidate and normalize.

    Returns a dict with:
        normalized: normalized recommendation dict
        execution_record: per-candidate execution record
        raw_result: raw executor result
    """
    symbol = payload_data.get("symbol")
    payload_status = payload_data.get("payload_status")
    t0 = time.monotonic()

    # ── Prepare input based on mode ─────────────────────────────
    rendered_text = payload_data.get("rendered_prompt_text")
    if input_mode == "text":
        exec_payload = payload_data
    else:
        exec_payload = payload_data

    # ── Execute model ───────────────────────────────────────────
    try:
        raw_result = executor(exec_payload, rendered_text)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return {
            "normalized": None,
            "execution_record": _build_execution_record(
                candidate_id=candidate_id,
                symbol=symbol,
                payload_status=payload_status,
                execution_status=STATUS_FAILED,
                source_prompt_payload_ref=payload_art_id,
                provider=None,
                model_name=None,
                input_mode_used=input_mode,
                override_used=override_used,
                output_artifact_ref=None,
                downstream_usable=False,
                degraded_reasons=[str(exc)],
                elapsed_ms=elapsed_ms,
                error_info={
                    "code": "MODEL_EXECUTION_ERROR",
                    "message": str(exc),
                },
            ),
            "raw_result": None,
        }

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # ── Record actual input mode used ───────────────────────────
    if not isinstance(raw_result, dict):
        raw_result = {
            "status": "error",
            "raw_response": {},
            "provider": "unknown",
            "model_name": "unknown",
            "latency_ms": elapsed_ms,
            "metadata": {"error": "executor returned non-dict"},
        }

    raw_result["input_mode"] = input_mode
    raw_result["override_used"] = override_used

    # ── Normalize ───────────────────────────────────────────────
    normalized = normalize_model_response(raw_result, payload_data, run_id)

    provider = raw_result.get("provider")
    model_name = raw_result.get("model_name")

    return {
        "normalized": normalized,
        "execution_record": _build_execution_record(
            candidate_id=candidate_id,
            symbol=symbol,
            payload_status=payload_status,
            execution_status=normalized["final_status"],
            source_prompt_payload_ref=payload_art_id,
            provider=provider,
            model_name=model_name,
            input_mode_used=input_mode,
            override_used=override_used,
            output_artifact_ref=None,  # filled after artifact write
            downstream_usable=normalized["quality"]["downstream_usable"],
            degraded_reasons=normalized["quality"]["degraded_reasons"],
            elapsed_ms=elapsed_ms,
        ),
        "raw_result": raw_result,
    }


# =====================================================================
#  Stage handler — public entry point
# =====================================================================

def final_recommendation_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Final recommendation / model execution stage handler (Step 14).

    Executes the final model for each candidate through a **sequential
    execution queue** — strictly one candidate at a time, in
    deterministic order.

    Execution contract
    ──────────────────
    1. Load runnable payloads from Step 13.
    2. For each runnable payload, in order:
       a. Emit ``candidate_execution_started`` event.
       b. Call the model executor (one active call at a time).
       c. Wait for the response to complete.
       d. Normalize and write the result artifact.
       e. Update per-candidate progress tracking.
       f. Emit ``candidate_execution_completed`` event.
       g. Invoke ``progress_callback`` with live progress snapshot.
    3. After all candidates: write stage summary, emit completion.

    The executor seam (``model_executor`` kwarg) is the single
    insertion point for future distributed routing (local machine,
    model machine, Bedrock, etc.).

    Parameters
    ----------
    run : dict
        The pipeline run dict (passed by orchestrator).
    artifact_store : dict
        The artifact store (passed by orchestrator).
    stage_key : str
        Expected to be "final_model_decision".
    **kwargs
        event_callback : callable | None
            Optional event callback for structured events.
        model_executor : ModelExecutor | None
            Injectable model execution function.  Defaults to
            ``real_model_executor`` (live LLM calls).
        input_mode : str
            "structured" or "text" — how to pass to executor.
        override_used : bool
            Whether an override routing mode is active.
        progress_callback : ProgressCallback | None
            Called after each candidate finishes with a progress
            snapshot dict for live UI updates.

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
    executor: ModelExecutor = kwargs.get(
        "model_executor", real_model_executor,
    )
    input_mode: str = kwargs.get("input_mode", _DEFAULT_INPUT_MODE)
    override_used: bool = kwargs.get("override_used", False)
    progress_callback: ProgressCallback | None = kwargs.get(
        "progress_callback",
    )

    # ── 2. Emit final_model_started ─────────────────────────────
    if emit:
        emit(
            "final_model_started",
            message="Final model execution stage started",
        )

    # ── 3. Retrieve prompt payload summary (required) ───────────
    try:
        pp_summary = _retrieve_prompt_payload_summary(artifact_store)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "Final model stage failed during prompt payload "
            "summary retrieval: %s", exc, exc_info=True,
        )
        if emit:
            emit(
                "final_model_failed",
                level="error",
                message=f"Prompt payload summary retrieval failed: {exc}",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="FINAL_MODEL_UPSTREAM_ERROR",
                message=(
                    f"Failed to retrieve prompt payload summary: {exc}"
                ),
                source=_STAGE_KEY,
            ),
        }

    if pp_summary is None:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("No prompt_payload_summary found")
        if emit:
            emit(
                "final_model_failed",
                level="error",
                message="No prompt payload summary found",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="NO_PROMPT_PAYLOAD_SOURCE",
                message="prompt_payload_summary not found",
                source=_STAGE_KEY,
            ),
        }

    # ── 4. Extract candidate IDs ────────────────────────────────
    payload_records_upstream = pp_summary.get("payload_records", [])
    candidate_ids = [
        r.get("candidate_id") for r in payload_records_upstream
        if r.get("candidate_id")
    ]

    if not candidate_ids:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return _vacuous_completion(
            artifact_store, run_id, emit, elapsed_ms,
            note="Zero candidates in prompt payload summary",
        )

    # ── 5. Retrieve payloads and classify runnable vs skipped ───
    payloads: list[tuple[str, dict[str, Any] | None, str | None]] = []
    for cid in candidate_ids:
        pdata, part_id = _retrieve_prompt_payload(artifact_store, cid)
        payloads.append((cid, pdata, part_id))

    runnable: list[tuple[str, dict[str, Any], str | None]] = []
    skipped_records: list[dict[str, Any]] = []

    for cid, pdata, part_id in payloads:
        if pdata is None:
            skipped_records.append(_build_execution_record(
                candidate_id=cid,
                symbol=None,
                payload_status=None,
                execution_status=STATUS_FAILED,
                source_prompt_payload_ref=None,
                provider=None,
                model_name=None,
                input_mode_used=input_mode,
                override_used=override_used,
                output_artifact_ref=None,
                downstream_usable=False,
                degraded_reasons=["prompt payload missing"],
                elapsed_ms=0,
                error_info={
                    "code": "PROMPT_PAYLOAD_MISSING",
                    "message": f"No prompt payload for {cid}",
                },
            ))
            continue

        if not pdata.get("downstream_usable", False):
            skipped_records.append(_build_execution_record(
                candidate_id=cid,
                symbol=pdata.get("symbol"),
                payload_status=pdata.get("payload_status"),
                execution_status=STATUS_SKIPPED_NOT_RUNNABLE,
                source_prompt_payload_ref=part_id,
                provider=None,
                model_name=None,
                input_mode_used=input_mode,
                override_used=override_used,
                output_artifact_ref=None,
                downstream_usable=False,
                degraded_reasons=["downstream_usable=false"],
                elapsed_ms=0,
            ))
            continue

        runnable.append((cid, pdata, part_id))

    total_loaded = len(candidate_ids)
    total_skipped = len(skipped_records)

    if not runnable and total_skipped > 0:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return _vacuous_completion(
            artifact_store, run_id, emit, elapsed_ms,
            note=f"No runnable payloads ({total_skipped} skipped)",
            status="no_runnable_candidates",
        )

    if not runnable:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return _vacuous_completion(
            artifact_store, run_id, emit, elapsed_ms,
            note="Zero runnable payloads",
        )

    # ── 6. Sequential execution queue ─────────────────────────────
    # Candidates are executed strictly one at a time, in the order
    # they appear in the Step 13 summary.  The next model call does
    # not begin until the previous one fully completes.
    #
    # This loop is the single insertion point for future distributed
    # routing (local / model-machine / Bedrock).  To add routing,
    # replace or wrap the `executor` callable — do not add
    # concurrency here.

    execution_records: list[dict[str, Any]] = []
    output_artifact_refs: dict[str, str] = {}
    provider_usage_counts: dict[str, int] = {}
    override_usage_counts: dict[str, int] = {}
    warnings: list[str] = []
    total_completed = 0
    total_degraded = 0
    total_failed = 0

    # Track which candidates have been submitted to prevent
    # accidental re-submission in the same pass.
    _submitted: set[str] = set()

    for queue_position, (cid, pdata, part_id) in enumerate(
        runnable, start=1,
    ):
        # ── Guard: prevent duplicate execution ──────────────────
        if cid in _submitted:
            logger.error(
                "Duplicate candidate_id '%s' in runnable queue; "
                "skipping to prevent double execution", cid,
            )
            continue
        _submitted.add(cid)

        symbol = pdata.get("symbol")

        # ── 6a. Emit candidate_execution_started ────────────────
        if emit:
            emit(
                "candidate_execution_started",
                message=(
                    f"Executing candidate {queue_position}/"
                    f"{len(runnable)}: {symbol} ({cid})"
                ),
                metadata={
                    "candidate_id": cid,
                    "symbol": symbol,
                    "queue_position": queue_position,
                    "total_runnable": len(runnable),
                },
            )

        # ── 6b. Execute model (one active call at a time) ───────
        candidate_t0 = time.monotonic()
        result = _execute_single_candidate(
            candidate_id=cid,
            payload_data=pdata,
            payload_art_id=part_id,
            run_id=run_id,
            executor=executor,
            input_mode=input_mode,
            override_used=override_used,
        )
        candidate_elapsed_ms = int(
            (time.monotonic() - candidate_t0) * 1000
        )

        # ── 6c. Normalize and persist artifact immediately ──────
        rec = result["execution_record"]
        normalized = result.get("normalized")
        candidate_status = "failed"

        if normalized is not None:
            art_id = _write_recommendation_artifact(
                artifact_store, run_id, cid, normalized,
            )
            rec["output_artifact_ref"] = art_id
            output_artifact_refs[cid] = art_id

            final_status = normalized.get("final_status")
            if final_status == STATUS_COMPLETED:
                total_completed += 1
                candidate_status = "completed"
            elif final_status == STATUS_COMPLETED_DEGRADED:
                total_completed += 1
                total_degraded += 1
                candidate_status = "degraded"
            elif final_status == STATUS_FAILED:
                total_failed += 1
                candidate_status = "failed"

            provider = rec.get("provider")
            if provider:
                provider_usage_counts[provider] = (
                    provider_usage_counts.get(provider, 0) + 1
                )
            if rec.get("override_used"):
                override_usage_counts["override"] = (
                    override_usage_counts.get("override", 0) + 1
                )

            norm_warnings = normalized.get("warnings", [])
            for w in norm_warnings:
                warnings.append(f"[{cid}] {w}")
        else:
            total_failed += 1

        execution_records.append(rec)
        completed_so_far = total_completed + total_failed

        # ── 6d. Emit candidate_execution_completed ──────────────
        if emit:
            emit(
                "candidate_execution_completed",
                message=(
                    f"Candidate {queue_position}/{len(runnable)} "
                    f"{candidate_status}: {symbol} ({cid}) "
                    f"in {candidate_elapsed_ms}ms"
                ),
                metadata={
                    "candidate_id": cid,
                    "symbol": symbol,
                    "queue_position": queue_position,
                    "candidate_status": candidate_status,
                    "completed_count": completed_so_far,
                    "remaining_count": len(runnable) - queue_position,
                    "elapsed_ms": candidate_elapsed_ms,
                },
            )

        # ── 6e. Invoke progress callback for live UI updates ────
        if progress_callback is not None:
            progress = _build_candidate_progress(
                queue_position=queue_position,
                candidate_id=cid,
                symbol=symbol,
                candidate_status=candidate_status,
                completed_count=completed_so_far,
                remaining_count=len(runnable) - queue_position,
                total_runnable=len(runnable),
                elapsed_ms=candidate_elapsed_ms,
            )
            try:
                progress_callback(progress)
            except Exception:
                logger.warning(
                    "progress_callback raised for candidate %s",
                    cid, exc_info=True,
                )

    # Add skipped records to execution_records
    execution_records.extend(skipped_records)

    # ── 7. Compute stage status ─────────────────────────────────
    if total_failed > 0 and total_completed == 0:
        stage_status = "failed"
    elif total_failed > 0 or total_degraded > 0:
        stage_status = "degraded"
    else:
        stage_status = "success"

    # ── 8. Build and write stage summary ────────────────────────
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    summary = _build_stage_summary(
        stage_status=stage_status,
        total_candidates_loaded=total_loaded,
        total_runnable=len(runnable),
        total_completed=total_completed,
        total_degraded=total_degraded,
        total_skipped=total_skipped,
        total_failed=total_failed,
        execution_records=execution_records,
        output_artifact_refs=output_artifact_refs,
        provider_usage_counts=provider_usage_counts,
        override_usage_counts=override_usage_counts,
        warnings=warnings,
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
                "final_model_failed",
                level="error",
                message=(
                    f"Final model execution failed: "
                    f"{total_failed}/{len(runnable)} executions failed"
                ),
                metadata={
                    "total_failed": total_failed,
                    "total_completed": total_completed,
                },
            )
        return {
            "outcome": "failed",
            "summary_counts": {
                "total_completed": total_completed,
                "total_degraded": total_degraded,
                "total_skipped": total_skipped,
                "total_failed": total_failed,
            },
            "artifacts": [],
            "metadata": {
                "stage_status": stage_status,
                "stage_summary": summary,
                "elapsed_ms": elapsed_ms,
            },
            "error": build_run_error(
                code="FINAL_MODEL_ALL_FAILED",
                message=(
                    f"All {total_failed} runnable executions failed"
                ),
                source=_STAGE_KEY,
            ),
        }

    # ── 10. Emit success / degraded ─────────────────────────────
    if emit:
        emit(
            "final_model_completed",
            message=(
                f"Final model execution completed: "
                f"{total_completed}/{len(runnable)} completed"
                + (f" ({total_degraded} degraded)" if total_degraded else "")
                + (f" ({total_skipped} skipped)" if total_skipped else "")
            ),
            metadata={
                "total_completed": total_completed,
                "total_degraded": total_degraded,
                "total_skipped": total_skipped,
                "total_failed": total_failed,
                "provider_usage_counts": provider_usage_counts,
            },
        )

    return {
        "outcome": "completed",
        "summary_counts": {
            "total_completed": total_completed,
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
