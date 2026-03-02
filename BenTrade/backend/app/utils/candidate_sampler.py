"""Heap-based soft-cap candidate selection.

Replaces hard truncation (first-N by generation order) with quality-based
selection.  The soft cap runs BETWEEN build_candidates and enrich — it uses
only cheap chain-level data (no API calls).

Two distinct caps:
  generation_cap   — safety ceiling applied inside each plugin's
                     build_candidates to prevent runaway combinatorial
                     explosions (~20 000).  Already applied before
                     candidates reach this module.
  enrichment_cap   — resolved_thresholds.max_candidates from the preset.
                     The ONLY cap that controls how many candidates
                     reach enrichment.  Applied here via select_top_n().

Functions
---------
extract_leg_contracts(candidate) -> list[object]
    Pull contract objects from any strategy's raw candidate dict.

compute_pre_score(candidate) -> float
    Quick quality score using only chain-embedded bid/ask/OI/volume.

select_top_n(candidates, n, generation_cap) -> tuple[list[dict], CapSummary]
    Heap-based top-N selection by pre_score.  Returns selected candidates
    and a CapSummary dict for observability.

CapSummary schema
-----------------
{
    "generation_cap": int,
    "enrichment_cap": int,
    "generated_total": int,           # candidates produced by builder
    "generated_after_generation_cap": int,  # after safety ceiling
    "kept_for_enrichment": int,       # after soft cap (== enrichment_cap or less)
    "cap_reached_generation": bool,
    "cap_reached_enrichment": bool,
    "discarded_due_to_generation_cap": int,
    "discarded_due_to_enrichment_cap": int,
    "pre_score_min": float | None,
    "pre_score_max": float | None,
    "pre_score_median": float | None,
    "pre_score_cutoff": float | None, # lowest pre_score in the kept set
    "penny_count": int,               # legs with penny pricing
    "missing_quote_count": int,       # legs with None bid or ask
}

Pre-score formula
-----------------
  pre_score = bid_ask_quality + liquidity_score - penny_penalty

  bid_ask_quality (0..1):
      mean(1 - spread_pct) across legs
      spread_pct = (ask - bid) / ask  if ask > 0  else 1.0

  liquidity_score (0..~1):
      log2(1 + sum_OI) / 20 + log2(1 + sum_vol) / 20

  penny_penalty:
      0.5 * count_of_penny_legs
      (penny = bid < 0.05 AND ask < 0.10, OR bid is None/0)

  Input fields: leg contract attributes .bid, .ask, .open_interest, .volume
"""

from __future__ import annotations

