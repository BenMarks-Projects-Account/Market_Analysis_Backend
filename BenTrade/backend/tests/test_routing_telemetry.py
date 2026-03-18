"""Step 7 — Routing telemetry, logs, and traceability tests.

Validates:
    A. Telemetry module helpers (summarize_request, build_override_inputs, etc.)
    B. ExecutionTrace new fields populated correctly
    C. Skip/fallback reason consistency and stability
    D. Gate telemetry events (acquire, deny)
    E. Provider attribution metadata
    F. Direct vs distributed mode telemetry differences
    G. Bedrock attribution specifics
    H. Failed route traces (all skipped, non-retryable)
    I. No sensitive data in telemetry (prompt content safety)
    J. Structured log event emitters
    K. trace_to_summary output shape

Total: ~65 tests across 11 sections.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from app.services.model_execution_gate import (
    GateSnapshot,
    ProviderExecutionGate,
)
from app.services.model_provider_base import ProbeResult, ProviderResult
from app.services.model_provider_registry import ProviderRegistry
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
    summarize_request,
    trace_to_summary,
)
from app.services.model_router_policy import (
    SkipReason,
    route_and_execute,
)


# ── Helpers ────────────────────────────────────────────────────

def _make_request(**kwargs) -> ExecutionRequest:
    defaults = {
        "mode": ExecutionMode.LOCAL.value,
        "model_name": "test-model",
        "task_type": "unit_test",
        "prompt": [{"role": "user", "content": "hello"}],
        "system_prompt": "You are a test.",
    }
    defaults.update(kwargs)
    return ExecutionRequest(**defaults)


def _make_probe(provider_id: str, state: str = ProviderState.AVAILABLE.value,
                configured: bool = True, **meta) -> ProbeResult:
    return ProbeResult(
        provider=provider_id,
        configured=configured,
        state=state,
        probe_success=True,
        status_reason="test",
        metadata=meta,
    )


def _make_result(provider_id: str, success: bool = True, **kwargs) -> ProviderResult:
    defaults = {
        "provider": provider_id,
        "success": success,
        "execution_status": ExecutionStatus.SUCCESS.value if success else ExecutionStatus.FAILED.value,
        "content": "test response" if success else None,
        "timing_ms": 42.5,
    }
    defaults.update(kwargs)
    return ProviderResult(**defaults)


def _build_registry_and_gate(
    providers: dict[str, tuple[ProbeResult, ProviderResult | None]],
) -> tuple[ProviderRegistry, ProviderExecutionGate]:
    """Create a registry + gate for test scenarios."""
    registry = ProviderRegistry()
    gate = ProviderExecutionGate()

    for pid, (probe, result) in providers.items():
        adapter = MagicMock()
        adapter.provider_id = pid
        adapter.probe.return_value = probe
        adapter.is_configured = probe.configured
        if result is not None:
            adapter.execute.return_value = result
        registry.register(adapter)

    return registry, gate


# ═══════════════════════════════════════════════════════════════
# A. Telemetry module helpers
# ═══════════════════════════════════════════════════════════════


class TestSummarizeRequest:
    """summarize_request — safe metadata extraction."""

    def test_basic_summary(self):
        req = _make_request()
        s = summarize_request(req)
        assert s["mode"] == "local"
        assert s["task_type"] == "unit_test"
        assert s["model_name"] == "test-model"
        assert s["prompt_summary"]["message_count"] == 1
        assert s["prompt_summary"]["has_system_prompt"] is True

    def test_no_prompt(self):
        req = _make_request(prompt=None, system_prompt=None)
        s = summarize_request(req)
        assert s["prompt_summary"] is None

    def test_override_inputs_captured(self):
        req = _make_request(
            override_mode="model_machine",
            preferred_provider="localhost_llm",
            premium_override=True,
        )
        s = summarize_request(req)
        oi = s["override_inputs"]
        assert oi["override_mode"] == "model_machine"
        assert oi["preferred_provider"] == "localhost_llm"
        assert oi["premium_override"] is True

    def test_no_overrides_returns_none(self):
        req = _make_request()
        s = summarize_request(req)
        assert s["override_inputs"] is None

    def test_prompt_char_count(self):
        req = _make_request(prompt=[
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi"},
        ])
        s = summarize_request(req)
        assert s["prompt_summary"]["approx_chars"] == 13  # 11 + 2


class TestBuildOverrideInputs:
    """build_override_inputs — trace attachment."""

    def test_no_overrides(self):
        req = _make_request()
        assert build_override_inputs(req) == {}

    def test_override_mode(self):
        req = _make_request(override_mode="model_machine")
        result = build_override_inputs(req)
        assert result == {"override_mode": "model_machine"}

    def test_premium_override(self):
        req = _make_request(premium_override=True)
        result = build_override_inputs(req)
        assert result == {"premium_override": True}

    def test_all_overrides(self):
        req = _make_request(
            override_mode="local_distributed",
            preferred_provider="localhost_llm",
            premium_override=True,
        )
        result = build_override_inputs(req)
        assert "override_mode" in result
        assert "preferred_provider" in result
        assert "premium_override" in result


class TestBuildProviderAttribution:
    """build_provider_attribution — selected provider metadata."""

    def test_no_provider(self):
        attr = build_provider_attribution(None, ["a", "b"])
        assert attr["provider"] is None
        assert attr["reason"] == "no_provider_selected"

    def test_first_candidate(self):
        attr = build_provider_attribution("a", ["a", "b"])
        assert attr["provider"] == "a"
        assert attr["route_position"] == 0
        assert attr["is_fallback"] is False

    def test_fallback_provider(self):
        attr = build_provider_attribution("b", ["a", "b"], fallback_used=True)
        assert attr["provider"] == "b"
        assert attr["route_position"] == 1
        assert attr["is_fallback"] is True

    def test_with_state_and_probe(self):
        attr = build_provider_attribution(
            "a", ["a"], provider_state="available", probe_type="config_only",
        )
        assert attr["provider_state"] == "available"
        assert attr["probe_type"] == "config_only"

    def test_unknown_provider(self):
        attr = build_provider_attribution("x", ["a", "b"])
        assert attr["route_position"] == -1


class TestBuildSkipSummary:
    """build_skip_summary — counts from decision log."""

    def test_empty_log(self):
        assert build_skip_summary([]) == {}

    def test_counts_skips(self):
        log = [
            {"action": "skipped", "reason": "provider_busy"},
            {"action": "dispatched"},
            {"action": "skipped", "reason": "provider_busy"},
            {"action": "skipped", "reason": "provider_unavailable"},
        ]
        result = build_skip_summary(log)
        assert result == {"provider_busy": 2, "provider_unavailable": 1}

    def test_ignores_non_skip(self):
        log = [{"action": "success"}, {"action": "dispatched"}]
        assert build_skip_summary(log) == {}


class TestBuildGateOutcomes:
    """build_gate_outcomes — gate events from decision log."""

    def test_empty(self):
        assert build_gate_outcomes([]) == []

    def test_denied(self):
        log = [
            {"provider": "a", "action": "skipped", "reason": "at_max_concurrency",
             "gate_in_flight": "1", "gate_max": "1"},
        ]
        result = build_gate_outcomes(log)
        assert len(result) == 1
        assert result[0]["outcome"] == "denied"

    def test_acquired(self):
        log = [{"provider": "a", "action": "dispatched"}]
        result = build_gate_outcomes(log)
        assert len(result) == 1
        assert result[0]["outcome"] == "acquired"

    def test_slot_denied(self):
        log = [
            {"provider": "a", "action": "skipped", "reason": "slot_acquisition_failed"},
        ]
        result = build_gate_outcomes(log)
        assert result[0]["outcome"] == "denied"


# ═══════════════════════════════════════════════════════════════
# B. ExecutionTrace new fields populated via route_and_execute
# ═══════════════════════════════════════════════════════════════


class TestTraceNewFields:
    """Verify new telemetry fields are populated in traces."""

    def test_trace_has_new_fields(self):
        """Check that ExecutionTrace has all Step 7 fields."""
        trace = ExecutionTrace(requested_mode="local", resolved_mode="local")
        assert hasattr(trace, "override_inputs")
        assert hasattr(trace, "resolved_candidate_order")
        assert hasattr(trace, "gate_outcomes")
        assert hasattr(trace, "provider_attribution")
        assert hasattr(trace, "is_direct_mode")
        assert hasattr(trace, "task_type")
        assert hasattr(trace, "skip_summary")

    def test_build_trace_populates_new_fields(self):
        req = _make_request()
        trace = build_execution_trace(
            req,
            override_inputs={"override_mode": "x"},
            resolved_candidate_order=["a", "b"],
            gate_outcomes=[{"provider": "a", "outcome": "acquired"}],
            provider_attribution={"provider": "a", "route_position": 0},
            is_direct_mode=False,
            skip_summary={"busy": 1},
        )
        assert trace.override_inputs == {"override_mode": "x"}
        assert trace.resolved_candidate_order == ["a", "b"]
        assert len(trace.gate_outcomes) == 1
        assert trace.provider_attribution["provider"] == "a"
        assert trace.is_direct_mode is False
        assert trace.task_type == "unit_test"
        assert trace.skip_summary == {"busy": 1}

    def test_defaults_for_new_fields(self):
        req = _make_request()
        trace = build_execution_trace(req)
        assert trace.override_inputs == {}
        assert trace.resolved_candidate_order == []
        assert trace.gate_outcomes == []
        assert trace.provider_attribution == {}
        assert trace.is_direct_mode is True
        assert trace.task_type == "unit_test"
        assert trace.skip_summary == {}


# ═══════════════════════════════════════════════════════════════
# C. Skip/fallback reason stability
# ═══════════════════════════════════════════════════════════════


class TestSkipReasonStability:
    """SkipReason constants must be stable — never renamed."""

    EXPECTED_REASONS = {
        "not_registered",
        "not_configured",
        "provider_unavailable",
        "provider_failed",
        "provider_busy",
        "at_max_concurrency",
        "slot_acquisition_failed",
    }

    def test_all_expected_reasons_exist(self):
        for reason in self.EXPECTED_REASONS:
            # Find the constant by value.
            found = any(
                getattr(SkipReason, attr) == reason
                for attr in dir(SkipReason)
                if not attr.startswith("_")
            )
            assert found, f"SkipReason missing: {reason}"

    def test_reason_values_unchanged(self):
        assert SkipReason.NOT_REGISTERED == "not_registered"
        assert SkipReason.NOT_CONFIGURED == "not_configured"
        assert SkipReason.UNAVAILABLE == "provider_unavailable"
        assert SkipReason.FAILED == "provider_failed"
        assert SkipReason.BUSY == "provider_busy"
        assert SkipReason.AT_CAPACITY == "at_max_concurrency"
        assert SkipReason.SLOT_DENIED == "slot_acquisition_failed"


# ═══════════════════════════════════════════════════════════════
# D. Gate telemetry in route_and_execute
# ═══════════════════════════════════════════════════════════════


class TestGateTelemetryInTrace:
    """Gate outcomes appear in traces from route_and_execute."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_gate_acquired_in_trace(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        req = _make_request(mode=ExecutionMode.LOCAL.value)
        _, trace = route_and_execute(req, registry=registry, gate=gate)

        assert trace.execution_status == "success"
        assert len(trace.gate_outcomes) >= 1
        acquired = [g for g in trace.gate_outcomes if g["outcome"] == "acquired"]
        assert len(acquired) == 1
        assert acquired[0]["provider"] == pid

    @patch("app.services.model_provider_adapters.get_settings")
    def test_gate_denied_in_trace(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})
        # Fill the gate to capacity.
        gate.acquire(pid)

        req = _make_request(mode=ExecutionMode.LOCAL.value)
        _, trace = route_and_execute(req, registry=registry, gate=gate)

        # Provider was at capacity — skip_summary should have the reason.
        assert trace.skip_summary.get("at_max_concurrency", 0) >= 1
        gate.release(pid)


