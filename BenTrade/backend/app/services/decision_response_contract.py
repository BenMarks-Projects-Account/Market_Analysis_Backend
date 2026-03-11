"""
Final Decision Response Contract v1.

Defines the stable output shape for higher-order trade decision results.
Provides builders, validators, and placeholder generators so that
downstream consumers (UI cards, logs, orchestration) can rely on a
single, versioned schema regardless of whether the decision was produced
by a live model, a manual review, or a development placeholder.

Public API
----------
build_decision_response(...)   – full builder; every field explicit
build_placeholder_response(**) – quick mock/dev generator
validate_decision_response(r)  – schema check; returns (ok, errors)
normalize_decision_response(r) – coerce/fill missing fields safely

Decision semantics
------------------
approve            – all factors aligned; proceed at recommended size
cautious_approve   – positive with caveats; proceed with reduced size
watchlist           – interesting but not actionable now; monitor
reject             – does not meet criteria; skip
insufficient_data  – cannot determine; data too incomplete
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

# ── version lock ──────────────────────────────────────────────────────────
_RESPONSE_VERSION = "1.0"

# ── allowed enum values ──────────────────────────────────────────────────
VALID_DECISIONS = frozenset({
    "approve",
    "cautious_approve",
    "watchlist",
    "reject",
    "insufficient_data",
})

VALID_CONVICTION_LEVELS = frozenset({
    "high",
    "moderate",
    "low",
    "none",
})

VALID_ALIGNMENTS = frozenset({
    "aligned",
    "neutral",
    "misaligned",
    "unknown",
})

VALID_FIT_LEVELS = frozenset({
    "good",
    "acceptable",
    "poor",
    "unknown",
})

VALID_POLICY_ALIGNMENTS = frozenset({
    "clear",
    "conditional",
    "restricted",
    "blocked",
    "unknown",
})

VALID_EVENT_RISK_LEVELS = frozenset({
    "low",
    "moderate",
    "elevated",
    "high",
    "unknown",
})

VALID_SIZE_GUIDANCE = frozenset({
    "normal",
    "reduced",
    "minimal",
    "none",
})

# ── human-readable decision labels ──────────────────────────────────────
_DECISION_LABELS: dict[str, str] = {
    "approve":           "Approve",
    "cautious_approve":  "Cautious Approve",
    "watchlist":         "Watchlist",
    "reject":            "Reject",
    "insufficient_data": "Insufficient Data",
}

# ── required top-level keys (always present, may be empty/default) ──────
_REQUIRED_KEYS = frozenset({
    "response_version",
    "generated_at",
    "status",
    "decision",
    "decision_label",
    "conviction",
    "market_alignment",
    "portfolio_fit",
    "policy_alignment",
    "event_risk",
    "time_horizon",
    "summary",
    "reasons_for",
    "reasons_against",
    "key_risks",
    "size_guidance",
    "invalidation_notes",
    "monitoring_notes",
    "warning_flags",
    "evidence",
    "metadata",
})


# =====================================================================
#  Internal: response-level confidence assessment
# =====================================================================

def _build_response_confidence(
    decision: str,
    conviction: str,
    market_alignment: str,
    portfolio_fit: str,
    policy_alignment: str,
    event_risk: str,
    warning_flags: list[str] | None,
) -> dict[str, Any]:
    """Derive a confidence assessment for the response itself.

    Uses the confidence_framework to produce a standardised block.
    """
    from app.services.confidence_framework import (
        build_confidence_assessment,
        make_impact,
    )

    # Base score from conviction strength
    _conviction_base: dict[str, float] = {
        "high": 0.95,
        "moderate": 0.70,
        "low": 0.45,
        "none": 0.15,
    }
    base = _conviction_base.get(conviction, 0.50)

    # Collect impacts from alignment fields
    impacts: list[dict[str, Any]] = []

    if market_alignment == "misaligned":
        impacts.append(make_impact("conflict", 0.10, "market misaligned"))
    if portfolio_fit == "poor":
        impacts.append(make_impact("coverage", 0.10, "poor portfolio fit"))
    if policy_alignment in ("blocked", "restricted"):
        impacts.append(make_impact("readiness", 0.15, f"policy: {policy_alignment}"))
    elif policy_alignment == "conditional":
        impacts.append(make_impact("readiness", 0.05, "policy: conditional"))
    if event_risk in ("high", "elevated"):
        impacts.append(make_impact("conflict", 0.05, f"event risk: {event_risk}"))

    # Warnings penalty
    wf = warning_flags or []
    if len(wf) > 2:
        impacts.append(make_impact("quality", 0.05 * min(len(wf) - 2, 4),
                                   f"{len(wf)} warning flags present"))

    return build_confidence_assessment(
        base_score=base,
        extra_impacts=impacts,
        source="decision_response_contract",
    )


# =====================================================================
#  Public: build_decision_response
# =====================================================================

def build_decision_response(
    *,
    decision: str,
    conviction: str = "moderate",
    market_alignment: str = "unknown",
    portfolio_fit: str = "unknown",
    policy_alignment: str = "unknown",
    event_risk: str = "unknown",
    time_horizon: str | None = None,
    summary: str = "",
    reasons_for: list[str] | None = None,
    reasons_against: list[str] | None = None,
    key_risks: list[str] | None = None,
    size_guidance: str = "normal",
    invalidation_notes: list[str] | None = None,
    monitoring_notes: list[str] | None = None,
    warning_flags: list[str] | None = None,
    evidence: dict | None = None,
    metadata: dict | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    """Build a fully-populated decision response conforming to v1 contract.

    Parameters
    ----------
    decision : str
        One of VALID_DECISIONS.
    conviction : str
        One of VALID_CONVICTION_LEVELS (default "moderate").
    market_alignment, portfolio_fit, policy_alignment, event_risk : str
        Alignment / fit / risk indicators with their own valid sets.
    time_horizon : str | None
        E.g. "1-5 DTE", "weekly", "swing".
    summary : str
        Human-readable summary paragraph.
    reasons_for / reasons_against / key_risks : list[str]
        Bullet-point lists; empty list if None.
    size_guidance : str
        One of VALID_SIZE_GUIDANCE (default "normal").
    invalidation_notes / monitoring_notes : list[str]
        Guidance lists; empty list if None.
    warning_flags : list[str]
        Aggregated warnings; empty list if None.
    evidence : dict
        Supporting evidence blob; empty dict if None.
    metadata : dict
        Additional metadata; merged with contract-level metadata.
    source : str
        Origin tag: "model", "manual", "placeholder", etc.

    Returns
    -------
    dict  –  Stable decision response conforming to _RESPONSE_VERSION.
    """
    # Normalise decision
    decision = _normalise_enum(decision, VALID_DECISIONS, "insufficient_data")
    conviction = _normalise_enum(conviction, VALID_CONVICTION_LEVELS, "none")
    market_alignment = _normalise_enum(market_alignment, VALID_ALIGNMENTS, "unknown")
    portfolio_fit = _normalise_enum(portfolio_fit, VALID_FIT_LEVELS, "unknown")
    policy_alignment = _normalise_enum(policy_alignment, VALID_POLICY_ALIGNMENTS, "unknown")
    event_risk = _normalise_enum(event_risk, VALID_EVENT_RISK_LEVELS, "unknown")
    size_guidance = _normalise_enum(size_guidance, VALID_SIZE_GUIDANCE, "normal")

    # Insufficient data forces conviction to "none" — cannot have
    # conviction when the decision is indeterminate.
    if decision == "insufficient_data":
        conviction = "none"

    # Derive status
    status = _derive_status(decision, warning_flags)

    # Derive label
    decision_label = _DECISION_LABELS.get(decision, decision.replace("_", " ").title())

    now_iso = datetime.now(timezone.utc).isoformat()

    base_meta: dict[str, Any] = {
        "response_version": _RESPONSE_VERSION,
        "generated_at": now_iso,
        "source": str(source) if source else "manual",
    }
    if metadata and isinstance(metadata, dict):
        base_meta.update(metadata)

    return {
        "response_version": _RESPONSE_VERSION,
        "generated_at": now_iso,
        "status": status,
        "decision": decision,
        "decision_label": decision_label,
        "conviction": conviction,
        "market_alignment": market_alignment,
        "portfolio_fit": portfolio_fit,
        "policy_alignment": policy_alignment,
        "event_risk": event_risk,
        "time_horizon": time_horizon or "",
        "summary": str(summary) if summary else "",
        "reasons_for": _safe_str_list(reasons_for),
        "reasons_against": _safe_str_list(reasons_against),
        "key_risks": _safe_str_list(key_risks),
        "size_guidance": size_guidance,
        "invalidation_notes": _safe_str_list(invalidation_notes),
        "monitoring_notes": _safe_str_list(monitoring_notes),
        "warning_flags": _safe_str_list(warning_flags),
        "evidence": dict(evidence) if isinstance(evidence, dict) else {},
        "metadata": base_meta,
        "confidence_assessment": _build_response_confidence(
            decision, conviction, market_alignment, portfolio_fit,
            policy_alignment, event_risk, warning_flags,
        ),
    }


# =====================================================================
#  Public: build_placeholder_response
# =====================================================================

def build_placeholder_response(
    *,
    decision: str = "watchlist",
    symbol: str = "UNKNOWN",
    strategy: str = "unknown",
    summary: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a placeholder/dev/mock decision response.

    Useful for:
    - frontend development before the model is wired
    - integration tests that need realistic contract shapes
    - manual review entries

    Returns a valid decision response with ``source="placeholder"``.
    """
    if not summary:
        summary = (
            f"Placeholder decision for {symbol} {strategy}. "
            "This response was generated for development or testing purposes."
        )

    defaults: dict[str, Any] = {
        "decision": decision,
        "conviction": "moderate",
        "market_alignment": "neutral",
        "portfolio_fit": "acceptable",
        "policy_alignment": "clear",
        "event_risk": "low",
        "time_horizon": "",
        "summary": summary,
        "reasons_for": ["Placeholder: meets basic criteria"],
        "reasons_against": ["Placeholder: no live analysis performed"],
        "key_risks": ["Placeholder data — not a real assessment"],
        "size_guidance": "normal",
        "invalidation_notes": [],
        "monitoring_notes": [],
        "warning_flags": ["placeholder_response"],
        "evidence": {"symbol": symbol, "strategy": strategy},
        "metadata": {"symbol": symbol, "strategy": strategy},
        "source": "placeholder",
    }
    defaults.update(overrides)
    return build_decision_response(**defaults)


