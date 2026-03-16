"""Options Scanners Stage Handler — pipeline stage for V2 options scanner execution.

Runs the 11 V2 options scanners against pre-fetched chain data, collects
candidates, writes per-scanner and summary artifacts, and returns a
standard handler result.

Execution path:
    1. Pre-fetch options chains for all target symbols (once)
    2. For each enabled scanner key:
           run scanner against cached chain data per symbol

This handler does NOT know about stock scanners.
Legacy StrategyService is not used — all options scanners route through V2.

Stage key: "options_scanners"
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.services.pipeline_artifact_store import (
    build_artifact_record,
    put_artifact,
)
from app.services.pipeline_run_contract import build_run_error

logger = logging.getLogger("bentrade.pipeline_options_scanners_stage")

_STAGE_KEY = "options_scanners"


def _build_scanner_dependencies() -> dict[str, Any]:
    """Construct shared (event-loop-agnostic) scanner dependencies.

    Returns settings, cache, and results_dir.  Per-scanner httpx +
    API clients are created inside each scanner's asyncio.run().
    """
    from pathlib import Path

    from app.config import Settings
    from app.utils.cache import TTLCache

    settings = Settings()
    cache = TTLCache()
    backend_dir = Path(__file__).resolve().parents[1]
    results_dir = backend_dir / "results"
    return {"settings": settings, "cache": cache, "results_dir": results_dir}


# ── Default generation cap for V2 scanner families ──────────────
DEFAULT_GENERATION_CAP: int = 50_000

# ── Default symbols to scan ─────────────────────────────────────
DEFAULT_SYMBOLS: list[str] = ["SPY", "QQQ", "IWM", "DIA"]


# ── V2 options scanner keys (all implemented families) ──────────
OPTIONS_SCANNER_KEYS: list[str] = [
    "put_credit_spread",
    "call_credit_spread",
    "iron_condor",
    "butterfly_debit",
    "iron_butterfly",
    "put_debit",
    "call_debit",
    "calendar_call_spread",
    "calendar_put_spread",
    "diagonal_call_spread",
    "diagonal_put_spread",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prefetch_chains(
    scanner_deps: dict[str, Any],
    symbols: list[str],
) -> dict[str, dict[str, Any]]:
    """Pre-fetch options chain data for all symbols (once).

    Returns symbol → {price, chain} mapping.  Fetches quote,
    expirations, and full chains from Tradier for each symbol.
    Symbols that fail are logged and omitted from the result.
    """
    import asyncio
    import httpx

    async def _run() -> dict[str, dict[str, Any]]:
        from app.clients.finnhub_client import FinnhubClient
        from app.clients.fred_client import FredClient
        from app.clients.polygon_client import PolygonClient
        from app.clients.tradier_client import TradierClient
        from app.services.base_data_service import BaseDataService

        settings = scanner_deps["settings"]
        cache = scanner_deps["cache"]
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=5.0, read=30.0),
        )
        chain_data: dict[str, dict[str, Any]] = {}
        try:
            tradier_client = TradierClient(settings, http_client, cache)
            finnhub_client = FinnhubClient(settings, http_client, cache)
            fred_client = FredClient(settings, http_client, cache)
            polygon_client = PolygonClient(settings, http_client, cache)
            BaseDataService(
                tradier_client=tradier_client,
                finnhub_client=finnhub_client,
                fred_client=fred_client,
                polygon_client=polygon_client,
            )
            tc = tradier_client

            for sym in symbols:
                try:
                    quote = await tc.get_quote(sym)
                    price = float(
                        quote.get("last") or quote.get("close") or 0
                    )
                    if not price:
                        logger.warning(
                            "prefetch %s: no underlying price, skipped", sym,
                        )
                        continue

                    expirations = await tc.get_expirations(sym)
                    if not expirations:
                        logger.warning(
                            "prefetch %s: no expirations, skipped", sym,
                        )
                        continue

                    merged_contracts: list[dict[str, Any]] = []
                    for exp in expirations:
                        merged_contracts.extend(
                            await tc.get_chain(sym, exp)
                        )
                    if not merged_contracts:
                        logger.warning(
                            "prefetch %s: empty chain, skipped", sym,
                        )
                        continue

                    chain_data[sym] = {
                        "price": price,
                        "chain": {"options": {"option": merged_contracts}},
                    }
                    logger.info(
                        "event=chain_prefetched symbol=%s contracts=%d",
                        sym, len(merged_contracts),
                    )
                except Exception as exc:
                    logger.warning(
                        "prefetch %s failed: %s: %s",
                        sym, type(exc).__name__, exc,
                    )
        finally:
            await http_client.aclose()
        return chain_data

    return asyncio.run(_run())


def _execute_v2_scanner_with_chains(
    scanner_key: str,
    chain_data: dict[str, dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single V2 scanner against pre-fetched chain data.

    No network I/O — operates entirely on cached chain payloads.
    """
    from app.services.scanner_v2.migration import execute_v2_scanner

    all_candidates: list[dict[str, Any]] = []
    total_constructed = 0
    total_passed = 0

    for sym, entry in chain_data.items():
        try:
            v2_context = {
                "generation_cap": context.get(
                    "generation_cap", DEFAULT_GENERATION_CAP,
                ),
            }
            result = execute_v2_scanner(
                scanner_key,
                symbol=sym,
                chain=entry["chain"],
                underlying_price=entry["price"],
                context=v2_context,
            )
            all_candidates.extend(result.get("candidates", []))
            total_constructed += result.get("candidate_count", 0)
            total_passed += result.get("accepted_count", 0)
        except Exception as exc:
            logger.warning(
                "V2 scanner %s/%s failed: %s: %s",
                scanner_key, sym, type(exc).__name__, exc,
            )

    return {
        "candidates": all_candidates,
        "candidate_count": total_constructed,
        "accepted_count": total_passed,
    }


