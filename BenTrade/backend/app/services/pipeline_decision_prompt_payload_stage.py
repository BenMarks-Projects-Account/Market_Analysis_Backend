"""Pipeline Prompt Payload Builder Stage — Step 13.

Converts each canonical decision packet (Step 12) into a compact,
model-consumable prompt payload artifact for the final recommendation
stage.

Public API
──────────
    prompt_payload_handler(run, artifact_store, stage_key, **kwargs)
        Stage handler compatible with the Step 3 orchestrator.
    build_prompt_payload(decision_packet, run_id, ...)
        Build a single per-candidate prompt payload dict.

Role boundary
─────────────
This module:
- Retrieves per-candidate decision packets from Step 12.
- Compresses and shapes packet sections into model-input form.
- Preserves policy guardrails and traceability.
- Writes per-candidate prompt_payload artifacts keyed prompt_{cid}.
- Writes a prompt_payload_summary artifact.
- Emits structured events via event_callback.

This module does NOT:
- Perform the final model call.
- Re-evaluate policy logic or override guardrails.
- Rank or compare candidates.
- Generate user-facing narratives.
- Mutate decision packets in place.
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

logger = logging.getLogger("bentrade.pipeline_decision_prompt_payload_stage")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "prompt_payload"
_PROMPT_PAYLOAD_VERSION = "1.0"


# =====================================================================
#  Payload status vocabulary
# =====================================================================

PAYLOAD_BUILT = "built"
PAYLOAD_BUILT_DEGRADED = "built_degraded"
PAYLOAD_FAILED = "failed"

VALID_PAYLOAD_STATUSES = frozenset({
    PAYLOAD_BUILT,
    PAYLOAD_BUILT_DEGRADED,
    PAYLOAD_FAILED,
})


# =====================================================================
#  Section compression — candidate
# =====================================================================

def compress_candidate_section(
    candidate_section: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Compress the candidate section into a compact model-input block.

    Keeps identifying fields, rank info, and setup quality.
    Trims bulky enrichment detail that the final model does not need.

    Returns
    -------
    tuple[dict, list[str]]
        (compact_block, list of trimmed_fields)
    """
    if candidate_section is None:
        return {}, ["candidate_section_missing"]

    trimmed: list[str] = []

    compact = {
        "candidate_id": candidate_section.get("candidate_id"),
        "symbol": candidate_section.get("symbol"),
        "strategy_type": candidate_section.get("strategy_type"),
        "scanner_family": candidate_section.get("scanner_family"),
        "direction": candidate_section.get("direction"),
        "rank_position": candidate_section.get("rank_position"),
        "rank_score": candidate_section.get("rank_score"),
        "setup_quality": candidate_section.get("setup_quality"),
        "confidence": candidate_section.get("confidence"),
        "enrichment_status": candidate_section.get("enrichment_status"),
    }

    # compact_context_summary: keep if present, note if trimmed
    ctx_summary = candidate_section.get("compact_context_summary")
    if ctx_summary:
        compact["context_summary"] = ctx_summary
    else:
        trimmed.append("compact_context_summary")

    # enrichment_notes: keep if short, summarize if long
    notes = candidate_section.get("enrichment_notes", [])
    if len(notes) <= 3:
        compact["enrichment_notes"] = notes
    else:
        compact["enrichment_notes"] = notes[:3]
        trimmed.append(f"enrichment_notes_truncated_from_{len(notes)}")

    # scanner_key is informational — keep it compact
    scanner_key = candidate_section.get("scanner_key")
    if scanner_key:
        compact["scanner_key"] = scanner_key

    return compact, trimmed


# =====================================================================
#  Section compression — event
# =====================================================================

