"""File-backed artifact strategy for all BenTrade workflows.

This module defines the canonical directory layout, naming conventions,
artifact categories, lineage rules, atomic publish strategy, retention
philosophy, and manifest/index conventions used across all new workflows.

Greenfield design — does NOT reference archived pipeline code.

Directory layout (relative to ``BenTrade/backend/data/``)::

    data/
    ├── market_state/                         ← Market Intelligence
    │   ├── latest.json                       ← pointer to current valid artifact
    │   ├── market_state_20260316_143000.json ← timestamped published artifacts
    │   └── ...
    │
    ├── workflows/                            ← Per-run workflow outputs
    │   ├── stock_opportunity/                ← Stock Workflow runs
    │   │   ├── latest.json                   ← pointer to latest completed run
    │   │   ├── run_20260316_150000_a1b2/     ← individual run folder
    │   │   │   ├── manifest.json             ← run manifest (stages, status, outputs)
    │   │   │   ├── stage_load_market_state.json
    │   │   │   ├── stage_scan.json
    │   │   │   ├── stage_normalize.json
    │   │   │   ├── stage_enrich_evaluate.json
    │   │   │   ├── output.json               ← final compact consumer output
    │   │   │   └── summary.json              ← compact run summary
    │   │   └── run_20260316_160000_c3d4/
    │   │       └── ...
    │   │
    │   └── options_opportunity/              ← Options Workflow runs
    │       ├── latest.json                   ← pointer to latest completed run
    │       ├── run_20260316_151000_e5f6/
    │       │   ├── manifest.json
    │       │   ├── stage_load_market_state.json
    │       │   ├── stage_scan.json
    │       │   ├── stage_validate_math.json
    │       │   ├── stage_enrich_evaluate.json
    │       │   ├── output.json
    │       │   └── summary.json
    │       └── ...
    │
    └── (existing dirs: snapshots/, etc.)

Design principles:
    - market_state/ stays at top level (already established in Prompt 2)
    - workflows/ groups per-run outputs by workflow type
    - each run gets its own folder — no commingling
    - latest.json pointers at each workflow level
    - manifests inside run folders for inspectability
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════
# 1. DIRECTORY CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

# Top-level directory under data/ for per-run workflow outputs.
WORKFLOWS_DIR_NAME = "workflows"

# Per-workflow subdirectory names (match WorkflowID values).
STOCK_WORKFLOW_DIR = "stock_opportunity"
OPTIONS_WORKFLOW_DIR = "options_opportunity"

# Market Intelligence uses its own top-level dir (already established).
# Imported from market_state_discovery for consistency.
MARKET_STATE_DIR_NAME = "market_state"

# Pointer filename — consistent across all workflows.
POINTER_FILENAME = "latest.json"

# Manifest filename — inside each run folder.
MANIFEST_FILENAME = "manifest.json"

# Final output filename — the compact consumer-facing artifact.
OUTPUT_FILENAME = "output.json"

# Run summary filename — compact run-level summary.
SUMMARY_FILENAME = "summary.json"


# ═══════════════════════════════════════════════════════════════════════
# 2. RUN IDENTITY & NAMING CONVENTIONS
# ═══════════════════════════════════════════════════════════════════════
#
# Run ID format:  ``run_YYYYMMDD_HHMMSS_<short_hex>``
#
# Components:
#   - ``run_`` prefix — identifies this as a run folder
#   - ``YYYYMMDD_HHMMSS`` — UTC timestamp, sortable, human-readable
#   - ``<short_hex>`` — 4 hex chars from uuid4 for uniqueness
#
# Example: ``run_20260316_150000_a1b2``
#
# This format is:
#   - Sortable by timestamp (ls/dir gives chronological order)
#   - Unique (short_hex prevents collisions for same-second runs)
#   - Human-readable (you can see the date at a glance)
#   - Filesystem-safe (no colons, spaces, or special chars)

RUN_ID_PREFIX = "run_"
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
SHORT_HEX_LENGTH = 4


def make_run_id(timestamp: datetime | None = None) -> str:
    """Generate a canonical run ID.

    Parameters
    ----------
    timestamp : datetime | None
        Run start time.  Defaults to now (UTC).

    Returns
    -------
    str
        e.g. ``"run_20260316_150000_a1b2"``
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    ts_str = timestamp.strftime(TIMESTAMP_FORMAT)
    short_hex = uuid.uuid4().hex[:SHORT_HEX_LENGTH]
    return f"{RUN_ID_PREFIX}{ts_str}_{short_hex}"


