"""Model endpoint health check with 30-second cache.

Probes the **currently active** model endpoint (Local, Model Machine,
or Premium Online) via ``GET /v1/models`` to verify the server is
reachable and has at least one model loaded.  The active source is
read from ``model_state`` at each call, so switching sources in the
Data Health dashboard immediately re-probes the new endpoint.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests as _requests

from app.model_sources import MODEL_SOURCES

logger = logging.getLogger("bentrade.model_health")

_CACHE_TTL_S = 30
_PROBE_TIMEOUT_S = 3

_cached_result: dict[str, Any] | None = None
_cached_at: float = 0.0
_cached_source_key: str | None = None


def _get_models_url() -> tuple[str, str, str]:
    """Return (models_url, source_name, source_key) for the currently active model source."""
    from app.services.model_state import get_model_source

    source_key = get_model_source()
    source = MODEL_SOURCES.get(source_key, {})
    endpoint = source.get("endpoint") or ""
    name = source.get("name") or source_key

    if not endpoint:
        return "", name, source_key

    # Derive /v1/models from the chat completions endpoint
    # e.g. http://host:1234/v1/chat/completions → http://host:1234/v1/models
    base = endpoint.rsplit("/v1/", 1)[0] if "/v1/" in endpoint else endpoint.rstrip("/")
    return f"{base}/v1/models", name, source_key


def check_model_health(*, force: bool = False) -> dict[str, Any]:
    """Probe the active model endpoint. Results are cached for 30 seconds.

    The cache is automatically invalidated when the active model source
    changes (e.g. switching from Local to Model Machine in the UI).

    Returns::

        {
            "status": "healthy" | "unhealthy",
            "latency_ms": int,
            "models_loaded": ["model-name", ...],
            "endpoint": "http://...",
            "source_name": "Local" | "Model Machine" | ...,
            "error": "..." | None,
        }
    """
    global _cached_result, _cached_at, _cached_source_key

    url, source_name, source_key = _get_models_url()

    # Invalidate cache when the active source changed
    source_changed = (source_key != _cached_source_key)

    now = time.monotonic()
    if (
        not force
        and not source_changed
        and _cached_result is not None
        and (now - _cached_at) < _CACHE_TTL_S
    ):
        return _cached_result
    result: dict[str, Any] = {
        "status": "unhealthy",
        "latency_ms": 0,
        "models_loaded": [],
        "endpoint": url or "(not configured)",
        "source_name": source_name,
        "error": None,
    }

    # Sources with no endpoint configured (e.g. Premium Online placeholder)
    if not url:
        result["error"] = "No endpoint configured"
        logger.info("[MODEL_HEALTH] source=%s status=unhealthy error=no_endpoint_configured", source_name)
        _cached_result = result
        _cached_at = time.monotonic()
        _cached_source_key = source_key
        return result

    try:
        t0 = time.perf_counter()
        resp = _requests.get(url, timeout=_PROBE_TIMEOUT_S)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        result["latency_ms"] = latency_ms

        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            logger.warning(
                "[MODEL_HEALTH] endpoint=%s status=unhealthy latency=%dms error=HTTP_%d",
                url, latency_ms, resp.status_code,
            )
        else:
            data = resp.json()
            models = data.get("data") or []
            model_ids = [m.get("id", "unknown") for m in models if isinstance(m, dict)]

            if model_ids:
                result["status"] = "healthy"
                result["models_loaded"] = model_ids
                logger.info(
                    "[MODEL_HEALTH] endpoint=%s status=healthy latency=%dms models=%s",
                    url, latency_ms, model_ids,
                )
            else:
                result["error"] = "No models loaded"
                logger.warning(
                    "[MODEL_HEALTH] endpoint=%s status=unhealthy latency=%dms error=no_models_loaded",
                    url, latency_ms,
                )
    except _requests.Timeout:
        result["error"] = "Connection timed out"
        logger.warning("[MODEL_HEALTH] endpoint=%s status=unhealthy error=timeout", url)
    except _requests.ConnectionError:
        result["error"] = "Connection refused"
        logger.warning("[MODEL_HEALTH] endpoint=%s status=unhealthy error=connection_refused", url)
    except Exception as exc:
        result["error"] = str(exc)
        logger.warning("[MODEL_HEALTH] endpoint=%s status=unhealthy error=%s", url, exc)

    _cached_result = result
    _cached_at = time.monotonic()
    _cached_source_key = source_key
    return result
