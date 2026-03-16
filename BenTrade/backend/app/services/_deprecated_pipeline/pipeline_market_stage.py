"""Pipeline Market-Picture Stage v1.0 — bounded-parallel engine execution.

Implements the ``market_data`` stage handler for the BenTrade pipeline
orchestrator (Step 4).  Runs the 6 market-picture engines with bounded
parallelism, normalizes per-engine results, writes artifacts, and
produces a stage summary for downstream consumers.

Public API
──────────
    market_stage_handler(...)        Orchestrator-compatible stage handler.
    get_engine_registry(...)         Return the engine registry.
    build_engine_result(...)         Build a per-engine execution record.
    build_stage_summary(...)         Build the market stage summary.
    DEFAULT_MAX_WORKERS              Default concurrency limit.

Role boundary
─────────────
This module owns the *market-picture execution pass* — engine
selection, bounded-parallel execution, per-engine result
normalization, artifact writing, and stage summary assembly.

It does NOT:
- execute scanners or per-candidate logic
- run model-analysis (that is Step 5: market_model_analysis)
- wire live UI streaming beyond the event seam
- persist to disk / database (artifact store handles that)
- make final trade decisions
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.pipeline_artifact_store import (
    build_artifact_record,
    put_artifact,
)
from app.services.pipeline_run_contract import (
    build_log_event,
    build_run_error,
)

logger = logging.getLogger("bentrade.pipeline_market_stage")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "market_data"

# ── Failure classification codes ────────────────────────────────
FAILURE_CATEGORIES = frozenset({
    "missing_configuration",
    "authentication_error",
    "network_error",
    "timeout",
    "rate_limited",
    "provider_error",
    "construction_error",
    "unexpected_error",
})

# ── Engine-to-API-key mapping ──────────────────────────────────
# required_keys: engine cannot produce meaningful data without them.
# optional_keys: engine can still run (possibly degraded) without them.
ENGINE_CREDENTIAL_MAP: dict[str, dict[str, list[str]]] = {
    "breadth_participation": {
        "required_keys": ["TRADIER_TOKEN"],
        "optional_keys": [],
    },
    "volatility_options": {
        "required_keys": ["TRADIER_TOKEN"],
        "optional_keys": ["FRED_KEY", "FINNHUB_KEY"],
    },
    "liquidity_financial_conditions": {
        "required_keys": ["FRED_KEY"],
        "optional_keys": ["TRADIER_TOKEN", "FINNHUB_KEY"],
    },
    "cross_asset_macro": {
        "required_keys": ["FRED_KEY"],
        "optional_keys": ["TRADIER_TOKEN", "FINNHUB_KEY"],
    },
    "flows_positioning": {
        "required_keys": [],
        "optional_keys": ["TRADIER_TOKEN", "FINNHUB_KEY", "FRED_KEY"],
    },
    "news_sentiment": {
        "required_keys": [],
        "optional_keys": ["FRED_KEY", "FINNHUB_KEY", "POLYGON_API_KEY"],
    },
}

# ── Concurrency ─────────────────────────────────────────────────
DEFAULT_MAX_WORKERS: int = 3
"""Default concurrency limit for parallel engine execution.

