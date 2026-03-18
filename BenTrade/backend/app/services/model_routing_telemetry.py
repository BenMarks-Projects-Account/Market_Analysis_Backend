"""Routing telemetry — structured event helpers for observability.

Emits structured log events at each routing decision point so that
routing behaviour is inspectable without changing routing semantics.

Safety:
    • No prompt content / payloads are logged by default.
    • Only identifiers, modes, provider IDs, timing, and skip reasons.
    • Raw prompt text is replaced with metadata summaries (message count,
      estimated token length) so logs stay safe for shared systems.

Logger hierarchy:
    bentrade.routing.telemetry   — top-level telemetry events
    bentrade.routing.gate        — gate acquire/release events
    bentrade.routing.decision    — per-provider decision events

All events use ``logger.info`` for normal flow and ``logger.warning``
for failures / degraded states, making them visible at standard log
levels without needing DEBUG.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from app.services.model_routing_contract import (
    ExecutionRequest,
    ExecutionTrace,
)

# ---------------------------------------------------------------------------
# Loggers
# ---------------------------------------------------------------------------

_telemetry_logger = logging.getLogger("bentrade.routing.telemetry")
_gate_logger = logging.getLogger("bentrade.routing.gate")
_decision_logger = logging.getLogger("bentrade.routing.decision")


# ---------------------------------------------------------------------------
# 1. Request metadata summary (safe — no prompt content)
# ---------------------------------------------------------------------------

def summarize_request(request: ExecutionRequest) -> dict[str, Any]:
    """Build a safe metadata summary of a request for logging.

    Includes mode, task type, override fields, and prompt shape —
    but never prompt content.
    """
    prompt_summary: dict[str, Any] = {}
    if request.prompt:
        prompt_summary["message_count"] = len(request.prompt)
        # Approximate token count from total character length.
        total_chars = sum(
            len(str(m.get("content", ""))) for m in request.prompt
        )
        prompt_summary["approx_chars"] = total_chars
    if request.system_prompt:
        prompt_summary["has_system_prompt"] = True
        prompt_summary["system_prompt_chars"] = len(request.system_prompt)

    override_inputs: dict[str, Any] = {}
    if request.override_mode is not None:
        override_inputs["override_mode"] = request.override_mode
    if request.preferred_provider is not None:
        override_inputs["preferred_provider"] = request.preferred_provider
    if request.premium_override:
        override_inputs["premium_override"] = True

    return {
        "mode": request.mode,
        "task_type": request.task_type,
        "model_name": request.model_name,
        "prompt_summary": prompt_summary or None,
        "override_inputs": override_inputs or None,
    }


# ---------------------------------------------------------------------------
# 2. Build override_inputs snapshot for trace
# ---------------------------------------------------------------------------

def build_override_inputs(request: ExecutionRequest) -> dict[str, Any]:
    """Capture the override fields from a request for trace attachment."""
    inputs: dict[str, Any] = {}
    if request.override_mode is not None:
        inputs["override_mode"] = request.override_mode
    if request.preferred_provider is not None:
        inputs["preferred_provider"] = request.preferred_provider
    if request.premium_override:
        inputs["premium_override"] = True
    return inputs


# ---------------------------------------------------------------------------
# 3. Build provider attribution for trace
# ---------------------------------------------------------------------------

def build_provider_attribution(
    selected_provider: str | None,
    candidate_order: list[str],
    *,
    fallback_used: bool = False,
    provider_state: str | None = None,
    probe_type: str | None = None,
) -> dict[str, Any]:
    """Construct attribution metadata for the selected provider.

    Fields:
        provider         – ID of the selected provider.
        route_position   – 0-based index in the candidate order.
        is_fallback      – True if not the first candidate.
        provider_state   – State at selection time.
        probe_type       – Type of probe used (e.g. "config_only", "http_model_list").
    """
    if selected_provider is None:
        return {"provider": None, "reason": "no_provider_selected"}

    position = (
        candidate_order.index(selected_provider)
        if selected_provider in candidate_order
        else -1
    )
    attr: dict[str, Any] = {
        "provider": selected_provider,
        "route_position": position,
        "is_fallback": fallback_used,
    }
    if provider_state is not None:
        attr["provider_state"] = provider_state
    if probe_type is not None:
        attr["probe_type"] = probe_type
    return attr


# ---------------------------------------------------------------------------
# 4. Build skip summary from decision log
# ---------------------------------------------------------------------------

def build_skip_summary(decision_log: list[dict[str, str]]) -> dict[str, int]:
    """Count skip reasons from the route decision log.

    Returns a dict of {skip_reason: count}.
    """
    counts: dict[str, int] = {}
    for entry in decision_log:
        if entry.get("action") == "skipped":
            reason = entry.get("reason", "unknown")
            counts[reason] = counts.get(reason, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# 5. Build gate outcomes from decision log
# ---------------------------------------------------------------------------

def build_gate_outcomes(decision_log: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Extract gate-related outcomes from the decision log.

    Captures slot denied, dispatched (slot acquired), and capacity info.
    """
    outcomes: list[dict[str, Any]] = []
    for entry in decision_log:
        action = entry.get("action", "")
        provider = entry.get("provider")
        if not provider:
            continue
        if action == "skipped" and entry.get("reason") in (
            "at_max_concurrency", "slot_acquisition_failed"
        ):
            outcomes.append({
                "provider": provider,
                "outcome": "denied",
                "reason": entry.get("reason"),
                "in_flight": entry.get("gate_in_flight"),
                "max_concurrency": entry.get("gate_max"),
            })
        elif action == "dispatched":
            outcomes.append({
                "provider": provider,
                "outcome": "acquired",
            })
    return outcomes


