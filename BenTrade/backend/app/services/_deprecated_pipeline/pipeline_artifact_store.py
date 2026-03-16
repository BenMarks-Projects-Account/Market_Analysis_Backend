"""Pipeline Artifact Store v1.0 — per-run working memory.

Provides the canonical artifact registry for one BenTrade pipeline
execution.  Stages write structured artifacts here during execution;
later stages, replay flows, and debugging tools read them back.

Public API
──────────
    create_artifact_store(...)        Build an empty store for a run.
    build_artifact_record(...)        Build a single artifact record.
    put_artifact(...)                 Write an artifact into the store.
    get_artifact(...)                 Retrieve by artifact_id.
    get_artifact_by_key(...)          Retrieve by (stage_key, artifact_key).
    list_artifacts(...)               All artifacts, optionally filtered.
    list_stage_artifacts(...)         Artifacts for a specific stage.
    list_candidate_artifacts(...)     Artifacts for a specific candidate.
    get_latest_by_type(...)           Most-recent artifact of a given type.
    summarize_artifact_store(...)     Compact digest for UI / logging.
    validate_artifact_store(...)      Validate a store dict.
    validate_artifact_record(...)     Validate a single record.
    export_store(...)                 JSON-safe snapshot for persistence.
    import_store(...)                 Reconstitute from persisted snapshot.

Role boundary
─────────────
This module owns the *shape* and retrieval logic of the artifact
registry.  It does NOT:
- execute any pipeline stages
- decide what artifacts to produce
- persist to disk / database (future layer)
- stream events over SSE / WebSocket
- implement retry / cancellation logic
- manage run state (that is pipeline_run_contract's job)

Designed for inspectability, serialization, and replay.
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("bentrade.pipeline_artifact_store")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "storage"
_ARTIFACT_STORE_VERSION = "1.0"
_COMPATIBLE_VERSIONS = frozenset({"1.0"})


# =====================================================================
#  Artifact type vocabulary
# =====================================================================

VALID_ARTIFACT_TYPES = frozenset({
    # Market data stage
    "market_engine_output",
    "market_stage_summary",
    "market_data_quality",
    # Market model analysis stage
    "market_model_output",
    # Scanner stage
    "scanner_output",
    "scanner_stage_summary",
    # Candidate selection
    "normalized_candidate",
    "selected_candidate",
    "candidate_selection_ledger",
    "candidate_selection_summary",
    # Context assembly
    "shared_context",
    "assembled_context",
    "context_assembly_summary",
    # Enrichment
    "enriched_candidate",
    "candidate_enrichment_summary",
    # Policy
    "policy_output",
    "policy_stage_summary",
    # Events
    "event_context",
    "event_context_summary",
    # Portfolio (future)
    "portfolio_context",
    # Orchestration
    "decision_packet",
    "decision_packet_summary",
    "conflict_report",
    "confidence_assessment",
    "market_composite",
    # Prompt payload
    "prompt_payload",
    "prompt_payload_summary",
    # Final model
    "final_model_output",
    "final_model_summary",
    # Final normalization
    "final_decision_response",
    "final_response_ledger",
    "final_response_summary",
    # Feedback / post-trade (future)
    "feedback_record",
})
"""Canonical artifact type vocabulary.

Centralised here so every producer and consumer agrees on names.
New types should be added to this set — never invent ad-hoc names.
"""

VALID_ARTIFACT_STATUSES = frozenset({
    "active",
    "superseded",
    "invalid",
})
"""Status of an artifact record within the store.

- active: current, authoritative version
- superseded: replaced by a newer version (retained for lineage)
- invalid: marked bad (data-quality issue, recomputation required)
"""


# =====================================================================
#  Required-key sets (for validation)
# =====================================================================

_REQUIRED_STORE_KEYS = frozenset({
    "run_id",
    "artifact_store_version",
    "created_at",
    "updated_at",
    "artifacts",
    "artifact_index",
    "stage_index",
    "type_index",
    "candidate_index",
    "counts",
    "metadata",
})

_REQUIRED_RECORD_KEYS = frozenset({
    "artifact_id",
    "run_id",
    "stage_key",
    "artifact_key",
    "artifact_type",
    "candidate_id",
    "created_at",
    "updated_at",
    "status",
    "data",
    "summary",
    "metadata",
})


# =====================================================================
#  Timestamp helper (shared pattern)
# =====================================================================

def _now_iso() -> str:
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


# =====================================================================
#  Public API: create_artifact_store
# =====================================================================

def create_artifact_store(
    run_id: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an empty artifact store for a pipeline run.

    Parameters
    ----------
    run_id : str
        The pipeline run this store belongs to.
    metadata : dict | None
        Additional extensibility fields.

    Returns
    -------
    dict[str, Any]
        A fully initialized, empty artifact store.
    """
    now = _now_iso()
    return {
        "run_id": run_id,
        "artifact_store_version": _ARTIFACT_STORE_VERSION,
        "created_at": now,
        "updated_at": now,
        # Primary storage: artifact_id → record
        "artifacts": {},
        # Lookup indices (maintained by put_artifact)
        "artifact_index": {},     # (stage_key, artifact_key) → artifact_id
        "stage_index": {},        # stage_key → [artifact_id, ...]
        "type_index": {},         # artifact_type → [artifact_id, ...]
        "candidate_index": {},    # candidate_id → [artifact_id, ...]
        # Aggregate counts
        "counts": {
            "total": 0,
            "by_stage": {},
            "by_type": {},
            "active": 0,
            "superseded": 0,
            "invalid": 0,
        },
        "metadata": metadata or {},
    }


