"""Options Opportunity workflow runner — Prompt 6.

Consumes the latest valid market-state artifact and runs V2 options scanner
families to produce ranked options opportunity candidates.

This workflow preserves BenTrade's quantitative options identity:
- Structural validation (phase C checks)
- Recomputed math validation (phase E checks)
- Trust hygiene (quote sanity, liquidity sanity, dedup)
- EV / POP / RoR / max-loss / width / breakeven fields
- Family-specific scanner behavior (V2 architecture)
- Stable rejection reason codes (v2_* taxonomy)

Candidate contract decision
---------------------------
The V2 scanner architecture already produces ``V2Candidate`` dataclasses
with full quant data (math, diagnostics, legs, validation).  Rather than
creating a redundant contract, this runner:
- Uses ``V2Candidate.to_dict()`` as the rich stage-artifact shape
- Extracts compact workflow-level fields for final output
- Preserves math, diagnostics, and validation summaries explicitly

Stage flow (matches definitions.py OPTIONS_OPPORTUNITY_STAGES)
--------------------------------------------------------------
1. load_market_state  — Load via market_state_consumer seam
2. scan               — Run V2 options scanner families through service seam
3. validate_math      — Surface structural/math validation & trust hygiene
4. enrich_evaluate    — Attach market context, rank candidates
5. select_package     — Apply selection cap, write output + summary + manifest

Artifact layout per artifact_strategy.py::

    data/workflows/options_opportunity/
        latest.json
        run_<id>/
            stage_load_market_state.json
            stage_scan.json
            stage_validate_math.json
            stage_enrich_evaluate.json
            stage_select_package.json
            output.json
            summary.json
            manifest.json

Greenfield design — does NOT reference archived pipeline code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
)
from app.workflows.definitions import WORKFLOW_VERSION
from app.workflows.market_state_consumer import (
    MarketStateConsumerResult,
    load_market_state_for_consumer,
)
from app.workflows.workflow_debug_log import WorkflowDebugLogger

logger = logging.getLogger(__name__)

# Debug log file path — overwritten each run.
_OPTIONS_DEBUG_LOG = Path(__file__).resolve().parents[2] / "data" / "workflows" / "options_pipeline_debug.log"


# ═══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

WORKFLOW_ID = "options_opportunity"

STAGE_KEYS: tuple[str, ...] = (
    "load_market_state",
    "scan",
    "validate_math",
    "enrich_evaluate",
    "select_package",
)

# Default: return top 30 options candidates in the final output.
DEFAULT_TOP_N: int = 30

# Default symbol universe — index ETFs per BenTrade philosophy.
DEFAULT_SYMBOLS: tuple[str, ...] = ("SPY", "QQQ", "IWM", "DIA")

# All V2 scanner keys across all implemented families.
ALL_V2_SCANNER_KEYS: tuple[str, ...] = (
    # Vertical spreads
    "put_credit_spread",
    "call_credit_spread",
    "put_debit",
    "call_debit",
    # Iron condors
    "iron_condor",
    # Butterflies
    "butterfly_debit",
    "iron_butterfly",
    # Calendars / Diagonals
    "calendar_call_spread",
    "calendar_put_spread",
    "diagonal_call_spread",
    "diagonal_put_spread",
)


# ═══════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class RunnerConfig:
    """Configuration for an Options Opportunity workflow run."""

    data_dir: str | Path
    freshness_policy: FreshnessPolicy | None = None
    top_n: int = DEFAULT_TOP_N
    symbols: tuple[str, ...] | list[str] = DEFAULT_SYMBOLS
    scanner_keys: tuple[str, ...] | list[str] = ALL_V2_SCANNER_KEYS


@dataclass
class OptionsOpportunityDeps:
    """Injectable service dependencies for the options runner.

    ``options_scanner_service`` is the provider boundary for all options
    chain/scanner work.  It must implement::

        async scan(
            symbols: list[str],
            scanner_keys: list[str],
            context: dict[str, Any] | None = None,
        ) -> dict[str, Any]

    Expected return shape::

        {
            "scan_results": [  # list of per-symbol-per-family results
                {
                    "scanner_key": str,
                    "strategy_id": str,
                    "family_key": str,
                    "symbol": str,
                    "candidates": [V2Candidate.to_dict(), ...],
                    "rejected": [V2Candidate.to_dict(), ...],
                    "total_constructed": int,
                    "total_passed": int,
                    "total_rejected": int,
                    "reject_reason_counts": dict,
                    "warning_counts": dict,
                    "phase_counts": list,
                    "elapsed_ms": float,
                },
                ...
            ],
            "warnings": [str, ...],
            "scanners_total": int,
            "scanners_ok": int,
            "scanners_failed": int,
        }

    The workflow treats this service as the data-provider boundary:
    the runner itself never calls Tradier or any market-data API.
    If testing without live data, inject a mock service.
    """

    options_scanner_service: Any


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
    """Compact structured result of one Options Opportunity workflow run."""

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


# ── Workflow-level candidate extraction ─────────────────────────────


def _extract_compact_candidate(cand: dict[str, Any]) -> dict[str, Any]:
    """Extract a compact workflow-level candidate from a V2 candidate dict.

    Preserves quant identity fields for final output without copying
    the full V2 blob.  Stage artifacts keep the full dict.

    Input: V2Candidate.to_dict() shape.
    Output: compact dict for output.json candidates.
    """
    math = cand.get("math") or {}
    diag = cand.get("diagnostics") or {}

    # Structural validation summary from diagnostics.structural_checks
    structural_checks = diag.get("structural_checks", [])
    structural_pass = sum(1 for c in structural_checks if c.get("passed", False))
    structural_fail = len(structural_checks) - structural_pass

    # Math validation summary from diagnostics.math_checks
    math_checks = diag.get("math_checks", [])
    math_pass = sum(1 for c in math_checks if c.get("passed", False))
    math_fail = len(math_checks) - math_pass

    # Hygiene summary from diagnostics.quote_checks + liquidity_checks
    quote_checks = diag.get("quote_checks", [])
    liquidity_checks = diag.get("liquidity_checks", [])
    quote_ok = all(c.get("passed", False) for c in quote_checks) if quote_checks else True
    liquidity_ok = all(c.get("passed", False) for c in liquidity_checks) if liquidity_checks else True

    # Compact legs (strike/side/type only — full legs in stage artifact)
    legs = cand.get("legs", [])
    compact_legs = [
        {
            "strike": leg.get("strike"),
            "side": leg.get("side"),
            "option_type": leg.get("option_type"),
            "expiration": leg.get("expiration"),
            "bid": leg.get("bid"),
            "ask": leg.get("ask"),
            "delta": leg.get("delta"),
            "iv": leg.get("iv"),
            "open_interest": leg.get("open_interest"),
            "volume": leg.get("volume"),
        }
        for leg in legs
    ]

    return {
        # Identity
        "candidate_id": cand.get("candidate_id"),
        "scanner_key": cand.get("scanner_key"),
        "strategy_id": cand.get("strategy_id"),
        "family_key": cand.get("family_key"),
        # Asset
        "symbol": cand.get("symbol"),
        "underlying_price": cand.get("underlying_price"),
        # Expiry
        "expiration": cand.get("expiration"),
        "expiration_back": cand.get("expiration_back"),
        "dte": cand.get("dte"),
        "dte_back": cand.get("dte_back"),
        # Structure
        "legs": compact_legs,
        "leg_count": len(legs),
        # Recomputed math — preserved fully, per-contract monetary convention
        # Formula: see V2RecomputedMath docstring in scanner_v2/contracts.py
        "math": {
            "net_credit": math.get("net_credit"),     # per-share
            "net_debit": math.get("net_debit"),        # per-share
            "max_profit": math.get("max_profit"),      # per-contract (×100)
            "max_loss": math.get("max_loss"),           # per-contract (×100)
            "width": math.get("width"),                # per-share (strike distance)
            "pop": math.get("pop"),                     # [0, 1]
            "pop_source": math.get("pop_source"),
            "ev": math.get("ev"),                       # per-contract
            "ev_per_day": math.get("ev_per_day"),       # EV / DTE
            "ror": math.get("ror"),                     # max_profit / max_loss
            "kelly": math.get("kelly"),
            "breakeven": math.get("breakeven", []),
        },
        # Validation summaries — preserves pass/warn/fail reasoning
        "structural_validation": {
            "total_checks": len(structural_checks),
            "passed": structural_fail == 0,
            "pass_count": structural_pass,
            "failure_count": structural_fail,
        },
        "math_validation": {
            "total_checks": len(math_checks),
            "passed": math_fail == 0,
            "pass_count": math_pass,
            "failure_count": math_fail,
        },
        # Trust hygiene summary
        "hygiene": {
            "quote_sanity_ok": quote_ok,
            "liquidity_ok": liquidity_ok,
            "quote_checks_count": len(quote_checks),
            "liquidity_checks_count": len(liquidity_checks),
        },
        # Diagnostics summary (compact)
        "diagnostics_summary": {
            "reject_reasons": diag.get("reject_reasons", []),
            "warnings": diag.get("warnings", []),
            "pass_reasons": diag.get("pass_reasons", []),
        },
        # Status
        "passed": cand.get("passed", False),
        "downstream_usable": cand.get("downstream_usable", False),
        # Lineage / metadata
        "contract_version": cand.get("contract_version"),
        "scanner_version": cand.get("scanner_version"),
        "generated_at": cand.get("generated_at"),
    }


def _extract_scan_diagnostics(scan_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate diagnostics from all scan results."""
    total_constructed = 0
    total_passed = 0
    total_rejected = 0
    all_reject_reasons: dict[str, int] = {}
    all_warning_counts: dict[str, int] = {}
    family_summaries: list[dict[str, Any]] = []

    for sr in scan_results:
        total_constructed += sr.get("total_constructed", 0)
        total_passed += sr.get("total_passed", 0)
        total_rejected += sr.get("total_rejected", 0)

        for code, count in sr.get("reject_reason_counts", {}).items():
            all_reject_reasons[code] = all_reject_reasons.get(code, 0) + count
        for code, count in sr.get("warning_counts", {}).items():
            all_warning_counts[code] = all_warning_counts.get(code, 0) + count

        # Per-family summary with narrowing and phase details
        narrowing = sr.get("narrowing_diagnostics") or {}
        family_summaries.append({
            "scanner_key": sr.get("scanner_key"),
            "family_key": sr.get("family_key"),
            "symbol": sr.get("symbol"),
            "total_constructed": sr.get("total_constructed", 0),
            "total_passed": sr.get("total_passed", 0),
            "total_rejected": sr.get("total_rejected", 0),
            "reject_reason_counts": sr.get("reject_reason_counts", {}),
            "phase_counts": sr.get("phase_counts", []),
            "narrowing": {
                "contracts_loaded": narrowing.get("total_contracts_loaded", 0),
                "expirations_kept": narrowing.get("expirations_kept", 0),
                "expirations_dropped": narrowing.get("expirations_dropped", 0),
                "contracts_final": narrowing.get("contracts_final", 0),
                "missing_bid": narrowing.get("contracts_missing_bid", 0),
                "missing_ask": narrowing.get("contracts_missing_ask", 0),
                "missing_delta": narrowing.get("contracts_missing_delta", 0),
            },
            "elapsed_ms": sr.get("elapsed_ms", 0),
        })

    return {
        "total_constructed": total_constructed,
        "total_passed": total_passed,
        "total_rejected": total_rejected,
        "reject_reason_counts": all_reject_reasons,
        "warning_counts": all_warning_counts,
        "family_summaries": family_summaries,
    }


