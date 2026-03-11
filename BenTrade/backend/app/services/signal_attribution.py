"""Signal Attribution and Regime Calibration v1.1.

Consumes closed feedback records (from feedback_loop.py) and produces
structured calibration outputs showing which signals, strategies,
policy flags, conflict patterns, and event states were associated with
stronger or weaker outcomes under different market regimes.

Role boundary
-------------
This module is **summary / review only**.  It produces descriptive,
retrospective statistics.  It does NOT:

*  automate policy changes or threshold tuning
*  retrain models or adjust upstream weights
*  override decisions or inject live feedback loops
*  mutate any upstream state

Downstream consumers own any action taken on these summaries.

Public API
----------
build_calibration_report(records, *, low_sample_threshold=5)
    Main entry point.  Accepts a list of feedback records, returns a
    full calibration report dict.

classify_outcome(record)
    Classify a single feedback record → "win" / "loss" / "breakeven" / "unknown".

validate_calibration_report(report)
    Schema check → (ok, errors).

report_summary(report)
    Compact overview of a calibration report for UI/logging.

Output version: 1.1
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

# ── Module role ─────────────────────────────────────────────────────────
# This module produces descriptive retrospective summaries.  It does NOT
# adjust weights, retune thresholds, or override decisions.
_MODULE_ROLE = "summary"

# ── Version lock ────────────────────────────────────────────────────────
_CALIBRATION_VERSION = "1.1"
_COMPATIBLE_VERSIONS = frozenset({"1.0", "1.1"})

# ── Low-sample default ──────────────────────────────────────────────────
_DEFAULT_LOW_SAMPLE_THRESHOLD = 5

# ── Outcome classification values ───────────────────────────────────────
VALID_OUTCOME_CLASSIFICATIONS = frozenset({"win", "loss", "breakeven", "unknown"})

# ── Required top-level keys (for validation) ────────────────────────────
_REQUIRED_REPORT_KEYS = frozenset({
    "calibration_version",
    "generated_at",
    "status",
    "sample_size",
    "summary",
    "regime_calibration",
    "signal_attribution",
    "strategy_attribution",
    "policy_attribution",
    "conflict_attribution",
    "event_attribution",
    "conviction_attribution",
    "alignment_attribution",
    "warning_flags",
    "metadata",
})


# =====================================================================
#  Outcome classification
# =====================================================================

def classify_outcome(record: dict[str, Any]) -> str:
    """Classify a feedback record outcome.

    Returns one of VALID_OUTCOME_CLASSIFICATIONS:

    Classification rules (deterministic, in order):
    1. If outcome_snapshot is missing or not a dict → ``"unknown"``
    2. If outcome_snapshot.realized_pnl is missing or non-numeric → ``"unknown"``
    3. If realized_pnl > 0 → ``"win"``
    4. If realized_pnl == 0.0 → ``"breakeven"``
       (Explicitly separated from loss — a breakeven trade is neither
       positive nor negative.  Grouping it with loss would overstate
       losing patterns in summary data.)
    5. If realized_pnl < 0 → ``"loss"``

    Open/incomplete outcomes (no realized_pnl) are ``"unknown"`` and
    are excluded from win-rate denominators.  This prevents blurring
    open positions with closed realized results.

    Input fields: outcome_snapshot.realized_pnl
    """
    outcome = record.get("outcome_snapshot")
    if not isinstance(outcome, dict):
        return "unknown"
    pnl = outcome.get("realized_pnl")
    if pnl is None or not isinstance(pnl, (int, float)):
        return "unknown"
    if pnl > 0:
        return "win"
    if pnl == 0.0:
        return "breakeven"
    return "loss"


# =====================================================================
#  Internal helpers – grouping & aggregation
# =====================================================================

def _safe_get(record: dict, snapshot_key: str, field: str, default: Any = None) -> Any:
    """Safely extract nested snapshot field from a feedback record."""
    snap = record.get(snapshot_key)
    if not isinstance(snap, dict):
        return default
    return snap.get(field, default)


def _compute_stats(
    outcomes: list[dict[str, Any]],
    low_sample_threshold: int,
) -> dict[str, Any]:
    """Compute win/loss/breakeven/pnl stats for a group of classified outcomes.

    Each entry in ``outcomes`` must have keys: "classification", "pnl".

    Derived fields and formulas:
    - win_count = count where classification == "win"
    - loss_count = count where classification == "loss"
    - breakeven_count = count where classification == "breakeven"
    - unknown_count = count where classification == "unknown"
    - decided_count = win_count + loss_count (breakeven excluded from win-rate)
    - win_rate = win_count / decided_count if decided_count > 0
    - avg_pnl = mean(pnl values) where pnl is not None
    - median_pnl = median(pnl values) where pnl is not None
    - total_pnl = sum(pnl values) where pnl is not None
    - confidence_state:
        "insufficient" if decided_count == 0
        "low" if decided_count < low_sample_threshold
        "adequate" if decided_count >= low_sample_threshold
    """
    wins = sum(1 for o in outcomes if o["classification"] == "win")
    losses = sum(1 for o in outcomes if o["classification"] == "loss")
    breakevens = sum(1 for o in outcomes if o["classification"] == "breakeven")
    unknowns = sum(1 for o in outcomes if o["classification"] == "unknown")
    pnl_values = [o["pnl"] for o in outcomes if o["pnl"] is not None]

    decided = wins + losses
    win_rate = (wins / decided) if decided > 0 else None
    avg_pnl = statistics.mean(pnl_values) if pnl_values else None
    median_pnl = statistics.median(pnl_values) if pnl_values else None
    total_pnl = sum(pnl_values) if pnl_values else None

    sample = len(outcomes)
    low_sample = sample < low_sample_threshold

    # Confidence state based on decided outcomes, not raw sample size
    if decided == 0:
        confidence_state = "insufficient"
    elif decided < low_sample_threshold:
        confidence_state = "low"
    else:
        confidence_state = "adequate"

    return {
        "sample_count": sample,
        "win_count": wins,
        "loss_count": losses,
        "breakeven_count": breakevens,
        "unknown_count": unknowns,
        "decided_count": decided,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "avg_pnl": round(avg_pnl, 4) if avg_pnl is not None else None,
        "median_pnl": round(median_pnl, 4) if median_pnl is not None else None,
        "total_pnl": round(total_pnl, 4) if total_pnl is not None else None,
        "low_sample_warning": low_sample,
        "confidence_state": confidence_state,
    }


def _classify_record(record: dict[str, Any]) -> dict[str, Any]:
    """Classify and extract pnl from a single record."""
    classification = classify_outcome(record)
    outcome = record.get("outcome_snapshot")
    pnl = None
    if isinstance(outcome, dict):
        raw = outcome.get("realized_pnl")
        if isinstance(raw, (int, float)):
            pnl = float(raw)
    return {"classification": classification, "pnl": pnl}


def _build_group_note(stats: dict[str, Any]) -> str:
    """Build a human-readable note string for a group."""
    parts: list[str] = []
    n = stats["sample_count"]
    if stats["low_sample_warning"]:
        parts.append(f"Low sample size ({n})")
    wr = stats["win_rate"]
    if wr is not None:
        parts.append(f"Win rate {wr:.1%}")
    avg = stats["avg_pnl"]
    if avg is not None:
        parts.append(f"Avg P&L ${avg:+.2f}")
    be = stats.get("breakeven_count", 0)
    if be > 0:
        parts.append(f"{be} breakeven")
    return "; ".join(parts) if parts else "No outcome data"


# =====================================================================
#  Regime calibration
# =====================================================================

def _build_regime_calibration(
    records: list[dict[str, Any]],
    low_sample_threshold: int,
) -> list[dict[str, Any]]:
    """Group by (regime_label, overall_bias, volatility_label) → stats.

    Input fields:
    - market_snapshot.regime_label
    - market_snapshot.overall_bias
    - market_snapshot.volatility_label
    - outcome_snapshot.realized_pnl
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for rec in records:
        regime = _safe_get(rec, "market_snapshot", "regime_label", "unknown")
        bias = _safe_get(rec, "market_snapshot", "overall_bias", "unknown")
        vol = _safe_get(rec, "market_snapshot", "volatility_label", "unknown")
        key = (str(regime), str(bias), str(vol))
        groups[key].append(_classify_record(rec))

    results: list[dict[str, Any]] = []
    for (regime, bias, vol), outcomes in sorted(groups.items()):
        stats = _compute_stats(outcomes, low_sample_threshold)
        results.append({
            "regime_label": regime,
            "overall_bias": bias,
            "volatility_label": vol,
            **stats,
            "notes": _build_group_note(stats),
        })

    # Sort by sample_count descending
    results.sort(key=lambda x: x["sample_count"], reverse=True)
    return results


