"""Tests for Step 6 — Bedrock integration (standard + premium execution path).

Covers:
    Part A: Bedrock provider configured vs unconfigured
    Part B: Bedrock execution success normalization
    Part C: Bedrock execution error normalization
    Part D: premium_online resolves to Bedrock path
    Part E: online_distributed can fall through to Bedrock
    Part F: premium_override activates Bedrock path
    Part G: Provider registry reflects Bedrock status
    Part H: Execution gate applies to Bedrock
    Part I: Bedrock request/response formatting
    Part J: Bedrock error classification
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.services.model_execution_gate import ProviderExecutionGate
from app.services.model_provider_base import ProbeResult, ProviderResult
from app.services.model_provider_adapters import (
    BedrockTitanNovaProProvider,
    _build_bedrock_messages,
    _classify_bedrock_error,
    _extract_content_from_converse_response,
)
from app.services.model_provider_registry import ProviderRegistry
from app.services.model_router_policy import route_and_execute
from app.services.model_routing_contract import (
    ExecutionMode,
    ExecutionRequest,
    ExecutionStatus,
    Provider,
    ProviderState,
    RouteResolutionStatus,
    mode_to_default_provider_order,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _request(
    mode: str = ExecutionMode.ONLINE_DISTRIBUTED.value,
    **kwargs: Any,
) -> ExecutionRequest:
    return ExecutionRequest(
        mode=mode,
        model_name=kwargs.pop("model_name", None),
        task_type=kwargs.pop("task_type", None),
        prompt=kwargs.pop("prompt", [{"role": "user", "content": "test"}]),
        system_prompt=kwargs.pop("system_prompt", None),
        override_mode=kwargs.pop("override_mode", None),
        preferred_provider=kwargs.pop("preferred_provider", None),
        premium_override=kwargs.pop("premium_override", False),
        routing_overrides=kwargs.pop("routing_overrides", {}),
        metadata=kwargs.pop("metadata", {}),
    )


def _make_mock_adapter(provider_id: str, *, configured: bool = True,
                       probe_state: str = ProviderState.AVAILABLE.value,
                       execute_success: bool = True,
                       execute_content: str = "mock response"):
    """Create a mock adapter with configurable behavior."""
    adapter = MagicMock()
    adapter.provider_id = provider_id
    adapter.is_configured = configured

    adapter.probe.return_value = ProbeResult(
        provider=provider_id,
        configured=configured,
        state=probe_state if configured else ProviderState.UNAVAILABLE.value,
        probe_success=True,
        status_reason="mock probe",
    )

    if execute_success:
        adapter.execute.return_value = ProviderResult(
            provider=provider_id,
            success=True,
            execution_status=ExecutionStatus.SUCCESS.value,
            content=execute_content,
            raw_response={"mock": True},
            timing_ms=50.0,
            provider_state_observed=ProviderState.AVAILABLE.value,
        )
    else:
        adapter.execute.return_value = ProviderResult(
            provider=provider_id,
            success=False,
            execution_status=ExecutionStatus.FAILED.value,
            error_code="connection_error",
            error_message="mock connection error",
            timing_ms=10.0,
            provider_state_observed=ProviderState.UNAVAILABLE.value,
        )
    return adapter


def _converse_response(text: str = "Hello from Bedrock",
                       stop_reason: str = "end_turn",
                       input_tokens: int = 10,
                       output_tokens: int = 20) -> dict:
    """Build a mock Bedrock Converse API response."""
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": text}],
            },
        },
        "stopReason": stop_reason,
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
        },
    }


# =========================================================================
# Part A — Bedrock provider configured vs unconfigured
# =========================================================================

class TestBedrockConfigured:
    """BedrockTitanNovaProProvider respects configuration state."""

    def test_provider_id(self):
        provider = BedrockTitanNovaProProvider()
        assert provider.provider_id == Provider.BEDROCK_TITAN_NOVA_PRO.value

    @patch("app.services.model_provider_adapters.get_settings")
    def test_configured_when_enabled_and_client_ok(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = True
        settings.BEDROCK_REGION = "us-east-1"
        mock_settings.return_value = settings

        provider = BedrockTitanNovaProProvider()
        with patch.object(provider, "_ensure_client", return_value=MagicMock()):
            assert provider.is_configured is True

    @patch("app.services.model_provider_adapters.get_settings")
    def test_not_configured_when_disabled(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        mock_settings.return_value = settings

        provider = BedrockTitanNovaProProvider()
        assert provider.is_configured is False

    @patch("app.services.model_provider_adapters.get_settings")
    def test_not_configured_when_client_fails(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = True
        settings.BEDROCK_REGION = "us-east-1"
        mock_settings.return_value = settings

        provider = BedrockTitanNovaProProvider()
        with patch.object(provider, "_ensure_client", return_value=None):
            provider._client_error = "boto3 client creation failed"
            assert provider.is_configured is False


class TestBedrockProbe:
    """Probe is config-level only and honest about limitations."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_probe_disabled(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        mock_settings.return_value = settings

        provider = BedrockTitanNovaProProvider()
        result = provider.probe()

        assert result.configured is False
        assert result.state == ProviderState.UNAVAILABLE.value
        assert "BEDROCK_ENABLED=false" in result.status_reason
        assert result.metadata.get("probe_type") == "config_only"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_probe_enabled_client_ok(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = True
        settings.BEDROCK_REGION = "us-east-1"
        settings.BEDROCK_MODEL_ID = "us.amazon.nova-pro-v1:0"
        mock_settings.return_value = settings

        provider = BedrockTitanNovaProProvider()
        with patch.object(provider, "_ensure_client", return_value=MagicMock()):
            result = provider.probe()

        assert result.configured is True
        assert result.state == ProviderState.AVAILABLE.value
        assert result.metadata.get("probe_type") == "config_only"
        assert "config-level" in result.status_reason
        assert result.metadata.get("region") == "us-east-1"
        assert result.metadata.get("model_id") == "us.amazon.nova-pro-v1:0"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_probe_client_creation_failed(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = True
        settings.BEDROCK_REGION = "us-east-1"
        mock_settings.return_value = settings

        provider = BedrockTitanNovaProProvider()
        provider._client = None
        provider._client_initialised = True
        provider._client_error = "boto3 client creation failed: no credentials"

        result = provider.probe()

        assert result.configured is False
        assert result.state == ProviderState.UNAVAILABLE.value
        assert "boto3" in result.status_reason


# =========================================================================
# Part B — Bedrock execution success normalization
# =========================================================================

class TestBedrockExecutionSuccess:
    """Successful Bedrock execution normalizes into ProviderResult."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_execute_success(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = True
        settings.BEDROCK_REGION = "us-east-1"
        settings.BEDROCK_MODEL_ID = "us.amazon.nova-pro-v1:0"
        settings.BEDROCK_TIMEOUT_SECONDS = 120.0
        mock_settings.return_value = settings

        mock_client = MagicMock()
        mock_client.converse.return_value = _converse_response("Test response")

        provider = BedrockTitanNovaProProvider()
        provider._client = mock_client
        provider._client_initialised = True

        req = _request()
        result = provider.execute(req)

        assert result.success is True
        assert result.execution_status == ExecutionStatus.SUCCESS.value
        assert result.content == "Test response"
        assert result.provider == Provider.BEDROCK_TITAN_NOVA_PRO.value
        assert result.timing_ms is not None and result.timing_ms >= 0
        assert result.provider_state_observed == ProviderState.AVAILABLE.value
        assert result.metadata.get("model_id") == "us.amazon.nova-pro-v1:0"
        assert result.metadata.get("stop_reason") == "end_turn"
        assert result.metadata.get("region") == "us-east-1"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_execute_uses_request_model_name(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = True
        settings.BEDROCK_REGION = "us-east-1"
        settings.BEDROCK_MODEL_ID = "us.amazon.nova-pro-v1:0"
        settings.BEDROCK_TIMEOUT_SECONDS = 120.0
        mock_settings.return_value = settings

        mock_client = MagicMock()
        mock_client.converse.return_value = _converse_response()

        provider = BedrockTitanNovaProProvider()
        provider._client = mock_client
        provider._client_initialised = True

        req = _request(model_name="custom-model-id")
        provider.execute(req)

        # Verify the converse call used the custom model ID.
        call_kwargs = mock_client.converse.call_args
        assert call_kwargs[1]["modelId"] == "custom-model-id" or \
               call_kwargs.kwargs.get("modelId") == "custom-model-id"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_execute_includes_usage_in_metadata(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = True
        settings.BEDROCK_REGION = "us-east-1"
        settings.BEDROCK_MODEL_ID = "us.amazon.nova-pro-v1:0"
        settings.BEDROCK_TIMEOUT_SECONDS = 120.0
        mock_settings.return_value = settings

        mock_client = MagicMock()
        mock_client.converse.return_value = _converse_response(
            input_tokens=50, output_tokens=100,
        )

        provider = BedrockTitanNovaProProvider()
        provider._client = mock_client
        provider._client_initialised = True

        req = _request()
        result = provider.execute(req)

        assert result.metadata["usage"]["inputTokens"] == 50
        assert result.metadata["usage"]["outputTokens"] == 100


# =========================================================================
# Part C — Bedrock execution error normalization
# =========================================================================

class TestBedrockExecutionError:
    """Failed Bedrock execution normalizes errors into ProviderResult."""

    @patch("app.services.model_provider_adapters.get_settings")
    def test_execute_disabled(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        mock_settings.return_value = settings

        provider = BedrockTitanNovaProProvider()
        result = provider.execute(_request())

        assert result.success is False
        assert result.execution_status == ExecutionStatus.SKIPPED.value
        assert result.error_code == "not_configured"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_execute_client_unavailable(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = True
        settings.BEDROCK_REGION = "us-east-1"
        mock_settings.return_value = settings

        provider = BedrockTitanNovaProProvider()
        provider._client = None
        provider._client_initialised = True
        provider._client_error = "no credentials"

        result = provider.execute(_request())

        assert result.success is False
        assert result.execution_status == ExecutionStatus.FAILED.value
        assert result.error_code == "not_configured"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_execute_api_exception(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = True
        settings.BEDROCK_REGION = "us-east-1"
        settings.BEDROCK_MODEL_ID = "us.amazon.nova-pro-v1:0"
        settings.BEDROCK_TIMEOUT_SECONDS = 120.0
        mock_settings.return_value = settings

        mock_client = MagicMock()
        mock_client.converse.side_effect = RuntimeError("API exploded")

        provider = BedrockTitanNovaProProvider()
        provider._client = mock_client
        provider._client_initialised = True

        result = provider.execute(_request())

        assert result.success is False
        assert result.execution_status == ExecutionStatus.FAILED.value
        assert "RuntimeError" in (result.error_message or "")
        assert result.timing_ms is not None and result.timing_ms >= 0
        assert result.metadata.get("exception_type") == "RuntimeError"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_execute_raw_response_on_success(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = True
        settings.BEDROCK_REGION = "us-east-1"
        settings.BEDROCK_MODEL_ID = "us.amazon.nova-pro-v1:0"
        settings.BEDROCK_TIMEOUT_SECONDS = 120.0
        mock_settings.return_value = settings

        response = _converse_response("raw test")
        mock_client = MagicMock()
        mock_client.converse.return_value = response

        provider = BedrockTitanNovaProProvider()
        provider._client = mock_client
        provider._client_initialised = True

        result = provider.execute(_request())

        assert result.raw_response is response


# =========================================================================
# Part D — premium_online resolves to Bedrock path
# =========================================================================

class TestPremiumOnlineMapsToBedrockPath:
    """premium_online mode now has bedrock_titan_nova_pro as provider."""

    def test_premium_online_provider_order(self):
        order = mode_to_default_provider_order(ExecutionMode.PREMIUM_ONLINE.value)
        assert order == [Provider.BEDROCK_TITAN_NOVA_PRO.value]

    def test_premium_online_uses_bedrock_when_configured(self):
        """premium_online dispatches to Bedrock adapter when available."""
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
            execute_content="premium response",
        )
        reg = ProviderRegistry()
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        req = _request(mode=ExecutionMode.PREMIUM_ONLINE.value)
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is not None
        assert result.success is True
        assert result.content == "premium response"
        assert trace.selected_provider == Provider.BEDROCK_TITAN_NOVA_PRO.value
        assert trace.resolved_mode == ExecutionMode.PREMIUM_ONLINE.value

    def test_premium_online_is_strict_mode(self):
        """premium_online is a direct mode — no fallback chain."""
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=False,
        )
        reg = ProviderRegistry()
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        req = _request(mode=ExecutionMode.PREMIUM_ONLINE.value)
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is None
        # No fallback to local providers.
        assert trace.attempted_providers == []

    def test_premium_online_bedrock_unconfigured_honest_trace(self):
        """When Bedrock is unconfigured, premium_online fails honestly."""
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=False,
        )
        reg = ProviderRegistry()
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        req = _request(mode=ExecutionMode.PREMIUM_ONLINE.value)
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is None
        assert trace.route_resolution == RouteResolutionStatus.NO_CANDIDATES.value
        # Decision log should show bedrock was skipped as not_configured.
        skip_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "skipped"
            and e.get("provider") == Provider.BEDROCK_TITAN_NOVA_PRO.value
        ]
        assert len(skip_entries) == 1
        assert skip_entries[0]["reason"] == "not_configured"


# =========================================================================
# Part E — online_distributed can fall through to Bedrock
# =========================================================================

class TestOnlineDistributedBedrockFallback:
    """online_distributed uses Bedrock as final fallback."""

    def test_online_distributed_order_includes_bedrock(self):
        order = mode_to_default_provider_order(ExecutionMode.ONLINE_DISTRIBUTED.value)
        assert order == [
            Provider.LOCALHOST_LLM.value,
            Provider.NETWORK_MODEL_MACHINE.value,
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
        ]

    def test_falls_through_to_bedrock_when_locals_fail(self):
        """When localhost and model_machine fail, Bedrock handles the request."""
        localhost = _make_mock_adapter(
            Provider.LOCALHOST_LLM.value,
            configured=True,
            execute_success=False,
        )
        model_machine = _make_mock_adapter(
            Provider.NETWORK_MODEL_MACHINE.value,
            configured=True,
            execute_success=False,
        )
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
            execute_content="Bedrock fallback",
        )

        reg = ProviderRegistry()
        reg.register(localhost)
        reg.register(model_machine)
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        req = _request(mode=ExecutionMode.ONLINE_DISTRIBUTED.value)
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is not None
        assert result.success is True
        assert result.content == "Bedrock fallback"
        assert trace.selected_provider == Provider.BEDROCK_TITAN_NOVA_PRO.value
        assert trace.fallback_used is True

    def test_falls_through_to_bedrock_when_locals_unavailable(self):
        """When localhost and model_machine are unavailable, Bedrock handles."""
        localhost = _make_mock_adapter(
            Provider.LOCALHOST_LLM.value,
            configured=True,
            probe_state=ProviderState.UNAVAILABLE.value,
        )
        model_machine = _make_mock_adapter(
            Provider.NETWORK_MODEL_MACHINE.value,
            configured=True,
            probe_state=ProviderState.UNAVAILABLE.value,
        )
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
            execute_content="Bedrock after unavailable",
        )

        reg = ProviderRegistry()
        reg.register(localhost)
        reg.register(model_machine)
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        req = _request(mode=ExecutionMode.ONLINE_DISTRIBUTED.value)
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is not None
        assert result.success is True
        assert trace.selected_provider == Provider.BEDROCK_TITAN_NOVA_PRO.value

    def test_uses_localhost_first_when_available(self):
        """When localhost is healthy, Bedrock is not used."""
        localhost = _make_mock_adapter(
            Provider.LOCALHOST_LLM.value,
            configured=True,
            execute_content="localhost response",
        )
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
        )

        reg = ProviderRegistry()
        reg.register(localhost)
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        req = _request(mode=ExecutionMode.ONLINE_DISTRIBUTED.value)
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is True
        assert trace.selected_provider == Provider.LOCALHOST_LLM.value
        bedrock.execute.assert_not_called()


# =========================================================================
# Part F — premium_override activates Bedrock path
# =========================================================================

class TestPremiumOverrideActivatesBedrock:
    """premium_override=True can route to Bedrock."""

    def test_premium_override_routes_to_bedrock(self):
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
            execute_content="premium override response",
        )
        reg = ProviderRegistry()
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        req = _request(
            mode=ExecutionMode.LOCAL.value,
            premium_override=True,
        )
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is not None
        assert result.success is True
        assert result.content == "premium override response"
        assert trace.resolved_mode == ExecutionMode.PREMIUM_ONLINE.value
        assert trace.selected_provider == Provider.BEDROCK_TITAN_NOVA_PRO.value
        assert trace.route_resolution == RouteResolutionStatus.OVERRIDE_APPLIED.value

    def test_premium_override_bedrock_unconfigured(self):
        """premium_override with unconfigured Bedrock fails honestly."""
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=False,
        )
        reg = ProviderRegistry()
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        req = _request(
            mode=ExecutionMode.LOCAL.value,
            premium_override=True,
        )
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is None
        assert trace.resolved_mode == ExecutionMode.PREMIUM_ONLINE.value
        # Bedrock was there but not configured.
        assert trace.route_resolution == RouteResolutionStatus.NO_CANDIDATES.value

    def test_premium_override_trace_shows_bedrock_dispatch(self):
        """Trace shows Bedrock was dispatched via premium_override."""
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
            execute_content="traced",
        )
        reg = ProviderRegistry()
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        req = _request(
            mode=ExecutionMode.LOCAL_DISTRIBUTED.value,
            premium_override=True,
        )
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        # Should have override_resolution entry.
        override_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "override_resolution"
        ]
        assert len(override_entries) >= 1
        assert override_entries[0].get("premium_override") == "true"

        # Should have dispatch entry for Bedrock.
        dispatch_entries = [
            e for e in trace.route_decision_log
            if e.get("action") == "dispatched"
            and e.get("provider") == Provider.BEDROCK_TITAN_NOVA_PRO.value
        ]
        assert len(dispatch_entries) == 1


# =========================================================================
# Part G — Provider registry reflects Bedrock status
# =========================================================================

class TestRegistryBedrockStatus:
    """Registry correctly reflects Bedrock configuration status."""

    def test_registry_lists_bedrock(self):
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
        )
        reg = ProviderRegistry()
        reg.register(bedrock)

        assert Provider.BEDROCK_TITAN_NOVA_PRO.value in reg.list_registered()

    def test_registry_status_configured(self):
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
        )
        reg = ProviderRegistry()
        reg.register(bedrock)

        status = reg.get_provider_status(Provider.BEDROCK_TITAN_NOVA_PRO.value)
        assert status.registered is True
        assert status.configured is True

    def test_registry_status_unconfigured(self):
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=False,
        )
        reg = ProviderRegistry()
        reg.register(bedrock)

        status = reg.get_provider_status(Provider.BEDROCK_TITAN_NOVA_PRO.value)
        assert status.registered is True
        assert status.configured is False
        assert status.state == ProviderState.UNAVAILABLE.value

    def test_registry_probe_configured(self):
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
        )
        reg = ProviderRegistry()
        reg.register(bedrock)

        probe = reg.probe_provider(Provider.BEDROCK_TITAN_NOVA_PRO.value)
        assert probe.configured is True
        assert probe.state == ProviderState.AVAILABLE.value

    def test_registry_probe_unconfigured(self):
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=False,
        )
        reg = ProviderRegistry()
        reg.register(bedrock)

        probe = reg.probe_provider(Provider.BEDROCK_TITAN_NOVA_PRO.value)
        assert probe.configured is False


# =========================================================================
# Part H — Execution gate applies to Bedrock
# =========================================================================

class TestBedrockGateSemantics:
    """Bedrock goes through the same execution gate as other providers."""

    def test_bedrock_gate_blocks_when_at_capacity(self):
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
        )
        reg = ProviderRegistry()
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        # Fill the slot.
        gate.acquire(Provider.BEDROCK_TITAN_NOVA_PRO.value)

        req = _request(mode=ExecutionMode.PREMIUM_ONLINE.value)
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result is None
        # Decision log should show slot denied.
        slot_denied = [
            e for e in trace.route_decision_log
            if e.get("reason") in ("at_max_concurrency", "slot_acquisition_failed")
        ]
        assert len(slot_denied) >= 1

        gate.release(Provider.BEDROCK_TITAN_NOVA_PRO.value)

    def test_bedrock_gate_default_concurrency(self):
        gate = ProviderExecutionGate()
        assert gate.get_max_concurrency(Provider.BEDROCK_TITAN_NOVA_PRO.value) == 1

    def test_bedrock_gate_releases_on_success(self):
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
        )
        reg = ProviderRegistry()
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        req = _request(mode=ExecutionMode.PREMIUM_ONLINE.value)
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is True
        assert gate.in_flight_count(Provider.BEDROCK_TITAN_NOVA_PRO.value) == 0

    def test_bedrock_gate_releases_on_failure(self):
        bedrock = _make_mock_adapter(
            Provider.BEDROCK_TITAN_NOVA_PRO.value,
            configured=True,
            execute_success=False,
        )
        reg = ProviderRegistry()
        reg.register(bedrock)
        gate = ProviderExecutionGate()

        req = _request(mode=ExecutionMode.PREMIUM_ONLINE.value)
        result, trace = route_and_execute(req, registry=reg, gate=gate)

        assert result.success is False
        assert gate.in_flight_count(Provider.BEDROCK_TITAN_NOVA_PRO.value) == 0


# =========================================================================
# Part I — Bedrock request/response formatting
# =========================================================================

class TestBedrockMessageFormatting:
    """_build_bedrock_messages translates ExecutionRequest correctly."""

    def test_user_message(self):
        req = _request(prompt=[{"role": "user", "content": "Hello"}])
        messages, system = _build_bedrock_messages(req)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == [{"text": "Hello"}]
        assert system == []

    def test_system_prompt_from_field(self):
        req = _request(system_prompt="You are helpful", prompt=[{"role": "user", "content": "Hi"}])
        messages, system = _build_bedrock_messages(req)

        assert len(messages) == 1
        assert len(system) == 1
        assert system[0]["text"] == "You are helpful"

    def test_system_role_in_prompt_moves_to_system(self):
        req = _request(
            prompt=[
                {"role": "system", "content": "System msg"},
                {"role": "user", "content": "User msg"},
            ],
        )
        messages, system = _build_bedrock_messages(req)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert len(system) == 1
        assert system[0]["text"] == "System msg"

    def test_empty_prompt_creates_empty_user_message(self):
        req = _request(prompt=None)
        messages, system = _build_bedrock_messages(req)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_multi_turn_conversation(self):
        req = _request(
            prompt=[
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First answer"},
                {"role": "user", "content": "Follow up"},
            ],
        )
        messages, system = _build_bedrock_messages(req)

        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "user"


class TestBedrockResponseExtraction:
    """_extract_content_from_converse_response handles various shapes."""

    def test_normal_response(self):
        resp = _converse_response("Test content")
        assert _extract_content_from_converse_response(resp) == "Test content"

    def test_missing_output(self):
        assert _extract_content_from_converse_response({}) is None

    def test_missing_message(self):
        assert _extract_content_from_converse_response({"output": {}}) is None

    def test_empty_content_list(self):
        resp = {"output": {"message": {"content": []}}}
        assert _extract_content_from_converse_response(resp) is None

    def test_non_dict_content_block(self):
        resp = {"output": {"message": {"content": ["not a dict"]}}}
        assert _extract_content_from_converse_response(resp) is None


# =========================================================================
# Part J — Bedrock error classification
# =========================================================================

class TestBedrockErrorClassification:
    """_classify_bedrock_error maps exceptions to structured tuples."""

    def test_throttling(self):
        exc = type("ClientError", (Exception,), {
            "response": {"Error": {"Code": "ThrottlingException"}},
        })()
        code, status, state = _classify_bedrock_error(exc)
        assert code == "throttled"
        assert state == ProviderState.BUSY.value

    def test_service_unavailable(self):
        exc = type("ClientError", (Exception,), {
            "response": {"Error": {"Code": "ServiceUnavailableException"}},
        })()
        code, status, state = _classify_bedrock_error(exc)
        assert code == "service_unavailable"
        assert state == ProviderState.UNAVAILABLE.value

    def test_access_denied(self):
        exc = type("ClientError", (Exception,), {
            "response": {"Error": {"Code": "AccessDeniedException"}},
        })()
        code, status, state = _classify_bedrock_error(exc)
        assert code == "access_denied"
        assert state == ProviderState.FAILED.value

    def test_validation_error(self):
        exc = type("ClientError", (Exception,), {
            "response": {"Error": {"Code": "ValidationException"}},
        })()
        code, status, state = _classify_bedrock_error(exc)
        assert code == "validation_error"
        # Provider is still available — it's a request issue.
        assert state == ProviderState.AVAILABLE.value

    def test_model_timeout(self):
        exc = type("ClientError", (Exception,), {
            "response": {"Error": {"Code": "ModelTimeoutException"}},
        })()
        code, status, state = _classify_bedrock_error(exc)
        assert code == "timeout"
        assert status == ExecutionStatus.TIMEOUT.value

    def test_read_timeout_error(self):
        exc = type("ReadTimeoutError", (Exception,), {})()
        code, status, state = _classify_bedrock_error(exc)
        assert code == "timeout"
        assert status == ExecutionStatus.TIMEOUT.value

    def test_connection_error(self):
        exc = type("EndpointConnectionError", (Exception,), {})()
        code, status, state = _classify_bedrock_error(exc)
        assert code == "connection_error"
        assert state == ProviderState.UNAVAILABLE.value

    def test_generic_exception(self):
        exc = RuntimeError("something")
        code, status, state = _classify_bedrock_error(exc)
        assert code == "request_error"
        assert status == ExecutionStatus.FAILED.value

    def test_model_not_ready(self):
        exc = type("ClientError", (Exception,), {
            "response": {"Error": {"Code": "ModelNotReadyException"}},
        })()
        code, status, state = _classify_bedrock_error(exc)
        assert code == "model_not_ready"
        assert state == ProviderState.UNAVAILABLE.value
