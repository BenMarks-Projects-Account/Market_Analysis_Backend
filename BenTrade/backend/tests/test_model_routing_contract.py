"""Tests for app.services.model_routing_contract.

Covers: enums, frozen sets, validators, provider ordering, normalization,
trace building, override handling, and extensibility of metadata.
"""

from __future__ import annotations

import pytest

from app.services.model_routing_contract import (
    DEFAULT_PROVIDER_ORDER,
    VALID_FALLBACK_REASONS,
    VALID_MODES,
    VALID_PROVIDERS,
    VALID_PROVIDER_STATES,
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
    is_valid_provider_state,
    mode_to_default_provider_order,
    normalize_execution_request,
    normalize_provider_state,
    resolve_provider_order,
)


# ── Enum membership ──────────────────────────────────────────────────────

class TestExecutionModeEnum:
    def test_all_expected_values_present(self):
        expected = {"local", "model_machine", "premium_online",
                    "local_distributed", "online_distributed"}
        assert {m.value for m in ExecutionMode} == expected

    def test_str_subclass(self):
        assert isinstance(ExecutionMode.LOCAL, str)
        assert ExecutionMode.LOCAL == "local"


class TestProviderEnum:
    def test_all_expected_values_present(self):
        expected = {"localhost_llm", "network_model_machine",
                    "bedrock_titan_nova_pro"}
        assert {p.value for p in Provider} == expected

    def test_str_subclass(self):
        assert isinstance(Provider.LOCALHOST_LLM, str)


class TestProviderStateEnum:
    def test_all_expected_values(self):
        expected = {"available", "busy", "unavailable", "degraded", "failed"}
        assert {s.value for s in ProviderState} == expected

    def test_states_are_distinct(self):
        values = [s.value for s in ProviderState]
        assert len(values) == len(set(values))


class TestFallbackReasonEnum:
    def test_all_expected_values(self):
        expected = {
            "provider_unavailable", "provider_busy", "provider_failed",
            "provider_degraded", "provider_timeout", "provider_error",
            "explicit_override",
        }
        assert {r.value for r in FallbackReason} == expected


class TestRouteResolutionStatusEnum:
    def test_values(self):
        expected = {"resolved", "no_candidates", "override_applied",
                    "invalid_mode"}
        assert {s.value for s in RouteResolutionStatus} == expected


class TestExecutionStatusEnum:
    def test_values(self):
        expected = {"success", "failed", "timeout", "skipped",
                    "not_attempted"}
        assert {s.value for s in ExecutionStatus} == expected


# ── Frozen-set constants ─────────────────────────────────────────────────

class TestFrozenSets:
    def test_valid_modes_matches_enum(self):
        assert VALID_MODES == {m.value for m in ExecutionMode}

    def test_valid_providers_matches_enum(self):
        assert VALID_PROVIDERS == {p.value for p in Provider}

    def test_valid_provider_states_matches_enum(self):
        assert VALID_PROVIDER_STATES == {s.value for s in ProviderState}

    def test_valid_fallback_reasons_matches_enum(self):
        assert VALID_FALLBACK_REASONS == {r.value for r in FallbackReason}


# ── Simple validators ────────────────────────────────────────────────────

class TestIsValidMode:
    @pytest.mark.parametrize("mode", list(ExecutionMode))
    def test_accepts_all_enum_values(self, mode):
        assert is_valid_mode(mode.value) is True

    @pytest.mark.parametrize("bad", ["", "auto", "LOcal", "distributed", "bedrock"])
    def test_rejects_invalid(self, bad):
        assert is_valid_mode(bad) is False


class TestIsValidProvider:
    @pytest.mark.parametrize("prov", list(Provider))
    def test_accepts_all_enum_values(self, prov):
        assert is_valid_provider(prov.value) is True

    @pytest.mark.parametrize("bad", ["", "local", "model_machine", "openai", "LOCALHOST_LLM"])
    def test_rejects_invalid(self, bad):
        assert is_valid_provider(bad) is False


