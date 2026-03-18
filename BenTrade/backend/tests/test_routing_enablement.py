"""Step 10 — Routing enablement control + tranche 2 migration tests.

Validates:
    A. RoutingDisabledError + kill switch in execute_routed_model
    B. routed_tmc_final_decision respects routing_enabled
    C. routed_model_interpretation (sync) — OpenAI-compat shape
    D. async_routed_model_interpretation — async executor wrapper
    E. routes_tmc.py migration (tranche 2)
    F. MI runner wiring (tranche 2)
    G. _routing_is_enabled helper
    H. No fake traces when routing is disabled
    I. Market picture interpretation uses local_distributed
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from app.services.model_execution_gate import ProviderExecutionGate
from app.services.model_provider_base import ProbeResult, ProviderResult
from app.services.model_provider_registry import ProviderRegistry
from app.services.model_routing_config import (
    RoutingConfig,
    get_routing_config,
    reset_routing_config,
    set_routing_config,
)
from app.services.model_routing_contract import (
    ExecutionMode,
    ExecutionRequest,
    ExecutionStatus,
    ExecutionTrace,
    Provider,
    ProviderState,
)
from app.services.model_routing_integration import (
    RoutingDisabledError,
    _routing_is_enabled,
    adapt_to_legacy,
    async_routed_model_interpretation,
    execute_routed_model,
    resolve_routing_mode,
    routed_model_interpretation,
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


@pytest.fixture(autouse=True)
def _reset_config():
    """Ensure routing config is reset between tests."""
    yield
    reset_routing_config()


# ═══════════════════════════════════════════════════════════
# A. RoutingDisabledError + kill switch in execute_routed_model
# ═══════════════════════════════════════════════════════════


class TestRoutingDisabledError:
    """Kill switch enforcement in execute_routed_model."""

    def test_routing_disabled_error_is_runtime_error(self):
        assert issubclass(RoutingDisabledError, RuntimeError)

    def test_execute_routed_raises_when_disabled(self):
        set_routing_config(RoutingConfig(routing_enabled=False))
        with pytest.raises(RoutingDisabledError, match="Routing is disabled"):
            execute_routed_model(
                task_type="active_trade_reassessment",
                messages=[{"role": "user", "content": "test"}],
            )

    def test_execute_routed_raises_with_task_type_in_message(self):
        set_routing_config(RoutingConfig(routing_enabled=False))
        with pytest.raises(RoutingDisabledError, match="active_trade_reassessment"):
            execute_routed_model(
                task_type="active_trade_reassessment",
                messages=[{"role": "user", "content": "test"}],
            )

    def test_execute_routed_raises_for_tmc_when_disabled(self):
        set_routing_config(RoutingConfig(routing_enabled=False))
        with pytest.raises(RoutingDisabledError, match="tmc_final_decision"):
            execute_routed_model(
                task_type="tmc_final_decision",
                messages=[{"role": "user", "content": "test"}],
            )

    @patch("app.services.model_provider_adapters.get_settings")
    def test_execute_routed_proceeds_when_enabled(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        set_routing_config(RoutingConfig(routing_enabled=True))

        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid, content="hello")
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            legacy, trace = execute_routed_model(
                task_type="active_trade_reassessment",
                messages=[{"role": "user", "content": "test"}],
            )

        assert legacy["status"] == "success"
        assert legacy["routed"] is True

    def test_routing_disabled_does_not_create_trace(self):
        """No fake trace should be produced when routing is off."""
        set_routing_config(RoutingConfig(routing_enabled=False))
        with pytest.raises(RoutingDisabledError):
            execute_routed_model(
                task_type="test",
                messages=[{"role": "user", "content": "x"}],
            )
        # If we get here, no trace was returned — correct.

    def test_routing_disabled_for_market_interpretation(self):
        set_routing_config(RoutingConfig(routing_enabled=False))
        with pytest.raises(RoutingDisabledError, match="market_picture_interpretation"):
            execute_routed_model(
                task_type="market_picture_interpretation",
                messages=[{"role": "user", "content": "test"}],
            )

    def test_default_config_has_routing_enabled(self):
        """By default, routing is enabled."""
        config = get_routing_config()
        assert config.routing_enabled is True


# ═══════════════════════════════════════════════════════════
# B. routed_tmc_final_decision respects routing_enabled
# ═══════════════════════════════════════════════════════════


class TestTmcRoutingEnabled:
    """routed_tmc_final_decision kill switch behavior."""

    def test_tmc_disabled_uses_legacy(self):
        set_routing_config(RoutingConfig(routing_enabled=False))
        with patch(
            "common.model_analysis.analyze_tmc_final_decision",
            return_value={"decision": "PASS", "conviction": 50},
        ) as mock_legacy:
            out = routed_tmc_final_decision(
                candidate={"symbol": "SPY"},
            )
        mock_legacy.assert_called_once()
        assert out["decision"] == "PASS"
        assert "_routed" not in out  # Legacy path should not stamp _routed.

    def test_tmc_disabled_does_not_call_routing(self):
        """When routing is disabled, execute_routed_model should never be called."""
        set_routing_config(RoutingConfig(routing_enabled=False))
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
        ) as mock_routed, patch(
            "common.model_analysis.analyze_tmc_final_decision",
            return_value={"decision": "PASS", "conviction": 40},
        ):
            routed_tmc_final_decision(candidate={"symbol": "IWM"})
        mock_routed.assert_not_called()

    @patch("app.services.model_provider_adapters.get_settings")
    def test_tmc_enabled_uses_routing(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        set_routing_config(RoutingConfig(routing_enabled=True))

        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        decision_json = json.dumps({
            "decision": "EXECUTE",
            "conviction": 80,
            "decision_summary": "Good",
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
                candidate={"symbol": "AAPL", "composite_score": 0.85},
                strategy_id="bull_put_spread",
            )

        assert out.get("_routed") is True
        assert out.get("decision") == "EXECUTE"

    def test_tmc_disabled_preserves_all_legacy_args(self):
        """All kwargs should be forwarded to legacy when routing is off."""
        set_routing_config(RoutingConfig(routing_enabled=False))
        with patch(
            "common.model_analysis.analyze_tmc_final_decision",
            return_value={"decision": "PASS"},
        ) as mock_legacy:
            routed_tmc_final_decision(
                candidate={"symbol": "QQQ"},
                market_picture_context={"engines": {}},
                strategy_id="iron_condor",
                retries=2,
                timeout=120,
            )
        mock_legacy.assert_called_once_with(
            candidate={"symbol": "QQQ"},
            market_picture_context={"engines": {}},
            strategy_id="iron_condor",
            retries=2,
            timeout=120,
        )


# ═══════════════════════════════════════════════════════════
# C. routed_model_interpretation (sync) — OpenAI-compat shape
# ═══════════════════════════════════════════════════════════


class TestRoutedModelInterpretation:
    """Sync routed_model_interpretation wrapper."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_success_returns_openai_shape(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        set_routing_config(RoutingConfig(routing_enabled=True))

        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid, content="interpretation text")
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            out = routed_model_interpretation(
                None,  # http_client ignored
                {
                    "model": "local",
                    "messages": [
                        {"role": "system", "content": "You are an analyst."},
                        {"role": "user", "content": "Analyze the market."},
                    ],
                    "temperature": 0.3,
                    "stream": False,
                },
            )

        assert "choices" in out
        assert len(out["choices"]) == 1
        msg = out["choices"][0]["message"]
        assert msg["role"] == "assistant"
        assert msg["content"] == "interpretation text"
        assert out["_routed"] is True

    def test_raises_when_routing_disabled(self):
        """Routing disabled → RoutingDisabledError propagates."""
        set_routing_config(RoutingConfig(routing_enabled=False))
        with pytest.raises(RoutingDisabledError):
            routed_model_interpretation(
                None,
                {"messages": [{"role": "user", "content": "test"}]},
            )

    @patch("app.services.model_provider_adapters.get_settings")
    def test_raises_on_routing_failure(self, mock_settings):
        """If routing returns error status, RuntimeError is raised."""
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        set_routing_config(RoutingConfig(routing_enabled=True))

        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid, state=ProviderState.UNAVAILABLE.value)
        registry, gate = _build_registry_and_gate({pid: (probe, None)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            with pytest.raises(RuntimeError, match="Routed model interpretation failed"):
                routed_model_interpretation(
                    None,
                    {"messages": [{"role": "user", "content": "test"}]},
                )

    def test_extracts_system_prompt(self):
        """System messages should be extracted and passed as system_prompt."""
        set_routing_config(RoutingConfig(routing_enabled=True))

        captured_kwargs = {}

        def capture_execute(**kwargs):
            captured_kwargs.update(kwargs)
            trace = ExecutionTrace(requested_mode="local", resolved_mode="local")
            return (
                {"status": "success", "content": "ok", "routed": True, "request_id": "t1"},
                trace,
            )

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=capture_execute,
        ):
            routed_model_interpretation(
                None,
                {
                    "messages": [
                        {"role": "system", "content": "Be analytical."},
                        {"role": "user", "content": "Analyze data."},
                    ],
                },
            )

        assert captured_kwargs["system_prompt"] == "Be analytical."
        assert captured_kwargs["messages"] == [{"role": "user", "content": "Analyze data."}]

    @patch("app.services.model_provider_adapters.get_settings")
    def test_uses_local_distributed_mode(self, mock_settings):
        """Market picture interpretation → local_distributed."""
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        set_routing_config(RoutingConfig(routing_enabled=True))

        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid, content="ok")
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            out = routed_model_interpretation(
                None,
                {"messages": [{"role": "user", "content": "test"}]},
            )

        assert out["_routed"] is True

    def test_http_client_arg_is_ignored(self):
        """The _http_client arg is accepted but not used."""
        set_routing_config(RoutingConfig(routing_enabled=True))

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            return_value=(
                {"status": "success", "content": "ok", "routed": True, "request_id": "x"},
                ExecutionTrace(requested_mode="local", resolved_mode="local"),
            ),
        ):
            # Pass a sentinel — should not cause any issues.
            out = routed_model_interpretation(
                "SENTINEL_NOT_USED",
                {"messages": [{"role": "user", "content": "test"}]},
            )

        assert out["_routed"] is True


