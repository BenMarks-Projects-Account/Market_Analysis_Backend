"""Breadth & Participation Scoring Engine.

Institutional-grade engine answering: "How broad, durable, and trustworthy
is the current market move?"

Architecture — 5 scored pillars:
  1. Participation Breadth   (25%)  — how many names are participating
  2. Trend Breadth            (25%)  — alignment with MA trend structure
  3. Volume Breadth           (20%)  — volume confirming participation
  4. Leadership Quality       (20%)  — breadth vs concentration / EW vs CW
  5. Participation Stability  (10%)  — persistence and follow-through

Composite formula:
  BreadthComposite = Σ(pillar_score × weight) / Σ(active_weights)

Label mapping (composite → regime):
  85-100  →  Strong Breadth
  70-84   →  Constructive
  55-69   →  Mixed but Positive
  45-54   →  Mixed / Fragile
  30-44   →  Weak Breadth
  0-29    →  Deteriorating

Confidence score (0-100) is independent of breadth score, derived from
data completeness, cross-pillar agreement, and universe coverage.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from typing import Any

from app.services.breadth_diagnostics import (
    compute_quality_scores,
    group_warnings_for_ui,
    is_scaffolded_metric,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION — weights, thresholds, scoring bands
# ═══════════════════════════════════════════════════════════════════════

# Pillar weights (must sum to 1.0)
PILLAR_WEIGHTS: dict[str, float] = {
    "participation_breadth": 0.25,
    "trend_breadth": 0.25,
    "volume_breadth": 0.20,
    "leadership_quality": 0.20,
    "participation_stability": 0.10,
}

# Trend Breadth sub-component weights (sum to 1.0 within pillar)
_TREND_SUB_WEIGHTS: dict[str, float] = {
    "short": 0.30,   # % above 20DMA, 20>50 cross
    "intermediate": 0.40,  # % above 50DMA
    "long": 0.30,    # % above 200DMA, 50>200 cross
}

# Label mapping ranges — composite score → label
_LABEL_BANDS: list[tuple[float, float, str, str]] = [
    # (min, max, full_label, short_label)
    (85, 100, "Strong Breadth", "Strong"),
    (70, 84.99, "Constructive", "Constructive"),
    (55, 69.99, "Mixed but Positive", "Mixed"),
    (45, 54.99, "Mixed / Fragile", "Fragile"),
    (30, 44.99, "Weak Breadth", "Weak"),
    (0, 29.99, "Deteriorating", "Deteriorating"),
]

# Confidence thresholds
_CONFIDENCE_HIGH = 80
_CONFIDENCE_MEDIUM = 60

# ═══════════════════════════════════════════════════════════════════════
# SCORING UTILITIES — clamp, interpolate, band-score
# ═══════════════════════════════════════════════════════════════════════


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Clamp value between lo and hi."""
    return max(lo, min(hi, value))