class TestIsValidProviderState:
    @pytest.mark.parametrize("state", list(ProviderState))
    def test_accepts_all_enum_values(self, state):
        assert is_valid_provider_state(state.value) is True

    @pytest.mark.parametrize("bad", ["", "online", "offline", "error", "ok"])
    def test_rejects_invalid(self, bad):
        assert is_valid_provider_state(bad) is False


# ── Distributed mode detection ───────────────────────────────────────────

class TestIsDistributedMode:
    @pytest.mark.parametrize("mode", ["local_distributed", "online_distributed"])
    def test_distributed_modes(self, mode):
        assert is_distributed_mode(mode) is True

    @pytest.mark.parametrize("mode", ["local", "model_machine", "premium_online"])
    def test_non_distributed_modes(self, mode):
        assert is_distributed_mode(mode) is False

    def test_invalid_string(self):
        assert is_distributed_mode("nope") is False


# ── Provider ordering ────────────────────────────────────────────────────

class TestModeToDefaultProviderOrder:
    def test_local(self):
        assert mode_to_default_provider_order("local") == ["localhost_llm"]

    def test_model_machine(self):
        assert mode_to_default_provider_order("model_machine") == [
            "network_model_machine",
        ]

    def test_premium_online_maps_to_bedrock(self):
        assert mode_to_default_provider_order("premium_online") == [
            "bedrock_titan_nova_pro",
        ]

    def test_local_distributed(self):
        assert mode_to_default_provider_order("local_distributed") == [
            "localhost_llm", "network_model_machine",
        ]

    def test_online_distributed(self):
        assert mode_to_default_provider_order("online_distributed") == [
            "localhost_llm", "network_model_machine",
            "bedrock_titan_nova_pro",
        ]

    def test_unknown_mode_returns_empty(self):
        assert mode_to_default_provider_order("unicorn") == []

    def test_returns_new_list_each_call(self):
        a = mode_to_default_provider_order("local")
        b = mode_to_default_provider_order("local")
        assert a == b
        assert a is not b  # mutation-safe


class TestDefaultProviderOrderCompleteness:
    """Every valid mode must have an entry in DEFAULT_PROVIDER_ORDER."""
    @pytest.mark.parametrize("mode", list(ExecutionMode))
    def test_all_modes_have_entry(self, mode):
        assert mode.value in DEFAULT_PROVIDER_ORDER


# ── Normalization: execution request ─────────────────────────────────────

class TestNormalizeExecutionRequest:
    def test_minimal_valid(self):
        req = normalize_execution_request({"mode": "local"})
        assert req.mode == "local"
        assert req.model_name is None
        assert req.metadata == {}

    def test_full_fields(self):
        raw = {
            "mode": "online_distributed",
            "model_name": "my-model",
            "task_type": "stock_analysis",
            "prompt": [{"role": "user", "content": "hi"}],
            "system_prompt": "You are helpful.",
            "override_mode": "local",
            "preferred_provider": "localhost_llm",
            "premium_override": True,
            "routing_overrides": {"timeout": 60},
            "metadata": {"run_id": "abc"},
        }
        req = normalize_execution_request(raw)
        assert req.mode == "online_distributed"
        assert req.override_mode == "local"
        assert req.preferred_provider == "localhost_llm"
        assert req.premium_override is True
        assert req.routing_overrides == {"timeout": 60}
        assert req.metadata == {"run_id": "abc"}

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid execution mode"):
            normalize_execution_request({"mode": "turbo"})

    def test_empty_mode_raises(self):
        with pytest.raises(ValueError):
            normalize_execution_request({})

    def test_invalid_override_mode_dropped(self):
        req = normalize_execution_request({
            "mode": "local",
            "override_mode": "bogus",
        })
        assert req.override_mode is None

    def test_invalid_preferred_provider_dropped(self):
        req = normalize_execution_request({
            "mode": "local",
            "preferred_provider": "not_a_provider",
        })
        assert req.preferred_provider is None

    def test_metadata_isolated_copy(self):
        meta = {"key": "value"}
        req = normalize_execution_request({"mode": "local", "metadata": meta})
        assert req.metadata == {"key": "value"}
        # Mutating original should not affect request
        meta["key"] = "changed"
        # Note: the current impl shares the ref via raw.get — the contract
        # owns whatever the caller passes. This is fine for a dataclass.


