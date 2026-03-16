"""Pipeline Trade Decision Packet Assembly Stage — Step 12.

Assembles one canonical per-candidate decision packet by combining
candidate enrichment (Step 9), event context (Step 10), and policy
outputs (Step 11) into a downstream-ready artifact with explicit
section boundaries.

Public API
──────────
    decision_packet_handler(run, artifact_store, stage_key, **kwargs)
        Stage handler compatible with the Step 3 orchestrator.
    assemble_decision_packet(enriched, policy, event_ctx, run_id, ...)
        Build a single per-candidate decision packet dict.

Role boundary
─────────────
This module:
- Retrieves per-candidate enriched packets from Step 9.
- Retrieves per-candidate policy outcomes from Step 11.
- Retrieves per-candidate event context from Step 10 (optional).
- Assembles one clean per-candidate decision packet.
- Preserves section boundaries (candidate / event / policy / quality).
- Writes per-candidate decision_packet artifacts keyed decision_{cid}.
- Writes a decision_packet_summary artifact.
- Emits structured events via event_callback.

This module does NOT:
- Re-run any earlier stage.
- Mutate Step 9 / 10 / 11 artifacts in place.
- Make final recommendation decisions.
- Perform new policy decisions or override policy outcomes.
- Build prompt payloads or invoke models.
- Compress or narrativize sections for model consumption.
- Rank or compare candidates against each other.
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

logger = logging.getLogger("bentrade.pipeline_trade_decision_packet_stage")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "orchestration"
_DECISION_PACKET_VERSION = "1.0"


# =====================================================================
#  Packet status vocabulary
# =====================================================================

PACKET_ASSEMBLED = "assembled"
PACKET_ASSEMBLED_DEGRADED = "assembled_degraded"
PACKET_FAILED = "failed"

VALID_PACKET_STATUSES = frozenset({
    PACKET_ASSEMBLED,
    PACKET_ASSEMBLED_DEGRADED,
    PACKET_FAILED,
})


# =====================================================================
#  Section availability vocabulary
# =====================================================================

SECTION_PRESENT = "present"
SECTION_MISSING = "missing"
SECTION_DEGRADED = "degraded"


# =====================================================================
#  Candidate section builder
# =====================================================================

def build_candidate_section(enriched_data: dict[str, Any]) -> dict[str, Any]:
    """Extract a compact candidate/setup section from the enriched packet.

    Pulls identifying fields, ranking info, and enrichment metadata.
    Does NOT deep-copy the entire enriched packet — keeps it lean.
    """
    return {
        "candidate_id": enriched_data.get("candidate_id"),
        "symbol": enriched_data.get("symbol"),
        "scanner_key": enriched_data.get("scanner_key"),
        "scanner_family": enriched_data.get("scanner_family"),
        "strategy_type": enriched_data.get("strategy_type"),
        "direction": enriched_data.get("direction"),
        "rank_position": enriched_data.get("rank_position"),
        "rank_score": enriched_data.get("rank_score"),
        "setup_quality": enriched_data.get("setup_quality"),
        "confidence": enriched_data.get("confidence"),
        "enrichment_status": enriched_data.get("enrichment_status"),
        "enrichment_notes": enriched_data.get("enrichment_notes", []),
        "compact_context_summary": enriched_data.get(
            "compact_context_summary",
        ),
    }


# =====================================================================
#  Event section builder
# =====================================================================

def build_event_section(
    event_ctx: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a compact event section from Step 10 event context.

    If event_ctx is None, returns an explicit unavailable section
    so downstream stages know events were absent — never silently omits.
    """
    if event_ctx is None:
        return {
            "event_data_available": False,
            "event_status": None,
            "event_summary": {},
            "risk_flags": [],
            "nearest_event_type": None,
            "nearest_days_until": None,
            "degraded_reasons": ["event context not available"],
        }

    summary = event_ctx.get("event_summary", {})
    return {
        "event_data_available": True,
        "event_status": event_ctx.get("event_status"),
        "event_summary": summary,
        "risk_flags": event_ctx.get("event_risk_flags", []),
        "nearest_event_type": summary.get("nearest_event_type"),
        "nearest_days_until": summary.get("nearest_days_until"),
        "degraded_reasons": event_ctx.get("degraded_reasons", []),
    }


