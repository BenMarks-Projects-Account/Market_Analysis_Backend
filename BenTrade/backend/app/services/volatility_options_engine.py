"""Volatility & Options Structure Scoring Engine.

Institutional-grade engine answering: "How is the market pricing fear,
hedging demand, and option structure conditions?"

Architecture — 5 scored pillars:
  1. Volatility Regime       (25%)  — VIX level, trend, and regime classification
  2. Volatility Structure    (25%)  — term structure shape, contango/backwardation
  3. Tail Risk & Skew        (20%)  — skew, put demand, tail risk signals
  4. Positioning & Options   (15%)  — put/call ratios, option richness, VVIX
  5. Strategy Suitability    (15%)  — how well current conditions suit our strategies

Composite formula:
  VolComposite = Σ(pillar_score × weight) / Σ(active_weights)

Label mapping (composite → regime):
  85-100  →  Premium Selling Strongly Favored
  70-84   →  Constructive / Favorable Structure
  55-69   →  Mixed but Tradable
  45-54   →  Fragile / Neutral
  30-44   →  Risk Elevated
  0-29    →  Volatility Stress / Defensive

Confidence score (0-100) is independent of vol score, derived from
data completeness and cross-pillar agreement.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION — weights, thresholds, scoring bands
# ═══════════════════════════════════════════════════════════════════════

# Pillar weights (must sum to 1.0)
PILLAR_WEIGHTS: dict[str, float] = {
    "volatility_regime": 0.25,
    "volatility_structure": 0.25,
    "tail_risk_skew": 0.20,
    "positioning_options_posture": 0.15,
    "strategy_suitability": 0.15,
}

# Label mapping ranges — composite score → label
_LABEL_BANDS: list[tuple[float, float, str, str]] = [
    # (min, max, full_label, short_label)
    (85, 100, "Premium Selling Strongly Favored", "Strongly Favored"),
    (70, 84.99, "Constructive / Favorable Structure", "Favorable"),
    (55, 69.99, "Mixed but Tradable", "Mixed"),
    (45, 54.99, "Fragile / Neutral", "Fragile"),
    (30, 44.99, "Risk Elevated", "Elevated Risk"),
    (0, 29.99, "Volatility Stress / Defensive", "Defensive"),
]

# Confidence thresholds
_CONFIDENCE_HIGH = 80
_CONFIDENCE_MEDIUM = 60


# ═══════════════════════════════════════════════════════════════════════
# SCORING UTILITIES
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

    Formula: score = out_lo + (value - in_lo) / (in_hi - in_lo) * (out_hi - out_lo)
    Clamps to output range.
    """
    if in_hi == in_lo:
        return (out_lo + out_hi) / 2
    ratio = (value - in_lo) / (in_hi - in_lo)
    return _clamp(out_lo + ratio * (out_hi - out_lo), min(out_lo, out_hi), max(out_lo, out_hi))


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


