"""Tests for routing dashboard contract, service, and API routes.

Step 13 — Distributed Model Routing / UI visibility layer.

Sections:
    A — Contract: state/severity mapping
    B — Contract: ProviderHealthSummary shape
    C — Contract: RequestRoutingSummary shape
    D — Contract: RoutingSystemSummary shape
    E — Contract: blocked fields stripping
    F — Service: provider health summary builder
    G — Service: routing system summary builder
    H — Service: request routing summary builder
    I — Service: route summary text builder
    J — Service: trace ring buffer
    K — Service: composite dashboard payload
    L — API route: /routing/health
    M — API route: /routing/system
    N — API route: /routing/recent
    O — API route: /routing/dashboard
    P — Telemetry hook: emit_route_completed records trace
    Q — Security: no sensitive fields in summaries
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from app.services.routing_dashboard_contract import (
    BLOCKED_FIELDS,
    PROVIDER_DISPLAY_LABELS,
    STATE_SEVERITY_MAP,
    ProviderHealthSummary,
    RequestRoutingSummary,
    RoutingSystemSummary,
    provider_display_label,
    state_to_severity,
    strip_blocked_fields,
)
from app.services.routing_dashboard_service import (
    _build_route_summary_text,
    build_dashboard_payload,
    build_provider_health_summaries,
    build_request_routing_summary,
    build_routing_system_summary,
    clear_recent_traces,
    get_recent_traces,
    record_trace,
)
from app.services.model_routing_contract import (
    ExecutionTrace,
    ExecutionStatus,
    Provider,
    ProviderState,
    RouteResolutionStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_traces():
    """Ensure trace buffer is empty before and after each test."""
    clear_recent_traces()
    yield
    clear_recent_traces()


def _make_trace(**overrides) -> ExecutionTrace:
    """Build a minimal ExecutionTrace with sensible defaults."""
    defaults = dict(
        requested_mode="local_distributed",
        resolved_mode="local_distributed",
        attempted_providers=["localhost_llm"],
        selected_provider="localhost_llm",
        provider_states={"localhost_llm": "available"},
        fallback_used=False,
        fallback_reason=None,
        route_resolution=RouteResolutionStatus.RESOLVED.value,
        execution_status=ExecutionStatus.SUCCESS.value,
        timing_ms=120.5,
        request_id="test123",
        task_type="regime_analysis",
        resolved_candidate_order=["localhost_llm", "network_model_machine"],
        override_inputs={},
        provider_attribution={"provider": "localhost_llm", "route_position": 0},
        gate_outcomes=[{"provider": "localhost_llm", "outcome": "acquired"}],
        skip_summary={},
        is_direct_mode=False,
    )
    defaults.update(overrides)
    return ExecutionTrace(**defaults)


def _mock_registry():
    """Build a mock ProviderRegistry with realistic statuses."""
    from app.services.model_provider_registry import ProviderStatusSnapshot

    mock_reg = MagicMock()
    mock_reg.all_statuses.return_value = [
        ProviderStatusSnapshot(
            provider_id="localhost_llm",
            registered=True,
            configured=True,
            state="available",
            probe_success=True,
            status_reason="",
            timing_ms=45.0,
        ),
        ProviderStatusSnapshot(
            provider_id="network_model_machine",
            registered=True,
            configured=True,
            state="busy",
            probe_success=True,
            status_reason="in-flight",
            timing_ms=80.0,
        ),
        ProviderStatusSnapshot(
            provider_id="bedrock_titan_nova_pro",
            registered=True,
            configured=False,
            state="unavailable",
            probe_success=True,
            status_reason="not configured",
            timing_ms=None,
        ),
    ]
    mock_reg.list_registered.return_value = [
        "bedrock_titan_nova_pro", "localhost_llm", "network_model_machine",
    ]
    return mock_reg


def _mock_gate():
    """Build a mock ProviderExecutionGate with realistic snapshots."""
    from app.services.model_execution_gate import GateSnapshot

    mock_gate = MagicMock()
    mock_gate.all_snapshots.return_value = {
        "localhost_llm": GateSnapshot(
            provider_id="localhost_llm", in_flight=0, max_concurrency=1, has_capacity=True,
        ),
        "network_model_machine": GateSnapshot(
            provider_id="network_model_machine", in_flight=1, max_concurrency=1, has_capacity=False,
        ),
        "bedrock_titan_nova_pro": GateSnapshot(
            provider_id="bedrock_titan_nova_pro", in_flight=0, max_concurrency=1, has_capacity=True,
        ),
    }
    return mock_gate


def _mock_config():
    """Build a mock RoutingConfig."""
    mock_cfg = MagicMock()
    mock_cfg.routing_enabled = True
    mock_cfg.bedrock_enabled = True
    mock_cfg.default_max_concurrency = 1
    mock_cfg.provider_concurrency = {
        "localhost_llm": 1,
        "network_model_machine": 1,
        "bedrock_titan_nova_pro": 1,
    }
    mock_cfg.probe_timeout_seconds = 3.0
    mock_cfg.probe_degraded_threshold_ms = 2000.0
    mock_cfg.config_source = "defaults"
    return mock_cfg


# Patch targets — patch at source modules since service uses local imports.
_P_REG = "app.services.model_provider_registry.get_registry"
_P_GATE = "app.services.model_execution_gate.get_execution_gate"
_P_CFG = "app.services.model_routing_config.get_routing_config"


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# A — Contract: state/severity mapping
# ═══════════════════════════════════════════════════════════════════════════


class TestStateSeverityMapping:
    def test_all_provider_states_mapped(self):
        for state in ProviderState:
            assert state.value in STATE_SEVERITY_MAP

    def test_available_is_healthy(self):
        assert state_to_severity("available") == "healthy"

    def test_busy_is_warning(self):
        assert state_to_severity("busy") == "warning"

    def test_degraded_is_caution(self):
        assert state_to_severity("degraded") == "caution"

    def test_unavailable_is_offline(self):
        assert state_to_severity("unavailable") == "offline"

    def test_failed_is_error(self):
        assert state_to_severity("failed") == "error"

    def test_unknown_state_returns_offline(self):
        assert state_to_severity("some_unknown") == "offline"


class TestProviderDisplayLabels:
    def test_all_known_providers_have_labels(self):
        for p in Provider:
            assert p.value in PROVIDER_DISPLAY_LABELS

    def test_localhost_label(self):
        assert provider_display_label("localhost_llm") == "Localhost LLM"

    def test_model_machine_label(self):
        assert provider_display_label("network_model_machine") == "Model Machine"

    def test_bedrock_label(self):
        assert provider_display_label("bedrock_titan_nova_pro") == "Bedrock Titan Nova Pro"

    def test_unknown_provider_returns_id(self):
        assert provider_display_label("future_provider") == "future_provider"


# ═══════════════════════════════════════════════════════════════════════════
# B — Contract: ProviderHealthSummary shape
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderHealthSummaryShape:
    def test_to_dict_has_expected_fields(self):
        summary = ProviderHealthSummary(
            provider="localhost_llm", display_label="Localhost LLM",
            configured=True, current_state="available", severity="healthy",
            probe_success=True, status_reason="", timing_ms=42.0,
            max_concurrency=1, in_flight_count=0, available_capacity=1,
        )
        d = summary.to_dict()
        expected = {
            "provider", "display_label", "configured", "current_state",
            "severity", "probe_success", "status_reason", "timing_ms",
            "max_concurrency", "in_flight_count", "available_capacity",
            "registered",
            # Step 16 additions
            "probe_type", "degraded_threshold_ms", "state_display_label",
            "status_detail_text", "last_checked_at",
            # Circuit breaker
            "circuit_breaker",
        }
        assert set(d.keys()) == expected

    def test_no_sensitive_fields(self):
        d = ProviderHealthSummary(
            provider="x", display_label="X", configured=True,
            current_state="available", severity="healthy",
            probe_success=True, status_reason="", timing_ms=None,
            max_concurrency=1, in_flight_count=0, available_capacity=1,
        ).to_dict()
        for f in BLOCKED_FIELDS:
            assert f not in d


# ═══════════════════════════════════════════════════════════════════════════
# C — Contract: RequestRoutingSummary shape
# ═══════════════════════════════════════════════════════════════════════════


class TestRequestRoutingSummaryShape:
    def test_to_dict_has_expected_fields(self):
        d = RequestRoutingSummary(
            request_id="abc", task_type="regime_analysis",
            requested_mode="local_distributed", resolved_mode="local_distributed",
            actual_provider="localhost_llm", provider_label="Localhost LLM",
            is_direct_mode=False, fallback_used=False, selected_position=0,
            override_applied=False, route_status="resolved",
            execution_status="success",
            route_summary_text="Localhost LLM → success", timing_ms=100.0,
        ).to_dict()
        expected = {
            "request_id", "task_type", "requested_mode", "resolved_mode",
            "actual_provider", "provider_label", "is_direct_mode",
            "fallback_used", "selected_position", "override_applied",
            "route_status", "execution_status", "route_summary_text",
            "skip_summary", "gate_outcomes_summary", "timing_ms",
        }
        assert set(d.keys()) == expected

    def test_no_sensitive_fields(self):
        d = RequestRoutingSummary(
            request_id="x", task_type=None, requested_mode="local",
            resolved_mode="local", actual_provider=None,
            provider_label=None, is_direct_mode=True, fallback_used=False,
            selected_position=None, override_applied=False,
            route_status="resolved", execution_status="not_attempted",
            route_summary_text="",
        ).to_dict()
        for f in BLOCKED_FIELDS:
            assert f not in d


# ═══════════════════════════════════════════════════════════════════════════
# D — Contract: RoutingSystemSummary shape
# ═══════════════════════════════════════════════════════════════════════════


class TestRoutingSystemSummaryShape:
    def test_to_dict_has_expected_fields(self):
        d = RoutingSystemSummary(
            routing_enabled=True, bedrock_enabled=True,
            default_max_concurrency=1,
        ).to_dict()
        expected = {
            "routing_enabled", "bedrock_enabled", "default_max_concurrency",
            "provider_concurrency", "probe_timeout_seconds",
            "probe_degraded_threshold_ms", "config_source", "provider_count",
            "config_loaded_at",
            # Step 17 additions
            "selected_execution_mode", "execution_mode_label",
        }
        assert set(d.keys()) == expected

    def test_routing_disabled_visible(self):
        d = RoutingSystemSummary(
            routing_enabled=False, bedrock_enabled=True,
            default_max_concurrency=1,
        ).to_dict()
        assert d["routing_enabled"] is False


# ═══════════════════════════════════════════════════════════════════════════
# E — Contract: blocked fields stripping
# ═══════════════════════════════════════════════════════════════════════════


class TestStripBlockedFields:
    def test_removes_response_payload(self):
        result = strip_blocked_fields({"request_id": "x", "response_payload": "s"})
        assert "response_payload" not in result
        assert result["request_id"] == "x"

    def test_removes_prompt_and_system_prompt(self):
        result = strip_blocked_fields({"prompt": [], "system_prompt": "h", "ok": 1})
        assert "prompt" not in result
        assert "system_prompt" not in result

    def test_removes_error_detail(self):
        result = strip_blocked_fields({"error_detail": "tb", "error_summary": "f"})
        assert "error_detail" not in result
        assert result["error_summary"] == "f"

    def test_removes_raw_response(self):
        result = strip_blocked_fields({"raw_response": {}, "timing_ms": 100})
        assert "raw_response" not in result

    def test_preserves_safe_fields(self):
        data = {"request_id": "a", "task_type": "b", "execution_status": "c"}
        assert strip_blocked_fields(data) == data


# ═══════════════════════════════════════════════════════════════════════════
# F — Service: provider health summary builder
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildProviderHealthSummaries:
    @patch(_P_GATE, return_value=_mock_gate())
    @patch(_P_REG, return_value=_mock_registry())
    def test_returns_three_providers(self, _reg, _gate):
        result = build_provider_health_summaries()
        assert len(result) == 3

    @patch(_P_GATE, return_value=_mock_gate())
    @patch(_P_REG, return_value=_mock_registry())
    def test_localhost_is_healthy(self, _reg, _gate):
        result = build_provider_health_summaries()
        localhost = [p for p in result if p.provider == "localhost_llm"][0]
        assert localhost.severity == "healthy"
        assert localhost.current_state == "available"
        assert localhost.in_flight_count == 0
        assert localhost.available_capacity == 1

    @patch(_P_GATE, return_value=_mock_gate())
    @patch(_P_REG, return_value=_mock_registry())
    def test_busy_provider_warning(self, _reg, _gate):
        result = build_provider_health_summaries()
        mm = [p for p in result if p.provider == "network_model_machine"][0]
        assert mm.severity == "warning"
        assert mm.in_flight_count == 1
        assert mm.available_capacity == 0

    @patch(_P_GATE, return_value=_mock_gate())
    @patch(_P_REG, return_value=_mock_registry())
    def test_unconfigured_provider(self, _reg, _gate):
        result = build_provider_health_summaries()
        bedrock = [p for p in result if p.provider == "bedrock_titan_nova_pro"][0]
        assert bedrock.configured is False
        assert bedrock.severity == "offline"

    @patch(_P_GATE, return_value=_mock_gate())
    @patch(_P_REG, return_value=_mock_registry())
    def test_to_dict_shape(self, _reg, _gate):
        d = build_provider_health_summaries()[0].to_dict()
        assert "provider" in d
        assert "severity" in d
        assert "display_label" in d


# ═══════════════════════════════════════════════════════════════════════════
# G — Service: routing system summary builder
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildRoutingSystemSummary:
    @patch(_P_REG)
    @patch(_P_CFG, return_value=_mock_config())
    def test_basic_shape(self, _cfg, mock_reg):
        mock_reg.return_value = MagicMock(
            list_registered=MagicMock(return_value=["a", "b", "c"])
        )
        result = build_routing_system_summary()
        assert result.routing_enabled is True
        assert result.bedrock_enabled is True
        assert result.provider_count == 3

    @patch(_P_REG)
    @patch(_P_CFG)
    def test_routing_disabled(self, mock_cfg, mock_reg):
        cfg = _mock_config()
        cfg.routing_enabled = False
        mock_cfg.return_value = cfg
        mock_reg.return_value = MagicMock(
            list_registered=MagicMock(return_value=[])
        )
        assert build_routing_system_summary().routing_enabled is False

    @patch(_P_REG)
    @patch(_P_CFG, return_value=_mock_config())
    def test_to_dict_serializable(self, _cfg, mock_reg):
        mock_reg.return_value = MagicMock(
            list_registered=MagicMock(return_value=["a"])
        )
        d = build_routing_system_summary().to_dict()
        assert isinstance(d, dict)
        assert "routing_enabled" in d


# ═══════════════════════════════════════════════════════════════════════════
# H — Service: request routing summary builder
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildRequestRoutingSummary:
    def test_basic_success(self):
        s = build_request_routing_summary(_make_trace())
        assert s.request_id == "test123"
        assert s.actual_provider == "localhost_llm"
        assert s.provider_label == "Localhost LLM"
        assert s.fallback_used is False
        assert s.selected_position == 0
        assert s.execution_status == "success"

    def test_fallback_trace(self):
        s = build_request_routing_summary(_make_trace(
            selected_provider="network_model_machine",
            fallback_used=True,
            fallback_reason="provider_busy",
            skip_summary={"busy": 1},
        ))
        assert s.fallback_used is True
        assert s.selected_position == 1
        assert s.skip_summary == {"busy": 1}

    def test_no_provider(self):
        s = build_request_routing_summary(_make_trace(
            selected_provider=None, execution_status="failed",
        ))
        assert s.actual_provider is None
        assert s.provider_label is None
        assert s.selected_position is None

    def test_override_applied(self):
        s = build_request_routing_summary(
            _make_trace(override_inputs={"override_mode": "premium_online"})
        )
        assert s.override_applied is True

    def test_to_dict_no_blocked(self):
        d = build_request_routing_summary(_make_trace()).to_dict()
        for f in BLOCKED_FIELDS:
            assert f not in d


# ═══════════════════════════════════════════════════════════════════════════
# I — Service: route summary text builder
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildRouteSummaryText:
    def test_direct_success(self):
        text = _build_route_summary_text(_make_trace())
        assert "Localhost LLM" in text
        assert "success" in text

    def test_fallback_includes_reason(self):
        text = _build_route_summary_text(_make_trace(
            selected_provider="network_model_machine",
            fallback_used=True,
            fallback_reason="provider_busy",
        ))
        assert "fallback" in text
        assert "provider_busy" in text

    def test_no_provider(self):
        text = _build_route_summary_text(_make_trace(
            selected_provider=None, execution_status="failed",
        ))
        assert "no provider available" in text
        assert "failed" in text


# ═══════════════════════════════════════════════════════════════════════════
# J — Service: trace ring buffer
# ═══════════════════════════════════════════════════════════════════════════


class TestTraceRingBuffer:
    def test_record_and_retrieve(self):
        record_trace(_make_trace())
        recent = get_recent_traces()
        assert len(recent) == 1
        assert recent[0]["request_id"] == "test123"

    def test_newest_first(self):
        record_trace(_make_trace(request_id="first"))
        record_trace(_make_trace(request_id="second"))
        recent = get_recent_traces()
        assert recent[0]["request_id"] == "second"
        assert recent[1]["request_id"] == "first"

    def test_limit(self):
        for i in range(10):
            record_trace(_make_trace(request_id=f"req_{i}"))
        assert len(get_recent_traces(limit=3)) == 3

    def test_clear(self):
        record_trace(_make_trace())
        clear_recent_traces()
        assert len(get_recent_traces()) == 0

    def test_no_blocked_fields_in_stored(self):
        record_trace(_make_trace(response_payload="secret"))
        recent = get_recent_traces()
        assert "response_payload" not in recent[0]

    def test_capacity_capped(self):
        from app.services.routing_dashboard_service import _MAX_RECENT_TRACES
        for i in range(_MAX_RECENT_TRACES + 20):
            record_trace(_make_trace(request_id=f"req_{i}"))
        assert len(get_recent_traces(limit=200)) == _MAX_RECENT_TRACES


# ═══════════════════════════════════════════════════════════════════════════
# K — Service: composite dashboard payload
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildDashboardPayload:
    @patch(_P_CFG, return_value=_mock_config())
    @patch(_P_GATE, return_value=_mock_gate())
    @patch(_P_REG, return_value=_mock_registry())
    def test_payload_shape(self, _reg, _gate, _cfg):
        payload = build_dashboard_payload()
        assert "system" in payload
        assert "providers" in payload
        assert "recent_traces" in payload
        assert isinstance(payload["providers"], list)
        assert isinstance(payload["system"], dict)

    @patch(_P_CFG, return_value=_mock_config())
    @patch(_P_GATE, return_value=_mock_gate())
    @patch(_P_REG, return_value=_mock_registry())
    def test_payload_routing_enabled(self, _reg, _gate, _cfg):
        payload = build_dashboard_payload()
        assert "routing_enabled" in payload["system"]


# ═══════════════════════════════════════════════════════════════════════════
# L — API route: /routing/health
# ═══════════════════════════════════════════════════════════════════════════


class TestRoutingHealthAPI:
    @patch(_P_GATE, return_value=_mock_gate())
    @patch(_P_REG, return_value=_mock_registry())
    def test_health_endpoint_shape(self, _reg, _gate):
        from app.api.routes_routing import get_routing_health
        result = _run(get_routing_health(refresh=False))
        assert "providers" in result
        assert len(result["providers"]) == 3

    @patch(_P_GATE, return_value=_mock_gate())
    @patch(_P_REG, return_value=_mock_registry())
    def test_providers_have_severity(self, _reg, _gate):
        from app.api.routes_routing import get_routing_health
        result = _run(get_routing_health(refresh=False))
        for p in result["providers"]:
            assert "severity" in p
            assert p["severity"] in {"healthy", "warning", "caution", "offline", "error"}


# ═══════════════════════════════════════════════════════════════════════════
# M — API route: /routing/system
# ═══════════════════════════════════════════════════════════════════════════


class TestRoutingSystemAPI:
    @patch(_P_REG)
    @patch(_P_CFG, return_value=_mock_config())
    def test_system_endpoint_shape(self, _cfg, mock_reg):
        from app.api.routes_routing import get_routing_system
        mock_reg.return_value = MagicMock(
            list_registered=MagicMock(return_value=["a"])
        )
        result = _run(get_routing_system())
        assert "system" in result
        assert result["system"]["routing_enabled"] is True


# ═══════════════════════════════════════════════════════════════════════════
# N — API route: /routing/recent
# ═══════════════════════════════════════════════════════════════════════════


class TestRoutingRecentAPI:
    def test_recent_empty(self):
        from app.api.routes_routing import get_recent_routing_traces
        result = _run(get_recent_routing_traces(limit=10))
        assert result["traces"] == []
        assert result["count"] == 0

    def test_recent_with_traces(self):
        from app.api.routes_routing import get_recent_routing_traces
        record_trace(_make_trace(request_id="r1"))
        record_trace(_make_trace(request_id="r2"))
        result = _run(get_recent_routing_traces(limit=10))
        assert result["count"] == 2
        assert result["traces"][0]["request_id"] == "r2"


# ═══════════════════════════════════════════════════════════════════════════
# O — API route: /routing/dashboard
# ═══════════════════════════════════════════════════════════════════════════


class TestRoutingDashboardAPI:
    @patch(_P_CFG, return_value=_mock_config())
    @patch(_P_GATE, return_value=_mock_gate())
    @patch(_P_REG, return_value=_mock_registry())
    def test_dashboard_endpoint_shape(self, _reg, _gate, _cfg):
        from app.api.routes_routing import get_routing_dashboard
        result = _run(get_routing_dashboard(refresh=False, recent_limit=5))
        assert "system" in result
        assert "providers" in result
        assert "recent_traces" in result


# ═══════════════════════════════════════════════════════════════════════════
# P — Telemetry hook: emit_route_completed records trace
# ═══════════════════════════════════════════════════════════════════════════


class TestEmitRouteCompletedHook:
    def test_emit_records_trace_in_buffer(self):
        from app.services.model_routing_telemetry import emit_route_completed
        trace = _make_trace(request_id="hook_test")
        emit_route_completed("hook_test", trace)
        recent = get_recent_traces()
        assert len(recent) == 1
        assert recent[0]["request_id"] == "hook_test"

    def test_emit_survives_record_failure(self):
        """emit_route_completed must not raise if dashboard recording fails."""
        from app.services.model_routing_telemetry import emit_route_completed
        with patch(
            "app.services.routing_dashboard_service.record_trace",
            side_effect=RuntimeError("boom"),
        ):
            # Should not raise
            emit_route_completed("req1", _make_trace())


# ═══════════════════════════════════════════════════════════════════════════
# Q — Security: no sensitive fields in any summary output
# ═══════════════════════════════════════════════════════════════════════════


class TestSecurityNoSensitiveFields:
    def test_trace_summary_excludes_response_payload(self):
        from app.services.model_routing_telemetry import trace_to_summary
        summary = trace_to_summary(_make_trace(response_payload="secret"))
        assert "response_payload" not in summary

    def test_recorded_trace_excludes_blocked(self):
        record_trace(_make_trace(response_payload="hidden"))
        recent = get_recent_traces()
        assert "response_payload" not in recent[0]

    def test_request_summary_excludes_sensitive(self):
        d = build_request_routing_summary(_make_trace()).to_dict()
        for f in BLOCKED_FIELDS:
            assert f not in d

    @patch(_P_CFG, return_value=_mock_config())
    @patch(_P_GATE, return_value=_mock_gate())
    @patch(_P_REG, return_value=_mock_registry())
    def test_dashboard_payload_no_secrets(self, _reg, _gate, _cfg):
        payload = build_dashboard_payload()
        for f in BLOCKED_FIELDS:
            assert f not in payload["system"]
        for p in payload["providers"]:
            for f in BLOCKED_FIELDS:
                assert f not in p