# =====================================================================
#  Public API: build_artifact_record
# =====================================================================

def build_artifact_record(
    *,
    run_id: str,
    stage_key: str,
    artifact_key: str,
    artifact_type: str,
    data: Any = None,
    summary: dict[str, Any] | None = None,
    candidate_id: str | None = None,
    artifact_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a single artifact record.

    Parameters
    ----------
    run_id : str
        Pipeline run identifier.
    stage_key : str
        Which stage produced this artifact.
    artifact_key : str
        Unique key within the stage (e.g. "breadth_engine",
        "candidate_SPY_001_decision_packet").
    artifact_type : str
        One of VALID_ARTIFACT_TYPES.
    data : Any
        The full artifact payload.  Must be JSON-serializable.
    summary : dict | None
        Lightweight preview / digest for UI / debug.
    candidate_id : str | None
        Optional candidate linkage for per-candidate lineage.
    artifact_id : str | None
        Override auto-generated ID (useful for replays).
    metadata : dict | None
        Additional extensibility fields.

    Returns
    -------
    dict[str, Any]
        A structured artifact record.
    """
    if artifact_type not in VALID_ARTIFACT_TYPES:
        logger.warning(
            "Unknown artifact_type '%s'; valid types: %s",
            artifact_type, sorted(VALID_ARTIFACT_TYPES),
        )

    now = _now_iso()
    aid = artifact_id or f"art-{uuid.uuid4().hex[:12]}"

    return {
        "artifact_id": aid,
        "run_id": run_id,
        "stage_key": stage_key,
        "artifact_key": artifact_key,
        "artifact_type": artifact_type,
        "candidate_id": candidate_id,
        "created_at": now,
        "updated_at": now,
        "status": "active",
        "data": data,
        "summary": summary or {},
        "metadata": metadata or {},
    }


# =====================================================================
#  Public API: put_artifact
# =====================================================================

def put_artifact(
    store: dict[str, Any],
    record: dict[str, Any],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write an artifact record into the store.

    If an artifact with the same (stage_key, artifact_key) already
    exists:
    - overwrite=False (default) → raises ValueError
    - overwrite=True → supersedes the old record, inserts the new one

    Parameters
    ----------
    store : dict
        The artifact store to write into.
    record : dict
        A record built by build_artifact_record.
    overwrite : bool
        Whether to allow replacing an existing artifact.

    Returns
    -------
    dict[str, Any]
        The store (mutated in place for efficiency).

    Raises
    ------
    ValueError
        If artifact_key collision and overwrite=False.
    """
    aid = record["artifact_id"]
    stage_key = record["stage_key"]
    artifact_key = record["artifact_key"]
    artifact_type = record["artifact_type"]
    candidate_id = record.get("candidate_id")
    lookup_key = _lookup_key(stage_key, artifact_key)

    # ── Handle collision ────────────────────────────────────────
    existing_id = store["artifact_index"].get(lookup_key)
    if existing_id is not None:
        if not overwrite:
            raise ValueError(
                f"Artifact collision: ({stage_key}, {artifact_key}) "
                f"already mapped to '{existing_id}'. "
                f"Use overwrite=True to supersede."
            )
        # Supersede old record
        old = store["artifacts"].get(existing_id)
        if old and old["status"] == "active":
            old["status"] = "superseded"
            old["updated_at"] = _now_iso()
            _adjust_status_count(store, "active", -1)
            _adjust_status_count(store, "superseded", 1)

    # ── Insert new record ───────────────────────────────────────
    store["artifacts"][aid] = record

    # ── Update indices ──────────────────────────────────────────
    store["artifact_index"][lookup_key] = aid

    stage_list = store["stage_index"].setdefault(stage_key, [])
    stage_list.append(aid)

    type_list = store["type_index"].setdefault(artifact_type, [])
    type_list.append(aid)

    if candidate_id:
        cand_list = store["candidate_index"].setdefault(candidate_id, [])
        cand_list.append(aid)

    # ── Update counts ───────────────────────────────────────────
    store["counts"]["total"] += 1
    _adjust_status_count(store, "active", 1)
    store["counts"]["by_stage"][stage_key] = (
        store["counts"]["by_stage"].get(stage_key, 0) + 1
    )
    store["counts"]["by_type"][artifact_type] = (
        store["counts"]["by_type"].get(artifact_type, 0) + 1
    )

    store["updated_at"] = _now_iso()
    return store


# =====================================================================
#  Public API: get_artifact
# =====================================================================

def get_artifact(
    store: dict[str, Any],
    artifact_id: str,
) -> dict[str, Any] | None:
    """Retrieve an artifact record by ID.

    Returns None if not found.
    """
    return store.get("artifacts", {}).get(artifact_id)


# =====================================================================
#  Public API: get_artifact_by_key
# =====================================================================

def get_artifact_by_key(
    store: dict[str, Any],
    stage_key: str,
    artifact_key: str,
) -> dict[str, Any] | None:
    """Retrieve the current artifact for a (stage_key, artifact_key) pair.

    Returns the artifact mapped by the index (the latest if overwritten).
    Returns None if not found.
    """
    lookup = _lookup_key(stage_key, artifact_key)
    aid = store.get("artifact_index", {}).get(lookup)
    if aid is None:
        return None
    return store.get("artifacts", {}).get(aid)


# =====================================================================
#  Public API: list_artifacts
# =====================================================================

def list_artifacts(
    store: dict[str, Any],
    *,
    artifact_type: str | None = None,
    status: str | None = None,
    stage_key: str | None = None,
    candidate_id: str | None = None,
) -> list[dict[str, Any]]:
    """List artifacts with optional filters.

    All filters are AND-combined.  Returns records in insertion order.

    Parameters
    ----------
    store : dict
        The artifact store.
    artifact_type : str | None
        Filter by artifact type.
    status : str | None
        Filter by record status (active / superseded / invalid).
    stage_key : str | None
        Filter by stage.
    candidate_id : str | None
        Filter by candidate linkage.

    Returns
    -------
    list[dict[str, Any]]
        Matching artifact records.
    """
    results: list[dict[str, Any]] = []
    for record in store.get("artifacts", {}).values():
        if artifact_type and record.get("artifact_type") != artifact_type:
            continue
        if status and record.get("status") != status:
            continue
        if stage_key and record.get("stage_key") != stage_key:
            continue
        if candidate_id and record.get("candidate_id") != candidate_id:
            continue
        results.append(record)
    return results


# =====================================================================
#  Public API: list_stage_artifacts
# =====================================================================

def list_stage_artifacts(
    store: dict[str, Any],
    stage_key: str,
    *,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    """List all artifacts produced by a specific stage.

    Parameters
    ----------
    store : dict
        The artifact store.
    stage_key : str
        Stage to filter by.
    active_only : bool
        If True, exclude superseded / invalid records.

    Returns
    -------
    list[dict[str, Any]]
        Artifact records for the stage.
    """
    aids = store.get("stage_index", {}).get(stage_key, [])
    artifacts = store.get("artifacts", {})
    results = [artifacts[aid] for aid in aids if aid in artifacts]
    if active_only:
        results = [r for r in results if r.get("status") == "active"]
    return results


# =====================================================================
#  Public API: list_candidate_artifacts
# =====================================================================

def list_candidate_artifacts(
    store: dict[str, Any],
    candidate_id: str,
    *,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    """List all artifacts linked to a specific candidate.

    Parameters
    ----------
    store : dict
        The artifact store.
    candidate_id : str
        Candidate to filter by.
    active_only : bool
        If True, exclude superseded / invalid records.

    Returns
    -------
    list[dict[str, Any]]
        Artifact records for the candidate.
    """
    aids = store.get("candidate_index", {}).get(candidate_id, [])
    artifacts = store.get("artifacts", {})
    results = [artifacts[aid] for aid in aids if aid in artifacts]
    if active_only:
        results = [r for r in results if r.get("status") == "active"]
    return results


# =====================================================================
#  Public API: get_latest_by_type
# =====================================================================

def get_latest_by_type(
    store: dict[str, Any],
    artifact_type: str,
    *,
    active_only: bool = True,
) -> dict[str, Any] | None:
    """Retrieve the most recently created artifact of a given type.

    Parameters
    ----------
    store : dict
        The artifact store.
    artifact_type : str
        The artifact type to search for.
    active_only : bool
        If True, only consider active records.

    Returns
    -------
    dict[str, Any] | None
        The latest artifact record, or None if none found.
    """
    aids = store.get("type_index", {}).get(artifact_type, [])
    artifacts = store.get("artifacts", {})
    candidates = [artifacts[aid] for aid in aids if aid in artifacts]
    if active_only:
        candidates = [r for r in candidates if r.get("status") == "active"]
    if not candidates:
        return None
    # Last inserted is latest (insertion order preserved in lists)
    return candidates[-1]


# =====================================================================
#  Public API: summarize_artifact_store
# =====================================================================

def summarize_artifact_store(store: dict[str, Any]) -> dict[str, Any]:
    """Return a compact overview of the artifact store.

    Designed for UI dashboards and log outputs — read-only digest.

    Output keys:
    - run_id: str
    - artifact_store_version: str
    - total_artifacts: int
    - active_artifacts: int
    - stages_with_artifacts: list[str]
    - artifact_types_present: list[str]
    - candidates_tracked: int
    - counts: dict
    - module_role: str
    """
    counts = store.get("counts", {})
    return {
        "run_id": store.get("run_id", "unknown"),
        "artifact_store_version": store.get(
            "artifact_store_version", "unknown"
        ),
        "total_artifacts": counts.get("total", 0),
        "active_artifacts": counts.get("active", 0),
        "stages_with_artifacts": sorted(store.get("stage_index", {}).keys()),
        "artifact_types_present": sorted(store.get("type_index", {}).keys()),
        "candidates_tracked": len(store.get("candidate_index", {})),
        "counts": counts,
        "module_role": _MODULE_ROLE,
    }


# =====================================================================
#  Public API: validate_artifact_store
# =====================================================================

def validate_artifact_store(
    store: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Validate an artifact store dict against the expected schema.

    Returns (ok, errors) where ok is True if store passes all checks.
    """
    errors: list[str] = []

    if not isinstance(store, dict):
        return False, ["store must be a dict"]

    # ── Required top-level keys ─────────────────────────────────
    for key in _REQUIRED_STORE_KEYS:
        if key not in store:
            errors.append(f"missing required key: {key}")

    # ── Version compatibility ───────────────────────────────────
    version = store.get("artifact_store_version")
    if version not in _COMPATIBLE_VERSIONS:
        errors.append(
            f"artifact_store_version mismatch: expected one of "
            f"{sorted(_COMPATIBLE_VERSIONS)}, got {version}"
        )

    # ── Type checks ─────────────────────────────────────────────
    if not isinstance(store.get("artifacts"), dict):
        errors.append("artifacts must be a dict")

    for idx_key in ("artifact_index", "stage_index",
                     "type_index", "candidate_index"):
        if not isinstance(store.get(idx_key), dict):
            errors.append(f"{idx_key} must be a dict")

    if not isinstance(store.get("counts"), dict):
        errors.append("counts must be a dict")

    if not isinstance(store.get("metadata"), dict):
        errors.append("metadata must be a dict")

    # ── Validate each artifact record ───────────────────────────
    artifacts = store.get("artifacts")
    if isinstance(artifacts, dict):
        for aid, record in artifacts.items():
            rok, rerrs = validate_artifact_record(record)
            if not rok:
                for e in rerrs:
                    errors.append(f"artifact '{aid}': {e}")

    return (len(errors) == 0, errors)


# =====================================================================
#  Public API: validate_artifact_record
# =====================================================================

def validate_artifact_record(
    record: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Validate a single artifact record.

    Returns (ok, errors) where ok is True if record passes all checks.
    """
    errors: list[str] = []

    if not isinstance(record, dict):
        return False, ["record must be a dict"]

    for key in _REQUIRED_RECORD_KEYS:
        if key not in record:
            errors.append(f"missing required key: {key}")

    if record.get("status") not in VALID_ARTIFACT_STATUSES:
        errors.append(f"invalid status: {record.get('status')}")

    return (len(errors) == 0, errors)


# =====================================================================
#  Public API: export_store / import_store
# =====================================================================

def export_store(store: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copy snapshot suitable for JSON serialization.

    This is the round-trip persistence boundary.  The returned dict
    is fully detached from the live store.
    """
    return copy.deepcopy(store)


def import_store(data: dict[str, Any]) -> dict[str, Any]:
    """Reconstitute an artifact store from a persisted snapshot.

    Validates the snapshot and returns a deep copy.

    Raises
    ------
    ValueError
        If the snapshot fails validation.
    """
    ok, errors = validate_artifact_store(data)
    if not ok:
        raise ValueError(
            f"Cannot import artifact store: {errors}"
        )
    return copy.deepcopy(data)


# =====================================================================
#  Private helpers
# =====================================================================

def _lookup_key(stage_key: str, artifact_key: str) -> str:
    """Build the composite index key for (stage_key, artifact_key).

    Derived field — used as the key in artifact_index.
    Formula: "{stage_key}::{artifact_key}"
    """
    return f"{stage_key}::{artifact_key}"


def _adjust_status_count(
    store: dict[str, Any],
    status: str,
    delta: int,
) -> None:
    """Adjust a status counter in store['counts']."""
    store["counts"][status] = store["counts"].get(status, 0) + delta
