"""Provider abstraction — base protocol, result contract, and legacy bridge.

This module defines:
    • ``ModelProviderBase``  — the interface every provider adapter implements.
    • ``ProviderResult``     — normalized per-provider execution result.
    • Legacy ↔ new vocabulary bridges for the transition period.

Design:
    Router → picks a Provider → calls adapter.execute(request) → gets ProviderResult
    The router never touches HTTP, endpoint URLs, or provider-specific parsing.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.model_sources import MODEL_SOURCES
from app.services.model_routing_contract import (
    ExecutionRequest,
    ExecutionStatus,
    Provider,
    ProviderState,
)


# ---------------------------------------------------------------------------
# 1. Normalized provider result
# ---------------------------------------------------------------------------

@dataclass
class ProviderResult:
    """Output of a single provider adapter execution.

    Fields:
        provider           – Provider enum value that produced this result.
        success            – True if the call completed and returned valid data.
        execution_status   – ExecutionStatus value.
        raw_response       – Unmodified provider response (dict, httpx.Response, etc.).
        content            – Extracted text/content for convenience (may be None).
        error_code         – Short machine-readable error tag (None on success).
        error_message      – Human-readable error description (None on success).
        timing_ms          – Wall-clock time for this single provider call.
        provider_state_observed – Provider health observed *during* the call.
        degraded           – True if the provider responded but quality may be reduced.
        request_id         – Unique id for this provider invocation.
        metadata           – Arbitrary extra data from the adapter.
    """
    provider: str
    success: bool
    execution_status: str = ExecutionStatus.NOT_ATTEMPTED.value
    raw_response: Any = None
    content: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    timing_ms: float | None = None
    provider_state_observed: str = ProviderState.AVAILABLE.value
    degraded: bool = False
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1b. Probe result — structured output from provider health checks
# ---------------------------------------------------------------------------

# Default probe timeout: short and bounded to avoid blocking callers.
PROBE_TIMEOUT_SECONDS: float = 3.0

@dataclass
class ProbeResult:
    """Structured output from a single provider health probe.

    Fields:
        provider           – Provider enum value probed.
        configured         – Whether the provider has enough config to attempt calls.
        state              – Observed ProviderState value.
        probe_success      – True if the probe itself completed without exception.
        status_reason      – Short diagnostic string explaining the state.
        raw_probe_data     – Any raw data returned by the probe endpoint.
        checked_at         – ISO timestamp of when the probe was performed.
        timing_ms          – Wall-clock time for the probe in milliseconds.
        metadata           – Arbitrary extra probe data.
    """
    provider: str
    configured: bool
    state: str = ProviderState.UNAVAILABLE.value
    probe_success: bool = False
    status_reason: str = ""
    raw_probe_data: Any = None
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    timing_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 2. Provider adapter base class
# ---------------------------------------------------------------------------

class ModelProviderBase(ABC):
    """Abstract base for all provider adapters.

    Subclasses must implement at minimum ``provider_id`` and ``execute``.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Return the ``Provider`` enum value this adapter handles."""

    @abstractmethod
    def execute(self, request: ExecutionRequest, *, timeout: float | None = None) -> ProviderResult:
        """Synchronous single-provider execution."""

    async def async_execute(self, request: ExecutionRequest, *, timeout: float | None = None,
                            http_client: Any = None) -> ProviderResult:
        """Async execution — default delegates to sync ``execute``.

        Providers that have native async transports should override this.
        """
        return self.execute(request, timeout=timeout)

    def probe_state(self) -> str:
        """Return current ``ProviderState`` value.

        Default implementation delegates to ``probe()`` for the full result
        and returns only the state string.  Subclasses that override
        ``probe()`` get this for free.
        """
        return self.probe().state

    def probe(self) -> ProbeResult:
        """Perform a health probe and return structured ``ProbeResult``.

        Default: returns available if configured, unavailable otherwise.
        Concrete providers should override with real health-check logic.
        """
        configured = self.is_configured
        if configured:
            return ProbeResult(
                provider=self.provider_id,
                configured=True,
                state=ProviderState.AVAILABLE.value,
                probe_success=True,
                status_reason="configured (no real probe implemented)",
            )
        return ProbeResult(
            provider=self.provider_id,
            configured=False,
            state=ProviderState.UNAVAILABLE.value,
            probe_success=True,
            status_reason="not configured",
        )

    @property
    def is_configured(self) -> bool:
        """Return True if this provider has enough configuration to attempt calls.

        Default: True.  Override for providers that need credentials/endpoints.
        """
        return True

    def supports_model(self, model_name: str | None) -> bool:
        """Return True if *model_name* is compatible with this provider.

        Default: True (accept anything).  Override to restrict.
        """
        return True


# ---------------------------------------------------------------------------
# 3. Legacy ↔ new vocabulary bridge
# ---------------------------------------------------------------------------

# Old source keys (model_sources.py) → new Provider enum values
_LEGACY_SOURCE_TO_PROVIDER: dict[str, str] = {
    "local": Provider.LOCALHOST_LLM.value,
    "model_machine": Provider.NETWORK_MODEL_MACHINE.value,
    "premium_online": Provider.BEDROCK_TITAN_NOVA_PRO.value,
}

# Reverse: Provider enum value → legacy source key
_PROVIDER_TO_LEGACY_SOURCE: dict[str, str] = {
    v: k for k, v in _LEGACY_SOURCE_TO_PROVIDER.items()
}


def legacy_source_to_provider(source_key: str) -> str | None:
    """Map an old model_sources.py key to a Provider enum value.

    Returns None for unknown keys.
    """
    return _LEGACY_SOURCE_TO_PROVIDER.get(source_key)


def provider_to_legacy_source(provider: str) -> str | None:
    """Map a Provider enum value back to an old model_sources.py key.

    Returns None for providers with no legacy equivalent.
    """
    return _PROVIDER_TO_LEGACY_SOURCE.get(provider)


def get_provider_endpoint(provider: str) -> str | None:
    """Resolve the HTTP endpoint for *provider* via MODEL_SOURCES.

    Uses the legacy bridge to look up the endpoint in model_sources.py.
    Returns None if no endpoint is configured.
    """
    source_key = provider_to_legacy_source(provider)
    if source_key is None:
        return None
    source = MODEL_SOURCES.get(source_key)
    if source is None:
        return None
    return source.get("endpoint")


def is_legacy_source_enabled(source_key: str) -> bool:
    """Return True if the legacy source is present and enabled."""
    source = MODEL_SOURCES.get(source_key)
    return bool(source and source.get("enabled"))


# ---------------------------------------------------------------------------
# Helpers used by adapters
# ---------------------------------------------------------------------------

def extract_content_from_openai_response(data: dict[str, Any]) -> str | None:
    """Pull the assistant message content from an OpenAI-compatible response.

    Input fields: data["choices"][0]["message"]["content"]
    Returns None if the path is missing or malformed.
    """
    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    return message.get("content")
