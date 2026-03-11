"""Model-vs-Engine Disagreement Tracking v1.

Measures and summarises where deterministic engine / context / policy
outputs and model-driven decision outputs diverge.  Lays safe
groundwork for future adaptive-weighting refinement.

This module is **tracking / diagnostic only** — it does NOT
automatically change live weights, decisions, or policy thresholds.

Public API
----------
build_disagreement_record(response, policy, composite, conflict_report,
                          confidence, *, feedback_record=None)
    Compare a single decision response against structured context.
    Returns a list of disagreement records (may be empty if aligned).

build_tracking_report(records, *, low_sample_threshold=5)
    Aggregate many feedback records into a full disagreement-tracking
    report with rates, grouping, and weighting diagnostics.

validate_tracking_report(report)
    Schema check → (ok, errors).

Output version: 1.0
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

# ── Version lock ────────────────────────────────────────────────────────
_TRACKING_VERSION = "1.0"

# ── Low-sample default ──────────────────────────────────────────────────
_DEFAULT_LOW_SAMPLE_THRESHOLD = 5

# ── Disagreement categories ────────────────────────────────────────────
VALID_CATEGORIES = frozenset({
    "direction",
    "size_guidance",
    "caution_level",
    "risk_acceptance",
    "confidence_uncertainty",
    "model_vs_policy",
    "model_vs_market_composite",
})

# ── Disagreement severities ────────────────────────────────────────────
VALID_SEVERITIES = frozenset({"low", "moderate", "high"})

# ── Required top-level keys (for validation) ────────────────────────────
_REQUIRED_REPORT_KEYS = frozenset({
    "tracking_version",
    "generated_at",
    "status",
    "summary",
    "sample_size",
    "disagreement_records",
    "disagreement_summary",
    "disagreement_rates",
    "disagreement_by_regime",
    "disagreement_by_strategy",
    "disagreement_by_policy_state",
    "weighting_diagnostics",
    "warning_flags",
    "evidence",
    "metadata",
})


# =====================================================================
#  Internal helpers
# =====================================================================

def _safe_get(source: dict | None, key: str, default: Any = None) -> Any:
    """Safely get a key from a dict or None."""
    if not isinstance(source, dict):
        return default
    return source.get(key, default)


def _snap(record: dict, snapshot_key: str) -> dict:
    """Get a snapshot dict from a feedback record, or empty dict."""
    val = record.get(snapshot_key)
    return val if isinstance(val, dict) else {}


# =====================================================================
#  Single-record disagreement detection
# =====================================================================

# ── Decision → aggressiveness mapping ──────────────────────────────────
# Higher = more aggressive (willing to trade).
_DECISION_RANK = {
    "approve": 4,
    "cautious_approve": 3,
    "watchlist": 2,
    "reject": 1,
    "insufficient_data": 0,
}

# Policy decision → restrictiveness  (higher = more restrictive)
_POLICY_RANK = {
    "allow": 0,
    "caution": 1,
    "restrict": 2,
    "block": 3,
    "insufficient_data": 4,
}

# Size guidance → restrictiveness  (higher = more restricted)
_SIZE_RANK = {
    "normal": 0,
    "reduced": 1,
    "minimal": 2,
    "none": 3,
}

# Market state → bullishness  (higher = more risk-on)
_MARKET_STATE_RANK = {
    "risk_off": 0,
    "neutral": 1,
    "risk_on": 2,
}

# Support state → stability  (higher = more supportive)
_SUPPORT_STATE_RANK = {
    "fragile": 0,
    "mixed": 1,
    "supportive": 2,
}

# Stability state → orderliness  (higher = more orderly)
_STABILITY_STATE_RANK = {
    "unstable": 0,
    "noisy": 1,
    "orderly": 2,
}

# Conviction → forcefulness  (higher = more conviction)
_CONVICTION_RANK = {
    "none": 0,
    "low": 1,
    "moderate": 2,
    "high": 3,
}

# Confidence label → certainty ranking
_CONFIDENCE_RANK = {
    "none": 0,
    "low": 1,
    "moderate": 2,
    "high": 3,
}


def _severity_from_gap(gap: int, thresholds: tuple[int, int] = (1, 2)) -> str:
    """Derive disagreement severity from a rank gap.

    Formula: severity = "low" if gap == thresholds[0],
             "moderate" if gap == thresholds[1],
             "high" if gap > thresholds[1].
    """
    if gap <= thresholds[0]:
        return "low"
    if gap <= thresholds[1]:
        return "moderate"
    return "high"


def build_disagreement_record(
    response: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    composite: dict[str, Any] | None = None,
    conflict_report: dict[str, Any] | None = None,
    confidence: dict[str, Any] | None = None,
    *,
    feedback_record: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compare a single decision response against structured context.

    Accepts either direct module outputs OR a feedback record (which
    contains snapshots of each).  If both are provided, the direct
    outputs take priority.

    Returns a list of disagreement records (empty list if fully aligned).

    Parameters
    ----------
    response : dict | None
        Final decision response (from decision_response_contract).
    policy : dict | None
        Policy evaluation output (from decision_policy).
    composite : dict | None
        Market-composite summary (from market_composite).
    conflict_report : dict | None
        Conflict detection output (from conflict_detector).
    confidence : dict | None
        Confidence assessment (from confidence_framework).
    feedback_record : dict | None
        If provided, snapshots are extracted from it for any
        missing direct inputs.

    Returns
    -------
    list[dict] — disagreement records.  Each has: record_id, category,
    severity, model_position, engine_position, policy_position,
    context, outcome, notes.
    """
    # Resolve inputs: direct > feedback-record snapshots
    resp = dict(response) if isinstance(response, dict) else {}
    pol = dict(policy) if isinstance(policy, dict) else {}
    comp = dict(composite) if isinstance(composite, dict) else {}
    conf_report = dict(conflict_report) if isinstance(conflict_report, dict) else {}
    conf_assess = dict(confidence) if isinstance(confidence, dict) else {}

    # Fill from feedback record if available
    if isinstance(feedback_record, dict):
        if not resp:
            resp = _snap(feedback_record, "response_snapshot")
        if not pol:
            pol = _snap(feedback_record, "policy_snapshot")
        if not comp:
            comp = _snap(feedback_record, "market_snapshot")
        if not conf_report:
            conf_report = _snap(feedback_record, "conflict_snapshot")

    # Outcome from feedback record (if closed)
    outcome = None
    if isinstance(feedback_record, dict):
        out_snap = _snap(feedback_record, "outcome_snapshot")
        pnl = out_snap.get("realized_pnl")
        if isinstance(pnl, (int, float)):
            outcome = "win" if pnl > 0 else "loss"

    disagreements: list[dict[str, Any]] = []
    idx = 0

    # ─── 1. Model-vs-Policy disagreement ──────────────────────────────
    model_decision = resp.get("decision", "")
    policy_decision = pol.get("policy_decision", "")
    if model_decision and policy_decision:
        m_rank = _DECISION_RANK.get(model_decision, -1)
        p_rank = _POLICY_RANK.get(policy_decision, -1)
        if m_rank >= 0 and p_rank >= 0:
            # Model is aggressive (approve/cautious) but policy is restrictive
            model_is_aggressive = m_rank >= 3  # approve or cautious_approve
            policy_is_restrictive = p_rank >= 2  # restrict or block
            # -OR- Model rejects but policy allows
            model_rejects = m_rank <= 1  # reject or insufficient
            policy_allows = p_rank == 0  # allow

            if model_is_aggressive and policy_is_restrictive:
                gap = p_rank - 1  # how far apart
                idx += 1
                disagreements.append({
                    "record_id": f"d-{idx:03d}",
                    "category": "model_vs_policy",
                    "severity": _severity_from_gap(gap),
                    "model_position": model_decision,
                    "engine_position": None,
                    "policy_position": policy_decision,
                    "context": {
                        "model_decision_rank": m_rank,
                        "policy_restrictiveness": p_rank,
                        "failed_checks": pol.get("failed_check_names",
                                                  pol.get("blocking_checks", [])),
                    },
                    "outcome": outcome,
                    "notes": (
                        f"Model decision '{model_decision}' overrides "
                        f"restrictive policy '{policy_decision}'"
                    ),
                })
            elif model_rejects and policy_allows:
                idx += 1
                disagreements.append({
                    "record_id": f"d-{idx:03d}",
                    "category": "model_vs_policy",
                    "severity": "low",
                    "model_position": model_decision,
                    "engine_position": None,
                    "policy_position": policy_decision,
                    "context": {
                        "model_decision_rank": m_rank,
                        "policy_restrictiveness": p_rank,
                    },
                    "outcome": outcome,
                    "notes": (
                        f"Model rejects ('{model_decision}') despite policy "
                        f"allowing ('{policy_decision}')"
                    ),
                })

    # ─── 2. Size-guidance disagreement ────────────────────────────────
    model_size = resp.get("size_guidance", "")
    policy_size = pol.get("size_guidance", "")
    if model_size and policy_size:
        ms = _SIZE_RANK.get(model_size, -1)
        ps = _SIZE_RANK.get(policy_size, -1)
        if ms >= 0 and ps >= 0 and ms != ps:
            gap = abs(ms - ps)
            if gap >= 1:
                idx += 1
                disagreements.append({
                    "record_id": f"d-{idx:03d}",
                    "category": "size_guidance",
                    "severity": _severity_from_gap(gap),
                    "model_position": model_size,
                    "engine_position": None,
                    "policy_position": policy_size,
                    "context": {
                        "model_size_rank": ms,
                        "policy_size_rank": ps,
                        "direction": "model_larger" if ms < ps else "model_smaller",
                    },
                    "outcome": outcome,
                    "notes": (
                        f"Model size '{model_size}' vs policy size "
                        f"'{policy_size}'"
                    ),
                })

    # ─── 3. Direction / market-composite disagreement ─────────────────
    # Model says approve in a risk_off or fragile market
    market_state = comp.get("market_state", "")
    support_state = comp.get("support_state", "")
    stability_state = comp.get("stability_state", "")

    if model_decision and market_state:
        ms_rank = _MARKET_STATE_RANK.get(market_state, -1)
        md_rank = _DECISION_RANK.get(model_decision, -1)
        if ms_rank >= 0 and md_rank >= 0:
            # Model approves but market is risk_off
            if md_rank >= 3 and ms_rank == 0:
                idx += 1
                disagreements.append({
                    "record_id": f"d-{idx:03d}",
                    "category": "direction",
                    "severity": "high",
                    "model_position": model_decision,
                    "engine_position": market_state,
                    "policy_position": policy_decision or None,
                    "context": {
                        "market_state": market_state,
                        "support_state": support_state,
                        "stability_state": stability_state,
                    },
                    "outcome": outcome,
                    "notes": (
                        f"Model '{model_decision}' against risk_off "
                        f"market state"
                    ),
                })

    # Model says approve with fragile support
    if model_decision:
        md_rank = _DECISION_RANK.get(model_decision, -1)
        ss_rank = _SUPPORT_STATE_RANK.get(support_state, -1)
        if md_rank >= 3 and ss_rank == 0:
            idx += 1
            disagreements.append({
                "record_id": f"d-{idx:03d}",
                "category": "model_vs_market_composite",
                "severity": "moderate",
                "model_position": model_decision,
                "engine_position": support_state,
                "policy_position": policy_decision or None,
                "context": {
                    "market_state": market_state,
                    "support_state": support_state,
                },
                "outcome": outcome,
                "notes": (
                    f"Model '{model_decision}' despite fragile support state"
                ),
            })

    # Model says approve with unstable market
    if model_decision:
        md_rank = _DECISION_RANK.get(model_decision, -1)
        stab_rank = _STABILITY_STATE_RANK.get(stability_state, -1)
        if md_rank >= 3 and stab_rank == 0:
            idx += 1
            disagreements.append({
                "record_id": f"d-{idx:03d}",
                "category": "model_vs_market_composite",
                "severity": "high",
                "model_position": model_decision,
                "engine_position": stability_state,
                "policy_position": policy_decision or None,
                "context": {
                    "stability_state": stability_state,
                },
                "outcome": outcome,
                "notes": (
                    f"Model '{model_decision}' despite unstable market"
                ),
            })

    # ─── 4. Caution-level disagreement ────────────────────────────────
    # Model high conviction despite elevated conflict severity
    model_conviction = resp.get("conviction", "")
    conflict_severity = conf_report.get("max_severity",
                                         conf_report.get("conflict_severity", ""))
    if model_conviction and conflict_severity:
        cv_rank = _CONVICTION_RANK.get(model_conviction, -1)
        # Map conflict severity to a rank
        sev_map = {"none": 0, "low": 1, "moderate": 2, "high": 3}
        cs_rank = sev_map.get(conflict_severity, -1)
        if cv_rank >= 0 and cs_rank >= 0:
            # High conviction + high conflict = disagreement
            if cv_rank >= 2 and cs_rank >= 2:
                gap = min(cv_rank, cs_rank)
                idx += 1
                disagreements.append({
                    "record_id": f"d-{idx:03d}",
                    "category": "caution_level",
                    "severity": _severity_from_gap(gap),
                    "model_position": model_conviction,
                    "engine_position": conflict_severity,
                    "policy_position": policy_decision or None,
                    "context": {
                        "conviction_rank": cv_rank,
                        "conflict_severity_rank": cs_rank,
                        "has_conflicts": conf_report.get("has_conflicts"),
                        "conflict_count": conf_report.get("conflict_count"),
                    },
                    "outcome": outcome,
                    "notes": (
                        f"Model conviction '{model_conviction}' despite "
                        f"conflict severity '{conflict_severity}'"
                    ),
                })

    # ─── 5. Risk-acceptance disagreement ──────────────────────────────
    # Model approves despite "elevated" or "high" event_risk in response,
    # or despite event_snapshot showing elevated risk
    model_event_risk = resp.get("event_risk", "")
    if model_decision and model_event_risk:
        md_rank = _DECISION_RANK.get(model_decision, -1)
        risk_map = {"low": 0, "moderate": 1, "elevated": 2, "high": 3, "unknown": -1}
        er_rank = risk_map.get(model_event_risk, -1)
        if md_rank >= 3 and er_rank >= 2:
            idx += 1
            disagreements.append({
                "record_id": f"d-{idx:03d}",
                "category": "risk_acceptance",
                "severity": "moderate" if er_rank == 2 else "high",
                "model_position": model_decision,
                "engine_position": model_event_risk,
                "policy_position": policy_decision or None,
                "context": {
                    "event_risk": model_event_risk,
                    "event_risk_rank": er_rank,
                },
                "outcome": outcome,
                "notes": (
                    f"Model '{model_decision}' despite event risk "
                    f"'{model_event_risk}'"
                ),
            })

    # ─── 6. Confidence / uncertainty disagreement ─────────────────────
    # Model high conviction but confidence assessment is low/none
    confidence_label = conf_assess.get("confidence_label", "")
    uncertainty_level = conf_assess.get("uncertainty_level", "")
    if model_conviction and (confidence_label or uncertainty_level):
        cv_rank = _CONVICTION_RANK.get(model_conviction, -1)
        cl_rank = _CONFIDENCE_RANK.get(confidence_label, -1)
        if cv_rank >= 2 and cl_rank >= 0 and cl_rank <= 1:
            # High/moderate conviction + low/none confidence
            gap = cv_rank - cl_rank
            idx += 1
            disagreements.append({
                "record_id": f"d-{idx:03d}",
                "category": "confidence_uncertainty",
                "severity": _severity_from_gap(gap),
                "model_position": model_conviction,
                "engine_position": confidence_label,
                "policy_position": None,
                "context": {
                    "conviction_rank": cv_rank,
                    "confidence_rank": cl_rank,
                    "uncertainty_level": uncertainty_level,
                },
                "outcome": outcome,
                "notes": (
                    f"Model conviction '{model_conviction}' despite "
                    f"confidence '{confidence_label}'"
                ),
            })

    return disagreements