Set conservatively — most engines hit overlapping external APIs
(Tradier, FRED, Finnhub).  Override via handler_kwargs['max_workers'].
"""

# ── Engine status vocabulary ────────────────────────────────────
ENGINE_STATUSES = frozenset({
    "success",
    "failed",
    "skipped",
    "unavailable",
    "degraded",
})

# ── Stage outcome thresholds ────────────────────────────────────
# If fewer than this many engines succeed, the stage fails.
_MIN_ENGINES_FOR_SUCCESS: int = 1
"""Minimum number of engines that must succeed for the stage to not
be a total failure.  If 0 engines succeed → stage fails.
If some succeed but not all → stage completes with degraded metadata.
"""


# =====================================================================
#  Engine registry
# =====================================================================

def _now_iso() -> str:
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _make_engine_entry(
    engine_key: str,
    display_name: str,
    *,
    enabled: bool = True,
    required: bool = False,
    service_factory: Callable[[], Any] | None = None,
    run_method: str = "",
) -> dict[str, Any]:
    """Build one engine registry entry.

    Parameters
    ----------
    engine_key : str
        Stable machine identifier matching ENGINE_METADATA keys.
    display_name : str
        Human-readable name.
    enabled : bool
        Whether this engine is eligible for execution.
    required : bool
        If True, failure of this engine forces the entire stage to fail.
    service_factory : callable | None
        Zero-arg callable returning the service instance.
        None means the engine is structurally registered but unavailable.
    run_method : str
        Name of the async method on the service (e.g. "get_breadth_analysis").
    """
    return {
        "engine_key": engine_key,
        "display_name": display_name,
        "enabled": enabled,
        "required": required,
        "service_factory": service_factory,
        "run_method": run_method,
    }


def _build_market_dependencies() -> dict[str, Any]:
    """Construct the shared dependency graph for market engine services.

    Built once per ``_default_engine_registry()`` call.  Individual
    factory closures create per-call ``httpx.AsyncClient`` instances
    because each engine runs in its own event loop thread.

    Dependency tree
    ───────────────
    settings          ← Settings()                  (zero-arg, reads env)
    cache             ← TTLCache()                  (zero-arg)
    --- per factory call (each engine gets its own event loop) ---
    http_client       ← httpx.AsyncClient()
    tradier_client    ← TradierClient(settings, http_client, cache)
    fred_client       ← FredClient(settings, http_client, cache)
    finnhub_client    ← FinnhubClient(settings, http_client, cache)
    market_context    ← MarketContextService(fred_client, finnhub_client, cache, tradier_client)
    """
    from app.config import Settings
    from app.utils.cache import TTLCache

    return {
        "settings": Settings(),
        "cache": TTLCache(),
    }


def _make_per_engine_clients(deps: dict[str, Any]) -> dict[str, Any]:
    """Create per-engine-call client instances.

    Each engine runs in its own thread with its own event loop, so
    httpx.AsyncClient and the API clients built on it must be
    constructed fresh per engine invocation.

    Input: shared deps from ``_build_market_dependencies()``.
    Output: dict with tradier_client, fred_client, finnhub_client,
            market_context_service, http_client, plus the shared deps.
    """
    import httpx
    from app.clients.tradier_client import TradierClient
    from app.clients.fred_client import FredClient
    from app.clients.finnhub_client import FinnhubClient
    from app.services.market_context_service import MarketContextService

    settings = deps["settings"]
    cache = deps["cache"]
    http_client = httpx.AsyncClient()

    tradier_client = TradierClient(settings, http_client, cache)
    fred_client = FredClient(settings, http_client, cache)
    finnhub_client = FinnhubClient(settings, http_client, cache)
    market_context_service = MarketContextService(
        fred_client, finnhub_client, cache, tradier_client=tradier_client,
    )

    return {
        "settings": settings,
        "cache": cache,
        "http_client": http_client,
        "tradier_client": tradier_client,
        "fred_client": fred_client,
        "finnhub_client": finnhub_client,
        "market_context_service": market_context_service,
    }


# =====================================================================
#  Preflight config validation
# =====================================================================

def check_engine_config_eligibility(
    settings: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Check which engines are eligible to run given current API keys.

    Returns a dict keyed by engine_key:
        {
            "eligible": bool,
            "missing_required": ["FRED_KEY", ...],
            "missing_optional": ["FINNHUB_KEY", ...],
        }

    An engine is eligible if all its required_keys are non-empty.
    """
    if settings is None:
        from app.config import Settings
        settings = Settings()

    result: dict[str, dict[str, Any]] = {}
    for engine_key, cred_spec in ENGINE_CREDENTIAL_MAP.items():
        missing_req = [
            k for k in cred_spec["required_keys"]
            if not getattr(settings, k, "")
        ]
        missing_opt = [
            k for k in cred_spec["optional_keys"]
            if not getattr(settings, k, "")
        ]
        result[engine_key] = {
            "eligible": len(missing_req) == 0,
            "missing_required": missing_req,
            "missing_optional": missing_opt,
        }
    return result


# =====================================================================
#  Failure classification
# =====================================================================

