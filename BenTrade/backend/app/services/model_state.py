"""Runtime model source state — persisted to data/runtime_config.json.

Compatibility layer — controls which LLM endpoint non-routed callers
(model_router, model_health_service, active_trade_pipeline) target.

For routed execution (Step 8+), the authoritative mode is managed by
``execution_mode_state.get_execution_mode()`` and persisted separately
as ``execution_mode`` in runtime_config.json.

Functions:
    get_model_source() -> str       # e.g. "local"
    set_model_source(source: str)   # validates and persists
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.model_sources import MODEL_SOURCES, VALID_SOURCE_KEYS

logger = logging.getLogger("bentrade.model_state")

_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "runtime_config.json"
_lock = threading.RLock()

# In-memory cache — initialised lazily from disk
_active_source: str | None = None


def _load_from_disk() -> str:
    """Read persisted model source from runtime_config.json."""
    try:
        if _RUNTIME_CONFIG_PATH.exists():
            data = json.loads(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
            source = data.get("model_source", "local")
            if source in VALID_SOURCE_KEYS:
                return source
    except Exception as exc:
        logger.warning("Failed to read model_source from runtime_config.json: %s", exc)
    return "local"


def _persist(source: str) -> None:
    """Write model_source back into runtime_config.json (merge, not overwrite)."""
    try:
        data: dict = {}
        if _RUNTIME_CONFIG_PATH.exists():
            data = json.loads(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
        data["model_source"] = source
        data["model_source_updated_at"] = datetime.now(timezone.utc).isoformat()
        _RUNTIME_CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to persist model_source to runtime_config.json: %s", exc)


def get_model_source() -> str:
    """Return active model source key (e.g. 'local', 'model_machine')."""
    global _active_source
    with _lock:
        if _active_source is None:
            _active_source = _load_from_disk()
        return _active_source


def set_model_source(source: str) -> str:
    """Validate, persist, and return the new active source key.

    Raises ValueError if source is unknown or disabled.
    """
    global _active_source
    source = source.strip().lower()
    if source not in VALID_SOURCE_KEYS:
        raise ValueError(f"Unknown model source '{source}'. Valid: {', '.join(sorted(VALID_SOURCE_KEYS))}")
    cfg = MODEL_SOURCES[source]
    if not cfg.get("enabled"):
        raise ValueError(f"Model source '{source}' ({cfg['name']}) is currently disabled")

    with _lock:
        _active_source = source
        _persist(source)
    logger.info("Model source changed to '%s' (%s)", source, cfg["name"])
    return source
