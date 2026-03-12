"""V2 Scanner — shared duplicate suppression framework.

Detects and suppresses duplicate and near-duplicate candidates so
downstream consumers receive a clean, non-redundant set.

Concepts
--------
**Dedup key**: a hashable tuple that uniquely identifies a candidate's
structural identity (symbol, strategy, expiration, strikes).  Two
candidates with the same dedup key are *exact duplicates* — they
represent the same trade constructed via different code paths.

**Near-duplicate**: candidates with the same structural identity but
slightly different quote values (e.g. due to race conditions or
rounding).  These share the same dedup key and are collapsed to the
best one via the keeper policy.

**Keeper policy**: When duplicates are detected, the framework
deterministically selects one "keeper" and suppresses the rest.
The policy is explicit and inspectable.

Keeper preference order (first criterion that distinguishes wins):
1. Better quote quality score (fewer missing/inverted quotes).
2. Better liquidity score (higher min-leg OI × volume).
3. Richer diagnostics (more completed checks).
4. Stable tie-break by candidate_id (lexicographic).

Family extension
----------------
The default ``candidate_dedup_key()`` works for vertical spreads
(2-leg, single expiry).  Families with richer structure (iron condors,
butterflies, calendars/diagonals) can provide custom key functions
via the ``key_fn`` parameter.

Examples:
- Vertical:   (symbol, strategy_id, expiration, frozenset({short_strike, long_strike}))
- Condor:     ... + inner/outer wing strikes
- Calendar:   ... + back_expiration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.services.scanner_v2.contracts import V2Candidate, V2CheckResult
from app.services.scanner_v2.diagnostics.builder import DiagnosticsBuilder
from app.services.scanner_v2.diagnostics.reason_codes import (
    REJECT_EXACT_DUPLICATE,
    WARN_NEAR_DUPLICATE_SUPPRESSED,
    PASS_DEDUP_UNIQUE,
)


# =====================================================================
#  Dedup key helpers
# =====================================================================

def candidate_dedup_key(cand: V2Candidate) -> tuple:
    """Default dedup key for any family.

    Returns a hashable tuple: (symbol, strategy_id, expiration,
    frozenset of (side, strike) pairs).

    This naturally handles vertical spreads, and should work for any
    family where legs are fully identified by side + strike within a
    single expiration.
    """
    leg_tuples = frozenset(
        (leg.side, leg.strike, leg.option_type)
        for leg in cand.legs
    )
    return (
        cand.symbol,
        cand.strategy_id,
        cand.expiration,
        leg_tuples,
    )


# =====================================================================
#  Keeper scoring
# =====================================================================

def _quote_quality_score(cand: V2Candidate) -> float:
    """Higher is better.  Counts legs with valid, non-inverted quotes."""
    score = 0.0
    for leg in cand.legs:
        if leg.bid is not None and leg.ask is not None:
            if leg.ask >= leg.bid:
                score += 1.0
            else:
                score -= 1.0  # Penalty for inverted
        else:
            score -= 0.5  # Penalty for missing
    return score


def _liquidity_score(cand: V2Candidate) -> float:
    """Higher is better.  Product of min-leg OI and min-leg volume."""
    oi_vals = [
        leg.open_interest for leg in cand.legs
        if leg.open_interest is not None
    ]
    vol_vals = [
        leg.volume for leg in cand.legs
        if leg.volume is not None
    ]
    min_oi = min(oi_vals) if oi_vals else 0
    min_vol = min(vol_vals) if vol_vals else 0
    return float(min_oi * min_vol)


def _diagnostics_richness(cand: V2Candidate) -> int:
    """Higher is better.  Count of completed diagnostic checks."""
    d = cand.diagnostics
    return (
        len(d.structural_checks)
        + len(d.quote_checks)
        + len(d.liquidity_checks)
        + len(d.math_checks)
    )


def _keeper_sort_key(cand: V2Candidate) -> tuple:
    """Sort key for keeper selection.  Higher tuple wins.

    Deterministic: if all scores tie, candidate_id breaks the tie.
    """
    return (
        _quote_quality_score(cand),
        _liquidity_score(cand),
        _diagnostics_richness(cand),
        cand.candidate_id,  # Stable lexicographic tie-break
    )


# =====================================================================
#  Dedup result
# =====================================================================

@dataclass
class DedupResult:
    """Summary of duplicate suppression for a scan run.

    Attributes
    ----------
    total_before
        Candidates entering dedup.
    total_after
        Unique candidates after dedup.
    duplicates_suppressed
        Number of candidates suppressed.
    groups
        Mapping of dedup_key → list of candidate_ids in that group.
        Only populated for groups with >1 member (actual duplicates).
    keeper_ids
        Set of candidate_ids that were kept.
    suppressed_ids
        Set of candidate_ids that were suppressed.
    """
    total_before: int = 0
    total_after: int = 0
    duplicates_suppressed: int = 0
    groups: dict[str, list[str]] = field(default_factory=dict)
    keeper_ids: set[str] = field(default_factory=set)
    suppressed_ids: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_before": self.total_before,
            "total_after": self.total_after,
            "duplicates_suppressed": self.duplicates_suppressed,
            "duplicate_groups": {
                k: v for k, v in self.groups.items()
            },
            "keeper_count": len(self.keeper_ids),
            "suppressed_count": len(self.suppressed_ids),
        }


# =====================================================================
#  Main dedup runner
# =====================================================================

def run_dedup(
    candidates: list[V2Candidate],
    *,
    key_fn: Callable[[V2Candidate], tuple] | None = None,
) -> tuple[list[V2Candidate], DedupResult]:
    """Suppress duplicate candidates, returning unique list + result.

    Only operates on candidates that have NOT been rejected by prior
    phases.  Already-rejected candidates pass through unchanged.

    Parameters
    ----------
    candidates
        Full list of candidates (passed + rejected).
    key_fn
        Custom dedup key function.  Defaults to
        ``candidate_dedup_key()``.

    Returns
    -------
    (deduplicated_candidates, dedup_result)
        The first element contains all rejected candidates (unchanged)
        plus one keeper per dedup group.  Suppressed duplicates are
        moved to rejected status with appropriate diagnostics.
        The second element is a DedupResult summary.
    """
    get_key = key_fn or candidate_dedup_key
    result = DedupResult()

    # Separate already-rejected from live candidates
    rejected_prior = [c for c in candidates if c.diagnostics.reject_reasons]
    live = [c for c in candidates if not c.diagnostics.reject_reasons]

    result.total_before = len(live)

    if not live:
        result.total_after = 0
        return list(rejected_prior), result

    # Group by dedup key
    groups: dict[tuple, list[V2Candidate]] = {}
    for cand in live:
        key = get_key(cand)
        groups.setdefault(key, []).append(cand)

    keepers: list[V2Candidate] = []
    suppressed: list[V2Candidate] = []

    for key, group in groups.items():
        if len(group) == 1:
            # Unique — mark as passing dedup
            keeper = group[0]
            _mark_unique(keeper)
            keepers.append(keeper)
            result.keeper_ids.add(keeper.candidate_id)
        else:
            # Duplicates found — select keeper, suppress rest
            group.sort(key=_keeper_sort_key, reverse=True)
            keeper = group[0]
            dupes = group[1:]

            _mark_unique(keeper)
            keepers.append(keeper)
            result.keeper_ids.add(keeper.candidate_id)

            key_str = str(key)
            result.groups[key_str] = [c.candidate_id for c in group]

            for dupe in dupes:
                _mark_suppressed(dupe, keeper)
                suppressed.append(dupe)
                result.suppressed_ids.add(dupe.candidate_id)

    result.total_after = len(keepers)
    result.duplicates_suppressed = len(suppressed)

    # Combine: prior rejected + suppressed (now rejected) + keepers
    return rejected_prior + suppressed + keepers, result


# =====================================================================
#  Diagnostics helpers
# =====================================================================

def _mark_unique(cand: V2Candidate) -> None:
    """Mark a candidate as passing dedup (unique or selected keeper)."""
    builder = DiagnosticsBuilder(source_phase="D2")
    builder.add_pass(
        PASS_DEDUP_UNIQUE,
        source_check="dedup",
        message="Candidate is unique or was selected as keeper",
    )
    builder.set_check_results("dedup", [
        V2CheckResult("dedup_unique", True, "unique or keeper"),
    ])
    builder.apply(cand.diagnostics)


def _mark_suppressed(
    dupe: V2Candidate,
    keeper: V2Candidate,
) -> None:
    """Mark a candidate as suppressed duplicate with full diagnostics."""
    builder = DiagnosticsBuilder(source_phase="D2")
    builder.add_reject(
        REJECT_EXACT_DUPLICATE,
        source_check="dedup",
        message=(
            f"Duplicate of {keeper.candidate_id}; "
            f"keeper selected by quality score"
        ),
        keeper_id=keeper.candidate_id,
        keeper_quote_quality=_quote_quality_score(keeper),
        dupe_quote_quality=_quote_quality_score(dupe),
        keeper_liquidity=_liquidity_score(keeper),
        dupe_liquidity=_liquidity_score(dupe),
    )
    builder.set_check_results("dedup", [
        V2CheckResult(
            "dedup_unique", False,
            f"suppressed — keeper={keeper.candidate_id}",
        ),
    ])
    builder.apply(dupe.diagnostics)