# =====================================================================
#  Signal attribution  (individual market signals)
# =====================================================================

def _build_signal_attribution(
    records: list[dict[str, Any]],
    low_sample_threshold: int,
) -> list[dict[str, Any]]:
    """Group by individual market signal values → stats.

    Signals extracted:
    - market_snapshot.overall_bias
    - market_snapshot.trend_label
    - market_snapshot.volatility_label
    - market_snapshot.macro_label
    - market_snapshot.regime_label
    - market_snapshot.signal_quality

    Input fields: market_snapshot.{signal}, outcome_snapshot.realized_pnl
    """
    signal_fields = [
        "overall_bias", "trend_label", "volatility_label",
        "macro_label", "regime_label", "signal_quality",
    ]
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rec in records:
        classified = _classify_record(rec)
        for field in signal_fields:
            val = _safe_get(rec, "market_snapshot", field)
            if val is not None:
                groups[(field, str(val))].append(classified)

    results: list[dict[str, Any]] = []
    for (signal_key, signal_value), outcomes in sorted(groups.items()):
        stats = _compute_stats(outcomes, low_sample_threshold)
        results.append({
            "signal_key": signal_key,
            "signal_value": signal_value,
            **stats,
            "notes": _build_group_note(stats),
        })

    results.sort(key=lambda x: x["sample_count"], reverse=True)
    return results


