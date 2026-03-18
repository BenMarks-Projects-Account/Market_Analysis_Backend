"""Step 9 — Routing configuration hardening + configurable concurrency tests.

Validates:
    A. RoutingConfig defaults match Step 1-8 hardcoded behavior
    B. RoutingConfig validation catches invalid values
    C. RoutingConfig environment loading
    D. ProviderExecutionGate config-driven construction
    E. Gate behavior with configured concurrency
    F. Gate observability (effective_config_summary)
    G. Probe config integration
    H. Policy engine compatibility under config
    I. Config singleton lifecycle

Total: ~60 tests across 9 sections.
"""

from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from app.services.model_execution_gate import (
    DEFAULT_MAX_CONCURRENCY,
    GateSnapshot,
    ProviderExecutionGate,
    get_execution_gate,
    reset_execution_gate,
)
from app.services.model_routing_config import (
    RoutingConfig,
    _SAFE_DEFAULT_MAX_CONCURRENCY,
    _SAFE_DEFAULT_PROBE_DEGRADED_THRESHOLD_MS,
    _SAFE_DEFAULT_PROBE_TIMEOUT_SECONDS,
    _SAFE_DEFAULT_PROVIDER_CONCURRENCY,
    _validate_concurrency_map,
    _validate_positive_float,
    _validate_positive_int,
    get_routing_config,
    reset_routing_config,
    set_routing_config,
)
from app.services.model_routing_contract import Provider


# ═══════════════════════════════════════════════════════════
# A. RoutingConfig defaults match Step 1-8 behavior
# ═══════════════════════════════════════════════════════════


class TestRoutingConfigDefaults:
    """Default RoutingConfig preserves existing hardcoded behavior."""

    def test_default_max_concurrency_is_one(self):
        cfg = RoutingConfig()
        assert cfg.default_max_concurrency == 1

    def test_default_provider_concurrency_matches_hardcoded(self):
        cfg = RoutingConfig()
        assert cfg.provider_concurrency == _SAFE_DEFAULT_PROVIDER_CONCURRENCY

    def test_all_known_providers_default_to_one(self):
        cfg = RoutingConfig()
        for pid in Provider:
            assert cfg.get_max_concurrency(pid.value) == 1

    def test_unknown_provider_defaults_to_one(self):
        cfg = RoutingConfig()
        assert cfg.get_max_concurrency("some_future_provider") == 1

    def test_default_probe_timeout(self):
        cfg = RoutingConfig()
        assert cfg.probe_timeout_seconds == _SAFE_DEFAULT_PROBE_TIMEOUT_SECONDS
        assert cfg.probe_timeout_seconds == 3.0

    def test_default_probe_degraded_threshold(self):
        cfg = RoutingConfig()
        assert cfg.probe_degraded_threshold_ms == _SAFE_DEFAULT_PROBE_DEGRADED_THRESHOLD_MS
        assert cfg.probe_degraded_threshold_ms == 2000.0

    def test_default_routing_enabled(self):
        cfg = RoutingConfig()
        assert cfg.routing_enabled is True

    def test_default_bedrock_enabled(self):
        cfg = RoutingConfig()
        assert cfg.bedrock_enabled is True

    def test_default_config_source(self):
        cfg = RoutingConfig()
        assert cfg.config_source == "defaults"

    def test_effective_summary_complete(self):
        cfg = RoutingConfig()
        summary = cfg.effective_summary()
        assert "config_source" in summary
        assert "routing_enabled" in summary
        assert "bedrock_enabled" in summary
        assert "default_max_concurrency" in summary
        assert "provider_concurrency" in summary
        assert "probe_timeout_seconds" in summary
        assert "probe_degraded_threshold_ms" in summary


# ═══════════════════════════════════════════════════════════
# B. RoutingConfig validation catches invalid values
# ═══════════════════════════════════════════════════════════


