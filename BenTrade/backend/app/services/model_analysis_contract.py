"""
Normalized Model Analysis Response Contract
=============================================

Shared normalization layer for ALL LLM/AI model-analysis responses across
BenTrade.  Wraps domain-specific model analysis outputs into a single
standardized contract shape, handling success, degraded (plaintext fallback),
and error states uniformly.

Follows the additive ``normalized`` key pattern established by:
  - ``engine_output_contract.py``  (engine output normalization)
  - ``scanner_candidate_contract.py``  (scanner candidate normalization)

Integration points
------------------
Each Market Picture service's ``_run_model_analysis()`` calls the domain
analysis function (``common.model_analysis.analyze_*``), then wraps the
result via ``wrap_service_model_response()`` to attach a ``normalized``
key before returning to the async ``run_model_analysis()`` entry point.

Contract fields
---------------
status             – "success" | "error" | "degraded"
analysis_type      – domain key (e.g. "breadth_participation")
analysis_name      – human-readable name
category           – "market_picture" | "options" | "stocks" | "active_trades"
model_source       – active endpoint key (e.g. "local")
requested_at       – ISO 8601 timestamp
completed_at       – ISO 8601 timestamp
duration_ms        – wall-clock model call time in ms
raw_content        – raw LLM text before parsing (when available)
normalized_text    – after sanitization, before JSON (when available)
structured_payload – the coerced domain-specific output dict
summary            – extracted summary text
key_points         – extracted key findings / uncertainty flags
risks              – extracted risk items
actions            – extracted action items / takeaways
confidence         – confidence normalized to 0–1 float
warnings           – data-quality / processing warnings
error_type         – classified error kind (from classify_model_error)
error_message      – user-facing error message
parse_strategy     – JSON repair method used
response_format    – "json" | "plaintext" | "empty" | "error"
metadata           – trace data, domain-specific extras
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.utils.time_horizon import resolve_model_horizon

logger = logging.getLogger("bentrade.model_analysis_contract")


# ── Analysis type metadata ────────────────────────────────────────────────

ANALYSIS_METADATA: dict[str, dict[str, str]] = {
    "regime": {"name": "Regime Analysis", "category": "market_picture"},
    "breadth_participation": {
        "name": "Breadth & Participation",
        "category": "market_picture",
    },
    "volatility_options": {
        "name": "Volatility & Options",
        "category": "market_picture",
    },
    "cross_asset_macro": {
        "name": "Cross-Asset Macro",
        "category": "market_picture",
    },
    "flows_positioning": {
        "name": "Flows & Positioning",
        "category": "market_picture",
    },
    "news_sentiment": {
        "name": "News & Sentiment",
        "category": "market_picture",
    },
    "liquidity_conditions": {
        "name": "Liquidity Conditions",
        "category": "market_picture",
    },
    "trade_analysis": {"name": "Trade Analysis", "category": "options"},
    "stock_idea": {"name": "Stock Idea", "category": "stocks"},
    "stock_strategy": {"name": "Stock Strategy", "category": "stocks"},
    "active_trade": {
        "name": "Active Trade Review",
        "category": "active_trades",
    },
}


# ── Public API ────────────────────────────────────────────────────────────


def normalize_model_analysis_response(
    analysis_type: str,
    *,
    model_result: dict[str, Any] | None = None,
    error: Exception | None = None,
    error_info: dict[str, str] | None = None,
    requested_at: str | None = None,
    completed_at: str | None = None,
    duration_ms: int | None = None,
    model_source: str | None = None,
    raw_content: str | None = None,
    normalized_text: str | None = None,
) -> dict[str, Any]:
    """Normalize a model analysis result into the standard contract shape.

    Args:
        analysis_type: Domain key (e.g. ``"breadth_participation"``).
        model_result: Coerced dict from domain analysis function (success/degraded).
        error: Exception instance (on error; mutually exclusive with model_result).
        error_info: Pre-classified ``{kind, message}`` dict (alternative to error).
        requested_at: ISO timestamp when analysis was requested.
        completed_at: ISO timestamp when normalization completed (auto-set).
        duration_ms: Wall-clock model call time in milliseconds.
        model_source: Active model source key (auto-detected if omitted).
        raw_content: Raw LLM text before parsing (if available).
        normalized_text: Sanitized text before JSON parse (if available).

    Returns:
        Normalized contract dict with all standard fields.
    """
    from common.model_sanitize import classify_model_error, user_facing_error_message

    meta = ANALYSIS_METADATA.get(
        analysis_type, {"name": analysis_type, "category": "unknown"}
    )
    now = datetime.now(timezone.utc).isoformat()

    if completed_at is None:
        completed_at = now

    # Auto-detect model source
    if model_source is None:
        try:
            from app.services.model_state import get_model_source

            model_source = get_model_source()
        except Exception:
            model_source = None

    # ── Determine status ──────────────────────────────────────────────
    if model_result is not None:
        if model_result.get("_plaintext_fallback"):
            status = "degraded"
            response_format = "plaintext"
        else:
            status = "success"
            response_format = "json"
    elif error is not None or error_info is not None:
        status = "error"
        response_format = "error"
    else:
        status = "error"
        response_format = "empty"

    # ── Classify error ────────────────────────────────────────────────
    error_type = None
    error_message = None
    if error is not None:
        error_type = classify_model_error(error)
        error_message = user_facing_error_message(error_type)
    elif error_info is not None:
        error_type = error_info.get("kind")
        error_message = error_info.get("message")

    # ── Extract fields from model_result ──────────────────────────────
    summary = None
    key_points: list[str] = []
    risks: list[str] = []
    actions: list[str] = []
    confidence: float | None = None
    warnings: list[str] = []
    parse_strategy: str | None = None
    metadata: dict[str, Any] = {}

    if model_result is not None:
        summary = _extract_summary(model_result)
        key_points = _extract_key_points(model_result)
        risks = _extract_risks(model_result)
        actions = _extract_actions(model_result)
        confidence = _extract_confidence(model_result)
        warnings = _extract_warnings(model_result)
        parse_strategy = _extract_parse_strategy(model_result)

        # Trace metadata
        trace = model_result.get("_trace")
        if isinstance(trace, dict):
            metadata["trace"] = trace

        # Carry forward domain-specific label / score
        if "label" in model_result:
            metadata["label"] = model_result["label"]
        if "score" in model_result:
            metadata["score"] = model_result["score"]

    # ── Time horizon ──────────────────────────────────────────────────
    # Derive from model_result's raw time_horizon (e.g. "1D"/"1W"/"1M")
    # or fall back to analysis_type (market-picture types share engine keys).
    raw_horizon = None
    if model_result and isinstance(model_result, dict):
        raw_horizon = model_result.get("time_horizon")
    time_horizon = resolve_model_horizon(raw_horizon, analysis_type)

    return {
        "status": status,
        "analysis_type": analysis_type,
        "analysis_name": meta["name"],
        "category": meta["category"],
        "model_source": model_source,
        "requested_at": requested_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "raw_content": raw_content,
        "normalized_text": normalized_text,
        "structured_payload": model_result,
        "summary": summary,
        "key_points": key_points,
        "risks": risks,
        "actions": actions,
        "confidence": confidence,
        "warnings": warnings,
        "error_type": error_type,
        "error_message": error_message,
        "parse_strategy": parse_strategy,
        "response_format": response_format,
        "time_horizon": time_horizon,
        "metadata": metadata,
    }


def wrap_service_model_response(
    analysis_type: str,
    service_result: dict[str, Any],
    *,
    requested_at: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """Add the normalized contract to a service's existing model analysis result.

    Takes the existing ``{model_analysis, as_of, error?}`` dict produced by
    ``_run_model_analysis()`` and attaches a ``normalized`` key with the
    standard contract.  All existing keys are preserved unchanged.

    Args:
        analysis_type: Domain key (e.g. ``"breadth_participation"``).
        service_result: Dict with ``model_analysis`` and optional ``error``.
        requested_at: ISO timestamp when analysis was requested.
        duration_ms: Wall-clock model call time in milliseconds.

    Returns:
        The same ``service_result`` dict with an added ``normalized`` key.
    """
    model_result = service_result.get("model_analysis")
    error_info = service_result.get("error")

    normalized = normalize_model_analysis_response(
        analysis_type,
        model_result=model_result,
        error_info=error_info,
        requested_at=requested_at,
        duration_ms=duration_ms,
    )

    service_result["normalized"] = normalized
    return service_result


def parse_raw_model_text(
    raw_text: str | bytes | None,
    analysis_type: str = "unknown",
) -> dict[str, Any]:
    """Parse raw LLM response text into the standard contract.

    Handles: valid JSON, JSON string, markdown/code-fenced JSON,
    plain-text prose, partial JSON, empty response, null content,
    invalid UTF-8 bytes.

    This is a standalone parser for cases where you want the normalized
    contract directly from raw model output, bypassing domain-specific
    coercion.

    Args:
        raw_text: Raw text (or bytes) from LLM response.
        analysis_type: Domain key for metadata.

    Returns:
        Normalized contract dict.
    """
    from common.json_repair import extract_and_repair_json
    from common.model_sanitize import sanitize_model_text

    requested_at = datetime.now(timezone.utc).isoformat()

    # Handle None
    if raw_text is None:
        return normalize_model_analysis_response(
            analysis_type,
            error_info={
                "kind": "empty_response",
                "message": "Model returned null content.",
            },
            requested_at=requested_at,
        )

    # Handle bytes (invalid UTF-8)
    if isinstance(raw_text, bytes):
        raw_text = raw_text.decode("utf-8", errors="replace")

    raw_text = str(raw_text)

    # Empty
    if not raw_text.strip():
        return normalize_model_analysis_response(
            analysis_type,
            error_info={
                "kind": "empty_response",
                "message": "Model returned empty response.",
            },
            requested_at=requested_at,
            raw_content=raw_text,
        )

    # Sanitize (strip think tags)
    sanitized = sanitize_model_text(raw_text)

    if not sanitized.strip():
        return normalize_model_analysis_response(
            analysis_type,
            error_info={
                "kind": "empty_response",
                "message": "Model response contained only reasoning tags.",
            },
            requested_at=requested_at,
            raw_content=raw_text,
            normalized_text="",
        )

    # Attempt JSON extraction
    parsed, method = extract_and_repair_json(sanitized)

    if parsed is not None and isinstance(parsed, dict):
        return normalize_model_analysis_response(
            analysis_type,
            model_result=parsed,
            requested_at=requested_at,
            raw_content=raw_text,
            normalized_text=sanitized,
        )

    if parsed is not None and isinstance(parsed, list) and parsed:
        first = parsed[0]
        if isinstance(first, dict):
            return normalize_model_analysis_response(
                analysis_type,
                model_result=first,
                requested_at=requested_at,
                raw_content=raw_text,
                normalized_text=sanitized,
            )

    # Plaintext fallback — model returned prose, not JSON
    if len(sanitized) >= 20:
        fallback = {
            "summary": (
                sanitized[:1500].strip()
                + ("\u2026" if len(sanitized) > 1500 else "")
            ),
            "_plaintext_fallback": True,
        }
        return normalize_model_analysis_response(
            analysis_type,
            model_result=fallback,
            requested_at=requested_at,
            raw_content=raw_text,
            normalized_text=sanitized,
        )

    # Total failure
    return normalize_model_analysis_response(
        analysis_type,
        error_info={
            "kind": "malformed_response",
            "message": "Model response could not be parsed.",
        },
        requested_at=requested_at,
        raw_content=raw_text,
        normalized_text=sanitized,
    )


# ── Private field extractors ──────────────────────────────────────────────


def _extract_summary(result: dict[str, Any]) -> str | None:
    """Extract summary text from a domain-specific model result."""
    for key in ("summary", "executive_summary", "headline"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _extract_key_points(result: dict[str, Any]) -> list[str]:
    """Extract key findings / flags from a domain-specific model result."""
    points: list[str] = []
    for key in ("uncertainty_flags", "key_drivers", "key_supports"):
        val = result.get(key)
        if isinstance(val, list):
            points.extend(str(v).strip() for v in val if str(v).strip())
    return points[:10]


def _extract_risks(result: dict[str, Any]) -> list[str]:
    """Extract risk items from a domain-specific model result."""
    risks: list[str] = []
    for key in ("key_risks", "risk_review", "risk_flags"):
        val = result.get(key)
        if isinstance(val, list):
            risks.extend(str(v).strip() for v in val if str(v).strip())
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, str) and sub_val.strip():
                    risks.append(f"{sub_key}: {sub_val.strip()}")
    return risks[:10]


def _extract_actions(result: dict[str, Any]) -> list[str]:
    """Extract action items / takeaways from a domain-specific model result."""
    actions: list[str] = []
    for key in ("trader_takeaway", "action", "action_plan"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            actions.append(val.strip())
        elif isinstance(val, dict):
            primary = val.get("primary_action") or val.get("next_step")
            if isinstance(primary, str) and primary.strip():
                actions.append(primary.strip())
    return actions[:5]


def _extract_confidence(result: dict[str, Any]) -> float | None:
    """Extract and normalize confidence to 0–1 scale.

    Formula: if raw > 1.0 → divide by 100 (assumes 0–100 scale).
    Input: result["confidence"] (float, int, or str).
    Output: 0.0–1.0 float or None.
    """
    raw = result.get("confidence")
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val > 1.0:
        val = val / 100.0
    return round(max(0.0, min(1.0, val)), 2)


def _extract_parse_strategy(result: dict[str, Any]) -> str | None:
    """Extract parse strategy from ``_trace`` metadata."""
    trace = result.get("_trace", {})
    if isinstance(trace, dict):
        return trace.get("method") or trace.get("parse_method")
    return None


def _extract_warnings(result: dict[str, Any]) -> list[str]:
    """Extract warnings from model result."""
    warnings: list[str] = []
    for key in ("warnings", "data_quality_flags", "missing_data"):
        val = result.get(key)
        if isinstance(val, list):
            warnings.extend(str(v).strip() for v in val if str(v).strip())
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if sub_val:
                    warnings.append(f"{sub_key}: {sub_val}")
    if result.get("_plaintext_fallback"):
        warnings.append("Model returned plain text instead of structured JSON")
    return warnings[:15]
