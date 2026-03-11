"""
Shared Dashboard Metadata & Data-Quality Contract
===================================================

Standardizes how all Market Picture dashboards represent and communicate
data quality, coverage, freshness, proxy reliance, source failures,
and confidence impact.

Sits alongside (not replacing) existing ``data_quality`` dicts in service
payloads.  Services call ``build_dashboard_metadata(engine_key, ...)`` and
attach the result under a ``dashboard_metadata`` key in their response.

Design principles
-----------------
- Additive: existing ``data_quality`` / ``warnings`` / ``source_freshness``
  fields are preserved unchanged for backward compatibility.
- Honest: each field-level status distinguishes ok / proxy_only / stale /
  failed_source / missing_source_data / insufficient_history / partial /
  unimplemented / degraded / unknown.
- Stable vocabulary: status values are frozen for downstream consumers
  (Context Assembler, contradiction detection, policy layer).

Field-level status vocabulary
-----------------------------
ok                  – field populated from a direct, timely source
proxy_only          – field inferred from a substitute/proxy source
stale               – field available but data is older than expected
failed_source       – field unavailable because the source request failed
missing_source_data – field unavailable because the source returned no data
insufficient_history – field unavailable due to insufficient lookback
partial             – field partially populated (some sub-data missing)
unimplemented       – field not yet built / scaffolded for future
degraded            – field available but quality is compromised
unknown             – field status cannot be determined

Top-level metadata fields
-------------------------
data_quality_status   – "good" | "acceptable" | "degraded" | "poor" | "unavailable"
coverage_level        – "full" | "high" | "partial" | "minimal" | "none"
freshness_status      – "live" | "recent" | "stale" | "very_stale" | "unknown"
proxy_reliance_level  – "none" | "low" | "moderate" | "high" | "critical"
confidence_impact     – summary of how data quality affects confidence
missing_fields        – fields with no data at all
stale_fields          – fields with stale data
proxy_fields          – fields using proxy/inferred data
failed_sources        – sources that failed to respond
insufficient_history_fields – fields lacking enough history
unimplemented_fields  – fields not yet built
partial_fields        – fields with partial data
field_status_map      – {field_name: status_string} for every tracked field
source_status         – [{source, status, last_fetched, error, ...}]
warnings              – list[str] from engine
notes                 – list[str] additional context
last_successful_update – ISO timestamp of last good data
evaluation_metadata   – {evaluated_at, engine_key, engine_version, ...}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.utils.time_horizon import resolve_engine_horizon

logger = logging.getLogger("bentrade.dashboard_metadata")


# ── Frozen vocabularies ───────────────────────────────────────────────────

FIELD_STATUS_VALUES = frozenset({
    "ok",
    "proxy_only",
    "stale",
    "failed_source",
    "missing_source_data",
    "insufficient_history",
    "partial",
    "unimplemented",
    "degraded",
    "unknown",
})

DATA_QUALITY_STATUSES = frozenset({
    "good", "acceptable", "degraded", "poor", "unavailable",
})

COVERAGE_LEVELS = frozenset({
    "full", "high", "partial", "minimal", "none",
})

FRESHNESS_STATUSES = frozenset({
    "live", "recent", "stale", "very_stale", "unknown",
})

PROXY_RELIANCE_LEVELS = frozenset({
    "none", "low", "moderate", "high", "critical",
})


# ── Engine metadata for dashboard_metadata ────────────────────────────────

ENGINE_DASHBOARD_META: dict[str, dict[str, str]] = {
    "breadth_participation": {
        "display_name": "Breadth & Participation",
        "engine_version": "1.0",
    },
    "volatility_options": {
        "display_name": "Volatility & Options",
        "engine_version": "1.0",
    },
    "cross_asset_macro": {
        "display_name": "Cross-Asset Macro",
        "engine_version": "1.0",
    },
    "flows_positioning": {
        "display_name": "Flows & Positioning",
        "engine_version": "1.0",
    },
    "liquidity_financial_conditions": {
        "display_name": "Liquidity & Financial Conditions",
        "engine_version": "1.0",
    },
    "news_sentiment": {
        "display_name": "News & Sentiment",
        "engine_version": "1.0",
    },
}


# ── Public API ────────────────────────────────────────────────────────────


def build_dashboard_metadata(
    engine_key: str,
    *,
    engine_result: dict[str, Any] | None = None,
    source_errors: dict[str, str] | None = None,
    source_freshness: list[dict[str, Any]] | None = None,
    proxy_summary: dict[str, Any] | None = None,
    signal_provenance: dict[str, Any] | None = None,
    raw_data_meta: dict[str, Any] | None = None,
    compute_duration_s: float | None = None,
    is_error_payload: bool = False,
    error_stage: str | None = None,
) -> dict[str, Any]:
    """Build the shared dashboard metadata / data-quality structure.

    Args:
        engine_key: Stable engine identifier (e.g. ``"breadth_participation"``).
        engine_result: The engine's output dict (contains warnings, missing_inputs,
            confidence_score, signal_quality, diagnostics, etc.).
        source_errors: ``{source_name: error_message}`` from data provider.
        source_freshness: List of ``{source, status, last_fetched, ...}`` dicts
            (used by news_sentiment).
        proxy_summary: ``{total_proxy_signals, proxy_signal_names, ...}`` from
            engines with SIGNAL_PROVENANCE.
        signal_provenance: Full SIGNAL_PROVENANCE dict from engine diagnostics.
        raw_data_meta: Extra raw-data-level metadata (data_sources, metric_availability).
        compute_duration_s: Wall-clock compute time.
        is_error_payload: True if this is an error/fallback payload.
        error_stage: Failure stage label (e.g. ``"data_fetch"``).

    Returns:
        Shared dashboard metadata dict.
    """
    er = engine_result or {}
    diag = er.get("diagnostics", {})
    now = datetime.now(timezone.utc).isoformat()

    # ── Extract engine-level data quality signals ─────────────────────
    warnings = list(er.get("warnings", []))
    missing_inputs = list(er.get("missing_inputs", []))
    confidence_score = er.get("confidence_score", 0)
    signal_quality = er.get("signal_quality", "low")

    # ── Build field_status_map ────────────────────────────────────────
    field_status_map = _build_field_status_map(
        engine_key=engine_key,
        missing_inputs=missing_inputs,
        signal_provenance=signal_provenance or diag.get("signal_provenance"),
        proxy_summary=proxy_summary or diag.get("proxy_summary"),
        source_errors=source_errors,
        source_freshness=source_freshness,
        raw_data_meta=raw_data_meta,
        is_error_payload=is_error_payload,
    )

    # ── Classify fields by status ─────────────────────────────────────
    missing_fields = [f for f, s in field_status_map.items() if s in ("missing_source_data", "failed_source")]
    stale_fields = [f for f, s in field_status_map.items() if s == "stale"]
    proxy_fields = [f for f, s in field_status_map.items() if s == "proxy_only"]
    failed_sources = _collect_failed_sources(source_errors, source_freshness)
    insufficient_history_fields = [f for f, s in field_status_map.items() if s == "insufficient_history"]
    unimplemented_fields = [f for f, s in field_status_map.items() if s == "unimplemented"]
    partial_fields = [f for f, s in field_status_map.items() if s == "partial"]

    # ── Compute high-level labels ─────────────────────────────────────
    data_quality_status = _compute_data_quality_status(
        confidence_score=confidence_score,
        signal_quality=signal_quality,
        missing_count=len(missing_fields),
        proxy_count=len(proxy_fields),
        failed_source_count=len(failed_sources),
        is_error_payload=is_error_payload,
    )

    coverage_level = _compute_coverage_level(
        field_status_map=field_status_map,
        missing_count=len(missing_fields) + len(insufficient_history_fields),
        unimplemented_count=len(unimplemented_fields),
    )

    freshness_status = _compute_freshness_status(
        source_freshness=source_freshness,
        stale_count=len(stale_fields),
        compute_duration_s=compute_duration_s,
    )

    proxy_reliance_level = _compute_proxy_reliance(
        proxy_count=len(proxy_fields),
        total_fields=len(field_status_map) if field_status_map else 1,
        proxy_summary=proxy_summary or diag.get("proxy_summary"),
    )

    confidence_impact = _build_confidence_impact(
        confidence_score=confidence_score,
        signal_quality=signal_quality,
        proxy_reliance_level=proxy_reliance_level,
        missing_count=len(missing_fields),
        stale_count=len(stale_fields),
        failed_source_count=len(failed_sources),
    )

    # ── Build source_status ───────────────────────────────────────────
    source_status = _build_source_status(source_errors, source_freshness)

    # ── Notes ─────────────────────────────────────────────────────────
    notes: list[str] = []
    if is_error_payload:
        stage_label = error_stage or "unknown"
        notes.append(f"Engine failed at stage: {stage_label}")
    if proxy_reliance_level in ("high", "critical"):
        notes.append(f"Heavy proxy reliance ({len(proxy_fields)} proxy fields)")

    # ── Evaluation metadata ───────────────────────────────────────────
    engine_meta = ENGINE_DASHBOARD_META.get(engine_key, {})
    evaluation_metadata = {
        "evaluated_at": now,
        "engine_key": engine_key,
        "engine_version": engine_meta.get("engine_version", "unknown"),
        "compute_duration_s": compute_duration_s,
    }

    return {
        "data_quality_status": data_quality_status,
        "coverage_level": coverage_level,
        "freshness_status": freshness_status,
        "proxy_reliance_level": proxy_reliance_level,
        "time_horizon": resolve_engine_horizon(engine_key),
        "confidence_impact": confidence_impact,
        "missing_fields": missing_fields,
        "stale_fields": stale_fields,
        "proxy_fields": proxy_fields,
        "failed_sources": failed_sources,
        "insufficient_history_fields": insufficient_history_fields,
        "unimplemented_fields": unimplemented_fields,
        "partial_fields": partial_fields,
        "field_status_map": field_status_map,
        "source_status": source_status,
        "warnings": warnings,
        "notes": notes,
        "last_successful_update": er.get("as_of") if not is_error_payload else None,
        "evaluation_metadata": evaluation_metadata,
    }


# ── Field-status classification helpers ───────────────────────────────────


def classify_field_status(
    field_name: str,
    *,
    is_missing: bool = False,
    is_proxy: bool = False,
    is_stale: bool = False,
    is_failed_source: bool = False,
    is_insufficient_history: bool = False,
    is_unimplemented: bool = False,
    is_partial: bool = False,
) -> str:
    """Classify a single field into the standard status vocabulary.

    Priority order (first match wins):
      failed_source > missing_source_data > insufficient_history >
      unimplemented > stale > proxy_only > partial > degraded > ok
    """
    if is_failed_source:
        return "failed_source"
    if is_missing:
        return "missing_source_data"
    if is_insufficient_history:
        return "insufficient_history"
    if is_unimplemented:
        return "unimplemented"
    if is_stale:
        return "stale"
    if is_proxy:
        return "proxy_only"
    if is_partial:
        return "partial"
    return "ok"


def validate_field_status(status: str) -> bool:
    """Check whether a status string is in the allowed vocabulary."""
    return status in FIELD_STATUS_VALUES


# ── Private builders ──────────────────────────────────────────────────────


def _build_field_status_map(
    *,
    engine_key: str,
    missing_inputs: list[str],
    signal_provenance: dict[str, Any] | None,
    proxy_summary: dict[str, Any] | None,
    source_errors: dict[str, str] | None,
    source_freshness: list[dict[str, Any]] | None,
    raw_data_meta: dict[str, Any] | None,
    is_error_payload: bool,
) -> dict[str, str]:
    """Build a {field_name: status} map for all tracked fields.

    Uses signal_provenance (if available) to distinguish proxy vs. direct.
    Falls back to missing_inputs list for simpler engines.
    """
    fsm: dict[str, str] = {}
    missing_set = set(missing_inputs)

    # Failed source names (lowered for matching)
    failed_src_names: set[str] = set()
    if source_errors:
        failed_src_names = {k.lower() for k in source_errors}
    if source_freshness:
        for sf in source_freshness:
            if sf.get("status") in ("error", "unavailable"):
                failed_src_names.add(sf.get("source", "").lower())

    if is_error_payload:
        # All fields unknown in error state
        known_fields = _get_known_fields_for_engine(engine_key)
        for f in known_fields:
            fsm[f] = "failed_source"
        return fsm

    # ── From signal_provenance (richest source) ───────────────────
    if signal_provenance:
        for signal_name, prov in signal_provenance.items():
            if not isinstance(prov, dict):
                continue
            sig_type = prov.get("type", "unknown")
            sig_source = (prov.get("source") or "").lower()

            if signal_name in missing_set:
                if sig_source and sig_source in failed_src_names:
                    fsm[signal_name] = "failed_source"
                else:
                    fsm[signal_name] = "missing_source_data"
            elif sig_type == "proxy":
                fsm[signal_name] = "proxy_only"
            elif sig_type == "derived":
                fsm[signal_name] = "ok"  # derived from direct sources is ok
            else:
                # Check staleness hints
                stale_warning = prov.get("stale_warning") or prov.get("days_stale")
                if stale_warning:
                    fsm[signal_name] = "stale"
                else:
                    fsm[signal_name] = "ok"

    # ── From proxy_summary (if provenance not available) ──────────
    elif proxy_summary:
        proxy_names = set(proxy_summary.get("proxy_signal_names", []))
        direct_names = set(proxy_summary.get("direct_signal_names", []))
        for name in proxy_names:
            if name in missing_set:
                fsm[name] = "missing_source_data"
            else:
                fsm[name] = "proxy_only"
        for name in direct_names:
            if name in missing_set:
                fsm[name] = "missing_source_data"
            else:
                fsm[name] = "ok"

    # ── From missing_inputs alone (simplest engines) ──────────────
    for mi in missing_inputs:
        if mi not in fsm:
            fsm[mi] = "missing_source_data"

    # ── News-specific: map source failures to dependent component fields ─
    if engine_key == "news_sentiment" and source_freshness:
        _apply_news_source_field_status(fsm, source_freshness)

    # ── Fill known fields as "ok" if not already classified ───────
    known_fields = _get_known_fields_for_engine(engine_key)
    for f in known_fields:
        if f not in fsm:
            fsm[f] = "ok"

    return fsm


# News component → upstream source dependency mapping.
# headline_sentiment, negative_pressure, narrative_severity, source_agreement,
# recency_pressure all require news items from finnhub/polygon.
# macro_stress requires FRED macro data.
_NEWS_FIELD_SOURCE_DEPS: dict[str, list[str]] = {
    "headline_sentiment": ["finnhub", "polygon"],
    "negative_pressure": ["finnhub", "polygon"],
    "narrative_severity": ["finnhub", "polygon"],
    "source_agreement": ["finnhub", "polygon"],
    "recency_pressure": ["finnhub", "polygon"],
    "macro_stress": ["fred"],
}


def _apply_news_source_field_status(
    fsm: dict[str, str],
    source_freshness: list[dict[str, Any]],
) -> None:
    """Mark news component fields as degraded when their upstream sources fail.

    A field is marked ``degraded`` when ALL of its required sources have
    failed (error/unavailable).  If only some sources fail, the component
    still computes from remaining data — leave status to be filled as "ok".
    """
    failed_sources: set[str] = set()
    for sf in source_freshness:
        if sf.get("status") in ("error", "unavailable"):
            failed_sources.add(sf.get("source", "").lower())

    for field, deps in _NEWS_FIELD_SOURCE_DEPS.items():
        if field in fsm:
            continue  # already classified by provenance / missing_inputs
        if all(dep in failed_sources for dep in deps):
            fsm[field] = "degraded"


def _get_known_fields_for_engine(engine_key: str) -> list[str]:
    """Return the canonical list of tracked fields for an engine.

    This is the stable set of field names that each engine is expected
    to populate, used to fill field_status_map with "ok" defaults.
    """
    # Common fields across pillar engines
    common = ["score", "label", "confidence_score", "signal_quality", "summary"]

    engine_specific: dict[str, list[str]] = {
        "breadth_participation": common + [
            "participation", "trend", "volume", "leadership", "stability",
        ],
        "volatility_options": common + [
            "regime", "structure", "skew", "positioning",
        ],
        "cross_asset_macro": common + [
            "rates", "dollar_commodity", "credit", "defensive_growth", "coherence",
        ],
        "flows_positioning": common + [
            "positioning", "crowding", "squeeze", "flow", "stability",
        ],
        "liquidity_financial_conditions": common + [
            "rates", "conditions", "credit", "dollar", "stability",
        ],
        "news_sentiment": common + [
            # Maps to component keys from compute_engine_scores()
            "headline_sentiment", "negative_pressure", "narrative_severity",
            "source_agreement", "macro_stress", "recency_pressure",
        ],
    }
    return engine_specific.get(engine_key, common)


def _collect_failed_sources(
    source_errors: dict[str, str] | None,
    source_freshness: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Collect failed sources from both error dicts and freshness lists."""
    failed: list[dict[str, str]] = []
    seen: set[str] = set()

    if source_errors:
        for src, err in source_errors.items():
            if src not in seen:
                failed.append({"source": src, "error": str(err)[:200]})
                seen.add(src)

    if source_freshness:
        for sf in source_freshness:
            src = sf.get("source", "unknown")
            if sf.get("status") in ("error", "unavailable") and src not in seen:
                failed.append({
                    "source": src,
                    "error": sf.get("error") or f"status={sf.get('status')}",
                })
                seen.add(src)

    return failed


