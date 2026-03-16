"""Pipeline Event Context Stage — Step 10.

Attaches candidate-relevant event/catalyst/calendar context to each
enriched candidate packet from Step 9.  Produces per-candidate event
context artifacts plus a stage summary.

Public API
──────────
    event_context_handler(run, artifact_store, stage_key, **kwargs)
        Stage handler compatible with the Step 3 orchestrator.
    default_event_provider(lookup_input)
        Default event lookup provider (no live sources).

Role boundary
─────────────
This module:
- Retrieves per-candidate enriched packets from Step 9.
- Calls an injectable event provider to look up relevant events.
- Classifies event relevance and timing deterministically.
- Computes deterministic risk flags.
- Writes per-candidate event context artifacts (keyed event_{candidate_id}).
- Writes an event_context_summary artifact.
- Emits structured events via event_callback.

This module does NOT:
- Re-run any earlier stage.
- Perform policy gating or rejection logic.
- Build decision packets or prompt payloads.
- Make final recommendations.
- Mutate Step 9 enriched candidate artifacts in place.
- Deep-copy candidate packets into event artifacts.

This stage illuminates event-based risk; it does not swing the hammer.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
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

logger = logging.getLogger("bentrade.pipeline_event_context_stage")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "events"
_EVENT_CONTEXT_VERSION = "1.0"


# =====================================================================
#  Time window thresholds (calendar days)
# =====================================================================

NEARBY_DAYS = 7
"""Events within this many calendar days are considered nearby."""

SOON_DAYS = 14
"""Events within this many calendar days are considered soon."""

EXTENDED_DAYS = 30
"""Events within this many calendar days are considered within extended range."""


# =====================================================================
#  Vocabularies
# =====================================================================

# Event type vocabulary
EVENT_TYPE_EARNINGS = "earnings"
EVENT_TYPE_ECONOMIC = "economic"
EVENT_TYPE_EXPIRY = "expiry"
EVENT_TYPE_DIVIDEND = "dividend"
EVENT_TYPE_HOLIDAY = "holiday"

VALID_EVENT_CATEGORY_TYPES = frozenset({
    EVENT_TYPE_EARNINGS,
    EVENT_TYPE_ECONOMIC,
    EVENT_TYPE_EXPIRY,
    EVENT_TYPE_DIVIDEND,
    EVENT_TYPE_HOLIDAY,
})

# Relevance vocabulary
RELEVANCE_HIGH = "high"
RELEVANCE_MODERATE = "moderate"
RELEVANCE_LOW = "low"
RELEVANCE_NOT_RELEVANT = "not_relevant"

VALID_RELEVANCE_LEVELS = frozenset({
    RELEVANCE_HIGH,
    RELEVANCE_MODERATE,
    RELEVANCE_LOW,
    RELEVANCE_NOT_RELEVANT,
})

# Per-candidate event status vocabulary
EVENT_STATUS_ENRICHED = "enriched"
EVENT_STATUS_ENRICHED_DEGRADED = "enriched_degraded"
EVENT_STATUS_NO_RELEVANT = "no_relevant_events"
EVENT_STATUS_FAILED = "failed"

# Risk flag vocabulary
RISK_EARNINGS_NEARBY = "earnings_nearby"
RISK_MACRO_NEARBY = "macro_event_nearby"
RISK_EXPIRY_NEARBY = "expiry_nearby"
RISK_WINDOW_OVERLAP = "event_window_overlap"
RISK_NO_COVERAGE = "no_event_coverage"
RISK_LOOKUP_DEGRADED = "event_lookup_degraded"

VALID_RISK_FLAGS = frozenset({
    RISK_EARNINGS_NEARBY,
    RISK_MACRO_NEARBY,
    RISK_EXPIRY_NEARBY,
    RISK_WINDOW_OVERLAP,
    RISK_NO_COVERAGE,
    RISK_LOOKUP_DEGRADED,
})


# =====================================================================
#  Event provider type
# =====================================================================

EventProvider = Callable[[dict[str, Any]], dict[str, Any]]
"""Callable that takes a lookup_input dict and returns event data.

