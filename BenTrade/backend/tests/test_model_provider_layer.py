"""Tests for the provider abstraction layer (Step 2).

Covers:
    • model_provider_base: ProviderResult, legacy bridge, content extraction
    • model_provider_adapters: localhost, model_machine, bedrock stub
    • model_provider_registry: registration, lookup, status snapshots
    • model_router.execute_with_provider: seam correctness
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.model_provider_base import (
    ModelProviderBase,
    ProviderResult,
    extract_content_from_openai_response,
    get_provider_endpoint,
    is_legacy_source_enabled,
    legacy_source_to_provider,
    provider_to_legacy_source,
)
from app.services.model_provider_adapters import (
    BedrockTitanNovaProProvider,
    LocalhostLLMProvider,
    NetworkModelMachineProvider,
)
from app.services.model_provider_registry import (
    ProviderRegistry,
    ProviderStatusSnapshot,
    get_registry,
    reset_registry,
)
from app.services.model_routing_contract import (
    ExecutionRequest,
    ExecutionStatus,
    Provider,
    ProviderState,
)


# ══════════════════════════════════════════════════════════════════════════
# Provider Result
# ══════════════════════════════════════════════════════════════════════════

class TestProviderResult:
    def test_defaults(self):
        pr = ProviderResult(provider="localhost_llm", success=True)
        assert pr.execution_status == "not_attempted"
        assert pr.raw_response is None
        assert pr.content is None
        assert pr.error_code is None
        assert pr.error_message is None
        assert pr.timing_ms is None
        assert pr.provider_state_observed == "available"
        assert pr.degraded is False
        assert len(pr.request_id) == 32
        assert pr.metadata == {}

    def test_success_result(self):
        pr = ProviderResult(
            provider="localhost_llm",
            success=True,
            execution_status=ExecutionStatus.SUCCESS.value,
            raw_response={"choices": [{"message": {"content": "hi"}}]},
            content="hi",
            timing_ms=150.0,
        )
        assert pr.success is True
        assert pr.content == "hi"
        assert pr.timing_ms == 150.0

    def test_failure_result(self):
        pr = ProviderResult(
            provider="network_model_machine",
            success=False,
            execution_status=ExecutionStatus.FAILED.value,
            error_code="connection_error",
            error_message="Connection refused",
            provider_state_observed=ProviderState.UNAVAILABLE.value,
        )
        assert pr.success is False
        assert pr.error_code == "connection_error"
        assert pr.provider_state_observed == "unavailable"

    def test_mutable_defaults_isolated(self):
        a = ProviderResult(provider="x", success=True)
        b = ProviderResult(provider="x", success=True)
        a.metadata["key"] = "val"
        assert "key" not in b.metadata


# ══════════════════════════════════════════════════════════════════════════
# Legacy bridge
# ══════════════════════════════════════════════════════════════════════════

class TestLegacyBridge:
    def test_source_to_provider_local(self):
        assert legacy_source_to_provider("local") == "localhost_llm"

    def test_source_to_provider_model_machine(self):
        assert legacy_source_to_provider("model_machine") == "network_model_machine"

    def test_source_to_provider_premium(self):
        assert legacy_source_to_provider("premium_online") == "bedrock_titan_nova_pro"

    def test_source_to_provider_unknown(self):
        assert legacy_source_to_provider("nope") is None

    def test_provider_to_source_roundtrip(self):
        assert provider_to_legacy_source("localhost_llm") == "local"
        assert provider_to_legacy_source("network_model_machine") == "model_machine"
        assert provider_to_legacy_source("bedrock_titan_nova_pro") == "premium_online"

    def test_provider_to_source_unknown(self):
        assert provider_to_legacy_source("unknown_provider") is None

    def test_get_provider_endpoint_localhost(self):
        ep = get_provider_endpoint("localhost_llm")
        assert ep is not None
        assert "localhost" in ep

    def test_get_provider_endpoint_model_machine(self):
        ep = get_provider_endpoint("network_model_machine")
        assert ep is not None
        assert "192.168" in ep

    def test_get_provider_endpoint_bedrock_none(self):
        # premium_online has endpoint=None in MODEL_SOURCES
        ep = get_provider_endpoint("bedrock_titan_nova_pro")
        assert ep is None

    def test_get_provider_endpoint_unknown(self):
        assert get_provider_endpoint("nonexistent") is None

    def test_is_legacy_source_enabled(self):
        assert is_legacy_source_enabled("local") is True
        assert is_legacy_source_enabled("model_machine") is True
        assert is_legacy_source_enabled("premium_online") is False
        assert is_legacy_source_enabled("nonexistent") is False


# ══════════════════════════════════════════════════════════════════════════
# Content extraction
# ══════════════════════════════════════════════════════════════════════════

class TestExtractContent:
    def test_valid_response(self):
        data = {"choices": [{"message": {"content": "hello world"}}]}
        assert extract_content_from_openai_response(data) == "hello world"

    def test_missing_choices(self):
        assert extract_content_from_openai_response({}) is None

    def test_empty_choices(self):
        assert extract_content_from_openai_response({"choices": []}) is None

    def test_no_message(self):
        assert extract_content_from_openai_response({"choices": [{}]}) is None

    def test_no_content(self):
        data = {"choices": [{"message": {}}]}
        assert extract_content_from_openai_response(data) is None

    def test_non_dict_choice(self):
        assert extract_content_from_openai_response({"choices": ["string"]}) is None

    def test_non_dict_message(self):
        data = {"choices": [{"message": "string"}]}
        assert extract_content_from_openai_response(data) is None


# ══════════════════════════════════════════════════════════════════════════
# Provider adapters
# ══════════════════════════════════════════════════════════════════════════

class TestLocalhostLLMProvider:
    def test_provider_id(self):
        p = LocalhostLLMProvider()
        assert p.provider_id == "localhost_llm"

    def test_is_configured(self):
        p = LocalhostLLMProvider()
        assert p.is_configured is True  # has endpoint in MODEL_SOURCES

    def test_supports_model_default(self):
        p = LocalhostLLMProvider()
        assert p.supports_model("any-model") is True
        assert p.supports_model(None) is True

    @patch("app.services.model_provider_adapters._requests.post")
    def test_execute_success(self, mock_post):
        resp_data = {"choices": [{"message": {"content": "result text"}, "finish_reason": "stop"}]}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"choices": [...]}'
        mock_resp.json.return_value = resp_data
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        req = ExecutionRequest(mode="local", prompt=[{"role": "user", "content": "hi"}])
        result = LocalhostLLMProvider().execute(req, timeout=10.0)

        assert result.success is True
        assert result.execution_status == "success"
        assert result.content == "result text"
        assert result.raw_response == resp_data
        assert result.provider == "localhost_llm"
        assert result.timing_ms is not None
        assert result.provider_state_observed == "available"

    @patch("app.services.model_provider_adapters._requests.post")
    def test_execute_system_prompt(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        req = ExecutionRequest(
            mode="local",
            system_prompt="You are helpful.",
            prompt=[{"role": "user", "content": "hi"}],
            model_name="test-model",
        )
        LocalhostLLMProvider().execute(req, timeout=5.0)

        call_body = mock_post.call_args[1]["json"]
        assert call_body["messages"][0] == {"role": "system", "content": "You are helpful."}
        assert call_body["messages"][1] == {"role": "user", "content": "hi"}
        assert call_body["model"] == "test-model"
        assert call_body["stream"] is False

    @patch("app.services.model_provider_adapters._requests.post")
    def test_execute_timeout(self, mock_post):
        import requests
        mock_post.side_effect = requests.ReadTimeout("timed out")

        req = ExecutionRequest(mode="local", prompt=[{"role": "user", "content": "hi"}])
        result = LocalhostLLMProvider().execute(req, timeout=1.0)

        assert result.success is False
        assert result.execution_status == "timeout"
        assert result.error_code == "timeout"
        assert result.provider_state_observed == "degraded"

    @patch("app.services.model_provider_adapters._requests.post")
    def test_execute_connection_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.ConnectionError("refused")

        req = ExecutionRequest(mode="local", prompt=[{"role": "user", "content": "hi"}])
        result = LocalhostLLMProvider().execute(req, timeout=1.0)

        assert result.success is False
        assert result.execution_status == "failed"
        assert result.error_code == "connection_error"
        assert result.provider_state_observed == "unavailable"

    @patch("app.services.model_provider_adapters.get_provider_endpoint", return_value=None)
    def test_execute_no_endpoint(self, _mock):
        req = ExecutionRequest(mode="local")
        result = LocalhostLLMProvider().execute(req)

        assert result.success is False
        assert result.error_code == "no_endpoint"
        assert result.provider_state_observed == "unavailable"


class TestNetworkModelMachineProvider:
    def test_provider_id(self):
        p = NetworkModelMachineProvider()
        assert p.provider_id == "network_model_machine"

    def test_is_configured(self):
        p = NetworkModelMachineProvider()
        assert p.is_configured is True

    @patch("app.services.model_provider_adapters._requests.post")
    def test_execute_success(self, mock_post):
        resp_data = {"choices": [{"message": {"content": "answer"}}]}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = resp_data
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        req = ExecutionRequest(mode="model_machine", prompt=[{"role": "user", "content": "q"}])
        result = NetworkModelMachineProvider().execute(req, timeout=10.0)

        assert result.success is True
        assert result.provider == "network_model_machine"
        assert result.content == "answer"

    @patch("app.services.model_provider_adapters._requests.post")
    def test_execute_request_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.RequestException("500 error")

        req = ExecutionRequest(mode="model_machine", prompt=[{"role": "user", "content": "q"}])
        result = NetworkModelMachineProvider().execute(req, timeout=1.0)

        assert result.success is False
        assert result.execution_status == "failed"
        assert result.error_code == "request_error"
        assert result.provider_state_observed == "failed"

    @patch("app.services.model_provider_adapters.get_provider_endpoint", return_value=None)
    def test_execute_no_endpoint(self, _mock):
        req = ExecutionRequest(mode="model_machine")
        result = NetworkModelMachineProvider().execute(req)

        assert result.success is False
        assert result.error_code == "no_endpoint"


class TestBedrockTitanNovaProProvider:
    def test_provider_id(self):
        p = BedrockTitanNovaProProvider()
        assert p.provider_id == "bedrock_titan_nova_pro"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_is_not_configured_when_disabled(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        mock_settings.return_value = settings

        p = BedrockTitanNovaProProvider()
        assert p.is_configured is False

    @patch("app.services.model_provider_adapters.get_settings")
    def test_execute_returns_skipped_when_disabled(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        mock_settings.return_value = settings

        req = ExecutionRequest(mode="online_distributed")
        result = BedrockTitanNovaProProvider().execute(req)

        assert result.success is False
        assert result.execution_status == "skipped"
        assert result.error_code == "not_configured"
        assert result.provider_state_observed == "unavailable"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_probe_state_when_disabled(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        mock_settings.return_value = settings

        p = BedrockTitanNovaProProvider()
        assert p.probe_state() == "unavailable"


# ══════════════════════════════════════════════════════════════════════════
# Provider Registry
# ══════════════════════════════════════════════════════════════════════════

class TestProviderRegistry:
    def test_register_and_lookup(self):
        reg = ProviderRegistry()
        adapter = LocalhostLLMProvider()
        reg.register(adapter)

        assert reg.get_provider("localhost_llm") is adapter
        assert reg.get_provider("nonexistent") is None

    def test_list_registered(self):
        reg = ProviderRegistry()
        reg.register(LocalhostLLMProvider())
        reg.register(NetworkModelMachineProvider())
        assert reg.list_registered() == ["localhost_llm", "network_model_machine"]

    def test_re_register_replaces(self):
        reg = ProviderRegistry()
        a1 = LocalhostLLMProvider()
        a2 = LocalhostLLMProvider()
        reg.register(a1)
        reg.register(a2)
        assert reg.get_provider("localhost_llm") is a2

    def test_get_provider_status_registered_configured(self):
        reg = ProviderRegistry()
        reg.register(LocalhostLLMProvider())
        with patch("app.services.model_provider_adapters._requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b'{}'
            mock_resp.json.return_value = {}
            mock_get.return_value = mock_resp
            status = reg.get_provider_status("localhost_llm")

        assert status.registered is True
        assert status.configured is True
        assert status.state == "available"

    def test_get_provider_status_registered_not_configured(self):
        reg = ProviderRegistry()
        bedrock = BedrockTitanNovaProProvider()
        # Simulate unconfigured by setting internal state.
        bedrock._client = None
        bedrock._client_initialised = True
        bedrock._client_error = "no credentials"
        with patch("app.services.model_provider_adapters.get_settings") as mock_settings:
            settings = MagicMock()
            settings.BEDROCK_ENABLED = False
            mock_settings.return_value = settings
            reg.register(bedrock)
            status = reg.get_provider_status("bedrock_titan_nova_pro")

        assert status.registered is True
        assert status.configured is False
        assert status.state == "unavailable"

    def test_get_provider_status_unknown(self):
        reg = ProviderRegistry()
        status = reg.get_provider_status("unknown_provider")

        assert status.registered is False
        assert status.configured is False
        assert status.state == "unavailable"

    def test_all_statuses(self):
        reg = ProviderRegistry()
        reg.register(LocalhostLLMProvider())
        reg.register(BedrockTitanNovaProProvider())
        statuses = reg.all_statuses()
        assert len(statuses) == 2
        pids = {s.provider_id for s in statuses}
        assert pids == {"localhost_llm", "bedrock_titan_nova_pro"}

    def test_empty_registry(self):
        reg = ProviderRegistry()
        assert reg.list_registered() == []
        assert reg.all_statuses() == []
        assert reg.get_provider("anything") is None


class TestDefaultRegistry:
    """Test the module-level ``get_registry()`` singleton."""

    def setup_method(self):
        reset_registry()

    def teardown_method(self):
        reset_registry()

    def test_default_has_three_providers(self):
        reg = get_registry()
        registered = reg.list_registered()
        assert "localhost_llm" in registered
        assert "network_model_machine" in registered
        assert "bedrock_titan_nova_pro" in registered

    def test_singleton_identity(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_reset_clears(self):
        r1 = get_registry()
        reset_registry()
        r2 = get_registry()
        assert r1 is not r2


# ══════════════════════════════════════════════════════════════════════════
# execute_with_provider seam (model_router)
# ══════════════════════════════════════════════════════════════════════════

class TestExecuteWithProvider:
    """Tests for model_router.execute_with_provider."""

    def setup_method(self):
        reset_registry()

    def teardown_method(self):
        reset_registry()

    def test_unknown_provider_returns_failure(self):
        from app.services.model_router import execute_with_provider
        req = ExecutionRequest(mode="local")
        result = execute_with_provider(req, "nonexistent_provider")

        assert result.success is False
        assert result.error_code == "unknown_provider"
        assert result.execution_status == "failed"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_unconfigured_provider_returns_skipped(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        mock_settings.return_value = settings

        from app.services.model_router import execute_with_provider
        req = ExecutionRequest(mode="online_distributed")
        result = execute_with_provider(req, "bedrock_titan_nova_pro")

        assert result.success is False
        assert result.error_code == "not_configured"
        assert result.execution_status == "skipped"

    @patch("app.services.model_provider_adapters._requests.post")
    def test_configured_provider_calls_adapter(self, mock_post):
        from app.services.model_router import execute_with_provider

        resp_data = {"choices": [{"message": {"content": "ok"}}]}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = resp_data
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        req = ExecutionRequest(mode="local", prompt=[{"role": "user", "content": "test"}])
        result = execute_with_provider(req, "localhost_llm", timeout=5.0)

        assert result.success is True
        assert result.provider == "localhost_llm"
        assert result.content == "ok"
        assert result.raw_response == resp_data

    @patch("app.services.model_provider_adapters._requests.post")
    def test_provider_failure_preserved(self, mock_post):
        import requests
        from app.services.model_router import execute_with_provider

        mock_post.side_effect = requests.ConnectionError("refused")

        req = ExecutionRequest(mode="local", prompt=[{"role": "user", "content": "hi"}])
        result = execute_with_provider(req, "localhost_llm", timeout=1.0)

        assert result.success is False
        assert result.execution_status == "failed"
        assert result.error_code == "connection_error"

    @patch("app.services.model_provider_adapters._requests.post")
    def test_raw_payload_passthrough(self, mock_post):
        from app.services.model_router import execute_with_provider

        raw = {"id": "chatcmpl-1", "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}], "usage": {"total_tokens": 42}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = raw
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        req = ExecutionRequest(mode="local", prompt=[{"role": "user", "content": "q"}])
        result = execute_with_provider(req, "localhost_llm")

        # Full raw payload preserved for traceability
        assert result.raw_response["usage"]["total_tokens"] == 42
        assert result.raw_response["id"] == "chatcmpl-1"

    @patch("app.services.model_provider_adapters._requests.post")
    def test_timing_populated(self, mock_post):
        from app.services.model_router import execute_with_provider

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {"choices": [{"message": {"content": "x"}}]}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        req = ExecutionRequest(mode="local", prompt=[{"role": "user", "content": "q"}])
        result = execute_with_provider(req, "localhost_llm", timeout=5.0)

        assert result.timing_ms is not None
        assert result.timing_ms >= 0