class TestRoutingConfigValidation:
    """Invalid config values fall back to safe defaults."""

    def test_negative_max_concurrency_falls_back(self):
        cfg = RoutingConfig(default_max_concurrency=-1)
        assert cfg.default_max_concurrency == _SAFE_DEFAULT_MAX_CONCURRENCY

    def test_zero_max_concurrency_falls_back(self):
        cfg = RoutingConfig(default_max_concurrency=0)
        assert cfg.default_max_concurrency == _SAFE_DEFAULT_MAX_CONCURRENCY

    def test_string_max_concurrency_falls_back(self):
        cfg = RoutingConfig(default_max_concurrency="invalid")  # type: ignore
        assert cfg.default_max_concurrency == _SAFE_DEFAULT_MAX_CONCURRENCY

    def test_negative_probe_timeout_falls_back(self):
        cfg = RoutingConfig(probe_timeout_seconds=-5.0)
        assert cfg.probe_timeout_seconds == _SAFE_DEFAULT_PROBE_TIMEOUT_SECONDS

    def test_zero_probe_timeout_falls_back(self):
        cfg = RoutingConfig(probe_timeout_seconds=0.0)
        assert cfg.probe_timeout_seconds == _SAFE_DEFAULT_PROBE_TIMEOUT_SECONDS

    def test_string_probe_timeout_falls_back(self):
        cfg = RoutingConfig(probe_timeout_seconds="bad")  # type: ignore
        assert cfg.probe_timeout_seconds == _SAFE_DEFAULT_PROBE_TIMEOUT_SECONDS

    def test_negative_degraded_threshold_falls_back(self):
        cfg = RoutingConfig(probe_degraded_threshold_ms=-100.0)
        assert cfg.probe_degraded_threshold_ms == _SAFE_DEFAULT_PROBE_DEGRADED_THRESHOLD_MS

    def test_invalid_provider_concurrency_value_falls_back(self):
        cfg = RoutingConfig(
            provider_concurrency={
                Provider.LOCALHOST_LLM.value: -3,
                Provider.NETWORK_MODEL_MACHINE.value: 2,
            }
        )
        assert cfg.get_max_concurrency(Provider.LOCALHOST_LLM.value) == 1
        assert cfg.get_max_concurrency(Provider.NETWORK_MODEL_MACHINE.value) == 2

    def test_mixed_valid_invalid_provider_concurrency(self):
        cfg = RoutingConfig(
            provider_concurrency={
                "good_provider": 5,
                "bad_provider": "not_a_number",  # type: ignore
            }
        )
        assert cfg.get_max_concurrency("good_provider") == 5
        assert cfg.get_max_concurrency("bad_provider") == 1

    def test_valid_high_concurrency_accepted(self):
        cfg = RoutingConfig(default_max_concurrency=10)
        assert cfg.default_max_concurrency == 10

    def test_valid_probe_timeout_accepted(self):
        cfg = RoutingConfig(probe_timeout_seconds=5.0)
        assert cfg.probe_timeout_seconds == 5.0


# ═══════════════════════════════════════════════════════════
# C. Validation helper functions
# ═══════════════════════════════════════════════════════════


class TestValidationHelpers:
    """Unit tests for validation primitives."""

    def test_validate_positive_int_valid(self):
        assert _validate_positive_int(5, "test", 1) == 5

    def test_validate_positive_int_zero(self):
        assert _validate_positive_int(0, "test", 42) == 42

    def test_validate_positive_int_negative(self):
        assert _validate_positive_int(-3, "test", 7) == 7

    def test_validate_positive_int_string_number(self):
        assert _validate_positive_int("3", "test", 1) == 3

    def test_validate_positive_int_invalid_string(self):
        assert _validate_positive_int("abc", "test", 9) == 9

    def test_validate_positive_int_none(self):
        assert _validate_positive_int(None, "test", 5) == 5

    def test_validate_positive_float_valid(self):
        assert _validate_positive_float(3.5, "test", 1.0) == 3.5

    def test_validate_positive_float_zero(self):
        assert _validate_positive_float(0.0, "test", 2.0) == 2.0

    def test_validate_positive_float_negative(self):
        assert _validate_positive_float(-1.5, "test", 3.0) == 3.0

    def test_validate_positive_float_string(self):
        assert _validate_positive_float("bad", "test", 4.0) == 4.0

    def test_validate_concurrency_map_none(self):
        result = _validate_concurrency_map(None, "test")
        assert result == _SAFE_DEFAULT_PROVIDER_CONCURRENCY

    def test_validate_concurrency_map_valid(self):
        result = _validate_concurrency_map({"p1": 3, "p2": 5}, "test")
        assert result == {"p1": 3, "p2": 5}

    def test_validate_concurrency_map_invalid_value(self):
        result = _validate_concurrency_map({"p1": -1}, "test")
        assert result["p1"] == 1  # fell back to safe default


