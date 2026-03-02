"""Platform-level runtime settings with JSON file persistence.

Provides a thread-safe, runtime-mutable settings store that survives
backend restarts.  The primary consumer is the Admin → Data Health
dashboard's "Platform Data Source" toggle.

Persistence file: ``{data_dir}/platform_settings.json``

Settings schema (v1):
    {
      "data_source_mode": "live" | "snapshot",
      "updated_at": "ISO-8601 UTC timestamp",
      "version": 1
    }
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)

_SETTINGS_FILENAME = "platform_settings.json"
_VERSION = 1

# Valid data source modes
MODE_LIVE = "live"
MODE_SNAPSHOT = "snapshot"
_VALID_MODES = {MODE_LIVE, MODE_SNAPSHOT}

_DEFAULT_SETTINGS: dict[str, Any] = {
    "data_source_mode": MODE_LIVE,
    "updated_at": None,
    "version": _VERSION,
}


class PlatformSettings:
    """Thread-safe runtime platform settings backed by a JSON file.

    Parameters
    ----------
    data_dir : Path
        Directory where ``platform_settings.json`` is stored.
    env_default_mode : str
        Fallback mode from ``OPTION_CHAIN_SOURCE`` env var.
        Used as the initial value when no persisted file exists.
    """

    def __init__(self, data_dir: Path, *, env_default_mode: str = MODE_LIVE) -> None:
        self._data_dir = data_dir
        self._path = data_dir / _SETTINGS_FILENAME
        self._lock = RLock()

        # Resolve the startup default from env (backward compat)
        _env = env_default_mode.strip().lower()
        self._env_default = _env if _env in _VALID_MODES else MODE_LIVE

        self._state: dict[str, Any] = dict(_DEFAULT_SETTINGS)
        self._load()

    # -- public API ---------------------------------------------------------

    @property
    def data_source_mode(self) -> str:
        """Current data source mode: ``'live'`` or ``'snapshot'``."""
        with self._lock:
            return str(self._state.get("data_source_mode") or MODE_LIVE)

    def set_data_source_mode(self, mode: str) -> dict[str, Any]:
        """Set data source mode and persist.

        Returns the full settings dict after mutation.

        Raises ``ValueError`` for invalid modes.
        """
        normalized = str(mode).strip().lower()
        if normalized not in _VALID_MODES:
            raise ValueError(
                f"Invalid data_source_mode '{mode}'. "
                f"Must be one of: {', '.join(sorted(_VALID_MODES))}"
            )
        with self._lock:
            self._state["data_source_mode"] = normalized
            self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._state["version"] = _VERSION
            self._persist()
            logger.info(
                "event=platform_data_source_changed mode=%s updated_at=%s",
                normalized,
                self._state["updated_at"],
            )
            return dict(self._state)

    def get_state(self) -> dict[str, Any]:
        """Return a copy of the full settings dict."""
        with self._lock:
            return dict(self._state)

    # -- internal -----------------------------------------------------------

    def _load(self) -> None:
        """Load from disk, falling back to env default."""
        with self._lock:
            if self._path.is_file():
                try:
                    raw = json.loads(self._path.read_text(encoding="utf-8"))
                    mode = str(raw.get("data_source_mode") or "").strip().lower()
                    if mode not in _VALID_MODES:
                        mode = self._env_default
                    self._state = {
                        "data_source_mode": mode,
                        "updated_at": raw.get("updated_at"),
                        "version": raw.get("version", _VERSION),
                    }
                    logger.info(
                        "event=platform_settings_loaded mode=%s path=%s",
                        mode,
                        self._path,
                    )
                    return
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning(
                        "event=platform_settings_load_error path=%s error=%s",
                        self._path,
                        exc,
                    )

            # No file or parse error — use env default
            self._state = {
                "data_source_mode": self._env_default,
                "updated_at": None,
                "version": _VERSION,
            }
            logger.info(
                "event=platform_settings_default mode=%s",
                self._env_default,
            )

    def _persist(self) -> None:
        """Write current state to disk."""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._state, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(
                "event=platform_settings_persist_error path=%s error=%s",
                self._path,
                exc,
            )