def _build_source_status(
    source_errors: dict[str, str] | None,
    source_freshness: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Build normalized source_status list."""
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    if source_freshness:
        for sf in source_freshness:
            src = sf.get("source", "unknown")
            sources.append({
                "source": src,
                "status": sf.get("status", "unknown"),
                "last_fetched": sf.get("last_fetched"),
                "item_count": sf.get("item_count"),
                "error": sf.get("error"),
            })
            seen.add(src)

    if source_errors:
        for src, err in source_errors.items():
            if src not in seen:
                sources.append({
                    "source": src,
                    "status": "error",
                    "last_fetched": None,
                    "item_count": None,
                    "error": str(err)[:200],
                })
                seen.add(src)

    return sources


# ── High-level label computation ──────────────────────────────────────────


def _compute_data_quality_status(
    *,
    confidence_score: float,
    signal_quality: str,
    missing_count: int,
    proxy_count: int,
    failed_source_count: int,
    is_error_payload: bool,
) -> str:
    """Compute the top-level data_quality_status label.

    Formula:
      - unavailable: error payload or confidence == 0
      - poor: confidence < 40 OR ≥3 failed sources OR signal_quality == "low" and missing > 3
      - degraded: confidence < 60 OR ≥2 failed sources OR proxy > 3
      - acceptable: confidence < 80
      - good: confidence ≥ 80
    """
    if is_error_payload or confidence_score == 0:
        return "unavailable"
    if confidence_score < 40 or failed_source_count >= 3 or (signal_quality == "low" and missing_count > 3):
        return "poor"
    if confidence_score < 60 or failed_source_count >= 2 or proxy_count > 3:
        return "degraded"
    if confidence_score < 80:
        return "acceptable"
    return "good"


def _compute_coverage_level(
    *,
    field_status_map: dict[str, str],
    missing_count: int,
    unimplemented_count: int,
) -> str:
    """Compute the coverage_level label.

    Formula:
      total tracked = len(field_status_map)
      unavailable = missing + unimplemented
      ratio = (total - unavailable) / total
      - none: ratio == 0 or total == 0
      - minimal: ratio < 0.5
      - partial: ratio < 0.8
      - high: ratio < 1.0
      - full: ratio == 1.0
    """
    total = len(field_status_map)
    if total == 0:
        return "none"
    unavailable = missing_count + unimplemented_count
    ratio = (total - unavailable) / total
    if ratio <= 0:
        return "none"
    if ratio < 0.5:
        return "minimal"
    if ratio < 0.8:
        return "partial"
    if ratio < 1.0:
        return "high"
    return "full"


def _compute_freshness_status(
    *,
    source_freshness: list[dict[str, Any]] | None,
    stale_count: int,
    compute_duration_s: float | None,
) -> str:
    """Compute freshness_status label.

    Formula:
      - unknown: no freshness info available
      - very_stale: stale_count > 2
      - stale: stale_count > 0
      - recent: compute_duration_s is known (data was just computed)
      - live: no stale fields and data was just computed
    """
    if stale_count > 2:
        return "very_stale"
    if stale_count > 0:
        return "stale"
    if compute_duration_s is not None:
        return "live"
    if source_freshness:
        return "recent"
    return "unknown"


def _compute_proxy_reliance(
    *,
    proxy_count: int,
    total_fields: int,
    proxy_summary: dict[str, Any] | None,
) -> str:
    """Compute proxy_reliance_level label.

    Formula:
      ratio = proxy_count / total_fields
      - none: ratio == 0
      - low: ratio < 0.2
      - moderate: ratio < 0.4
      - high: ratio < 0.7
      - critical: ratio >= 0.7
    """
    if proxy_count == 0:
        return "none"
    ratio = proxy_count / max(total_fields, 1)
    if ratio < 0.2:
        return "low"
    if ratio < 0.4:
        return "moderate"
    if ratio < 0.7:
        return "high"
    return "critical"


def _build_confidence_impact(
    *,
    confidence_score: float,
    signal_quality: str,
    proxy_reliance_level: str,
    missing_count: int,
    stale_count: int,
    failed_source_count: int,
) -> dict[str, Any]:
    """Build confidence_impact summary.

    Provides a structured summary of how data quality factors affect
    the engine's confidence score.
    """
    factors: list[str] = []
    if missing_count > 0:
        factors.append(f"{missing_count} missing field(s)")
    if stale_count > 0:
        factors.append(f"{stale_count} stale field(s)")
    if failed_source_count > 0:
        factors.append(f"{failed_source_count} failed source(s)")
    if proxy_reliance_level in ("high", "critical"):
        factors.append(f"proxy reliance: {proxy_reliance_level}")

    return {
        "confidence_score": confidence_score,
        "signal_quality": signal_quality,
        "degradation_factors": factors,
        "proxy_reliance_level": proxy_reliance_level,
        "is_actionable": confidence_score >= 40 and signal_quality != "low",
    }
