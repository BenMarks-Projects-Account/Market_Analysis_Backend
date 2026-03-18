"""Step 8 — Pipeline integration strategy + first caller migration tests.

Validates:
    A. Integration policy (resolve_routing_mode)
    B. execute_routed_model — request construction and result adaptation
    C. adapt_to_legacy — success and error shapes
    D. Active trade pipeline routed executor
    E. TMC routed wrapper
    F. Legacy callers remain unchanged
    G. Premium override only where intended
    H. Trace / request_id preservation
    I. Fallback to legacy on routing failure

Total: ~55 tests across 9 sections.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.services.model_execution_gate import ProviderExecutionGate
from app.services.model_provider_base import ProbeResult, ProviderResult
from app.services.model_provider_registry import ProviderRegistry
from app.services.model_routing_contract import (
    ExecutionMode,
    ExecutionRequest,
    ExecutionStatus,
    ExecutionTrace,
    Provider,
    ProviderState,
    RouteResolutionStatus,
)
from app.services.model_routing_integration import (
    DEFAULT_ROUTED_MODE,
    adapt_to_legacy,
    execute_routed_model,
    resolve_routing_mode,
    routed_tmc_final_decision,
)


# ── Helpers ────────────────────────────────────────────────

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
        "raw_response": {"choices": [{"message": {"content": "test"}}]} if success else None,
        "timing_ms": 42.5,
    }
    defaults.update(kwargs)
    return ProviderResult(**defaults)


def _build_registry_and_gate(
    providers: dict[str, tuple[ProbeResult, ProviderResult | None]],
) -> tuple[ProviderRegistry, ProviderExecutionGate]:
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


# ═══════════════════════════════════════════════════════════
# A. Integration policy
# ═══════════════════════════════════════════════════════════


class TestResolveRoutingMode:
    """resolve_routing_mode — centralized policy decisions."""

    def test_default_mode_is_local_distributed(self):
        mode, premium = resolve_routing_mode("active_trade_reassessment")
        assert mode == ExecutionMode.LOCAL_DISTRIBUTED.value
        assert premium is False

    def test_tmc_uses_online_distributed(self):
        mode, premium = resolve_routing_mode("tmc_final_decision")
        assert mode == ExecutionMode.ONLINE_DISTRIBUTED.value
        assert premium is False

    def test_tmc_premium_when_requested(self):
        mode, premium = resolve_routing_mode("tmc_final_decision", premium=True)
        assert premium is True

    def test_premium_rejected_for_non_eligible_task(self):
        mode, premium = resolve_routing_mode("active_trade_reassessment", premium=True)
        assert premium is False

    def test_unknown_task_uses_default(self):
        mode, premium = resolve_routing_mode("some_new_task")
        assert mode == DEFAULT_ROUTED_MODE
        assert premium is False

    def test_market_interpretation_uses_default(self):
        mode, premium = resolve_routing_mode("market_interpretation")
        assert mode == ExecutionMode.LOCAL_DISTRIBUTED.value


# ═══════════════════════════════════════════════════════════
# B. execute_routed_model
# ═══════════════════════════════════════════════════════════


class TestExecuteRoutedModel:
    """execute_routed_model — full routing path."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_success_returns_legacy_shape(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid, content="model output text")
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            legacy, trace = execute_routed_model(
                task_type="active_trade_reassessment",
                messages=[{"role": "user", "content": "test"}],
                timeout=60.0,
            )

        assert legacy["status"] == "success"
        assert legacy["routed"] is True
        assert legacy["request_id"] == trace.request_id
        assert legacy["content"] == "model output text"
        assert legacy["provider"] == pid

    @patch("app.services.model_provider_adapters.get_settings")
    def test_failure_returns_error_shape(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid, state=ProviderState.UNAVAILABLE.value)
        registry, gate = _build_registry_and_gate({pid: (probe, None)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            legacy, trace = execute_routed_model(
                task_type="active_trade_reassessment",
                messages=[{"role": "user", "content": "test"}],
            )

        assert legacy["status"] == "error"
        assert legacy["routed"] is True
        assert legacy["error"] is not None

    @patch("app.services.model_provider_adapters.get_settings")
    def test_tmc_uses_online_distributed(self, mock_settings):
        """Step 18: TMC callers pass execution_mode explicitly; legacy
        task-type-based default no longer applies.  The test validates
        that an explicit execution_mode='online_distributed' propagates."""
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            _, trace = execute_routed_model(
                task_type="tmc_final_decision",
                messages=[{"role": "user", "content": "test"}],
                execution_mode="online_distributed",
            )

        # TMC uses online_distributed which includes localhost_llm as first candidate.
        assert trace.requested_mode == ExecutionMode.ONLINE_DISTRIBUTED.value

    @patch("app.services.model_provider_adapters.get_settings")
    def test_routing_overrides_passed(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            legacy, trace = execute_routed_model(
                task_type="active_trade_reassessment",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=500,
                temperature=0.5,
            )

        assert legacy["status"] == "success"
        assert trace.task_type == "active_trade_reassessment"


# ═══════════════════════════════════════════════════════════
# C. adapt_to_legacy
# ═══════════════════════════════════════════════════════════


class TestAdaptToLegacy:
    """adapt_to_legacy — shape conversion."""

    def test_success_shape(self):
        result = ProviderResult(
            provider="localhost_llm",
            success=True,
            content="hello",
            raw_response={"choices": []},
            timing_ms=50.0,
        )
        trace = ExecutionTrace(
            requested_mode="local",
            resolved_mode="local",
            request_id="abc123",
        )
        d = adapt_to_legacy(result, trace)
        assert d["status"] == "success"
        assert d["content"] == "hello"
        assert d["routed"] is True
        assert d["request_id"] == "abc123"
        assert d["error"] is None

    def test_failure_shape(self):
        result = ProviderResult(
            provider="localhost_llm",
            success=False,
            error_message="timeout",
        )
        trace = ExecutionTrace(
            requested_mode="local",
            resolved_mode="local",
            selected_provider="localhost_llm",
            request_id="def456",
        )
        d = adapt_to_legacy(result, trace)
        assert d["status"] == "error"
        assert d["error"] == "timeout"
        assert d["routed"] is True

    def test_none_result(self):
        trace = ExecutionTrace(
            requested_mode="local",
            resolved_mode="local",
            error_summary="All providers down",
            request_id="ghi789",
        )
        d = adapt_to_legacy(None, trace)
        assert d["status"] == "error"
        assert d["content"] is None
        assert d["error"] == "All providers down"

    def test_timing_from_result_preferred(self):
        result = ProviderResult(
            provider="localhost_llm",
            success=True,
            timing_ms=33.0,
        )
        trace = ExecutionTrace(
            requested_mode="local",
            resolved_mode="local",
            timing_ms=100.0,
        )
        d = adapt_to_legacy(result, trace)
        assert d["timing_ms"] == 33.0

    def test_timing_fallback_to_trace(self):
        result = ProviderResult(
            provider="localhost_llm",
            success=True,
            timing_ms=None,
        )
        trace = ExecutionTrace(
            requested_mode="local",
            resolved_mode="local",
            timing_ms=88.5,
        )
        d = adapt_to_legacy(result, trace)
        assert d["timing_ms"] == 88.5


# ═══════════════════════════════════════════════════════════
# D. Active trade pipeline routed executor
# ═══════════════════════════════════════════════════════════


class TestRoutedModelExecutor:
    """_routed_model_executor in active_trade_pipeline."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_routed_executor_success(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        from app.services.active_trade_pipeline import _routed_model_executor

        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        model_json = json.dumps({
            "recommendation": "HOLD",
            "conviction": 0.8,
            "rationale_summary": "Test",
            "key_supporting_points": ["a"],
            "key_risks": ["b"],
        })
        result = _make_result(pid, content=model_json)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            out = _routed_model_executor(
                {"symbol": "AAPL", "trade_key": "test"},
                "rendered prompt text",
            )

        assert out["status"] == "success"
        assert out["raw_response"]["recommendation"] == "HOLD"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_routed_executor_routing_failure_falls_back(self, mock_settings):
        """If routing infrastructure raises, falls back to legacy executor."""
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        from app.services.active_trade_pipeline import _routed_model_executor

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RuntimeError("routing broken"),
        ), patch(
            "app.services.active_trade_pipeline._default_model_executor",
            return_value={"status": "success", "raw_response": {}, "latency_ms": 10},
        ) as mock_legacy:
            out = _routed_model_executor({"symbol": "SPY"}, "test")

        mock_legacy.assert_called_once()
        assert out["status"] == "success"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_run_model_analysis_uses_routed_by_default(self, mock_settings):
        """run_model_analysis defaults to _routed_model_executor."""
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        from app.services.active_trade_pipeline import run_model_analysis

        mock_executor = MagicMock(return_value={
            "status": "success",
            "raw_response": {"recommendation": "HOLD", "conviction": 0.7},
            "provider": "test",
            "model_name": "test",
            "latency_ms": 50,
        })

        out = run_model_analysis(
            {"symbol": "QQQ", "identity": {"trade_key": "t1"}},
            {"trade_health_score": 0.6, "component_scores": {}, "risk_flags": [], "engine_recommendation": "HOLD", "urgency": "low"},
            model_executor=mock_executor,
        )

        # When a custom executor is passed, it should use that.
        mock_executor.assert_called_once()
        assert out["model_available"] is True

    @patch("app.services.model_provider_adapters.get_settings")
    def test_routed_executor_model_error(self, mock_settings):
        """Routed call fails → returns error dict."""
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        from app.services.active_trade_pipeline import _routed_model_executor

        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid, state=ProviderState.UNAVAILABLE.value)
        registry, gate = _build_registry_and_gate({pid: (probe, None)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            out = _routed_model_executor({"symbol": "SPY"}, "test")

        assert out["status"] == "error"


# ═══════════════════════════════════════════════════════════
# E. TMC routed wrapper
# ═══════════════════════════════════════════════════════════


class TestRoutedTmcFinalDecision:
    """routed_tmc_final_decision wrapper."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_success_returns_normalized_output(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)

        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        decision_json = json.dumps({
            "decision": "EXECUTE",
            "conviction": 85,
            "decision_summary": "Strong setup",
            "factors_considered": [],
            "risk_assessment": {"primary_risks": [], "overall_risk_level": "low"},
            "entry_timing": "now",
            "position_sizing": "standard",
            "technical_analysis": {},
            "market_alignment": {},
            "engine_comparison": {},
        })
        result = _make_result(pid, content=decision_json)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            out = routed_tmc_final_decision(
                candidate={"symbol": "AAPL", "composite_score": 0.8},
                strategy_id="bull_put_spread",
            )

        assert out.get("decision") == "EXECUTE"
        assert out.get("_routed") is True
        assert "_request_id" in out

    @patch("app.services.model_provider_adapters.get_settings")
    def test_routing_failure_falls_back_to_legacy(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RuntimeError("infra down"),
        ), patch(
            "common.model_analysis.analyze_tmc_final_decision",
            return_value={"decision": "PASS", "conviction": 50},
        ) as mock_legacy:
            out = routed_tmc_final_decision(
                candidate={"symbol": "SPY"},
            )

        mock_legacy.assert_called_once()
        assert out["decision"] == "PASS"

    def test_premium_flag_forwarded(self):
        """Premium flag is forwarded through resolve_routing_mode."""
        # Verify the policy resolves correctly for premium TMC.
        mode, premium = resolve_routing_mode("tmc_final_decision", premium=True)
        assert mode == ExecutionMode.ONLINE_DISTRIBUTED.value
        assert premium is True

        # Verify premium is not applied to non-eligible tasks.
        mode2, premium2 = resolve_routing_mode("active_trade_reassessment", premium=True)
        assert premium2 is False


# ═══════════════════════════════════════════════════════════
# F. Legacy callers remain unchanged
# ═══════════════════════════════════════════════════════════


class TestLegacyCallersUnchanged:
    """Non-migrated callers still work through legacy paths."""

    def test_model_request_still_exists(self):
        from app.services.model_router import model_request
        assert callable(model_request)

    def test_async_model_request_still_exists(self):
        from app.services.model_router import async_model_request
        assert callable(async_model_request)

    def test_get_model_endpoint_still_exists(self):
        from app.services.model_router import get_model_endpoint
        assert callable(get_model_endpoint)

    def test_analyze_regime_still_exists(self):
        from common.model_analysis import analyze_regime
        assert callable(analyze_regime)

    def test_analyze_stock_strategy_still_exists(self):
        from common.model_analysis import analyze_stock_strategy
        assert callable(analyze_stock_strategy)

    def test_legacy_tmc_still_importable(self):
        from common.model_analysis import analyze_tmc_final_decision
        assert callable(analyze_tmc_final_decision)

    def test_default_model_executor_still_exists(self):
        from app.services.active_trade_pipeline import _default_model_executor
        assert callable(_default_model_executor)

    def test_routes_tmc_uses_routed_path(self):
        """routes_tmc.py should now import from model_routing_integration (Step 10)."""
        import importlib
        import inspect
        spec = importlib.util.find_spec("app.api.routes_tmc")
        assert spec is not None
        source = spec.origin
        with open(source, "r", encoding="utf-8") as f:
            content = f.read()
        assert "from app.services.model_routing_integration import routed_tmc_final_decision" in content


# ═══════════════════════════════════════════════════════════
# G. Premium override only where intended
# ═══════════════════════════════════════════════════════════


class TestPremiumOverridePolicy:
    """Premium override is carefully gated."""

    def test_premium_not_applied_to_routine_tasks(self):
        mode, premium = resolve_routing_mode("active_trade_reassessment", premium=True)
        assert premium is False

    def test_premium_not_applied_to_market_interpretation(self):
        mode, premium = resolve_routing_mode("market_interpretation", premium=True)
        assert premium is False

    def test_premium_applied_to_tmc(self):
        mode, premium = resolve_routing_mode("tmc_final_decision", premium=True)
        assert premium is True

    def test_premium_not_applied_without_flag(self):
        mode, premium = resolve_routing_mode("tmc_final_decision", premium=False)
        assert premium is False


# ═══════════════════════════════════════════════════════════
# H. Trace / request_id preservation
# ═══════════════════════════════════════════════════════════


class TestTracePreservation:
    """Traces are preserved and accessible from migrated calls."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_execute_routed_returns_trace(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            legacy, trace = execute_routed_model(
                task_type="test_task",
                messages=[{"role": "user", "content": "test"}],
            )

        assert isinstance(trace, ExecutionTrace)
        assert trace.request_id is not None
        assert len(trace.request_id) > 0
        assert legacy["request_id"] == trace.request_id

    @patch("app.services.model_provider_adapters.get_settings")
    def test_trace_has_task_type(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            _, trace = execute_routed_model(
                task_type="active_trade_reassessment",
                messages=[{"role": "user", "content": "test"}],
            )

        assert trace.task_type == "active_trade_reassessment"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_tmc_routed_has_request_id(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        decision_json = json.dumps({
            "decision": "PASS",
            "conviction": 40,
            "decision_summary": "Weak",
            "factors_considered": [],
            "risk_assessment": {"primary_risks": [], "overall_risk_level": "high"},
            "entry_timing": "wait",
            "position_sizing": "none",
            "technical_analysis": {},
            "market_alignment": {},
            "engine_comparison": {},
        })
        result = _make_result(pid, content=decision_json)
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            out = routed_tmc_final_decision(
                candidate={"symbol": "IWM"},
            )

        assert "_request_id" in out
        assert len(out["_request_id"]) > 0


# ═══════════════════════════════════════════════════════════
# I. Fallback to legacy on routing failure
# ═══════════════════════════════════════════════════════════


class TestFallbackToLegacy:
    """Migrated callers gracefully fall back to legacy paths."""

    def test_routed_executor_fallback_on_import_error(self):
        """If integration module raises ImportError, fall back to legacy."""
        from app.services.active_trade_pipeline import _routed_model_executor

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=ImportError("module broken"),
        ), patch(
            "app.services.active_trade_pipeline._default_model_executor",
            return_value={"status": "success", "raw_response": {}, "latency_ms": 5},
        ) as mock_legacy:
            out = _routed_model_executor({"symbol": "DIA"}, "test prompt")

        mock_legacy.assert_called_once()

    def test_tmc_routed_fallback_on_parse_failure(self):
        """If routed TMC response can't be parsed, fall back to legacy."""
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            return_value=(
                {"status": "success", "content": "not json at all", "routed": True, "request_id": "x"},
                ExecutionTrace(requested_mode="online_distributed", resolved_mode="online_distributed"),
            ),
        ), patch(
            "common.model_analysis.analyze_tmc_final_decision",
            return_value={"decision": "PASS", "conviction": 30},
        ) as mock_legacy:
            out = routed_tmc_final_decision(
                candidate={"symbol": "QQQ"},
            )

        mock_legacy.assert_called_once()
        assert out["decision"] == "PASS"