def classify_engine_failure(exc: BaseException) -> str:
    """Return a normalized failure category for an engine exception.

    Categories (from FAILURE_CATEGORIES):
        missing_configuration  — missing API key or env var
        authentication_error   — 401/403 from provider
        network_error          — connection refused, DNS, reset
        timeout                — request or operation timed out
        rate_limited           — 429 from provider
        provider_error         — non-auth HTTP error from provider
        construction_error     — service/provider instantiation failure
        unexpected_error       — anything else
    """
    msg = str(exc).lower()
    exc_type = type(exc).__name__

    # Authentication / authorization
    if any(code in msg for code in ("401", "403", "unauthorized", "forbidden")):
        return "authentication_error"

    # Rate limiting
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return "rate_limited"

    # Timeout
    if "timeout" in msg or exc_type in ("TimeoutError", "ReadTimeout", "ConnectTimeout"):
        return "timeout"

    # Network
    if any(s in msg for s in ("connection", "dns", "reset", "refused", "unreachable")):
        return "network_error"
    if exc_type in ("ConnectError", "NetworkError", "ConnectionError"):
        return "network_error"

    # Missing config (checked before network since it can also be a ValueError)
    if any(s in msg for s in ("api key", "api_key", "token", "credential", "not configured")):
        return "missing_configuration"

    # Construction failures
    if exc_type in ("TypeError", "AttributeError") and any(
        s in msg for s in ("__init__", "argument", "has no method", "has no attribute")
    ):
        return "construction_error"

    # HTTP provider errors (non-auth, non-rate)
    import httpx as _httpx_mod
    if isinstance(exc, _httpx_mod.HTTPStatusError):
        return "provider_error"
    if any(code in msg for code in ("400", "404", "500", "502", "503")):
        return "provider_error"

    return "unexpected_error"


def _default_engine_registry() -> list[dict[str, Any]]:
    """Build the default engine registry with real service factories.

    Each entry's ``service_factory`` is a zero-arg callable that lazily
    imports the service class and instantiates it with the dependencies
    it actually requires.  Per-engine ``httpx.AsyncClient`` and API
    client instances are created at factory-call time (not at registry
    build time) because each engine runs in its own event loop thread.

    Shared deps (Settings, TTLCache) are built once and closed over.
    """
    deps = _build_market_dependencies()

    # -- Factories: each creates per-call clients + the service ------
    # Factories return (service, http_client) so _run_single_engine
    # can close the httpx.AsyncClient after execution.

    def _breadth_factory():
        from app.services.breadth_service import BreadthService
        from app.services.breadth_data_provider import BreadthDataProvider
        c = _make_per_engine_clients(deps)
        provider = BreadthDataProvider(c["tradier_client"])
        return BreadthService(provider, c["cache"]), c["http_client"]

    def _volatility_factory():
        from app.services.volatility_options_service import VolatilityOptionsService
        from app.services.volatility_options_data_provider import VolatilityOptionsDataProvider
        c = _make_per_engine_clients(deps)
        provider = VolatilityOptionsDataProvider(
            c["tradier_client"],
            market_context_service=c["market_context_service"],
            fred_client=c["fred_client"],
        )
        return VolatilityOptionsService(provider, c["cache"]), c["http_client"]

    def _liquidity_factory():
        from app.services.liquidity_conditions_service import LiquidityConditionsService
        from app.services.liquidity_conditions_data_provider import LiquidityConditionsDataProvider
        c = _make_per_engine_clients(deps)
        provider = LiquidityConditionsDataProvider(c["market_context_service"])
        return LiquidityConditionsService(provider, c["cache"]), c["http_client"]

    def _cross_asset_factory():
        from app.services.cross_asset_macro_service import CrossAssetMacroService
        from app.services.cross_asset_macro_data_provider import CrossAssetMacroDataProvider
        c = _make_per_engine_clients(deps)
        provider = CrossAssetMacroDataProvider(
            c["market_context_service"], c["fred_client"],
        )
        return CrossAssetMacroService(provider, c["cache"]), c["http_client"]

    def _flows_factory():
        from app.services.flows_positioning_service import FlowsPositioningService
        from app.services.flows_positioning_data_provider import FlowsPositioningDataProvider
        c = _make_per_engine_clients(deps)
        provider = FlowsPositioningDataProvider(c["market_context_service"])
        return FlowsPositioningService(provider, c["cache"]), c["http_client"]

    def _news_factory():
        from app.services.news_sentiment_service import NewsSentimentService
        c = _make_per_engine_clients(deps)
        return NewsSentimentService(
            c["settings"], c["http_client"], c["cache"],
            fred_client=c["fred_client"],
            market_context_service=c["market_context_service"],
        ), c["http_client"]

    return [
        _make_engine_entry(
            "breadth_participation",
            "Breadth & Participation",
            service_factory=_breadth_factory,
            run_method="get_breadth_analysis",
        ),
        _make_engine_entry(
            "volatility_options",
            "Volatility & Options",
            service_factory=_volatility_factory,
            run_method="get_volatility_analysis",
        ),
        _make_engine_entry(
            "liquidity_financial_conditions",
            "Liquidity & Financial Conditions",
            service_factory=_liquidity_factory,
            run_method="get_liquidity_conditions_analysis",
        ),
        _make_engine_entry(
            "cross_asset_macro",
            "Cross-Asset Macro",
            service_factory=_cross_asset_factory,
            run_method="get_cross_asset_analysis",
        ),
        _make_engine_entry(
            "flows_positioning",
            "Flows & Positioning",
            service_factory=_flows_factory,
            run_method="get_flows_positioning_analysis",
        ),
        _make_engine_entry(
            "news_sentiment",
            "News & Sentiment",
            service_factory=_news_factory,
            run_method="get_news_sentiment",
        ),
    ]


