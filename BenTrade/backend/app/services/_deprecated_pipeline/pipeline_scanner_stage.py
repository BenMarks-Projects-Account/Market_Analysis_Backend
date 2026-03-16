"""Pipeline Scanner Stage v1.0 — bounded-parallel scanner execution.

Implements the ``scanners`` stage handler for the BenTrade pipeline
orchestrator (Step 6).  Discovers configured scanners, executes them
with bounded parallelism, normalizes outputs into candidate records,
writes per-scanner and candidate artifacts, and produces a stage
summary for downstream consumers.

Public API
──────────
    scanner_stage_handler(...)             Orchestrator-compatible handler.
    get_default_scanner_registry(...)      Return the default scanner registry.
    build_scanner_execution_record(...)    Per-scanner execution record.
    build_scanner_stage_summary(...)       Stage-level summary.
    normalize_scanner_candidates(...)      Raw result → pipeline candidates.
    DEFAULT_SCANNER_MAX_WORKERS            Default concurrency limit.

Role boundary
─────────────
This module owns the *scanner execution pass* — scanner discovery,
bounded-parallel execution, per-scanner result normalization,
candidate packaging, artifact writing, and stage summary assembly.

It does NOT:
- run market engines (Step 4's job)
- run model analysis (Step 5's job)
- perform candidate scoring, filtering, or model review (later stages)
- make final trade decisions
- persist to disk / database (artifact store handles that)
"""

from __future__ import annotations

import copy
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.pipeline_artifact_store import (
    build_artifact_record,
    get_artifact_by_key,
    put_artifact,
)
from app.services.pipeline_run_contract import (
    build_log_event,
    build_run_error,
)

logger = logging.getLogger("bentrade.pipeline_scanner_stage")


# ── Finalization checkpoint tracker ─────────────────────────────
# Tracks explicit checkpoint states through the post-processing path.
# Inspectable by tests and reproduction harnesses to pinpoint exactly
# where the handler stalled.

FINALIZATION_STATES = (
    "not_started",
    "results_collected",
    "post_processing_started",
    "artifact_loop_completed",
    "summary_built",
    "summary_artifact_written",
    "outcome_determined",
    "result_built",
    "handler_returning",
    "post_processing_failed",
)


class FinalizationCheckpoint:
    """Thread-safe tracker for scanner-stage finalization progress.

    Each checkpoint records the state name and monotonic timestamp.
    Attach to the run dict so tests / diagnostics can inspect.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: str = "not_started"
        self._history: list[dict[str, Any]] = []
        self._t0: float = time.monotonic()

    def advance(self, state: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._state = state
            self._history.append({
                "state": state,
                "elapsed_ms": int((now - self._t0) * 1000),
            })

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "current_state": self._state,
                "history": list(self._history),
                "elapsed_ms": int((time.monotonic() - self._t0) * 1000),
            }

    # -- Deep-copy safety ---------------------------------------------------
    # threading.Lock cannot be pickled / deep-copied.  The run dict that
    # holds this checkpoint is deep-copied on every event callback, so we
    # must provide a safe copy path.

    def __deepcopy__(self, memo: dict) -> "FinalizationCheckpoint":
        new = FinalizationCheckpoint()
        with self._lock:
            new._state = self._state
            new._history = copy.deepcopy(self._history, memo)
            new._t0 = self._t0
        return new

    def __copy__(self) -> "FinalizationCheckpoint":
        new = FinalizationCheckpoint()
        with self._lock:
            new._state = self._state
            new._history = list(self._history)
            new._t0 = self._t0
        return new

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "scanners"

# ── Concurrency ─────────────────────────────────────────────────
DEFAULT_SCANNER_MAX_WORKERS: int = 3
"""Default concurrency limit for parallel scanner execution.

Override via handler_kwargs['max_workers'].
"""

# ── Default generation cap ─────────────────────────────────────
DEFAULT_GENERATION_CAP: int = 50_000
"""Default per-symbol generation cap passed to V2 scanner families.
Limits combinatorial explosion in Phase B construction loops.
Override via handler_kwargs['generation_cap'].
"""

# ── Scanner status vocabulary ───────────────────────────────────
SCANNER_STATUSES = frozenset({
    "completed",
    "completed_empty",
    "skipped_disabled",
    "skipped_not_selected",
    "failed",
})

# ── Stage outcome thresholds ────────────────────────────────────
_MIN_SCANNERS_FOR_SUCCESS: int = 1
"""Minimum number of scanners that must complete for the stage to
succeed.  0 completions → stage fails."""


# =====================================================================
#  Timestamp helper
# =====================================================================

def _now_iso() -> str:
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


# =====================================================================
#  Scanner registry
# =====================================================================

def _make_scanner_entry(
    scanner_key: str,
    display_name: str,
    scanner_family: str,
    strategy_type: str,
    *,
    enabled: bool = True,
    required: bool = False,
) -> dict[str, Any]:
    """Build one scanner registry entry.

    Parameters
    ----------
    scanner_key : str
        Stable machine identifier (e.g. ``"stock_pullback_swing"``).
    display_name : str
        Human-readable name.
    scanner_family : str
        ``"stock"`` or ``"options"``.
    strategy_type : str
        Specific strategy/setup type.
    enabled : bool
        Whether the scanner is active by default.
    required : bool
        Reserved — whether failure is fatal (future use).
    """
    return {
        "scanner_key": scanner_key,
        "display_name": display_name,
        "scanner_family": scanner_family,
        "strategy_type": strategy_type,
        "enabled": enabled,
        "required": required,
    }


def get_default_scanner_registry() -> dict[str, dict[str, Any]]:
    """Return the default scanner registry.

    Each entry describes a scanner eligible for pipeline execution.
    Scanner keys align with ``SCANNER_METADATA`` from
    ``scanner_candidate_contract.py``.

    Returns
    -------
    dict[str, dict]
        scanner_key → scanner entry.
    """
    return {
        # ── Stock scanners ──────────────────────────────────────
        "stock_pullback_swing": _make_scanner_entry(
            "stock_pullback_swing", "Pullback Swing",
            "stock", "pullback_swing",
        ),
        "stock_momentum_breakout": _make_scanner_entry(
            "stock_momentum_breakout", "Momentum Breakout",
            "stock", "momentum_breakout",
        ),
        "stock_mean_reversion": _make_scanner_entry(
            "stock_mean_reversion", "Mean Reversion",
            "stock", "mean_reversion",
        ),
        "stock_volatility_expansion": _make_scanner_entry(
            "stock_volatility_expansion", "Volatility Expansion",
            "stock", "volatility_expansion",
        ),
        # ── Options scanners ────────────────────────────────────
        #
        # All options scanners now route V2-forward (Prompt 13).
        # New V2 families are added directly here.
        #
        "put_credit_spread": _make_scanner_entry(
            "put_credit_spread", "Put Credit Spread",
            "options", "put_credit_spread",
        ),
        "call_credit_spread": _make_scanner_entry(
            "call_credit_spread", "Call Credit Spread",
            "options", "call_credit_spread",
        ),
        "iron_condor": _make_scanner_entry(
            "iron_condor", "Iron Condor",
            "options", "iron_condor",
        ),
        "butterfly_debit": _make_scanner_entry(
            "butterfly_debit", "Debit Butterfly",
            "options", "butterfly_debit",
        ),
        "iron_butterfly": _make_scanner_entry(
            "iron_butterfly", "Iron Butterfly",
            "options", "iron_butterfly",
        ),
        "put_debit": _make_scanner_entry(
            "put_debit", "Put Debit Spread",
            "options", "put_debit",
        ),
        "call_debit": _make_scanner_entry(
            "call_debit", "Call Debit Spread",
            "options", "call_debit",
        ),
        "calendar_call_spread": _make_scanner_entry(
            "calendar_call_spread", "Calendar Call Spread",
            "options", "calendar_call_spread",
        ),
        "calendar_put_spread": _make_scanner_entry(
            "calendar_put_spread", "Calendar Put Spread",
            "options", "calendar_put_spread",
        ),
        "diagonal_call_spread": _make_scanner_entry(
            "diagonal_call_spread", "Diagonal Call Spread",
            "options", "diagonal_call_spread",
        ),
        "diagonal_put_spread": _make_scanner_entry(
            "diagonal_put_spread", "Diagonal Put Spread",
            "options", "diagonal_put_spread",
        ),
    }


# =====================================================================
#  Per-scanner execution record
# =====================================================================

def build_scanner_execution_record(
    *,
    scanner_key: str,
    scanner_family: str = "",
    strategy_type: str = "",
    status: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    elapsed_ms: int | None = None,
    candidate_count: int = 0,
    raw_result_present: bool = False,
    output_artifact_ref: str | None = None,
    candidate_artifact_ref: str | None = None,
    downstream_usable: bool = False,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a normalized per-scanner execution record.

    Parameters
    ----------
    scanner_key : str
        Stable scanner identifier.
    scanner_family : str
        ``"stock"`` or ``"options"``.
    strategy_type : str
        Specific strategy/setup type.
    status : str
        One of SCANNER_STATUSES.
    started_at / completed_at : str | None
        ISO timestamps.
    elapsed_ms : int | None
        Wall-clock time in ms.
    candidate_count : int
        Number of candidates produced.
    raw_result_present : bool
        Whether the scanner returned a result (even if empty).
    output_artifact_ref : str | None
        artifact_id of the raw scanner output artifact.
    candidate_artifact_ref : str | None
        artifact_id of the normalized candidate artifact.
    downstream_usable : bool
        Whether candidates from this scanner are usable downstream.
    warnings / notes : list[str] | None
        Diagnostic warnings and operational notes.
    error : dict | None
        Structured error if failed.
    """
    return {
        "scanner_key": scanner_key,
        "scanner_family": scanner_family,
        "strategy_type": strategy_type,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_ms": elapsed_ms,
        "candidate_count": candidate_count,
        "raw_result_present": raw_result_present,
        "output_artifact_ref": output_artifact_ref,
        "candidate_artifact_ref": candidate_artifact_ref,
        "downstream_usable": downstream_usable,
        "warnings": warnings or [],
        "notes": notes or [],
        "error": error,
    }


