"""Stock Opportunity workflow runner — Prompt 12C.

Consumes the latest valid market-state artifact and runs stock scanners
to produce ranked stock opportunity candidates with full Market Picture
enrichment and optional model analysis.

Stage flow (8 stages)
----------
1. load_market_state            — Load via market_state_consumer seam
2. resolve_stock_scanner_suite  — Enumerate configured/available/unavailable scanners
3. run_stock_scanner_suite      — Run all available stock scanners, emit coverage diagnostics
4. aggregate_dedup_candidates   — Normalize + dedup with multi-scanner provenance (source_scanners)
5. enrich_filter_rank_select    — Attach market context, filter, rank, select
6. append_market_picture_context — Full 6-module Market Picture from MI artifact
7. run_final_model_analysis     — Optional LLM review per candidate with Market Picture context
8. package_publish_output       — Compact output + summary + manifest + pointer

Artifact layout per artifact_strategy.py::

    data/workflows/stock_opportunity/
        latest.json
        run_<id>/
            stage_load_market_state.json
            stage_resolve_stock_scanner_suite.json
            stage_run_stock_scanner_suite.json
            stage_aggregate_dedup_candidates.json
            stage_enrich_filter_rank_select.json
            stage_append_market_picture_context.json
            stage_run_final_model_analysis.json
            stage_package_publish_output.json
            output.json
            summary.json
            manifest.json
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.scanner_candidate_contract import normalize_candidate_output
from app.workflows.architecture import FreshnessPolicy
from app.workflows.artifact_strategy import (

    ManifestStageEntry,
    WorkflowPointerData,
    atomic_write_json,
    get_manifest_path,
    get_output_path,
    get_run_dir,
    get_stage_artifact_path,
    get_summary_path,
    make_run_id,
    make_stage_filename,
    write_workflow_pointer,
    OUTPUT_ARTIFACT_REQUIRED_KEYS,
)
from app.workflows.definitions import WORKFLOW_VERSION
from app.workflows.market_state_consumer import (
    MarketStateConsumerResult,
    load_market_state_for_consumer,
)
from app.workflows.workflow_debug_log import WorkflowDebugLogger

logger = logging.getLogger(__name__)

# Debug log file path — overwritten each run.
_STOCK_DEBUG_LOG = Path(__file__).resolve().parents[2] / "data" / "workflows" / "stock_pipeline_debug.log"


# ═══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

WORKFLOW_ID = "stock_opportunity"

STAGE_KEYS: tuple[str, ...] = (
    "load_market_state",
    "resolve_stock_scanner_suite",
    "run_stock_scanner_suite",
    "aggregate_dedup_candidates",
    "enrich_filter_rank_select",
    "append_market_picture_context",
    "run_final_model_analysis",
    "package_publish_output",
)

# Minimum setup_quality threshold for filter stage.  Candidates below
# this score are rejected with reason "below_quality_threshold".
MIN_SETUP_QUALITY: float = 30.0

# Default: return top 20 candidates in the final output.
DEFAULT_TOP_N: int = 20

# Request all candidates from the engine service so the runner
# controls multi-scanner aggregation.  Individual scanners return
# up to 30 each (4 × 30 = 120 max), so 200 avoids any silent trim.
_ENGINE_SCAN_LIMIT: int = 200

# Scanner keys (must match StockEngineService scanner ordering).
STOCK_SCANNER_KEYS: tuple[str, ...] = (
    "stock_pullback_swing",
    "stock_momentum_breakout",
    "stock_mean_reversion",
    "stock_volatility_expansion",
)

# Market Intelligence engine keys for Market Picture enrichment.
# These are the 6 structured engine outputs from the MI artifact.
MARKET_PICTURE_ENGINE_KEYS: tuple[str, ...] = (
    "breadth_participation",
    "volatility_options",
    "cross_asset_macro",
    "flows_positioning",
    "liquidity_financial_conditions",
    "news_sentiment",
)


# ═══════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class RunnerConfig:
    """Configuration for a Stock Opportunity workflow run."""

    data_dir: str | Path
    freshness_policy: FreshnessPolicy | None = None
    top_n: int = DEFAULT_TOP_N


@dataclass
class StockOpportunityDeps:
    """Injectable service dependencies for the stock runner.

    ``stock_engine_service`` is the aggregator that orchestrates the
    four stock scanners.  It is the ONLY service boundary the runner
    touches for market-data-dependent scanning.

    ``model_request_fn`` is an optional callable for synchronous LLM
    model analysis.  When ``None``, the model-analysis stage degrades
    gracefully (candidates pass through without model review).
    Signature: ``(payload: dict) -> dict`` (same as model_router.model_request).

    If you need to test without live Tradier/LLM calls, inject mocks
    for both services.
    """

    stock_engine_service: Any
    model_request_fn: Any = None  # Optional[Callable[[dict], dict]]


@dataclass
class StageOutcome:
    """Records what happened at one stage."""

    stage_key: str
    status: str  # "completed" | "degraded" | "failed" | "skipped"
    started_at: str
    completed_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "stage_key": self.stage_key,
            "status": self.status,
            "started_at": self.started_at,
        }
        if self.completed_at is not None:
            d["completed_at"] = self.completed_at
        if self.error is not None:
            d["error"] = self.error
        return d


@dataclass
class RunResult:
    """Compact structured result of one Stock Opportunity workflow run."""

    run_id: str
    workflow_id: str = WORKFLOW_ID
    status: str = "completed"  # "completed" | "failed"
    publication_status: str | None = None
    started_at: str = ""
    completed_at: str = ""
    artifact_filename: str | None = None
    artifact_path: str | None = None
    stages: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "publication_status": self.publication_status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "artifact_filename": self.artifact_filename,
            "artifact_path": self.artifact_path,
            "stages": self.stages,
            "warnings": self.warnings,
            "error": self.error,
        }


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float:
    """Coerce to float, defaulting to 0.0 for sorting safety."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ── Top-metrics selection ─────────────────────────────────────────────
#   Stable subset of candidate_metrics for card display.
#   Input: normalized candidate dict (27-field contract).
#   Output: flat dict of selected metrics.

# Which candidate_metrics keys to surface on the card.
_TOP_METRIC_KEYS: tuple[str, ...] = (
    "composite_score",
    "rsi",
    "atr_pct",
    "volume_ratio",
    "macd_hist",
)


