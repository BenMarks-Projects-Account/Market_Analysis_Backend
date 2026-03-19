"""
Normalized Market Engine Output Contract
=========================================

Defines the canonical output shape that ALL 6 Market Picture engines
must produce for downstream consumers (Context Assembler, contradiction
detection, composite world-state summaries, portfolio-aware decisioning,
higher-order trade-decision prompts).

The contract sits alongside (not replacing) the existing ``engine_result``
dict so frontends keep working unchanged.  Services call
``normalize_engine_output(engine_key, service_payload)`` and attach the
result under the ``normalized`` key of their response.

Contract fields
---------------
engine_key            – stable machine identifier
engine_name           – human-readable display name
as_of                 – ISO 8601 timestamp
score                 – 0-100 composite (None when unavailable)
label / short_label   – full / abbreviated regime labels
confidence            – 0-100 independent confidence
signal_quality        – "high" | "medium" | "low"
time_horizon          – from shared vocabulary (see app.utils.time_horizon)
freshness             – compute duration, cache info, source-level freshness
summary               – 1-2 sentence overview
trader_takeaway       – actionable guidance
bull_factors          – positive / confirming signals
bear_factors          – negative / contradicting signals
risks                 – identified risks (warnings + missing inputs)
regime_tags           – machine-readable tags derived from label
supporting_metrics    – key submetrics [{name, value, score, pillar}]
contradiction_flags   – cross-pillar / cross-signal conflicts
data_quality          – normalized quality summary
warnings              – all warnings
source_status         – per-source availability / proxy counts
pillar_scores         – ordered [{name, score, weight, explanation}]
detail_sections       – engine-specific extras (strategy_scores, items, etc.)

Second-pass additions (v1.1)
----------------------------
engine_status         – "ok" | "degraded" | "error" | "no_data"
status_detail         – {normalization_source, is_fallback, is_legacy,
                         degraded_reasons, staleness_warning}
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.utils.time_horizon import resolve_engine_horizon

# ── Contract constants ───────────────────────────────────────────────────

REQUIRED_FIELDS: frozenset[str] = frozenset({
    "engine_key", "engine_name", "as_of",
    "score", "label", "short_label",
    "confidence", "signal_quality", "time_horizon",
    "freshness", "summary", "trader_takeaway",
    "bull_factors", "bear_factors", "risks",
    "regime_tags", "supporting_metrics", "contradiction_flags",
    "data_quality", "warnings", "source_status",
    "pillar_scores", "detail_sections",
    "engine_status", "status_detail",
})

VALID_ENGINE_STATUSES: frozenset[str] = frozenset({
    "ok", "degraded", "error", "no_data",
})

# ── Engine metadata ──────────────────────────────────────────────────────

ENGINE_METADATA: dict[str, dict[str, str]] = {
    "breadth_participation": {
        "name": "Breadth & Participation",
        "time_horizon": "short_term",
    },
    "volatility_options": {
        "name": "Volatility & Options",
        "time_horizon": "short_term",
    },
    "cross_asset_macro": {
        "name": "Cross-Asset Macro",
        "time_horizon": "short_term",
    },
    "flows_positioning": {
        "name": "Flows & Positioning",
        "time_horizon": "short_term",
    },
    "liquidity_financial_conditions": {
        "name": "Liquidity & Financial Conditions",
        "time_horizon": "medium_term",
    },
    "news_sentiment": {
        "name": "News & Sentiment",
        "time_horizon": "intraday",
    },
}


# ── Public API ───────────────────────────────────────────────────────────

def normalize_engine_output(
    engine_key: str,
    service_payload: dict[str, Any],
) -> dict[str, Any]:
    """Convert a raw service payload into the normalized contract shape.

    Parameters
    ----------
    engine_key:
        One of the keys in ENGINE_METADATA (e.g. ``"breadth_participation"``).
    service_payload:
        The full dict returned by the service's ``get_*()`` method, containing
        ``engine_result`` (or ``internal_engine`` for news) plus service-level
        wrapper fields (``data_quality``, ``compute_duration_s``, ``as_of``,
        ``cache_info``, etc.).

    Returns
    -------
    dict  The normalized contract dict.
    """
    if engine_key == "news_sentiment":
        return _normalize_news(service_payload)
    return _normalize_pillar_engine(engine_key, service_payload)


# ── Pillar-based engines (breadth, vol, cross-asset, flows, liquidity) ───

def _normalize_pillar_engine(
    engine_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    raw_er = payload.get("engine_result")
    er = raw_er if isinstance(raw_er, dict) else {}
    raw_dq = payload.get("data_quality")
    dq = raw_dq if isinstance(raw_dq, dict) else {}
    cache = payload.get("cache_info")
    meta = ENGINE_METADATA.get(engine_key, {})

    score = er.get("score")
    label = er.get("label", "Unknown")
    short_label = er.get("short_label", "Unknown")

    pillar_scores = _extract_pillar_scores(er)
    supporting_metrics = _extract_supporting_metrics(er, max_items=10)

    # Bull / bear / contradiction mapping varies by engine
    bull, bear, contradictions = _extract_drivers(engine_key, er)

    # Risks = warnings + missing inputs deduplicated
    warnings_list = list(er.get("warnings") or [])
    missing = list(er.get("missing_inputs") or [])
    risks = _build_risks(warnings_list, missing)

    # Source status
    source_status = _build_source_status(engine_key, er, dq)

    # Detail sections (engine-specific extras)
    detail_sections = _extract_detail_sections(engine_key, er)

    # Engine status / status_detail
    sig_qual = er.get("signal_quality", "low")
    engine_status, degraded_reasons = _derive_engine_status(
        score=score,
        signal_quality=sig_qual,
        missing_count=len(missing),
        warning_count=len(warnings_list),
        as_of=payload.get("as_of") or er.get("as_of"),
    )

    return {
        "engine_key": engine_key,
        "engine_name": meta.get("name", engine_key),
        "as_of": payload.get("as_of") or er.get("as_of"),
        "score": score,
        "label": label,
        "short_label": short_label,
        "confidence": er.get("confidence_score", 0),
        "signal_quality": sig_qual,
        "time_horizon": resolve_engine_horizon(engine_key),
        "freshness": _build_freshness(payload, cache),
        "summary": er.get("summary", ""),
        "trader_takeaway": er.get("trader_takeaway", ""),
        "bull_factors": bull,
        "bear_factors": bear,
        "risks": risks,
        "regime_tags": _derive_regime_tags(label),
        "supporting_metrics": supporting_metrics,
        "contradiction_flags": contradictions,
        "data_quality": {
            "confidence_score": dq.get("confidence_score", er.get("confidence_score", 0)),
            "signal_quality": dq.get("signal_quality", er.get("signal_quality", "low")),
            "missing_inputs_count": dq.get("missing_inputs_count", len(missing)),
            "warning_count": dq.get("warning_count", len(warnings_list)),
            "coverage_pct": dq.get("universe_coverage_pct"),
        },
        "warnings": warnings_list,
        "source_status": source_status,
        "pillar_scores": pillar_scores,
        "detail_sections": detail_sections,
        "engine_status": engine_status,
        "status_detail": _build_status_detail(
            normalization_source="engine",
            degraded_reasons=degraded_reasons,
        ),
    }


# ── News & Sentiment (structurally different) ───────────────────────────

def _normalize_news(payload: dict[str, Any]) -> dict[str, Any]:
    # News uses "internal_engine" instead of "engine_result"
    raw_er = payload.get("internal_engine") or payload.get("engine_result")
    er = raw_er if isinstance(raw_er, dict) else {}
    meta = ENGINE_METADATA["news_sentiment"]

    score = er.get("score")
    # News uses "regime_label" instead of "label"
    label = er.get("regime_label", "Unknown")
    short_label = label  # News has no separate short_label

    # Components → pillar_scores mapping
    components = er.get("components") or {}
    weights = er.get("weights") or {}
    pillar_scores = []
    for comp_name, comp_data in components.items():
        comp_score = comp_data.get("score") if isinstance(comp_data, dict) else comp_data
        pillar_scores.append({
            "name": comp_name,
            "score": comp_score,
            "weight": weights.get(comp_name, 0),
            "explanation": (
                comp_data.get("signal", "")
                if isinstance(comp_data, dict)
                else ""
            ),
        })

    # Explanation block (may be nested or top-level)
    explanation = er.get("explanation") or {}
    summary = (
        explanation.get("summary", "")
        if isinstance(explanation, dict)
        else str(explanation)
    )
    trader_takeaway = (
        explanation.get("trader_takeaway", "")
        if isinstance(explanation, dict)
        else ""
    )

    # Source freshness → source_status
    source_freshness = payload.get("source_freshness") or []
    source_errors: dict[str, str] = {}
    for sf in source_freshness:
        if sf.get("error"):
            source_errors[sf.get("source", "unknown")] = sf["error"]

    # Items & macro_context go into detail_sections
    detail_sections: dict[str, Any] = {}
    if payload.get("items") is not None:
        detail_sections["items"] = payload["items"]
    if payload.get("macro_context") is not None:
        detail_sections["macro_context"] = payload["macro_context"]
    if source_freshness:
        detail_sections["source_freshness"] = source_freshness

    # Build bull/bear from explanation components
    bull: list[str] = []
    bear: list[str] = []
    for comp_name, comp_data in components.items():
        if not isinstance(comp_data, dict):
            continue
        comp_score = comp_data.get("score")
        if comp_score is not None and comp_score >= 60:
            signal = comp_data.get("signal", comp_name)
            bull.append(f"{comp_name}: {signal}" if signal else comp_name)
        elif comp_score is not None and comp_score < 40:
            signal = comp_data.get("signal", comp_name)
            bear.append(f"{comp_name}: {signal}" if signal else comp_name)

    item_count = payload.get("item_count", 0)
    warnings_list: list[str] = []
    if item_count == 0:
        warnings_list.append("No news items available")

    news_sig_qual = (
        explanation.get("signal_quality", "low")
        if isinstance(explanation, dict)
        else "low"
    )

    engine_status, degraded_reasons = _derive_engine_status(
        score=score,
        signal_quality=news_sig_qual,
        missing_count=len(source_errors),
        warning_count=len(warnings_list),
        as_of=payload.get("as_of") or er.get("as_of"),
    )

    return {
        "engine_key": "news_sentiment",
        "engine_name": meta["name"],
        "as_of": payload.get("as_of") or er.get("as_of"),
        "score": score,
        "label": label,
        "short_label": short_label,
        "confidence": er.get("confidence_score", 0),
        "signal_quality": news_sig_qual,
        "time_horizon": resolve_engine_horizon("news_sentiment"),
        "freshness": {
            "compute_duration_s": payload.get("compute_duration_s"),
            "cache_hit": None,
            "sources": source_freshness or None,
        },
        "summary": summary,
        "trader_takeaway": trader_takeaway,
        "bull_factors": bull,
        "bear_factors": bear,
        "risks": warnings_list,
        "regime_tags": _derive_regime_tags(label),
        "supporting_metrics": _extract_news_metrics(components),
        "contradiction_flags": [],
        "data_quality": {
            "confidence_score": er.get("confidence_score", 0),
            "signal_quality": (
                explanation.get("signal_quality", "low")
                if isinstance(explanation, dict)
                else "low"
            ),
            "missing_inputs_count": len(source_errors),
            "warning_count": len(warnings_list),
            "coverage_pct": None,
        },
        "warnings": warnings_list,
        "source_status": {
            "errors": source_errors,
            "proxy_count": 0,
            "direct_count": len(source_freshness) - len(source_errors),
        },
        "pillar_scores": pillar_scores,
        "detail_sections": detail_sections,
        "engine_status": engine_status,
        "status_detail": _build_status_detail(
            normalization_source="engine",
            degraded_reasons=degraded_reasons,
        ),
    }


# ── Private helpers ──────────────────────────────────────────────────────

def _extract_pillar_scores(er: dict[str, Any]) -> list[dict[str, Any]]:
    """Build ordered pillar_scores list from engine_result."""
    raw_scores = er.get("pillar_scores") or {}
    weights = er.get("pillar_weights") or {}
    explanations = er.get("pillar_explanations") or {}

    pillars = []
    for name in raw_scores:
        pillars.append({
            "name": name,
            "score": raw_scores[name],
            "weight": weights.get(name, 0),
            "explanation": explanations.get(name, ""),
        })
    # Sort descending by weight so most important pillars come first
    pillars.sort(key=lambda p: p.get("weight", 0), reverse=True)
    return pillars


def _extract_supporting_metrics(
    er: dict[str, Any],
    max_items: int = 10,
) -> list[dict[str, Any]]:
    """Extract top submetrics from diagnostics as supporting_metrics."""
    diag = er.get("diagnostics") or {}
    pillar_details = diag.get("pillar_details") or {}

    metrics: list[dict[str, Any]] = []
    for pillar_name, pillar_data in pillar_details.items():
        if not isinstance(pillar_data, dict):
            continue
        for sm in pillar_data.get("submetrics") or []:
            if not isinstance(sm, dict):
                continue
            raw_val = sm.get("raw_value")
            sm_score = sm.get("score")
            if raw_val is None and sm_score is None:
                continue
            metrics.append({
                "name": sm.get("name", "unknown"),
                "value": raw_val,
                "score": sm_score,
                "pillar": pillar_name,
            })
    # Return up to max_items, sorted by absolute distance from 50
    # (most decisive metrics first)
    metrics.sort(
        key=lambda m: abs((m.get("score") or 50) - 50),
        reverse=True,
    )
    return metrics[:max_items]


def _extract_news_metrics(
    components: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract supporting_metrics from news components."""
    metrics = []
    for name, data in components.items():
        if isinstance(data, dict):
            metrics.append({
                "name": name,
                "value": data.get("score"),
                "score": data.get("score"),
                "pillar": "news",
            })
        elif isinstance(data, (int, float)):
            metrics.append({
                "name": name,
                "value": data,
                "score": data,
                "pillar": "news",
            })
    return metrics