import heapq
import logging
import math
import statistics
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> float | None:
    """Convert val to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def extract_leg_contracts(candidate: dict[str, Any]) -> list[Any]:
    """Pull contract objects from any strategy's raw candidate dict.

    Supports:
      - iron_condor: legs[i]["_contract"]
      - credit_spread / debit_spread: short_leg, long_leg
      - calendars: near_leg, far_leg
      - butterflies: short_leg, long_leg  (or legs[])

    Returns a list of contract-like objects (may have .bid, .ask, etc.).
    """
    contracts: list[Any] = []

    # Path 1: legs[] array (iron condors, possibly butterflies)
    legs = candidate.get("legs")
    if isinstance(legs, list) and legs:
        for leg in legs:
            if isinstance(leg, dict):
                c = leg.get("_contract")
                if c is not None:
                    contracts.append(c)
            elif leg is not None:
                contracts.append(leg)
        if contracts:
            return contracts

    # Path 2: named leg fields (credit/debit spreads, butterflies)
    for field in ("short_leg", "long_leg"):
        obj = candidate.get(field)
        if obj is not None:
            contracts.append(obj)

    # Path 3: calendar-specific named legs
    for field in ("near_leg", "far_leg"):
        obj = candidate.get(field)
        if obj is not None:
            contracts.append(obj)

    return contracts


def compute_pre_score(candidate: dict[str, Any]) -> float:
    """Cheap pre-score for candidate quality ranking.

    Uses ONLY chain-embedded data (no API calls).

    Returns a float; higher = better candidate quality.

    Components (additive):
      bid_ask_quality  ∈ [0, 1]  — tightness of bid-ask spreads
      liquidity_score  ∈ [0, ~1] — log-scaled OI + volume
      penny_penalty    ≥ 0       — 0.5 per penny-priced leg (subtracted)
    """
    contracts = extract_leg_contracts(candidate)
    if not contracts:
        return 0.0

    spread_quality_scores: list[float] = []
    total_oi = 0.0
    total_vol = 0.0
    penny_count = 0
    missing_quote_count = 0

    for c in contracts:
        bid = _safe_float(getattr(c, "bid", None))
        ask = _safe_float(getattr(c, "ask", None))
        oi = _safe_float(getattr(c, "open_interest", None))
        vol = _safe_float(getattr(c, "volume", None))

        # --- bid/ask quality ---
        if bid is None or ask is None:
            missing_quote_count += 1
            spread_quality_scores.append(0.0)
        elif ask > 0:
            spread_pct = (ask - bid) / ask if ask > bid else 0.0
            spread_quality_scores.append(max(0.0, 1.0 - spread_pct))
        else:
            # ask <= 0: garbage quote
            spread_quality_scores.append(0.0)

        # --- penny detection ---
        # Penny = trivial pricing where the contract is nearly worthless
        if bid is None or bid <= 0:
            penny_count += 1
        elif bid < 0.05 and (ask is None or ask < 0.10):
            penny_count += 1

        # --- liquidity ---
        if oi is not None and oi > 0:
            total_oi += oi
        if vol is not None and vol > 0:
            total_vol += vol

    # Component 1: bid-ask quality (mean across legs)
    bid_ask_quality = (
        sum(spread_quality_scores) / len(spread_quality_scores)
        if spread_quality_scores else 0.0
    )

    # Component 2: liquidity (log-scaled, range ~0–1 for typical values)
    # log2(1 + 10000) ≈ 13.3 → 13.3/20 ≈ 0.67
    # log2(1 + 1000)  ≈ 10.0 → 10.0/20 ≈ 0.50
    # log2(1 + 100)   ≈  6.7 →  6.7/20 ≈ 0.33
    liquidity_score = (
        math.log2(1.0 + total_oi) / 20.0
        + math.log2(1.0 + total_vol) / 20.0
    )

    # Component 3: penny penalty
    penny_penalty = 0.5 * penny_count

    pre_score = bid_ask_quality + liquidity_score - penny_penalty

    return round(pre_score, 6)


# ── High-water safety ceiling for bypass mode ──────────────────────────
# When bypass_enrichment_cap is True, all candidates pass through.
# To prevent accidental OOM or multi-hour enrichment, clamp at this
# ceiling and log a warning.
BYPASS_HIGH_WATER_MARK: int = 20_000


def select_top_n(
    candidates: list[dict[str, Any]],
    n: int,
    *,
    generation_cap: int = 20_000,
    bypass_enrichment_cap: bool = False,
    bypass_reason: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Heap-based top-N selection by pre_score.

    Parameters
    ----------
    candidates : list[dict]
        Raw candidates from plugin.build_candidates().
        May have already been truncated by the generation_cap safety
        ceiling inside the plugin.
    n : int
        Enrichment cap — resolved_thresholds.max_candidates.
        This is the ONLY cap controlling how many candidates reach enrichment
        **unless** ``bypass_enrichment_cap`` is True.
    generation_cap : int
        Safety ceiling used during candidate generation.
        Passed through for observability only (the truncation itself
        happens inside the plugin builder, not here).
    bypass_enrichment_cap : bool
        When True, keep ALL candidates (up to BYPASS_HIGH_WATER_MARK)
        regardless of ``n``.  Candidates are still scored and sorted by
        pre_score descending.  The cap_summary will include bypass
        metadata for experiment tracing.
    bypass_reason : str | None
        Human-readable reason for bypass (e.g. "credit_spread_wide_experiment").
        Recorded in cap_summary for trace visibility.

    Returns
    -------
    (selected, cap_summary) : tuple
        selected: list of up to *n* candidates (or all if bypassed),
                  highest pre_score first.
        cap_summary: observability dict (see module docstring).
    """
    # generated_total is what the builder actually returned.
    # If the builder hit the generation_cap, generated_total == generation_cap.
    generated_total = len(candidates)
    cap_reached_generation = generated_total >= generation_cap
    # We can't know how many the builder *would* have produced, so
    # discarded_due_to_generation_cap is only knowable when the cap bound.
    discarded_gen = 0  # unknowable without builder cooperation

    # ── Bypass mode: override enrichment cap ──────────────────────────
    original_enrichment_cap = n
    _bypass_high_water_clamped = False
    if bypass_enrichment_cap:
        if generated_total > BYPASS_HIGH_WATER_MARK:
            logger.warning(
                "event=bypass_high_water_clamped generated_total=%d "
                "high_water_mark=%d — clamping bypass to safety ceiling",
                generated_total, BYPASS_HIGH_WATER_MARK,
            )
            n = BYPASS_HIGH_WATER_MARK
            _bypass_high_water_clamped = True
        else:
            # Let all candidates through
            n = generated_total
        logger.info(
            "event=soft_cap_bypass_active original_enrichment_cap=%d "
            "effective_enrichment_cap=%d generated_total=%d",
            original_enrichment_cap, n, generated_total,
        )

    if generated_total == 0:
        _summary = _build_cap_summary(
            generation_cap=generation_cap,
            enrichment_cap=n,
            generated_total=0,
            generated_after_generation_cap=0,
            kept_for_enrichment=0,
            cap_reached_generation=False,
            cap_reached_enrichment=False,
            discarded_due_to_generation_cap=0,
            discarded_due_to_enrichment_cap=0,
            scores=[],
            cutoff=None,
            penny_count=0,
            missing_quote_count=0,
        )
        if bypass_enrichment_cap:
            _summary["bypassed"] = True
            _summary["bypass_enabled"] = True
            _summary["bypass_reason"] = bypass_reason or "experiment"
            _summary["original_enrichment_cap"] = original_enrichment_cap
            _summary["effective_enrichment_cap"] = 0
            _summary["high_water_clamped"] = False
        else:
            _summary["bypassed"] = False
        return [], _summary

    # Score every candidate (cheap — no API calls)
    scored: list[tuple[float, int, dict[str, Any]]] = []
    all_scores: list[float] = []
    penny_total = 0
    missing_total = 0

    for idx, cand in enumerate(candidates):
        score = compute_pre_score(cand)
        cand["_pre_score"] = score
        scored.append((score, idx, cand))
        all_scores.append(score)

        # Count penny / missing for summary
        for c in extract_leg_contracts(cand):
            bid = _safe_float(getattr(c, "bid", None))
            ask = _safe_float(getattr(c, "ask", None))
            if bid is None or ask is None:
                missing_total += 1
            if bid is None or bid <= 0:
                penny_total += 1
            elif bid < 0.05 and (ask is None or ask < 0.10):
                penny_total += 1

    cap_reached_enrichment = generated_total > n

    if cap_reached_enrichment:
        # Use heapq.nlargest — O(total * log(n)), memory-efficient
        top_n = heapq.nlargest(n, scored, key=lambda t: (t[0], -t[1]))
        selected = [t[2] for t in top_n]
        cutoff = min(t[0] for t in top_n)
    else:
        # No cap needed — keep all, sorted by pre_score descending
        scored.sort(key=lambda t: (t[0], -t[1]), reverse=True)
        selected = [t[2] for t in scored]
        cutoff = min(all_scores) if all_scores else None

    discarded_enrich = generated_total - len(selected)

    summary = _build_cap_summary(
        generation_cap=generation_cap,
        enrichment_cap=n,
        generated_total=generated_total,
        generated_after_generation_cap=generated_total,
        kept_for_enrichment=len(selected),
        cap_reached_generation=cap_reached_generation,
        cap_reached_enrichment=cap_reached_enrichment,
        discarded_due_to_generation_cap=discarded_gen,
        discarded_due_to_enrichment_cap=discarded_enrich,
        scores=all_scores,
        cutoff=cutoff if cap_reached_enrichment else None,
        penny_count=penny_total,
        missing_quote_count=missing_total,
    )

    # ── Bypass metadata in cap_summary ────────────────────────────────
    if bypass_enrichment_cap:
        summary["bypassed"] = True
        summary["bypass_enabled"] = True
        summary["bypass_reason"] = bypass_reason or "experiment"
        summary["original_enrichment_cap"] = original_enrichment_cap
        summary["effective_enrichment_cap"] = n
        summary["high_water_clamped"] = _bypass_high_water_clamped
    else:
        summary["bypassed"] = False

    if cap_reached_enrichment:
        logger.info(
            "event=soft_cap_applied generated_total=%d enrichment_cap=%d "
            "kept=%d cutoff=%.4f median=%.4f bypass=%s",
            generated_total, n, len(selected),
            cutoff or 0.0,
            summary.get("pre_score_median") or 0.0,
            bypass_enrichment_cap,
        )

    return selected, summary


