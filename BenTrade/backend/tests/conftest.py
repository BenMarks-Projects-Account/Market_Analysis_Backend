"""Shared test fixtures for the BenTrade backend test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_routing_rotation():
    """Reset the round-robin rotation counter and circuit breaker before each test.

    The routing policy uses a module-level counter to rotate candidates
    across sequential requests.  Without resetting, test ordering would
    affect which provider is selected first, making tests flaky.

    The circuit breaker is also module-level — without resetting, failures
    from one test would cause providers to be skipped in subsequent tests.
    """
    from app.services.model_router_policy import get_circuit_breaker, reset_rotation_counter
    reset_rotation_counter()
    get_circuit_breaker().reset()
    yield
    reset_rotation_counter()
    get_circuit_breaker().reset()
