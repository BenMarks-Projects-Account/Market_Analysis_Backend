"""Pipeline Candidate Enrichment Stage — Step 9.

Combines selected candidates (Step 7) with the shared run-level
context (Step 8) to produce per-candidate enriched work-item packets.
Each packet carries a compact reference to the shared context plus
candidate-specific metadata — NOT a deep copy of the full context.

Public API
──────────
    candidate_enrichment_handler(run, artifact_store, stage_key, **kwargs)
        Stage handler compatible with the Step 3 orchestrator.

Role boundary
─────────────
This module:
- Retrieves selected_candidates artifact from Step 7.
- Retrieves shared_context artifact from Step 8.
- Builds one enriched packet per candidate with compact inherited
  context (shared_context_artifact_ref, not deep copies).
- Writes one "enriched_candidate" artifact per candidate.
- Writes one "candidate_enrichment_summary" artifact.
- Emits structured events via event_callback.
- Leaves downstream extension seams as None placeholders:
  event_context, portfolio_context, policy_context,
  decision_packet, prompt_payload, final_response.

This module does NOT:
- Re-run any earlier stage (scanners, market analysis, etc.).
- Perform event-calendar lookup (that is the events stage).
- Perform portfolio policy checks (that is the policy stage).
- Apply scoring or decisioning logic.
- Deep-copy giant shared payloads into every candidate packet.
- Make network or API calls.

This stage is preparing the battlefield, not fighting the war.
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

logger = logging.getLogger("bentrade.pipeline_candidate_enrichment_stage")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "candidate_enrichment"


# =====================================================================
#  Enrichment status constants
# =====================================================================

ENRICHMENT_STATUS_FULL = "full"
ENRICHMENT_STATUS_DEGRADED = "degraded"
ENRICHMENT_STATUS_FAILED = "failed"


# =====================================================================
#  Event emission helper
# =====================================================================

def _make_event_emitter(
    run: dict[str, Any],
    event_callback: Callable[..., None] | None,
) -> Callable[..., None] | None:
    """Build an event emitter closure for candidate enrichment events.

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
                "Event callback raised during enrichment event '%s'",
                event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Upstream artifact retrieval
# =====================================================================

