"""Tests for Step 14 — runtime control and operator refinements.

Sections:
    A — Config refresh: refresh_routing_config produces diff
    B — Config refresh: timestamp tracking (get_config_loaded_at)
    C — Adaptive MI wrapper: per-request routing_enabled dispatch
    D — Centralized refresh: refresh_routing_runtime ordering
    E — Gate rebuild: config refresh → gate coherence
    F — API: POST /routing/refresh-config
    G — API: POST /routing/refresh-providers
    H — API: POST /routing/refresh-runtime
    I — Dashboard summary: config_loaded_at in system summary
    J — No secrets in control action responses
    K — Existing routing_enabled paths unbroken
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from app.services.model_routing_config import (
    RoutingConfig,
    get_routing_config,
    get_config_loaded_at,
    refresh_routing_config,
    reset_routing_config,
    set_routing_config,
)
from app.services.model_routing_contract import (
    ExecutionTrace,
    ExecutionStatus,
    Provider,
    RouteResolutionStatus,
)
from app.services.model_execution_gate import (
    get_execution_gate,
    reset_execution_gate,
)
from app.services.routing_dashboard_contract import BLOCKED_FIELDS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset all routing singletons before/after each test."""
    from app.api.routes_routing import _last_call
    reset_routing_config()
    reset_execution_gate()
    _last_call.clear()
    yield
    reset_routing_config()
    reset_execution_gate()
    _last_call.clear()


# Patch targets — patch at source modules since service uses local imports.
_P_REG = "app.services.model_provider_registry.get_registry"
_P_GATE = "app.services.model_execution_gate.get_execution_gate"
_P_CFG = "app.services.model_routing_config.get_routing_config"
_P_CFG_LOADED = "app.services.model_routing_config.get_config_loaded_at"


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _mock_registry():
    """Build a mock ProviderRegistry."""
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
    mock_reg.list_registered.return_value = ["localhost_llm"]
    return mock_reg


def _mock_gate():
    """Build a mock ProviderExecutionGate."""
    from app.services.model_execution_gate import GateSnapshot

    mock_gate = MagicMock()
    mock_gate.all_snapshots.return_value = {
        "localhost_llm": GateSnapshot(
            provider_id="localhost_llm", in_flight=0,
            max_concurrency=1, has_capacity=True,
        ),
    }
    mock_gate.effective_config_summary.return_value = {
        "config_source": "defaults",
        "default_max_concurrency": 1,
        "provider_limits": {"localhost_llm": 1},
        "in_flight": {"localhost_llm": 0},
    }
    return mock_gate


# ═══════════════════════════════════════════════════════════════════════════
# A — Config refresh: refresh_routing_config produces diff
# ═══════════════════════════════════════════════════════════════════════════


class TestRefreshRoutingConfig:
    def test_refresh_returns_previous_and_current(self):
        # Load initial config
        get_routing_config()
        result = refresh_routing_config()
        assert "previous" in result
        assert "current" in result
        assert "changed_fields" in result
        assert "config_loaded_at" in result

    def test_refresh_detects_no_changes(self):
        get_routing_config()
        result = refresh_routing_config()
        assert result["changed_fields"] == {}

    def test_refresh_detects_changes_after_env_update(self):
        # Load with defaults
        get_routing_config()
        # Simulate env change for next reload
        with patch.dict("os.environ", {"ROUTING_ENABLED": "false"}):
            result = refresh_routing_config()
        assert "routing_enabled" in result["changed_fields"]
        change = result["changed_fields"]["routing_enabled"]
        assert change["old"] is True
        assert change["new"] is False

    def test_refresh_replaces_global_config(self):
        get_routing_config()
        with patch.dict("os.environ", {"ROUTING_ENABLED": "false"}):
            refresh_routing_config()
        assert get_routing_config().routing_enabled is False

    def test_refresh_updates_loaded_at(self):
        get_routing_config()
        first_time = get_config_loaded_at()
        refresh_routing_config()
        second_time = get_config_loaded_at()
        assert second_time is not None
        assert second_time >= first_time


# ═══════════════════════════════════════════════════════════════════════════
# B — Config timestamp tracking
# ═══════════════════════════════════════════════════════════════════════════


