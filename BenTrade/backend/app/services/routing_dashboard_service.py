"""Routing dashboard service — builds UI-safe summaries from routing infrastructure.

Reuses existing helpers:
    • ProviderRegistry.all_statuses() / get_provider_status()
    • ProviderExecutionGate.all_snapshots() / effective_config_summary()
    • RoutingConfig.effective_summary()
    • model_routing_telemetry.trace_to_summary()

Adds:
    • In-memory ring buffer for recent routing traces (capacity-capped).
    • Builder functions that compose the UI-safe contract shapes.
    • ``refresh_routing_runtime()`` — centralized, ordered refresh of
      config → gate → providers for coherent runtime state.

Steps 13–14 — Distributed Model Routing / UI visibility + runtime control.
"""

from __future__ import annotations

import collections
import logging
import threading
from typing import Any

from app.services.model_routing_contract import ExecutionTrace
from app.services.model_routing_telemetry import trace_to_summary
from app.services.routing_dashboard_contract import (
    ProviderHealthSummary,
    RequestRoutingSummary,
    RoutingSystemSummary,
    build_status_detail_text,
    execution_mode_display_label,
    provider_display_label,
    state_display_label,
    state_to_severity,
    strip_blocked_fields,
)

logger = logging.getLogger("bentrade.routing.dashboard")


# ---------------------------------------------------------------------------
# 1. Recent trace ring buffer (thread-safe, capacity-capped)
# ---------------------------------------------------------------------------

_MAX_RECENT_TRACES: int = 50

_recent_traces: collections.deque[dict[str, Any]] = collections.deque(
    maxlen=_MAX_RECENT_TRACES,
)
_trace_lock = threading.Lock()


def record_trace(trace: ExecutionTrace) -> None:
    """Append a routing trace to the recent-traces ring buffer.

    Converts the trace to a UI-safe summary dict before storing.
    Thread-safe.
    """
    summary = trace_to_summary(trace)
    safe = strip_blocked_fields(summary)
    with _trace_lock:
        _recent_traces.append(safe)