lookup_input keys:
    symbol: str
    strategy_type: str | None
    scanner_family: str | None
    direction: str | None
    as_of_date: str (ISO date)
    candidate_metadata: dict

Return shape:
    provider_status: "available" | "no_live_sources" | "degraded" | "failed"
    company_events: list[dict]
    macro_events: list[dict]
    expiry_events: list[dict]
    source_info: dict
    degraded_reasons: list[str]
"""


# =====================================================================
#  Default event provider
# =====================================================================

def default_event_provider(lookup_input: dict[str, Any]) -> dict[str, Any]:
    """Default event provider — no live data sources.

    Returns empty results with honest ``no_live_sources`` status.
    Replace with API-backed providers (earnings calendars, economic
    calendars, etc.) without changing stage logic.

    Parameters
    ----------
    lookup_input : dict
        Event lookup request.

    Returns
    -------
    dict
        Provider result with empty event lists.
    """
    return {
        "provider_status": "no_live_sources",
        "company_events": [],
        "macro_events": [],
        "expiry_events": [],
        "source_info": {
            "sources": [],
            "note": "No live event data sources configured",
        },
        "degraded_reasons": ["no live event data sources configured"],
    }


# =====================================================================
#  Time window helpers
# =====================================================================

def days_between(from_date_str: str, to_date_str: str) -> int | None:
    """Compute calendar days from *from_date* to *to_date*.

    Returns positive if *to_date* is in the future relative to
    *from_date*, negative if in the past.  Returns ``None`` if
    either date is unparseable.

    Parameters
    ----------
    from_date_str : str
        Reference date in ISO format (at least YYYY-MM-DD).
    to_date_str : str
        Target date in ISO format.
    """
    try:
        from_d = date.fromisoformat(from_date_str[:10])
        to_d = date.fromisoformat(to_date_str[:10])
        return (to_d - from_d).days
    except (ValueError, TypeError, AttributeError):
        return None


def is_within_window(days_until: int | None, window_days: int) -> bool:
    """Check if *days_until* falls within ``[0, window_days]``."""
    if days_until is None:
        return False
    return 0 <= days_until <= window_days


# =====================================================================
#  Relevance classification
# =====================================================================

def classify_event_relevance(
    event_type: str,
    days_until: int | None,
) -> str:
    """Classify event relevance based on type and proximity.

    Classification rules
    ────────────────────
    Input fields: event_type, days_until (calendar days, positive=future)

    earnings / economic:
        ≤ NEARBY_DAYS  → high
        ≤ SOON_DAYS    → moderate
        ≤ EXTENDED_DAYS → low
        > EXTENDED_DAYS → not_relevant

    expiry:
        ≤ NEARBY_DAYS  → high
        ≤ SOON_DAYS    → moderate
        > SOON_DAYS    → low

    other (dividend, holiday, unknown):
        ≤ NEARBY_DAYS  → moderate
        ≤ SOON_DAYS    → low
        > SOON_DAYS    → not_relevant

    None days_until → low (unknown timing)

    Parameters
    ----------
    event_type : str
        One of the event category vocabulary.
    days_until : int | None
        Calendar days from as_of_date (positive = future).

    Returns
    -------
    str
        One of: "high", "moderate", "low", "not_relevant"
    """
    if days_until is None:
        return RELEVANCE_LOW

    abs_days = abs(days_until)

    if event_type in (EVENT_TYPE_EARNINGS, EVENT_TYPE_ECONOMIC):
        if abs_days <= NEARBY_DAYS:
            return RELEVANCE_HIGH
        if abs_days <= SOON_DAYS:
            return RELEVANCE_MODERATE
        if abs_days <= EXTENDED_DAYS:
            return RELEVANCE_LOW
        return RELEVANCE_NOT_RELEVANT

    if event_type == EVENT_TYPE_EXPIRY:
        if abs_days <= NEARBY_DAYS:
            return RELEVANCE_HIGH
        if abs_days <= SOON_DAYS:
            return RELEVANCE_MODERATE
        return RELEVANCE_LOW

    # Other types (dividend, holiday, unknown)
    if abs_days <= NEARBY_DAYS:
        return RELEVANCE_MODERATE
    if abs_days <= SOON_DAYS:
        return RELEVANCE_LOW
    return RELEVANCE_NOT_RELEVANT


# =====================================================================
#  Risk flag computation
# =====================================================================

def compute_risk_flags(
    all_events: list[dict[str, Any]],
    provider_status: str,
) -> list[str]:
    """Compute deterministic risk flags from events and provider status.

    Risk flags are informational — they do NOT drive rejection.

    Flags
    ─────
    - earnings_nearby:      earnings event within SOON_DAYS
    - macro_event_nearby:   economic event within SOON_DAYS
    - expiry_nearby:        expiry event within NEARBY_DAYS
    - event_window_overlap: 2+ high-relevance upcoming events
    - no_event_coverage:    provider returned no_live_sources
    - event_lookup_degraded: provider returned degraded status

    Parameters
    ----------
    all_events : list[dict]
        Normalized events with event_type, days_until, relevance.
    provider_status : str
        Status from the event provider.

    Returns
    -------
    list[str]
        Deterministic list of risk flag strings.
    """
    flags: list[str] = []
    high_count = 0

    for ev in all_events:
        days = ev.get("days_until")
        etype = ev.get("event_type")
        relevance = ev.get("relevance")

        if relevance == RELEVANCE_HIGH:
            high_count += 1

        if (etype == EVENT_TYPE_EARNINGS
                and days is not None and 0 <= days <= SOON_DAYS
                and RISK_EARNINGS_NEARBY not in flags):
            flags.append(RISK_EARNINGS_NEARBY)

        if (etype == EVENT_TYPE_ECONOMIC
                and days is not None and 0 <= days <= SOON_DAYS
                and RISK_MACRO_NEARBY not in flags):
            flags.append(RISK_MACRO_NEARBY)

        if (etype == EVENT_TYPE_EXPIRY
                and days is not None and 0 <= days <= NEARBY_DAYS
                and RISK_EXPIRY_NEARBY not in flags):
            flags.append(RISK_EXPIRY_NEARBY)

    if high_count >= 2:
        flags.append(RISK_WINDOW_OVERLAP)

    if provider_status == "no_live_sources":
        flags.append(RISK_NO_COVERAGE)
    elif provider_status == "degraded":
        flags.append(RISK_LOOKUP_DEGRADED)

    return flags


# =====================================================================
#  Event normalization
# =====================================================================

def normalize_event(
    raw_event: dict[str, Any],
    as_of_date: str,
) -> dict[str, Any]:
    """Normalize a raw event record and add computed fields.

    Parameters
    ----------
    raw_event : dict
        Event from provider with at least: event_type, event_name,
        event_date, source.  May also have metadata.
    as_of_date : str
        Reference date for days_until computation.

    Returns
    -------
    dict
        Normalized event with keys: event_type, event_name, event_date,
        days_until, relevance, source, metadata.
    """
    event_type = raw_event.get("event_type", "unknown")
    event_date = raw_event.get("event_date")
    days_until = days_between(as_of_date, event_date) if event_date else None
    relevance = classify_event_relevance(event_type, days_until)

    return {
        "event_type": event_type,
        "event_name": raw_event.get("event_name", ""),
        "event_date": event_date,
        "days_until": days_until,
        "relevance": relevance,
        "source": raw_event.get("source", "unknown"),
        "metadata": raw_event.get("metadata", {}),
    }


def split_upcoming_recent(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split normalized events into upcoming (future/today) and recent (past).

    Returns ``(upcoming, recent)`` where:
    - upcoming: days_until >= 0 (including today)
    - recent: days_until < 0 (past events)
    - Events with ``None`` days_until go to upcoming (unknown timing).
    """
    upcoming: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    for ev in events:
        days = ev.get("days_until")
        if days is not None and days < 0:
            recent.append(ev)
        else:
            upcoming.append(ev)
    return upcoming, recent


