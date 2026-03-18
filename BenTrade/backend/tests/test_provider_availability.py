"""Tests for provider busy-state / availability detection (Step 3).

Covers:
    • ProbeResult dataclass defaults and fields
    • _lmstudio_probe: available, busy (429/503), degraded (slow),
      unavailable (conn error, timeout), failed (4xx/5xx)
    • LocalhostLLMProvider.probe() delegation
    • NetworkModelMachineProvider.probe() delegation
    • BedrockTitanNovaProProvider.probe() — unconfigured honesty
    • probe_state() derives from probe()
    • ProviderRegistry.get_provider_status(refresh=True/False)
    • ProviderRegistry.probe_provider()
    • ProviderRegistry.all_statuses(refresh=True)
    • Exception mapping and timeout hygiene
    • Busy vs unavailable vs failed distinction
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import time

import pytest
import requests

from app.services.model_provider_base import (
    PROBE_TIMEOUT_SECONDS,
    ModelProviderBase,
    ProbeResult,
    ProviderResult,
)
from app.services.model_provider_adapters import (
    BedrockTitanNovaProProvider,
    LocalhostLLMProvider,
    NetworkModelMachineProvider,
    _lmstudio_probe,
    _PROBE_DEGRADED_THRESHOLD_MS,
)
from app.services.model_provider_registry import (
    ProviderRegistry,
    ProviderStatusSnapshot,
    get_registry,
    reset_registry,
)
from app.services.model_routing_contract import (
    ExecutionRequest,
    Provider,
    ProviderState,
)


# ══════════════════════════════════════════════════════════════════════════
# ProbeResult dataclass
# ══════════════════════════════════════════════════════════════════════════

class TestProbeResult:
    def test_defaults(self):
        pr = ProbeResult(provider="localhost_llm", configured=True)
        assert pr.state == "unavailable"
        assert pr.probe_success is False
        assert pr.status_reason == ""
        assert pr.raw_probe_data is None
        assert pr.timing_ms is None
        assert pr.metadata == {}
        assert pr.checked_at  # ISO timestamp present

    def test_available_result(self):
        pr = ProbeResult(
            provider="localhost_llm",
            configured=True,
            state=ProviderState.AVAILABLE.value,
            probe_success=True,
            status_reason="healthy",
            timing_ms=45.0,
        )
        assert pr.state == "available"
        assert pr.probe_success is True
        assert pr.timing_ms == 45.0

    def test_busy_result(self):
        pr = ProbeResult(
            provider="network_model_machine",
            configured=True,
            state=ProviderState.BUSY.value,
            probe_success=True,
            status_reason="HTTP 429",
        )
        assert pr.state == "busy"

    def test_mutable_defaults_isolated(self):
        a = ProbeResult(provider="x", configured=True)
        b = ProbeResult(provider="x", configured=True)
        a.metadata["k"] = "v"
        assert "k" not in b.metadata


# ══════════════════════════════════════════════════════════════════════════
# _lmstudio_probe helper
# ══════════════════════════════════════════════════════════════════════════

class TestLmstudioProbe:
    """Tests for the shared LM Studio probe function."""

    ENDPOINT = "http://localhost:1234/v1/chat/completions"
    PID = "localhost_llm"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_available_200(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"data": []}'
        mock_resp.json.return_value = {"data": [{"id": "model-1"}]}
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, self.PID)

        assert result.state == "available"
        assert result.probe_success is True
        assert result.configured is True
        assert result.status_reason == "healthy"
        assert result.timing_ms is not None
        assert result.raw_probe_data == {"data": [{"id": "model-1"}]}
        # Verify it hit /v1/models
        called_url = mock_get.call_args[0][0]
        assert called_url.endswith("/v1/models")

    @patch("app.services.model_provider_adapters._requests.get")
    def test_busy_429(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, self.PID)

        assert result.state == "busy"
        assert result.probe_success is True
        assert "429" in result.status_reason
        assert result.raw_probe_data == {"status_code": 429}

    @patch("app.services.model_provider_adapters._requests.get")
    def test_busy_503(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, self.PID)

        assert result.state == "busy"
        assert result.probe_success is True
        assert "503" in result.status_reason

    @patch("app.services.model_provider_adapters._requests.get")
    def test_failed_500(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, self.PID)

        assert result.state == "failed"
        assert result.probe_success is True
        assert "500" in result.status_reason

    @patch("app.services.model_provider_adapters._requests.get")
    def test_failed_404(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, self.PID)

        assert result.state == "failed"
        assert "404" in result.status_reason

    @patch("app.services.model_provider_adapters._requests.get")
    def test_degraded_slow_response(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {"data": []}
        mock_get.return_value = mock_resp

        # Patch time.perf_counter to simulate a slow probe
        original_perf = time.perf_counter
        call_count = 0

        def slow_perf():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 0.0  # t0
            # Second call: simulate 3 seconds elapsed (above threshold)
            return 3.0

        with patch("app.services.model_provider_adapters.time.perf_counter", side_effect=slow_perf):
            result = _lmstudio_probe(self.ENDPOINT, self.PID)

        assert result.state == "degraded"
        assert result.probe_success is True
        assert "slow" in result.status_reason.lower()

    @patch("app.services.model_provider_adapters._requests.get")
    def test_unavailable_connection_error(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("Connection refused")

        result = _lmstudio_probe(self.ENDPOINT, self.PID)

        assert result.state == "unavailable"
        assert result.probe_success is False
        assert "connection error" in result.status_reason.lower()

    @patch("app.services.model_provider_adapters._requests.get")
    def test_unavailable_read_timeout(self, mock_get):
        mock_get.side_effect = requests.ReadTimeout("timed out")

        result = _lmstudio_probe(self.ENDPOINT, self.PID)

        assert result.state == "unavailable"
        assert result.probe_success is False
        assert "timed out" in result.status_reason.lower()

    @patch("app.services.model_provider_adapters._requests.get")
    def test_failed_unexpected_exception(self, mock_get):
        mock_get.side_effect = RuntimeError("something weird")

        result = _lmstudio_probe(self.ENDPOINT, self.PID)

        assert result.state == "failed"
        assert result.probe_success is False
        assert "exception" in result.status_reason.lower()

    @patch("app.services.model_provider_adapters._requests.get")
    def test_timing_always_populated(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, self.PID)
        assert result.timing_ms is not None
        assert result.timing_ms >= 0

    @patch("app.services.model_provider_adapters._requests.get")
    def test_models_url_derived_correctly(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp

        _lmstudio_probe("http://192.168.1.143:1234/v1/chat/completions", "test")
        called_url = mock_get.call_args[0][0]
        assert called_url == "http://192.168.1.143:1234/v1/models"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_json_parse_failure_graceful(self, mock_get):
        """If /v1/models returns non-JSON body, probe still succeeds."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'not json'
        mock_resp.json.side_effect = ValueError("bad json")
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, self.PID)
        assert result.state == "available"
        assert result.probe_success is True
        # raw_probe_data falls back to body_length
        assert result.raw_probe_data == {"body_length": 8}

    @patch("app.services.model_provider_adapters._requests.get")
    def test_probe_uses_short_timeout(self, mock_get):
        """Verify the probe passes a bounded timeout to requests.get."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp

        _lmstudio_probe(self.ENDPOINT, self.PID)
        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == PROBE_TIMEOUT_SECONDS


# ══════════════════════════════════════════════════════════════════════════
# Provider adapter probe() methods
# ══════════════════════════════════════════════════════════════════════════

class TestLocalhostProbe:
    @patch("app.services.model_provider_adapters._requests.get")
    def test_probe_available(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {"data": []}
        mock_get.return_value = mock_resp

        result = LocalhostLLMProvider().probe()
        assert result.state == "available"
        assert result.provider == "localhost_llm"
        assert result.configured is True

    @patch("app.services.model_provider_adapters._requests.get")
    def test_probe_busy(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        result = LocalhostLLMProvider().probe()
        assert result.state == "busy"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_probe_unavailable(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")

        result = LocalhostLLMProvider().probe()
        assert result.state == "unavailable"

    @patch("app.services.model_provider_adapters.get_provider_endpoint", return_value=None)
    def test_probe_no_endpoint(self, _mock):
        result = LocalhostLLMProvider().probe()
        assert result.state == "unavailable"
        assert result.configured is False

    @patch("app.services.model_provider_adapters._requests.get")
    def test_probe_state_delegates_to_probe(self, mock_get):
        """probe_state() should return the same state as probe().state."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp

        p = LocalhostLLMProvider()
        assert p.probe_state() == "available"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_probe_state_busy(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_get.return_value = mock_resp

        p = LocalhostLLMProvider()
        assert p.probe_state() == "busy"


class TestNetworkModelMachineProbe:
    @patch("app.services.model_provider_adapters._requests.get")
    def test_probe_available(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {"data": []}
        mock_get.return_value = mock_resp

        result = NetworkModelMachineProvider().probe()
        assert result.state == "available"
        assert result.provider == "network_model_machine"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_probe_busy(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        result = NetworkModelMachineProvider().probe()
        assert result.state == "busy"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_probe_unavailable(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")

        result = NetworkModelMachineProvider().probe()
        assert result.state == "unavailable"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_probe_degraded(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp

        call_count = 0
        def slow_perf():
            nonlocal call_count
            call_count += 1
            return 0.0 if call_count == 1 else 3.0

        with patch("app.services.model_provider_adapters.time.perf_counter", side_effect=slow_perf):
            result = NetworkModelMachineProvider().probe()

        assert result.state == "degraded"

    @patch("app.services.model_provider_adapters.get_provider_endpoint", return_value=None)
    def test_probe_no_endpoint(self, _mock):
        result = NetworkModelMachineProvider().probe()
        assert result.state == "unavailable"
        assert result.configured is False


class TestBedrockProbe:
    @patch("app.services.model_provider_adapters.get_settings")
    def test_probe_unconfigured(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        mock_settings.return_value = settings

        result = BedrockTitanNovaProProvider().probe()
        assert result.state == "unavailable"
        assert result.configured is False
        assert result.probe_success is True
        assert "bedrock_enabled=false" in result.status_reason.lower()

    @patch("app.services.model_provider_adapters.get_settings")
    def test_probe_state_delegates(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        mock_settings.return_value = settings

        assert BedrockTitanNovaProProvider().probe_state() == "unavailable"


# ══════════════════════════════════════════════════════════════════════════
# Registry live probing
# ══════════════════════════════════════════════════════════════════════════

class TestRegistryRefresh:
    def setup_method(self):
        reset_registry()

    def teardown_method(self):
        reset_registry()

    @patch("app.services.model_provider_adapters._requests.get")
    def test_get_provider_status_refresh_available(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {"data": []}
        mock_get.return_value = mock_resp

        reg = get_registry()
        status = reg.get_provider_status("localhost_llm", refresh=True)

        assert status.state == "available"
        assert status.configured is True
        assert status.probe_success is True
        assert status.timing_ms is not None

    @patch("app.services.model_provider_adapters._requests.get")
    def test_get_provider_status_refresh_busy(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        reg = get_registry()
        status = reg.get_provider_status("localhost_llm", refresh=True)

        assert status.state == "busy"
        assert status.probe_success is True
        assert "429" in status.status_reason

    @patch("app.services.model_provider_adapters._requests.get")
    def test_get_provider_status_refresh_unavailable(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")

        reg = get_registry()
        status = reg.get_provider_status("localhost_llm", refresh=True)

        assert status.state == "unavailable"
        assert status.probe_success is False

    def test_get_provider_status_no_refresh_configured(self):
        """Without refresh, configured providers use probe_state() default."""
        reg = ProviderRegistry()
        reg.register(LocalhostLLMProvider())
        # Without mocking requests.get, probe_state() will call probe()
        # which will try a real HTTP call. Use a mock adapter instead.
        adapter = MagicMock(spec=ModelProviderBase)
        adapter.provider_id = "localhost_llm"
        adapter.is_configured = True
        adapter.probe_state.return_value = "available"
        adapter.probe.return_value = ProbeResult(
            provider="localhost_llm", configured=True,
            state="available", probe_success=True,
        )

        reg2 = ProviderRegistry()
        reg2.register(adapter)
        status = reg2.get_provider_status("localhost_llm", refresh=False)
        assert status.state == "available"

    def test_get_provider_status_unknown_provider(self):
        reg = get_registry()
        status = reg.get_provider_status("nonexistent", refresh=True)

        assert status.registered is False
        assert status.state == "unavailable"
        assert status.status_reason == "not registered"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_get_provider_status_unconfigured(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        mock_settings.return_value = settings

        reset_registry()
        reg = get_registry()
        status = reg.get_provider_status("bedrock_titan_nova_pro", refresh=True)

        assert status.registered is True
        assert status.configured is False
        assert status.state == "unavailable"

    @patch("app.services.model_provider_adapters.get_settings")
    def test_probe_provider_direct(self, mock_settings):
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        mock_settings.return_value = settings

        reset_registry()
        reg = get_registry()
        result = reg.probe_provider("bedrock_titan_nova_pro")

        assert isinstance(result, ProbeResult)
        assert result.state == "unavailable"
        assert result.configured is False

    def test_probe_provider_unknown(self):
        reg = get_registry()
        result = reg.probe_provider("nonexistent")

        assert isinstance(result, ProbeResult)
        assert result.state == "unavailable"
        assert "not registered" in result.status_reason

    @patch("app.services.model_provider_adapters.get_settings")
    @patch("app.services.model_provider_adapters._requests.get")
    def test_all_statuses_refresh(self, mock_get, mock_settings):
        # Mock settings: Bedrock disabled so it appears unconfigured.
        settings = MagicMock()
        settings.BEDROCK_ENABLED = False
        settings.MODEL_TIMEOUT_SECONDS = 180.0
        mock_settings.return_value = settings

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp

        reset_registry()
        reg = get_registry()
        statuses = reg.all_statuses(refresh=True)

        assert len(statuses) == 3
        by_id = {s.provider_id: s for s in statuses}

        # localhost + model_machine: available (mocked 200)
        assert by_id["localhost_llm"].state == "available"
        assert by_id["network_model_machine"].state == "available"
        # bedrock: disabled → unavailable
        assert by_id["bedrock_titan_nova_pro"].state == "unavailable"
        assert by_id["bedrock_titan_nova_pro"].configured is False


# ══════════════════════════════════════════════════════════════════════════
# State distinction tests — busy vs unavailable vs failed
# ══════════════════════════════════════════════════════════════════════════

class TestStateDistinctions:
    """Ensure busy, unavailable, degraded, and failed remain distinct."""

    ENDPOINT = "http://localhost:1234/v1/chat/completions"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_busy_is_not_unavailable(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, "test")
        assert result.state == "busy"
        assert result.state != "unavailable"
        assert result.state != "failed"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_unavailable_is_not_busy(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")

        result = _lmstudio_probe(self.ENDPOINT, "test")
        assert result.state == "unavailable"
        assert result.state != "busy"
        assert result.state != "failed"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_failed_is_not_busy_or_unavailable(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, "test")
        assert result.state == "failed"
        assert result.state != "busy"
        assert result.state != "unavailable"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_degraded_is_distinct(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp

        call_count = 0
        def slow():
            nonlocal call_count
            call_count += 1
            return 0.0 if call_count == 1 else 3.0

        with patch("app.services.model_provider_adapters.time.perf_counter", side_effect=slow):
            result = _lmstudio_probe(self.ENDPOINT, "test")

        assert result.state == "degraded"
        assert result.state not in ("available", "busy", "unavailable", "failed")

    @patch("app.services.model_provider_adapters._requests.get")
    def test_all_five_states_reachable(self, mock_get):
        """Verify every ProviderState value can be produced by the probe."""
        states_observed = set()

        # AVAILABLE
        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.content = b'{}'
        mock_resp_ok.json.return_value = {}
        mock_get.return_value = mock_resp_ok
        states_observed.add(_lmstudio_probe(self.ENDPOINT, "t").state)

        # BUSY (429)
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_get.return_value = mock_resp_429
        states_observed.add(_lmstudio_probe(self.ENDPOINT, "t").state)

        # FAILED (500)
        mock_resp_500 = MagicMock()
        mock_resp_500.status_code = 500
        mock_get.return_value = mock_resp_500
        states_observed.add(_lmstudio_probe(self.ENDPOINT, "t").state)

        # UNAVAILABLE (conn error)
        mock_get.side_effect = requests.ConnectionError("refused")
        states_observed.add(_lmstudio_probe(self.ENDPOINT, "t").state)
        mock_get.side_effect = None

        # DEGRADED (slow)
        mock_get.return_value = mock_resp_ok
        call_count = 0
        def slow():
            nonlocal call_count
            call_count += 1
            return 0.0 if call_count == 1 else 3.0

        with patch("app.services.model_provider_adapters.time.perf_counter", side_effect=slow):
            states_observed.add(_lmstudio_probe(self.ENDPOINT, "t").state)

        expected = {"available", "busy", "failed", "unavailable", "degraded"}
        assert states_observed == expected


# ══════════════════════════════════════════════════════════════════════════
# Probe metadata preservation
# ══════════════════════════════════════════════════════════════════════════

class TestProbeMetadata:
    ENDPOINT = "http://localhost:1234/v1/chat/completions"

    @patch("app.services.model_provider_adapters._requests.get")
    def test_raw_data_preserved_on_success(self, mock_get):
        payload = {"data": [{"id": "model-abc"}]}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = payload
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, "test")
        assert result.raw_probe_data == payload

    @patch("app.services.model_provider_adapters._requests.get")
    def test_status_code_preserved_on_busy(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, "test")
        assert result.raw_probe_data == {"status_code": 503}

    @patch("app.services.model_provider_adapters._requests.get")
    def test_checked_at_populated(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp

        result = _lmstudio_probe(self.ENDPOINT, "test")
        assert result.checked_at  # ISO string present

    @patch("app.services.model_provider_adapters._requests.get")
    def test_timing_on_connection_error(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")

        result = _lmstudio_probe(self.ENDPOINT, "test")
        assert result.timing_ms is not None
        assert result.timing_ms >= 0


# ══════════════════════════════════════════════════════════════════════════
# ProviderStatusSnapshot extended fields
# ══════════════════════════════════════════════════════════════════════════

class TestProviderStatusSnapshotFields:
    def test_defaults(self):
        snap = ProviderStatusSnapshot(
            provider_id="x", registered=True, configured=True, state="available",
        )
        assert snap.probe_success is True
        assert snap.status_reason == ""
        assert snap.timing_ms is None

    def test_full_fields(self):
        snap = ProviderStatusSnapshot(
            provider_id="localhost_llm",
            registered=True,
            configured=True,
            state="busy",
            probe_success=True,
            status_reason="HTTP 429",
            timing_ms=55.0,
        )
        assert snap.state == "busy"
        assert snap.status_reason == "HTTP 429"
        assert snap.timing_ms == 55.0
