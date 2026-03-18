"""Provider execution gate — per-provider concurrency control.

Prevents multiple concurrent prompts from being sent to the same
provider endpoint.  This is an app-side mechanism that does NOT rely
on external probe signals — it is authoritative for dispatch gating.

Design:
    • Thread-safe (uses threading.Lock per provider).
    • Default max_concurrency = 1 for all providers (serialised dispatch).
    • acquire(provider) returns True if a slot is available, False otherwise.
    • release(provider) is guaranteed via ``finally`` in all call paths.
    • Context-manager support via ``reservation(provider)`` for clean usage.
    • Condition variable allows callers to wait until capacity opens up.

Usage:
    gate = get_execution_gate()

    # Option 1 — explicit acquire / release
    if gate.acquire(provider_id):
        try:
            result = adapter.execute(request)
        finally:
            gate.release(provider_id)

    # Option 2 — context manager
    with gate.reservation(provider_id) as acquired:
        if acquired:
            result = adapter.execute(request)

    # Option 3 — wait for any provider to have capacity
    gate.wait_for_any_capacity(["localhost_llm", "network_model_machine"], timeout=60)
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator

from app.services.model_routing_contract import Provider

logger = logging.getLogger("bentrade.execution_gate")


# ---------------------------------------------------------------------------
# Default concurrency limits
# ---------------------------------------------------------------------------

DEFAULT_MAX_CONCURRENCY: dict[str, int] = {
    Provider.LOCALHOST_LLM.value: 1,
    Provider.NETWORK_MODEL_MACHINE.value: 1,
    Provider.BEDROCK_TITAN_NOVA_PRO.value: 1,
}


# ---------------------------------------------------------------------------
# Gate snapshot — for inspection / tracing
# ---------------------------------------------------------------------------

@dataclass
class GateSnapshot:
    """Point-in-time snapshot of a single provider's gate state."""
    provider_id: str
    in_flight: int
    max_concurrency: int
    has_capacity: bool


# ---------------------------------------------------------------------------
# Execution gate implementation
# ---------------------------------------------------------------------------

