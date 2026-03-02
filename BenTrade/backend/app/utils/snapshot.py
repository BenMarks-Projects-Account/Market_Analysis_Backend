"""Snapshot recorder and chain-source abstraction for after-hours dev/test.

Provides:
- ``SnapshotRecorder``   — saves raw option-chain HTTP responses to JSON
- ``OptionChainSource``  — protocol for fetching raw (pre-normalization) chain data
- ``TradierChainSource`` — live source (delegates to TradierClient)
- ``SnapshotChainSource``— replays saved snapshots from disk
- ``run_snapshot_cleanup``— prunes snapshot date-dirs older than retention window
"""
from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
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
        ``{snapshot_dir}/{provider}/{YYYYMMDD}/{SYMBOL}/prices_history.json``

    Each file contains ``{"meta": {...}, "raw": <chain data>}``.
    """

    def __init__(
        self,
        snapshot_dir: Path,
        provider: str = "tradier",
        *,
        max_age_hours: int | None = None,
    ) -> None:
        self._snapshot_dir = snapshot_dir
        self._provider = provider
        self._max_age_hours = max_age_hours

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
        """Read ``underlying_price`` from the latest snapshot metadata.

        Fallback strategy (if meta.underlying_price is missing):
          1. Look for a saved underlying quote file.
          2. Approximate from nearest ATM strike mid-prices.
          3. Return None (caller must handle).
        """
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
                if not exp_dir.is_dir():
                    continue
                chain_files = sorted(exp_dir.glob("chain_*.json"), reverse=True)
                if chain_files:
                    try:
                        data = json.loads(chain_files[0].read_text(encoding="utf-8"))
                        price = data.get("meta", {}).get("underlying_price")
                        if price is not None:
                            return float(price)
                    except (json.JSONDecodeError, ValueError, OSError):
                        continue
            # Fallback: attempt to derive from chain strikes (ATM approximation)
            price = self._derive_underlying_from_chain(sym_dir)
            if price is not None:
                return price
        return None

    def _derive_underlying_from_chain(self, sym_dir: Path) -> float | None:
        """Best-effort underlying price from nearest ATM call/put mid-prices.

        Picks the strike where call bid ≈ put bid (closest absolute
        difference).  Returns midpoint of call and put mid-prices at that
        strike, or None if derivation fails.
        """
        try:
            for exp_dir in sorted(sym_dir.iterdir(), reverse=True):
                if not exp_dir.is_dir():
                    continue
                chain_files = sorted(exp_dir.glob("chain_*.json"), reverse=True)
                if not chain_files:
                    continue
                data = json.loads(chain_files[0].read_text(encoding="utf-8"))
                raw_chain = self._read_chain_file(chain_files[0])
                if not raw_chain:
                    continue

                # Bucket by strike: calls and puts
                calls: dict[float, dict] = {}
                puts: dict[float, dict] = {}
                for contract in raw_chain:
                    if not isinstance(contract, dict):
                        continue
                    strike = contract.get("strike")
                    if strike is None:
                        continue
                    strike = float(strike)
                    opt_type = str(contract.get("option_type") or "").lower()
                    bid = contract.get("bid")
                    ask = contract.get("ask")
                    if bid is None or ask is None:
                        continue
                    mid = (float(bid) + float(ask)) / 2.0
                    entry = {"strike": strike, "mid": mid}
                    if opt_type == "call":
                        calls[strike] = entry
                    elif opt_type == "put":
                        puts[strike] = entry

                # Find ATM strike: where call mid ≈ put mid
                shared_strikes = set(calls.keys()) & set(puts.keys())
                if not shared_strikes:
                    continue

                best_strike = min(
                    shared_strikes,
                    key=lambda s: abs(calls[s]["mid"] - puts[s]["mid"]),
                )
                # Underlying ≈ strike (ATM), but refine with put-call parity
                # approximation: S ≈ strike + call_mid - put_mid
                c_mid = calls[best_strike]["mid"]
                p_mid = puts[best_strike]["mid"]
                estimated = best_strike + c_mid - p_mid
                if estimated > 0:
                    logger.info(
                        "event=underlying_price_derived_from_chain symbol_dir=%s "
                        "strike=%s call_mid=%s put_mid=%s estimated=%s",
                        sym_dir.name, best_strike, c_mid, p_mid, estimated,
                    )
                    return round(estimated, 2)
        except Exception as exc:
            logger.warning(
                "event=underlying_price_derivation_failed dir=%s error=%s",
                sym_dir, exc,
            )
        return None

    # --- prices_history support --------------------------------------------

    def get_prices_history(self, symbol: str) -> list[float]:
        """Load saved prices_history for *symbol* from the latest date dir.

        Returns empty list if no prices_history file exists.
        """
        symbol_upper = symbol.upper()
        provider_dir = self._snapshot_dir / self._provider
        if not provider_dir.is_dir():
            return []

        date_dirs = sorted(
            (d for d in provider_dir.iterdir() if d.is_dir() and d.name.isdigit()),
            key=lambda d: d.name,
            reverse=True,
        )
        for date_dir in date_dirs:
            sym_dir = date_dir / symbol_upper
            if not sym_dir.is_dir():
                continue
            history_file = sym_dir / "prices_history.json"
            if history_file.is_file():
                try:
                    data = json.loads(history_file.read_text(encoding="utf-8"))
                    closes = data.get("closes") if isinstance(data, dict) else data
                    if isinstance(closes, list):
                        return [float(x) for x in closes if x is not None]
                except (json.JSONDecodeError, ValueError, OSError) as exc:
                    logger.warning(
                        "event=prices_history_load_error symbol=%s path=%s error=%s",
                        symbol_upper, history_file, exc,
                    )
        return []

    # --- staleness checking ------------------------------------------------

    def check_staleness(self, symbol: str, expiration: str) -> dict[str, Any]:
        """Check whether the latest snapshot for *(symbol, expiration)* is stale.

        Returns a dict with:
          - stale: bool
          - snapshot_timestamp: str | None (ISO-8601)
          - age_seconds: float | None
          - max_age_hours: int | None
          - warning: str | None
        """
        result: dict[str, Any] = {
            "stale": False,
            "snapshot_timestamp": None,
            "age_seconds": None,
            "max_age_hours": self._max_age_hours,
            "warning": None,
        }
        if self._max_age_hours is None:
            return result

        symbol_upper = symbol.upper()
        provider_dir = self._snapshot_dir / self._provider
        if not provider_dir.is_dir():
            result["stale"] = True
            result["warning"] = f"No snapshot directory for {symbol_upper}"
            return result

        # Find the latest chain file for this symbol + expiration
        date_dirs = sorted(
            (d for d in provider_dir.iterdir() if d.is_dir() and d.name.isdigit()),
            key=lambda d: d.name,
            reverse=True,
        )
        for date_dir in date_dirs:
            chain_dir = date_dir / symbol_upper / expiration
            if not chain_dir.is_dir():
                continue
            chain_files = sorted(chain_dir.glob("chain_*.json"), reverse=True)
            if not chain_files:
                continue
            try:
                data = json.loads(chain_files[0].read_text(encoding="utf-8"))
                ts_str = data.get("meta", {}).get("timestamp")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    age = (now - ts).total_seconds()
                    result["snapshot_timestamp"] = ts_str
                    result["age_seconds"] = age
                    max_seconds = self._max_age_hours * 3600
                    if age > max_seconds:
                        result["stale"] = True
                        result["warning"] = (
                            f"Snapshot for {symbol_upper} {expiration} is "
                            f"{age / 3600:.1f}h old (max {self._max_age_hours}h)"
                        )
                    return result
            except (json.JSONDecodeError, ValueError, OSError):
                continue

        result["stale"] = True
        result["warning"] = f"No snapshot found for {symbol_upper} {expiration}"
        return result

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

    # -- prices_history saving ----------------------------------------------

    def save_prices_history(
        self,
        closes: list[float],
        *,
        provider: str,
        symbol: str,
    ) -> Path | None:
        """Save underlying prices_history alongside chain snapshots.

        Stored as ``{snapshot_dir}/{provider}/{YYYYMMDD}/{SYMBOL}/prices_history.json``.
        Returns the path or None if capture is disabled for the symbol.
        """
        sym = symbol.upper()
        if not self.should_capture(sym):
            return None

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y%m%d")

        file_dir = self._snapshot_dir / provider / date_str / sym
        file_dir.mkdir(parents=True, exist_ok=True)
        file_path = file_dir / "prices_history.json"

        data = {
            "meta": {
                "provider": provider,
                "symbol": sym,
                "timestamp": now.isoformat(),
                "trace_id": self._trace_id,
                "close_count": len(closes),
            },
            "closes": closes,
        }

        file_path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )

        logger.info(
            "event=prices_history_saved symbol=%s closes=%d path=%s",
            sym, len(closes), file_path,
        )
        return file_path


# ---------------------------------------------------------------------------
# Snapshot retention cleanup
# ---------------------------------------------------------------------------


def run_snapshot_cleanup(
    snapshot_dir: Path,
    retention_days: int = 7,
) -> list[str]:
    """Remove snapshot date directories older than *retention_days*.

    Scans ``{snapshot_dir}/{provider}/{YYYYMMDD}/`` directories and
    deletes those where the date is strictly before ``today - retention_days``.
    Today's directory is never removed.

    Returns a list of removed directory paths (as strings).
    """
    if not snapshot_dir.is_dir():
        return []

    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=max(1, retention_days))
    removed: list[str] = []

    for provider_dir in snapshot_dir.iterdir():
        if not provider_dir.is_dir():
            continue
        for date_dir in sorted(provider_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            name = date_dir.name
            if not name.isdigit() or len(name) != 8:
                continue
            try:
                dir_date = datetime.strptime(name, "%Y%m%d").date()
            except ValueError:
                continue
            if dir_date >= cutoff:
                continue
            # Safety: never delete today
            if dir_date == today:
                continue
            try:
                shutil.rmtree(date_dir)
                removed.append(str(date_dir))
                logger.info(
                    "event=snapshot_dir_cleaned path=%s age_days=%d",
                    date_dir,
                    (today - dir_date).days,
                )
            except OSError as exc:
                logger.warning(
                    "event=snapshot_cleanup_error path=%s error=%s",
                    date_dir, exc,
                )

    return removed