# =====================================================================
#  Feedback-record batch processing
# =====================================================================

def _extract_disagreements_from_feedback(
    record: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract disagreement records from a single feedback record.

    Uses snapshots stored in the feedback record as inputs to
    build_disagreement_record.
    """
    return build_disagreement_record(feedback_record=record)


# =====================================================================
#  Aggregation helpers
# =====================================================================

def _compute_outcome_stats(
    outcomes: list[str | None],
) -> dict[str, Any]:
    """Compute win/loss/unknown aggregates from outcome labels.

    Derived fields:
    - win_count: outcomes == "win"
    - loss_count: outcomes == "loss"
    - unknown_count: outcomes not in ("win", "loss")
    - win_rate: win_count / (win_count + loss_count) if denominator > 0
    """
    wins = sum(1 for o in outcomes if o == "win")
    losses = sum(1 for o in outcomes if o == "loss")
    unknowns = len(outcomes) - wins - losses
    decided = wins + losses
    return {
        "win_count": wins,
        "loss_count": losses,
        "unknown_count": unknowns,
        "win_rate": round(wins / decided, 4) if decided > 0 else None,
    }


def _build_disagreement_summary(
    all_disagreements: list[dict[str, Any]],
    low_sample_threshold: int,
) -> dict[str, Any]:
    """Summarise all disagreement records by category.

    Output: dict mapping category → {count, severity_distribution,
    outcome_stats, low_sample_warning, notes}.
    """
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for d in all_disagreements:
        cat = d.get("category", "unknown")
        by_cat[cat].append(d)

    summary: dict[str, Any] = {}
    for cat, items in sorted(by_cat.items()):
        sev_dist = defaultdict(int)
        outcomes = []
        for item in items:
            sev_dist[item.get("severity", "unknown")] += 1
            outcomes.append(item.get("outcome"))
        n = len(items)
        ostats = _compute_outcome_stats(outcomes)
        low = n < low_sample_threshold
        note_parts = [f"{n} disagreement(s)"]
        if ostats["win_rate"] is not None:
            note_parts.append(f"win rate after disagreement: {ostats['win_rate']:.1%}")
        if low:
            note_parts.append("low sample")
        summary[cat] = {
            "count": n,
            "severity_distribution": dict(sev_dist),
            "outcome_stats": ostats,
            "low_sample_warning": low,
            "notes": "; ".join(note_parts),
        }

    return summary


def _build_disagreement_rates(
    total_records: int,
    records_with_disagreement: int,
    all_disagreements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute overall disagreement rates.

    Derived fields:
    - total_records
    - records_with_disagreement
    - disagreement_rate = records_with_disagreement / total_records
    - total_disagreements (one record can produce multiple disagreements)
    - avg_disagreements_per_record = total / total_records
    """
    rate = (
        round(records_with_disagreement / total_records, 4)
        if total_records > 0 else None
    )
    avg = (
        round(len(all_disagreements) / total_records, 4)
        if total_records > 0 else None
    )
    return {
        "total_records": total_records,
        "records_with_disagreement": records_with_disagreement,
        "disagreement_rate": rate,
        "total_disagreements": len(all_disagreements),
        "avg_disagreements_per_record": avg,
    }


def _group_by_dimension(
    records_with_disags: list[tuple[dict, list[dict]]],
    snapshot_key: str,
    field: str,
    low_sample_threshold: int,
) -> list[dict[str, Any]]:
    """Group disagreement records by a dimension field from a snapshot.

    Parameters
    ----------
    records_with_disags : list of (feedback_record, [disagreement_records])
    snapshot_key : e.g. "market_snapshot"
    field : e.g. "regime_label"
    low_sample_threshold : int
    """
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "total_records": 0,
        "records_with_disagreement": 0,
        "disagreements": [],
        "outcomes": [],
    })

    for rec, disags in records_with_disags:
        snap = _snap(rec, snapshot_key) if snapshot_key else rec
        val = str(snap.get(field, "unknown")) if isinstance(snap, dict) else "unknown"
        grp = groups[val]
        grp["total_records"] += 1
        if disags:
            grp["records_with_disagreement"] += 1
            grp["disagreements"].extend(disags)
        # Outcome
        out_snap = _snap(rec, "outcome_snapshot")
        pnl = out_snap.get("realized_pnl") if isinstance(out_snap, dict) else None
        if isinstance(pnl, (int, float)):
            grp["outcomes"].append("win" if pnl > 0 else "loss")
        else:
            grp["outcomes"].append(None)

    results = []
    for val, grp in sorted(groups.items()):
        total = grp["total_records"]
        with_d = grp["records_with_disagreement"]
        cat_counts: dict[str, int] = defaultdict(int)
        for d in grp["disagreements"]:
            cat_counts[d.get("category", "unknown")] += 1
        ostats = _compute_outcome_stats(grp["outcomes"])
        low = total < low_sample_threshold
        results.append({
            field: val,
            "total_records": total,
            "records_with_disagreement": with_d,
            "disagreement_rate": round(with_d / total, 4) if total > 0 else None,
            "category_counts": dict(cat_counts),
            "outcome_stats": ostats,
            "low_sample_warning": low,
        })

    results.sort(key=lambda x: x["total_records"], reverse=True)
    return results