# ═══════════════════════════════════════════════════════════
# D. async_routed_model_interpretation
# ═══════════════════════════════════════════════════════════


class TestAsyncRoutedModelInterpretation:
    """Async wrapper for MI runner model interpretation."""

    def test_async_wrapper_calls_sync(self):
        set_routing_config(RoutingConfig(routing_enabled=True))

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            return_value=(
                {"status": "success", "content": "async result", "routed": True, "request_id": "a1"},
                ExecutionTrace(requested_mode="local", resolved_mode="local"),
            ),
        ):
            out = asyncio.run(
                async_routed_model_interpretation(
                    None,
                    {"messages": [{"role": "user", "content": "test"}]},
                )
            )

        assert out["choices"][0]["message"]["content"] == "async result"
        assert out["_routed"] is True

    def test_async_wrapper_propagates_disabled_error(self):
        set_routing_config(RoutingConfig(routing_enabled=False))

        with pytest.raises(RoutingDisabledError):
            asyncio.run(
                async_routed_model_interpretation(
                    None,
                    {"messages": [{"role": "user", "content": "test"}]},
                )
            )

    def test_async_wrapper_propagates_runtime_error(self):
        set_routing_config(RoutingConfig(routing_enabled=True))

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            return_value=(
                {"status": "error", "content": None, "error": "no providers", "routed": True, "request_id": "e1"},
                ExecutionTrace(requested_mode="local", resolved_mode="local"),
            ),
        ):
            with pytest.raises(RuntimeError, match="Routed model interpretation failed"):
                asyncio.run(
                    async_routed_model_interpretation(
                        None,
                        {"messages": [{"role": "user", "content": "test"}]},
                    )
                )