# =====================================================================
#  Strategy attribution
# =====================================================================

def _build_strategy_attribution(
    records: list[dict[str, Any]],
    low_sample_threshold: int,
) -> list[dict[str, Any]]:
    """Group by (strategy, spread_type) → stats.

    Input fields:
    - candidate_snapshot.strategy
    - candidate_snapshot.spread_type
    - outcome_snapshot.realized_pnl
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rec in records:
        strategy = _safe_get(rec, "candidate_snapshot", "strategy", "unknown")
        spread_type = _safe_get(rec, "candidate_snapshot", "spread_type", "unknown")
        key = (str(strategy), str(spread_type))
        groups[key].append(_classify_record(rec))

    results: list[dict[str, Any]] = []
    for (strategy, spread_type), outcomes in sorted(groups.items()):
        stats = _compute_stats(outcomes, low_sample_threshold)
        results.append({
            "strategy": strategy,
            "spread_type": spread_type,
            **stats,
            "notes": _build_group_note(stats),
        })

    results.sort(key=lambda x: x["sample_count"], reverse=True)
    return results


# =====================================================================
#  Policy attribution
# =====================================================================

def _build_policy_attribution(
    records: list[dict[str, Any]],
    low_sample_threshold: int,
) -> list[dict[str, Any]]:
    """Group by (policy_decision, failed_check_names_key) → stats.

    Input fields:
    - policy_snapshot.policy_decision
    - policy_snapshot.failed_check_names (list → sorted comma-joined key)
    - outcome_snapshot.realized_pnl
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rec in records:
        decision = _safe_get(rec, "policy_snapshot", "policy_decision", "unknown")
        failed_raw = _safe_get(rec, "policy_snapshot", "failed_check_names")
        if isinstance(failed_raw, list):
            failed_key = ",".join(sorted(str(f) for f in failed_raw)) if failed_raw else "none"
        else:
            failed_key = "none"
        key = (str(decision), failed_key)
        groups[key].append(_classify_record(rec))

    results: list[dict[str, Any]] = []
    for (decision, failed_key), outcomes in sorted(groups.items()):
        stats = _compute_stats(outcomes, low_sample_threshold)
        failed_list = failed_key.split(",") if failed_key != "none" else []
        results.append({
            "policy_decision": decision,
            "failed_checks": failed_list,
            **stats,
            "notes": _build_group_note(stats),
        })

    results.sort(key=lambda x: x["sample_count"], reverse=True)
    return results


