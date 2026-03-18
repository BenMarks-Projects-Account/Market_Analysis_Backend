"""UI-safe routing dashboard contract — summary shapes for frontend consumption.

Defines lightweight, JSON-serialisable summary shapes derived from
ExecutionTrace, ProviderRegistry, ProviderExecutionGate, and RoutingConfig.

Safety rules:
    • No raw prompts or payloads.
    • No secrets or credentials.
    • No verbose internal trace metadata unless explicitly debug-only.
    • All fields are primitive types (str, int, float, bool, None, list, dict).

Step 13 — Distributed Model Routing / UI visibility layer.
Step 16 — Provider health semantics + dashboard accuracy refinement.
Step 17 — UI execution mode control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 1. State → severity mapping for UI rendering
# ---------------------------------------------------------------------------

#: Maps ProviderState values to UI severity labels.
#: Severity labels control pill colour / icon in the frontend.
STATE_SEVERITY_MAP: dict[str, str] = {
    "available": "healthy",
    "busy": "warning",
    "degraded": "caution",
    "unavailable": "offline",
    "failed": "error",
}

#: Human-readable display labels for provider state values (Step 16).
#: Used by the frontend to show friendlier language without renaming states.
STATE_DISPLAY_LABELS: dict[str, str] = {
    "available": "Healthy",
    "busy": "Busy",
    "degraded": "Slow",
    "unavailable": "Offline",
    "failed": "Error",
}

#: Human-readable display labels for provider IDs.
PROVIDER_DISPLAY_LABELS: dict[str, str] = {
    "localhost_llm": "Localhost LLM",
    "network_model_machine": "Model Machine",
    "bedrock_titan_nova_pro": "Bedrock Titan Nova Pro",
}


def state_to_severity(state: str) -> str:
    """Map a ProviderState value to a UI severity label."""
    return STATE_SEVERITY_MAP.get(state, "offline")


def state_display_label(state: str) -> str:
    """Return a human-friendly display label for a ProviderState value."""
    return STATE_DISPLAY_LABELS.get(state, state.upper())


def provider_display_label(provider_id: str) -> str:
    """Return a human-readable label for a provider ID."""
    return PROVIDER_DISPLAY_LABELS.get(provider_id, provider_id)


# ---------------------------------------------------------------------------
# 2. Status detail text builder (Step 16)
# ---------------------------------------------------------------------------

def build_status_detail_text(
    *,
    state: str,
    status_reason: str,
    timing_ms: float | None,
    degraded_threshold_ms: float | None,
    probe_type: str,
    configured: bool,
) -> str:
    """Build a compact, human-readable status explanation.

    Input fields: state, status_reason, timing_ms, degraded_threshold_ms,
                  probe_type, configured.
    Derived: detail_text — single sentence explaining the current state.

    Examples:
        "Responded in 2054 ms; exceeds degraded threshold of 2000 ms"
        "Config-only readiness; no live inference probe available"
        "Provider reachable but returned HTTP 503 — server busy"
        "Connection timed out after 3.0 s"
        "Healthy — responded in 45 ms"
    """
    if not configured:
        return status_reason or "Not configured"

    if state == "available":
        if probe_type == "config_only":
            return "Configured (no live probe) — config-only readiness"
        if timing_ms is not None:
            return f"Healthy — responded in {timing_ms:.0f} ms"
        return status_reason or "Available"

    if state == "degraded":
        if timing_ms is not None and degraded_threshold_ms is not None:
            return (
                f"Responded in {timing_ms:.0f} ms; "
                f"exceeds degraded threshold of {degraded_threshold_ms:.0f} ms"
            )
        if timing_ms is not None:
            return f"Slow — responded in {timing_ms:.0f} ms"
        return status_reason or "Degraded performance detected"

    if state == "busy":
        return status_reason or "All capacity slots occupied"

    if state == "unavailable":
        return status_reason or "Provider unreachable or not configured"

    if state == "failed":
        # Truncate long error reasons for UI safety
        reason = status_reason or "Provider error"
        if len(reason) > 200:
            reason = reason[:197] + "..."
        return reason

    return status_reason or state


# ---------------------------------------------------------------------------
# 3. Per-provider health summary
# ---------------------------------------------------------------------------

@dataclass
class ProviderHealthSummary:
    """UI-safe health snapshot for a single provider.

    Extended in Step 16 with:
        - probe_type: "live" | "config_only" | "cached"
        - degraded_threshold_ms: active threshold (from RoutingConfig)
        - state_display_label: human-friendly state name
        - status_detail_text: compact explanation of current state
        - last_checked_at: ISO timestamp of last probe
    """
    provider: str
    display_label: str
    configured: bool
    current_state: str
    severity: str
    probe_success: bool
    status_reason: str
    timing_ms: float | None
    max_concurrency: int
    in_flight_count: int
    available_capacity: int
    registered: bool = True
    # Step 16 additions
    probe_type: str = "live"
    degraded_threshold_ms: float | None = None
    state_display_label: str = ""
    status_detail_text: str = ""
    last_checked_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_label": self.display_label,
            "configured": self.configured,
            "current_state": self.current_state,
            "severity": self.severity,
            "probe_success": self.probe_success,
            "status_reason": self.status_reason,
            "timing_ms": self.timing_ms,
            "max_concurrency": self.max_concurrency,
            "in_flight_count": self.in_flight_count,
            "available_capacity": self.available_capacity,
            "registered": self.registered,
            "probe_type": self.probe_type,
            "degraded_threshold_ms": self.degraded_threshold_ms,
            "state_display_label": self.state_display_label,
            "status_detail_text": self.status_detail_text,
            "last_checked_at": self.last_checked_at,
        }


# ---------------------------------------------------------------------------
# 3. Per-request routing attribution summary
# ---------------------------------------------------------------------------

@dataclass
class RequestRoutingSummary:
    """UI-safe routing attribution for a single model call."""
    request_id: str
    task_type: str | None
    requested_mode: str
    resolved_mode: str
    actual_provider: str | None
    provider_label: str | None
    is_direct_mode: bool
    fallback_used: bool
    selected_position: int | None
    override_applied: bool
    route_status: str
    execution_status: str
    route_summary_text: str
    skip_summary: dict[str, int] = field(default_factory=dict)
    gate_outcomes_summary: list[dict[str, Any]] = field(default_factory=list)
    timing_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "task_type": self.task_type,
            "requested_mode": self.requested_mode,
            "resolved_mode": self.resolved_mode,
            "actual_provider": self.actual_provider,
            "provider_label": self.provider_label,
            "is_direct_mode": self.is_direct_mode,
            "fallback_used": self.fallback_used,
            "selected_position": self.selected_position,
            "override_applied": self.override_applied,
            "route_status": self.route_status,
            "execution_status": self.execution_status,
            "route_summary_text": self.route_summary_text,
            "skip_summary": self.skip_summary,
            "gate_outcomes_summary": self.gate_outcomes_summary,
            "timing_ms": self.timing_ms,
        }


# ---------------------------------------------------------------------------
# 4. Global routing system summary
# ---------------------------------------------------------------------------

@dataclass
class RoutingSystemSummary:
    """UI-safe global routing system status.

    Step 17 adds selected_execution_mode and execution_mode_label.
    """
    routing_enabled: bool
    bedrock_enabled: bool
    default_max_concurrency: int
    provider_concurrency: dict[str, int] = field(default_factory=dict)
    probe_timeout_seconds: float = 3.0
    probe_degraded_threshold_ms: float = 2000.0
    config_source: str = "defaults"
    provider_count: int = 0
    config_loaded_at: str | None = None
    # Step 17 additions
    selected_execution_mode: str = ""
    execution_mode_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "routing_enabled": self.routing_enabled,
            "bedrock_enabled": self.bedrock_enabled,
            "default_max_concurrency": self.default_max_concurrency,
            "provider_concurrency": self.provider_concurrency,
            "probe_timeout_seconds": self.probe_timeout_seconds,
            "probe_degraded_threshold_ms": self.probe_degraded_threshold_ms,
            "config_source": self.config_source,
            "provider_count": self.provider_count,
            "config_loaded_at": self.config_loaded_at,
            "selected_execution_mode": self.selected_execution_mode,
            "execution_mode_label": self.execution_mode_label,
        }


# ---------------------------------------------------------------------------
# 5. Sensitive field blocklist — never include these in UI summaries
# ---------------------------------------------------------------------------

#: Fields that must NEVER appear in UI-safe summaries.
BLOCKED_FIELDS: frozenset[str] = frozenset({
    "response_payload",
    "prompt",
    "system_prompt",
    "raw_response",
    "error_detail",
})


def strip_blocked_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *data* with all blocked fields removed."""
    return {k: v for k, v in data.items() if k not in BLOCKED_FIELDS}


