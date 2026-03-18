"""Tests for Step 15 — control-plane hardening.

Sections:
    A — Cooldown gate: _check_cooldown / _record_call / _cooldown_response
    B — POST /refresh-config with cooldown
    C — POST /refresh-providers with cooldown
    D — POST /refresh-runtime with cooldown
    E — Cooldown expiry allows retry
    F — Independent cooldown per endpoint
    G — 429 response structure
    H — Successful response still includes action key
    I — No secrets in 429 response
    J — Frontend HTML has operator buttons
    K — Frontend JS has control action handler
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from app.api.routes_routing import (
    COOLDOWN_SECONDS,
    _check_cooldown,
    _cooldown_response,
    _last_call,
    _record_call,
    post_refresh_config,
    post_refresh_providers,
    post_refresh_runtime,
)
from app.services.model_routing_config import (
    get_routing_config,
    reset_routing_config,
)
from app.services.model_execution_gate import reset_execution_gate
from app.services.routing_dashboard_contract import BLOCKED_FIELDS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    """Reset routing singletons and cooldown state before/after each test."""
    reset_routing_config()
    reset_execution_gate()
    _last_call.clear()
    yield
    reset_routing_config()
    reset_execution_gate()
    _last_call.clear()


# Patch targets — patch at SOURCE modules since route handlers use local imports.
_P_REG = "app.services.model_provider_registry.get_registry"
_P_REFRESH_CFG = "app.services.model_routing_config.refresh_routing_config"
_P_REFRESH_RUNTIME = "app.services.routing_dashboard_service.refresh_routing_runtime"


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _mock_registry():
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
    ]
    return mock_reg


# ═══════════════════════════════════════════════════════════════════════════
# A — Cooldown gate unit tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCooldownGate:
    def test_check_cooldown_returns_none_when_no_prior_call(self):
        assert _check_cooldown("test-endpoint") is None

    def test_record_call_stores_timestamp(self):
        _record_call("test-endpoint")
        assert "test-endpoint" in _last_call
        assert isinstance(_last_call["test-endpoint"], float)

    def test_check_cooldown_returns_remaining_after_record(self):
        _record_call("test-endpoint")
        remaining = _check_cooldown("test-endpoint")
        assert remaining is not None
        assert remaining > 0
        assert remaining <= COOLDOWN_SECONDS

    def test_check_cooldown_returns_none_after_expiry(self):
        # Force an old timestamp
        _last_call["test-endpoint"] = time.monotonic() - COOLDOWN_SECONDS - 1.0
        assert _check_cooldown("test-endpoint") is None

    def test_cooldown_response_returns_429(self):
        resp = _cooldown_response(5.0)
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") is not None

    def test_cooldown_response_body_has_retry_after(self):
        resp = _cooldown_response(7.3)
        import json
        body = json.loads(resp.body.decode())
        assert body["error"] == "cooldown_active"
        assert body["retry_after"] == 7.3
        assert "7.3s" in body["message"]

    def test_cooldown_seconds_is_positive(self):
        assert COOLDOWN_SECONDS > 0


# ═══════════════════════════════════════════════════════════════════════════
# B — POST /refresh-config with cooldown
# ═══════════════════════════════════════════════════════════════════════════


class TestRefreshConfigCooldown:
    def test_first_call_succeeds(self):
        with patch(_P_REFRESH_CFG) as mock_fn:
            mock_fn.return_value = {"previous": {}, "current": {}, "changed_fields": {}, "config_loaded_at": "t"}
            result = _run(post_refresh_config())
        assert isinstance(result, dict)
        assert result["action"] == "config_refreshed"

    def test_immediate_second_call_returns_429(self):
        with patch(_P_REFRESH_CFG) as mock_fn:
            mock_fn.return_value = {"previous": {}, "current": {}, "changed_fields": {}, "config_loaded_at": "t"}
            _run(post_refresh_config())
            result = _run(post_refresh_config())
        assert hasattr(result, "status_code")
        assert result.status_code == 429

    def test_call_after_expiry_succeeds(self):
        with patch(_P_REFRESH_CFG) as mock_fn:
            mock_fn.return_value = {"previous": {}, "current": {}, "changed_fields": {}, "config_loaded_at": "t"}
            _run(post_refresh_config())
            # Force expiry
            _last_call["refresh-config"] = time.monotonic() - COOLDOWN_SECONDS - 1.0
            result = _run(post_refresh_config())
        assert isinstance(result, dict)
        assert result["action"] == "config_refreshed"


# ═══════════════════════════════════════════════════════════════════════════
# C — POST /refresh-providers with cooldown
# ═══════════════════════════════════════════════════════════════════════════


class TestRefreshProvidersCooldown:
    def test_first_call_succeeds(self):
        with patch(_P_REG) as mock_get_reg:
            mock_get_reg.return_value = _mock_registry()
            result = _run(post_refresh_providers())
        assert isinstance(result, dict)
        assert result["action"] == "providers_refreshed"
        assert "providers" in result

    def test_immediate_second_call_returns_429(self):
        with patch(_P_REG) as mock_get_reg:
            mock_get_reg.return_value = _mock_registry()
            _run(post_refresh_providers())
            result = _run(post_refresh_providers())
        assert hasattr(result, "status_code")
        assert result.status_code == 429


# ═══════════════════════════════════════════════════════════════════════════
# D — POST /refresh-runtime with cooldown
# ═══════════════════════════════════════════════════════════════════════════


class TestRefreshRuntimeCooldown:
    def test_first_call_succeeds(self):
        with patch(_P_REFRESH_RUNTIME) as mock_fn:
            mock_fn.return_value = {"config_diff": {}, "gate_summary": {}, "provider_summary": []}
            result = _run(post_refresh_runtime())
        assert isinstance(result, dict)
        assert result["action"] == "runtime_refreshed"

    def test_immediate_second_call_returns_429(self):
        with patch(_P_REFRESH_RUNTIME) as mock_fn:
            mock_fn.return_value = {"config_diff": {}, "gate_summary": {}, "provider_summary": []}
            _run(post_refresh_runtime())
            result = _run(post_refresh_runtime())
        assert hasattr(result, "status_code")
        assert result.status_code == 429


# ═══════════════════════════════════════════════════════════════════════════
# E — Cooldown expiry allows retry
# ═══════════════════════════════════════════════════════════════════════════


class TestCooldownExpiry:
    def test_providers_succeeds_after_expiry(self):
        with patch(_P_REG) as mock_get_reg:
            mock_get_reg.return_value = _mock_registry()
            _run(post_refresh_providers())
            _last_call["refresh-providers"] = time.monotonic() - COOLDOWN_SECONDS - 1.0
            result = _run(post_refresh_providers())
        assert isinstance(result, dict)
        assert result["action"] == "providers_refreshed"

    def test_runtime_succeeds_after_expiry(self):
        with patch(_P_REFRESH_RUNTIME) as mock_fn:
            mock_fn.return_value = {"config_diff": {}, "gate_summary": {}, "provider_summary": []}
            _run(post_refresh_runtime())
            _last_call["refresh-runtime"] = time.monotonic() - COOLDOWN_SECONDS - 1.0
            result = _run(post_refresh_runtime())
        assert isinstance(result, dict)
        assert result["action"] == "runtime_refreshed"


# ═══════════════════════════════════════════════════════════════════════════
# F — Independent cooldown per endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestIndependentCooldowns:
    def test_config_cooldown_does_not_block_providers(self):
        with patch(_P_REFRESH_CFG) as mock_cfg, \
             patch(_P_REG) as mock_get_reg:
            mock_cfg.return_value = {"previous": {}, "current": {}, "changed_fields": {}, "config_loaded_at": "t"}
            mock_get_reg.return_value = _mock_registry()

            _run(post_refresh_config())
            result = _run(post_refresh_providers())
        assert isinstance(result, dict)
        assert result["action"] == "providers_refreshed"

    def test_providers_cooldown_does_not_block_runtime(self):
        with patch(_P_REG) as mock_get_reg, \
             patch(_P_REFRESH_RUNTIME) as mock_rt:
            mock_get_reg.return_value = _mock_registry()
            mock_rt.return_value = {"config_diff": {}, "gate_summary": {}, "provider_summary": []}

            _run(post_refresh_providers())
            result = _run(post_refresh_runtime())
        assert isinstance(result, dict)
        assert result["action"] == "runtime_refreshed"

    def test_all_three_first_calls_succeed(self):
        with patch(_P_REFRESH_CFG) as mock_cfg, \
             patch(_P_REG) as mock_get_reg, \
             patch(_P_REFRESH_RUNTIME) as mock_rt:
            mock_cfg.return_value = {"previous": {}, "current": {}, "changed_fields": {}, "config_loaded_at": "t"}
            mock_get_reg.return_value = _mock_registry()
            mock_rt.return_value = {"config_diff": {}, "gate_summary": {}, "provider_summary": []}

            r1 = _run(post_refresh_config())
            r2 = _run(post_refresh_providers())
            r3 = _run(post_refresh_runtime())

        assert r1["action"] == "config_refreshed"
        assert r2["action"] == "providers_refreshed"
        assert r3["action"] == "runtime_refreshed"


# ═══════════════════════════════════════════════════════════════════════════
# G — 429 response structure
# ═══════════════════════════════════════════════════════════════════════════


class Test429ResponseStructure:
    def test_429_has_error_field(self):
        result = _cooldown_response(5.0)
        import json
        body = json.loads(result.body.decode())
        assert body["error"] == "cooldown_active"

    def test_429_has_retry_after_header(self):
        result = _cooldown_response(3.5)
        header = result.headers.get("Retry-After")
        assert header is not None
        assert int(header) >= 4

    def test_429_has_message(self):
        result = _cooldown_response(2.0)
        import json
        body = json.loads(result.body.decode())
        assert "message" in body
        assert "2.0s" in body["message"]


# ═══════════════════════════════════════════════════════════════════════════
# H — Successful response includes action key
# ═══════════════════════════════════════════════════════════════════════════


class TestSuccessResponseShape:
    def test_config_response_has_action_and_result(self):
        with patch(_P_REFRESH_CFG) as mock_fn:
            mock_fn.return_value = {"previous": {}, "current": {}, "changed_fields": {}, "config_loaded_at": "t"}
            result = _run(post_refresh_config())
        assert result["action"] == "config_refreshed"
        assert "result" in result

    def test_providers_response_has_action_and_providers(self):
        with patch(_P_REG) as mock_get_reg:
            mock_get_reg.return_value = _mock_registry()
            result = _run(post_refresh_providers())
        assert result["action"] == "providers_refreshed"
        assert isinstance(result["providers"], list)

    def test_runtime_response_has_action_and_result(self):
        with patch(_P_REFRESH_RUNTIME) as mock_fn:
            mock_fn.return_value = {"config_diff": {}, "gate_summary": {}, "provider_summary": []}
            result = _run(post_refresh_runtime())
        assert result["action"] == "runtime_refreshed"
        assert "result" in result


# ═══════════════════════════════════════════════════════════════════════════
# I — No secrets in 429 response
# ═══════════════════════════════════════════════════════════════════════════


class TestNoSecretsIn429:
    def test_cooldown_response_has_no_blocked_fields(self):
        import json
        resp = _cooldown_response(5.0)
        body_str = resp.body.decode()
        for field in BLOCKED_FIELDS:
            assert field not in body_str.lower(), f"Blocked field '{field}' found in 429 response"


# ═══════════════════════════════════════════════════════════════════════════
# J — Frontend HTML has operator buttons
# ═══════════════════════════════════════════════════════════════════════════


class TestFrontendHTML:
    @pytest.fixture()
    def html_content(self):
        import pathlib
        html_path = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "dashboards" / "data_health.html"
        return html_path.read_text(encoding="utf-8")

    def test_refresh_config_button_exists(self, html_content):
        assert 'id="dhRefreshConfigBtn"' in html_content

    def test_refresh_providers_button_exists(self, html_content):
        assert 'id="dhRefreshProvidersBtn"' in html_content

    def test_refresh_runtime_button_exists(self, html_content):
        assert 'id="dhRefreshRuntimeBtn"' in html_content

    def test_feedback_element_exists(self, html_content):
        assert 'id="dhRoutingActionFeedback"' in html_content

    def test_buttons_are_inside_routing_system_card(self, html_content):
        # Buttons should appear after dhRoutingSystemContent but within dhRoutingSystemCard
        card_start = html_content.index('id="dhRoutingSystemCard"')
        card_section = html_content[card_start:card_start + 2000]
        assert 'dhRefreshConfigBtn' in card_section
        assert 'dhRefreshProvidersBtn' in card_section
        assert 'dhRefreshRuntimeBtn' in card_section


# ═══════════════════════════════════════════════════════════════════════════
# K — Frontend JS has control action handler
# ═══════════════════════════════════════════════════════════════════════════


class TestFrontendJS:
    @pytest.fixture()
    def js_content(self):
        import pathlib
        js_path = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "assets" / "js" / "pages" / "data_health.js"
        return js_path.read_text(encoding="utf-8")

    def test_routing_control_url_defined(self, js_content):
        assert "ROUTING_CONTROL_URL" in js_content

    def test_routing_control_action_function(self, js_content):
        assert "routingControlAction" in js_content

    def test_in_flight_guard(self, js_content):
        assert "_routingCtrlInFlight" in js_content

    def test_button_disabled_during_flight(self, js_content):
        assert "setRoutingCtrlButtonsDisabled" in js_content

    def test_feedback_display_function(self, js_content):
        assert "showRoutingFeedback" in js_content

    def test_handles_429_status(self, js_content):
        assert "429" in js_content

    def test_re_fetches_dashboard_after_action(self, js_content):
        assert "renderRoutingDashboard" in js_content
        # Should fetch /dashboard after a control action
        assert "/dashboard" in js_content