# ═══════════════════════════════════════════════════════════
# D. ProviderExecutionGate config-driven construction
# ═══════════════════════════════════════════════════════════


class TestGateFromConfig:
    """Gate constructed from RoutingConfig."""

    def test_from_default_config(self):
        cfg = RoutingConfig()
        gate = ProviderExecutionGate.from_config(cfg)
        for pid in Provider:
            assert gate.get_max_concurrency(pid.value) == 1

    def test_from_config_with_custom_concurrency(self):
        cfg = RoutingConfig(
            provider_concurrency={
                Provider.LOCALHOST_LLM.value: 3,
                Provider.BEDROCK_TITAN_NOVA_PRO.value: 5,
            },
        )
        gate = ProviderExecutionGate.from_config(cfg)
        assert gate.get_max_concurrency(Provider.LOCALHOST_LLM.value) == 3
        assert gate.get_max_concurrency(Provider.BEDROCK_TITAN_NOVA_PRO.value) == 5

    def test_from_config_with_custom_default(self):
        cfg = RoutingConfig(
            provider_concurrency={},
            default_max_concurrency=4,
        )
        gate = ProviderExecutionGate.from_config(cfg)
        # Unknown providers fall back to 4.
        assert gate.get_max_concurrency("unknown_provider") == 4

    def test_from_none_config_uses_routing_config(self):
        """When config=None, from_config loads from get_routing_config()."""
        with patch(
            "app.services.model_routing_config.get_routing_config",
            side_effect=ImportError("mock"),
        ):
            # Should fall back to hardcoded defaults on ImportError.
            gate = ProviderExecutionGate.from_config()
            assert gate.get_max_concurrency(Provider.LOCALHOST_LLM.value) == 1

    def test_from_config_preserves_config_source(self):
        cfg = RoutingConfig(config_source="test_source")
        gate = ProviderExecutionGate.from_config(cfg)
        summary = gate.effective_config_summary()
        assert summary["config_source"] == "test_source"

    def test_direct_constructor_still_works(self):
        """Backward compat: direct constructor with max_concurrency dict."""
        gate = ProviderExecutionGate(
            max_concurrency={Provider.LOCALHOST_LLM.value: 2}
        )
        assert gate.get_max_concurrency(Provider.LOCALHOST_LLM.value) == 2


# ═══════════════════════════════════════════════════════════
# E. Gate behavior with configured concurrency
# ═══════════════════════════════════════════════════════════


