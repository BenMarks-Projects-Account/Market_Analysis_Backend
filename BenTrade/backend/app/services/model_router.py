"""Model router — all LLM calls go through this module.

Provides both synchronous (``requests``) and async (``httpx``) call paths
so that existing sync callers (common/model_analysis.py, common/utils.py)
and async callers (routes_active_trades.py) both route through here.

Usage (sync):
    from app.services.model_router import model_request
    result = model_request(payload, timeout=120)

Usage (async):
    from app.services.model_router import async_model_request
    result = await async_model_request(http_client, payload, timeout=120.0)

Endpoint resolution:
    from app.services.model_router import get_model_endpoint
    url = get_model_endpoint()
"""

from __future__ import annotations

import logging
from typing import Any

import requests as _requests

from app.model_sources import MODEL_SOURCES

logger = logging.getLogger("bentrade.model_router")


def get_model_endpoint() -> str:
    """Return the active model endpoint URL.

    Reads the current source from model_state at call time so switching
    source takes effect immediately.
    """
    from app.services.model_state import get_model_source

    source_key = get_model_source()
    source = MODEL_SOURCES.get(source_key)
    if not source or not source.get("enabled"):
        raise RuntimeError(f"Model source '{source_key}' is not available or not enabled")
    endpoint = source.get("endpoint")
    if not endpoint:
        raise RuntimeError(f"Model source '{source_key}' has no endpoint configured")
    return endpoint


def model_request(payload: dict[str, Any], *, timeout: int = 120, retries: int = 0) -> dict[str, Any]:
    """Synchronous model call (for common/model_analysis.py and common/utils.py).

    Replaces direct ``requests.post(model_url, ...)`` calls.
    """
    endpoint = get_model_endpoint()
    last_exc: Exception | None = None

    for attempt in range(1 + retries):
        try:
            logger.info("[model_router] POST %s (attempt %d/%d)", endpoint, attempt + 1, 1 + retries)
            response = _requests.post(endpoint, json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except _requests.RequestException as exc:
            last_exc = exc
            logger.warning("[model_router] attempt %d failed: %s", attempt + 1, exc)

    raise last_exc  # type: ignore[misc]


async def async_model_request(
    http_client: Any,
    payload: dict[str, Any],
    *,
    timeout: float = 120.0,
) -> Any:
    """Async model call (for route handlers using httpx.AsyncClient).

    Returns the raw httpx.Response so callers can check status_code.
    """
    endpoint = get_model_endpoint()
    logger.info("[model_router] async POST %s", endpoint)
    return await http_client.post(endpoint, json=payload, timeout=timeout)
