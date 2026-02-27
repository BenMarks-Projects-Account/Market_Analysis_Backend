"""Snapshot recorder and chain-source abstraction for after-hours dev/test.

Provides:
- ``SnapshotRecorder``   — saves raw option-chain HTTP responses to JSON
- ``OptionChainSource``  — protocol for fetching raw (pre-normalization) chain data
- ``TradierChainSource`` — live source (delegates to TradierClient)
- ``SnapshotChainSource``— replays saved snapshots from disk
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data-source abstraction
# ---------------------------------------------------------------------------

@runtime_checkable
class OptionChainSource(Protocol):
    """Pluggable source for raw (pre-normalization) option-chain data.

    Both ``TradierChainSource`` and ``SnapshotChainSource`` implement this.
    """

    async def get_chain(
        self, symbol: str, expiration: str, greeks: bool = True,
    ) -> list[dict[str, Any]]:  # pragma: no cover
        ...


class TradierChainSource:
    """Live source — delegates to ``TradierClient.get_chain()``."""

    def __init__(self, tradier_client: Any) -> None:
        self._client = tradier_client

    async def get_chain(
        self, symbol: str, expiration: str, greeks: bool = True,
    ) -> list[dict[str, Any]]:
        return await self._client.get_chain(symbol, expiration, greeks=greeks)


class SnapshotChainSource:
    """Loads option-chain data from saved JSON snapshots on disk.

    Folder layout (written by ``SnapshotRecorder``):
        ``{snapshot_dir}/{provider}/{YYYYMMDD}/{SYMBOL}/{expiration}/chain_*.json``

    Each file contains ``{"meta": {...}, "raw": <chain data>}``.
    """

    def __init__(self, snapshot_dir: Path, provider: str = "tradier") -> None:
        self._snapshot_dir = snapshot_dir
        self._provider = provider

    # --- OptionChainSource protocol ----------------------------------------

    async def get_chain(
        self, symbol: str, expiration: str, greeks: bool = True,
    ) -> list[dict[str, Any]]:
        return self._load_latest(symbol, expiration)

    # --- extra helpers (used by BaseDataService in snapshot mode) -----------

    def get_available_expirations(self, symbol: str) -> list[str]:
        """Derive expirations present in snapshots for *symbol*.

        Searches across all date folders (latest first) and collects unique
        expiration sub-directories.  Returns sorted ascending.
        """
        symbol_upper = symbol.upper()
        provider_dir = self._snapshot_dir / self._provider
        if not provider_dir.is_dir():
            return []

        expirations: set[str] = set()
        date_dirs = sorted(
            (d for d in provider_dir.iterdir() if d.is_dir() and d.name.isdigit()),
            key=lambda d: d.name,
            reverse=True,
        )
        for date_dir in date_dirs:
            sym_dir = date_dir / symbol_upper
            if not sym_dir.is_dir():
                continue
            for exp_dir in sym_dir.iterdir():
                if exp_dir.is_dir() and any(exp_dir.glob("chain_*.json")):
                    expirations.add(exp_dir.name)
        return sorted(expirations)

    def get_underlying_price(self, symbol: str) -> float | None:
        """Read ``underlying_price`` from the latest snapshot metadata."""
        symbol_upper = symbol.upper()
        provider_dir = self._snapshot_dir / self._provider
        if not provider_dir.is_dir():
            return None

        date_dirs = sorted(
            (d for d in provider_dir.iterdir() if d.is_dir() and d.name.isdigit()),
            key=lambda d: d.name,
            reverse=True,
        )
        for date_dir in date_dirs:
            sym_dir = date_dir / symbol_upper
            if not sym_dir.is_dir():
                continue
            # Find any chain file for this symbol — latest date dir first
            for exp_dir in sorted(sym_dir.iterdir(), reverse=True):
                chain_files = sorted(exp_dir.glob("chain_*.json"), reverse=True)
                if chain_files:
                    try:
                        data = json.loads(chain_files[0].read_text(encoding="utf-8"))
                        price = data.get("meta", {}).get("underlying_price")
                        if price is not None:
                            return float(price)
                    except (json.JSONDecodeError, ValueError, OSError):
                        continue
        return None

    # --- internal ----------------------------------------------------------

    def _load_latest(self, symbol: str, expiration: str) -> list[dict[str, Any]]:
        """Find the most-recent snapshot for *(symbol, expiration)*."""
        symbol_upper = symbol.upper()

        provider_dir = self._snapshot_dir / self._provider
        if not provider_dir.is_dir():
            raise FileNotFoundError(
                f"No snapshot directory for provider '{self._provider}'. "
                f"Capture with SNAPSHOT_CAPTURE=1 during market hours."
            )

        # Search date dirs newest-first
        date_dirs = sorted(
            (d for d in provider_dir.iterdir() if d.is_dir() and d.name.isdigit()),
            key=lambda d: d.name,
            reverse=True,
        )
        for date_dir in date_dirs:
            symbol_dir = date_dir / symbol_upper / expiration
            if not symbol_dir.is_dir():
                continue
            chain_files = sorted(symbol_dir.glob("chain_*.json"), reverse=True)
            if chain_files:
                return self._read_chain_file(chain_files[0])

        raise FileNotFoundError(
            f"No snapshot available for {symbol_upper} {expiration}. "
            f"Capture with SNAPSHOT_CAPTURE=1 during market hours."
        )

    @staticmethod
    def _read_chain_file(path: Path) -> list[dict[str, Any]]:
        """Parse a snapshot JSON file and return the raw chain list."""
        data = json.loads(path.read_text(encoding="utf-8"))

        raw = data.get("raw")
        if raw is None:
            raise ValueError(f"Snapshot file {path} missing 'raw' key")

        # Full Tradier envelope → extract options.option
        if isinstance(raw, dict):
            if "options" in raw:
                options = (raw.get("options") or {}).get("option") or []
                if isinstance(options, dict):
                    return [options]
                return options if isinstance(options, list) else []
            if "_raw_text" in raw:
                raise ValueError(
                    f"Snapshot {path} contains non-JSON raw response; cannot replay."
                )
            # Single contract stored as a dict
            return [raw]

        if isinstance(raw, list):
            return raw

        raise ValueError(f"Snapshot {path} has unexpected 'raw' format: {type(raw)}")

    def load_from_path(self, path: str | Path) -> list[dict[str, Any]]:
        """Load chain data from an explicit snapshot file path."""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Snapshot file not found: {p}")
        return self._read_chain_file(p)


# ---------------------------------------------------------------------------
# Snapshot recorder
# ---------------------------------------------------------------------------

class SnapshotRecorder:
    """Records raw option-chain HTTP responses to JSON files on disk.

    File path convention:
        ``{snapshot_dir}/{provider}/{YYYYMMDD}/{SYMBOL}/{expiration}/
          chain_{HHMMSS}_{trace_id}.json``

    Index file per run:
        ``{snapshot_dir}/{provider}/{YYYYMMDD}/index_{trace_id}.json``

    File format:
        ``{"meta": {…}, "raw": <chain_data_as_json>}``
    """

    def __init__(
        self,
        snapshot_dir: Path,
        *,
        enabled: bool = False,
        capture_symbols: set[str] | None = None,
        limit_per_symbol: int | None = None,
    ) -> None:
        self._snapshot_dir = snapshot_dir
        self._enabled = enabled
        self._capture_symbols = capture_symbols  # None ⇒ all
        self._limit_per_symbol = limit_per_symbol
        self._capture_counts: dict[str, int] = {}
        self._trace_id: str = uuid.uuid4().hex[:12]
        self._saved_files: list[dict[str, Any]] = []

    # -- public properties --------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def trace_id(self) -> str:
        return self._trace_id

    # -- lifecycle ----------------------------------------------------------

    def reset_run(self) -> None:
        """Reset counters for a new scan run."""
        self._trace_id = uuid.uuid4().hex[:12]
        self._capture_counts.clear()
        self._saved_files.clear()

    # -- gating -------------------------------------------------------------

    def should_capture(self, symbol: str) -> bool:
        if not self._enabled:
            return False
        sym = symbol.upper()
        if self._capture_symbols and sym not in self._capture_symbols:
            return False
        if self._limit_per_symbol is not None:
            if self._capture_counts.get(sym, 0) >= self._limit_per_symbol:
                return False
        return True

    # -- save ---------------------------------------------------------------

    def save_chain_response(
        self,
        raw_data: Any,
        *,
        provider: str,
        symbol: str,
        expiration: str,
        endpoint: str = "",
        request_params: dict[str, Any] | None = None,
        http_status: int = 200,
        strategy_id: str | None = None,
        underlying_price: float | None = None,
    ) -> Path | None:
        """Write a raw chain response to disk.

        Returns the written file ``Path``, or ``None`` if capture was
        skipped (disabled / symbol filtered / limit reached).
        """
        sym = symbol.upper()
        if not self.should_capture(sym):
            return None

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y%m%d")
        time_str = now.strftime("%H%M%S")

        meta: dict[str, Any] = {
            "provider": provider,
            "endpoint": endpoint,
            "request_params": request_params or {},
            "timestamp": now.isoformat(),
            "strategy_id": strategy_id,
            "symbol": sym,
            "expiration": expiration,
            "http_status": http_status,
            "trace_id": self._trace_id,
            "underlying_price": underlying_price,
        }

        # Build ``raw`` field — keep original JSON structure.
        if isinstance(raw_data, (dict, list)):
            raw_field: Any = raw_data
        elif isinstance(raw_data, str):
            try:
                raw_field = json.loads(raw_data)
            except (json.JSONDecodeError, ValueError):
                raw_field = {"_raw_text": raw_data}
        else:
            raw_field = {"_raw_text": str(raw_data)}

        snapshot = {"meta": meta, "raw": raw_field}

        # Deterministic path
        file_dir = self._snapshot_dir / provider / date_str / sym / expiration
        file_dir.mkdir(parents=True, exist_ok=True)
        filename = f"chain_{time_str}_{self._trace_id}.json"
        file_path = file_dir / filename

        file_path.write_text(
            json.dumps(snapshot, indent=2, default=str),
            encoding="utf-8",
        )

        self._capture_counts[sym] = self._capture_counts.get(sym, 0) + 1
        file_info: dict[str, Any] = {"path": str(file_path), "meta": meta}
        self._saved_files.append(file_info)

        logger.info(
            "event=snapshot_saved provider=%s symbol=%s expiration=%s path=%s",
            provider, sym, expiration, file_path,
        )
        return file_path

    # -- index --------------------------------------------------------------

    def write_index(self) -> Path | None:
        """Write the per-run index file.  Returns path or ``None`` if empty."""
        if not self._saved_files:
            return None

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y%m%d")

        providers = {f["meta"]["provider"] for f in self._saved_files}
        provider = sorted(providers)[0] if providers else "unknown"

        index_dir = self._snapshot_dir / provider / date_str
        index_dir.mkdir(parents=True, exist_ok=True)
        index_path = index_dir / f"index_{self._trace_id}.json"

        index_data = {
            "trace_id": self._trace_id,
            "timestamp": now.isoformat(),
            "file_count": len(self._saved_files),
            "files": self._saved_files,
        }

        index_path.write_text(
            json.dumps(index_data, indent=2, default=str),
            encoding="utf-8",
        )

        logger.info(
            "event=snapshot_index_written trace_id=%s file_count=%d path=%s",
            self._trace_id, len(self._saved_files), index_path,
        )
        return index_path
