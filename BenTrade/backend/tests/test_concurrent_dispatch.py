"""Tests for concurrent model dispatch (Step 22).

Covers:
    • ProviderExecutionGate.wait_for_any_capacity — blocking wait semantics
    • route_and_execute wait-retry when all providers at capacity
    • Concurrent dispatch routes to different providers
    • Timeout and max-attempt exhaustion
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.model_execution_gate import (
    GateSnapshot,
    ProviderExecutionGate,
)
from app.services.model_provider_base import ProbeResult, ProviderResult
from app.services.model_provider_registry import ProviderRegistry
from app.services.model_router_policy import (
    route_and_execute,
)
from app.services.model_routing_contract import (
    ExecutionMode,
    ExecutionRequest,
    ExecutionStatus,
    Provider,
    ProviderState,
)


# ---------------------------------------------------------------------------
# Helpers (reused from test_router_policy patterns)
# ---------------------------------------------------------------------------

def _probe(
    provider: str,
    state: str = ProviderState.AVAILABLE.value,
    configured: bool = True,
    **kwargs: Any,
) -> ProbeResult:
    return ProbeResult(
        provider=provider,
        configured=configured,
        state=state,
        probe_success=True,
        status_reason=kwargs.get("status_reason", "test"),
        **{k: v for k, v in kwargs.items() if k != "status_reason"},
    )


def _result(
    provider: str,
    success: bool = True,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ProviderResult:
    return ProviderResult(
        provider=provider,
        success=success,
        execution_status=(
            ExecutionStatus.SUCCESS.value if success
            else ExecutionStatus.FAILED.value
        ),
        content="test content" if success else None,
        error_code=error_code,
        error_message=error_message,
    )


def _request(mode: str = ExecutionMode.LOCAL_DISTRIBUTED.value, **kw: Any) -> ExecutionRequest:
    return ExecutionRequest(mode=mode, **kw)


def _build_registry(
    probe_map: dict[str, ProbeResult] | None = None,
    execute_map: dict[str, ProviderResult] | None = None,
) -> ProviderRegistry:
    probe_map = probe_map or {}
    execute_map = execute_map or {}
    registry = ProviderRegistry()
    for pid in set(list(probe_map.keys()) + list(execute_map.keys())):
        adapter = MagicMock()
        adapter.provider_id = pid
        probe = probe_map.get(pid, _probe(pid))
        adapter.probe.return_value = probe
        adapter.probe_state.return_value = probe.state
        adapter.is_configured = probe.configured
        result = execute_map.get(pid, _result(pid))
        adapter.execute.return_value = result
        registry.register(adapter)
    return registry


# =========================================================================
# Part A — wait_for_any_capacity
# =========================================================================


class TestWaitForAnyCapacity:
    """Gate.wait_for_any_capacity blocks until a provider is free."""

    def test_returns_true_immediately_when_capacity_available(self):
        gate = ProviderExecutionGate()
        providers = [Provider.LOCALHOST_LLM.value, Provider.NETWORK_MODEL_MACHINE.value]
        # Nothing acquired — should return True immediately.
        assert gate.wait_for_any_capacity(providers, timeout=0.1) is True

    def test_returns_true_when_partial_capacity(self):
        gate = ProviderExecutionGate()
        p1 = Provider.LOCALHOST_LLM.value
        p2 = Provider.NETWORK_MODEL_MACHINE.value
        gate.acquire(p1)
        # p1 is busy but p2 is free.
        assert gate.wait_for_any_capacity([p1, p2], timeout=0.1) is True
        gate.release(p1)

    def test_blocks_then_returns_true_on_release(self):
        gate = ProviderExecutionGate()
        p1 = Provider.LOCALHOST_LLM.value
        gate.acquire(p1)

        result = [None]

        def waiter():
            result[0] = gate.wait_for_any_capacity([p1], timeout=5.0)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)  # Let waiter block
        gate.release(p1)  # Free up the slot
        t.join(timeout=2)

        assert result[0] is True
        assert gate.in_flight_count(p1) == 0

    def test_returns_false_on_timeout(self):
        gate = ProviderExecutionGate()
        p1 = Provider.LOCALHOST_LLM.value
        gate.acquire(p1)
        # Short timeout — should return False without release.
        assert gate.wait_for_any_capacity([p1], timeout=0.1) is False
        gate.release(p1)

    def test_wakes_on_any_provider_release(self):
        """Release of provider B wakes a waiter on [A, B]."""
        gate = ProviderExecutionGate()
        p1 = Provider.LOCALHOST_LLM.value
        p2 = Provider.NETWORK_MODEL_MACHINE.value
        gate.acquire(p1)
        gate.acquire(p2)

        result = [None]

        def waiter():
            result[0] = gate.wait_for_any_capacity([p1, p2], timeout=5.0)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        gate.release(p2)  # Free p2 while p1 still busy
        t.join(timeout=2)

        assert result[0] is True
        gate.release(p1)  # Cleanup

    def test_multiple_waiters_all_wake(self):
        """Multiple threads waiting should all wake on release."""
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        gate.acquire(pid)

        results = [None, None]

        def waiter(idx):
            results[idx] = gate.wait_for_any_capacity([pid], timeout=5.0)

        threads = [threading.Thread(target=waiter, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        time.sleep(0.05)
        gate.release(pid)
        for t in threads:
            t.join(timeout=2)

        assert results[0] is True
        assert results[1] is True


# =========================================================================
# Part B — route_and_execute wait-retry
# =========================================================================


class TestRouteAndExecuteWaitRetry:
    """route_and_execute retries when all providers at capacity."""

    def test_waits_and_dispatches_after_slot_frees(self):
        """Concurrent call sees all providers busy, waits, then dispatches."""
        p1 = Provider.LOCALHOST_LLM.value
        p2 = Provider.NETWORK_MODEL_MACHINE.value

        registry = _build_registry(
            probe_map={p1: _probe(p1), p2: _probe(p2)},
            execute_map={p1: _result(p1), p2: _result(p2)},
        )
        gate = ProviderExecutionGate()

        # Occupy both providers.
        gate.acquire(p1)
        gate.acquire(p2)

        results = [None]

        def call_route():
            req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
            results[0] = route_and_execute(req, registry=registry, gate=gate)

        t = threading.Thread(target=call_route)
        t.start()
        time.sleep(0.1)  # Let route_and_execute see both busy
        gate.release(p1)  # Free up localhost
        t.join(timeout=5)

        result, trace = results[0]
        assert result.success is True
        assert trace.selected_provider == p1

        # Verify wait-retry trace entries.
        actions = [d["action"] for d in trace.route_decision_log if "action" in d]
        assert "waiting_for_capacity" in actions

        gate.release(p2)  # Cleanup

    def test_all_providers_busy_exhausts_wait_attempts(self):
        """If providers never free up, exhaust max wait attempts."""
        p1 = Provider.LOCALHOST_LLM.value

        registry = _build_registry(
            probe_map={p1: _probe(p1)},
            execute_map={p1: _result(p1)},
        )
        gate = ProviderExecutionGate()
        gate.acquire(p1)

        req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
        # Patch wait_for_any_capacity to always return False (simulate timeout).
        with patch.object(gate, "wait_for_any_capacity", return_value=False):
            result, trace = route_and_execute(req, registry=registry, gate=gate)

        # Should have failed to dispatch.
        assert result is None or not result.success
        actions = [d.get("action") for d in trace.route_decision_log]

        # Should see wait_timeout or all_providers_busy.
        assert any(
            a in ("wait_timeout", "all_providers_busy") for a in actions
        ), f"Expected wait_timeout or all_providers_busy, got: {actions}"

        gate.release(p1)

    def test_no_wait_when_provider_available(self):
        """If a provider is immediately available, no wait is needed."""
        p1 = Provider.LOCALHOST_LLM.value
        p2 = Provider.NETWORK_MODEL_MACHINE.value

        registry = _build_registry(
            probe_map={p1: _probe(p1), p2: _probe(p2)},
        )
        gate = ProviderExecutionGate()

        with patch("app.services.model_router_policy.get_registry", return_value=registry), \
             patch("app.services.model_router_policy.get_execution_gate", return_value=gate):
            req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
            result, trace = route_and_execute(req, registry=registry, gate=gate)

        assert result.success is True
        actions = [d.get("action") for d in trace.route_decision_log]
        assert "waiting_for_capacity" not in actions


# =========================================================================
# Part C — Concurrent dispatch routes to different providers
# =========================================================================


class TestConcurrentDispatchRouting:
    """Concurrent calls are routed to different providers by the gate."""

    def test_two_concurrent_calls_use_different_providers(self):
        """Two overlapping calls should each get a different provider."""
        p1 = Provider.LOCALHOST_LLM.value
        p2 = Provider.NETWORK_MODEL_MACHINE.value

        call_events = {"p1_started": threading.Event(), "p1_done": threading.Event()}

        def slow_execute(request, *, timeout=None):
            """Simulate slow execution for p1."""
            call_events["p1_started"].set()
            call_events["p1_done"].wait(timeout=5)
            return _result(p1)

        registry = _build_registry(
            probe_map={p1: _probe(p1), p2: _probe(p2)},
            execute_map={p2: _result(p2)},
        )
        # Override p1 with slow execution.
        registry.get_provider(p1).execute.side_effect = slow_execute

        gate = ProviderExecutionGate()
        results = [None, None]

        def worker(idx):
            req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
            results[idx] = route_and_execute(req, registry=registry, gate=gate)

        # Start call 1 — will grab p1 and hold it.
        t1 = threading.Thread(target=worker, args=(0,))
        t1.start()
        call_events["p1_started"].wait(timeout=3)

        # Start call 2 — p1 is busy, should fall through to p2.
        t2 = threading.Thread(target=worker, args=(1,))
        t2.start()
        t2.join(timeout=5)

        # Release p1.
        call_events["p1_done"].set()
        t1.join(timeout=5)

        r1, t1_trace = results[0]
        r2, t2_trace = results[1]
        assert r1.success is True
        assert r2.success is True
        providers_used = {t1_trace.selected_provider, t2_trace.selected_provider}
        assert providers_used == {p1, p2}

        assert gate.in_flight_count(p1) == 0
        assert gate.in_flight_count(p2) == 0

    def test_three_concurrent_calls_third_waits(self):
        """With 2 providers (max_concurrency=1 each), third call waits."""
        p1 = Provider.LOCALHOST_LLM.value
        p2 = Provider.NETWORK_MODEL_MACHINE.value

        finished = threading.Event()
        started_count = {"n": 0}
        started_lock = threading.Lock()

        def slow_execute(provider_id):
            def _inner(request, *, timeout=None):
                with started_lock:
                    started_count["n"] += 1
                finished.wait(timeout=10)
                return _result(provider_id)
            return _inner

        registry = _build_registry(
            probe_map={p1: _probe(p1), p2: _probe(p2)},
        )
        registry.get_provider(p1).execute.side_effect = slow_execute(p1)
        registry.get_provider(p2).execute.side_effect = slow_execute(p2)

        gate = ProviderExecutionGate()
        results = [None, None, None]

        def worker(idx):
            req = _request(mode=ExecutionMode.LOCAL_DISTRIBUTED.value)
            results[idx] = route_and_execute(req, registry=registry, gate=gate)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]

        # Start workers 0 and 1 — stagger so they grab p1 and p2.
        threads[0].start()
        time.sleep(0.05)
        threads[1].start()
        time.sleep(0.15)  # Let both acquire their slots

        # Start worker 2 — both providers busy, should wait.
        threads[2].start()
        time.sleep(0.2)

        # Release all slow executions.
        finished.set()
        for t in threads:
            t.join(timeout=10)

        # All three should have succeeded.
        for i in range(3):
            assert results[i] is not None, f"Worker {i} returned None"
            r, trace = results[i]
            assert r.success is True, f"Worker {i} failed: {trace}"

        assert gate.in_flight_count(p1) == 0
        assert gate.in_flight_count(p2) == 0


# =========================================================================
# Part D — Gate release notifies waiters
# =========================================================================


class TestGateReleaseNotification:
    """Verify release() wakes threads via notify_all()."""

    def test_release_notifies_waiting_threads(self):
        gate = ProviderExecutionGate()
        pid = Provider.LOCALHOST_LLM.value
        gate.acquire(pid)

        woken = threading.Event()

        def waiter():
            gate.wait_for_any_capacity([pid], timeout=5.0)
            woken.set()

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        assert not woken.is_set()  # Still waiting
        gate.release(pid)
        t.join(timeout=2)
        assert woken.is_set()  # Woken by release

    def test_condition_variable_shared_with_lock(self):
        """Condition variable uses the same lock as acquire/release."""
        gate = ProviderExecutionGate()
        # Internal implementation detail: _capacity_available uses _lock.
        assert gate._capacity_available._lock is gate._lock