def _aggregate_submetrics(
    submetrics: list[dict[str, Any]],
    weights: dict[str, float],
) -> tuple[float | None, str]:
    """Score a pillar from its submetrics using specified weights.

    Only includes submetrics present in weights dict.
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
# VIX SCORING — maps VIX level to premium-selling favorability (0-100)
# ═══════════════════════════════════════════════════════════════════════

def _vix_level_score(vix: float) -> float:
    """Score VIX level for premium-selling favorability.

    Scoring bands (VIX → score):
      12-18 → 80-95  (sweet spot — enough premium, not too risky)
      18-22 → 65-80  (elevated but manageable)
      22-30 → 40-65  (caution — vol rising)
      30-40 → 20-40  (high risk — only defined-risk trades)
      >40   → 0-20   (crisis)
      <12   → 60-80  (very low — limited premium but stable)

    Input: VIX spot value
    """
    if vix <= 12:
        # Very low VIX — stable but limited premium
        return _interpolate(vix, 8, 12, 60, 80)
    if vix <= 18:
        # Sweet spot for premium selling
        return _interpolate(vix, 12, 18, 80, 95)
    if vix <= 22:
        # Elevated but manageable
        return _interpolate(vix, 18, 22, 65, 80)
    if vix <= 30:
        # Getting risky
        return _interpolate(vix, 22, 30, 40, 65)
    if vix <= 40:
        # High vol — only defined risk
        return _interpolate(vix, 30, 40, 20, 40)
    # Crisis
    return _interpolate(vix, 40, 80, 0, 20)


def _vix_trend_score(vix_spot: float, vix_avg_20d: float | None) -> float | None:
    """Score VIX trend — declining VIX is favorable for premium selling.

    Formula: pct_change = (vix_spot - vix_avg_20d) / vix_avg_20d
    Declining VIX (negative pct_change) → higher score.

    Input: vix_spot, vix_avg_20d
    """
    if vix_avg_20d is None or vix_avg_20d <= 0:
        return None
    pct_change = (vix_spot - vix_avg_20d) / vix_avg_20d
    # Range: -0.30 (strongly declining) to +0.30 (spiking)
    # Declining = good for selling → higher score
    return _interpolate(pct_change, 0.30, -0.30, 20, 95)


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 1 — VOLATILITY REGIME (25%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_volatility_regime(data: dict[str, Any]) -> dict[str, Any]:
    """Classify the current volatility regime.

    Submetrics:
      vix_level — VIX spot scored for premium-selling favorability
      vix_trend — VIX direction (declining = favorable)
      vix_rank_30d — VIX rank over 30 days (proxy metric; moderate = best for selling)
      vix_percentile_1y — VIX percentile over 1 year (proxy metric)
      vvix_level — VVIX (vol of vol) — low = stable regime

    Weights within pillar:
      vix_level         35%
      vix_trend         20%
      vix_rank_30d      20%  (proxy metric)
      vix_percentile_1y  10%  (proxy metric)
      vvix_level        15%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    vix_spot = _safe_float(data.get("vix_spot"))
    vix_avg_20d = _safe_float(data.get("vix_avg_20d"))
    vix_rank = _safe_float(data.get("vix_rank_30d"))
    vix_pctl = _safe_float(data.get("vix_percentile_1y"))
    vvix = _safe_float(data.get("vvix"))

    raw_inputs = {
        "vix_spot": vix_spot, "vix_avg_20d": vix_avg_20d,
        "vix_rank_30d": vix_rank, "vix_percentile_1y": vix_pctl,
        "vvix": vvix,
    }

    # ── vix_level ────────────────────────────────────────────────
    # Input: VIX spot price
    if vix_spot is not None:
        vl_score = _vix_level_score(vix_spot)
        submetrics.append(_build_submetric("vix_level", vix_spot, vl_score))
    else:
        total_missing += 1
        warnings.append("vix_level: VIX spot unavailable")
        submetrics.append(_build_submetric("vix_level", None, None))

    # ── vix_trend ────────────────────────────────────────────────
    # Input: VIX spot, VIX 20-day average
    if vix_spot is not None and vix_avg_20d is not None:
        vt_score = _vix_trend_score(vix_spot, vix_avg_20d)
        if vt_score is not None:
            pct_chg = (vix_spot - vix_avg_20d) / vix_avg_20d
            submetrics.append(_build_submetric(
                "vix_trend", pct_chg, vt_score,
                details={"vix_spot": vix_spot, "vix_avg_20d": vix_avg_20d},
            ))
        else:
            total_missing += 1
            warnings.append("vix_trend: couldn't compute — bad average")
            submetrics.append(_build_submetric("vix_trend", None, None))
    else:
        total_missing += 1
        warnings.append("vix_trend: missing VIX spot or 20d average")
        submetrics.append(_build_submetric("vix_trend", None, None))

    # ── vix_rank_30d ───────────────────────────────────────────
    # Input: VIX rank proxy 0-100 (PROXY metric, not true option IV)
    # Moderate (20-50) is best for selling premium
    # Very high rank = contract expensive relative to history but may be risky
    # Very low rank = options cheap, limited premium available
    if vix_rank is not None:
        # Sweet spot: 20-50 → 75-95, low <20 → 50-75, high >50 → 40-75
        if vix_rank <= 20:
            vixr_score = _interpolate(vix_rank, 0, 20, 50, 75)
        elif vix_rank <= 50:
            vixr_score = _interpolate(vix_rank, 20, 50, 75, 95)
        elif vix_rank <= 70:
            vixr_score = _interpolate(vix_rank, 50, 70, 75, 55)
        else:
            vixr_score = _interpolate(vix_rank, 70, 100, 55, 30)
        submetrics.append(_build_submetric("vix_rank_30d", vix_rank, vixr_score))
    else:
        total_missing += 1
        warnings.append("vix_rank_30d (proxy): unavailable")
        submetrics.append(_build_submetric("vix_rank_30d", None, None))

    # ── vix_percentile_1y ──────────────────────────────────────
    # Input: VIX percentile proxy 0-100 (PROXY metric, not true option IV)
    # Year-long horizon provides longer-term context
    if vix_pctl is not None:
        if vix_pctl <= 25:
            vixp_score = _interpolate(vix_pctl, 0, 25, 55, 80)
        elif vix_pctl <= 50:
            vixp_score = _interpolate(vix_pctl, 25, 50, 80, 90)
        elif vix_pctl <= 75:
            vixp_score = _interpolate(vix_pctl, 50, 75, 70, 50)
        else:
            vixp_score = _interpolate(vix_pctl, 75, 100, 50, 25)
        submetrics.append(_build_submetric("vix_percentile_1y", vix_pctl, vixp_score))
    else:
        total_missing += 1
        warnings.append("vix_percentile_1y (proxy): unavailable")
        submetrics.append(_build_submetric("vix_percentile_1y", None, None))

    # ── vvix_level ───────────────────────────────────────────────
    # Input: VVIX index — low <85 = calm, 85-100 = normal, >100 = elevated
    # Lower VVIX = more stable vol regime → better for premium selling
    if vvix is not None:
        if vvix <= 80:
            vvix_score = _interpolate(vvix, 60, 80, 95, 85)
        elif vvix <= 100:
            vvix_score = _interpolate(vvix, 80, 100, 85, 60)
        elif vvix <= 120:
            vvix_score = _interpolate(vvix, 100, 120, 60, 35)
        else:
            vvix_score = _interpolate(vvix, 120, 160, 35, 10)
        submetrics.append(_build_submetric("vvix_level", vvix, vvix_score))
    else:
        total_missing += 1
        warnings.append("vvix_level: VVIX unavailable")
        submetrics.append(_build_submetric("vvix_level", None, None))

    # ── Aggregate ────────────────────────────────────────────────
    sub_weights = {
        "vix_level": 0.35,
        "vix_trend": 0.20,
        "vix_rank_30d": 0.20,
        "vix_percentile_1y": 0.10,
        "vvix_level": 0.15,
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
# PILLAR 2 — VOLATILITY STRUCTURE (25%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_volatility_structure(data: dict[str, Any]) -> dict[str, Any]:
    """Assess VIX term structure shape and IV vs RV dynamics.

    Submetrics:
      term_structure_shape — contango (normal) vs backwardation (stress)
      contango_steepness — degree of contango (steeper = more favorable)
      iv_rv_spread — implied vol minus realized vol (positive = overpriced options)
      vol_risk_premium — IV/RV ratio as risk premium indicator

    Weights within pillar:
      term_structure_shape  30%
      contango_steepness    20%
      iv_rv_spread          30%
      vol_risk_premium      20%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    vix_front = _safe_float(data.get("vix_front_month"))
    vix_2nd = _safe_float(data.get("vix_2nd_month"))
    vix_3rd = _safe_float(data.get("vix_3rd_month"))
    iv_30d = _safe_float(data.get("iv_30d"))
    rv_30d = _safe_float(data.get("rv_30d"))

    raw_inputs = {
        "vix_front_month": vix_front, "vix_2nd_month": vix_2nd,
        "vix_3rd_month": vix_3rd, "iv_30d": iv_30d, "rv_30d": rv_30d,
    }

    # ── term_structure_shape ─────────────────────────────────────
    # Formula: contango_ratio = vix_2nd / vix_front
    # >1.0 = contango (normal), <1.0 = backwardation (stress)
    if vix_front is not None and vix_2nd is not None and vix_front > 0:
        contango_ratio = vix_2nd / vix_front
        # contango (>1.0) is favorable: ratio 1.05-1.15 → 80-95
        # backwardation (<1.0) is unfavorable: ratio 0.85-0.95 → 15-40
        if contango_ratio >= 1.0:
            ts_score = _interpolate(contango_ratio, 1.0, 1.15, 65, 95)
        else:
            ts_score = _interpolate(contango_ratio, 0.85, 1.0, 10, 65)
        submetrics.append(_build_submetric(
            "term_structure_shape", contango_ratio, ts_score,
            details={"vix_front": vix_front, "vix_2nd": vix_2nd,
                     "shape": "contango" if contango_ratio >= 1.0 else "backwardation"},
        ))
    else:
        total_missing += 1
        warnings.append("term_structure_shape: missing VIX front/2nd month")
        submetrics.append(_build_submetric("term_structure_shape", None, None))

    # ── contango_steepness ───────────────────────────────────────
    # Formula: (vix_3rd - vix_front) / vix_front — full curve slope
    if vix_front is not None and vix_3rd is not None and vix_front > 0:
        steepness = (vix_3rd - vix_front) / vix_front
        # Positive steepness = healthy contango
        # Range: -0.15 (inverted) to +0.20 (steep contango) → 0-100
        steep_score = _interpolate(steepness, -0.15, 0.20, 10, 95)
        submetrics.append(_build_submetric(
            "contango_steepness", steepness, steep_score,
            details={"vix_front": vix_front, "vix_3rd": vix_3rd},
        ))
    else:
        total_missing += 1
        warnings.append("contango_steepness: missing VIX front/3rd month")
        submetrics.append(_build_submetric("contango_steepness", None, None))

    # ── iv_rv_spread ─────────────────────────────────────────────
    # Formula: iv_30d - rv_30d (in percentage points)
    # Positive = options overpriced (good for sellers)
    if iv_30d is not None and rv_30d is not None:
        spread = iv_30d - rv_30d
        # Range: -5 (underpriced) to +10 (overpriced) → 0-100
        spread_score = _interpolate(spread, -5, 10, 10, 95)
        submetrics.append(_build_submetric(
            "iv_rv_spread", spread, spread_score,
            details={"iv_30d": iv_30d, "rv_30d": rv_30d,
                     "premium": "positive" if spread > 0 else "negative"},
        ))
    else:
        total_missing += 1
        warnings.append("iv_rv_spread: missing IV or RV data")
        submetrics.append(_build_submetric("iv_rv_spread", None, None))

    # ── vol_risk_premium ─────────────────────────────────────────
    # Formula: iv_30d / rv_30d — >1.0 = options richer than realized
    if iv_30d is not None and rv_30d is not None and rv_30d > 0:
        vrp = iv_30d / rv_30d
        # Ratio 1.0-1.5 → 65-95 (healthy premium)
        # Ratio <1.0 → 20-65 (options cheap)
        # Ratio >1.5 → 80-60 (excessively expensive, may revert)
        if vrp >= 1.0 and vrp <= 1.5:
            vrp_score = _interpolate(vrp, 1.0, 1.5, 65, 95)
        elif vrp < 1.0:
            vrp_score = _interpolate(vrp, 0.5, 1.0, 20, 65)
        else:
            vrp_score = _interpolate(vrp, 1.5, 2.5, 80, 40)
        submetrics.append(_build_submetric(
            "vol_risk_premium", vrp, vrp_score,
            details={"label": "positive" if vrp > 1.0 else "negative"},
        ))
    else:
        total_missing += 1
        warnings.append("vol_risk_premium: missing IV or RV data")
        submetrics.append(_build_submetric("vol_risk_premium", None, None))

    # ── Aggregate ────────────────────────────────────────────────
    sub_weights = {
        "term_structure_shape": 0.30,
        "contango_steepness": 0.20,
        "iv_rv_spread": 0.30,
        "vol_risk_premium": 0.20,
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
# PILLAR 3 — TAIL RISK & SKEW (20%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_tail_risk_skew(data: dict[str, Any]) -> dict[str, Any]:
    """Assess skew, put demand, and tail risk indicators.

    Submetrics:
      cboe_skew — CBOE SKEW index (higher = more tail risk hedging)
      put_skew_25d — 25-delta put skew (put IV premium over ATM)
      tail_risk_signal — composite tail risk assessment

    Weights within pillar:
      cboe_skew         40%
      put_skew_25d      35%
      tail_risk_signal  25%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    skew = _safe_float(data.get("cboe_skew"))
    put_skew = _safe_float(data.get("put_skew_25d"))
    tail_risk_numeric = _safe_float(data.get("tail_risk_numeric"))  # numeric 0-100 for scoring
    tail_risk_label = data.get("tail_risk_signal")  # "Low"|"Moderate"|"Elevated"|"High" for UI

    raw_inputs = {
        "cboe_skew": skew, "put_skew_25d": put_skew,
        "tail_risk_signal": tail_risk_label,  # Label for diagnostics
        "tail_risk_numeric": tail_risk_numeric,  # Numeric for scoring
    }

    # ── cboe_skew ────────────────────────────────────────────────
    # Input: CBOE SKEW index — typical range 110-160
    # Lower = less tail concern (better for selling), higher = more hedging demand
    if skew is not None:
        # Invert: low skew (110-120) = high score (85-95), high skew (150+) = low score
        if skew <= 120:
            skew_score = _interpolate(skew, 100, 120, 95, 85)
        elif skew <= 135:
            skew_score = _interpolate(skew, 120, 135, 85, 60)
        elif skew <= 150:
            skew_score = _interpolate(skew, 135, 150, 60, 35)
        else:
            skew_score = _interpolate(skew, 150, 175, 35, 10)
        submetrics.append(_build_submetric("cboe_skew", skew, skew_score))
    else:
        total_missing += 1
        warnings.append("cboe_skew: CBOE SKEW index unavailable")
        submetrics.append(_build_submetric("cboe_skew", None, None))

    # ── put_skew_25d ─────────────────────────────────────────────
    # Input: 25-delta put skew in percentage points (put IV - ATM IV)
    # Typical range: 2-10%. Low = calm, high = fear
    if put_skew is not None:
        # Low skew (2-4%) = favorable (score 80-90)
        # Moderate (4-7%) = normal (60-80)
        # High (>7%) = stressed (20-60)
        if put_skew <= 4:
            ps_score = _interpolate(put_skew, 0, 4, 90, 80)
        elif put_skew <= 7:
            ps_score = _interpolate(put_skew, 4, 7, 80, 55)
        else:
            ps_score = _interpolate(put_skew, 7, 15, 55, 15)
        submetrics.append(_build_submetric("put_skew_25d", put_skew, ps_score))
    else:
        total_missing += 1
        warnings.append("put_skew_25d: 25-delta put skew unavailable")
        submetrics.append(_build_submetric("put_skew_25d", None, None))

    # ── tail_risk_signal ─────────────────────────────────────────
    # Input: composite numeric 0-100 (0 = no tail risk, 100 = extreme)
    # Label: "Low"|"Moderate"|"Elevated"|"High" for UI/diagnostics
    # Invert for scoring: low tail risk = high score
    if tail_risk_numeric is not None:
        # 0-20 = favorable (85-95), 20-50 = moderate (50-85), 50+ = elevated (10-50)
        tr_score = _interpolate(tail_risk_numeric, 0, 100, 95, 5)
        submetrics.append(_build_submetric(
            "tail_risk_signal", tail_risk_numeric, tr_score,
            details={"label": tail_risk_label},
        ))
    else:
        total_missing += 1
        warnings.append("tail_risk_signal: tail risk signal unavailable (need put_skew or cboe_skew)")
        submetrics.append(_build_submetric("tail_risk_signal", None, None))

    # ── Aggregate ────────────────────────────────────────────────
    sub_weights = {
        "cboe_skew": 0.40,
        "put_skew_25d": 0.35,
        "tail_risk_signal": 0.25,
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
# PILLAR 4 — POSITIONING & OPTIONS POSTURE (15%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_positioning_options(data: dict[str, Any]) -> dict[str, Any]:
    """Assess put/call ratios, option richness, and positioning signals.

    Submetrics:
      equity_pc_ratio — equity put/call ratio (low = bullish)
      spy_pc_ratio_proxy — SPY P/C ratio used as index-level proxy (context for hedging)
      option_richness — overall option pricing relative to historical (blended logic)
      premium_bias — net bias toward selling or buying premium

    Weights within pillar:
      equity_pc_ratio       30%
      spy_pc_ratio_proxy    25%  (PROXY: SPY options, not broader index)
      option_richness       25%  (DERIVED: blended VIX rank + IV-RV spread)
      premium_bias          20%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    eq_pc = _safe_float(data.get("equity_pc_ratio"))
    spy_pc_proxy = _safe_float(data.get("spy_pc_ratio_proxy"))  # SPY P/C as index proxy
    richness = _safe_float(data.get("option_richness"))
    richness_label = data.get("option_richness_label")  # "Rich"|"Fair"|"Cheap" from blended logic
    bias = _safe_float(data.get("premium_bias"))

    raw_inputs = {
        "equity_pc_ratio": eq_pc, "spy_pc_ratio_proxy": spy_pc_proxy,
        "option_richness": richness, "option_richness_label": richness_label,
        "premium_bias": bias,
    }

    # ── equity_pc_ratio ──────────────────────────────────────────
    # Input: equity put/call ratio — typical 0.40-1.20
    # Low (<0.65) = bullish sentiment (generally good for selling puts)
    # High (>1.0) = bearish/fearful (options demand high, could sell)
    # Sweet spot for premium selling: moderate fear (0.7-0.9)
    if eq_pc is not None:
        if eq_pc <= 0.5:
            epc_score = _interpolate(eq_pc, 0.3, 0.5, 55, 70)
        elif eq_pc <= 0.7:
            epc_score = _interpolate(eq_pc, 0.5, 0.7, 70, 85)
        elif eq_pc <= 0.9:
            epc_score = _interpolate(eq_pc, 0.7, 0.9, 85, 80)
        elif eq_pc <= 1.1:
            epc_score = _interpolate(eq_pc, 0.9, 1.1, 80, 60)
        else:
            epc_score = _interpolate(eq_pc, 1.1, 1.5, 60, 30)
        submetrics.append(_build_submetric("equity_pc_ratio", eq_pc, epc_score))
    else:
        total_missing += 1
        warnings.append("equity_pc_ratio: unavailable")
        submetrics.append(_build_submetric("equity_pc_ratio", None, None))

    # ── spy_pc_ratio_proxy ───────────────────────────────────────
    # Input: SPY put/call ratio used as index-level proxy
    # (No dedicated index options feed, so SPY proxy is best available)
    # Typical range: 0.4-1.2 (similar to equity, but context-dependent)
    # Normal hedging (0.7-0.9) = stable, elevated >1.0 = fear, <0.65 = calm
    if spy_pc_proxy is not None:
        if spy_pc_proxy <= 0.65:
            spy_score = _interpolate(spy_pc_proxy, 0.3, 0.65, 75, 90)
        elif spy_pc_proxy <= 0.85:
            spy_score = _interpolate(spy_pc_proxy, 0.65, 0.85, 90, 85)
        elif spy_pc_proxy <= 1.05:
            spy_score = _interpolate(spy_pc_proxy, 0.85, 1.05, 85, 65)
        else:
            spy_score = _interpolate(spy_pc_proxy, 1.05, 1.5, 65, 30)
        submetrics.append(_build_submetric(
            "spy_pc_ratio_proxy", spy_pc_proxy, spy_score,
            details={"proxy_note": "SPY options proxy for broader index context"},
        ))
    else:
        total_missing += 1
        warnings.append("spy_pc_ratio_proxy: SPY options P/C data unavailable")
        submetrics.append(_build_submetric("spy_pc_ratio_proxy", None, None))

    # ── option_richness ──────────────────────────────────────────
    # Input: 0-100 scale (Cheap ~25, Fair ~50, Rich ~75) from blended logic
    # Blended logic: Rich if (vix_rank>60 AND iv>rv), Cheap if (vix_rank<30 OR iv≤rv), else Fair
    # Label: "Cheap"|"Fair"|"Rich" from data provider's blended logic
    if richness is not None:
        rich_score = _interpolate(richness, 0, 100, 30, 95)
        # Use label if available from provider (more reliable), else compute
        label = richness_label or ("cheap" if richness < 30 else "fair" if richness < 60 else "rich")
        submetrics.append(_build_submetric(
            "option_richness", richness, rich_score,
            details={"label": label, "methodology": "blended VIX rank + IV-RV spread"},
        ))
    else:
        total_missing += 1
        warnings.append("option_richness: unavailable (need VIX rank and IV/RV context)")
        submetrics.append(_build_submetric("option_richness", None, None))

    # ── premium_bias ─────────────────────────────────────────────
    # Input: -100 (strongly favors buying) to +100 (strongly favors selling)
    if bias is not None:
        bias_score = _interpolate(bias, -100, 100, 10, 95)
        submetrics.append(_build_submetric(
            "premium_bias", bias, bias_score,
            details={"direction": "sell" if bias > 0 else "buy"},
        ))
    else:
        total_missing += 1
        warnings.append("premium_bias: unavailable")
        submetrics.append(_build_submetric("premium_bias", None, None))

    # ── Aggregate ────────────────────────────────────────────────
    sub_weights = {
        "equity_pc_ratio": 0.30,
        "spy_pc_ratio_proxy": 0.25,  # Proxy metric
        "option_richness": 0.25,      # Blended derived metric
        "premium_bias": 0.20,
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
# PILLAR 5 — STRATEGY SUITABILITY (15%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_strategy_suitability(
    regime_data: dict[str, Any],
    structure_data: dict[str, Any],
    skew_data: dict[str, Any],
    positioning_data: dict[str, Any],
) -> dict[str, Any]:
    """Score how well current conditions suit specific strategy families.

    This pillar is derived from the other four pillars' raw data.

    Submetrics:
      premium_selling — iron condors, credit spreads, short strangles
      directional — debit spreads, long straddles
      vol_structure_plays — calendars, diagonals
      hedging — protective puts, collars

    Weights within pillar:
      premium_selling       40%  (our primary strategy family)
      directional           20%
      vol_structure_plays   20%
      hedging               20%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    total_missing = 0

    # Gather inputs from other pillar data
    vix_spot = _safe_float(regime_data.get("vix_spot"))
    vix_rank = _safe_float(regime_data.get("vix_rank_30d"))
    vvix = _safe_float(regime_data.get("vvix"))
    iv_30d = _safe_float(structure_data.get("iv_30d"))
    rv_30d = _safe_float(structure_data.get("rv_30d"))
    vix_front = _safe_float(structure_data.get("vix_front_month"))
    vix_2nd = _safe_float(structure_data.get("vix_2nd_month"))
    skew = _safe_float(skew_data.get("cboe_skew"))
    eq_pc = _safe_float(positioning_data.get("equity_pc_ratio"))

    raw_inputs = {
        "vix_spot": vix_spot, "vix_rank_30d": vix_rank, "vvix": vvix,
        "iv_30d": iv_30d, "rv_30d": rv_30d,
        "vix_front_month": vix_front, "vix_2nd_month": vix_2nd,
        "cboe_skew": skew, "equity_pc_ratio": eq_pc,
    }

    # ── premium_selling ──────────────────────────────────────────
    # Best when: VIX 12-25, IV > RV, contango, moderate skew, moderate P/C
    # Formula: weighted average of component scores
    ps_components: list[tuple[float | None, float]] = []

    if vix_spot is not None:
        ps_components.append((_vix_level_score(vix_spot), 0.30))
    if iv_30d is not None and rv_30d is not None and rv_30d > 0:
        # IV > RV premium
        vrp = iv_30d / rv_30d
        vrp_s = _interpolate(vrp, 0.8, 1.4, 30, 95) if vrp <= 1.4 else _interpolate(vrp, 1.4, 2.0, 95, 70)
        ps_components.append((vrp_s, 0.25))
    if vix_front is not None and vix_2nd is not None and vix_front > 0:
        contango = vix_2nd / vix_front
        cs = _interpolate(contango, 0.90, 1.10, 30, 90)
        ps_components.append((cs, 0.20))
    if skew is not None:
        # Moderate skew is fine
        ss = _interpolate(skew, 150, 110, 30, 90)
        ps_components.append((ss, 0.15))
    if vix_rank is not None:
        # Moderate IV rank = good premium
        ir_s = _interpolate(vix_rank, 0, 50, 40, 90) if vix_rank <= 50 else _interpolate(vix_rank, 50, 100, 90, 50)
        ps_components.append((ir_s, 0.10))

    ps_score = _weighted_avg(ps_components)
    if ps_score is not None:
        submetrics.append(_build_submetric(
            "premium_selling", ps_score, ps_score,
            details={"description": "Iron condors, credit spreads, short strangles"},
        ))
    else:
        total_missing += 1
        warnings.append("premium_selling: insufficient data to score")
        submetrics.append(_build_submetric("premium_selling", None, None))

    # ── directional ──────────────────────────────────────────────
    # Best when: high IV (cheap debit spreads relative to movement),
    # or very low IV (cheap long straddles before expansion)
    dir_components: list[tuple[float | None, float]] = []

    if vix_rank is not None:
        # Low IV rank = good for buying (call debit spreads cheap)
        # High IV rank = good for selling direction (expensive premium to sell)
        # U-shaped: best at extremes, worst in middle
        if vix_rank <= 20:
            dir_iv_s = _interpolate(vix_rank, 0, 20, 80, 55)
        elif vix_rank <= 60:
            dir_iv_s = _interpolate(vix_rank, 20, 60, 55, 40)
        else:
            dir_iv_s = _interpolate(vix_rank, 60, 100, 40, 65)
        dir_components.append((dir_iv_s, 0.40))
    if vix_spot is not None:
        # High VIX = bigger moves = directional opportunities
        dir_vix_s = _interpolate(vix_spot, 10, 35, 30, 80)
        dir_components.append((dir_vix_s, 0.35))
    if vvix is not None:
        # High VVIX = vol may expand, good for long vol
        dir_vvix_s = _interpolate(vvix, 70, 130, 35, 80)
        dir_components.append((dir_vvix_s, 0.25))

    dir_score = _weighted_avg(dir_components)
    if dir_score is not None:
        submetrics.append(_build_submetric(
            "directional", dir_score, dir_score,
            details={"description": "Debit spreads, long straddles"},
        ))
    else:
        total_missing += 1
        warnings.append("directional: insufficient data")
        submetrics.append(_build_submetric("directional", None, None))

    # ── vol_structure_plays ──────────────────────────────────────
    # Best when: steep contango (calendars benefit), moderate vol
    vs_components: list[tuple[float | None, float]] = []

    if vix_front is not None and vix_2nd is not None and vix_front > 0:
        contango = vix_2nd / vix_front
        # Steep contango = great for calendars
        vs_c = _interpolate(contango, 0.95, 1.15, 25, 95)
        vs_components.append((vs_c, 0.50))
    if vix_spot is not None:
        # Moderate VIX is ideal for calendar spreads
        if vix_spot <= 20:
            vs_v = _interpolate(vix_spot, 10, 20, 60, 85)
        else:
            vs_v = _interpolate(vix_spot, 20, 35, 85, 45)
        vs_components.append((vs_v, 0.30))
    if vix_rank is not None:
        # Moderate IV rank is best for structure plays
        vs_ir = _interpolate(vix_rank, 0, 50, 50, 85) if vix_rank <= 50 else _interpolate(vix_rank, 50, 100, 85, 45)
        vs_components.append((vs_ir, 0.20))

    vs_score = _weighted_avg(vs_components)
    if vs_score is not None:
        submetrics.append(_build_submetric(
            "vol_structure_plays", vs_score, vs_score,
            details={"description": "Calendar spreads, diagonals"},
        ))
    else:
        total_missing += 1
        warnings.append("vol_structure_plays: insufficient data")
        submetrics.append(_build_submetric("vol_structure_plays", None, None))

    # ── hedging ──────────────────────────────────────────────────
    # Hedging is cheapest when: low IV, low skew, calm markets
    # But most needed when conditions are ugly — score = cheapness
    hdg_components: list[tuple[float | None, float]] = []

    if vix_rank is not None:
        # Low IV = cheap hedges → high score
        hdg_iv = _interpolate(vix_rank, 0, 100, 90, 20)
        hdg_components.append((hdg_iv, 0.40))
    if skew is not None:
        # Low skew = cheaper puts → higher score
        hdg_skew = _interpolate(skew, 110, 160, 90, 25)
        hdg_components.append((hdg_skew, 0.35))
    if vix_spot is not None:
        # Low VIX = cheap puts
        hdg_vix = _interpolate(vix_spot, 10, 40, 90, 20)
        hdg_components.append((hdg_vix, 0.25))

    hdg_score = _weighted_avg(hdg_components)
    if hdg_score is not None:
        submetrics.append(_build_submetric(
            "hedging", hdg_score, hdg_score,
            details={"description": "Protective puts, collars"},
        ))
    else:
        total_missing += 1
        warnings.append("hedging: insufficient data")
        submetrics.append(_build_submetric("hedging", None, None))

    # ── Aggregate ────────────────────────────────────────────────
    sub_weights = {
        "premium_selling": 0.40,
        "directional": 0.20,
        "vol_structure_plays": 0.20,
        "hedging": 0.20,
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
# LABEL / SIGNAL QUALITY HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _label_from_score(score: float) -> tuple[str, str]:
    """Map composite score to (full_label, short_label)."""
    for lo, hi, full, short in _LABEL_BANDS:
        if lo <= score <= hi:
            return full, short
    return "Unknown", "Unknown"


def _signal_quality(confidence: float) -> str:
    """Map confidence score to signal quality label."""
    if confidence >= _CONFIDENCE_HIGH:
        return "high"
    if confidence >= _CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


# ═══════════════════════════════════════════════════════════════════════
# CONFIDENCE SCORE
# ═══════════════════════════════════════════════════════════════════════

def _compute_confidence(
    pillars: dict[str, dict[str, Any]],
) -> tuple[float, list[str]]:
    """Compute confidence score (0-100) independent of vol score.

    Factors:
      - Data completeness: -5 per missing submetric
      - Cross-pillar agreement: -penalty if pillars diverge >30 points
    """
    base = 100.0
    penalties: list[str] = []

    # Data completeness penalty
    total_missing = sum(p.get("missing_count", 0) for p in pillars.values())
    if total_missing > 0:
        penalty = min(total_missing * 5, 40)
        base -= penalty
        penalties.append(f"missing_data: {total_missing} inputs missing (-{penalty:.1f})")

    # Cross-pillar disagreement penalty
    valid_scores = [
        p["score"] for p in pillars.values() if p.get("score") is not None
    ]
    if len(valid_scores) >= 2:
        spread = max(valid_scores) - min(valid_scores)
        if spread > 30:
            penalty = min((spread - 30) * 0.5, 15)
            base -= penalty
            penalties.append(
                f"pillar_disagreement: spread {spread:.0f} points (-{penalty:.1f})"
            )

    # Penalty if fewer than 3 pillars have data
    active_pillars = sum(1 for p in pillars.values() if p.get("score") is not None)
    if active_pillars < 3:
        penalty = (3 - active_pillars) * 10
        base -= penalty
        penalties.append(f"few_active_pillars: only {active_pillars} (-{penalty:.1f})")

    return round(_clamp(base), 2), penalties


# ═══════════════════════════════════════════════════════════════════════
# COMPOSITE EXPLANATION BUILDER
# ═══════════════════════════════════════════════════════════════════════

def _build_composite_explanation(
    composite: float,
    label: str,
    pillars: dict[str, dict[str, Any]],
    confidence: float,
) -> dict[str, Any]:
    """Build structured explanation for UI rendering."""
    positive: list[str] = []
    negative: list[str] = []
    conflicting: list[str] = []

    for pname, pdata in pillars.items():
        score = pdata.get("score")
        if score is None:
            continue
        readable = pname.replace("_", " ").title()
        if score >= 70:
            positive.append(f"{readable} is favorable ({score:.0f}/100)")
        elif score >= 55:
            positive.append(f"{readable} is constructive ({score:.0f}/100)")
        elif score < 40:
            negative.append(f"{readable} is stressed ({score:.0f}/100)")
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

    # Build summary
    summary_parts: list[str] = [
        f"Volatility regime is {label.lower()} (composite {composite:.0f}/100)."
    ]
    if positive:
        summary_parts.append(f"Strengths: {positive[0].split(' is ')[0].lower()}.")
    if negative:
        summary_parts.append(f"Concerns: {negative[0].split(' is ')[0].lower()}.")

    # Trader takeaway
    if composite >= 85:
        takeaway = (
            "Premium-selling conditions are excellent. IV exceeds realized, "
            "term structure is healthy, and risk is well-contained. "
            "Iron condors, credit spreads, and short strangles on index ETFs "
            "are strongly favored. Full position sizing is appropriate."
        )
    elif composite >= 70:
        takeaway = (
            "Conditions are constructive for premium selling. "
            "Structure supports income strategies with normal position sizing. "
            "Monitor for any deterioration in term structure or skew."
        )
    elif composite >= 55:
        takeaway = (
            "Mixed but tradable conditions. Premium selling is viable "
            "but use smaller position sizes and tighter risk management. "
            "Watch for directional opportunities as vol may be transitioning."
        )
    elif composite >= 45:
        takeaway = (
            "Conditions are fragile. Keep positions small and risk well-defined. "
            "Consider hedging existing short vol exposure. "
            "Calendar spreads or protective structures may be preferable."
        )
    elif composite >= 30:
        takeaway = (
            "Risk is elevated. Reduce premium selling activity. "
            "Use only defined-risk strategies with tight width. "
            "Consider protective puts or collar strategies."
        )
    else:
        takeaway = (
            "Volatility stress regime — defensive posture recommended. "
            "Avoid new premium selling. Close or hedge existing short vol positions. "
            "Long vol strategies (VIX calls, long straddles) may have edge. "
            "Wait for regime normalization before returning to income strategies."
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

def compute_volatility_scores(
    regime_data: dict[str, Any],
    structure_data: dict[str, Any],
    skew_data: dict[str, Any],
    positioning_data: dict[str, Any],
) -> dict[str, Any]:
    """Compute the Volatility & Options Structure engine result.

    Parameters
    ----------
    regime_data : dict
        Raw inputs for Pillar 1 (VIX spot, trend, IV rank, VVIX)
    structure_data : dict
        Raw inputs for Pillar 2 (term structure, IV vs RV)
    skew_data : dict
        Raw inputs for Pillar 3 (CBOE skew, put skew, tail risk)
    positioning_data : dict
        Raw inputs for Pillar 4 (put/call ratios, richness, bias)

    Note: Pillar 5 (strategy suitability) is derived from the other
    four pillars' raw data — no separate input dict needed.

    Returns
    -------
    dict with engine result conforming to canonical structure.
    """
    as_of = datetime.now(timezone.utc).isoformat()
    logger.info("event=vol_engine_start")

    # ── Compute each pillar (guarded — one failure won't crash all) ──
    _pillar_fns: list[tuple[str, Any, tuple]] = [
        ("volatility_regime", _compute_volatility_regime, (regime_data,)),
        ("volatility_structure", _compute_volatility_structure, (structure_data,)),
        ("tail_risk_skew", _compute_tail_risk_skew, (skew_data,)),
        ("positioning_options_posture", _compute_positioning_options, (positioning_data,)),
        ("strategy_suitability", _compute_strategy_suitability,
         (regime_data, structure_data, skew_data, positioning_data)),
    ]

    pillars: dict[str, dict[str, Any]] = {}
    for pname, fn, args in _pillar_fns:
        try:
            pillars[pname] = fn(*args)
            logger.debug("event=pillar_computed pillar=%s score=%s",
                         pname, pillars[pname].get("score"))
        except Exception as exc:
            logger.error(
                "event=pillar_failed pillar=%s error=%s", pname, exc,
                exc_info=True,
            )
            pillars[pname] = {
                "score": None,
                "submetrics": [],
                "explanation": f"Pillar computation failed: {exc}",
                "warnings": [f"pillar_error: {exc}"],
                "raw_inputs": {},
                "missing_count": 0,
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
        logger.warning("event=vol_composite_failed reason=no_valid_pillars")

    # ── Label mapping ────────────────────────────────────────────
    full_label, short_label = _label_from_score(composite)

    # ── Confidence ───────────────────────────────────────────────
    confidence, confidence_penalties = _compute_confidence(pillars)
    sig_quality = _signal_quality(confidence)

    # ── Explanation ───────────────────────────────────────────────
    try:
        explanation = _build_composite_explanation(
            composite, full_label, pillars, confidence)
    except Exception as exc:
        logger.error("event=explanation_failed error=%s", exc, exc_info=True)
        explanation = {
            "summary": f"Volatility regime is {full_label.lower()} (composite {composite:.0f}/100).",
            "positive_contributors": [],
            "negative_contributors": [],
            "conflicting_signals": [],
            "trader_takeaway": "Explanation generation failed — review pillar data.",
        }

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

    # ── Pillar summaries ─────────────────────────────────────────
    pillar_scores = {
        pname: round(pdata["score"], 2) if pdata.get("score") is not None else None
        for pname, pdata in pillars.items()
    }
    pillar_explanations = {
        pname: pdata.get("explanation", "")
        for pname, pdata in pillars.items()
    }

    # ── Strategy suitability detail (for UI rendering) ───────────
    strat_pillar = pillars.get("strategy_suitability", {})
    strategy_scores = {}
    for sm in strat_pillar.get("submetrics", []):
        strategy_scores[sm["name"]] = {
            "score": sm.get("score"),
            "description": sm.get("details", {}).get("description", ""),
        }

    # ── Diagnostics ──────────────────────────────────────────────
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

    # ── Raw inputs ───────────────────────────────────────────────
    raw_inputs = {
        "regime": pillars["volatility_regime"].get("raw_inputs", {}),
        "structure": pillars["volatility_structure"].get("raw_inputs", {}),
        "skew": pillars["tail_risk_skew"].get("raw_inputs", {}),
        "positioning": pillars["positioning_options_posture"].get("raw_inputs", {}),
        "strategy": pillars["strategy_suitability"].get("raw_inputs", {}),
    }

    result = {
        "engine": "volatility_options",
        "as_of": as_of,
        "score": round(composite, 2),
        "label": full_label,
        "short_label": short_label,
        "confidence_score": confidence,
        "signal_quality": sig_quality,
        "summary": explanation["summary"],
        "pillar_scores": pillar_scores,
        "pillar_weights": PILLAR_WEIGHTS,
        "pillar_explanations": pillar_explanations,
        "strategy_scores": strategy_scores,
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
        "event=vol_engine_computed score=%.2f label=%s confidence=%.1f "
        "signal_quality=%s pillars=%s warnings=%d missing=%d",
        composite, full_label, confidence, sig_quality,
        {k: round(v, 1) if v is not None else None for k, v in pillar_scores.items()},
        len(all_warnings), len(all_missing),
    )

    return result