# =====================================================================
#  Conflict attribution
# =====================================================================

def _build_conflict_attribution(
    records: list[dict[str, Any]],
    low_sample_threshold: int,
) -> list[dict[str, Any]]:
    """Group by (has_conflicts, max_severity) → stats.

    Input fields:
    - conflict_snapshot.has_conflicts
    - conflict_snapshot.max_severity
    - outcome_snapshot.realized_pnl
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rec in records:
        has = _safe_get(rec, "conflict_snapshot", "has_conflicts")
        has_str = str(bool(has)) if has is not None else "unknown"
        severity = _safe_get(rec, "conflict_snapshot", "max_severity", "none")
        key = (has_str, str(severity))
        groups[key].append(_classify_record(rec))

    results: list[dict[str, Any]] = []
    for (has_str, severity), outcomes in sorted(groups.items()):
        stats = _compute_stats(outcomes, low_sample_threshold)
        results.append({
            "has_conflicts": has_str,
            "max_severity": severity,
            **stats,
            "notes": _build_group_note(stats),
        })

    results.sort(key=lambda x: x["sample_count"], reverse=True)
    return results


# =====================================================================
#  Event attribution
# =====================================================================

def _build_event_attribution(
    records: list[dict[str, Any]],
    low_sample_threshold: int,
) -> list[dict[str, Any]]:
    """Group by event_risk_state → stats.

    Input fields:
    - event_snapshot.event_risk_state
    - outcome_snapshot.realized_pnl
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        state = _safe_get(rec, "event_snapshot", "event_risk_state", "unknown")
        groups[str(state)].append(_classify_record(rec))

    results: list[dict[str, Any]] = []
    for state, outcomes in sorted(groups.items()):
        stats = _compute_stats(outcomes, low_sample_threshold)
        results.append({
            "event_risk_state": state,
            **stats,
            "notes": _build_group_note(stats),
        })

    results.sort(key=lambda x: x["sample_count"], reverse=True)
    return results


# =====================================================================
#  Conviction attribution
# =====================================================================