# ---------------------------------------------------------------------------
# 6. Structured log event emitters
# ---------------------------------------------------------------------------

def emit_route_started(
    request_id: str,
    request: ExecutionRequest,
    resolved_mode: str,
    candidate_order: list[str],
    override_applied: bool,
) -> None:
    """Log the start of a routing cycle."""
    summary = summarize_request(request)
    _telemetry_logger.info(
        "[route:start] request_id=%s mode=%s→%s candidates=%s "
        "override_applied=%s task_type=%s",
        request_id,
        request.mode,
        resolved_mode,
        candidate_order,
        override_applied,
        request.task_type,
    )
    _decision_logger.debug(
        "[route:start:detail] request_id=%s summary=%s",
        request_id,
        summary,
    )


def emit_provider_probed(
    request_id: str,
    provider_id: str,
    state: str,
    *,
    probe_type: str | None = None,
    timing_ms: float | None = None,
) -> None:
    """Log the result of probing a provider."""
    _decision_logger.info(
        "[route:probe] request_id=%s provider=%s state=%s "
        "probe_type=%s timing_ms=%s",
        request_id,
        provider_id,
        state,
        probe_type,
        f"{timing_ms:.1f}" if timing_ms is not None else None,
    )


def emit_provider_skipped(
    request_id: str,
    provider_id: str,
    reason: str,
    *,
    state: str | None = None,
    gate_in_flight: int | None = None,
    gate_max: int | None = None,
) -> None:
    """Log that a provider was skipped during routing."""
    _decision_logger.info(
        "[route:skip] request_id=%s provider=%s reason=%s state=%s "
        "gate=%s/%s",
        request_id,
        provider_id,
        reason,
        state,
        gate_in_flight,
        gate_max,
    )


def emit_gate_acquired(
    request_id: str,
    provider_id: str,
) -> None:
    """Log successful gate slot acquisition."""
    _gate_logger.info(
        "[gate:acquired] request_id=%s provider=%s",
        request_id,
        provider_id,
    )


