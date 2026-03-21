"""Trade Management Center execution seam and compact read models — Prompt 7.

Thin backend service that connects the new Stock Opportunity and Options
Opportunity workflow runners to a TMC-oriented execution/read layer.

This module provides:
    1. **Execution seam** — trigger stock/options workflow runs
    2. **Compact read models** — app-facing shapes for latest outputs
    3. **Latest-output readers** — load pointer → output.json → read model
    4. **Status vocabulary** — TMC-facing status enum
    5. **Lightweight run summary reader** — compact run history

Design rules
-------------
- TMC is a thin caller/reader seam, NOT an orchestration engine.
- TMC never parses raw stage artifacts for normal reads.
- TMC loads only ``output.json`` and ``summary.json`` via pointers.
- Execution is delegated to the existing headless runners.
- All market data stays behind runner/service boundaries.
- Lineage (``market_state_ref``, ``run_id``) is preserved in read models.

Greenfield design — does NOT reference archived pipeline code.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app.workflows.artifact_strategy import (
    WorkflowPointerData,
    get_output_path,
    get_pointer_path,
    get_summary_path,
    WORKFLOW_POINTER_REQUIRED_KEYS,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 1. TMC STATUS VOCABULARY
# ═══════════════════════════════════════════════════════════════════════


class TMCStatus(str, Enum):
    """Compact TMC-facing workflow run/output status."""

    COMPLETED = "completed"
    """Run finished successfully with usable output."""

    DEGRADED = "degraded"
    """Run finished but with quality caveats (partial scanners, etc.)."""

    FAILED = "failed"
    """Run failed — no usable output produced."""

    NO_OUTPUT = "no_output"
    """No workflow output exists yet (never run or pointer missing)."""

    UNAVAILABLE = "unavailable"
    """Output exists but cannot be loaded (corrupt, missing file)."""


def _run_status_to_tmc(run_status: str, publication_status: str | None) -> str:
    """Map runner status + publication status to TMC vocabulary."""
    if run_status == "failed":
        return TMCStatus.FAILED
    if publication_status == "degraded":
        return TMCStatus.DEGRADED
    return TMCStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════
# 2. COMPACT EXECUTION RESULT
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TMCExecutionResult:
    """Compact result returned to TMC after triggering a workflow run.

    This is NOT the full RunResult — it is a thin TMC-oriented summary
    of what happened, suitable for API responses.
    """

    workflow_id: str
    run_id: str
    status: str             # TMCStatus value
    started_at: str
    completed_at: str
    candidate_count: int = 0
    warnings_count: int = 0
    market_state_ref: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "candidate_count": self.candidate_count,
            "warnings_count": self.warnings_count,
            "market_state_ref": self.market_state_ref,
        }
        if self.error is not None:
            d["error"] = self.error
        return d


# ═══════════════════════════════════════════════════════════════════════
# 3. COMPACT READ MODELS
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class StockOpportunityReadModel:
    """Compact app-facing read model for latest stock opportunities.

    Built from ``output.json`` — no stage artifact parsing needed.
    """

    run_id: str
    workflow_id: str
    generated_at: str
    market_state_ref: str | None
    status: str                     # TMCStatus value
    batch_status: str               # "completed" | "partial" | unknown
    total_candidates: int
    selected_count: int
    quality_level: str
    candidates: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "generated_at": self.generated_at,
            "market_state_ref": self.market_state_ref,
            "status": self.status,
            "batch_status": self.batch_status,
            "total_candidates": self.total_candidates,
            "selected_count": self.selected_count,
            "quality_level": self.quality_level,
            "candidates": list(self.candidates),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class OptionsOpportunityReadModel:
    """Compact app-facing read model for latest options opportunities.

    Built from ``output.json`` — no stage artifact parsing needed.
    Preserves quantitative richness (EV, POP, max_loss, etc.) from
    the compact candidate shape produced by the options runner.
    """

    run_id: str
    workflow_id: str
    generated_at: str
    market_state_ref: str | None
    status: str                     # TMCStatus value
    batch_status: str               # "completed" | "partial" | unknown
    total_candidates: int
    selected_count: int
    quality_level: str
    candidates: tuple[dict[str, Any], ...]
    scan_diagnostics: dict[str, Any]
    validation_summary: dict[str, Any]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "generated_at": self.generated_at,
            "market_state_ref": self.market_state_ref,
            "status": self.status,
            "batch_status": self.batch_status,
            "total_candidates": self.total_candidates,
            "selected_count": self.selected_count,
            "quality_level": self.quality_level,
            "candidates": list(self.candidates),
            "scan_diagnostics": self.scan_diagnostics,
            "validation_summary": self.validation_summary,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class WorkflowRunSummaryReadModel:
    """Lightweight run summary for TMC display / run-history lists.

    Built from ``summary.json`` — no manifest or stage parsing needed.
    """

    run_id: str
    workflow_id: str
    status: str                     # TMCStatus value
    started_at: str
    completed_at: str
    market_state_ref: str | None
    total_candidates: int
    selected_count: int
    quality_level: str
    stage_count: int
    warnings_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "market_state_ref": self.market_state_ref,
            "total_candidates": self.total_candidates,
            "selected_count": self.selected_count,
            "quality_level": self.quality_level,
            "stage_count": self.stage_count,
            "warnings_count": self.warnings_count,
        }


# ═══════════════════════════════════════════════════════════════════════
# 4. LATEST-OUTPUT READERS
# ═══════════════════════════════════════════════════════════════════════


def _load_pointer(data_dir: Path, workflow_id: str) -> WorkflowPointerData | None:
    """Load and validate a workflow pointer.  Returns None on failure."""
    pointer_path = get_pointer_path(data_dir, workflow_id)
    if not pointer_path.is_file():
        logger.debug("[TMC] No pointer file at %s", pointer_path)
        return None
    try:
        raw = json.loads(pointer_path.read_text(encoding="utf-8"))
        for key in WORKFLOW_POINTER_REQUIRED_KEYS:
            if key not in raw:
                logger.warning("Pointer missing key %s for %s", key, workflow_id)
                return None
        pointer = WorkflowPointerData.from_dict(raw)
        logger.debug(
            "[TMC] Loaded pointer for %s: run_id=%s completed_at=%s batch_status=%s",
            workflow_id, pointer.run_id, pointer.completed_at,
            pointer.batch_status or "n/a",
        )
        return pointer
    except Exception as exc:
        logger.warning("Failed to load pointer for %s: %s", workflow_id, exc)
        return None


def _load_output_json(
    data_dir: Path, workflow_id: str, run_id: str,
) -> dict[str, Any] | None:
    """Load an output.json for a given run.  Returns None on failure."""
    output_path = get_output_path(data_dir, workflow_id, run_id)
    if not output_path.is_file():
        return None
    try:
        return json.loads(output_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load output for %s/%s: %s", workflow_id, run_id, exc)
        return None


def _load_summary_json(
    data_dir: Path, workflow_id: str, run_id: str,
) -> dict[str, Any] | None:
    """Load a summary.json for a given run.  Returns None on failure."""
    summary_path = get_summary_path(data_dir, workflow_id, run_id)
    if not summary_path.is_file():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load summary for %s/%s: %s", workflow_id, run_id, exc)
        return None


def load_latest_stock_output(
    data_dir: str | Path,
) -> StockOpportunityReadModel | None:
    """Load the latest stock opportunities compact read model.

    Uses the pointer → output.json path.  Returns None if no output
    exists yet (``TMCStatus.NO_OUTPUT``).

    Parameters
    ----------
    data_dir : str | Path
        Backend data directory.

    Returns
    -------
    StockOpportunityReadModel | None
        Compact read model, or None if unavailable.
    """
    data_dir = Path(data_dir)
    workflow_id = "stock_opportunity"

    pointer = _load_pointer(data_dir, workflow_id)
    if pointer is None:
        return None

    output = _load_output_json(data_dir, workflow_id, pointer.run_id)
    if output is None:
        return None

    pub = output.get("publication", {})
    quality = output.get("quality", {})
    pub_status = pub.get("status")

    return StockOpportunityReadModel(
        run_id=pointer.run_id,
        workflow_id=workflow_id,
        generated_at=output.get("generated_at", ""),
        market_state_ref=output.get("market_state_ref"),
        status=_run_status_to_tmc("completed", pub_status),
        batch_status=output.get("batch_status") or pointer.batch_status or "completed",
        total_candidates=quality.get("total_candidates_found", 0),
        selected_count=quality.get("selected_count", 0),
        quality_level=quality.get("level", "unknown"),
        candidates=tuple(output.get("candidates", [])),
        warnings=tuple(),
    )


def load_latest_options_output(
    data_dir: str | Path,
) -> OptionsOpportunityReadModel | None:
    """Load the latest options opportunities compact read model.

    Uses the pointer → output.json path.  Returns None if no output
    exists yet (``TMCStatus.NO_OUTPUT``).

    Parameters
    ----------
    data_dir : str | Path
        Backend data directory.

    Returns
    -------
    OptionsOpportunityReadModel | None
        Compact read model with full quant richness, or None if unavailable.
    """
    data_dir = Path(data_dir)
    workflow_id = "options_opportunity"

    pointer = _load_pointer(data_dir, workflow_id)
    if pointer is None:
        return None

    output = _load_output_json(data_dir, workflow_id, pointer.run_id)
    if output is None:
        return None

    pub = output.get("publication", {})
    quality = output.get("quality", {})
    pub_status = pub.get("status")

    return OptionsOpportunityReadModel(
        run_id=pointer.run_id,
        workflow_id=workflow_id,
        generated_at=output.get("generated_at", ""),
        market_state_ref=output.get("market_state_ref"),
        status=_run_status_to_tmc("completed", pub_status),
        batch_status=output.get("batch_status") or pointer.batch_status or "completed",
        total_candidates=quality.get("total_candidates_found", 0),
        selected_count=quality.get("selected_count", 0),
        quality_level=quality.get("level", "unknown"),
        candidates=tuple(output.get("candidates", [])),
        scan_diagnostics=output.get("scan_diagnostics", {}),
        validation_summary=output.get("validation_summary", {}),
        warnings=tuple(),
    )


def load_latest_run_summary(
    data_dir: str | Path,
    workflow_id: str,
) -> WorkflowRunSummaryReadModel | None:
    """Load a lightweight run summary for a given workflow.

    Uses the pointer → summary.json path.  Returns None if no output
    exists yet.

    Parameters
    ----------
    data_dir : str | Path
        Backend data directory.
    workflow_id : str
        ``"stock_opportunity"`` or ``"options_opportunity"``.

    Returns
    -------
    WorkflowRunSummaryReadModel | None
        Compact summary, or None if unavailable.
    """
    data_dir = Path(data_dir)

    pointer = _load_pointer(data_dir, workflow_id)
    if pointer is None:
        return None

    summary = _load_summary_json(data_dir, workflow_id, pointer.run_id)
    if summary is None:
        return None

    stages = summary.get("stages", [])
    run_warnings = summary.get("warnings", [])

    return WorkflowRunSummaryReadModel(
        run_id=pointer.run_id,
        workflow_id=workflow_id,
        status=_run_status_to_tmc(
            summary.get("status", "completed"),
            summary.get("quality_level"),
        ),
        started_at=summary.get("started_at", ""),
        completed_at=summary.get("completed_at", ""),
        market_state_ref=summary.get("market_state_ref"),
        total_candidates=summary.get("total_candidates", 0),
        selected_count=summary.get("selected_count", 0),
        quality_level=summary.get("quality_level", "unknown"),
        stage_count=len(stages),
        warnings_count=len(run_warnings),
    )


# ═══════════════════════════════════════════════════════════════════════
# 5. TMC EXECUTION SERVICE
# ═══════════════════════════════════════════════════════════════════════


class TMCExecutionService:
    """Thin execution seam for Trade Management Center.

    This service wraps the headless workflow runners so TMC can trigger
    runs and read compact results without knowing about file layout,
    stage artifacts, or runner internals.

    Usage
    -----
    ::

        tmc = TMCExecutionService(
            data_dir="BenTrade/backend/data",
            stock_deps=StockOpportunityDeps(stock_engine_service=...),
            options_deps=OptionsOpportunityDeps(options_scanner_service=...),
        )

        # Trigger a run
        result = await tmc.run_stock_opportunities()
        result = await tmc.run_options_opportunities()

        # Read latest compact outputs
        stock_model = tmc.get_latest_stock_opportunities()
        options_model = tmc.get_latest_options_opportunities()

        # Read summaries
        stock_summary = tmc.get_latest_run_summary("stock_opportunity")
    """

    def __init__(
        self,
        data_dir: str | Path,
        stock_deps: Any | None = None,
        options_deps: Any | None = None,
        freshness_policy: Any | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._stock_deps = stock_deps
        self._options_deps = options_deps
        self._freshness_policy = freshness_policy

    # ── Execution triggers ───────────────────────────────────────

    async def run_stock_opportunities(
        self,
        *,
        top_n: int | None = None,
    ) -> TMCExecutionResult:
        """Trigger a Stock Opportunity workflow run.

        Returns a compact ``TMCExecutionResult`` — not the full
        runner ``RunResult``.
        """
        # Late import to avoid circular deps
        from app.workflows.stock_opportunity_runner import (
            RunnerConfig as StockConfig,
            run_stock_opportunity,
        )

        if self._stock_deps is None:
            return TMCExecutionResult(
                workflow_id="stock_opportunity",
                run_id="",
                status=TMCStatus.FAILED,
                started_at=_now_iso(),
                completed_at=_now_iso(),
                error="Stock workflow dependencies not configured",
            )

        config = StockConfig(
            data_dir=self._data_dir,
            freshness_policy=self._freshness_policy,
        )
        if top_n is not None:
            config.top_n = top_n

        rr = await run_stock_opportunity(config, self._stock_deps)
        return _run_result_to_tmc_execution(rr)

    async def run_options_opportunities(
        self,
        *,
        top_n: int | None = None,
        symbols: list[str] | None = None,
    ) -> TMCExecutionResult:
        """Trigger an Options Opportunity workflow run.

        Returns a compact ``TMCExecutionResult`` — not the full
        runner ``RunResult``.
        """
        from app.workflows.options_opportunity_runner import (
            RunnerConfig as OptionsConfig,
            run_options_opportunity,
        )

        if self._options_deps is None:
            return TMCExecutionResult(
                workflow_id="options_opportunity",
                run_id="",
                status=TMCStatus.FAILED,
                started_at=_now_iso(),
                completed_at=_now_iso(),
                error="Options workflow dependencies not configured",
            )

        config = OptionsConfig(
            data_dir=self._data_dir,
            freshness_policy=self._freshness_policy,
        )
        if top_n is not None:
            config.top_n = top_n
        if symbols is not None:
            config.symbols = symbols

        rr = await run_options_opportunity(config, self._options_deps)
        return _run_result_to_tmc_execution(rr)

    # ── Read models ──────────────────────────────────────────────

    def get_latest_stock_opportunities(self) -> StockOpportunityReadModel | None:
        """Load the latest stock opportunities compact read model."""
        return load_latest_stock_output(self._data_dir)

    def get_latest_options_opportunities(self) -> OptionsOpportunityReadModel | None:
        """Load the latest options opportunities compact read model."""
        return load_latest_options_output(self._data_dir)

    def get_latest_run_summary(
        self,
        workflow_id: str,
    ) -> WorkflowRunSummaryReadModel | None:
        """Load a lightweight run summary for a given workflow."""
        return load_latest_run_summary(self._data_dir, workflow_id)


# ═══════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_result_to_tmc_execution(rr: Any) -> TMCExecutionResult:
    """Convert a runner RunResult to a compact TMCExecutionResult.

    Works with both stock and options RunResult shapes (they have
    identical top-level fields).
    """
    # Count candidates from stages if available.
    candidate_count = 0
    if rr.artifact_path:
        try:
            output = json.loads(Path(rr.artifact_path).read_text(encoding="utf-8"))
            candidate_count = len(output.get("candidates", []))
        except Exception:
            pass

    # Extract market_state_ref from first stage if available.
    market_state_ref = None
    for stage in (rr.stages or []):
        if stage.get("stage_key") == "load_market_state":
            break
    if rr.artifact_path:
        try:
            output = json.loads(Path(rr.artifact_path).read_text(encoding="utf-8"))
            market_state_ref = output.get("market_state_ref")
        except Exception:
            pass

    return TMCExecutionResult(
        workflow_id=rr.workflow_id,
        run_id=rr.run_id,
        status=_run_status_to_tmc(rr.status, rr.publication_status),
        started_at=rr.started_at,
        completed_at=rr.completed_at,
        candidate_count=candidate_count,
        warnings_count=len(rr.warnings or []),
        market_state_ref=market_state_ref,
        error=rr.error,
    )