def _build_cap_summary(
    *,
    generation_cap: int,
    enrichment_cap: int,
    generated_total: int,
    generated_after_generation_cap: int,
    kept_for_enrichment: int,
    cap_reached_generation: bool,
    cap_reached_enrichment: bool,
    discarded_due_to_generation_cap: int,
    discarded_due_to_enrichment_cap: int,
    scores: list[float],
    cutoff: float | None,
    penny_count: int,
    missing_quote_count: int,
) -> dict[str, Any]:
    """Build observability dict for the soft-cap stage."""
    return {
        "generation_cap": generation_cap,
        "enrichment_cap": enrichment_cap,
        "generated_total": generated_total,
        "generated_after_generation_cap": generated_after_generation_cap,
        "kept_for_enrichment": kept_for_enrichment,
        "cap_reached_generation": cap_reached_generation,
        "cap_reached_enrichment": cap_reached_enrichment,
        "discarded_due_to_generation_cap": discarded_due_to_generation_cap,
        "discarded_due_to_enrichment_cap": discarded_due_to_enrichment_cap,
        "pre_score_min": round(min(scores), 6) if scores else None,
        "pre_score_max": round(max(scores), 6) if scores else None,
        "pre_score_median": (
            round(statistics.median(scores), 6) if scores else None
        ),
        "pre_score_cutoff": round(cutoff, 6) if cutoff is not None else None,
        "penny_count": penny_count,
        "missing_quote_count": missing_quote_count,
    }