def _extract_drivers(
    engine_key: str,
    er: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """Return (bull_factors, bear_factors, contradiction_flags).

    Cross-Asset uses confirming/contradicting/mixed instead of
    positive/negative/conflicting.
    """
    if engine_key == "cross_asset_macro":
        bull = list(er.get("confirming_signals") or [])
        bear = list(er.get("contradicting_signals") or [])
        contradictions = list(er.get("mixed_signals") or [])
    else:
        bull = list(er.get("positive_contributors") or [])
        bear = list(er.get("negative_contributors") or [])
        contradictions = list(er.get("conflicting_signals") or [])
    return bull, bear, contradictions


def _build_risks(
    warnings: list[str],
    missing: list[str],
) -> list[str]:
    """Combine warnings + missing inputs into a deduplicated risk list."""
    seen: set[str] = set()
    risks: list[str] = []
    for w in warnings:
        if w and w not in seen:
            risks.append(w)
            seen.add(w)
    for m in missing:
        entry = f"Missing input: {m}"
        if entry not in seen:
            risks.append(entry)
            seen.add(entry)
    return risks


def _build_freshness(
    payload: dict[str, Any],
    cache: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build freshness dict from compute_duration_s + cache_info."""
    return {
        "compute_duration_s": payload.get("compute_duration_s"),
        "cache_hit": cache.get("cache_hit") if cache else None,
        "sources": None,
    }


def _build_source_status(
    engine_key: str,
    er: dict[str, Any],
    dq: dict[str, Any],
) -> dict[str, Any]:
    """Build source_status from data_quality + diagnostics provenance."""
    errors = dict(dq.get("source_errors") or {})

    # Count proxy vs direct from signal_provenance if available
    diag = er.get("diagnostics") or {}
    provenance = diag.get("signal_provenance") or {}
    proxy_count = 0
    direct_count = 0
    for _sig, info in provenance.items():
        if isinstance(info, dict):
            sig_type = info.get("type", "")
            if sig_type == "proxy":
                proxy_count += 1
            else:
                direct_count += 1

    return {
        "errors": errors,
        "proxy_count": proxy_count,
        "direct_count": direct_count,
    }


_REGIME_TAG_CLEANUP = re.compile(r"[^a-z0-9_]+")


def _derive_regime_tags(label: str) -> list[str]:
    """Derive machine-readable regime tags from a human-readable label.

    Example: "Premium Selling Strongly Favored" →
             ["premium_selling_strongly_favored"]
    """
    if not label or label == "Unknown":
        return []
    cleaned = _REGIME_TAG_CLEANUP.sub("_", label.lower()).strip("_")
    return [cleaned] if cleaned else []


def _extract_detail_sections(
    engine_key: str,
    er: dict[str, Any],
) -> dict[str, Any]:
    """Pull engine-specific extras into detail_sections.

    Only includes keys that are unique to a particular engine
    (not shared canonical fields).
    """
    sections: dict[str, Any] = {}

    if engine_key == "volatility_options":
        if er.get("strategy_scores"):
            sections["strategy_scores"] = er["strategy_scores"]

    elif engine_key == "flows_positioning":
        if er.get("strategy_bias"):
            sections["strategy_bias"] = er["strategy_bias"]

    elif engine_key == "liquidity_financial_conditions":
        if er.get("support_vs_stress"):
            sections["support_vs_stress"] = er["support_vs_stress"]

    elif engine_key == "cross_asset_macro":
        diag = er.get("diagnostics") or {}
        if diag.get("signal_provenance"):
            sections["signal_provenance"] = diag["signal_provenance"]

    elif engine_key == "breadth_participation":
        if er.get("universe"):
            sections["universe"] = er["universe"]

    return sections


# ── Status helpers ───────────────────────────────────────────────────────

def _derive_engine_status(
    *,
    score: Any,
    signal_quality: str,
    missing_count: int,
    warning_count: int,
    as_of: str | None,
) -> tuple[str, list[str]]:
    """Determine engine_status from data-quality indicators.

    Returns (status, degraded_reasons).

    Status rules:
      - no_data:   score is None and no signal_quality
      - error:     score is None (engine likely failed)
      - degraded:  score present but data quality is materially compromised
      - ok:        score present, quality acceptable

    Degraded thresholds scale with signal_quality:
      high:   missing > 3  or warnings > 6
      medium: missing > 2  or warnings > 4
      low:    always degraded (low signal quality itself is a reason)

    Staleness always degrades regardless of signal_quality.
    """
    reasons: list[str] = []

    if score is None:
        if signal_quality == "low" and missing_count == 0 and warning_count == 0:
            return "no_data", ["no_score_returned"]
        reasons.append("no_score_returned")
        if missing_count > 0:
            reasons.append(f"missing_inputs:{missing_count}")
        if warning_count > 0:
            reasons.append(f"warnings:{warning_count}")
        return "error", reasons

    # Score present — check for degraded conditions.
    # Thresholds are signal-quality-aware: a high-quality engine with a
    # few proxy warnings is operating normally, not degraded.
    if signal_quality == "low":
        reasons.append("low_signal_quality")
        # Also enumerate specifics for traceability
        if missing_count > 0:
            reasons.append(f"missing_inputs:{missing_count}")
        if warning_count > 0:
            reasons.append(f"elevated_warnings:{warning_count}")
    elif signal_quality == "medium":
        if missing_count > 2:
            reasons.append(f"missing_inputs:{missing_count}")
        if warning_count > 4:
            reasons.append(f"elevated_warnings:{warning_count}")
    else:
        # high (or unknown treated as high)
        if missing_count > 3:
            reasons.append(f"missing_inputs:{missing_count}")
        if warning_count > 6:
            reasons.append(f"elevated_warnings:{warning_count}")

    # Staleness check — always applies
    if as_of:
        staleness = _check_staleness(as_of)
        if staleness:
            reasons.append(staleness)

    if reasons:
        return "degraded", reasons
    return "ok", []


def _check_staleness(as_of: str) -> str | None:
    """Return a staleness reason if as_of is >1 hour old, else None."""
    try:
        ts = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - ts
        hours = age.total_seconds() / 3600
        if hours > 1.0:
            return f"stale_data:{hours:.1f}h"
    except (ValueError, TypeError):
        pass
    return None


def _build_status_detail(
    *,
    normalization_source: str = "engine",
    is_fallback: bool = False,
    is_legacy: bool = False,
    degraded_reasons: list[str] | None = None,
    staleness_warning: str | None = None,
) -> dict[str, Any]:
    """Build the status_detail metadata dict."""
    return {
        "normalization_source": normalization_source,
        "is_fallback": is_fallback,
        "is_legacy": is_legacy,
        "degraded_reasons": degraded_reasons or [],
        "staleness_warning": staleness_warning,
    }


# ── Public helpers: error / degraded / legacy / validation ──────────────


def build_error_output(
    engine_key: str,
    error_message: str,
    *,
    exception_type: str | None = None,
) -> dict[str, Any]:
    """Build a contract-shaped error payload when an engine fails completely.

    Downstream consumers can process this through the same contract path
    without special error branching.
    """
    meta = ENGINE_METADATA.get(engine_key, {})
    now = datetime.now(timezone.utc).isoformat()

    return {
        "engine_key": engine_key,
        "engine_name": meta.get("name", engine_key),
        "as_of": now,
        "score": None,
        "label": "Error",
        "short_label": "Error",
        "confidence": 0,
        "signal_quality": "low",
        "time_horizon": resolve_engine_horizon(engine_key),
        "freshness": {"compute_duration_s": None, "cache_hit": None, "sources": None},
        "summary": f"Engine failed: {error_message}",
        "trader_takeaway": "",
        "bull_factors": [],
        "bear_factors": [],
        "risks": [f"Engine error: {error_message}"],
        "regime_tags": [],
        "supporting_metrics": [],
        "contradiction_flags": [],
        "data_quality": {
            "confidence_score": 0,
            "signal_quality": "low",
            "missing_inputs_count": 0,
            "warning_count": 1,
            "coverage_pct": None,
        },
        "warnings": [f"Engine error: {error_message}"],
        "source_status": {"errors": {"engine": error_message}, "proxy_count": 0, "direct_count": 0},
        "pillar_scores": [],
        "detail_sections": {},
        "engine_status": "error",
        "status_detail": _build_status_detail(
            normalization_source="error_handler",
            degraded_reasons=[
                "engine_failure",
                *(["exception:" + exception_type] if exception_type else []),
            ],
        ),
    }


def build_degraded_output(
    engine_key: str,
    partial_payload: dict[str, Any],
    *,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Build a contract-shaped degraded payload from partial engine data.

    Uses normalize_engine_output() for whatever is available, then
    overrides the status fields to reflect degradation.
    """
    normalized = normalize_engine_output(engine_key, partial_payload)
    extra_reasons = reasons or []

    # Merge degraded_reasons
    existing_reasons = normalized.get("status_detail", {}).get("degraded_reasons", [])
    all_reasons = existing_reasons + extra_reasons

    # Force degraded status if not already error
    if normalized.get("engine_status") != "error":
        normalized["engine_status"] = "degraded"

    normalized["status_detail"] = _build_status_detail(
        normalization_source="engine",
        degraded_reasons=all_reasons,
    )
    return normalized


def detect_legacy_payload(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    """Detect whether a cached payload is a legacy format lacking normalized fields.

    Returns (is_legacy, reasons).
    """
    if not isinstance(payload, dict):
        return True, ["not_a_dict"]

    reasons: list[str] = []

    if "normalized" not in payload:
        reasons.append("missing_normalized_key")

    normalized = payload.get("normalized")
    if isinstance(normalized, dict):
        # Check for v1.1 fields
        if "engine_status" not in normalized:
            reasons.append("missing_engine_status")
        if "status_detail" not in normalized:
            reasons.append("missing_status_detail")
    elif normalized is not None:
        reasons.append("normalized_not_dict")

    return bool(reasons), reasons


def normalize_legacy_payload(
    engine_key: str,
    legacy_payload: dict[str, Any],
) -> dict[str, Any]:
    """Normalize a legacy cached payload into the current contract shape.

    If the payload already has a valid normalized dict, it is enriched
    with any missing v1.1 fields. Otherwise, a full normalization is
    attempted from engine_result / dashboard_metadata.
    """
    if not isinstance(legacy_payload, dict):
        return build_error_output(engine_key, "Legacy payload is not a dict")

    existing_normalized = legacy_payload.get("normalized")

    # If there's a valid normalized dict, just patch in missing v1.1 fields
    if isinstance(existing_normalized, dict) and existing_normalized.get("engine_key"):
        patched = dict(existing_normalized)
        if "engine_status" not in patched:
            status, reasons = _derive_engine_status(
                score=patched.get("score"),
                signal_quality=patched.get("signal_quality", "low"),
                missing_count=patched.get("data_quality", {}).get("missing_inputs_count", 0),
                warning_count=patched.get("data_quality", {}).get("warning_count", 0),
                as_of=patched.get("as_of"),
            )
            patched["engine_status"] = status
            patched["status_detail"] = _build_status_detail(
                normalization_source="legacy_bridge",
                is_legacy=True,
                degraded_reasons=reasons + ["legacy_payload_patched"],
            )
        return patched

    # No usable normalized dict — build from scratch
    meta = ENGINE_METADATA.get(engine_key, {})
    er = (
        legacy_payload.get("engine_result")
        or legacy_payload.get("internal_engine")
        or {}
    )
    dq = legacy_payload.get("data_quality") or {}

    score = er.get("score") if isinstance(er, dict) else None
    label = "Unknown"
    if isinstance(er, dict):
        label = er.get("label") or er.get("regime_label") or "Unknown"

    warnings_list = list(er.get("warnings", [])) if isinstance(er, dict) else []
    missing = list(er.get("missing_inputs", [])) if isinstance(er, dict) else []

    return {
        "engine_key": engine_key,
        "engine_name": meta.get("name", engine_key.replace("_", " ").title()),
        "as_of": (er.get("as_of") if isinstance(er, dict) else None)
                 or legacy_payload.get("as_of"),
        "score": score,
        "label": label,
        "short_label": (er.get("short_label", "Unknown") if isinstance(er, dict) else "Unknown"),
        "confidence": (er.get("confidence_score", 0) if isinstance(er, dict) else 0),
        "signal_quality": (
            er.get("signal_quality") or dq.get("signal_quality", "low")
            if isinstance(er, dict)
            else dq.get("signal_quality", "low")
        ),
        "time_horizon": resolve_engine_horizon(engine_key),
        "freshness": {"compute_duration_s": legacy_payload.get("compute_duration_s"), "cache_hit": None, "sources": None},
        "summary": (er.get("summary", "") if isinstance(er, dict) else ""),
        "trader_takeaway": (er.get("trader_takeaway", "") if isinstance(er, dict) else ""),
        "bull_factors": [],
        "bear_factors": [],
        "risks": _build_risks(warnings_list, missing),
        "regime_tags": _derive_regime_tags(label),
        "supporting_metrics": [],
        "contradiction_flags": [],
        "data_quality": {
            "confidence_score": dq.get("confidence_score", 0),
            "signal_quality": dq.get("signal_quality", "low"),
            "missing_inputs_count": dq.get("missing_inputs_count", len(missing)),
            "warning_count": dq.get("warning_count", len(warnings_list)),
            "coverage_pct": None,
        },
        "warnings": warnings_list,
        "source_status": {"errors": {}, "proxy_count": 0, "direct_count": 0},
        "pillar_scores": [],
        "detail_sections": {},
        "engine_status": "degraded" if score is not None else "error",
        "status_detail": _build_status_detail(
            normalization_source="legacy_bridge",
            is_fallback=True,
            is_legacy=True,
            degraded_reasons=["legacy_payload_no_normalized"],
        ),
    }


def validate_normalized_output(
    normalized: Any,
) -> tuple[bool, list[str]]:
    """Validate a normalized engine output against the contract schema.

    Returns (ok, errors).
    """
    errors: list[str] = []

    if not isinstance(normalized, dict):
        return False, ["normalized output must be a dict"]

    # Required keys
    for key in REQUIRED_FIELDS:
        if key not in normalized:
            errors.append(f"missing required field: {key}")

    # engine_status value check
    status = normalized.get("engine_status")
    if status is not None and status not in VALID_ENGINE_STATUSES:
        errors.append(f"invalid engine_status: {status!r}")

    # Type checks for list fields
    for key in ("bull_factors", "bear_factors", "risks", "regime_tags",
                "supporting_metrics", "contradiction_flags", "warnings",
                "pillar_scores"):
        val = normalized.get(key)
        if val is not None and not isinstance(val, list):
            errors.append(f"{key} must be a list, got {type(val).__name__}")

    # Type checks for dict fields
    for key in ("data_quality", "source_status", "freshness",
                "detail_sections", "status_detail"):
        val = normalized.get(key)
        if val is not None and not isinstance(val, dict):
            errors.append(f"{key} must be a dict, got {type(val).__name__}")

    return (len(errors) == 0), errors
