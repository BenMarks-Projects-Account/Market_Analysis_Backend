"""Manifest-based offline data source for scanner replay.

Provides ``ManifestSnapshotSource`` that loads ALL data from a snapshot
captured by ``SnapshotCaptureService``.  Unlike the legacy
``SnapshotChainSource`` which guesses filenames from directory structure,
this source reads the ``snapshot_manifest.json`` and locates every
artifact deterministically.

Also provides ``OfflineLiveCallGuard`` — a context manager that monkey-
patches live provider methods to raise on any attempted live call while
in offline mode.

Usage:
    source = ManifestSnapshotSource.from_trace_id(snapshot_dir, trace_id)
    chain = await source.get_chain("SPY", "2026-03-20")
    price = source.get_underlying_price("SPY")
    history = source.get_prices_history("SPY")
    vix = source.get_vix()
"""
from __future__ import annotations

import functools
import json
import logging
from pathlib import Path
from typing import Any

from app.models.snapshot_manifest import SnapshotManifest

logger = logging.getLogger(__name__)


class SnapshotDataMissing(Exception):
    """Raised when a required artifact is absent from the snapshot.

    Contains an actionable message listing what is missing and how to
    recapture.
    """

    def __init__(self, artifact: str, trace_id: str, suggestion: str = "") -> None:
        self.artifact = artifact
        self.trace_id = trace_id
        msg = (
            f"Offline snapshot {trace_id!r} is missing required artifact: "
            f"{artifact}."
        )
        if suggestion:
            msg += f" {suggestion}"
        else:
            msg += (
                " Re-run capture with: "
                "POST /api/admin/snapshots/capture or "
                "python -m app.tools.capture_snapshot"
            )
        super().__init__(msg)


class OfflineLiveCallError(RuntimeError):
    """Raised when a live provider method is called during offline mode.

    Enforces the fail-closed invariant: no live API calls in offline mode.
    """

    def __init__(self, function_name: str) -> None:
        super().__init__(
            f"OFFLINE MODE VIOLATION: Live provider method {function_name!r} "
            f"was called while running in offline/snapshot mode.  "
            f"All data must come from the loaded snapshot.  "
            f"If data is missing, re-capture with "
            f"POST /api/admin/snapshots/capture."
        )