# ═══════════════════════════════════════════════════════════════════════
# MAIN RUNNER
# ═══════════════════════════════════════════════════════════════════════


async def run_options_opportunity(
    config: RunnerConfig,
    deps: OptionsOpportunityDeps,
) -> RunResult:
    """Execute one complete Options Opportunity workflow run (5 stages).

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
    dbg = WorkflowDebugLogger(_OPTIONS_DEBUG_LOG)
    dbg.open(run_id=run_id, workflow_id=WORKFLOW_ID)
    dbg.detail("Config", {
        "data_dir": str(config.data_dir),
        "top_n": config.top_n,
        "symbols": list(config.symbols),
        "scanner_keys": list(config.scanner_keys),
        "freshness_policy": str(policy),
    })

    logger.info("[options_opportunity] Starting run %s", run_id)

    try:
        # ── Stage 1: load_market_state ───────────────────────────────
        dbg.stage_start("load_market_state", {"freshness_policy": str(policy)})
        outcome = _stage_load_market_state(config, policy, stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "load_market_state")
        dbg.stage_end("load_market_state", outcome.status, {
            "market_state_ref": stage_data.get("market_state_ref"),
            "consumer_summary": stage_data.get("consumer_summary"),
            "error": outcome.error,
        })

        # Market state is enrichment-only — degraded is OK, only hard errors abort.
        if outcome.status == "failed":
            result.status = "failed"
            result.error = f"load_market_state failed: {outcome.error}"
            result.stages = [s.to_dict() for s in stages]
            result.completed_at = _now_iso()
            return result

        market_state_degraded = outcome.status == "degraded"

        # ── Stage 2: scan ────────────────────────────────────────────
        dbg.stage_start("scan", {
            "symbols": list(config.symbols),
            "scanner_keys": list(config.scanner_keys),
        })
        outcome = await _stage_scan(config, deps, stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "scan")
        dbg.stage_end("scan", outcome.status, {
            "scanners_total": stage_data.get("scanners_total"),
            "scanners_ok": stage_data.get("scanners_ok"),
            "scanners_failed": stage_data.get("scanners_failed"),
            "passed_candidates": len(stage_data.get("raw_candidates", [])),
            "rejected_candidates": len(stage_data.get("rejected_candidates", [])),
            "scan_diagnostics": stage_data.get("scan_diagnostics"),
        })
        dbg.candidates("Passed candidates (from scan)",
                        stage_data.get("raw_candidates", []),
                        keys=["candidate_id", "scanner_key", "strategy_id",
                              "symbol", "expiration", "passed",
                              "downstream_usable"])
        # Log rejected candidates summary
        rejected = stage_data.get("rejected_candidates", [])
        if rejected:
            dbg.candidates("Rejected candidates (from scan)", rejected,
                            keys=["candidate_id", "scanner_key", "symbol",
                                  "expiration", "passed"],
                            limit=30)

        # Log family-by-family breakdown for diagnostic visibility
        scan_diag = stage_data.get("scan_diagnostics", {})
        fam_summaries = scan_diag.get("family_summaries", [])
        if fam_summaries:
            dbg.detail("Family-by-family scan breakdown", fam_summaries)
        # Log aggregate reject reasons
        agg_rejects = scan_diag.get("reject_reason_counts", {})
        if agg_rejects:
            dbg.detail("Aggregate reject reasons", agg_rejects)

        if outcome.status == "failed":
            result.status = "failed"
            result.error = f"scan failed: {outcome.error}"
            result.stages = [s.to_dict() for s in stages]
            result.completed_at = _now_iso()
            return result

        # ── Stage 3: validate_math ───────────────────────────────────
        dbg.stage_start("validate_math", {
            "input_count": len(stage_data.get("raw_candidates", [])),
        })
        outcome = _stage_validate_math(stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "validate_math")
        dbg.stage_end("validate_math", outcome.status, {
            "validated_count": len(stage_data.get("validated_candidates", [])),
            "filtered_count": stage_data.get("validation_filtered_count", 0),
            "filter_reasons": stage_data.get("validation_filter_reasons", {}),
            "validation_summary": stage_data.get("validation_summary", {}),
        })
        dbg.candidates("Validated candidates",
                        stage_data.get("validated_candidates", []),
                        keys=["candidate_id", "scanner_key", "symbol",
                              "expiration", "downstream_usable"],
                        limit=40)

        if outcome.status == "failed":
            result.status = "failed"
            result.error = f"validate_math failed: {outcome.error}"
            result.stages = [s.to_dict() for s in stages]
            result.completed_at = _now_iso()
            return result

        # ── Stage 4: enrich_evaluate ─────────────────────────────────
        dbg.stage_start("enrich_evaluate", {
            "input_count": len(stage_data.get("validated_candidates", [])),
        })
        outcome = _stage_enrich_evaluate(stage_data, warnings)
        stages.append(outcome)
        _write_stage_artifact(config, run_id, outcome, stage_data, "enrich_evaluate")
        dbg.stage_end("enrich_evaluate", outcome.status, {
            "enriched_count": len(stage_data.get("enriched_candidates", [])),
            "market_state_ref": stage_data.get("market_state_ref"),
            "market_regime": (stage_data.get("consumer_summary") or {}).get("market_state"),
            "credibility_filter": stage_data.get("credibility_filter"),
        })
        # Log full enriched candidates with math details
        dbg.candidates("Enriched candidates (ranked by EV)",
                        stage_data.get("enriched_candidates", []),
                        keys=["candidate_id", "scanner_key", "symbol",
                              "expiration", "rank", "math"])

    except asyncio.CancelledError:
        # HTTP client disconnected or task was cancelled mid-pipeline.
        # Still attempt to package whatever candidates we have so far so
        # the latest.json pointer gets updated and TMC sees fresh data.
        logger.warning(
            "[options_opportunity] CancelledError in run %s at stage %d — "
            "attempting to package partial output",
            run_id, len(stages),
        )
        warnings.append("[pipeline] Run interrupted (CancelledError) — packaging partial output")
        dbg.note(f"⚠ CancelledError at stage {len(stages)} — packaging partial output")
    except Exception as exc:
        logger.error(
            "[options_opportunity] Unexpected error in run %s: %s",
            run_id, exc, exc_info=True,
        )
        warnings.append(f"[pipeline] Unexpected error — packaging partial output: {exc}")
        dbg.note(f"⚠ Unexpected error: {exc} — packaging partial output")

    # ── Stage 5: select_package ──────────────────────────────────
    # Always attempt stage 5 so output.json + latest.json are written.
    dbg.stage_start("select_package", {
        "candidates_to_package": len(stage_data.get("enriched_candidates", [])),
        "top_n": config.top_n,
    })
    outcome = _stage_select_package(config, run_id, now, stage_data, stages, warnings)
    stages.append(outcome)
    dbg.stage_end("select_package", outcome.status, {
        "publication_status": stage_data.get("publication_status"),
        "artifact_filename": stage_data.get("artifact_filename"),
        "artifact_path": str(stage_data.get("artifact_path", "")),
    })

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
        result.error = f"select_package failed: {outcome.error}"
    else:
        result.status = "completed"

    logger.info(
        "[options_opportunity] Run %s finished: status=%s candidates=%d",
        run_id, result.status,
        len(stage_data.get("enriched_candidates", [])),
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
        "enriched_candidates": len(stage_data.get("enriched_candidates", [])),
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
            warnings.append(f"[market_state] {reason} \u2014 proceeding without market context")
            stage_data["market_state_ref"] = None
            stage_data["consumer_summary"] = {}
            stage_data["composite"] = {}
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

        return StageOutcome(
            stage_key="load_market_state",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.warning("load_market_state unavailable: %s", exc, exc_info=True)
        reason = str(exc)
        warnings.append(f"[market_state] {reason} \u2014 proceeding without market context")
        stage_data["market_state_ref"] = None
        stage_data["consumer_summary"] = {}
        stage_data["composite"] = {}
        return StageOutcome(
            stage_key="load_market_state",
            status="degraded",
            started_at=started,
            completed_at=_now_iso(),
            error=reason,
        )


async def _stage_scan(
    config: RunnerConfig,
    deps: OptionsOpportunityDeps,
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 2: Run V2 options scanners via options_scanner_service.

    The options_scanner_service orchestrates V2 scanner families across
    the configured symbol universe.  Each scanner family calls Tradier
    internally for option chains.

    The workflow treats options_scanner_service as the data-provider
    boundary: the runner itself never calls Tradier or any market-data API.
    """
    started = _now_iso()
    try:
        scan_result = await deps.options_scanner_service.scan(
            symbols=list(config.symbols),
            scanner_keys=list(config.scanner_keys),
            context={
                "market_state_ref": stage_data.get("market_state_ref"),
                "consumer_summary": stage_data.get("consumer_summary"),
            },
        )
        stage_data["scan_result"] = scan_result

        scan_results = scan_result.get("scan_results", [])
        scan_warnings = scan_result.get("warnings", [])

        for w in scan_warnings:
            warnings.append(f"[scan] {w}")

        # Collect all passed candidates across all scan results.
        all_candidates: list[dict[str, Any]] = []
        all_rejected: list[dict[str, Any]] = []
        for sr in scan_results:
            all_candidates.extend(sr.get("candidates", []))
            all_rejected.extend(sr.get("rejected", []))

        stage_data["raw_candidates"] = all_candidates
        stage_data["rejected_candidates"] = all_rejected
        stage_data["scan_results"] = scan_results
        stage_data["scanners_total"] = scan_result.get("scanners_total", 0)
        stage_data["scanners_ok"] = scan_result.get("scanners_ok", 0)
        stage_data["scanners_failed"] = scan_result.get("scanners_failed", 0)

        # Build aggregate scan diagnostics.
        stage_data["scan_diagnostics"] = _extract_scan_diagnostics(scan_results)

        logger.info(
            "Scan complete: %d passed candidates, %d rejected from %d scanner runs",
            len(all_candidates),
            len(all_rejected),
            len(scan_results),
        )

        return StageOutcome(
            stage_key="scan",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("scan stage failed: %s", exc, exc_info=True)
        return StageOutcome(
            stage_key="scan",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


def _stage_validate_math(
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 3: Surface structural validation, math validation, and trust hygiene.

    V2 scanners already perform full validation in phases C-E during
    scanning.  This stage surfaces those results explicitly as workflow
    artifacts and applies any workflow-level validation/filtering.

    Preserved outputs:
    - Structural validation results (phase C checks) per candidate
    - Recomputed math validation results (phase E checks) per candidate
    - Trust hygiene outcomes (quote sanity, liquidity sanity)
    - Reason codes from V2 rejection taxonomy

    Candidates that passed V2 scanning (passed=True) are forwarded.
    Candidates with downstream_usable=False are filtered with reason tracking.
    """
    started = _now_iso()
    try:
        raw_candidates: list[dict[str, Any]] = stage_data.get("raw_candidates", [])

        validated: list[dict[str, Any]] = []
        filtered_count = 0
        filter_reasons: dict[str, int] = {}

        for cand in raw_candidates:
            # V2 candidates arrive with passed/downstream_usable already set.
            if not cand.get("downstream_usable", False):
                filtered_count += 1
                diag = cand.get("diagnostics", {})
                for reason in diag.get("reject_reasons", ["unknown"]):
                    filter_reasons[reason] = filter_reasons.get(reason, 0) + 1
                continue

            # Preserve candidate with full V2 data for next stages.
            validated.append(cand)

        if filtered_count:
            warnings.append(
                f"[validate_math] {filtered_count} candidate(s) filtered "
                f"as not downstream-usable"
            )

        stage_data["validated_candidates"] = validated
        stage_data["validation_filter_reasons"] = filter_reasons
        stage_data["validation_filtered_count"] = filtered_count

        # Build validation summary from validated candidates.
        val_summary = _build_validation_summary(validated)
        stage_data["validation_summary"] = val_summary

        logger.info(
            "Validation: %d validated, %d filtered",
            len(validated),
            filtered_count,
        )

        return StageOutcome(
            stage_key="validate_math",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("validate_math stage failed: %s", exc, exc_info=True)
        return StageOutcome(
            stage_key="validate_math",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


def _build_validation_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate validation summary across all validated candidates."""
    total = len(candidates)
    structural_all_pass = 0
    math_all_pass = 0
    quote_ok_count = 0
    liquidity_ok_count = 0
    candidates_with_warnings = 0

    for cand in candidates:
        diag = cand.get("diagnostics", {})

        # Structural checks
        s_checks = diag.get("structural_checks", [])
        if all(c.get("passed", False) for c in s_checks):
            structural_all_pass += 1

        # Math checks
        m_checks = diag.get("math_checks", [])
        if all(c.get("passed", False) for c in m_checks):
            math_all_pass += 1

        # Quote sanity
        q_checks = diag.get("quote_checks", [])
        if all(c.get("passed", False) for c in q_checks):
            quote_ok_count += 1

        # Liquidity sanity
        l_checks = diag.get("liquidity_checks", [])
        if all(c.get("passed", False) for c in l_checks):
            liquidity_ok_count += 1

        # Warnings
        if diag.get("warnings"):
            candidates_with_warnings += 1

    return {
        "total_validated": total,
        "structural_all_passed": structural_all_pass,
        "math_all_passed": math_all_pass,
        "quote_sanity_ok": quote_ok_count,
        "liquidity_sanity_ok": liquidity_ok_count,
        "candidates_with_warnings": candidates_with_warnings,
    }


def _stage_enrich_evaluate(
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 4: Enrich and evaluate validated candidates.

    Enrichment adds:
    - ``market_state_ref`` (upstream lineage reference)
    - ``market_regime`` (from consumer_summary if available)
    - ``risk_environment`` (from composite if available)

    Evaluation:
    - Sort by EV descending (primary), then RoR descending (secondary).
    - Assign ``rank`` (1-based).
    - Do not create fake precision — use only fields the V2 scanner
      already computed honestly.
    """
    started = _now_iso()
    try:
        candidates: list[dict[str, Any]] = stage_data.get("validated_candidates", [])
        market_state_ref = stage_data.get("market_state_ref")
        consumer_summary = stage_data.get("consumer_summary") or {}
        composite = stage_data.get("composite") or {}

        # Extract regime + risk context from market state.
        # consumer_summary keys: market_state, support_state, stability_state,
        #   confidence, vix, regime_tags, is_degraded, summary_text
        market_regime = consumer_summary.get("market_state")
        risk_environment = consumer_summary.get("stability_state")

        enriched: list[dict[str, Any]] = []
        for cand in candidates:
            enriched_cand = dict(cand)
            enriched_cand["market_state_ref"] = market_state_ref
            enriched_cand["market_regime"] = market_regime
            enriched_cand["risk_environment"] = risk_environment
            enriched.append(enriched_cand)

        # ── Credibility gate ─────────────────────────────────────
        # Filter out trades that are technically valid but represent
        # worthless deep-OTM options with no real premium.
        # Criteria:
        #   1. net_credit or net_debit must be >= $0.05 per share
        #   2. POP must be < 0.995 (pop=1.0 means short delta=0, worthless)
        #   3. At least one leg must have bid > 0 (fillable)
        MIN_PREMIUM = 0.05       # per-share minimum net premium
        MAX_POP_THRESHOLD = 0.995  # reject delta-zero shorts
        credible: list[dict[str, Any]] = []
        credibility_rejections = 0
        credibility_reasons: dict[str, int] = {}
        for cand in enriched:
            math = cand.get("math") or {}
            legs = cand.get("legs", [])

            net_credit = _safe_float(math.get("net_credit"))
            net_debit = _safe_float(math.get("net_debit"))
            pop = _safe_float(math.get("pop"))
            max_premium = max(net_credit, net_debit)

            # Check 1: minimum premium
            if max_premium < MIN_PREMIUM:
                credibility_rejections += 1
                credibility_reasons["penny_premium"] = credibility_reasons.get("penny_premium", 0) + 1
                continue

            # Check 2: pop must indicate a real trade (delta != 0)
            if pop >= MAX_POP_THRESHOLD:
                credibility_rejections += 1
                credibility_reasons["zero_delta_short"] = credibility_reasons.get("zero_delta_short", 0) + 1
                continue

            # Check 3: at least one leg must be fillable (bid > 0)
            has_fillable_leg = any(
                _safe_float(leg.get("bid")) > 0 for leg in legs
            )
            if not has_fillable_leg:
                credibility_rejections += 1
                credibility_reasons["all_legs_zero_bid"] = credibility_reasons.get("all_legs_zero_bid", 0) + 1
                continue

            credible.append(cand)

        logger.info(
            "[enrich] Credibility gate: %d → %d (rejected %d: %s)",
            len(enriched), len(credible),
            credibility_rejections, credibility_reasons,
        )
        stage_data["credibility_filter"] = {
            "input_count": len(enriched),
            "passed_count": len(credible),
            "rejected_count": credibility_rejections,
            "rejection_reasons": credibility_reasons,
        }

        # Sort by EV descending (None → bottom), then RoR descending for ties.
        credible.sort(
            key=lambda c: (
                -_safe_float((c.get("math") or {}).get("ev")),
                -_safe_float((c.get("math") or {}).get("ror")),
                c.get("symbol", ""),
            ),
        )

        # Assign rank.
        for i, cand in enumerate(credible, start=1):
            cand["rank"] = i

        stage_data["enriched_candidates"] = credible

        return StageOutcome(
            stage_key="enrich_evaluate",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("enrich_evaluate failed: %s", exc, exc_info=True)
        return StageOutcome(
            stage_key="enrich_evaluate",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


def _stage_select_package(
    config: RunnerConfig,
    run_id: str,
    started_ts: datetime,
    stage_data: dict[str, Any],
    all_stages: list[StageOutcome],
    warnings: list[str],
) -> StageOutcome:
    """Stage 5: Select top-N, write output, summary, manifest, and pointer.

    Writes (atomically):
    1. ``stage_select_package.json``  — full selected candidates
    2. ``output.json``                — compact consumer output
    3. ``summary.json``               — run summary
    4. ``manifest.json``              — run-level index
    5. ``latest.json``                — workflow pointer update
    """
    started = _now_iso()
    try:
        enriched: list[dict[str, Any]] = stage_data.get("enriched_candidates", [])
        top_n = config.top_n
        selected = enriched[:top_n]

        market_state_ref = stage_data.get("market_state_ref")
        consumer_result: MarketStateConsumerResult | None = stage_data.get("market_state_consumer")
        pub_status = (
            consumer_result.publication_status
            if consumer_result
            else None
        )

        # ── Determine quality ────────────────────────────────────
        total_candidates = len(enriched)
        selected_count = len(selected)
        scanners_ok = stage_data.get("scanners_ok", 0)
        scanners_total = stage_data.get("scanners_total", 0)
        scan_diag = stage_data.get("scan_diagnostics", {})
        validation_summary = stage_data.get("validation_summary", {})

        quality_level = "good"
        if total_candidates == 0:
            quality_level = "no_candidates"
        elif scanners_ok < scanners_total:
            quality_level = "degraded"

        # ── Determine batch status ───────────────────────────────
        # batch_status: "completed" | "partial"
        # "partial" = CancelledError or unexpected error interrupted pipeline
        _has_interruption = any(
            "[pipeline] Run interrupted" in w or "[pipeline] Unexpected error" in w
            for w in warnings
        )
        batch_status = "partial" if _has_interruption else "completed"

        # ── Build compact candidates for output.json ─────────────
        compact_candidates = [_extract_compact_candidate(c) for c in selected]

        # Attach market_state_ref and rank to compact candidates.
        for i, cc in enumerate(compact_candidates, start=1):
            cc["market_state_ref"] = market_state_ref
            cc["rank"] = i

        # ── Log payload field completeness ────────────────────────
        # Helps distinguish scanner success from card-contract completeness.
        if compact_candidates:
            sample = compact_candidates[0]
            math_sample = sample.get("math", {})
            _payload_fields = {
                "candidate_id": sample.get("candidate_id") is not None,
                "symbol": sample.get("symbol") is not None,
                "strategy_id": sample.get("strategy_id") is not None,
                "family_key": sample.get("family_key") is not None,
                "expiration": sample.get("expiration") is not None,
                "dte": sample.get("dte") is not None,
                "underlying_price": sample.get("underlying_price") is not None,
                "legs": len(sample.get("legs", [])) > 0,
                "math.net_credit": math_sample.get("net_credit") is not None,
                "math.net_debit": math_sample.get("net_debit") is not None,
                "math.max_profit": math_sample.get("max_profit") is not None,
                "math.max_loss": math_sample.get("max_loss") is not None,
                "math.width": math_sample.get("width") is not None,
                "math.pop": math_sample.get("pop") is not None,
                "math.ev": math_sample.get("ev") is not None,
                "math.ror": math_sample.get("ror") is not None,
                "math.ev_per_day": math_sample.get("ev_per_day") is not None,
                "math.breakeven": len(math_sample.get("breakeven", [])) > 0,
            }
            present = [k for k, v in _payload_fields.items() if v]
            absent = [k for k, v in _payload_fields.items() if not v]
            logger.info(
                "[select_package] Payload field audit (%d selected): present=%s absent=%s",
                selected_count, present, absent,
            )

        # ── Build output.json ────────────────────────────────────
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
                "top_n_cap": top_n,
                "scanners_ok": scanners_ok,
                "scanners_total": scanners_total,
                "credibility_filter": stage_data.get("credibility_filter"),
            },
            "scan_diagnostics": {
                "total_constructed": scan_diag.get("total_constructed", 0),
                "total_passed": scan_diag.get("total_passed", 0),
                "total_rejected": scan_diag.get("total_rejected", 0),
                "reject_reason_counts": scan_diag.get("reject_reason_counts", {}),
            },
            "validation_summary": validation_summary,
        }

        # ── Build summary.json ───────────────────────────────────
        stage_list = []
        for i, so in enumerate(all_stages):
            stage_list.append({
                "stage_key": so.stage_key,
                "stage_index": i,
                "status": so.status,
            })
        stage_list.append({
            "stage_key": "select_package",
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
            "scan_diagnostics_summary": {
                "total_constructed": scan_diag.get("total_constructed", 0),
                "total_passed": scan_diag.get("total_passed", 0),
                "total_rejected": scan_diag.get("total_rejected", 0),
            },
            "validation_summary": validation_summary,
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
                stage_key="select_package",
                stage_index=len(all_stages),
                status="completed",
                artifact_filename=make_stage_filename("select_package"),
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
            "stage_key": "select_package",
            "stage_index": len(all_stages),
            "generated_at": completed_at,
            "status": "completed",
            "selected_count": selected_count,
            "top_n_cap": top_n,
            "candidates": selected,
        }
        stage_path = get_stage_artifact_path(data_dir, WORKFLOW_ID, run_id, "select_package")
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
            "[options_opportunity] Pointer updated: run_id=%s batch_status=%s",
            run_id, batch_status,
        )

        # Store for result.
        stage_data["publication_status"] = output_data["publication"]["status"]
        stage_data["artifact_filename"] = "output.json"
        stage_data["artifact_path"] = output_path

        return StageOutcome(
            stage_key="select_package",
            status="completed",
            started_at=started,
            completed_at=completed_at,
        )
    except Exception as exc:
        logger.error("select_package failed: %s", exc, exc_info=True)
        return StageOutcome(
            stage_key="select_package",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


# ═══════════════════════════════════════════════════════════════════════
# STAGE ARTIFACT WRITER (for stages 1-4)
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

    elif stage_key == "scan":
        scan_diag = stage_data.get("scan_diagnostics", {})
        base["total_passed_candidates"] = len(stage_data.get("raw_candidates", []))
        base["total_rejected_candidates"] = len(stage_data.get("rejected_candidates", []))
        base["scanners_total"] = stage_data.get("scanners_total", 0)
        base["scanners_ok"] = stage_data.get("scanners_ok", 0)
        base["scanners_failed"] = stage_data.get("scanners_failed", 0)
        base["scan_diagnostics"] = scan_diag

    elif stage_key == "validate_math":
        base["validated_count"] = len(stage_data.get("validated_candidates", []))
        base["filtered_count"] = stage_data.get("validation_filtered_count", 0)
        base["filter_reasons"] = stage_data.get("validation_filter_reasons", {})
        base["validation_summary"] = stage_data.get("validation_summary", {})

    elif stage_key == "enrich_evaluate":
        enriched = stage_data.get("enriched_candidates", [])
        base["enriched_count"] = len(enriched)
        # Include full enriched list for debug/replay.
        base["candidates"] = enriched

    try:
        path = get_stage_artifact_path(data_dir, WORKFLOW_ID, run_id, stage_key)
        atomic_write_json(path, base)
    except Exception as exc:
        logger.warning("Failed to write stage artifact %s: %s", stage_key, exc)
