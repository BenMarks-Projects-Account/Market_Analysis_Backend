"""Pipeline Candidate Selection Stage v1.0 — deterministic narrowing layer.

Implements the ``candidate_selection`` stage handler for the BenTrade
pipeline orchestrator (Step 7).  Retrieves normalized candidates from
Step 6, applies deterministic eligibility/quality gates, detects
duplicates, ranks using stable heuristics, selects a bounded subset,
and produces explicit selection artifacts and summary.

Public API
──────────
    candidate_selection_handler(...)      Orchestrator-compatible handler.
    build_selection_record(...)           Per-candidate selection record.
    build_selection_summary(...)          Stage-level summary.
    compute_candidate_rank_score(...)     Deterministic ranking.
    build_candidate_dedup_key(...)        Duplicate detection key.
    DEFAULT_MAX_SELECTED_CANDIDATES       Default selection cap.

Role boundary
─────────────
This module owns the *deterministic candidate narrowing pass* —
candidate retrieval from Step 6, eligibility gating, duplicate
handling, ranking, bounded selection, artifact writing, and
summary assembly.

It does NOT:
- re-run scanners (Step 6's job)
- perform model-based candidate review (later stage)
- enforce portfolio policy checks (policy stage's job)
- make final trade decisions (final decision stage's job)
- perform candidate enrichment (enrichment stage's job)
"""

from __future__ import annotations

import logging
import time
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

logger = logging.getLogger("bentrade.pipeline_candidate_selection_stage")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "candidate_selection"

# ── Selection cap ───────────────────────────────────────────────
DEFAULT_MAX_SELECTED_CANDIDATES: int = 20
"""Default maximum number of candidates to pass downstream.

Override via handler kwargs['max_selected_candidates'].
"""

# ── Candidate eligibility statuses ──────────────────────────────
SELECTION_STATUSES = frozenset({
    "eligible",
    "selected",
    "excluded_not_usable",
    "excluded_invalid_payload",
    "excluded_missing_required_fields",
    "excluded_disabled_strategy",
    "excluded_below_threshold",
    "excluded_duplicate",
    "excluded_by_rank_cutoff",
})

# ── Required candidate fields for eligibility ───────────────────
_REQUIRED_CANDIDATE_FIELDS: tuple[str, ...] = (
    "candidate_id",
    "symbol",
)
"""Minimum fields a candidate must have to pass eligibility.

scanner_key is strongly preferred but not strictly required
because some normalization paths may omit it.
"""

# ── Default ranking weights ─────────────────────────────────────
_DEFAULT_FAMILY_WEIGHTS: dict[str, float] = {
    "options": 1.0,
    "stock": 0.8,
}
"""Default scanner family priority weights.

Higher weight = higher ranking preference.
Options strategies are prioritized by default for BenTrade's
income-focused trading philosophy.
"""

_DEFAULT_STRATEGY_WEIGHTS: dict[str, float] = {
    "put_credit_spread": 1.0,
    "call_credit_spread": 0.95,
    "iron_condor": 0.90,
    "butterfly_debit": 0.80,
    "put_debit": 0.75,
    "call_debit": 0.75,
    "pullback_swing": 0.70,
    "momentum_breakout": 0.65,
    "mean_reversion": 0.60,
    "volatility_expansion": 0.55,
}
"""Default strategy type priority weights.

Higher weight = higher ranking preference.
Credit strategies are weighted highest per BenTrade's
high-probability, risk-defined philosophy.
"""


# =====================================================================
#  Timestamp helper
# =====================================================================

def _now_iso() -> str:
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


# =====================================================================
#  Per-candidate selection record
# =====================================================================