class ManifestSnapshotSource:
    """Loads all data from a captured snapshot via its manifest.

    Satisfies the ``OptionChainSource`` protocol and additionally
    provides underlying quotes, price history, VIX, and market context.
    """

    def __init__(self, run_dir: Path, manifest: SnapshotManifest) -> None:
        self._run_dir = run_dir
        self._manifest = manifest
        # In-memory caches populated on first access
        self._chains_cache: dict[str, list[dict[str, Any]]] = {}
        self._quotes_cache: dict[str, dict[str, Any]] = {}
        self._history_cache: dict[str, list[float]] = {}
        self._history_dated_cache: dict[str, list[dict[str, Any]]] = {}
        self._market_context: dict[str, Any] | None = None

    # -- Factory -----------------------------------------------------------

    @classmethod
    def from_manifest_path(cls, manifest_path: Path) -> ManifestSnapshotSource:
        """Load from an explicit manifest file path."""
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = SnapshotManifest(**raw)
        return cls(manifest_path.parent, manifest)

    @classmethod
    def from_trace_id(
        cls,
        snapshot_dir: Path,
        trace_id: str,
        provider: str = "tradier",
    ) -> ManifestSnapshotSource:
        """Locate and load a snapshot by trace_id."""
        from app.services.snapshot_capture_service import (
            SnapshotCaptureService,
        )

        run_dir = SnapshotCaptureService.find_snapshot_by_trace_id(
            snapshot_dir, trace_id, provider=provider,
        )
        if run_dir is None:
            raise FileNotFoundError(
                f"No snapshot found for trace_id={trace_id!r} "
                f"under {snapshot_dir / provider}"
            )
        return cls.from_manifest_path(run_dir / "snapshot_manifest.json")

    @classmethod
    def from_latest(
        cls,
        snapshot_dir: Path,
        provider: str = "tradier",
        strategy_id: str | None = None,
    ) -> ManifestSnapshotSource:
        """Load the most recent snapshot, optionally filtered by strategy."""
        from app.services.snapshot_capture_service import (
            SnapshotCaptureService,
        )

        snapshots = SnapshotCaptureService.list_snapshots(
            snapshot_dir, provider=provider, strategy_id=strategy_id,
        )
        if not snapshots:
            raise FileNotFoundError(
                f"No snapshots available under {snapshot_dir / provider}"
            )
        latest = snapshots[0]  # Already sorted newest-first
        return cls.from_manifest_path(
            Path(latest["path"]) / "snapshot_manifest.json",
        )

    # -- Properties --------------------------------------------------------

    @property
    def manifest(self) -> SnapshotManifest:
        return self._manifest

    @property
    def trace_id(self) -> str:
        return self._manifest.trace_id

    # -- OptionChainSource protocol ----------------------------------------

    async def get_chain(
        self,
        symbol: str,
        expiration: str,
        greeks: bool = True,
    ) -> list[dict[str, Any]]:
        """Load chain data from the snapshot."""
        cache_key = f"{symbol.upper()}:{expiration}"
        if cache_key in self._chains_cache:
            return self._chains_cache[cache_key]

        sym = symbol.upper()
        arts = self._manifest.symbol_artifacts.get(sym)
        if arts is None:
            raise SnapshotDataMissing(
                f"{sym} (not in snapshot)", self._manifest.trace_id,
            )

        rel_path = arts.option_chains.get(expiration)
        if rel_path is None:
            available = list(arts.option_chains.keys())
            raise SnapshotDataMissing(
                f"{sym} chain for expiration {expiration} "
                f"(available: {available})",
                self._manifest.trace_id,
            )

        full_path = self._run_dir / rel_path
        if not full_path.is_file():
            raise SnapshotDataMissing(
                f"Chain file {rel_path} not found on disk",
                self._manifest.trace_id,
            )

        chain = self._read_chain_file(full_path)
        self._chains_cache[cache_key] = chain
        return chain

    # -- Extra data accessors (beyond OptionChainSource) -------------------

    def get_available_expirations(self, symbol: str) -> list[str]:
        """Return expirations captured for *symbol*, sorted ascending."""
        sym = symbol.upper()
        arts = self._manifest.symbol_artifacts.get(sym)
        if arts is None:
            return []
        return sorted(arts.option_chains.keys())

    def get_underlying_price(self, symbol: str) -> float | None:
        """Read underlying price from the captured quote file."""
        sym = symbol.upper()
        if sym in self._quotes_cache:
            quote = self._quotes_cache[sym]
            for field in ("last", "close", "mark", "bid", "ask"):
                val = quote.get(field)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        continue
            return None

        arts = self._manifest.symbol_artifacts.get(sym)
        if arts is None or arts.underlying_quote is None:
            # Fallback: try from chain metadata
            return self._derive_price_from_chain_meta(sym)

        quote_path = self._run_dir / arts.underlying_quote
        if not quote_path.is_file():
            return self._derive_price_from_chain_meta(sym)

        try:
            data = json.loads(quote_path.read_text(encoding="utf-8"))
            quote = data.get("quote", {})
            self._quotes_cache[sym] = quote
            for field in ("last", "close", "mark", "bid", "ask"):
                val = quote.get(field)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        continue
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "event=snapshot_quote_load_error symbol=%s error=%s",
                sym, exc,
            )
        return self._derive_price_from_chain_meta(sym)

    def get_prices_history(self, symbol: str) -> list[float]:
        """Load saved close prices for *symbol*."""
        sym = symbol.upper()
        if sym in self._history_cache:
            return self._history_cache[sym]

        arts = self._manifest.symbol_artifacts.get(sym)
        if arts is None or arts.prices_history is None:
            return []

        history_path = self._run_dir / arts.prices_history
        if not history_path.is_file():
            return []

        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
            closes = data.get("closes")
            if isinstance(closes, list):
                result = [float(x) for x in closes if x is not None]
                self._history_cache[sym] = result
                return result
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning(
                "event=snapshot_history_load_error symbol=%s error=%s",
                sym, exc,
            )
        return []

    def get_prices_history_dated(self, symbol: str) -> list[dict[str, Any]]:
        """Load saved dated bars for *symbol*."""
        sym = symbol.upper()
        if sym in self._history_dated_cache:
            return self._history_dated_cache[sym]

        arts = self._manifest.symbol_artifacts.get(sym)
        if arts is None or arts.prices_history is None:
            return []

        history_path = self._run_dir / arts.prices_history
        if not history_path.is_file():
            return []

        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
            bars = data.get("bars")
            if isinstance(bars, list):
                self._history_dated_cache[sym] = bars
                return bars
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning(
                "event=snapshot_dated_history_load_error symbol=%s error=%s",
                sym, exc,
            )
        return []

    def get_vix(self) -> float | None:
        """Return the VIX value from market context."""
        ctx = self._load_market_context()
        return ctx.get("vix")

    def get_market_context(self) -> dict[str, Any]:
        """Return the full market context dict."""
        return self._load_market_context()

    # -- Staleness (compat with SnapshotChainSource) -----------------------

    def check_staleness(
        self, symbol: str, expiration: str,
    ) -> dict[str, Any]:
        """Always returns not-stale since manifest snapshots are intentional."""
        return {
            "stale": False,
            "snapshot_timestamp": self._manifest.created_at,
            "age_seconds": None,
            "max_age_hours": None,
            "warning": None,
        }

    # -- Internal helpers --------------------------------------------------

    def _load_market_context(self) -> dict[str, Any]:
        if self._market_context is not None:
            return self._market_context

        if self._manifest.market_context_path is None:
            self._market_context = {}
            return self._market_context

        ctx_path = self._run_dir / self._manifest.market_context_path
        if not ctx_path.is_file():
            self._market_context = {}
            return self._market_context

        try:
            self._market_context = json.loads(
                ctx_path.read_text(encoding="utf-8"),
            )
        except (json.JSONDecodeError, OSError):
            self._market_context = {}
        return self._market_context

    def _derive_price_from_chain_meta(self, symbol: str) -> float | None:
        """Attempt to read underlying_price from first chain file's meta."""
        arts = self._manifest.symbol_artifacts.get(symbol)
        if arts is None:
            return None
        for rel_path in arts.option_chains.values():
            full_path = self._run_dir / rel_path
            if not full_path.is_file():
                continue
            try:
                data = json.loads(full_path.read_text(encoding="utf-8"))
                price = data.get("meta", {}).get("underlying_price")
                if price is not None:
                    return float(price)
            except (json.JSONDecodeError, ValueError, OSError):
                continue
        return None

    @staticmethod
    def _read_chain_file(path: Path) -> list[dict[str, Any]]:
        """Parse a snapshot chain JSON file and return the raw chain list."""
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
                    f"Snapshot {path} contains non-JSON raw response; "
                    f"cannot replay."
                )
            return [raw]

        if isinstance(raw, list):
            return raw

        raise ValueError(
            f"Snapshot {path} has unexpected 'raw' format: {type(raw)}"
        )