# =====================================================================
#  Public: validate_decision_response
# =====================================================================

def validate_decision_response(response: Any) -> tuple[bool, list[str]]:
    """Validate a decision response against the v1 contract.

    Returns
    -------
    (ok, errors) : tuple[bool, list[str]]
        ok is True when the response is fully valid.
        errors lists every violation found.
    """
    errors: list[str] = []

    if not isinstance(response, dict):
        return False, ["response is not a dict"]

    # Required keys
    missing = _REQUIRED_KEYS - set(response.keys())
    if missing:
        errors.append(f"missing required keys: {sorted(missing)}")

    # Version
    if response.get("response_version") != _RESPONSE_VERSION:
        errors.append(
            f"response_version mismatch: expected {_RESPONSE_VERSION!r}, "
            f"got {response.get('response_version')!r}"
        )

    # Decision enum
    decision = response.get("decision")
    if decision not in VALID_DECISIONS:
        errors.append(f"invalid decision: {decision!r}")

    # Conviction enum
    conviction = response.get("conviction")
    if conviction not in VALID_CONVICTION_LEVELS:
        errors.append(f"invalid conviction: {conviction!r}")

    # Alignment enums
    if response.get("market_alignment") not in VALID_ALIGNMENTS:
        errors.append(f"invalid market_alignment: {response.get('market_alignment')!r}")
    if response.get("portfolio_fit") not in VALID_FIT_LEVELS:
        errors.append(f"invalid portfolio_fit: {response.get('portfolio_fit')!r}")
    if response.get("policy_alignment") not in VALID_POLICY_ALIGNMENTS:
        errors.append(f"invalid policy_alignment: {response.get('policy_alignment')!r}")
    if response.get("event_risk") not in VALID_EVENT_RISK_LEVELS:
        errors.append(f"invalid event_risk: {response.get('event_risk')!r}")
    if response.get("size_guidance") not in VALID_SIZE_GUIDANCE:
        errors.append(f"invalid size_guidance: {response.get('size_guidance')!r}")

    # List fields
    for key in ("reasons_for", "reasons_against", "key_risks",
                "invalidation_notes", "monitoring_notes", "warning_flags"):
        val = response.get(key)
        if val is not None and not isinstance(val, list):
            errors.append(f"{key} must be a list, got {type(val).__name__}")

    # Dict fields
    for key in ("evidence", "metadata"):
        val = response.get(key)
        if val is not None and not isinstance(val, dict):
            errors.append(f"{key} must be a dict, got {type(val).__name__}")

    # String fields
    for key in ("summary", "decision_label", "time_horizon", "generated_at", "status"):
        val = response.get(key)
        if val is not None and not isinstance(val, str):
            errors.append(f"{key} must be a str, got {type(val).__name__}")

    return (len(errors) == 0, errors)