# ═══════════════════════════════════════════════════════════════
# E. Provider attribution in route_and_execute
# ═══════════════════════════════════════════════════════════════


class TestProviderAttributionInTrace:
    """provider_attribution populated by route_and_execute."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_direct_mode_attribution(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        req = _make_request(mode=ExecutionMode.LOCAL.value)
        _, trace = route_and_execute(req, registry=registry, gate=gate)

        assert trace.provider_attribution["provider"] == pid
        assert trace.provider_attribution["route_position"] == 0
        assert trace.provider_attribution["is_fallback"] is False

    @patch("app.services.model_provider_adapters.get_settings")
    def test_fallback_attribution(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid1 = Provider.LOCALHOST_LLM.value
        pid2 = Provider.NETWORK_MODEL_MACHINE.value
        probe1 = _make_probe(pid1, state=ProviderState.UNAVAILABLE.value)
        probe2 = _make_probe(pid2)
        result2 = _make_result(pid2)
        registry, gate = _build_registry_and_gate({
            pid1: (probe1, None),
            pid2: (probe2, result2),
        })

        req = _make_request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
        _, trace = route_and_execute(req, registry=registry, gate=gate)

        assert trace.provider_attribution["provider"] == pid2
        assert trace.provider_attribution["route_position"] == 1
        assert trace.provider_attribution["is_fallback"] is True

    @patch("app.services.model_provider_adapters.get_settings")
    def test_no_provider_attribution(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid, state=ProviderState.UNAVAILABLE.value)
        registry, gate = _build_registry_and_gate({pid: (probe, None)})

        req = _make_request(mode=ExecutionMode.LOCAL.value)
        _, trace = route_and_execute(req, registry=registry, gate=gate)

        assert trace.provider_attribution["provider"] is None


# ═══════════════════════════════════════════════════════════════
# F. Direct vs distributed telemetry differences
# ═══════════════════════════════════════════════════════════════


class TestDirectVsDistributed:
    """is_direct_mode reflects routing topology."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_local_is_direct(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        req = _make_request(mode=ExecutionMode.LOCAL.value)
        _, trace = route_and_execute(req, registry=registry, gate=gate)
        assert trace.is_direct_mode is True

    @patch("app.services.model_provider_adapters.get_settings")
    def test_distributed_is_not_direct(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid1 = Provider.LOCALHOST_LLM.value
        pid2 = Provider.NETWORK_MODEL_MACHINE.value
        probe1 = _make_probe(pid1)
        result1 = _make_result(pid1)
        probe2 = _make_probe(pid2)
        result2 = _make_result(pid2)
        registry, gate = _build_registry_and_gate({
            pid1: (probe1, result1),
            pid2: (probe2, result2),
        })

        req = _make_request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
        _, trace = route_and_execute(req, registry=registry, gate=gate)
        assert trace.is_direct_mode is False

    @patch("app.services.model_provider_adapters.get_settings")
    def test_override_to_direct_sets_flag(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        req = _make_request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            override_mode=ExecutionMode.LOCAL.value,
        )
        _, trace = route_and_execute(req, registry=registry, gate=gate)
        assert trace.is_direct_mode is True


# ═══════════════════════════════════════════════════════════════
# G. Bedrock attribution
# ═══════════════════════════════════════════════════════════════


class TestBedrockAttribution:
    """Bedrock-specific attribution metadata."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_bedrock_probe_type_in_attribution(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.BEDROCK_TITAN_NOVA_PRO.value
        probe = _make_probe(pid, probe_type="config_only")
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        req = _make_request(mode=ExecutionMode.PREMIUM_ONLINE.value)
        _, trace = route_and_execute(req, registry=registry, gate=gate)

        assert trace.provider_attribution["probe_type"] == "config_only"


# ═══════════════════════════════════════════════════════════════
# H. Failed route traces
# ═══════════════════════════════════════════════════════════════


class TestFailedRouteTraces:
    """Telemetry completeness when routing fails."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_all_providers_skipped(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid1 = Provider.LOCALHOST_LLM.value
        pid2 = Provider.NETWORK_MODEL_MACHINE.value
        probe1 = _make_probe(pid1, state=ProviderState.UNAVAILABLE.value)
        probe2 = _make_probe(pid2, state=ProviderState.FAILED.value)
        registry, gate = _build_registry_and_gate({
            pid1: (probe1, None),
            pid2: (probe2, None),
        })

        req = _make_request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
        result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result is None
        assert trace.execution_status == ExecutionStatus.NOT_ATTEMPTED.value
        assert trace.selected_provider is None
        assert len(trace.skip_summary) > 0
        assert trace.provider_attribution["provider"] is None
        assert len(trace.resolved_candidate_order) == 2

    def test_invalid_mode_trace(self):
        req = _make_request(mode="invalid_mode_xyz")
        result, trace = route_and_execute(req)

        assert result is None
        assert trace.execution_status == ExecutionStatus.NOT_ATTEMPTED.value
        assert trace.override_inputs == {}
        assert trace.resolved_candidate_order == []
        assert trace.is_direct_mode is True

    @patch("app.services.model_provider_adapters.get_settings")
    def test_non_retryable_failure_trace(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(
            pid, success=False,
            error_code="content_filter",
            error_message="content blocked",
        )
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        req = _make_request(mode=ExecutionMode.LOCAL.value)
        _, trace = route_and_execute(req, registry=registry, gate=gate)

        assert trace.execution_status != ExecutionStatus.SUCCESS.value
        assert trace.provider_attribution["provider"] == pid
        assert len(trace.attempted_providers) == 1


# ═══════════════════════════════════════════════════════════════
# I. No sensitive data in telemetry
# ═══════════════════════════════════════════════════════════════


class TestNoSensitiveData:
    """Verify telemetry doesn't leak prompt content."""

    def test_summarize_request_no_prompt_content(self):
        req = _make_request(
            prompt=[{"role": "user", "content": "secret password 12345"}],
            system_prompt="top secret instructions",
        )
        s = summarize_request(req)
        # Must not contain the actual content.
        s_str = str(s)
        assert "secret password 12345" not in s_str
        assert "top secret instructions" not in s_str
        # Must contain shape info.
        assert s["prompt_summary"]["message_count"] == 1
        assert s["prompt_summary"]["has_system_prompt"] is True

    def test_trace_summary_no_response_payload(self):
        req = _make_request()
        trace = build_execution_trace(
            req,
            response_payload={"secret": "data"},
        )
        summary = trace_to_summary(trace)
        assert "response_payload" not in summary
        assert "secret" not in str(summary)

    @patch("app.services.model_provider_adapters.get_settings")
    def test_override_inputs_no_prompt(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        req = _make_request(
            mode=ExecutionMode.LOCAL.value,
            prompt=[{"role": "user", "content": "sensitive data"}],
        )
        _, trace = route_and_execute(req, registry=registry, gate=gate)

        # override_inputs should NOT contain prompt content.
        oi_str = str(trace.override_inputs)
        assert "sensitive data" not in oi_str


# ═══════════════════════════════════════════════════════════════
# J. Structured log event emitters
# ═══════════════════════════════════════════════════════════════


class TestLogEventEmitters:
    """Verify emitters log at the right levels without errors."""

    def test_emit_route_started(self, caplog):
        req = _make_request()
        with caplog.at_level(logging.DEBUG):
            emit_route_started("req1", req, "local", ["localhost_llm"], False)
        assert "route:start" in caplog.text

    def test_emit_provider_skipped(self, caplog):
        with caplog.at_level(logging.INFO):
            emit_provider_skipped("req1", "a", "busy", state="busy")
        assert "route:skip" in caplog.text

    def test_emit_gate_acquired(self, caplog):
        with caplog.at_level(logging.INFO):
            emit_gate_acquired("req1", "a")
        assert "gate:acquired" in caplog.text

    def test_emit_gate_denied(self, caplog):
        with caplog.at_level(logging.WARNING):
            emit_gate_denied("req1", "a", in_flight=1, max_concurrency=1)
        assert "gate:denied" in caplog.text

    def test_emit_provider_dispatched(self, caplog):
        with caplog.at_level(logging.INFO):
            emit_provider_dispatched("req1", "a", "available")
        assert "route:dispatch" in caplog.text

    def test_emit_provider_success(self, caplog):
        with caplog.at_level(logging.INFO):
            emit_provider_success("req1", "a", timing_ms=42.5)
        assert "route:success" in caplog.text
        assert "42.5" in caplog.text

    def test_emit_provider_failed(self, caplog):
        with caplog.at_level(logging.WARNING):
            emit_provider_failed("req1", "a", "timeout", retryable=True)
        assert "route:failed" in caplog.text

    def test_emit_route_completed_success(self, caplog):
        trace = ExecutionTrace(
            requested_mode="local",
            resolved_mode="local",
            execution_status="success",
            selected_provider="a",
        )
        with caplog.at_level(logging.INFO):
            emit_route_completed("req1", trace)
        assert "route:complete" in caplog.text

    def test_emit_route_completed_failure(self, caplog):
        trace = ExecutionTrace(
            requested_mode="local",
            resolved_mode="local",
            execution_status="failed",
        )
        with caplog.at_level(logging.WARNING):
            emit_route_completed("req1", trace)
        assert "route:complete" in caplog.text


# ═══════════════════════════════════════════════════════════════
# K. trace_to_summary
# ═══════════════════════════════════════════════════════════════


class TestTraceToSummary:
    """trace_to_summary — JSON-safe output shape."""

    def test_has_all_expected_keys(self):
        req = _make_request()
        trace = build_execution_trace(
            req,
            resolved_candidate_order=["a"],
            provider_attribution={"provider": "a"},
            skip_summary={"busy": 1},
            gate_outcomes=[{"provider": "a", "outcome": "acquired"}],
            override_inputs={"premium_override": True},
        )
        s = trace_to_summary(trace)
        expected_keys = {
            "request_id", "requested_mode", "resolved_mode",
            "is_direct_mode", "task_type",
            "resolved_candidate_order", "attempted_providers",
            "selected_provider", "provider_states",
            "fallback_used", "fallback_reason",
            "route_resolution", "execution_status",
            "error_summary", "timing_ms",
            "override_inputs", "provider_attribution",
            "gate_outcomes", "skip_summary",
            "route_decision_log",
        }
        assert set(s.keys()) == expected_keys

    def test_excludes_response_payload(self):
        req = _make_request()
        trace = build_execution_trace(req, response_payload={"big": "data"})
        s = trace_to_summary(trace)
        assert "response_payload" not in s

    def test_excludes_metadata(self):
        req = _make_request(metadata={"internal": "stuff"})
        trace = build_execution_trace(req)
        s = trace_to_summary(trace)
        assert "metadata" not in s


# ═══════════════════════════════════════════════════════════════
# L. Resolved candidate order in trace
# ═══════════════════════════════════════════════════════════════


class TestResolvedCandidateOrder:
    """resolved_candidate_order populated by route_and_execute."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_local_single_candidate(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        req = _make_request(mode=ExecutionMode.LOCAL.value)
        _, trace = route_and_execute(req, registry=registry, gate=gate)

        assert trace.resolved_candidate_order == [pid]

    @patch("app.services.model_provider_adapters.get_settings")
    def test_distributed_multi_candidate(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid1 = Provider.LOCALHOST_LLM.value
        pid2 = Provider.NETWORK_MODEL_MACHINE.value
        probe1 = _make_probe(pid1)
        result1 = _make_result(pid1)
        probe2 = _make_probe(pid2)
        result2 = _make_result(pid2)
        registry, gate = _build_registry_and_gate({
            pid1: (probe1, result1),
            pid2: (probe2, result2),
        })

        req = _make_request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
        _, trace = route_and_execute(req, registry=registry, gate=gate)

        assert trace.resolved_candidate_order == [pid1, pid2]

    @patch("app.services.model_provider_adapters.get_settings")
    def test_override_reorders_candidates(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid1 = Provider.LOCALHOST_LLM.value
        pid2 = Provider.NETWORK_MODEL_MACHINE.value
        probe1 = _make_probe(pid1)
        result1 = _make_result(pid1)
        probe2 = _make_probe(pid2)
        result2 = _make_result(pid2)
        registry, gate = _build_registry_and_gate({
            pid1: (probe1, result1),
            pid2: (probe2, result2),
        })

        req = _make_request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            preferred_provider=pid2,
        )
        _, trace = route_and_execute(req, registry=registry, gate=gate)

        # pid2 should be first in the candidate order.
        assert trace.resolved_candidate_order[0] == pid2
        assert trace.override_inputs["preferred_provider"] == pid2
