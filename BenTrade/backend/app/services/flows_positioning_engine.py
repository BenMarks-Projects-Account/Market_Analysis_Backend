"""Flows & Positioning Scoring Engine.

Institutional-grade engine answering: "Are positioning and flows supporting
continuation, crowding, squeezes, or reversal risk right now?"

Architecture — 5 scored pillars:
  1. Positioning Pressure           (25%)  — directional pressure from positioning
  2. Crowding / Stretch             (20%)  — how crowded or stretched positioning is
  3. Squeeze / Unwind Risk          (20%)  — fragility and asymmetry of positions
  4. Flow Direction & Persistence   (20%)  — flow momentum and stickiness
  5. Positioning Stability          (15%)  — overall coherence and fragility assessment

Design honesty:
  Phase 1 uses PROXY data (ETF flows, put/call ratios, VIX-derived positioning
  signals, short-interest estimates) rather than true institutional feeds.
  The engine labels proxies explicitly, reduces confidence accordingly, and
  never claims proxy precision equals direct measurement.

Composite formula:
  FlowsPositioningComposite = Σ(pillar_score × weight) / Σ(active_weights)

Label mapping (composite → regime):
  85–100  →  Strongly Supportive Flows
  70–84   →  Supportive Positioning
  55–69   →  Mixed but Tradable
  45–54   →  Fragile / Crowded
  30–44   →  Reversal Risk Elevated
  0–29    →  Unstable / Unwind Risk

All submetrics are built via _build_submetric() for uniform schema.
Each pillar function returns {score, submetrics, explanation, warnings,
raw_inputs, missing_count}.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION — weights, thresholds, scoring bands
# ═══════════════════════════════════════════════════════════════════════

PILLAR_WEIGHTS: dict[str, float] = {
    "positioning_pressure": 0.25,
    "crowding_stretch": 0.20,
    "squeeze_unwind_risk": 0.20,
    "flow_direction_persistence": 0.20,
    "positioning_stability": 0.15,
}

_LABEL_BANDS: list[tuple[float, float, str, str]] = [
    (85, 100, "Strongly Supportive Flows", "Strongly Supportive"),
    (70, 84.99, "Supportive Positioning", "Supportive"),
    (55, 69.99, "Mixed but Tradable", "Mixed"),
    (45, 54.99, "Fragile / Crowded", "Fragile"),
    (30, 44.99, "Reversal Risk Elevated", "Reversal Risk"),
    (0, 29.99, "Unstable / Unwind Risk", "Unstable"),
]

_CONFIDENCE_HIGH = 80
_CONFIDENCE_MEDIUM = 60


# ═══════════════════════════════════════════════════════════════════════
# SIGNAL PROVENANCE — documents source, delay, and proxy status
# ═══════════════════════════════════════════════════════════════════════

SIGNAL_PROVENANCE: dict[str, dict[str, str]] = {
    "put_call_ratio": {
        "source": "MarketContextService / derived",
        "type": "proxy",
        "delay": "EOD (options activity summarized daily)",
        "unit": "ratio",
        "notes": "Equity put/call ratio. Higher = more protective buying = cautious.",
    },
    "vix_level": {
        "source": "MarketContextService → Tradier/Finnhub/FRED waterfall",
        "type": "direct",
        "delay": "near-realtime when market open; EOD otherwise",
        "unit": "index level",
        "notes": "CBOE Volatility Index — used as positioning posture proxy.",
    },
    "vix_term_structure": {
        "source": "derived from VIX vs VIX3M (when available)",
        "type": "proxy",
        "delay": "EOD",
        "unit": "ratio (VIX/VIX3M)",
        "notes": "Contango (<1.0) = normal; backwardation (>1.0) = stress/hedging demand.",
    },
    "etf_flow_proxy": {
        "source": "volume/price behavior estimation",
        "type": "proxy",
        "delay": "EOD aggregation",
        "unit": "normalized flow score",
        "notes": "Phase 1 proxy — estimated from volume anomalies and price "
                 "action. Not true fund-flow data. Confidence reduced.",
    },
    "short_interest_proxy": {
        "source": "proxy estimation / exchange-reported SI when available",
        "type": "proxy",
        "delay": "bi-monthly (exchange) or estimated daily",
        "unit": "normalized level",
        "notes": "True short interest is reported bi-monthly with ~10 day lag. "
                 "Phase 1 uses proxy estimation.",
    },
    "futures_positioning_proxy": {
        "source": "CFTC COT or proxy estimation",
        "type": "proxy",
        "delay": "weekly (CFTC COT published Friday for Tuesday data)",
        "unit": "normalized net long % or percentile",
        "notes": "Institutional futures positioning. Phase 1 may use proxy. "
                 "True CFTC data has ~3 day lag.",
    },
    "retail_sentiment": {
        "source": "AAII Sentiment Survey or proxy",
        "type": "proxy",
        "delay": "weekly (AAII published Thursday)",
        "unit": "bull-bear spread or ratio",
        "notes": "Weekly survey of individual investor sentiment. Contrarian when extreme.",
    },
    "systematic_flow_proxy": {
        "source": "derived from volatility regime and trend signals",
        "type": "proxy",
        "delay": "EOD",
        "unit": "normalized allocation estimate 0-100",
        "notes": "Proxy for CTA/vol-control/risk-parity allocation. "
                 "Not direct fund data. Based on vol+trend heuristics.",
    },
    "dealer_gamma_proxy": {
        "source": "derived heuristic (vol level + skew estimation)",
        "type": "proxy",
        "delay": "EOD",
        "unit": "normalized score",
        "notes": "Phase 1 heuristic proxy — not actual dealer gamma exposure. "
                 "Uses VIX level, term structure, and options activity to "
                 "estimate directional gamma impact. Treat with caution.",
    },
    "crowding_composite": {
        "source": "derived from multiple proxy inputs",
        "type": "derived",
        "delay": "inherits slowest proxy input",
        "unit": "normalized 0-100 score",
        "notes": "Composite crowding score derived from put/call ratio, "
                 "positioning proxies, and sentiment extremeness.",
    },
    "flow_persistence": {
        "source": "derived from multi-day flow direction consistency",
        "type": "derived",
        "delay": "EOD",
        "unit": "normalized 0-100",
        "notes": "Measures how consistent flow direction has been over 5d and 20d windows.",
    },
}


# ═══════════════════════════════════════════════════════════════════════
# SCORING UTILITIES
# ═══════════════════════════════════════════════════════════════════════

def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _interpolate(value: float, in_lo: float, in_hi: float,
                 out_lo: float = 0.0, out_hi: float = 100.0) -> float:
    """Linearly interpolate value from [in_lo, in_hi] → [out_lo, out_hi], clamped."""
    if in_hi == in_lo:
        return (out_lo + out_hi) / 2
    ratio = (value - in_lo) / (in_hi - in_lo)
    return _clamp(out_lo + ratio * (out_hi - out_lo), min(out_lo, out_hi), max(out_lo, out_hi))


def _weighted_avg(parts: list[tuple[float | None, float]]) -> float | None:
    """Weighted average ignoring None values. Returns None if no valid parts."""
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
    """Build a standardized submetric result object."""
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
    """Score a pillar from its submetrics using specified weights."""
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


def _label_from_score(score: float) -> tuple[str, str]:
    """Map composite score to (full_label, short_label)."""
    for lo, hi, full, short in _LABEL_BANDS:
        if lo <= score <= hi:
            return full, short
    return "Unknown", "Unknown"


# Gate thresholds — prevents "Supportive" labels when underlying
# crowding or stability is dangerously weak despite high composite.
_CROWDING_GATE_THRESHOLD = 40  # Pillar 2 below this blocks "Supportive" labels
_STABILITY_GATE_THRESHOLD = 35  # Pillar 5 below this blocks "Supportive" labels
_SQUEEZE_RISK_GATE_THRESHOLD = 35  # Pillar 3 below this blocks "Supportive" labels


def _label_from_score_with_gates(
    score: float,
    pillars: dict[str, dict[str, Any]],
) -> tuple[str, str, float, bool, list[str], list[str]]:
    """Map composite score to label, applying safety gates.

    If crowding, stability, or squeeze pillars are dangerously low,
    the label is capped at 'Mixed but Tradable' and the score is
    reduced proportionally. Missing gate data applies a conservative
    penalty instead of bypassing.

    Returns (full_label, short_label, adjusted_score, gate_applied,
             gate_warnings, gate_details).
    """
    gate_warnings: list[str] = []
    gate_details: list[str] = []
    composite = score

    # Only apply gates to the top two label bands
    if composite < 55:
        base_label, base_short = _label_from_score(composite)
        return base_label, base_short, composite, False, gate_warnings, gate_details

    crowding_score = pillars.get("crowding_stretch", {}).get("score")
    stability_score = pillars.get("positioning_stability", {}).get("score")
    squeeze_score = pillars.get("squeeze_unwind_risk", {}).get("score")

    gate_applied = False

    # Gate 1: Crowding
    if composite >= 55:
        if crowding_score is not None and crowding_score < _CROWDING_GATE_THRESHOLD:
            gate_penalty = min(15, (_CROWDING_GATE_THRESHOLD - crowding_score) * 0.5)
            composite = max(45, composite - gate_penalty)
            gate_warnings.append(
                f"Label capped: crowding/stretch pillar ({crowding_score:.0f}) "
                f"below {_CROWDING_GATE_THRESHOLD} gate — positioning is too crowded "
                f"for a 'Supportive' label."
            )
            gate_details.append(
                f"crowding={crowding_score:.1f} < {_CROWDING_GATE_THRESHOLD}"
                f" → penalty={gate_penalty:.1f}"
            )
            gate_applied = True
        elif crowding_score is None:
            composite = max(50, composite - 5)
            gate_details.append("crowding=None → conservative penalty=5")
            gate_applied = True

    # Gate 2: Stability
    if composite >= 55:
        if stability_score is not None and stability_score < _STABILITY_GATE_THRESHOLD:
            gate_penalty = min(15, (_STABILITY_GATE_THRESHOLD - stability_score) * 0.5)
            composite = max(45, composite - gate_penalty)
            gate_warnings.append(
                f"Label capped: stability pillar ({stability_score:.0f}) "
                f"below {_STABILITY_GATE_THRESHOLD} gate — positioning is too fragile "
                f"for a 'Supportive' label."
            )
            gate_details.append(
                f"stability={stability_score:.1f} < {_STABILITY_GATE_THRESHOLD}"
                f" → penalty={gate_penalty:.1f}"
            )
            gate_applied = True
        elif stability_score is None:
            composite = max(50, composite - 5)
            gate_details.append("stability=None → conservative penalty=5")
            gate_applied = True

    # Gate 3: Squeeze risk
    if composite >= 55:
        if squeeze_score is not None and squeeze_score < _SQUEEZE_RISK_GATE_THRESHOLD:
            gate_penalty = min(15, (_SQUEEZE_RISK_GATE_THRESHOLD - squeeze_score) * 0.5)
            composite = max(45, composite - gate_penalty)
            gate_warnings.append(
                f"Label capped: squeeze/unwind pillar ({squeeze_score:.0f}) "
                f"below {_SQUEEZE_RISK_GATE_THRESHOLD} gate — squeeze risk too elevated "
                f"for a 'Supportive' label."
            )
            gate_details.append(
                f"squeeze={squeeze_score:.1f} < {_SQUEEZE_RISK_GATE_THRESHOLD}"
                f" → penalty={gate_penalty:.1f}"
            )
            gate_applied = True
        elif squeeze_score is None:
            composite = max(50, composite - 5)
            gate_details.append("squeeze=None → conservative penalty=5")
            gate_applied = True

    if gate_applied and composite < score:
        capped_label = "Mixed but Tradable (Gated)"
        capped_short = "Mixed (Gated)"
        logger.info(
            "event=flows_positioning_label_gated original_score=%.2f "
            "gated_score=%.2f original_label=%s capped_label=%s "
            "crowding=%.1f stability=%.1f squeeze=%.1f",
            score, composite,
            _label_from_score(score)[0], capped_label,
            crowding_score if crowding_score is not None else -1,
            stability_score if stability_score is not None else -1,
            squeeze_score if squeeze_score is not None else -1,
        )
        return capped_label, capped_short, composite, gate_applied, gate_warnings, gate_details

    base_label, base_short = _label_from_score(composite)
    return base_label, base_short, composite, gate_applied, gate_warnings, gate_details


def _signal_quality(confidence: float) -> str:
    if confidence >= _CONFIDENCE_HIGH:
        return "high"
    if confidence >= _CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 1 — POSITIONING PRESSURE (25%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_positioning_pressure(data: dict[str, Any]) -> dict[str, Any]:
    """Score whether current positioning exerts supportive, neutral, or
    reversal pressure.

    Submetrics:
      positioning_bias         — net directional tilt from put/call + sentiment
      directional_exposure     — how one-sided exposure appears (proxy)
      options_posture          — hedging vs speculative activity
      systematic_pressure      — CTA/vol-control allocation proxy

    Weights:
      positioning_bias       30%
      directional_exposure   25%
      options_posture        25%
      systematic_pressure    20%

    Interpretation:
      Supportive-but-not-extreme positioning scores best (60-80 range).
      Extreme one-sided positioning (very high exposure) actually REDUCES
      this pillar's score because it implies fragility.
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    put_call = _safe_float(data.get("put_call_ratio"))
    vix = _safe_float(data.get("vix"))
    retail_bull = _safe_float(data.get("retail_bull_pct"))
    systematic_alloc = _safe_float(data.get("systematic_allocation"))
    futures_net_pct = _safe_float(data.get("futures_net_long_pct"))

    raw_inputs = {
        "put_call_ratio": put_call,
        "vix": vix,
        "retail_bull_pct": retail_bull,
        "systematic_allocation": systematic_alloc,
        "futures_net_long_pct": futures_net_pct,
    }

    # ── positioning_bias ─────────────────────────────────────────
    # Put/call ratio: 0.6 = very bullish options activity, 1.2 = heavy hedging
    # Moderate bullish positioning (0.7-0.9) is healthiest.
    # Formula: bell-curve — best around 0.8, worse at extremes
    if put_call is not None:
        if put_call <= 0.8:
            # Very bullish → moderate (maybe TOO bullish if <0.6)
            # 0.5→55, 0.6→65, 0.7→78, 0.8→85
            bias_score = _interpolate(put_call, 0.5, 0.8, 55, 85)
        else:
            # Rising put/call → more hedging → less supportive
            # 0.8→85, 1.0→60, 1.2→35, 1.5→20
            bias_score = _interpolate(put_call, 0.8, 1.5, 85, 20)
        submetrics.append(_build_submetric(
            "positioning_bias", put_call, bias_score,
            details={"interpretation": "put/call ratio — lower = more bullish positioning"},
        ))
    else:
        total_missing += 1
        warnings.append("positioning_bias: missing put/call ratio (proxy)")
        submetrics.append(_build_submetric("positioning_bias", None, None))

    # ── directional_exposure ─────────────────────────────────────
    # Futures net long % — moderate net long (40-60%) is supportive.
    # Extreme net long (>80%) = crowded → reduces this score.
    # Extreme net short (<20%) = cautious → lower score but squeeze potential.
    # Formula: bell-curve centered on 55%
    if futures_net_pct is not None:
        if futures_net_pct <= 55:
            # 0→25, 20→45, 40→70, 55→82
            dir_score = _interpolate(futures_net_pct, 0, 55, 25, 82)
        else:
            # 55→82, 70→65, 80→48, 90→30, 100→18
            dir_score = _interpolate(futures_net_pct, 55, 100, 82, 18)
        submetrics.append(_build_submetric(
            "directional_exposure", futures_net_pct, dir_score,
            details={"interpretation": "futures net long percentile — moderate is best"},
        ))
    else:
        total_missing += 1
        warnings.append("directional_exposure: missing futures positioning proxy")
        submetrics.append(_build_submetric("directional_exposure", None, None))

    # ── options_posture ──────────────────────────────────────────
    # VIX as options posture proxy: low VIX → confident positioning,
    # moderate VIX → healthy caution, high VIX → defensive positioning.
    # Sweet spot: 14-20 → balanced.
    if vix is not None:
        if vix <= 17:
            # 8→60, 12→75, 14→82, 17→88
            opt_score = _interpolate(vix, 8, 17, 60, 88)
        elif vix <= 22:
            # 17→88, 20→72, 22→62
            opt_score = _interpolate(vix, 17, 22, 88, 62)
        else:
            # 22→62, 30→35, 40→15
            opt_score = _interpolate(vix, 22, 40, 62, 15)
        submetrics.append(_build_submetric(
            "options_posture", vix, opt_score,
            details={"interpretation": "VIX level as options posture proxy"},
        ))
    else:
        total_missing += 1
        warnings.append("options_posture: missing VIX data")
        submetrics.append(_build_submetric("options_posture", None, None))

    # ── systematic_pressure ──────────────────────────────────────
    # Systematic allocation proxy (0-100): 0 = fully de-risked, 100 = max long.
    # Moderate allocation (40-70) is supportive. Extremes are fragile.
    if systematic_alloc is not None:
        if systematic_alloc <= 60:
            # 0→20, 30→50, 50→72, 60→80
            sys_score = _interpolate(systematic_alloc, 0, 60, 20, 80)
        else:
            # 60→80, 75→65, 85→50, 95→30, 100→22
            sys_score = _interpolate(systematic_alloc, 60, 100, 80, 22)
        submetrics.append(_build_submetric(
            "systematic_pressure", systematic_alloc, sys_score,
            fallback_used=True,
            details={"interpretation": "systematic allocation proxy — moderate is healthiest"},
        ))
    else:
        total_missing += 1
        warnings.append("systematic_pressure: missing systematic flow proxy")
        submetrics.append(_build_submetric("systematic_pressure", None, None))

    sub_weights = {
        "positioning_bias": 0.30,
        "directional_exposure": 0.25,
        "options_posture": 0.25,
        "systematic_pressure": 0.20,
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
# PILLAR 2 — CROWDING / STRETCH (20%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_crowding_stretch(data: dict[str, Any]) -> dict[str, Any]:
    """Score whether flows/positioning appear crowded, stretched, or balanced.

    Submetrics:
      crowding_proxy           — aggregate crowding estimate
      stretch_vs_range         — how far positioning extends vs recent norm
      flow_concentration       — whether flows are piling into narrow names/sectors
      speculative_excess       — retail/speculative overextension
      one_sided_risk           — risk from extreme one-sided positioning

    Weights:
      crowding_proxy       30%
      stretch_vs_range     20%
      flow_concentration   15%
      speculative_excess   20%
      one_sided_risk       15%

    Interpretation:
      Higher scores = LESS crowded = healthier.
      Lower scores = MORE crowded = elevated reversal risk.
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    futures_net_pct = _safe_float(data.get("futures_net_long_pct"))
    put_call = _safe_float(data.get("put_call_ratio"))
    retail_bull = _safe_float(data.get("retail_bull_pct"))
    retail_bear = _safe_float(data.get("retail_bear_pct"))
    vix = _safe_float(data.get("vix"))
    short_interest = _safe_float(data.get("short_interest_pct"))

    raw_inputs = {
        "futures_net_long_pct": futures_net_pct,
        "put_call_ratio": put_call,
        "retail_bull_pct": retail_bull,
        "retail_bear_pct": retail_bear,
        "vix": vix,
        "short_interest_pct": short_interest,
    }

    # ── crowding_proxy ───────────────────────────────────────────
    # Composite: higher futures net long % = more crowded.
    # Score INVERSELY — crowded = low score.
    # Formula: score = interpolate(futures_pct, 90, 30, 15, 88) — inverse
    if futures_net_pct is not None:
        crowd_score = _interpolate(futures_net_pct, 90, 30, 15, 88)
        submetrics.append(_build_submetric(
            "crowding_proxy", futures_net_pct, crowd_score,
            fallback_used=True,
            details={"interpretation": "lower futures net long = less crowded = higher score"},
        ))
    else:
        total_missing += 1
        warnings.append("crowding_proxy: missing futures positioning proxy")
        submetrics.append(_build_submetric("crowding_proxy", None, None))

    # ── stretch_vs_range ─────────────────────────────────────────
    # Put/call ratio as stretch indicator: very low p/c = stretched bullish.
    # Normal range 0.7-1.0. Below 0.65 = stretched.
    # Higher p/c = more hedged = less stretched.
    # Score: lower stretch = higher score.
    if put_call is not None:
        # Very low p/c → very stretched → low score
        # 0.5→20, 0.65→40, 0.8→70, 0.95→82, 1.1→78, 1.3→65
        if put_call <= 0.95:
            stretch_score = _interpolate(put_call, 0.5, 0.95, 20, 82)
        else:
            # Excessive hedging — less stretched but also cautious
            stretch_score = _interpolate(put_call, 0.95, 1.5, 82, 55)
        submetrics.append(_build_submetric(
            "stretch_vs_range", put_call, stretch_score,
            details={"interpretation": "put/call as stretch proxy — moderately hedged is healthiest"},
        ))
    else:
        total_missing += 1
        warnings.append("stretch_vs_range: missing put/call data")
        submetrics.append(_build_submetric("stretch_vs_range", None, None))

    # ── flow_concentration ───────────────────────────────────────
    # VIX as flow-concentration proxy: very low VIX suggests complacency/
    # concentration in momentum. Moderate VIX = more distributed positioning.
    if vix is not None:
        if vix <= 14:
            # Complacency → flows likely concentrated → lower score
            # 8→40, 12→55, 14→65
            conc_score = _interpolate(vix, 8, 14, 40, 65)
        elif vix <= 22:
            # Moderate → healthy dispersion → higher score
            # 14→65, 18→80, 22→75
            conc_score = _interpolate(vix, 14, 22, 65, 75)
        else:
            # High VIX → stress-concentration → moderate score
            # 22→75, 30→55, 40→35
            conc_score = _interpolate(vix, 22, 40, 75, 35)
        submetrics.append(_build_submetric(
            "flow_concentration", vix, conc_score,
            fallback_used=True,
            details={"interpretation": "VIX as proxy for flow concentration/complacency"},
        ))
    else:
        total_missing += 1
        warnings.append("flow_concentration: missing VIX data")
        submetrics.append(_build_submetric("flow_concentration", None, None))

    # ── speculative_excess ───────────────────────────────────────
    # Retail bullishness: elevated bull % = speculative excess.
    # Typical AAII range: bull 25-55%. Above 50% = elevated.
    # Score inversely.
    if retail_bull is not None:
        # 25→88, 35→78, 45→60, 55→40, 65→22
        spec_score = _interpolate(retail_bull, 25, 65, 88, 22)
        submetrics.append(_build_submetric(
            "speculative_excess", retail_bull, spec_score,
            fallback_used=True,
            details={"interpretation": "retail bull % — elevated = speculative excess = lower score"},
        ))
    else:
        total_missing += 1
        warnings.append("speculative_excess: missing retail sentiment data")
        submetrics.append(_build_submetric("speculative_excess", None, None))

    # ── one_sided_risk ───────────────────────────────────────────
    # Derived from bull/bear spread. Large spread = one-sided.
    # Bull-bear spread >30pp = one-sided bullish = risky.
    if retail_bull is not None and retail_bear is not None:
        bb_spread = retail_bull - retail_bear
        # -10→90, 0→80, 10→68, 20→52, 30→35, 40→20
        osr_score = _interpolate(bb_spread, -10, 40, 90, 20)
        submetrics.append(_build_submetric(
            "one_sided_risk", bb_spread, osr_score,
            details={
                "bull_pct": retail_bull, "bear_pct": retail_bear,
                "interpretation": "bull-bear spread — large positive = one-sided",
            },
        ))
    else:
        total_missing += 1
        warnings.append("one_sided_risk: missing bull/bear spread data")
        submetrics.append(_build_submetric("one_sided_risk", None, None))

    sub_weights = {
        "crowding_proxy": 0.30,
        "stretch_vs_range": 0.20,
        "flow_concentration": 0.15,
        "speculative_excess": 0.20,
        "one_sided_risk": 0.15,
    }
    pillar_score, explanation = _aggregate_submetrics(submetrics, sub_weights)

    # Surface the aggregate crowding level for frontend display.
    # Higher score = less crowded; invert for "crowding level" semantics.
    raw_inputs["crowding_level"] = round(100 - pillar_score, 1) if pillar_score is not None else None

    return {
        "score": pillar_score,
        "submetrics": submetrics,
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": total_missing,
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 3 — SQUEEZE / UNWIND RISK (20%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_squeeze_unwind_risk(data: dict[str, Any]) -> dict[str, Any]:
    """Score whether current positioning creates high squeeze, unwind, or neutral risk.

    Submetrics:
      short_squeeze_risk      — potential for short-covering rally
      long_unwind_risk        — potential for crowded-long selloff
      positioning_fragility   — overall fragility of positioning
      asymmetry               — imbalance between long/short positioning

    Weights:
      short_squeeze_risk    25%
      long_unwind_risk      30%
      positioning_fragility 25%
      asymmetry             20%

    Interpretation:
      Higher score = LESS squeeze/unwind risk = more stable positioning.
      Lower score = elevated risk of forced flows (squeeze or unwind).
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    short_interest = _safe_float(data.get("short_interest_pct"))
    futures_net_pct = _safe_float(data.get("futures_net_long_pct"))
    put_call = _safe_float(data.get("put_call_ratio"))
    vix = _safe_float(data.get("vix"))
    vix_term_ratio = _safe_float(data.get("vix_term_structure"))

    raw_inputs = {
        "short_interest_pct": short_interest,
        "futures_net_long_pct": futures_net_pct,
        "put_call_ratio": put_call,
        "vix": vix,
        "vix_term_structure": vix_term_ratio,
    }

    # ── short_squeeze_risk ───────────────────────────────────────
    # Higher short interest → more squeeze fuel → risks forced buying.
    # SI% range: 1-6% for indices, >4% is elevated.
    # High SI is both RISK (squeeze) and OPPORTUNITY (fuel for rally).
    # For this pillar (stability-focused), high SI = lower stability score.
    if short_interest is not None:
        # 1.0→88, 2.0→75, 3.0→60, 4.0→42, 5.0→28, 6.0→18
        sq_score = _interpolate(short_interest, 1.0, 6.0, 88, 18)
        submetrics.append(_build_submetric(
            "short_squeeze_risk", short_interest, sq_score,
            details={"interpretation": "higher SI = more squeeze risk = lower stability score"},
        ))
    else:
        total_missing += 1
        warnings.append("short_squeeze_risk: missing short interest data (proxy)")
        submetrics.append(_build_submetric("short_squeeze_risk", None, None))

    # ── long_unwind_risk ─────────────────────────────────────────
    # Crowded longs → risk of forced selling if catalyst triggers exit.
    # Score inversely: higher futures net long = more unwind risk = lower score.
    if futures_net_pct is not None:
        # 20→90, 40→78, 60→62, 75→42, 85→28, 95→15
        lu_score = _interpolate(futures_net_pct, 20, 95, 90, 15)
        submetrics.append(_build_submetric(
            "long_unwind_risk", futures_net_pct, lu_score,
            details={"interpretation": "extreme net long = elevated unwind risk"},
        ))
    else:
        total_missing += 1
        warnings.append("long_unwind_risk: missing futures positioning proxy")
        submetrics.append(_build_submetric("long_unwind_risk", None, None))

    # ── positioning_fragility ────────────────────────────────────
    # Composite of VIX term structure + put/call: backwardation + low p/c = fragile.
    # VIX term ratio > 1.0 (backwardation) = stress/positioning instability.
    frag_components: list[float] = []
    frag_details: dict[str, Any] = {}

    if vix_term_ratio is not None:
        # Contango (<0.9) → stable → higher score contribution
        # Flat (0.9-1.0) → neutral
        # Backwardation (>1.0) → fragile → lower score
        # 0.75→88, 0.9→72, 1.0→55, 1.1→38, 1.2→22
        vts = _interpolate(vix_term_ratio, 0.75, 1.2, 88, 22)
        frag_components.append(vts)
        frag_details["vix_term_score"] = round(vts, 1)

    if put_call is not None:
        # Very low p/c = complacent = fragile to shock
        # 0.5→25, 0.7→55, 0.85→75, 1.0→80, 1.2→65
        if put_call <= 1.0:
            pc_frag = _interpolate(put_call, 0.5, 1.0, 25, 80)
        else:
            pc_frag = _interpolate(put_call, 1.0, 1.5, 80, 55)
        frag_components.append(pc_frag)
        frag_details["put_call_frag_score"] = round(pc_frag, 1)

    if frag_components:
        frag_score = sum(frag_components) / len(frag_components)
        submetrics.append(_build_submetric(
            "positioning_fragility", frag_score, frag_score,
            fallback_used=True,
            details=frag_details,
        ))
    else:
        total_missing += 1
        warnings.append("positioning_fragility: missing VIX term structure and put/call data")
        submetrics.append(_build_submetric("positioning_fragility", None, None))

    # ── asymmetry ────────────────────────────────────────────────
    # How imbalanced is long vs short positioning?
    # Futures net long 50% = balanced. Distance from 50 = asymmetric.
    if futures_net_pct is not None:
        distance_from_balance = abs(futures_net_pct - 50)
        # 0→92 (perfectly balanced), 15→72, 30→48, 45→25
        asym_score = _interpolate(distance_from_balance, 0, 45, 92, 25)
        submetrics.append(_build_submetric(
            "asymmetry", distance_from_balance, asym_score,
            details={
                "futures_net_pct": futures_net_pct,
                "interpretation": "distance from 50% balance — closer to balanced = higher score",
            },
        ))
    else:
        total_missing += 1
        warnings.append("asymmetry: missing futures positioning data")
        submetrics.append(_build_submetric("asymmetry", None, None))

    sub_weights = {
        "short_squeeze_risk": 0.25,
        "long_unwind_risk": 0.30,
        "positioning_fragility": 0.25,
        "asymmetry": 0.20,
    }
    pillar_score, explanation = _aggregate_submetrics(submetrics, sub_weights)

    # Surface positioning asymmetry for frontend display.
    # Derived from distance_from_balance (abs(futures_net_pct - 50)).
    if futures_net_pct is not None:
        raw_inputs["positioning_asymmetry"] = round(abs(futures_net_pct - 50), 1)
    else:
        raw_inputs["positioning_asymmetry"] = None

    return {
        "score": pillar_score,
        "submetrics": submetrics,
        "explanation": explanation,
        "warnings": warnings,
        "raw_inputs": raw_inputs,
        "missing_count": total_missing,
    }


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 4 — FLOW DIRECTION & PERSISTENCE (20%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_flow_direction_persistence(data: dict[str, Any]) -> dict[str, Any]:
    """Score whether recent flow direction is supportive, fading, or unstable.

    Submetrics:
      recent_flow_direction   — current net flow direction
      flow_persistence_5d     — 5-day flow consistency
      flow_persistence_20d    — 20-day flow consistency
      inflow_outflow_balance  — aggregate inflow vs outflow
      follow_through          — whether flow direction has price follow-through

    Weights:
      recent_flow_direction    25%
      flow_persistence_5d      25%
      flow_persistence_20d     20%
      inflow_outflow_balance   15%
      follow_through           15%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    flow_direction = _safe_float(data.get("flow_direction_score"))
    persistence_5d = _safe_float(data.get("flow_persistence_5d"))
    persistence_20d = _safe_float(data.get("flow_persistence_20d"))
    inflow_balance = _safe_float(data.get("inflow_outflow_balance"))
    follow_through = _safe_float(data.get("follow_through_score"))

    raw_inputs = {
        "flow_direction_score": flow_direction,
        "flow_persistence_5d": persistence_5d,
        "flow_persistence_20d": persistence_20d,
        "inflow_outflow_balance": inflow_balance,
        "follow_through_score": follow_through,
    }

    # ── recent_flow_direction ────────────────────────────────────
    # Pre-computed 0-100 score where 50 = neutral, >50 = net inflow, <50 = outflow.
    if flow_direction is not None:
        fd_score = _clamp(flow_direction)
        submetrics.append(_build_submetric(
            "recent_flow_direction", flow_direction, fd_score,
            fallback_used=True,
            details={"interpretation": "50 = neutral, >50 = inflows, <50 = outflows"},
        ))
    else:
        total_missing += 1
        warnings.append("recent_flow_direction: missing flow direction signal")
        submetrics.append(_build_submetric("recent_flow_direction", None, None))

    # ── flow_persistence_5d ──────────────────────────────────────
    # Pre-computed 0-100 persistence score. High = consistent direction.
    if persistence_5d is not None:
        fp5_score = _clamp(persistence_5d)
        submetrics.append(_build_submetric(
            "flow_persistence_5d", persistence_5d, fp5_score,
            fallback_used=True,
            details={"interpretation": "5-day consistency — higher = more persistent flow direction"},
        ))
    else:
        total_missing += 1
        warnings.append("flow_persistence_5d: missing 5-day flow persistence")
        submetrics.append(_build_submetric("flow_persistence_5d", None, None))

    # ── flow_persistence_20d ─────────────────────────────────────
    if persistence_20d is not None:
        fp20_score = _clamp(persistence_20d)
        submetrics.append(_build_submetric(
            "flow_persistence_20d", persistence_20d, fp20_score,
            fallback_used=True,
            details={"interpretation": "20-day consistency — higher = more persistent trend"},
        ))
    else:
        total_missing += 1
        warnings.append("flow_persistence_20d: missing 20-day flow persistence")
        submetrics.append(_build_submetric("flow_persistence_20d", None, None))

    # ── inflow_outflow_balance ───────────────────────────────────
    # Pre-computed 0-100: 50 = balanced, >50 = net inflow, <50 = net outflow.
    if inflow_balance is not None:
        iob_score = _clamp(inflow_balance)
        submetrics.append(_build_submetric(
            "inflow_outflow_balance", inflow_balance, iob_score,
            fallback_used=True,
            details={"interpretation": "50 = balanced, >50 = net inflow dominated"},
        ))
    else:
        total_missing += 1
        warnings.append("inflow_outflow_balance: missing inflow/outflow balance")
        submetrics.append(_build_submetric("inflow_outflow_balance", None, None))

    # ── follow_through ───────────────────────────────────────────
    # Does flow direction translate to price movement? Pre-computed 0-100.
    if follow_through is not None:
        ft_score = _clamp(follow_through)
        submetrics.append(_build_submetric(
            "follow_through", follow_through, ft_score,
            fallback_used=True,
            details={"interpretation": "price follow-through on flows — higher = flows are working"},
        ))
    else:
        total_missing += 1
        warnings.append("follow_through: missing follow-through signal")
        submetrics.append(_build_submetric("follow_through", None, None))

    sub_weights = {
        "recent_flow_direction": 0.25,
        "flow_persistence_5d": 0.25,
        "flow_persistence_20d": 0.20,
        "inflow_outflow_balance": 0.15,
        "follow_through": 0.15,
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
# PILLAR 5 — POSITIONING STABILITY / FRAGILITY (15%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_positioning_stability(data: dict[str, Any]) -> dict[str, Any]:
    """Assess whether the positioning/flow setup is stable or fragile.

    Submetrics:
      stability_signal         — composite stability assessment
      flow_volatility          — how erratic flow direction has been
      flow_position_contradiction — conflict between flows and positioning
      fragility_penalty        — penalty for fragile combinations
      crowded_fragile_state    — compound crowding + fragility assessment

    Weights:
      stability_signal              25%
      flow_volatility               20%
      flow_position_contradiction   20%
      fragility_penalty             15%
      crowded_fragile_state         20%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    vix = _safe_float(data.get("vix"))
    vix_term_ratio = _safe_float(data.get("vix_term_structure"))
    futures_net_pct = _safe_float(data.get("futures_net_long_pct"))
    flow_direction = _safe_float(data.get("flow_direction_score"))
    flow_vol = _safe_float(data.get("flow_volatility"))
    put_call = _safe_float(data.get("put_call_ratio"))

    raw_inputs = {
        "vix": vix,
        "vix_term_structure": vix_term_ratio,
        "futures_net_long_pct": futures_net_pct,
        "flow_direction_score": flow_direction,
        "flow_volatility": flow_vol,
        "put_call_ratio": put_call,
    }

    # ── stability_signal ─────────────────────────────────────────
    # Composite: VIX term structure in contango + moderate VIX = stable.
    stability_parts: list[float] = []
    stab_details: dict[str, Any] = {}

    if vix is not None:
        # Moderate VIX (14-20) = stable regime
        if vix <= 17:
            v_stab = _interpolate(vix, 8, 17, 55, 85)
        elif vix <= 22:
            v_stab = _interpolate(vix, 17, 22, 85, 65)
        else:
            v_stab = _interpolate(vix, 22, 40, 65, 20)
        stability_parts.append(v_stab)
        stab_details["vix_stability"] = round(v_stab, 1)

    if vix_term_ratio is not None:
        # Contango (<0.92) = stable; backwardation (>1.0) = fragile
        vt_stab = _interpolate(vix_term_ratio, 1.15, 0.80, 20, 90)
        stability_parts.append(vt_stab)
        stab_details["term_structure_stability"] = round(vt_stab, 1)

    if stability_parts:
        stab_score = sum(stability_parts) / len(stability_parts)
        submetrics.append(_build_submetric(
            "stability_signal", stab_score, stab_score,
            fallback_used=True, details=stab_details,
        ))
    else:
        total_missing += 1
        warnings.append("stability_signal: missing VIX / term structure data")
        submetrics.append(_build_submetric("stability_signal", None, None))

    # ── flow_volatility ──────────────────────────────────────────
    # How erratic have flow-direction signals been? Pre-computed 0-100.
    # Higher flow_vol = more erratic = lower stability score.
    if flow_vol is not None:
        # Low flow vol → stable → high score
        # 10→90, 30→72, 50→52, 70→32, 90→15
        fv_score = _interpolate(flow_vol, 10, 90, 90, 15)
        submetrics.append(_build_submetric(
            "flow_volatility", flow_vol, fv_score,
            fallback_used=True,
            details={"interpretation": "lower flow volatility = more stable = higher score"},
        ))
    else:
        total_missing += 1
        warnings.append("flow_volatility: missing flow volatility signal")
        submetrics.append(_build_submetric("flow_volatility", None, None))

    # ── flow_position_contradiction ──────────────────────────────
    # Do flows and positioning agree? If positioning is long but flows
    # are outflowing → contradiction → fragile.
    if futures_net_pct is not None and flow_direction is not None:
        # Both above 50 → agreement → higher score
        # Both below 50 → bearish agreement → higher score
        # One above, one below → contradiction → lower score
        pos_bull = futures_net_pct > 50
        flow_bull = flow_direction > 50
        if pos_bull == flow_bull:
            # Agreement — scale by strength of agreement
            agreement_strength = 100 - abs(futures_net_pct - flow_direction)
            contra_score = _interpolate(agreement_strength, 50, 100, 60, 90)
        else:
            # Contradiction — wider gap = worse
            gap = abs(futures_net_pct - flow_direction)
            contra_score = _interpolate(gap, 10, 50, 55, 15)
        submetrics.append(_build_submetric(
            "flow_position_contradiction", contra_score, contra_score,
            details={
                "positioning_direction": "bullish" if pos_bull else "bearish",
                "flow_direction": "inflow" if flow_bull else "outflow",
                "interpretation": "higher = more agreement between flows and positioning",
            },
        ))
    else:
        total_missing += 1
        warnings.append("flow_position_contradiction: missing positioning or flow data")
        submetrics.append(_build_submetric("flow_position_contradiction", None, None))

    # ── fragility_penalty ────────────────────────────────────────
    # Combines low VIX (complacency) + high positioning (crowded) = fragile.
    if vix is not None and futures_net_pct is not None:
        # If VIX is very low AND positioning is very long → fragile
        complacency = _interpolate(vix, 22, 10, 0, 100)   # lower VIX → more complacent
        crowding = _interpolate(futures_net_pct, 40, 90, 0, 100)  # higher → more crowded
        fragility_raw = (complacency + crowding) / 2
        # Invert: high fragility_raw = high fragility = low stability score
        frag_score = _interpolate(fragility_raw, 0, 100, 90, 15)
        submetrics.append(_build_submetric(
            "fragility_penalty", fragility_raw, frag_score,
            details={
                "complacency_pct": round(complacency, 1),
                "crowding_pct": round(crowding, 1),
                "interpretation": "higher raw value = more fragile = lower score",
            },
        ))
    else:
        total_missing += 1
        warnings.append("fragility_penalty: missing VIX or positioning data")
        submetrics.append(_build_submetric("fragility_penalty", None, None))

    # ── crowded_fragile_state ────────────────────────────────────
    # Similar to fragility_penalty but includes put/call.
    # Low p/c + high positioning + low VIX = maximally fragile.
    cfs_parts: list[float] = []
    cfs_details: dict[str, Any] = {}

    if put_call is not None:
        # Low p/c → complacent → fragile
        pc_cfs = _interpolate(put_call, 0.5, 1.1, 20, 85)
        cfs_parts.append(pc_cfs)
        cfs_details["put_call_stability"] = round(pc_cfs, 1)

    if futures_net_pct is not None:
        # Moderate positioning → stable
        if futures_net_pct <= 55:
            fp_cfs = _interpolate(futures_net_pct, 10, 55, 50, 85)
        else:
            fp_cfs = _interpolate(futures_net_pct, 55, 95, 85, 20)
        cfs_parts.append(fp_cfs)
        cfs_details["positioning_stability"] = round(fp_cfs, 1)

    if cfs_parts:
        cfs_score = sum(cfs_parts) / len(cfs_parts)
        submetrics.append(_build_submetric(
            "crowded_fragile_state", cfs_score, cfs_score,
            fallback_used=True, details=cfs_details,
        ))
    else:
        total_missing += 1
        warnings.append("crowded_fragile_state: missing put/call or positioning data")
        submetrics.append(_build_submetric("crowded_fragile_state", None, None))

    sub_weights = {
        "stability_signal": 0.25,
        "flow_volatility": 0.20,
        "flow_position_contradiction": 0.20,
        "fragility_penalty": 0.15,
        "crowded_fragile_state": 0.20,
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
# CONFIDENCE SCORING
# ═══════════════════════════════════════════════════════════════════════

def _compute_confidence(
    pillars: dict[str, dict[str, Any]],
    source_meta: dict[str, Any] | None = None,
) -> tuple[float, list[str]]:
    """Compute confidence score from data completeness, cross-pillar agreement,
    and source freshness.

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

    # Penalty: heavy proxy reliance (Phase 1 always applies)
    if source_meta:
        proxy_count = source_meta.get("proxy_source_count", 0)
        if proxy_count >= 4:
            confidence -= 8
            penalties.append(f"Heavy proxy reliance ({proxy_count} proxy sources) (-8)")
        elif proxy_count >= 2:
            confidence -= 4
            penalties.append(f"Moderate proxy reliance ({proxy_count} proxy sources) (-4)")

        # Penalty: stale data
        stale_count = source_meta.get("stale_source_count", 0)
        if stale_count > 0:
            stale_pen = min(stale_count * 3, 12)
            confidence -= stale_pen
            penalties.append(f"{stale_count} stale source(s) (-{stale_pen})")

        # Penalty: no direct institutional flow data
        if not source_meta.get("has_direct_flow_data", False):
            confidence -= 5
            penalties.append("No direct institutional flow data — proxy only (-5)")

        # Penalty: no futures positioning data
        if not source_meta.get("has_futures_positioning", False):
            confidence -= 5
            penalties.append("No direct futures positioning data (-5)")

        # Penalty: single-source dependency — all signals derived from one input
        unique_sources = source_meta.get("unique_upstream_count", 0)
        if unique_sources <= 1 and proxy_count >= 6:
            confidence -= 12
            penalties.append(
                f"Single-source dependency: {proxy_count} signals from 1 upstream (-12)"
            )

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
        "positioning_pressure": "Positioning Pressure",
        "crowding_stretch": "Crowding / Stretch",
        "squeeze_unwind_risk": "Squeeze / Unwind Risk",
        "flow_direction_persistence": "Flow Direction & Persistence",
        "positioning_stability": "Positioning Stability",
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
            negative.append(f"{readable} signals risk ({score:.0f}/100)")

    summary_parts = [
        f"Flows & positioning composite is {label.lower()} ({composite:.0f}/100)."
    ]
    if positive:
        summary_parts.append(
            f"Supportive: {positive[0].split(' is ')[0].lower()}."
        )
    if negative:
        summary_parts.append(
            f"Risk area: {negative[0].split(' signals')[0].lower()}."
        )

    # Strategy bias — derived from pillar scores
    pos_pressure = pillars.get("positioning_pressure", {}).get("score")
    crowding = pillars.get("crowding_stretch", {}).get("score")
    squeeze = pillars.get("squeeze_unwind_risk", {}).get("score")
    stability = pillars.get("positioning_stability", {}).get("score")

    strategy_bias = {
        # Higher positioning_pressure + higher stability → continuation support
        "continuation_support": round(
            _weighted_avg([(pos_pressure, 0.6), (stability, 0.4)]) or 0, 1
        ),
        # Lower crowding score → higher reversal risk
        "reversal_risk": round(100 - (crowding or 50), 1),
        # Lower squeeze score → higher squeeze potential
        "squeeze_potential": round(100 - (squeeze or 50), 1),
        # Lower stability → higher fragility
        "fragility": round(100 - (stability or 50), 1),
    }

    # ── Squeeze-vs-continuation distinction ────────────────────
    # When squeeze pillar is low but flow pillar is high, the
    # apparent "support" may actually be squeeze-driven momentum
    # rather than healthy continuation.
    squeeze_driven = False
    flow_score = pillars.get("flow_direction_persistence", {}).get("score")
    if (squeeze is not None and squeeze < 45
            and flow_score is not None and flow_score >= 60):
        squeeze_driven = True
        conflicting.append(
            "Flow strength may be squeeze-driven rather than organic "
            f"(squeeze risk {squeeze:.0f}/100, flow direction {flow_score:.0f}/100)"
        )

    # Trader takeaway
    if composite >= 70 and not squeeze_driven:
        takeaway = (
            "Flows and positioning are supportive of continuation. "
            "Positioning is not overcrowded and flow direction is persistent. "
            "Full-confidence setups are supported — normal position sizing."
        )
    elif composite >= 70 and squeeze_driven:
        takeaway = (
            "Flow direction appears strong but may be squeeze-driven rather "
            "than organic continuation support. Elevated squeeze risk means "
            "price action could reverse abruptly if short-covering exhausts. "
            "Reduce position sizes and use tighter stops despite seemingly "
            "supportive flows."
        )
    elif composite >= 55:
        takeaway = (
            "Flows are mixed but tradable. Some positioning metrics are supportive "
            "while others show early signs of crowding or stretch. "
            "Favor defined-risk strategies and monitor flow persistence."
        )
    elif composite >= 45:
        takeaway = (
            "Positioning appears fragile or crowded. Flow direction may be fading. "
            "Reduce position sizes, tighten risk management, and favor "
            "hedged/defined-risk structures. Watch for squeeze or unwind catalysts."
        )
    elif composite >= 30:
        takeaway = (
            "Reversal risk is elevated. Positioning is stretched and flow support "
            "is weakening. Consider defensive adjustments — smaller positions, "
            "wider stops, and protective hedges. Avoid aggressive directional bets."
        )
    else:
        takeaway = (
            "Positioning is unstable with significant unwind risk. "
            "Flows are hostile and positioning is fragile. "
            "Prioritize capital preservation — reduce exposure, hedge existing positions, "
            "and avoid new premium-selling strategies."
        )

    if confidence < 60:
        takeaway += (
            " (Note: confidence is low due to proxy-heavy data and/or "
            "incomplete inputs — interpret cautiously.)"
        )

    return {
        "summary": " ".join(summary_parts),
        "positive_contributors": positive,
        "negative_contributors": negative,
        "conflicting_signals": conflicting,
        "strategy_bias": strategy_bias,
        "trader_takeaway": takeaway,
    }


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENGINE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def compute_flows_positioning_scores(
    positioning_data: dict[str, Any],
    crowding_data: dict[str, Any],
    squeeze_data: dict[str, Any],
    flow_data: dict[str, Any],
    stability_data: dict[str, Any],
    source_meta: dict[str, Any],
) -> dict[str, Any]:
    """Compute the Flows & Positioning engine result.

    Parameters
    ----------
    positioning_data : dict — inputs for Pillar 1 (positioning pressure)
    crowding_data : dict — inputs for Pillar 2 (crowding/stretch)
    squeeze_data : dict — inputs for Pillar 3 (squeeze/unwind risk)
    flow_data : dict — inputs for Pillar 4 (flow direction/persistence)
    stability_data : dict — inputs for Pillar 5 (positioning stability)
    source_meta : dict — data provenance and freshness metadata

    Returns
    -------
    dict — full engine result with score, label, pillars, diagnostics, etc.
    """
    as_of = datetime.now(timezone.utc).isoformat()

    # ── Compute each pillar (hardened with per-pillar try/except) ─
    pillars: dict[str, dict[str, Any]] = {}
    pillar_funcs = {
        "positioning_pressure": (positioning_data, _compute_positioning_pressure),
        "crowding_stretch": (crowding_data, _compute_crowding_stretch),
        "squeeze_unwind_risk": (squeeze_data, _compute_squeeze_unwind_risk),
        "flow_direction_persistence": (flow_data, _compute_flow_direction_persistence),
        "positioning_stability": (stability_data, _compute_positioning_stability),
    }

    for pname, (pdata, pfunc) in pillar_funcs.items():
        try:
            pillars[pname] = pfunc(pdata)
        except Exception as exc:
            logger.error(
                "event=flows_positioning_pillar_error pillar=%s error=%s",
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

    # ── Composite score ──────────────────────────────────────────
    weighted_parts: list[tuple[float | None, float]] = []
    for pname, weight in PILLAR_WEIGHTS.items():
        pdata = pillars.get(pname, {})
        weighted_parts.append((pdata.get("score"), weight))

    composite = _weighted_avg(weighted_parts)
    if composite is None:
        composite = 50.0
        data_status = "no_data"
        logger.warning("event=flows_positioning_composite_failed reason=no_valid_pillars")
    else:
        data_status = "ok"

    if data_status == "no_data":
        full_label, short_label = "Neutral / No Data", "Neutral"
        gate_applied, gate_warnings, gate_details = False, [], []
    else:
        full_label, short_label, composite, gate_applied, gate_warnings, gate_details = (
            _label_from_score_with_gates(composite, pillars)
        )
    confidence, confidence_penalties = _compute_confidence(pillars, source_meta)
    sig_quality = _signal_quality(confidence)
    explanation = _build_composite_explanation(composite, full_label, pillars, confidence)

    # ── Aggregate warnings and missing inputs ────────────────────
    all_warnings: list[str] = list(gate_warnings)  # Gate warnings first
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
        "label_gates": {
            "crowding_gate_threshold": _CROWDING_GATE_THRESHOLD,
            "stability_gate_threshold": _STABILITY_GATE_THRESHOLD,
            "squeeze_risk_gate_threshold": _SQUEEZE_RISK_GATE_THRESHOLD,
            "gate_warnings": gate_warnings,
            "gate_details": gate_details,
            "gate_applied": gate_applied,
            "label_was_gated": gate_applied,
        },
    }

    raw_inputs = {
        "positioning": pillars["positioning_pressure"].get("raw_inputs", {}),
        "crowding": pillars["crowding_stretch"].get("raw_inputs", {}),
        "squeeze": pillars["squeeze_unwind_risk"].get("raw_inputs", {}),
        "flow": pillars["flow_direction_persistence"].get("raw_inputs", {}),
        "stability": pillars["positioning_stability"].get("raw_inputs", {}),
    }

    # Structured data-quality metadata: which fields are proxy, direct, or unavailable
    data_quality = {
        "field_status": {
            sig_name: {
                "type": meta.get("type", "unknown"),
                "source": meta.get("source", "unknown"),
                "is_proxy": meta.get("type") == "proxy",
            }
            for sig_name, meta in SIGNAL_PROVENANCE.items()
        },
        "proxy_count": sum(1 for v in SIGNAL_PROVENANCE.values() if v.get("type") == "proxy"),
        "direct_count": sum(1 for v in SIGNAL_PROVENANCE.values() if v.get("type") == "direct"),
        "upstream_sources": source_meta.get("unique_upstream_count", 0) if source_meta else 0,
        "phase": "1 — VIX-proxy only",
    }

    result = {
        "engine": "flows_positioning",
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
        "strategy_bias": explanation["strategy_bias"],
        "positive_contributors": explanation["positive_contributors"],
        "negative_contributors": explanation["negative_contributors"],
        "conflicting_signals": explanation["conflicting_signals"],
        "trader_takeaway": explanation["trader_takeaway"],
        "warnings": all_warnings,
        "missing_inputs": all_missing,
        "diagnostics": diagnostics,
        "raw_inputs": raw_inputs,
        "data_quality": data_quality,
        "data_status": data_status,
    }

    logger.info(
        "event=flows_positioning_engine_computed score=%.2f label=%s confidence=%.1f "
        "signal_quality=%s pillars=%s warnings=%d missing=%d",
        composite, full_label, confidence, sig_quality,
        {k: round(v, 1) if v is not None else None for k, v in pillar_scores.items()},
        len(all_warnings), len(all_missing),
    )

    return result