# =====================================================================
#  Weighting diagnostics
# =====================================================================

def _build_weighting_diagnostics(
    disagreement_summary: dict[str, Any],
    by_regime: list[dict],
    by_strategy: list[dict],
    by_policy: list[dict],
    total_records: int,
    low_sample_threshold: int,
) -> list[dict[str, Any]]:
    """Build advisory weighting diagnostics.

    These are plain-language observations, not automated actions.
    Each diagnostic has: diagnostic_id, category, observation,
    confidence_note, recommendation, evidence.
    """
    diagnostics: list[dict[str, Any]] = []
    idx = 0

    insufficient = total_records < low_sample_threshold

    if insufficient:
        idx += 1
        diagnostics.append({
            "diagnostic_id": f"w-{idx:03d}",
            "category": "sample_size",
            "observation": (
                f"Only {total_records} record(s) available. "
                f"All diagnostics below are preliminary."
            ),
            "confidence_note": "very low — insufficient data",
            "recommendation": "Collect more closed feedback records before acting.",
            "evidence": {"total_records": total_records},
        })
        return diagnostics

    # Policy-override pattern
    mvp = disagreement_summary.get("model_vs_policy", {})
    if mvp.get("count", 0) > 0:
        mvp_rate = mvp["count"] / total_records if total_records > 0 else 0
        ostats = mvp.get("outcome_stats", {})
        wr = ostats.get("win_rate")
        idx += 1
        if wr is not None and wr < 0.5:
            diagnostics.append({
                "diagnostic_id": f"w-{idx:03d}",
                "category": "model_vs_policy",
                "observation": (
                    f"Model overrides restrictive policy in {mvp['count']} case(s) "
                    f"({mvp_rate:.0%} of records). Win rate after override: {wr:.0%}."
                ),
                "confidence_note": (
                    "low" if mvp.get("low_sample_warning") else "moderate"
                ),
                "recommendation": (
                    "Model tends to be over-aggressive when policy is restrictive. "
                    "Consider higher caution weight for policy-restricted trades."
                ),
                "evidence": {
                    "override_count": mvp["count"],
                    "override_rate": round(mvp_rate, 4),
                    "win_rate": wr,
                },
            })
        elif wr is not None and wr >= 0.5:
            diagnostics.append({
                "diagnostic_id": f"w-{idx:03d}",
                "category": "model_vs_policy",
                "observation": (
                    f"Model overrides policy in {mvp['count']} case(s) "
                    f"with {wr:.0%} win rate."
                ),
                "confidence_note": (
                    "low" if mvp.get("low_sample_warning") else "moderate"
                ),
                "recommendation": (
                    "Policy-aligned decisions may be overly conservative. "
                    "Review policy thresholds for potential loosening."
                ),
                "evidence": {
                    "override_count": mvp["count"],
                    "win_rate": wr,
                },
            })
        else:
            diagnostics.append({
                "diagnostic_id": f"w-{idx:03d}",
                "category": "model_vs_policy",
                "observation": (
                    f"Model overrides policy in {mvp['count']} case(s). "
                    f"No outcome data available to assess quality."
                ),
                "confidence_note": "very low — no outcome data",
                "recommendation": (
                    "Close feedback records with outcomes before drawing conclusions."
                ),
                "evidence": {"override_count": mvp["count"]},
            })

    # Market-composite disagreement pattern
    mmc = disagreement_summary.get("model_vs_market_composite", {})
    dir_d = disagreement_summary.get("direction", {})
    composite_count = mmc.get("count", 0) + dir_d.get("count", 0)
    if composite_count > 0:
        combined_outcomes = []
        for cat_key in ("model_vs_market_composite", "direction"):
            cat_data = disagreement_summary.get(cat_key, {})
            if cat_data:
                os = cat_data.get("outcome_stats", {})
                w = os.get("win_count", 0)
                l = os.get("loss_count", 0)
                combined_outcomes.extend(["win"] * w)
                combined_outcomes.extend(["loss"] * l)
        ostats = _compute_outcome_stats(combined_outcomes)
        wr = ostats.get("win_rate")
        idx += 1
        obs = (
            f"Model disagrees with market composite in {composite_count} case(s)."
        )
        if wr is not None:
            obs += f" Win rate after disagreement: {wr:.0%}."
        rec_text = (
            "Model disagreement with unstable/fragile market states "
            "has weak outcomes. Consider increased caution."
            if wr is not None and wr < 0.5
            else "Monitor market-composite disagreement trends."
        )
        diagnostics.append({
            "diagnostic_id": f"w-{idx:03d}",
            "category": "market_composite",
            "observation": obs,
            "confidence_note": (
                "low" if composite_count < low_sample_threshold else "moderate"
            ),
            "recommendation": rec_text,
            "evidence": {
                "composite_disagreement_count": composite_count,
                "win_rate": wr,
            },
        })

    # Confidence/uncertainty pattern
    cu = disagreement_summary.get("confidence_uncertainty", {})
    if cu.get("count", 0) > 0:
        wr = cu.get("outcome_stats", {}).get("win_rate")
        idx += 1
        diagnostics.append({
            "diagnostic_id": f"w-{idx:03d}",
            "category": "confidence_uncertainty",
            "observation": (
                f"High conviction with low confidence in {cu['count']} case(s)."
                + (f" Win rate: {wr:.0%}." if wr is not None else "")
            ),
            "confidence_note": (
                "low" if cu.get("low_sample_warning") else "moderate"
            ),
            "recommendation": (
                "Review whether conviction is well-supported by data quality."
            ),
            "evidence": {
                "count": cu["count"],
                "win_rate": wr,
            },
        })

    # Regime-specific patterns
    for grp in by_regime:
        regime = grp.get("regime_label", "unknown")
        rate = grp.get("disagreement_rate")
        wr = grp.get("outcome_stats", {}).get("win_rate")
        if rate is not None and rate > 0.5 and not grp.get("low_sample_warning"):
            idx += 1
            diagnostics.append({
                "diagnostic_id": f"w-{idx:03d}",
                "category": "regime_specific",
                "observation": (
                    f"High disagreement rate ({rate:.0%}) in regime "
                    f"'{regime}'."
                    + (f" Win rate: {wr:.0%}." if wr is not None else "")
                ),
                "confidence_note": "moderate",
                "recommendation": (
                    f"Review model behaviour in '{regime}' regime for "
                    f"potential calibration."
                ),
                "evidence": {
                    "regime": regime,
                    "disagreement_rate": rate,
                    "win_rate": wr,
                    "total_records": grp["total_records"],
                },
            })

    if not diagnostics:
        idx += 1
        diagnostics.append({
            "diagnostic_id": f"w-{idx:03d}",
            "category": "general",
            "observation": "No notable disagreement patterns detected.",
            "confidence_note": (
                "low" if total_records < low_sample_threshold else "moderate"
            ),
            "recommendation": "Continue collecting feedback records.",
            "evidence": {"total_records": total_records},
        })

    return diagnostics