# =====================================================================
#  Policy section builder
# =====================================================================

def build_policy_section(policy_output: dict[str, Any]) -> dict[str, Any]:
    """Build the policy section from Step 11 policy output.

    Preserves the authoritative policy outcome, structured checks,
    blockers/cautions/restrictions, portfolio summary, and event risk
    summary exactly as produced by the policy stage.

    Does NOT reinterpret or downgrade policy outcomes.
    """
    return {
        "policy_version": policy_output.get("policy_version"),
        "policy_status": policy_output.get("policy_status"),
        "overall_outcome": policy_output.get("overall_outcome"),
        "checks": policy_output.get("checks", []),
        "blocking_reasons": policy_output.get("blocking_reasons", []),
        "caution_reasons": policy_output.get("caution_reasons", []),
        "restriction_reasons": policy_output.get("restriction_reasons", []),
        "eligibility_flags": policy_output.get("eligibility_flags", {}),
        "portfolio_context_summary": policy_output.get(
            "portfolio_context_summary", {},
        ),
        "event_risk_summary": policy_output.get("event_risk_summary", {}),
        "downstream_usable": policy_output.get("downstream_usable", False),
        "degraded_reasons": policy_output.get("degraded_reasons", []),
        "policy_metadata": policy_output.get("policy_metadata", {}),
    }


# =====================================================================
#  Quality section builder
# =====================================================================

