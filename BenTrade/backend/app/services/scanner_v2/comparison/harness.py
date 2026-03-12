"""Comparison harness — the side-by-side runner.

``compare_scanner_family()`` is the primary entry point.  It runs legacy
and V2 scanners on the same snapshot and produces a structured
``ComparisonReport``.

How it works
────────────
1. Receive a ``ComparisonSnapshot`` (frozen market data).
2. Run the **legacy** scanner for the given scanner_key on that data.
3. Run the **V2** scanner for the same scanner_key on the same data.
4. Normalize both outputs into comparable candidate dicts.
5. Match candidates by structural comparison key.
6. Compute metric deltas, diagnostics diffs, trust signals.
7. Aggregate into a ``ComparisonReport``.

The harness does NOT make pass/fail judgments about differences.
It exposes them for human inspection and targeted test assertions.

Usage
─────
    from app.services.scanner_v2.comparison import (
        compare_scanner_family,
        load_snapshot,
    )

    snapshot = load_snapshot("tests/fixtures/scanner_snapshots/spy_puts.json")
    report = compare_scanner_family(
        scanner_key="put_credit_spread",
        snapshot=snapshot,
    )
    print(report.overlap_count, report.legacy_only_count, report.v2_only_count)

For testing without live scanner invocation, use
``compare_from_results()`` with pre-computed scanner outputs.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.services.scanner_v2.comparison.contracts import (
    COMPARISON_CONTRACT_VERSION,
    CandidateMatch,
    ComparisonReport,
    ComparisonSnapshot,
    MetricDelta,
)
from app.services.scanner_v2.comparison.equivalence import (
    match_candidates,
)

_log = logging.getLogger("bentrade.scanner_v2.comparison.harness")


# ── Primary entry point ─────────────────────────────────────────────

def compare_scanner_family(
    *,
    scanner_key: str,
    snapshot: ComparisonSnapshot,
    legacy_runner: Any | None = None,
    v2_runner: Any | None = None,
    legacy_preset: str = "wide",
) -> ComparisonReport:
    """Run legacy and V2 scanners on the same snapshot, compare outputs.

    Parameters
    ----------
    scanner_key
        The scanner key to compare (e.g. ``"put_credit_spread"``).
    snapshot
        Frozen market-data snapshot.
    legacy_runner
        Callable that runs legacy scanner.  Signature:
        ``(scanner_key, snapshot, preset) → dict`` returning legacy
        output shape.  If ``None``, uses default legacy runner.
    v2_runner
        Callable that runs V2 scanner.  Signature:
        ``(scanner_key, snapshot) → V2ScanResult | dict``.
        If ``None``, uses default V2 runner.
    legacy_preset
        Preset level for legacy scanner.  Use ``"wide"`` to get
        maximum candidate counts for comparison.

    Returns
    -------
    ComparisonReport
        Structured comparison with all matches, deltas, trust signals.
    """
    t0 = time.monotonic()
    comparison_id = f"cmp_{uuid.uuid4().hex[:12]}"

    _log.info(
        "Starting comparison %s: scanner_key=%s snapshot=%s",
        comparison_id, scanner_key, snapshot.snapshot_id,
    )

    # ── Run legacy ──────────────────────────────────────────────
    t_leg = time.monotonic()
    if legacy_runner:
        legacy_result = legacy_runner(scanner_key, snapshot, legacy_preset)
    else:
        legacy_result = _default_legacy_runner(
            scanner_key, snapshot, legacy_preset,
        )
    legacy_ms = (time.monotonic() - t_leg) * 1000

    # ── Run V2 ──────────────────────────────────────────────────
    t_v2 = time.monotonic()
    if v2_runner:
        v2_result = v2_runner(scanner_key, snapshot)
    else:
        v2_result = _default_v2_runner(scanner_key, snapshot)
    v2_ms = (time.monotonic() - t_v2) * 1000

    # ── Compare ─────────────────────────────────────────────────
    report = compare_from_results(
        scanner_key=scanner_key,
        snapshot=snapshot,
        legacy_result=legacy_result,
        v2_result=v2_result,
        comparison_id=comparison_id,
    )

    report.legacy_elapsed_ms = legacy_ms
    report.v2_elapsed_ms = v2_ms
    report.comparison_elapsed_ms = (time.monotonic() - t0) * 1000

    _log.info(
        "Comparison %s complete: %d matched, %d legacy-only, %d v2-only "
        "(legacy=%.0fms, v2=%.0fms)",
        comparison_id, report.overlap_count, report.legacy_only_count,
        report.v2_only_count, legacy_ms, v2_ms,
    )

    return report


# ── Compare from pre-computed results ───────────────────────────────

def compare_from_results(
    *,
    scanner_key: str,
    snapshot: ComparisonSnapshot,
    legacy_result: dict[str, Any],
    v2_result: dict[str, Any],
    comparison_id: str = "",
) -> ComparisonReport:
    """Build a comparison report from pre-computed legacy and V2 results.

    This is the core comparison logic, usable without live scanner
    invocation.  Useful for test fixtures and offline analysis.

    Parameters
    ----------
    scanner_key
        Scanner key being compared.
    snapshot
        The shared input snapshot.
    legacy_result
        Legacy output dict with ``accepted_trades``, ``candidate_count``,
        ``filter_trace``, etc.
    v2_result
        V2 output dict (serialized ``V2ScanResult.to_dict()``).
    comparison_id
        Optional comparison run ID.

    Returns
    -------
    ComparisonReport
    """
    if not comparison_id:
        comparison_id = f"cmp_{uuid.uuid4().hex[:12]}"

    # ── Extract candidate lists ─────────────────────────────────
    legacy_passed = legacy_result.get("accepted_trades", [])
    legacy_rejected = legacy_result.get("rejected_trades", [])
    all_legacy = legacy_passed + legacy_rejected

    v2_passed = v2_result.get("candidates", [])
    v2_rejected = v2_result.get("rejected", [])
    all_v2 = v2_passed + v2_rejected

    # ── Match ───────────────────────────────────────────────────
    matches = match_candidates(all_legacy, all_v2)

    # ── Categorize ──────────────────────────────────────────────
    overlap = [m for m in matches if m.match_type == "matched"]
    legacy_only = [m for m in matches if m.match_type == "legacy_only"]
    v2_only = [m for m in matches if m.match_type == "v2_only"]

    # ── Trust signals ───────────────────────────────────────────
    v2_caught_broken = sum(
        1 for m in overlap
        if m.v2_structurally_improved is True
    )
    v2_new_valid = sum(
        1 for m in matches
        if m.match_type == "v2_only"
        and m.v2_candidate
        and m.v2_candidate.get("passed", False)
    )
    v2_diag_richer = sum(
        1 for m in overlap
        if m.v2_diagnostics_richer is True
    )

    # ── Metric summary ──────────────────────────────────────────
    metric_summary = _aggregate_metric_deltas(overlap)

    # ── Legacy counts ───────────────────────────────────────────
    leg_constructed = legacy_result.get("candidate_count", len(all_legacy))
    leg_passed = legacy_result.get("accepted_count", len(legacy_passed))
    leg_rejected = leg_constructed - leg_passed

    # ── V2 counts ───────────────────────────────────────────────
    v2_constructed = v2_result.get("total_constructed", len(all_v2))
    v2_passed_count = v2_result.get("total_passed", len(v2_passed))
    v2_rejected_count = v2_result.get("total_rejected", len(v2_rejected))

    # ── Rejection counts ────────────────────────────────────────
    legacy_rej_counts = {}
    ft = legacy_result.get("filter_trace", {})
    if ft:
        legacy_rej_counts = dict(ft.get("rejection_reason_counts", {}))

    v2_rej_counts = dict(v2_result.get("reject_reason_counts", {}))

    # ── Phase trace ─────────────────────────────────────────────
    legacy_stages = ft.get("stage_counts", [])
    v2_phases = v2_result.get("phase_counts", [])

    # ── Family detection ────────────────────────────────────────
    family = v2_result.get("family_key", "")
    if not family:
        family = _infer_family(scanner_key)

    # ── Anomalies ───────────────────────────────────────────────
    anomalies = _detect_anomalies(
        leg_constructed, v2_constructed,
        leg_passed, v2_passed_count,
        overlap, legacy_only, v2_only,
    )

    # ── Conclusions ─────────────────────────────────────────────
    conclusions = _generate_conclusions(
        overlap_count=len(overlap),
        legacy_only_count=len(legacy_only),
        v2_only_count=len(v2_only),
        v2_caught_broken=v2_caught_broken,
        v2_new_valid=v2_new_valid,
        metric_summary=metric_summary,
    )

    return ComparisonReport(
        comparison_id=comparison_id,
        comparison_version=COMPARISON_CONTRACT_VERSION,
        scanner_family=family,
        scanner_key=scanner_key,
        snapshot_id=snapshot.snapshot_id,
        symbol=snapshot.symbol,
        underlying_price=snapshot.underlying_price,
        snapshot_metadata=snapshot.metadata,
        legacy_total_constructed=leg_constructed,
        legacy_total_passed=leg_passed,
        legacy_total_rejected=leg_rejected,
        v2_total_constructed=v2_constructed,
        v2_total_passed=v2_passed_count,
        v2_total_rejected=v2_rejected_count,
        overlap_count=len(overlap),
        legacy_only_count=len(legacy_only),
        v2_only_count=len(v2_only),
        matches=matches,
        legacy_rejection_counts=legacy_rej_counts,
        v2_rejection_counts=v2_rej_counts,
        legacy_stage_counts=legacy_stages,
        v2_phase_counts=v2_phases,
        v2_caught_broken=v2_caught_broken,
        v2_new_valid=v2_new_valid,
        v2_diagnostics_richer_count=v2_diag_richer,
        metric_summary=metric_summary,
        anomalies=anomalies,
        conclusions=conclusions,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Metric aggregation ──────────────────────────────────────────────

def _aggregate_metric_deltas(
    matched: list[CandidateMatch],
) -> dict[str, dict[str, float | None]]:
    """Aggregate per-metric deltas across all matched candidates."""
    if not matched:
        return {}

    # Collect deltas by metric name
    by_metric: dict[str, list[MetricDelta]] = {}
    for m in matched:
        for delta in m.metric_deltas:
            by_metric.setdefault(delta.metric, []).append(delta)

    summary: dict[str, dict[str, float | None]] = {}
    for metric_name, deltas in by_metric.items():
        abs_diffs = [d.abs_diff for d in deltas if d.abs_diff is not None]
        pct_diffs = [d.pct_diff for d in deltas if d.pct_diff is not None]

        summary[metric_name] = {
            "count": len(deltas),
            "with_values": len(abs_diffs),
            "mean_abs_diff": (
                sum(abs_diffs) / len(abs_diffs) if abs_diffs else None
            ),
            "max_abs_diff": max(abs_diffs) if abs_diffs else None,
            "mean_pct_diff": (
                sum(pct_diffs) / len(pct_diffs) if pct_diffs else None
            ),
            "max_pct_diff": max(pct_diffs) if pct_diffs else None,
        }

    return summary


# ── Family inference ────────────────────────────────────────────────

_FAMILY_MAP: dict[str, str] = {
    "put_credit_spread": "vertical_spreads",
    "call_credit_spread": "vertical_spreads",
    "put_debit": "vertical_spreads",
    "call_debit": "vertical_spreads",
    "iron_condor": "iron_condors",
    "butterfly_debit": "butterflies",
    "iron_butterfly": "butterflies",
    "calendar_spread": "calendars",
    "calendar_call_spread": "calendars",
    "calendar_put_spread": "calendars",
}


def _infer_family(scanner_key: str) -> str:
    return _FAMILY_MAP.get(scanner_key, "unknown")


# ── Anomaly detection ───────────────────────────────────────────────

def _detect_anomalies(
    leg_constructed: int,
    v2_constructed: int,
    leg_passed: int,
    v2_passed: int,
    overlap: list[CandidateMatch],
    legacy_only: list[CandidateMatch],
    v2_only: list[CandidateMatch],
) -> list[str]:
    """Detect unusual comparison situations worth human review."""
    anomalies: list[str] = []

    # Extreme count divergence
    total = max(leg_constructed, v2_constructed, 1)
    count_diff = abs(leg_constructed - v2_constructed) / total
    if count_diff > 0.5 and total > 10:
        anomalies.append(
            f"Construction count divergence >50%: "
            f"legacy={leg_constructed}, v2={v2_constructed}"
        )

    # No overlap at all
    if overlap and not legacy_only and not v2_only:
        pass  # Perfect — no anomaly
    elif not overlap and (legacy_only or v2_only):
        anomalies.append(
            "Zero candidate overlap between legacy and V2"
        )

    # V2 passed more candidates than legacy constructed
    if v2_passed > leg_constructed and leg_constructed > 0:
        anomalies.append(
            f"V2 passed more candidates ({v2_passed}) than "
            f"legacy constructed ({leg_constructed})"
        )

    return anomalies


# ── Conclusion generation ───────────────────────────────────────────

def _generate_conclusions(
    *,
    overlap_count: int,
    legacy_only_count: int,
    v2_only_count: int,
    v2_caught_broken: int,
    v2_new_valid: int,
    metric_summary: dict[str, dict[str, float | None]],
) -> list[str]:
    """Generate human-readable summary conclusions."""
    conclusions: list[str] = []
    total = overlap_count + legacy_only_count + v2_only_count

    if total == 0:
        conclusions.append("No candidates from either system.")
        return conclusions

    if overlap_count == total:
        conclusions.append("Perfect structural overlap: all candidates match.")
    else:
        conclusions.append(
            f"Overlap: {overlap_count}/{total} "
            f"({overlap_count/total:.0%}). "
            f"Legacy-only: {legacy_only_count}. "
            f"V2-only: {v2_only_count}."
        )

    if v2_caught_broken:
        conclusions.append(
            f"V2 caught {v2_caught_broken} structurally broken candidates "
            f"that legacy accepted."
        )

    if v2_new_valid:
        conclusions.append(
            f"V2 surfaced {v2_new_valid} valid candidates "
            f"that legacy rejected (over-filtering removed)."
        )

    # Check for large metric deltas
    for metric, agg in metric_summary.items():
        mean_pct = agg.get("mean_pct_diff")
        if mean_pct is not None and mean_pct > 0.05:
            conclusions.append(
                f"Mean {metric} difference: {mean_pct:.1%} "
                f"(review recommended)."
            )

    return conclusions


# ── Default runners ─────────────────────────────────────────────────
# Stubs that will be replaced in later prompts when family
# implementations are complete.


def _default_legacy_runner(
    scanner_key: str,
    snapshot: ComparisonSnapshot,
    preset: str,
) -> dict[str, Any]:
    """Placeholder: run legacy scanner on snapshot.

    In a real execution this calls ``strategy_service.generate()``
    with the snapshot's chain data injected.  Since the legacy
    scanner requires async invocation and full service setup, the
    harness defaults to requiring an explicit ``legacy_runner``
    callable for now.
    """
    _log.warning(
        "Using placeholder legacy runner for %s — "
        "inject a real runner via legacy_runner parameter",
        scanner_key,
    )
    return {
        "accepted_trades": [],
        "rejected_trades": [],
        "candidate_count": 0,
        "accepted_count": 0,
        "filter_trace": {
            "preset_name": preset,
            "resolved_thresholds": {},
            "stage_counts": [],
            "rejection_reason_counts": {},
            "data_quality_counts": {},
        },
    }


def _default_v2_runner(
    scanner_key: str,
    snapshot: ComparisonSnapshot,
) -> dict[str, Any]:
    """Placeholder: run V2 scanner on snapshot.

    Will call ``execute_v2_scanner()`` from the migration module
    once family implementations exist.
    """
    _log.warning(
        "Using placeholder V2 runner for %s — "
        "inject a real runner via v2_runner parameter",
        scanner_key,
    )
    return {
        "candidates": [],
        "rejected": [],
        "total_constructed": 0,
        "total_passed": 0,
        "total_rejected": 0,
        "reject_reason_counts": {},
        "phase_counts": [],
        "family_key": _infer_family(scanner_key),
    }
