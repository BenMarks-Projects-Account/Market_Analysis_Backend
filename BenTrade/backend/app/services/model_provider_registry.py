"""Provider registry — maps Provider enum values to adapter instances.

Responsibilities:
    • ``get_provider(provider_id)``   → adapter or None
    • ``list_registered()``           → all known provider IDs
    • ``get_provider_status(id)``     → live or cached status snapshot
    • ``probe_provider(id)``          → full ProbeResult from the adapter
    • Lazy singleton creation — adapters are instantiated once on first access.

The registry is the single place the router looks up a concrete adapter.
It never embeds provider-specific logic — that stays in the adapters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.services.model_provider_base import ModelProviderBase, ProbeResult
from app.services.model_routing_contract import Provider, ProviderState

logger = logging.getLogger("bentrade.provider_registry")


# ---------------------------------------------------------------------------
# Status snapshot returned by get_provider_status
# ---------------------------------------------------------------------------

@dataclass
class ProviderStatusSnapshot:
    """Status view of a registered provider.

    Extended in Step 3 to carry probe diagnostics alongside the summary fields.
    Extended in Step 16 with probe_type and checked_at for dashboard accuracy.
    """
    provider_id: str
    registered: bool
    configured: bool
    state: str  # ProviderState value
    probe_success: bool = True
    status_reason: str = ""
    timing_ms: float | None = None
    probe_type: str = "live"  # "live" | "config_only" | "cached"
    checked_at: str | None = None  # ISO timestamp


# ---------------------------------------------------------------------------
# Registry implementation
# ---------------------------------------------------------------------------

class ProviderRegistry:
    """Holds provider adapter singletons and exposes lookup helpers."""

    def __init__(self) -> None:
        self._adapters: dict[str, ModelProviderBase] = {}

    # ── Registration ────────────────────────────────────────────

    def register(self, adapter: ModelProviderBase) -> None:
        """Register an adapter instance, keyed by its ``provider_id``."""
        pid = adapter.provider_id
        if pid in self._adapters:
            logger.warning("Provider '%s' re-registered — replacing existing adapter", pid)
        self._adapters[pid] = adapter
        logger.debug("Provider '%s' registered (configured=%s)", pid, adapter.is_configured)

    # ── Lookup ──────────────────────────────────────────────────

    def get_provider(self, provider_id: str) -> ModelProviderBase | None:
        """Return the adapter for *provider_id*, or None if not registered."""
        return self._adapters.get(provider_id)

    def list_registered(self) -> list[str]:
        """Return sorted list of registered provider IDs."""
        return sorted(self._adapters)

    def get_provider_status(self, provider_id: str, *, refresh: bool = False) -> ProviderStatusSnapshot:
        """Return a status snapshot for *provider_id*.

        If *refresh* is True, performs a live probe via the adapter's
        ``probe()`` method.  Otherwise returns a lightweight snapshot
        based on configuration and the adapter's default probe.

        Works for both registered and unknown providers.
        """
        adapter = self._adapters.get(provider_id)
        if adapter is None:
            return ProviderStatusSnapshot(
                provider_id=provider_id,
                registered=False,
                configured=False,
                state=ProviderState.UNAVAILABLE.value,
                status_reason="not registered",
            )

        if not adapter.is_configured:
            return ProviderStatusSnapshot(
                provider_id=provider_id,
                registered=True,
                configured=False,
                state=ProviderState.UNAVAILABLE.value,
                status_reason="not configured",
            )

        if refresh:
            probe = adapter.probe()
            return ProviderStatusSnapshot(
                provider_id=provider_id,
                registered=True,
                configured=probe.configured,
                state=probe.state,
                probe_success=probe.probe_success,
                status_reason=probe.status_reason,
                timing_ms=probe.timing_ms,
                probe_type=probe.metadata.get("probe_type", "live"),
                checked_at=probe.checked_at,
            )

        # Non-refresh: delegate to probe_state() which may or may not
        # do a real probe depending on the adapter implementation.
        return ProviderStatusSnapshot(
            provider_id=provider_id,
            registered=True,
            configured=True,
            state=adapter.probe_state(),
            probe_type="cached",
        )

    def probe_provider(self, provider_id: str) -> ProbeResult:
        """Perform a full live probe for *provider_id*.

        Returns a ``ProbeResult`` directly.  For unknown or unconfigured
        providers, returns a synthetic ProbeResult with appropriate state.
        """
        adapter = self._adapters.get(provider_id)
        if adapter is None:
            return ProbeResult(
                provider=provider_id,
                configured=False,
                state=ProviderState.UNAVAILABLE.value,
                probe_success=True,
                status_reason="not registered in provider registry",
            )
        return adapter.probe()

    def all_statuses(self, *, refresh: bool = False) -> list[ProviderStatusSnapshot]:
        """Return status snapshots for every registered provider.

        If *refresh* is True, each provider is live-probed.
        """
        return [self.get_provider_status(pid, refresh=refresh) for pid in self._adapters]


# ---------------------------------------------------------------------------
# Module-level singleton — populated with default adapters on first import
# ---------------------------------------------------------------------------

_registry: ProviderRegistry | None = None


def _build_default_registry() -> ProviderRegistry:
    """Create and populate the default registry with known adapters."""
    from app.services.model_provider_adapters import (
        BedrockTitanNovaProProvider,
        LocalhostLLMProvider,
        NetworkModelMachineProvider,
    )

    reg = ProviderRegistry()
    reg.register(LocalhostLLMProvider())
    reg.register(NetworkModelMachineProvider())
    reg.register(BedrockTitanNovaProProvider())
    return reg


def get_registry() -> ProviderRegistry:
    """Return the module-level provider registry (lazy-init)."""
    global _registry
    if _registry is None:
        _registry = _build_default_registry()
    return _registry


def reset_registry() -> None:
    """Reset the global registry — primarily for testing."""
    global _registry
    _registry = None
