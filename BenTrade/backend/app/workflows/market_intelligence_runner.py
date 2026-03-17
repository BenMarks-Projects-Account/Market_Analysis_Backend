"""Market Intelligence workflow runner — Prompt 4.

The scheduled producer workflow that collects market data, runs engines,
executes model interpretation, and publishes the canonical market_state.json.

Stage flow
----------
1. collect_inputs           — Fetch macro metrics from market_context_service
2. build_snapshot           — Normalize into market_snapshot section
3. run_engines              — Execute all 6 engines, normalize outputs
4. run_model_interpretation — LLM-based market analysis (degradation-safe)
5. assemble_market_state    — Build full 15-key artifact
6. publish_market_state     — Validate, write timestamped file, update pointer

Greenfield design — does NOT reference archived pipeline code.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services.conflict_detector import detect_conflicts
from app.services.engine_output_contract import normalize_engine_output
from app.services.market_composite import build_market_composite
from app.workflows.architecture import FreshnessPolicy
from app.workflows.artifact_strategy import atomic_write_json, make_run_id
from app.workflows.market_state_contract import (
    ENGINE_KEYS,
    MACRO_METRIC_KEYS,
    MARKET_STATE_CONTRACT_VERSION,
    OverallQuality,
    PublicationStatus,
    validate_market_state,
)
from app.workflows.market_state_discovery import (
    PointerData,
    get_market_state_dir,
    make_artifact_filename,
    write_pointer,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

MI_STAGES: tuple[str, ...] = (
    "collect_inputs",
    "build_snapshot",
    "run_engines",
    "run_model_interpretation",
    "assemble_market_state",
    "publish_market_state",
)

# engine_key → (deps attribute name, async method name)
_ENGINE_DISPATCH: dict[str, tuple[str, str]] = {
    "breadth_participation": ("breadth_service", "get_breadth_analysis"),
    "volatility_options": (
        "volatility_options_service",
        "get_volatility_analysis",
    ),
    "cross_asset_macro": (
        "cross_asset_macro_service",
        "get_cross_asset_analysis",
    ),
    "flows_positioning": (
        "flows_positioning_service",
        "get_flows_positioning_analysis",
    ),
    "liquidity_financial_conditions": (
        "liquidity_conditions_service",
        "get_liquidity_conditions_analysis",
    ),
    "news_sentiment": ("news_sentiment_service", "get_news_sentiment"),
}


# ═══════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class RunnerConfig:
    """Configuration for a Market Intelligence workflow run."""

    data_dir: str | Path
    freshness_policy: FreshnessPolicy | None = None


@dataclass
class MarketIntelligenceDeps:
    """Injectable service dependencies for the MI runner.

    All engine services are required.  ``http_client`` and
    ``model_request_fn`` are optional — when absent, model
    interpretation is skipped rather than failed.
    """

    market_context_service: Any
    breadth_service: Any
    volatility_options_service: Any
    cross_asset_macro_service: Any
    flows_positioning_service: Any
    liquidity_conditions_service: Any
    news_sentiment_service: Any
    http_client: Any | None = None
    model_request_fn: Callable | None = None


@dataclass
class StageOutcome:
    """Records what happened at one stage."""

    stage_key: str
    status: str  # "completed" | "failed" | "skipped"
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
    """Compact structured result of one MI workflow run."""

    run_id: str
    workflow_id: str = "market_intelligence"
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
# MAIN RUNNER
# ═══════════════════════════════════════════════════════════════════════


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_market_intelligence(
    config: RunnerConfig,
    deps: MarketIntelligenceDeps,
    on_stage_fn: Callable[[str], None] | None = None,
) -> RunResult:
    """Execute one complete Market Intelligence workflow run.

    This is the primary entry point.  Suitable for scheduled execution.
    Returns a ``RunResult`` whether the run succeeds, degrades, or fails.
    Never raises — all errors are captured in the result.

    ``on_stage_fn`` — optional sync callback invoked with the stage key
    before each stage begins (e.g. ``"collect_inputs"``, ``"run_model_interpretation"``).
    """
    now = datetime.now(timezone.utc)
    run_id = make_run_id(now)
    result = RunResult(run_id=run_id, started_at=now.isoformat())

    def _notify(stage_key: str) -> None:
        if on_stage_fn:
            try:
                on_stage_fn(stage_key)
            except Exception:
                pass  # never let callback errors crash the workflow

    # Mutable accumulators shared across stages.
    stage_data: dict[str, Any] = {}
    stages: list[StageOutcome] = []
    warnings: list[str] = []
    policy = config.freshness_policy or FreshnessPolicy()

    # ── Stage 1: collect_inputs ──────────────────────────────────
    _notify("collect_inputs")
    outcome = await _stage_collect_inputs(deps, stage_data)
    stages.append(outcome)
    if outcome.status == "failed":
        result.status = "failed"
        result.error = f"collect_inputs failed: {outcome.error}"
        result.stages = [s.to_dict() for s in stages]
        result.completed_at = _now_iso()
        return result

    # ── Stage 2: build_snapshot ──────────────────────────────────
    _notify("build_snapshot")
    outcome = _stage_build_snapshot(stage_data, policy)
    stages.append(outcome)

    # ── Stage 3: run_engines ─────────────────────────────────────
    _notify("run_engines")
    outcome = await _stage_run_engines(deps, stage_data, warnings)
    stages.append(outcome)

    # ── Stage 4: run_model_interpretation ────────────────────────
    _notify("run_model_interpretation")
    outcome = await _stage_run_model_interpretation(
        deps, stage_data, warnings,
    )
    stages.append(outcome)

    # ── Stage 5: assemble_market_state ───────────────────────────
    _notify("assemble_market_state")
    outcome = _stage_assemble_market_state(
        run_id, now, stage_data, warnings,
    )
    stages.append(outcome)
    if outcome.status == "failed":
        result.status = "failed"
        result.error = f"assemble failed: {outcome.error}"
        result.stages = [s.to_dict() for s in stages]
        result.completed_at = _now_iso()
        return result

    # ── Stage 6: publish_market_state ────────────────────────────
    _notify("publish_market_state")
    outcome = _stage_publish_market_state(
        config, run_id, now, stage_data, warnings,
    )
    stages.append(outcome)

    # ── Finalize result ──────────────────────────────────────────
    result.stages = [s.to_dict() for s in stages]
    result.warnings = warnings
    result.completed_at = _now_iso()
    result.publication_status = stage_data.get("publication_status")
    result.artifact_filename = stage_data.get("artifact_filename")
    ap = stage_data.get("artifact_path")
    result.artifact_path = str(ap) if ap else None

    if outcome.status == "failed":
        result.status = "failed"
        result.error = f"publish failed: {outcome.error}"
    else:
        result.status = "completed"

    return result


# ═══════════════════════════════════════════════════════════════════════
# STAGE IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════


async def _stage_collect_inputs(
    deps: MarketIntelligenceDeps,
    stage_data: dict[str, Any],
) -> StageOutcome:
    """Stage 1: Gather macro metrics from market_context_service."""
    started = _now_iso()
    try:
        market_context = await deps.market_context_service.get_market_context()
        stage_data["market_context_raw"] = market_context
        return StageOutcome(
            stage_key="collect_inputs",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("collect_inputs failed: %s", exc, exc_info=True)
        return StageOutcome(
            stage_key="collect_inputs",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


def _stage_build_snapshot(
    stage_data: dict[str, Any],
    policy: FreshnessPolicy,
) -> StageOutcome:
    """Stage 2: Normalize market context into market_snapshot section."""
    started = _now_iso()
    try:
        raw = stage_data.get("market_context_raw", {})
        metrics: dict[str, Any] = {}
        sources_total = 0
        sources_available = 0
        sources_degraded = 0
        sources_failed = 0

        for key in MACRO_METRIC_KEYS:
            sources_total += 1
            metric_data = raw.get(key)
            if metric_data is None:
                sources_failed += 1
                metrics[key] = None
                continue
            value = (
                metric_data.get("value")
                if isinstance(metric_data, dict)
                else None
            )
            if value is None:
                sources_degraded += 1
            else:
                sources_available += 1
            metrics[key] = metric_data

        snapshot_at = raw.get("context_generated_at", _now_iso())

        stage_data["market_snapshot"] = {
            "metrics": metrics,
            "snapshot_at": snapshot_at,
        }
        stage_data["source_health"] = {
            "sources_total": sources_total,
            "sources_available": sources_available,
            "sources_degraded": sources_degraded,
            "sources_failed": sources_failed,
        }
        # Pre-compute freshness for the assembled dict used by composite/
        # conflict detection later.
        stage_data["freshness"] = _build_freshness_section(
            stage_data["market_snapshot"], policy,
        )

        return StageOutcome(
            stage_key="build_snapshot",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error("build_snapshot failed: %s", exc, exc_info=True)
        stage_data["market_snapshot"] = {"metrics": {}, "snapshot_at": _now_iso()}
        stage_data["source_health"] = {
            "sources_total": 0,
            "sources_available": 0,
            "sources_degraded": 0,
            "sources_failed": 0,
        }
        stage_data["freshness"] = {"overall": "unknown", "per_source": {}}
        return StageOutcome(
            stage_key="build_snapshot",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


async def _stage_run_engines(
    deps: MarketIntelligenceDeps,
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 3: Execute all 6 market engines and normalize outputs."""
    started = _now_iso()
    engines_output: dict[str, Any] = {}
    engines_succeeded = 0
    engines_degraded = 0
    engines_failed = 0

    for engine_key in ENGINE_KEYS:
        dispatch = _ENGINE_DISPATCH.get(engine_key)
        if dispatch is None:
            engines_failed += 1
            warnings.append(f"No dispatch entry for engine: {engine_key}")
            continue

        attr_name, method_name = dispatch
        service = getattr(deps, attr_name, None)
        if service is None:
            engines_failed += 1
            warnings.append(f"Service not provided: {engine_key}")
            continue

        try:
            method = getattr(service, method_name)
            raw_result = await method(force=True)

            # Prefer pre-normalized output; fall back to contract normalizer.
            normalized = (
                raw_result.get("normalized")
                if isinstance(raw_result, dict)
                else None
            )
            if normalized is None:
                normalized = normalize_engine_output(engine_key, raw_result)

            engines_output[engine_key] = normalized

            engine_status = (
                normalized.get("engine_status", "ok")
                if isinstance(normalized, dict)
                else "error"
            )
            if engine_status == "ok":
                engines_succeeded += 1
            elif engine_status == "degraded":
                engines_degraded += 1
                warnings.append(
                    f"Engine {engine_key} returned degraded status"
                )
            else:
                engines_failed += 1
                warnings.append(
                    f"Engine {engine_key} status: {engine_status}"
                )
        except Exception as exc:
            engines_failed += 1
            warnings.append(f"Engine {engine_key} failed: {exc}")
            logger.error(
                "Engine %s failed: %s", engine_key, exc, exc_info=True,
            )

    stage_data["engines"] = engines_output
    stage_data["engine_health"] = {
        "engines_total": len(ENGINE_KEYS),
        "engines_succeeded": engines_succeeded,
        "engines_degraded": engines_degraded,
        "engines_failed": engines_failed,
    }

    overall_quality = _determine_overall_quality(
        engines_succeeded, engines_degraded, engines_failed,
    )
    freshness = stage_data.get("freshness", {})

    # Build the "assembled" dict consumed by conflict_detector +
    # market_composite (preserved domain logic).
    stage_data["assembled"] = {
        "market_context": engines_output,
        "candidate_context": {},
        "model_context": {},
        "quality_summary": {
            "overall_quality": overall_quality,
            "degraded_count": engines_degraded,
        },
        "freshness_summary": {
            "overall_freshness": freshness.get("overall", "unknown"),
        },
        "horizon_summary": {},
    }

    status = "completed" if (engines_succeeded + engines_degraded) > 0 else "failed"
    return StageOutcome(
        stage_key="run_engines",
        status=status,
        started_at=started,
        completed_at=_now_iso(),
        error=None if status == "completed" else "All engines failed",
    )