def _safe_float(value: Any, default: float | None = None) -> float | None:
    """Safely coerce value to float; return default if not possible."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _interpolate(value: float, in_lo: float, in_hi: float,
                 out_lo: float = 0.0, out_hi: float = 100.0) -> float:
    """Linearly interpolate value from [in_lo, in_hi] → [out_lo, out_hi].

    Clamps to output range.

    Formula: score = out_lo + (value - in_lo) / (in_hi - in_lo) * (out_hi - out_lo)
    """
    if in_hi == in_lo:
        return (out_lo + out_hi) / 2
    ratio = (value - in_lo) / (in_hi - in_lo)
    return _clamp(out_lo + ratio * (out_hi - out_lo), min(out_lo, out_hi), max(out_lo, out_hi))


def _pct_score(pct: float) -> float:
    """Convert a participation percentage (0.0-1.0) to a 0-100 score.

    Scoring bands (percentage → score):
      ≥ 0.80 → 90-100  (strong participation)
      0.60-0.80 → 70-90  (constructive)
      0.50-0.60 → 50-70  (moderate)
      0.40-0.50 → 30-50  (weak)
      < 0.40 → 0-30  (poor)

    Uses interpolation within each band.
    """
    if pct >= 0.80:
        return _interpolate(pct, 0.80, 1.0, 90, 100)
    if pct >= 0.60:
        return _interpolate(pct, 0.60, 0.80, 70, 90)
    if pct >= 0.50:
        return _interpolate(pct, 0.50, 0.60, 50, 70)
    if pct >= 0.40:
        return _interpolate(pct, 0.40, 0.50, 30, 50)
    return _interpolate(pct, 0.0, 0.40, 0, 30)


def _ratio_score(ratio: float) -> float:
    """Convert an A/D-style ratio to 0-100 score.

    Scoring bands:
      ≥ 3.0 → 95-100  (extreme breadth, rare)
      2.0-3.0 → 85-95  (very strong)
      1.5-2.0 → 70-85  (strong)
      1.0-1.5 → 50-70  (positive)
      0.7-1.0 → 30-50  (neutral-to-weak)
      0.5-0.7 → 15-30  (weak)
      < 0.5 → 0-15  (very weak)
    """
    if ratio >= 3.0:
        return _interpolate(ratio, 3.0, 5.0, 95, 100)
    if ratio >= 2.0:
        return _interpolate(ratio, 2.0, 3.0, 85, 95)
    if ratio >= 1.5:
        return _interpolate(ratio, 1.5, 2.0, 70, 85)
    if ratio >= 1.0:
        return _interpolate(ratio, 1.0, 1.5, 50, 70)
    if ratio >= 0.7:
        return _interpolate(ratio, 0.7, 1.0, 30, 50)
    if ratio >= 0.5:
        return _interpolate(ratio, 0.5, 0.7, 15, 30)
    return _interpolate(ratio, 0.0, 0.5, 0, 15)


def _build_submetric(
    name: str,
    raw_value: float | None,
    score: float | None,
    *,
    observations: int = 0,
    missing_count: int = 0,
    fallback_used: bool = False,
    warnings: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a standardized submetric result object.

    Schema:
      name: str — metric identifier
      raw_value: float | None — actual computed value
      score: float | None — normalized 0-100 score
      status: "valid" | "degraded" | "unavailable"
      observations: int — count of data points used
      missing_count: int — count of missing data points
      fallback_used: bool — whether a fallback was used
      warnings: list[str]
      details: dict — additional context
    """
    if raw_value is None or score is None:
        status = "unavailable"
    elif missing_count > 0 or fallback_used:
        status = "degraded"
    else:
        status = "valid"
    return {
        "name": name,
        "raw_value": round(raw_value, 6) if raw_value is not None else None,
        "score": round(score, 2) if score is not None else None,
        "status": status,
        "observations": observations,
        "missing_count": missing_count,
        "fallback_used": fallback_used,
        "warnings": warnings or [],
        "details": details or {},
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 1 — PARTICIPATION BREADTH (25%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_participation_breadth(data: dict[str, Any]) -> dict[str, Any]:
    """Measure how many names are actually participating in the move.

    Submetrics:
      advance_decline_ratio = advancing / max(declining, 1)
      net_advances_pct = (advancing - declining) / total_valid
      percent_up = advancing / total_valid
      new_high_new_low_balance = new_highs / max(new_highs + new_lows, 1)
      sector_participation_pct = positive_sectors / total_sectors
      equal_weight_confirmation = relative EW vs CW behavior (1 = confirming)

    Weights within pillar:
      advance_decline_ratio     20%
      net_advances_pct          15%
      percent_up                15%
      new_high_new_low_balance  20%
      sector_participation_pct  15%
      equal_weight_confirmation 15%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    advancing = data.get("advancing")
    declining = data.get("declining")
    total_valid = data.get("total_valid")
    new_highs = data.get("new_highs")
    new_lows = data.get("new_lows")
    sector_up = data.get("sectors_positive")
    sector_total = data.get("sectors_total")
    ew_return = _safe_float(data.get("ew_return"))
    cw_return = _safe_float(data.get("cw_return"))

    raw_inputs = {
        "advancing": advancing, "declining": declining,
        "total_valid": total_valid, "new_highs": new_highs,
        "new_lows": new_lows, "sectors_positive": sector_up,
        "sectors_total": sector_total,
        "ew_return": ew_return, "cw_return": cw_return,
    }

    obs = _safe_float(total_valid, 0)

    # ── advance_decline_ratio ────────────────────────────────────
    # Formula: advancing_names / max(declining_names, 1)
    if advancing is not None and declining is not None:
        ad_ratio = advancing / max(declining, 1)
        ad_score = _ratio_score(ad_ratio)
        submetrics.append(_build_submetric(
            "advance_decline_ratio", ad_ratio, ad_score,
            observations=int(obs or 0),
        ))
    else:
        total_missing += 1
        warnings.append("advance_decline_ratio: missing advancing/declining counts")
        submetrics.append(_build_submetric(
            "advance_decline_ratio", None, None,
            warnings=["Missing advancing/declining input data"],
        ))

    # ── net_advances_pct ─────────────────────────────────────────
    # Formula: (advancing - declining) / total_valid
    if advancing is not None and declining is not None and total_valid and total_valid > 0:
        net_adv_pct = (advancing - declining) / total_valid
        # Range: -1.0 to +1.0 → score 0-100
        net_adv_score = _interpolate(net_adv_pct, -0.5, 0.5, 0, 100)
        submetrics.append(_build_submetric(
            "net_advances_pct", net_adv_pct, net_adv_score,
            observations=int(total_valid),
        ))
    else:
        total_missing += 1
        warnings.append("net_advances_pct: missing input data")
        submetrics.append(_build_submetric("net_advances_pct", None, None))

    # ── percent_up ───────────────────────────────────────────────
    # Formula: advancing / total_valid
    if advancing is not None and total_valid and total_valid > 0:
        pct_up = advancing / total_valid
        pct_up_score = _pct_score(pct_up)
        submetrics.append(_build_submetric(
            "percent_up", pct_up, pct_up_score,
            observations=int(total_valid),
        ))
    else:
        total_missing += 1
        warnings.append("percent_up: missing input data")
        submetrics.append(_build_submetric("percent_up", None, None))

    # ── new_high_new_low_balance ─────────────────────────────────
    # Formula: new_highs / max(new_highs + new_lows, 1)
    if new_highs is not None and new_lows is not None:
        nh_nl_total = new_highs + new_lows
        nh_nl_balance = new_highs / max(nh_nl_total, 1)
        # 0-1 → 0-100. 0.5 = neutral, 1.0 = all highs
        nh_nl_score = _pct_score(nh_nl_balance)
        submetrics.append(_build_submetric(
            "new_high_new_low_balance", nh_nl_balance, nh_nl_score,
            observations=nh_nl_total,
            details={"new_highs": new_highs, "new_lows": new_lows},
        ))
    else:
        total_missing += 1
        warnings.append("new_high_new_low_balance: missing new highs/lows data")
        submetrics.append(_build_submetric("new_high_new_low_balance", None, None))

    # ── sector_participation_pct ─────────────────────────────────
    # Formula: positive_sectors / total_sectors
    if sector_up is not None and sector_total and sector_total > 0:
        sect_pct = sector_up / sector_total
        sect_score = _pct_score(sect_pct)
        submetrics.append(_build_submetric(
            "sector_participation_pct", sect_pct, sect_score,
            observations=sector_total,
        ))
    else:
        total_missing += 1
        warnings.append("sector_participation_pct: missing sector data")
        submetrics.append(_build_submetric("sector_participation_pct", None, None))

    # ── equal_weight_confirmation ────────────────────────────────
    # Concept: if EW return ~ CW return, broad confirmation.
    #   if EW >> CW lag, narrow leadership.
    # Score: interpolate relative gap to 0-100.
    # Formula: 1.0 - clamp(abs(ew_return - cw_return) / max(abs(cw_return), 0.001), 0, 2) / 2
    if ew_return is not None and cw_return is not None:
        gap = ew_return - cw_return  # positive = EW outperforming (broad)
        abs_cw = max(abs(cw_return), 0.001)
        # Relative gap: > 0 means EW outperforming (good), < 0 means lagging (bad)
        relative_gap = gap / abs_cw
        # Confirmation score: strong confirmation at +1, penalty at -1
        # Range roughly -2 to +2, map to 0-100
        ew_score = _interpolate(relative_gap, -1.0, 1.0, 20, 90)
        submetrics.append(_build_submetric(
            "equal_weight_confirmation", relative_gap, ew_score,
            details={"ew_return": ew_return, "cw_return": cw_return, "gap": gap},
        ))
    else:
        total_missing += 1
        warnings.append("equal_weight_confirmation: missing EW/CW return data")
        submetrics.append(_build_submetric(
            "equal_weight_confirmation", None, None,
            warnings=["Equal-weight benchmark data unavailable"],
        ))

    # ── Aggregate pillar score ───────────────────────────────────
    # Weights for submetrics within pillar
    sub_weights = {
        "advance_decline_ratio": 0.20,
        "net_advances_pct": 0.15,
        "percent_up": 0.15,
        "new_high_new_low_balance": 0.20,
        "sector_participation_pct": 0.15,
        "equal_weight_confirmation": 0.15,
    }

    pillar_score, explanation = _aggregate_submetrics(submetrics, sub_weights)

    return {
        "score": pillar_score,
        "submetrics": submetrics,
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": total_missing,
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 2 — TREND BREADTH (25%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_trend_breadth(data: dict[str, Any]) -> dict[str, Any]:
    """Measure how well the universe is aligned with short/intermediate/long trend.

    Submetrics:
      pct_above_20dma — short trend (weight: 30% of short sub)
      pct_above_50dma — intermediate trend
      pct_above_200dma — long trend
      pct_20_over_50 — short/intermediate alignment
      pct_50_over_200 — golden/death cross breadth
      trend_momentum_short — 5D change in pct_above_20dma
      trend_momentum_intermediate — 10D change in pct_above_50dma
      trend_momentum_long — 20D change in pct_above_200dma

    Internal aggregation:
      short_trend = 0.30  (pct_above_20dma, pct_20_over_50, momentum_short)
      intermediate_trend = 0.40  (pct_above_50dma, momentum_intermediate)
      long_trend = 0.30  (pct_above_200dma, pct_50_over_200, momentum_long)
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    pct_20 = _safe_float(data.get("pct_above_20dma"))
    pct_50 = _safe_float(data.get("pct_above_50dma"))
    pct_200 = _safe_float(data.get("pct_above_200dma"))
    pct_20_50 = _safe_float(data.get("pct_20_over_50"))
    pct_50_200 = _safe_float(data.get("pct_50_over_200"))
    mom_short = _safe_float(data.get("trend_momentum_short"))
    mom_int = _safe_float(data.get("trend_momentum_intermediate"))
    mom_long = _safe_float(data.get("trend_momentum_long"))

    raw_inputs = {
        "pct_above_20dma": pct_20, "pct_above_50dma": pct_50,
        "pct_above_200dma": pct_200, "pct_20_over_50": pct_20_50,
        "pct_50_over_200": pct_50_200,
        "trend_momentum_short": mom_short,
        "trend_momentum_intermediate": mom_int,
        "trend_momentum_long": mom_long,
    }

    obs = int(_safe_float(data.get("total_valid"), 0))

    # ── pct_above_20dma ──────────────────────────────────────────
    # Input: fraction (0.0-1.0) of constituents above 20-day SMA
    if pct_20 is not None:
        submetrics.append(_build_submetric(
            "pct_above_20dma", pct_20, _pct_score(pct_20), observations=obs))
    else:
        total_missing += 1
        warnings.append("pct_above_20dma: unavailable")
        submetrics.append(_build_submetric("pct_above_20dma", None, None))

    # ── pct_above_50dma ──────────────────────────────────────────
    if pct_50 is not None:
        submetrics.append(_build_submetric(
            "pct_above_50dma", pct_50, _pct_score(pct_50), observations=obs))
    else:
        total_missing += 1
        warnings.append("pct_above_50dma: unavailable")
        submetrics.append(_build_submetric("pct_above_50dma", None, None))

    # ── pct_above_200dma ─────────────────────────────────────────
    if pct_200 is not None:
        submetrics.append(_build_submetric(
            "pct_above_200dma", pct_200, _pct_score(pct_200), observations=obs))
    else:
        total_missing += 1
        warnings.append("pct_above_200dma: unavailable")
        submetrics.append(_build_submetric("pct_above_200dma", None, None))

    # ── pct_20_over_50 ───────────────────────────────────────────
    # Fraction of names where 20DMA > 50DMA (short-term trend alignment)
    if pct_20_50 is not None:
        submetrics.append(_build_submetric(
            "pct_20_over_50", pct_20_50, _pct_score(pct_20_50), observations=obs))
    else:
        total_missing += 1
        warnings.append("pct_20_over_50: unavailable")
        submetrics.append(_build_submetric("pct_20_over_50", None, None))

    # ── pct_50_over_200 ──────────────────────────────────────────
    # Fraction of names where 50DMA > 200DMA (golden cross breadth)
    if pct_50_200 is not None:
        submetrics.append(_build_submetric(
            "pct_50_over_200", pct_50_200, _pct_score(pct_50_200), observations=obs))
    else:
        total_missing += 1
        warnings.append("pct_50_over_200: unavailable")
        submetrics.append(_build_submetric("pct_50_over_200", None, None))

    # ── Trend momentum (5D/10D/20D change in breadth percentages) ─
    # Positive change = improving breadth → bonus score
    # Negative change = decaying breadth → penalty
    # Input: change in pct (e.g., +0.05 means 5% more names above MA)
    _momentum_metrics = [
        ("trend_momentum_short", mom_short, "5D change pct_above_20dma"),
        ("trend_momentum_intermediate", mom_int, "10D change pct_above_50dma"),
        ("trend_momentum_long", mom_long, "20D change pct_above_200dma"),
    ]
    for name, val, desc in _momentum_metrics:
        if val is not None:
            # Range: -0.30 to +0.30 → score 0-100, 0.0 = 50
            mom_score = _interpolate(val, -0.20, 0.20, 10, 90)
            submetrics.append(_build_submetric(
                name, val, mom_score, details={"description": desc}))
        else:
            total_missing += 1
            warnings.append(f"{name}: unavailable")
            submetrics.append(_build_submetric(name, None, None))

    # ── Aggregate using tiered sub-weights ───────────────────────
    # Short tier: pct_above_20dma (50%), pct_20_over_50 (30%), momentum_short (20%)
    # Intermediate tier: pct_above_50dma (70%), momentum_intermediate (30%)
    # Long tier: pct_above_200dma (40%), pct_50_over_200 (40%), momentum_long (20%)
    sub_scores_by_name = {s["name"]: s["score"] for s in submetrics}

    tier_scores: dict[str, float | None] = {}

    # Short tier
    short_parts = [
        (sub_scores_by_name.get("pct_above_20dma"), 0.50),
        (sub_scores_by_name.get("pct_20_over_50"), 0.30),
        (sub_scores_by_name.get("trend_momentum_short"), 0.20),
    ]
    tier_scores["short"] = _weighted_avg(short_parts)

    # Intermediate tier
    int_parts = [
        (sub_scores_by_name.get("pct_above_50dma"), 0.70),
        (sub_scores_by_name.get("trend_momentum_intermediate"), 0.30),
    ]
    tier_scores["intermediate"] = _weighted_avg(int_parts)

    # Long tier
    long_parts = [
        (sub_scores_by_name.get("pct_above_200dma"), 0.40),
        (sub_scores_by_name.get("pct_50_over_200"), 0.40),
        (sub_scores_by_name.get("trend_momentum_long"), 0.20),
    ]
    tier_scores["long"] = _weighted_avg(long_parts)

    # Final pillar: weighted average of tiers
    tier_weight_parts = [
        (tier_scores.get("short"), _TREND_SUB_WEIGHTS["short"]),
        (tier_scores.get("intermediate"), _TREND_SUB_WEIGHTS["intermediate"]),
        (tier_scores.get("long"), _TREND_SUB_WEIGHTS["long"]),
    ]
    pillar_score = _weighted_avg(tier_weight_parts)

    explanation = _trend_explanation(pillar_score, tier_scores, sub_scores_by_name)

    return {
        "score": pillar_score,
        "submetrics": submetrics,
        "tier_scores": {k: round(v, 2) if v is not None else None for k, v in tier_scores.items()},
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": total_missing,
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 3 — VOLUME BREADTH (20%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_volume_breadth(data: dict[str, Any]) -> dict[str, Any]:
    """Measure whether participation is backed by credible volume.

    Submetrics:
      up_down_volume_ratio = total_up_volume / max(total_down_volume, 1)
      pct_volume_in_advancers = advancer_volume / total_volume
      volume_weighted_ad_ratio = (up_vol × advancing) / max(down_vol × declining, 1)
      accumulation_distribution_bias = scaffolded, logged if unavailable
      volume_thrust_signal = scaffolded for future

    Weights within pillar:
      up_down_volume_ratio      35%
      pct_volume_in_advancers   30%
      volume_weighted_ad_ratio  35%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    up_vol = _safe_float(data.get("up_volume"))
    down_vol = _safe_float(data.get("down_volume"))
    total_vol = _safe_float(data.get("total_volume"))
    advancing = _safe_float(data.get("advancing"))
    declining = _safe_float(data.get("declining"))

    raw_inputs = {
        "up_volume": up_vol, "down_volume": down_vol,
        "total_volume": total_vol,
        "advancing": advancing, "declining": declining,
    }

    # ── up_down_volume_ratio ─────────────────────────────────────
    # Formula: up_volume / max(down_volume, 1)
    if up_vol is not None and down_vol is not None:
        ud_ratio = up_vol / max(down_vol, 1)
        ud_score = _ratio_score(ud_ratio)
        submetrics.append(_build_submetric(
            "up_down_volume_ratio", ud_ratio, ud_score,
            details={"up_volume": up_vol, "down_volume": down_vol},
        ))
    else:
        total_missing += 1
        warnings.append("up_down_volume_ratio: missing volume data")
        submetrics.append(_build_submetric("up_down_volume_ratio", None, None))

    # ── pct_volume_in_advancers ──────────────────────────────────
    # Formula: advancer_volume / total_volume
    if up_vol is not None and total_vol and total_vol > 0:
        pct_vol_adv = up_vol / total_vol
        pct_vol_score = _pct_score(pct_vol_adv)
        submetrics.append(_build_submetric(
            "pct_volume_in_advancers", pct_vol_adv, pct_vol_score))
    else:
        total_missing += 1
        warnings.append("pct_volume_in_advancers: missing volume data")
        submetrics.append(_build_submetric("pct_volume_in_advancers", None, None))

    # ── volume_weighted_ad_ratio ─────────────────────────────────
    # Formula: (up_vol × advancing) / max(down_vol × declining, 1)
    if all(v is not None for v in [up_vol, down_vol, advancing, declining]):
        numerator = up_vol * advancing
        denominator = max(down_vol * declining, 1)
        vw_ad = numerator / denominator
        # This ratio can be very large; normalize differently
        # Use log-like compression: score = interpolate(min(vw_ad, 10), 0, 5, 0, 100)
        vw_score = _interpolate(min(vw_ad, 10), 0, 5, 0, 100)
        submetrics.append(_build_submetric(
            "volume_weighted_ad_ratio", vw_ad, vw_score,
            details={"numerator": numerator, "denominator": denominator},
        ))
    else:
        total_missing += 1
        warnings.append("volume_weighted_ad_ratio: missing input data")
        submetrics.append(_build_submetric("volume_weighted_ad_ratio", None, None))

    # ── accumulation_distribution_bias (scaffolded) ──────────────
    acc_dist = _safe_float(data.get("accumulation_distribution_bias"))
    if acc_dist is not None:
        # Range: -1.0 (distribution) to +1.0 (accumulation) → 0-100
        ad_score = _interpolate(acc_dist, -1.0, 1.0, 0, 100)
        submetrics.append(_build_submetric(
            "accumulation_distribution_bias", acc_dist, ad_score))
    else:
        total_missing += 1
        warnings.append("accumulation_distribution_bias: not yet implemented — scaffolded")
        submetrics.append(_build_submetric(
            "accumulation_distribution_bias", None, None,
            warnings=["Accumulation/distribution bias not yet available"],
        ))

    # ── volume_thrust_signal (scaffolded) ────────────────────────
    vol_thrust = _safe_float(data.get("volume_thrust_signal"))
    if vol_thrust is not None:
        vt_score = _interpolate(vol_thrust, -1.0, 1.0, 0, 100)
        submetrics.append(_build_submetric(
            "volume_thrust_signal", vol_thrust, vt_score))
    else:
        total_missing += 1
        warnings.append("volume_thrust_signal: not yet implemented — scaffolded")
        submetrics.append(_build_submetric(
            "volume_thrust_signal", None, None,
            warnings=["Volume thrust signal not yet available"],
        ))

    # ── Aggregate pillar score ───────────────────────────────────
    sub_weights = {
        "up_down_volume_ratio": 0.35,
        "pct_volume_in_advancers": 0.30,
        "volume_weighted_ad_ratio": 0.35,
        # scaffolded metrics excluded from weighting until available
    }
    pillar_score, explanation = _aggregate_submetrics(submetrics, sub_weights)

    return {
        "score": pillar_score,
        "submetrics": submetrics,
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": total_missing,
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 4 — LEADERSHIP QUALITY (20%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_leadership_quality(data: dict[str, Any]) -> dict[str, Any]:
    """Measure whether leadership is healthy and broad or narrow and distorted.

    Submetrics:
      ew_vs_cw_relative — EW return minus CW return, normalized
      sector_concentration_penalty — penalty for narrow sector leadership
      pct_outperforming_index — fraction of names beating the index
      median_return_vs_index — median stock return minus index return

    Weights within pillar:
      ew_vs_cw_relative          30%
      sector_concentration_penalty 25%
      pct_outperforming_index    25%
      median_return_vs_index     20%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    ew_return = _safe_float(data.get("ew_return"))
    cw_return = _safe_float(data.get("cw_return"))
    sector_returns = data.get("sector_returns")  # dict: sector → return
    pct_outperf = _safe_float(data.get("pct_outperforming_index"))
    median_return = _safe_float(data.get("median_return"))
    index_return = _safe_float(data.get("index_return"))

    raw_inputs = {
        "ew_return": ew_return, "cw_return": cw_return,
        "sector_returns": sector_returns,
        "pct_outperforming_index": pct_outperf,
        "median_return": median_return, "index_return": index_return,
    }

    # ── ew_vs_cw_relative ────────────────────────────────────────
    # Positive = EW outperforming CW = broad leadership = good
    # Negative = CW outperforming EW = narrow/concentrated = bad
    # Formula: ew_return - cw_return, mapped via interpolation
    if ew_return is not None and cw_return is not None:
        ew_cw_diff = ew_return - cw_return
        # Typical daily range: -2% to +2%, map to 0-100
        ew_score = _interpolate(ew_cw_diff, -0.02, 0.02, 15, 90)
        submetrics.append(_build_submetric(
            "ew_vs_cw_relative", ew_cw_diff, ew_score,
            details={"ew_return": ew_return, "cw_return": cw_return},
        ))
    else:
        total_missing += 1
        warnings.append("ew_vs_cw_relative: missing EW/CW data")
        submetrics.append(_build_submetric(
            "ew_vs_cw_relative", None, None,
            warnings=["Equal-weight vs cap-weight data unavailable"],
        ))

    # ── sector_concentration_penalty ─────────────────────────────
    # If only 1-2 sectors driving gains → penalty (lower score)
    # If most sectors contributing → no penalty (higher score)
    # Input: sector_returns dict
    if sector_returns and isinstance(sector_returns, dict) and len(sector_returns) >= 2:
        returns = [v for v in sector_returns.values() if v is not None]
        if returns:
            positive_sectors = sum(1 for r in returns if r > 0)
            total_sectors = len(returns)
            sector_breadth = positive_sectors / total_sectors

            # Also check variance — high variance = concentrated
            if len(returns) >= 2:
                ret_std = statistics.stdev(returns)
                # Lower std = more uniform = better breadth
                # Typical daily sector return std: 0.005-0.03
                concentration_penalty = _interpolate(ret_std, 0.005, 0.03, 0, 40)
            else:
                concentration_penalty = 0

            raw_concentration = sector_breadth
            # base 0-100 from sector breadth, then subtract concentration penalty
            conc_score = _clamp(_pct_score(sector_breadth) - concentration_penalty)
            submetrics.append(_build_submetric(
                "sector_concentration_penalty", raw_concentration, conc_score,
                observations=total_sectors,
                details={
                    "positive_sectors": positive_sectors,
                    "total_sectors": total_sectors,
                    "concentration_penalty": round(concentration_penalty, 2),
                },
            ))
        else:
            total_missing += 1
            warnings.append("sector_concentration_penalty: all sector returns are None")
            submetrics.append(_build_submetric("sector_concentration_penalty", None, None))
    else:
        total_missing += 1
        warnings.append("sector_concentration_penalty: missing sector return data")
        submetrics.append(_build_submetric("sector_concentration_penalty", None, None))

    # ── pct_outperforming_index ──────────────────────────────────
    # Fraction of constituents beating the index return
    # Higher = broader leadership
    if pct_outperf is not None:
        outperf_score = _pct_score(pct_outperf)
        submetrics.append(_build_submetric(
            "pct_outperforming_index", pct_outperf, outperf_score))
    else:
        total_missing += 1
        warnings.append("pct_outperforming_index: unavailable")
        submetrics.append(_build_submetric("pct_outperforming_index", None, None))

    # ── median_return_vs_index ───────────────────────────────────
    # Median constituent return minus index return
    # Positive = typical stock doing better than index = broad
    # Negative = typical stock lagging index = narrow
    if median_return is not None and index_return is not None:
        med_vs_idx = median_return - index_return
        # Typical range: -0.02 to +0.02
        med_score = _interpolate(med_vs_idx, -0.015, 0.015, 10, 90)
        submetrics.append(_build_submetric(
            "median_return_vs_index", med_vs_idx, med_score,
            details={"median_return": median_return, "index_return": index_return},
        ))
    else:
        total_missing += 1
        warnings.append("median_return_vs_index: missing data")
        submetrics.append(_build_submetric("median_return_vs_index", None, None))

    # ── Aggregate ────────────────────────────────────────────────
    sub_weights = {
        "ew_vs_cw_relative": 0.30,
        "sector_concentration_penalty": 0.25,
        "pct_outperforming_index": 0.25,
        "median_return_vs_index": 0.20,
    }
    pillar_score, explanation = _aggregate_submetrics(submetrics, sub_weights)

    return {
        "score": pillar_score,
        "submetrics": submetrics,
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": total_missing,
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 5 — PARTICIPATION STABILITY (10%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_participation_stability(data: dict[str, Any]) -> dict[str, Any]:
    """Measure whether breadth is persistent and stable versus erratic.

    Submetrics:
      breadth_persistence_10d — fraction of last 10 days with net positive A/D
      ad_ratio_volatility_5d — rolling 5-day std of A/D ratio (lower = better)
      pct_above_20dma_vol_5d — rolling 5-day std of pct_above_20dma (lower = better)
      thrust_followthrough — scaffolded for future
      breadth_reversal_frequency — scaffolded for future

    Weights within pillar:
      breadth_persistence_10d   40%
      ad_ratio_volatility_5d    30%
      pct_above_20dma_vol_5d    30%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    persistence = _safe_float(data.get("breadth_persistence_10d"))
    ad_vol = _safe_float(data.get("ad_ratio_volatility_5d"))
    pct20_vol = _safe_float(data.get("pct_above_20dma_volatility_5d"))

    raw_inputs = {
        "breadth_persistence_10d": persistence,
        "ad_ratio_volatility_5d": ad_vol,
        "pct_above_20dma_volatility_5d": pct20_vol,
    }

    # ── breadth_persistence_10d ──────────────────────────────────
    # Fraction of last 10 sessions with net advances > 0
    # Range: 0-1. Higher = more persistent = better.
    if persistence is not None:
        persist_score = _pct_score(persistence)
        submetrics.append(_build_submetric(
            "breadth_persistence_10d", persistence, persist_score,
            observations=10,
        ))
    else:
        total_missing += 1
        warnings.append("breadth_persistence_10d: unavailable")
        submetrics.append(_build_submetric("breadth_persistence_10d", None, None))

    # ── ad_ratio_volatility_5d ───────────────────────────────────
    # Rolling 5-day std of A/D ratio. Lower = more stable = better.
    # Typical range: 0.1-1.5
    # Formula (inverse): score = 100 - interpolate(vol, 0.1, 1.0, 0, 80)
    if ad_vol is not None:
        # Invert: low vol → high score
        stab_score = _clamp(100 - _interpolate(ad_vol, 0.1, 1.0, 0, 80))
        submetrics.append(_build_submetric(
            "ad_ratio_volatility_5d", ad_vol, stab_score,
            observations=5,
        ))
    else:
        total_missing += 1
        warnings.append("ad_ratio_volatility_5d: unavailable")
        submetrics.append(_build_submetric("ad_ratio_volatility_5d", None, None))

    # ── pct_above_20dma_volatility_5d ────────────────────────────
    # Rolling 5-day std of pct_above_20dma. Lower = more stable.
    # Typical range: 0.02-0.15
    if pct20_vol is not None:
        # Invert: low vol → high score
        pct_stab_score = _clamp(100 - _interpolate(pct20_vol, 0.02, 0.12, 0, 80))
        submetrics.append(_build_submetric(
            "pct_above_20dma_volatility_5d", pct20_vol, pct_stab_score,
            observations=5,
        ))
    else:
        total_missing += 1
        warnings.append("pct_above_20dma_volatility_5d: unavailable")
        submetrics.append(_build_submetric("pct_above_20dma_volatility_5d", None, None))

    # ── Scaffolded future metrics ────────────────────────────────
    for future_name in ["thrust_followthrough", "breadth_reversal_frequency"]:
        val = _safe_float(data.get(future_name))
        if val is not None:
            submetrics.append(_build_submetric(
                future_name, val, _interpolate(val, 0, 1, 0, 100)))
        else:
            warnings.append(f"{future_name}: not yet implemented — scaffolded")
            submetrics.append(_build_submetric(
                future_name, None, None,
                warnings=[f"{future_name} not yet available"],
            ))

    # ── Aggregate ────────────────────────────────────────────────
    sub_weights = {
        "breadth_persistence_10d": 0.40,
        "ad_ratio_volatility_5d": 0.30,
        "pct_above_20dma_volatility_5d": 0.30,
    }
    pillar_score, explanation = _aggregate_submetrics(submetrics, sub_weights)

    return {
        "score": pillar_score,
        "submetrics": submetrics,
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": total_missing,
    }


# ═══════════════════════════════════════════════════════════════════════
# AGGREGATION HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _weighted_avg(parts: list[tuple[float | None, float]]) -> float | None:
    """Compute weighted average, ignoring None values.

    Returns None if no valid parts.

    Formula: sum(value × weight) / sum(weight) for non-None values.
    """
    total_w = 0.0
    weighted_s = 0.0
    for val, weight in parts:
        if val is not None:
            weighted_s += val * weight
            total_w += weight
    if total_w == 0:
        return None
    return round(weighted_s / total_w, 2)


def _aggregate_submetrics(
    submetrics: list[dict[str, Any]],
    weights: dict[str, float],
) -> tuple[float | None, str]:
    """Score a pillar from its submetrics using specified weights.

    Only includes submetrics present in weights dict (excludes scaffolded).
    Returns (pillar_score, explanation_string).
    """
    parts: list[tuple[float | None, float]] = []
    valid_names: list[str] = []
    missing_names: list[str] = []

    for sm in submetrics:
        name = sm["name"]
        if name not in weights:
            continue
        score = sm.get("score")
        if score is not None:
            parts.append((score, weights[name]))
            valid_names.append(name)
        else:
            missing_names.append(name)

    pillar_score = _weighted_avg(parts)

    if pillar_score is not None:
        explanation = f"Score {pillar_score:.0f}/100 based on {len(valid_names)} of {len(weights)} submetrics."
        if missing_names:
            explanation += f" Missing: {', '.join(missing_names)}."
    else:
        explanation = "Pillar unavailable — all submetrics are missing."

    return pillar_score, explanation


# ═══════════════════════════════════════════════════════════════════════
# EXPLANATION BUILDERS
# ═══════════════════════════════════════════════════════════════════════

def _trend_explanation(
    pillar_score: float | None,
    tier_scores: dict[str, float | None],
    sub_scores: dict[str, float | None],
) -> str:
    """Build human-readable explanation for trend breadth pillar."""
    if pillar_score is None:
        return "Trend breadth unavailable — insufficient data."

    parts: list[str] = [f"Trend breadth score {pillar_score:.0f}/100."]

    short = tier_scores.get("short")
    intermediate = tier_scores.get("intermediate")
    long_ = tier_scores.get("long")

    if short is not None:
        parts.append(f"Short-term trend: {short:.0f}.")
    if intermediate is not None:
        parts.append(f"Intermediate trend: {intermediate:.0f}.")
    if long_ is not None:
        parts.append(f"Long-term trend: {long_:.0f}.")

    pct50 = sub_scores.get("pct_above_50dma")
    if pct50 is not None:
        if pct50 >= 70:
            parts.append("Strong intermediate trend alignment.")
        elif pct50 < 40:
            parts.append("Weak intermediate trend alignment — many names below 50DMA.")

    return " ".join(parts)


def _label_from_score(score: float) -> tuple[str, str]:
    """Map composite score to (full_label, short_label).

    Bands:
      85-100  → Strong Breadth / Strong
      70-84   → Constructive / Constructive
      55-69   → Mixed but Positive / Mixed
      45-54   → Mixed / Fragile / Fragile
      30-44   → Weak Breadth / Weak
      0-29    → Deteriorating / Deteriorating
    """
    for lo, hi, full, short in _LABEL_BANDS:
        if lo <= score <= hi:
            return full, short
    return "Unknown", "Unknown"


def _signal_quality(confidence: float) -> str:
    """Map confidence score to signal quality label.

    80+  → high
    60-79 → medium
    <60  → low
    """
    if confidence >= _CONFIDENCE_HIGH:
        return "high"
    if confidence >= _CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


# ═══════════════════════════════════════════════════════════════════════
# CONFIDENCE SCORE (delegates to breadth_diagnostics)
# ═══════════════════════════════════════════════════════════════════════

def _compute_confidence(
    pillars: dict[str, dict[str, Any]],
    universe_meta: dict[str, Any],
) -> tuple[float, list[str]]:
    """Compute confidence score (0-100) independent of breadth score.

    Legacy API preserved for backward compatibility.
    Delegates to breadth_diagnostics.compute_quality_scores()
    and returns (confidence_score, penalty_summary_strings).
    """
    quality = compute_quality_scores(pillars, universe_meta)
    # Convert structured penalties to legacy string format
    penalty_strings = [
        f"{p['factor']}: {p['detail']} (-{p.get('confidence_penalty', 0):.1f})"
        for p in quality["penalties"]
        if p.get("confidence_penalty", 0) > 0
    ]
    # Preserve legacy EW-specific penalty strings for backward compatibility
    for w in quality.get("structured_warnings", []):
        if w.get("code") == "MISSING_EW_BENCHMARK":
            penalty_strings.append(
                "equal-weight benchmark: unavailable — cannot confirm breadth via EW/CW (-5.0)"
            )
            break
    return quality["confidence_score"], penalty_strings


# ═══════════════════════════════════════════════════════════════════════
# COMPOSITE EXPLANATION BUILDER
# ═══════════════════════════════════════════════════════════════════════

def _build_composite_explanation(
    composite: float,
    label: str,
    pillars: dict[str, dict[str, Any]],
    confidence: float,
) -> dict[str, Any]:
    """Build structured explanation output for UI rendering.

    Returns dict with:
      summary, positive_contributors, negative_contributors,
      conflicting_signals, trader_takeaway
    """
    positive: list[str] = []
    negative: list[str] = []
    conflicting: list[str] = []

    # Analyze each pillar for contributors
    for pname, pdata in pillars.items():
        score = pdata.get("score")
        if score is None:
            continue

        readable = pname.replace("_", " ").title()

        if score >= 70:
            positive.append(f"{readable} is strong ({score:.0f}/100)")
        elif score >= 55:
            positive.append(f"{readable} is constructive ({score:.0f}/100)")
        elif score < 40:
            negative.append(f"{readable} is weak ({score:.0f}/100)")
        elif score < 55:
            negative.append(f"{readable} is fragile ({score:.0f}/100)")

    # Detect cross-pillar conflicts
    valid_pillars = {
        k: v["score"] for k, v in pillars.items() if v.get("score") is not None
    }
    if valid_pillars:
        max_p = max(valid_pillars.values())
        min_p = min(valid_pillars.values())
        if max_p - min_p > 30:
            high_name = [k for k, v in valid_pillars.items() if v == max_p][0]
            low_name = [k for k, v in valid_pillars.items() if v == min_p][0]
            conflicting.append(
                f"Large gap between {high_name.replace('_', ' ')} "
                f"({max_p:.0f}) and {low_name.replace('_', ' ')} ({min_p:.0f})"
            )

    # Build summary sentence
    summary_parts: list[str] = [f"Breadth is {label.lower()} (composite {composite:.0f}/100)."]
    if positive:
        summary_parts.append(f"Strengths: {positive[0].split(' is ')[0].lower()} breadth.")
    if negative:
        summary_parts.append(f"Weaknesses: {negative[0].split(' is ')[0].lower()}.")

    # Trader takeaway
    if composite >= 70:
        takeaway = (
            "Broad market participation supports confident positioning. "
            "Breadth is healthy — favor full-size positions and premium selling."
        )
    elif composite >= 55:
        takeaway = (
            "Breadth is constructive but not dominant. "
            "Positions are supported but watch for deterioration in any weak pillar."
        )
    elif composite >= 45:
        takeaway = (
            "Breadth is fragile. Participation is mixed and follow-through uncertain. "
            "Favor smaller positions, tighter stops, and shorter duration."
        )
    elif composite >= 30:
        takeaway = (
            "Breadth is weak — the rally (if any) is narrow and vulnerable. "
            "Reduce directional exposure, favor hedged or income strategies."
        )
    else:
        takeaway = (
            "Internal structure is deteriorating. Broad participation is absent. "
            "Defensive posture recommended — consider hedging, raising cash, or "
            "avoiding new directional exposure."
        )

    if confidence < 60:
        takeaway += " (Note: confidence is low due to incomplete data — interpret cautiously.)"

    return {
        "summary": " ".join(summary_parts),
        "positive_contributors": positive,
        "negative_contributors": negative,
        "conflicting_signals": conflicting,
        "trader_takeaway": takeaway,
    }


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENGINE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def compute_breadth_scores(
    participation_data: dict[str, Any],
    trend_data: dict[str, Any],
    volume_data: dict[str, Any],
    leadership_data: dict[str, Any],
    stability_data: dict[str, Any],
    universe_meta: dict[str, Any],
) -> dict[str, Any]:
    """Compute the Breadth & Participation engine result.

    Parameters
    ----------
    participation_data : dict
        Raw inputs for Pillar 1 (advancing, declining, sector counts, etc.)
    trend_data : dict
        Raw inputs for Pillar 2 (pct above MAs, cross data, momentum)
    volume_data : dict
        Raw inputs for Pillar 3 (up/down volume, etc.)
    leadership_data : dict
        Raw inputs for Pillar 4 (EW vs CW, sector returns, etc.)
    stability_data : dict
        Raw inputs for Pillar 5 (persistence, A/D volatility, etc.)
    universe_meta : dict
        Universe metadata (name, expected/actual counts, coverage, etc.)

    Returns
    -------
    dict with:
      engine, as_of, universe, score, label, short_label,
      confidence_score, signal_quality, summary, pillar_scores,
      pillar_explanations, pillar_details, positive_contributors,
      negative_contributors, conflicting_signals, trader_takeaway,
      warnings, missing_inputs, diagnostics, raw_inputs
    """
    as_of = datetime.now(timezone.utc).isoformat()

    # ── Compute each pillar ──────────────────────────────────────
    pillars: dict[str, dict[str, Any]] = {
        "participation_breadth": _compute_participation_breadth(participation_data),
        "trend_breadth": _compute_trend_breadth(trend_data),
        "volume_breadth": _compute_volume_breadth(volume_data),
        "leadership_quality": _compute_leadership_quality(leadership_data),
        "participation_stability": _compute_participation_stability(stability_data),
    }

    # ── Composite score ──────────────────────────────────────────
    # Formula: Σ(pillar_score × weight) / Σ(active_weights)
    weighted_parts: list[tuple[float | None, float]] = []
    for pname, weight in PILLAR_WEIGHTS.items():
        pdata = pillars.get(pname, {})
        weighted_parts.append((pdata.get("score"), weight))

    composite = _weighted_avg(weighted_parts)
    if composite is None:
        composite = 0.0
        logger.warning("event=breadth_composite_failed reason=no_valid_pillars")

    # ── Label mapping ────────────────────────────────────────────
    full_label, short_label = _label_from_score(composite)

    # ── Confidence score ─────────────────────────────────────────
    confidence, confidence_penalties = _compute_confidence(pillars, universe_meta)
    sig_quality = _signal_quality(confidence)

    # ── Explanation ───────────────────────────────────────────────
    explanation = _build_composite_explanation(
        composite, full_label, pillars, confidence)

    # ── Aggregate warnings and missing inputs ────────────────────
    all_warnings: list[str] = []
    all_missing: list[str] = []
    for pname, pdata in pillars.items():
        for w in pdata.get("warnings", []):
            all_warnings.append(f"[{pname}] {w}")
        for sm in pdata.get("submetrics", []):
            if sm.get("status") == "unavailable":
                all_missing.append(sm["name"])

    all_warnings.extend(confidence_penalties)

    # ── Build pillar-level summary dicts ─────────────────────────
    pillar_scores = {
        pname: round(pdata["score"], 2) if pdata.get("score") is not None else None
        for pname, pdata in pillars.items()
    }
    pillar_explanations = {
        pname: pdata.get("explanation", "")
        for pname, pdata in pillars.items()
    }

    # ── Diagnostics ──────────────────────────────────────────────
    # Compute full quality scores via diagnostics framework
    quality_scores = compute_quality_scores(pillars, universe_meta)
    grouped_warnings = group_warnings_for_ui(
        quality_scores["structured_warnings"]
    )

    diagnostics = {
        "pillar_weights": PILLAR_WEIGHTS,
        "pillar_details": {
            pname: {
                "score": pdata.get("score"),
                "submetrics": pdata.get("submetrics", []),
                "missing_count": pdata.get("missing_count", 0),
                "warning_count": len(pdata.get("warnings", [])),
            }
            for pname, pdata in pillars.items()
        },
        "confidence_penalties": confidence_penalties,
        "quality_scores": {
            "confidence_score": quality_scores["confidence_score"],
            "data_quality_score": quality_scores["data_quality_score"],
            "historical_validity_score": quality_scores["historical_validity_score"],
        },
        "quality_penalties": quality_scores["penalties"],
        "survivorship": quality_scores["survivorship"],
        "disagreement": quality_scores["disagreement"],
        "structured_warnings": quality_scores["structured_warnings"],
        "grouped_warnings": grouped_warnings,
        "total_submetrics": sum(
            len(p.get("submetrics", [])) for p in pillars.values()
        ),
        "unavailable_submetrics": len(all_missing),
        "composite_computation": {
            "formula": "sum(pillar_score * weight) / sum(active_weights)",
            "active_pillars": [
                pname for pname, pdata in pillars.items()
                if pdata.get("score") is not None
            ],
            "inactive_pillars": [
                pname for pname, pdata in pillars.items()
                if pdata.get("score") is None
            ],
        },
    }

    # ── Raw inputs (for UI debug panels) ─────────────────────────
    raw_inputs = {
        "participation": pillars["participation_breadth"].get("raw_inputs", {}),
        "trend": pillars["trend_breadth"].get("raw_inputs", {}),
        "volume": pillars["volume_breadth"].get("raw_inputs", {}),
        "leadership": pillars["leadership_quality"].get("raw_inputs", {}),
        "stability": pillars["participation_stability"].get("raw_inputs", {}),
        "universe": universe_meta,
    }

    result = {
        "engine": "breadth_participation",
        "as_of": as_of,
        "universe": {
            "name": universe_meta.get("name", "unknown"),
            "expected_count": universe_meta.get("expected_count", 0),
            "actual_count": universe_meta.get("actual_count", 0),
            "coverage_pct": round(
                universe_meta.get("actual_count", 0) /
                max(universe_meta.get("expected_count", 1), 1) * 100, 2
            ),
        },
        "score": round(composite, 2),
        "label": full_label,
        "short_label": short_label,
        "confidence_score": confidence,
        "data_quality_score": quality_scores["data_quality_score"],
        "historical_validity_score": quality_scores["historical_validity_score"],
        "signal_quality": sig_quality,
        "point_in_time_constituents_available": quality_scores["survivorship"]["point_in_time_available"],
        "survivorship_bias_risk": quality_scores["survivorship"]["survivorship_bias_risk"],
        "historical_validity_degraded": quality_scores["survivorship"]["historical_validity_degraded"],
        "summary": explanation["summary"],
        "pillar_scores": pillar_scores,
        "pillar_weights": PILLAR_WEIGHTS,
        "pillar_explanations": pillar_explanations,
        "positive_contributors": explanation["positive_contributors"],
        "negative_contributors": explanation["negative_contributors"],
        "conflicting_signals": explanation["conflicting_signals"],
        "trader_takeaway": explanation["trader_takeaway"],
        "warnings": all_warnings,
        "missing_inputs": all_missing,
        "diagnostics": diagnostics,
        "raw_inputs": raw_inputs,
    }

    logger.info(
        "event=breadth_engine_computed score=%.2f label=%s confidence=%.1f "
        "signal_quality=%s pillars=%s warnings=%d missing=%d",
        composite, full_label, confidence, sig_quality,
        {k: round(v, 1) if v is not None else None for k, v in pillar_scores.items()},
        len(all_warnings), len(all_missing),
    )

    return result