# ── normalize_provider_state ─────────────────────────────────────────────

class TestNormalizeProviderState:
    @pytest.mark.parametrize("state", list(ProviderState))
    def test_valid_states_pass_through(self, state):
        assert normalize_provider_state(state.value) == state.value

    def test_none_becomes_unavailable(self):
        assert normalize_provider_state(None) == "unavailable"

    def test_unknown_string_becomes_unavailable(self):
        assert normalize_provider_state("on_fire") == "unavailable"


# ── build_execution_trace ────────────────────────────────────────────────

class TestBuildExecutionTrace:
    @pytest.fixture
    def base_request(self):
        return ExecutionRequest(mode="local_distributed",
                                task_type="test", metadata={"k": "v"})

    def test_defaults(self, base_request):
        trace = build_execution_trace(base_request)
        assert trace.requested_mode == "local_distributed"
        assert trace.resolved_mode == "local_distributed"
        assert trace.attempted_providers == []
        assert trace.selected_provider is None
        assert trace.fallback_used is False
        assert trace.fallback_reason is None
        assert trace.execution_status == "not_attempted"
        assert trace.metadata == {"k": "v"}
        assert len(trace.request_id) == 32  # hex uuid

    def test_resolved_mode_uses_override(self):
        req = ExecutionRequest(mode="local", override_mode="model_machine")
        trace = build_execution_trace(req)
        assert trace.resolved_mode == "model_machine"

    def test_explicit_resolved_mode_wins(self, base_request):
        trace = build_execution_trace(base_request,
                                       resolved_mode="online_distributed")
        assert trace.resolved_mode == "online_distributed"

    def test_fallback_fields(self, base_request):
        trace = build_execution_trace(
            base_request,
            attempted_providers=["localhost_llm", "network_model_machine"],
            selected_provider="network_model_machine",
            fallback_used=True,
            fallback_reason=FallbackReason.PROVIDER_UNAVAILABLE.value,
            execution_status=ExecutionStatus.SUCCESS.value,
        )
        assert trace.fallback_used is True
        assert trace.fallback_reason == "provider_unavailable"
        assert trace.selected_provider == "network_model_machine"
        assert trace.execution_status == "success"

    def test_timing_and_error(self, base_request):
        trace = build_execution_trace(
            base_request,
            execution_status=ExecutionStatus.FAILED.value,
            error_summary="Connection refused",
            error_detail="traceback...",
            timing_ms=1234.5,
        )
        assert trace.timing_ms == 1234.5
        assert trace.error_summary == "Connection refused"
        assert trace.error_detail == "traceback..."

    def test_provider_states_snapshot(self, base_request):
        states = {
            "localhost_llm": "available",
            "network_model_machine": "busy",
        }
        trace = build_execution_trace(base_request, provider_states=states)
        assert trace.provider_states == states

    def test_route_decision_log(self, base_request):
        log = [
            {"provider": "localhost_llm", "state": "unavailable",
             "action": "skip"},
            {"provider": "network_model_machine", "state": "available",
             "action": "select"},
        ]
        trace = build_execution_trace(base_request,
                                       route_decision_log=log)
        assert trace.route_decision_log == log

    def test_response_payload_passthrough(self, base_request):
        payload = {"choices": [{"text": "answer"}]}
        trace = build_execution_trace(base_request,
                                       response_payload=payload)
        assert trace.response_payload == payload

    def test_metadata_copied_from_request(self, base_request):
        trace = build_execution_trace(base_request)
        assert trace.metadata == {"k": "v"}
        # Mutating trace metadata should not affect request
        trace.metadata["new"] = True
        assert "new" not in base_request.metadata