async def _stage_run_model_interpretation(
    deps: MarketIntelligenceDeps,
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 4: LLM-based market interpretation (degradation-safe).

    Model interpretation failure does NOT fail the workflow — the
    market_state artifact remains usable without it.
    """
    started = _now_iso()

    # Skip if no engine data to interpret.
    engines = stage_data.get("engines", {})
    if not engines:
        stage_data["model_interpretation"] = {"status": "skipped"}
        warnings.append("Model interpretation skipped: no engine data")
        return StageOutcome(
            stage_key="run_model_interpretation",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )

    # Skip if model endpoint is not configured.
    if deps.model_request_fn is None or deps.http_client is None:
        stage_data["model_interpretation"] = {"status": "skipped"}
        warnings.append(
            "Model interpretation skipped: no model endpoint configured"
        )
        return StageOutcome(
            stage_key="run_model_interpretation",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )

    try:
        prompt_context = _build_model_interpretation_context(engines)
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": _MODEL_INTERPRETATION_SYSTEM},
                {"role": "user", "content": prompt_context},
            ],
            "temperature": 0.3,
            "stream": False,
        }

        response = await deps.model_request_fn(
            deps.http_client, payload,
        )

        # Handle httpx.Response or raw dict.
        if hasattr(response, "status_code"):
            if response.status_code != 200:
                raise RuntimeError(
                    f"Model returned status {response.status_code}"
                )
            content = response.json()
        elif isinstance(response, dict):
            content = response
        else:
            raise RuntimeError(
                f"Unexpected model response type: {type(response)}"
            )

        choices = content.get("choices", [])
        if not choices:
            raise RuntimeError("Model returned no choices")

        text = choices[0].get("message", {}).get("content", "")
        interpretation = _parse_model_interpretation(text)
        stage_data["model_interpretation"] = interpretation

        return StageOutcome(
            stage_key="run_model_interpretation",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error(
            "Model interpretation failed: %s", exc, exc_info=True,
        )
        warnings.append(f"Model interpretation failed: {exc}")
        stage_data["model_interpretation"] = {"status": "failed"}
        return StageOutcome(
            stage_key="run_model_interpretation",
            status="completed",  # model failure does not fail the workflow
            started_at=started,
            completed_at=_now_iso(),
        )


def _stage_assemble_market_state(
    run_id: str,
    timestamp: datetime,
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 5: Build the canonical 15-key market_state artifact."""
    started = _now_iso()
    try:
        engines = stage_data.get("engines", {})
        engine_health = stage_data.get("engine_health", {})
        source_health = stage_data.get("source_health", {})
        market_snapshot = stage_data.get("market_snapshot", {})
        model_interp = stage_data.get("model_interpretation")
        freshness = stage_data.get("freshness", {"overall": "unknown", "per_source": {}})
        assembled = stage_data.get("assembled", {})

        # Run composite and conflict detection.
        conflict_report = None
        composite = None
        try:
            conflict_report = detect_conflicts(assembled)
        except Exception as exc:
            warnings.append(f"Conflict detection failed: {exc}")

        try:
            composite = build_market_composite(assembled, conflict_report)
        except Exception as exc:
            warnings.append(f"Composite build failed: {exc}")

        # Determine publication status from actual outcomes.
        pub_status = _determine_publication_status(
            engine_health, source_health, model_interp,
        )

        overall_quality = _determine_overall_quality(
            engine_health.get("engines_succeeded", 0),
            engine_health.get("engines_degraded", 0),
            engine_health.get("engines_failed", 0),
        )

        quality = {
            **source_health,
            **engine_health,
            "overall_quality": overall_quality,
        }

        consumer_summary = _build_consumer_summary(
            composite, market_snapshot, pub_status,
        )

        generated_at = timestamp.isoformat()
        artifact: dict[str, Any] = {
            "contract_version": MARKET_STATE_CONTRACT_VERSION,
            "artifact_id": run_id,
            "workflow_id": "market_intelligence",
            "generated_at": generated_at,
            "publication": {
                "status": pub_status,
                "published_at": generated_at,
            },
            "freshness": freshness,
            "quality": quality,
            "market_snapshot": market_snapshot,
            "engines": engines,
            "composite": composite,
            "conflicts": conflict_report,
            "model_interpretation": model_interp,
            "consumer_summary": consumer_summary,
            "lineage": {
                "workflow_id": "market_intelligence",
                "workflow_version": MARKET_STATE_CONTRACT_VERSION,
                "run_id": run_id,
            },
            "warnings": list(warnings),
        }

        stage_data["artifact"] = artifact
        stage_data["publication_status"] = pub_status

        return StageOutcome(
            stage_key="assemble_market_state",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error(
            "assemble_market_state failed: %s", exc, exc_info=True,
        )
        return StageOutcome(
            stage_key="assemble_market_state",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


def _stage_publish_market_state(
    config: RunnerConfig,
    run_id: str,
    timestamp: datetime,
    stage_data: dict[str, Any],
    warnings: list[str],
) -> StageOutcome:
    """Stage 6: Validate and atomically publish the artifact."""
    started = _now_iso()
    try:
        artifact = stage_data.get("artifact")
        if artifact is None:
            return StageOutcome(
                stage_key="publish_market_state",
                status="failed",
                started_at=started,
                completed_at=_now_iso(),
                error="No artifact to publish",
            )

        # Structural validation (warnings, not blocking).
        validation = validate_market_state(artifact)
        if not validation.is_valid:
            warnings.append(
                f"Artifact validation issues: "
                f"missing={validation.missing_keys}, "
                f"invalid={validation.invalid_sections}"
            )

        pub_status = stage_data.get("publication_status", "failed")
        should_update_pointer = pub_status in (
            PublicationStatus.VALID.value,
            PublicationStatus.DEGRADED.value,
        )

        # Write timestamped artifact (always — even failed, for diagnostics).
        data_dir = Path(config.data_dir)
        ms_dir = get_market_state_dir(data_dir)
        artifact_filename = make_artifact_filename(timestamp)
        artifact_path = ms_dir / artifact_filename

        atomic_write_json(artifact_path, artifact)

        stage_data["artifact_filename"] = artifact_filename
        stage_data["artifact_path"] = artifact_path

        # Update pointer ONLY when the artifact is consumable.
        if should_update_pointer:
            pointer = PointerData(
                artifact_filename=artifact_filename,
                artifact_id=run_id,
                published_at=timestamp.isoformat(),
                status=pub_status,
                contract_version=MARKET_STATE_CONTRACT_VERSION,
            )
            write_pointer(data_dir, pointer)
            logger.info(
                "Published market state: %s (status=%s)",
                artifact_filename,
                pub_status,
            )
        else:
            warnings.append(
                f"Artifact written but pointer NOT updated "
                f"(publication_status={pub_status})"
            )
            logger.warning(
                "Artifact %s written but not published (status=%s)",
                artifact_filename,
                pub_status,
            )

        return StageOutcome(
            stage_key="publish_market_state",
            status="completed",
            started_at=started,
            completed_at=_now_iso(),
        )
    except Exception as exc:
        logger.error(
            "publish_market_state failed: %s", exc, exc_info=True,
        )
        return StageOutcome(
            stage_key="publish_market_state",
            status="failed",
            started_at=started,
            completed_at=_now_iso(),
            error=str(exc),
        )


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════


def _determine_publication_status(
    engine_health: dict[str, Any],
    source_health: dict[str, Any],
    model_interp: dict[str, Any] | None,
) -> str:
    """Derive publication status from engine/source/model outcomes.

    Formula inputs:
        engine_health.engines_succeeded / engines_degraded / engines_failed
        source_health.sources_failed / sources_degraded
        model_interp.status  (ok | skipped | failed)

    Rules:
        0 engines succeeded+degraded          → FAILED
        succeeded+degraded < half of total    → INCOMPLETE
        any degraded/failed engines or sources → DEGRADED
        model interpretation failed            → DEGRADED
        everything clean                       → VALID
    """
    succeeded = engine_health.get("engines_succeeded", 0)
    degraded = engine_health.get("engines_degraded", 0)
    failed = engine_health.get("engines_failed", 0)
    total = engine_health.get("engines_total", 6)

    if succeeded == 0 and degraded == 0:
        return PublicationStatus.FAILED.value

    if (succeeded + degraded) < (total // 2):
        return PublicationStatus.INCOMPLETE.value

    if degraded > 0 or failed > 0:
        return PublicationStatus.DEGRADED.value

    if model_interp and model_interp.get("status") == "failed":
        return PublicationStatus.DEGRADED.value

    src_failed = source_health.get("sources_failed", 0)
    src_degraded = source_health.get("sources_degraded", 0)
    if src_failed > 0 or src_degraded > 0:
        return PublicationStatus.DEGRADED.value

    return PublicationStatus.VALID.value


def _determine_overall_quality(
    succeeded: int,
    degraded: int,
    failed: int,
) -> str:
    """Map engine outcome counts to quality vocabulary.

    Formula inputs: succeeded, degraded, failed engine counts.
    Output: one of OverallQuality values.
    """
    total = succeeded + degraded + failed
    if total == 0 or failed == total:
        return OverallQuality.UNAVAILABLE.value
    if succeeded == total:
        return OverallQuality.GOOD.value
    if succeeded >= total - 1 and degraded <= 1 and failed == 0:
        return OverallQuality.ACCEPTABLE.value
    if (succeeded + degraded) >= (total // 2):
        return OverallQuality.DEGRADED.value
    return OverallQuality.POOR.value


def _build_freshness_section(
    market_snapshot: dict[str, Any],
    policy: FreshnessPolicy,
) -> dict[str, Any]:
    """Build the freshness section from snapshot metrics.

    Formula inputs: metric envelopes' fetched_at timestamps.
    Uses policy thresholds for tier classification.
    """
    metrics = market_snapshot.get("metrics", {})
    if not metrics:
        return {"overall": "unknown", "per_source": {}}

    per_source: dict[str, Any] = {}
    tier_rank = {"fresh": 0, "warning": 1, "stale": 2, "unknown": 3}
    worst_tier = "fresh"
    now = datetime.now(timezone.utc)

    for key, metric in metrics.items():
        if metric is None:
            per_source[key] = {
                "tier": "unknown",
                "age_seconds": None,
                "last_update": None,
            }
            if tier_rank.get("unknown", 3) > tier_rank.get(worst_tier, 0):
                worst_tier = "unknown"
            continue

        fetched_at = (
            metric.get("fetched_at") if isinstance(metric, dict) else None
        )
        if fetched_at:
            try:
                fetched = datetime.fromisoformat(fetched_at)
                if fetched.tzinfo is None:
                    fetched = fetched.replace(tzinfo=timezone.utc)
                age_seconds = (now - fetched).total_seconds()
                if age_seconds < policy.warn_after_seconds:
                    tier = "fresh"
                elif age_seconds < policy.degrade_after_seconds:
                    tier = "warning"
                else:
                    tier = "stale"
            except (ValueError, TypeError):
                age_seconds = None
                tier = "unknown"
        else:
            age_seconds = None
            tier = "unknown"

        per_source[key] = {
            "tier": tier,
            "age_seconds": (
                round(age_seconds, 1) if age_seconds is not None else None
            ),
            "last_update": fetched_at,
        }
        if tier_rank.get(tier, 0) > tier_rank.get(worst_tier, 0):
            worst_tier = tier

    return {"overall": worst_tier, "per_source": per_source}


def _build_consumer_summary(
    composite: dict[str, Any] | None,
    market_snapshot: dict[str, Any],
    pub_status: str,
) -> dict[str, Any]:
    """Build the compact consumer_summary section.

    Formula inputs:
        composite.market_state / support_state / stability_state / confidence
        market_snapshot.metrics.vix.value
        pub_status (to set is_degraded)
    """
    metrics = market_snapshot.get("metrics", {})
    vix_data = metrics.get("vix")
    vix_value = None
    if isinstance(vix_data, dict):
        vix_value = vix_data.get("value")

    if composite and isinstance(composite, dict):
        market_state = composite.get("market_state", "neutral")
        support_state = composite.get("support_state", "mixed")
        stability_state = composite.get("stability_state", "noisy")
        confidence = composite.get("confidence", 0.0)
        summary_text = composite.get("summary", "")
        # Collect regime tags from composite metadata if available.
        regime_tags: list[str] = []
        metadata = composite.get("metadata", {})
        if isinstance(metadata, dict):
            tags = metadata.get("regime_tags", [])
            if isinstance(tags, list):
                regime_tags = tags
    else:
        market_state = "neutral"
        support_state = "mixed"
        stability_state = "noisy"
        confidence = 0.0
        summary_text = "Market state composite unavailable."
        regime_tags = []

    return {
        "market_state": market_state,
        "support_state": support_state,
        "stability_state": stability_state,
        "confidence": confidence,
        "vix": vix_value,
        "regime_tags": regime_tags,
        "is_degraded": pub_status != PublicationStatus.VALID.value,
        "summary_text": summary_text,
    }


# ═══════════════════════════════════════════════════════════════════════
# MODEL INTERPRETATION HELPERS
# ═══════════════════════════════════════════════════════════════════════

_MODEL_INTERPRETATION_SYSTEM = (
    "You are an expert market analyst. Analyze the market engine outputs "
    "and produce a structured JSON response with these fields:\n\n"
    "{\n"
    '  "executive_summary": "1-2 sentence overall market assessment",\n'
    '  "regime_breakdown": "Description of current market regime",\n'
    '  "primary_fit": "What strategies fit this environment best",\n'
    '  "avoid_rationale": "What to avoid and why",\n'
    '  "change_triggers": "What would signal a regime change",\n'
    '  "confidence_caveats": "Caveats about confidence",\n'
    '  "key_drivers": ["driver1", "driver2", "driver3"],\n'
    '  "confidence": 0.0\n'
    "}\n\n"
    "Return ONLY valid JSON. No markdown, no explanation."
)


def _build_model_interpretation_context(
    engines: dict[str, Any],
) -> str:
    """Build a compact prompt context from engine outputs."""
    parts = ["Current Market Engine Summary:\n"]

    for engine_key, output in engines.items():
        if not isinstance(output, dict):
            continue
        parts.append(f"\n## {engine_key}")
        parts.append(f"Score: {output.get('score', 'N/A')}")
        parts.append(f"Label: {output.get('label', 'N/A')}")
        parts.append(f"Summary: {output.get('summary', 'N/A')}")
        parts.append(
            f"Trader Takeaway: {output.get('trader_takeaway', 'N/A')}"
        )
        bull = output.get("bull_factors", [])
        bear = output.get("bear_factors", [])
        if bull:
            parts.append(
                f"Bull factors: {', '.join(str(b) for b in bull[:3])}"
            )
        if bear:
            parts.append(
                f"Bear factors: {', '.join(str(b) for b in bear[:3])}"
            )
        parts.append(
            f"Engine status: {output.get('engine_status', 'N/A')}"
        )

    return "\n".join(parts)


def _parse_model_interpretation(text: str) -> dict[str, Any]:
    """Parse the LLM's market interpretation response into contract shape."""
    text = text.strip()

    # Strip markdown code fences if present.
    if text.startswith("```"):
        lines = text.split("\n")
        json_lines: list[str] = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```") and not in_block:
                in_block = True
                continue
            if line.strip() == "```" and in_block:
                break
            if in_block:
                json_lines.append(line)
        text = "\n".join(json_lines)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            parsed["status"] = "ok"
            return parsed
    except json.JSONDecodeError:
        pass

    return {"status": "failed", "raw_content": text[:2000]}


# ═══════════════════════════════════════════════════════════════════════
# SCHEDULER-READY ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════


async def run_scheduled_market_intelligence(
    data_dir: str | Path,
    deps: MarketIntelligenceDeps,
    freshness_policy: FreshnessPolicy | None = None,
    on_stage_fn: Callable[[str], None] | None = None,
) -> RunResult:
    """Scheduler-ready entry point for the MI workflow.

    Intended to be called on a ~5-minute schedule.
    Returns a compact ``RunResult`` for logging / diagnostics.
    """
    config = RunnerConfig(
        data_dir=data_dir,
        freshness_policy=freshness_policy,
    )
    return await run_market_intelligence(config, deps, on_stage_fn=on_stage_fn)
