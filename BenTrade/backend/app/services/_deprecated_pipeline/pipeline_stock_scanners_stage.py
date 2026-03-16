"""Stock Scanners Stage Handler — pipeline stage for stock scanner execution.

Runs the 4 stock scanners sequentially, collects candidates, writes
per-scanner and summary artifacts, and returns a standard handler result.

Execution path:
    For each enabled stock scanner in the registry:
        _execute_stock_scanner(scanner_key, scanner_deps, context)

This handler does NOT know about options scanners.

Stage key: "stock_scanners"
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

logger = logging.getLogger("bentrade.pipeline_stock_scanners_stage")

_STAGE_KEY = "stock_scanners"


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


# ── Stock scanner registry ──────────────────────────────────────
# Maps scanner_key → (module_path, class_name)
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

# ── Default timeout for each stock scanner coroutine ────────────
DEFAULT_STOCK_SCANNER_TOTAL_TIMEOUT: float = 240.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execute_stock_scanner(
    scanner_key: str,
    scanner_deps: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single stock scanner via its service class.

    Creates per-scanner async clients, runs the scan coroutine with
    a hard timeout, and returns the raw result dict.
    """
    import asyncio
    import httpx

    spec = _STOCK_SERVICES.get(scanner_key)
    if spec is None:
        raise ValueError(f"No stock scanner service for '{scanner_key}'")

    total_timeout = context.get(
        "stock_scanner_total_timeout", DEFAULT_STOCK_SCANNER_TOTAL_TIMEOUT,
    )

    async def _run() -> dict[str, Any]:
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
        try:
            tradier_client = TradierClient(settings, http_client, cache)
            finnhub_client = FinnhubClient(settings, http_client, cache)
            fred_client = FredClient(settings, http_client, cache)
            polygon_client = PolygonClient(settings, http_client, cache)
            bds = BaseDataService(
                tradier_client=tradier_client,
                finnhub_client=finnhub_client,
                fred_client=fred_client,
                polygon_client=polygon_client,
            )
            mod = __import__(spec[0], fromlist=[spec[1]])
            service = getattr(mod, spec[1])(bds)
            max_candidates = context.get("max_candidates", 30)
            return await asyncio.wait_for(
                service.scan(max_candidates=max_candidates),
                timeout=total_timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Stock scanner '{scanner_key}' timed out after "
                f"{total_timeout}s"
            )
        finally:
            await http_client.aclose()

    return asyncio.run(_run())


def stock_scanners_stage_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Orchestrator-compatible handler for the stock_scanners stage.

    Runs each stock scanner sequentially, writes per-scanner candidate
    artifacts and a stage summary artifact.

    Parameters (via kwargs)
    -----------------------
    scanner_executor : callable | None
        Override the scanner execution function (for testing).
    scanner_deps : dict | None
        Shared dependencies (settings, cache) for scanner client creation.

    Returns
    -------
    dict[str, Any]
        Standard handler result: {outcome, summary_counts, artifacts,
        metadata, error}.
    """
    run_id = run["run_id"]
    executor = kwargs.get("scanner_executor", _execute_stock_scanner)
    scanner_deps = (
        kwargs.get("scanner_deps")
        or run.get("metadata", {}).get("_scanner_deps")
        or _build_scanner_dependencies()
    )

    scanner_summaries: dict[str, dict[str, Any]] = {}
    all_candidates: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    warnings: list[str] = []
    completed_count = 0
    failed_count = 0

    for scanner_key in _STOCK_SERVICES:
        t0 = time.monotonic()
        try:
            logger.info("event=stock_scanner_started scanner=%s", scanner_key)
            raw_result = executor(scanner_key, scanner_deps, {})
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
                "event=stock_scanner_completed scanner=%s candidates=%d elapsed_ms=%d",
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
                "event=stock_scanner_failed scanner=%s error=%s",
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
        artifact_key="stock_scanners_summary",
        artifact_type="scanner_stage_summary",
        data=summary_data,
    )
    put_artifact(artifact_store, summary_record)
    artifacts.append(summary_record)

    outcome = "completed" if completed_count > 0 else "failed"
    error = None
    if outcome == "failed":
        error = build_run_error(
            code="ALL_STOCK_SCANNERS_FAILED",
            message=f"All {failed_count} stock scanners failed",
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
