"""Post-Trade Feedback Loop v1.1.

Captures and normalises the full context surrounding a trade decision so
that the system (and the user) can later review and compare decisions
against outcomes.

Role boundary
-------------
This module is **capture-only**.  It records what happened and what was
known at decision time.  It does NOT:

*  score correctness or attribution
*  adjust future weights or thresholds
*  run calibration or performance analysis

Those responsibilities belong to downstream consumers
(e.g. signal_attribution, disagreement_tracking).

A **feedback record** preserves:

*  the candidate / trade setup at decision time
*  the decision packet & response at decision time
*  market / portfolio / policy / event / conflict snapshots
*  what the user actually did  (taken / skipped / modified / exited)
*  optional execution snapshot  (fill data, if available)
*  optional outcome snapshot  (P&L, status, close reason)
*  optional trade_key for lifecycle correlation
*  review notes and warning flags

Public API
----------
build_feedback_record(...)
    Full builder: accepts all snapshots → normalised record.

update_feedback_execution(record, execution_snapshot)
    Append/update execution data on an existing record.

update_feedback_outcome(record, outcome_snapshot)
    Append/update outcome data and optionally close the record.

close_feedback_record(record, *, outcome_snapshot=None, review_notes=None)
    Mark a record as closed (trade concluded).

validate_feedback_record(record)
    Schema check → (ok, errors).

snapshot_from_decision_packet(packet)
    Extract compact decision-time snapshots from a full decision packet.

record_to_serializable(record)
    Return a JSON-safe copy of a feedback record.

record_from_serializable(data)
    Reconstruct a feedback record from serialized data (migration hook).

record_summary(record)
    Compact human-readable overview of a feedback record.

snapshot_coverage(record)
    Which snapshots are populated vs None.

Output version: 1.1
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

# ── Module role ─────────────────────────────────────────────────────────
# This module captures decision context — it does NOT score, calibrate,
# or adjust.  Downstream consumers own attribution and learning.
_MODULE_ROLE = "capture"

# ── Version lock ────────────────────────────────────────────────────────
_FEEDBACK_VERSION = "1.1"
_COMPATIBLE_VERSIONS = frozenset({"1.0", "1.1"})

# ── Valid enumerations ──────────────────────────────────────────────────
VALID_TRADE_ACTIONS = frozenset({
    "taken",
    "skipped",
    "modified",
    "exited",
    "unknown",
})

VALID_STATUSES = frozenset({
    "recorded",
    "partial",
    "closed",
    "invalid",
})

# ── Execution source / fill quality (honest provenance tagging) ─────────
VALID_EXECUTION_SOURCES = frozenset({
    "live_broker",      # Real broker fill (Tradier live account)
    "paper_sim",        # Paper/simulated fill
    "manual_entry",     # User-entered fill data
    "unknown",          # Source not specified
})

VALID_FILL_QUALITIES = frozenset({
    "confirmed",        # Broker-verified fill
    "estimated",        # Simulated or paper-mode fill
    "unverified",       # Manual entry, no verification
})

# ── Required top-level keys (for validation) ────────────────────────────
_REQUIRED_KEYS = frozenset({
    "feedback_version",
    "feedback_id",
    "recorded_at",
    "status",
    "trade_action",
})

# ── Snapshot key allow-lists (what we preserve from each upstream) ──────
_CANDIDATE_SNAPSHOT_KEYS = [
    "symbol", "underlying", "underlying_symbol", "spread_type",
    "strategy", "expiration", "dte", "short_strike", "long_strike",
    "short_put_strike", "long_put_strike", "short_call_strike",
    "long_call_strike", "net_credit", "width", "max_profit_per_share",
    "max_loss_per_share", "break_even", "return_on_risk",
    "pop_delta_approx", "expected_fill_price", "iv", "trade_quality_score",
    "composite_score", "confidence", "setup_quality",
]

_MARKET_SNAPSHOT_KEYS = [
    "overall_bias", "composite_score", "regime_label",
    "trend_label", "volatility_label", "macro_label",
    "confidence", "signal_quality", "warning_flags",
]

_PORTFOLIO_SNAPSHOT_KEYS = [
    "total_positions", "total_delta", "total_theta",
    "total_vega", "max_sector_concentration",
    "greeks_coverage", "sector_coverage", "event_coverage",
    "risk_flags", "portfolio_risk_level",
]

_POLICY_SNAPSHOT_KEYS = [
    "policy_decision", "severity", "checks_passed",
    "checks_failed", "total_checks", "pass_rate",
    "failed_check_names", "confidence_impact",
]

_EVENT_SNAPSHOT_KEYS = [
    "event_risk_state", "total_events", "high_impact_events",
    "events_in_window", "nearest_event_days",
    "calendar_risk_score", "recommendation",
]

_CONFLICT_SNAPSHOT_KEYS = [
    "has_conflicts", "conflict_count", "max_severity",
    "confidence_impact", "conflicts",
]

_RESPONSE_SNAPSHOT_KEYS = [
    "decision", "decision_label", "conviction",
    "market_alignment", "portfolio_fit", "policy_alignment",
    "event_risk", "size_guidance", "summary",
    "reasons_for", "reasons_against", "key_risks",
    "warning_flags", "status", "confidence_assessment",
]


# =====================================================================
#  Snapshot extraction helpers
# =====================================================================

def _extract_snapshot(
    source: dict[str, Any] | None,
    keys: list[str],
) -> dict[str, Any] | None:
    """Extract a compact snapshot from a source dict.

    Returns None if source is None/empty.  Only includes keys that
    exist in the source (no fabrication of missing fields).
    """
    if not source or not isinstance(source, dict):
        return None
    snap: dict[str, Any] = {}
    for k in keys:
        if k in source:
            val = source[k]
            # Deep-copy mutable values to freeze the snapshot
            if isinstance(val, (dict, list)):
                snap[k] = copy.deepcopy(val)
            else:
                snap[k] = val
    return snap if snap else None


def snapshot_from_decision_packet(
    packet: dict[str, Any] | None,
) -> dict[str, dict[str, Any] | None]:
    """Extract compact snapshots from a full trade-decision-orchestrator packet.

    Returns a dict with keys: candidate, market, portfolio, policy,
    events, conflicts — each a compact snapshot or None.
    """
    if not packet or not isinstance(packet, dict):
        return {
            "candidate": None,
            "market": None,
            "portfolio": None,
            "policy": None,
            "events": None,
            "conflicts": None,
        }
    return {
        "candidate": _extract_snapshot(packet.get("candidate"), _CANDIDATE_SNAPSHOT_KEYS),
        "market": _extract_snapshot(packet.get("market"), _MARKET_SNAPSHOT_KEYS),
        "portfolio": _extract_snapshot(packet.get("portfolio"), _PORTFOLIO_SNAPSHOT_KEYS),
        "policy": _extract_snapshot(packet.get("policy"), _POLICY_SNAPSHOT_KEYS),
        "events": _extract_snapshot(packet.get("events"), _EVENT_SNAPSHOT_KEYS),
        "conflicts": _extract_snapshot(packet.get("conflicts"), _CONFLICT_SNAPSHOT_KEYS),
    }


# =====================================================================
#  Execution snapshot normalisation
# =====================================================================

def normalise_execution_snapshot(
    execution: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Normalise an execution/fill snapshot.

    Accepts whatever execution data is available (fill price, quantity,
    broker order id, status, timestamps) and returns a clean dict.
    Does not fabricate missing fields.

    Provenance tagging (v1.1):
    - ``execution_source``: one of VALID_EXECUTION_SOURCES — tags where
      the fill data originated.  Defaults to ``"unknown"`` if absent.
    - ``fill_quality``: one of VALID_FILL_QUALITIES — tags verification
      level.  Defaults to ``"unverified"`` if absent.
    """
    if not execution or not isinstance(execution, dict):
        return None

    snap: dict[str, Any] = {}
    _copy_if = [
        "broker_order_id", "broker", "order_status",
        "fill_price", "fill_quantity", "fill_timestamp",
        "limit_price", "estimated_max_profit", "estimated_max_loss",
        "account_mode", "mode", "strategy", "underlying",
        "legs", "quantity", "price_effect", "time_in_force",
        "slippage", "actual_credit", "actual_debit",
        "submitted_at", "filled_at",
    ]
    for k in _copy_if:
        if k in execution:
            val = execution[k]
            snap[k] = copy.deepcopy(val) if isinstance(val, (dict, list)) else val

    # Preserve any extra keys the caller included (future-proof)
    for k, v in execution.items():
        if k not in snap:
            snap[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v

    # ── Provenance tagging (v1.1) ───────────────────────────────────
    src = str(snap.get("execution_source", "")).lower().strip()
    snap["execution_source"] = src if src in VALID_EXECUTION_SOURCES else "unknown"

    qual = str(snap.get("fill_quality", "")).lower().strip()
    snap["fill_quality"] = qual if qual in VALID_FILL_QUALITIES else "unverified"

    return snap if snap else None


# =====================================================================
#  Outcome snapshot normalisation
# =====================================================================

def normalise_outcome_snapshot(
    outcome: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Normalise an outcome/result snapshot.

    Accepts whatever outcome data is available (realized P&L, exit
    reason, close timestamp, status at close) and returns a clean dict.
    Does not fabricate missing fields.
    """
    if not outcome or not isinstance(outcome, dict):
        return None

    snap: dict[str, Any] = {}
    _copy_if = [
        "realized_pnl", "unrealized_pnl", "unrealized_pnl_pct",
        "exit_reason", "exit_method", "close_timestamp",
        "close_date", "hold_duration_days", "final_status",
        "market_value_at_close", "cost_basis",
        "outcome_vs_expectation", "notes",
    ]
    for k in _copy_if:
        if k in outcome:
            val = outcome[k]
            snap[k] = copy.deepcopy(val) if isinstance(val, (dict, list)) else val

    for k, v in outcome.items():
        if k not in snap:
            snap[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v

    return snap if snap else None


# =====================================================================
#  Decision snapshot normalisation
# =====================================================================

def _normalise_decision_snapshot(
    decision_packet: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Compact snapshot of the orchestrator decision packet itself.

    Preserves packet-level metadata (status, coverage, warnings) but
    NOT the full nested subsystem blobs — those go into their own
    snapshot slots.
    """
    if not decision_packet or not isinstance(decision_packet, dict):
        return None
    keys = [
        "decision_packet_version", "generated_at", "status", "summary",
    ]
    snap = _extract_snapshot(decision_packet, keys)
    # Include quality_overview compactly
    qo = decision_packet.get("quality_overview")
    if isinstance(qo, dict):
        snap = snap or {}
        snap["quality_overview"] = {
            "packet_status": qo.get("packet_status"),
            "decision_ready": qo.get("decision_ready"),
            "coverage_ratio": qo.get("coverage_ratio"),
            "confidence_assessment": qo.get("confidence_assessment"),
        }
    wf = decision_packet.get("warning_flags")
    if isinstance(wf, list) and wf:
        snap = snap or {}
        snap["warning_flags"] = list(wf)
    return snap


# =====================================================================
#  build_feedback_record
# =====================================================================

def build_feedback_record(
    *,
    trade_action: str = "unknown",
    # Decision-time context
    decision_packet: dict[str, Any] | None = None,
    decision_response: dict[str, Any] | None = None,
    # Individual snapshots (override packet-derived if provided)
    candidate_snapshot: dict[str, Any] | None = None,
    market_snapshot: dict[str, Any] | None = None,
    portfolio_snapshot: dict[str, Any] | None = None,
    policy_snapshot: dict[str, Any] | None = None,
    event_snapshot: dict[str, Any] | None = None,
    conflict_snapshot: dict[str, Any] | None = None,
    # Post-decision
    execution_snapshot: dict[str, Any] | None = None,
    outcome_snapshot: dict[str, Any] | None = None,
    # Lifecycle correlation (v1.1)
    trade_key: str | None = None,
    # Annotations
    review_notes: list[str] | None = None,
    warning_flags: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    source: str = "",
) -> dict[str, Any]:
    """Build a normalised post-trade feedback record.

    Parameters
    ----------
    trade_action : str
        One of VALID_TRADE_ACTIONS: taken / skipped / modified / exited / unknown.
    decision_packet : dict | None
        Full decision packet from trade_decision_orchestrator.
        Subsystem snapshots are extracted automatically.
    decision_response : dict | None
        Final decision response from decision_response_contract.
    candidate_snapshot … conflict_snapshot : dict | None
        Individual snapshots.  If both a decision_packet AND individual
        snapshots are provided, the individual snapshots take priority
        (they are assumed to be more specific / curated).
    execution_snapshot : dict | None
        Fill / order data.  May be None for skipped trades.
    outcome_snapshot : dict | None
        Realized / unrealized result data.  May be None.
    trade_key : str | None
        Lifecycle correlation key (e.g. "SPY|2026-03-20|put_credit_spread|510|505|10").
        Links this feedback record to trade_lifecycle_service events.
    review_notes : list[str] | None
        User or system notes for later review.
    warning_flags : list[str] | None
        Aggregated warnings.
    evidence : dict | None
        Supporting evidence blob.
    metadata : dict | None
        Additional metadata.
    source : str
        Origin tag (e.g. "scanner", "manual", "api").

    Returns
    -------
    dict — normalised feedback record conforming to _FEEDBACK_VERSION.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # Normalise trade_action
    action = str(trade_action).lower().strip() if trade_action else "unknown"
    if action not in VALID_TRADE_ACTIONS:
        action = "unknown"

    # Extract packet-derived snapshots
    pkt_snaps = snapshot_from_decision_packet(decision_packet)

    # Resolve each snapshot: explicit > packet-derived
    cand = _extract_snapshot(candidate_snapshot, _CANDIDATE_SNAPSHOT_KEYS) or pkt_snaps["candidate"]
    mkt = _extract_snapshot(market_snapshot, _MARKET_SNAPSHOT_KEYS) or pkt_snaps["market"]
    port = _extract_snapshot(portfolio_snapshot, _PORTFOLIO_SNAPSHOT_KEYS) or pkt_snaps["portfolio"]
    pol = _extract_snapshot(policy_snapshot, _POLICY_SNAPSHOT_KEYS) or pkt_snaps["policy"]
    evt = _extract_snapshot(event_snapshot, _EVENT_SNAPSHOT_KEYS) or pkt_snaps["events"]
    conf = _extract_snapshot(conflict_snapshot, _CONFLICT_SNAPSHOT_KEYS) or pkt_snaps["conflicts"]

    dec_snap = _normalise_decision_snapshot(decision_packet)
    resp_snap = _extract_snapshot(decision_response, _RESPONSE_SNAPSHOT_KEYS)
    exec_snap = normalise_execution_snapshot(execution_snapshot)
    out_snap = normalise_outcome_snapshot(outcome_snapshot)

    # Collect warning flags
    wf: list[str] = list(warning_flags) if warning_flags else []
    _check_missing_context(
        wf, action, cand, mkt, dec_snap, resp_snap, exec_snap, out_snap,
    )

    # Derive status
    status = _derive_status(action, cand, dec_snap, resp_snap, exec_snap, out_snap)

    # Generate deterministic feedback_id
    feedback_id = _generate_feedback_id(now_iso, action, cand, source, trade_key)

    # Normalise trade_key
    tk = str(trade_key).strip() if trade_key else None

    # Build metadata
    base_meta: dict[str, Any] = {
        "feedback_version": _FEEDBACK_VERSION,
        "generated_at": now_iso,
        "source": str(source) if source else "",
    }
    if metadata and isinstance(metadata, dict):
        base_meta.update(metadata)

    return {
        "feedback_version": _FEEDBACK_VERSION,
        "feedback_id": feedback_id,
        "recorded_at": now_iso,
        "status": status,
        "trade_action": action,
        "trade_key": tk,
        "decision_snapshot": dec_snap,
        "candidate_snapshot": cand,
        "market_snapshot": mkt,
        "portfolio_snapshot": port,
        "policy_snapshot": pol,
        "event_snapshot": evt,
        "conflict_snapshot": conf,
        "response_snapshot": resp_snap,
        "execution_snapshot": exec_snap,
        "outcome_snapshot": out_snap,
        "review_notes": _safe_str_list(review_notes),
        "warning_flags": wf,
        "evidence": dict(evidence) if isinstance(evidence, dict) else {},
        "metadata": base_meta,
    }


# =====================================================================
#  update_feedback_execution
# =====================================================================

def update_feedback_execution(
    record: dict[str, Any],
    execution_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Update an existing feedback record with execution data.

    Returns a new dict (does not mutate input).
    """
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    out = copy.deepcopy(record)
    out["execution_snapshot"] = normalise_execution_snapshot(execution_snapshot)
    out["metadata"] = out.get("metadata") or {}
    out["metadata"]["execution_updated_at"] = datetime.now(timezone.utc).isoformat()
    # Re-derive status
    out["status"] = _derive_status(
        out.get("trade_action", "unknown"),
        out.get("candidate_snapshot"),
        out.get("decision_snapshot"),
        out.get("response_snapshot"),
        out.get("execution_snapshot"),
        out.get("outcome_snapshot"),
    )
    return out


# =====================================================================
#  update_feedback_outcome
# =====================================================================

def update_feedback_outcome(
    record: dict[str, Any],
    outcome_snapshot: dict[str, Any],
    *,
    close: bool = False,
) -> dict[str, Any]:
    """Update an existing feedback record with outcome data.

    If close=True, also sets status to 'closed'.
    Returns a new dict (does not mutate input).
    """
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    out = copy.deepcopy(record)
    out["outcome_snapshot"] = normalise_outcome_snapshot(outcome_snapshot)
    out["metadata"] = out.get("metadata") or {}
    out["metadata"]["outcome_updated_at"] = datetime.now(timezone.utc).isoformat()
    if close:
        out["status"] = "closed"
    else:
        out["status"] = _derive_status(
            out.get("trade_action", "unknown"),
            out.get("candidate_snapshot"),
            out.get("decision_snapshot"),
            out.get("response_snapshot"),
            out.get("execution_snapshot"),
            out.get("outcome_snapshot"),
        )
    return out


# =====================================================================
#  close_feedback_record
# =====================================================================

def close_feedback_record(
    record: dict[str, Any],
    *,
    outcome_snapshot: dict[str, Any] | None = None,
    review_notes: list[str] | None = None,
) -> dict[str, Any]:
    """Mark a feedback record as closed.

    Optionally appends final outcome and review notes.
    Returns a new dict (does not mutate input).
    """
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    out = copy.deepcopy(record)
    if outcome_snapshot:
        out["outcome_snapshot"] = normalise_outcome_snapshot(outcome_snapshot)
    if review_notes:
        existing = out.get("review_notes") or []
        out["review_notes"] = existing + _safe_str_list(review_notes)
    out["status"] = "closed"
    out["metadata"] = out.get("metadata") or {}
    out["metadata"]["closed_at"] = datetime.now(timezone.utc).isoformat()
    return out


# =====================================================================
#  validate_feedback_record
# =====================================================================

def validate_feedback_record(
    record: Any,
) -> tuple[bool, list[str]]:
    """Validate a feedback record against the v1 schema.

    Returns (ok: bool, errors: list[str]).
    """
    errors: list[str] = []

    if not isinstance(record, dict):
        return False, ["record must be a dict"]

    # Required keys
    missing = _REQUIRED_KEYS - set(record.keys())
    if missing:
        errors.append(f"missing required keys: {sorted(missing)}")

    # trade_action validity
    action = record.get("trade_action")
    if action and action not in VALID_TRADE_ACTIONS:
        errors.append(f"invalid trade_action: {action}")

    # status validity
    status = record.get("status")
    if status and status not in VALID_STATUSES:
        errors.append(f"invalid status: {status}")

    # version check (accept any compatible version)
    ver = record.get("feedback_version")
    if ver not in _COMPATIBLE_VERSIONS:
        errors.append(f"unexpected feedback_version: {ver} (expected one of {sorted(_COMPATIBLE_VERSIONS)})")

    # feedback_id presence
    fid = record.get("feedback_id")
    if not fid or not isinstance(fid, str):
        errors.append("feedback_id must be a non-empty string")

    # trade_key type check (optional, but must be str or None if present)
    tk = record.get("trade_key")
    if tk is not None and not isinstance(tk, str):
        errors.append(f"trade_key must be str or None, got {type(tk).__name__}")

    # Snapshot type checks (if present, must be dict or None)
    for snap_key in [
        "decision_snapshot", "candidate_snapshot", "market_snapshot",
        "portfolio_snapshot", "policy_snapshot", "event_snapshot",
        "conflict_snapshot", "response_snapshot", "execution_snapshot",
        "outcome_snapshot",
    ]:
        val = record.get(snap_key)
        if val is not None and not isinstance(val, dict):
            errors.append(f"{snap_key} must be dict or None, got {type(val).__name__}")

    # Execution provenance checks (if execution_snapshot present)
    exec_snap = record.get("execution_snapshot")
    if isinstance(exec_snap, dict):
        esrc = exec_snap.get("execution_source")
        if esrc is not None and esrc not in VALID_EXECUTION_SOURCES:
            errors.append(f"invalid execution_source: {esrc}")
        fq = exec_snap.get("fill_quality")
        if fq is not None and fq not in VALID_FILL_QUALITIES:
            errors.append(f"invalid fill_quality: {fq}")

    # List-type checks
    for list_key in ["review_notes", "warning_flags"]:
        val = record.get(list_key)
        if val is not None and not isinstance(val, list):
            errors.append(f"{list_key} must be list or None, got {type(val).__name__}")

    return len(errors) == 0, errors


# =====================================================================
#  Internal helpers
# =====================================================================

def _derive_status(
    action: str,
    candidate: dict | None,
    decision: dict | None,
    response: dict | None,
    execution: dict | None,
    outcome: dict | None,
) -> str:
    """Derive feedback record status from available data.

    Rules:
    - If outcome is present and action in (taken, exited, modified) → closed
    - If candidate or decision present and action is clear → recorded
    - If very minimal data → partial
    - Fallback → partial
    """
    has_candidate = candidate is not None and bool(candidate)
    has_decision = decision is not None and bool(decision)
    has_response = response is not None and bool(response)
    has_execution = execution is not None and bool(execution)
    has_outcome = outcome is not None and bool(outcome)

    # Closed: outcome present for a trade that was acted on
    if has_outcome and action in ("taken", "exited", "modified"):
        return "closed"

    # Recorded: at least candidate or decision context present
    if has_candidate or has_decision or has_response:
        return "recorded"

    # Partial: something exists but minimal
    return "partial"


def _check_missing_context(
    wf: list[str],
    action: str,
    candidate: dict | None,
    market: dict | None,
    decision: dict | None,
    response: dict | None,
    execution: dict | None,
    outcome: dict | None,
) -> None:
    """Append warning flags for missing context areas."""
    if not candidate:
        wf.append("missing_candidate_snapshot")
    if not decision and not response:
        wf.append("missing_decision_context")
    if not market:
        wf.append("missing_market_snapshot")
    if action == "taken" and not execution:
        wf.append("taken_without_execution_data")
    if action in ("exited", "taken") and not outcome:
        wf.append("no_outcome_data")


def _generate_feedback_id(
    timestamp: str,
    action: str,
    candidate: dict | None,
    source: str,
    trade_key: str | None = None,
) -> str:
    """Generate a deterministic feedback ID.

    Uses a hash of timestamp + action + symbol + source + trade_key
    to avoid collisions while remaining reproducible.
    """
    symbol = ""
    if candidate and isinstance(candidate, dict):
        symbol = str(candidate.get("symbol") or candidate.get("underlying") or "")
    tk = str(trade_key) if trade_key else ""
    raw = f"{timestamp}|{action}|{symbol}|{source}|{tk}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"fb-{h}"


def _safe_str_list(val: Any) -> list[str]:
    """Coerce to list[str], never None."""
    if not val:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x is not None]
    return []


# =====================================================================
#  Serialization helpers (persistence-readiness, v1.1)
# =====================================================================

def record_to_serializable(record: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe deep copy of a feedback record.

    All values produced by build_feedback_record() are already JSON-safe
    (strings, numbers, bools, None, lists, dicts).  This function exists
    as a formal contract: callers can rely on the output being safe to
    pass to ``json.dumps()`` without custom encoders.

    Also serves as the future hook for any pre-serialization transforms
    (e.g. datetime object → ISO string) if upstream data changes.
    """
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    out = copy.deepcopy(record)
    # Round-trip through json to verify safety (catches stray objects)
    json.dumps(out, default=str)
    return out


def record_from_serializable(data: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct a feedback record from serialized (JSON-parsed) data.

    Currently identity — returns a deep copy.  Exists as the migration
    hook: future versions can detect ``feedback_version`` and upgrade
    old records to the current schema.
    """
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    out = copy.deepcopy(data)
    ver = out.get("feedback_version")
    if ver and ver not in _COMPATIBLE_VERSIONS:
        raise ValueError(
            f"unsupported feedback_version: {ver} "
            f"(compatible: {sorted(_COMPATIBLE_VERSIONS)})"
        )
    # v1.0 → v1.1 migration: add trade_key if missing
    if "trade_key" not in out:
        out["trade_key"] = None
    return out


# =====================================================================
#  Inspectability helpers (v1.1)
# =====================================================================

_SNAPSHOT_KEYS = [
    "decision_snapshot", "candidate_snapshot", "market_snapshot",
    "portfolio_snapshot", "policy_snapshot", "event_snapshot",
    "conflict_snapshot", "response_snapshot",
    "execution_snapshot", "outcome_snapshot",
]


def snapshot_coverage(record: dict[str, Any]) -> dict[str, bool]:
    """Return which snapshot slots are populated (non-None, non-empty).

    Useful for quick inspectability — shows what context was captured.
    """
    if not isinstance(record, dict):
        return {}
    return {
        k: record.get(k) is not None and bool(record.get(k))
        for k in _SNAPSHOT_KEYS
    }


def record_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Return a compact human-readable overview of a feedback record.

    Includes: version, id, status, trade_action, trade_key, symbol,
    key timestamps, snapshot coverage count, warning count.
    """
    if not isinstance(record, dict):
        return {"error": "not a dict"}

    cand = record.get("candidate_snapshot") or {}
    meta = record.get("metadata") or {}
    cov = snapshot_coverage(record)
    wf = record.get("warning_flags") or []

    return {
        "feedback_version": record.get("feedback_version"),
        "feedback_id": record.get("feedback_id"),
        "status": record.get("status"),
        "trade_action": record.get("trade_action"),
        "trade_key": record.get("trade_key"),
        "symbol": cand.get("symbol") or cand.get("underlying"),
        "recorded_at": record.get("recorded_at"),
        "source": meta.get("source"),
        "snapshots_present": sum(1 for v in cov.values() if v),
        "snapshots_total": len(cov),
        "warning_count": len(wf),
        "timestamps": {
            "generated_at": meta.get("generated_at"),
            "execution_updated_at": meta.get("execution_updated_at"),
            "outcome_updated_at": meta.get("outcome_updated_at"),
            "closed_at": meta.get("closed_at"),
        },
    }