def get_engine_registry(
    *,
    override_registry: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the engine registry for the market stage.

    Parameters
    ----------
    override_registry : list | None
        If provided, replaces the default registry entirely.
        Useful for testing with mock engines.
    """
    if override_registry is not None:
        return list(override_registry)
    return _default_engine_registry()


# =====================================================================
#  Per-engine execution record
# =====================================================================

def build_engine_result(
    *,
    engine_key: str,
    status: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    elapsed_ms: int | None = None,
    summary: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    artifact_ref: str | None = None,
    eligible_for_model_analysis: bool = False,
    raw_result: Any = None,
) -> dict[str, Any]:
    """Build a normalized per-engine execution record.

    Parameters
    ----------
    engine_key : str
        Stable engine identifier.
    status : str
        One of ENGINE_STATUSES.
    started_at / completed_at : str | None
        ISO timestamps.
    elapsed_ms : int | None
        Wall-clock time in ms.
    summary : dict | None
        Compact metadata (score, label, confidence, etc.).
    error : dict | None
        Structured error if failed (use build_run_error format).
    artifact_ref : str | None
        artifact_id of the persisted engine output, if any.
    eligible_for_model_analysis : bool
        Whether this result is suitable for downstream Step 5.
    raw_result : Any
        The raw engine service output (used for artifact writing).
    """
    rec: dict[str, Any] = {
        "engine_key": engine_key,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_ms": elapsed_ms,
        "summary": summary or {},
        "error": error,
        "artifact_ref": artifact_ref,
        "eligible_for_model_analysis": eligible_for_model_analysis,
    }
    if raw_result is not None:
        rec["raw_result"] = raw_result
    return rec


# =====================================================================
#  Engine execution (single engine)
# =====================================================================

def _close_http_client(http_client: Any | None) -> None:
    """Best-effort close of an httpx.AsyncClient.

    Called from a sync context (ThreadPoolExecutor thread), so we
    spin up a tiny event loop just for the close.
    """
    if http_client is None:
        return
    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(http_client.aclose())
        finally:
            loop.close()
    except Exception:
        logger.debug("Failed to close httpx.AsyncClient", exc_info=True)


def _run_single_engine(
    entry: dict[str, Any],
    run_id: str,
    event_emitter: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Execute a single market engine synchronously.

    Handles service instantiation, async method invocation (via
    asyncio event loop), timing, and error capture.

    Returns a per-engine execution record.
    """
    engine_key = entry["engine_key"]
    started_at = _now_iso()
    t0 = time.monotonic()

    # ── Emit engine_started ─────────────────────────────────────
    if event_emitter:
        event_emitter(
            "engine_started",
            engine_key=engine_key,
            message=f"Engine '{engine_key}' starting",
        )

    # ── Check availability ──────────────────────────────────────
    factory = entry.get("service_factory")
    run_method = entry.get("run_method", "")
    if factory is None or not run_method:
        if event_emitter:
            event_emitter(
                "engine_failed",
                engine_key=engine_key,
                level="warning",
                message=f"Engine '{engine_key}' unavailable: no service factory",
            )
        return build_engine_result(
            engine_key=engine_key,
            status="unavailable",
            started_at=started_at,
            completed_at=_now_iso(),
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            error=build_run_error(
                code="ENGINE_UNAVAILABLE",
                message=f"No service factory for '{engine_key}'",
                source=engine_key,
            ),
        )

    try:
        # Instantiate the service
        # Factories return (service, http_client) or just service
        factory_result = factory()
        if isinstance(factory_result, tuple):
            service, http_client = factory_result
        else:
            service, http_client = factory_result, None

        method = getattr(service, run_method, None)
        if method is None:
            _close_http_client(http_client)
            raise AttributeError(
                f"Service for '{engine_key}' has no method '{run_method}'"
            )

        # Run async method — get or create event loop
        # Engine services are async; run them in an event loop
        try:
            result = _invoke_async_method(method)
        finally:
            _close_http_client(http_client)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        completed_at = _now_iso()

        # Extract summary metadata from the engine result
        summary = _extract_engine_summary(engine_key, result)

        if event_emitter:
            event_emitter(
                "engine_completed",
                engine_key=engine_key,
                message=f"Engine '{engine_key}' completed in {elapsed_ms}ms",
                metadata={"elapsed_ms": elapsed_ms, "score": summary.get("score")},
            )

        return build_engine_result(
            engine_key=engine_key,
            status="success",
            started_at=started_at,
            completed_at=completed_at,
            elapsed_ms=elapsed_ms,
            summary=summary,
            eligible_for_model_analysis=True,
            raw_result=result,
        )

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        completed_at = _now_iso()
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        failure_category = classify_engine_failure(exc)

        logger.error(
            "Engine '%s' failed (%s): %s: %s",
            engine_key, failure_category, type(exc).__name__, exc,
            exc_info=True,
        )

        if event_emitter:
            event_emitter(
                "engine_failed",
                engine_key=engine_key,
                level="error",
                message=f"Engine '{engine_key}' failed: {type(exc).__name__}: {exc}",
            )

        return build_engine_result(
            engine_key=engine_key,
            status="failed",
            started_at=started_at,
            completed_at=completed_at,
            elapsed_ms=elapsed_ms,
            error=build_run_error(
                code="ENGINE_EXCEPTION",
                message=f"{type(exc).__name__}: {exc}",
                source=engine_key,
                detail={
                    "traceback": tb,
                    "failure_category": failure_category,
                },
            ),
        )


def _invoke_async_method(method: Callable) -> Any:
    """Run an async service method, handling event loop creation safely."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # We're inside an existing event loop (e.g. FastAPI).
        # Use a new loop in a thread would be too complex here;
        # rely on the thread pool executor context where each
        # thread gets its own loop.
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(method(force=True))
        finally:
            new_loop.close()
    else:
        return asyncio.run(method(force=True))


def _extract_engine_summary(
    engine_key: str,
    raw_result: Any,
) -> dict[str, Any]:
    """Extract compact summary metadata from raw engine output.

    Pulls score, label, confidence, and signal_quality from the
    engine_result sub-dict common to all 6 engines.
    """
    if not isinstance(raw_result, dict):
        return {"raw_type": type(raw_result).__name__}

    # Most engines nest under "engine_result"
    er = raw_result.get("engine_result", raw_result)
    if not isinstance(er, dict):
        er = {}

    return {
        "score": er.get("score"),
        "label": er.get("label"),
        "confidence": er.get("confidence_score", er.get("confidence")),
        "signal_quality": er.get("signal_quality"),
        "engine_key": engine_key,
    }


# =====================================================================
#  Bounded parallel execution
# =====================================================================

def _execute_engines_parallel(
    eligible_entries: list[dict[str, Any]],
    run_id: str,
    max_workers: int,
    event_emitter: Callable[..., None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run eligible engines with bounded parallelism.

    Uses ThreadPoolExecutor because the engine services are async
    (each thread creates its own event loop).

    Returns
    -------
    dict[str, dict[str, Any]]
        engine_key → per-engine execution record
    """
    results: dict[str, dict[str, Any]] = {}

    if not eligible_entries:
        return results

    actual_workers = min(max_workers, len(eligible_entries))

    with ThreadPoolExecutor(max_workers=actual_workers) as pool:
        futures = {
            pool.submit(
                _run_single_engine, entry, run_id, event_emitter
            ): entry["engine_key"]
            for entry in eligible_entries
        }

        for future in futures:
            engine_key = futures[future]
            try:
                result = future.result()
                results[engine_key] = result
            except Exception as exc:
                # Should not normally happen since _run_single_engine
                # catches all exceptions, but be safe.
                logger.error(
                    "Unexpected executor error for '%s': %s",
                    engine_key, exc, exc_info=True,
                )
                results[engine_key] = build_engine_result(
                    engine_key=engine_key,
                    status="failed",
                    error=build_run_error(
                        code="EXECUTOR_ERROR",
                        message=f"Executor error: {type(exc).__name__}: {exc}",
                        source=engine_key,
                    ),
                )

    return results


# =====================================================================
#  Stage summary builder
# =====================================================================

def build_stage_summary(
    engine_results: dict[str, dict[str, Any]],
    skipped_engines: list[str],
    unavailable_engines: list[str],
    elapsed_ms: int | None = None,
    config_eligibility: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the market stage summary artifact payload.

    This is the compact rollup that downstream stages (Step 5+)
    and monitoring/inspection tools consume to understand what
    happened in the market-picture pass without reading every
    raw engine output.

    Parameters
    ----------
    engine_results : dict
        engine_key → per-engine execution record.
    skipped_engines : list
        engine_keys that were disabled/skipped.
    unavailable_engines : list
        engine_keys that had no service factory.
    elapsed_ms : int | None
        Total wall-clock time for the stage.
    config_eligibility : dict | None
        Output of check_engine_config_eligibility(). Included in
        summary for diagnostic transparency.
    """
    succeeded = []
    failed = []
    degraded = []
    artifact_refs: dict[str, str | None] = {}

    for key, rec in engine_results.items():
        status = rec.get("status", "failed")
        artifact_refs[key] = rec.get("artifact_ref")
        if status == "success":
            succeeded.append(key)
        elif status in ("failed", "unavailable"):
            failed.append(key)
        elif status == "degraded":
            degraded.append(key)

    total_attempted = len(engine_results)
    success_count = len(succeeded)
    fail_count = len(failed)

    # Determine stage-level status rollup
    if total_attempted == 0 and not skipped_engines:
        stage_status = "no_engines"
    elif success_count == 0:
        stage_status = "failed"
    elif fail_count > 0 or degraded:
        stage_status = "degraded"
    else:
        stage_status = "success"

    # Degraded reasoning
    degraded_reasons = []
    if failed:
        degraded_reasons.append(f"engines_failed: {failed}")
    if unavailable_engines:
        degraded_reasons.append(f"engines_unavailable: {unavailable_engines}")
    if degraded:
        degraded_reasons.append(f"engines_degraded: {degraded}")

    # Per-engine summaries for quick lookup
    engine_summaries: dict[str, dict[str, Any]] = {}
    for key, rec in engine_results.items():
        err = rec.get("error") or {}
        detail = err.get("detail") if isinstance(err, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        engine_summaries[key] = {
            "status": rec.get("status"),
            "score": rec.get("summary", {}).get("score"),
            "label": rec.get("summary", {}).get("label"),
            "confidence": rec.get("summary", {}).get("confidence"),
            "elapsed_ms": rec.get("elapsed_ms"),
            "artifact_ref": rec.get("artifact_ref"),
            "eligible_for_model_analysis": rec.get("eligible_for_model_analysis", False),
            "failure_category": detail.get("failure_category"),
        }

    summary: dict[str, Any] = {
        "stage_key": _STAGE_KEY,
        "stage_status": stage_status,
        "total_attempted": total_attempted,
        "engines_succeeded": succeeded,
        "engines_failed": failed,
        "engines_degraded": degraded,
        "engines_skipped": skipped_engines,
        "engines_unavailable": unavailable_engines,
        "success_count": success_count,
        "fail_count": fail_count,
        "skip_count": len(skipped_engines),
        "unavailable_count": len(unavailable_engines),
        "artifact_refs": artifact_refs,
        "degraded_reasons": degraded_reasons,
        "engine_summaries": engine_summaries,
        "elapsed_ms": elapsed_ms,
        "generated_at": _now_iso(),
    }
    if config_eligibility is not None:
        summary["config_eligibility"] = config_eligibility
    return summary


# =====================================================================
#  Artifact writing helpers
# =====================================================================

def _write_engine_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    engine_key: str,
    raw_result: Any,
    engine_record: dict[str, Any],
) -> str | None:
    """Write a per-engine output artifact and return its ID.

    Returns None if the engine failed (no useful data to persist).
    """
    if engine_record.get("status") != "success":
        return None

    record = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key=f"engine_{engine_key}",
        artifact_type="market_engine_output",
        data=raw_result,
        summary=engine_record.get("summary", {}),
        metadata={
            "engine_key": engine_key,
            "status": engine_record.get("status"),
            "elapsed_ms": engine_record.get("elapsed_ms"),
        },
    )
    put_artifact(artifact_store, record, overwrite=True)
    return record["artifact_id"]


def _write_stage_summary_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    summary: dict[str, Any],
) -> str:
    """Write the market stage summary artifact. Returns artifact_id."""
    record = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="market_stage_summary",
        artifact_type="market_stage_summary",
        data=summary,
        summary={
            "stage_status": summary.get("stage_status"),
            "success_count": summary.get("success_count"),
            "fail_count": summary.get("fail_count"),
        },
        metadata={"engine_keys": list(summary.get("engine_summaries", {}).keys())},
    )
    put_artifact(artifact_store, record, overwrite=True)
    return record["artifact_id"]


# =====================================================================
#  Event emission helper (within stage)
# =====================================================================

def _make_event_emitter(
    run: dict[str, Any],
    event_callback: Callable[..., None] | None,
) -> Callable[..., None] | None:
    """Build a thread-safe event emitter closure for use during engine
    execution.

    Returns None if no callback is configured.  The returned callable
    has the signature:

        emitter(event_type, engine_key="", level="info", message="",
                metadata=None)
    """
    if event_callback is None:
        return None

    run_id = run["run_id"]

    def _emit(
        event_type: str,
        engine_key: str = "",
        level: str = "info",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        merged_meta = {"engine_key": engine_key}
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
                "Event callback raised during engine '%s' event '%s'",
                engine_key, event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Stage handler (orchestrator-compatible)
# =====================================================================

def market_stage_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Orchestrator-compatible handler for the market_data stage.

    Sequence:
    1. Resolve engine registry (from kwargs or defaults).
    2. Classify engines into eligible / skipped / unavailable.
    3. Execute eligible engines with bounded parallelism.
    4. Normalize per-engine results.
    5. Write per-engine artifacts to the artifact store.
    6. Backfill artifact refs into engine records.
    7. Build stage summary and write summary artifact.
    8. Determine stage outcome from engine results.
    9. Return handler result dict for the orchestrator.

    Handler kwargs (passed via orchestrator handler_kwargs)
    ──────────────────────────────────────────────────────
    engine_registry : list | None
        Override the default engine registry (for testing).
    max_workers : int
        Concurrency limit for parallel execution.
    event_callback : callable | None
        Event callback (fallback if orchestrator doesn't inject one).
    engine_raw_results : dict | None
        Pre-fetched raw results keyed by engine_key (for testing —
        skips actual service invocation).

    Returns
    -------
    dict[str, Any]
        Handler result compatible with Step 3 orchestrator:
        { outcome, summary_counts, artifacts, metadata, error }
    """
    t0 = time.monotonic()
    run_id = run["run_id"]

    # ── Resolve parameters ──────────────────────────────────────
    registry = get_engine_registry(
        override_registry=kwargs.get("engine_registry"),
    )
    max_workers = kwargs.get("max_workers", DEFAULT_MAX_WORKERS)
    event_callback = kwargs.get("event_callback")
    event_emitter = _make_event_emitter(run, event_callback)
    raw_results_override: dict[str, Any] | None = kwargs.get("engine_raw_results")

    # ── Preflight config eligibility ────────────────────────────
    config_eligibility = check_engine_config_eligibility()
    ineligible_engines = {
        k for k, v in config_eligibility.items() if not v["eligible"]
    }
    if ineligible_engines:
        logger.warning(
            "Engines ineligible due to missing credentials: %s",
            {k: config_eligibility[k]["missing_required"]
             for k in ineligible_engines},
        )

    # ── Classify engines ────────────────────────────────────────
    eligible: list[dict[str, Any]] = []
    skipped: list[str] = []
    unavailable: list[str] = []

    for entry in registry:
        key = entry["engine_key"]
        if not entry.get("enabled", True):
            skipped.append(key)
            continue
        if entry.get("service_factory") is None and raw_results_override is None:
            unavailable.append(key)
            continue
        eligible.append(entry)

    # ── Handle empty registry ───────────────────────────────────
    if not eligible and not raw_results_override:
        return _build_no_engines_result(
            artifact_store, run_id, skipped, unavailable, t0,
        )

    # ── Execute engines ─────────────────────────────────────────
    engine_records: dict[str, dict[str, Any]]
    # raw_payloads maps engine_key → raw service output for artifact writing
    raw_payloads: dict[str, Any] = {}

    if raw_results_override is not None:
        # Test/replay mode: use pre-supplied results
        engine_records = {}
        for entry in eligible:
            key = entry["engine_key"]
            if key in raw_results_override:
                raw = raw_results_override[key]
                raw_payloads[key] = raw
                summary = _extract_engine_summary(key, raw)
                engine_records[key] = build_engine_result(
                    engine_key=key,
                    status="success",
                    started_at=_now_iso(),
                    completed_at=_now_iso(),
                    elapsed_ms=0,
                    summary=summary,
                    eligible_for_model_analysis=True,
                )
            else:
                engine_records[key] = build_engine_result(
                    engine_key=key,
                    status="skipped",
                    summary={"reason": "not in raw_results_override"},
                )
                skipped.append(key)
    else:
        engine_records = _execute_engines_parallel(
            eligible, run_id, max_workers, event_emitter,
        )
        # Extract raw_result from records (populated by _run_single_engine)
        for key, rec in engine_records.items():
            raw = rec.pop("raw_result", None)
            if raw is not None:
                raw_payloads[key] = raw

    # ── Write per-engine artifacts ──────────────────────────────
    artifact_ids: list[str] = []
    for key, rec in engine_records.items():
        raw = raw_payloads.get(key)
        art_id = _write_engine_artifact(
            artifact_store, run_id, key, raw, rec,
        )
        if art_id:
            rec["artifact_ref"] = art_id
            artifact_ids.append(art_id)

    # ── Build stage summary ─────────────────────────────────────
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    summary = build_stage_summary(
        engine_records, skipped, unavailable, elapsed_ms,
        config_eligibility=config_eligibility,
    )
    summary_art_id = _write_stage_summary_artifact(
        artifact_store, run_id, summary,
    )
    artifact_ids.append(summary_art_id)

    # ── Determine stage outcome ─────────────────────────────────
    success_count = summary["success_count"]
    fail_count = summary["fail_count"]
    total = summary["total_attempted"]

    # Check required engines
    required_failures = [
        entry["engine_key"]
        for entry in registry
        if entry.get("required") and engine_records.get(
            entry["engine_key"], {}
        ).get("status") != "success"
    ]

    if required_failures:
        outcome = "failed"
        error = build_run_error(
            code="REQUIRED_ENGINE_FAILED",
            message=f"Required engine(s) failed: {required_failures}",
            source=_STAGE_KEY,
            detail={"required_failures": required_failures},
        )
    elif success_count == 0:
        outcome = "failed"
        error = build_run_error(
            code="ALL_ENGINES_FAILED",
            message=f"All {total} engines failed",
            source=_STAGE_KEY,
            detail={"fail_count": fail_count},
        )
    else:
        outcome = "completed"
        error = None

    # ── Build handler result ────────────────────────────────────
    # Collect all artifact specs already written — the orchestrator's
    # _write_handler_artifacts will attempt to write these again,
    # so we pass pre-built records that the orchestrator can handle.
    artifact_specs = [
        {
            "artifact_key": f"engine_{key}",
            "artifact_type": "market_engine_output",
            "data": raw_payloads.get(key),
            "summary": rec.get("summary", {}),
            "metadata": {"engine_key": key},
        }
        for key, rec in engine_records.items()
        if rec.get("status") == "success" and key in raw_payloads
    ]

    return {
        "outcome": outcome,
        "summary_counts": {
            "engines_attempted": total,
            "engines_succeeded": success_count,
            "engines_failed": fail_count,
            "engines_skipped": len(skipped),
            "engines_unavailable": len(unavailable),
        },
        "artifacts": [],   # artifacts already written directly
        "metadata": {
            "stage_summary_artifact_id": summary_art_id,
            "engine_artifact_ids": {
                k: r.get("artifact_ref")
                for k, r in engine_records.items()
                if r.get("artifact_ref")
            },
            "stage_status": summary["stage_status"],
            "elapsed_ms": elapsed_ms,
            "engine_results": engine_records,
            "degraded_reasons": summary.get("degraded_reasons", []),
        },
        "error": error,
    }


# =====================================================================
#  No-engines fallback
# =====================================================================

def _build_no_engines_result(
    artifact_store: dict[str, Any],
    run_id: str,
    skipped: list[str],
    unavailable: list[str],
    t0: float,
) -> dict[str, Any]:
    """Build handler result when no engines are eligible to run."""
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    summary = build_stage_summary({}, skipped, unavailable, elapsed_ms)
    summary_art_id = _write_stage_summary_artifact(
        artifact_store, run_id, summary,
    )

    return {
        "outcome": "failed",
        "summary_counts": {
            "engines_attempted": 0,
            "engines_succeeded": 0,
            "engines_failed": 0,
            "engines_skipped": len(skipped),
            "engines_unavailable": len(unavailable),
        },
        "artifacts": [],
        "metadata": {
            "stage_summary_artifact_id": summary_art_id,
            "stage_status": "no_engines",
        },
        "error": build_run_error(
            code="NO_ELIGIBLE_ENGINES",
            message="No engines eligible for execution",
            source=_STAGE_KEY,
            detail={"skipped": skipped, "unavailable": unavailable},
        ),
    }
