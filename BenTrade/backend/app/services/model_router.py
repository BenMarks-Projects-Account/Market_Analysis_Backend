"""Model router — all LLM calls go through this module.

Provides both synchronous (``requests``) and async (``httpx``) call paths
so that existing sync callers (common/model_analysis.py, common/utils.py)
and async callers (routes_active_trades.py) both route through here.

Usage (sync — legacy path, unchanged):
    from app.services.model_router import model_request
    result = model_request(payload, timeout=180)

Usage (async — legacy path, unchanged):
    from app.services.model_router import async_model_request
    result = await async_model_request(http_client, payload, timeout=180.0)

Usage (new provider-abstraction path):
    from app.services.model_router import execute_with_provider
    provider_result = execute_with_provider(execution_request, provider_id)

Usage (distributed routing — Step 4):
    from app.services.model_router import route_and_execute
    result, trace = route_and_execute(execution_request)

Endpoint resolution:
    from app.services.model_router import get_model_endpoint
    url = get_model_endpoint()
"""

from __future__ import annotations

import logging
from typing import Any

import requests as _requests

from app.config import get_settings
from app.model_sources import MODEL_SOURCES

logger = logging.getLogger("bentrade.model_router")

_settings = get_settings()
MODEL_TIMEOUT_SECONDS = _settings.MODEL_TIMEOUT_SECONDS


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


def model_request(payload: dict[str, Any], *, timeout: float = MODEL_TIMEOUT_SECONDS, retries: int = 0) -> dict[str, Any]:
    """Synchronous model call (for common/model_analysis.py and common/utils.py).

    Replaces direct ``requests.post(model_url, ...)`` calls.
    Always forces ``stream: false`` to prevent LM Studio from returning
    an SSE event-stream that the requests library cannot consume.
    """
    endpoint = get_model_endpoint()
    # Force non-streaming so LM Studio returns a single JSON response
    payload = {**payload, "stream": False}
    last_exc: Exception | None = None

    for attempt in range(1 + retries):
        try:
            logger.info("[model_router] POST %s (attempt %d/%d, timeout=%ds)", endpoint, attempt + 1, 1 + retries, timeout)
            response = _requests.post(endpoint, json=payload, timeout=timeout)
            logger.info(
                "[model_router] response HTTP %d (%d bytes, %.1fs)",
                response.status_code, len(response.content), response.elapsed.total_seconds(),
            )
            response.raise_for_status()
            data = response.json()
            # Log completion status from OpenAI-compatible response
            finish = None
            choices = data.get("choices") or []
            if choices and isinstance(choices[0], dict):
                finish = choices[0].get("finish_reason")
            logger.info("[model_router] response OK — finish_reason=%s", finish)
            return data
        except _requests.ReadTimeout as exc:
            last_exc = exc
            logger.error(
                "[model_router] attempt %d TIMED OUT after %ds — endpoint=%s",
                attempt + 1, timeout, endpoint,
            )
        except _requests.RequestException as exc:
            last_exc = exc
            logger.warning("[model_router] attempt %d failed: %s", attempt + 1, exc)

    raise last_exc  # type: ignore[misc]


async def async_model_request(
    http_client: Any,
    payload: dict[str, Any],
    *,
    timeout: float = MODEL_TIMEOUT_SECONDS,
) -> Any:
    """Async model call (for route handlers using httpx.AsyncClient).

    Returns the raw httpx.Response so callers can check status_code.
    Always forces ``stream: false`` to prevent LM Studio streaming.
    """
    endpoint = get_model_endpoint()
    # Force non-streaming
    payload = {**payload, "stream": False}
    logger.info("[model_router] async POST %s (timeout=%.0fs)", endpoint, timeout)
    resp = await http_client.post(endpoint, json=payload, timeout=timeout)
    logger.info(
        "[model_router] async response HTTP %d (%d bytes)",
        resp.status_code, len(resp.content),
    )
    return resp


# ---------------------------------------------------------------------------
# Provider-abstraction execution seam (Step 2)
# ---------------------------------------------------------------------------

def execute_with_provider(
    request: "ExecutionRequest",
    provider_id: str,
    *,
    timeout: float | None = None,
) -> "ProviderResult":
    """Execute *request* through a single named provider adapter.

    This is the new abstraction-layer entry point.  It does NOT implement
    multi-provider fallback — that is the router policy's job (Step 4).

    Returns a ``ProviderResult`` regardless of success/failure so the
    caller always gets structured trace data.
    """
    from app.services.model_provider_base import ProviderResult
    from app.services.model_provider_registry import get_registry
    from app.services.model_routing_contract import (
        ExecutionRequest,
        ExecutionStatus,
        ProviderState,
    )

    registry = get_registry()
    adapter = registry.get_provider(provider_id)

    if adapter is None:
        logger.warning("[model_router] provider '%s' not registered", provider_id)
        return ProviderResult(
            provider=provider_id,
            success=False,
            execution_status=ExecutionStatus.FAILED.value,
            error_code="unknown_provider",
            error_message=f"No adapter registered for provider '{provider_id}'",
            provider_state_observed=ProviderState.UNAVAILABLE.value,
        )

    if not adapter.is_configured:
        logger.info("[model_router] provider '%s' is not configured — skipping", provider_id)
        return ProviderResult(
            provider=provider_id,
            success=False,
            execution_status=ExecutionStatus.SKIPPED.value,
            error_code="not_configured",
            error_message=f"Provider '{provider_id}' is not configured",
            provider_state_observed=ProviderState.UNAVAILABLE.value,
        )

    return adapter.execute(request, timeout=timeout)


# ---------------------------------------------------------------------------
# Distributed routing entry point (Step 4)
# ---------------------------------------------------------------------------

def route_and_execute(
    request: "ExecutionRequest",
    *,
    registry: Any = None,
    gate: Any = None,
    timeout: float | None = None,
) -> tuple["ProviderResult | None", "ExecutionTrace"]:
    """Route *request* through the policy engine and execute.

    Delegates to ``model_router_policy.route_and_execute()`` which
    handles provider selection, probing, gating, and fallback.

    For direct modes (local, model_machine, premium_online):
        Single-provider dispatch with honest results.

    For distributed modes (local_distributed, online_distributed):
        Multi-provider fallback with execution gating.

    Returns (ProviderResult | None, ExecutionTrace).
    """
    from app.services.model_router_policy import (
        route_and_execute as _policy_route_and_execute,
    )

    return _policy_route_and_execute(
        request, registry=registry, gate=gate, timeout=timeout,
    )