def _execute_v2_options_scanner(
    scanner_key: str,
    scanner_deps: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single V2 options scanner across all target symbols.

    Legacy entry point — fetches chains per-scanner.  Retained for
    test compatibility; the stage handler uses _prefetch_chains +
    _execute_v2_scanner_with_chains instead.
    """
    symbols = context.get("symbols") or DEFAULT_SYMBOLS
    chain_data = _prefetch_chains(scanner_deps, symbols)
    return _execute_v2_scanner_with_chains(scanner_key, chain_data, context)


def options_scanners_stage_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Orchestrator-compatible handler for the options_scanners stage.

    Runs each V2 options scanner sequentially, writes per-scanner
    candidate artifacts and a stage summary artifact.

    Optimization: chains are pre-fetched once for all symbols, then
    each scanner runs against the cached chain data (no redundant I/O).

    Parameters (via kwargs)
    -----------------------
    scanner_executor : callable | None
        Override the scanner execution function (for testing).
        Signature: (scanner_key, scanner_deps_or_chains, context) -> dict
    scanner_deps : dict | None
        Shared dependencies (settings, cache) for scanner client creation.
    scanner_keys : list[str] | None
        Override the list of scanner keys to run.

    Returns
    -------
    dict[str, Any]
        Standard handler result: {outcome, summary_counts, artifacts,
        metadata, error}.
    """
    run_id = run["run_id"]
    executor = kwargs.get("scanner_executor")
    scanner_deps = (
        kwargs.get("scanner_deps")
        or run.get("metadata", {}).get("_scanner_deps")
        or _build_scanner_dependencies()
    )
    scanner_keys = kwargs.get("scanner_keys") or OPTIONS_SCANNER_KEYS
    context = kwargs.get("scanner_context") or {}

    scanner_summaries: dict[str, dict[str, Any]] = {}
    all_candidates: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    warnings: list[str] = []
    completed_count = 0
    failed_count = 0

    # ── Pre-fetch chain data once for all symbols ───────────────
    # When a test supplies scanner_executor, skip prefetch (the
    # executor manages its own data).
    chain_data: dict[str, dict[str, Any]] | None = None
    if executor is None:
        symbols = context.get("symbols") or DEFAULT_SYMBOLS
        t_fetch = time.monotonic()
        try:
            chain_data = _prefetch_chains(scanner_deps, symbols)
            fetch_ms = int((time.monotonic() - t_fetch) * 1000)
            logger.info(
                "event=chains_prefetched symbols=%d fetch_ms=%d",
                len(chain_data), fetch_ms,
            )
        except Exception as exc:
            logger.error(
                "event=chain_prefetch_failed error=%s", exc, exc_info=True,
            )
            return {
                "outcome": "failed",
                "summary_counts": {
                    "total_candidates": 0,
                    "completed_scanners": 0,
                    "failed_scanners": len(scanner_keys),
                },
                "artifacts": [],
                "metadata": {"warnings": [f"Chain prefetch failed: {exc}"]},
                "error": build_run_error(
                    code="CHAIN_PREFETCH_FAILED",
                    message=f"Failed to fetch options chains: {type(exc).__name__}: {exc}",
                    source=_STAGE_KEY,
                ),
            }
        if not chain_data:
            return {
                "outcome": "failed",
                "summary_counts": {
                    "total_candidates": 0,
                    "completed_scanners": 0,
                    "failed_scanners": 0,
                },
                "artifacts": [],
                "metadata": {"warnings": ["No chain data available for any symbol"]},
                "error": build_run_error(
                    code="NO_CHAIN_DATA",
                    message="No options chain data available for any target symbol",
                    source=_STAGE_KEY,
                ),
            }

    # ── Run each scanner against pre-fetched chains ─────────────
    for scanner_key in scanner_keys:
        t0 = time.monotonic()
        try:
            logger.info("event=options_scanner_started scanner=%s", scanner_key)

            if executor is not None:
                # Test executor path (legacy signature)
                raw_result = executor(scanner_key, scanner_deps, context)
            else:
                # Production path: run scanner against cached chains
                raw_result = _execute_v2_scanner_with_chains(
                    scanner_key, chain_data, context,
                )

            elapsed_ms = int((time.monotonic() - t0) * 1000)

            candidates = raw_result.get("candidates", [])
            if not isinstance(candidates, list):
                candidates = []

            # Write per-scanner candidate artifact
            cand_record = build_artifact_record(
                run_id=run_id,
                stage_key=_STAGE_KEY,
                artifact_key=f"candidates_{scanner_key}",
                artifact_type="scanner_output",
                data=candidates,
                summary={"candidate_count": len(candidates)},
            )
            put_artifact(artifact_store, cand_record)
            artifacts.append(cand_record)

            all_candidates.extend(candidates)
            scanner_summaries[scanner_key] = {
                "status": "completed",
                "candidate_count": len(candidates),
                "elapsed_ms": elapsed_ms,
                "downstream_usable": len(candidates) > 0,
            }
            completed_count += 1
            logger.info(
                "event=options_scanner_completed scanner=%s candidates=%d elapsed_ms=%d",
                scanner_key, len(candidates), elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            failed_count += 1
            scanner_summaries[scanner_key] = {
                "status": "failed",
                "candidate_count": 0,
                "elapsed_ms": elapsed_ms,
                "downstream_usable": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            warnings.append(f"Scanner '{scanner_key}' failed: {exc}")
            logger.warning(
                "event=options_scanner_failed scanner=%s error=%s",
                scanner_key, exc,
            )

    # Write stage summary artifact
    summary_data = {
        "scanner_summaries": scanner_summaries,
        "total_candidates": len(all_candidates),
        "completed_count": completed_count,
        "failed_count": failed_count,
        "candidate_artifact_refs": {
            k: f"candidates_{k}"
            for k, v in scanner_summaries.items()
            if v.get("downstream_usable")
        },
    }
    summary_record = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="options_scanners_summary",
        artifact_type="scanner_stage_summary",
        data=summary_data,
    )
    put_artifact(artifact_store, summary_record)
    artifacts.append(summary_record)

    outcome = "completed" if completed_count > 0 else "failed"
    error = None
    if outcome == "failed":
        error = build_run_error(
            code="ALL_OPTIONS_SCANNERS_FAILED",
            message=f"All {failed_count} options scanners failed",
            source=_STAGE_KEY,
        )

    return {
        "outcome": outcome,
        "summary_counts": {
            "total_candidates": len(all_candidates),
            "completed_scanners": completed_count,
            "failed_scanners": failed_count,
        },
        "artifacts": artifacts,
        "metadata": {"warnings": warnings},
        "error": error,
    }