# =====================================================================
#  Scanner selection logic
# =====================================================================

def _select_scanners(
    registry: dict[str, dict[str, Any]],
    *,
    disabled_scanners: set[str] | None = None,
    selected_scanners: set[str] | None = None,
    selected_families: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Determine which scanners should run.

    Returns
    -------
    (eligible, skipped)
        eligible: list of scanner entries that should execute.
        skipped: dict of scanner_key → execution record for skipped.
    """
    disabled = disabled_scanners or set()
    eligible: list[dict[str, Any]] = []
    skipped: dict[str, dict[str, Any]] = {}

    for key, entry in registry.items():
        family = entry.get("scanner_family", "")
        strategy = entry.get("strategy_type", key)

        # Disabled in registry
        if not entry.get("enabled", True):
            skipped[key] = build_scanner_execution_record(
                scanner_key=key,
                scanner_family=family,
                strategy_type=strategy,
                status="skipped_disabled",
            )
            continue

        # Disabled via kwargs
        if key in disabled:
            skipped[key] = build_scanner_execution_record(
                scanner_key=key,
                scanner_family=family,
                strategy_type=strategy,
                status="skipped_disabled",
            )
            continue

        # Family filter
        if selected_families is not None and family not in selected_families:
            skipped[key] = build_scanner_execution_record(
                scanner_key=key,
                scanner_family=family,
                strategy_type=strategy,
                status="skipped_not_selected",
            )
            continue

        # Key filter
        if selected_scanners is not None and key not in selected_scanners:
            skipped[key] = build_scanner_execution_record(
                scanner_key=key,
                scanner_family=family,
                strategy_type=strategy,
                status="skipped_not_selected",
            )
            continue

        eligible.append(entry)

    return eligible, skipped


# =====================================================================
#  Scanner dependency construction
# =====================================================================

def _build_scanner_dependencies() -> dict[str, Any]:
    """Construct *shared* (event-loop-agnostic) scanner dependencies.

    Each scanner runs inside its own ``asyncio.run()`` call which
    creates a fresh event loop.  httpx.AsyncClient (and the API clients
    wrapping it) are bound to the loop where they are created, so they
    **cannot** be shared across scanners.

    This function therefore returns only loop-agnostic objects:
      * settings, cache, results_dir

    Per-scanner httpx + API clients + BaseDataService are created inside
    ``_make_per_scanner_clients()`` which is called within each
    scanner's ``asyncio.run()`` boundary.
    """
    from pathlib import Path

    from app.config import Settings
    from app.utils.cache import TTLCache

    settings = Settings()
    cache = TTLCache()

    # results_dir: same resolution as main.py
    backend_dir = Path(__file__).resolve().parents[1]
    results_dir = backend_dir / "results"

    return {
        "settings": settings,
        "cache": cache,
        "results_dir": results_dir,
    }


def _make_per_scanner_clients(deps: dict[str, Any]) -> dict[str, Any]:
    """Create per-scanner httpx.AsyncClient + API clients + BaseDataService.

    Must be called inside the event loop where the clients will be used
    (i.e. inside the coroutine passed to ``asyncio.run()``).
    """
    import httpx

    from app.clients.finnhub_client import FinnhubClient
    from app.clients.fred_client import FredClient
    from app.clients.polygon_client import PolygonClient
    from app.clients.tradier_client import TradierClient
    from app.services.base_data_service import BaseDataService

    settings = deps["settings"]
    cache = deps["cache"]
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(45.0, connect=5.0, read=30.0),
    )

    tradier_client = TradierClient(settings, http_client, cache)
    finnhub_client = FinnhubClient(settings, http_client, cache)
    fred_client = FredClient(settings, http_client, cache)
    polygon_client = PolygonClient(settings, http_client, cache)

    base_data_service = BaseDataService(
        tradier_client=tradier_client,
        finnhub_client=finnhub_client,
        fred_client=fred_client,
        polygon_client=polygon_client,
    )

    return {
        "http_client": http_client,
        "base_data_service": base_data_service,
    }


# =====================================================================
#  Isolated scanner executors (Prompt 13 — V2-forward)
# =====================================================================

_STOCK_SERVICES: dict[str, tuple[str, str]] = {
    "stock_pullback_swing": (
        "app.services.pullback_swing_service", "PullbackSwingService",
    ),
    "stock_momentum_breakout": (
        "app.services.momentum_breakout_service", "MomentumBreakoutService",
    ),
    "stock_mean_reversion": (
        "app.services.mean_reversion_service", "MeanReversionService",
    ),
    "stock_volatility_expansion": (
        "app.services.volatility_expansion_service", "VolatilityExpansionService",
    ),
}

def _execute_stock_scanner(
    scanner_key: str,
    scanner_deps: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute a stock scanner via its service class."""
    import asyncio

    spec = _STOCK_SERVICES.get(scanner_key)
    if spec is None:
        raise ValueError(f"No stock scanner service for '{scanner_key}'")

    async def _run() -> dict[str, Any]:
        clients = _make_per_scanner_clients(scanner_deps)
        try:
            bds = clients["base_data_service"]
            mod = __import__(spec[0], fromlist=[spec[1]])
            service = getattr(mod, spec[1])(bds)
            max_candidates = context.get("max_candidates", 30)
            return await service.scan(max_candidates=max_candidates)
        finally:
            http = clients.get("http_client")
            if http:
                await http.aclose()

    return asyncio.run(_run())


def _execute_v2_options_scanner(
    scanner_key: str,
    scanner_deps: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute an options scanner via V2 pipeline (primary path).

    This is the V2-forward execution path for all implemented options
    families.  For each target symbol, fetches the options chain from
    Tradier and runs the V2 scanner.
    """
    import asyncio

    from app.services.scanner_v2.migration import execute_v2_scanner

    symbols = context.get("symbols") or [
        "SPY", "QQQ", "IWM", "DIA",
    ]

    async def _run() -> dict[str, Any]:
        clients = _make_per_scanner_clients(scanner_deps)
        try:
            tc = clients["base_data_service"].tradier_client
            all_candidates: list[dict[str, Any]] = []
            total_constructed = 0
            total_passed = 0

            for sym in symbols:
                try:
                    quote = await tc.get_quote(sym)
                    price = float(
                        quote.get("last") or quote.get("close") or 0
                    )
                    if not price:
                        logger.warning(
                            "V2 %s/%s: no underlying price, skipped",
                            scanner_key, sym,
                        )
                        continue

                    expirations = await tc.get_expirations(sym)
                    if not expirations:
                        continue

                    merged_contracts: list[dict[str, Any]] = []
                    for exp in expirations:
                        merged_contracts.extend(
                            await tc.get_chain(sym, exp)
                        )
                    if not merged_contracts:
                        continue

                    chain = {
                        "options": {"option": merged_contracts},
                    }
                    # Pass generation_cap and other context to V2 scanner
                    v2_context = {
                        "generation_cap": context.get(
                            "generation_cap", DEFAULT_GENERATION_CAP,
                        ),
                    }
                    result = execute_v2_scanner(
                        scanner_key,
                        symbol=sym,
                        chain=chain,
                        underlying_price=price,
                        context=v2_context,
                    )
                    all_candidates.extend(
                        result.get("candidates", [])
                    )
                    total_constructed += result.get(
                        "candidate_count", 0
                    )
                    total_passed += result.get("accepted_count", 0)
                except Exception as exc:
                    logger.warning(
                        "V2 scanner %s/%s failed: %s: %s",
                        scanner_key, sym,
                        type(exc).__name__, exc,
                    )

            return {
                "candidates": all_candidates,
                "candidate_count": total_constructed,
                "accepted_count": total_passed,
            }
        finally:
            http = clients.get("http_client")
            if http:
                await http.aclose()

    return asyncio.run(_run())


# ── Legacy options executor (RETIREMENT TARGET: Prompt 15) ──────
#
# This function is the ONLY remaining integration with legacy
# StrategyService.  When all families are validated on V2, this
# function and its _LEGACY_STRATEGY_MAP can be deleted entirely.

_LEGACY_STRATEGY_MAP: dict[str, tuple[str, dict[str, Any]]] = {
    "put_credit_spread":  ("credit_spread",  {}),
    "call_credit_spread": ("credit_spread",  {}),
    "iron_condor":        ("iron_condor",    {}),
    "butterfly_debit":    ("butterflies",    {}),
    "put_debit":          ("debit_spreads",  {"direction": "put"}),
    "call_debit":         ("debit_spreads",  {"direction": "call"}),
}


def _execute_legacy_options_scanner(
    scanner_key: str,
    strategy_type: str,
    scanner_deps: dict[str, Any],
    context: dict[str, Any],
    results_dir: Any,
) -> dict[str, Any]:
    """Execute an options scanner via legacy StrategyService.

    RETIREMENT TARGET: This function and ``_LEGACY_STRATEGY_MAP``
    should be deleted once all V2 families are validated and legacy
    scanner code is retired (Prompt 15).
    """
    import asyncio

    from app.services.strategy_service import StrategyService

    mapped = _LEGACY_STRATEGY_MAP.get(strategy_type)
    if mapped is None:
        raise ValueError(
            f"No legacy strategy plugin mapping for '{strategy_type}' "
            f"(scanner_key='{scanner_key}'). "
            f"This scanner has no V2 implementation and no legacy mapping."
        )
    plugin_id, extra_payload = mapped

    if results_dir is None:
        from pathlib import Path
        results_dir = Path(__file__).resolve().parents[1] / "results"

    symbols = context.get("symbols") or [
        "SPY", "QQQ", "IWM", "DIA",
    ]
    preset = context.get("preset", "balanced")

    async def _run() -> dict[str, Any]:
        clients = _make_per_scanner_clients(scanner_deps)
        try:
            bds = clients["base_data_service"]
            service = StrategyService(
                base_data_service=bds,
                results_dir=results_dir,
            )
            payload: dict[str, Any] = {
                "symbols": symbols,
                "preset": preset,
                **extra_payload,
            }
            return await service.generate(plugin_id, payload)
        finally:
            http = clients.get("http_client")
            if http:
                await http.aclose()

    return asyncio.run(_run())


# =====================================================================
#  Default scanner executor
# =====================================================================

def _default_scanner_executor(
    scanner_key: str,
    scanner_entry: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Default scanner executor — dispatches to real scanner services.

    Routing (Prompt 13 — V2-forward):
    - Stock scanners: lazy-import service class → ``scan()``.
    - Options scanners with V2 implementation: V2 path (default).
    - Options scanners without V2: legacy ``StrategyService`` fallback.

    Each scanner runs inside ``asyncio.run()`` which creates its own
    event loop.  httpx.AsyncClient and the API clients wrapping it are
    event-loop-bound, so we construct per-scanner clients inside the
    coroutine via ``_make_per_scanner_clients()``.

    Override via kwargs['scanner_executor'] for testing.
    """
    family = scanner_entry.get("scanner_family", "")
    strategy_type = scanner_entry.get("strategy_type", scanner_key)

    # Shared (loop-agnostic) deps from context
    scanner_deps = context.get("_scanner_deps") or {}
    results_dir = context.get("results_dir")

    if family == "stock":
        return _execute_stock_scanner(scanner_key, scanner_deps, context)

    if family == "options":
        from app.services.scanner_v2.migration import should_run_v2

        # ── V2 path (default for all implemented families) ──────
        if should_run_v2(scanner_key):
            result = _execute_v2_options_scanner(
                scanner_key, scanner_deps, context,
            )
            result["_execution_path"] = "v2"
            return result

        # ── Legacy fallback (RETIREMENT TARGET: Prompt 15) ──────
        #
        # This path only runs for scanner_keys that have NO V2
        # implementation, or that have been explicitly overridden
        # to v1 via _SCANNER_VERSION_OVERRIDES in migration.py.
        #
        # As of Prompt 13, all four options families (vertical_spreads,
        # iron_condors, butterflies, calendars) are V2-implemented.
        # This legacy path should only execute if an override forces
        # a key to v1 for emergency rollback.
        logger.info(
            "Scanner '%s' routing to legacy StrategyService "
            "(no V2 implementation or v1 override active)",
            scanner_key,
        )
        result = _execute_legacy_options_scanner(
            scanner_key, strategy_type, scanner_deps, context,
            results_dir,
        )
        result["_execution_path"] = "legacy"
        return result

    raise ValueError(
        f"No executor for scanner family '{family}' (key='{scanner_key}')"
    )


# =====================================================================
#  Candidate normalization
# =====================================================================

def normalize_scanner_candidates(
    scanner_key: str,
    scanner_entry: dict[str, Any],
    raw_result: dict[str, Any],
    *,
    run_id: str,
    source_artifact_ref: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize raw scanner candidates into pipeline candidate records.

    For each raw candidate:
    1. If it already has a ``normalized`` key, uses that as base.
    2. Otherwise, calls ``scanner_candidate_contract.normalize_candidate_output()``.
    3. Wraps the result with pipeline lineage fields.

    Parameters
    ----------
    scanner_key : str
        Stable scanner identifier.
    scanner_entry : dict
        Scanner registry entry.
    raw_result : dict
        The raw scanner result containing a ``candidates`` list.
    run_id : str
        Pipeline run identifier.
    source_artifact_ref : str | None
        artifact_id of the raw scanner output artifact.

    Returns
    -------
    (candidates, warnings)
        candidates: list of pipeline candidate records.
        warnings: list of diagnostic messages.
    """
    raw_candidates = raw_result.get("candidates", [])
    if not isinstance(raw_candidates, list):
        return [], [
            f"'candidates' is not a list: {type(raw_candidates).__name__}",
        ]

    scanner_family = scanner_entry.get("scanner_family", "unknown")
    strategy_type = scanner_entry.get("strategy_type", scanner_key)

    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []

    for i, raw in enumerate(raw_candidates):
        if not isinstance(raw, dict):
            warnings.append(f"Candidate {i} is not a dict, skipped")
            continue

        try:
            # Prefer pre-normalized data from the scanner itself
            existing = raw.get("normalized")
            if isinstance(existing, dict) and existing:
                base = dict(existing)
            else:
                from app.services.scanner_candidate_contract import (
                    normalize_candidate_output,
                )
                base = normalize_candidate_output(scanner_key, raw)

            # Ensure candidate_id
            if not base.get("candidate_id"):
                base["candidate_id"] = (
                    raw.get("trade_key")
                    or raw.get("candidate_id")
                    or f"{scanner_key}_{i}"
                )

            # Ensure strategy_type and scanner_family — required by
            # downstream stages (policy checks these as required fields)
            base.setdefault("strategy_type", strategy_type)
            base.setdefault("scanner_family", scanner_family)
            base.setdefault("scanner_key", scanner_key)

            # Pipeline lineage fields
            base["source_scanner_artifact_ref"] = source_artifact_ref
            base["run_id"] = run_id
            base["stage_key"] = _STAGE_KEY
            base["normalization_status"] = "normalized"
            base["downstream_usable"] = True

            candidates.append(base)

        except Exception as exc:
            warnings.append(
                f"Candidate {i} normalization failed: "
                f"{type(exc).__name__}: {exc}"
            )
            # Raw passthrough — preserve traceability
            cid = str(
                raw.get("candidate_id")
                or raw.get("trade_key")
                or f"{scanner_key}_{i}"
            )
            candidates.append({
                "candidate_id": cid,
                "scanner_key": scanner_key,
                "scanner_family": scanner_family,
                "strategy_type": strategy_type,
                "symbol": str(raw.get("symbol", "UNKNOWN")).upper(),
                "raw_candidate": raw,
                "source_scanner_artifact_ref": source_artifact_ref,
                "run_id": run_id,
                "stage_key": _STAGE_KEY,
                "normalization_status": "raw_passthrough",
                "downstream_usable": False,
                "normalization_error": f"{type(exc).__name__}: {exc}",
            })

    return candidates, warnings


# =====================================================================
#  Single scanner execution
# =====================================================================

def _run_single_scanner(
    scanner_key: str,
    scanner_entry: dict[str, Any],
    context: dict[str, Any],
    scanner_executor: Callable[
        [str, dict[str, Any], dict[str, Any]], dict[str, Any]
    ],
    run_id: str,
    event_emitter: Callable[..., None] | None = None,
    liveness_tracker: "ScannerLivenessTracker | None" = None,
) -> dict[str, Any]:
    """Execute a single scanner and capture timing/results.

    Returns
    -------
    dict[str, Any]
        {record, raw_result, candidates, candidate_warnings}
    """
    family = scanner_entry.get("scanner_family", "")
    strategy_type = scanner_entry.get("strategy_type", scanner_key)
    started_at = _now_iso()
    t0 = time.monotonic()

    if event_emitter:
        event_emitter(
            "scanner_started",
            scanner_key=scanner_key,
            message=f"Scanner '{scanner_key}' starting",
            metadata={"scanner_family": family},
        )

    # Heartbeat right before the (potentially long) executor call so
    # the liveness tracker and dashboard see fresh activity.
    if liveness_tracker:
        liveness_tracker.heartbeat(scanner_key)
    if event_emitter:
        event_emitter(
            "scanner_executing",
            scanner_key=scanner_key,
            message=f"Scanner '{scanner_key}' executing",
            metadata={"scanner_family": family},
        )

    try:
        raw_result = scanner_executor(scanner_key, scanner_entry, context)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        completed_at = _now_iso()

        if not isinstance(raw_result, dict):
            raw_result = {"candidates": [], "_raw_non_dict": True}

        raw_candidates = raw_result.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raw_candidates = []

        candidate_count = len(raw_candidates)
        status = "completed" if candidate_count > 0 else "completed_empty"

        # Execution path marker (V2-forward, Prompt 13)
        execution_path = raw_result.get("_execution_path", "unknown")

        # Clear liveness BEFORE emitting scanner_completed so the
        # event callback's deepcopy of run["_scanner_liveness"]
        # captures the cleared state.  Without this ordering the
        # monitor snapshot shows the scanner still in-flight even
        # though scanner_completed already fired.
        if liveness_tracker:
            liveness_tracker.mark_completed(scanner_key)

        if event_emitter:
            event_emitter(
                "scanner_completed",
                scanner_key=scanner_key,
                message=(
                    f"Scanner '{scanner_key}' completed in {elapsed_ms}ms "
                    f"({candidate_count} candidates, path={execution_path})"
                ),
                metadata={
                    "scanner_family": family,
                    "elapsed_ms": elapsed_ms,
                    "candidate_count": candidate_count,
                    "execution_path": execution_path,
                },
            )

        record = build_scanner_execution_record(
                scanner_key=scanner_key,
                scanner_family=family,
                strategy_type=strategy_type,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                elapsed_ms=elapsed_ms,
                candidate_count=candidate_count,
                raw_result_present=True,
                downstream_usable=True,
            )
        record["execution_path"] = execution_path

        return {
            "record": record,
            "raw_result": raw_result,
        }

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        completed_at = _now_iso()
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)

        logger.error(
            "Scanner '%s' failed: %s: %s",
            scanner_key, type(exc).__name__, exc, exc_info=True,
        )

        # Clear liveness BEFORE emitting scanner_failed (same
        # ordering rationale as scanner_completed above).
        if liveness_tracker:
            liveness_tracker.mark_failed(scanner_key)

        if event_emitter:
            event_emitter(
                "scanner_failed",
                scanner_key=scanner_key,
                level="error",
                message=(
                    f"Scanner '{scanner_key}' failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                metadata={"scanner_family": family},
            )

        return {
            "record": build_scanner_execution_record(
                scanner_key=scanner_key,
                scanner_family=family,
                strategy_type=strategy_type,
                status="failed",
                started_at=started_at,
                completed_at=completed_at,
                elapsed_ms=elapsed_ms,
                error=build_run_error(
                    code="SCANNER_EXCEPTION",
                    message=f"{type(exc).__name__}: {exc}",
                    source=f"scanner.{scanner_key}",
                    detail={"traceback": tb},
                ),
            ),
            "raw_result": None,
        }


# =====================================================================
#  Scanner liveness tracker
# =====================================================================

class ScannerLivenessTracker:
    """Thread-safe tracker for in-flight scanner liveness.

    Exposes a snapshot of currently-running scanners with elapsed time,
    completed/failed/timed-out counts, and per-scanner diagnostics.
    Attached to the run dict so the monitoring API can surface it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight: dict[str, float] = {}  # scanner_key → start monotonic
        self._completed: list[str] = []
        self._failed: list[str] = []
        self._timed_out: list[str] = []
        self._cap_hit: list[str] = []
        self._last_update: float = time.monotonic()

    def mark_started(self, scanner_key: str) -> None:
        with self._lock:
            self._in_flight[scanner_key] = time.monotonic()
            self._last_update = time.monotonic()

    def mark_completed(self, scanner_key: str) -> None:
        with self._lock:
            self._in_flight.pop(scanner_key, None)
            self._completed.append(scanner_key)
            self._last_update = time.monotonic()

    def mark_failed(self, scanner_key: str) -> None:
        with self._lock:
            self._in_flight.pop(scanner_key, None)
            self._failed.append(scanner_key)
            self._last_update = time.monotonic()

    def mark_timed_out(self, scanner_key: str) -> None:
        with self._lock:
            self._in_flight.pop(scanner_key, None)
            self._timed_out.append(scanner_key)
            self._last_update = time.monotonic()

    def mark_cap_hit(self, scanner_key: str) -> None:
        with self._lock:
            self._cap_hit.append(scanner_key)

    def heartbeat(self, scanner_key: str) -> None:
        """Update last_update timestamp without changing scanner state.

        Called periodically during long-running scanners to signal
        that the stage is still alive.
        """
        with self._lock:
            self._last_update = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        """Return a point-in-time liveness snapshot."""
        now = time.monotonic()
        with self._lock:
            in_flight = {
                k: round((now - start) * 1000)
                for k, start in self._in_flight.items()
            }
            return {
                "in_flight_scanners": in_flight,
                "in_flight_count": len(in_flight),
                "completed": list(self._completed),
                "failed": list(self._failed),
                "timed_out": list(self._timed_out),
                "cap_hit": list(self._cap_hit),
                "last_update_ms_ago": round((now - self._last_update) * 1000),
            }

    # -- Deep-copy safety ---------------------------------------------------
    # threading.Lock cannot be pickled / deep-copied.  The run dict that
    # holds this tracker is deep-copied on every event callback, so we
    # must provide a safe copy path.

    def __deepcopy__(self, memo: dict) -> "ScannerLivenessTracker":
        new = ScannerLivenessTracker()
        with self._lock:
            new._in_flight = copy.deepcopy(self._in_flight, memo)
            new._completed = list(self._completed)
            new._failed = list(self._failed)
            new._timed_out = list(self._timed_out)
            new._cap_hit = list(self._cap_hit)
            new._last_update = self._last_update
        return new

    def __copy__(self) -> "ScannerLivenessTracker":
        new = ScannerLivenessTracker()
        with self._lock:
            new._in_flight = dict(self._in_flight)
            new._completed = list(self._completed)
            new._failed = list(self._failed)
            new._timed_out = list(self._timed_out)
            new._cap_hit = list(self._cap_hit)
            new._last_update = self._last_update
        return new

    def reconcile(self, completed_keys: set[str]) -> set[str]:
        """Force-clear any in-flight entries whose keys are in *completed_keys*.

        Returns the set of scanner keys that were stale (had results but
        were still listed in _in_flight).  An empty set means no stale
        entries were found.
        """
        with self._lock:
            stale = set(self._in_flight.keys()) & completed_keys
            for key in stale:
                self._in_flight.pop(key, None)
                # Don't double-append to _completed/_failed — just clear
                # the in-flight entry so the snapshot is accurate.
                if key not in self._completed and key not in self._failed:
                    self._completed.append(key)
            if stale:
                self._last_update = time.monotonic()
            return stale


# =====================================================================
#  Bounded parallel execution
# =====================================================================


def _execute_scanners_parallel(
    work_items: list[dict[str, Any]],
    scanner_executor: Callable[
        [str, dict[str, Any], dict[str, Any]], dict[str, Any]
    ],
    context: dict[str, Any],
    run_id: str,
    max_workers: int,
    event_emitter: Callable[..., None] | None = None,
    *,
    liveness_tracker: ScannerLivenessTracker | None = None,
) -> dict[str, dict[str, Any]]:
    """Run scanners with bounded parallelism.

    Parameters
    ----------
    work_items : list
        Each item is a scanner entry dict with ``scanner_key``.
    scanner_executor : callable
        (scanner_key, scanner_entry, context) → dict
    context : dict
        Execution context passed to each scanner.
    run_id : str
        Pipeline run identifier.
    max_workers : int
        Concurrency limit.
    event_emitter : callable | None
        Event callback.
    liveness_tracker : ScannerLivenessTracker | None
        Optional tracker for real-time liveness monitoring.

    Returns
    -------
    dict[str, dict[str, Any]]
        scanner_key → {record, raw_result}
    """
    results: dict[str, dict[str, Any]] = {}
    if not work_items:
        return results

    actual_workers = min(max_workers, len(work_items))
    tracker = liveness_tracker

    # ── Pool lifecycle ──────────────────────────────────────────
    # We manage the pool explicitly (NOT via ``with`` context
    # manager) because ``ThreadPoolExecutor.__exit__`` calls
    # ``pool.shutdown(wait=True)`` which blocks until every worker
    # thread fully exits.  Even after all futures are done, worker
    # threads may be stuck in asyncio event-loop cleanup (every V2
    # scanner runs ``asyncio.run()`` which calls
    # ``loop.shutdown_default_executor()`` → inner
    # ``shutdown(wait=True)``).  This caused the scanner-stage to
    # hang indefinitely after all scanner_completed events fired.
    #
    # Fix: collect all results via ``as_completed``, then shut down
    # with ``wait=False`` so lingering thread cleanup cannot block
    # stage finalization.
    pool = ThreadPoolExecutor(max_workers=actual_workers)
    try:
        futures = {}
        for item in work_items:
            key = item["scanner_key"]
            if tracker:
                tracker.mark_started(key)
            fut = pool.submit(
                _run_single_scanner,
                key,
                item,
                context,
                scanner_executor,
                run_id,
                event_emitter,
                tracker,
            )
            futures[fut] = item

        # Wait for all futures — no timeout.  Every scanner must
        # complete (or raise) before the stage proceeds.
        for future in as_completed(futures):
            item = futures[future]
            scanner_key = item["scanner_key"]
            try:
                result = future.result()
                results[scanner_key] = result
            except Exception as exc:
                # future.result() itself raised — _run_single_scanner
                # catches all exceptions so this is truly unexpected.
                logger.error(
                    "Unexpected executor error for scanner '%s': %s",
                    scanner_key, exc, exc_info=True,
                )
                results[scanner_key] = {
                    "record": build_scanner_execution_record(
                        scanner_key=scanner_key,
                        scanner_family=item.get("scanner_family", ""),
                        strategy_type=item.get("strategy_type", scanner_key),
                        status="failed",
                        error=build_run_error(
                            code="SCANNER_EXECUTOR_ERROR",
                            message=(
                                f"Executor error: "
                                f"{type(exc).__name__}: {exc}"
                            ),
                            source=f"scanner.{scanner_key}",
                        ),
                    ),
                    "raw_result": None,
                }
                if tracker:
                    tracker.mark_failed(scanner_key)
    finally:
        # Shut down WITHOUT waiting for worker threads to exit.
        # All results have been collected above; lingering threads
        # are doing asyncio event-loop cleanup and will finish on
        # their own.
        #
        # IMPORTANT: Do NOT pass cancel_futures=True.  In Python
        # 3.14+ the cancel path acquires _shutdown_lock and drains
        # the work queue; if any worker thread is simultaneously in
        # asyncio.run() cleanup (shutdown_default_executor →
        # inner shutdown(wait=True)), the two shutdown codepaths
        # can deadlock on the queue interaction.  Since all futures
        # are already done (collected via as_completed), there is
        # nothing to cancel.
        try:
            logger.debug("Scanner pool shutdown(wait=False) — %d results collected", len(results))
            pool.shutdown(wait=False)
        except Exception as _shutdown_exc:
            logger.warning(
                "pool.shutdown raised %s: %s (ignored — results already collected)",
                type(_shutdown_exc).__name__, _shutdown_exc,
            )

    # ── Reconciliation safety net ───────────────────────────────
    # If any scanner remains in _in_flight despite having a result,
    # force-clear it and log the mismatch.  This prevents stale
    # in-flight entries from polluting the liveness snapshot.
    if tracker:
        stale = tracker.reconcile(set(results.keys()))
        if stale:
            logger.warning(
                "Liveness reconciliation cleared %d stale in-flight "
                "scanner(s) after parallel execution: %s",
                len(stale), sorted(stale),
            )

    return results


# =====================================================================
#  Stage summary builder
# =====================================================================

def build_scanner_stage_summary(
    execution_records: dict[str, dict[str, Any]],
    skipped_records: dict[str, dict[str, Any]],
    *,
    candidate_counts: dict[str, int] | None = None,
    usable_candidate_counts: dict[str, int] | None = None,
    artifact_refs: dict[str, str | None] | None = None,
    candidate_artifact_refs: dict[str, str | None] | None = None,
    candidate_index: dict[str, list[str]] | None = None,
    scanner_diagnostics: dict[str, dict[str, Any]] | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    """Build the scanner stage summary artifact payload.

    Parameters
    ----------
    execution_records : dict
        scanner_key → execution record for scanners that ran.
    skipped_records : dict
        scanner_key → execution record for scanners that were skipped.
    candidate_counts : dict | None
        scanner_key → candidate count.
    usable_candidate_counts : dict | None
        scanner_key → usable candidate count.
    artifact_refs : dict | None
        scanner_key → raw output artifact_id.
    candidate_artifact_refs : dict | None
        scanner_key → candidate artifact_id.
    candidate_index : dict | None
        scanner_key → list of candidate_ids.
    scanner_diagnostics : dict | None
        scanner_key → diagnostics dict with filter_trace, phase_counts,
        reject_reason_counts, candidate_count, accepted_count from V2.
    elapsed_ms : int | None
        Total wall-clock time for the stage.
    """
    cand_counts = candidate_counts or {}
    usable_counts = usable_candidate_counts or {}
    art_refs = artifact_refs or {}
    cand_art_refs = candidate_artifact_refs or {}
    cand_idx = candidate_index or {}
    sc_diag = scanner_diagnostics or {}

    scanners_completed: list[str] = []
    scanners_completed_empty: list[str] = []
    scanners_failed: list[str] = []
    skipped_by_reason: dict[str, list[str]] = {}
    scanner_summaries: dict[str, dict[str, Any]] = {}

    # Process executed scanners
    for key, rec in execution_records.items():
        status = rec.get("status", "failed")

        if status == "completed":
            scanners_completed.append(key)
        elif status == "completed_empty":
            scanners_completed_empty.append(key)
        else:
            scanners_failed.append(key)

        diag = sc_diag.get(key)
        scanner_summaries[key] = {
            "status": status,
            "scanner_family": rec.get("scanner_family", ""),
            "strategy_type": rec.get("strategy_type", key),
            "execution_path": rec.get("execution_path", "unknown"),
            "elapsed_ms": rec.get("elapsed_ms"),
            "candidate_count": cand_counts.get(key, rec.get("candidate_count", 0)),
            "usable_candidate_count": usable_counts.get(key, 0),
            "output_artifact_ref": art_refs.get(key),
            "candidate_artifact_ref": cand_art_refs.get(key),
            "candidate_ids": cand_idx.get(key, []),
            "downstream_usable": rec.get("downstream_usable", False),
            "diagnostics": diag,
        }

    # Process skipped scanners
    for key, rec in skipped_records.items():
        status = rec.get("status", "skipped_disabled")
        bucket = skipped_by_reason.setdefault(status, [])
        bucket.append(key)

        scanner_summaries[key] = {
            "status": status,
            "scanner_family": rec.get("scanner_family", ""),
            "strategy_type": rec.get("strategy_type", key),
            "elapsed_ms": None,
            "candidate_count": 0,
            "usable_candidate_count": 0,
            "output_artifact_ref": None,
            "candidate_artifact_ref": None,
            "candidate_ids": [],
            "downstream_usable": False,
        }

    total_considered = len(execution_records) + len(skipped_records)
    total_run = len(execution_records)
    completed_count = len(scanners_completed) + len(scanners_completed_empty)
    failed_count = len(scanners_failed)
    skipped_count = len(skipped_records)

    total_candidates = sum(cand_counts.values())
    total_usable = sum(usable_counts.values())

    # All candidate IDs across scanners
    all_candidate_ids = [
        cid for ids in cand_idx.values() for cid in ids
    ]

    # Stage-level status rollup
    # completed_empty is not a failure, just no candidates
    if total_run == 0:
        stage_status = "no_eligible_scanners"
    elif completed_count == 0:
        stage_status = "failed"
    elif failed_count > 0:
        stage_status = "degraded"
    elif total_candidates == 0:
        stage_status = "no_candidates"
    else:
        stage_status = "success"

    # Degraded reasons
    degraded_reasons: list[str] = []
    if scanners_failed:
        degraded_reasons.append(f"scanners_failed: {scanners_failed}")

    # V2-forward routing summary (Prompt 13)
    v2_scanners = [
        k for k, s in scanner_summaries.items()
        if s.get("execution_path") == "v2"
    ]
    legacy_scanners = [
        k for k, s in scanner_summaries.items()
        if s.get("execution_path") == "legacy"
    ]

    return {
        "stage_key": _STAGE_KEY,
        "stage_status": stage_status,
        "total_considered": total_considered,
        "total_run": total_run,
        "scanners_completed": scanners_completed,
        "scanners_completed_empty": scanners_completed_empty,
        "scanners_failed": scanners_failed,
        "scanners_skipped": skipped_by_reason,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "total_candidates": total_candidates,
        "total_usable_candidates": total_usable,
        "all_candidate_ids": all_candidate_ids,
        "artifact_refs": dict(art_refs),
        "candidate_artifact_refs": dict(cand_art_refs),
        "candidate_index": dict(cand_idx),
        "degraded_reasons": degraded_reasons,
        "scanner_summaries": scanner_summaries,
        # V2-forward routing summary (Prompt 13)
        "routing_summary": {
            "v2_scanners": v2_scanners,
            "legacy_scanners": legacy_scanners,
            "v2_count": len(v2_scanners),
            "legacy_count": len(legacy_scanners),
        },
        "elapsed_ms": elapsed_ms,
        "generated_at": _now_iso(),
    }


# =====================================================================
#  Artifact writing helpers
# =====================================================================

def _write_scanner_output_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    scanner_key: str,
    raw_result: dict[str, Any],
    record: dict[str, Any],
) -> str:
    """Write a raw per-scanner output artifact. Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key=f"scanner_{scanner_key}",
        artifact_type="scanner_output",
        data=raw_result,
        summary={
            "scanner_key": scanner_key,
            "status": record.get("status"),
            "candidate_count": record.get("candidate_count", 0),
        },
        metadata={
            "scanner_key": scanner_key,
            "scanner_family": record.get("scanner_family", ""),
            "strategy_type": record.get("strategy_type", scanner_key),
            "elapsed_ms": record.get("elapsed_ms"),
        },
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_candidate_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    scanner_key: str,
    candidates: list[dict[str, Any]],
    source_artifact_ref: str | None = None,
) -> str:
    """Write a normalized candidate artifact for one scanner.

    Groups all candidates from a single scanner into one artifact.
    Returns artifact_id.
    """
    usable = [c for c in candidates if c.get("downstream_usable", False)]
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key=f"candidates_{scanner_key}",
        artifact_type="normalized_candidate",
        data=candidates,
        summary={
            "scanner_key": scanner_key,
            "total_candidates": len(candidates),
            "usable_candidates": len(usable),
            "candidate_ids": [c.get("candidate_id", "") for c in candidates],
        },
        metadata={
            "scanner_key": scanner_key,
            "source_artifact_ref": source_artifact_ref,
        },
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_scanner_stage_summary_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    summary: dict[str, Any],
) -> str:
    """Write the scanner stage summary artifact. Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="scanner_stage_summary",
        artifact_type="scanner_stage_summary",
        data=summary,
        summary={
            "stage_status": summary.get("stage_status"),
            "completed_count": summary.get("completed_count"),
            "failed_count": summary.get("failed_count"),
            "skipped_count": summary.get("skipped_count"),
            "total_candidates": summary.get("total_candidates"),
            "total_usable_candidates": summary.get("total_usable_candidates"),
        },
        metadata={
            "scanner_keys": list(summary.get("scanner_summaries", {}).keys()),
        },
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


# =====================================================================
#  Event emission helper (within stage)
# =====================================================================

def _make_event_emitter(
    run: dict[str, Any],
    event_callback: Callable[..., None] | None,
) -> Callable[..., None] | None:
    """Build a thread-safe event emitter closure for scanner events.

    Returns None if no callback is configured.
    """
    if event_callback is None:
        return None

    run_id = run["run_id"]

    def _emit(
        event_type: str,
        scanner_key: str = "",
        level: str = "info",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        merged_meta = {"scanner_key": scanner_key}
        if metadata:
            merged_meta.update(metadata)

        event = build_log_event(
            run_id=run_id,
            stage_key=_STAGE_KEY,
            event_type=event_type,
            level=level,
            message=message,
            metadata=merged_meta,
        )

        # Update run log counts (thread-safe enough for counters)
        counts = run.get("log_event_counts", {})
        counts["total"] = counts.get("total", 0) + 1
        by_level = counts.get("by_level", {})
        by_level[level] = by_level.get(level, 0) + 1

        try:
            event_callback(event)
        except Exception:
            logger.warning(
                "Event callback raised during scanner '%s' event '%s'",
                scanner_key, event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Stage handler (orchestrator-compatible)
# =====================================================================

def scanner_stage_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Orchestrator-compatible handler for the scanners stage.

    Sequence:
    1. Resolve scanner registry and parameters.
    2. Optionally retrieve Step 4 market context.
    3. Select which scanners should run.
    4. Execute eligible scanners (bounded parallel).
    5. For each scanner result:
       a. Write raw scanner_output artifact.
       b. Normalize candidates.
       c. Write normalized_candidate artifact.
    6. Build stage summary.
    7. Write stage summary artifact.
    8. Determine stage outcome.
    9. Return handler result dict.

    Handler kwargs (passed via orchestrator handler_kwargs)
    ──────────────────────────────────────────────────────
    scanner_registry : dict | None
        Override the default scanner registry.
    scanner_executor : callable | None
        Override scanner execution.
        Signature: (scanner_key, scanner_entry, context) → dict
    max_workers : int
        Concurrency limit.
    event_callback : callable | None
        Event callback.
    disabled_scanners : set[str] | None
        Scanner keys to skip.
    selected_scanners : set[str] | None
        If provided, only these scanner keys run.
    selected_families : set[str] | None
        If provided, only scanners in these families run.
    scanner_results_override : dict | None
        Pre-computed scanner results keyed by scanner_key (testing/replay).
    symbols : list[str] | None
        Target symbols for scanners.
    preset : str | None
        Scanner preset (e.g. "balanced", "strict").
    max_candidates : int | None
        Per-scanner candidate limit.

    Returns
    -------
    dict[str, Any]
        Handler result compatible with Step 3 orchestrator:
        { outcome, summary_counts, artifacts, metadata, error }
    """
    t0 = time.monotonic()
    run_id = run["run_id"]

    # ── Finalization checkpoint ───────────────────────────────────
    finalization = FinalizationCheckpoint()
    run["_finalization_checkpoint"] = finalization

    # ── 1. Resolve parameters ───────────────────────────────────
    registry = kwargs.get("scanner_registry") or get_default_scanner_registry()
    scanner_executor = kwargs.get("scanner_executor", _default_scanner_executor)
    max_workers = kwargs.get("max_workers", DEFAULT_SCANNER_MAX_WORKERS)
    event_callback = kwargs.get("event_callback")
    event_emitter = _make_event_emitter(run, event_callback)
    if event_emitter is not None:
        event_emitter("handler_entered", "", message="scanner_stage_handler entered")
    disabled_scanners: set[str] = set(kwargs.get("disabled_scanners") or [])
    selected_scanners: set[str] | None = (
        set(kwargs["selected_scanners"]) if kwargs.get("selected_scanners") else None
    )
    selected_families: set[str] | None = (
        set(kwargs["selected_families"]) if kwargs.get("selected_families") else None
    )
    scanner_results_override: dict[str, Any] | None = kwargs.get(
        "scanner_results_override",
    )
    generation_cap = kwargs.get("generation_cap", DEFAULT_GENERATION_CAP)

    # Liveness tracker — attached to run dict for monitoring API
    liveness_tracker = ScannerLivenessTracker()
    run.setdefault("_scanner_liveness", {})
    run["_scanner_liveness"] = liveness_tracker

    # Scanner execution context — pass shared (loop-agnostic) deps;
    # per-scanner clients are created inside _default_scanner_executor.
    scanner_deps = _build_scanner_dependencies()
    context: dict[str, Any] = {
        "run_id": run_id,
        "symbols": kwargs.get("symbols"),
        "preset": kwargs.get("preset"),
        "max_candidates": kwargs.get("max_candidates"),
        "generation_cap": generation_cap,
        "_scanner_deps": scanner_deps,
        "results_dir": scanner_deps["results_dir"],
    }

    # ── 2. Optionally retrieve Step 4 market context ────────────
    market_summary_art = get_artifact_by_key(
        artifact_store, "market_data", "market_stage_summary",
    )
    if market_summary_art is not None:
        context["market_summary"] = market_summary_art.get("data")

    # ── 3. Select scanners ──────────────────────────────────────
    eligible, skipped_records = _select_scanners(
        registry,
        disabled_scanners=disabled_scanners,
        selected_scanners=selected_scanners,
        selected_families=selected_families,
    )

    # ── Handle zero eligible scanners ───────────────────────────
    if not eligible and scanner_results_override is None:
        return _build_no_scanners_result(
            artifact_store, run_id, skipped_records, t0,
        )

    # ── 4. Execute scanners + 5-9. Post-processing ───────────────
    # The ENTIRE execution + finalization is wrapped in try/except
    # so the stage ALWAYS returns a result, even if parallel execution
    # hangs/raises or post-processing fails.  This guarantees the
    # orchestrator receives a terminal outcome and can emit
    # stage_completed/stage_failed — the pipeline NEVER stays stuck
    # in "running" after scanner work finishes.
    scanner_results: dict[str, dict[str, Any]] = {}
    _t_post = t0  # default; updated after parallel execution

    try:
        if scanner_results_override is not None:
            # Test/replay mode: use pre-supplied results
            for entry in eligible:
                key = entry["scanner_key"]
                if key in scanner_results_override:
                    raw = scanner_results_override[key]
                    raw_candidates = raw.get("candidates", []) if isinstance(raw, dict) else []
                    scanner_results[key] = {
                        "record": build_scanner_execution_record(
                            scanner_key=key,
                            scanner_family=entry.get("scanner_family", ""),
                            strategy_type=entry.get("strategy_type", key),
                            status="completed" if raw_candidates else "completed_empty",
                            started_at=_now_iso(),
                            completed_at=_now_iso(),
                            elapsed_ms=0,
                            candidate_count=len(raw_candidates),
                            raw_result_present=True,
                            downstream_usable=bool(raw_candidates),
                        ),
                        "raw_result": raw,
                    }
                else:
                    skipped_records[key] = build_scanner_execution_record(
                        scanner_key=key,
                        scanner_family=entry.get("scanner_family", ""),
                        strategy_type=entry.get("strategy_type", key),
                        status="skipped_not_selected",
                    )
            # Handle override keys for scanners not in eligible list
            for key, override_result in scanner_results_override.items():
                if key not in scanner_results and key not in skipped_records:
                    raw_candidates = (
                        override_result.get("candidates", [])
                        if isinstance(override_result, dict) else []
                    )
                    scanner_results[key] = {
                        "record": build_scanner_execution_record(
                            scanner_key=key,
                            status="completed" if raw_candidates else "completed_empty",
                            started_at=_now_iso(),
                            completed_at=_now_iso(),
                            elapsed_ms=0,
                            candidate_count=len(raw_candidates),
                            raw_result_present=True,
                            downstream_usable=bool(raw_candidates),
                        ),
                        "raw_result": override_result,
                    }
        else:
            finalization.advance("parallel_execution_starting")
            logger.info("CHECKPOINT_0: parallel_execution_starting  eligible=%d", len(eligible))
            scanner_results = _execute_scanners_parallel(
                eligible, scanner_executor, context,
                run_id, max_workers, event_emitter,
                liveness_tracker=liveness_tracker,
            )

        # ── Finalization diagnostics ────────────────────────────
        _t_post = time.monotonic()
        _parallel_ms = int((_t_post - t0) * 1000)
        finalization.advance("results_collected")
        logger.info(
            "CHECKPOINT_1: all_scanners_done  results=%d  parallel_ms=%d  "
            "keys=%s",
            len(scanner_results), _parallel_ms,
            sorted(scanner_results.keys()),
        )

        # ── 5. Process results: artifacts + candidate normalization
        finalization.advance("post_processing_started")
        logger.info("CHECKPOINT_2: post_processing_started")
        all_artifact_ids: list[str] = []
        candidate_counts: dict[str, int] = {}
        usable_candidate_counts: dict[str, int] = {}
        raw_artifact_refs: dict[str, str | None] = {}
        candidate_artifact_refs: dict[str, str | None] = {}
        candidate_index: dict[str, list[str]] = {}

        _t_loop_start = time.monotonic()
        for key, entry in scanner_results.items():
            rec = entry.get("record", {})
            raw_result = entry.get("raw_result")

            # 5a. Write raw scanner output artifact
            if raw_result is not None:
                art_id = _write_scanner_output_artifact(
                    artifact_store, run_id, key, raw_result, rec,
                )
                rec["output_artifact_ref"] = art_id
                raw_artifact_refs[key] = art_id
                all_artifact_ids.append(art_id)
            else:
                raw_artifact_refs[key] = None

            # 5b. Normalize candidates
            if raw_result is not None and rec.get("status") in (
                "completed", "completed_empty",
            ):
                scanner_entry = registry.get(key, {"scanner_key": key})
                candidates, norm_warnings = normalize_scanner_candidates(
                    key, scanner_entry, raw_result,
                    run_id=run_id,
                    source_artifact_ref=raw_artifact_refs.get(key),
                )

                if norm_warnings:
                    rec.setdefault("warnings", []).extend(norm_warnings)

                usable = [c for c in candidates if c.get("downstream_usable", False)]
                candidate_counts[key] = len(candidates)
                usable_candidate_counts[key] = len(usable)
                candidate_index[key] = [
                    c.get("candidate_id", "") for c in candidates
                ]

                # 5c. Write candidate artifact
                if candidates:
                    cand_art_id = _write_candidate_artifact(
                        artifact_store, run_id, key, candidates,
                        source_artifact_ref=raw_artifact_refs.get(key),
                    )
                    rec["candidate_artifact_ref"] = cand_art_id
                    candidate_artifact_refs[key] = cand_art_id
                    all_artifact_ids.append(cand_art_id)
                else:
                    candidate_artifact_refs[key] = None
            else:
                candidate_counts[key] = 0
                usable_candidate_counts[key] = 0
                candidate_artifact_refs[key] = None

        _t_loop_end = time.monotonic()
        finalization.advance("artifact_loop_completed")
        logger.info(
            "CHECKPOINT_3: artifact_loop_completed  scanners=%d  "
            "loop_ms=%d",
            len(scanner_results),
            int((_t_loop_end - _t_loop_start) * 1000),
        )

        # ── 6-7. Build and write stage summary ──────────────────
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        execution_recs = {
            k: entry.get("record", {}) for k, entry in scanner_results.items()
        }

        # Extract per-scanner diagnostics from raw results
        scanner_diag: dict[str, dict[str, Any]] = {}
        for key, entry in scanner_results.items():
            raw = entry.get("raw_result")
            if not isinstance(raw, dict):
                continue
            ft = raw.get("filter_trace")
            diag_entry: dict[str, Any] = {}
            if isinstance(ft, dict):
                diag_entry["stage_counts"] = ft.get("stage_counts", [])
                diag_entry["rejection_reason_counts"] = ft.get(
                    "rejection_reason_counts", {},
                )
                diag_entry["data_quality_counts"] = ft.get(
                    "data_quality_counts", {},
                )
            diag_entry["candidate_count"] = raw.get("candidate_count", 0)
            diag_entry["accepted_count"] = raw.get("accepted_count", 0)
            scanner_diag[key] = diag_entry

        liveness_snapshot = liveness_tracker.snapshot()

        if liveness_snapshot["in_flight_count"] > 0:
            logger.warning(
                "Scanner stage finalization: %d scanner(s) still in "
                "liveness in_flight after all results collected: %s",
                liveness_snapshot["in_flight_count"],
                sorted(liveness_snapshot["in_flight_scanners"].keys()),
            )

        logger.info("CHECKPOINT_4: summary_build_started")
        summary = build_scanner_stage_summary(
            execution_recs,
            skipped_records,
            candidate_counts=candidate_counts,
            usable_candidate_counts=usable_candidate_counts,
            artifact_refs=raw_artifact_refs,
            candidate_artifact_refs=candidate_artifact_refs,
            candidate_index=candidate_index,
            scanner_diagnostics=scanner_diag,
            elapsed_ms=elapsed_ms,
        )
        summary["liveness_snapshot"] = liveness_snapshot
        finalization.advance("summary_built")
        logger.info("CHECKPOINT_5: summary_built")
        summary_art_id = _write_scanner_stage_summary_artifact(
            artifact_store, run_id, summary,
        )
        all_artifact_ids.append(summary_art_id)
        finalization.advance("summary_artifact_written")
        logger.info(
            "CHECKPOINT_6: summary_artifact_written  artifact=%s",
            summary_art_id,
        )

        # ── 8. Determine stage outcome ──────────────────────────
        completed_count = summary["completed_count"]
        failed_count = summary["failed_count"]
        total_run = summary["total_run"]

        if total_run > 0 and completed_count == 0:
            outcome = "failed"
            error = build_run_error(
                code="ALL_SCANNERS_FAILED",
                message=f"All {total_run} scanners failed",
                source=_STAGE_KEY,
                detail={"failed_count": failed_count},
            )
        else:
            outcome = "completed"
            error = None

        finalization.advance("outcome_determined")
        logger.info(
            "CHECKPOINT_7: outcome_determined  outcome=%s  "
            "completed=%d  failed=%d  total=%d",
            outcome, completed_count, failed_count, total_run,
        )

        # ── 9. Return handler result ────────────────────────────
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _finalization_ms = int((time.monotonic() - _t_post) * 1000)
        logger.info(
            "CHECKPOINT_8: result_built  outcome=%s  total_ms=%d  "
            "post_processing_ms=%d",
            outcome, elapsed_ms, _finalization_ms,
        )
        finalization.advance("result_built")
        if _finalization_ms > 30_000:
            logger.warning(
                "Scanner stage finalization took %dms (>30s threshold)",
                _finalization_ms,
            )
        result = {
            "outcome": outcome,
            "summary_counts": {
                "scanners_run": total_run,
                "scanners_completed": completed_count,
                "scanners_failed": failed_count,
                "scanners_skipped": summary["skipped_count"],
                "total_candidates": summary["total_candidates"],
                "total_usable_candidates": summary["total_usable_candidates"],
            },
            "artifacts": [],  # artifacts already written directly
            "metadata": {
                "stage_summary_artifact_id": summary_art_id,
                "scanner_artifact_ids": {
                    k: v for k, v in raw_artifact_refs.items() if v
                },
                "candidate_artifact_ids": {
                    k: v for k, v in candidate_artifact_refs.items() if v
                },
                "stage_status": summary["stage_status"],
                "elapsed_ms": elapsed_ms,
                "scanner_records": execution_recs,
                "degraded_reasons": summary.get("degraded_reasons", []),
                "liveness_snapshot": liveness_snapshot,
                "finalization_checkpoint": finalization.snapshot(),
                "finalization_duration_ms": _finalization_ms,
            },
            "error": error,
        }
        finalization.advance("handler_returning")
        logger.info("CHECKPOINT_9: handler_returning  outcome=%s", outcome)
        return result

    except Exception as post_exc:
        # ── Guaranteed finalization ──────────────────────────────
        # If parallel execution OR post-processing fails, the stage
        # MUST still return a result so the orchestrator can emit
        # stage_completed or stage_failed.  The pipeline MUST NOT
        # remain stuck in "running" forever.
        finalization.advance("post_processing_failed")
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _finalization_ms = int((time.monotonic() - _t_post) * 1000)
        logger.error(
            "Scanner stage execution/post-processing failed: %s: %s",
            type(post_exc).__name__, post_exc, exc_info=True,
        )

        # Count what we can from scanner_results
        exec_records = {
            k: entry.get("record", {})
            for k, entry in scanner_results.items()
        }
        n_completed = sum(
            1 for r in exec_records.values()
            if r.get("status") in ("completed", "completed_empty")
        )
        n_failed = sum(
            1 for r in exec_records.values()
            if r.get("status") == "failed"
        )
        # Compute total_candidates from whatever results we DO have
        _rescue_total = sum(
            len(entry.get("raw_result", {}).get("candidates", []))
            for entry in scanner_results.values()
            if isinstance(entry.get("raw_result"), dict)
        )

        return {
            "outcome": "failed" if n_completed == 0 else "completed",
            "summary_counts": {
                "scanners_run": len(exec_records),
                "scanners_completed": n_completed,
                "scanners_failed": n_failed,
                "scanners_skipped": len(skipped_records),
                "total_candidates": _rescue_total,
                "total_usable_candidates": 0,
            },
            "artifacts": [],
            "metadata": {
                "stage_status": "degraded" if n_completed > 0 else "failed",
                "elapsed_ms": elapsed_ms,
                "scanner_records": exec_records,
                "degraded_reasons": [
                    f"Execution/post-processing error: {type(post_exc).__name__}: {post_exc}",
                ],
                "liveness_snapshot": liveness_tracker.snapshot(),
                "finalization_checkpoint": finalization.snapshot(),
                "finalization_duration_ms": _finalization_ms,
            },
            "error": build_run_error(
                code="SCANNER_STAGE_POST_PROCESSING_ERROR",
                message=f"Execution/post-processing failed: {type(post_exc).__name__}: {post_exc}",
                source=_STAGE_KEY,
            ),
        }


# =====================================================================
#  Fallback result builders
# =====================================================================

def _build_no_scanners_result(
    artifact_store: dict[str, Any],
    run_id: str,
    skipped_records: dict[str, dict[str, Any]],
    t0: float,
) -> dict[str, Any]:
    """Build result when zero scanners are eligible."""
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    summary = build_scanner_stage_summary(
        {}, skipped_records, elapsed_ms=elapsed_ms,
    )
    summary_art_id = _write_scanner_stage_summary_artifact(
        artifact_store, run_id, summary,
    )

    return {
        "outcome": "completed",
        "summary_counts": {
            "scanners_run": 0,
            "scanners_completed": 0,
            "scanners_failed": 0,
            "scanners_skipped": len(skipped_records),
            "total_candidates": 0,
            "total_usable_candidates": 0,
        },
        "artifacts": [],
        "metadata": {
            "stage_summary_artifact_id": summary_art_id,
            "stage_status": summary["stage_status"],
            "elapsed_ms": elapsed_ms,
        },
        "error": None,
    }