# =====================================================================
#  Sample-size summary
# =====================================================================

def _build_sample_size(
    records: list[dict[str, Any]],
    all_disagreements: list[dict[str, Any]],
    records_with_disagreement: int,
) -> dict[str, int]:
    """Compute sample-size summary.

    Derived fields:
    - total_records
    - closed_records: status == "closed"
    - with_outcome: outcome_snapshot has realized_pnl
    - records_with_disagreement
    - total_disagreements
    """
    total = len(records)
    closed = sum(1 for r in records if r.get("status") == "closed")
    with_outcome = sum(
        1 for r in records
        if isinstance(r.get("outcome_snapshot"), dict)
        and isinstance(r["outcome_snapshot"].get("realized_pnl"), (int, float))
    )
    return {
        "total_records": total,
        "closed_records": closed,
        "with_outcome": with_outcome,
        "records_with_disagreement": records_with_disagreement,
        "total_disagreements": len(all_disagreements),
    }


def _derive_status(sample_size: dict[str, int], low_sample_threshold: int) -> str:
    """Determine report status from sample size.

    - "insufficient" if total_records == 0
    - "sparse" if total_records < low_sample_threshold
    - "sufficient" if total_records >= low_sample_threshold
    """
    total = sample_size.get("total_records", 0)
    if total == 0:
        return "insufficient"
    if total < low_sample_threshold:
        return "sparse"
    return "sufficient"


