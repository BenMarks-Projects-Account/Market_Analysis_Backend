"""Comparison harness — candidate equivalence and matching.

Defines how to structurally identify the "same" candidate across
legacy and V2 outputs, even when the two systems generate different
candidate IDs.

Comparison key
──────────────
A comparison key is a stable identity string built from the candidate's
structural properties:

    ``"{symbol}|{strategy_id}|{expiration}|{sorted_strikes}"``

Example:
    ``"SPY|put_credit_spread|2026-03-20|585.0/590.0"``

This works because two candidates with the same underlying, strategy,
expiration, and strikes are structurally equivalent regardless of which
system built them.

Matching
────────
``match_candidates()`` takes two lists of normalized candidate dicts
(one from legacy, one from V2) and produces matched pairs plus
unmatched residuals.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.scanner_v2.comparison.contracts import (
    CandidateMatch,
    DiagnosticsDiff,
    MetricDelta,
)

_log = logging.getLogger("bentrade.scanner_v2.comparison.equivalence")


# ── Comparison key builders ─────────────────────────────────────────

def build_comparison_key(candidate: dict[str, Any]) -> str:
    """Build a stable structural identity for a candidate.

    Works for both legacy dicts and V2 serialized dicts.
    Falls back to best-effort extraction from available fields.
    """
    symbol = _extract_symbol(candidate)
    strategy = _extract_strategy(candidate)
    expiration = _extract_expiration(candidate)
    strikes = _extract_sorted_strikes(candidate)

    return f"{symbol}|{strategy}|{expiration}|{strikes}"


def _extract_symbol(c: dict[str, Any]) -> str:
    return str(c.get("symbol", "")).upper()


def _extract_strategy(c: dict[str, Any]) -> str:
    # V2: strategy_id; legacy: strategy_id or setup_type or strategy
    for key in ("strategy_id", "setup_type", "strategy"):
        val = c.get(key)
        if val:
            return str(val)
    return "unknown"


def _extract_expiration(c: dict[str, Any]) -> str:
    return str(c.get("expiration", ""))


def _extract_sorted_strikes(c: dict[str, Any]) -> str:
    """Extract strikes from legs array (V2 or legacy) and sort them."""
    legs = c.get("legs", [])
    if legs:
        strikes = sorted(
            leg.get("strike", 0.0)
            for leg in legs
            if leg.get("strike") is not None
        )
        return "/".join(f"{s:g}" for s in strikes)

    # Legacy fallback: short_strike / long_strike fields
    short = c.get("short_strike")
    long = c.get("long_strike")
    if short is not None and long is not None:
        strikes = sorted([float(short), float(long)])
        return "/".join(f"{s:g}" for s in strikes)

    return ""


# ── Candidate matching ──────────────────────────────────────────────

def match_candidates(
    legacy_candidates: list[dict[str, Any]],
    v2_candidates: list[dict[str, Any]],
) -> list[CandidateMatch]:
    """Match legacy and V2 candidates by structural comparison key.

    Returns a list of ``CandidateMatch`` objects covering:
    - Matched candidates (found in both)
    - Legacy-only candidates
    - V2-only candidates

    For matched candidates, metric deltas and diagnostics diffs are computed.
    """
    # Build key → candidate maps
    legacy_by_key: dict[str, dict[str, Any]] = {}
    for c in legacy_candidates:
        key = build_comparison_key(c)
        legacy_by_key[key] = c

    v2_by_key: dict[str, dict[str, Any]] = {}
    for c in v2_candidates:
        key = build_comparison_key(c)
        v2_by_key[key] = c

    all_keys = set(legacy_by_key) | set(v2_by_key)
    matches: list[CandidateMatch] = []

    for key in sorted(all_keys):
        legacy_c = legacy_by_key.get(key)
        v2_c = v2_by_key.get(key)

        if legacy_c and v2_c:
            match = _build_matched(key, legacy_c, v2_c)
        elif legacy_c:
            match = CandidateMatch(
                comparison_key=key,
                match_type="legacy_only",
                legacy_candidate=legacy_c,
            )
        else:
            match = CandidateMatch(
                comparison_key=key,
                match_type="v2_only",
                v2_candidate=v2_c,
            )

        matches.append(match)

    _log.info(
        "Candidate matching: %d matched, %d legacy-only, %d v2-only",
        sum(1 for m in matches if m.match_type == "matched"),
        sum(1 for m in matches if m.match_type == "legacy_only"),
        sum(1 for m in matches if m.match_type == "v2_only"),
    )

    return matches


# ── Matched candidate analysis ──────────────────────────────────────

def _build_matched(
    key: str,
    legacy: dict[str, Any],
    v2: dict[str, Any],
) -> CandidateMatch:
    """Build a fully analyzed match for a candidate found in both systems."""
    metric_deltas = _compute_metric_deltas(legacy, v2)
    diag_diff = _compute_diagnostics_diff(legacy, v2)

    # Trust signals
    structurally_improved = _is_structurally_improved(legacy, v2, diag_diff)
    math_recomputed = _has_recomputed_math(v2)
    diagnostics_richer = _is_diagnostics_richer(v2)

    notes = _generate_notes(legacy, v2, metric_deltas, diag_diff)

    return CandidateMatch(
        comparison_key=key,
        match_type="matched",
        legacy_candidate=legacy,
        v2_candidate=v2,
        metric_deltas=metric_deltas,
        diagnostics_diff=diag_diff,
        v2_structurally_improved=structurally_improved,
        v2_math_recomputed=math_recomputed,
        v2_diagnostics_richer=diagnostics_richer,
        notes=notes,
    )


# ── Metric comparison ───────────────────────────────────────────────

# Metrics extracted from both systems for comparison.
# (legacy_key, v2_key) — supports different field names between systems.
_COMPARABLE_METRICS: list[tuple[str, str, str]] = [
    # (display_name, legacy_path, v2_path)
    ("net_credit",  "net_credit",       "math.net_credit"),
    ("max_profit",  "max_profit",       "math.max_profit"),
    ("max_loss",    "max_loss",         "math.max_loss"),
    ("width",       "width",            "math.width"),
    ("pop",         "p_win_used",       "math.pop"),
    ("ev",          "ev_per_share",     "math.ev"),
    ("ror",         "return_on_risk",   "math.ror"),
]


def _get_nested(d: dict[str, Any], path: str) -> Any:
    """Get a value from a nested dict using dot-separated path."""
    parts = path.split(".")
    current = d
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _compute_metric_deltas(
    legacy: dict[str, Any],
    v2: dict[str, Any],
) -> list[MetricDelta]:
    """Compare numeric metrics between legacy and V2 candidates."""
    deltas: list[MetricDelta] = []

    for metric_name, legacy_path, v2_path in _COMPARABLE_METRICS:
        lv = _get_nested(legacy, legacy_path)
        vv = _get_nested(v2, v2_path)

        # Convert to float if possible
        lv_f = _safe_float(lv)
        vv_f = _safe_float(vv)

        abs_diff = None
        pct_diff = None
        if lv_f is not None and vv_f is not None:
            abs_diff = abs(vv_f - lv_f)
            if lv_f != 0:
                pct_diff = abs_diff / abs(lv_f)

        deltas.append(MetricDelta(
            metric=metric_name,
            legacy_value=lv_f,
            v2_value=vv_f,
            abs_diff=abs_diff,
            pct_diff=pct_diff,
        ))

    return deltas


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Diagnostics comparison ──────────────────────────────────────────

def _compute_diagnostics_diff(
    legacy: dict[str, Any],
    v2: dict[str, Any],
) -> DiagnosticsDiff:
    """Compare diagnostic/rejection information between systems."""
    # Legacy: rejection_codes or data_quality_flags lists
    legacy_codes = list(legacy.get("rejection_codes", []))
    if not legacy_codes:
        legacy_codes = list(legacy.get("data_quality_flags", []))

    # Legacy pass status
    legacy_passed = not legacy_codes

    # V2: diagnostics.reject_reasons
    v2_diag = v2.get("diagnostics", {})
    v2_codes = list(v2_diag.get("reject_reasons", []))
    v2_passed = v2.get("passed", not v2_codes)

    legacy_set = set(legacy_codes)
    v2_set = set(v2_codes)

    return DiagnosticsDiff(
        legacy_rejection_codes=legacy_codes,
        v2_rejection_codes=v2_codes,
        legacy_only_rejections=sorted(legacy_set - v2_set),
        v2_only_rejections=sorted(v2_set - legacy_set),
        shared_rejections=sorted(legacy_set & v2_set),
        legacy_passed=legacy_passed,
        v2_passed=v2_passed,
        v2_structural_checks=len(v2_diag.get("structural_checks", [])),
        v2_quote_checks=len(v2_diag.get("quote_checks", [])),
        v2_liquidity_checks=len(v2_diag.get("liquidity_checks", [])),
        v2_math_checks=len(v2_diag.get("math_checks", [])),
        v2_warnings=list(v2_diag.get("warnings", [])),
        v2_pass_reasons=list(v2_diag.get("pass_reasons", [])),
    )


# ── Trust signal helpers ────────────────────────────────────────────

def _is_structurally_improved(
    legacy: dict[str, Any],
    v2: dict[str, Any],
    diag_diff: DiagnosticsDiff,
) -> bool:
    """True if V2 caught structural issues legacy missed."""
    # V2 rejected but legacy accepted → V2 caught something
    if diag_diff.legacy_passed and not diag_diff.v2_passed:
        structural_rejects = {
            "v2_malformed_legs", "v2_mismatched_expiry",
            "v2_invalid_width", "v2_impossible_pricing",
        }
        if any(r in structural_rejects for r in diag_diff.v2_rejection_codes):
            return True
    return False


def _has_recomputed_math(v2: dict[str, Any]) -> bool:
    """True if V2 has recomputed math from raw quotes."""
    math = v2.get("math", {})
    notes = math.get("notes", {})
    # Any computation note means math was recomputed
    return bool(notes)


def _is_diagnostics_richer(v2: dict[str, Any]) -> bool:
    """True if V2 provides meaningful diagnostic detail."""
    diag = v2.get("diagnostics", {})
    total_checks = (
        len(diag.get("structural_checks", []))
        + len(diag.get("quote_checks", []))
        + len(diag.get("liquidity_checks", []))
        + len(diag.get("math_checks", []))
    )
    return total_checks > 0


# ── Note generation ─────────────────────────────────────────────────

def _generate_notes(
    legacy: dict[str, Any],
    v2: dict[str, Any],
    deltas: list[MetricDelta],
    diag_diff: DiagnosticsDiff,
) -> list[str]:
    """Generate human-readable observations about the comparison."""
    notes: list[str] = []

    # Pass/reject status difference
    if diag_diff.legacy_passed and not diag_diff.v2_passed:
        notes.append(
            f"V2 REJECTED (legacy accepted): {diag_diff.v2_rejection_codes}"
        )
    elif not diag_diff.legacy_passed and diag_diff.v2_passed:
        notes.append(
            f"V2 ACCEPTED (legacy rejected): {diag_diff.legacy_rejection_codes}"
        )

    # Large metric differences
    for delta in deltas:
        if delta.pct_diff is not None and delta.pct_diff > 0.10:
            notes.append(
                f"Metric '{delta.metric}' differs by "
                f"{delta.pct_diff:.1%}: legacy={delta.legacy_value}, "
                f"v2={delta.v2_value}"
            )

    # Missing on one side
    for delta in deltas:
        if delta.legacy_value is not None and delta.v2_value is None:
            notes.append(f"Metric '{delta.metric}' missing in V2")
        elif delta.legacy_value is None and delta.v2_value is not None:
            notes.append(f"Metric '{delta.metric}' missing in legacy")

    return notes