def _build_conviction_attribution(
    records: list[dict[str, Any]],
    low_sample_threshold: int,
) -> list[dict[str, Any]]:
    """Group by (conviction, decision) → stats.

    Input fields:
    - response_snapshot.conviction
    - response_snapshot.decision
    - outcome_snapshot.realized_pnl
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rec in records:
        conviction = _safe_get(rec, "response_snapshot", "conviction", "unknown")
        decision = _safe_get(rec, "response_snapshot", "decision", "unknown")
        key = (str(conviction), str(decision))
        groups[key].append(_classify_record(rec))

    results: list[dict[str, Any]] = []
    for (conviction, decision), outcomes in sorted(groups.items()):
        stats = _compute_stats(outcomes, low_sample_threshold)
        results.append({
            "conviction": conviction,
            "decision": decision,
            **stats,
            "notes": _build_group_note(stats),
        })

    results.sort(key=lambda x: x["sample_count"], reverse=True)
    return results


# =====================================================================
#  Alignment attribution  (market_alignment × portfolio_fit)
# =====================================================================

def _build_alignment_attribution(
    records: list[dict[str, Any]],
    low_sample_threshold: int,
) -> list[dict[str, Any]]:
    """Group by (market_alignment, portfolio_fit) → stats.

    Input fields:
    - response_snapshot.market_alignment
    - response_snapshot.portfolio_fit
    - outcome_snapshot.realized_pnl

    These fields are present in feedback v1.1 records that include
    the full response_snapshot.  Missing values default to "unknown".
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rec in records:
        alignment = _safe_get(rec, "response_snapshot", "market_alignment", "unknown")
        fit = _safe_get(rec, "response_snapshot", "portfolio_fit", "unknown")
        key = (str(alignment), str(fit))
        groups[key].append(_classify_record(rec))

    results: list[dict[str, Any]] = []
    for (alignment, fit), outcomes in sorted(groups.items()):
        stats = _compute_stats(outcomes, low_sample_threshold)
        results.append({
            "market_alignment": alignment,
            "portfolio_fit": fit,
            **stats,
            "notes": _build_group_note(stats),
        })

    results.sort(key=lambda x: x["sample_count"], reverse=True)
    return results


# =====================================================================
#  Sample-size summary
# =====================================================================

def _build_sample_size(records: list[dict[str, Any]]) -> dict[str, int]:
    """Compute sample-size summary from records.

    Derived fields:
    - total_records = len(records)
    - closed_records = count where status == "closed"
    - with_outcome = count where outcome_snapshot is a non-empty dict
    - with_pnl = count where outcome_snapshot.realized_pnl is a number
    """
    total = len(records)
    closed = sum(1 for r in records if r.get("status") == "closed")
    with_outcome = sum(
        1 for r in records
        if isinstance(r.get("outcome_snapshot"), dict) and r["outcome_snapshot"]
    )
    with_pnl = sum(
        1 for r in records
        if isinstance(r.get("outcome_snapshot"), dict)
        and isinstance(r["outcome_snapshot"].get("realized_pnl"), (int, float))
    )
    # with_decided: records that classify as "win" or "loss" (not breakeven/unknown).
    # This counts records where a pnl exists and pnl != 0.
    with_decided = sum(
        1 for r in records
        if isinstance(r.get("outcome_snapshot"), dict)
        and isinstance(r["outcome_snapshot"].get("realized_pnl"), (int, float))
        and r["outcome_snapshot"]["realized_pnl"] != 0
    )
    return {
        "total_records": total,
        "closed_records": closed,
        "with_outcome": with_outcome,
        "with_pnl": with_pnl,
        "with_decided": with_decided,
    }


def _derive_status(sample_size: dict[str, int], low_sample_threshold: int) -> str:
    """Determine report status from sample size.

    - "insufficient" if with_pnl == 0
    - "sparse" if with_pnl < low_sample_threshold
    - "sufficient" if with_pnl >= low_sample_threshold
    """
    pnl_count = sample_size.get("with_pnl", 0)
    if pnl_count == 0:
        return "insufficient"
    if pnl_count < low_sample_threshold:
        return "sparse"
    return "sufficient"


def _build_summary(
    status: str,
    sample_size: dict[str, int],
    regime_calibration: list[dict],
    warning_flags: list[str],
) -> str:
    """Build a human-readable summary string."""
    total = sample_size["total_records"]
    with_pnl = sample_size["with_pnl"]
    regimes = len(regime_calibration)

    if status == "insufficient":
        return (
            f"Insufficient data for calibration. {total} feedback record(s) "
            f"found but none have realized P&L data."
        )
    if status == "sparse":
        return (
            f"Sparse data available. {with_pnl} record(s) with P&L data "
            f"across {regimes} regime combination(s). Results should be "
            f"treated as preliminary."
        )
    w = f" {len(warning_flags)} warning(s) raised." if warning_flags else ""
    return (
        f"Calibration report based on {with_pnl} record(s) with P&L data "
        f"across {regimes} regime combination(s).{w}"
    )