class TestGateBehaviorWithConfig:
    """Gate acquire/release/reservation with configured limits."""

    def test_single_flight_default(self):
        gate = ProviderExecutionGate.from_config(RoutingConfig())
        pid = Provider.LOCALHOST_LLM.value
        assert gate.acquire(pid) is True
        assert gate.acquire(pid) is False  # at capacity
        gate.release(pid)
        assert gate.acquire(pid) is True
        gate.release(pid)

    def test_multi_slot_concurrency(self):
        cfg = RoutingConfig(
            provider_concurrency={Provider.LOCALHOST_LLM.value: 3},
        )
        gate = ProviderExecutionGate.from_config(cfg)
        pid = Provider.LOCALHOST_LLM.value

        assert gate.acquire(pid) is True  # slot 1
        assert gate.acquire(pid) is True  # slot 2
        assert gate.acquire(pid) is True  # slot 3
        assert gate.acquire(pid) is False  # at capacity

        gate.release(pid)
        assert gate.acquire(pid) is True  # slot 3 again
        gate.release(pid)
        gate.release(pid)
        gate.release(pid)

    def test_reservation_context_manager(self):
        cfg = RoutingConfig(
            provider_concurrency={Provider.LOCALHOST_LLM.value: 2},
        )
        gate = ProviderExecutionGate.from_config(cfg)
        pid = Provider.LOCALHOST_LLM.value

        with gate.reservation(pid) as acquired1:
            assert acquired1 is True
            with gate.reservation(pid) as acquired2:
                assert acquired2 is True
                with gate.reservation(pid) as acquired3:
                    assert acquired3 is False  # at capacity

        # All released now.
        assert gate.in_flight_count(pid) == 0

    def test_different_providers_independent(self):
        cfg = RoutingConfig(
            provider_concurrency={
                Provider.LOCALHOST_LLM.value: 1,
                Provider.BEDROCK_TITAN_NOVA_PRO.value: 2,
            },
        )
        gate = ProviderExecutionGate.from_config(cfg)

        assert gate.acquire(Provider.LOCALHOST_LLM.value) is True
        assert gate.acquire(Provider.LOCALHOST_LLM.value) is False
        assert gate.acquire(Provider.BEDROCK_TITAN_NOVA_PRO.value) is True
        assert gate.acquire(Provider.BEDROCK_TITAN_NOVA_PRO.value) is True

        gate.release(Provider.LOCALHOST_LLM.value)
        gate.release(Provider.BEDROCK_TITAN_NOVA_PRO.value)
        gate.release(Provider.BEDROCK_TITAN_NOVA_PRO.value)

    def test_thread_safety_with_configured_limits(self):
        """Concurrent acquire/release under configured concurrency."""
        cfg = RoutingConfig(
            provider_concurrency={Provider.LOCALHOST_LLM.value: 5},
        )
        gate = ProviderExecutionGate.from_config(cfg)
        pid = Provider.LOCALHOST_LLM.value

        acquired_count = {"value": 0}
        lock = threading.Lock()

        def worker():
            if gate.acquire(pid):
                with lock:
                    acquired_count["value"] += 1
                gate.release(pid)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert gate.in_flight_count(pid) == 0
        assert acquired_count["value"] == 20  # all eventually acquired

    def test_set_max_concurrency_still_works(self):
        """Runtime override via set_max_concurrency is still supported."""
        gate = ProviderExecutionGate.from_config(RoutingConfig())
        pid = Provider.LOCALHOST_LLM.value
        assert gate.get_max_concurrency(pid) == 1

        gate.set_max_concurrency(pid, 3)
        assert gate.get_max_concurrency(pid) == 3

    def test_set_max_concurrency_rejects_zero(self):
        gate = ProviderExecutionGate.from_config(RoutingConfig())
        with pytest.raises(ValueError, match="must be >= 1"):
            gate.set_max_concurrency(Provider.LOCALHOST_LLM.value, 0)


# ═══════════════════════════════════════════════════════════
# F. Gate observability
# ═══════════════════════════════════════════════════════════


class TestGateObservability:
    """effective_config_summary and snapshot methods."""

    def test_effective_config_summary_structure(self):
        cfg = RoutingConfig(
            provider_concurrency={Provider.LOCALHOST_LLM.value: 2},
            config_source="test",
        )
        gate = ProviderExecutionGate.from_config(cfg)
        summary = gate.effective_config_summary()

        assert summary["config_source"] == "test"
        assert summary["default_max_concurrency"] >= 1
        assert Provider.LOCALHOST_LLM.value in summary["provider_limits"]
        assert summary["provider_limits"][Provider.LOCALHOST_LLM.value] == 2

    def test_in_flight_visible_in_summary(self):
        gate = ProviderExecutionGate.from_config(RoutingConfig(
            provider_concurrency={Provider.LOCALHOST_LLM.value: 3},
        ))
        pid = Provider.LOCALHOST_LLM.value

        gate.acquire(pid)
        summary = gate.effective_config_summary()
        assert summary["in_flight"][pid] == 1

        gate.release(pid)
        summary = gate.effective_config_summary()
        assert summary["in_flight"][pid] == 0

    def test_snapshot_reflects_config(self):
        cfg = RoutingConfig(
            provider_concurrency={Provider.LOCALHOST_LLM.value: 4},
        )
        gate = ProviderExecutionGate.from_config(cfg)
        snap = gate.snapshot(Provider.LOCALHOST_LLM.value)
        assert snap.max_concurrency == 4
        assert snap.in_flight == 0
        assert snap.has_capacity is True

    def test_all_snapshots_complete(self):
        cfg = RoutingConfig(
            provider_concurrency={
                Provider.LOCALHOST_LLM.value: 1,
                Provider.BEDROCK_TITAN_NOVA_PRO.value: 2,
            },
        )
        gate = ProviderExecutionGate.from_config(cfg)
        snaps = gate.all_snapshots()
        assert Provider.LOCALHOST_LLM.value in snaps
        assert Provider.BEDROCK_TITAN_NOVA_PRO.value in snaps