def compress_event_section(
    event_section: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Compress the event section into a compact model-input block.

    Keeps nearest event, risk flags, and timing summary.
    Trims long event lists to top/nearest/most-relevant items.

    Returns
    -------
    tuple[dict, list[str]]
        (compact_block, list of trimmed_fields)
    """
    if event_section is None:
        return {"event_data_available": False}, ["event_section_missing"]

    trimmed: list[str] = []
    available = event_section.get("event_data_available", False)

    compact: dict[str, Any] = {
        "event_data_available": available,
        "event_status": event_section.get("event_status"),
    }

    if not available:
        compact["degraded_reasons"] = event_section.get(
            "degraded_reasons", [],
        )
        return compact, trimmed

    # Nearest event — always keep
    compact["nearest_event_type"] = event_section.get("nearest_event_type")
    compact["nearest_days_until"] = event_section.get("nearest_days_until")

    # Risk flags — keep all (compact already)
    risk_flags = event_section.get("risk_flags", [])
    compact["risk_flags"] = risk_flags

    # Event summary — keep but trim if oversized
    summary = event_section.get("event_summary", {})
    if isinstance(summary, dict) and len(summary) > 10:
        # Keep only the most important summary keys
        keep_keys = {
            "nearest_event_type", "nearest_days_until",
            "has_earnings", "has_dividend", "has_fda",
            "total_events", "high_impact_count",
        }
        compact["event_summary"] = {
            k: v for k, v in summary.items() if k in keep_keys
        }
        trimmed.append(
            f"event_summary_trimmed_from_{len(summary)}_keys",
        )
    else:
        compact["event_summary"] = summary

    # Degraded reasons — always keep
    deg = event_section.get("degraded_reasons", [])
    if deg:
        compact["degraded_reasons"] = deg

    return compact, trimmed


# =====================================================================
#  Section compression — policy
# =====================================================================

def compress_policy_section(
    policy_section: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Compress the policy section into a compact model-input block.

    Policy guardrails are non-negotiable — blockers, cautions, and
    restrictions remain explicit and machine-readable.  The checks
    list is summarized to pass/fail counts with failing checks
    preserved in full.

    Returns
    -------
    tuple[dict, list[str]]
        (compact_block, list of trimmed_fields)
    """
    if policy_section is None:
        return {}, ["policy_section_missing"]

    trimmed: list[str] = []

    compact: dict[str, Any] = {
        "overall_outcome": policy_section.get("overall_outcome"),
        "policy_status": policy_section.get("policy_status"),
        "downstream_usable": policy_section.get("downstream_usable", False),
    }

    # Blockers / cautions / restrictions — ALWAYS keep in full
    compact["blocking_reasons"] = policy_section.get(
        "blocking_reasons", [],
    )
    compact["caution_reasons"] = policy_section.get(
        "caution_reasons", [],
    )
    compact["restriction_reasons"] = policy_section.get(
        "restriction_reasons", [],
    )

    # Eligibility flags — keep (compact already)
    compact["eligibility_flags"] = policy_section.get(
        "eligibility_flags", {},
    )

    # Checks — summarize pass/fail, preserve failing checks in full
    checks = policy_section.get("checks", [])
    if checks:
        passing = [c for c in checks if c.get("passed", True)]
        failing = [c for c in checks if not c.get("passed", True)]
        compact["check_summary"] = {
            "total": len(checks),
            "passed": len(passing),
            "failed": len(failing),
        }
        # Keep failing checks in full — these are the ones that matter
        compact["failing_checks"] = failing
        if len(passing) > 0:
            trimmed.append(
                f"passing_checks_summarized_{len(passing)}",
            )
    else:
        compact["check_summary"] = {
            "total": 0, "passed": 0, "failed": 0,
        }
        compact["failing_checks"] = []

    # Portfolio context summary — keep compact form
    portfolio_ctx = policy_section.get("portfolio_context_summary", {})
    if portfolio_ctx:
        compact["portfolio_context_summary"] = portfolio_ctx

    # Event risk summary — keep compact form
    event_risk = policy_section.get("event_risk_summary", {})
    if event_risk:
        compact["event_risk_summary"] = event_risk

    # Degraded reasons — always keep
    deg = policy_section.get("degraded_reasons", [])
    if deg:
        compact["degraded_reasons"] = deg

    return compact, trimmed


# =====================================================================
#  Section compression — quality
# =====================================================================

def compress_quality_section(
    quality_section: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Compress the quality section into a compact model-input block.

    Quality signals affect recommendation reliability — keep all
    degradation and missing-section indicators.

    Returns
    -------
    tuple[dict, list[str]]
        (compact_block, list of trimmed_fields)
    """
    if quality_section is None:
        return {"downstream_usable": False}, ["quality_section_missing"]

    return {
        "section_statuses": quality_section.get("section_statuses", {}),
        "missing_sections": quality_section.get("missing_sections", []),
        "degraded_sections": quality_section.get("degraded_sections", []),
        "degraded_reasons": quality_section.get("degraded_reasons", []),
        "downstream_usable": quality_section.get("downstream_usable", False),
    }, []


# =====================================================================
#  Rendered prompt text builder (optional, template-based)
# =====================================================================

def render_prompt_text(
    *,
    compact_candidate: dict[str, Any],
    compact_event: dict[str, Any],
    compact_policy: dict[str, Any],
    compact_quality: dict[str, Any],
) -> str:
    """Render a deterministic text summary from the compact sections.

    This is secondary to the structured payload — it provides a
    human-readable (and model-readable) text wrapper built from the
    same structured data.  It does NOT replace the structured payload.
    """
    lines: list[str] = []

    # ── Candidate identity ──────────────────────────────────────
    symbol = compact_candidate.get("symbol", "UNKNOWN")
    strategy = compact_candidate.get("strategy_type", "unknown")
    direction = compact_candidate.get("direction", "unknown")
    rank = compact_candidate.get("rank_position")
    confidence = compact_candidate.get("confidence")

    lines.append(f"CANDIDATE: {symbol} | {strategy} | {direction}")
    if rank is not None:
        lines.append(f"  Rank: {rank}")
    if confidence is not None:
        lines.append(f"  Confidence: {confidence}")

    setup = compact_candidate.get("setup_quality")
    if setup:
        lines.append(f"  Setup Quality: {setup}")

    # ── Policy outcome ──────────────────────────────────────────
    outcome = compact_policy.get("overall_outcome", "unknown")
    lines.append(f"POLICY OUTCOME: {outcome}")

    blockers = compact_policy.get("blocking_reasons", [])
    if blockers:
        lines.append(f"  BLOCKERS: {'; '.join(str(b) for b in blockers)}")

    cautions = compact_policy.get("caution_reasons", [])
    if cautions:
        lines.append(f"  CAUTIONS: {'; '.join(str(c) for c in cautions)}")

    restrictions = compact_policy.get("restriction_reasons", [])
    if restrictions:
        lines.append(
            f"  RESTRICTIONS: {'; '.join(str(r) for r in restrictions)}",
        )

    check_summary = compact_policy.get("check_summary", {})
    if check_summary:
        lines.append(
            f"  Checks: {check_summary.get('passed', 0)} passed, "
            f"{check_summary.get('failed', 0)} failed "
            f"of {check_summary.get('total', 0)}",
        )

    # ── Event context ───────────────────────────────────────────
    if compact_event.get("event_data_available"):
        nearest = compact_event.get("nearest_event_type")
        days = compact_event.get("nearest_days_until")
        if nearest:
            lines.append(f"NEAREST EVENT: {nearest} in {days} days")
        risk_flags = compact_event.get("risk_flags", [])
        if risk_flags:
            lines.append(
                f"  Risk Flags: {', '.join(str(f) for f in risk_flags)}",
            )
    else:
        lines.append("EVENT CONTEXT: unavailable")

    # ── Quality notes ───────────────────────────────────────────
    missing = compact_quality.get("missing_sections", [])
    degraded = compact_quality.get("degraded_sections", [])
    if missing:
        lines.append(f"MISSING SECTIONS: {', '.join(missing)}")
    if degraded:
        lines.append(f"DEGRADED SECTIONS: {', '.join(degraded)}")

    usable = compact_quality.get("downstream_usable", False)
    lines.append(f"DOWNSTREAM USABLE: {usable}")

    return "\n".join(lines)


# =====================================================================
#  Prompt payload assembler
# =====================================================================

def build_prompt_payload(
    *,
    decision_packet: dict[str, Any],
    run_id: str,
    decision_packet_artifact_ref: str | None,
) -> dict[str, Any]:
    """Build a per-candidate prompt payload from a decision packet.

    Compresses each section, preserves traceability, and produces
    a structured payload that the final model execution stage can
    consume.

    Parameters
    ----------
    decision_packet : dict
        Per-candidate decision packet from Step 12.
    run_id : str
        Pipeline run identifier.
    decision_packet_artifact_ref : str | None
        Artifact ID of the source decision packet.

    Returns
    -------
    dict
        Structured prompt payload with compact sections.
    """
    candidate_id = decision_packet.get("candidate_id")
    symbol = decision_packet.get("symbol")

    # ── Compress each section ───────────────────────────────────
    compact_candidate, cand_trimmed = compress_candidate_section(
        decision_packet.get("candidate_section"),
    )
    compact_event, event_trimmed = compress_event_section(
        decision_packet.get("event_section"),
    )
    compact_policy, policy_trimmed = compress_policy_section(
        decision_packet.get("policy_section"),
    )
    compact_quality, quality_trimmed = compress_quality_section(
        decision_packet.get("quality_section"),
    )

    # ── Aggregate trimmed fields ────────────────────────────────
    all_trimmed = cand_trimmed + event_trimmed + policy_trimmed + quality_trimmed

    # ── Determine payload status ────────────────────────────────
    quality_section = decision_packet.get("quality_section", {})
    has_degradation = bool(
        quality_section.get("missing_sections")
        or quality_section.get("degraded_sections")
    )
    payload_status = (
        PAYLOAD_BUILT_DEGRADED if has_degradation else PAYLOAD_BUILT
    )

    # ── Downstream usability ────────────────────────────────────
    downstream_usable = quality_section.get("downstream_usable", False)

    # ── Warnings ────────────────────────────────────────────────
    warnings: list[str] = []
    degraded_reasons = quality_section.get("degraded_reasons", [])

    packet_status = decision_packet.get("packet_status")
    if packet_status == "assembled_degraded":
        warnings.append("source decision packet was degraded")

    # ── Rendered prompt text ────────────────────────────────────
    rendered_text = render_prompt_text(
        compact_candidate=compact_candidate,
        compact_event=compact_event,
        compact_policy=compact_policy,
        compact_quality=compact_quality,
    )

    # ── Sections compressed list ────────────────────────────────
    sections_compressed = []
    if compact_candidate:
        sections_compressed.append("candidate_section")
    if compact_event:
        sections_compressed.append("event_section")
    if compact_policy:
        sections_compressed.append("policy_section")
    if compact_quality:
        sections_compressed.append("quality_section")

    return {
        "prompt_payload_version": _PROMPT_PAYLOAD_VERSION,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "symbol": symbol,
        "payload_status": payload_status,
        "source_decision_packet_ref": decision_packet_artifact_ref,
        # Compact model-input sections
        "compact_candidate_block": compact_candidate,
        "compact_event_block": compact_event,
        "compact_policy_block": compact_policy,
        "compact_quality_block": compact_quality,
        # Rendered text wrapper (secondary, template-based)
        "rendered_prompt_text": rendered_text,
        # Compression / traceability metadata
        "compression_metadata": {
            "sections_compressed": sections_compressed,
            "trimmed_fields": all_trimmed,
            "payload_version": _PROMPT_PAYLOAD_VERSION,
        },
        "source_section_refs": {
            "decision_packet_ref": decision_packet_artifact_ref,
            "decision_packet_version": decision_packet.get(
                "decision_packet_version",
            ),
        },
        "downstream_usable": downstream_usable,
        "warnings": warnings,
        "degraded_reasons": list(degraded_reasons),
        "metadata": {
            "assembly_timestamp": datetime.now(timezone.utc).isoformat(),
            "payload_version": _PROMPT_PAYLOAD_VERSION,
            "stage_key": _STAGE_KEY,
            "policy_outcome": compact_policy.get("overall_outcome"),
            "policy_status": compact_policy.get("policy_status"),
            "downstream_usable": downstream_usable,
        },
    }


# =====================================================================
#  Per-candidate payload assembly record builder
# =====================================================================

def _build_payload_record(
    *,
    candidate_id: str | None,
    symbol: str | None,
    payload_status: str,
    source_decision_packet_ref: str | None,
    included_sections: list[str],
    trimmed_fields: list[str],
    degraded_reasons: list[str],
    output_artifact_ref: str | None,
    downstream_usable: bool,
    policy_outcome: str | None,
    elapsed_ms: int,
    error_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a per-candidate payload assembly record for the summary."""
    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "payload_status": payload_status,
        "source_decision_packet_ref": source_decision_packet_ref,
        "included_sections": included_sections,
        "trimmed_fields": trimmed_fields,
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
    total_built: int,
    total_degraded: int,
    total_failed: int,
    payload_records: list[dict[str, Any]],
    output_artifact_refs: dict[str, str],
    policy_outcome_counts: dict[str, int],
    compression_stats: dict[str, int],
    warnings: list[str],
    elapsed_ms: int,
) -> dict[str, Any]:
    """Build the prompt payload stage summary dict."""
    return {
        "stage_key": _STAGE_KEY,
        "stage_status": stage_status,
        "total_candidates_in": total_candidates_in,
        "total_built": total_built,
        "total_degraded": total_degraded,
        "total_failed": total_failed,
        "candidate_ids_processed": [
            r.get("candidate_id") for r in payload_records
        ],
        "output_artifact_refs": output_artifact_refs,
        "policy_outcome_counts": policy_outcome_counts,
        "compression_stats": compression_stats,
        "payload_records": payload_records,
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
    """Build an event emitter closure for prompt payload stage events."""
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
                "Event callback raised during prompt payload event '%s'",
                event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Upstream artifact retrieval
# =====================================================================

def _retrieve_decision_packet_summary(
    artifact_store: dict[str, Any],
) -> dict[str, Any] | None:
    """Retrieve decision_packet_summary from Step 12."""
    art = get_artifact_by_key(
        artifact_store, "orchestration", "decision_packet_summary",
    )
    if art is None:
        return None
    return art.get("data") or {}


def _retrieve_decision_packet(
    artifact_store: dict[str, Any],
    candidate_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Retrieve a per-candidate decision packet from Step 12.

    Returns ``(packet_data, artifact_id)`` or ``(None, None)``.
    """
    art = get_artifact_by_key(
        artifact_store, "orchestration", f"decision_{candidate_id}",
    )
    if art is None:
        return None, None
    return art.get("data") or {}, art.get("artifact_id")


# =====================================================================
#  Artifact writers
# =====================================================================

def _write_prompt_payload_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    candidate_id: str | None,
    payload: dict[str, Any],
) -> str:
    """Write one prompt_payload artifact.  Returns artifact_id."""
    artifact_key = (
        f"prompt_{candidate_id}" if candidate_id
        else "prompt_unknown"
    )

    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key=artifact_key,
        artifact_type="prompt_payload",
        data=payload,
        candidate_id=candidate_id,
        summary={
            "candidate_id": candidate_id,
            "symbol": payload.get("symbol"),
            "payload_status": payload.get("payload_status"),
            "policy_outcome": payload.get("metadata", {}).get(
                "policy_outcome",
            ),
            "downstream_usable": payload.get("downstream_usable"),
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
    """Write the prompt_payload_summary artifact.  Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="prompt_payload_summary",
        artifact_type="prompt_payload_summary",
        data=summary,
        summary={
            "stage_status": summary.get("stage_status"),
            "total_built": summary.get("total_built"),
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
        total_built=0,
        total_degraded=0,
        total_failed=0,
        payload_records=[],
        output_artifact_refs={},
        policy_outcome_counts={},
        compression_stats={},
        warnings=[note],
        elapsed_ms=elapsed_ms,
    )
    summary_art_id = _write_summary_artifact(artifact_store, run_id, summary)
    summary["summary_artifact_ref"] = summary_art_id

    if emit:
        emit(
            "prompt_payload_completed",
            message=f"Prompt payload build vacuous: {note}",
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
        "total_built": 0,
        "total_degraded": 0,
        "total_failed": 0,
    }


# =====================================================================
#  Stage handler — public entry point
# =====================================================================

def prompt_payload_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Prompt payload builder stage handler (Step 13).

    Retrieves per-candidate decision packets (Step 12), compresses
    and shapes sections, and produces per-candidate prompt payload
    artifacts for the final recommendation stage.

    Parameters
    ----------
    run : dict
        The pipeline run dict (passed by orchestrator).
    artifact_store : dict
        The artifact store (passed by orchestrator).
    stage_key : str
        Expected to be "prompt_payload".
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

    # ── 2. Emit prompt_payload_started ──────────────────────────
    if emit:
        emit(
            "prompt_payload_started",
            message="Prompt payload build stage started",
        )

    # ── 3. Retrieve decision packet summary (required) ──────────
    try:
        dp_summary = _retrieve_decision_packet_summary(artifact_store)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "Prompt payload stage failed during decision packet "
            "summary retrieval: %s", exc, exc_info=True,
        )
        if emit:
            emit(
                "prompt_payload_failed",
                level="error",
                message=f"Decision packet summary retrieval failed: {exc}",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="PROMPT_PAYLOAD_UPSTREAM_ERROR",
                message=(
                    f"Failed to retrieve decision packet summary: {exc}"
                ),
                source=_STAGE_KEY,
            ),
        }

    if dp_summary is None:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("No decision_packet_summary found")
        if emit:
            emit(
                "prompt_payload_failed",
                level="error",
                message="No decision packet summary found",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="NO_DECISION_PACKET_SOURCE",
                message="decision_packet_summary not found",
                source=_STAGE_KEY,
            ),
        }

    # ── 4. Extract candidate IDs from decision packet summary ───
    assembly_records_upstream = dp_summary.get("assembly_records", [])
    candidate_ids = [
        r.get("candidate_id") for r in assembly_records_upstream
        if r.get("candidate_id")
    ]

    if not candidate_ids:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return _vacuous_completion(
            artifact_store, run_id, emit, elapsed_ms,
            note="Zero candidates in decision packet summary",
        )

    # ── 5. Process each candidate ───────────────────────────────
    payload_records: list[dict[str, Any]] = []
    output_artifact_refs: dict[str, str] = {}
    policy_outcome_counts: dict[str, int] = {}
    total_trimmed_fields = 0
    warnings: list[str] = []
    total_built = 0
    total_degraded = 0
    total_failed = 0

    for cand_id in candidate_ids:
        cand_t0 = time.monotonic()

        # ── 5a. Retrieve decision packet (required) ─────────────
        packet_data, packet_art_id = _retrieve_decision_packet(
            artifact_store, cand_id,
        )

        if packet_data is None:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            payload_records.append(_build_payload_record(
                candidate_id=cand_id,
                symbol=None,
                payload_status=PAYLOAD_FAILED,
                source_decision_packet_ref=None,
                included_sections=[],
                trimmed_fields=[],
                degraded_reasons=["decision packet missing"],
                output_artifact_ref=None,
                downstream_usable=False,
                policy_outcome=None,
                elapsed_ms=cand_elapsed,
                error_info={
                    "code": "DECISION_PACKET_MISSING",
                    "message": f"No decision packet for {cand_id}",
                },
            ))
            continue

        symbol = packet_data.get("symbol")

        # ── 5b. Build prompt payload ───────────────────────────
        try:
            payload = build_prompt_payload(
                decision_packet=packet_data,
                run_id=run_id,
                decision_packet_artifact_ref=packet_art_id,
            )

            art_id = _write_prompt_payload_artifact(
                artifact_store, run_id, cand_id, payload,
            )
            output_artifact_refs[cand_id] = art_id
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)

            p_status = payload["payload_status"]
            trimmed = payload["compression_metadata"]["trimmed_fields"]
            total_trimmed_fields += len(trimmed)

            included = payload["compression_metadata"][
                "sections_compressed"
            ]
            degraded_reasons = payload.get("degraded_reasons", [])
            downstream_usable = payload["downstream_usable"]
            policy_outcome = payload["metadata"].get("policy_outcome")

            # Track policy outcome counts
            if policy_outcome:
                policy_outcome_counts[policy_outcome] = (
                    policy_outcome_counts.get(policy_outcome, 0) + 1
                )

            if p_status == PAYLOAD_BUILT:
                total_built += 1
            elif p_status == PAYLOAD_BUILT_DEGRADED:
                total_built += 1
                total_degraded += 1

            payload_records.append(_build_payload_record(
                candidate_id=cand_id,
                symbol=symbol,
                payload_status=p_status,
                source_decision_packet_ref=packet_art_id,
                included_sections=included,
                trimmed_fields=trimmed,
                degraded_reasons=degraded_reasons,
                output_artifact_ref=art_id,
                downstream_usable=downstream_usable,
                policy_outcome=policy_outcome,
                elapsed_ms=cand_elapsed,
            ))

        except Exception as exc:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            logger.error(
                "Prompt payload build failed for candidate %s: %s",
                cand_id, exc, exc_info=True,
            )
            payload_records.append(_build_payload_record(
                candidate_id=cand_id,
                symbol=symbol,
                payload_status=PAYLOAD_FAILED,
                source_decision_packet_ref=packet_art_id,
                included_sections=[],
                trimmed_fields=[],
                degraded_reasons=[str(exc)],
                output_artifact_ref=None,
                downstream_usable=False,
                policy_outcome=None,
                elapsed_ms=cand_elapsed,
                error_info={
                    "code": "PAYLOAD_BUILD_ERROR",
                    "message": str(exc),
                },
            ))

    # ── 6. Compute stage status ─────────────────────────────────
    if total_failed > 0 and total_built == 0:
        stage_status = "failed"
    elif total_failed > 0:
        stage_status = "degraded"
    elif total_degraded > 0:
        stage_status = "degraded"
    else:
        stage_status = "success"

    # ── 7. Build and write stage summary ────────────────────────
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    compression_stats = {
        "total_trimmed_fields": total_trimmed_fields,
        "candidates_with_trimming": sum(
            1 for r in payload_records if r.get("trimmed_fields")
        ),
    }

    summary = _build_stage_summary(
        stage_status=stage_status,
        total_candidates_in=len(candidate_ids),
        total_built=total_built,
        total_degraded=total_degraded,
        total_failed=total_failed,
        payload_records=payload_records,
        output_artifact_refs=output_artifact_refs,
        policy_outcome_counts=policy_outcome_counts,
        compression_stats=compression_stats,
        warnings=warnings,
        elapsed_ms=elapsed_ms,
    )
    summary_art_id = _write_summary_artifact(artifact_store, run_id, summary)
    summary["summary_artifact_ref"] = summary_art_id

    # ── 8. Handle all-failed case ───────────────────────────────
    if stage_status == "failed":
        if emit:
            emit(
                "prompt_payload_failed",
                level="error",
                message=(
                    f"Prompt payload build failed: "
                    f"{total_failed}/{len(candidate_ids)} candidates failed"
                ),
                metadata={
                    "total_failed": total_failed,
                    "total_built": total_built,
                },
            )
        return {
            "outcome": "failed",
            "summary_counts": {
                "total_built": total_built,
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
                code="PROMPT_PAYLOAD_ALL_FAILED",
                message=(
                    f"All {total_failed} candidates failed "
                    f"prompt payload build"
                ),
                source=_STAGE_KEY,
            ),
        }

    # ── 9. Emit success / degraded ──────────────────────────────
    if emit:
        emit(
            "prompt_payload_completed",
            message=(
                f"Prompt payload build completed: "
                f"{total_built}/{len(candidate_ids)} built"
                + (f" ({total_degraded} degraded)" if total_degraded else "")
            ),
            metadata={
                "total_built": total_built,
                "total_degraded": total_degraded,
                "total_failed": total_failed,
                "policy_outcome_counts": policy_outcome_counts,
                "compression_stats": compression_stats,
            },
        )

    return {
        "outcome": "completed",
        "summary_counts": {
            "total_built": total_built,
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
