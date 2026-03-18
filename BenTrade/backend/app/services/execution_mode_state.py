"""Centralized execution mode state — persisted to data/runtime_config.json.

**AUTHORITATIVE** — This is the single source of truth for mode selection
in the routed execution path (Step 8+).  All routed callers resolve their
mode via ``resolve_effective_execution_mode()`` which reads from here.

The legacy ``model_state.get_model_source()`` is a separate compatibility
layer for non-routed callers and does NOT affect routed mode selection.

Functions:
    get_execution_mode() -> str       # e.g. "local_distributed"
    set_execution_mode(mode: str)     # validates and persists
    reset_execution_mode()            # revert to default

Step 17 — Distributed Model Routing / UI execution mode control.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.services.model_routing_contract import VALID_MODES

logger = logging.getLogger("bentrade.execution_mode")

_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "runtime_config.json"
_lock = threading.RLock()

_DEFAULT_MODE: str = "local_distributed"

# In-memory cache — initialised lazily from disk
_active_mode: str | None = None


def _load_from_disk() -> str:
    """Read persisted execution mode from runtime_config.json."""
    try:
        if _RUNTIME_CONFIG_PATH.exists():
            data = json.loads(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
            mode = data.get("execution_mode")
            if mode and mode in VALID_MODES:
                return mode
    except Exception as exc:
        logger.warning("Failed to read execution_mode from runtime_config.json: %s", exc)
    return _DEFAULT_MODE


def _persist(mode: str) -> None:
    """Write execution_mode into runtime_config.json (merge, not overwrite)."""
    try:
        data: dict = {}
        if _RUNTIME_CONFIG_PATH.exists():
            data = json.loads(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
        data["execution_mode"] = mode
        data["execution_mode_updated_at"] = datetime.now(timezone.utc).isoformat()
        _RUNTIME_CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to persist execution_mode to runtime_config.json: %s", exc)


def get_execution_mode() -> str:
    """Return active execution mode (e.g. 'local_distributed')."""
    global _active_mode
    with _lock:
        if _active_mode is None:
            _active_mode = _load_from_disk()
        return _active_mode


def set_execution_mode(mode: str) -> str:
    """Validate, persist, and return the new execution mode.

    Raises ValueError if mode is not a recognised execution mode.
    """
    global _active_mode
    mode = mode.strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(
            f"Unknown execution mode '{mode}'. "
            f"Valid: {', '.join(sorted(VALID_MODES))}"
        )
    with _lock:
        _active_mode = mode
        _persist(mode)
    logger.info("Execution mode changed to '%s'", mode)
    return mode


def reset_execution_mode() -> None:
    """Reset in-memory cache so next get reloads from disk. For testing."""
    global _active_mode
    with _lock:
        _active_mode = None
