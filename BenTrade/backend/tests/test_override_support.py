"""Tests for Step 5 — Function-level override support.

Covers:
    • resolve_effective_routing() — override resolution helper
    • override_mode changes effective mode
    • preferred_provider reorders candidate list
    • premium_override elevates to premium path
    • Invalid overrides handled explicitly and traced
    • Overrides respect gate capacity and provider availability
    • ExecutionTrace records override effects
    • No-override behavior remains unchanged
    • Request builder helpers
    • Strict vs flexible override semantics
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.model_execution_gate import ProviderExecutionGate
from app.services.model_provider_base import ProbeResult, ProviderResult
from app.services.model_provider_registry import ProviderRegistry
from app.services.model_router_policy import (
    RoutingResolution,
    SkipReason,
    resolve_effective_routing,
    route_and_execute,
)
from app.services.model_routing_contract import (
    ExecutionMode,
    ExecutionRequest,
    ExecutionStatus,
    ExecutionTrace,
    Provider,
    ProviderState,
    RouteResolutionStatus,
    build_premium_request,
    with_override_mode,
    with_preferred_provider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _probe(
    provider: str,
    state: str = ProviderState.AVAILABLE.value,
    configured: bool = True,
    **kwargs: Any,
) -> ProbeResult:
    return ProbeResult(
        provider=provider,
        configured=configured,
        state=state,
        probe_success=True,
        status_reason=kwargs.get("status_reason", "test"),
    )


def _result(
    provider: str,
    success: bool = True,
    error_code: str | None = None,
) -> ProviderResult:
    return ProviderResult(
        provider=provider,
        success=success,
        execution_status=ExecutionStatus.SUCCESS.value if success else ExecutionStatus.FAILED.value,
        content="test" if success else None,
        error_code=error_code,
    )


def _request(mode: str = ExecutionMode.LOCAL_DISTRIBUTED.value, **kwargs: Any) -> ExecutionRequest:
    return ExecutionRequest(mode=mode, **kwargs)


def _registry(
    probe_map: dict[str, ProbeResult] | None = None,
    execute_map: dict[str, ProviderResult] | None = None,
) -> ProviderRegistry:
    probe_map = probe_map or {}
    execute_map = execute_map or {}
    registry = ProviderRegistry()
    for pid in set(list(probe_map.keys()) + list(execute_map.keys())):
        adapter = MagicMock()
        adapter.provider_id = pid
        probe = probe_map.get(pid, _probe(pid))
        adapter.probe.return_value = probe
        adapter.probe_state.return_value = probe.state
        adapter.is_configured = probe.configured
        result = execute_map.get(pid, _result(pid))
        adapter.execute.return_value = result
        registry.register(adapter)
    return registry


# =========================================================================
# Part A — resolve_effective_routing()
# =========================================================================


class TestResolveEffectiveRoutingNoOverrides:
    """No overrides applied — base mode used."""

    def test_local_distributed_no_overrides(self):
        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
        res = resolve_effective_routing(req)
        assert res.resolved_mode == ExecutionMode.LOCAL_DISTRIBUTED.value
        assert res.override_applied is False
        assert res.is_strict is False
        assert res.route_resolution == RouteResolutionStatus.RESOLVED.value
        assert len(res.override_notes) == 0

    def test_local_no_overrides(self):
        req = _request(mode=ExecutionMode.LOCAL.value)
        res = resolve_effective_routing(req)
        assert res.resolved_mode == ExecutionMode.LOCAL.value
        assert res.is_strict is True
        assert res.override_applied is False

    def test_candidate_order_matches_default(self):
        req = _request(mode=ExecutionMode.ONLINE_DISTRIBUTED.value)
        res = resolve_effective_routing(req)
        assert res.candidate_order == [
            Provider.LOCALHOST_LLM.value,
            Provider.NETWORK_MODEL_MACHINE.value,
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
        ]


class TestResolveEffectiveRoutingOverrideMode:
    """override_mode changes effective mode."""

    def test_override_to_local(self):
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            override_mode=ExecutionMode.LOCAL.value,
        )
        res = resolve_effective_routing(req)
        assert res.resolved_mode == ExecutionMode.LOCAL.value
        assert res.override_applied is True
        assert res.is_strict is True
        assert res.route_resolution == RouteResolutionStatus.OVERRIDE_APPLIED.value
        assert any("override_mode" in n for n in res.override_notes)

    def test_override_to_model_machine(self):
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode=ExecutionMode.MODEL_MACHINE.value,
        )
        res = resolve_effective_routing(req)
        assert res.resolved_mode == ExecutionMode.MODEL_MACHINE.value
        assert res.candidate_order == [Provider.NETWORK_MODEL_MACHINE.value]
        assert res.is_strict is True

    def test_override_to_distributed(self):
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode=ExecutionMode.ONLINE_DISTRIBUTED.value,
        )
        res = resolve_effective_routing(req)
        assert res.resolved_mode == ExecutionMode.ONLINE_DISTRIBUTED.value
        assert res.is_strict is False  # distributed = flexible

    def test_invalid_override_mode_ignored(self):
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode="nonexistent_mode",
        )
        res = resolve_effective_routing(req)
        assert res.resolved_mode == ExecutionMode.LOCAL.value  # falls back
        assert res.override_applied is False
        assert any("invalid" in n.lower() for n in res.override_notes)

    def test_override_to_premium_online(self):
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode=ExecutionMode.PREMIUM_ONLINE.value,
        )
        res = resolve_effective_routing(req)
        assert res.resolved_mode == ExecutionMode.PREMIUM_ONLINE.value
        assert res.candidate_order == [Provider.BEDROCK_TITAN_NOVA_PRO.value]
        assert res.route_resolution == RouteResolutionStatus.OVERRIDE_APPLIED.value


class TestResolveEffectiveRoutingPreferredProvider:
    """preferred_provider reorders candidate list."""

    def test_preferred_reorders(self):
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            preferred_provider=Provider.NETWORK_MODEL_MACHINE.value,
        )
        res = resolve_effective_routing(req)
        assert res.candidate_order[0] == Provider.NETWORK_MODEL_MACHINE.value
        assert res.override_applied is True
        assert any("preferred_provider" in n for n in res.override_notes)

    def test_preferred_no_duplicates(self):
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            preferred_provider=Provider.LOCALHOST_LLM.value,
        )
        res = resolve_effective_routing(req)
        # localhost is already first — should appear exactly once.
        assert res.candidate_order.count(Provider.LOCALHOST_LLM.value) == 1

    def test_invalid_preferred_provider_ignored(self):
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            preferred_provider="fake_provider",
        )
        res = resolve_effective_routing(req)
        # Order unchanged.
        assert res.candidate_order[0] == Provider.LOCALHOST_LLM.value
        assert any("invalid" in n.lower() for n in res.override_notes)

    def test_preferred_with_override_mode(self):
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode=ExecutionMode.ONLINE_DISTRIBUTED.value,
            preferred_provider=Provider.BEDROCK_TITAN_NOVA_PRO.value,
        )
        res = resolve_effective_routing(req)
        assert res.resolved_mode == ExecutionMode.ONLINE_DISTRIBUTED.value
        assert res.candidate_order[0] == Provider.BEDROCK_TITAN_NOVA_PRO.value
        assert res.override_applied is True


class TestResolveEffectiveRoutingPremiumOverride:
    """premium_override elevates to premium path."""

    def test_premium_override_forces_premium(self):
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            premium_override=True,
        )
        res = resolve_effective_routing(req)
        assert res.resolved_mode == ExecutionMode.PREMIUM_ONLINE.value
        assert res.override_applied is True
        assert any("premium_override" in n for n in res.override_notes)

    def test_premium_override_beats_override_mode(self):
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode=ExecutionMode.MODEL_MACHINE.value,
            premium_override=True,
        )
        res = resolve_effective_routing(req)
        # premium_override has highest precedence.
        assert res.resolved_mode == ExecutionMode.PREMIUM_ONLINE.value

    def test_premium_override_maps_to_bedrock(self):
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            premium_override=True,
        )
        res = resolve_effective_routing(req)
        assert res.candidate_order == [Provider.BEDROCK_TITAN_NOVA_PRO.value]
        assert res.route_resolution == RouteResolutionStatus.OVERRIDE_APPLIED.value


class TestResolveEffectiveRoutingInvalidBaseMode:
    """Invalid base mode produces INVALID_MODE resolution."""

    def test_invalid_base_mode(self):
        req = _request(mode="bad_mode")
        res = resolve_effective_routing(req)
        assert res.route_resolution == RouteResolutionStatus.INVALID_MODE.value
        assert res.candidate_order == []


# =========================================================================
# Part B — route_and_execute with overrides
# =========================================================================


class TestOverrideModeRouting:
    """override_mode changes routing behavior end-to-end."""

    def test_override_to_local_uses_localhost_only(self):
        reg = _registry(
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
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            override_mode=ExecutionMode.LOCAL.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is not None
        assert result.success is True
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value
        assert trace.resolved_mode == ExecutionMode.LOCAL.value
        assert trace.requested_mode == ExecutionMode.LOCAL_DISTRIBUTED.value
        assert trace.route_resolution == RouteResolutionStatus.OVERRIDE_APPLIED.value
        # model_machine should NOT have been called.
        reg.get_provider(Provider.NETWORK_MODEL_MACHINE.value).execute.assert_not_called()

    def test_override_to_model_machine_uses_model_machine_only(self):
        reg = _registry(
            probe_map={
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
            },
            execute_map={
                Provider.NETWORK_MODEL_MACHINE.value: _result(Provider.NETWORK_MODEL_MACHINE.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode=ExecutionMode.MODEL_MACHINE.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
        assert trace.resolved_mode == ExecutionMode.MODEL_MACHINE.value

    def test_override_to_premium_online_traces_bedrock_skip(self):
        reg = ProviderRegistry()
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode=ExecutionMode.PREMIUM_ONLINE.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is None
        assert trace.resolved_mode == ExecutionMode.PREMIUM_ONLINE.value
        # Bedrock is the candidate but not registered in empty registry.
        assert trace.route_resolution == RouteResolutionStatus.NO_CANDIDATES.value
        # Should have override entry in decision log.
        override_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "override_resolution"
        ]
        assert len(override_entries) >= 1

    def test_override_to_distributed_allows_fallback(self):
        reg = _registry(
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
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
        assert trace.fallback_used is True

    def test_invalid_override_mode_falls_back_to_base(self):
        reg = _registry(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
            execute_map={Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value)},
        )
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode="totally_bogus",
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        # Should succeed using base mode since override is invalid.
        assert result.success is True
        assert trace.resolved_mode == ExecutionMode.LOCAL.value
        # Override notes should mention invalid override.
        override_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "override_resolution"
        ]
        assert len(override_entries) >= 1
        notes = override_entries[0].get("notes", "")
        assert "invalid" in notes.lower()


class TestPreferredProviderRouting:
    """preferred_provider reorders candidates in routing."""

    def test_preferred_provider_tried_first(self):
        reg = _registry(
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

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
        assert trace.attempted_providers[0] == Provider.NETWORK_MODEL_MACHINE.value
        assert trace.route_resolution == RouteResolutionStatus.OVERRIDE_APPLIED.value

    def test_preferred_no_duplicate_in_candidates(self):
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            preferred_provider=Provider.LOCALHOST_LLM.value,
        )
        res = resolve_effective_routing(req)
        assert res.candidate_order.count(Provider.LOCALHOST_LLM.value) == 1

    def test_invalid_preferred_provider_ignored_in_routing(self):
        reg = _registry(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
            execute_map={Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value)},
        )
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            preferred_provider="bogus_provider",
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        # Should still work — invalid preferred is ignored.
        assert result.success is True
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value
        # Check that the invalid preferred was noted in the trace.
        override_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "override_resolution"
        ]
        assert len(override_entries) >= 1
        assert "bogus_provider" in override_entries[0].get("preferred_provider", "")


class TestPremiumOverrideRouting:
    """premium_override routes to premium path."""

    def test_premium_override_forces_premium_mode(self):
        reg = ProviderRegistry()
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            premium_override=True,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is None
        assert trace.resolved_mode == ExecutionMode.PREMIUM_ONLINE.value
        # Bedrock is candidate but not registered in empty registry.
        assert trace.route_resolution == RouteResolutionStatus.NO_CANDIDATES.value
        # Should have override entry.
        override_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "override_resolution"
        ]
        assert len(override_entries) >= 1
        assert override_entries[0].get("premium_override") == "true"

    def test_premium_override_beats_override_mode(self):
        reg = ProviderRegistry()
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode=ExecutionMode.MODEL_MACHINE.value,
            premium_override=True,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert trace.resolved_mode == ExecutionMode.PREMIUM_ONLINE.value


# =========================================================================
# Part C — Overrides still respect gate and availability
# =========================================================================


class TestOverridesRespectGate:
    """Overrides do not bypass execution gate or provider status."""

    def test_override_respects_gate_capacity(self):
        reg = _registry(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
        )
        gate = ProviderExecutionGate()
        gate.acquire(Provider.LOCALHOST_LLM.value)  # Fill slot.

        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            override_mode=ExecutionMode.LOCAL.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        # Strict override to local, but localhost at capacity → fail.
        assert result is None
        assert trace.execution_status == ExecutionStatus.NOT_ATTEMPTED.value
        gate.release(Provider.LOCALHOST_LLM.value)

    def test_override_respects_provider_unavailable(self):
        reg = _registry(
            probe_map={
                Provider.LOCALHOST_LLM.value: _probe(
                    Provider.LOCALHOST_LLM.value, state=ProviderState.UNAVAILABLE.value,
                ),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            override_mode=ExecutionMode.LOCAL.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is None
        assert trace.execution_status == ExecutionStatus.NOT_ATTEMPTED.value

    def test_preferred_provider_unavailable_falls_to_next(self):
        reg = _registry(
            probe_map={
                Provider.NETWORK_MODEL_MACHINE.value: _probe(
                    Provider.NETWORK_MODEL_MACHINE.value, state=ProviderState.UNAVAILABLE.value,
                ),
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
            },
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            preferred_provider=Provider.NETWORK_MODEL_MACHINE.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        # preferred model_machine is unavailable → falls to localhost.
        assert result.success is True
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value
        assert trace.fallback_used is True

    def test_preferred_at_capacity_falls_to_next(self):
        reg = _registry(
            probe_map={
                Provider.NETWORK_MODEL_MACHINE.value: _probe(Provider.NETWORK_MODEL_MACHINE.value),
                Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value),
            },
            execute_map={
                Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value),
            },
        )
        gate = ProviderExecutionGate()
        gate.acquire(Provider.NETWORK_MODEL_MACHINE.value)  # Fill slot.

        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            preferred_provider=Provider.NETWORK_MODEL_MACHINE.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value
        assert trace.fallback_used is True
        gate.release(Provider.NETWORK_MODEL_MACHINE.value)


# =========================================================================
# Part D — Trace records override effects
# =========================================================================


class TestTraceOverrideVisibility:
    """ExecutionTrace clearly shows override effects."""

    def test_trace_shows_override_mode(self):
        reg = _registry(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
            execute_map={Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value)},
        )
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            override_mode=ExecutionMode.LOCAL.value,
        )

        _, trace = route_and_execute(req, registry=reg, gate=gate)

        assert trace.requested_mode == ExecutionMode.LOCAL_DISTRIBUTED.value
        assert trace.resolved_mode == ExecutionMode.LOCAL.value
        override_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "override_resolution"
        ]
        assert len(override_entries) == 1
        assert override_entries[0]["override_mode"] == ExecutionMode.LOCAL.value
        assert override_entries[0]["override_applied"] == "True"
        assert override_entries[0]["is_strict"] == "True"

    def test_trace_shows_preferred_provider(self):
        reg = _registry(
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

        _, trace = route_and_execute(req, registry=reg, gate=gate)

        override_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "override_resolution"
        ]
        assert len(override_entries) == 1
        assert override_entries[0]["preferred_provider"] == Provider.NETWORK_MODEL_MACHINE.value

    def test_trace_shows_premium_override(self):
        reg = ProviderRegistry()
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            premium_override=True,
        )

        _, trace = route_and_execute(req, registry=reg, gate=gate)

        override_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "override_resolution"
        ]
        assert len(override_entries) == 1
        assert override_entries[0]["premium_override"] == "true"

    def test_no_override_entry_when_no_overrides(self):
        reg = _registry(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
            execute_map={Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value)},
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL.value)

        _, trace = route_and_execute(req, registry=reg, gate=gate)

        override_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "override_resolution"
        ]
        assert len(override_entries) == 0


# =========================================================================
# Part E — No-override behavior unchanged
# =========================================================================


class TestNoOverrideBehaviorUnchanged:
    """Verify Step 4 behavior with no overrides still works identically."""

    def test_local_distributed_picks_localhost_when_healthy(self):
        reg = _registry(
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

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value
        assert trace.fallback_used is False
        assert trace.route_resolution == RouteResolutionStatus.RESOLVED.value

    def test_local_mode_single_provider(self):
        reg = _registry(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
            execute_map={Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value)},
        )
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.LOCAL.value)

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is True
        assert trace.resolved_mode == ExecutionMode.LOCAL.value

    def test_premium_online_honest_not_ready(self):
        reg = ProviderRegistry()
        gate = ProviderExecutionGate()
        req = _request(mode=ExecutionMode.PREMIUM_ONLINE.value)

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is None
        # Bedrock is the candidate but not registered in empty registry.
        assert trace.route_resolution == RouteResolutionStatus.NO_CANDIDATES.value

    def test_invalid_mode_still_rejected(self):
        reg = ProviderRegistry()
        gate = ProviderExecutionGate()
        req = _request(mode="nonexistent")

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is None
        assert trace.route_resolution == RouteResolutionStatus.INVALID_MODE.value

    def test_fallback_still_works(self):
        reg = _registry(
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

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
        assert trace.fallback_used is True


# =========================================================================
# Part F — Request builder helpers
# =========================================================================


class TestWithOverrideMode:
    """with_override_mode() helper."""

    def test_sets_override_mode(self):
        base = _request(mode=ExecutionMode.LOCAL.value)
        modified = with_override_mode(base, ExecutionMode.MODEL_MACHINE.value)
        assert modified.override_mode == ExecutionMode.MODEL_MACHINE.value
        assert modified.mode == ExecutionMode.LOCAL.value  # base unchanged

    def test_preserves_other_fields(self):
        base = _request(
            mode=ExecutionMode.LOCAL.value,
            model_name="test-model",
            premium_override=True,
            metadata={"key": "value"},
        )
        modified = with_override_mode(base, ExecutionMode.MODEL_MACHINE.value)
        assert modified.model_name == "test-model"
        assert modified.premium_override is True
        assert modified.metadata == {"key": "value"}

    def test_does_not_mutate_original(self):
        base = _request(mode=ExecutionMode.LOCAL.value)
        with_override_mode(base, ExecutionMode.MODEL_MACHINE.value)
        assert base.override_mode is None


class TestWithPreferredProvider:
    """with_preferred_provider() helper."""

    def test_sets_preferred_provider(self):
        base = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
        modified = with_preferred_provider(base, Provider.NETWORK_MODEL_MACHINE.value)
        assert modified.preferred_provider == Provider.NETWORK_MODEL_MACHINE.value

    def test_preserves_other_fields(self):
        base = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            override_mode=ExecutionMode.ONLINE_DISTRIBUTED.value,
        )
        modified = with_preferred_provider(base, Provider.LOCALHOST_LLM.value)
        assert modified.override_mode == ExecutionMode.ONLINE_DISTRIBUTED.value

    def test_does_not_mutate_original(self):
        base = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
        with_preferred_provider(base, Provider.NETWORK_MODEL_MACHINE.value)
        assert base.preferred_provider is None


class TestBuildPremiumRequest:
    """build_premium_request() helper."""

    def test_sets_premium_override(self):
        base = _request(mode=ExecutionMode.LOCAL.value)
        premium = build_premium_request(base)
        assert premium.premium_override is True
        assert premium.mode == ExecutionMode.LOCAL.value

    def test_does_not_mutate_original(self):
        base = _request(mode=ExecutionMode.LOCAL.value)
        build_premium_request(base)
        assert base.premium_override is False


# =========================================================================
# Part G — Strict override fails honestly when target unusable
# =========================================================================


class TestStrictOverrideFailsHonestly:
    """Strict direct-mode override fails without fallback."""

    def test_override_to_local_fails_when_unavailable(self):
        reg = _registry(
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
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            override_mode=ExecutionMode.LOCAL.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        # Strict override to local — cannot fall back to model_machine.
        assert result is None
        assert trace.resolved_mode == ExecutionMode.LOCAL.value
        # model_machine should NOT be attempted.
        reg.get_provider(Provider.NETWORK_MODEL_MACHINE.value).execute.assert_not_called()

    def test_override_to_model_machine_fails_when_busy(self):
        reg = _registry(
            probe_map={
                Provider.NETWORK_MODEL_MACHINE.value: _probe(
                    Provider.NETWORK_MODEL_MACHINE.value, state=ProviderState.BUSY.value,
                ),
            },
        )
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL.value,
            override_mode=ExecutionMode.MODEL_MACHINE.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is None
        assert trace.resolved_mode == ExecutionMode.MODEL_MACHINE.value


# =========================================================================
# Part H — Reservation release with overrides
# =========================================================================


class TestOverrideReservationRelease:
    """Gate slots are always released even with overrides."""

    def test_override_releases_on_success(self):
        reg = _registry(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
            execute_map={Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value)},
        )
        gate = ProviderExecutionGate()
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            override_mode=ExecutionMode.LOCAL.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is True
        assert gate.in_flight_count(Provider.LOCALHOST_LLM.value) == 0

    def test_override_releases_on_failure(self):
        reg = _registry(
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
        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            override_mode=ExecutionMode.LOCAL.value,
        )

        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is False
        assert gate.in_flight_count(Provider.LOCALHOST_LLM.value) == 0

    def test_override_no_leak_across_repeated_calls(self):
        reg = _registry(
            probe_map={Provider.LOCALHOST_LLM.value: _probe(Provider.LOCALHOST_LLM.value)},
            execute_map={Provider.LOCALHOST_LLM.value: _result(Provider.LOCALHOST_LLM.value)},
        )
        gate = ProviderExecutionGate()
        for _ in range(5):
            req = _request(
                mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
                override_mode=ExecutionMode.LOCAL.value,
            )
            route_and_execute(req, registry=reg, gate=gate)

        assert gate.in_flight_count(Provider.LOCALHOST_LLM.value) == 0