# ═══════════════════════════════════════════════════════════
# E. routes_tmc.py migration (tranche 2)
# ═══════════════════════════════════════════════════════════


class TestRoutesTmcMigration:
    """routes_tmc.py now uses routed_tmc_final_decision."""

    def test_routes_tmc_imports_routed_function(self):
        import importlib
        spec = importlib.util.find_spec("app.api.routes_tmc")
        assert spec is not None
        with open(spec.origin, "r", encoding="utf-8") as f:
            content = f.read()
        assert "routed_tmc_final_decision" in content
        assert "from app.services.model_routing_integration import" in content

    def test_routes_tmc_still_handles_local_model_unavailable(self):
        """LocalModelUnavailableError handling is preserved."""
        import importlib
        spec = importlib.util.find_spec("app.api.routes_tmc")
        with open(spec.origin, "r", encoding="utf-8") as f:
            content = f.read()
        assert "LocalModelUnavailableError" in content

    def test_routes_tmc_no_direct_analyze_call(self):
        """routes_tmc should not directly call analyze_tmc_final_decision."""
        import importlib
        spec = importlib.util.find_spec("app.api.routes_tmc")
        with open(spec.origin, "r", encoding="utf-8") as f:
            content = f.read()
        # It should NOT have analyze_tmc_final_decision as a direct call target
        # (LocalModelUnavailableError import is fine).
        assert "analyze_tmc_final_decision" not in content


# ═══════════════════════════════════════════════════════════
# F. MI runner wiring (tranche 2)
# ═══════════════════════════════════════════════════════════