# ---------------------------------------------------------------------------
# 6. Execution mode display metadata (Step 17)
# ---------------------------------------------------------------------------

#: Display labels, descriptions, and grouping for each execution mode.
#: group: "primary" = distributed modes shown first; "direct" = single-provider.
EXECUTION_MODE_DISPLAY: dict[str, dict[str, str]] = {
    "local_distributed": {
        "label": "Local Distributed",
        "description": "Main machine, then model machine",
        "group": "primary",
    },
    "online_distributed": {
        "label": "Online Distributed",
        "description": "Main machine, then model machine, then Bedrock",
        "group": "primary",
    },
    "local": {
        "label": "Local",
        "description": "Main machine only",
        "group": "direct",
    },
    "model_machine": {
        "label": "Model Machine",
        "description": "Model machine only",
        "group": "direct",
    },
    "premium_online": {
        "label": "Premium Online",
        "description": "Bedrock only",
        "group": "direct",
    },
}


def execution_mode_display_label(mode: str) -> str:
    """Return the human-friendly label for an execution mode."""
    entry = EXECUTION_MODE_DISPLAY.get(mode)
    return entry["label"] if entry else mode


def execution_mode_description(mode: str) -> str:
    """Return the short description for an execution mode."""
    entry = EXECUTION_MODE_DISPLAY.get(mode)
    return entry["description"] if entry else ""


def build_execution_mode_options() -> list[dict[str, str]]:
    """Return the full list of execution mode options for the UI.

    Each entry has: mode, label, description, group.
    Primary modes are listed first.
    """
    options: list[dict[str, str]] = []
    for mode_key, meta in EXECUTION_MODE_DISPLAY.items():
        options.append({
            "mode": mode_key,
            "label": meta["label"],
            "description": meta["description"],
            "group": meta["group"],
        })
    return options