def _build_summary(
    status: str,
    sample_size: dict[str, int],
    rates: dict[str, Any],
    warning_flags: list[str],
) -> str:
    """Build a human-readable summary string."""
    total = sample_size["total_records"]
    with_d = sample_size["records_with_disagreement"]
    total_d = sample_size["total_disagreements"]
    rate = rates.get("disagreement_rate")

    if status == "insufficient":
        return "Insufficient data for disagreement tracking. No feedback records available."

    if status == "sparse":
        return (
            f"Sparse data available. {total} record(s) analysed, "
            f"{with_d} with disagreement(s) ({total_d} total). "
            f"Results should be treated as preliminary."
        )

    w = f" {len(warning_flags)} warning(s) raised." if warning_flags else ""
    rate_str = f" ({rate:.0%})" if rate is not None else ""
    return (
        f"Tracking report based on {total} record(s). "
        f"{with_d} record(s){rate_str} have disagreement(s) "
        f"({total_d} total).{w}"
    )


# =====================================================================
#  Warning flags
# =====================================================================

def _collect_warning_flags(
    status: str,
    sample_size: dict[str, int],
    disagreement_summary: dict[str, Any],
    low_sample_threshold: int,
) -> list[str]:
    """Collect report-level warning flags."""
    flags: list[str] = []

    if status == "insufficient":
        flags.append("no_data_available")
    elif status == "sparse":
        flags.append("sparse_data")

    with_outcome = sample_size.get("with_outcome", 0)
    total = sample_size.get("total_records", 0)
    if total > 0 and with_outcome == 0:
        flags.append("no_outcome_data")
    elif total > 0 and with_outcome < total:
        missing = total - with_outcome
        flags.append(f"records_without_outcome:{missing}")

    # Check for persistent model override of policy
    mvp = disagreement_summary.get("model_vs_policy", {})
    if mvp.get("count", 0) >= 3:
        flags.append(f"persistent_policy_override:{mvp['count']}")

    # All categories low-sample
    if disagreement_summary:
        all_low = all(
            v.get("low_sample_warning", True)
            for v in disagreement_summary.values()
        )
        if all_low:
            flags.append("all_categories_low_sample")

    return flags


