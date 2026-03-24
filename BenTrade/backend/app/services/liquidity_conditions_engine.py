"""Liquidity & Financial Conditions — Deterministic Scoring Engine.

Computes a composite liquidity/conditions score (0–100) from five pillars:

  1. Rates & Policy Pressure              (25%)
  2. Financial Conditions Tightness       (25%)
  3. Credit & Funding Stress              (20%)
  4. Dollar / Global Liquidity Pressure   (15%)
  5. Liquidity Stability & Fragility      (15%)

Composite formula:
  LiquidityConditionsComposite =
      0.25 * RatesPolicyPressure
    + 0.25 * FinancialConditionsTightness
    + 0.20 * CreditFundingStress
    + 0.15 * DollarGlobalLiquidity
    + 0.15 * LiquidityStabilityFragility

All calculations are deterministic, auditable, and do NOT use any LLM.
The LLM model-analysis layer consumes this engine's raw outputs only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# GLOBAL CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

PILLAR_WEIGHTS: dict[str, float] = {
    "rates_policy_pressure": 0.25,
    "financial_conditions_tightness": 0.25,
    "credit_funding_stress": 0.20,
    "dollar_global_liquidity": 0.15,
    "liquidity_stability_fragility": 0.15,
}

# Label bands: (lo, hi, full_label, short_label)
_LABEL_BANDS: list[tuple[float, float, str, str]] = [
    (85, 100, "Liquidity Strongly Supportive", "Strongly Supportive"),
    (70, 84.99, "Supportive Conditions", "Supportive"),
    (55, 69.99, "Mixed but Manageable", "Mixed"),
    (45, 54.99, "Neutral / Tightening", "Tightening"),
    (30, 44.99, "Restrictive Conditions", "Restrictive"),
    (0, 29.99, "Liquidity Stress", "Stress"),
]

# Signal provenance — every input signal with source, type, delay, notes
SIGNAL_PROVENANCE: dict[str, dict[str, str]] = {
    # ── Rates & Policy (direct from FRED) ─────────────────────────
    "two_year_yield": {
        "source": "FRED DGS2",
        "type": "direct",
        "delay": "EOD (previous business day)",
        "unit": "%",
        "notes": "2-year Treasury constant maturity. Front-end rate pressure proxy.",
    },
    "ten_year_yield": {
        "source": "FRED DGS10",
        "type": "direct",
        "delay": "EOD (previous business day)",
        "unit": "%",
        "notes": "10-year Treasury constant maturity. Long-end rate context.",
    },
    "fed_funds_rate": {
        "source": "FRED DFF",
        "type": "direct",
        "delay": "EOD",
        "unit": "%",
        "notes": "Effective federal funds rate. Policy stance indicator.",
    },
    "yield_curve_spread": {
        "source": "Derived (10Y - 2Y)",
        "type": "derived",
        "delay": "EOD",
        "unit": "%",
        "notes": "Yield curve slope. Inversion signals restrictive conditions.",
    },
    # ── Financial Conditions ──────────────────────────────────────
    "vix_level": {
        "source": "Tradier / Finnhub / FRED waterfall",
        "type": "direct",
        "delay": "near-realtime when market open, EOD otherwise",
        "unit": "index level",
        "notes": "VIX as financial conditions proxy. Higher VIX = tighter conditions.",
    },
    "financial_conditions_proxy": {
        "source": "Derived composite (VIX + credit + rates)",
        "type": "proxy",
        "delay": "EOD",
        "unit": "index (0-100)",
        "notes": "Composite proxy for broad financial conditions. Not a true FCI index.",
    },
    # ── Credit & Funding ──────────────────────────────────────────
    "ig_spread": {
        "source": "FRED BAMLC0A0CM (ICE BofA US Corporate IG OAS)",
        "type": "direct",
        "delay": "EOD (1-day lag typical)",
        "unit": "% (OAS)",
        "notes": "IG corporate credit spread. Wider = more stress.",
    },
    "hy_spread": {
        "source": "FRED BAMLH0A0HYM2 (ICE BofA US HY OAS)",
        "type": "direct",
        "delay": "EOD (1-day lag typical)",
        "unit": "% (OAS)",
        "notes": "HY corporate credit spread. Wider = more stress.",
    },
    "funding_stress_proxy": {
        "source": "Derived (VIX + credit + rate level heuristic)",
        "type": "proxy",
        "delay": "EOD",
        "unit": "score (0-100)",
        "notes": "Proxy for funding stress. True SOFR/FRA-OIS not yet integrated.",
    },
    # ── Dollar / Global Liquidity ─────────────────────────────────
    "usd_index": {
        "source": "FRED DTWEXBGS",
        "type": "direct",
        "delay": "EOD / weekly",
        "unit": "index",
        "notes": "Trade-weighted USD index. Stronger dollar = tighter global liquidity.",
    },
    # ── Stability ─────────────────────────────────────────────────
    "cpi_yoy": {
        "source": "FRED CPIAUCSL (derived YoY)",
        "type": "derived",
        "delay": "Monthly (~2 week lag)",
        "unit": "% YoY",
        "notes": "Inflation context for rate/policy interpretation.",
    },
}


# ═══════════════════════════════════════════════════════════════════════
# SCORING UTILITIES
# ═══════════════════════════════════════════════════════════════════════

def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, val))


def _safe_float(val: Any, *, default: float | None = None) -> float | None:
    """Convert to float safely; returns default on failure."""
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _interpolate(
    value: float,
    in_lo: float,
    in_hi: float,
    out_lo: float = 0.0,
    out_hi: float = 100.0,
) -> float:
    """Linear interpolation with clamping.

    Maps value from [in_lo, in_hi] → [out_lo, out_hi], clamped.
    """
    if in_hi == in_lo:
        return (out_lo + out_hi) / 2
    ratio = (value - in_lo) / (in_hi - in_lo)
    ratio = max(0.0, min(1.0, ratio))
    return out_lo + ratio * (out_hi - out_lo)


def _weighted_avg(parts: list[tuple[float | None, float]]) -> float | None:
    """Weighted average ignoring None entries.

    Parameters
    ----------
    parts : list of (score_or_None, weight) tuples

    Returns None if no valid scores.
    """
    total_weight = 0.0
    total_value = 0.0
    for score, weight in parts:
        if score is not None:
            total_weight += weight
            total_value += score * weight
    if total_weight == 0:
        return None
    return round(total_value / total_weight, 2)


def _label_from_score(score: float) -> tuple[str, str]:
    """Map composite score to (full_label, short_label)."""
    for lo, hi, full, short in _LABEL_BANDS:
        if lo <= score <= hi:
            return full, short
    return "Unknown", "Unknown"


def _signal_quality(confidence: float) -> str:
    """Map confidence to signal quality tier."""
    if confidence >= 80:
        return "high"
    if confidence >= 60:
        return "medium"
    return "low"


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 1 — RATES & POLICY PRESSURE (25%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_rates_policy_pressure(data: dict[str, Any]) -> dict[str, Any]:
    """Score rates and policy pressure for risk assets.

    Higher score = more supportive (easing, low pressure).
    Lower score = restrictive (tightening, high front-end pressure).

    Submetrics (weights):
      two_year_yield_level   (25%) — front-end rate pressure
      ten_year_yield_level   (20%) — long-end context
      policy_pressure_proxy  (20%) — fed funds vs neutral estimate
      curve_context_signal   (15%) — yield curve shape
      front_end_rate_press   (10%) — 2Y absolute level pressure
      rate_trend_pressure    (10%) — implied rate direction from curve
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    missing_count = 0

    two_y = _safe_float(data.get("two_year_yield"))
    ten_y = _safe_float(data.get("ten_year_yield"))
    fed_funds = _safe_float(data.get("fed_funds_rate"))
    curve_spread = _safe_float(data.get("yield_curve_spread"))

    raw_inputs = {
        "two_year_yield": two_y,
        "ten_year_yield": ten_y,
        "fed_funds_rate": fed_funds,
        "yield_curve_spread": curve_spread,
    }

    # --- Submetric 1: Two-year yield level (25%) ---
    # Lower 2Y = less front-end pressure = more supportive
    # 2Y context: 0% = very supportive, 3% = neutral, 5.5%+ = very restrictive
    if two_y is not None:
        # Score: low yields supportive (100), high yields restrictive (0)
        two_y_score = _clamp(_interpolate(two_y, 0.5, 5.5, 100, 0))
        submetrics.append({
            "name": "two_year_yield_level",
            "raw_value": two_y,
            "score": round(two_y_score, 1),
            "weight": 0.25,
            "status": "ok",
            "interpretation": (
                f"2Y at {two_y:.2f}%: "
                + ("supportive" if two_y_score >= 60 else
                   "neutral" if two_y_score >= 40 else "restrictive")
                + " front-end pressure"
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "two_year_yield_level",
            "raw_value": None, "score": None, "weight": 0.25,
            "status": "unavailable",
            "interpretation": "2Y yield unavailable",
        })
        warnings.append("Missing 2-year yield for front-end rate assessment")

    # --- Submetric 2: Ten-year yield level (20%) ---
    # Moderate long-end rates are healthiest for risk assets
    # Very low (deflation fear) or very high (tightening) both negative
    # Sweet spot: 2%-4% for current regime
    if ten_y is not None:
        # Bell curve: best at ~3.2%, penalize extremes
        dist = abs(ten_y - 3.2)
        ten_y_score = _clamp(100 - dist * 25)
        submetrics.append({
            "name": "ten_year_yield_level",
            "raw_value": ten_y,
            "score": round(ten_y_score, 1),
            "weight": 0.20,
            "status": "ok",
            "interpretation": (
                f"10Y at {ten_y:.2f}%: "
                + ("orderly" if ten_y_score >= 60 else
                   "moderate pressure" if ten_y_score >= 35 else
                   "significant rate pressure")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "ten_year_yield_level",
            "raw_value": None, "score": None, "weight": 0.20,
            "status": "unavailable",
            "interpretation": "10Y yield unavailable",
        })
        warnings.append("Missing 10-year yield")

    # --- Submetric 3: Policy pressure proxy (20%) ---
    # Fed funds rate vs an estimated neutral rate (~3.0% in current cycle)
    # Higher fed funds above neutral = more restrictive
    if fed_funds is not None:
        neutral_estimate = 3.0  # rough neutral rate estimate
        policy_gap = fed_funds - neutral_estimate
        # Negative gap (below neutral) → supportive; positive gap → restrictive
        # -2pp below = very supportive (100), +2pp above = very restrictive (0)
        policy_score = _clamp(_interpolate(policy_gap, -2.0, 2.5, 100, 0))
        submetrics.append({
            "name": "policy_pressure_proxy",
            "raw_value": fed_funds,
            "score": round(policy_score, 1),
            "weight": 0.20,
            "status": "ok",
            "interpretation": (
                f"Fed funds {fed_funds:.2f}% vs ~{neutral_estimate:.1f}% neutral: "
                + ("accommodative" if policy_score >= 65 else
                   "neutral" if policy_score >= 40 else "restrictive")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "policy_pressure_proxy",
            "raw_value": None, "score": None, "weight": 0.20,
            "status": "unavailable",
            "interpretation": "Fed funds rate unavailable",
        })
        warnings.append("Missing fed funds rate for policy assessment")

    # --- Submetric 4: Curve context signal (15%) ---
    # Normal curve (positive spread) = healthy; inversion = stress signal
    # Spread context: -0.5 = deeply inverted, 0 = flat, +1.5 = steep
    if curve_spread is not None:
        curve_score = _clamp(_interpolate(curve_spread, -0.8, 2.0, 15, 95))
        submetrics.append({
            "name": "curve_context_signal",
            "raw_value": curve_spread,
            "score": round(curve_score, 1),
            "weight": 0.15,
            "status": "ok",
            "interpretation": (
                f"Curve spread {curve_spread:+.3f}%: "
                + ("inverted — stress signal" if curve_spread < -0.1 else
                   "flat — cautious" if curve_spread < 0.3 else
                   "normal — healthy")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "curve_context_signal",
            "raw_value": None, "score": None, "weight": 0.15,
            "status": "unavailable",
            "interpretation": "Yield curve spread unavailable",
        })

    # --- Submetric 5: Front-end rate pressure (10%) ---
    # Absolute 2Y level as a direct pressure metric
    # 2Y > 4.5% = heavy front-end pressure; < 2.5% = supportive
    if two_y is not None:
        fep_score = _clamp(_interpolate(two_y, 1.5, 5.0, 90, 10))
        submetrics.append({
            "name": "front_end_rate_pressure",
            "raw_value": two_y,
            "score": round(fep_score, 1),
            "weight": 0.10,
            "status": "ok",
            "interpretation": f"Front-end pressure from 2Y at {two_y:.2f}%",
        })
    else:
        submetrics.append({
            "name": "front_end_rate_pressure",
            "raw_value": None, "score": None, "weight": 0.10,
            "status": "unavailable",
            "interpretation": "Derived from 2Y (unavailable)",
        })

    # --- Submetric 6: Rate trend pressure (10%) ---
    # Derived from curve shape: steepening = easing expectations,
    # flattening/inverting = tightening expectations
    if curve_spread is not None and two_y is not None:
        # Combine: positive curve + moderate 2Y = easing trend
        trend_raw = curve_spread * 20 + (4.5 - two_y) * 15
        trend_score = _clamp(50 + trend_raw)
        submetrics.append({
            "name": "rate_trend_pressure",
            "raw_value": round(trend_raw, 2),
            "score": round(trend_score, 1),
            "weight": 0.10,
            "status": "ok",
            "interpretation": (
                "Rate trend is "
                + ("easing" if trend_score >= 60 else
                   "neutral" if trend_score >= 40 else "tightening")
            ),
        })
    else:
        submetrics.append({
            "name": "rate_trend_pressure",
            "raw_value": None, "score": None, "weight": 0.10,
            "status": "unavailable",
            "interpretation": "Rate trend derivation unavailable",
        })

    # Aggregate pillar
    pillar_parts = [(sm["score"], sm["weight"]) for sm in submetrics]
    pillar_score = _weighted_avg(pillar_parts)
    if pillar_score is None:
        pillar_score = 50.0  # neutral fallback
        warnings.append("Rates/policy pillar using neutral fallback — no valid submetrics")

    explanation = _build_pillar_explanation(
        "Rates & Policy Pressure", pillar_score, submetrics,
    )

    return {
        "score": round(_clamp(pillar_score), 2),
        "submetrics": submetrics,
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": missing_count,
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 2 — FINANCIAL CONDITIONS TIGHTNESS (25%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_financial_conditions_tightness(data: dict[str, Any]) -> dict[str, Any]:
    """Score broad financial conditions.

    Higher score = easier conditions = more supportive.
    Lower score = tighter conditions = headwind for risk assets.

    Submetrics (weights):
      fci_proxy                  (30%) — composite FCI proxy (VIX + IG + 2Y)
      vix_conditions_signal      (25%) — VIX as volatility/conditions gauge
      conditions_supportiveness  (25%) — credit + rates combined support (no VIX)
      broad_tightness_score      (20%) — tightness from IG + curve (no VIX)

    NOTE: VIX contributes to fci_proxy (as 1/3 of composite) and
    vix_conditions_signal only. Other submetrics intentionally exclude
    VIX to avoid double-counting across pillars.
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    missing_count = 0
    proxy_count = 0  # track proxy-derived submetrics

    vix = _safe_float(data.get("vix"))
    ig_spread = _safe_float(data.get("ig_spread"))
    hy_spread = _safe_float(data.get("hy_spread"))
    two_y = _safe_float(data.get("two_year_yield"))
    ten_y = _safe_float(data.get("ten_year_yield"))
    curve_spread = _safe_float(data.get("yield_curve_spread"))

    raw_inputs = {
        "vix": vix,
        "ig_spread": ig_spread,
        "hy_spread": hy_spread,
        "two_year_yield": two_y,
        "ten_year_yield": ten_y,
        "yield_curve_spread": curve_spread,
    }

    # --- Submetric 1: FCI proxy (30%) ---
    # Composite proxy from VIX + credit spreads + rates
    # True FCI (e.g., Chicago Fed NFCI) not yet integrated
    fci_inputs = []
    if vix is not None:
        # VIX contribution: 12 = very easy (100), 35 = very tight (0)
        fci_inputs.append(_clamp(_interpolate(vix, 12, 35, 100, 0)))
    if ig_spread is not None:
        # IG OAS: 0.6% = very easy (100), 2.5% = very tight (0)
        fci_inputs.append(_clamp(_interpolate(ig_spread, 0.6, 2.5, 100, 0)))
    if two_y is not None:
        # 2Y: lower = easier conditions
        fci_inputs.append(_clamp(_interpolate(two_y, 1.0, 5.5, 95, 10)))

    if fci_inputs:
        fci_score = round(sum(fci_inputs) / len(fci_inputs), 1)
        proxy_count += 1
        submetrics.append({
            "name": "fci_proxy",
            "raw_value": round(fci_score, 1),
            "score": fci_score,
            "weight": 0.30,
            "status": "proxy",
            "interpretation": (
                f"FCI proxy estimate {fci_score:.0f}/100 "
                f"(composite of {len(fci_inputs)} inputs — "
                "not a true FCI index): "
                + ("suggests easy conditions" if fci_score >= 65 else
                   "suggests roughly neutral" if fci_score >= 40 else
                   "suggests tight conditions")
            ),
        })
        if len(fci_inputs) < 3:
            warnings.append(
                f"FCI proxy degraded: only {len(fci_inputs)}/3 inputs available"
            )
    else:
        missing_count += 1
        submetrics.append({
            "name": "fci_proxy",
            "raw_value": None, "score": None, "weight": 0.30,
            "status": "unavailable",
            "interpretation": "No inputs available for FCI proxy",
        })
        warnings.append("FCI proxy completely unavailable")

    # --- Submetric 2: VIX conditions signal (25%) ---
    # VIX as volatility-implied conditions gauge.  This is the ONE place
    # VIX level is scored as a primary input in this pillar (aside from
    # its 1/3 contribution in the FCI proxy above).
    if vix is not None:
        # VIX 12 = calm / supportive (90); VIX 35 = extreme pressure (10)
        vix_cond_score = _clamp(_interpolate(vix, 12, 35, 90, 10))
        if curve_spread is not None and curve_spread < -0.2:
            vix_cond_score = max(0, vix_cond_score - 8)  # inversion penalty
        submetrics.append({
            "name": "vix_conditions_signal",
            "raw_value": vix,
            "score": round(vix_cond_score, 1),
            "weight": 0.25,
            "status": "ok",
            "interpretation": (
                f"VIX at {vix:.1f} suggests conditions are "
                + ("relatively easy" if vix_cond_score >= 60 else
                   "roughly neutral" if vix_cond_score >= 40 else
                   "under pressure")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "vix_conditions_signal",
            "raw_value": None, "score": None, "weight": 0.25,
            "status": "unavailable",
            "interpretation": "VIX unavailable for conditions assessment",
        })

    # --- Submetric 3: Conditions supportiveness (25%) ---
    # Credit + rates combined supportiveness — intentionally NO VIX here
    # to keep this submetric independent from the VIX channel above.
    support_inputs = []
    if ig_spread is not None:
        support_inputs.append(_clamp(_interpolate(ig_spread, 0.8, 2.0, 90, 20)))
    if hy_spread is not None:
        support_inputs.append(_clamp(_interpolate(hy_spread, 3.0, 8.0, 90, 10)))
    if ten_y is not None:
        support_inputs.append(_clamp(_interpolate(ten_y, 2.0, 5.0, 80, 20)))

    if support_inputs:
        support_score = round(sum(support_inputs) / len(support_inputs), 1)
        submetrics.append({
            "name": "conditions_supportiveness",
            "raw_value": round(support_score, 1),
            "score": support_score,
            "weight": 0.25,
            "status": "ok",
            "interpretation": (
                f"Credit/rate supportiveness {support_score:.0f}/100: "
                + ("supportive" if support_score >= 60 else
                   "neutral" if support_score >= 40 else "restrictive")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "conditions_supportiveness",
            "raw_value": None, "score": None, "weight": 0.25,
            "status": "unavailable",
            "interpretation": "No credit/rate inputs for supportiveness",
        })

    # --- Submetric 4: Broad tightness score (20%) ---
    # Overall tightness from credit + curve — NO VIX here to avoid
    # triple-counting VIX within this pillar.
    tightness_inputs = []
    if ig_spread is not None:
        tightness_inputs.append(_clamp(_interpolate(ig_spread, 0.7, 2.2, 85, 15)))
    if curve_spread is not None:
        tightness_inputs.append(_clamp(_interpolate(curve_spread, -0.5, 1.5, 20, 85)))

    if tightness_inputs:
        broad_score = round(sum(tightness_inputs) / len(tightness_inputs), 1)
        submetrics.append({
            "name": "broad_tightness_score",
            "raw_value": round(broad_score, 1),
            "score": broad_score,
            "weight": 0.20,
            "status": "ok",
            "interpretation": (
                f"Broad tightness {broad_score:.0f}/100 "
                + ("loose" if broad_score >= 65 else
                   "neutral" if broad_score >= 40 else "tight")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "broad_tightness_score",
            "raw_value": None, "score": None, "weight": 0.20,
            "status": "unavailable",
            "interpretation": "No IG spread or curve data for broad tightness",
        })

    # Aggregate pillar
    pillar_parts = [(sm["score"], sm["weight"]) for sm in submetrics]
    pillar_score = _weighted_avg(pillar_parts)
    if pillar_score is None:
        pillar_score = 50.0
        warnings.append("Financial conditions pillar using neutral fallback")

    explanation = _build_pillar_explanation(
        "Financial Conditions Tightness", pillar_score, submetrics,
    )

    return {
        "score": round(_clamp(pillar_score), 2),
        "submetrics": submetrics,
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": missing_count,
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 3 — CREDIT & FUNDING STRESS (20%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_credit_funding_stress(data: dict[str, Any]) -> dict[str, Any]:
    """Score credit and funding stress signals.

    Higher score = less stress = stable/supportive.
    Lower score = elevated stress = liquidity deterioration.

    Submetrics (weights):
      ig_spread             (25%) — investment grade OAS (direct)
      hy_spread             (25%) — high yield OAS (direct)
      credit_stress_signal  (25%) — composite credit stress (credit-led, VIX secondary)
      funding_stress_proxy  (15%) — funding/repo proxy (VIX + fed funds heuristic)
      breakage_risk         (10%) — likelihood of liquidity breakage (credit-only)

    VIX policy: VIX contributes to credit_stress_signal as a secondary
    input (≤1/3 weight) and to funding_stress_proxy. The former
    stress_trend_signal (which was just VIX again) has been merged into
    credit_stress_signal to avoid redundancy.
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    missing_count = 0
    proxy_count = 0  # track proxy-derived submetrics

    ig = _safe_float(data.get("ig_spread"))
    hy = _safe_float(data.get("hy_spread"))
    vix = _safe_float(data.get("vix"))
    fed_funds = _safe_float(data.get("fed_funds_rate"))
    two_y = _safe_float(data.get("two_year_yield"))

    raw_inputs = {
        "ig_spread": ig,
        "hy_spread": hy,
        "vix": vix,
        "fed_funds_rate": fed_funds,
        "two_year_yield": two_y,
    }

    # --- Submetric 1: IG spread (25%) ---
    # IG OAS context: 0.8% = very tight/healthy, 2.0% = elevated, 3.5%+ = stress
    if ig is not None:
        ig_score = _clamp(_interpolate(ig, 0.6, 3.0, 95, 5))
        submetrics.append({
            "name": "ig_spread",
            "raw_value": ig,
            "score": round(ig_score, 1),
            "weight": 0.25,
            "status": "ok",
            "interpretation": (
                f"IG OAS at {ig:.2f}%: "
                + ("tight — stable" if ig_score >= 65 else
                   "moderate" if ig_score >= 35 else "wide — stress")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "ig_spread",
            "raw_value": None, "score": None, "weight": 0.25,
            "status": "unavailable",
            "interpretation": "IG credit spread unavailable",
        })
        warnings.append("Missing IG credit spread — degraded credit assessment")

    # --- Submetric 2: HY spread (25%) ---
    # HY OAS context: 3.0% = very tight, 5.5% = moderate, 8%+ = stress
    if hy is not None:
        hy_score = _clamp(_interpolate(hy, 2.5, 9.0, 95, 5))
        submetrics.append({
            "name": "hy_spread",
            "raw_value": hy,
            "score": round(hy_score, 1),
            "weight": 0.25,
            "status": "ok",
            "interpretation": (
                f"HY OAS at {hy:.2f}%: "
                + ("tight — stable" if hy_score >= 65 else
                   "moderate" if hy_score >= 35 else "wide — stress")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "hy_spread",
            "raw_value": None, "score": None, "weight": 0.25,
            "status": "unavailable",
            "interpretation": "HY credit spread unavailable",
        })
        warnings.append("Missing HY credit spread — degraded credit assessment")

    # --- Submetric 3: Credit stress signal (25%) ---
    # Composite from available credit spreads; VIX is a secondary input
    # capped at 1/3 of the composite to avoid VIX domination.
    credit_parts: list[float] = []
    if ig is not None:
        credit_parts.append(_clamp(_interpolate(ig, 0.7, 2.5, 90, 10)))
    if hy is not None:
        credit_parts.append(_clamp(_interpolate(hy, 3.0, 7.5, 90, 10)))

    if credit_parts:
        # Credit-sourced average
        credit_avg = sum(credit_parts) / len(credit_parts)
        if vix is not None:
            # VIX augments but never dominates: max 30% of final score
            vix_stress = _clamp(_interpolate(vix, 12, 35, 85, 10))
            credit_stress_score = round(credit_avg * 0.70 + vix_stress * 0.30, 1)
        else:
            credit_stress_score = round(credit_avg, 1)
        submetrics.append({
            "name": "credit_stress_signal",
            "raw_value": round(credit_stress_score, 1),
            "score": credit_stress_score,
            "weight": 0.25,
            "status": "ok",
            "interpretation": (
                f"Credit stress composite {credit_stress_score:.0f}/100: "
                + ("stable" if credit_stress_score >= 60 else
                   "moderate stress" if credit_stress_score >= 35 else
                   "elevated stress")
            ),
        })
    elif vix is not None:
        # VIX-only fallback — mark as proxy since no actual credit data
        credit_stress_score = round(
            _clamp(_interpolate(vix, 12, 35, 85, 10)), 1,
        )
        proxy_count += 1
        submetrics.append({
            "name": "credit_stress_signal",
            "raw_value": round(credit_stress_score, 1),
            "score": credit_stress_score,
            "weight": 0.25,
            "status": "proxy",
            "interpretation": (
                f"Credit stress VIX-only proxy {credit_stress_score:.0f}/100 "
                "(no direct credit spread data)"
            ),
        })
        warnings.append("Credit stress using VIX-only proxy — no spreads available")
    else:
        missing_count += 1
        submetrics.append({
            "name": "credit_stress_signal",
            "raw_value": None, "score": None, "weight": 0.25,
            "status": "unavailable",
            "interpretation": "No inputs for credit stress composite",
        })

    # --- Submetric 4: Funding stress proxy (15%) ---
    # Proxy from VIX + rate levels (true SOFR/FRA-OIS not available)
    # Higher VIX + higher rates = more funding stress
    if vix is not None and fed_funds is not None:
        # Funding proxy: calm VIX + low rates = no stress (high score)
        vix_component = _interpolate(vix, 12, 35, 80, 15)
        rate_component = _interpolate(fed_funds, 1.0, 5.5, 80, 20)
        funding_score = _clamp(round(vix_component * 0.6 + rate_component * 0.4, 1))
        proxy_count += 1
        submetrics.append({
            "name": "funding_stress_proxy",
            "raw_value": round(funding_score, 1),
            "score": funding_score,
            "weight": 0.15,
            "status": "proxy",
            "interpretation": (
                f"Funding stress proxy estimate {funding_score:.0f}/100 "
                "(heuristic from VIX + fed funds — not direct repo/SOFR): "
                + ("suggests stable" if funding_score >= 60 else
                   "suggests moderate stress" if funding_score >= 35 else
                   "suggests elevated stress")
            ),
        })
        warnings.append(
            "Funding stress is a PROXY (VIX + rate heuristic), "
            "not a direct measurement (SOFR/repo data not yet integrated)"
        )
    elif vix is not None:
        # VIX-only fallback
        funding_score = _clamp(_interpolate(vix, 12, 35, 75, 15))
        proxy_count += 1
        submetrics.append({
            "name": "funding_stress_proxy",
            "raw_value": round(funding_score, 1),
            "score": round(funding_score, 1),
            "weight": 0.15,
            "status": "proxy",
            "interpretation": (
                f"Funding stress VIX-only proxy {funding_score:.0f}/100 "
                "(no rate data available)"
            ),
        })
        warnings.append("Funding stress using VIX-only proxy — fed funds unavailable")
    else:
        missing_count += 1
        submetrics.append({
            "name": "funding_stress_proxy",
            "raw_value": None, "score": None, "weight": 0.15,
            "status": "unavailable",
            "interpretation": "Funding stress proxy unavailable",
        })

    # --- Submetric 5: Liquidity breakage risk (10%) ---
    # Credit-only breakage assessment — VIX excluded here to avoid
    # triple-counting (already in credit_stress_signal and funding_proxy).
    breakage_inputs = []
    if hy is not None:
        breakage_inputs.append(_interpolate(hy, 3.5, 10.0, 90, 5))
    if ig is not None:
        breakage_inputs.append(_interpolate(ig, 1.0, 3.5, 90, 10))

    if breakage_inputs:
        breakage_score = _clamp(round(sum(breakage_inputs) / len(breakage_inputs), 1))
        submetrics.append({
            "name": "liquidity_breakage_risk",
            "raw_value": round(breakage_score, 1),
            "score": breakage_score,
            "weight": 0.10,
            "status": "ok",
            "interpretation": (
                f"Breakage risk {breakage_score:.0f}/100: "
                + ("low" if breakage_score >= 65 else
                   "moderate" if breakage_score >= 35 else "elevated")
            ),
        })
    else:
        submetrics.append({
            "name": "liquidity_breakage_risk",
            "raw_value": None, "score": None, "weight": 0.10,
            "status": "unavailable",
            "interpretation": "Breakage risk derivation unavailable (no credit data)",
        })

    # Aggregate pillar
    pillar_parts = [(sm["score"], sm["weight"]) for sm in submetrics]
    pillar_score = _weighted_avg(pillar_parts)
    if pillar_score is None:
        pillar_score = 50.0
        warnings.append("Credit/funding pillar using neutral fallback")

    explanation = _build_pillar_explanation(
        "Credit & Funding Stress", pillar_score, submetrics,
    )

    return {
        "score": round(_clamp(pillar_score), 2),
        "submetrics": submetrics,
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": missing_count,
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 4 — DOLLAR / GLOBAL LIQUIDITY PRESSURE (15%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_dollar_global_liquidity(data: dict[str, Any]) -> dict[str, Any]:
    """Score dollar strength and global liquidity pressure.

    Higher score = less dollar pressure = more supportive.
    Lower score = strong dollar = tightening global liquidity.

    Submetrics (weights):
      dxy_level                (40%) — USD index level & trend (single DXY read)
      dollar_liquidity_press   (35%) — dollar + VIX as liquidity pressure
      dollar_risk_asset_imp    (25%) — risk-asset impact from dollar

    NOTE: Previous version had 5 submetrics but 4 were just different
    interpolation transforms of the same DXY value. Consolidated to 3
    meaningfully distinct submetrics.
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    missing_count = 0

    dxy = _safe_float(data.get("dxy_level"))
    vix = _safe_float(data.get("vix"))

    raw_inputs = {
        "dxy_level": dxy,
        "vix": vix,
    }

    # --- Submetric 1: DXY level (40%) ---
    # DXY context: 95 = weak dollar (supportive), 105 = neutral, 115 = very strong (restrictive)
    # This is the primary DXY read — one score for the level, no separate "trend"
    # submetric since we only have a single DXY snapshot (no historical delta).
    if dxy is not None:
        dxy_score = _clamp(_interpolate(dxy, 95, 115, 90, 10))
        submetrics.append({
            "name": "dxy_level",
            "raw_value": dxy,
            "score": round(dxy_score, 1),
            "weight": 0.40,
            "status": "ok",
            "interpretation": (
                f"DXY at {dxy:.1f}: "
                + ("weak dollar — supportive" if dxy_score >= 65 else
                   "moderate dollar" if dxy_score >= 35 else
                   "strong dollar — tightening pressure")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "dxy_level",
            "raw_value": None, "score": None, "weight": 0.40,
            "status": "unavailable",
            "interpretation": "USD index unavailable",
        })
        warnings.append("Missing USD index (DXY) — degraded dollar assessment")

    # --- Submetric 2: Dollar liquidity pressure (35%) ---
    # Strong dollar + high VIX = global liquidity squeeze
    # This is the ONE submetric where VIX enters the Dollar pillar.
    if dxy is not None and vix is not None:
        dxy_liq = _interpolate(dxy, 95, 115, 85, 15)
        vix_liq = _interpolate(vix, 12, 30, 80, 20)
        liq_press_score = _clamp(round(dxy_liq * 0.65 + vix_liq * 0.35, 1))
        submetrics.append({
            "name": "dollar_liquidity_pressure",
            "raw_value": round(liq_press_score, 1),
            "score": liq_press_score,
            "weight": 0.35,
            "status": "ok",
            "interpretation": (
                f"Dollar liquidity pressure {liq_press_score:.0f}/100: "
                + ("low" if liq_press_score >= 60 else
                   "moderate" if liq_press_score >= 35 else "elevated")
            ),
        })
    elif dxy is not None:
        liq_press_score = _clamp(_interpolate(dxy, 95, 115, 85, 15))
        submetrics.append({
            "name": "dollar_liquidity_pressure",
            "raw_value": round(liq_press_score, 1),
            "score": round(liq_press_score, 1),
            "weight": 0.35,
            "status": "degraded",
            "interpretation": (
                f"Dollar pressure DXY-only {liq_press_score:.0f}/100 "
                "(VIX unavailable)"
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "dollar_liquidity_pressure",
            "raw_value": None, "score": None, "weight": 0.35,
            "status": "unavailable",
            "interpretation": "Dollar liquidity pressure unavailable",
        })

    # --- Submetric 3: Dollar risk-asset impact (25%) ---
    # Broader impact: strong dollar pressures risk assets, weak supports
    if dxy is not None:
        impact_score = _clamp(_interpolate(dxy, 96, 110, 80, 20))
        submetrics.append({
            "name": "dollar_risk_asset_impact",
            "raw_value": dxy,
            "score": round(impact_score, 1),
            "weight": 0.25,
            "status": "ok",
            "interpretation": (
                "Dollar impact on risk assets: "
                + ("minimal" if impact_score >= 60 else
                   "moderate drag" if impact_score >= 35 else
                   "significant headwind")
            ),
        })
    else:
        submetrics.append({
            "name": "dollar_risk_asset_impact",
            "raw_value": None, "score": None, "weight": 0.25,
            "status": "unavailable",
            "interpretation": "Dollar impact unavailable",
        })

    # Aggregate pillar
    pillar_parts = [(sm["score"], sm["weight"]) for sm in submetrics]
    pillar_score = _weighted_avg(pillar_parts)
    if pillar_score is None:
        pillar_score = 50.0
        warnings.append("Dollar/global liquidity pillar using neutral fallback")

    explanation = _build_pillar_explanation(
        "Dollar / Global Liquidity Pressure", pillar_score, submetrics,
    )

    return {
        "score": round(_clamp(pillar_score), 2),
        "submetrics": submetrics,
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": missing_count,
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 5 — LIQUIDITY STABILITY & FRAGILITY (15%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_liquidity_stability_fragility(data: dict[str, Any]) -> dict[str, Any]:
    """Score overall liquidity stability and fragility risk.

    Higher score = stable, manageable conditions.
    Lower score = fragile, prone to breakage/sudden stress.

    Submetrics (weights):
      contradiction_between_pllrs  (30%) — cross-pillar disagreement (unique data)
      stability_of_conditions      (25%) — are conditions coherent? (multi-input)
      fragility_penalty            (20%) — compound fragility signals
      sudden_stress_risk           (15%) — potential for quick deterioration
      support_vs_stress_balance    (10%) — net supportive vs stress

    VIX policy: VIX contributes to stability_of_conditions as one of
    three inputs and to fragility_penalty as a threshold trigger. It is
    intentionally NOT the primary driver; cross-pillar scores (which
    are this pillar's unique data source) carry the most weight.
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    missing_count = 0

    vix = _safe_float(data.get("vix"))
    ig = _safe_float(data.get("ig_spread"))
    hy = _safe_float(data.get("hy_spread"))
    two_y = _safe_float(data.get("two_year_yield"))
    dxy = _safe_float(data.get("dxy_level"))
    curve = _safe_float(data.get("yield_curve_spread"))
    # Pillar scores from other pillars (passed in for cross-check)
    pillar_scores_ext = data.get("_pillar_scores", {})

    raw_inputs = {
        "vix": vix,
        "ig_spread": ig,
        "hy_spread": hy,
        "two_year_yield": two_y,
        "dxy_level": dxy,
        "yield_curve_spread": curve,
    }

    # --- Submetric 1: Contradiction between pillars (30%) ---
    # This pillar's UNIQUE value: cross-pillar coherence analysis
    # Large range across pillar scores = fractured picture
    ext_scores = [v for v in pillar_scores_ext.values() if v is not None]
    if len(ext_scores) >= 2:
        pillar_range = max(ext_scores) - min(ext_scores)
        # Range < 15 = coherent (90), range > 45 = fractured (15)
        contra_score = _clamp(_interpolate(pillar_range, 10, 50, 90, 10))
        submetrics.append({
            "name": "contradiction_between_pillars",
            "raw_value": round(pillar_range, 1),
            "score": round(contra_score, 1),
            "weight": 0.30,
            "status": "ok",
            "interpretation": (
                f"Cross-pillar range {pillar_range:.0f}pp: "
                + ("coherent" if contra_score >= 65 else
                   "some tension" if contra_score >= 40 else
                   "fractured — conflicting signals")
            ),
        })
        if pillar_range > 35:
            warnings.append(
                f"High cross-pillar disagreement ({pillar_range:.0f}pp range) "
                "— liquidity picture is fractured"
            )
    else:
        missing_count += 1
        submetrics.append({
            "name": "contradiction_between_pillars",
            "raw_value": None, "score": None, "weight": 0.30,
            "status": "unavailable",
            "interpretation": "Insufficient pillar data for contradiction check",
        })
        warnings.append("Fewer than 2 pillar scores — cross-pillar check skipped")

    # --- Submetric 2: Stability of conditions (25%) ---
    # Multi-input stability: moderate VIX + tight credit + stable rates
    # VIX is ONE of three inputs here, not dominant.
    stability_inputs = []
    if vix is not None:
        # VIX 12-18 = stable; 20+ = less stable; 30+ = unstable
        stability_inputs.append(_interpolate(vix, 12, 35, 90, 10))
    if ig is not None:
        stability_inputs.append(_interpolate(ig, 0.7, 2.5, 85, 15))
    if two_y is not None:
        # Moderate rates more stable than extremes
        dist = abs(two_y - 3.0)
        stability_inputs.append(_clamp(85 - dist * 20))

    if stability_inputs:
        stab_score = _clamp(round(sum(stability_inputs) / len(stability_inputs), 1))
        submetrics.append({
            "name": "stability_of_conditions",
            "raw_value": round(stab_score, 1),
            "score": stab_score,
            "weight": 0.25,
            "status": "ok",
            "interpretation": (
                f"Conditions stability {stab_score:.0f}/100: "
                + ("stable" if stab_score >= 60 else
                   "moderate" if stab_score >= 35 else "fragile")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "stability_of_conditions",
            "raw_value": None, "score": None, "weight": 0.25,
            "status": "unavailable",
            "interpretation": "Stability assessment unavailable",
        })
        warnings.append("No inputs for stability assessment")

    # --- Submetric 3: Fragility penalty (20%) ---
    # Compound: low VIX (complacency) + tight credit + high rates = fragile
    # VIX is a threshold trigger here, not a continuous score driver.
    if vix is not None:
        fragility = 70.0  # start with moderate stability
        if vix < 14 and ig is not None and ig < 1.0:
            # Very calm + very tight credit = complacent = fragile
            fragility -= 20
            warnings.append("Complacency risk: very low VIX + tight credit spreads")
        if vix > 25:
            fragility -= 15  # already stressed = fragile
        if hy is not None and hy > 5.5:
            fragility -= 15  # wide HY = stress
        if curve is not None and curve < -0.2:
            fragility -= 10  # inverted curve = fragile
        fragility = _clamp(fragility)
        submetrics.append({
            "name": "fragility_penalty",
            "raw_value": round(fragility, 1),
            "score": round(fragility, 1),
            "weight": 0.20,
            "status": "ok",
            "interpretation": (
                f"Fragility assessment {fragility:.0f}/100: "
                + ("resilient" if fragility >= 60 else
                   "moderate fragility" if fragility >= 35 else
                   "elevated fragility")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "fragility_penalty",
            "raw_value": None, "score": None, "weight": 0.20,
            "status": "unavailable",
            "interpretation": "Fragility assessment unavailable",
        })

    # --- Submetric 4: Sudden stress risk (15%) ---
    # Could conditions crack quickly? Uses credit + DXY, NO VIX
    # (VIX stress already captured in stability_of_conditions above).
    stress_risk_inputs = []
    if hy is not None:
        stress_risk_inputs.append(_interpolate(hy, 3.5, 8.0, 80, 10))
    if ig is not None:
        stress_risk_inputs.append(_interpolate(ig, 0.8, 2.5, 80, 15))
    if dxy is not None:
        stress_risk_inputs.append(_interpolate(dxy, 97, 112, 75, 20))

    if stress_risk_inputs:
        stress_risk_score = _clamp(round(
            sum(stress_risk_inputs) / len(stress_risk_inputs), 1,
        ))
        submetrics.append({
            "name": "sudden_stress_risk",
            "raw_value": round(stress_risk_score, 1),
            "score": stress_risk_score,
            "weight": 0.15,
            "status": "ok",
            "interpretation": (
                f"Sudden stress risk {stress_risk_score:.0f}/100: "
                + ("low" if stress_risk_score >= 60 else
                   "moderate" if stress_risk_score >= 35 else "elevated")
            ),
        })
    else:
        missing_count += 1
        submetrics.append({
            "name": "sudden_stress_risk",
            "raw_value": None, "score": None, "weight": 0.15,
            "status": "unavailable",
            "interpretation": "Sudden stress risk unavailable",
        })

    # --- Submetric 5: Support vs stress balance (10%) ---
    # Net balance of supportive vs restrictive signals (NO VIX —
    # uses credit, DXY, and curve only for an independent view).
    support_count = 0
    stress_count = 0
    if ig is not None:
        if ig < 1.2:
            support_count += 1
        elif ig > 1.8:
            stress_count += 1
    if hy is not None:
        if hy < 4.0:
            support_count += 1
        elif hy > 5.5:
            stress_count += 1
    if dxy is not None:
        if dxy < 100:
            support_count += 1
        elif dxy > 105:
            stress_count += 1
    if curve is not None:
        if curve > 0.5:
            support_count += 1
        elif curve < -0.1:
            stress_count += 1

    total_signals = support_count + stress_count
    if total_signals > 0:
        balance_ratio = support_count / total_signals
        balance_score = _clamp(round(balance_ratio * 100, 1))
        submetrics.append({
            "name": "support_vs_stress_balance",
            "raw_value": round(balance_ratio, 2),
            "score": balance_score,
            "weight": 0.10,
            "status": "ok",
            "interpretation": (
                f"Support/stress balance {support_count}:{stress_count}: "
                + ("net supportive" if balance_score >= 60 else
                   "balanced" if balance_score >= 40 else "net stressed")
            ),
        })
    else:
        submetrics.append({
            "name": "support_vs_stress_balance",
            "raw_value": None, "score": None, "weight": 0.10,
            "status": "unavailable",
            "interpretation": "Balance assessment unavailable",
        })

    # Aggregate pillar
    pillar_parts = [(sm["score"], sm["weight"]) for sm in submetrics]
    pillar_score = _weighted_avg(pillar_parts)
    if pillar_score is None:
        pillar_score = 50.0
        warnings.append("Stability/fragility pillar using neutral fallback")

    explanation = _build_pillar_explanation(
        "Liquidity Stability & Fragility", pillar_score, submetrics,
    )

    return {
        "score": round(_clamp(pillar_score), 2),
        "submetrics": submetrics,
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": missing_count,
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR EXPLANATION HELPER
# ═══════════════════════════════════════════════════════════════════════

def _build_pillar_explanation(
    pillar_name: str,
    score: float,
    submetrics: list[dict[str, Any]],
) -> str:
    """Build a human-readable explanation from a pillar's submetrics."""
    parts = [f"{pillar_name} scores {score:.0f}/100."]
    for sm in submetrics:
        if sm.get("score") is not None and sm.get("interpretation"):
            parts.append(sm["interpretation"] + ".")
    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# CONFIDENCE SCORING
# ═══════════════════════════════════════════════════════════════════════

def _compute_confidence(
    pillars: dict[str, dict[str, Any]],
    source_meta: dict[str, Any] | None = None,
) -> tuple[float, list[str]]:
    """Compute confidence score from data completeness and coherence.

    Checks:
      - Missing entire pillars
      - Missing submetrics within pillars
      - Cross-pillar disagreement
      - Per-pillar proxy concentration (submetrics with status="proxy")
      - Source-level proxy/stale/missing counts
      - VIX concentration risk (VIX used across too many pillars)

    Returns (confidence 0-100, penalty strings).
    """
    penalties: list[str] = []
    confidence = 100.0

    # Penalty: missing entire pillars
    valid_pillars = [p for p in pillars.values() if p.get("score") is not None]
    missing_pillar_count = len(pillars) - len(valid_pillars)
    if missing_pillar_count > 0:
        penalty = missing_pillar_count * 15
        confidence -= penalty
        penalties.append(f"Missing {missing_pillar_count} pillar(s) (-{penalty})")

    # Penalty: degraded submetrics
    total_missing_subs = sum(p.get("missing_count", 0) for p in pillars.values())
    if total_missing_subs > 0:
        sub_penalty = min(total_missing_subs * 3, 30)
        confidence -= sub_penalty
        penalties.append(f"{total_missing_subs} missing submetric(s) (-{sub_penalty})")

    # Penalty: per-pillar proxy concentration
    # If >50% of a pillar's weighted score comes from proxy submetrics,
    # penalise confidence for that pillar.
    proxy_heavy_pillars: list[str] = []
    for pname, pdata in pillars.items():
        subs = pdata.get("submetrics", [])
        total_weight = sum(s.get("weight", 0) for s in subs if s.get("score") is not None)
        proxy_weight = sum(
            s.get("weight", 0) for s in subs
            if s.get("status") == "proxy" and s.get("score") is not None
        )
        if total_weight > 0 and proxy_weight / total_weight > 0.50:
            proxy_heavy_pillars.append(pname)
    if proxy_heavy_pillars:
        ppillar_pen = len(proxy_heavy_pillars) * 4
        confidence -= ppillar_pen
        names = ", ".join(p.replace("_", " ").title() for p in proxy_heavy_pillars)
        penalties.append(
            f"{len(proxy_heavy_pillars)} pillar(s) >50% proxy-derived "
            f"[{names}] (-{ppillar_pen})"
        )

    # Penalty: cross-pillar disagreement
    valid_scores = [p["score"] for p in valid_pillars]
    if len(valid_scores) >= 2:
        score_range = max(valid_scores) - min(valid_scores)
        if score_range > 35:
            disagree_penalty = min((score_range - 35) * 0.5, 15)
            confidence -= disagree_penalty
            penalties.append(
                f"Cross-pillar range {score_range:.0f} (-{disagree_penalty:.1f})"
            )

    # Proxy penalties: Per-pillar proxy concentration (above) + source-level
    # proxy checks (below) already cover the proxy metrics in this engine
    # (financial_conditions_proxy, funding_stress_proxy).
    # SIGNAL_PROVENANCE-based penalties not needed — existing coverage is sufficient.
    if source_meta:
        proxy_count = source_meta.get("proxy_source_count", 0)
        if proxy_count >= 4:
            confidence -= 8
            penalties.append(f"Heavy proxy reliance ({proxy_count} proxy sources) (-8)")
        elif proxy_count >= 2:
            confidence -= 4
            penalties.append(f"Moderate proxy reliance ({proxy_count} proxy sources) (-4)")

        stale_count = source_meta.get("stale_source_count", 0)
        if stale_count > 0:
            stale_pen = min(stale_count * 3, 12)
            confidence -= stale_pen
            penalties.append(f"{stale_count} stale source(s) (-{stale_pen})")

        if not source_meta.get("has_credit_spreads", False):
            confidence -= 5
            penalties.append("No credit spread data — proxy only (-5)")

        if not source_meta.get("has_funding_data", False):
            confidence -= 5
            penalties.append("No direct funding stress data (-5)")

    # Penalty for aggregate data staleness (from data_quality tags)
    dq_summary = (source_meta or {}).get("data_quality", {}).get("_summary", {})
    dq_max_age = dq_summary.get("max_age_days")
    if dq_max_age is not None and dq_max_age > 3:
        age_penalty = min(15, round((dq_max_age - 3) * 2, 1))
        confidence -= age_penalty
        penalties.append(f"data_staleness: max_age={dq_max_age}d (-{age_penalty})")

    return _clamp(round(confidence, 1), 0, 100), penalties


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

    _readable_names = {
        "rates_policy_pressure": "Rates & Policy Pressure",
        "financial_conditions_tightness": "Financial Conditions",
        "credit_funding_stress": "Credit & Funding Stress",
        "dollar_global_liquidity": "Dollar / Global Liquidity",
        "liquidity_stability_fragility": "Stability / Fragility",
    }

    for pname, pdata in pillars.items():
        score = pdata.get("score")
        if score is None:
            continue
        readable = _readable_names.get(pname, pname.replace("_", " ").title())
        if score >= 65:
            positive.append(f"{readable} is supportive ({score:.0f}/100)")
        elif score >= 45:
            conflicting.append(f"{readable} is mixed ({score:.0f}/100)")
        else:
            negative.append(f"{readable} signals stress ({score:.0f}/100)")

    summary_parts = [
        f"Liquidity & conditions composite is {label.lower()} ({composite:.0f}/100)."
    ]
    if positive:
        summary_parts.append(
            f"Supportive: {positive[0].split(' is ')[0].lower()}."
        )
    if negative:
        summary_parts.append(
            f"Pressure from: {negative[0].split(' signals')[0].lower()}."
        )

    # Support vs stress metrics
    rates = pillars.get("rates_policy_pressure", {}).get("score")
    conditions = pillars.get("financial_conditions_tightness", {}).get("score")
    credit = pillars.get("credit_funding_stress", {}).get("score")
    dollar = pillars.get("dollar_global_liquidity", {}).get("score")
    stability = pillars.get("liquidity_stability_fragility", {}).get("score")

    support_vs_stress = {
        "supportive_for_risk_assets": round(
            _weighted_avg([(rates, 0.3), (conditions, 0.4), (credit, 0.3)]) or 0, 1
        ),
        "tightening_pressure": round(
            100 - (_weighted_avg([(rates, 0.4), (conditions, 0.3), (dollar, 0.3)]) or 50), 1
        ),
        "stress_risk": round(
            100 - (_weighted_avg([(credit, 0.5), (stability, 0.5)]) or 50), 1
        ),
        "fragility": round(100 - (stability or 50), 1),
    }

    # Trader takeaway
    if composite >= 70:
        takeaway = (
            "Liquidity and financial conditions are supportive for risk assets. "
            "Rates, credit, and funding conditions are favorable. "
            "Full-confidence setups supported — normal position sizing."
        )
    elif composite >= 55:
        takeaway = (
            "Conditions are mixed but manageable. Some areas are supportive "
            "while others show early signs of tightening or pressure. "
            "Favor defined-risk strategies and monitor conditions closely."
        )
    elif composite >= 45:
        takeaway = (
            "Conditions are tightening. Rate pressure, credit widening, or "
            "dollar strength may be creating headwinds. Reduce position sizes "
            "and tighten risk management."
        )
    elif composite >= 30:
        takeaway = (
            "Restrictive conditions are present. Multiple pillars show stress "
            "or tightening pressure. Consider defensive positioning — smaller "
            "sizes, hedged structures, and caution on new risk."
        )
    else:
        takeaway = (
            "Liquidity stress is evident. Conditions are hostile for risk assets. "
            "Prioritize capital preservation — reduce exposure, hedge existing "
            "positions, and avoid new premium-selling strategies."
        )

    if confidence < 60:
        takeaway += (
            " (Note: confidence is low due to missing data and/or "
            "proxy-heavy inputs — interpret cautiously.)"
        )

    return {
        "summary": " ".join(summary_parts),
        "positive_contributors": positive,
        "negative_contributors": negative,
        "conflicting_signals": conflicting,
        "support_vs_stress": support_vs_stress,
        "trader_takeaway": takeaway,
    }


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENGINE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def compute_liquidity_conditions_scores(
    rates_data: dict[str, Any],
    conditions_data: dict[str, Any],
    credit_data: dict[str, Any],
    dollar_data: dict[str, Any],
    stability_data: dict[str, Any],
    source_meta: dict[str, Any],
) -> dict[str, Any]:
    """Compute the Liquidity & Financial Conditions engine result.

    Parameters
    ----------
    rates_data      : dict — inputs for Pillar 1 (Rates & Policy Pressure)
    conditions_data : dict — inputs for Pillar 2 (Financial Conditions Tightness)
    credit_data     : dict — inputs for Pillar 3 (Credit & Funding Stress)
    dollar_data     : dict — inputs for Pillar 4 (Dollar / Global Liquidity)
    stability_data  : dict — inputs for Pillar 5 (Liquidity Stability & Fragility)
    source_meta     : dict — data provenance and freshness metadata

    Returns
    -------
    dict — full engine result with score, label, pillars, diagnostics, etc.
    """
    as_of = datetime.now(timezone.utc).isoformat()

    # ── Compute each pillar (hardened with per-pillar try/except) ─
    pillars: dict[str, dict[str, Any]] = {}
    pillar_funcs = {
        "rates_policy_pressure": (rates_data, _compute_rates_policy_pressure),
        "financial_conditions_tightness": (conditions_data, _compute_financial_conditions_tightness),
        "credit_funding_stress": (credit_data, _compute_credit_funding_stress),
        "dollar_global_liquidity": (dollar_data, _compute_dollar_global_liquidity),
    }

    for pname, (pdata, pfunc) in pillar_funcs.items():
        try:
            pillars[pname] = pfunc(pdata)
        except Exception as exc:
            logger.error(
                "event=liquidity_pillar_error pillar=%s error=%s",
                pname, exc, exc_info=True,
            )
            pillars[pname] = {
                "score": None,
                "submetrics": [],
                "explanation": f"Pillar computation failed: {exc}",
                "warnings": [f"Pillar error: {exc}"],
                "raw_inputs": {},
                "missing_count": 0,
            }

    # Pillar 5 needs cross-pillar scores for contradiction check
    pillar_5_data = dict(stability_data)
    pillar_5_data["_pillar_scores"] = {
        pname: pdata.get("score") for pname, pdata in pillars.items()
    }
    try:
        pillars["liquidity_stability_fragility"] = _compute_liquidity_stability_fragility(
            pillar_5_data,
        )
    except Exception as exc:
        logger.error(
            "event=liquidity_pillar_error pillar=liquidity_stability_fragility error=%s",
            exc, exc_info=True,
        )
        pillars["liquidity_stability_fragility"] = {
            "score": None,
            "submetrics": [],
            "explanation": f"Pillar computation failed: {exc}",
            "warnings": [f"Pillar error: {exc}"],
            "raw_inputs": {},
            "missing_count": 0,
        }

    # ── Composite score ──────────────────────────────────────────
    weighted_parts: list[tuple[float | None, float]] = []
    for pname, weight in PILLAR_WEIGHTS.items():
        pdata = pillars.get(pname, {})
        weighted_parts.append((pdata.get("score"), weight))

    composite = _weighted_avg(weighted_parts)
    if composite is None:
        composite = 50.0
        data_status = "no_data"
        logger.warning("event=liquidity_composite_failed reason=no_valid_pillars")
    else:
        data_status = "ok"

    if data_status == "no_data":
        full_label, short_label = "Neutral / No Data", "Neutral"
    else:
        full_label, short_label = _label_from_score(composite)
    confidence, confidence_penalties = _compute_confidence(pillars, source_meta)
    sig_quality = _signal_quality(confidence)
    explanation = _build_composite_explanation(composite, full_label, pillars, confidence)

    # ── Diagnostic trace ─────────────────────────────────────────
    active = [pn for pn, pd in pillars.items() if pd.get("score") is not None]
    inactive = [pn for pn, pd in pillars.items() if pd.get("score") is None]
    missing_subs = sum(p.get("missing_count", 0) for p in pillars.values())
    logger.info(
        "event=liquidity_composite "
        "composite=%.2f label=%s confidence=%.1f signal_quality=%s "
        "active_pillars=%d/%d inactive=%s missing_submetrics=%d penalties=%d",
        composite, short_label, confidence, sig_quality,
        len(active), len(pillars), inactive or "none",
        missing_subs, len(confidence_penalties),
    )

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

    pillar_scores = {
        pname: round(pdata["score"], 2) if pdata.get("score") is not None else None
        for pname, pdata in pillars.items()
    }
    pillar_explanations = {
        pname: pdata.get("explanation", "")
        for pname, pdata in pillars.items()
    }

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
        "source_meta": source_meta,
        "signal_provenance": SIGNAL_PROVENANCE,
        "proxy_summary": {
            "total_proxy_signals": sum(
                1 for v in SIGNAL_PROVENANCE.values()
                if v.get("type") == "proxy"
            ),
            "total_direct_signals": sum(
                1 for v in SIGNAL_PROVENANCE.values()
                if v.get("type") == "direct"
            ),
            "total_derived_signals": sum(
                1 for v in SIGNAL_PROVENANCE.values()
                if v.get("type") == "derived"
            ),
            "proxy_signal_names": [
                k for k, v in SIGNAL_PROVENANCE.items()
                if v.get("type") == "proxy"
            ],
            "direct_signal_names": [
                k for k, v in SIGNAL_PROVENANCE.items()
                if v.get("type") == "direct"
            ],
        },
    }

    raw_inputs = {
        "rates": pillars["rates_policy_pressure"].get("raw_inputs", {}),
        "conditions": pillars["financial_conditions_tightness"].get("raw_inputs", {}),
        "credit": pillars["credit_funding_stress"].get("raw_inputs", {}),
        "dollar": pillars["dollar_global_liquidity"].get("raw_inputs", {}),
        "stability": pillars["liquidity_stability_fragility"].get("raw_inputs", {}),
    }

    result = {
        "engine": "liquidity_financial_conditions",
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
        "support_vs_stress": explanation["support_vs_stress"],
        "positive_contributors": explanation["positive_contributors"],
        "negative_contributors": explanation["negative_contributors"],
        "conflicting_signals": explanation["conflicting_signals"],
        "trader_takeaway": explanation["trader_takeaway"],
        "warnings": all_warnings,
        "missing_inputs": all_missing,
        "diagnostics": diagnostics,
        "raw_inputs": raw_inputs,
        "data_status": data_status,
    }

    logger.info(
        "event=liquidity_conditions_engine_computed score=%.2f label=%s confidence=%.1f "
        "pillars=%s warnings=%d missing=%d",
        composite, full_label, confidence,
        {k: v for k, v in pillar_scores.items()},
        len(all_warnings), len(all_missing),
    )

    return result