def make_stage_filename(stage_key: str) -> str:
    """Generate the filename for a stage artifact.

    Parameters
    ----------
    stage_key : str
        The stage key from definitions.py (e.g. ``"scan"``, ``"enrich_evaluate"``).

    Returns
    -------
    str
        e.g. ``"stage_scan.json"``
    """
    return f"stage_{stage_key}.json"


# ═══════════════════════════════════════════════════════════════════════
# 3. ARTIFACT CATEGORIES
# ═══════════════════════════════════════════════════════════════════════
#
# Every artifact file belongs to exactly one category.  Categories
# define the file's purpose, size expectation, and retention policy.


class ArtifactCategory(str, Enum):
    """Classification for artifact files."""

    PUBLISHED_STATE = "published_state"
    # The published source-of-truth artifact.
    # Used by: Market Intelligence (market_state_<ts>.json).
    # Retention: keep history for freshness/replay/debugging.

    STAGE_HANDOFF = "stage_handoff"
    # Intermediate artifact written by one stage and consumed by the
    # next stage within the same workflow run.
    # Used by: all workflows (stage_<key>.json).
    # Retention: keep within the run folder; prune old runs by policy.

    FINAL_OUTPUT = "final_output"
    # Compact consumer-facing output — the main deliverable of a run.
    # Used by: stock/options workflows (output.json).
    # Retention: keep within the run folder; latest.json points here.

    RUN_SUMMARY = "run_summary"
    # Compact summary of what happened in a run — stages completed,
    # timing, status, counts.
    # Used by: all workflows (summary.json).
    # Retention: keep within the run folder.

    MANIFEST = "manifest"
    # Run-level index listing all artifacts, stages, and outcomes.
    # Used by: stock/options workflows (manifest.json).
    # Retention: keep within the run folder.

    POINTER = "pointer"
    # Small file pointing to the latest valid artifact or run.
    # Used by: all workflows (latest.json).
    # Retention: always overwritten — only one copy at a time.


# ═══════════════════════════════════════════════════════════════════════
# 4. FULL VS COMPACT ARTIFACT PHILOSOPHY
# ═══════════════════════════════════════════════════════════════════════
#
# Stage handoff artifacts (STAGE_HANDOFF):
#   - Write the full output of the stage.
#   - Include enough data for the next stage to operate without
#     re-fetching or recomputing.
#   - Include quality/freshness metadata.
#   - OK to be larger — they live inside a run folder and are
#     only read by the next stage or by debug/replay tools.
#
# Final outputs (FINAL_OUTPUT):
#   - Compact: only the data consumers need.
#   - Include top-N ranked candidates (not the entire universe).
#   - Include market-state reference (not a full copy of market state).
#   - Include filter trace summary (not per-candidate raw data).
#   - This is what TMC will read and render.
#
# Run summaries (RUN_SUMMARY):
#   - Very compact: stage list, status, timing, counts.
#   - No candidate data or engine outputs.
#   - Useful for dashboards, run history, quick inspection.
#
# Published state (PUBLISHED_STATE):
#   - Rich but bounded: full engine outputs, composite, model
#     interpretation, consumer summary.
#   - This IS the source of truth — it should be complete.
#
# Anti-patterns:
#   - Copying the entire market_state.json into every stage artifact.
#     Use a reference (artifact_id) instead.
#   - Writing unbounded per-candidate debug data in the final output.
#     Put that in the stage artifact if needed.
#   - Omitting stage artifacts entirely — they are your replay/debug
#     safety net.
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# 5. LINEAGE & REFERENCE RULES
# ═══════════════════════════════════════════════════════════════════════
#
# Every artifact carries lineage fields that trace its provenance:
#
# Rule 1: UPSTREAM REFERENCE — NOT DEEP COPY
#   Stock/Options output artifacts include a ``market_state_ref`` field
#   containing the ``artifact_id`` of the consumed market-state artifact.
#   They do NOT embed a full copy of market state.
#
# Rule 2: RUN IDENTITY
#   Every artifact within a run includes ``run_id`` matching the folder
#   name.  This makes it possible to reassemble a run from loose files.
#
# Rule 3: STAGE SEQUENCE
#   Stage handoff artifacts include ``stage_key`` and ``stage_index``
#   so the execution order is explicit.
#
# Rule 4: WORKFLOW IDENTITY
#   Every artifact includes ``workflow_id`` (from WorkflowID enum).
#
# Rule 5: TIMESTAMPS
#   ``generated_at`` on every artifact — ISO 8601 UTC.
#   ``started_at`` / ``completed_at`` on run summaries and manifests.
#
# Rule 6: VERSION
#   ``contract_version`` on every artifact — tied to WORKFLOW_VERSION.