# =====================================================================
#  Main entry point
# =====================================================================

def build_tracking_report(
    records: list[dict[str, Any]],
    *,
    low_sample_threshold: int = _DEFAULT_LOW_SAMPLE_THRESHOLD,
) -> dict[str, Any]:
    """Build a full model-vs-engine disagreement tracking report.

    Parameters
    ----------
    records : list[dict]
        List of feedback records (from feedback_loop.build_feedback_record).
        Records of any status are accepted.
    low_sample_threshold : int
        Groups with fewer than this many records get low_sample_warning.
        Default: 5.

    Returns
    -------
    dict — tracking report conforming to _TRACKING_VERSION.
    """
    if not isinstance(records, list):
        records = []

    valid = [r for r in records if isinstance(r, dict)]
    now_iso = datetime.now(timezone.utc).isoformat()

    # Process each record
    all_disagreements: list[dict[str, Any]] = []
    records_with_disags: list[tuple[dict, list[dict]]] = []
    records_with_disagreement_count = 0

    for rec in valid:
        disags = _extract_disagreements_from_feedback(rec)
        records_with_disags.append((rec, disags))
        all_disagreements.extend(disags)
        if disags:
            records_with_disagreement_count += 1

    # Sample size
    sample_size = _build_sample_size(
        valid, all_disagreements, records_with_disagreement_count,
    )

    # Disagreement summary
    disagreement_summary = _build_disagreement_summary(
        all_disagreements, low_sample_threshold,
    )

    # Disagreement rates
    disagreement_rates = _build_disagreement_rates(
        len(valid), records_with_disagreement_count, all_disagreements,
    )

    # Groupings
    by_regime = _group_by_dimension(
        records_with_disags, "market_snapshot", "regime_label",
        low_sample_threshold,
    )
    by_strategy = _group_by_dimension(
        records_with_disags, "candidate_snapshot", "strategy",
        low_sample_threshold,
    )
    by_policy = _group_by_dimension(
        records_with_disags, "policy_snapshot", "policy_decision",
        low_sample_threshold,
    )

    # Weighting diagnostics
    weighting_diagnostics = _build_weighting_diagnostics(
        disagreement_summary, by_regime, by_strategy, by_policy,
        len(valid), low_sample_threshold,
    )

    # Status & warnings
    status = _derive_status(sample_size, low_sample_threshold)
    warning_flags = _collect_warning_flags(
        status, sample_size, disagreement_summary, low_sample_threshold,
    )
    summary = _build_summary(status, sample_size, disagreement_rates, warning_flags)

    return {
        "tracking_version": _TRACKING_VERSION,
        "generated_at": now_iso,
        "status": status,
        "summary": summary,
        "sample_size": sample_size,
        "disagreement_records": all_disagreements,
        "disagreement_summary": disagreement_summary,
        "disagreement_rates": disagreement_rates,
        "disagreement_by_regime": by_regime,
        "disagreement_by_strategy": by_strategy,
        "disagreement_by_policy_state": by_policy,
        "weighting_diagnostics": weighting_diagnostics,
        "warning_flags": warning_flags,
        "evidence": {
            "categories_detected": sorted(disagreement_summary.keys()),
            "total_disagreements": len(all_disagreements),
        },
        "metadata": {
            "tracking_version": _TRACKING_VERSION,
            "generated_at": now_iso,
            "low_sample_threshold": low_sample_threshold,
        },
    }