# =====================================================================
#  Report-level warning flags
# =====================================================================

def _collect_warning_flags(
    status: str,
    sample_size: dict[str, int],
    regime_calibration: list[dict],
    signal_attribution: list[dict],
    low_sample_threshold: int,
) -> list[str]:
    """Collect report-level warning flags."""
    flags: list[str] = []

    if status == "insufficient":
        flags.append("no_pnl_data_available")
    elif status == "sparse":
        flags.append("sparse_pnl_data")

    # Check for regimes with all-unknown outcomes
    all_unknown_regimes = [
        r for r in regime_calibration
        if r.get("win_count", 0) == 0 and r.get("loss_count", 0) == 0
    ]
    if all_unknown_regimes:
        flags.append(f"regimes_with_no_decided_outcomes:{len(all_unknown_regimes)}")

    # Check for heavily skewed regime distribution
    if len(regime_calibration) >= 2:
        counts = [r["sample_count"] for r in regime_calibration]
        total = sum(counts)
        if total > 0:
            max_share = max(counts) / total
            if max_share > 0.8:
                flags.append("regime_distribution_skewed")

    # Low sample across all signal groups
    low_signal_groups = sum(
        1 for s in signal_attribution if s.get("low_sample_warning")
    )
    if signal_attribution and low_signal_groups == len(signal_attribution):
        flags.append("all_signal_groups_low_sample")

    closed = sample_size.get("closed_records", 0)
    with_pnl = sample_size.get("with_pnl", 0)
    if closed > 0 and with_pnl < closed:
        missing = closed - with_pnl
        flags.append(f"closed_records_without_pnl:{missing}")

    return flags


# =====================================================================
#  Main entry point
# =====================================================================

def build_calibration_report(
    records: list[dict[str, Any]],
    *,
    low_sample_threshold: int = _DEFAULT_LOW_SAMPLE_THRESHOLD,
) -> dict[str, Any]:
    """Build a full signal attribution and regime calibration report.

    Parameters
    ----------
    records : list[dict]
        List of feedback records (from feedback_loop.build_feedback_record).
        Records of any status are accepted, but only those with
        outcome_snapshot.realized_pnl contribute to win/loss stats.
    low_sample_threshold : int
        Groups with fewer than this many records get low_sample_warning=True.
        Default: 5.

    Returns
    -------
    dict — calibration report conforming to _CALIBRATION_VERSION.
    """
    if not isinstance(records, list):
        records = []

    # Filter to valid dicts only
    valid = [r for r in records if isinstance(r, dict)]

    now_iso = datetime.now(timezone.utc).isoformat()

    # Sample size
    sample_size = _build_sample_size(valid)

    # Build attribution sections
    regime_calibration = _build_regime_calibration(valid, low_sample_threshold)
    signal_attribution = _build_signal_attribution(valid, low_sample_threshold)
    strategy_attribution = _build_strategy_attribution(valid, low_sample_threshold)
    policy_attribution = _build_policy_attribution(valid, low_sample_threshold)
    conflict_attribution = _build_conflict_attribution(valid, low_sample_threshold)
    event_attribution = _build_event_attribution(valid, low_sample_threshold)
    conviction_attribution = _build_conviction_attribution(valid, low_sample_threshold)
    alignment_attribution = _build_alignment_attribution(valid, low_sample_threshold)

    # Status & summary
    status = _derive_status(sample_size, low_sample_threshold)
    warning_flags = _collect_warning_flags(
        status, sample_size, regime_calibration,
        signal_attribution, low_sample_threshold,
    )
    summary = _build_summary(status, sample_size, regime_calibration, warning_flags)

    return {
        "calibration_version": _CALIBRATION_VERSION,
        "generated_at": now_iso,
        "status": status,
        "sample_size": sample_size,
        "summary": summary,
        "regime_calibration": regime_calibration,
        "signal_attribution": signal_attribution,
        "strategy_attribution": strategy_attribution,
        "policy_attribution": policy_attribution,
        "conflict_attribution": conflict_attribution,
        "event_attribution": event_attribution,
        "conviction_attribution": conviction_attribution,
        "alignment_attribution": alignment_attribution,
        "warning_flags": warning_flags,
        "metadata": {
            "calibration_version": _CALIBRATION_VERSION,
            "generated_at": now_iso,
            "low_sample_threshold": low_sample_threshold,
        },
    }