def find_nearest_relevant(
    upcoming_events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the nearest upcoming event with relevance != not_relevant.

    Returns ``None`` if no relevant events are upcoming.
    """
    relevant = [
        ev for ev in upcoming_events
        if ev.get("relevance") != RELEVANCE_NOT_RELEVANT
        and ev.get("days_until") is not None
    ]
    if not relevant:
        return None
    return min(relevant, key=lambda e: e["days_until"])


# =====================================================================
#  Category context builders
# =====================================================================

def build_category_context(
    events: list[dict[str, Any]],
    category_event_type: str,
) -> dict[str, Any]:
    """Build a context dict for a specific event category.

    Parameters
    ----------
    events : list[dict]
        All normalized events.
    category_event_type : str
        Filter to events of this type.

    Returns
    -------
    dict
        available, event_count, events, nearest, has_nearby
    """
    filtered = [e for e in events if e.get("event_type") == category_event_type]
    upcoming = [
        e for e in filtered
        if e.get("days_until") is not None and e["days_until"] >= 0
    ]
    nearest = min(upcoming, key=lambda e: e["days_until"]) if upcoming else None

    return {
        "available": len(filtered) > 0,
        "event_count": len(filtered),
        "events": filtered,
        "nearest": nearest,
        "has_nearby": any(
            is_within_window(e.get("days_until"), NEARBY_DAYS)
            for e in filtered
        ),
    }


# =====================================================================
#  Event emission helper
# =====================================================================

def _make_event_emitter(
    run: dict[str, Any],
    event_callback: Callable[..., None] | None,
) -> Callable[..., None] | None:
    """Build an event emitter closure for event context events."""
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

        counts = run.get("log_event_counts", {})
        counts["total"] = counts.get("total", 0) + 1
        by_level = counts.get("by_level", {})
        by_level[level] = by_level.get(level, 0) + 1

        try:
            event_callback(event)
        except Exception:
            logger.warning(
                "Event callback raised during event context event '%s'",
                event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Upstream artifact retrieval
# =====================================================================

def _retrieve_enrichment_summary(
    artifact_store: dict[str, Any],
) -> dict[str, Any] | None:
    """Retrieve candidate_enrichment_summary from Step 9.

    Returns the summary data dict, or None if not found.
    """
    art = get_artifact_by_key(
        artifact_store, "candidate_enrichment", "candidate_enrichment_summary",
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
        artifact_store, "candidate_enrichment", f"enriched_{candidate_id}",
    )
    if art is None:
        return None, None
    return art.get("data") or {}, art.get("artifact_id")


# =====================================================================
#  Per-candidate event context builder
# =====================================================================

def build_candidate_event_context(
    enriched_data: dict[str, Any],
    enriched_artifact_ref: str | None,
    provider_result: dict[str, Any],
    run_id: str,
    as_of_date: str,
) -> dict[str, Any]:
    """Build the per-candidate event context record.

    Combines enriched candidate data with event provider results
    to produce a stable event context dict.

    Parameters
    ----------
    enriched_data : dict
        Per-candidate enriched packet from Step 9.
    enriched_artifact_ref : str | None
        Artifact ID of the enriched packet.
    provider_result : dict
        Output from the event provider.
    run_id : str
        Pipeline run ID.
    as_of_date : str
        Reference date for timing calculations.

    Returns
    -------
    dict
        Per-candidate event context with all contract fields.
    """
    candidate_id = enriched_data.get("candidate_id")
    symbol = enriched_data.get("symbol")

    # Normalize all events
    all_raw = (
        provider_result.get("company_events", [])
        + provider_result.get("macro_events", [])
        + provider_result.get("expiry_events", [])
    )
    all_normalized = [normalize_event(ev, as_of_date) for ev in all_raw]

    # Split upcoming / recent
    upcoming, recent = split_upcoming_recent(all_normalized)

    # Find nearest relevant
    nearest = find_nearest_relevant(upcoming)

    # Build category contexts
    company_ctx = build_category_context(all_normalized, EVENT_TYPE_EARNINGS)
    macro_ctx = build_category_context(all_normalized, EVENT_TYPE_ECONOMIC)
    expiry_ctx = build_category_context(all_normalized, EVENT_TYPE_EXPIRY)

    # Compute risk flags
    provider_status = provider_result.get("provider_status", "unknown")
    risk_flags = compute_risk_flags(all_normalized, provider_status)

    # Determine event status
    degraded_reasons = list(provider_result.get("degraded_reasons", []))

    if provider_status == "failed":
        event_status = EVENT_STATUS_FAILED
    elif not all_normalized and provider_status == "degraded":
        event_status = EVENT_STATUS_ENRICHED_DEGRADED
    elif degraded_reasons and all_normalized:
        event_status = EVENT_STATUS_ENRICHED_DEGRADED
    elif all_normalized:
        event_status = EVENT_STATUS_ENRICHED
    else:
        event_status = EVENT_STATUS_NO_RELEVANT

    # Event summary — compact overview
    event_summary = {
        "total_events": len(all_normalized),
        "upcoming_count": len(upcoming),
        "recent_count": len(recent),
        "nearest_event_type": nearest["event_type"] if nearest else None,
        "nearest_event_date": nearest["event_date"] if nearest else None,
        "nearest_days_until": nearest["days_until"] if nearest else None,
        "risk_flag_count": len(risk_flags),
        "provider_status": provider_status,
    }

    downstream_usable = event_status in (
        EVENT_STATUS_ENRICHED,
        EVENT_STATUS_ENRICHED_DEGRADED,
        EVENT_STATUS_NO_RELEVANT,
    )

    return {
        "event_context_version": _EVENT_CONTEXT_VERSION,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "source_enriched_candidate_ref": enriched_artifact_ref,
        "symbol": symbol,
        "event_status": event_status,
        "event_summary": event_summary,
        "upcoming_events": upcoming,
        "recent_events": recent,
        "nearest_relevant_event": nearest,
        "macro_event_context": macro_ctx,
        "company_event_context": company_ctx,
        "expiry_event_context": expiry_ctx,
        "event_risk_flags": risk_flags,
        "event_source_refs": provider_result.get("source_info", {}),
        "degraded_reasons": degraded_reasons,
        "downstream_usable": downstream_usable,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
#  Per-candidate execution record builder
# =====================================================================

def _build_execution_record(
    candidate_id: str | None,
    symbol: str | None,
    event_status: str,
    lookup_status: str,
    source_enriched_candidate_ref: str | None,
    event_context: dict[str, Any] | None,
    output_artifact_ref: str | None,
    elapsed_ms: int,
    error_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a per-candidate execution record for the stage summary."""
    summary = event_context.get("event_summary", {}) if event_context else {}
    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "event_status": event_status,
        "lookup_status": lookup_status,
        "source_enriched_candidate_ref": source_enriched_candidate_ref,
        "event_count": summary.get("total_events", 0),
        "upcoming_count": summary.get("upcoming_count", 0),
        "recent_count": summary.get("recent_count", 0),
        "nearest_event_type": summary.get("nearest_event_type"),
        "nearest_event_date": summary.get("nearest_event_date"),
        "degraded_reasons": (event_context or {}).get("degraded_reasons", []),
        "output_artifact_ref": output_artifact_ref,
        "downstream_usable": (event_context or {}).get("downstream_usable", False),
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
    execution_records: list[dict[str, Any]],
    output_artifact_refs: dict[str, str],
    risk_flag_counts: dict[str, int],
    elapsed_ms: int,
    total_enriched: int = 0,
    total_no_events: int = 0,
    total_degraded: int = 0,
    total_failed: int = 0,
) -> dict[str, Any]:
    """Build the event context stage summary dict."""
    total_processed = total_enriched + total_no_events + total_degraded

    candidates_with_earnings = sum(
        1 for r in execution_records
        if r.get("nearest_event_type") == EVENT_TYPE_EARNINGS
    )
    candidates_with_macro = sum(
        1 for r in execution_records
        if r.get("nearest_event_type") == EVENT_TYPE_ECONOMIC
    )

    return {
        "stage_key": _STAGE_KEY,
        "stage_status": stage_status,
        "total_candidates_in": total_candidates_in,
        "total_processed": total_processed,
        "total_enriched": total_enriched,
        "total_no_events": total_no_events,
        "total_degraded": total_degraded,
        "total_failed": total_failed,
        "candidate_ids_processed": [
            r.get("candidate_id") for r in execution_records
        ],
        "output_artifact_refs": output_artifact_refs,
        "risk_flag_counts": risk_flag_counts,
        "candidates_with_nearby_earnings": candidates_with_earnings,
        "candidates_with_nearby_macro": candidates_with_macro,
        "execution_records": execution_records,
        "degraded_reasons": [
            reason
            for r in execution_records
            for reason in r.get("degraded_reasons", [])
        ],
        "summary_artifact_ref": None,  # filled after write
        "elapsed_ms": elapsed_ms,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
#  Artifact writers
# =====================================================================

def _write_event_context_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    candidate_id: str | None,
    event_context: dict[str, Any],
) -> str:
    """Write one event_context artifact.  Returns artifact_id."""
    artifact_key = f"event_{candidate_id}" if candidate_id else "event_unknown"

    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key=artifact_key,
        artifact_type="event_context",
        data=event_context,
        candidate_id=candidate_id,
        summary={
            "candidate_id": candidate_id,
            "symbol": event_context.get("symbol"),
            "event_status": event_context.get("event_status"),
            "risk_flag_count": len(event_context.get("event_risk_flags", [])),
            "downstream_usable": event_context.get("downstream_usable"),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_event_context_summary_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    summary: dict[str, Any],
) -> str:
    """Write the event_context_summary artifact.  Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="event_context_summary",
        artifact_type="event_context_summary",
        data=summary,
        summary={
            "stage_status": summary.get("stage_status"),
            "total_processed": summary.get("total_processed"),
            "total_enriched": summary.get("total_enriched"),
            "total_failed": summary.get("total_failed"),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


# =====================================================================
#  Stage handler — public entry point
# =====================================================================

def event_context_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Event context stage handler (Step 10).

    Retrieves per-candidate enriched packets from Step 9,
    looks up relevant events via injectable provider,
    classifies relevance and risk, writes per-candidate
    event context artifacts and stage summary.

    Parameters
    ----------
    run : dict
        The pipeline run dict (passed by orchestrator).
    artifact_store : dict
        The artifact store (passed by orchestrator).
    stage_key : str
        Expected to be "events".
    **kwargs
        event_callback : callable | None
            Optional event callback for structured events.
        event_provider : EventProvider | None
            Injectable event lookup provider.  Defaults to
            default_event_provider.

    Returns
    -------
    dict[str, Any]
        Handler result: { outcome, summary_counts, artifacts, metadata, error }
    """
    t0 = time.monotonic()
    run_id = run["run_id"]

    # ── 1. Resolve parameters ───────────────────────────────────
    event_callback = kwargs.get("event_callback")
    emit = _make_event_emitter(run, event_callback)
    provider: EventProvider = kwargs.get("event_provider") or default_event_provider

    as_of_date = (run.get("started_at") or datetime.now(timezone.utc).isoformat())[:10]

    # ── 2. Emit event_context_started ───────────────────────────
    if emit:
        emit(
            "event_context_started",
            message="Event context stage started",
        )

    # ── 3. Retrieve enrichment summary ──────────────────────────
    try:
        enrichment_summary = _retrieve_enrichment_summary(artifact_store)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "Event context failed during upstream retrieval: %s",
            exc, exc_info=True,
        )
        if emit:
            emit(
                "event_context_failed",
                level="error",
                message=f"Upstream retrieval failed: {exc}",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="EVENT_CONTEXT_UPSTREAM_ERROR",
                message=f"Failed to retrieve upstream artifacts: {exc}",
                source=_STAGE_KEY,
            ),
        }

    # ── 4. Extract candidate IDs ────────────────────────────────
    if enrichment_summary is None:
        # No enrichment summary — treat as no candidates
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("No candidate_enrichment_summary found")
        return _vacuous_completion(
            artifact_store, run_id, emit, elapsed_ms,
            note="No enrichment summary found",
        )

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

    # ── 5. Process each candidate ───────────────────────────────
    execution_records: list[dict[str, Any]] = []
    output_artifact_refs: dict[str, str] = {}
    risk_flag_counts: dict[str, int] = {}
    total_enriched = 0
    total_no_events = 0
    total_degraded = 0
    total_failed = 0

    for cand_id in candidate_ids:
        cand_t0 = time.monotonic()

        # Retrieve enriched packet
        enriched_data, enriched_art_id = _retrieve_enriched_candidate(
            artifact_store, cand_id,
        )

        if enriched_data is None:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            execution_records.append(_build_execution_record(
                candidate_id=cand_id,
                symbol=None,
                event_status=EVENT_STATUS_FAILED,
                lookup_status="enriched_packet_missing",
                source_enriched_candidate_ref=None,
                event_context=None,
                output_artifact_ref=None,
                elapsed_ms=cand_elapsed,
                error_info={
                    "code": "ENRICHED_PACKET_MISSING",
                    "message": f"No enriched packet for {cand_id}",
                },
            ))
            continue

        symbol = enriched_data.get("symbol")

        # Call event provider
        try:
            lookup_input = {
                "symbol": symbol,
                "strategy_type": enriched_data.get("strategy_type"),
                "scanner_family": enriched_data.get("scanner_family"),
                "direction": enriched_data.get("direction"),
                "as_of_date": as_of_date,
                "candidate_metadata": {
                    "candidate_id": cand_id,
                    "rank_position": enriched_data.get("rank_position"),
                    "confidence": enriched_data.get("confidence"),
                },
            }
            provider_result = provider(lookup_input)
        except Exception as exc:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            logger.error(
                "Event provider failed for candidate %s: %s",
                cand_id, exc, exc_info=True,
            )
            execution_records.append(_build_execution_record(
                candidate_id=cand_id,
                symbol=symbol,
                event_status=EVENT_STATUS_FAILED,
                lookup_status="provider_error",
                source_enriched_candidate_ref=enriched_art_id,
                event_context=None,
                output_artifact_ref=None,
                elapsed_ms=cand_elapsed,
                error_info={
                    "code": "EVENT_PROVIDER_ERROR",
                    "message": str(exc),
                },
            ))
            continue

        # Build event context and write artifact
        try:
            event_ctx = build_candidate_event_context(
                enriched_data=enriched_data,
                enriched_artifact_ref=enriched_art_id,
                provider_result=provider_result,
                run_id=run_id,
                as_of_date=as_of_date,
            )

            art_id = _write_event_context_artifact(
                artifact_store, run_id, cand_id, event_ctx,
            )
            output_artifact_refs[cand_id] = art_id
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)

            e_status = event_ctx["event_status"]

            if e_status == EVENT_STATUS_ENRICHED:
                total_enriched += 1
            elif e_status == EVENT_STATUS_NO_RELEVANT:
                total_no_events += 1
            elif e_status == EVENT_STATUS_ENRICHED_DEGRADED:
                total_degraded += 1
            else:
                total_failed += 1

            for flag in event_ctx.get("event_risk_flags", []):
                risk_flag_counts[flag] = risk_flag_counts.get(flag, 0) + 1

            execution_records.append(_build_execution_record(
                candidate_id=cand_id,
                symbol=symbol,
                event_status=e_status,
                lookup_status="completed",
                source_enriched_candidate_ref=enriched_art_id,
                event_context=event_ctx,
                output_artifact_ref=art_id,
                elapsed_ms=cand_elapsed,
            ))
        except Exception as exc:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            logger.error(
                "Event context build failed for candidate %s: %s",
                cand_id, exc, exc_info=True,
            )
            execution_records.append(_build_execution_record(
                candidate_id=cand_id,
                symbol=symbol,
                event_status=EVENT_STATUS_FAILED,
                lookup_status="build_error",
                source_enriched_candidate_ref=enriched_art_id,
                event_context=None,
                output_artifact_ref=None,
                elapsed_ms=cand_elapsed,
                error_info={
                    "code": "EVENT_CONTEXT_BUILD_ERROR",
                    "message": str(exc),
                },
            ))

    # ── 6. Compute stage status ─────────────────────────────────
    total_processed = total_enriched + total_no_events + total_degraded

    if total_failed > 0 and total_processed == 0:
        stage_status = "failed"
    elif total_failed > 0 or total_degraded > 0:
        stage_status = "degraded"
    else:
        stage_status = "success"

    # ── 7. Build and write stage summary ────────────────────────
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    summary = _build_stage_summary(
        stage_status=stage_status,
        total_candidates_in=len(candidate_ids),
        execution_records=execution_records,
        output_artifact_refs=output_artifact_refs,
        risk_flag_counts=risk_flag_counts,
        elapsed_ms=elapsed_ms,
        total_enriched=total_enriched,
        total_no_events=total_no_events,
        total_degraded=total_degraded,
        total_failed=total_failed,
    )
    summary_art_id = _write_event_context_summary_artifact(
        artifact_store, run_id, summary,
    )
    summary["summary_artifact_ref"] = summary_art_id

    # ── 8. Determine outcome ────────────────────────────────────
    if stage_status == "failed":
        if emit:
            emit(
                "event_context_failed",
                level="error",
                message=(
                    f"Event context failed: "
                    f"{total_failed}/{len(candidate_ids)} candidates failed"
                ),
                metadata={
                    "total_failed": total_failed,
                    "total_processed": total_processed,
                },
            )
        return {
            "outcome": "failed",
            "summary_counts": {
                "total_processed": total_processed,
                "total_enriched": total_enriched,
                "total_no_events": total_no_events,
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
                code="EVENT_CONTEXT_ALL_FAILED",
                message=f"All {total_failed} candidates failed event context",
                source=_STAGE_KEY,
            ),
        }

    # ── 9. Emit success / degraded ──────────────────────────────
    if emit:
        emit(
            "event_context_completed",
            message=(
                f"Event context completed: "
                f"{total_processed}/{len(candidate_ids)} processed"
                + (f" ({total_degraded} degraded)" if total_degraded else "")
                + (f" ({total_no_events} no events)" if total_no_events else "")
            ),
            metadata={
                "total_processed": total_processed,
                "total_enriched": total_enriched,
                "total_no_events": total_no_events,
                "total_degraded": total_degraded,
                "total_failed": total_failed,
                "risk_flag_counts": risk_flag_counts,
            },
        )

    return {
        "outcome": "completed",
        "summary_counts": {
            "total_processed": total_processed,
            "total_enriched": total_enriched,
            "total_no_events": total_no_events,
            "total_degraded": total_degraded,
            "total_failed": total_failed,
        },
        "artifacts": [],
        "metadata": {
            "stage_status": stage_status,
            "stage_summary": summary,
            "summary_artifact_id": summary_art_id,
            "output_artifact_refs": output_artifact_refs,
            "risk_flag_counts": risk_flag_counts,
            "elapsed_ms": elapsed_ms,
        },
        "error": None,
    }