# ---------------------------------------------------------------------------
# Fail-closed guard: block live calls in offline mode
# ---------------------------------------------------------------------------


class OfflineLiveCallGuard:
    """Context manager that patches live provider methods to raise on call.

    Usage:
        with OfflineLiveCallGuard(base_data_service):
            # Any live provider call inside this block raises
            # OfflineLiveCallError
            await scanner.run(...)
    """

    # Methods that must NOT be called in offline mode.
    # Format: (attribute_path_on_bds, method_name)
    _GUARDED_METHODS: list[tuple[str, str]] = [
        ("tradier_client", "get_chain"),
        ("tradier_client", "get_quote"),
        ("tradier_client", "get_quotes"),
        ("tradier_client", "get_expirations"),
        ("tradier_client", "get_daily_closes"),
        ("tradier_client", "get_daily_closes_dated"),
        ("tradier_client", "get_daily_bars"),
        ("tradier_client", "get_option_quotes"),
        ("tradier_client", "fetch_chain_raw_payload"),
        ("finnhub_client", "get_quote"),
        ("finnhub_client", "get_daily_candles"),
        ("polygon_client", "get_daily_closes"),
        ("polygon_client", "get_daily_closes_dated"),
        ("fred_client", "get_latest_series_value"),
    ]

    def __init__(self, base_data_service: Any) -> None:
        self._bds = base_data_service
        self._originals: list[tuple[Any, str, Any]] = []

    def __enter__(self) -> OfflineLiveCallGuard:
        for attr_path, method_name in self._GUARDED_METHODS:
            obj = getattr(self._bds, attr_path, None)
            if obj is None:
                continue
            original = getattr(obj, method_name, None)
            if original is None:
                continue
            self._originals.append((obj, method_name, original))
            full_name = f"{attr_path}.{method_name}"

            # Create a wrapper that raises
            async def _blocked(*args, _fn=full_name, **kwargs):
                raise OfflineLiveCallError(_fn)

            setattr(obj, method_name, _blocked)
        return self

    def __exit__(self, *exc_info) -> None:
        for obj, method_name, original in self._originals:
            setattr(obj, method_name, original)
        self._originals.clear()
