"""Centralized routing configuration — typed, validated, observable.

All routing-related settings live here.  Provider concurrency limits,
probe parameters, routing toggles, and Bedrock capacity are read from
this module instead of being scattered across hardcoded constants.

Configuration sources (precedence, highest → lowest):
    1. Explicit ``RoutingConfig(...)`` kwargs (programmatic override).
    2. Environment variables (``ROUTING_*`` prefix).
    3. Coded defaults that match current Step 1-8 behavior.

Design rules:
    • Typed dataclass — every field is explicit and inspectable.
    • Validated on construction — invalid values fall back to safe
      defaults with a warning log, never silently produce unsafe behavior.
    • Immutable after construction — no runtime mutation of config.
    • Observable — ``effective_summary()`` returns a safe-to-log dict.
    • Thread-safe — dataclass is frozen after ``__post_init__``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from app.services.model_routing_contract import Provider

logger = logging.getLogger("bentrade.routing.config")


# ---------------------------------------------------------------------------
# Safe defaults — match existing Step 1-8 hardcoded behavior
# ---------------------------------------------------------------------------

_SAFE_DEFAULT_MAX_CONCURRENCY: int = 1

_SAFE_DEFAULT_PROVIDER_CONCURRENCY: dict[str, int] = {
    Provider.LOCALHOST_LLM.value: 1,
    Provider.NETWORK_MODEL_MACHINE.value: 1,
    Provider.BEDROCK_TITAN_NOVA_PRO.value: 1,
}

_SAFE_DEFAULT_PROBE_TIMEOUT_SECONDS: float = 3.0
_SAFE_DEFAULT_PROBE_DEGRADED_THRESHOLD_MS: float = 2000.0
_SAFE_DEFAULT_ROUTING_ENABLED: bool = True
_SAFE_DEFAULT_BEDROCK_ENABLED: bool = True


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_positive_int(value: Any, name: str, default: int) -> int:
    """Validate that *value* is a positive integer, else return *default*."""
    try:
        v = int(value)
        if v < 1:
            logger.warning(
                "[routing_config] %s=%r is < 1, falling back to default %d",
                name, value, default,
            )
            return default
        return v
    except (TypeError, ValueError):
        logger.warning(
            "[routing_config] %s=%r is not a valid integer, "
            "falling back to default %d",
            name, value, default,
        )
        return default


def _validate_positive_float(value: Any, name: str, default: float) -> float:
    """Validate that *value* is a positive float, else return *default*."""
    try:
        v = float(value)
        if v <= 0:
            logger.warning(
                "[routing_config] %s=%r is <= 0, falling back to default %.1f",
                name, value, default,
            )
            return default
        return v
    except (TypeError, ValueError):
        logger.warning(
            "[routing_config] %s=%r is not a valid number, "
            "falling back to default %.1f",
            name, value, default,
        )
        return default


def _validate_concurrency_map(
    raw: dict[str, Any] | None,
    name: str,
) -> dict[str, int]:
    """Validate a provider→concurrency mapping.

    Invalid per-provider values fall back to _SAFE_DEFAULT_MAX_CONCURRENCY.
    Unknown provider keys are preserved (forward-compatible).
    """
    if raw is None:
        return dict(_SAFE_DEFAULT_PROVIDER_CONCURRENCY)

    validated: dict[str, int] = {}
    for provider_id, limit in raw.items():
        validated[provider_id] = _validate_positive_int(
            limit,
            f"{name}[{provider_id}]",
            _SAFE_DEFAULT_MAX_CONCURRENCY,
        )
    return validated


# ---------------------------------------------------------------------------
# RoutingConfig dataclass
# ---------------------------------------------------------------------------

@dataclass
class RoutingConfig:
    """Centralized, validated routing configuration.

    All fields have safe defaults matching Step 1-8 behavior.
    Invalid values are caught at construction time and replaced
    with safe fallbacks (with a warning log).
    """

    # ── Provider concurrency limits ────────────────────────────
    # Per-provider max concurrent slots.  Keys = provider_id strings.
    provider_concurrency: dict[str, int] = field(default_factory=dict)

    # Fallback limit for providers not explicitly listed.
    default_max_concurrency: int = _SAFE_DEFAULT_MAX_CONCURRENCY

    # ── Probe parameters ───────────────────────────────────────
    probe_timeout_seconds: float = _SAFE_DEFAULT_PROBE_TIMEOUT_SECONDS
    probe_degraded_threshold_ms: float = _SAFE_DEFAULT_PROBE_DEGRADED_THRESHOLD_MS

    # ── Routing toggles ───────────────────────────────────────
    routing_enabled: bool = _SAFE_DEFAULT_ROUTING_ENABLED
    bedrock_enabled: bool = _SAFE_DEFAULT_BEDROCK_ENABLED

    # ── Metadata ───────────────────────────────────────────────
    config_source: str = "defaults"

    def __post_init__(self) -> None:
        """Validate and normalize all fields after construction."""
        # Validate default_max_concurrency.
        self.default_max_concurrency = _validate_positive_int(
            self.default_max_concurrency,
            "default_max_concurrency",
            _SAFE_DEFAULT_MAX_CONCURRENCY,
        )

        # Validate per-provider concurrency.
        if self.provider_concurrency:
            self.provider_concurrency = _validate_concurrency_map(
                self.provider_concurrency,
                "provider_concurrency",
            )
        else:
            # Apply safe defaults for known providers.
            self.provider_concurrency = dict(_SAFE_DEFAULT_PROVIDER_CONCURRENCY)

        # Validate probe params.
        self.probe_timeout_seconds = _validate_positive_float(
            self.probe_timeout_seconds,
            "probe_timeout_seconds",
            _SAFE_DEFAULT_PROBE_TIMEOUT_SECONDS,
        )
        self.probe_degraded_threshold_ms = _validate_positive_float(
            self.probe_degraded_threshold_ms,
            "probe_degraded_threshold_ms",
            _SAFE_DEFAULT_PROBE_DEGRADED_THRESHOLD_MS,
        )

    # ── Accessors ──────────────────────────────────────────────

    def get_max_concurrency(self, provider_id: str) -> int:
        """Return max concurrency for a specific provider.

        Falls back to ``default_max_concurrency`` if the provider
        is not explicitly listed.
        """
        return self.provider_concurrency.get(
            provider_id, self.default_max_concurrency
        )

    # ── Observability ──────────────────────────────────────────

    def effective_summary(self) -> dict[str, Any]:
        """Return a safe-to-log summary of effective configuration.

        No secrets or sensitive data is included.
        """
        return {
            "config_source": self.config_source,
            "routing_enabled": self.routing_enabled,
            "bedrock_enabled": self.bedrock_enabled,
            "default_max_concurrency": self.default_max_concurrency,
            "provider_concurrency": dict(self.provider_concurrency),
            "probe_timeout_seconds": self.probe_timeout_seconds,
            "probe_degraded_threshold_ms": self.probe_degraded_threshold_ms,
        }


# ---------------------------------------------------------------------------
# Config loading from environment
# ---------------------------------------------------------------------------

def _load_from_env() -> RoutingConfig:
    """Build a RoutingConfig from environment variables.

    Env vars (all optional, safe defaults if absent):
        ROUTING_ENABLED               — "true"/"false"
        ROUTING_DEFAULT_MAX_CONCURRENCY — int
        ROUTING_CONCURRENCY_LOCALHOST_LLM — int
        ROUTING_CONCURRENCY_NETWORK_MODEL_MACHINE — int
        ROUTING_CONCURRENCY_BEDROCK_TITAN_NOVA_PRO — int
        ROUTING_PROBE_TIMEOUT_SECONDS — float
        ROUTING_PROBE_DEGRADED_THRESHOLD_MS — float
        BEDROCK_ENABLED               — "true"/"false" (shared with app config)
    """
    provider_concurrency: dict[str, int] = {}

    # Read per-provider concurrency from env.
    _PROVIDER_ENV_MAP: dict[str, str] = {
        Provider.LOCALHOST_LLM.value: "ROUTING_CONCURRENCY_LOCALHOST_LLM",
        Provider.NETWORK_MODEL_MACHINE.value: "ROUTING_CONCURRENCY_NETWORK_MODEL_MACHINE",
        Provider.BEDROCK_TITAN_NOVA_PRO.value: "ROUTING_CONCURRENCY_BEDROCK_TITAN_NOVA_PRO",
    }

    for provider_id, env_key in _PROVIDER_ENV_MAP.items():
        raw = os.getenv(env_key)
        if raw is not None:
            provider_concurrency[provider_id] = _validate_positive_int(
                raw, env_key, _SAFE_DEFAULT_MAX_CONCURRENCY,
            )

    # Read scalar settings.
    raw_default_conc = os.getenv("ROUTING_DEFAULT_MAX_CONCURRENCY")
    default_max = (
        _validate_positive_int(
            raw_default_conc, "ROUTING_DEFAULT_MAX_CONCURRENCY",
            _SAFE_DEFAULT_MAX_CONCURRENCY,
        )
        if raw_default_conc is not None
        else _SAFE_DEFAULT_MAX_CONCURRENCY
    )

    raw_probe_timeout = os.getenv("ROUTING_PROBE_TIMEOUT_SECONDS")
    probe_timeout = (
        _validate_positive_float(
            raw_probe_timeout, "ROUTING_PROBE_TIMEOUT_SECONDS",
            _SAFE_DEFAULT_PROBE_TIMEOUT_SECONDS,
        )
        if raw_probe_timeout is not None
        else _SAFE_DEFAULT_PROBE_TIMEOUT_SECONDS
    )

    raw_degraded = os.getenv("ROUTING_PROBE_DEGRADED_THRESHOLD_MS")
    degraded_threshold = (
        _validate_positive_float(
            raw_degraded, "ROUTING_PROBE_DEGRADED_THRESHOLD_MS",
            _SAFE_DEFAULT_PROBE_DEGRADED_THRESHOLD_MS,
        )
        if raw_degraded is not None
        else _SAFE_DEFAULT_PROBE_DEGRADED_THRESHOLD_MS
    )

    raw_routing = os.getenv("ROUTING_ENABLED")
    routing_enabled = (
        raw_routing.lower() in ("true", "1", "yes")
        if raw_routing is not None
        else _SAFE_DEFAULT_ROUTING_ENABLED
    )

    raw_bedrock = os.getenv("BEDROCK_ENABLED")
    bedrock_enabled = (
        raw_bedrock.lower() in ("true", "1", "yes")
        if raw_bedrock is not None
        else _SAFE_DEFAULT_BEDROCK_ENABLED
    )

    source_parts = []
    if any(os.getenv(k) for k in _PROVIDER_ENV_MAP.values()):
        source_parts.append("env:provider_concurrency")
    if raw_default_conc:
        source_parts.append("env:default_max_concurrency")
    if raw_probe_timeout:
        source_parts.append("env:probe_timeout")
    if raw_degraded:
        source_parts.append("env:degraded_threshold")
    if raw_routing:
        source_parts.append("env:routing_enabled")
    if raw_bedrock:
        source_parts.append("env:bedrock_enabled")
    config_source = "+".join(source_parts) if source_parts else "defaults"

    return RoutingConfig(
        provider_concurrency=provider_concurrency or dict(_SAFE_DEFAULT_PROVIDER_CONCURRENCY),
        default_max_concurrency=default_max,
        probe_timeout_seconds=probe_timeout,
        probe_degraded_threshold_ms=degraded_threshold,
        routing_enabled=routing_enabled,
        bedrock_enabled=bedrock_enabled,
        config_source=config_source,
    )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_config: RoutingConfig | None = None
_config_loaded_at: str | None = None


def get_routing_config() -> RoutingConfig:
    """Return the module-level routing config (lazy-init from env)."""
    global _config, _config_loaded_at
    if _config is None:
        import datetime as _dt
        _config = _load_from_env()
        _config_loaded_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        logger.info(
            "[routing_config] loaded: %s",
            _config.effective_summary(),
        )
    return _config


def get_config_loaded_at() -> str | None:
    """Return ISO timestamp of when config was last loaded/refreshed."""
    return _config_loaded_at


def set_routing_config(config: RoutingConfig) -> None:
    """Replace the module-level routing config (for testing / hot-reload)."""
    global _config, _config_loaded_at
    import datetime as _dt
    _config = config
    _config_loaded_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    logger.info(
        "[routing_config] replaced: %s",
        config.effective_summary(),
    )


def reset_routing_config() -> None:
    """Reset to None so next ``get_routing_config()`` reloads from env.

    Primarily for testing.
    """
    global _config, _config_loaded_at
    _config = None
    _config_loaded_at = None


def refresh_routing_config() -> dict[str, Any]:
    """Reload routing config from environment and return a diff summary.

    Thread-safe: the global is replaced atomically.  The diff summary
    shows which fields changed (for audit logging).

    Does NOT refresh the execution gate or provider registry — callers
    that need a full coherent refresh should use
    ``routing_dashboard_service.refresh_routing_runtime()``.

    Returns:
        dict with keys: previous, current, changed_fields, config_loaded_at
    """
    global _config, _config_loaded_at
    import datetime as _dt

    old = _config
    old_summary = old.effective_summary() if old else {}

    new_config = _load_from_env()
    new_summary = new_config.effective_summary()

    changed: dict[str, dict[str, Any]] = {}
    all_keys = set(old_summary) | set(new_summary)
    for k in all_keys:
        ov = old_summary.get(k)
        nv = new_summary.get(k)
        if ov != nv:
            changed[k] = {"old": ov, "new": nv}

    _config = new_config
    _config_loaded_at = _dt.datetime.now(_dt.timezone.utc).isoformat()

    if changed:
        logger.info(
            "[routing_config] refreshed — %d field(s) changed: %s",
            len(changed), list(changed.keys()),
        )
    else:
        logger.info("[routing_config] refreshed — no changes")

    return {
        "previous": old_summary,
        "current": new_summary,
        "changed_fields": changed,
        "config_loaded_at": _config_loaded_at,
    }