def emit_gate_denied(
    request_id: str,
    provider_id: str,
    *,
    in_flight: int | None = None,
    max_concurrency: int | None = None,
) -> None:
    """Log gate slot denial."""
    _gate_logger.warning(
        "[gate:denied] request_id=%s provider=%s in_flight=%s max=%s",
        request_id,
        provider_id,
        in_flight,
        max_concurrency,
    )


def emit_provider_dispatched(
    request_id: str,
    provider_id: str,
    state: str,
) -> None:
    """Log that a request was dispatched to a provider."""
    _decision_logger.info(
        "[route:dispatch] request_id=%s provider=%s state=%s",
        request_id,
        provider_id,
        state,
    )


def emit_provider_success(
    request_id: str,
    provider_id: str,
    *,
    timing_ms: float | None = None,
) -> None:
    """Log successful provider execution."""
    _telemetry_logger.info(
        "[route:success] request_id=%s provider=%s timing_ms=%s",
        request_id,
        provider_id,
        f"{timing_ms:.1f}" if timing_ms is not None else None,
    )


def emit_provider_failed(
    request_id: str,
    provider_id: str,
    error_code: str | None,
    *,
    retryable: bool = False,
) -> None:
    """Log a provider execution failure."""
    _telemetry_logger.warning(
        "[route:failed] request_id=%s provider=%s error_code=%s retryable=%s",
        request_id,
        provider_id,
        error_code,
        retryable,
    )


def emit_route_completed(
    request_id: str,
    trace: ExecutionTrace,
) -> None:
    """Log the final outcome of a routing cycle."""
    level = (
        logging.INFO
        if trace.execution_status == "success"
        else logging.WARNING
    )
    _telemetry_logger.log(
        level,
        "[route:complete] request_id=%s status=%s provider=%s "
        "fallback=%s timing_ms=%s candidates=%d attempted=%d "
        "skips=%s mode=%s→%s task_type=%s",
        request_id,
        trace.execution_status,
        trace.selected_provider,
        trace.fallback_used,
        f"{trace.timing_ms:.1f}" if trace.timing_ms is not None else None,
        len(trace.resolved_candidate_order),
        len(trace.attempted_providers),
        trace.skip_summary or {},
        trace.requested_mode,
        trace.resolved_mode,
        trace.task_type,
    )

    # Record trace for dashboard visibility (Step 13).
    try:
        from app.services.routing_dashboard_service import record_trace
        record_trace(trace)
    except Exception:
        pass  # Dashboard recording must never break routing.


# ---------------------------------------------------------------------------
# 7. Trace summary (for pipeline artifacts / debugging)
# ---------------------------------------------------------------------------

def trace_to_summary(trace: ExecutionTrace) -> dict[str, Any]:
    """Convert an ExecutionTrace to a JSON-safe summary dict.

    Excludes response_payload and prompt content.  Suitable for
    inclusion in pipeline artifacts, debug logs, or future UI.
    """
    return {
        "request_id": trace.request_id,
        "requested_mode": trace.requested_mode,
        "resolved_mode": trace.resolved_mode,
        "is_direct_mode": trace.is_direct_mode,
        "task_type": trace.task_type,
        "resolved_candidate_order": trace.resolved_candidate_order,
        "attempted_providers": trace.attempted_providers,
        "selected_provider": trace.selected_provider,
        "provider_states": trace.provider_states,
        "fallback_used": trace.fallback_used,
        "fallback_reason": trace.fallback_reason,
        "route_resolution": trace.route_resolution,
        "execution_status": trace.execution_status,
        "error_summary": trace.error_summary,
        "timing_ms": trace.timing_ms,
        "override_inputs": trace.override_inputs,
        "provider_attribution": trace.provider_attribution,
        "gate_outcomes": trace.gate_outcomes,
        "skip_summary": trace.skip_summary,
        "route_decision_log": trace.route_decision_log,
    }
