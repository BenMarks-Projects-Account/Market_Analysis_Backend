"""Tests for Step 4 — Distributed router policy engine + execution gating.

Covers:
    • ProviderExecutionGate (acquire / release / concurrency / context manager)
    • Router policy helpers (eligibility, ranking, skip classification)
    • route_and_execute for direct modes
    • route_and_execute for distributed modes with fallback
    • Reservation gating under concurrency
    • Trace population and decision logging
    • Edge cases (double release, all disqualified, premium_online)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.model_execution_gate import (
    DEFAULT_MAX_CONCURRENCY,
    GateSnapshot,
    ProviderExecutionGate,
    get_execution_gate,
    reset_execution_gate,
)
from app.services.model_provider_base import ProbeResult, ProviderResult
from app.services.model_provider_registry import ProviderRegistry
from app.services.model_router_policy import (
    SkipReason,
    _RoutingCycleProbeCache,
    classify_fallback_reason,
    is_provider_eligible,
    rank_candidates,
    route_and_execute,
    should_attempt_next_provider,
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
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _probe(
    provider: str,
    state: str = ProviderState.AVAILABLE.value,
    configured: bool = True,
    **kwargs: Any,
) -> ProbeResult:
    """Quick ProbeResult factory."""
    return ProbeResult(
        provider=provider,
        configured=configured,
        state=state,
        probe_success=True,
        status_reason=kwargs.get("status_reason", "test"),
        **{k: v for k, v in kwargs.items() if k != "status_reason"},
    )


def _result(
    provider: str,
    success: bool = True,
    error_code: str | None = None,
    error_message: str | None = None,
    execution_status: str | None = None,
) -> ProviderResult:
    """Quick ProviderResult factory."""
    return ProviderResult(
        provider=provider,
        success=success,
        execution_status=execution_status or (
            ExecutionStatus.SUCCESS.value if success else ExecutionStatus.FAILED.value
        ),
        content="test content" if success else None,
        error_code=error_code,
        error_message=error_message,
    )


def _request(mode: str = ExecutionMode.LOCAL_DISTRIBUTED.value, **kwargs: Any) -> ExecutionRequest:
    """Quick ExecutionRequest factory."""
    return ExecutionRequest(mode=mode, **kwargs)


def _build_registry_with_mock_adapters(
    probe_map: dict[str, ProbeResult] | None = None,
    execute_map: dict[str, ProviderResult] | None = None,
) -> ProviderRegistry:
    """Build a registry with mock adapters that return controlled probes/results."""
    probe_map = probe_map or {}
    execute_map = execute_map or {}
    registry = ProviderRegistry()

    for pid in set(list(probe_map.keys()) + list(execute_map.keys())):
        adapter = MagicMock()
        adapter.provider_id = pid

        # Default probe
        probe = probe_map.get(pid, _probe(pid))
        adapter.probe.return_value = probe
        adapter.probe_state.return_value = probe.state
        adapter.is_configured = probe.configured

        # Default execute
        result = execute_map.get(pid, _result(pid))
        adapter.execute.return_value = result

        registry.register(adapter)

    return registry


# =========================================================================
# Part A — ProviderExecutionGate
# =========================================================================


class TestGateBasics:
    """Core acquire / release / inspection."""

    def test_default_max_concurrency(self):
        gate = ProviderExecutionGate()
        assert gate.get_max_concurrency(Provider.LOCALHOST_LLM.value) == 1
        assert gate.get_max_concurrency(Provider.NETWORK_MODEL_MACHINE.value) == 1
        assert gate.get_max_concurrency(Provider.BEDROCK_TITAN_NOVA_PRO.value) == 1

    def test_unknown_provider_defaults_to_1(self):
        gate = ProviderExecutionGate()
        assert gate.get_max_concurrency("unknown_thing") == 1

    def test_set_max_concurrency(self):
        gate = ProviderExecutionGate()
        gate.set_max_concurrency(Provider.LOCALHOST_LLM.value, 3)
        assert gate.get_max_concurrency(Provider.LOCALHOST_LLM.value) == 3

    def test_set_max_concurrency_rejects_zero(self):
        gate = ProviderExecutionGate()
        with pytest.raises(ValueError, match="must be >= 1"):
            gate.set_max_concurrency(Provider.LOCALHOST_LLM.value, 0)

    def test_acquire_and_release(self):
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        assert gate.acquire(pid) is True
        assert gate.in_flight_count(pid) == 1
        gate.release(pid)
        assert gate.in_flight_count(pid) == 0

    def test_acquire_at_capacity_returns_false(self):
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        assert gate.acquire(pid) is True
        assert gate.acquire(pid) is False  # max=1, already at 1
        gate.release(pid)

    def test_acquire_after_release_works(self):
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        gate.acquire(pid)
        gate.release(pid)
        assert gate.acquire(pid) is True
        gate.release(pid)

    def test_multiple_providers_independent(self):
        gate = ProviderExecutionGate()
        p1 = Provider.LOCALHOST_LLM.value
        p2 = Provider.NETWORK_MODEL_MACHINE.value
        assert gate.acquire(p1) is True
        assert gate.acquire(p2) is True
        assert gate.in_flight_count(p1) == 1
        assert gate.in_flight_count(p2) == 1
        gate.release(p1)
        gate.release(p2)

    def test_higher_concurrency(self):
        gate = ProviderExecutionGate({Provider.LOCALHOST_LLM.value: 3})
        pid = Provider.LOCALHOST_LLM.value
        assert gate.acquire(pid) is True  # 1/3
        assert gate.acquire(pid) is True  # 2/3
        assert gate.acquire(pid) is True  # 3/3
        assert gate.acquire(pid) is False  # 4/3 denied
        gate.release(pid)
        assert gate.acquire(pid) is True  # back to 3/3
        # Clean up
        gate.release(pid)
        gate.release(pid)
        gate.release(pid)


class TestGateReleaseSafety:
    """Ensure release is safe and never goes negative."""

    def test_release_without_acquire_is_noop(self):
        gate = ProviderExecutionGate()
        gate.release(Provider.LOCALHOST_LLM.value)  # Should not raise
        assert gate.in_flight_count(Provider.LOCALHOST_LLM.value) == 0

    def test_double_release_clamps_to_zero(self):
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        gate.acquire(pid)
        gate.release(pid)
        gate.release(pid)  # Extra release
        assert gate.in_flight_count(pid) == 0

    def test_no_negative_in_flight(self):
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        for _ in range(5):
            gate.release(pid)
        assert gate.in_flight_count(pid) == 0


class TestGateContextManager:
    """reservation() context manager guarantees release."""

    def test_context_manager_acquires_and_releases(self):
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        with gate.reservation(pid) as acquired:
            assert acquired is True
            assert gate.in_flight_count(pid) == 1
        assert gate.in_flight_count(pid) == 0

    def test_context_manager_releases_on_exception(self):
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        with pytest.raises(RuntimeError):
            with gate.reservation(pid) as acquired:
                assert acquired is True
                raise RuntimeError("boom")
        assert gate.in_flight_count(pid) == 0

    def test_context_manager_at_capacity(self):
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        gate.acquire(pid)  # Fill 1/1
        with gate.reservation(pid) as acquired:
            assert acquired is False
            assert gate.in_flight_count(pid) == 1  # unchanged
        assert gate.in_flight_count(pid) == 1  # original still held
        gate.release(pid)


class TestGateInspection:
    """Snapshot and has_capacity methods."""

    def test_has_capacity_when_free(self):
        gate = ProviderExecutionGate()
        assert gate.has_capacity(Provider.LOCALHOST_LLM.value) is True

    def test_has_capacity_when_full(self):
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        gate.acquire(pid)
        assert gate.has_capacity(pid) is False
        gate.release(pid)

    def test_snapshot_reflects_state(self):
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        snap = gate.snapshot(pid)
        assert snap.in_flight == 0
        assert snap.max_concurrency == 1
        assert snap.has_capacity is True

        gate.acquire(pid)
        snap = gate.snapshot(pid)
        assert snap.in_flight == 1
        assert snap.has_capacity is False
        gate.release(pid)

    def test_all_snapshots(self):
        gate = ProviderExecutionGate()
        snaps = gate.all_snapshots()
        assert len(snaps) >= 3  # All default providers

    def test_reset(self):
        gate = ProviderExecutionGate()
        gate.acquire(Provider.LOCALHOST_LLM.value)
        gate.reset()
        assert gate.in_flight_count(Provider.LOCALHOST_LLM.value) == 0


class TestGateThreadSafety:
    """Concurrent acquire respects limits."""

    def test_concurrent_acquire_max1(self):
        """Only one thread should successfully acquire when max=1."""
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        results = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            results.append(gate.acquire(pid))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 1
        assert results.count(False) == 1
        gate.release(pid)


class TestGateSingleton:
    """Module-level singleton behavior."""

    def test_get_execution_gate_returns_same_instance(self):
        reset_execution_gate()
        g1 = get_execution_gate()
        g2 = get_execution_gate()
        assert g1 is g2
        reset_execution_gate()

    def test_reset_clears_singleton(self):
        reset_execution_gate()
        g1 = get_execution_gate()
        reset_execution_gate()
        g2 = get_execution_gate()
        assert g1 is not g2
        reset_execution_gate()


# =========================================================================
# Part B — Policy helpers
# =========================================================================


class TestIsProviderEligible:
    """is_provider_eligible(probe, gate_snapshot)."""

    def test_available_with_capacity(self):
        probe = _probe(Provider.LOCALHOST_LLM.value)
        snap = GateSnapshot(Provider.LOCALHOST_LLM.value, 0, 1, True)
        eligible, reason = is_provider_eligible(probe, snap)
        assert eligible is True
        assert reason == ""

    def test_not_configured(self):
        probe = _probe(Provider.BEDROCK_TITAN_NOVA_PRO.value, configured=False)
        snap = GateSnapshot(Provider.BEDROCK_TITAN_NOVA_PRO.value, 0, 1, True)
        eligible, reason = is_provider_eligible(probe, snap)
        assert eligible is False
        assert reason == SkipReason.NOT_CONFIGURED

    def test_unavailable_state(self):
        probe = _probe(Provider.LOCALHOST_LLM.value, state=ProviderState.UNAVAILABLE.value)
        snap = GateSnapshot(Provider.LOCALHOST_LLM.value, 0, 1, True)
        eligible, reason = is_provider_eligible(probe, snap)
        assert eligible is False
        assert reason == SkipReason.UNAVAILABLE

    def test_failed_state(self):
        probe = _probe(Provider.LOCALHOST_LLM.value, state=ProviderState.FAILED.value)
        snap = GateSnapshot(Provider.LOCALHOST_LLM.value, 0, 1, True)
        eligible, reason = is_provider_eligible(probe, snap)
        assert eligible is False
        assert reason == SkipReason.FAILED

    def test_busy_state(self):
        probe = _probe(Provider.LOCALHOST_LLM.value, state=ProviderState.BUSY.value)
        snap = GateSnapshot(Provider.LOCALHOST_LLM.value, 0, 1, True)
        eligible, reason = is_provider_eligible(probe, snap)
        assert eligible is False
        assert reason == SkipReason.BUSY

    def test_at_max_concurrency(self):
        probe = _probe(Provider.LOCALHOST_LLM.value)
        snap = GateSnapshot(Provider.LOCALHOST_LLM.value, 1, 1, False)
        eligible, reason = is_provider_eligible(probe, snap)
        assert eligible is False
        assert reason == SkipReason.AT_CAPACITY

    def test_degraded_is_eligible(self):
        probe = _probe(Provider.LOCALHOST_LLM.value, state=ProviderState.DEGRADED.value)
        snap = GateSnapshot(Provider.LOCALHOST_LLM.value, 0, 1, True)
        eligible, reason = is_provider_eligible(probe, snap)
        assert eligible is True
        assert reason == ""


class TestRankCandidates:
    """rank_candidates preserves order and annotates eligibility."""

    def test_all_available(self):
        gate = ProviderExecutionGate()
        probes = {
            Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
            Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
        }
        order = [Provider.LOCALHOST_LLM.value, Provider.NETWORK_MODEL_MACHINE.value]
        ranked = rank_candidates(probes, gate, order)
        assert len(ranked) == 2
        assert ranked[0] == (Provider.LOCALHOST_LLM.value, True, "")
        assert ranked[1] == (Provider.NETWORK_MODEL_MACHINE.value, True, "")

    def test_first_unavailable_second_available(self):
        gate = ProviderExecutionGate()
        probes = {
            Provider.LOCALHOST_LLM.value: _probe(
                Provider.LOCALHOST_LLM.value, state=ProviderState.UNAVAILABLE.value
            ),
            Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
        }
        order = [Provider.LOCALHOST_LLM.value, Provider.NETWORK_MODEL_MACHINE.value]
        ranked = rank_candidates(probes, gate, order)
        assert ranked[0][1] is False
        assert ranked[1][1] is True

    def test_unknown_provider_not_registered(self):
        gate = ProviderExecutionGate()
        probes = {}
        ranked = rank_candidates(probes, gate, ["unknown_provider"])
        assert ranked[0] == ("unknown_provider", False, SkipReason.NOT_REGISTERED)


class TestClassifyFallbackReason:
    """classify_fallback_reason maps skip reasons to FallbackReason values."""

    def test_unavailable(self):
        assert classify_fallback_reason(SkipReason.UNAVAILABLE) == FallbackReason.PROVIDER_UNAVAILABLE.value

    def test_busy(self):
        assert classify_fallback_reason(SkipReason.BUSY) == FallbackReason.PROVIDER_BUSY.value

    def test_at_capacity(self):
        assert classify_fallback_reason(SkipReason.AT_CAPACITY) == FallbackReason.PROVIDER_BUSY.value

    def test_failed(self):
        assert classify_fallback_reason(SkipReason.FAILED) == FallbackReason.PROVIDER_FAILED.value

    def test_unknown_maps_to_error(self):
        assert classify_fallback_reason("something_weird") == FallbackReason.PROVIDER_ERROR.value


class TestShouldAttemptNextProvider:
    """should_attempt_next_provider classifies retryable vs non-retryable."""

    def test_success_not_retryable(self):
        assert should_attempt_next_provider(_result("p", success=True)) is False

    def test_connection_error_retryable(self):
        assert should_attempt_next_provider(
            _result("p", success=False, error_code="connection_error")
        ) is True

    def test_timeout_retryable(self):
        assert should_attempt_next_provider(
            _result("p", success=False, error_code="timeout")
        ) is True

    def test_request_error_not_retryable(self):
        assert should_attempt_next_provider(
            _result("p", success=False, error_code="request_error")
        ) is False

    def test_no_endpoint_retryable(self):
        assert should_attempt_next_provider(
            _result("p", success=False, error_code="no_endpoint")
        ) is True


# =========================================================================
# Part C — route_and_execute: direct modes
# =========================================================================


class TestRouteAndExecuteDirectLocal:
    """mode=local — single provider dispatch."""

    def test_local_healthy_succeeds(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
            execute_map={Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value)},
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is not None
        assert result.success is True
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value
        assert trace.execution_status == ExecutionStatus.SUCCESS.value
        assert trace.fallback_used is False
        assert gate.in_flight_count(Provider.LOCALHOST_LLM.value) == 0

    def test_local_unavailable_fails(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(
                    Provider.LOCALHOST_LLM.value, state=ProviderState.UNAVAILABLE.value,
                ),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is None
        assert trace.execution_status == ExecutionStatus.NOT_ATTEMPTED.value
        assert "disqualified" in (trace.error_summary or "").lower()


class TestRouteAndExecuteDirectModelMachine:
    """mode=model_machine — single provider."""

    def test_model_machine_healthy(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value)},
            execute_map={Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value)},
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.MODEL_MACHINE.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is not None
        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value


class TestRouteAndExecutePremiumOnline:
    """mode=premium_online — Bedrock path (honest failure when not registered)."""

    def test_premium_online_bedrock_not_registered(self):
        registry = ProviderRegistry()
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.PREMIUM_ONLINE.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is None
        # Bedrock is the candidate but not registered in empty registry.
        assert trace.route_resolution == RouteResolutionStatus.NO_CANDIDATES.value

    def test_premium_override_forces_premium(self):
        registry = ProviderRegistry()
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            premium_override=True,
        )

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is None
        assert trace.resolved_mode == ExecutionMode.PREMIUM_ONLINE.value
        # premium_override routes to Bedrock, but empty registry has no adapters.
        assert trace.route_resolution == RouteResolutionStatus.NO_CANDIDATES.value


class TestRouteAndExecuteInvalidMode:
    """Invalid mode produces controlled error."""

    def test_invalid_mode(self):
        registry = ProviderRegistry()
        gate = ProviderExecutionGate()
        req = _request(mode="nonexistent_mode")

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is None
        assert trace.route_resolution == RouteResolutionStatus.INVALID_MODE.value
        assert "invalid" in (trace.error_summary or "").lower()


# =========================================================================
# Part D — route_and_execute: distributed modes
# =========================================================================


class TestDistributedLocalDistributed:
    """mode=local_distributed: localhost -> model_machine fallback."""

    def test_picks_localhost_when_healthy_and_free(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is not None
        assert result.success is True
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value
        assert trace.fallback_used is False
        # Model machine should NOT have been called.
        adapter_mm = registry.get_provider(Provider.NETWORK_MODEL_MACHINE.value)
        adapter_mm.execute.assert_not_called()

    def test_falls_back_to_model_machine_when_localhost_unavailable(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(
                    Provider.LOCALHOST_LLM.value, state=ProviderState.UNAVAILABLE.value,
                ),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is not None
        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
        assert trace.fallback_used is True
        assert trace.fallback_reason is not None
        # Trace should log that localhost was skipped.
        skip_entries = [
            e for e in trace.route_decision_log if e.get("action") == "skipped"
        ]
        assert len(skip_entries) >= 1
        assert skip_entries[0]["provider"] == Provider.LOCALHOST_LLM.value

    def test_skips_localhost_when_internally_reserved(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        gate = ProviderExecutionGate()
        # Pre-fill localhost slot.
        gate.acquire(Provider.LOCALHOST_LLM.value)

        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is not None
        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
        assert trace.fallback_used is True

        # localhost should show "at_max_concurrency" in the log.
        skip_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "skipped"
            and e.get("provider") == Provider.LOCALHOST_LLM.value
        ]
        assert len(skip_entries) == 1
        assert skip_entries[0]["reason"] == SkipReason.AT_CAPACITY

        # Clean up the pre-acquired slot.
        gate.release(Provider.LOCALHOST_LLM.value)

    def test_skips_localhost_when_busy(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(
                    Provider.LOCALHOST_LLM.value, state=ProviderState.BUSY.value,
                ),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
        assert trace.fallback_used is True
        skip_entries = [
            e for e in trace.route_decision_log
            if e.get("reason") == SkipReason.BUSY
        ]
        assert len(skip_entries) == 1


class TestDistributedOnlineDistributed:
    """mode=online_distributed: localhost -> model_machine -> bedrock."""

    def test_falls_through_to_bedrock_when_eligible(self):
        """All local providers unavailable, Bedrock configured + available."""
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(
                    Provider.LOCALHOST_LLM.value, state=ProviderState.UNAVAILABLE.value,
                ),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(
                    Provider.NETWORK_MODEL_MACHINE.value, state=ProviderState.UNAVAILABLE.value,
                ),
                Provider.BEDROCK_TITAN_NOVA_PRO.value: _probe(
                    Provider.BEDROCK_TITAN_NOVA_PRO.value,
                    state=ProviderState.AVAILABLE.value,
                    configured=True,
                ),
            },
            execute_map={
                Provider.BEDROCK_TITAN_NOVA_PRO.value: _result(
                    Provider.BEDROCK_TITAN_NOVA_PRO.value,
                ),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.ONLINE_DISTRIBUTED.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is not None
        assert result.success is True
        assert trace.selected_provider == Provider.BEDROCK_TITAN_NOVA_PRO.value
        assert trace.fallback_used is True
        # Two skip entries (localhost, model_machine).
        skip_entries = [
            e for e in trace.route_decision_log if e.get("action") == "skipped"
        ]
        assert len(skip_entries) == 2


class TestDegradedProviders:
    """DEGRADED remains eligible but is noted in the trace."""

    def test_degraded_used_when_first(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(
                    Provider.LOCALHOST_LLM.value, state=ProviderState.DEGRADED.value,
                ),
            },
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value
        # Check that degraded note is in the decision log.
        degraded_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "eligible_degraded"
        ]
        assert len(degraded_entries) == 1

    def test_degraded_used_only_when_healthy_not_eligible(self):
        """Healthy provider is at capacity; degraded second provider is used."""
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(
                    Provider.NETWORK_MODEL_MACHINE.value,
                    state=ProviderState.DEGRADED.value,
                ),
            },
            execute_map={
                Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        gate = ProviderExecutionGate()
        gate.acquire(Provider.LOCALHOST_LLM.value)  # Fill slot

        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
        assert trace.fallback_used is True
        gate.release(Provider.LOCALHOST_LLM.value)


class TestAllDisqualified:
    """All providers disqualified → controlled failure."""

    def test_all_unavailable(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(
                    Provider.LOCALHOST_LLM.value, state=ProviderState.UNAVAILABLE.value,
                ),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(
                    Provider.NETWORK_MODEL_MACHINE.value, state=ProviderState.UNAVAILABLE.value,
                ),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is None
        assert trace.execution_status == ExecutionStatus.NOT_ATTEMPTED.value
        assert trace.route_resolution == RouteResolutionStatus.NO_CANDIDATES.value
        assert trace.error_summary is not None

    def test_all_at_capacity(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        gate = ProviderExecutionGate()
        gate.acquire(Provider.LOCALHOST_LLM.value)
        gate.acquire(Provider.NETWORK_MODEL_MACHINE.value)

        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is None
        assert trace.execution_status == ExecutionStatus.NOT_ATTEMPTED.value
        gate.release(Provider.LOCALHOST_LLM.value)
        gate.release(Provider.NETWORK_MODEL_MACHINE.value)


# =========================================================================
# Part E — Reservation release guarantees
# =========================================================================


class TestReservationRelease:
    """Gate slot is always released after dispatch."""

    def test_released_on_success(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
            execute_map={Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value)},
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result.success is True
        assert gate.in_flight_count(Provider.LOCALHOST_LLM.value) == 0

    def test_released_on_execution_failure(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(
                    Provider.LOCALHOST_LLM.value,
                    success=False,
                    error_code="request_error",
                ),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result.success is False
        assert gate.in_flight_count(Provider.LOCALHOST_LLM.value) == 0

    def test_released_on_exception_during_execute(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
        )
        # Make execute() raise an exception.
        adapter = registry.get_provider(Provider.LOCALHOST_LLM.value)
        adapter.execute.side_effect = RuntimeError("kaboom")

        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL.value)

        with pytest.raises(RuntimeError, match="kaboom"):
            route_and_execute(req, registry=registry, gate=gate)

        assert gate.in_flight_count(Provider.LOCALHOST_LLM.value) == 0

    def test_no_reservation_leak_across_calls(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        gate = ProviderExecutionGate()

        for _ in range(5):
            req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
            result, trace = route_and_execute(req, registry=registry, gate=gate)
            assert result.success is True

        assert gate.in_flight_count(Provider.LOCALHOST_LLM.value) == 0
        assert gate.in_flight_count(Provider.NETWORK_MODEL_MACHINE.value) == 0


# =========================================================================
# Part F — Execution fallback with retryable errors
# =========================================================================


class TestFallbackOnRetryableError:
    """Router advances to next provider on retryable execution failure."""

    def test_connection_error_falls_back(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(
                    Provider.LOCALHOST_LLM.value,
                    success=False,
                    error_code="connection_error",
                    error_message="Connection refused",
                ),
                Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
        assert trace.fallback_used is True
        assert len(trace.attempted_providers) == 2
        # Both providers should have had execute called.
        registry.get_provider(Provider.LOCALHOST_LLM.value).execute.assert_called_once()
        registry.get_provider(Provider.NETWORK_MODEL_MACHINE.value).execute.assert_called_once()

    def test_timeout_falls_back(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(
                    Provider.LOCALHOST_LLM.value,
                    success=False,
                    error_code="timeout",
                    execution_status=ExecutionStatus.TIMEOUT.value,
                ),
                Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
        assert trace.fallback_used is True

    def test_non_retryable_error_stops_chain(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(
                    Provider.LOCALHOST_LLM.value,
                    success=False,
                    error_code="request_error",
                ),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result.success is False
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value
        # Model machine should NOT have been called.
        registry.get_provider(Provider.NETWORK_MODEL_MACHINE.value).execute.assert_not_called()


# =========================================================================
# Part G — Trace population
# =========================================================================


class TestTracePopulation:
    """ExecutionTrace is fully populated with routing decisions."""

    def test_trace_has_all_required_fields(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert trace.requested_mode == ExecutionMode.LOCAL_DISTRIBUTED.value
        assert trace.resolved_mode == ExecutionMode.LOCAL_DISTRIBUTED.value
        assert trace.attempted_providers == [Provider.LOCALHOST_LLM.value]
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value
        assert Provider.LOCALHOST_LLM.value in trace.provider_states
        assert Provider.NETWORK_MODEL_MACHINE.value in trace.provider_states
        assert trace.execution_status == ExecutionStatus.SUCCESS.value
        assert trace.timing_ms is not None
        assert trace.timing_ms >= 0
        assert trace.request_id  # non-empty
        assert len(trace.route_decision_log) > 0
        assert trace.error_summary is None  # successes have no error

    def test_trace_records_skip_reasons(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(
                    Provider.LOCALHOST_LLM.value, state=ProviderState.BUSY.value,
                ),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(
                    Provider.NETWORK_MODEL_MACHINE.value, state=ProviderState.FAILED.value,
                ),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is None
        log_reasons = [e.get("reason") for e in trace.route_decision_log]
        assert SkipReason.BUSY in log_reasons
        assert SkipReason.FAILED in log_reasons

    def test_trace_records_provider_states(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(
                    Provider.LOCALHOST_LLM.value, state=ProviderState.DEGRADED.value,
                ),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(
                    Provider.NETWORK_MODEL_MACHINE.value, state=ProviderState.AVAILABLE.value,
                ),
            },
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)

        _, trace = route_and_execute(req, registry=registry, gate=gate)

        assert trace.provider_states[Provider.LOCALHOST_LLM.value] == ProviderState.DEGRADED.value
        assert trace.provider_states[Provider.NETWORK_MODEL_MACHINE.value] == ProviderState.AVAILABLE.value

    def test_override_mode_reflected_in_trace(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
        )

        _, trace = route_and_execute(req, registry=registry, gate=gate)

        assert trace.requested_mode == ExecutionMode.LOCAL.value
        assert trace.resolved_mode == ExecutionMode.LOCAL_DISTRIBUTED.value


# =========================================================================
# Part H — Concurrency gating integration
# =========================================================================


class TestConcurrencyGatingIntegration:
    """Verify the gate blocks concurrent dispatch to the same provider."""

    def test_concurrent_routing_blocked(self):
        """Two simultaneous route_and_execute calls — only one gets localhost."""
        call_order = []
        barrier = threading.Barrier(2, timeout=5)

        def slow_execute(request, *, timeout=None):
            """Simulates a slow provider call."""
            call_order.append(threading.current_thread().name)
            time.sleep(0.1)
            return _result(Provider.LOCALHOST_LLM.value)

        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        # Make localhost use the slow execute.
        adapter = registry.get_provider(Provider.LOCALHOST_LLM.value)
        adapter.execute.side_effect = slow_execute

        gate = ProviderExecutionGate()
        results = [None, None]

        def worker(idx):
            req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
            results[idx] = route_and_execute(req, registry=registry, gate=gate)

        t1 = threading.Thread(target=worker, args=(0,), name="worker-0")
        t2 = threading.Thread(target=worker, args=(1,), name="worker-1")
        t1.start()
        time.sleep(0.01)  # Slight stagger so t1 gets localhost first.
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Both should have succeeded.
        r1, t1_trace = results[0]
        r2, t2_trace = results[1]
        assert r1.success is True
        assert r2.success is True

        # They should have used different providers (one localhost, one model_machine).
        providers_used = {t1_trace.selected_provider, t2_trace.selected_provider}
        assert providers_used == {
            Provider.LOCALHOST_LLM.value,
            Provider.NETWORK_MODEL_MACHINE.value,
        }

        # Gate should be clean after both complete.
        assert gate.in_flight_count(Provider.LOCALHOST_LLM.value) == 0
        assert gate.in_flight_count(Provider.NETWORK_MODEL_MACHINE.value) == 0


# =========================================================================
# Part I — Probe cache
# =========================================================================


class TestProbeCacheInRoutingCycle:
    """Probes are cached within a single routing cycle."""

    def test_probe_called_once_per_provider(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
            },
        )
        cache = _RoutingCycleProbeCache(registry)

        p1 = cache.probe(Provider.LOCALHOST_LLM.value)
        p2 = cache.probe(Provider.LOCALHOST_LLM.value)

        assert p1 is p2
        # The adapter's probe should have been called exactly once.
        adapter = registry.get_provider(Provider.LOCALHOST_LLM.value)
        adapter.probe.assert_called_once()

    def test_probe_all_populates_cache(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        cache = _RoutingCycleProbeCache(registry)
        probes = cache.probe_all([
            Provider.LOCALHOST_LLM.value,
            Provider.NETWORK_MODEL_MACHINE.value,
        ])

        assert len(probes) == 2
        assert Provider.LOCALHOST_LLM.value in probes
        assert Provider.NETWORK_MODEL_MACHINE.value in probes


# =========================================================================
# Part J — route_and_execute via model_router.py integration
# =========================================================================


class TestModelRouterIntegration:
    """model_router.route_and_execute delegates to the policy engine."""

    def test_route_and_execute_accessible_via_model_router(self):
        from app.services.model_router import route_and_execute as router_entry

        registry = _build_registry_with_mock_adapters(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
            execute_map={Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value)},
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL.value)

        result, trace = router_entry(req, registry=registry, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value


# =========================================================================
# Part K — Preferred provider
# =========================================================================


class TestPreferredProvider:
    """preferred_provider reorders the candidate list."""

    def test_preferred_provider_tried_first(self):
        registry = _build_registry_with_mock_adapters(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            preferred_provider=Provider.NETWORK_MODEL_MACHINE.value,
        )

        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
        # localhost should not have been tried first.
        assert trace.attempted_providers[0] == Provider.NETWORK_MODEL_MACHINE.value