class ProviderExecutionGate:
    """Thread-safe per-provider in-flight tracking and dispatch gating.

    Each provider has a max_concurrency limit.  ``acquire()`` atomically
    checks capacity and increments the in-flight counter.  ``release()``
    decrements it.  The gate is the authoritative source of truth for
    whether a provider slot is available — probe signals are supplemental.
    """

    def __init__(
        self,
        max_concurrency: dict[str, int] | None = None,
        *,
        default_max_concurrency: int = 1,
        config_source: str = "explicit",
    ) -> None:
        self._max_concurrency: dict[str, int] = dict(
            max_concurrency or DEFAULT_MAX_CONCURRENCY
        )
        self._default_max_concurrency: int = max(1, default_max_concurrency)
        self._config_source: str = config_source
        self._in_flight: dict[str, int] = {}
        self._lock = threading.Lock()
        # Condition variable for blocking wait when all providers busy.
        self._capacity_available = threading.Condition(self._lock)

    @classmethod
    def from_config(cls, config: Any = None) -> ProviderExecutionGate:
        """Build a gate from a ``RoutingConfig`` instance.

        If *config* is None, falls back to ``get_routing_config()``.
        If the config module is unavailable, uses hardcoded defaults.
        """
        if config is None:
            try:
                from app.services.model_routing_config import get_routing_config
                config = get_routing_config()
            except Exception:
                logger.warning(
                    "[gate] routing config unavailable, using hardcoded defaults"
                )
                return cls()

        return cls(
            max_concurrency=dict(getattr(config, "provider_concurrency", {})
                                 or DEFAULT_MAX_CONCURRENCY),
            default_max_concurrency=getattr(
                config, "default_max_concurrency", 1
            ),
            config_source=getattr(config, "config_source", "routing_config"),
        )

    # ── Concurrency configuration ──────────────────────────────

    def get_max_concurrency(self, provider_id: str) -> int:
        """Return the max concurrent slots for *provider_id*.

        Falls back to ``_default_max_concurrency`` (from config or 1)
        if not explicitly configured.
        """
        return self._max_concurrency.get(
            provider_id, self._default_max_concurrency
        )

    def set_max_concurrency(self, provider_id: str, limit: int) -> None:
        """Override max_concurrency for *provider_id*."""
        if limit < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {limit}")
        with self._lock:
            self._max_concurrency[provider_id] = limit

    # ── Acquire / release ──────────────────────────────────────

    def acquire(self, provider_id: str) -> bool:
        """Try to reserve a slot for *provider_id*.

        Returns True if a slot was acquired, False if at capacity.
        Thread-safe: uses a lock around the check-and-increment.
        """
        with self._lock:
            current = self._in_flight.get(provider_id, 0)
            limit = self.get_max_concurrency(provider_id)
            if current >= limit:
                logger.info(
                    "[gate] %s — at capacity (%d/%d), slot denied",
                    provider_id, current, limit,
                )
                return False
            self._in_flight[provider_id] = current + 1
            logger.debug(
                "[gate] %s — slot acquired (%d/%d)",
                provider_id, current + 1, limit,
            )
            return True

    def release(self, provider_id: str) -> None:
        """Release a previously acquired slot for *provider_id*.

        Clamps to zero to prevent negative counts if release is called
        without a matching acquire (defensive).  Notifies any threads
        waiting for capacity via wait_for_any_capacity().
        """
        with self._lock:
            current = self._in_flight.get(provider_id, 0)
            if current <= 0:
                logger.warning(
                    "[gate] %s — release called with in_flight=%d (no-op)",
                    provider_id, current,
                )
                return
            self._in_flight[provider_id] = current - 1
            logger.debug(
                "[gate] %s — slot released (%d/%d)",
                provider_id, current - 1,
                self.get_max_concurrency(provider_id),
            )
            # Wake up any threads waiting for capacity.
            self._capacity_available.notify_all()

    # ── Context manager ────────────────────────────────────────

    @contextmanager
    def reservation(self, provider_id: str) -> Generator[bool, None, None]:
        """Context manager that acquires a slot, yields success, and
        guarantees release.

        Usage:
            with gate.reservation(pid) as acquired:
                if acquired:
                    ...  # do work
        """
        acquired = self.acquire(provider_id)
        try:
            yield acquired
        finally:
            if acquired:
                self.release(provider_id)

    # ── Blocking wait ──────────────────────────────────────────

    def wait_for_any_capacity(
        self,
        provider_ids: list[str],
        timeout: float = 60.0,
    ) -> bool:
        """Block until at least one of *provider_ids* has capacity.

        Returns True if capacity became available, False on timeout.
        Uses a condition variable — threads are woken by release().
        """
        with self._capacity_available:
            def _any_has_capacity() -> bool:
                for pid in provider_ids:
                    current = self._in_flight.get(pid, 0)
                    limit = self._max_concurrency.get(
                        pid, self._default_max_concurrency
                    )
                    if current < limit:
                        return True
                return False

            if _any_has_capacity():
                return True

            logger.info(
                "[gate] all providers busy (%s), waiting up to %.1fs for capacity",
                ", ".join(provider_ids), timeout,
            )
            return self._capacity_available.wait_for(
                _any_has_capacity, timeout=timeout,
            )

    # ── Inspection ─────────────────────────────────────────────

    def in_flight_count(self, provider_id: str) -> int:
        """Return the current in-flight count for *provider_id*."""
        with self._lock:
            return self._in_flight.get(provider_id, 0)

    def has_capacity(self, provider_id: str) -> bool:
        """Return True if *provider_id* has at least one free slot."""
        with self._lock:
            current = self._in_flight.get(provider_id, 0)
            return current < self.get_max_concurrency(provider_id)

    def snapshot(self, provider_id: str) -> GateSnapshot:
        """Return a point-in-time snapshot for tracing / logging."""
        with self._lock:
            current = self._in_flight.get(provider_id, 0)
            limit = self.get_max_concurrency(provider_id)
            return GateSnapshot(
                provider_id=provider_id,
                in_flight=current,
                max_concurrency=limit,
                has_capacity=current < limit,
            )

    def all_snapshots(self) -> dict[str, GateSnapshot]:
        """Return snapshots for all known providers."""
        with self._lock:
            all_ids = set(self._max_concurrency) | set(self._in_flight)
            return {
                pid: GateSnapshot(
                    provider_id=pid,
                    in_flight=self._in_flight.get(pid, 0),
                    max_concurrency=self._max_concurrency.get(pid, 1),
                    has_capacity=(
                        self._in_flight.get(pid, 0)
                        < self._max_concurrency.get(pid, 1)
                    ),
                )
                for pid in sorted(all_ids)
            }

    def reset(self) -> None:
        """Clear all in-flight counts.  For testing only."""
        with self._lock:
            self._in_flight.clear()

    # ── Observability ──────────────────────────────────────────

    def effective_config_summary(self) -> dict[str, Any]:
        """Return a safe-to-log summary of effective gate configuration."""
        with self._lock:
            all_ids = set(self._max_concurrency) | set(self._in_flight)
            return {
                "config_source": self._config_source,
                "default_max_concurrency": self._default_max_concurrency,
                "provider_limits": dict(self._max_concurrency),
                "in_flight": {
                    pid: self._in_flight.get(pid, 0)
                    for pid in sorted(all_ids)
                },
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_gate: ProviderExecutionGate | None = None


def get_execution_gate() -> ProviderExecutionGate:
    """Return the module-level execution gate (lazy-init from config)."""
    global _gate
    if _gate is None:
        _gate = ProviderExecutionGate.from_config()
    return _gate


def reset_execution_gate() -> None:
    """Reset the global gate — primarily for testing."""
    global _gate
    _gate = None
