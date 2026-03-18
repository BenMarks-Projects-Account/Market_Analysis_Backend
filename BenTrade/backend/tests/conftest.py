"""Shared test fixtures for the BenTrade backend test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_routing_rotation():
    """Reset the round-robin rotation counter before each test.

    The routing policy uses a module-level counter to rotate candidates
    across sequential requests.  Without resetting, test ordering would
    affect which provider is selected first, making tests flaky.
    """
    from app.services.model_router_policy import reset_rotation_counter
    reset_rotation_counter()
    yield
    reset_rotation_counter()
