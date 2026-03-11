"""Trade Decision Orchestrator v1.1.

Assembles the major BenTrade decision subsystems into a single structured
**decision packet** that represents the full picture of a proposed trade
opportunity.

This is a packaging layer — it does NOT make the final approve/reject
decision, call an LLM, execute trades, or replace policy logic.  It
normalises, packages, surfaces missing/degraded areas, and emits a clean
decision packet for downstream review.

Public API
----------
build_decision_packet(
    *,
    candidate      = None,   # normalized candidate (scanner_candidate_contract)
    market         = None,   # market composite (build_market_composite)
    conflicts      = None,   # conflict report (detect_conflicts)
    portfolio      = None,   # portfolio exposure (build_portfolio_exposure)
    policy         = None,   # policy evaluation (evaluate_policy)
    events         = None,   # event context (build_event_context)
    model_context  = None,   # normalized model analysis (or list thereof)
    assembled      = None,   # assembled context (assemble_context) — supporting
) -> dict

v1.1 changes (second pass)
--------------------------
- ``assembled`` section now included in output as supporting context
- Evidence tracks events integration status, model context details,
  assembled context degradation, and per-section statuses
- Metadata includes ``component_roles`` documenting each section's
  integration depth (evaluated / support_context_only / metadata_only)
- Version bumped to 1.1

Output version: 1.1
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
_PACKET_VERSION = "1.1"

# ---------------------------------------------------------------------------
# Subsystem registry — order matters for summary rendering
# ---------------------------------------------------------------------------
# (key, human label, required for "complete")
_SUBSYSTEMS: list[tuple[str, str, bool]] = [
    ("candidate", "Candidate", True),
    ("market", "Market Composite", True),
    ("policy", "Decision Policy", True),
    ("conflicts", "Conflict Detector", False),
    ("portfolio", "Portfolio Exposure", False),
    ("events", "Event Context", False),
    ("model_context", "Model Analysis", False),
    ("assembled", "Assembled Context", False),
]

_REQUIRED_KEYS = {k for k, _, req in _SUBSYSTEMS if req}
_ALL_KEYS = {k for k, _, _ in _SUBSYSTEMS}
_LABEL_MAP = {k: label for k, label, _ in _SUBSYSTEMS}

# Component roles — documents what each section represents in the packet.
# "evaluated"             = deterministic checks were run, output is authoritative
# "support_context_only"  = structured supporting context, not policy-enforced
# "metadata_only"         = fallback/metadata source, not evaluated
_COMPONENT_ROLES: dict[str, str] = {
    "candidate":     "evaluated",
    "market":        "evaluated",
    "policy":        "evaluated",
    "conflicts":     "evaluated",
    "portfolio":     "evaluated",
    "events":        "support_context_only",
    "model_context": "support_context_only",
    "assembled":     "metadata_only",
}


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def build_decision_packet(
    *,
    candidate: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
    conflicts: dict[str, Any] | None = None,
    portfolio: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    events: dict[str, Any] | None = None,
    model_context: dict[str, Any] | list[dict[str, Any]] | None = None,
    assembled: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured decision packet from available subsystem outputs.

    Parameters
    ----------
    candidate : dict | None
        Normalized candidate from ``normalize_candidate_output``.
    market : dict | None
        Market composite from ``build_market_composite``.
    conflicts : dict | None
        Conflict report from ``detect_conflicts``.
    portfolio : dict | None
        Portfolio exposure from ``build_portfolio_exposure``.
    policy : dict | None
        Policy evaluation from ``evaluate_policy``.
    events : dict | None
        Event context from ``build_event_context``.
    model_context : dict | list[dict] | None
        One or more normalized model-analysis results.
    assembled : dict | None
        Full assembled context — used as fallback metadata only.

    Returns
    -------
    dict
        Decision packet with the canonical shape.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Collect all inputs into a lookup for uniform processing
    inputs: dict[str, Any] = {
        "candidate": candidate,
        "market": market,
        "conflicts": conflicts,
        "portfolio": portfolio,
        "policy": policy,
        "events": events,
        "model_context": model_context,
        "assembled": assembled,
    }

    # --- Presence / status analysis ------------------------------------------
    presence = _analyse_presence(inputs)
    section_statuses = _analyse_section_statuses(inputs)
    status = _derive_packet_status(presence, section_statuses)
    warning_flags = _collect_warning_flags(presence, section_statuses, inputs)
    quality_overview = _build_quality_overview(
        presence, section_statuses, status, warning_flags,
    )

    # --- Confidence assessment (framework v1) --------------------------------
    from app.services.confidence_framework import (
        build_confidence_assessment,
        build_uncertainty_summary,
    )

    _cov_ratio = quality_overview.get("coverage_ratio", 0.0)
    _degraded = quality_overview.get("subsystems_degraded", [])
    _quality_st = (
        "degraded" if _degraded
        else ("good" if _cov_ratio >= 0.85 else "acceptable")
    )
    _coverage_lvl = (
        "full" if _cov_ratio >= 1.0
        else "high" if _cov_ratio >= 0.75
        else "partial" if _cov_ratio >= 0.50
        else "minimal" if _cov_ratio > 0
        else "none"
    )
    _conf_assessment = build_confidence_assessment(
        base_score=_cov_ratio,
        quality_status=_quality_st,
        coverage_level=_coverage_lvl,
        source="trade_decision_orchestrator",
    )
    quality_overview["confidence_assessment"] = _conf_assessment
    quality_overview["uncertainty_summary"] = build_uncertainty_summary(
        _conf_assessment,
    )

    # --- Build sections ------------------------------------------------------
    candidate_section = _build_candidate_section(candidate)
    market_section = _build_market_section(market)
    conflicts_section = _build_conflicts_section(conflicts)
    portfolio_section = _build_portfolio_section(portfolio)
    policy_section = _build_policy_section(policy)
    events_section = _build_events_section(events)
    model_section = _build_model_section(model_context)
    assembled_section = _build_assembled_section(assembled)

    # --- Evidence & metadata -------------------------------------------------
    evidence = _build_evidence(inputs, presence, section_statuses)
    metadata = _build_metadata(inputs, presence, now)

    # --- Summary -------------------------------------------------------------
    summary = _build_summary(
        status, presence, section_statuses, inputs, warning_flags,
    )

    return {
        "decision_packet_version": _PACKET_VERSION,
        "generated_at": now,
        "status": status,
        "summary": summary,
        "candidate": candidate_section,
        "market": market_section,
        "portfolio": portfolio_section,
        "policy": policy_section,
        "events": events_section,
        "conflicts": conflicts_section,
        "model_context": model_section,
        "assembled": assembled_section,
        "quality_overview": quality_overview,
        "warning_flags": warning_flags,
        "evidence": evidence,
        "metadata": metadata,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Presence & status helpers
# ═══════════════════════════════════════════════════════════════════════════

def _analyse_presence(inputs: dict[str, Any]) -> dict[str, str]:
    """Return 'present' | 'missing' for each subsystem key."""
    result: dict[str, str] = {}
    for key in _ALL_KEYS:
        val = inputs.get(key)
        if val is None:
            result[key] = "missing"
        elif isinstance(val, dict) and not val:
            result[key] = "missing"
        elif isinstance(val, list) and not val:
            result[key] = "missing"
        else:
            result[key] = "present"
    return result


def _analyse_section_statuses(inputs: dict[str, Any]) -> dict[str, str]:
    """Extract per-section upstream status where available.

    Returns 'ok' | 'degraded' | 'partial' | 'error' | 'missing' | 'unknown'.
    """
    result: dict[str, str] = {}
    for key in _ALL_KEYS:
        val = inputs.get(key)
        if val is None or (isinstance(val, (dict, list)) and not val):
            result[key] = "missing"
            continue

        if isinstance(val, list):
            # model_context as list — aggregate
            statuses = [
                _normalise_upstream_status(item.get("status") if isinstance(item, dict) else None)
                for item in val
            ]
            if all(s == "ok" for s in statuses):
                result[key] = "ok"
            elif any(s == "error" for s in statuses):
                result[key] = "degraded"
            else:
                result[key] = "partial"
            continue

        if not isinstance(val, dict):
            result[key] = "unknown"
            continue

        # Map upstream status vocabulary to our simplified vocabulary
        raw_status = val.get("status") or val.get("assembly_status")
        result[key] = _normalise_upstream_status(raw_status)

    return result


_STATUS_MAP: dict[str, str] = {
    # market_composite
    "ok": "ok",
    "degraded": "degraded",
    "insufficient_data": "error",
    # conflict_detector
    "clean": "ok",
    "conflicts_detected": "ok",
    # portfolio_risk_engine
    "partial": "partial",
    "empty": "error",
    # decision_policy
    "evaluated": "ok",
    # event_calendar_context
    "no_data": "error",
    # model_analysis_contract
    "success": "ok",
    "error": "error",
    # context_assembler
    "complete": "ok",
}


def _normalise_upstream_status(raw: str | None) -> str:
    if raw is None:
        return "unknown"
    return _STATUS_MAP.get(raw, "unknown")


# ═══════════════════════════════════════════════════════════════════════════
# Packet-level status
# ═══════════════════════════════════════════════════════════════════════════

def _derive_packet_status(
    presence: dict[str, str],
    section_statuses: dict[str, str],
) -> str:
    """Derive overall packet status.

    Rules (v1, deterministic):
    - candidate missing → insufficient_data
    - all *required* subsystems present and none in error → complete
    - otherwise → partial
    """
    # Candidate is the absolute minimum
    if presence.get("candidate") == "missing":
        return "insufficient_data"

    # Check all required subsystems
    all_required_present = all(
        presence.get(k) == "present" for k in _REQUIRED_KEYS
    )
    any_required_error = any(
        section_statuses.get(k) == "error" for k in _REQUIRED_KEYS
    )

    if all_required_present and not any_required_error:
        return "complete"

    return "partial"


# ═══════════════════════════════════════════════════════════════════════════
# Warning flags
# ═══════════════════════════════════════════════════════════════════════════

def _collect_warning_flags(
    presence: dict[str, str],
    section_statuses: dict[str, str],
    inputs: dict[str, Any],
) -> list[str]:
    flags: list[str] = []

    # Missing subsystems
    for key in _ALL_KEYS:
        if presence.get(key) == "missing":
            flags.append(f"{key}_not_provided")

    # Degraded / error upstream subsystems
    for key in _ALL_KEYS:
        ss = section_statuses.get(key, "missing")
        if ss == "degraded":
            flags.append(f"{key}_degraded")
        elif ss == "error":
            flags.append(f"{key}_error")
        elif ss == "partial":
            flags.append(f"{key}_partial")

    # Specific high-value warnings
    market = inputs.get("market")
    if isinstance(market, dict):
        ms = market.get("status")
        if ms == "degraded":
            flags.append("market_composite_degraded")
        elif ms == "insufficient_data":
            flags.append("market_composite_insufficient")

    policy = inputs.get("policy")
    if isinstance(policy, dict):
        pd = policy.get("policy_decision")
        if pd == "block":
            flags.append("policy_blocks_trade")
        elif pd == "restrict":
            flags.append("policy_restricts_trade")
        elif pd == "insufficient_data":
            flags.append("policy_insufficient_data")

    events = inputs.get("events")
    if isinstance(events, dict):
        ers = events.get("event_risk_state")
        if ers == "crowded":
            flags.append("event_calendar_crowded")
        elif ers == "elevated":
            flags.append("event_calendar_elevated")

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            deduped.append(f)
    return deduped


# ═══════════════════════════════════════════════════════════════════════════
# Quality overview
# ═══════════════════════════════════════════════════════════════════════════

def _build_quality_overview(
    presence: dict[str, str],
    section_statuses: dict[str, str],
    packet_status: str,
    warning_flags: list[str],
) -> dict[str, Any]:
    present = [k for k in _ALL_KEYS if presence.get(k) == "present"]
    missing = [k for k in _ALL_KEYS if presence.get(k) == "missing"]
    degraded = [
        k for k in _ALL_KEYS
        if section_statuses.get(k) in ("degraded", "partial", "error")
        and presence.get(k) == "present"
    ]

    total = len(_ALL_KEYS)
    present_count = len(present)

    # Decision readiness
    if packet_status == "complete" and not degraded:
        decision_ready = True
        readiness_note = "All required subsystems present and healthy."
    elif packet_status == "complete" and degraded:
        decision_ready = True
        readiness_note = (
            "All required subsystems present but some are degraded: "
            + ", ".join(_LABEL_MAP.get(k, k) for k in degraded)
            + "."
        )
    elif packet_status == "partial":
        decision_ready = False
        readiness_note = (
            "Packet is partial — missing or errored: "
            + ", ".join(_LABEL_MAP.get(k, k) for k in missing)
            + "."
        )
    else:
        decision_ready = False
        readiness_note = "Insufficient data for decision review."

    return {
        "packet_status": packet_status,
        "decision_ready": decision_ready,
        "readiness_note": readiness_note,
        "subsystems_present": sorted(present),
        "subsystems_missing": sorted(missing),
        "subsystems_degraded": sorted(degraded),
        "present_count": present_count,
        "total_subsystems": total,
        "coverage_ratio": round(present_count / total, 2) if total else 0.0,
        "warning_count": len(warning_flags),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Section builders — preserve upstream structure, don't fabricate
# ═══════════════════════════════════════════════════════════════════════════

def _build_candidate_section(
    candidate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not candidate or not isinstance(candidate, dict):
        return None
    return dict(candidate)


def _build_market_section(
    market: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not market or not isinstance(market, dict):
        return None
    return dict(market)


def _build_conflicts_section(
    conflicts: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not conflicts or not isinstance(conflicts, dict):
        return None
    return dict(conflicts)


def _build_portfolio_section(
    portfolio: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not portfolio or not isinstance(portfolio, dict):
        return None
    return dict(portfolio)


def _build_policy_section(
    policy: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not policy or not isinstance(policy, dict):
        return None
    return dict(policy)


def _build_events_section(
    events: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not events or not isinstance(events, dict):
        return None
    return dict(events)


def _build_model_section(
    model_context: dict[str, Any] | list[dict[str, Any]] | None,
) -> dict[str, Any] | list[dict[str, Any]] | None:
    if model_context is None:
        return None
    if isinstance(model_context, list):
        if not model_context:
            return None
        return [dict(m) if isinstance(m, dict) else m for m in model_context]
    if isinstance(model_context, dict):
        if not model_context:
            return None
        return dict(model_context)
    return None


def _build_assembled_section(
    assembled: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not assembled or not isinstance(assembled, dict):
        return None
    return dict(assembled)


# ═══════════════════════════════════════════════════════════════════════════
# Evidence
# ═══════════════════════════════════════════════════════════════════════════

def _build_evidence(
    inputs: dict[str, Any],
    presence: dict[str, str],
    section_statuses: dict[str, str],
) -> dict[str, Any]:
    """Top-level evidence for quick downstream consumption."""
    candidate = inputs.get("candidate")
    market = inputs.get("market")
    policy = inputs.get("policy")
    events = inputs.get("events")
    conflicts = inputs.get("conflicts")
    portfolio = inputs.get("portfolio")
    model_context = inputs.get("model_context")
    assembled = inputs.get("assembled")

    # Model context details
    model_count, model_types = _extract_model_details(model_context)

    # Events integration status — events are support context, not
    # wired into deterministic policy checks as of v1.1
    events_integration = (
        _COMPONENT_ROLES.get("events", "support_context_only")
        if presence.get("events") == "present"
        else None
    )

    # Assembled context degradation surfacing
    assembled_degraded = _extract_assembled_degraded(assembled)

    return {
        "candidate_symbol": _safe_get(candidate, "symbol"),
        "candidate_strategy": _safe_get(candidate, "strategy_family"),
        "candidate_direction": _safe_get(candidate, "direction"),
        "candidate_confidence": _safe_get(candidate, "confidence"),
        "market_status": _safe_get(market, "status"),
        "market_state": _safe_get(market, "market_state"),
        "market_confidence": _safe_get(market, "confidence"),
        "policy_decision": _safe_get(policy, "policy_decision"),
        "policy_severity": _safe_get(policy, "decision_severity"),
        "policy_size_guidance": _safe_get(policy, "size_guidance"),
        "event_risk_state": _safe_get(events, "event_risk_state"),
        "event_status": _safe_get(events, "status"),
        "events_integration_status": events_integration,
        "conflict_severity": _safe_get(conflicts, "conflict_severity"),
        "conflict_count": _safe_get(conflicts, "conflict_count"),
        "portfolio_status": _safe_get(portfolio, "status"),
        "portfolio_position_count": _safe_get(portfolio, "position_count"),
        "model_context_count": model_count,
        "model_context_types": model_types,
        "assembled_degraded_modules": assembled_degraded,
        "section_statuses": dict(section_statuses),
        "sections_present": sum(
            1 for v in presence.values() if v == "present"
        ),
        "sections_total": len(_ALL_KEYS),
    }


def _safe_get(d: Any, key: str) -> Any:
    if isinstance(d, dict):
        return d.get(key)
    return None


def _extract_model_details(
    model_context: dict | list | None,
) -> tuple[int, list[str]]:
    """Return (count, list_of_analysis_types) from model context."""
    if model_context is None:
        return 0, []
    if isinstance(model_context, dict):
        at = model_context.get("analysis_type")
        return 1, [at] if at else []
    if isinstance(model_context, list):
        types = []
        for m in model_context:
            if isinstance(m, dict):
                at = m.get("analysis_type")
                if at:
                    types.append(at)
        return len(model_context), types
    return 0, []


def _extract_assembled_degraded(assembled: dict | None) -> list[str]:
    """Surface degraded/failed module names from assembled context."""
    if not isinstance(assembled, dict):
        return []
    degraded = assembled.get("degraded_modules") or []
    failed = assembled.get("failed_modules") or []
    return sorted(set(
        str(m) for m in (list(degraded) + list(failed)) if m
    ))


# ═══════════════════════════════════════════════════════════════════════════
# Metadata
# ═══════════════════════════════════════════════════════════════════════════

def _build_metadata(
    inputs: dict[str, Any],
    presence: dict[str, str],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "decision_packet_version": _PACKET_VERSION,
        "generated_at": generated_at,
        "candidate_provided": presence.get("candidate") == "present",
        "market_provided": presence.get("market") == "present",
        "conflicts_provided": presence.get("conflicts") == "present",
        "portfolio_provided": presence.get("portfolio") == "present",
        "policy_provided": presence.get("policy") == "present",
        "events_provided": presence.get("events") == "present",
        "model_context_provided": presence.get("model_context") == "present",
        "assembled_provided": presence.get("assembled") == "present",
        "upstream_versions": _collect_upstream_versions(inputs),
        "component_roles": dict(_COMPONENT_ROLES),
    }


def _collect_upstream_versions(inputs: dict[str, Any]) -> dict[str, str]:
    """Collect version strings from upstream subsystem outputs."""
    versions: dict[str, str] = {}
    _try_version(versions, "market", inputs.get("market"), "composite_version")
    _try_version(
        versions, "conflicts", inputs.get("conflicts"),
        "metadata.detector_version",
    )
    _try_version(
        versions, "portfolio", inputs.get("portfolio"), "portfolio_version",
    )
    _try_version(versions, "policy", inputs.get("policy"), "policy_version")
    _try_version(
        versions, "events", inputs.get("events"), "event_context_version",
    )
    assembled = inputs.get("assembled")
    if isinstance(assembled, dict):
        v = assembled.get("context_version")
        if v:
            versions["assembled"] = str(v)
    return versions


def _try_version(
    versions: dict[str, str],
    key: str,
    section: Any,
    field_path: str,
) -> None:
    if not isinstance(section, dict):
        return
    parts = field_path.split(".")
    val = section
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return
    if val is not None:
        versions[key] = str(val)


# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════

def _build_summary(
    status: str,
    presence: dict[str, str],
    section_statuses: dict[str, str],
    inputs: dict[str, Any],
    warning_flags: list[str],
) -> str:
    parts: list[str] = []

    # Status opener
    if status == "complete":
        parts.append("Decision packet is complete.")
    elif status == "partial":
        missing = [
            _LABEL_MAP.get(k, k)
            for k in _ALL_KEYS
            if presence.get(k) == "missing"
        ]
        if missing:
            parts.append(
                "Decision packet is partial — missing: "
                + ", ".join(missing)
                + "."
            )
        else:
            parts.append("Decision packet is partial.")
    else:
        parts.append("Insufficient data to build decision packet.")

    # Candidate context
    candidate = inputs.get("candidate")
    if isinstance(candidate, dict):
        sym = candidate.get("symbol", "?")
        strat = candidate.get("strategy_family", "?")
        parts.append(f"Candidate: {sym} ({strat}).")

    # Market context
    market = inputs.get("market")
    if isinstance(market, dict):
        ms = market.get("market_state", "?")
        parts.append(f"Market state: {ms}.")

    # Policy context
    policy = inputs.get("policy")
    if isinstance(policy, dict):
        pd = policy.get("policy_decision", "?")
        parts.append(f"Policy decision: {pd}.")

    # Event context
    events = inputs.get("events")
    if isinstance(events, dict):
        ers = events.get("event_risk_state", "?")
        parts.append(f"Event risk: {ers}.")

    # Warning count
    if warning_flags:
        parts.append(f"{len(warning_flags)} warning(s).")

    return " ".join(parts)