# =====================================================================
#  Validation
# =====================================================================

def validate_tracking_report(
    report: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Validate a tracking report against the expected schema.

    Returns (ok, errors) where ok is True if report passes all checks.
    """
    errors: list[str] = []

    if not isinstance(report, dict):
        return False, ["report must be a dict"]

    for key in _REQUIRED_REPORT_KEYS:
        if key not in report:
            errors.append(f"missing required key: {key}")

    if report.get("tracking_version") != _TRACKING_VERSION:
        errors.append(
            f"tracking_version mismatch: expected {_TRACKING_VERSION}, "
            f"got {report.get('tracking_version')}"
        )

    valid_statuses = {"sufficient", "sparse", "insufficient"}
    if report.get("status") not in valid_statuses:
        errors.append(f"invalid status: {report.get('status')}")

    ss = report.get("sample_size")
    if not isinstance(ss, dict):
        errors.append("sample_size must be a dict")
    else:
        for k in ("total_records", "closed_records", "with_outcome",
                   "records_with_disagreement", "total_disagreements"):
            if k not in ss:
                errors.append(f"sample_size missing key: {k}")

    if not isinstance(report.get("disagreement_records"), list):
        errors.append("disagreement_records must be a list")

    if not isinstance(report.get("disagreement_summary"), dict):
        errors.append("disagreement_summary must be a dict")

    if not isinstance(report.get("disagreement_rates"), dict):
        errors.append("disagreement_rates must be a dict")

    for section in ("disagreement_by_regime", "disagreement_by_strategy",
                    "disagreement_by_policy_state"):
        if not isinstance(report.get(section), list):
            errors.append(f"{section} must be a list")

    if not isinstance(report.get("weighting_diagnostics"), list):
        errors.append("weighting_diagnostics must be a list")

    if not isinstance(report.get("warning_flags"), list):
        errors.append("warning_flags must be a list")

    return (len(errors) == 0, errors)