def build_quality_section(
    *,
    enriched_data: dict[str, Any],
    policy_output: dict[str, Any],
    event_ctx: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a quality / availability section for the decision packet.

    Tracks section-level statuses so downstream consumers can quickly
    assess packet completeness without inspecting each section.
    """
    # Section availability
    candidate_status = SECTION_PRESENT
    enrichment_status = enriched_data.get("enrichment_status")
    if enrichment_status == "degraded":
        candidate_status = SECTION_DEGRADED

    policy_status_raw = policy_output.get("policy_status")
    if policy_status_raw in ("evaluated_degraded",):
        policy_section_status = SECTION_DEGRADED
    elif policy_status_raw in ("failed", "skipped_invalid_candidate"):
        policy_section_status = SECTION_DEGRADED
    else:
        policy_section_status = SECTION_PRESENT

    if event_ctx is None:
        event_section_status = SECTION_MISSING
    elif event_ctx.get("event_status") in ("failed",):
        event_section_status = SECTION_DEGRADED
    elif event_ctx.get("event_status") in ("enriched_degraded",):
        event_section_status = SECTION_DEGRADED
    else:
        event_section_status = SECTION_PRESENT

    sections = {
        "candidate_section": candidate_status,
        "event_section": event_section_status,
        "policy_section": policy_section_status,
    }

    missing_sections = [
        name for name, st in sections.items() if st == SECTION_MISSING
    ]
    degraded_sections = [
        name for name, st in sections.items() if st == SECTION_DEGRADED
    ]

    # Collect degraded reasons from all sources
    degraded_reasons: list[str] = []
    if enrichment_status == "degraded":
        degraded_reasons.extend(enriched_data.get("enrichment_notes", []))
    degraded_reasons.extend(policy_output.get("degraded_reasons", []))
    if event_ctx is None:
        degraded_reasons.append("event context not available")
    else:
        degraded_reasons.extend(event_ctx.get("degraded_reasons", []))

    # Downstream usable — requires both candidate and policy sections
    # to be present/degraded.  Policy downstream_usable is authoritative.
    policy_usable = policy_output.get("downstream_usable", False)
    downstream_usable = (
        policy_usable
        and candidate_status != SECTION_MISSING
    )

    return {
        "section_statuses": sections,
        "missing_sections": missing_sections,
        "degraded_sections": degraded_sections,
        "degraded_reasons": degraded_reasons,
        "downstream_usable": downstream_usable,
    }


# =====================================================================
#  Decision packet assembler
# =====================================================================

def assemble_decision_packet(
    *,
    enriched_data: dict[str, Any],
    policy_output: dict[str, Any],
    event_ctx: dict[str, Any] | None,
    run_id: str,
    enriched_artifact_ref: str | None,
    policy_artifact_ref: str | None,
    event_artifact_ref: str | None,
) -> dict[str, Any]:
    """Assemble a canonical per-candidate decision packet.

    Combines enriched candidate data, policy output, and optional
    event context into a single structured packet with explicit
    section boundaries.

    Parameters
    ----------
    enriched_data : dict
        Per-candidate enriched packet from Step 9.
    policy_output : dict
        Per-candidate policy output from Step 11.
    event_ctx : dict | None
        Per-candidate event context from Step 10 (may be None).
    run_id : str
        Pipeline run identifier.
    enriched_artifact_ref : str | None
        Artifact ID of the enriched candidate artifact.
    policy_artifact_ref : str | None
        Artifact ID of the policy artifact.
    event_artifact_ref : str | None
        Artifact ID of the event context artifact.

    Returns
    -------
    dict
        Canonical decision packet with section boundaries preserved.
    """
    candidate_id = enriched_data.get("candidate_id")
    symbol = enriched_data.get("symbol")

    candidate_section = build_candidate_section(enriched_data)
    event_section = build_event_section(event_ctx)
    policy_section = build_policy_section(policy_output)
    quality_section = build_quality_section(
        enriched_data=enriched_data,
        policy_output=policy_output,
        event_ctx=event_ctx,
    )

    # Determine packet status
    has_degradation = (
        quality_section["missing_sections"]
        or quality_section["degraded_sections"]
    )
    packet_status = (
        PACKET_ASSEMBLED_DEGRADED if has_degradation
        else PACKET_ASSEMBLED
    )

    return {
        "decision_packet_version": _DECISION_PACKET_VERSION,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "symbol": symbol,
        "packet_status": packet_status,
        # Source references — lineage back to upstream artifacts
        "source_refs": {
            "enriched_candidate_ref": enriched_artifact_ref,
            "policy_ref": policy_artifact_ref,
            "event_context_ref": event_artifact_ref,
            "shared_context_ref": enriched_data.get(
                "shared_context_artifact_ref",
            ),
        },
        # Section boundaries — explicit and stable
        "candidate_section": candidate_section,
        "event_section": event_section,
        "policy_section": policy_section,
        "quality_section": quality_section,
        # Metadata
        "metadata": {
            "assembly_timestamp": datetime.now(timezone.utc).isoformat(),
            "packet_version": _DECISION_PACKET_VERSION,
            "stage_key": _STAGE_KEY,
            "policy_outcome": policy_output.get("overall_outcome"),
            "policy_version": policy_output.get("policy_version"),
            "downstream_usable": quality_section["downstream_usable"],
        },
    }


# =====================================================================
#  Per-candidate assembly record builder
# =====================================================================

def _build_assembly_record(
    *,
    candidate_id: str | None,
    symbol: str | None,
    packet_status: str,
    source_enriched_ref: str | None,
    source_policy_ref: str | None,
    source_event_ref: str | None,
    included_sections: list[str],
    missing_sections: list[str],
    degraded_reasons: list[str],
    output_artifact_ref: str | None,
    downstream_usable: bool,
    elapsed_ms: int,
    policy_outcome: str | None = None,
    error_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a per-candidate assembly record for the stage summary."""
    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "packet_status": packet_status,
        "source_enriched_candidate_ref": source_enriched_ref,
        "source_policy_ref": source_policy_ref,
        "source_event_ref": source_event_ref,
        "included_sections": included_sections,
        "missing_sections": missing_sections,
        "degraded_reasons": degraded_reasons,
        "output_artifact_ref": output_artifact_ref,
        "downstream_usable": downstream_usable,
        "policy_outcome": policy_outcome,
        "elapsed_ms": elapsed_ms,
        "error": error_info,
    }