LINEAGE_REQUIRED_KEYS: tuple[str, ...] = (
    "workflow_id",
    "run_id",
    "generated_at",
)

STAGE_ARTIFACT_REQUIRED_KEYS: tuple[str, ...] = (
    "workflow_id",
    "run_id",
    "stage_key",
    "stage_index",
    "generated_at",
    "status",
)

OUTPUT_ARTIFACT_REQUIRED_KEYS: tuple[str, ...] = (
    "contract_version",
    "workflow_id",
    "run_id",
    "generated_at",
    "market_state_ref",
    "publication",
    "candidates",
    "quality",
)


# ═══════════════════════════════════════════════════════════════════════
# 6. ATOMIC WRITE & PUBLISH RULES
# ═══════════════════════════════════════════════════════════════════════
#
# All artifact writes follow the same pattern to prevent consumers
# from reading half-written files:
#
#   1. Write content to ``<target>.tmp``
#   2. Validate if appropriate (structural check)
#   3. Atomic rename: ``os.replace(<target>.tmp, <target>)``
#   4. If rename fails (Windows edge case), fall back to
#      direct write + unlink tmp.
#
# Pointer updates follow the same pattern.
#
# Run folder creation:
#   1. ``mkdir(parents=True, exist_ok=True)`` for the run folder.
#   2. Stage artifacts are written sequentially — no parallel writes
#      within a run.
#   3. The manifest is written last, after all stages complete.
#   4. The workflow-level ``latest.json`` is updated only after
#      the manifest is written successfully.
#
# Failure behavior:
#   - If a stage write fails, the run folder exists but the manifest
#     will record the failure.  The ``latest.json`` pointer is NOT
#     updated, so consumers never discover a failed run via the
#     normal path.
#   - Half-written ``.tmp`` files are cleaned up on next run.


def atomic_write_json(
    path: Path,
    data: dict[str, Any],
    indent: int = 2,
) -> Path:
    """Write a JSON file atomically via tmp + rename.

    Parameters
    ----------
    path : Path
        Final destination path.
    data : dict
        JSON-serializable data.
    indent : int
        JSON indentation level.

    Returns
    -------
    Path
        The written file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")

    content = json.dumps(data, indent=indent, default=str) + "\n"
    tmp_path.write_text(content, encoding="utf-8")

    try:
        os.replace(str(tmp_path), str(path))
    except OSError:
        # Fallback for platforms where replace fails across volumes
        path.write_text(content, encoding="utf-8")
        tmp_path.unlink(missing_ok=True)

    return path


# ═══════════════════════════════════════════════════════════════════════
# 7. RETENTION / HISTORY / REPLAY PHILOSOPHY
# ═══════════════════════════════════════════════════════════════════════
#
# Market Intelligence (market_state/):
#   - Retain timestamped artifacts indefinitely by default.
#   - A future cleanup job may prune artifacts older than N days.
#   - ``latest.json`` always points to the most recent valid one.
#   - Rationale: enables freshness comparison, replay, debugging.
#
# Stock / Options runs (workflows/<type>/run_*/):
#   - Retain complete run folders indefinitely by default.
#   - A future cleanup job may prune run folders older than N days,
#     keeping only ``summary.json`` for historical reference.
#   - ``latest.json`` always points to the most recent completed run.
#   - Rationale: enables replay, audit, comparison across runs.
#
# Cleanup policy (not implemented yet):
#   - Define a configurable retention window (e.g. 7 days).
#   - Retain all run summaries beyond the window.
#   - Delete stage artifacts and full outputs beyond the window.
#   - Never delete the latest.json pointer target.
#
# "Current" vs "historical":
#   - "Current" = what latest.json points to.  Always the most recent
#     successfully completed run/artifact.
#   - "Historical" = everything else in the directory.  Available for
#     replay, comparison, or debugging, but not consumed by default.
# ═══════════════════════════════════════════════════════════════════════

# Default retention window in days (for future cleanup implementation).
DEFAULT_RETENTION_DAYS: int = 7


# ═══════════════════════════════════════════════════════════════════════
# 8. MANIFEST / INDEX STRATEGY
# ═══════════════════════════════════════════════════════════════════════
#
# Every Stock and Options workflow run produces a ``manifest.json``
# inside its run folder.  The manifest serves as the run-level index.
#
# Purpose:
#   - List all artifacts belonging to the run.
#   - Record stage completion status and timing.
#   - Expose the final output filename.
#   - Support replay and inspection tooling.
#   - Allow TMC/debug tools to browse a run without walking the
#     directory blindly.
#
# Market Intelligence does NOT use per-run manifests because it does
# not have a run-folder model — it writes timestamped artifacts
# directly to market_state/ with a latest.json pointer.

MANIFEST_REQUIRED_KEYS: tuple[str, ...] = (
    "workflow_id",
    "run_id",
    "started_at",
    "completed_at",
    "status",
    "stages",
    "output_filename",
)


@dataclass(frozen=True)
class ManifestStageEntry:
    """One stage's record within a run manifest."""

    stage_key: str
    stage_index: int
    status: str            # "completed" | "failed" | "skipped"
    artifact_filename: str | None
    started_at: str | None
    completed_at: str | None
    record_count: int | None = None  # e.g. candidates found

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "stage_key": self.stage_key,
            "stage_index": self.stage_index,
            "status": self.status,
            "artifact_filename": self.artifact_filename,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
        if self.record_count is not None:
            d["record_count"] = self.record_count
        return d


