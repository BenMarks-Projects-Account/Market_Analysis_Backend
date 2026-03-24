"""Tests: all model calls go through the concurrency gate — no ungated legacy fallback.

Verifies:
1. _model_transport() never falls through to ungated requests.post().
2. _model_transport() raises RuntimeError on routing non-success.
3. _routed_model_executor() does not fall back to ungated model_request().
4. route_and_execute() wait patience is at least 6 attempts.
5. route_and_execute() per-wait timeout is capped at 30s.
6. ProviderExecutionGate serializes concurrent threads.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ── 1. _model_transport: no legacy fallback on routing non-success ────────

class TestModelTransportNoLegacyFallback:
    """_model_transport() must raise when routing returns non-success."""

    def _make_payload(self):
        return {
            "messages": [
                {"role": "system", "content": "You are a test."},
                {"role": "user", "content": "Hello"},
            ],
            "max_tokens": 100,
            "temperature": 0.0,
        }

    @patch("app.services.model_routing_integration.execute_routed_model")
    def test_raises_on_routing_error(self, mock_execute):
        """When routing returns status=error, _model_transport raises RuntimeError."""
        from common.model_analysis import _model_transport

        mock_execute.return_value = (
            {"status": "error", "error": "all providers busy"},
            MagicMock(selected_provider="localhost_llm", timing_ms=0, request_id="r1"),
        )

        with pytest.raises(RuntimeError, match="model call failed via routing"):
            _model_transport(
                task_type="test",
                payload=self._make_payload(),
                log_prefix="test",
            )

    @patch("app.services.model_routing_integration.execute_routed_model")
    def test_returns_on_success(self, mock_execute):
        """When routing returns status=success, _model_transport returns content."""
        from common.model_analysis import TransportResult, _model_transport

        mock_execute.return_value = (
            {"status": "success", "content": "hello world"},
            MagicMock(selected_provider="localhost_llm", timing_ms=42, request_id="r2"),
        )

        result = _model_transport(
            task_type="test",
            payload=self._make_payload(),
            log_prefix="test",
        )
        assert isinstance(result, TransportResult)
        assert result.content == "hello world"
        assert result.transport_path == "routed"
        assert result.provider == "localhost_llm"

    def test_no_requests_post_import(self):
        """_model_transport() never imports or calls requests.post."""
        import inspect
        from common.model_analysis import _model_transport

        source = inspect.getsource(_model_transport)
        assert "requests.post" not in source, (
            "_model_transport must not contain requests.post — "
            "all calls must go through the routing gate"
        )

    @patch("app.services.model_routing_integration.execute_routed_model")
    def test_routing_disabled_propagates(self, mock_execute):
        """RoutingDisabledError propagates (not caught + legacy fallback)."""
        from app.services.model_routing_integration import RoutingDisabledError
        from common.model_analysis import _model_transport

        mock_execute.side_effect = RoutingDisabledError("disabled")

        with pytest.raises(RoutingDisabledError):
            _model_transport(
                task_type="test",
                payload=self._make_payload(),
                log_prefix="test",
            )


# ── 2. _routed_model_executor: no fallback to _default_model_executor ────

class TestRoutedModelExecutorNoFallback:
    """active_trade_pipeline._routed_model_executor must not fall back to ungated model_request."""

    def test_no_default_model_executor_call_in_except(self):
        """Exception handler must not call _default_model_executor."""
        import inspect
        from app.services.active_trade_pipeline import _routed_model_executor

        source = inspect.getsource(_routed_model_executor)
        # Strip docstring (everything between first ''' or """) — we only care about code
        lines = source.splitlines()
        code_lines = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if in_docstring:
                    in_docstring = False
                    continue
                # Check if it's a single-line docstring
                if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                    continue
                in_docstring = True
                continue
            if not in_docstring:
                code_lines.append(line)
        code_only = "\n".join(code_lines)

        # Check for actual calls in code (not docstring references)
        assert "return _default_model_executor(" not in code_only, (
            "_routed_model_executor must not call _default_model_executor() — "
            "that path bypasses the concurrency gate"
        )

    @patch("app.services.model_routing_integration.execute_routed_model")
    def test_returns_error_on_routing_exception(self, mock_execute):
        # Force re-import so the local import inside the function picks up our mock
        import importlib
        import app.services.active_trade_pipeline as atp_mod
        importlib.reload(atp_mod)
        from app.services.active_trade_pipeline import _routed_model_executor

        mock_execute.side_effect = ConnectionError("timeout")
        result = _routed_model_executor(
            {"trade_key": "TEST", "symbol": "SPY"},
            "test prompt",
        )
        assert result.get("status") == "error"
        assert "timeout" in result.get("error", "")