# =====================================================================
#  Stage summary builder
# =====================================================================

def _build_stage_summary(
    *,
    stage_status: str,
    total_candidates_in: int,
    total_assembled: int,
    total_degraded: int,
    total_failed: int,
    assembly_records: list[dict[str, Any]],
    output_artifact_refs: dict[str, str],
    section_availability: dict[str, int],
    policy_outcome_counts: dict[str, int],
    warnings: list[str],
    elapsed_ms: int,
) -> dict[str, Any]:
    """Build the decision packet stage summary dict."""
    return {
        "stage_key": _STAGE_KEY,
        "stage_status": stage_status,
        "total_candidates_in": total_candidates_in,
        "total_assembled": total_assembled,
        "total_degraded": total_degraded,
        "total_failed": total_failed,
        "candidate_ids_processed": [
            r.get("candidate_id") for r in assembly_records
        ],
        "output_artifact_refs": output_artifact_refs,
        "section_availability": section_availability,
        "policy_outcome_counts": policy_outcome_counts,
        "assembly_records": assembly_records,
        "warnings": warnings,
        "summary_artifact_ref": None,  # filled after write
        "elapsed_ms": elapsed_ms,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
#  Event emission helper
# =====================================================================

def _make_event_emitter(
    run: dict[str, Any],
    event_callback: Callable[..., None] | None,
) -> Callable[..., None] | None:
    """Build an event emitter closure for decision packet stage events."""
    if event_callback is None:
        return None

    run_id = run["run_id"]

    def _emit(
        event_type: str,
        level: str = "info",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        merged_meta: dict[str, Any] = {"stage_key": _STAGE_KEY}
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

        counts = run.get("log_event_counts", {})
        counts["total"] = counts.get("total", 0) + 1
        by_level = counts.get("by_level", {})
        by_level[level] = by_level.get(level, 0) + 1

        try:
            event_callback(event)
        except Exception:
            logger.warning(
                "Event callback raised during decision packet event '%s'",
                event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Upstream artifact retrieval
# =====================================================================

def _retrieve_enrichment_summary(
    artifact_store: dict[str, Any],
) -> dict[str, Any] | None:
    """Retrieve candidate_enrichment_summary from Step 9."""
    art = get_artifact_by_key(
        artifact_store, "candidate_enrichment",
        "candidate_enrichment_summary",
    )
    if art is None:
        return None
    return art.get("data") or {}


def _retrieve_policy_summary(
    artifact_store: dict[str, Any],
) -> dict[str, Any] | None:
    """Retrieve policy_stage_summary from Step 11."""
    art = get_artifact_by_key(
        artifact_store, "policy",
        "policy_stage_summary",
    )
    if art is None:
        return None
    return art.get("data") or {}


def _retrieve_enriched_candidate(
    artifact_store: dict[str, Any],
    candidate_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Retrieve a per-candidate enriched packet from Step 9.

    Returns ``(enriched_data, artifact_id)`` or ``(None, None)``.
    """
    art = get_artifact_by_key(
        artifact_store, "candidate_enrichment",
        f"enriched_{candidate_id}",
    )
    if art is None:
        return None, None
    return art.get("data") or {}, art.get("artifact_id")


def _retrieve_policy_output(
    artifact_store: dict[str, Any],
    candidate_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Retrieve per-candidate policy output from Step 11.

    Returns ``(policy_data, artifact_id)`` or ``(None, None)``.
    """
    art = get_artifact_by_key(
        artifact_store, "policy",
        f"policy_{candidate_id}",
    )
    if art is None:
        return None, None
    return art.get("data") or {}, art.get("artifact_id")


def _retrieve_event_context(
    artifact_store: dict[str, Any],
    candidate_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Retrieve per-candidate event context from Step 10.

    Returns ``(event_data, artifact_id)`` or ``(None, None)``.
    Event context is optional — absence is not a failure.
    """
    art = get_artifact_by_key(
        artifact_store, "events", f"event_{candidate_id}",
    )
    if art is None:
        return None, None
    return art.get("data") or {}, art.get("artifact_id")


# =====================================================================
#  Artifact writers
# =====================================================================

def _write_decision_packet_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    candidate_id: str | None,
    packet: dict[str, Any],
) -> str:
    """Write one decision_packet artifact.  Returns artifact_id."""
    artifact_key = (
        f"decision_{candidate_id}" if candidate_id
        else "decision_unknown"
    )

    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key=artifact_key,
        artifact_type="decision_packet",
        data=packet,
        candidate_id=candidate_id,
        summary={
            "candidate_id": candidate_id,
            "symbol": packet.get("symbol"),
            "packet_status": packet.get("packet_status"),
            "policy_outcome": packet.get("policy_section", {}).get(
                "overall_outcome",
            ),
            "downstream_usable": packet.get("quality_section", {}).get(
                "downstream_usable",
            ),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_summary_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    summary: dict[str, Any],
) -> str:
    """Write the decision_packet_summary artifact.  Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="decision_packet_summary",
        artifact_type="decision_packet_summary",
        data=summary,
        summary={
            "stage_status": summary.get("stage_status"),
            "total_assembled": summary.get("total_assembled"),
            "total_degraded": summary.get("total_degraded"),
            "total_failed": summary.get("total_failed"),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


# =====================================================================
#  Vacuous completion helper
# =====================================================================

def _vacuous_completion(
    artifact_store: dict[str, Any],
    run_id: str,
    emit: Callable[..., None] | None,
    elapsed_ms: int,
    note: str,
) -> dict[str, Any]:
    """Return a vacuous completion when there are no candidates."""
    summary = _build_stage_summary(
        stage_status="no_candidates_to_process",
        total_candidates_in=0,
        total_assembled=0,
        total_degraded=0,
        total_failed=0,
        assembly_records=[],
        output_artifact_refs={},
        section_availability={},
        policy_outcome_counts={},
        warnings=[note],
        elapsed_ms=elapsed_ms,
    )
    summary_art_id = _write_summary_artifact(artifact_store, run_id, summary)
    summary["summary_artifact_ref"] = summary_art_id

    if emit:
        emit(
            "decision_packet_completed",
            message=f"Decision packet assembly vacuous: {note}",
            metadata={"note": note},
        )

    return {
        "outcome": "completed",
        "summary_counts": _empty_summary_counts(),
        "artifacts": [],
        "metadata": {
            "stage_status": "no_candidates_to_process",
            "note": note,
            "elapsed_ms": elapsed_ms,
        },
        "error": None,
    }


def _empty_summary_counts() -> dict[str, int]:
    return {
        "total_assembled": 0,
        "total_degraded": 0,
        "total_failed": 0,
    }


# =====================================================================
#  Stage handler — public entry point
# =====================================================================

def decision_packet_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Trade decision packet assembly stage handler (Step 12).

    Retrieves enriched candidate packets (Step 9), policy outcomes
    (Step 11), and optional event context (Step 10), then assembles
    one canonical per-candidate decision packet per candidate.

    Parameters
    ----------
    run : dict
        The pipeline run dict (passed by orchestrator).
    artifact_store : dict
        The artifact store (passed by orchestrator).
    stage_key : str
        Expected to be "orchestration".
    **kwargs
        event_callback : callable | None
            Optional event callback for structured events.

    Returns
    -------
    dict[str, Any]
        Handler result: { outcome, summary_counts, artifacts,
        metadata, error }
    """
    t0 = time.monotonic()
    run_id = run["run_id"]

    # ── 1. Resolve parameters ───────────────────────────────────
    event_callback = kwargs.get("event_callback")
    emit = _make_event_emitter(run, event_callback)

    # ── 2. Emit decision_packet_started ─────────────────────────
    if emit:
        emit(
            "decision_packet_started",
            message="Decision packet assembly stage started",
        )

    # ── 3. Retrieve enrichment summary (required) ───────────────
    try:
        enrichment_summary = _retrieve_enrichment_summary(artifact_store)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "Decision packet stage failed during enrichment retrieval: %s",
            exc, exc_info=True,
        )
        if emit:
            emit(
                "decision_packet_failed",
                level="error",
                message=f"Enrichment retrieval failed: {exc}",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="DECISION_PACKET_UPSTREAM_ERROR",
                message=f"Failed to retrieve enrichment summary: {exc}",
                source=_STAGE_KEY,
            ),
        }

    if enrichment_summary is None:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("No candidate_enrichment_summary found")
        if emit:
            emit(
                "decision_packet_failed",
                level="error",
                message="No candidate enrichment summary found",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="NO_CANDIDATE_ENRICHMENT_SOURCE",
                message="candidate_enrichment_summary not found",
                source=_STAGE_KEY,
            ),
        }

    # ── 4. Retrieve policy summary (required) ───────────────────
    try:
        policy_summary = _retrieve_policy_summary(artifact_store)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "Decision packet stage failed during policy retrieval: %s",
            exc, exc_info=True,
        )
        if emit:
            emit(
                "decision_packet_failed",
                level="error",
                message=f"Policy retrieval failed: {exc}",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="DECISION_PACKET_UPSTREAM_ERROR",
                message=f"Failed to retrieve policy summary: {exc}",
                source=_STAGE_KEY,
            ),
        }

    if policy_summary is None:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("No policy_stage_summary found")
        if emit:
            emit(
                "decision_packet_failed",
                level="error",
                message="No policy stage summary found",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="NO_POLICY_SOURCE",
                message="policy_stage_summary not found",
                source=_STAGE_KEY,
            ),
        }

    # ── 5. Extract candidate IDs ────────────────────────────────
    enrichment_records = enrichment_summary.get("enrichment_records", [])
    candidate_ids = [
        r.get("candidate_id") for r in enrichment_records
        if r.get("candidate_id")
    ]

    if not candidate_ids:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return _vacuous_completion(
            artifact_store, run_id, emit, elapsed_ms,
            note="Zero enriched candidates",
        )

    # ── 6. Process each candidate ───────────────────────────────
    assembly_records: list[dict[str, Any]] = []
    output_artifact_refs: dict[str, str] = {}
    # Track section availability counts
    section_availability: dict[str, int] = {
        "candidate_present": 0,
        "candidate_degraded": 0,
        "event_present": 0,
        "event_missing": 0,
        "event_degraded": 0,
        "policy_present": 0,
        "policy_degraded": 0,
    }
    policy_outcome_counts: dict[str, int] = {}
    warnings: list[str] = []
    total_assembled = 0
    total_degraded = 0
    total_failed = 0

    for cand_id in candidate_ids:
        cand_t0 = time.monotonic()

        # ── 6a. Retrieve enriched packet (required) ─────────────
        enriched_data, enriched_art_id = _retrieve_enriched_candidate(
            artifact_store, cand_id,
        )

        if enriched_data is None:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            assembly_records.append(_build_assembly_record(
                candidate_id=cand_id,
                symbol=None,
                packet_status=PACKET_FAILED,
                source_enriched_ref=None,
                source_policy_ref=None,
                source_event_ref=None,
                included_sections=[],
                missing_sections=[
                    "candidate_section", "policy_section", "event_section",
                ],
                degraded_reasons=["enriched packet missing"],
                output_artifact_ref=None,
                downstream_usable=False,
                elapsed_ms=cand_elapsed,
                error_info={
                    "code": "ENRICHED_PACKET_MISSING",
                    "message": f"No enriched packet for {cand_id}",
                },
            ))
            continue

        symbol = enriched_data.get("symbol")

        # ── 6b. Retrieve policy output (required) ──────────────
        policy_output, policy_art_id = _retrieve_policy_output(
            artifact_store, cand_id,
        )

        if policy_output is None:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            assembly_records.append(_build_assembly_record(
                candidate_id=cand_id,
                symbol=symbol,
                packet_status=PACKET_FAILED,
                source_enriched_ref=enriched_art_id,
                source_policy_ref=None,
                source_event_ref=None,
                included_sections=["candidate_section"],
                missing_sections=["policy_section", "event_section"],
                degraded_reasons=["policy output missing"],
                output_artifact_ref=None,
                downstream_usable=False,
                elapsed_ms=cand_elapsed,
                error_info={
                    "code": "POLICY_OUTPUT_MISSING",
                    "message": f"No policy output for {cand_id}",
                },
            ))
            continue

        # ── 6c. Retrieve event context (optional) ──────────────
        event_ctx, event_art_id = _retrieve_event_context(
            artifact_store, cand_id,
        )

        # ── 6d. Assemble decision packet ───────────────────────
        try:
            packet = assemble_decision_packet(
                enriched_data=enriched_data,
                policy_output=policy_output,
                event_ctx=event_ctx,
                run_id=run_id,
                enriched_artifact_ref=enriched_art_id,
                policy_artifact_ref=policy_art_id,
                event_artifact_ref=event_art_id,
            )

            art_id = _write_decision_packet_artifact(
                artifact_store, run_id, cand_id, packet,
            )
            output_artifact_refs[cand_id] = art_id
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)

            pkt_status = packet["packet_status"]
            quality = packet["quality_section"]
            included = [
                name for name, st
                in quality["section_statuses"].items()
                if st != SECTION_MISSING
            ]
            missing = quality["missing_sections"]
            degraded_reasons = quality["degraded_reasons"]
            downstream_usable = quality["downstream_usable"]
            policy_outcome = packet["policy_section"]["overall_outcome"]

            # Track section availability
            cs = quality["section_statuses"].get("candidate_section")
            if cs == SECTION_PRESENT:
                section_availability["candidate_present"] += 1
            elif cs == SECTION_DEGRADED:
                section_availability["candidate_degraded"] += 1

            es = quality["section_statuses"].get("event_section")
            if es == SECTION_PRESENT:
                section_availability["event_present"] += 1
            elif es == SECTION_MISSING:
                section_availability["event_missing"] += 1
            elif es == SECTION_DEGRADED:
                section_availability["event_degraded"] += 1

            ps = quality["section_statuses"].get("policy_section")
            if ps == SECTION_PRESENT:
                section_availability["policy_present"] += 1
            elif ps == SECTION_DEGRADED:
                section_availability["policy_degraded"] += 1

            # Track policy outcome counts
            policy_outcome_counts[policy_outcome] = (
                policy_outcome_counts.get(policy_outcome, 0) + 1
            )

            if pkt_status == PACKET_ASSEMBLED:
                total_assembled += 1
            elif pkt_status == PACKET_ASSEMBLED_DEGRADED:
                total_assembled += 1
                total_degraded += 1

            assembly_records.append(_build_assembly_record(
                candidate_id=cand_id,
                symbol=symbol,
                packet_status=pkt_status,
                source_enriched_ref=enriched_art_id,
                source_policy_ref=policy_art_id,
                source_event_ref=event_art_id,
                included_sections=included,
                missing_sections=missing,
                degraded_reasons=degraded_reasons,
                output_artifact_ref=art_id,
                downstream_usable=downstream_usable,
                elapsed_ms=cand_elapsed,
                policy_outcome=policy_outcome,
            ))

        except Exception as exc:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            logger.error(
                "Decision packet assembly failed for candidate %s: %s",
                cand_id, exc, exc_info=True,
            )
            assembly_records.append(_build_assembly_record(
                candidate_id=cand_id,
                symbol=symbol,
                packet_status=PACKET_FAILED,
                source_enriched_ref=enriched_art_id,
                source_policy_ref=policy_art_id,
                source_event_ref=event_art_id,
                included_sections=[],
                missing_sections=[],
                degraded_reasons=[str(exc)],
                output_artifact_ref=None,
                downstream_usable=False,
                elapsed_ms=cand_elapsed,
                error_info={
                    "code": "PACKET_ASSEMBLY_ERROR",
                    "message": str(exc),
                },
            ))

    # ── 7. Compute stage status ─────────────────────────────────
    if total_failed > 0 and total_assembled == 0:
        stage_status = "failed"
    elif total_failed > 0:
        stage_status = "degraded"
    elif total_degraded > 0:
        stage_status = "degraded"
    else:
        stage_status = "success"

    # ── 8. Build and write stage summary ────────────────────────
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    summary = _build_stage_summary(
        stage_status=stage_status,
        total_candidates_in=len(candidate_ids),
        total_assembled=total_assembled,
        total_degraded=total_degraded,
        total_failed=total_failed,
        assembly_records=assembly_records,
        output_artifact_refs=output_artifact_refs,
        section_availability=section_availability,
        policy_outcome_counts=policy_outcome_counts,
        warnings=warnings,
        elapsed_ms=elapsed_ms,
    )
    summary_art_id = _write_summary_artifact(artifact_store, run_id, summary)
    summary["summary_artifact_ref"] = summary_art_id

    # ── 9. Handle all-failed case ───────────────────────────────
    if stage_status == "failed":
        if emit:
            emit(
                "decision_packet_failed",
                level="error",
                message=(
                    f"Decision packet assembly failed: "
                    f"{total_failed}/{len(candidate_ids)} candidates failed"
                ),
                metadata={
                    "total_failed": total_failed,
                    "total_assembled": total_assembled,
                },
            )
        return {
            "outcome": "failed",
            "summary_counts": {
                "total_assembled": total_assembled,
                "total_degraded": total_degraded,
                "total_failed": total_failed,
            },
            "artifacts": [],
            "metadata": {
                "stage_status": stage_status,
                "stage_summary": summary,
                "elapsed_ms": elapsed_ms,
            },
            "error": build_run_error(
                code="DECISION_PACKET_ALL_FAILED",
                message=(
                    f"All {total_failed} candidates failed "
                    f"decision packet assembly"
                ),
                source=_STAGE_KEY,
            ),
        }

    # ── 10. Emit success / degraded ─────────────────────────────
    if emit:
        emit(
            "decision_packet_completed",
            message=(
                f"Decision packet assembly completed: "
                f"{total_assembled}/{len(candidate_ids)} assembled"
                + (f" ({total_degraded} degraded)" if total_degraded else "")
            ),
            metadata={
                "total_assembled": total_assembled,
                "total_degraded": total_degraded,
                "total_failed": total_failed,
                "policy_outcome_counts": policy_outcome_counts,
            },
        )

    return {
        "outcome": "completed",
        "summary_counts": {
            "total_assembled": total_assembled,
            "total_degraded": total_degraded,
            "total_failed": total_failed,
        },
        "artifacts": list(output_artifact_refs.values()),
        "metadata": {
            "stage_status": stage_status,
            "stage_summary": summary,
            "elapsed_ms": elapsed_ms,
        },
        "error": None,
    }
