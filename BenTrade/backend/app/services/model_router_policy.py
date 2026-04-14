"""Router policy engine — distributed provider selection + dispatch.

Implements the routing policy for ``local_distributed`` and
``online_distributed`` execution modes.  Direct modes (``local``,
``model_machine``, ``premium_online``) also go through here but with
single-provider semantics.

Responsibilities:
    1. Resolve candidate provider order from the ExecutionRequest.
    2. Probe live provider status (one fresh probe per provider per cycle).
    3. Check internal reservation capacity via ProviderExecutionGate.
    4. Select the first eligible provider, acquire its slot, and execute.
    5. On retryable failure, advance to the next eligible candidate.
    6. Populate ExecutionTrace with full routing decision data.
    7. Guarantee slot release in all code paths.
    8. Honor function-level overrides (override_mode, preferred_provider,
       premium_override) centrally and trace their effects.

Override precedence (highest → lowest):
    1. premium_override=True  → forces premium_online mode
    2. override_mode          → replaces base mode
    3. preferred_provider     → reorders candidates within resolved mode
    4. base request.mode      → fallback if no override applies

Override semantics:
    • override to a direct mode (local, model_machine, premium_online)
      = strict: single provider, no fallback chain.
    • override to a distributed mode (local_distributed, online_distributed)
      = flexible: multi-provider fallback within that mode.
    • Invalid overrides are detected, traced, and the base mode is used.

Separation of concerns:
    • Policy helpers live HERE — they do not leak into adapters.
    • Adapter execution goes through ``execute_with_provider()`` on the
      model_router module (existing Step 2 seam).
    • Execution gating is delegated to ``ProviderExecutionGate``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.services.model_execution_gate import (
    GateSnapshot,
    ProviderExecutionGate,
    get_execution_gate,
)
from app.services.model_provider_base import ProbeResult, ProviderResult
from app.services.model_provider_registry import (
    ProviderRegistry,
    ProviderStatusSnapshot,
    get_registry,
)
from app.services.model_routing_contract import (
    ExecutionMode,
    ExecutionRequest,
    ExecutionStatus,
    ExecutionTrace,
    FallbackReason,
    Provider,
    ProviderState,
    RouteResolutionStatus,
    build_execution_trace,
    is_distributed_mode,
    is_valid_mode,
    is_valid_provider,
    resolve_provider_order,
)
from app.services.model_routing_telemetry import (
    build_gate_outcomes,
    build_override_inputs,
    build_provider_attribution,
    build_skip_summary,
    emit_gate_acquired,
    emit_gate_denied,
    emit_provider_dispatched,
    emit_provider_failed,
    emit_provider_skipped,
    emit_provider_success,
    emit_route_completed,
    emit_route_started,
)

logger = logging.getLogger("bentrade.router_policy")


# ---------------------------------------------------------------------------
# 0a. Crawler-aware routing — skip network_model_machine when crawler is active
# ---------------------------------------------------------------------------
# The model machine (192.168.1.143) hosts LM Studio shared between the
# Company Evaluator crawler (runs locally on that machine) and BenTrade
# (routes LLM calls over the network).  When both send requests
# simultaneously, LM Studio's queue overflows ("Channel Error").
#
# This check calls the upstream Company Evaluator pipeline status endpoint
# directly (NOT the BenTrade proxy at /api/company-evaluator/status) to
# avoid a BenTrade-to-BenTrade HTTP loopback.
#
# The upstream response is {"running": bool, ...} so we read
# data["running"] directly.  If anyone switches this to the BenTrade
# proxy URL in the future, the field becomes data["pipeline"]["running"]
# because the proxy wraps the response in {"service_healthy": ...,
# "pipeline": ...}.
#
# Cached for 5 seconds to avoid HTTP overhead on every LLM call.
# Fails OPEN (returns False) on errors — if Company Evaluator is
# unreachable, the crawler almost certainly isn't running either, so
# it's safe to allow normal routing.

import requests as _requests_sync

_crawler_state_cache: tuple[float, bool] | None = None
_CRAWLER_STATE_CACHE_TTL_SECONDS = 5
_CRAWLER_STATUS_TIMEOUT = 1.5  # seconds — short enough not to add latency


def _get_crawler_status_url() -> str:
    """Build the upstream Company Evaluator pipeline status URL from config."""
    from app.config import get_settings
    base = get_settings().COMPANY_EVALUATOR_URL.rstrip("/")
    return f"{base}/api/pipeline/status"


def is_crawler_running() -> bool:
    """Check whether the Company Evaluator crawler is currently running.

    Uses a 5-second TTL cache so consecutive LLM routing calls within
    the same window share a single HTTP check.
    """
    global _crawler_state_cache

    now = time.time()
    if _crawler_state_cache is not None:
        cached_at, cached_value = _crawler_state_cache
        if now - cached_at < _CRAWLER_STATE_CACHE_TTL_SECONDS:
            return cached_value

    is_running = False
    try:
        resp = _requests_sync.get(
            _get_crawler_status_url(), timeout=_CRAWLER_STATUS_TIMEOUT,
        )
        if resp.ok:
            data = resp.json()
            is_running = bool(data.get("running"))
    except Exception as exc:
        logger.debug("[router] crawler status check failed: %s", exc)
        is_running = False

    _crawler_state_cache = (now, is_running)
    return is_running


# ---------------------------------------------------------------------------
# 0b. Round-robin rotation for distributed modes
# ---------------------------------------------------------------------------
# For sequential requests, all providers pass probe + gate checks, so the
# first candidate always wins.  This counter rotates the starting index
# so consecutive requests alternate across available providers.

import threading as _threading

_rotation_lock = _threading.Lock()
_rotation_counter: int = 0


# ---------------------------------------------------------------------------
# 0b. Circuit breaker — skip providers after repeated failures
# ---------------------------------------------------------------------------

import time as _time


class ProviderCircuitBreaker:
    """Skip providers after repeated failures — exponential cooldown.

    States:
        CLOSED  – normal operation, provider is attempted.
        OPEN    – provider is skipped (cooldown active).
        HALF-OPEN – cooldown expired, one probe attempt allowed.
                    Success → CLOSED; failure → OPEN with longer cooldown.

    Cooldown progression: base × 2^(failures - threshold), capped at max.
    Default: 30s → 60s → 120s → 240s → 300s cap.

    Failure weighting:
        Hard failures (connection error, DNS) count as 1.0.
        Soft failures (timeouts) count as 0.5 — the provider is up but
        overwhelmed, not down.  This means 6 timeouts are needed to trip
        the circuit instead of 3 connection errors.

    Time decay:
        Failure weight decays toward zero over ``decay_window_s`` seconds
        of quiet (no new failures).  This allows providers to naturally
        recover after a burst of load without waiting for the full
        circuit-open/half-open cycle.
    """

    # Failure weight constants
    HARD_FAILURE_WEIGHT: float = 1.0
    SOFT_FAILURE_WEIGHT: float = 0.5

    def __init__(
        self,
        failure_threshold: int = 3,
        base_cooldown: float = 30.0,
        max_cooldown: float = 300.0,
        decay_window_s: float = 120.0,
    ) -> None:
        self._lock = _threading.Lock()
        self._failure_weight: dict[str, float] = {}
        self._last_failure_time: dict[str, float] = {}
        self._open_until: dict[str, float] = {}
        self._threshold = failure_threshold
        self._base = base_cooldown
        self._max = max_cooldown
        self._decay_window = decay_window_s
        # Legacy alias for external access (admin endpoints, etc.)
        self._failures = self._failure_weight

    def _decayed_weight(self, provider_id: str, now: float) -> float:
        """Return the effective failure weight after time decay.

        Linearly decays from last recorded weight to zero over
        ``_decay_window`` seconds since the last failure.
        Only applies decay when at least 1 second has elapsed to
        avoid float precision issues during rapid-fire calls.
        """
        raw = self._failure_weight.get(provider_id, 0.0)
        if raw <= 0:
            return 0.0
        last = self._last_failure_time.get(provider_id, now)
        elapsed = now - last
        if elapsed >= self._decay_window:
            return 0.0
        if elapsed < 1.0:
            return raw
        decay_fraction = elapsed / self._decay_window
        return raw * (1.0 - decay_fraction)

    def record_success(self, provider_id: str) -> None:
        """Reset failure count and close the circuit."""
        with self._lock:
            old_weight = self._failure_weight.pop(provider_id, 0.0)
            was_open = provider_id in self._open_until
            self._last_failure_time.pop(provider_id, None)
            self._open_until.pop(provider_id, None)
            if old_weight > 0 or was_open:
                logger.info(
                    "event=circuit_closed provider=%s old_weight=%.1f was_open=%s",
                    provider_id, old_weight, was_open,
                )

    def record_failure(
        self,
        provider_id: str,
        *,
        is_timeout: bool = False,
    ) -> float | None:
        """Record a failure and possibly open the circuit.

        Args:
            provider_id: Provider that failed.
            is_timeout: If True, counts at half weight (provider overwhelmed,
                        not down).

        Returns the cooldown duration in seconds if the circuit just
        opened, or ``None`` if the circuit is still closed.
        """
        weight = self.SOFT_FAILURE_WEIGHT if is_timeout else self.HARD_FAILURE_WEIGHT

        with self._lock:
            now = _time.time()
            # Apply time decay to existing weight before adding new failure
            effective = self._decayed_weight(provider_id, now) + weight
            self._failure_weight[provider_id] = effective
            self._last_failure_time[provider_id] = now

            if effective >= self._threshold:
                # Compute cooldown based on how far over threshold
                excess = effective - self._threshold
                exp = min(int(excess), 5)
                cooldown = min(self._base * (2 ** exp), self._max)
                self._open_until[provider_id] = now + cooldown
                logger.warning(
                    "event=circuit_opened provider=%s cooldown_s=%.0f "
                    "failure_weight=%.1f threshold=%d is_timeout=%s",
                    provider_id, cooldown, effective, self._threshold, is_timeout,
                )
                return cooldown
            return None

    def is_open(self, provider_id: str) -> bool:
        """Return True if the circuit is open (provider should be skipped).

        When the cooldown expires the circuit transitions to half-open:
        the deadline is cleared so the next call is allowed through as a
        probe.  If that probe fails, ``record_failure`` re-opens the
        circuit with a longer cooldown.
        """
        with self._lock:
            deadline = self._open_until.get(provider_id)
            if deadline is None:
                # Also check time decay: if enough time passed since last
                # failure, silently clear accumulated weight
                now = _time.time()
                if self._decayed_weight(provider_id, now) <= 0:
                    self._failure_weight.pop(provider_id, None)
                    self._last_failure_time.pop(provider_id, None)
                return False
            if _time.time() >= deadline:
                # Half-open: clear deadline so one probe attempt is allowed
                self._open_until.pop(provider_id, None)
                return False
            return True

    def status(self, provider_id: str) -> dict:
        """Return circuit breaker status for a provider (admin API)."""
        with self._lock:
            now = _time.time()
            raw_weight = self._failure_weight.get(provider_id, 0.0)
            effective_weight = self._decayed_weight(provider_id, now)
            deadline = self._open_until.get(provider_id)
            is_open = deadline is not None and now < deadline
            return {
                "consecutive_failures": round(effective_weight, 1),
                "failure_weight": round(effective_weight, 1),
                "raw_weight": round(raw_weight, 1),
                "circuit_open": is_open,
                "cooldown_remaining_s": round(max(0, (deadline or 0) - now), 1) if is_open else 0,
            }

    def reset(self, provider_id: str | None = None) -> None:
        """Reset circuit state.  If *provider_id* is None, reset all."""
        with self._lock:
            if provider_id is None:
                self._failure_weight.clear()
                self._last_failure_time.clear()
                self._open_until.clear()
                logger.info("event=circuit_reset_all")
            else:
                self._failure_weight.pop(provider_id, None)
                self._last_failure_time.pop(provider_id, None)
                self._open_until.pop(provider_id, None)
                logger.info("event=circuit_reset provider=%s", provider_id)


# Module-level singleton — lives for the process lifetime.
_circuit_breaker = ProviderCircuitBreaker()


def get_circuit_breaker() -> ProviderCircuitBreaker:
    """Return the module-level circuit breaker singleton."""
    return _circuit_breaker


def _rotate_candidates(candidates: list[str]) -> list[str]:
    """Rotate *candidates* list by the current round-robin counter.

    Thread-safe.  Returns a new list; original is not mutated.
    Only applied to distributed modes with >1 candidate.
    """
    global _rotation_counter
    if len(candidates) <= 1:
        return list(candidates)
    with _rotation_lock:
        idx = _rotation_counter % len(candidates)
        _rotation_counter += 1
    return candidates[idx:] + candidates[:idx]


def reset_rotation_counter() -> None:
    """Reset the round-robin counter.  For testing only."""
    global _rotation_counter
    with _rotation_lock:
        _rotation_counter = 0


# ---------------------------------------------------------------------------
# 1. Skip reason classification
# ---------------------------------------------------------------------------

class SkipReason:
    """Constants for why a provider was skipped during routing.

    These are used in route_decision_log entries (not an enum to keep
    the log values readable strings).
    """
    NOT_REGISTERED = "not_registered"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "provider_unavailable"
    FAILED = "provider_failed"
    BUSY = "provider_busy"
    AT_CAPACITY = "at_max_concurrency"
    SLOT_DENIED = "slot_acquisition_failed"
    CIRCUIT_OPEN = "circuit_open"


# ---------------------------------------------------------------------------
# 2. Provider eligibility evaluation
# ---------------------------------------------------------------------------

# States that disqualify a provider outright.
_DISQUALIFYING_STATES: frozenset[str] = frozenset({
    ProviderState.UNAVAILABLE.value,
    ProviderState.FAILED.value,
})


def is_provider_eligible(
    probe: ProbeResult,
    gate_snapshot: GateSnapshot,
) -> tuple[bool, str]:
    """Evaluate whether a provider is eligible for dispatch.

    Returns (eligible, skip_reason).
    skip_reason is empty string when eligible.

    Eligibility rules (ordered):
        1. Must be configured.
        2. Must not be in a disqualifying state (unavailable, failed).
        3. Must not be busy.
        4. Must have reservation capacity.
        5. DEGRADED is eligible but should be noted.
    """
    if not probe.configured:
        return False, SkipReason.NOT_CONFIGURED

    if probe.state in _DISQUALIFYING_STATES:
        # Map to the specific skip reason.
        if probe.state == ProviderState.UNAVAILABLE.value:
            return False, SkipReason.UNAVAILABLE
        return False, SkipReason.FAILED

    if probe.state == ProviderState.BUSY.value:
        return False, SkipReason.BUSY

    if not gate_snapshot.has_capacity:
        return False, SkipReason.AT_CAPACITY

    return True, ""


def rank_candidates(
    probes: dict[str, ProbeResult],
    gate: ProviderExecutionGate,
    ordered_ids: list[str],
) -> list[tuple[str, bool, str]]:
    """Rank candidates in the given order, annotating eligibility.

    Returns list of (provider_id, eligible, skip_reason) tuples
    preserving the requested order.  DEGRADED providers stay in
    their original position (they are eligible but noted).
    """
    results: list[tuple[str, bool, str]] = []
    for pid in ordered_ids:
        probe = probes.get(pid)
        if probe is None:
            results.append((pid, False, SkipReason.NOT_REGISTERED))
            continue
        gate_snap = gate.snapshot(pid)
        eligible, reason = is_provider_eligible(probe, gate_snap)
        results.append((pid, eligible, reason))
    return results


def classify_fallback_reason(skip_reason: str) -> str:
    """Map a SkipReason to a FallbackReason enum value."""
    _MAP = {
        SkipReason.NOT_REGISTERED: FallbackReason.PROVIDER_UNAVAILABLE.value,
        SkipReason.NOT_CONFIGURED: FallbackReason.PROVIDER_UNAVAILABLE.value,
        SkipReason.UNAVAILABLE: FallbackReason.PROVIDER_UNAVAILABLE.value,
        SkipReason.FAILED: FallbackReason.PROVIDER_FAILED.value,
        SkipReason.BUSY: FallbackReason.PROVIDER_BUSY.value,
        SkipReason.AT_CAPACITY: FallbackReason.PROVIDER_BUSY.value,
        SkipReason.SLOT_DENIED: FallbackReason.PROVIDER_BUSY.value,
        SkipReason.CIRCUIT_OPEN: FallbackReason.PROVIDER_FAILED.value,
    }
    return _MAP.get(skip_reason, FallbackReason.PROVIDER_ERROR.value)


def should_attempt_next_provider(result: ProviderResult) -> bool:
    """Return True if the execution result is retryable on a different provider.

    Retryable conditions:
        • Connection error (provider went away during call)
        • Timeout (provider too slow, try another)
        • Not-configured (shouldn't happen post-gate, but defensive)

    Non-retryable:
        • Success
        • Application-level error in the response content
          (the provider responded — trying another won't help)
    """
    if result.success:
        return False

    retryable_codes = {"connection_error", "timeout", "no_endpoint", "not_configured"}
    return result.error_code in retryable_codes


# ---------------------------------------------------------------------------
# 3. Probe cache — avoids redundant probes within one routing cycle
# ---------------------------------------------------------------------------

class _RoutingCycleProbeCache:
    """Cache probe results within a single routing cycle.

    Ensures each provider is probed at most once per routing decision.
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry
        self._cache: dict[str, ProbeResult] = {}

    def probe(self, provider_id: str) -> ProbeResult:
        """Return cached probe or perform a fresh one."""
        if provider_id not in self._cache:
            self._cache[provider_id] = self._registry.probe_provider(provider_id)
        return self._cache[provider_id]

    def probe_all(self, provider_ids: list[str]) -> dict[str, ProbeResult]:
        """Probe all given providers, returning the cache dict."""
        for pid in provider_ids:
            self.probe(pid)
        return dict(self._cache)

    @property
    def probes(self) -> dict[str, ProbeResult]:
        return dict(self._cache)


# ---------------------------------------------------------------------------
# 4. Override resolution — centralized effective-routing computation
# ---------------------------------------------------------------------------

@dataclass
class RoutingResolution:
    """Result of resolving overrides into an effective routing plan.

    Fields:
        resolved_mode       – Final effective mode after overrides.
        candidate_order     – Ordered provider list for dispatch.
        route_resolution    – RESOLVED, OVERRIDE_APPLIED, INVALID_MODE, NO_CANDIDATES.
        is_strict           – True if the resolved mode is direct (no fallback chain).
        override_applied    – True if any override changed the routing.
        override_notes      – Diagnostic strings describing each override effect.
    """
    resolved_mode: str
    candidate_order: list[str]
    route_resolution: str
    is_strict: bool
    override_applied: bool
    override_notes: list[str] = field(default_factory=list)


def resolve_effective_routing(request: ExecutionRequest) -> RoutingResolution:
    """Centralized override resolution.

    Examines request.override_mode, request.preferred_provider, and
    request.premium_override to produce the final effective mode and
    candidate order.

    Override precedence (highest → lowest):
        1. premium_override=True  → forces premium_online
        2. override_mode          → replaces base mode (validated)
        3. preferred_provider     → reorders candidates (validated)
        4. base request.mode      → fallback

    Returns a RoutingResolution with full diagnostics.
    """
    notes: list[str] = []
    override_applied = False

    # -- Validate base mode --------------------------------------------------
    if not is_valid_mode(request.mode):
        return RoutingResolution(
            resolved_mode=request.mode,
            candidate_order=[],
            route_resolution=RouteResolutionStatus.INVALID_MODE.value,
            is_strict=True,
            override_applied=False,
            override_notes=[f"invalid base mode: {request.mode!r}"],
        )

    effective_mode = request.mode

    # -- 1. premium_override (highest priority) ------------------------------
    if request.premium_override:
        effective_mode = ExecutionMode.PREMIUM_ONLINE.value
        override_applied = True
        notes.append(
            f"premium_override=True → mode elevated to '{effective_mode}'"
        )

    # -- 2. override_mode (second priority; skipped if premium_override) -----
    elif request.override_mode is not None:
        if is_valid_mode(request.override_mode):
            effective_mode = request.override_mode
            override_applied = True
            notes.append(
                f"override_mode='{request.override_mode}' → "
                f"mode changed from '{request.mode}' to '{effective_mode}'"
            )
        else:
            # Invalid override_mode — trace it and fall back to base mode.
            notes.append(
                f"override_mode='{request.override_mode}' is invalid — ignored, "
                f"using base mode '{request.mode}'"
            )

    # -- 3. Resolve candidate order using the contract helper ----------------
    candidate_order = resolve_provider_order(request)

    # -- 4. preferred_provider diagnostics -----------------------------------
    if request.preferred_provider is not None:
        if is_valid_provider(request.preferred_provider):
            override_applied = True
            notes.append(
                f"preferred_provider='{request.preferred_provider}' → "
                f"moved to front of candidate order"
            )
        else:
            notes.append(
                f"preferred_provider='{request.preferred_provider}' is invalid — ignored"
            )

    # -- Determine strict vs flexible ----------------------------------------
    is_strict = not is_distributed_mode(effective_mode)

    # -- Determine route_resolution status -----------------------------------
    if not candidate_order:
        route_resolution = RouteResolutionStatus.NO_CANDIDATES.value
    elif override_applied:
        route_resolution = RouteResolutionStatus.OVERRIDE_APPLIED.value
    else:
        route_resolution = RouteResolutionStatus.RESOLVED.value

    return RoutingResolution(
        resolved_mode=effective_mode,
        candidate_order=candidate_order,
        route_resolution=route_resolution,
        is_strict=is_strict,
        override_applied=override_applied,
        override_notes=notes,
    )


# ---------------------------------------------------------------------------
# 5. Core routing + dispatch
# ---------------------------------------------------------------------------

def route_and_execute(
    request: ExecutionRequest,
    *,
    registry: ProviderRegistry | None = None,
    gate: ProviderExecutionGate | None = None,
    timeout: float | None = None,
) -> tuple[ProviderResult | None, ExecutionTrace]:
    """Main entry point: route *request* to the best eligible provider
    and execute.

    Returns (ProviderResult | None, ExecutionTrace).
    ProviderResult is None only if no provider could be dispatched.

    Override handling:
        Delegates to ``resolve_effective_routing()`` for centralized
        override resolution.  Override effects are logged in the
        route_decision_log for full traceability.

    For direct modes (local, model_machine) and strict overrides:
        Single candidate — either it works or it doesn't.

    For premium_online:
        Honest "not ready" if no provider is wired.

    For distributed modes (local_distributed, online_distributed):
        Walk the candidate list, skip ineligible, acquire gate slot,
        dispatch, fallback on retryable failure.
    """
    registry = registry or get_registry()
    gate = gate or get_execution_gate()
    t0 = time.perf_counter()

    # -- Centralized override resolution -------------------------------------
    resolution = resolve_effective_routing(request)
    resolved_mode = resolution.resolved_mode
    candidate_order = resolution.candidate_order

    # -- Round-robin rotation for distributed modes --------------------------
    # Sequential requests always find all providers eligible (probe OK, gate
    # free), so the first candidate always wins.  Rotating the starting
    # position ensures requests distribute across providers.
    if not resolution.is_strict and len(candidate_order) > 1:
        candidate_order = _rotate_candidates(candidate_order)

    # -- Crawler-aware filtering: skip network_model_machine when crawler
    #    is active to avoid saturating the shared LM Studio instance ------
    _NMM = Provider.NETWORK_MODEL_MACHINE.value
    if _NMM in candidate_order and is_crawler_running():
        logger.info(
            "[router] crawler_active=true skipping network_model_machine "
            "for task=%s (using remaining providers only)",
            request.task_type,
        )
        candidate_order = [p for p in candidate_order if p != _NMM]

    # -- TEMPORARY DIAGNOSTIC: verify round-robin + concurrency behavior -----
    logger.info(
        "DIAG_ROUTE: request_arriving candidates=%s gate_state=%s",
        candidate_order,
        {p: gate.in_flight_count(p) for p in candidate_order},
    )

    # -- Capture override inputs for trace -----------------------------------
    override_inputs = build_override_inputs(request)

    # -- Pre-assign request_id for correlated telemetry ----------------------
    import uuid
    request_id = uuid.uuid4().hex

    # -- Invalid mode --------------------------------------------------------
    if resolution.route_resolution == RouteResolutionStatus.INVALID_MODE.value:
        trace = build_execution_trace(
            request,
            resolved_mode=resolved_mode,
            route_resolution=RouteResolutionStatus.INVALID_MODE.value,
            execution_status=ExecutionStatus.NOT_ATTEMPTED.value,
            error_summary=f"Invalid execution mode: {request.mode!r}",
            route_decision_log=[{
                "action": "override_resolution",
                "notes": "; ".join(resolution.override_notes),
            }] if resolution.override_notes else [],
            timing_ms=(time.perf_counter() - t0) * 1000,
            override_inputs=override_inputs,
            resolved_candidate_order=[],
            is_direct_mode=True,
        )
        emit_route_completed(request_id, trace)
        return None, trace

    # -- Emit route started --------------------------------------------------
    emit_route_started(
        request_id, request, resolved_mode, candidate_order,
        resolution.override_applied,
    )

    # -- Build override diagnostics log entry --------------------------------
    decision_log: list[dict[str, str]] = []
    if resolution.override_applied or resolution.override_notes:
        override_entry: dict[str, str] = {
            "action": "override_resolution",
            "resolved_mode": resolved_mode,
            "override_applied": str(resolution.override_applied),
            "is_strict": str(resolution.is_strict),
        }
        if request.override_mode is not None:
            override_entry["override_mode"] = request.override_mode
        if request.preferred_provider is not None:
            override_entry["preferred_provider"] = request.preferred_provider
        if request.premium_override:
            override_entry["premium_override"] = "true"
        if resolution.override_notes:
            override_entry["notes"] = "; ".join(resolution.override_notes)
        decision_log.append(override_entry)

    # -- Handle empty candidate list for any mode ----------------------------
    if not candidate_order:
        trace = build_execution_trace(
            request,
            resolved_mode=resolved_mode,
            route_resolution=resolution.route_resolution,
            execution_status=ExecutionStatus.NOT_ATTEMPTED.value,
            error_summary=f"No candidate providers for mode '{resolved_mode}'",
            route_decision_log=decision_log,
            timing_ms=(time.perf_counter() - t0) * 1000,
            override_inputs=override_inputs,
            resolved_candidate_order=[],
            is_direct_mode=resolution.is_strict,
        )
        emit_route_completed(request_id, trace)
        return None, trace

    # -- Probe all candidates once -------------------------------------------
    # Skip probing circuit-open providers — this avoids the 3s probe timeout
    # for providers with repeated recent failures.
    cb = _circuit_breaker
    probe_candidates = [
        pid for pid in candidate_order if not cb.is_open(pid)
    ]
    probe_cache = _RoutingCycleProbeCache(registry)
    probes = probe_cache.probe_all(probe_candidates)

    # -- Build provider state snapshot for trace -----------------------------
    provider_states: dict[str, str] = {
        pid: probes[pid].state for pid in candidate_order if pid in probes
    }

    # -- Log provider status summary for diagnostics -------------------------
    for pid in candidate_order:
        cb_status = cb.status(pid)
        probe_state = provider_states.get(pid, "no_probe")
        circuit_open = cb_status.get("circuit_open", False)
        weight = cb_status.get("failure_weight", 0)
        logger.info(
            "event=provider_status provider=%s probe_state=%s "
            "circuit_open=%s failure_weight=%.1f cooldown_s=%.0f",
            pid, probe_state, circuit_open, weight,
            cb_status.get("cooldown_remaining_s", 0),
        )

    # -- Walk candidates and dispatch ----------------------------------------
    attempted_providers: list[str] = []
    selected_provider: str | None = None
    fallback_used = False
    last_fallback_reason: str | None = None
    final_result: ProviderResult | None = None
    first_candidate = candidate_order[0] if candidate_order else None
    selected_probe_type: str | None = None
    selected_state: str | None = None

    # If all providers are at capacity (concurrent dispatches from other
    # threads), wait for a slot to free up and retry.  This enables true
    # concurrent dispatch: multiple requests in-flight across providers.
    # Wait up to 6 × 30s = 180s total — long enough for any in-flight
    # model call to complete and release its gate slot.
    _max_wait_attempts = 6
    _wait_attempt = 0

    while True:
        _dispatched = False

        for idx, pid in enumerate(candidate_order):
            # -- Circuit breaker: skip providers in cooldown ----------------
            if cb.is_open(pid):
                decision_log.append({
                    "provider": pid,
                    "action": "skipped",
                    "reason": SkipReason.CIRCUIT_OPEN,
                    "cooldown_remaining_s": str(cb.status(pid).get("cooldown_remaining_s", 0)),
                })
                emit_provider_skipped(request_id, pid, SkipReason.CIRCUIT_OPEN)
                last_fallback_reason = classify_fallback_reason(SkipReason.CIRCUIT_OPEN)
                continue

            probe = probes.get(pid)
            if probe is None:
                decision_log.append({
                    "provider": pid,
                    "action": "skipped",
                    "reason": SkipReason.NOT_REGISTERED,
                })
                emit_provider_skipped(request_id, pid, SkipReason.NOT_REGISTERED)
                if idx > 0 or pid != first_candidate:
                    last_fallback_reason = classify_fallback_reason(SkipReason.NOT_REGISTERED)
                continue

            gate_snap = gate.snapshot(pid)
            eligible, skip_reason = is_provider_eligible(probe, gate_snap)

            if not eligible:
                decision_log.append({
                    "provider": pid,
                    "action": "skipped",
                    "reason": skip_reason,
                    "state": probe.state,
                    "gate_in_flight": str(gate_snap.in_flight),
                    "gate_max": str(gate_snap.max_concurrency),
                })
                emit_provider_skipped(
                    request_id, pid, skip_reason,
                    state=probe.state,
                    gate_in_flight=gate_snap.in_flight,
                    gate_max=gate_snap.max_concurrency,
                )
                last_fallback_reason = classify_fallback_reason(skip_reason)
                continue

            # Note if provider is degraded.
            if probe.state == ProviderState.DEGRADED.value:
                decision_log.append({
                    "provider": pid,
                    "action": "eligible_degraded",
                    "reason": "provider is degraded but eligible",
                    "state": probe.state,
                })

            # Try to acquire a gate slot.
            acquired = gate.acquire(pid)
            if not acquired:
                decision_log.append({
                    "provider": pid,
                    "action": "skipped",
                    "reason": SkipReason.SLOT_DENIED,
                    "state": probe.state,
                    "gate_in_flight": str(gate.in_flight_count(pid)),
                    "gate_max": str(gate.get_max_concurrency(pid)),
                })
                emit_gate_denied(
                    request_id, pid,
                    in_flight=gate.in_flight_count(pid),
                    max_concurrency=gate.get_max_concurrency(pid),
                )
                last_fallback_reason = classify_fallback_reason(SkipReason.SLOT_DENIED)
                continue

            # Slot acquired — dispatch.
            _dispatched = True
            emit_gate_acquired(request_id, pid)
            logger.info(
                "DIAG_ROUTE: dispatching_to=%s gate_after_acquire=%d/%d",
                pid,
                gate.in_flight_count(pid),
                gate.get_max_concurrency(pid),
            )
            attempted_providers.append(pid)
            if pid != first_candidate:
                fallback_used = True

            decision_log.append({
                "provider": pid,
                "action": "dispatched",
                "state": probe.state,
            })
            emit_provider_dispatched(request_id, pid, probe.state)

            try:
                result = _execute_provider(
                    registry, pid, request, timeout=timeout,
                )
                final_result = result
                selected_provider = pid
                selected_state = probe.state
                selected_probe_type = probe.metadata.get("probe_type")

                if result.success:
                    logger.info(
                        "DIAG_ROUTE: response_received provider=%s duration_ms=%d",
                        pid, int(result.timing_ms or 0),
                    )
                    cb.record_success(pid)
                    decision_log.append({
                        "provider": pid,
                        "action": "success",
                    })
                    emit_provider_success(
                        request_id, pid, timing_ms=result.timing_ms,
                    )
                    break

                # Execution failed — decide whether to try next.
                decision_log.append({
                    "provider": pid,
                    "action": "execution_failed",
                    "error_code": result.error_code or "unknown",
                    "error_message": result.error_message or "",
                })

                retryable = should_attempt_next_provider(result)
                is_timeout = result.error_code == "timeout"
                cooldown = cb.record_failure(pid, is_timeout=is_timeout)
                emit_provider_failed(
                    request_id, pid, result.error_code, retryable=retryable,
                )

                if retryable:
                    last_fallback_reason = classify_fallback_reason(
                        SkipReason.UNAVAILABLE
                        if result.error_code == "connection_error"
                        else SkipReason.FAILED
                    )
                    decision_log.append({
                        "provider": pid,
                        "action": "will_try_next",
                        "reason": f"retryable error: {result.error_code}",
                    })
                    continue
                else:
                    # Non-retryable failure — stop trying.
                    decision_log.append({
                        "provider": pid,
                        "action": "non_retryable_failure",
                        "reason": f"error_code={result.error_code}",
                    })
                    break

            finally:
                gate.release(pid)

        # for-loop finished (break or exhausted) — exit while if dispatched
        if _dispatched:
            break

        # All candidates skipped (at capacity) — wait for a slot to free up
        _wait_attempt += 1
        if _wait_attempt > _max_wait_attempts:
            decision_log.append({
                "action": "all_providers_busy",
                "reason": f"Exhausted {_max_wait_attempts} wait attempts",
            })
            break

        eligible_providers = [
            pid for pid in candidate_order
            if pid in probes and probes[pid].state not in _DISQUALIFYING_STATES
        ]
        if not eligible_providers:
            break  # No healthy providers to wait for

        decision_log.append({
            "action": "waiting_for_capacity",
            "wait_attempt": str(_wait_attempt),
            "providers": ", ".join(eligible_providers),
        })

        waited = gate.wait_for_any_capacity(
            eligible_providers,
            timeout=min(getattr(request, "timeout", None) or 30.0, 30.0),
        )
        if not waited:
            decision_log.append({
                "action": "wait_timeout",
                "reason": "Timed out waiting for provider capacity",
            })
            break

    # -- Build telemetry summaries -------------------------------------------
    skip_summary = build_skip_summary(decision_log)
    gate_outcomes = build_gate_outcomes(decision_log)
    provider_attribution = build_provider_attribution(
        selected_provider,
        candidate_order,
        fallback_used=fallback_used,
        provider_state=selected_state,
        probe_type=selected_probe_type,
    )

    # -- Build final trace ---------------------------------------------------
    elapsed_ms = (time.perf_counter() - t0) * 1000

    if final_result is not None and final_result.success:
        exec_status = ExecutionStatus.SUCCESS.value
    elif final_result is not None:
        exec_status = final_result.execution_status
    else:
        exec_status = ExecutionStatus.NOT_ATTEMPTED.value

    route_resolution = resolution.route_resolution
    if selected_provider is None and not attempted_providers:
        route_resolution = RouteResolutionStatus.NO_CANDIDATES.value

    error_summary: str | None = None
    error_detail: str | None = None
    if final_result is not None and not final_result.success:
        error_summary = final_result.error_message
        error_detail = f"error_code={final_result.error_code}"
    elif final_result is None:
        error_summary = "All candidate providers were disqualified"
        error_detail = f"candidates={candidate_order}, decision_log has details"

    trace = build_execution_trace(
        request,
        resolved_mode=resolved_mode,
        attempted_providers=attempted_providers,
        selected_provider=selected_provider,
        provider_states=provider_states,
        fallback_used=fallback_used,
        fallback_reason=last_fallback_reason if fallback_used else None,
        route_resolution=route_resolution,
        execution_status=exec_status,
        error_summary=error_summary,
        error_detail=error_detail,
        timing_ms=elapsed_ms,
        route_decision_log=decision_log,
        response_payload=final_result.raw_response if final_result else None,
        override_inputs=override_inputs,
        resolved_candidate_order=candidate_order,
        gate_outcomes=gate_outcomes,
        provider_attribution=provider_attribution,
        is_direct_mode=resolution.is_strict,
        skip_summary=skip_summary,
    )

    emit_route_completed(request_id, trace)

    return final_result, trace


# ---------------------------------------------------------------------------
# 6. Internal dispatch helper
# ---------------------------------------------------------------------------

def _execute_provider(
    registry: ProviderRegistry,
    provider_id: str,
    request: ExecutionRequest,
    *,
    timeout: float | None = None,
) -> ProviderResult:
    """Dispatch execution to a single provider via the registry.

    This wraps the registry lookup + adapter.execute() call.
    Gate acquisition / release is the caller's responsibility.
    """
    adapter = registry.get_provider(provider_id)
    if adapter is None:
        return ProviderResult(
            provider=provider_id,
            success=False,
            execution_status=ExecutionStatus.FAILED.value,
            error_code="unknown_provider",
            error_message=f"No adapter registered for '{provider_id}'",
        )
    return adapter.execute(request, timeout=timeout)
