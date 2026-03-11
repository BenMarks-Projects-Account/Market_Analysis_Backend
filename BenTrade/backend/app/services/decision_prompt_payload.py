"""Final Decision Prompt Payload Builder v1.

Transforms a structured decision packet (from the Trade Decision
Orchestrator) into a compact, stable, model-ready payload suitable for
downstream higher-order decision calls.

This is a packaging / compression layer — it does NOT call an LLM,
make the final trade decision, replace policy logic, or replace the
orchestrator.  It extracts the most decision-relevant structured facts,
compresses noisy subsystem output into stable prompt blocks, and
surfaces warnings and incompleteness clearly.

Public API
----------
build_prompt_payload(
    *,
    decision_packet  = None,   # from build_decision_packet()
    # Optional fallback subsystem inputs when packet is partial/missing
    candidate   = None,
    market      = None,
    conflicts   = None,
    portfolio   = None,
    policy      = None,
    events      = None,
    model_context = None,
) -> dict

Output version: 1.0
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
_PAYLOAD_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Instruction block — stable, reusable scaffold
# ---------------------------------------------------------------------------
_INSTRUCTION_LINES: list[str] = [
    "You are reviewing a structured trade decision payload.",
    "Prioritise policy guardrails and structural risk over narrative tone.",
    "Respect missing or degraded inputs — do not invent context that is absent.",
    "Treat conflicts as uncertainty, not automatic rejection.",
    "Distinguish market context from portfolio fit.",
    "Avoid overconfidence when data quality is weak or sections are missing.",
    "If the payload status is 'partial' or 'insufficient_data', acknowledge "
    "the limitations explicitly before rendering a decision.",
    "Base your assessment on the structured evidence provided, not assumptions.",
]

_INSTRUCTION_BLOCK: dict[str, Any] = {
    "role": "decision_reviewer",
    "guidance": list(_INSTRUCTION_LINES),  # copy
    "version": _PAYLOAD_VERSION,
}


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def build_prompt_payload(
    *,
    decision_packet: dict[str, Any] | None = None,
    # Fallback subsystem inputs — used only when packet is missing a section
    candidate: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
    conflicts: dict[str, Any] | None = None,
    portfolio: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    events: dict[str, Any] | None = None,
    model_context: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a model-ready prompt payload from a decision packet.

    The builder reads from the *decision_packet* first.  If a section
    inside the packet is ``None`` but a direct fallback kwarg is provided,
    the fallback is used and recorded in ``metadata.fallbacks_used``.

    Parameters
    ----------
    decision_packet : dict | None
        Output of ``build_decision_packet()``.
    candidate, market, conflicts, portfolio, policy, events, model_context
        Optional direct subsystem outputs used as fallback when the
        decision packet is missing those sections.

    Returns
    -------
    dict
        Final decision prompt payload with the canonical shape.
    """
    now = datetime.now(timezone.utc).isoformat()

    packet = decision_packet if isinstance(decision_packet, dict) else {}
    fallbacks: dict[str, Any] = {
        "candidate": candidate,
        "market": market,
        "conflicts": conflicts,
        "portfolio": portfolio,
        "policy": policy,
        "events": events,
        "model_context": model_context,
    }

    # --- Resolve effective sections (packet-first, fallback second) --------
    resolved, fallbacks_used = _resolve_sections(packet, fallbacks)

    # --- Derive status -----------------------------------------------------
    packet_status = packet.get("status") if packet else None
    status = _derive_payload_status(packet_status, resolved)

    # --- Build blocks (compressed, model-friendly) -------------------------
    candidate_block = _compress_candidate(resolved.get("candidate"))
    market_block = _compress_market(resolved.get("market"))
    portfolio_block = _compress_portfolio(resolved.get("portfolio"))
    policy_block = _compress_policy(resolved.get("policy"))
    event_block = _compress_events(resolved.get("events"))
    conflict_block = _compress_conflicts(resolved.get("conflicts"))
    model_context_block = _compress_model_context(resolved.get("model_context"))
    quality_block = _build_quality_block(packet, resolved, status)
    summary_block = _build_summary_block(
        packet, status, candidate_block, market_block,
        policy_block, event_block,
    )

    # --- Warning flags (from packet + own) ---------------------------------
    warning_flags = _collect_warning_flags(packet, resolved, fallbacks_used)

    # --- Metadata ----------------------------------------------------------
    metadata = _build_metadata(packet, resolved, fallbacks_used, now)

    return {
        "payload_version": _PAYLOAD_VERSION,
        "generated_at": now,
        "status": status,
        "summary_block": summary_block,
        "candidate_block": candidate_block,
        "market_block": market_block,
        "portfolio_block": portfolio_block,
        "policy_block": policy_block,
        "event_block": event_block,
        "conflict_block": conflict_block,
        "model_context_block": model_context_block,
        "quality_block": quality_block,
        "instruction_block": dict(_INSTRUCTION_BLOCK),
        "warning_flags": warning_flags,
        "metadata": metadata,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Section resolution (packet-first, fallback second)
# ═══════════════════════════════════════════════════════════════════════════

_SECTION_KEYS = (
    "candidate", "market", "conflicts", "portfolio",
    "policy", "events", "model_context",
)


def _resolve_sections(
    packet: dict[str, Any],
    fallbacks: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Return resolved sections and list of fallback-filled keys."""
    resolved: dict[str, Any] = {}
    used: list[str] = []
    for key in _SECTION_KEYS:
        pkt_val = packet.get(key)
        if _is_present(pkt_val):
            resolved[key] = pkt_val
        elif _is_present(fallbacks.get(key)):
            resolved[key] = fallbacks[key]
            used.append(key)
        else:
            resolved[key] = None
    return resolved, used


def _is_present(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, dict) and not val:
        return False
    if isinstance(val, list) and not val:
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════════
# Payload-level status
# ═══════════════════════════════════════════════════════════════════════════

def _derive_payload_status(
    packet_status: str | None,
    resolved: dict[str, Any],
) -> str:
    """Derive payload status.

    If the decision packet already has a status, propagate it.
    Otherwise derive from resolved section availability:
      - candidate missing → insufficient_data
      - candidate+market+policy present → complete
      - otherwise → partial
    """
    if packet_status in ("complete", "partial", "insufficient_data"):
        return packet_status

    # No packet or unrecognised status — derive from resolved sections
    if not _is_present(resolved.get("candidate")):
        return "insufficient_data"
    required = ("candidate", "market", "policy")
    if all(_is_present(resolved.get(k)) for k in required):
        return "complete"
    return "partial"


# ═══════════════════════════════════════════════════════════════════════════
# Block compressors — extract decision-relevant fields, drop noise
# ═══════════════════════════════════════════════════════════════════════════

def _compress_candidate(section: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(section, dict) or not section:
        return None
    return {
        "symbol": section.get("symbol"),
        "strategy_family": section.get("strategy_family"),
        "setup_type": section.get("setup_type"),
        "direction": section.get("direction"),
        "time_horizon": section.get("time_horizon"),
        "confidence": section.get("confidence"),
        "setup_quality": section.get("setup_quality"),
        "thesis_summary": section.get("thesis_summary"),
        "entry_context": section.get("entry_context"),
        "risk_definition": section.get("risk_definition"),
        "reward_profile": section.get("reward_profile"),
        "key_metrics": section.get("candidate_metrics"),
        "risk_flags": section.get("risk_flags", []),
        "data_quality": section.get("data_quality"),
    }


def _compress_market(section: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(section, dict) or not section:
        return None
    return {
        "market_state": section.get("market_state"),
        "support_state": section.get("support_state"),
        "stability_state": section.get("stability_state"),
        "confidence": section.get("confidence"),
        "status": section.get("status"),
        "summary": section.get("summary"),
    }


def _compress_portfolio(section: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(section, dict) or not section:
        return None
    portfolio_summary = section.get("portfolio_summary")
    return {
        "status": section.get("status"),
        "position_count": section.get("position_count"),
        "underlying_count": section.get("underlying_count"),
        "directional_exposure": section.get("directional_exposure"),
        "capital_at_risk": section.get("capital_at_risk"),
        "risk_flags": section.get("risk_flags", []),
        "summary": (
            portfolio_summary.get("description")
            if isinstance(portfolio_summary, dict) else None
        ),
    }


def _compress_policy(section: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(section, dict) or not section:
        return None
    # Include top triggered checks (up to 5) for visibility
    triggered = section.get("triggered_checks") or []
    top_checks = [
        {
            "check_code": c.get("check_code"),
            "severity": c.get("severity"),
            "title": c.get("title"),
            "recommended_effect": c.get("recommended_effect"),
        }
        for c in triggered[:5]
        if isinstance(c, dict)
    ]
    return {
        "policy_decision": section.get("policy_decision"),
        "decision_severity": section.get("decision_severity"),
        "size_guidance": section.get("size_guidance"),
        "summary": section.get("summary"),
        "blocking_count": len(section.get("blocking_checks") or []),
        "caution_count": len(section.get("caution_checks") or []),
        "restrictive_count": len(section.get("restrictive_checks") or []),
        "top_checks": top_checks,
    }


def _compress_events(section: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(section, dict) or not section:
        return None
    # Surface nearest events (within_24h + within_3d, up to 5 total)
    windows = section.get("event_windows") or {}
    near = []
    for bucket in ("within_24h", "within_3d"):
        for evt in (windows.get(bucket) or []):
            if len(near) >= 5:
                break
            if isinstance(evt, dict):
                near.append({
                    "event_name": evt.get("event_name"),
                    "event_type": evt.get("event_type"),
                    "importance": evt.get("importance"),
                    "risk_window": evt.get("risk_window"),
                })
    overlap = section.get("candidate_event_overlap") or {}
    return {
        "event_risk_state": section.get("event_risk_state"),
        "status": section.get("status"),
        "summary": section.get("summary"),
        "risk_flags": section.get("risk_flags", []),
        "nearest_events": near,
        "candidate_overlap_count": (
            overlap.get("overlap_count") if isinstance(overlap, dict) else 0
        ),
    }


def _compress_conflicts(section: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(section, dict) or not section:
        return None
    return {
        "conflict_severity": section.get("conflict_severity"),
        "conflict_count": section.get("conflict_count"),
        "conflict_summary": section.get("conflict_summary"),
        "conflict_flags": section.get("conflict_flags", []),
    }


def _compress_model_context(
    section: dict[str, Any] | list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Compress one or many model-analysis results into a compact list."""
    if section is None:
        return None
    items: list[dict[str, Any]] = []
    raw_list = section if isinstance(section, list) else [section]
    for m in raw_list:
        if not isinstance(m, dict) or not m:
            continue
        items.append({
            "analysis_type": m.get("analysis_type"),
            "status": m.get("status"),
            "summary": m.get("summary"),
            "confidence": m.get("confidence"),
            "key_points": m.get("key_points", []),
            "risks": m.get("risks", []),
            "time_horizon": m.get("time_horizon"),
        })
    return items if items else None


# ═══════════════════════════════════════════════════════════════════════════
# Quality block
# ═══════════════════════════════════════════════════════════════════════════

def _build_quality_block(
    packet: dict[str, Any],
    resolved: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    # Prefer the packet's quality_overview if available
    pkt_qo = packet.get("quality_overview") if isinstance(packet, dict) else None
    if isinstance(pkt_qo, dict) and pkt_qo:
        block = {
            "decision_ready": pkt_qo.get("decision_ready"),
            "readiness_note": pkt_qo.get("readiness_note"),
            "coverage_ratio": pkt_qo.get("coverage_ratio"),
            "subsystems_present": pkt_qo.get("subsystems_present", []),
            "subsystems_missing": pkt_qo.get("subsystems_missing", []),
            "subsystems_degraded": pkt_qo.get("subsystems_degraded", []),
        }
        # Propagate confidence_assessment if present in upstream quality_overview
        if pkt_qo.get("confidence_assessment"):
            block["confidence_assessment"] = pkt_qo["confidence_assessment"]
        if pkt_qo.get("uncertainty_summary"):
            block["uncertainty_summary"] = pkt_qo["uncertainty_summary"]
        return block
    # Fallback: derive from resolved sections
    present = [k for k in _SECTION_KEYS if _is_present(resolved.get(k))]
    missing = [k for k in _SECTION_KEYS if not _is_present(resolved.get(k))]
    ready = status == "complete"
    note = (
        "All required blocks present." if ready
        else "Payload is incomplete — some sections missing."
    )
    return {
        "decision_ready": ready,
        "readiness_note": note,
        "coverage_ratio": round(len(present) / len(_SECTION_KEYS), 2) if _SECTION_KEYS else 0.0,
        "subsystems_present": sorted(present),
        "subsystems_missing": sorted(missing),
        "subsystems_degraded": [],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Summary block
# ═══════════════════════════════════════════════════════════════════════════

def _build_summary_block(
    packet: dict[str, Any],
    status: str,
    candidate_block: dict[str, Any] | None,
    market_block: dict[str, Any] | None,
    policy_block: dict[str, Any] | None,
    event_block: dict[str, Any] | None,
) -> str:
    """One-paragraph summary suitable for top-of-prompt framing."""
    # Start from packet summary if available
    pkt_summary = packet.get("summary") if isinstance(packet, dict) else None
    if isinstance(pkt_summary, str) and pkt_summary:
        return pkt_summary

    # Build from blocks
    parts: list[str] = []
    if status == "complete":
        parts.append("Decision payload is complete.")
    elif status == "partial":
        parts.append("Decision payload is partial — some context is missing.")
    else:
        parts.append("Insufficient data to build decision payload.")

    if isinstance(candidate_block, dict):
        sym = candidate_block.get("symbol", "?")
        strat = candidate_block.get("strategy_family", "?")
        parts.append(f"Candidate: {sym} ({strat}).")

    if isinstance(market_block, dict):
        ms = market_block.get("market_state", "?")
        parts.append(f"Market state: {ms}.")

    if isinstance(policy_block, dict):
        pd = policy_block.get("policy_decision", "?")
        parts.append(f"Policy decision: {pd}.")

    if isinstance(event_block, dict):
        ers = event_block.get("event_risk_state", "?")
        parts.append(f"Event risk: {ers}.")

    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Warning flags
# ═══════════════════════════════════════════════════════════════════════════

def _collect_warning_flags(
    packet: dict[str, Any],
    resolved: dict[str, Any],
    fallbacks_used: list[str],
) -> list[str]:
    flags: list[str] = []

    # Propagate packet-level warning flags
    pkt_flags = packet.get("warning_flags") if isinstance(packet, dict) else None
    if isinstance(pkt_flags, list):
        flags.extend(str(f) for f in pkt_flags)

    # Own flags for missing resolved sections
    for key in _SECTION_KEYS:
        tag = f"{key}_not_available"
        if not _is_present(resolved.get(key)) and tag not in flags:
            # avoid double-flagging if packet already flagged it
            pkt_equiv = f"{key}_not_provided"
            if pkt_equiv not in flags:
                flags.append(tag)

    # Fallback flags
    for key in fallbacks_used:
        flags.append(f"{key}_from_fallback")

    # Deduplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Metadata
# ═══════════════════════════════════════════════════════════════════════════

def _build_metadata(
    packet: dict[str, Any],
    resolved: dict[str, Any],
    fallbacks_used: list[str],
    generated_at: str,
) -> dict[str, Any]:
    pkt_meta = packet.get("metadata") if isinstance(packet, dict) else None
    pkt_version = (
        pkt_meta.get("decision_packet_version")
        if isinstance(pkt_meta, dict) else None
    )
    return {
        "payload_version": _PAYLOAD_VERSION,
        "generated_at": generated_at,
        "source_packet_version": pkt_version,
        "source_packet_status": packet.get("status") if isinstance(packet, dict) else None,
        "fallbacks_used": list(fallbacks_used),
        "sections_included": sorted(
            k for k in _SECTION_KEYS if _is_present(resolved.get(k))
        ),
        "sections_missing": sorted(
            k for k in _SECTION_KEYS if not _is_present(resolved.get(k))
        ),
    }