# ═══════════════════════════════════════════════════════════════════════
# 9. WORKFLOW-SPECIFIC ARTIFACT EXPECTATIONS
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class WorkflowArtifactExpectation:
    """Declares the expected artifact structure for a workflow."""

    workflow_id: str
    uses_run_folders: bool
    uses_manifest: bool
    uses_pointer: bool
    pointer_location: str           # relative to data/
    stage_artifact_keys: tuple[str, ...]
    final_output_filename: str
    summary_filename: str | None
    description: str


WORKFLOW_ARTIFACT_EXPECTATIONS: dict[str, WorkflowArtifactExpectation] = {
    "market_intelligence": WorkflowArtifactExpectation(
        workflow_id="market_intelligence",
        uses_run_folders=False,
        uses_manifest=False,
        uses_pointer=True,
        pointer_location="market_state/latest.json",
        stage_artifact_keys=(
            "collect", "engine_run", "model_interpret", "composite", "publish",
        ),
        final_output_filename="market_state_<ts>.json",
        summary_filename=None,
        description=(
            "Market Intelligence writes timestamped artifacts directly "
            "to data/market_state/.  No run folders or manifests.  "
            "latest.json pointer updated after each successful publish.  "
            "Stage artifacts are NOT written individually — the final "
            "published artifact contains all stage outputs assembled."
        ),
    ),
    "stock_opportunity": WorkflowArtifactExpectation(
        workflow_id="stock_opportunity",
        uses_run_folders=True,
        uses_manifest=True,
        uses_pointer=True,
        pointer_location="workflows/stock_opportunity/latest.json",
        stage_artifact_keys=(
            "load_market_state", "scan", "normalize",
            "enrich_evaluate", "select_package",
        ),
        final_output_filename=OUTPUT_FILENAME,
        summary_filename=SUMMARY_FILENAME,
        description=(
            "Stock Workflow writes per-run folders under "
            "data/workflows/stock_opportunity/.  Each run folder "
            "contains stage artifacts, a manifest, a compact output, "
            "and a run summary.  latest.json points to the most "
            "recent completed run."
        ),
    ),
    "options_opportunity": WorkflowArtifactExpectation(
        workflow_id="options_opportunity",
        uses_run_folders=True,
        uses_manifest=True,
        uses_pointer=True,
        pointer_location="workflows/options_opportunity/latest.json",
        stage_artifact_keys=(
            "load_market_state", "scan", "validate_math",
            "enrich_evaluate", "select_package",
        ),
        final_output_filename=OUTPUT_FILENAME,
        summary_filename=SUMMARY_FILENAME,
        description=(
            "Options Workflow writes per-run folders under "
            "data/workflows/options_opportunity/.  Each run folder "
            "contains stage artifacts (including validation/math), "
            "a manifest, a compact output with filter trace, and a "
            "run summary.  latest.json points to the most recent "
            "completed run."
        ),
    ),
}