# =====================================================================
#  Public: normalize_decision_response
# =====================================================================

def normalize_decision_response(response: Any) -> dict[str, Any]:
    """Coerce/fill a possibly-incomplete response into a valid contract shape.

    Missing fields receive safe defaults.  Invalid enum values are
    replaced with their safe fallback.  This does **not** mutate the
    input dict.

    Use this when consuming responses from untrusted or legacy sources.
    """
    if not isinstance(response, dict):
        response = {}
    else:
        response = copy.deepcopy(response)

    # Ensure all required keys exist with safe defaults
    now_iso = datetime.now(timezone.utc).isoformat()

    response.setdefault("response_version", _RESPONSE_VERSION)
    response.setdefault("generated_at", now_iso)
    response.setdefault("decision", "insufficient_data")
    response.setdefault("conviction", "none")
    response.setdefault("market_alignment", "unknown")
    response.setdefault("portfolio_fit", "unknown")
    response.setdefault("policy_alignment", "unknown")
    response.setdefault("event_risk", "unknown")
    response.setdefault("time_horizon", "")
    response.setdefault("summary", "")
    response.setdefault("reasons_for", [])
    response.setdefault("reasons_against", [])
    response.setdefault("key_risks", [])
    response.setdefault("size_guidance", "normal")
    response.setdefault("invalidation_notes", [])
    response.setdefault("monitoring_notes", [])
    response.setdefault("warning_flags", [])
    response.setdefault("evidence", {})
    response.setdefault("metadata", {})

    # Normalise enums
    response["decision"] = _normalise_enum(
        response["decision"], VALID_DECISIONS, "insufficient_data"
    )
    response["conviction"] = _normalise_enum(
        response["conviction"], VALID_CONVICTION_LEVELS, "none"
    )
    response["market_alignment"] = _normalise_enum(
        response["market_alignment"], VALID_ALIGNMENTS, "unknown"
    )
    response["portfolio_fit"] = _normalise_enum(
        response["portfolio_fit"], VALID_FIT_LEVELS, "unknown"
    )
    response["policy_alignment"] = _normalise_enum(
        response["policy_alignment"], VALID_POLICY_ALIGNMENTS, "unknown"
    )
    response["event_risk"] = _normalise_enum(
        response["event_risk"], VALID_EVENT_RISK_LEVELS, "unknown"
    )
    response["size_guidance"] = _normalise_enum(
        response["size_guidance"], VALID_SIZE_GUIDANCE, "normal"
    )

    # Derive status + label
    response["status"] = _derive_status(
        response["decision"], response.get("warning_flags")
    )
    response["decision_label"] = _DECISION_LABELS.get(
        response["decision"],
        response["decision"].replace("_", " ").title(),
    )

    # Coerce list fields
    for key in ("reasons_for", "reasons_against", "key_risks",
                "invalidation_notes", "monitoring_notes", "warning_flags"):
        response[key] = _safe_str_list(response.get(key))

    # Coerce dict fields
    for key in ("evidence", "metadata"):
        if not isinstance(response.get(key), dict):
            response[key] = {}

    # Coerce string fields
    for key in ("summary", "time_horizon"):
        if not isinstance(response.get(key), str):
            response[key] = str(response.get(key, "")) if response.get(key) is not None else ""

    return response


# =====================================================================
#  Internal helpers
# =====================================================================

def _normalise_enum(value: Any, valid: frozenset[str], fallback: str) -> str:
    """Return *value* if it's in *valid*, else *fallback*."""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in valid:
            return v
    return fallback


def _derive_status(decision: str, warning_flags: list[str] | None) -> str:
    """Derive a response status from decision + warnings.

    Status semantics:
    - "complete"           – decision made, data was sufficient
    - "partial"            – decision made but with warnings / degraded data
    - "insufficient_data"  – cannot determine
    """
    if decision == "insufficient_data":
        return "insufficient_data"
    if warning_flags and len(warning_flags) > 0:
        return "partial"
    return "complete"


def _safe_str_list(val: Any) -> list[str]:
    """Coerce *val* to a list of strings.  Non-list → []; non-str items → str()."""
    if not isinstance(val, list):
        return []
    return [str(item) for item in val if item is not None]