def build_selection_record(
    *,
    candidate_id: str,
    scanner_key: str = "",
    symbol: str = "",
    strategy_type: str = "",
    opportunity_type: str = "",
    eligibility_status: str,
    exclusion_reason: str = "",
    rank_score: float | None = None,
    rank_position: int | None = None,
    source_candidate_artifact_ref: str | None = None,
    source_scanner_artifact_ref: str | None = None,
    downstream_selected: bool = False,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Build a per-candidate selection/review record.

    Parameters
    ----------
    candidate_id : str
        Unique candidate identifier from Step 6.
    scanner_key : str
        Which scanner produced this candidate.
    symbol : str
        Underlying symbol.
    strategy_type : str
        Strategy type (e.g. 'put_credit_spread').
    opportunity_type : str
        Opportunity classification if available.
    eligibility_status : str
        One of SELECTION_STATUSES.
    exclusion_reason : str
        Why the candidate was excluded (empty if selected).
    rank_score : float | None
        Composite ranking score (higher = better).
    rank_position : int | None
        Position in ranked list (1-based).
    source_candidate_artifact_ref : str | None
        artifact_id of the Step 6 candidate artifact.
    source_scanner_artifact_ref : str | None
        artifact_id of the Step 6 raw scanner output.
    downstream_selected : bool
        Whether this candidate moves to downstream stages.
    warnings : list[str] | None
        Diagnostic warnings.
    notes : list[str] | None
        Operational notes.
    """
    return {
        "candidate_id": candidate_id,
        "scanner_key": scanner_key,
        "symbol": symbol,
        "strategy_type": strategy_type,
        "opportunity_type": opportunity_type,
        "eligibility_status": eligibility_status,
        "exclusion_reason": exclusion_reason,
        "rank_score": rank_score,
        "rank_position": rank_position,
        "source_candidate_artifact_ref": source_candidate_artifact_ref,
        "source_scanner_artifact_ref": source_scanner_artifact_ref,
        "downstream_selected": downstream_selected,
        "warnings": warnings or [],
        "notes": notes or [],
    }


# =====================================================================
#  Duplicate detection
# =====================================================================

def build_candidate_dedup_key(candidate: dict[str, Any]) -> str:
    """Build a deterministic deduplication key for a candidate.

    Composite key from:
    - symbol (uppercased)
    - scanner_family (or strategy_family)
    - strategy_type (or setup_type)
    - opportunity_type if present
    - direction if present

    Returns
    -------
    str
        Pipe-delimited dedup key.
    """
    parts = [
        str(candidate.get("symbol", "")).upper(),
        str(candidate.get("scanner_family")
            or candidate.get("strategy_family", "")).lower(),
        str(candidate.get("strategy_type")
            or candidate.get("setup_type", "")).lower(),
        str(candidate.get("opportunity_type", "")).lower(),
        str(candidate.get("direction", "")).lower(),
    ]
    return "|".join(parts)


def _deduplicate_candidates(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove duplicate candidates, keeping the first occurrence.

    Duplicates are detected via build_candidate_dedup_key.

    Returns
    -------
    (unique, duplicates)
        unique: list of candidates with duplicates removed.
        duplicates: list of removed duplicate candidates.
    """
    seen: dict[str, str] = {}  # dedup_key → candidate_id of first seen
    unique: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []

    for cand in candidates:
        key = build_candidate_dedup_key(cand)
        cid = cand.get("candidate_id", "")
        if key in seen:
            duplicates.append(cand)
        else:
            seen[key] = cid
            unique.append(cand)

    return unique, duplicates


# =====================================================================
#  Candidate eligibility gating
# =====================================================================

def _check_candidate_eligibility(
    candidate: dict[str, Any],
    *,
    disabled_strategies: set[str] | None = None,
) -> tuple[str, str]:
    """Evaluate a single candidate for eligibility.

    Returns
    -------
    (status, reason)
        status: one of SELECTION_STATUSES eligibility values.
        reason: human-readable exclusion reason (empty if eligible).
    """
    # downstream_usable flag from Step 6
    if not candidate.get("downstream_usable", True):
        norm_status = candidate.get("normalization_status", "unknown")
        return (
            "excluded_not_usable",
            f"downstream_usable=False (normalization_status={norm_status})",
        )

    # Must be a dict
    if not isinstance(candidate, dict):
        return "excluded_invalid_payload", "candidate is not a dict"

    # Check required fields
    missing = [
        f for f in _REQUIRED_CANDIDATE_FIELDS
        if not candidate.get(f)
    ]
    if missing:
        return (
            "excluded_missing_required_fields",
            f"missing required fields: {missing}",
        )

    # Disabled strategy check
    strategy = (
        candidate.get("strategy_type")
        or candidate.get("setup_type", "")
    )
    if disabled_strategies and strategy in disabled_strategies:
        return (
            "excluded_disabled_strategy",
            f"strategy '{strategy}' is disabled",
        )

    return "eligible", ""


# =====================================================================
#  Deterministic ranking / scoring
# =====================================================================

def compute_candidate_rank_score(
    candidate: dict[str, Any],
    *,
    family_weights: dict[str, float] | None = None,
    strategy_weights: dict[str, float] | None = None,
) -> float:
    """Compute a deterministic ranking score for a candidate.

    Scoring components (all deterministic, no model calls):
    ─────────────────────────────────────────────────────────
    1. Scanner-provided quality/confidence (0–100 range, normalized)
       - setup_quality or quality_score → 0.0–1.0
       - confidence → 0.0–1.0
    2. Scanner family weight (options preferred for income focus)
    3. Strategy type weight
    4. Payload completeness bonus

    Formula
    ───────
    score = (
        quality_component * 0.35
        + confidence_component * 0.25
        + family_weight * 0.20
        + strategy_weight * 0.15
        + completeness_bonus * 0.05
    )

    Input fields: setup_quality, quality_score, confidence,
                  scanner_family, strategy_family, strategy_type,
                  setup_type, symbol, direction, scanner_key

    Returns
    -------
    float
        Composite score in [0.0, 1.0] range.
    """
    f_weights = family_weights or _DEFAULT_FAMILY_WEIGHTS
    s_weights = strategy_weights or _DEFAULT_STRATEGY_WEIGHTS

    # Quality component (0.0–1.0)
    quality_raw = (
        candidate.get("setup_quality")
        or candidate.get("quality_score")
        or 0.0
    )
    if isinstance(quality_raw, (int, float)):
        # Normalize to 0–1 if provided as percentage (0–100)
        quality = min(1.0, quality_raw / 100.0) if quality_raw > 1.0 else float(quality_raw)
    else:
        quality = 0.0

    # Confidence component (0.0–1.0)
    conf_raw = candidate.get("confidence", 0.0)
    if isinstance(conf_raw, (int, float)):
        confidence = min(1.0, max(0.0, float(conf_raw)))
    else:
        confidence = 0.0

    # Family weight (0.0–1.0)
    family = str(
        candidate.get("scanner_family")
        or candidate.get("strategy_family", "")
    ).lower()
    family_w = f_weights.get(family, 0.5)

    # Strategy weight (0.0–1.0)
    strategy = str(
        candidate.get("strategy_type")
        or candidate.get("setup_type", "")
    ).lower()
    strategy_w = s_weights.get(strategy, 0.5)

    # Payload completeness bonus (0.0–1.0)
    completeness_fields = [
        "symbol", "direction", "scanner_key", "strategy_type",
        "scanner_family", "confidence", "setup_quality",
    ]
    present = sum(1 for f in completeness_fields if candidate.get(f))
    completeness = present / len(completeness_fields)

    # Weighted composite
    # Formula: quality*0.35 + confidence*0.25 + family*0.20
    #          + strategy*0.15 + completeness*0.05
    score = (
        quality * 0.35
        + confidence * 0.25
        + family_w * 0.20
        + strategy_w * 0.15
        + completeness * 0.05
    )
    return round(score, 6)


# =====================================================================
#  Selection summary builder
# =====================================================================

def build_selection_summary(
    *,
    total_loaded: int,
    total_eligible: int,
    total_excluded_pre_ranking: int,
    total_duplicates_excluded: int,
    total_selected: int,
    total_cut_by_rank: int,
    selection_cap: int,
    selected_candidate_ids: list[str],
    selected_artifact_ref: str | None = None,
    ledger_artifact_ref: str | None = None,
    summary_artifact_ref: str | None = None,
    counts_by_scanner: dict[str, dict[str, int]] | None = None,
    counts_by_family: dict[str, dict[str, int]] | None = None,
    counts_by_strategy: dict[str, dict[str, int]] | None = None,
    exclusion_reason_counts: dict[str, int] | None = None,
    stage_status: str = "success",
    degraded_reasons: list[str] | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    """Build the candidate selection stage summary.

    Parameters
    ----------
    total_loaded : int
        Total candidates loaded from Step 6.
    total_eligible : int
        Candidates passing eligibility gates.
    total_excluded_pre_ranking : int
        Excluded before ranking (not usable, invalid, missing fields,
        disabled strategy).
    total_duplicates_excluded : int
        Excluded as duplicates.
    total_selected : int
        Final selected count.
    total_cut_by_rank : int
        Eligible but cut by selection cap.
    selection_cap : int
        Max candidates allowed downstream.
    selected_candidate_ids : list[str]
        IDs of selected candidates.
    selected_artifact_ref : str | None
        artifact_id of selected_candidates artifact.
    ledger_artifact_ref : str | None
        artifact_id of candidate_selection_ledger artifact.
    summary_artifact_ref : str | None
        This summary's own artifact_id once written.
    counts_by_scanner : dict | None
        scanner_key → {loaded, eligible, selected, excluded}.
    counts_by_family : dict | None
        scanner_family → {loaded, eligible, selected, excluded}.
    counts_by_strategy : dict | None
        strategy_type → {loaded, eligible, selected, excluded}.
    exclusion_reason_counts : dict | None
        exclusion_reason_code → count.
    stage_status : str
        Stage status rollup.
    degraded_reasons : list[str] | None
        Reasons stage is degraded.
    elapsed_ms : int | None
        Wall-clock time in ms.
    """
    return {
        "stage_key": _STAGE_KEY,
        "stage_status": stage_status,
        "total_loaded": total_loaded,
        "total_eligible": total_eligible,
        "total_excluded_pre_ranking": total_excluded_pre_ranking,
        "total_duplicates_excluded": total_duplicates_excluded,
        "total_selected": total_selected,
        "total_cut_by_rank": total_cut_by_rank,
        "selection_cap": selection_cap,
        "selected_candidate_ids": selected_candidate_ids,
        "selected_artifact_ref": selected_artifact_ref,
        "ledger_artifact_ref": ledger_artifact_ref,
        "summary_artifact_ref": summary_artifact_ref,
        "counts_by_scanner": counts_by_scanner or {},
        "counts_by_family": counts_by_family or {},
        "counts_by_strategy": counts_by_strategy or {},
        "exclusion_reason_counts": exclusion_reason_counts or {},
        "degraded_reasons": degraded_reasons or [],
        "elapsed_ms": elapsed_ms,
        "generated_at": _now_iso(),
    }


# =====================================================================
#  Artifact writing helpers
# =====================================================================

def _write_selected_candidates_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    selected_candidates: list[dict[str, Any]],
) -> str:
    """Write the selected candidates artifact. Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="selected_candidates",
        artifact_type="selected_candidate",
        data=selected_candidates,
        summary={
            "total_selected": len(selected_candidates),
            "candidate_ids": [
                c.get("candidate_id", "") for c in selected_candidates
            ],
            "symbols": sorted(set(
                c.get("symbol", "") for c in selected_candidates
            )),
        },
        metadata={
            "stage_key": _STAGE_KEY,
        },
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_selection_ledger_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    selection_records: list[dict[str, Any]],
) -> str:
    """Write the full selection ledger artifact. Returns artifact_id.

    The ledger contains ALL reviewed candidates with their
    eligibility status, exclusion reason, ranking score, and
    selection decision.
    """
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="candidate_selection_ledger",
        artifact_type="candidate_selection_ledger",
        data=selection_records,
        summary={
            "total_reviewed": len(selection_records),
            "selected_count": sum(
                1 for r in selection_records
                if r.get("downstream_selected", False)
            ),
            "excluded_count": sum(
                1 for r in selection_records
                if not r.get("downstream_selected", False)
            ),
        },
        metadata={
            "stage_key": _STAGE_KEY,
        },
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_selection_summary_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    summary: dict[str, Any],
) -> str:
    """Write the selection stage summary artifact. Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="candidate_selection_summary",
        artifact_type="candidate_selection_summary",
        data=summary,
        summary={
            "stage_status": summary.get("stage_status"),
            "total_loaded": summary.get("total_loaded"),
            "total_selected": summary.get("total_selected"),
            "total_excluded_pre_ranking": summary.get("total_excluded_pre_ranking"),
            "total_duplicates_excluded": summary.get("total_duplicates_excluded"),
            "total_cut_by_rank": summary.get("total_cut_by_rank"),
            "selection_cap": summary.get("selection_cap"),
        },
        metadata={
            "stage_key": _STAGE_KEY,
        },
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


# =====================================================================
#  Event emission helper
# =====================================================================

def _make_event_emitter(
    run: dict[str, Any],
    event_callback: Callable[..., None] | None,
) -> Callable[..., None] | None:
    """Build an event emitter closure for selection events.

    Returns None if no callback is configured.
    """
    if event_callback is None:
        return None

    run_id = run["run_id"]

    def _emit(
        event_type: str,
        level: str = "info",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        merged_meta = {"stage_key": _STAGE_KEY}
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

        # Update run log counts
        counts = run.get("log_event_counts", {})
        counts["total"] = counts.get("total", 0) + 1
        by_level = counts.get("by_level", {})
        by_level[level] = by_level.get(level, 0) + 1

        try:
            event_callback(event)
        except Exception:
            logger.warning(
                "Event callback raised during selection event '%s'",
                event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Candidate loading from Step 6 artifacts
# =====================================================================

def _load_candidates_from_scanner_stage(
    artifact_store: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    """Load all normalized candidates from Step 6 scanner artifacts.

    Reads the scanner_stage_summary to discover which scanners
    produced candidates, then retrieves each grouped candidate
    artifact.

    Returns
    -------
    (candidates, artifact_ref_map, warnings)
        candidates: flat list of all candidate dicts.
        artifact_ref_map: candidate_id → source artifact_id.
        warnings: diagnostic messages.
    """
    warnings: list[str] = []

    # Locate scanner stage summary
    summary_art = get_artifact_by_key(
        artifact_store, "scanners", "scanner_stage_summary",
    )
    if summary_art is None:
        return [], {}, ["scanner_stage_summary artifact not found"]

    summary_data = summary_art.get("data", {})
    if not isinstance(summary_data, dict):
        return [], {}, ["scanner_stage_summary data is not a dict"]

    # Discover scanner keys with candidate artifacts
    scanner_summaries = summary_data.get("scanner_summaries", {})
    candidate_artifact_refs = summary_data.get("candidate_artifact_refs", {})

    candidates: list[dict[str, Any]] = []
    artifact_ref_map: dict[str, str] = {}

    for scanner_key, scanner_info in scanner_summaries.items():
        # Only process scanners that are downstream_usable
        if not scanner_info.get("downstream_usable", False):
            continue

        cand_art_ref = candidate_artifact_refs.get(scanner_key)
        if not cand_art_ref:
            continue

        # Retrieve grouped candidate artifact
        cand_art = get_artifact_by_key(
            artifact_store, "scanners", f"candidates_{scanner_key}",
        )
        if cand_art is None:
            warnings.append(
                f"Candidate artifact for scanner '{scanner_key}' "
                f"not found (ref={cand_art_ref})"
            )
            continue

        cand_data = cand_art.get("data", [])
        if not isinstance(cand_data, list):
            warnings.append(
                f"Candidate artifact for scanner '{scanner_key}' "
                f"data is not a list"
            )
            continue

        art_id = cand_art.get("artifact_id", "")
        for cand in cand_data:
            if isinstance(cand, dict):
                cid = cand.get("candidate_id", "")
                if cid:
                    artifact_ref_map[cid] = art_id
                candidates.append(cand)
            else:
                warnings.append(
                    f"Non-dict candidate in scanner '{scanner_key}', skipped"
                )

    return candidates, artifact_ref_map, warnings


# =====================================================================
#  Core selection pipeline
# =====================================================================

def _run_selection_pipeline(
    candidates: list[dict[str, Any]],
    artifact_ref_map: dict[str, str],
    *,
    max_selected: int,
    disabled_strategies: set[str] | None = None,
    family_weights: dict[str, float] | None = None,
    strategy_weights: dict[str, float] | None = None,
) -> tuple[
    list[dict[str, Any]],       # selected candidates
    list[dict[str, Any]],       # all selection records
    dict[str, int],             # exclusion_reason_counts
    dict[str, dict[str, int]],  # counts_by_scanner
    dict[str, dict[str, int]],  # counts_by_family
    dict[str, dict[str, int]],  # counts_by_strategy
]:
    """Run the deterministic selection pipeline.

    Steps:
    1. Eligibility gating
    2. Duplicate exclusion
    3. Ranking
    4. Bounded selection

    Returns
    -------
    Tuple of selection outputs for artifact/summary construction.
    """
    all_records: list[dict[str, Any]] = []
    exclusion_counts: dict[str, int] = {}
    scanner_counts: dict[str, dict[str, int]] = {}
    family_counts: dict[str, dict[str, int]] = {}
    strategy_counts: dict[str, dict[str, int]] = {}

    def _inc_count(bucket: dict[str, dict[str, int]], key: str, field: str) -> None:
        entry = bucket.setdefault(key, {
            "loaded": 0, "eligible": 0, "selected": 0, "excluded": 0,
        })
        entry[field] = entry.get(field, 0) + 1

    # ── 1. Eligibility gating ──────────────────────────────────
    eligible_candidates: list[dict[str, Any]] = []

    for cand in candidates:
        cid = cand.get("candidate_id", "")
        scanner_key = cand.get("scanner_key", "unknown")
        family = str(
            cand.get("scanner_family")
            or cand.get("strategy_family", "unknown")
        )
        strategy = str(
            cand.get("strategy_type")
            or cand.get("setup_type", "unknown")
        )

        # Track loaded counts
        _inc_count(scanner_counts, scanner_key, "loaded")
        _inc_count(family_counts, family, "loaded")
        _inc_count(strategy_counts, strategy, "loaded")

        status, reason = _check_candidate_eligibility(
            cand, disabled_strategies=disabled_strategies,
        )

        if status != "eligible":
            exclusion_counts[status] = exclusion_counts.get(status, 0) + 1
            _inc_count(scanner_counts, scanner_key, "excluded")
            _inc_count(family_counts, family, "excluded")
            _inc_count(strategy_counts, strategy, "excluded")
            all_records.append(build_selection_record(
                candidate_id=cid,
                scanner_key=scanner_key,
                symbol=cand.get("symbol", ""),
                strategy_type=strategy,
                opportunity_type=cand.get("opportunity_type", ""),
                eligibility_status=status,
                exclusion_reason=reason,
                source_candidate_artifact_ref=artifact_ref_map.get(cid),
                source_scanner_artifact_ref=cand.get(
                    "source_scanner_artifact_ref",
                ),
                downstream_selected=False,
            ))
            continue

        eligible_candidates.append(cand)

    # ── 2. Duplicate exclusion ─────────────────────────────────
    unique_candidates, duplicate_candidates = _deduplicate_candidates(
        eligible_candidates,
    )

    for dup in duplicate_candidates:
        cid = dup.get("candidate_id", "")
        scanner_key = dup.get("scanner_key", "unknown")
        family = str(
            dup.get("scanner_family")
            or dup.get("strategy_family", "unknown")
        )
        strategy = str(
            dup.get("strategy_type")
            or dup.get("setup_type", "unknown")
        )
        exclusion_counts["excluded_duplicate"] = (
            exclusion_counts.get("excluded_duplicate", 0) + 1
        )
        _inc_count(scanner_counts, scanner_key, "excluded")
        _inc_count(family_counts, family, "excluded")
        _inc_count(strategy_counts, strategy, "excluded")
        all_records.append(build_selection_record(
            candidate_id=cid,
            scanner_key=scanner_key,
            symbol=dup.get("symbol", ""),
            strategy_type=strategy,
            opportunity_type=dup.get("opportunity_type", ""),
            eligibility_status="excluded_duplicate",
            exclusion_reason="duplicate candidate (dedup key collision)",
            source_candidate_artifact_ref=artifact_ref_map.get(cid),
            source_scanner_artifact_ref=dup.get(
                "source_scanner_artifact_ref",
            ),
            downstream_selected=False,
        ))

    # ── 3. Rank eligible, unique candidates ────────────────────
    scored: list[tuple[float, dict[str, Any]]] = []
    for cand in unique_candidates:
        score = compute_candidate_rank_score(
            cand,
            family_weights=family_weights,
            strategy_weights=strategy_weights,
        )
        scored.append((score, cand))

    # Sort descending by score (stable sort preserves insertion order
    # for ties, which is deterministic since candidates arrive in
    # scanner registry order)
    scored.sort(key=lambda x: x[0], reverse=True)

    # ── 4. Bounded selection ───────────────────────────────────
    selected: list[dict[str, Any]] = []
    cut_count = 0

    for rank_pos, (score, cand) in enumerate(scored, start=1):
        cid = cand.get("candidate_id", "")
        scanner_key = cand.get("scanner_key", "unknown")
        family = str(
            cand.get("scanner_family")
            or cand.get("strategy_family", "unknown")
        )
        strategy = str(
            cand.get("strategy_type")
            or cand.get("setup_type", "unknown")
        )

        if len(selected) < max_selected:
            # Selected
            _inc_count(scanner_counts, scanner_key, "eligible")
            _inc_count(scanner_counts, scanner_key, "selected")
            _inc_count(family_counts, family, "eligible")
            _inc_count(family_counts, family, "selected")
            _inc_count(strategy_counts, strategy, "eligible")
            _inc_count(strategy_counts, strategy, "selected")

            all_records.append(build_selection_record(
                candidate_id=cid,
                scanner_key=scanner_key,
                symbol=cand.get("symbol", ""),
                strategy_type=strategy,
                opportunity_type=cand.get("opportunity_type", ""),
                eligibility_status="selected",
                rank_score=score,
                rank_position=rank_pos,
                source_candidate_artifact_ref=artifact_ref_map.get(cid),
                source_scanner_artifact_ref=cand.get(
                    "source_scanner_artifact_ref",
                ),
                downstream_selected=True,
            ))

            # Attach ranking metadata to the candidate record
            enriched = dict(cand)
            enriched["rank_score"] = score
            enriched["rank_position"] = rank_pos
            enriched["downstream_selected"] = True
            enriched["selection_stage_key"] = _STAGE_KEY
            selected.append(enriched)
        else:
            # Cut by rank
            cut_count += 1
            exclusion_counts["excluded_by_rank_cutoff"] = (
                exclusion_counts.get("excluded_by_rank_cutoff", 0) + 1
            )
            _inc_count(scanner_counts, scanner_key, "eligible")
            _inc_count(scanner_counts, scanner_key, "excluded")
            _inc_count(family_counts, family, "eligible")
            _inc_count(family_counts, family, "excluded")
            _inc_count(strategy_counts, strategy, "eligible")
            _inc_count(strategy_counts, strategy, "excluded")

            all_records.append(build_selection_record(
                candidate_id=cid,
                scanner_key=scanner_key,
                symbol=cand.get("symbol", ""),
                strategy_type=strategy,
                opportunity_type=cand.get("opportunity_type", ""),
                eligibility_status="excluded_by_rank_cutoff",
                exclusion_reason=(
                    f"rank {rank_pos} exceeds selection cap of {max_selected}"
                ),
                rank_score=score,
                rank_position=rank_pos,
                source_candidate_artifact_ref=artifact_ref_map.get(cid),
                source_scanner_artifact_ref=cand.get(
                    "source_scanner_artifact_ref",
                ),
                downstream_selected=False,
            ))

    return (
        selected,
        all_records,
        exclusion_counts,
        scanner_counts,
        family_counts,
        strategy_counts,
    )


# =====================================================================
#  Stage handler (orchestrator-compatible)
# =====================================================================

def candidate_selection_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Orchestrator-compatible handler for the candidate_selection stage.

    Sequence:
    1. Resolve configuration parameters.
    2. Load candidates from Step 6 scanner artifacts.
    3. Emit selection_started event.
    4. Run selection pipeline (eligibility, dedup, rank, select).
    5. Write selected_candidates artifact.
    6. Write candidate_selection_ledger artifact.
    7. Build and write candidate_selection_summary artifact.
    8. Emit selection_completed event.
    9. Return handler result dict.

    Handler kwargs (passed via orchestrator handler_kwargs)
    ──────────────────────────────────────────────────────
    max_selected_candidates : int
        Override DEFAULT_MAX_SELECTED_CANDIDATES.
    disabled_strategies : set[str] | None
        Strategy types to exclude.
    family_weights : dict[str, float] | None
        Override default family ranking weights.
    strategy_weights : dict[str, float] | None
        Override default strategy ranking weights.
    event_callback : callable | None
        Event callback for structured events.

    Returns
    -------
    dict[str, Any]
        Handler result compatible with Step 3 orchestrator:
        { outcome, summary_counts, artifacts, metadata, error }
    """
    t0 = time.monotonic()
    run_id = run["run_id"]

    # ── 1. Resolve parameters ───────────────────────────────────
    max_selected = kwargs.get(
        "max_selected_candidates", DEFAULT_MAX_SELECTED_CANDIDATES,
    )
    disabled_strategies: set[str] = set(
        kwargs.get("disabled_strategies") or [],
    )
    family_weights = kwargs.get("family_weights")
    strategy_weights = kwargs.get("strategy_weights")
    event_callback = kwargs.get("event_callback")
    event_emitter = _make_event_emitter(run, event_callback)

    # ── 2. Load candidates from Step 6 ─────────────────────────
    scanner_summary_art = get_artifact_by_key(
        artifact_store, "scanners", "scanner_stage_summary",
    )
    if scanner_summary_art is None:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        error = build_run_error(
            code="NO_SOURCE_SUMMARY",
            message="scanner_stage_summary artifact not found",
            source=_STAGE_KEY,
            detail={"expected_artifact_key": "scanner_stage_summary"},
        )
        if event_emitter:
            event_emitter(
                "selection_failed",
                level="error",
                message="No scanner stage summary found",
                metadata={"error_code": "NO_SOURCE_SUMMARY"},
            )
        return {
            "outcome": "failed",
            "summary_counts": {},
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": error,
        }

    candidates, artifact_ref_map, load_warnings = (
        _load_candidates_from_scanner_stage(artifact_store)
    )
    total_loaded = len(candidates)

    # ── 3. Emit selection_started event ─────────────────────────
    if event_emitter:
        event_emitter(
            "selection_started",
            message=(
                f"Candidate selection started with {total_loaded} candidates"
            ),
            metadata={"total_loaded": total_loaded},
        )

    # ── Handle zero loaded candidates ───────────────────────────
    if total_loaded == 0:
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Write empty artifacts
        sel_art_id = _write_selected_candidates_artifact(
            artifact_store, run_id, [],
        )
        ledger_art_id = _write_selection_ledger_artifact(
            artifact_store, run_id, [],
        )
        summary = build_selection_summary(
            total_loaded=0,
            total_eligible=0,
            total_excluded_pre_ranking=0,
            total_duplicates_excluded=0,
            total_selected=0,
            total_cut_by_rank=0,
            selection_cap=max_selected,
            selected_candidate_ids=[],
            selected_artifact_ref=sel_art_id,
            ledger_artifact_ref=ledger_art_id,
            stage_status="no_candidates_loaded",
            elapsed_ms=elapsed_ms,
            degraded_reasons=load_warnings,
        )
        summary_art_id = _write_selection_summary_artifact(
            artifact_store, run_id, summary,
        )
        summary["summary_artifact_ref"] = summary_art_id

        if event_emitter:
            event_emitter(
                "selection_completed",
                message="Candidate selection completed with 0 candidates loaded",
                metadata={
                    "total_loaded": 0,
                    "total_selected": 0,
                    "stage_status": "no_candidates_loaded",
                },
            )

        return {
            "outcome": "completed",
            "summary_counts": {
                "total_loaded": 0,
                "total_eligible": 0,
                "total_selected": 0,
                "total_excluded": 0,
                "total_duplicates": 0,
                "total_cut_by_rank": 0,
            },
            "artifacts": [],
            "metadata": {
                "stage_status": "no_candidates_loaded",
                "selected_artifact_id": sel_art_id,
                "ledger_artifact_id": ledger_art_id,
                "summary_artifact_id": summary_art_id,
                "selection_cap": max_selected,
                "elapsed_ms": elapsed_ms,
                "load_warnings": load_warnings,
            },
            "error": None,
        }

    # ── 4. Run selection pipeline ───────────────────────────────
    (
        selected_candidates,
        selection_records,
        exclusion_counts,
        counts_by_scanner,
        counts_by_family,
        counts_by_strategy,
    ) = _run_selection_pipeline(
        candidates,
        artifact_ref_map,
        max_selected=max_selected,
        disabled_strategies=disabled_strategies,
        family_weights=family_weights,
        strategy_weights=strategy_weights,
    )

    total_selected = len(selected_candidates)
    total_excluded_pre_ranking = sum(
        v for k, v in exclusion_counts.items()
        if k not in ("excluded_duplicate", "excluded_by_rank_cutoff")
    )
    total_duplicates = exclusion_counts.get("excluded_duplicate", 0)
    total_cut_by_rank = exclusion_counts.get("excluded_by_rank_cutoff", 0)
    total_eligible = total_selected + total_cut_by_rank
    selected_ids = [
        c.get("candidate_id", "") for c in selected_candidates
    ]

    # ── 5. Write selected_candidates artifact ───────────────────
    sel_art_id = _write_selected_candidates_artifact(
        artifact_store, run_id, selected_candidates,
    )

    # ── 6. Write selection ledger artifact ──────────────────────
    ledger_art_id = _write_selection_ledger_artifact(
        artifact_store, run_id, selection_records,
    )

    # ── 7. Build and write summary ──────────────────────────────
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Determine stage status
    degraded_reasons: list[str] = list(load_warnings)
    if total_loaded > 0 and total_selected == 0:
        stage_status = "no_selected_candidates"
    elif load_warnings:
        stage_status = "degraded"
    else:
        stage_status = "success"

    summary = build_selection_summary(
        total_loaded=total_loaded,
        total_eligible=total_eligible,
        total_excluded_pre_ranking=total_excluded_pre_ranking,
        total_duplicates_excluded=total_duplicates,
        total_selected=total_selected,
        total_cut_by_rank=total_cut_by_rank,
        selection_cap=max_selected,
        selected_candidate_ids=selected_ids,
        selected_artifact_ref=sel_art_id,
        ledger_artifact_ref=ledger_art_id,
        counts_by_scanner=counts_by_scanner,
        counts_by_family=counts_by_family,
        counts_by_strategy=counts_by_strategy,
        exclusion_reason_counts=exclusion_counts,
        stage_status=stage_status,
        degraded_reasons=degraded_reasons,
        elapsed_ms=elapsed_ms,
    )
    summary_art_id = _write_selection_summary_artifact(
        artifact_store, run_id, summary,
    )
    summary["summary_artifact_ref"] = summary_art_id

    # ── 8. Emit selection_completed event ───────────────────────
    if event_emitter:
        event_emitter(
            "selection_completed",
            message=(
                f"Candidate selection completed: "
                f"{total_selected}/{total_loaded} selected"
            ),
            metadata={
                "total_loaded": total_loaded,
                "total_eligible": total_eligible,
                "total_selected": total_selected,
                "total_excluded_pre_ranking": total_excluded_pre_ranking,
                "total_duplicates_excluded": total_duplicates,
                "total_cut_by_rank": total_cut_by_rank,
                "selection_cap": max_selected,
                "stage_status": stage_status,
                "selected_artifact_id": sel_art_id,
                "ledger_artifact_id": ledger_art_id,
                "summary_artifact_id": summary_art_id,
            },
        )

    # ── 9. Return handler result ────────────────────────────────
    return {
        "outcome": "completed",
        "summary_counts": {
            "total_loaded": total_loaded,
            "total_eligible": total_eligible,
            "total_selected": total_selected,
            "total_excluded": total_excluded_pre_ranking + total_duplicates,
            "total_duplicates": total_duplicates,
            "total_cut_by_rank": total_cut_by_rank,
        },
        "artifacts": [],  # artifacts already written directly
        "metadata": {
            "stage_status": stage_status,
            "selected_artifact_id": sel_art_id,
            "ledger_artifact_id": ledger_art_id,
            "summary_artifact_id": summary_art_id,
            "selection_cap": max_selected,
            "selected_candidate_ids": selected_ids,
            "elapsed_ms": elapsed_ms,
            "degraded_reasons": degraded_reasons,
            "exclusion_reason_counts": exclusion_counts,
            "load_warnings": load_warnings,
        },
        "error": None,
    }