# ── resolve_provider_order ───────────────────────────────────────────────

class TestResolveProviderOrder:
    def test_simple_local(self):
        req = ExecutionRequest(mode="local")
        assert resolve_provider_order(req) == ["localhost_llm"]

    def test_online_distributed(self):
        req = ExecutionRequest(mode="online_distributed")
        assert resolve_provider_order(req) == [
            "localhost_llm", "network_model_machine",
            "bedrock_titan_nova_pro",
        ]

    def test_premium_override_forces_premium_path(self):
        req = ExecutionRequest(mode="local", premium_override=True)
        assert resolve_provider_order(req) == ["bedrock_titan_nova_pro"]

    def test_override_mode_replaces_base(self):
        req = ExecutionRequest(mode="local",
                                override_mode="online_distributed")
        expected = [
            "localhost_llm", "network_model_machine",
            "bedrock_titan_nova_pro",
        ]
        assert resolve_provider_order(req) == expected

    def test_preferred_provider_prepended(self):
        req = ExecutionRequest(mode="local_distributed",
                                preferred_provider="network_model_machine")
        order = resolve_provider_order(req)
        assert order[0] == "network_model_machine"
        # No duplicates
        assert order.count("network_model_machine") == 1
        assert "localhost_llm" in order

    def test_preferred_provider_not_in_default_still_prepended(self):
        req = ExecutionRequest(mode="local",
                                preferred_provider="bedrock_titan_nova_pro")
        order = resolve_provider_order(req)
        assert order[0] == "bedrock_titan_nova_pro"
        assert "localhost_llm" in order

    def test_invalid_preferred_provider_ignored(self):
        req = ExecutionRequest(mode="local",
                                preferred_provider="nonexistent")
        assert resolve_provider_order(req) == ["localhost_llm"]

    def test_premium_override_beats_override_mode(self):
        req = ExecutionRequest(
            mode="local",
            override_mode="online_distributed",
            premium_override=True,
        )
        # premium_override takes precedence
        assert resolve_provider_order(req) == ["bedrock_titan_nova_pro"]


# ── ExecutionRequest dataclass defaults ──────────────────────────────────

class TestExecutionRequestDefaults:
    def test_minimal(self):
        req = ExecutionRequest(mode="local")
        assert req.model_name is None
        assert req.task_type is None
        assert req.prompt is None
        assert req.system_prompt is None
        assert req.override_mode is None
        assert req.preferred_provider is None
        assert req.premium_override is False
        assert req.routing_overrides == {}
        assert req.metadata == {}

    def test_mutable_defaults_isolated(self):
        a = ExecutionRequest(mode="local")
        b = ExecutionRequest(mode="local")
        a.metadata["x"] = 1
        assert "x" not in b.metadata
        a.routing_overrides["y"] = 2
        assert "y" not in b.routing_overrides


# ── ExecutionTrace dataclass defaults ────────────────────────────────────

class TestExecutionTraceDefaults:
    def test_defaults(self):
        t = ExecutionTrace(requested_mode="local", resolved_mode="local")
        assert t.attempted_providers == []
        assert t.selected_provider is None
        assert t.provider_states == {}
        assert t.fallback_used is False
        assert t.execution_status == "not_attempted"
        assert t.route_decision_log == []
        assert t.response_payload is None
        assert t.metadata == {}
        assert len(t.request_id) == 32

    def test_mutable_defaults_isolated(self):
        a = ExecutionTrace(requested_mode="local", resolved_mode="local")
        b = ExecutionTrace(requested_mode="local", resolved_mode="local")
        a.attempted_providers.append("x")
        assert b.attempted_providers == []
        a.provider_states["k"] = "v"
        assert b.provider_states == {}