def select_top_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return a stable subset of candidate_metrics for card display.

    Input fields:
        candidate["candidate_metrics"][key] for key in _TOP_METRIC_KEYS
        candidate["entry_context"]["price"]
        candidate["entry_context"]["state"]
    Output: dict with price, trend_state, and selected metric keys.
    """
    cm = candidate.get("candidate_metrics") or {}
    entry = candidate.get("entry_context") or {}

    top: dict[str, Any] = {
        "price": entry.get("price"),
        "trend_state": entry.get("state"),
    }
    for key in _TOP_METRIC_KEYS:
        val = cm.get(key)
        if val is not None:
            top[key] = val
    return top


# ── Review summary builder ────────────────────────────────────────────
#   Deterministic textual summary for the stock card.
#   Input: compact candidate fields (post-extraction).
#   Output: single-string review summary.


def build_review_summary(candidate: dict[str, Any]) -> str:
    """Build a deterministic review summary from candidate fields.

    Input fields:
        scanner_name, symbol, setup_quality, confidence,
        thesis_summary (list[str]), supporting_signals (list[str]),
        entry_context.state, market_regime
    Output: human-readable 1-3 sentence summary string.
    """
    scanner = candidate.get("scanner_name") or candidate.get("scanner_key") or "Unknown"
    symbol = candidate.get("symbol") or "???"
    sq = candidate.get("setup_quality")
    conf = candidate.get("confidence")
    entry = candidate.get("entry_context") or {}
    state = entry.get("state") or "unknown"
    regime = candidate.get("market_regime") or "unknown"
    thesis = candidate.get("thesis_summary") or []

    # Quality descriptor from setup_quality (0-100 scale).
    if sq is not None and sq >= 70:
        quality_word = "strong"
    elif sq is not None and sq >= 50:
        quality_word = "moderate"
    else:
        quality_word = "speculative"

    parts: list[str] = [
        f"{scanner} setup on {symbol} ({quality_word}, score {sq})."
    ]

    if state != "unknown":
        parts.append(f"Trend state: {state}.")

    if regime != "unknown":
        parts.append(f"Market regime: {regime}.")

    if thesis:
        # Include first thesis bullet as supporting context.
        parts.append(thesis[0] if isinstance(thesis[0], str) else str(thesis[0]))

    return " ".join(parts)


# ── Compact stock candidate extraction ────────────────────────────────
#   Mirrors _extract_compact_candidate from options runner.
#   Input: enriched normalized candidate (27-field contract + enrichment).
#   Output: card-friendly compact dict for output.json.


def _extract_compact_stock_candidate(cand: dict[str, Any]) -> dict[str, Any]:
    """Extract a compact card-friendly shape from an enriched stock candidate.

    Input fields (from normalized 27-field contract + enrichment + model review):
        symbol, scanner_key, scanner_name, setup_type, direction,
        setup_quality, confidence, rank, source_scanners,
        thesis_summary (list[str]), supporting_signals, risk_flags,
        entry_context, candidate_metrics,
        market_state_ref, market_regime, risk_environment,
        vix, regime_tags, support_state,
        market_picture_summary,
        model_recommendation, model_confidence, model_score,
        model_review_summary, model_key_factors, model_caution_notes

    Output: compact dict suitable for frontend stock cards via TMC API.
    Deterministic metrics are always present; model review fields are
    None when model analysis was skipped or failed.
    """
    compact = {
        # Identity
        "symbol": cand.get("symbol"),
        "scanner_key": cand.get("scanner_key"),
        "scanner_name": cand.get("scanner_name"),
        "setup_type": cand.get("setup_type"),
        "direction": cand.get("direction"),
        # Multi-scanner provenance
        "source_scanners": cand.get("source_scanners") or [cand.get("scanner_key")],
        # Scores
        "setup_quality": cand.get("setup_quality"),
        "confidence": cand.get("confidence"),
        "rank": cand.get("rank"),
        # Thesis & signals
        "thesis_summary": cand.get("thesis_summary") or [],
        "supporting_signals": cand.get("supporting_signals") or [],
        "risk_flags": cand.get("risk_flags") or [],
        # Context
        "entry_context": cand.get("entry_context"),
        "market_regime": cand.get("market_regime"),
        "risk_environment": cand.get("risk_environment"),
        "market_state_ref": cand.get("market_state_ref"),
        "vix": cand.get("vix"),
        "regime_tags": cand.get("regime_tags") or [],
        "support_state": cand.get("support_state"),
        # Market Picture summary (compact view of 6 engine modules)
        "market_picture_summary": cand.get("market_picture_summary"),
        # Derived
        "top_metrics": select_top_metrics(cand),
        "review_summary": build_review_summary(cand),
        # Model review (None when degraded/skipped)
        "model_recommendation": cand.get("model_recommendation"),
        "model_confidence": cand.get("model_confidence"),
        "model_score": cand.get("model_score"),
        "model_review_summary": cand.get("model_review_summary"),
        "model_key_factors": cand.get("model_key_factors"),
        "model_caution_notes": cand.get("model_caution_notes"),
        "model_technical_analysis": cand.get("model_technical_analysis"),
    }
    return compact


# ═══════════════════════════════════════════════════════════════════════
# MAIN RUNNER
# ═══════════════════════════════════════════════════════════════════════


async def run_stock_opportunity(
    config: RunnerConfig,
    deps: StockOpportunityDeps,
) -> RunResult:
    """Execute one complete Stock Opportunity workflow run (8 stages).

    This is the primary entry point.  Returns a ``RunResult`` whether
    the run succeeds, degrades, or fails.  Never raises — all errors
    are captured in the result.

    Handles ``asyncio.CancelledError`` so that if the HTTP connection
    drops mid-pipeline, the runner still attempts to finish writing
    output.json and latest.json (preventing stale TMC data).
    """
    import asyncio

    now = datetime.now(timezone.utc)
    run_id = make_run_id(now)
    result = RunResult(run_id=run_id, started_at=now.isoformat())

    stage_data: dict[str, Any] = {}
    stages: list[StageOutcome] = []
    warnings: list[str] = []
    policy = config.freshness_policy or FreshnessPolicy()

    # ── Debug log: open (overwrite) ──────────────────────────────
    dbg = WorkflowDebugLogger(_STOCK_DEBUG_LOG)
    dbg.open(run_id=run_id, workflow_id=WORKFLOW_ID)
    dbg.detail("Config", {
        "data_dir": str(config.data_dir),
        "top_n": config.top_n,
        "freshness_policy": str(policy),
    })

    logger.info("[stock_opportunity] Starting run %s", run_id)

    try:
        # ── Stage 1: load_market_state ───────────────────────────────
        dbg.stage_start("load_market_state", {"freshness_policy": str(policy)})
        outcome = _stage_load_market_state(config, policy, stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "load_market_state")
        dbg.stage_end("load_market_state", outcome.status, {
            "market_state_ref": stage_data.get("market_state_ref"),
            "consumer_summary": stage_data.get("consumer_summary"),
            "market_engines_available": len(stage_data.get("market_engines") or {}),
            "error": outcome.error,
        })

        # Market state is enrichment-only — degraded is OK, only hard errors abort.
        if outcome.status == "failed":
            result.status = "failed"
            result.error = f"load_market_state failed: {outcome.error}"
            result.stages = [s.to_dict() for s in stages]
            result.completed_at = _now_iso()
            return result

        # ── Stage 2: resolve_stock_scanner_suite ─────────────────────
        dbg.stage_start("resolve_stock_scanner_suite")
        outcome = _stage_resolve_stock_scanner_suite(stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "resolve_stock_scanner_suite")
        dbg.stage_end("resolve_stock_scanner_suite", outcome.status, stage_data.get("scanner_suite"))

        # ── Stage 3: run_stock_scanner_suite ─────────────────────────
        dbg.stage_start("run_stock_scanner_suite")
        outcome = await _stage_run_stock_scanner_suite(deps, stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "run_stock_scanner_suite")
        dbg.stage_end("run_stock_scanner_suite", outcome.status, {
            "scanner_coverage": stage_data.get("scanner_coverage"),
            "scanner_meta": stage_data.get("scanner_meta"),
        })
        raw = stage_data.get("raw_candidates", [])
        dbg.candidates("Raw scanner candidates", raw,
                        keys=["symbol", "strategy_id", "composite_score", "setup_quality"])

        if outcome.status == "failed":
            result.status = "failed"
            result.error = f"run_stock_scanner_suite failed: {outcome.error}"
            result.stages = [s.to_dict() for s in stages]
            result.completed_at = _now_iso()
            return result

        # ── Stage 4: aggregate_dedup_candidates ──────────────────────
        dbg.stage_start("aggregate_dedup_candidates")
        outcome = _stage_aggregate_dedup_candidates(stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "aggregate_dedup_candidates")
        dbg.stage_end("aggregate_dedup_candidates", outcome.status,
                       stage_data.get("aggregation_counts"))
        dbg.candidates("Normalized/deduped candidates",
                        stage_data.get("normalized_candidates", []),
                        keys=["symbol", "scanner_key", "setup_quality", "source_scanners"])

        if outcome.status == "failed":
            result.status = "failed"
            result.error = f"aggregate_dedup_candidates failed: {outcome.error}"
            result.stages = [s.to_dict() for s in stages]
            result.completed_at = _now_iso()
            return result

        # ── Stage 5: enrich_filter_rank_select ───────────────────────
        dbg.stage_start("enrich_filter_rank_select", {
            "min_setup_quality": MIN_SETUP_QUALITY,
            "top_n": config.top_n,
            "input_count": len(stage_data.get("normalized_candidates", [])),
        })
        outcome = _stage_enrich_filter_rank_select(config, stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "enrich_filter_rank_select")
        dbg.stage_end("enrich_filter_rank_select", outcome.status,
                       stage_data.get("filter_counts"))
        dbg.candidates("Selected candidates (post-filter)",
                        stage_data.get("selected_candidates", []),
                        keys=["symbol", "scanner_key", "setup_quality", "rank",
                              "market_regime", "risk_environment"])

        if outcome.status == "failed":
            result.status = "failed"
            result.error = f"enrich_filter_rank_select failed: {outcome.error}"
            result.stages = [s.to_dict() for s in stages]
            result.completed_at = _now_iso()
            return result

        # ── Stage 6: append_market_picture_context ───────────────────
        dbg.stage_start("append_market_picture_context")
        outcome = _stage_append_market_picture_context(stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "append_market_picture_context")
        dbg.stage_end("append_market_picture_context", outcome.status,
                       stage_data.get("market_picture_summary"))
        # Market picture is enrichment-only — degraded is OK.

        # ── Stage 7: run_final_model_analysis ────────────────────────
        dbg.stage_start("run_final_model_analysis", {
            "candidate_count": len(stage_data.get("selected_candidates", [])),
            "model_request_fn_configured": deps.model_request_fn is not None,
        })
        outcome = await _stage_run_final_model_analysis(deps, stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "run_final_model_analysis")
        dbg.stage_end("run_final_model_analysis", outcome.status,
                       stage_data.get("model_analysis_counts"))
        # Log per-candidate model results
        for cand in stage_data.get("selected_candidates", []):
            dbg.note(
                f"  Model result: {cand.get('symbol')} → "
                f"rec={cand.get('model_recommendation')} "
                f"score={cand.get('model_score')} "
                f"confidence={cand.get('model_confidence')}"
            )
        # Model analysis is optional — degraded is OK.

        # ── Stage 7b: model_filter_rank ──────────────────────────────
        #    Discard PASS recommendations, rank by model_score, keep top 10.
        dbg.stage_start("model_filter_rank", {
            "input_count": len(stage_data.get("selected_candidates", [])),
            "MODEL_FILTER_TOP_N": MODEL_FILTER_TOP_N,
        })
        outcome = _stage_model_filter_rank(stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "model_filter_rank")
        dbg.stage_end("model_filter_rank", outcome.status,
                       stage_data.get("model_filter_counts"))
        dbg.candidates("Final candidates (post-model-filter)",
                        stage_data.get("selected_candidates", []),
                        keys=["symbol", "scanner_key", "model_recommendation",
                              "model_score", "setup_quality", "rank"])

    except asyncio.CancelledError:
        # HTTP client disconnected or task was cancelled mid-pipeline.
        # Still attempt to package whatever candidates we have so far so
        # the latest.json pointer gets updated and TMC sees fresh data.
        logger.warning(
            "[stock_opportunity] CancelledError in run %s at stage %d — "
            "attempting to package partial output",
            run_id, len(stages),
        )
        warnings.append("[pipeline] Run interrupted (CancelledError) — packaging partial output")
        dbg.note(f"⚠ CancelledError at stage {len(stages)} — packaging partial output")
    except Exception as exc:
        logger.error(
            "[stock_opportunity] Unexpected error in run %s: %s",
            run_id, exc, exc_info=True,
        )
        warnings.append(f"[pipeline] Unexpected error — packaging partial output: {exc}")
        dbg.note(f"⚠ Unexpected error: {exc} — packaging partial output")

    # ── Stage 8: package_publish_output ──────────────────────────
    # Always attempt stage 8 so output.json + latest.json are written.
    dbg.stage_start("package_publish_output", {
        "candidates_to_package": len(stage_data.get("selected_candidates", [])),
    })
    outcome = _stage_package_publish_output(config, run_id, now, stage_data, stages, warnings)
    stages.append(outcome)
    dbg.stage_end("package_publish_output", outcome.status, {
        "publication_status": stage_data.get("publication_status"),
        "artifact_filename": stage_data.get("artifact_filename"),
        "artifact_path": str(stage_data.get("artifact_path", "")),
    })

    # ── Post-pipeline: truth-audit & model-input-preview (12C) ───
    _write_truth_audit_artifact(config, run_id, stage_data, warnings)
    _write_model_input_preview_artifact(config, run_id, stage_data)

    # ── Finalize ─────────────────────────────────────────────────
    result.stages = [s.to_dict() for s in stages]
    result.warnings = warnings
    result.completed_at = _now_iso()
    result.publication_status = stage_data.get("publication_status")
    result.artifact_filename = stage_data.get("artifact_filename")
    ap = stage_data.get("artifact_path")
    result.artifact_path = str(ap) if ap else None

    if outcome.status == "failed":
        result.status = "failed"
        result.error = f"package_publish_output failed: {outcome.error}"
    else:
        result.status = "completed"

    logger.info(
        "[stock_opportunity] Run %s finished: status=%s candidates=%d",
        run_id, result.status,
        len(stage_data.get("selected_candidates", [])),
    )

    # ── Debug log: close ─────────────────────────────────────────
    dbg.section("Final Result")
    dbg.detail("Result summary", {
        "run_id": result.run_id,
        "status": result.status,
        "publication_status": result.publication_status,
        "artifact_filename": result.artifact_filename,
        "artifact_path": result.artifact_path,
        "stages_completed": len(result.stages),
        "selected_candidates": len(stage_data.get("selected_candidates", [])),
    })
    dbg.close(status=result.status, warnings=warnings)

    return result


# ═══════════════════════════════════════════════════════════════════════
# STAGE IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════


def _stage_load_market_state(
    config: RunnerConfig,
    policy: FreshnessPolicy,
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 1: Load latest valid market state via consumer seam."""
    started = _now_iso()
    try:
        consumer_result: MarketStateConsumerResult = load_market_state_for_consumer(
            data_dir=config.data_dir,
            freshness_policy=policy,
        )
        stage_data["market_state_consumer"] = consumer_result

        if not consumer_result.loaded:
            # Market state is enrichment-only; allow workflow to continue degraded.
            reason = consumer_result.error or "Market state not available"
            warnings.append(f"[market_state] {reason} — proceeding without market context")
            stage_data["market_state_ref"] = None
            stage_data["consumer_summary"] = {}
            stage_data["composite"] = {}
            stage_data["market_engines"] = {}
            return StageOutcome(
                stage_key="load_market_state",
                status="degraded",
                started_at=started,
                completed_at=_now_iso(),
                error=reason,
            )

        # Propagate warnings from discovery.
        for w in consumer_result.warnings:
            warnings.append(f"[market_state] {w}")

        stage_data["market_state_ref"] = consumer_result.market_state_ref
        stage_data["consumer_summary"] = consumer_result.consumer_summary
        stage_data["composite"] = consumer_result.composite

        # Extract full MI engine outputs for Market Picture enrichment (stage 6).
        # artifact["engines"] contains all 6 structured engine modules.
        artifact = consumer_result.artifact or {}
        stage_data["market_engines"] = artifact.get("engines", {})

        return StageOutcome(
            stage_key="load_market_state",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.warning("load_market_state unavailable: %s", exc, exc_info=True)
        reason = str(exc)
        warnings.append(f"[market_state] {reason} — proceeding without market context")
        stage_data["market_state_ref"] = None
        stage_data["consumer_summary"] = {}
        stage_data["composite"] = {}
        stage_data["market_engines"] = {}
        return StageOutcome(
            stage_key="load_market_state",
            status="degraded",
            started_at=started,
            completed_at=_now_iso(),
            error=reason,
        )


def _stage_resolve_stock_scanner_suite(
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 2: Resolve scanner suite — enumerate configured/available/unavailable scanners.

    All 4 stock scanners are unconditionally instantiated (no feature flags),
    so available == configured.  This stage makes the suite explicit and
    traceable in the stage artifact.
    """
    started = _now_iso()
    configured = list(STOCK_SCANNER_KEYS)
    # All 4 scanners are unconditionally available.
    available = list(STOCK_SCANNER_KEYS)
    unavailable: list[str] = []

    stage_data["scanner_suite"] = {
        "configured": configured,
        "available": available,
        "unavailable": unavailable,
        "scanner_count": len(available),
    }

    logger.info(
        "Scanner suite resolved: %d configured, %d available, %d unavailable",
        len(configured), len(available), len(unavailable),
    )

    return StageOutcome(
        stage_key="resolve_stock_scanner_suite",
        status="completed",
        started_at=started,
        completed_at=_now_iso(),
    )


async def _stage_run_stock_scanner_suite(
    deps: StockOpportunityDeps,
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 3: Run all stock scanners via StockEngineService with coverage diagnostics.

    The StockEngineService orchestrates 4 scanners sequentially
    (pullback_swing, momentum_breakout, mean_reversion, volatility_expansion).
    Each scanner calls Tradier for OHLCV data internally.

    Coverage diagnostics emitted:
    - scanners_attempted / scanners_succeeded / scanners_failed
    - per-scanner candidate counts and max scores
    - total raw candidates across all scanners
    """
    started = _now_iso()
    try:
        # Request ALL candidates from the engine — pass _ENGINE_SCAN_LIMIT
        # so the engine does not silently trim to its internal TOP_N (9).
        # The runner controls final selection in stage 5.
        scan_result = await deps.stock_engine_service.scan(top_n=_ENGINE_SCAN_LIMIT)
        stage_data["scan_result"] = scan_result

        raw_candidates = scan_result.get("candidates", [])
        scanner_meta = scan_result.get("scanners", [])
        scan_warnings = scan_result.get("warnings", [])

        for w in scan_warnings:
            warnings.append(f"[scanner_suite] {w}")

        stage_data["raw_candidates"] = raw_candidates
        stage_data["scanner_meta"] = scanner_meta

        # ── Coverage diagnostics ─────────────────────────────────
        attempted = len(scanner_meta)
        succeeded = sum(1 for s in scanner_meta if s.get("status") == "ok")
        failed = sum(1 for s in scanner_meta if s.get("status") in ("error", "skipped"))
        per_scanner_counts = {
            s.get("strategy_id", "?"): s.get("candidates_count", 0)
            for s in scanner_meta
        }
        # total_candidates: pre-engine-trim count from the engine (true raw total).
        # len(raw_candidates): what the engine actually returned after its sort.
        engine_total = scan_result.get("total_candidates", len(raw_candidates))
        stage_data["scanner_coverage"] = {
            "scanners_attempted": attempted,
            "scanners_succeeded": succeeded,
            "scanners_failed": failed,
            "per_scanner_counts": per_scanner_counts,
            "total_raw_candidates": engine_total,
            "engine_returned_candidates": len(raw_candidates),
            "scan_time_seconds": scan_result.get("scan_time_seconds"),
        }

        logger.info(
            "Scanner suite complete: %d candidates (%d raw) from %d scanners "
            "(succeeded=%d, failed=%d)",
            len(raw_candidates), engine_total, attempted, succeeded, failed,
        )

        return StageOutcome(
            stage_key="run_stock_scanner_suite",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("run_stock_scanner_suite failed: %s", exc, exc_info=True)
        return StageOutcome(
            stage_key="run_stock_scanner_suite",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


def _stage_aggregate_dedup_candidates(
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 4: Normalize raw scanner candidates and deduplicate with provenance.

    Each candidate carries a ``strategy_id`` that maps to one of the
    four stock scanner keys.  ``normalize_candidate_output()`` converts
    the scanner dict into the 27-field canonical contract shape.

    Deduplication: if the same symbol appears from multiple scanners,
    keep the one with the highest setup_quality BUT preserve all
    scanner keys in a ``source_scanners`` list for provenance.

    Also preserves a ``raw_candidates_by_key`` map so the model-analysis
    stage can pass raw scanner dicts to ``analyze_stock_strategy()``.
    """
    started = _now_iso()
    try:
        raw_candidates: list[dict[str, Any]] = stage_data.get("raw_candidates", [])
        normalized: list[dict[str, Any]] = []
        raw_by_key: dict[str, dict[str, Any]] = {}
        skipped = 0
        per_scanner_normalized: dict[str, int] = {}

        for cand in raw_candidates:
            scanner_key = cand.get("strategy_id", "")
            if not scanner_key:
                skipped += 1
                continue
            try:
                norm = normalize_candidate_output(scanner_key, cand)
                normalized.append(norm)
                per_scanner_normalized[scanner_key] = per_scanner_normalized.get(scanner_key, 0) + 1
                # Keep raw candidate keyed by symbol+scanner for model analysis.
                sym = norm.get("symbol", "?")
                raw_by_key[f"{sym}:{scanner_key}"] = cand
            except Exception as exc:
                skipped += 1
                sym = cand.get("symbol", "?")
                warnings.append(
                    f"[aggregate_dedup] Failed for {sym}/{scanner_key}: {exc}"
                )

        if skipped:
            warnings.append(f"[aggregate_dedup] {skipped} candidate(s) skipped during normalization")

        # ── Dedup by symbol: keep highest setup_quality, merge source_scanners ──
        pre_dedup_count = len(normalized)
        best_by_symbol: dict[str, dict[str, Any]] = {}
        scanners_by_symbol: dict[str, list[str]] = {}

        for cand in normalized:
            sym = cand.get("symbol", "?")
            scanner_key = cand.get("scanner_key", "")
            sq = _safe_float(cand.get("setup_quality"))

            # Track all scanners that found this symbol.
            if sym not in scanners_by_symbol:
                scanners_by_symbol[sym] = []
            if scanner_key and scanner_key not in scanners_by_symbol[sym]:
                scanners_by_symbol[sym].append(scanner_key)

            existing = best_by_symbol.get(sym)
            if existing is None or sq > _safe_float(existing.get("setup_quality")):
                best_by_symbol[sym] = cand

        # Attach source_scanners to each deduped candidate.
        deduped: list[dict[str, Any]] = []
        for sym, cand in best_by_symbol.items():
            cand["source_scanners"] = scanners_by_symbol.get(sym, [])
            deduped.append(cand)

        dedup_removed = pre_dedup_count - len(deduped)

        # Count symbols found by multiple scanners.
        multi_scanner_count = sum(
            1 for scanners in scanners_by_symbol.values() if len(scanners) > 1
        )

        stage_data["normalized_candidates"] = deduped
        stage_data["raw_candidates_by_key"] = raw_by_key
        stage_data["aggregation_counts"] = {
            "raw_input": len(raw_candidates),
            "normalized": pre_dedup_count,
            "skipped": skipped,
            "dedup_removed": dedup_removed,
            "after_dedup": len(deduped),
            "multi_scanner_symbols": multi_scanner_count,
            "per_scanner_normalized": per_scanner_normalized,
        }

        logger.info(
            "Aggregate/dedup: %d raw → %d normalized → %d after dedup "
            "(skipped=%d, dedup_removed=%d, multi_scanner=%d)",
            len(raw_candidates), pre_dedup_count, len(deduped),
            skipped, dedup_removed, multi_scanner_count,
        )

        return StageOutcome(
            stage_key="aggregate_dedup_candidates",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("aggregate_dedup_candidates failed: %s", exc, exc_info=True)
        return StageOutcome(
            stage_key="aggregate_dedup_candidates",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


def _stage_enrich_filter_rank_select(
    config: RunnerConfig,
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 5: Enrich candidates with market context, then filter, rank, select.

    Enrichment: attaches consumer_summary fields (regime, VIX, tags, support)
    to each candidate from the market state loaded in stage 1.

    Filtering: reject candidates with setup_quality < MIN_SETUP_QUALITY.
    Ranking: sort by setup_quality DESC, then symbol ASC for determinism.
    Selection: apply top_n cap from config, assign rank (1-based).

    Emits filter_counts for the stage artifact:
      deduped_input → enriched → rejected (with reason counts) → passed → selected
    """
    started = _now_iso()
    try:
        candidates: list[dict[str, Any]] = stage_data.get("normalized_candidates", [])
        market_state_ref = stage_data.get("market_state_ref")
        consumer_summary = stage_data.get("consumer_summary") or {}

        # ── Enrich with market context ───────────────────────────
        market_regime = consumer_summary.get("market_state")
        risk_environment = consumer_summary.get("stability_state")
        vix = consumer_summary.get("vix")
        regime_tags = consumer_summary.get("regime_tags") or []
        support_state = consumer_summary.get("support_state")
        market_summary_text = consumer_summary.get("summary_text")
        market_confidence = consumer_summary.get("confidence")
        is_degraded = consumer_summary.get("is_degraded", False)

        enriched: list[dict[str, Any]] = []
        for cand in candidates:
            enriched_cand = dict(cand)
            enriched_cand["market_state_ref"] = market_state_ref
            enriched_cand["market_regime"] = market_regime
            enriched_cand["risk_environment"] = risk_environment
            enriched_cand["vix"] = vix
            enriched_cand["regime_tags"] = regime_tags
            enriched_cand["support_state"] = support_state
            enriched_cand["market_summary_text"] = market_summary_text
            enriched_cand["market_confidence"] = market_confidence
            enriched.append(enriched_cand)

        if is_degraded:
            warnings.append("[enrich] Market state is degraded — enrichment may be incomplete")

        enriched_input = len(enriched)

        # ── Filter: minimum quality threshold ────────────────────
        rejected_reasons: dict[str, int] = {}
        passed: list[dict[str, Any]] = []
        for cand in enriched:
            sq = _safe_float(cand.get("setup_quality"))
            if sq < MIN_SETUP_QUALITY:
                rejected_reasons["below_quality_threshold"] = rejected_reasons.get("below_quality_threshold", 0) + 1
                continue
            passed.append(cand)

        # ── Rank: sort by setup_quality DESC, symbol ASC ─────────
        passed.sort(
            key=lambda c: (-_safe_float(c.get("setup_quality")), c.get("symbol", "")),
        )

        # ── Select: apply top_n cap ──────────────────────────────
        top_n = config.top_n
        selected = passed[:top_n]

        # Assign rank.
        for i, cand in enumerate(selected, start=1):
            cand["rank"] = i

        stage_data["selected_candidates"] = selected
        stage_data["filter_counts"] = {
            "enriched_input": enriched_input,
            "rejected": sum(rejected_reasons.values()),
            "rejected_reasons": rejected_reasons,
            "passed": len(passed),
            "selected": len(selected),
            "top_n_cap": top_n,
        }

        logger.info(
            "Enrich/filter/rank/select: %d enriched → %d rejected → %d passed → %d selected (top_n=%d)",
            enriched_input,
            sum(rejected_reasons.values()),
            len(passed),
            len(selected),
            top_n,
        )

        return StageOutcome(
            stage_key="enrich_filter_rank_select",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("enrich_filter_rank_select failed: %s", exc, exc_info=True)
        return StageOutcome(
            stage_key="enrich_filter_rank_select",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


def _build_market_picture_context(engines: dict[str, Any]) -> dict[str, Any]:
    """Build compact Market Picture context from MI engine outputs.

    Extracts a meaningful subset of each engine's 25-field contract
    for use in candidate enrichment and model analysis prompts.

    Input: artifact["engines"] dict from MarketStateConsumerResult.
    Output: dict keyed by engine_key with compact summaries.
    """
    context: dict[str, Any] = {}
    for key in MARKET_PICTURE_ENGINE_KEYS:
        engine = engines.get(key)
        if engine is None:
            continue
        context[key] = {
            "score": engine.get("score"),
            "label": engine.get("label"),
            "confidence": engine.get("confidence"),
            "summary": engine.get("summary"),
            "trader_takeaway": engine.get("trader_takeaway"),
            "bull_factors": engine.get("bull_factors") or [],
            "bear_factors": engine.get("bear_factors") or [],
            "risks": engine.get("risks") or [],
            "regime_tags": engine.get("regime_tags") or [],
            "engine_status": engine.get("engine_status"),
        }
    return context


def _build_market_picture_summary(market_picture_context: dict[str, Any]) -> dict[str, Any]:
    """Build a compact summary from Market Picture context for output.

    This is the shape that goes into the compact candidate output
    so the frontend and diagnostics can verify Market Picture depth.
    """
    engines_available = len(market_picture_context)
    engine_summaries: dict[str, Any] = {}
    for key, eng in market_picture_context.items():
        engine_summaries[key] = {
            "score": eng.get("score"),
            "label": eng.get("label"),
            "summary": eng.get("summary"),
        }
    return {
        "engines_available": engines_available,
        "engines_total": len(MARKET_PICTURE_ENGINE_KEYS),
        "engine_summaries": engine_summaries,
    }


def _stage_append_market_picture_context(
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 6: Append full Market Picture context to each selected candidate.

    Extracts compact context from all 6 MI engine modules
    (breadth_participation, volatility_options, cross_asset_macro,
    flows_positioning, liquidity_financial_conditions, news_sentiment)
    and attaches:
    - market_picture_context: full 6-module context dict (for model analysis)
    - market_picture_summary: compact summary (for output/diagnostics)

    Degradable: if no engines are available, candidates pass through
    without Market Picture enrichment.
    """
    started = _now_iso()
    try:
        selected: list[dict[str, Any]] = stage_data.get("selected_candidates", [])
        engines = stage_data.get("market_engines") or {}

        market_picture_context = _build_market_picture_context(engines)
        market_picture_summary = _build_market_picture_summary(market_picture_context)

        engines_available = len(market_picture_context)

        for cand in selected:
            cand["market_picture_context"] = market_picture_context
            cand["market_picture_summary"] = market_picture_summary

        stage_data["market_picture_summary"] = market_picture_summary

        if engines_available == 0:
            warnings.append(
                "[market_picture] No MI engines available — "
                "Market Picture enrichment skipped"
            )
            return StageOutcome(
                stage_key="append_market_picture_context",
                status="degraded",
                started_at=started,
                completed_at=_now_iso(),
                error="No MI engines available",
            )

        logger.info(
            "Market Picture context appended: %d/%d engines available for %d candidates",
            engines_available, len(MARKET_PICTURE_ENGINE_KEYS), len(selected),
        )

        return StageOutcome(
            stage_key="append_market_picture_context",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("append_market_picture_context failed: %s", exc, exc_info=True)
        warnings.append(f"[market_picture] Stage failed: {exc}")
        return StageOutcome(
            stage_key="append_market_picture_context",
            status="degraded",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


async def _stage_run_final_model_analysis(
    deps: StockOpportunityDeps,
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 7: LLM model analysis per selected candidate (required).

    Every candidate MUST receive a model analysis result.  The stage uses
    retries (2 per candidate) and a second retry-pass for any failures
    so that transient network / model-cold-start issues do not leave
    candidates without analysis.

    Uses ``routed_tmc_final_decision`` from model_routing_integration (Step 8).
    Falls back to legacy ``analyze_tmc_final_decision`` if routing unavailable.
    The LLM call is synchronous and runs in an executor to avoid
    blocking the event loop.

    Model review fields attached to each candidate:
    - model_recommendation: "BUY" | "PASS"
    - model_confidence: 0-100
    - model_score: 0-100
    - model_review_summary: str
    - model_key_factors: list[dict] (factor, impact, evidence)
    - model_caution_notes: list[str] (primary risks)
    - model_review: full model analysis dict (for debug/stage artifact)
    """
    import asyncio
    import functools
    from concurrent.futures import ThreadPoolExecutor

    started = _now_iso()
    selected: list[dict[str, Any]] = stage_data.get("selected_candidates", [])

    if not deps.model_request_fn:
        # Model analysis not configured — degrade gracefully.
        for cand in selected:
            cand["model_review"] = None
        stage_data["model_analysis_counts"] = {
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped_reason": "model_request_fn not configured",
        }
        warnings.append("[model_analysis] No model_request_fn configured — skipping model review")
        return StageOutcome(
            stage_key="run_final_model_analysis",
            status="degraded",
            started_at=started,
            completed_at=_now_iso(),
            error="model_request_fn not configured",
        )

    try:
        from app.services.model_routing_integration import routed_tmc_final_decision

        raw_by_key = stage_data.get("raw_candidates_by_key", {})
        loop = asyncio.get_running_loop()
        attempted = 0
        succeeded = 0
        failed = 0
        failed_candidates: list[dict[str, str]] = []

        # Concurrent dispatch: limit in-flight model calls to avoid
        # overwhelming providers.  The per-provider execution gate
        # handles routing; this pool + semaphore cap total thread usage.
        total_candidates = len(selected)
        _max_concurrent = min(total_candidates, 4) if total_candidates else 1
        _model_pool = ThreadPoolExecutor(
            max_workers=_max_concurrent,
            thread_name_prefix="model_dispatch",
        )

        async def _analyze_one(cand: dict[str, Any]) -> bool:
            """Run model analysis for a single candidate. Returns True on success."""
            symbol = cand.get("symbol", "?")
            scanner_key = cand.get("scanner_key", "")

            # Find the raw candidate for this symbol+scanner.
            raw_cand = raw_by_key.get(f"{symbol}:{scanner_key}")
            if not raw_cand:
                raw_cand = {
                    "symbol": symbol,
                    "strategy_id": scanner_key,
                    "composite_score": cand.get("setup_quality"),
                    "thesis": cand.get("thesis_summary"),
                    "metrics": cand.get("candidate_metrics") or {},
                }

            mpc = cand.get("market_picture_context")

            raw_cand = dict(raw_cand)
            raw_cand["market_regime"] = cand.get("market_regime")
            raw_cand["risk_environment"] = cand.get("risk_environment")
            raw_cand["vix"] = cand.get("vix")
            raw_cand["regime_tags"] = cand.get("regime_tags")
            raw_cand["support_state"] = cand.get("support_state")
            raw_cand["supporting_signals"] = cand.get("supporting_signals")
            raw_cand["risk_flags"] = cand.get("risk_flags")
            raw_cand["entry_context"] = cand.get("entry_context")

            try:
                model_result = await loop.run_in_executor(
                    _model_pool,
                    functools.partial(
                        routed_tmc_final_decision,
                        candidate=raw_cand,
                        market_picture_context=mpc,
                        strategy_id=scanner_key,
                        retries=2,
                    ),
                )
                cand["model_review"] = model_result

                decision = model_result.get("decision", "PASS")
                cand["model_recommendation"] = "BUY" if decision == "EXECUTE" else "PASS"
                cand["model_confidence"] = model_result.get("conviction")
                ec = model_result.get("engine_comparison") or {}
                cand["model_score"] = ec.get("model_score")
                cand["model_review_summary"] = model_result.get("decision_summary")

                factors = model_result.get("factors_considered") or []
                cand["model_key_factors"] = [
                    {"factor": f.get("factor", ""), "impact": f.get("assessment", "neutral"), "evidence": f.get("detail", "")}
                    for f in factors
                ]

                ra = model_result.get("risk_assessment") or {}
                cand["model_caution_notes"] = ra.get("primary_risks") or []
                cand["model_technical_analysis"] = model_result.get("technical_analysis")

                return True
            except Exception as exc:
                logger.warning(
                    "Model analysis failed for %s/%s: %s",
                    symbol, scanner_key, exc,
                )
                return False

        # ── First pass: concurrent model analysis ────────────────
        first_pass_failures: list[dict[str, Any]] = []
        _sem = asyncio.Semaphore(_max_concurrent)

        async def _guarded_analyze(idx: int, cand: dict[str, Any]) -> bool:
            """Dispatch a single candidate under the concurrency semaphore."""
            async with _sem:
                logger.info(
                    "[model_analysis] Dispatching candidate %d/%d: %s",
                    idx, total_candidates, cand.get("symbol", "?"),
                )
                return await _analyze_one(cand)

        gather_tasks = [
            _guarded_analyze(i + 1, c) for i, c in enumerate(selected)
        ]
        gather_results = await asyncio.gather(*gather_tasks, return_exceptions=True)

        attempted = total_candidates
        for cand, result in zip(selected, gather_results):
            if isinstance(result, Exception):
                logger.warning(
                    "[model_analysis] Unexpected error for %s: %s",
                    cand.get("symbol", "?"), result,
                )
                first_pass_failures.append(cand)
            elif result:
                succeeded += 1
            else:
                first_pass_failures.append(cand)

        # ── Second pass: retry failures with a brief delay ───────
        if first_pass_failures:
            logger.info(
                "[model_analysis] %d/%d failed first pass, retrying after 3s delay...",
                len(first_pass_failures), attempted,
            )
            await asyncio.sleep(3)
            for idx, cand in enumerate(first_pass_failures, start=1):
                symbol = cand.get("symbol", "?")
                logger.info(
                    "[model_analysis] Retry %d/%d: %s",
                    idx, len(first_pass_failures), symbol,
                )
                ok = await _analyze_one(cand)
                if ok:
                    succeeded += 1
                else:
                    cand["model_review"] = None
                    failed += 1
                    failed_candidates.append({
                        "symbol": cand.get("symbol", "?"),
                        "scanner_key": cand.get("scanner_key", ""),
                    })

        _model_pool.shutdown(wait=False)

        stage_data["model_analysis_counts"] = {
            "attempted": attempted,
            "succeeded": succeeded,
            "failed": failed,
            "failed_candidates": failed_candidates,
        }

        logger.info(
            "[model_analysis] Complete: %d attempted, %d succeeded, %d failed, %d retried",
            attempted, succeeded, failed, len(first_pass_failures),
        )

        status = "completed" if failed == 0 else "degraded"
        if succeeded == 0 and attempted > 0:
            warnings.append("[model_analysis] All model analysis calls failed")

        return StageOutcome(
            stage_key="run_final_model_analysis",
            status=status,
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        # Total failure (e.g., import error) — degrade, don't abort.
        logger.error("run_final_model_analysis failed: %s", exc, exc_info=True)
        try:
            _model_pool.shutdown(wait=False)
        except (NameError, UnboundLocalError):
            pass
        for cand in selected:
            cand["model_review"] = None
        stage_data["model_analysis_counts"] = {
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped_reason": str(exc),
        }
        warnings.append(f"[model_analysis] Stage failed: {exc}")
        return StageOutcome(
            stage_key="run_final_model_analysis",
            status="degraded",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


# ── Post-model filtering constants ──────────────────────────────────
MODEL_FILTER_TOP_N = 10


def _stage_model_filter_rank(
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 7b: Filter and rank candidates by model analysis results.

    Rules (applied in order):
      1. Discard candidates where model_recommendation == "PASS".
      2. Discard candidates with no model analysis (model_review is None).
      3. Rank remaining by model_score descending (None scores sort last).
      4. Keep top MODEL_FILTER_TOP_N (default 10).

    Updates stage_data["selected_candidates"] in place and records
    filter counts in stage_data["model_filter_counts"].
    """
    started = _now_iso()
    selected: list[dict[str, Any]] = stage_data.get("selected_candidates", [])
    before_count = len(selected)

    # 1 + 2: Remove PASS recommendations and candidates without model analysis
    passed = []
    no_analysis = []
    buy_candidates = []

    for cand in selected:
        rec = cand.get("model_recommendation")
        if cand.get("model_review") is None:
            no_analysis.append(cand.get("symbol", "?"))
        elif rec == "PASS":
            passed.append(cand.get("symbol", "?"))
        else:
            buy_candidates.append(cand)

    # 3: Rank by model_score descending (None scores at end)
    buy_candidates.sort(
        key=lambda c: c.get("model_score") if c.get("model_score") is not None else -1,
        reverse=True,
    )

    # 4: Trim to top N
    trimmed = buy_candidates[:MODEL_FILTER_TOP_N]
    dropped_by_rank = buy_candidates[MODEL_FILTER_TOP_N:]

    stage_data["selected_candidates"] = trimmed

    stage_data["model_filter_counts"] = {
        "before": before_count,
        "passed_removed": len(passed),
        "passed_symbols": passed,
        "no_analysis_removed": len(no_analysis),
        "no_analysis_symbols": no_analysis,
        "buy_candidates": len(buy_candidates),
        "dropped_by_rank": len(dropped_by_rank),
        "dropped_symbols": [c.get("symbol", "?") for c in dropped_by_rank],
        "after": len(trimmed),
    }

    logger.info(
        "[model_filter_rank] %d → %d candidates "
        "(passed=%d, no_analysis=%d, rank_dropped=%d)",
        before_count, len(trimmed),
        len(passed), len(no_analysis), len(dropped_by_rank),
    )

    if len(passed) > 0:
        warnings.append(
            f"[model_filter] Removed {len(passed)} PASS candidates: {', '.join(passed)}"
        )
    if len(no_analysis) > 0:
        warnings.append(
            f"[model_filter] Removed {len(no_analysis)} candidates without model analysis: {', '.join(no_analysis)}"
        )

    return StageOutcome(
        stage_key="model_filter_rank",
        status="completed",
        started_at=started,
        completed_at=_now_iso(),
    )


def _stage_package_publish_output(
    config: RunnerConfig,
    run_id: str,
    started_ts: datetime,
    stage_data: dict[str, Any],
    all_stages: list[StageOutcome],
    warnings: list[str],
) -> StageOutcome:
    """Stage 8: Package compact output, write summary, manifest, and pointer.

    Writes (atomically):
    1. ``stage_package_publish_output.json``  — full selected candidates
    2. ``output.json``                        — compact consumer output
    3. ``summary.json``                       — run summary
    4. ``manifest.json``                      — run-level index
    5. ``latest.json``                        — workflow pointer update
    """
    started = _now_iso()
    try:
        selected: list[dict[str, Any]] = stage_data.get("selected_candidates", [])

        market_state_ref = stage_data.get("market_state_ref")
        consumer_result: MarketStateConsumerResult | None = stage_data.get("market_state_consumer")
        pub_status = (
            consumer_result.publication_status
            if consumer_result
            else None
        )

        # ── Determine quality ────────────────────────────────────
        aggregation_counts = stage_data.get("aggregation_counts", {})
        filter_counts = stage_data.get("filter_counts", {})
        scanner_meta = stage_data.get("scanner_meta", [])
        scanners_ok = sum(1 for s in scanner_meta if s.get("status") == "ok")
        scanners_total = len(scanner_meta)

        total_candidates = aggregation_counts.get("after_dedup", 0)
        selected_count = len(selected)

        quality_level = "good"
        if selected_count == 0:
            quality_level = "no_candidates"
        elif scanners_ok < scanners_total:
            quality_level = "degraded"

        # ── Determine batch status ───────────────────────────────
        # batch_status: "completed" | "partial" | "failed"
        # "partial" = CancelledError or unexpected error interrupted pipeline
        _has_interruption = any(
            "[pipeline] Run interrupted" in w or "[pipeline] Unexpected error" in w
            for w in warnings
        )
        batch_status = "partial" if _has_interruption else "completed"

        # ── Build output.json ────────────────────────────────────
        compact_candidates = [
            _extract_compact_stock_candidate(c) for c in selected
        ]
        completed_at = _now_iso()
        output_data: dict[str, Any] = {
            "contract_version": WORKFLOW_VERSION,
            "workflow_id": WORKFLOW_ID,
            "run_id": run_id,
            "generated_at": completed_at,
            "batch_status": batch_status,
            "market_state_ref": market_state_ref,
            "publication": {
                "status": "valid" if quality_level != "no_candidates" else "degraded",
                "market_state_publication_status": pub_status,
            },
            "candidates": compact_candidates,
            "quality": {
                "level": quality_level,
                "total_candidates_found": total_candidates,
                "selected_count": selected_count,
                "top_n_cap": config.top_n,
                "scanners_ok": scanners_ok,
                "scanners_total": scanners_total,
            },
            "scanner_coverage": stage_data.get("scanner_coverage"),
            "scanner_suite": stage_data.get("scanner_suite"),
            "filter_counts": filter_counts,
            "market_picture_summary": stage_data.get("market_picture_summary"),
            "model_analysis_counts": stage_data.get("model_analysis_counts"),
        }

        # ── Build summary.json ───────────────────────────────────
        stage_key = "package_publish_output"
        stage_list = []
        for i, so in enumerate(all_stages):
            stage_list.append({
                "stage_key": so.stage_key,
                "stage_index": i,
                "status": so.status,
            })
        stage_list.append({
            "stage_key": stage_key,
            "stage_index": len(all_stages),
            "status": "completed",
        })

        summary_data: dict[str, Any] = {
            "workflow_id": WORKFLOW_ID,
            "run_id": run_id,
            "started_at": started_ts.isoformat(),
            "completed_at": completed_at,
            "status": "completed",
            "batch_status": batch_status,
            "market_state_ref": market_state_ref,
            "total_candidates": total_candidates,
            "selected_count": selected_count,
            "quality_level": quality_level,
            "stages": stage_list,
            "warnings": warnings,
            "scanner_coverage": stage_data.get("scanner_coverage"),
            "scanner_suite": stage_data.get("scanner_suite"),
            "filter_counts": filter_counts,
            "market_picture_summary": stage_data.get("market_picture_summary"),
            "model_analysis_counts": stage_data.get("model_analysis_counts"),
        }

        # ── Build manifest.json ──────────────────────────────────
        manifest_stages: list[dict[str, Any]] = []
        for i, so in enumerate(all_stages):
            manifest_stages.append(
                ManifestStageEntry(
                    stage_key=so.stage_key,
                    stage_index=i,
                    status=so.status,
                    artifact_filename=make_stage_filename(so.stage_key),
                    started_at=so.started_at,
                    completed_at=so.completed_at,
                ).to_dict()
            )
        manifest_stages.append(
            ManifestStageEntry(
                stage_key=stage_key,
                stage_index=len(all_stages),
                status="completed",
                artifact_filename=make_stage_filename(stage_key),
                started_at=started,
                completed_at=completed_at,
                record_count=selected_count,
            ).to_dict()
        )

        manifest_data: dict[str, Any] = {
            "workflow_id": WORKFLOW_ID,
            "run_id": run_id,
            "started_at": started_ts.isoformat(),
            "completed_at": completed_at,
            "status": "completed",
            "stages": manifest_stages,
            "output_filename": "output.json",
        }

        # ── Write artifacts ──────────────────────────────────────
        data_dir = Path(config.data_dir)

        # Stage artifact.
        stage_artifact_data = {
            "workflow_id": WORKFLOW_ID,
            "run_id": run_id,
            "stage_key": stage_key,
            "stage_index": len(all_stages),
            "generated_at": completed_at,
            "status": "completed",
            "selected_count": selected_count,
            "top_n_cap": config.top_n,
            "candidates": selected,
        }
        stage_path = get_stage_artifact_path(data_dir, WORKFLOW_ID, run_id, stage_key)
        atomic_write_json(stage_path, stage_artifact_data)

        # output.json
        output_path = get_output_path(data_dir, WORKFLOW_ID, run_id)
        atomic_write_json(output_path, output_data)

        # summary.json
        summary_path = get_summary_path(data_dir, WORKFLOW_ID, run_id)
        atomic_write_json(summary_path, summary_data)

        # manifest.json
        manifest_path = get_manifest_path(data_dir, WORKFLOW_ID, run_id)
        atomic_write_json(manifest_path, manifest_data)

        # Update pointer (latest.json).
        # batch_status is "completed" or "partial" — always update pointer.
        # (If packaging itself fails, we never reach here.)
        pointer = WorkflowPointerData(
            run_id=run_id,
            workflow_id=WORKFLOW_ID,
            completed_at=completed_at,
            status="valid",
            output_filename="output.json",
            contract_version=WORKFLOW_VERSION,
            batch_status=batch_status,
        )
        write_workflow_pointer(data_dir, WORKFLOW_ID, pointer)
        logger.info(
            "[stock_opportunity] Pointer updated: run_id=%s batch_status=%s",
            run_id, batch_status,
        )

        # Store for result.
        stage_data["publication_status"] = output_data["publication"]["status"]
        stage_data["artifact_filename"] = "output.json"
        stage_data["artifact_path"] = output_path

        return StageOutcome(
            stage_key=stage_key,
            status="completed",
            started_at=started,
            completed_at=completed_at,
        )
    except Exception as exc:
        logger.error("package_publish_output failed: %s", exc, exc_info=True)
        return StageOutcome(
            stage_key="package_publish_output",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


# ═══════════════════════════════════════════════════════════════════════
# STAGE ARTIFACT WRITER (for stages 1-6)
# ═══════════════════════════════════════════════════════════════════════


def _write_stage_artifact(
    config: RunnerConfig,
    run_id: str,
    outcome: StageOutcome,
    stage_data: dict[str, Any],
    stage_key: str,
) -> None:
    """Write a stage's handoff artifact to disk.

    Stage artifacts are inspectable JSON files written after each stage
    completes (successfully or not).  They capture the stage output
    so that debugging, replay, and auditing can inspect intermediate
    state.
    """
    data_dir = Path(config.data_dir)
    stage_index = STAGE_KEYS.index(stage_key)

    base: dict[str, Any] = {
        "workflow_id": WORKFLOW_ID,
        "run_id": run_id,
        "stage_key": stage_key,
        "stage_index": stage_index,
        "generated_at": outcome.completed_at or _now_iso(),
        "status": outcome.status,
    }

    if outcome.error:
        base["error"] = outcome.error

    # Stage-specific payload.
    if stage_key == "load_market_state":
        consumer_result: MarketStateConsumerResult | None = stage_data.get("market_state_consumer")
        if consumer_result:
            base["consumer_result"] = consumer_result.to_dict()
        engines = stage_data.get("market_engines") or {}
        base["market_engines_available"] = len(engines)
        base["market_engine_keys"] = list(engines.keys())
    elif stage_key == "resolve_stock_scanner_suite":
        base["scanner_suite"] = stage_data.get("scanner_suite", {})
    elif stage_key == "run_stock_scanner_suite":
        base["total_raw_candidates"] = len(stage_data.get("raw_candidates", []))
        base["scanner_meta"] = stage_data.get("scanner_meta", [])
        base["scanner_coverage"] = stage_data.get("scanner_coverage", {})
    elif stage_key == "aggregate_dedup_candidates":
        base["aggregation_counts"] = stage_data.get("aggregation_counts", {})
    elif stage_key == "enrich_filter_rank_select":
        base["filter_counts"] = stage_data.get("filter_counts", {})
        base["selected_count"] = len(stage_data.get("selected_candidates", []))
    elif stage_key == "append_market_picture_context":
        base["market_picture_summary"] = stage_data.get("market_picture_summary", {})
    elif stage_key == "run_final_model_analysis":
        base["model_analysis_counts"] = stage_data.get("model_analysis_counts", {})

    try:
        path = get_stage_artifact_path(data_dir, WORKFLOW_ID, run_id, stage_key)
        atomic_write_json(path, base)
    except Exception as exc:
        logger.warning("Failed to write stage artifact %s: %s", stage_key, exc)


# ═══════════════════════════════════════════════════════════════════════
# TRUTH-AUDIT & MODEL-INPUT-PREVIEW ARTIFACTS (Prompt 12C)
# ═══════════════════════════════════════════════════════════════════════


def _build_truth_audit(
    stage_data: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    """Build the runtime truth-audit dict from actual stage outputs.

    Every field is populated from real stage_data — nothing synthetic.
    """
    scanner_suite = stage_data.get("scanner_suite") or {}
    scanner_coverage = stage_data.get("scanner_coverage") or {}
    aggregation_counts = stage_data.get("aggregation_counts") or {}
    filter_counts = stage_data.get("filter_counts") or {}
    model_analysis_counts = stage_data.get("model_analysis_counts") or {}
    market_picture_summary = stage_data.get("market_picture_summary") or {}
    selected: list[dict[str, Any]] = stage_data.get("selected_candidates") or []

    configured = scanner_suite.get("configured", [])
    available = scanner_suite.get("available", [])
    unavailable = scanner_suite.get("unavailable", [])

    scanner_meta: list[dict[str, Any]] = stage_data.get("scanner_meta") or []
    attempted = [s.get("strategy_id") for s in scanner_meta]
    per_scanner_status = {
        s.get("strategy_id", "?"): s.get("status", "unknown")
        for s in scanner_meta
    }
    per_scanner_raw = scanner_coverage.get("per_scanner_counts") or {}

    # Selected-candidate provenance.
    by_primary: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for cand in selected:
        pk = cand.get("scanner_key") or "unknown"
        by_primary[pk] = by_primary.get(pk, 0) + 1
        for sk in (cand.get("source_scanners") or [pk]):
            by_source[sk] = by_source.get(sk, 0) + 1

    primary_keys_used = set(by_primary.keys())
    source_keys_used = set(by_source.keys())
    effectively_multi = len(primary_keys_used) > 1 or len(source_keys_used) > 1

    # Market Picture fields appended.
    mp_ctx = selected[0].get("market_picture_context") if selected else None
    market_picture_fields: dict[str, bool] = {}
    for ek in MARKET_PICTURE_ENGINE_KEYS:
        market_picture_fields[ek] = bool(mp_ctx and ek in mp_ctx)

    # Consumer-summary-level fields.
    consumer_summary = stage_data.get("consumer_summary") or {}
    market_picture_fields["overall_market_summary"] = bool(consumer_summary.get("summary_text"))
    market_picture_fields["regime"] = bool(consumer_summary.get("market_state"))
    market_picture_fields["stability"] = bool(consumer_summary.get("stability_state"))
    market_picture_fields["confidence"] = consumer_summary.get("confidence") is not None
    market_picture_fields["vix"] = consumer_summary.get("vix") is not None

    return {
        "configured_default_scanners": configured,
        "runnable_scanners": available,
        "unavailable_scanners": unavailable,
        "attempted_scanners": attempted,
        "per_scanner_status": per_scanner_status,
        "per_scanner_raw_candidate_counts": per_scanner_raw,
        "total_raw_candidates": scanner_coverage.get("total_raw_candidates", 0),
        "engine_returned_candidates": scanner_coverage.get("engine_returned_candidates", 0),
        "post_dedup_candidate_count": aggregation_counts.get("after_dedup", 0),
        "multi_scanner_symbols": aggregation_counts.get("multi_scanner_symbols", 0),
        "post_filter_candidate_count": filter_counts.get("passed", 0),
        "shortlisted_for_model_count": filter_counts.get("selected", 0),
        "final_selected_count": len(selected),
        "selected_candidates_by_primary_scanner": by_primary,
        "selected_candidates_by_source_scanners": by_source,
        "model_analysis_invoked_count": model_analysis_counts.get("attempted", 0),
        "model_analysis_succeeded_count": model_analysis_counts.get("succeeded", 0),
        "market_picture_fields_appended": market_picture_fields,
        "market_picture_engines_available": market_picture_summary.get("engines_available", 0),
        "effectively_multi_scanner": effectively_multi,
        "multi_scanner_statement": (
            f"Run used {len(primary_keys_used)} primary scanner(s) across "
            f"{len(selected)} selected candidates. "
            f"Source scanners contributing: {sorted(source_keys_used)}. "
            + ("MULTI-SCANNER" if effectively_multi else "SINGLE-SCANNER")
            + " run."
        ),
    }


def _build_model_input_preview(
    stage_data: dict[str, Any],
    max_previews: int = 3,
) -> dict[str, Any]:
    """Build a compact preview of what actually went into model analysis.

    Shows the fields present on the raw candidate dict at the point of
    model analysis invocation, including Market Picture context.
    Does NOT dump full prompts — only field presence and compact values.
    """
    selected: list[dict[str, Any]] = stage_data.get("selected_candidates") or []
    previews: list[dict[str, Any]] = []

    for cand in selected[:max_previews]:
        mp_ctx = cand.get("market_picture_context") or {}
        mp_engines_present = sorted(mp_ctx.keys())
        mp_engine_summaries: dict[str, str | None] = {}
        for ek in MARKET_PICTURE_ENGINE_KEYS:
            eng = mp_ctx.get(ek)
            mp_engine_summaries[ek] = (
                eng.get("summary") if eng else None
            )

        preview: dict[str, Any] = {
            # Trade/setup fields
            "symbol": cand.get("symbol"),
            "scanner_key": cand.get("scanner_key"),
            "source_scanners": cand.get("source_scanners"),
            "setup_type": cand.get("setup_type"),
            "direction": cand.get("direction"),
            "setup_quality": cand.get("setup_quality"),
            "confidence": cand.get("confidence"),
            "rank": cand.get("rank"),
            "thesis_summary_present": bool(cand.get("thesis_summary")),
            "supporting_signals_present": bool(cand.get("supporting_signals")),
            "risk_flags_present": bool(cand.get("risk_flags")),
            # Deterministic stock metrics
            "entry_context_present": bool(cand.get("entry_context")),
            "candidate_metrics_present": bool(cand.get("candidate_metrics")),
            # Market state
            "market_state_ref": cand.get("market_state_ref"),
            "market_regime": cand.get("market_regime"),
            "risk_environment": cand.get("risk_environment"),
            "vix": cand.get("vix"),
            "regime_tags": cand.get("regime_tags"),
            "support_state": cand.get("support_state"),
            # Market Picture
            "market_picture_engines_present": mp_engines_present,
            "market_picture_engine_count": len(mp_engines_present),
            "market_picture_engine_summaries": mp_engine_summaries,
            # Model analysis outcome (if already run)
            "model_recommendation": cand.get("model_recommendation"),
            "model_confidence": cand.get("model_confidence"),
            "model_score": cand.get("model_score"),
        }
        previews.append(preview)

    return {
        "preview_count": len(previews),
        "total_selected": len(selected),
        "candidate_previews": previews,
    }


def _write_truth_audit_artifact(
    config: RunnerConfig,
    run_id: str,
    stage_data: dict[str, Any],
    warnings: list[str],
) -> None:
    """Write stock_workflow_truth_audit.json to the run directory."""
    try:
        audit = _build_truth_audit(stage_data, warnings)
        audit["run_id"] = run_id
        audit["workflow_id"] = WORKFLOW_ID
        audit["generated_at"] = _now_iso()

        data_dir = Path(config.data_dir)
        run_dir = get_run_dir(data_dir, WORKFLOW_ID, run_id)
        path = run_dir / "stock_workflow_truth_audit.json"
        atomic_write_json(path, audit)
        logger.info("Truth audit artifact written: %s", path)
    except Exception as exc:
        logger.warning("Failed to write truth audit artifact: %s", exc)


def _write_model_input_preview_artifact(
    config: RunnerConfig,
    run_id: str,
    stage_data: dict[str, Any],
) -> None:
    """Write stock_model_analysis_input_preview.json to the run directory."""
    try:
        preview = _build_model_input_preview(stage_data)
        preview["run_id"] = run_id
        preview["workflow_id"] = WORKFLOW_ID
        preview["generated_at"] = _now_iso()

        data_dir = Path(config.data_dir)
        run_dir = get_run_dir(data_dir, WORKFLOW_ID, run_id)
        path = run_dir / "stock_model_analysis_input_preview.json"
        atomic_write_json(path, preview)
        logger.info("Model input preview artifact written: %s", path)
    except Exception as exc:
        logger.warning("Failed to write model input preview artifact: %s", exc)
