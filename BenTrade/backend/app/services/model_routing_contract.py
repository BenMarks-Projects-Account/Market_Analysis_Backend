"""Model routing contract — canonical modes, providers, states, and trace shapes.

Contract-first module.  Defines the shared vocabulary that model_router,
execution_mode_state, and routing/fallback logic all build on.

Concepts:
    Mode        — What the caller *asks for* (e.g. "local_distributed").
    Provider    — A concrete LLM backend (e.g. "localhost_llm").
    State       — Runtime health of a provider.
    Trace       — Structured record of a single routing decision + execution.

Design rules:
    • Modes and providers are separate namespaces.
    • A mode maps to an *ordered* list of candidate providers.
    • The routing trace always records requested mode, resolved mode,
      attempted providers, selected provider, and fallback metadata.
    • Override fields live in the execution request — never scattered
      across callers.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# 1. Canonical enums / constants
# ---------------------------------------------------------------------------

class ExecutionMode(str, Enum):
    """What the caller asks for."""
    LOCAL = "local"
    MODEL_MACHINE = "model_machine"
    PREMIUM_ONLINE = "premium_online"
    LOCAL_DISTRIBUTED = "local_distributed"
    ONLINE_DISTRIBUTED = "online_distributed"


class Provider(str, Enum):
    """A concrete LLM backend."""
    LOCALHOST_LLM = "localhost_llm"
    NETWORK_MODEL_MACHINE = "network_model_machine"
    BEDROCK_TITAN_NOVA_PRO = "bedrock_titan_nova_pro"
    # Future premium Bedrock providers can be added here without
    # breaking existing mode→provider mappings.


class ProviderState(str, Enum):
    """Runtime health of a single provider."""
    AVAILABLE = "available"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"
    FAILED = "failed"


class FallbackReason(str, Enum):
    """Why the router moved to the next provider in the chain."""
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_BUSY = "provider_busy"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_DEGRADED = "provider_degraded"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    EXPLICIT_OVERRIDE = "explicit_override"


class RouteResolutionStatus(str, Enum):
    """Outcome of the route resolution step (before execution)."""
    RESOLVED = "resolved"
    NO_CANDIDATES = "no_candidates"
    OVERRIDE_APPLIED = "override_applied"
    INVALID_MODE = "invalid_mode"


class ExecutionStatus(str, Enum):
    """Outcome of the actual model call."""
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    NOT_ATTEMPTED = "not_attempted"


# ---------------------------------------------------------------------------
# Frozen sets for fast membership checks
# ---------------------------------------------------------------------------

VALID_MODES: frozenset[str] = frozenset(m.value for m in ExecutionMode)
VALID_PROVIDERS: frozenset[str] = frozenset(p.value for p in Provider)
VALID_PROVIDER_STATES: frozenset[str] = frozenset(s.value for s in ProviderState)
VALID_FALLBACK_REASONS: frozenset[str] = frozenset(r.value for r in FallbackReason)

_DISTRIBUTED_MODES: frozenset[str] = frozenset({
    ExecutionMode.LOCAL_DISTRIBUTED.value,
    ExecutionMode.ONLINE_DISTRIBUTED.value,
})


# ---------------------------------------------------------------------------
# 2. Default provider ordering per mode
# ---------------------------------------------------------------------------

DEFAULT_PROVIDER_ORDER: dict[str, list[str]] = {
    ExecutionMode.LOCAL.value: [
        Provider.LOCALHOST_LLM.value,
    ],
    ExecutionMode.MODEL_MACHINE.value: [
        Provider.NETWORK_MODEL_MACHINE.value,
    ],
    # premium_online: Bedrock premium execution path (Step 6).
    ExecutionMode.PREMIUM_ONLINE.value: [
        Provider.BEDROCK_TITAN_NOVA_PRO.value,
    ],
    ExecutionMode.LOCAL_DISTRIBUTED.value: [
        Provider.LOCALHOST_LLM.value,
        Provider.NETWORK_MODEL_MACHINE.value,
    ],
    ExecutionMode.ONLINE_DISTRIBUTED.value: [
        Provider.LOCALHOST_LLM.value,
        Provider.NETWORK_MODEL_MACHINE.value,
        Provider.BEDROCK_TITAN_NOVA_PRO.value,
    ],
}


# ---------------------------------------------------------------------------
# 3. Execution request contract
# ---------------------------------------------------------------------------

@dataclass
class ExecutionRequest:
    """Normalized input for a model routing + execution cycle.

    Derived fields:
        None — all fields are caller-supplied or defaulted.

    Fields:
        mode                – Requested execution mode (VALID_MODES).
        model_name          – Model identifier passed to the provider.
        task_type           – Semantic label for the call (e.g. "stock_analysis").
        prompt              – User/assistant message payload (list of dicts).
        system_prompt       – Optional system message text.
        override_mode       – If set, replaces mode during route resolution.
        preferred_provider  – If set, placed first in the candidate list.
        premium_override    – If True, force premium_online path.
        routing_overrides   – Bag of additional routing hints (future use).
        metadata            – Arbitrary caller-supplied metadata for tracing.
    """
    mode: str
    model_name: str | None = None
    task_type: str | None = None
    prompt: list[dict[str, Any]] | None = None
    system_prompt: str | None = None
    override_mode: str | None = None
    preferred_provider: str | None = None
    premium_override: bool = False
    routing_overrides: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 4. Execution trace / result contract
# ---------------------------------------------------------------------------

@dataclass
class ExecutionTrace:
    """Structured record of one model routing + execution cycle.

    Semantics:
        requested_mode            – Original mode from the ExecutionRequest.
        resolved_mode             – Mode after override logic (may differ).
        attempted_providers       – Providers tried, in order.
        selected_provider         – Provider that actually ran the call (or None).
        provider_states           – Snapshot of each provider's state at decision time.
        fallback_used             – True if selected_provider != first candidate.
        fallback_reason           – Why fallback occurred (None if no fallback).
        route_resolution          – Outcome of the resolution step.
        execution_status          – Outcome of the model call itself.
        error_summary             – Short error description (None on success).
        error_detail              – Full error info / traceback string (None on success).
        timing_ms                 – Wall-clock time for the execution in milliseconds.
        request_id                – Unique id for this routing cycle.
        route_decision_log        – Ordered list of (provider, state, action) tuples.
        response_payload          – Raw response from the provider (passthrough).
        metadata                  – Carried forward from the ExecutionRequest.

    Telemetry fields (Step 7):
        override_inputs           – Snapshot of override fields from the request.
        resolved_candidate_order  – Final candidate list after override resolution.
        gate_outcomes             – Per-provider gate acquisition results.
        provider_attribution      – Attribution metadata for the selected provider.
        is_direct_mode            – True if resolved mode is non-distributed (single provider).
        task_type                 – Semantic label from the request (e.g. "stock_analysis").
        skip_summary              – Counts of skip reasons across all candidates.
    """
    requested_mode: str
    resolved_mode: str
    attempted_providers: list[str] = field(default_factory=list)
    selected_provider: str | None = None
    provider_states: dict[str, str] = field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str | None = None
    route_resolution: str = RouteResolutionStatus.RESOLVED.value
    execution_status: str = ExecutionStatus.NOT_ATTEMPTED.value
    error_summary: str | None = None
    error_detail: str | None = None
    timing_ms: float | None = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    route_decision_log: list[dict[str, str]] = field(default_factory=list)
    response_payload: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # -- Telemetry fields (Step 7) ------------------------------------------
    override_inputs: dict[str, Any] = field(default_factory=dict)
    resolved_candidate_order: list[str] = field(default_factory=list)
    gate_outcomes: list[dict[str, Any]] = field(default_factory=list)
    provider_attribution: dict[str, Any] = field(default_factory=dict)
    is_direct_mode: bool = True
    task_type: str | None = None
    skip_summary: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 5. Helper / validator functions
# ---------------------------------------------------------------------------

def is_valid_mode(value: str) -> bool:
    """Return True if *value* is a recognised execution mode."""
    return value in VALID_MODES


def is_valid_provider(value: str) -> bool:
    """Return True if *value* is a recognised provider."""
    return value in VALID_PROVIDERS


def is_valid_provider_state(value: str) -> bool:
    """Return True if *value* is a recognised provider state."""
    return value in VALID_PROVIDER_STATES


def is_distributed_mode(value: str) -> bool:
    """Return True if the mode involves multi-provider fallback."""
    return value in _DISTRIBUTED_MODES


def mode_to_default_provider_order(mode: str) -> list[str]:
    """Return the default ordered provider list for *mode*.

    Returns an empty list for unknown modes rather than raising, so callers
    can distinguish "valid mode with no providers" (premium_online) from
    "invalid mode" via ``is_valid_mode`` first.
    """
    return list(DEFAULT_PROVIDER_ORDER.get(mode, []))


def normalize_execution_request(raw: dict[str, Any]) -> ExecutionRequest:
    """Build an ``ExecutionRequest`` from a plain dict, applying defaults.

    Validates mode.  Validates override_mode and preferred_provider if
    supplied.  Unknown/invalid values for optional fields are dropped
    (set to None) so the contract always holds cleanly typed data.
    """
    mode = raw.get("mode", "")
    if not is_valid_mode(mode):
        raise ValueError(f"Invalid execution mode: {mode!r}")

    override_mode = raw.get("override_mode")
    if override_mode is not None and not is_valid_mode(override_mode):
        override_mode = None

    preferred_provider = raw.get("preferred_provider")
    if preferred_provider is not None and not is_valid_provider(preferred_provider):
        preferred_provider = None

    return ExecutionRequest(
        mode=mode,
        model_name=raw.get("model_name"),
        task_type=raw.get("task_type"),
        prompt=raw.get("prompt"),
        system_prompt=raw.get("system_prompt"),
        override_mode=override_mode,
        preferred_provider=preferred_provider,
        premium_override=bool(raw.get("premium_override", False)),
        routing_overrides=raw.get("routing_overrides") or {},
        metadata=raw.get("metadata") or {},
    )


def normalize_provider_state(value: str | None) -> str:
    """Coerce a raw state string to a valid ``ProviderState`` value.

    Returns ``ProviderState.UNAVAILABLE`` for unrecognised / None inputs.
    """
    if value is not None and value in VALID_PROVIDER_STATES:
        return value
    return ProviderState.UNAVAILABLE.value


def build_execution_trace(
    request: ExecutionRequest,
    *,
    resolved_mode: str | None = None,
    attempted_providers: list[str] | None = None,
    selected_provider: str | None = None,
    provider_states: dict[str, str] | None = None,
    fallback_used: bool = False,
    fallback_reason: str | None = None,
    route_resolution: str = RouteResolutionStatus.RESOLVED.value,
    execution_status: str = ExecutionStatus.NOT_ATTEMPTED.value,
    error_summary: str | None = None,
    error_detail: str | None = None,
    timing_ms: float | None = None,
    route_decision_log: list[dict[str, str]] | None = None,
    response_payload: Any = None,
    override_inputs: dict[str, Any] | None = None,
    resolved_candidate_order: list[str] | None = None,
    gate_outcomes: list[dict[str, Any]] | None = None,
    provider_attribution: dict[str, Any] | None = None,
    is_direct_mode: bool = True,
    skip_summary: dict[str, int] | None = None,
) -> ExecutionTrace:
    """Construct a fully-populated ``ExecutionTrace`` from an
    ``ExecutionRequest`` and execution outcome fields.
    """
    final_resolved = resolved_mode or request.override_mode or request.mode
    return ExecutionTrace(
        requested_mode=request.mode,
        resolved_mode=final_resolved,
        attempted_providers=list(attempted_providers or []),
        selected_provider=selected_provider,
        provider_states=dict(provider_states or {}),
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        route_resolution=route_resolution,
        execution_status=execution_status,
        error_summary=error_summary,
        error_detail=error_detail,
        timing_ms=timing_ms,
        request_id=uuid.uuid4().hex,
        route_decision_log=list(route_decision_log or []),
        response_payload=response_payload,
        metadata=dict(request.metadata),
        override_inputs=dict(override_inputs or {}),
        resolved_candidate_order=list(resolved_candidate_order or []),
        gate_outcomes=list(gate_outcomes or []),
        provider_attribution=dict(provider_attribution or {}),
        is_direct_mode=is_direct_mode,
        task_type=request.task_type,
        skip_summary=dict(skip_summary or {}),
    )


def resolve_provider_order(request: ExecutionRequest) -> list[str]:
    """Determine the ordered provider candidate list for *request*.

    Resolution rules (applied in order):
        1. If ``premium_override`` is True → use premium_online provider order.
        2. If ``override_mode`` is set and valid → use that mode's order.
        3. Otherwise → use the requested mode's default order.

    If ``preferred_provider`` is set and valid, it is moved to the front
    of the list (deduplicated).
    """
    # Step 1: determine effective mode
    if request.premium_override:
        effective_mode = ExecutionMode.PREMIUM_ONLINE.value
    elif request.override_mode and is_valid_mode(request.override_mode):
        effective_mode = request.override_mode
    else:
        effective_mode = request.mode

    order = mode_to_default_provider_order(effective_mode)

    # Step 2: honour preferred_provider
    if request.preferred_provider and is_valid_provider(request.preferred_provider):
        pref = request.preferred_provider
        # Remove if already present, then prepend
        order = [pref] + [p for p in order if p != pref]

    return order


# ---------------------------------------------------------------------------
# 6. Request builder helpers — convenience for function-level overrides
# ---------------------------------------------------------------------------

def with_override_mode(request: ExecutionRequest, mode: str) -> ExecutionRequest:
    """Return a copy of *request* with ``override_mode`` set.

    Does NOT validate *mode* — the router policy will detect and trace
    invalid overrides at dispatch time.
    """
    return ExecutionRequest(
        mode=request.mode,
        model_name=request.model_name,
        task_type=request.task_type,
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        override_mode=mode,
        preferred_provider=request.preferred_provider,
        premium_override=request.premium_override,
        routing_overrides=dict(request.routing_overrides),
        metadata=dict(request.metadata),
    )


def with_preferred_provider(
    request: ExecutionRequest, provider: str,
) -> ExecutionRequest:
    """Return a copy of *request* with ``preferred_provider`` set."""
    return ExecutionRequest(
        mode=request.mode,
        model_name=request.model_name,
        task_type=request.task_type,
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        override_mode=request.override_mode,
        preferred_provider=provider,
        premium_override=request.premium_override,
        routing_overrides=dict(request.routing_overrides),
        metadata=dict(request.metadata),
    )


def build_premium_request(
    request: ExecutionRequest,
) -> ExecutionRequest:
    """Return a copy of *request* with ``premium_override=True``."""
    return ExecutionRequest(
        mode=request.mode,
        model_name=request.model_name,
        task_type=request.task_type,
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        override_mode=request.override_mode,
        preferred_provider=request.preferred_provider,
        premium_override=True,
        routing_overrides=dict(request.routing_overrides),
        metadata=dict(request.metadata),
    )