# =====================================================================
#  Internal helpers
# =====================================================================

def _empty_summary_counts() -> dict[str, int]:
    """Return zeroed summary_counts dict."""
    return {
        "total_processed": 0,
        "total_enriched": 0,
        "total_no_events": 0,
        "total_degraded": 0,
        "total_failed": 0,
    }


def _vacuous_completion(
    artifact_store: dict[str, Any],
    run_id: str,
    emit: Callable[..., None] | None,
    elapsed_ms: int,
    note: str = "",
) -> dict[str, Any]:
    """Handle the vacuous-completion path (no candidates to process)."""
    if emit:
        emit(
            "event_context_completed",
            message=f"Event context vacuous completion: {note}",
            metadata={"total_processed": 0},
        )

    summary = _build_stage_summary(
        stage_status="no_candidates_to_process",
        total_candidates_in=0,
        execution_records=[],
        output_artifact_refs={},
        risk_flag_counts={},
        elapsed_ms=elapsed_ms,
    )
    summary_art_id = _write_event_context_summary_artifact(
        artifact_store, run_id, summary,
    )
    summary["summary_artifact_ref"] = summary_art_id

    return {
        "outcome": "completed",
        "summary_counts": _empty_summary_counts(),
        "artifacts": [],
        "metadata": {
            "stage_status": "no_candidates_to_process",
            "stage_summary": summary,
            "elapsed_ms": elapsed_ms,
        },
        "error": None,
    }