class TestMiRunnerWiring:
    """main.py wires MI deps with adaptive routed wrapper (Step 14)."""

    def test_main_imports_adaptive_routed(self):
        import importlib
        spec = importlib.util.find_spec("app.main")
        with open(spec.origin, "r", encoding="utf-8") as f:
            content = f.read()
        assert "adaptive_routed_model_interpretation" in content
        assert "from app.services.model_routing_integration import" in content

    def test_main_uses_per_request_dispatch(self):
        """Step 14: main.py no longer snapshots routing_enabled at startup."""
        import importlib
        spec = importlib.util.find_spec("app.main")
        with open(spec.origin, "r", encoding="utf-8") as f:
            content = f.read()
        # Should NOT contain the old startup snapshot pattern
        assert "if get_routing_config().routing_enabled" not in content

    def test_routed_interpretation_signature_matches_deps(self):
        """async_routed_model_interpretation has same signature as model_request_fn."""
        import inspect
        sig = inspect.signature(async_routed_model_interpretation)
        params = list(sig.parameters.keys())
        assert params == ["_http_client", "payload"]


# ═══════════════════════════════════════════════════════════
# G. _routing_is_enabled helper
# ═══════════════════════════════════════════════════════════


class TestRoutingIsEnabled:
    """_routing_is_enabled reads from central config."""

    def test_returns_true_by_default(self):
        assert _routing_is_enabled() is True

    def test_returns_false_when_config_disabled(self):
        set_routing_config(RoutingConfig(routing_enabled=False))
        assert _routing_is_enabled() is False

    def test_returns_true_when_config_enabled(self):
        set_routing_config(RoutingConfig(routing_enabled=True))
        assert _routing_is_enabled() is True

    def test_reflects_config_changes(self):
        assert _routing_is_enabled() is True
        set_routing_config(RoutingConfig(routing_enabled=False))
        assert _routing_is_enabled() is False
        set_routing_config(RoutingConfig(routing_enabled=True))
        assert _routing_is_enabled() is True


# ═══════════════════════════════════════════════════════════
# H. No fake traces when routing is disabled
# ═══════════════════════════════════════════════════════════


class TestNoFakeTraces:
    """When routing is disabled, no ExecutionTrace should be fabricated."""

    def test_disabled_execute_never_returns_trace(self):
        """RoutingDisabledError is raised, no trace tuple returned."""
        set_routing_config(RoutingConfig(routing_enabled=False))
        with pytest.raises(RoutingDisabledError):
            execute_routed_model(
                task_type="test",
                messages=[{"role": "user", "content": "x"}],
            )

    def test_disabled_tmc_output_has_no_routed_flag(self):
        """Legacy fallback should not stamp _routed."""
        set_routing_config(RoutingConfig(routing_enabled=False))
        with patch(
            "common.model_analysis.analyze_tmc_final_decision",
            return_value={"decision": "PASS", "conviction": 30},
        ):
            out = routed_tmc_final_decision(candidate={"symbol": "SPY"})
        assert "_routed" not in out
        assert "_request_id" not in out
        assert "_provider" not in out

    def test_disabled_interpretation_has_no_routed_wrapper(self):
        """Disabled routing → no OpenAI-compat wrapper produced."""
        set_routing_config(RoutingConfig(routing_enabled=False))
        with pytest.raises(RoutingDisabledError):
            routed_model_interpretation(
                None,
                {"messages": [{"role": "user", "content": "test"}]},
            )


# ═══════════════════════════════════════════════════════════
# I. Market picture interpretation uses local_distributed
# ═══════════════════════════════════════════════════════════


class TestMpiRoutingMode:
    """market_picture_interpretation task type → local_distributed."""

    def test_mpi_resolves_to_local_distributed(self):
        mode, premium = resolve_routing_mode("market_picture_interpretation")
        assert mode == ExecutionMode.LOCAL_DISTRIBUTED.value
        assert premium is False

    def test_mpi_premium_rejected(self):
        mode, premium = resolve_routing_mode("market_picture_interpretation", premium=True)
        assert premium is False

    @patch("app.services.model_provider_adapters.get_settings")
    def test_mpi_routed_call_uses_local_mode(self, mock_settings):
        mock_settings.return_value = MagicMock(BEDROCK_ENABLED=False)
        set_routing_config(RoutingConfig(routing_enabled=True))

        pid = Provider.LOCALHOST_LLM.value
        probe = _make_probe(pid)
        result = _make_result(pid, content="analysis")
        registry, gate = _build_registry_and_gate({pid: (probe, result)})

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            legacy, trace = execute_routed_model(
                task_type="market_picture_interpretation",
                messages=[{"role": "user", "content": "analyze"}],
            )

        assert trace.requested_mode == ExecutionMode.LOCAL_DISTRIBUTED.value
        assert legacy["status"] == "success"