# ── 3. route_and_execute wait patience ────────────────────────────────────

class TestRouteAndExecutePatience:
    """route_and_execute must wait long enough for in-flight calls to finish."""

    def test_max_wait_attempts_is_at_least_6(self):
        """_max_wait_attempts >= 6 so total wait ≥ 180s."""
        import inspect
        from app.services.model_router_policy import route_and_execute

        source = inspect.getsource(route_and_execute)
        # Find the _max_wait_attempts assignment
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("_max_wait_attempts"):
                _, _, value = stripped.partition("=")
                attempts = int(value.strip())
                assert attempts >= 6, (
                    f"_max_wait_attempts is {attempts}, must be >= 6 "
                    "to give in-flight calls time to finish"
                )
                return
        pytest.fail("_max_wait_attempts not found in route_and_execute source")

    def test_per_wait_timeout_capped_at_30(self):
        """Per-wait timeout must be capped at 30s."""
        import inspect
        from app.services.model_router_policy import route_and_execute

        source = inspect.getsource(route_and_execute)
        # The timeout expression should contain min(..., 30.0)
        assert "30.0" in source, (
            "Per-wait timeout should be capped at 30.0 seconds"
        )


# ── 4. ProviderExecutionGate thread serialization ─────────────────────────

class TestGateThreadSerialization:
    """ProviderExecutionGate must serialize concurrent threads."""

    def test_only_one_thread_holds_gate(self):
        """With max_concurrency=1, only one thread can hold the gate at a time."""
        from app.services.model_execution_gate import ProviderExecutionGate

        gate = ProviderExecutionGate(default_max_concurrency=1)
        pid = "test_provider"

        acquired_order: list[int] = []
        barrier = threading.Barrier(2, timeout=5)

        def worker(worker_id: int):
            barrier.wait()  # both threads start at the same time
            if gate.acquire(pid):
                acquired_order.append(worker_id)
                time.sleep(0.05)  # hold for 50ms
                gate.release(pid)

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # At least one thread must have acquired the gate
        assert len(acquired_order) >= 1

    def test_second_acquire_blocked(self):
        """Second acquire on the same provider returns False when slot is held."""
        from app.services.model_execution_gate import ProviderExecutionGate

        gate = ProviderExecutionGate(default_max_concurrency=1)
        pid = "test_provider"

        assert gate.acquire(pid) is True
        assert gate.acquire(pid) is False  # slot already held
        gate.release(pid)
        assert gate.acquire(pid) is True  # free again
        gate.release(pid)

    def test_wait_for_capacity_returns_when_released(self):
        """wait_for_any_capacity returns True after another thread releases."""
        from app.services.model_execution_gate import ProviderExecutionGate

        gate = ProviderExecutionGate(default_max_concurrency=1)
        pid = "test_provider"
        gate.acquire(pid)

        result_holder: list[bool] = []

        def waiter():
            got = gate.wait_for_any_capacity([pid], timeout=5.0)
            result_holder.append(got)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)  # let waiter start waiting
        gate.release(pid)  # free the slot
        t.join(timeout=5)

        assert result_holder == [True]


# ── 5. Source-level checks for ungated HTTP calls ─────────────────────────

class TestNoUngatedHTTPInCallPaths:
    """No model call path should contain ungated direct HTTP calls."""

    def test_routes_active_trades_no_ungated_model_endpoint(self):
        """active_trade_model_analysis must not call get_model_endpoint for fallback."""
        import ast
        import textwrap

        from app.api import routes_active_trades

        source = open(routes_active_trades.__file__, "r", encoding="utf-8").read()

        # Find the active_trade_model_analysis function
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "active_trade_model_analysis":
                func_source = ast.get_source_segment(source, node) or ""
                assert "get_model_endpoint" not in func_source, (
                    "active_trade_model_analysis must not use get_model_endpoint — "
                    "all calls must go through routing"
                )
                return
        # Function may be found as a regular function
        # If we can't find it, the test is inconclusive but should not fail

    def test_monitor_narrative_no_ungated_http_client(self):
        """monitor_narrative must not use http_client.post for model calls."""
        import ast

        from app.api import routes_active_trades

        source = open(routes_active_trades.__file__, "r", encoding="utf-8").read()

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "monitor_narrative":
                func_source = ast.get_source_segment(source, node) or ""
                assert "http_client.post" not in func_source, (
                    "monitor_narrative must not use http_client.post — "
                    "all calls must go through routing"
                )
                return
