"""Step 20b â€” Routing rotation diagnostic test.

Simulates sequential model analysis calls through the routing system
and verifies that providers alternate via round-robin rotation.

This is the key test that proves the routing fix works:
    - Sequential requests should alternate between localhost_llm and
      network_model_machine (not always hit localhost).
    - The test prints which provider was selected for each request.

Run with:
    python -m pytest tests/test_routing_rotation_diagnostic.py -v -s
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.model_execution_gate import (
    ProviderExecutionGate,
    reset_execution_gate,
)
from app.services.model_provider_base import ProbeResult, ProviderResult
from app.services.model_provider_registry import (
    ProviderRegistry,
    reset_registry,
)
from app.services.model_routing_contract import (
    ExecutionMode,
    ExecutionRequest,
    ExecutionStatus,
    Provider,
    ProviderState,
)
from app.services.model_router_policy import (
    reset_rotation_counter,
    route_and_execute,
)


# â”€â”€ Fixtures â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.fixture(autouse=True)
def _clean_singletons():
    """Reset singletons before/after each test."""
    reset_rotation_counter()
    reset_execution_gate()
    reset_registry()
    yield
    reset_rotation_counter()
    reset_execution_gate()
    reset_registry()


def _make_registry_with_two_providers() -> ProviderRegistry:
    """Build a registry where both localhost and model_machine are available."""
    registry = ProviderRegistry()

    # Localhost provider
    localhost = MagicMock()
    localhost.provider_id = Provider.LOCALHOST_LLM.value
    localhost.is_configured = True
    localhost.probe.return_value = ProbeResult(
        provider=Provider.LOCALHOST_LLM.value,
        configured=True,
        state=ProviderState.AVAILABLE.value,
        probe_success=True,
        status_reason="healthy",
    )
    localhost.execute.return_value = ProviderResult(
        provider=Provider.LOCALHOST_LLM.value,
        success=True,
        execution_status=ExecutionStatus.SUCCESS.value,
        content='{"label":"HOLD","summary":"localhost response"}',
        timing_ms=500.0,
    )
    registry.register(localhost)

    # Model machine provider
    model_machine = MagicMock()
    model_machine.provider_id = Provider.NETWORK_MODEL_MACHINE.value
    model_machine.is_configured = True
    model_machine.probe.return_value = ProbeResult(
        provider=Provider.NETWORK_MODEL_MACHINE.value,
        configured=True,
        state=ProviderState.AVAILABLE.value,
        probe_success=True,
        status_reason="healthy",
    )
    model_machine.execute.return_value = ProviderResult(
        provider=Provider.NETWORK_MODEL_MACHINE.value,
        success=True,
        execution_status=ExecutionStatus.SUCCESS.value,
        content='{"label":"HOLD","summary":"model_machine response"}',
        timing_ms=600.0,
    )
    registry.register(model_machine)

    return registry


def _make_request(task_type: str = "active_trade_model_analysis") -> ExecutionRequest:
    """Build a test ExecutionRequest for local_distributed mode."""
    return ExecutionRequest(
        mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
        task_type=task_type,
        prompt=[{"role": "user", "content": "Evaluate this position."}],
        system_prompt="You are a senior analyst.",
        routing_overrides={"max_tokens": 900, "temperature": 0.2},
    )


def _make_request_online(task_type: str = "monitor_narrative") -> ExecutionRequest:
    """Build a test ExecutionRequest for online_distributed mode."""
    return ExecutionRequest(
        mode=ExecutionMode.ONLINE_DISTRIBUTED.value,
        task_type=task_type,
        prompt=[{"role": "user", "content": "Evaluate this position."}],
        system_prompt="You are a senior analyst.",
        routing_overrides={"max_tokens": 600, "temperature": 0.2},
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Part A â€” Core round-robin rotation proof
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class TestRoundRobinRotation:
    """Prove that sequential requests alternate providers."""

    def test_sequential_requests_alternate_providers(self):
        """KEY TEST: 4 sequential requests should alternate between
        localhost_llm and network_model_machine."""
        registry = _make_registry_with_two_providers()
        gate = ProviderExecutionGate()

        selected_providers = []
        for i in range(4):
            request = _make_request()
            result, trace = route_and_execute(
                request, registry=registry, gate=gate,
            )
            assert result is not None, f"Request {i} returned None result"
            assert result.success, f"Request {i} failed: {result.error_message}"
            selected_providers.append(trace.selected_provider)
            print(f"  Request {i+1}: selected_provider = {trace.selected_provider}")

        # Must have used BOTH providers (not all localhost)
        unique = set(selected_providers)
        assert len(unique) == 2, (
            f"Expected 2 unique providers but got {unique}. "
            f"Routing is NOT rotating! Selections: {selected_providers}"
        )

        # Check alternation pattern
        assert selected_providers[0] != selected_providers[1], (
            f"First two requests should use different providers: {selected_providers}"
        )
        assert selected_providers[0] == selected_providers[2], (
            f"Requests 1 and 3 should use same provider: {selected_providers}"
        )

        print(f"\n  âœ“ Round-robin rotation working: {selected_providers}")

    def test_sequential_requests_online_distributed(self):
        """Same rotation applies to online_distributed mode."""
        registry = _make_registry_with_two_providers()
        gate = ProviderExecutionGate()

        selected_providers = []
        for i in range(4):
            request = _make_request_online()
            result, trace = route_and_execute(
                request, registry=registry, gate=gate,
            )
            assert result is not None and result.success
            selected_providers.append(trace.selected_provider)
            print(f"  Request {i+1}: selected_provider = {trace.selected_provider}")

        unique = set(selected_providers)
        assert len(unique) == 2, (
            f"Expected 2 unique providers but got {unique}. "
            f"Selections: {selected_providers}"
        )
        print(f"\n  âœ“ Online distributed rotation working: {selected_providers}")

    def test_direct_mode_no_rotation(self):
        """Direct mode (local) should NOT rotate â€” always use localhost."""
        registry = _make_registry_with_two_providers()
        gate = ProviderExecutionGate()

        selected_providers = []
        for i in range(3):
            request = ExecutionRequest(
                mode=ExecutionMode.LOCAL.value,
                task_type="test",
                prompt=[{"role": "user", "content": "test"}],
            )
            result, trace = route_and_execute(
                request, registry=registry, gate=gate,
            )
            assert result is not None and result.success
            selected_providers.append(trace.selected_provider)

        # All should be localhost (no rotation for direct mode)
        assert all(p == Provider.LOCALHOST_LLM.value for p in selected_providers)
        print(f"  âœ“ Direct mode correctly does NOT rotate: {selected_providers}")

    def test_rotation_falls_back_on_provider_failure(self):
        """If rotated-first provider is unavailable, falls to next."""
        registry = ProviderRegistry()

        # Localhost: unavailable
        localhost = MagicMock()
        localhost.provider_id = Provider.LOCALHOST_LLM.value
        localhost.is_configured = True
        localhost.probe.return_value = ProbeResult(
            provider=Provider.LOCALHOST_LLM.value,
            configured=True,
            state=ProviderState.UNAVAILABLE.value,
            probe_success=False,
            status_reason="connection refused",
        )
        registry.register(localhost)

        # Model machine: available
        model_machine = MagicMock()
        model_machine.provider_id = Provider.NETWORK_MODEL_MACHINE.value
        model_machine.is_configured = True
        model_machine.probe.return_value = ProbeResult(
            provider=Provider.NETWORK_MODEL_MACHINE.value,
            configured=True,
            state=ProviderState.AVAILABLE.value,
            probe_success=True,
            status_reason="healthy",
        )
        model_machine.execute.return_value = ProviderResult(
            provider=Provider.NETWORK_MODEL_MACHINE.value,
            success=True,
            execution_status=ExecutionStatus.SUCCESS.value,
            content="ok",
        )
        registry.register(model_machine)

        gate = ProviderExecutionGate()

        # Even if rotation puts localhost first, it should fallback to model_machine
        for i in range(3):
            request = _make_request()
            result, trace = route_and_execute(
                request, registry=registry, gate=gate,
            )
            assert result is not None and result.success
            assert trace.selected_provider == Provider.NETWORK_MODEL_MACHINE.value
            print(f"  Request {i+1}: selected_provider = {trace.selected_provider} (fallback)")

        print("  âœ“ Fallback works correctly when rotated provider is down")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Part B â€” Integration with execute_routed_model
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class TestRoutedModelRotation:
    """Verify rotation works through the execute_routed_model integration layer."""

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    def test_execute_routed_model_rotation(self, _mock_enabled):
        """execute_routed_model should see rotation across sequential calls."""
        from app.services.model_routing_integration import execute_routed_model

        registry = _make_registry_with_two_providers()
        gate = ProviderExecutionGate()

        # Patch route_and_execute to use our registry and gate
        with patch(
            "app.services.model_router.route_and_execute",
            side_effect=lambda req, **kw: route_and_execute(
                req, registry=registry, gate=gate, timeout=kw.get("timeout"),
            ),
        ):
            selected_providers = []
            for i in range(4):
                result, trace = execute_routed_model(
                    task_type="active_trade_model_analysis",
                    messages=[{"role": "user", "content": f"Position data {i}"}],
                    system_prompt="You are a senior analyst.",
                    timeout=90.0,
                    max_tokens=900,
                    temperature=0.2,
                    metadata={"symbol": "AAPL", "request_num": i},
                )
                assert result["status"] == "success"
                selected_providers.append(trace.selected_provider)
                print(f"  Routed request {i+1}: provider = {trace.selected_provider}")

            unique = set(selected_providers)
            assert len(unique) == 2, (
                f"Expected rotation across 2 providers: {selected_providers}"
            )
            print(f"\n  âœ“ execute_routed_model rotation working: {selected_providers}")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Part C â€” Model analysis simulation (full pipeline)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class TestModelAnalysisRoutingSimulation:
    """Simulate the exact scenario the user reported: stock scanner
    model analysis requests all going to localhost."""

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    @patch("app.services.execution_mode_state.get_execution_mode", return_value="local_distributed")
    def test_stock_scanner_model_analysis_distributes(self, _mock_mode, _mock_enabled):
        """Multiple model analysis calls (like a stock scanner batch)
        should distribute across both providers."""
        from app.services.model_routing_integration import execute_routed_model

        registry = _make_registry_with_two_providers()
        gate = ProviderExecutionGate()

        symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"]

        with patch(
            "app.services.model_router.route_and_execute",
            side_effect=lambda req, **kw: route_and_execute(
                req, registry=registry, gate=gate, timeout=kw.get("timeout"),
            ),
        ):
            results = []
            for symbol in symbols:
                result, trace = execute_routed_model(
                    task_type="stock_analysis",
                    messages=[{"role": "user", "content": f"Analyze {symbol}"}],
                    system_prompt="You are a senior analyst.",
                    timeout=180.0,
                    max_tokens=900,
                    temperature=0.2,
                    metadata={"symbol": symbol},
                )
                results.append({
                    "symbol": symbol,
                    "provider": trace.selected_provider,
                    "status": result["status"],
                })
                print(f"  {symbol}: provider = {trace.selected_provider}")

            # Count per provider
            localhost_count = sum(1 for r in results if r["provider"] == Provider.LOCALHOST_LLM.value)
            model_machine_count = sum(1 for r in results if r["provider"] == Provider.NETWORK_MODEL_MACHINE.value)

            print(f"\n  Summary:")
            print(f"    localhost_llm:          {localhost_count} requests")
            print(f"    network_model_machine:  {model_machine_count} requests")

            assert localhost_count > 0, "Expected at least one request to localhost"
            assert model_machine_count > 0, "Expected at least one request to model machine"
            assert localhost_count == 3 and model_machine_count == 3, (
                f"Expected even 3/3 split but got {localhost_count}/{model_machine_count}"
            )
            print(f"  [OK] Balanced distribution: {localhost_count}/{model_machine_count}")


# ═══════════════════════════════════════════════════════════════════════
# Part D — Cascade elimination proof (Step 21)
# ═══════════════════════════════════════════════════════════════════════

class TestCascadeElimination:
    """Prove that routed_tmc_final_decision does NOT cascade to
    analyze_tmc_final_decision on parse failure — avoiding double
    model calls per candidate."""

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    def test_parse_failure_does_not_cascade_to_legacy(self, _mock_enabled):
        """When routing succeeds but JSON parse fails, routed_tmc_final_decision
        should do retry-with-fix inline and NOT call analyze_tmc_final_decision."""
        from app.services.model_routing_integration import routed_tmc_final_decision

        execute_call_count = 0
        original_execute = None

        def counting_execute(*args, **kwargs):
            nonlocal execute_call_count
            execute_call_count += 1
            # Return unparseable content on first call, valid JSON on fix
            if execute_call_count == 1:
                return (
                    {"status": "success", "content": "This is not valid JSON at all"},
                    MagicMock(
                        request_id="req-001",
                        selected_provider="localhost_llm",
                        timing_ms=500.0,
                    ),
                )
            else:
                # Fix attempt — return valid JSON
                valid_json = json.dumps({
                    "decision": "EXECUTE",
                    "conviction": 75,
                    "decision_summary": "Fixed response",
                    "factors_considered": [],
                    "market_alignment": {"overall": "neutral"},
                    "risk_assessment": {"primary_risks": []},
                    "engine_comparison": {},
                })
                return (
                    {"status": "success", "content": valid_json},
                    MagicMock(
                        request_id="req-002",
                        selected_provider="network_model_machine",
                        timing_ms=400.0,
                    ),
                )

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=counting_execute,
        ), patch(
            "common.model_analysis.analyze_tmc_final_decision",
        ) as mock_legacy:
            result = routed_tmc_final_decision(
                candidate={"symbol": "AAPL", "composite_score": 80},
                market_picture_context=None,
                strategy_id="stock_pullback_swing",
                retries=2,
            )

        # Should NOT have called analyze_tmc_final_decision (no cascade)
        mock_legacy.assert_not_called()

        # Should have made exactly 2 routed calls (original + fix)
        assert execute_call_count == 2, (
            f"Expected 2 execute_routed_model calls but got {execute_call_count}"
        )

        # Fix should have succeeded
        assert result["decision"] == "EXECUTE"
        assert result.get("_routed") is True
        print(f"  [OK] No cascade: {execute_call_count} calls (original + fix), legacy never called")

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    def test_total_parse_failure_returns_fallback_no_cascade(self, _mock_enabled):
        """When both original and fix parse fail, return fallback PASS
        without cascading to analyze_tmc_final_decision."""
        from app.services.model_routing_integration import routed_tmc_final_decision

        execute_call_count = 0

        def counting_execute(*args, **kwargs):
            nonlocal execute_call_count
            execute_call_count += 1
            return (
                {"status": "success", "content": "Not JSON response attempt " + str(execute_call_count)},
                MagicMock(
                    request_id=f"req-{execute_call_count:03d}",
                    selected_provider="localhost_llm",
                    timing_ms=500.0,
                ),
            )

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=counting_execute,
        ), patch(
            "common.model_analysis.analyze_tmc_final_decision",
        ) as mock_legacy:
            result = routed_tmc_final_decision(
                candidate={"symbol": "TSLA", "composite_score": 60},
                market_picture_context=None,
                strategy_id="stock_momentum_breakout",
                retries=2,
            )

        mock_legacy.assert_not_called()
        assert execute_call_count == 2, (
            f"Expected 2 calls (original + fix) but got {execute_call_count}"
        )
        assert result["decision"] == "PASS"
        assert result.get("_fallback") is True
        assert result.get("_routed") is True
        print(f"  [OK] Total failure: {execute_call_count} calls, fallback PASS, no cascade")

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    def test_successful_parse_makes_single_call(self, _mock_enabled):
        """When routing succeeds AND JSON parses on first try, only 1 model call."""
        from app.services.model_routing_integration import routed_tmc_final_decision

        execute_call_count = 0

        def counting_execute(*args, **kwargs):
            nonlocal execute_call_count
            execute_call_count += 1
            valid_json = json.dumps({
                "decision": "EXECUTE",
                "conviction": 85,
                "decision_summary": "Strong setup",
                "factors_considered": [{"factor": "trend", "assessment": "bullish"}],
                "market_alignment": {"overall": "favorable"},
                "risk_assessment": {"primary_risks": ["Gap risk"]},
                "engine_comparison": {"engine_score": 80, "model_score": 85},
            })
            return (
                {"status": "success", "content": valid_json},
                MagicMock(
                    request_id="req-001",
                    selected_provider="localhost_llm",
                    timing_ms=300.0,
                ),
            )

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=counting_execute,
        ):
            result = routed_tmc_final_decision(
                candidate={"symbol": "NVDA", "composite_score": 90},
                market_picture_context=None,
                strategy_id="stock_volatility_expansion",
                retries=2,
            )

        assert execute_call_count == 1, (
            f"Expected exactly 1 call for clean parse but got {execute_call_count}"
        )
        assert result["decision"] == "EXECUTE"
        assert result["conviction"] == 85
        assert result.get("_routed") is True
        print(f"  [OK] Clean parse: single model call, no retry needed")

    @patch("app.services.model_routing_integration._routing_is_enabled", return_value=True)
    def test_routing_failure_still_cascades_to_legacy(self, _mock_enabled):
        """When routing infrastructure fails (exception), cascading to
        legacy analyze_tmc_final_decision is still allowed — this is a
        legitimate fallback for infrastructure failures."""
        from app.services.model_routing_integration import routed_tmc_final_decision

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RuntimeError("Routing infrastructure down"),
        ), patch(
            "common.model_analysis.analyze_tmc_final_decision",
            return_value={"decision": "PASS", "conviction": 50},
        ) as mock_legacy:
            result = routed_tmc_final_decision(
                candidate={"symbol": "AMZN", "composite_score": 70},
                market_picture_context=None,
                strategy_id="stock_mean_reversion",
                retries=2,
            )

        # Infrastructure failure SHOULD cascade to legacy
        mock_legacy.assert_called_once()
        assert result["decision"] == "PASS"
        print(f"  [OK] Infrastructure failure correctly cascades to legacy")