# =====================================================================
#  Validation
# =====================================================================

def validate_calibration_report(
    report: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Validate a calibration report against the expected schema.

    Returns (ok, errors) where ok is True if report passes all checks.
    """
    errors: list[str] = []

    if not isinstance(report, dict):
        return False, ["report must be a dict"]

    # Required keys
    for key in _REQUIRED_REPORT_KEYS:
        if key not in report:
            errors.append(f"missing required key: {key}")

    # Version — accept any version in _COMPATIBLE_VERSIONS
    rv = report.get("calibration_version")
    if rv not in _COMPATIBLE_VERSIONS:
        errors.append(
            f"calibration_version mismatch: expected one of {sorted(_COMPATIBLE_VERSIONS)}, "
            f"got {rv}"
        )

    # Status
    valid_statuses = {"sufficient", "sparse", "insufficient"}
    if report.get("status") not in valid_statuses:
        errors.append(f"invalid status: {report.get('status')}")

    # Sample size
    ss = report.get("sample_size")
    if not isinstance(ss, dict):
        errors.append("sample_size must be a dict")
    else:
        for k in ("total_records", "closed_records", "with_outcome", "with_pnl", "with_decided"):
            if k not in ss:
                errors.append(f"sample_size missing key: {k}")

    # Attribution sections must be lists
    for section in (
        "regime_calibration", "signal_attribution", "strategy_attribution",
        "policy_attribution", "conflict_attribution", "event_attribution",
        "conviction_attribution", "alignment_attribution",
    ):
        val = report.get(section)
        if not isinstance(val, list):
            errors.append(f"{section} must be a list")

    # Warning flags
    wf = report.get("warning_flags")
    if not isinstance(wf, list):
        errors.append("warning_flags must be a list")

    return (len(errors) == 0, errors)


# =====================================================================
#  Compact report summary (for UI / logging)
# =====================================================================

def report_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Return a compact overview of a calibration report.

    Designed for UI dashboards and log outputs — this is a *read-only*
    digest; it does NOT alter or retune anything.

    Output keys:
    - calibration_version: str
    - status: str
    - total_records: int
    - with_pnl: int
    - with_decided: int   (win + loss, excludes breakeven/unknown)
    - regime_count: int
    - warning_count: int
    - top_regime: str | None   (regime_label with most samples, if any)
    - module_role: str          (always "summary")
    """
    ss = report.get("sample_size") or {}
    regimes = report.get("regime_calibration") or []
    warnings = report.get("warning_flags") or []

    top_regime = None
    if regimes:
        top = max(regimes, key=lambda r: r.get("sample_count", 0))
        top_regime = top.get("regime_label")

    return {
        "calibration_version": report.get("calibration_version", "unknown"),
        "status": report.get("status", "unknown"),
        "total_records": ss.get("total_records", 0),
        "with_pnl": ss.get("with_pnl", 0),
        "with_decided": ss.get("with_decided", 0),
        "regime_count": len(regimes),
        "warning_count": len(warnings),
        "top_regime": top_regime,
        "module_role": _MODULE_ROLE,
    }