# ═══════════════════════════════════════════════════════════
# G. Probe config integration
# ═══════════════════════════════════════════════════════════


class TestProbeConfigIntegration:
    """Verify probe helpers read from routing config."""

    def test_get_probe_timeout_from_config(self):
        cfg = RoutingConfig(probe_timeout_seconds=5.0)
        set_routing_config(cfg)
        try:
            from app.services.model_provider_adapters import _get_probe_timeout
            assert _get_probe_timeout() == 5.0
        finally:
            reset_routing_config()

    def test_get_probe_degraded_threshold_from_config(self):
        cfg = RoutingConfig(probe_degraded_threshold_ms=3000.0)
        set_routing_config(cfg)
        try:
            from app.services.model_provider_adapters import _get_probe_degraded_threshold
            assert _get_probe_degraded_threshold() == 3000.0
        finally:
            reset_routing_config()

    def test_probe_defaults_match_legacy(self):
        """With default config, probe params match Step 1-8 hardcoded values."""
        cfg = RoutingConfig()
        set_routing_config(cfg)
        try:
            from app.services.model_provider_adapters import _get_probe_timeout, _get_probe_degraded_threshold
            assert _get_probe_timeout() == 3.0
            assert _get_probe_degraded_threshold() == 2000.0
        finally:
            reset_routing_config()


# ═══════════════════════════════════════════════════════════
# H. Policy engine compatibility under config
# ═══════════════════════════════════════════════════════════


class TestPolicyCompatibility:
    """Policy engine still works correctly with config-driven gate."""

    def test_gate_from_config_used_in_eligibility(self):
        from app.services.model_provider_base import ProbeResult
        from app.services.model_router_policy import is_provider_eligible

        probe = ProbeResult(
            provider=Provider.LOCALHOST_LLM.value,
            configured=True,
            state="available",
            probe_success=True,
            status_reason="healthy",
        )
        # Gate with capacity.
        gate = ProviderExecutionGate.from_config(RoutingConfig(
            provider_concurrency={Provider.LOCALHOST_LLM.value: 2},
        ))
        snap = gate.snapshot(Provider.LOCALHOST_LLM.value)
        eligible, reason = is_provider_eligible(probe, snap)
        assert eligible is True

    def test_gate_at_capacity_blocks_dispatch(self):
        from app.services.model_provider_base import ProbeResult
        from app.services.model_router_policy import is_provider_eligible

        probe = ProbeResult(
            provider=Provider.LOCALHOST_LLM.value,
            configured=True,
            state="available",
            probe_success=True,
            status_reason="healthy",
        )
        gate = ProviderExecutionGate.from_config(RoutingConfig(
            provider_concurrency={Provider.LOCALHOST_LLM.value: 1},
        ))
        gate.acquire(Provider.LOCALHOST_LLM.value)
        snap = gate.snapshot(Provider.LOCALHOST_LLM.value)
        eligible, reason = is_provider_eligible(probe, snap)
        assert eligible is False
        assert reason == "at_max_concurrency"
        gate.release(Provider.LOCALHOST_LLM.value)

    def test_rank_candidates_respects_config(self):
        from app.services.model_provider_base import ProbeResult
        from app.services.model_router_policy import rank_candidates

        pid = Provider.LOCALHOST_LLM.value
        probes = {
            pid: ProbeResult(
                provider=pid,
                configured=True,
                state="available",
                probe_success=True,
                status_reason="healthy",
            ),
        }
        gate = ProviderExecutionGate.from_config(RoutingConfig(
            provider_concurrency={pid: 1},
        ))
        # No slots used — should be eligible.
        results = rank_candidates(probes, gate, [pid])
        assert results[0] == (pid, True, "")

        # Use the slot — should be at capacity.
        gate.acquire(pid)
        results = rank_candidates(probes, gate, [pid])
        assert results[0][1] is False  # not eligible