# ═══════════════════════════════════════════════════════════════════════
# 10. PATH BUILDER HELPERS
# ═══════════════════════════════════════════════════════════════════════


def get_workflow_dir(
    data_dir: str | Path,
    workflow_id: str,
) -> Path:
    """Return the canonical directory for a workflow's runs.

    For market_intelligence, returns data/market_state/.
    For stock/options, returns data/workflows/<workflow_id>/.
    """
    data_dir = Path(data_dir)
    if workflow_id == "market_intelligence":
        return data_dir / MARKET_STATE_DIR_NAME
    return data_dir / WORKFLOWS_DIR_NAME / workflow_id


def get_run_dir(
    data_dir: str | Path,
    workflow_id: str,
    run_id: str,
) -> Path:
    """Return the path to a specific run folder.

    Only valid for workflows that use run folders (stock/options).
    """
    return get_workflow_dir(data_dir, workflow_id) / run_id


def get_stage_artifact_path(
    data_dir: str | Path,
    workflow_id: str,
    run_id: str,
    stage_key: str,
) -> Path:
    """Return the path to a stage artifact within a run."""
    return get_run_dir(data_dir, workflow_id, run_id) / make_stage_filename(stage_key)


def get_output_path(
    data_dir: str | Path,
    workflow_id: str,
    run_id: str,
) -> Path:
    """Return the path to the final output artifact within a run."""
    return get_run_dir(data_dir, workflow_id, run_id) / OUTPUT_FILENAME


def get_summary_path(
    data_dir: str | Path,
    workflow_id: str,
    run_id: str,
) -> Path:
    """Return the path to the run summary artifact."""
    return get_run_dir(data_dir, workflow_id, run_id) / SUMMARY_FILENAME


def get_manifest_path(
    data_dir: str | Path,
    workflow_id: str,
    run_id: str,
) -> Path:
    """Return the path to the run manifest."""
    return get_run_dir(data_dir, workflow_id, run_id) / MANIFEST_FILENAME


def get_pointer_path(
    data_dir: str | Path,
    workflow_id: str,
) -> Path:
    """Return the path to the workflow's latest.json pointer."""
    return get_workflow_dir(data_dir, workflow_id) / POINTER_FILENAME


# ═══════════════════════════════════════════════════════════════════════
# 11. WORKFLOW-LEVEL POINTER (for stock/options)
# ═══════════════════════════════════════════════════════════════════════
#
# Stock and Options workflows use a latest.json pointer at the
# workflow directory level.  Shape:
#
#   {
#     "run_id": "run_20260316_150000_a1b2",
#     "workflow_id": "stock_opportunity",
#     "completed_at": "2026-03-16T15:00:05Z",
#     "status": "valid",
#     "output_filename": "output.json",
#     "contract_version": "1.0"
#   }
#
# This differs from the market-state pointer (which references an
# artifact filename) because stock/options runs live in folders.

WORKFLOW_POINTER_REQUIRED_KEYS: tuple[str, ...] = (
    "run_id",
    "workflow_id",
    "completed_at",
    "status",
    "output_filename",
    "contract_version",
)


@dataclass(frozen=True)
class WorkflowPointerData:
    """Parsed contents of a stock/options workflow latest.json.

    ``batch_status`` tracks run completeness:
    - ``"completed"`` — all stages ran successfully
    - ``"partial"``   — pipeline interrupted, partial output packaged
    Absent / None for legacy pointers written before this field existed.
    """

    run_id: str
    workflow_id: str
    completed_at: str
    status: str
    output_filename: str
    contract_version: str
    batch_status: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkflowPointerData:
        return cls(
            run_id=d["run_id"],
            workflow_id=d["workflow_id"],
            completed_at=d["completed_at"],
            status=d["status"],
            output_filename=d["output_filename"],
            contract_version=d["contract_version"],
            batch_status=d.get("batch_status"),
        )

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "completed_at": self.completed_at,
            "status": self.status,
            "output_filename": self.output_filename,
            "contract_version": self.contract_version,
        }
        if self.batch_status is not None:
            d["batch_status"] = self.batch_status
        return d


def write_workflow_pointer(
    data_dir: str | Path,
    workflow_id: str,
    pointer: WorkflowPointerData,
) -> Path:
    """Write a stock/options workflow latest.json pointer atomically."""
    pointer_path = get_pointer_path(data_dir, workflow_id)
    return atomic_write_json(pointer_path, pointer.to_dict())