def get_recent_traces(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent routing trace summaries (newest first).

    Thread-safe.
    """
    with _trace_lock:
        traces = list(_recent_traces)
    # Newest first
    traces.reverse()
    return traces[:limit]


def clear_recent_traces() -> None:
    """Clear the trace buffer — primarily for testing."""
    with _trace_lock:
        _recent_traces.clear()


# ---------------------------------------------------------------------------
# 2. Provider health summary builder
# ---------------------------------------------------------------------------

def build_provider_health_summaries(
    *, refresh: bool = False,
) -> list[ProviderHealthSummary]:
    """Build UI-safe health summaries for all registered providers.

    Combines ProviderRegistry status snapshots with ExecutionGate snapshots
    and RoutingConfig thresholds.  Step 16 adds detail text, probe type,
    degraded threshold, state display label, and last-checked timestamp.
    """
    from app.services.model_execution_gate import get_execution_gate
    from app.services.model_provider_registry import get_registry
    from app.services.model_routing_config import get_routing_config

    registry = get_registry()
    gate = get_execution_gate()
    config = get_routing_config()

    statuses = registry.all_statuses(refresh=refresh)
    gate_snapshots = gate.all_snapshots()
    degraded_threshold = config.probe_degraded_threshold_ms

    summaries: list[ProviderHealthSummary] = []
    for status in statuses:
        pid = status.provider_id
        gs = gate_snapshots.get(pid)
        in_flight = gs.in_flight if gs else 0
        max_conc = gs.max_concurrency if gs else 1
        available_cap = max(0, max_conc - in_flight)
        p_type = status.probe_type

        detail_text = build_status_detail_text(
            state=status.state,
            status_reason=status.status_reason,
            timing_ms=status.timing_ms,
            degraded_threshold_ms=degraded_threshold,
            probe_type=p_type,
            configured=status.configured,
        )

        summaries.append(ProviderHealthSummary(
            provider=pid,
            display_label=provider_display_label(pid),
            configured=status.configured,
            current_state=status.state,
            severity=state_to_severity(status.state),
            probe_success=status.probe_success,
            status_reason=status.status_reason,
            timing_ms=status.timing_ms,
            max_concurrency=max_conc,
            in_flight_count=in_flight,
            available_capacity=available_cap,
            registered=status.registered,
            probe_type=p_type,
            degraded_threshold_ms=degraded_threshold,
            state_display_label=state_display_label(status.state),
            status_detail_text=detail_text,
            last_checked_at=status.checked_at,
        ))

    return summaries


# ---------------------------------------------------------------------------
# 3. Routing system summary builder
# ---------------------------------------------------------------------------

def build_routing_system_summary() -> RoutingSystemSummary:
    """Build a UI-safe summary of the global routing system."""
    from app.services.execution_mode_state import get_execution_mode
    from app.services.model_provider_registry import get_registry
    from app.services.model_routing_config import (
        get_config_loaded_at,
        get_routing_config,
    )

    config = get_routing_config()
    registry = get_registry()
    mode = get_execution_mode()

    return RoutingSystemSummary(
        routing_enabled=config.routing_enabled,
        bedrock_enabled=config.bedrock_enabled,
        default_max_concurrency=config.default_max_concurrency,
        provider_concurrency=dict(config.provider_concurrency),
        probe_timeout_seconds=config.probe_timeout_seconds,
        probe_degraded_threshold_ms=config.probe_degraded_threshold_ms,
        config_source=config.config_source,
        provider_count=len(registry.list_registered()),
        config_loaded_at=get_config_loaded_at(),
        selected_execution_mode=mode,
        execution_mode_label=execution_mode_display_label(mode),
    )


# ---------------------------------------------------------------------------
# 4. Per-request routing summary builder
# ---------------------------------------------------------------------------

def build_request_routing_summary(
    trace: ExecutionTrace,
) -> RequestRoutingSummary:
    """Convert an ExecutionTrace to a UI-safe per-request summary."""
    actual = trace.selected_provider
    label = provider_display_label(actual) if actual else None

    # Determine position in candidate list
    position: int | None = None
    if actual and trace.resolved_candidate_order:
        try:
            position = trace.resolved_candidate_order.index(actual)
        except ValueError:
            position = -1

    # Determine if override was applied
    override_applied = bool(trace.override_inputs)

    # Build compact route summary text
    summary_text = _build_route_summary_text(trace)

    return RequestRoutingSummary(
        request_id=trace.request_id,
        task_type=trace.task_type,
        requested_mode=trace.requested_mode,
        resolved_mode=trace.resolved_mode,
        actual_provider=actual,
        provider_label=label,
        is_direct_mode=trace.is_direct_mode,
        fallback_used=trace.fallback_used,
        selected_position=position,
        override_applied=override_applied,
        route_status=trace.route_resolution,
        execution_status=trace.execution_status,
        route_summary_text=summary_text,
        skip_summary=dict(trace.skip_summary),
        gate_outcomes_summary=list(trace.gate_outcomes),
        timing_ms=trace.timing_ms,
    )


def _build_route_summary_text(trace: ExecutionTrace) -> str:
    """Build a compact human-readable route summary label.

    Examples:
        "localhost_llm → success"
        "localhost_llm → network_model_machine (fallback: busy) → success"
        "no provider available → failed"
    """
    parts: list[str] = []

    if trace.selected_provider:
        label = provider_display_label(trace.selected_provider)
        if trace.fallback_used and trace.fallback_reason:
            parts.append(f"{label} (fallback: {trace.fallback_reason})")
        else:
            parts.append(label)
    else:
        parts.append("no provider available")

    parts.append(trace.execution_status)
    return " → ".join(parts)


# ---------------------------------------------------------------------------
# 5. Composite dashboard payload
# ---------------------------------------------------------------------------

def build_dashboard_payload(
    *, refresh_providers: bool = False, recent_limit: int = 10,
) -> dict[str, Any]:
    """Build the complete routing dashboard payload for the API.

    Combines system summary, provider health, and recent traces.
    """
    system = build_routing_system_summary()
    providers = build_provider_health_summaries(refresh=refresh_providers)
    recent = get_recent_traces(limit=recent_limit)

    return {
        "system": system.to_dict(),
        "providers": [p.to_dict() for p in providers],
        "recent_traces": recent,
    }


# ---------------------------------------------------------------------------
# 6. Centralized runtime refresh (Step 14)
# ---------------------------------------------------------------------------

def refresh_routing_runtime() -> dict[str, Any]:
    """Refresh routing config, execution gate, and provider statuses.

    Ordering is deliberate:
        1. Config — reload from environment (source of truth).
        2. Execution gate — rebuild from new config (concurrency limits).
        3. Provider statuses — live-probe with new probe parameters.

    Returns a composite audit payload with before/after summaries
    and per-stage results.  Safe to log (no secrets).
    """
    from app.services.model_execution_gate import (
        ProviderExecutionGate,
        get_execution_gate,
        reset_execution_gate,
    )
    from app.services.model_provider_registry import get_registry
    from app.services.model_routing_config import (
        get_routing_config,
        refresh_routing_config,
    )

    result: dict[str, Any] = {"stages": []}

    # ── Stage 1: Config refresh ────────────────────────────────
    config_diff = refresh_routing_config()
    result["config"] = config_diff
    result["stages"].append("config_refreshed")
    logger.info("[runtime_refresh] stage 1/3 — config refreshed")

    # ── Stage 2: Gate rebuild ──────────────────────────────────
    old_gate = get_execution_gate()
    old_gate_summary = old_gate.effective_config_summary()
    reset_execution_gate()
    new_gate = get_execution_gate()
    new_gate_summary = new_gate.effective_config_summary()
    result["gate"] = {
        "previous": old_gate_summary,
        "current": new_gate_summary,
    }
    result["stages"].append("gate_rebuilt")
    logger.info("[runtime_refresh] stage 2/3 — gate rebuilt from new config")

    # ── Stage 3: Provider status refresh ───────────────────────
    registry = get_registry()
    provider_statuses = registry.all_statuses(refresh=True)
    result["providers"] = [
        {
            "provider_id": s.provider_id,
            "state": s.state,
            "probe_success": s.probe_success,
            "timing_ms": s.timing_ms,
        }
        for s in provider_statuses
    ]
    result["stages"].append("providers_refreshed")
    logger.info(
        "[runtime_refresh] stage 3/3 — %d provider(s) probed",
        len(provider_statuses),
    )

    return result