class TestConfigLoadedAt:
    def test_none_before_first_load(self):
        assert get_config_loaded_at() is None

    def test_populated_after_first_load(self):
        get_routing_config()
        ts = get_config_loaded_at()
        assert ts is not None
        assert "T" in ts  # ISO format

    def test_updated_on_set_routing_config(self):
        set_routing_config(RoutingConfig(routing_enabled=False))
        ts = get_config_loaded_at()
        assert ts is not None

    def test_cleared_on_reset(self):
        get_routing_config()
        reset_routing_config()
        assert get_config_loaded_at() is None


# ═══════════════════════════════════════════════════════════════════════════
# C — Adaptive MI wrapper: per-request dispatch
# ═══════════════════════════════════════════════════════════════════════════


class TestAdaptiveRoutedModelInterpretation:
    def test_routes_when_enabled(self):
        from app.services.model_routing_integration import (
            adaptive_routed_model_interpretation,
        )
        set_routing_config(RoutingConfig(routing_enabled=True))

        mock_result = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "_routed": True,
        }
        with patch(
            "app.services.model_routing_integration.async_routed_model_interpretation",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_routed:
            result = _run(adaptive_routed_model_interpretation(None, {"messages": []}))
            mock_routed.assert_called_once()
            assert result["_routed"] is True

    def test_falls_back_when_disabled(self):
        from app.services.model_routing_integration import (
            adaptive_routed_model_interpretation,
        )
        set_routing_config(RoutingConfig(routing_enabled=False))

        mock_result = {
            "choices": [{"message": {"role": "assistant", "content": "legacy"}}],
        }
        with patch(
            "app.services.model_router.async_model_request",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_legacy:
            result = _run(adaptive_routed_model_interpretation(None, {"messages": []}))
            mock_legacy.assert_called_once()
            assert "_routed" not in result

    def test_responds_to_runtime_toggle(self):
        """Switching routing_enabled between calls changes dispatch path."""
        from app.services.model_routing_integration import (
            adaptive_routed_model_interpretation,
        )
        routed_result = {
            "choices": [{"message": {"role": "assistant", "content": "r"}}],
            "_routed": True,
        }
        legacy_result = {
            "choices": [{"message": {"role": "assistant", "content": "l"}}],
        }

        # Call 1: enabled → routed
        set_routing_config(RoutingConfig(routing_enabled=True))
        with patch(
            "app.services.model_routing_integration.async_routed_model_interpretation",
            new_callable=AsyncMock,
            return_value=routed_result,
        ):
            r1 = _run(adaptive_routed_model_interpretation(None, {}))

        # Call 2: disabled → legacy
        set_routing_config(RoutingConfig(routing_enabled=False))
        with patch(
            "app.services.model_router.async_model_request",
            new_callable=AsyncMock,
            return_value=legacy_result,
        ):
            r2 = _run(adaptive_routed_model_interpretation(None, {}))

        assert r1.get("_routed") is True
        assert "_routed" not in r2

    def test_signature_matches_deps(self):
        """adaptive_routed_model_interpretation has (http_client, payload) signature."""
        import inspect
        from app.services.model_routing_integration import (
            adaptive_routed_model_interpretation,
        )
        sig = inspect.signature(adaptive_routed_model_interpretation)
        params = list(sig.parameters.keys())
        assert params == ["_http_client", "payload"]


# ═══════════════════════════════════════════════════════════════════════════
# D — Centralized refresh: refresh_routing_runtime ordering
# ═══════════════════════════════════════════════════════════════════════════


class TestRefreshRoutingRuntime:
    @patch(_P_REG, return_value=_mock_registry())
    def test_returns_three_stages(self, _reg):
        from app.services.routing_dashboard_service import refresh_routing_runtime
        result = refresh_routing_runtime()
        assert result["stages"] == [
            "config_refreshed",
            "gate_rebuilt",
            "providers_refreshed",
        ]

    @patch(_P_REG, return_value=_mock_registry())
    def test_result_has_config_diff(self, _reg):
        from app.services.routing_dashboard_service import refresh_routing_runtime
        result = refresh_routing_runtime()
        assert "config" in result
        assert "previous" in result["config"]
        assert "current" in result["config"]

    @patch(_P_REG, return_value=_mock_registry())
    def test_result_has_gate_summary(self, _reg):
        from app.services.routing_dashboard_service import refresh_routing_runtime
        result = refresh_routing_runtime()
        assert "gate" in result
        assert "previous" in result["gate"]
        assert "current" in result["gate"]

    @patch(_P_REG, return_value=_mock_registry())
    def test_result_has_provider_statuses(self, _reg):
        from app.services.routing_dashboard_service import refresh_routing_runtime
        result = refresh_routing_runtime()
        assert "providers" in result
        assert isinstance(result["providers"], list)


# ═══════════════════════════════════════════════════════════════════════════
# E — Gate rebuild: config refresh → gate coherence
# ═══════════════════════════════════════════════════════════════════════════


class TestGateConfigCoherence:
    def test_gate_uses_config_concurrency(self):
        set_routing_config(RoutingConfig(
            provider_concurrency={"localhost_llm": 3},
        ))
        reset_execution_gate()
        gate = get_execution_gate()
        assert gate.get_max_concurrency("localhost_llm") == 3

    def test_gate_rebuilds_after_config_change(self):
        """Gate reflects new config after reset + rebuild."""
        set_routing_config(RoutingConfig(
            provider_concurrency={"localhost_llm": 1},
        ))
        reset_execution_gate()
        g1 = get_execution_gate()
        assert g1.get_max_concurrency("localhost_llm") == 1

        # Change config
        set_routing_config(RoutingConfig(
            provider_concurrency={"localhost_llm": 5},
        ))
        reset_execution_gate()
        g2 = get_execution_gate()
        assert g2.get_max_concurrency("localhost_llm") == 5

    @patch(_P_REG, return_value=_mock_registry())
    def test_runtime_refresh_rebuilds_gate(self, _reg):
        """refresh_routing_runtime rebuilds gate from new config."""
        from app.services.routing_dashboard_service import refresh_routing_runtime
        set_routing_config(RoutingConfig(
            provider_concurrency={"localhost_llm": 1},
        ))
        reset_execution_gate()
        get_execution_gate()  # build initial gate

        with patch.dict("os.environ", {
            "ROUTING_CONCURRENCY_LOCALHOST_LLM": "4",
        }):
            result = refresh_routing_runtime()

        new_gate = get_execution_gate()
        assert new_gate.get_max_concurrency("localhost_llm") == 4


# ═══════════════════════════════════════════════════════════════════════════
# F — API: POST /routing/refresh-config
# ═══════════════════════════════════════════════════════════════════════════


class TestRefreshConfigAPI:
    def test_returns_action_and_result(self):
        from app.api.routes_routing import post_refresh_config
        get_routing_config()  # initialize first
        result = _run(post_refresh_config())
        assert result["action"] == "config_refreshed"
        assert "result" in result
        assert "changed_fields" in result["result"]

    def test_detects_env_change(self):
        from app.api.routes_routing import post_refresh_config
        get_routing_config()
        with patch.dict("os.environ", {"ROUTING_ENABLED": "false"}):
            result = _run(post_refresh_config())
        assert "routing_enabled" in result["result"]["changed_fields"]


# ═══════════════════════════════════════════════════════════════════════════
# G — API: POST /routing/refresh-providers
# ═══════════════════════════════════════════════════════════════════════════


class TestRefreshProvidersAPI:
    @patch(_P_REG, return_value=_mock_registry())
    def test_returns_action_and_providers(self, _reg):
        from app.api.routes_routing import post_refresh_providers
        result = _run(post_refresh_providers())
        assert result["action"] == "providers_refreshed"
        assert "providers" in result
        assert isinstance(result["providers"], list)

    @patch(_P_REG, return_value=_mock_registry())
    def test_provider_fields_are_safe(self, _reg):
        from app.api.routes_routing import post_refresh_providers
        result = _run(post_refresh_providers())
        for p in result["providers"]:
            assert "provider_id" in p
            assert "state" in p
            for f in BLOCKED_FIELDS:
                assert f not in p


# ═══════════════════════════════════════════════════════════════════════════
# H — API: POST /routing/refresh-runtime
# ═══════════════════════════════════════════════════════════════════════════


class TestRefreshRuntimeAPI:
    @patch(_P_REG, return_value=_mock_registry())
    def test_returns_action_and_stages(self, _reg):
        from app.api.routes_routing import post_refresh_runtime
        result = _run(post_refresh_runtime())
        assert result["action"] == "runtime_refreshed"
        assert "result" in result
        stages = result["result"]["stages"]
        assert "config_refreshed" in stages
        assert "gate_rebuilt" in stages
        assert "providers_refreshed" in stages


# ═══════════════════════════════════════════════════════════════════════════
# I — Dashboard summary: config_loaded_at in system summary
# ═══════════════════════════════════════════════════════════════════════════


class TestDashboardConfigLoadedAt:
    @patch(_P_REG)
    @patch(_P_CFG)
    @patch(_P_CFG_LOADED, return_value="2026-03-17T12:00:00+00:00")
    def test_system_summary_includes_loaded_at(self, _loaded, mock_cfg, mock_reg):
        from app.services.routing_dashboard_service import build_routing_system_summary
        mock_cfg.return_value = RoutingConfig()
        mock_reg.return_value = MagicMock(
            list_registered=MagicMock(return_value=["a"])
        )
        summary = build_routing_system_summary()
        d = summary.to_dict()
        assert "config_loaded_at" in d
        assert d["config_loaded_at"] == "2026-03-17T12:00:00+00:00"

    @patch(_P_REG)
    @patch(_P_CFG)
    @patch(_P_CFG_LOADED, return_value=None)
    def test_system_summary_loaded_at_none_when_not_loaded(self, _loaded, mock_cfg, mock_reg):
        from app.services.routing_dashboard_service import build_routing_system_summary
        mock_cfg.return_value = RoutingConfig()
        mock_reg.return_value = MagicMock(
            list_registered=MagicMock(return_value=[])
        )
        summary = build_routing_system_summary()
        assert summary.config_loaded_at is None


# ═══════════════════════════════════════════════════════════════════════════
# J — No secrets in control action responses
# ═══════════════════════════════════════════════════════════════════════════


class TestNoSecretsInControlActions:
    def test_config_refresh_no_secrets(self):
        get_routing_config()
        result = refresh_routing_config()
        flat = str(result)
        for f in BLOCKED_FIELDS:
            assert f not in flat

    @patch(_P_REG, return_value=_mock_registry())
    def test_runtime_refresh_no_secrets(self, _reg):
        from app.services.routing_dashboard_service import refresh_routing_runtime
        result = refresh_routing_runtime()
        flat = str(result)
        for f in BLOCKED_FIELDS:
            assert f not in flat


# ═══════════════════════════════════════════════════════════════════════════
# K — Existing routing_enabled paths unbroken
# ═══════════════════════════════════════════════════════════════════════════


class TestExistingPathsUnbroken:
    def test_routing_is_enabled_reads_live_config(self):
        from app.services.model_routing_integration import _routing_is_enabled
        set_routing_config(RoutingConfig(routing_enabled=True))
        assert _routing_is_enabled() is True
        set_routing_config(RoutingConfig(routing_enabled=False))
        assert _routing_is_enabled() is False

    def test_execute_routed_model_respects_kill_switch(self):
        from app.services.model_routing_integration import (
            RoutingDisabledError,
            execute_routed_model,
        )
        set_routing_config(RoutingConfig(routing_enabled=False))
        with pytest.raises(RoutingDisabledError):
            execute_routed_model(
                task_type="test",
                messages=[{"role": "user", "content": "hi"}],
            )

    def test_main_wires_adaptive_wrapper(self):
        """main.py now uses adaptive_routed_model_interpretation."""
        import importlib
        spec = importlib.util.find_spec("app.main")
        with open(spec.origin, "r", encoding="utf-8") as f:
            content = f.read()
        assert "adaptive_routed_model_interpretation" in content
        # Should NOT contain the old startup snapshot pattern
        assert "if get_routing_config().routing_enabled" not in content