# ═══════════════════════════════════════════════════════════
# I. Config singleton lifecycle
# ═══════════════════════════════════════════════════════════


class TestConfigSingletonLifecycle:
    """Singleton get/set/reset for routing config and gate."""

    def test_get_routing_config_returns_defaults(self):
        reset_routing_config()
        try:
            cfg = get_routing_config()
            assert isinstance(cfg, RoutingConfig)
            assert cfg.default_max_concurrency == 1
        finally:
            reset_routing_config()

    def test_set_routing_config_replaces(self):
        custom = RoutingConfig(default_max_concurrency=5, config_source="custom")
        set_routing_config(custom)
        try:
            cfg = get_routing_config()
            assert cfg.default_max_concurrency == 5
            assert cfg.config_source == "custom"
        finally:
            reset_routing_config()

    def test_reset_routing_config_clears(self):
        set_routing_config(RoutingConfig(config_source="temp"))
        reset_routing_config()
        cfg = get_routing_config()
        # Should be freshly loaded (not "temp").
        assert cfg.config_source != "temp"
        reset_routing_config()

    def test_get_execution_gate_uses_config(self):
        """Global gate picks up routing config."""
        reset_execution_gate()
        custom = RoutingConfig(
            provider_concurrency={Provider.LOCALHOST_LLM.value: 8},
            config_source="gate_test",
        )
        set_routing_config(custom)
        try:
            gate = get_execution_gate()
            assert gate.get_max_concurrency(Provider.LOCALHOST_LLM.value) == 8
            summary = gate.effective_config_summary()
            assert summary["config_source"] == "gate_test"
        finally:
            reset_routing_config()
            reset_execution_gate()

    def test_reset_gate_allows_config_reload(self):
        reset_routing_config()
        reset_execution_gate()

        # First gate — default config.
        gate1 = get_execution_gate()
        assert gate1.get_max_concurrency(Provider.LOCALHOST_LLM.value) == 1

        # Change config and reset gate.
        set_routing_config(RoutingConfig(
            provider_concurrency={Provider.LOCALHOST_LLM.value: 6},
        ))
        reset_execution_gate()

        gate2 = get_execution_gate()
        assert gate2.get_max_concurrency(Provider.LOCALHOST_LLM.value) == 6

        reset_routing_config()
        reset_execution_gate()

    def test_routing_config_env_loading(self):
        """Environment variables are picked up by _load_from_env."""
        reset_routing_config()
        env_vars = {
            "ROUTING_DEFAULT_MAX_CONCURRENCY": "3",
            "ROUTING_CONCURRENCY_LOCALHOST_LLM": "5",
            "ROUTING_PROBE_TIMEOUT_SECONDS": "7.5",
            "ROUTING_PROBE_DEGRADED_THRESHOLD_MS": "4000",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            reset_routing_config()
            cfg = get_routing_config()
            assert cfg.default_max_concurrency == 3
            assert cfg.get_max_concurrency(Provider.LOCALHOST_LLM.value) == 5
            assert cfg.probe_timeout_seconds == 7.5
            assert cfg.probe_degraded_threshold_ms == 4000.0
            assert "env:" in cfg.config_source
        reset_routing_config()

    def test_env_invalid_values_fall_back(self):
        """Invalid env values produce safe defaults."""
        reset_routing_config()
        env_vars = {
            "ROUTING_DEFAULT_MAX_CONCURRENCY": "not_a_number",
            "ROUTING_PROBE_TIMEOUT_SECONDS": "-5",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            reset_routing_config()
            cfg = get_routing_config()
            assert cfg.default_max_concurrency == 1  # safe fallback
            assert cfg.probe_timeout_seconds == 3.0  # safe fallback
        reset_routing_config()