def _retrieve_selected_candidates(
    artifact_store: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Retrieve selected_candidates and summary from Step 7.

    Returns
    -------
    (candidates_list, summary_data_or_None)
    """
    candidates_art = get_artifact_by_key(
        artifact_store, "candidate_selection", "selected_candidates",
    )
    summary_art = get_artifact_by_key(
        artifact_store, "candidate_selection", "candidate_selection_summary",
    )

    candidates = (candidates_art.get("data") or []) if candidates_art else []
    summary = (summary_art.get("data") or {}) if summary_art else None

    return candidates, summary


def _retrieve_shared_context(
    artifact_store: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Retrieve shared_context artifact from Step 8.

    Returns
    -------
    (shared_context_data_or_None, artifact_id_or_None)
    """
    ctx_art = get_artifact_by_key(
        artifact_store, "shared_context", "shared_context",
    )
    if ctx_art is None:
        return None, None
    return ctx_art.get("data") or {}, ctx_art.get("artifact_id")


# =====================================================================
#  Compact context summary builder
# =====================================================================

def _build_compact_context_summary(
    shared_context: dict[str, Any],
) -> dict[str, Any]:
    """Build a lightweight summary of the shared context for embedding
    in each enriched packet.

    This is NOT a deep copy — just the key status fields so downstream
    stages can quickly assess context health without dereferencing the
    full shared_context artifact.

    Fields included:
    - overall_status: "full" | "degraded" | "failed"
    - degraded_reasons: list[str]
    - module_availability: dict[str, bool]
    - module_statuses: dict[str, str | None]
    """
    modules = shared_context.get("context_modules", {})
    return {
        "overall_status": shared_context.get("overall_status"),
        "degraded_reasons": shared_context.get("degraded_reasons", []),
        "module_availability": {
            name: mod.get("available", False)
            for name, mod in modules.items()
        },
        "module_statuses": {
            name: mod.get("stage_status")
            for name, mod in modules.items()
        },
    }


# =====================================================================
#  Per-candidate enriched packet builder
# =====================================================================

def _build_enriched_packet(
    candidate: dict[str, Any],
    shared_context_artifact_ref: str | None,
    compact_context: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    """Build one enriched work-item packet for a candidate.

    Enriched packet contract
    ────────────────────────
    Input fields:
      - candidate dict from selected_candidates (Step 7)
      - shared_context_artifact_ref (artifact_id of shared_context)
      - compact_context (lightweight summary of shared context)
      - run_id

    Output: dict with the following keys:
      - candidate_id: str — from candidate
      - run_id: str
      - symbol: str — from candidate
      - strategy_type: str — from candidate
      - scanner_key: str — from candidate
      - scanner_family: str — from candidate
      - direction: str — from candidate
      - rank_position: int — from candidate
      - rank_score: float — from candidate
      - setup_quality: float — from candidate
      - confidence: float — from candidate
      - candidate_snapshot: dict — full candidate dict (for lineage)
      - shared_context_artifact_ref: str | None — pointer to full context
      - compact_context_summary: dict — lightweight context health info
      - enrichment_status: str — "full" | "degraded"
      - enrichment_notes: list[str] — any enrichment observations
      - event_context: None — downstream placeholder (events stage)
      - portfolio_context: None — downstream placeholder (policy stage)
      - policy_context: None — downstream placeholder (policy stage)
      - decision_packet: None — downstream placeholder (orchestration)
      - prompt_payload: None — downstream placeholder (prompt stage)
      - final_response: None — downstream placeholder (final stage)
      - enriched_at: str — ISO-8601 timestamp
    """
    notes: list[str] = []
    status = ENRICHMENT_STATUS_FULL

    # Degrade if shared context is missing or degraded
    ctx_overall = compact_context.get("overall_status")
    if shared_context_artifact_ref is None:
        status = ENRICHMENT_STATUS_DEGRADED
        notes.append("shared_context artifact not available")
    elif ctx_overall == "degraded":
        status = ENRICHMENT_STATUS_DEGRADED
        notes.append("shared_context is degraded")
    elif ctx_overall == "failed":
        status = ENRICHMENT_STATUS_DEGRADED
        notes.append("shared_context failed assembly")

    # Degrade if candidate is missing key fields
    if not candidate.get("candidate_id"):
        status = ENRICHMENT_STATUS_DEGRADED
        notes.append("candidate missing candidate_id")

    return {
        "candidate_id": candidate.get("candidate_id"),
        "run_id": run_id,
        "symbol": candidate.get("symbol"),
        "strategy_type": candidate.get("strategy_type"),
        "scanner_key": candidate.get("scanner_key"),
        "scanner_family": candidate.get("scanner_family"),
        "direction": candidate.get("direction"),
        "rank_position": candidate.get("rank_position"),
        "rank_score": candidate.get("rank_score"),
        "setup_quality": candidate.get("setup_quality"),
        "confidence": candidate.get("confidence"),
        "candidate_snapshot": candidate,
        "shared_context_artifact_ref": shared_context_artifact_ref,
        "compact_context_summary": compact_context,
        "enrichment_status": status,
        "enrichment_notes": notes,
        # Downstream placeholders — populated by later stages
        "event_context": None,
        "portfolio_context": None,
        "policy_context": None,
        "decision_packet": None,
        "prompt_payload": None,
        "final_response": None,
        "enriched_at": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
#  Per-candidate enrichment record builder
# =====================================================================

def _build_enrichment_record(
    candidate_id: str | None,
    enrichment_status: str,
    notes: list[str],
    elapsed_ms: int,
) -> dict[str, Any]:
    """Build a per-candidate enrichment record for the summary ledger.

    Fields:
      - candidate_id: str | None
      - enrichment_status: "full" | "degraded"
      - enrichment_notes: list[str]
      - elapsed_ms: int
    """
    return {
        "candidate_id": candidate_id,
        "enrichment_status": enrichment_status,
        "enrichment_notes": notes,
        "elapsed_ms": elapsed_ms,
    }


# =====================================================================
#  Artifact writers
# =====================================================================

def _write_enriched_candidate_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    candidate_id: str | None,
    packet: dict[str, Any],
) -> str:
    """Write one enriched_candidate artifact.  Returns artifact_id."""
    artifact_key = f"enriched_{candidate_id}" if candidate_id else "enriched_unknown"

    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key=artifact_key,
        artifact_type="enriched_candidate",
        data=packet,
        candidate_id=candidate_id,
        summary={
            "candidate_id": candidate_id,
            "symbol": packet.get("symbol"),
            "strategy_type": packet.get("strategy_type"),
            "enrichment_status": packet.get("enrichment_status"),
            "enrichment_notes": packet.get("enrichment_notes", []),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_enrichment_summary_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    summary: dict[str, Any],
) -> str:
    """Write the candidate_enrichment_summary artifact.  Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="candidate_enrichment_summary",
        artifact_type="candidate_enrichment_summary",
        data=summary,
        summary={
            "overall_status": summary.get("overall_status"),
            "total_enriched": summary.get("total_enriched"),
            "total_degraded": summary.get("total_degraded"),
            "total_failed": summary.get("total_failed"),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


# =====================================================================
#  Stage handler — public entry point
# =====================================================================

def candidate_enrichment_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Candidate enrichment stage handler (Step 9).

    Retrieves selected candidates (Step 7) and shared context
    (Step 8), builds per-candidate enriched work-item packets,
    writes per-candidate artifacts and a stage summary, and emits
    structured events.

    Parameters
    ----------
    run : dict
        The pipeline run dict (passed by orchestrator).
    artifact_store : dict
        The artifact store (passed by orchestrator).
    stage_key : str
        Expected to be "candidate_enrichment".
    **kwargs
        event_callback : callable | None
            Optional event callback for structured events.

    Returns
    -------
    dict[str, Any]
        Handler result compatible with Step 3 orchestrator:
        { outcome, summary_counts, artifacts, metadata, error }
    """
    t0 = time.monotonic()
    run_id = run["run_id"]

    # ── 1. Resolve parameters ───────────────────────────────────
    event_callback = kwargs.get("event_callback")
    emit = _make_event_emitter(run, event_callback)

    # ── 2. Emit enrichment started ──────────────────────────────
    if emit:
        emit(
            "candidate_enrichment_started",
            message="Candidate enrichment started",
        )

    # ── 3. Retrieve upstream artifacts ──────────────────────────
    try:
        candidates, selection_summary = _retrieve_selected_candidates(
            artifact_store,
        )
        shared_context, shared_context_art_id = _retrieve_shared_context(
            artifact_store,
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "Candidate enrichment failed during upstream retrieval: %s",
            exc, exc_info=True,
        )
        if emit:
            emit(
                "candidate_enrichment_failed",
                level="error",
                message=f"Upstream retrieval failed: {exc}",
            )
        return {
            "outcome": "failed",
            "summary_counts": {
                "total_enriched": 0,
                "total_degraded": 0,
                "total_failed": 0,
            },
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="ENRICHMENT_UPSTREAM_RETRIEVAL_ERROR",
                message=f"Failed to retrieve upstream artifacts: {exc}",
                source=_STAGE_KEY,
            ),
        }

    # ── 4. Validate upstream availability ───────────────────────
    if not candidates:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("No selected candidates to enrich")
        if emit:
            emit(
                "candidate_enrichment_completed",
                message="No candidates to enrich — stage completed vacuously",
                metadata={"total_enriched": 0},
            )

        summary = {
            "stage_key": _STAGE_KEY,
            "overall_status": ENRICHMENT_STATUS_FULL,
            "total_candidates_in": 0,
            "total_enriched": 0,
            "total_full": 0,
            "total_degraded": 0,
            "total_failed": 0,
            "enrichment_records": [],
            "enriched_artifact_refs": [],
            "shared_context_artifact_ref": shared_context_art_id,
            "summary_artifact_ref": None,
            "elapsed_ms": elapsed_ms,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        summary_art_id = _write_enrichment_summary_artifact(
            artifact_store, run_id, summary,
        )
        summary["summary_artifact_ref"] = summary_art_id

        return {
            "outcome": "completed",
            "summary_counts": {
                "total_enriched": 0,
                "total_degraded": 0,
                "total_failed": 0,
            },
            "artifacts": [],
            "metadata": {
                "overall_status": ENRICHMENT_STATUS_FULL,
                "stage_summary": summary,
                "elapsed_ms": elapsed_ms,
            },
            "error": None,
        }

    # ── 5. Build compact context summary ────────────────────────
    compact_context = _build_compact_context_summary(
        shared_context or {},
    )

    # ── 6. Enrich each candidate ────────────────────────────────
    enrichment_records: list[dict[str, Any]] = []
    enriched_art_refs: list[str] = []
    total_full = 0
    total_degraded = 0
    total_failed = 0

    for candidate in candidates:
        cand_t0 = time.monotonic()
        cand_id = candidate.get("candidate_id")

        try:
            packet = _build_enriched_packet(
                candidate=candidate,
                shared_context_artifact_ref=shared_context_art_id,
                compact_context=compact_context,
                run_id=run_id,
            )

            art_id = _write_enriched_candidate_artifact(
                artifact_store, run_id, cand_id, packet,
            )
            enriched_art_refs.append(art_id)

            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            e_status = packet["enrichment_status"]
            e_notes = packet["enrichment_notes"]

            if e_status == ENRICHMENT_STATUS_FULL:
                total_full += 1
            else:
                total_degraded += 1

            enrichment_records.append(
                _build_enrichment_record(cand_id, e_status, e_notes, cand_elapsed)
            )

        except Exception as exc:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            logger.error(
                "Enrichment failed for candidate %s: %s",
                cand_id, exc, exc_info=True,
            )
            enrichment_records.append(
                _build_enrichment_record(
                    cand_id, ENRICHMENT_STATUS_FAILED,
                    [f"enrichment exception: {exc}"], cand_elapsed,
                )
            )

    total_enriched = total_full + total_degraded

    # ── 7. Compute overall enrichment status ────────────────────
    if total_failed > 0 and total_enriched == 0:
        overall_status = ENRICHMENT_STATUS_FAILED
    elif total_failed > 0 or total_degraded > 0:
        overall_status = ENRICHMENT_STATUS_DEGRADED
    else:
        overall_status = ENRICHMENT_STATUS_FULL

    # ── 8. Build stage summary ──────────────────────────────────
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    generated_at = datetime.now(timezone.utc).isoformat()

    stage_summary = {
        "stage_key": _STAGE_KEY,
        "overall_status": overall_status,
        "total_candidates_in": len(candidates),
        "total_enriched": total_enriched,
        "total_full": total_full,
        "total_degraded": total_degraded,
        "total_failed": total_failed,
        "enrichment_records": enrichment_records,
        "enriched_artifact_refs": enriched_art_refs,
        "shared_context_artifact_ref": shared_context_art_id,
        "summary_artifact_ref": None,  # filled after write
        "elapsed_ms": elapsed_ms,
        "generated_at": generated_at,
    }

    # ── 9. Write summary artifact ───────────────────────────────
    summary_art_id = _write_enrichment_summary_artifact(
        artifact_store, run_id, stage_summary,
    )
    stage_summary["summary_artifact_ref"] = summary_art_id

    # ── 10. Determine outcome ───────────────────────────────────
    if overall_status == ENRICHMENT_STATUS_FAILED:
        if emit:
            emit(
                "candidate_enrichment_failed",
                level="error",
                message=(
                    f"Candidate enrichment failed: "
                    f"{total_failed}/{len(candidates)} candidates failed"
                ),
                metadata={
                    "total_failed": total_failed,
                    "total_enriched": total_enriched,
                },
            )
        return {
            "outcome": "failed",
            "summary_counts": {
                "total_enriched": total_enriched,
                "total_degraded": total_degraded,
                "total_failed": total_failed,
            },
            "artifacts": [],
            "metadata": {
                "overall_status": overall_status,
                "stage_summary": stage_summary,
                "elapsed_ms": elapsed_ms,
            },
            "error": build_run_error(
                code="CANDIDATE_ENRICHMENT_FAILED",
                message=(
                    f"All {total_failed} candidates failed enrichment"
                ),
                source=_STAGE_KEY,
            ),
        }

    # ── 11. Emit success / degraded ─────────────────────────────
    if emit:
        emit(
            "candidate_enrichment_completed",
            message=(
                f"Candidate enrichment completed: "
                f"{total_enriched}/{len(candidates)} enriched"
                + (f" ({total_degraded} degraded)" if total_degraded else "")
            ),
            metadata={
                "total_enriched": total_enriched,
                "total_full": total_full,
                "total_degraded": total_degraded,
                "total_failed": total_failed,
            },
        )

    return {
        "outcome": "completed",
        "summary_counts": {
            "total_enriched": total_enriched,
            "total_degraded": total_degraded,
            "total_failed": total_failed,
        },
        "artifacts": [],
        "metadata": {
            "overall_status": overall_status,
            "stage_summary": stage_summary,
            "shared_context_artifact_id": shared_context_art_id,
            "summary_artifact_id": summary_art_id,
            "enriched_artifact_refs": enriched_art_refs,
            "elapsed_ms": elapsed_ms,
        },
        "error": None,
    }
