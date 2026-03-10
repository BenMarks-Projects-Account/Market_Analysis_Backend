"""Cross-Asset / Macro Confirmation Scoring Engine.

Institutional-grade engine answering: "Are other major markets confirming
or contradicting the equity story right now?"

Architecture — 5 scored pillars:
  1. Rates & Yield Curve      (25%)  — treasury term structure signal
  2. Dollar & Commodity        (20%)  — USD, oil, gold, copper signals
  3. Credit & Risk Appetite    (25%)  — credit spreads + VIX (VIX PRIMARY home)
  4. Defensive vs Growth       (15%)  — safe-haven vs risk-on alignment (no VIX)
  5. Macro Coherence           (15%)  — cross-pillar consistency (graded ternary)

VIX placement rationale:
  VIX lives ONLY in Pillar 3 as a submetric. Pillar 4 was refactored in the
  second pass to remove vix_credit_alignment (which double-counted VIX with
  Pillar 3). Pillar 5 references VIX as one graded coherence signal but does
  NOT score it independently — it checks whether VIX *agrees* with other
  signals directionally. This is a cross-reference, not a direct score.

Oil interpretation rationale:
  Oil is one of the most ambiguous cross-asset signals. Declining oil can mean
  (a) supply glut (equity neutral/bullish), (b) demand destruction (bearish),
  or (c) inflation headwind easing (neutral). Without trend context, the engine
  treats oil in the $45–$85 range as ambiguous/neutral rather than forcing a
  directional interpretation. Extremes still score directionally. Oil weight
  in Pillar 2 is reduced to 15% (from 25%) to limit false-certainty impact
  from an inherently ambiguous signal.

Composite formula:
  CrossAssetComposite = Σ(pillar_score × weight) / Σ(active_weights)

Label mapping (composite → regime):
  85-100  →  Strong Confirmation
  70-84   →  Confirming
  55-69   →  Partial Confirmation
  45-54   →  Mixed Signals
  30-44   →  Partial Contradiction
  0-29    →  Strong Contradiction

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
    "rates_yield_curve": 0.25,
    "dollar_commodity": 0.20,
    "credit_risk_appetite": 0.25,
    "defensive_vs_growth": 0.15,
    "macro_coherence": 0.15,
}

_LABEL_BANDS: list[tuple[float, float, str, str]] = [
    (85, 100, "Strong Confirmation", "Strong Confirm"),
    (70, 84.99, "Confirming", "Confirming"),
    (55, 69.99, "Partial Confirmation", "Partial"),
    (45, 54.99, "Mixed Signals", "Mixed"),
    (30, 44.99, "Partial Contradiction", "Contradicting"),
    (0, 29.99, "Strong Contradiction", "Strong Contra"),
]

_CONFIDENCE_HIGH = 80
_CONFIDENCE_MEDIUM = 60


# ═══════════════════════════════════════════════════════════════════════
# SIGNAL PROVENANCE — documents source, delay, and proxy status
# of each major input. Surfaced in diagnostics for auditability.
# ═══════════════════════════════════════════════════════════════════════

SIGNAL_PROVENANCE: dict[str, dict[str, str]] = {
    "ten_year_yield": {
        "source": "MarketContextService → Tradier/Finnhub/FRED",
        "type": "direct",
        "delay": "intraday when market open; EOD otherwise",
        "unit": "percent",
        "notes": "10-Year Treasury Constant Maturity Rate",
    },
    "two_year_yield": {
        "source": "MarketContextService → FRED DGS2",
        "type": "direct",
        "delay": "1 business day (FRED daily series)",
        "unit": "percent",
        "notes": "2-Year Treasury Constant Maturity Rate",
    },
    "yield_curve_spread": {
        "source": "MarketContextService (derived: 10Y - 2Y)",
        "type": "derived",
        "delay": "inherits slowest input (~1 business day)",
        "unit": "percentage points",
        "formula": "ten_year_yield - two_year_yield",
    },
    "fed_funds_rate": {
        "source": "MarketContextService → FRED DFF",
        "type": "direct",
        "delay": "1 business day",
        "unit": "percent",
        "notes": "Effective Federal Funds Rate (daily)",
    },
    "vix": {
        "source": "MarketContextService → Tradier/Finnhub/FRED waterfall",
        "type": "direct",
        "delay": "near-realtime when market open; EOD otherwise",
        "unit": "index level",
        "notes": "CBOE Volatility Index. PRIMARY home is Pillar 3 (Credit).",
    },
    "usd_index": {
        "source": "MarketContextService → FRED DTWEXBGS",
        "type": "proxy",
        "delay": "1 business day (trade-weighted broad index, not DXY)",
        "unit": "index level",
        "notes": "Trade-Weighted US Dollar Index (Broad). Proxy for DXY; "
                 "directionally similar but not identical.",
    },
    "oil_wti": {
        "source": "MarketContextService → FRED DCOILWTICO",
        "type": "direct",
        "delay": "1 business day",
        "unit": "USD/barrel",
        "notes": "WTI Crude Oil Spot Price. Inherently ambiguous signal — "
                 "declining oil may be supply-driven or demand-destruction.",
    },
    "gold_price": {
        "source": "FRED GOLDAMGBD228NLBM",
        "type": "direct",
        "delay": "1 business day (London PM fixing, EOD)",
        "unit": "USD/troy ounce",
        "notes": "Gold Fixing Price London Bullion Market (PM). "
                 "Daily frequency, published ~1 day delayed.",
    },
    "copper_price": {
        "source": "FRED PCOPPUSDM",
        "type": "proxy",
        "delay": "monthly (significant lag for daily confirmation)",
        "unit": "USD/metric ton",
        "notes": "Global copper price, LME, monthly average. CAUTION: "
                 "monthly series is a SLOW proxy — do not treat as real-time "
                 "confirmation. Confidence is reduced when this is the only "
                 "growth signal available.",
    },
    "ig_spread": {
        "source": "FRED BAMLC0A0CM",
        "type": "direct",
        "delay": "1-2 business days",
        "unit": "percent (OAS)",
        "notes": "ICE BofA US Corporate Investment Grade OAS. "
                 "High-quality institutional signal for credit health.",
    },
    "hy_spread": {
        "source": "FRED BAMLH0A0HYM2",
        "type": "direct",
        "delay": "1-2 business days",
        "unit": "percent (OAS)",
        "notes": "ICE BofA US High Yield OAS. "
                 "Wider spreads = more credit stress / risk-off.",
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


def _signal_quality(confidence: float) -> str:
    if confidence >= _CONFIDENCE_HIGH:
        return "high"
    if confidence >= _CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


# ═══════════════════════════════════════════════════════════════════════
# PILLAR 1 — RATES & YIELD CURVE (25%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_rates_yield_curve(data: dict[str, Any]) -> dict[str, Any]:
    """Score the rates & yield curve pillar.

    Submetrics:
      yield_curve_spread — 10Y-2Y spread level → positive/normalizing = bullish
      ten_year_level     — absolute 10Y level → moderate = bullish, extremes = bearish
      rate_differential  — 10Y vs fed funds → steep = growth optimism

    Weights:
      yield_curve_spread  45%
      ten_year_level      30%
      rate_differential   25%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    ten_year = _safe_float(data.get("ten_year_yield"))
    two_year = _safe_float(data.get("two_year_yield"))
    yield_spread = _safe_float(data.get("yield_curve_spread"))
    fed_funds = _safe_float(data.get("fed_funds_rate"))

    raw_inputs = {
        "ten_year_yield": ten_year, "two_year_yield": two_year,
        "yield_curve_spread": yield_spread, "fed_funds_rate": fed_funds,
    }

    # ── yield_curve_spread ───────────────────────────────────────
    # Positive spread = normal curve = growth optimism = equity bullish
    # Negative spread = inverted curve = recession signal = equity bearish
    # Formula: score = interpolate(spread, -1.0, +2.0, 10, 95)
    # Input: spread in percentage points (e.g., 0.16 = 16bp)
    if yield_spread is not None:
        yc_score = _interpolate(yield_spread, -1.0, 2.0, 10, 95)
        submetrics.append(_build_submetric(
            "yield_curve_spread", yield_spread, yc_score,
            details={"ten_year": ten_year, "two_year": two_year},
        ))
    else:
        total_missing += 1
        warnings.append("yield_curve_spread: missing yield data")
        submetrics.append(_build_submetric("yield_curve_spread", None, None))

    # ── ten_year_level ───────────────────────────────────────────
    # Moderate yields (2.5-4.5%) are equity-friendly (growth, not restrictive)
    # Very high (>5.5%) or very low (<1.5%) = concerning
    # Formula: bell-curve-like scoring centered around 3.5%
    if ten_year is not None:
        if ten_year <= 3.5:
            # 0% → 40, 1.5% → 60, 2.5% → 80, 3.5% → 90
            ty_score = _interpolate(ten_year, 0.0, 3.5, 40, 90)
        else:
            # 3.5% → 90, 4.5% → 70, 5.5% → 40, 6.5% → 20
            ty_score = _interpolate(ten_year, 3.5, 6.5, 90, 20)
        submetrics.append(_build_submetric(
            "ten_year_level", ten_year, ty_score,
        ))
    else:
        total_missing += 1
        warnings.append("ten_year_level: missing 10Y yield")
        submetrics.append(_build_submetric("ten_year_level", None, None))

    # ── rate_differential ────────────────────────────────────────
    # 10Y minus fed funds: positive = market pricing growth above policy rate
    # Negative = market pessimistic vs policy rate (restrictive)
    # Formula: score = interpolate(diff, -2.0, +1.5, 15, 90)
    if ten_year is not None and fed_funds is not None:
        diff = ten_year - fed_funds
        rd_score = _interpolate(diff, -2.0, 1.5, 15, 90)
        submetrics.append(_build_submetric(
            "rate_differential", diff, rd_score,
            details={"ten_year": ten_year, "fed_funds": fed_funds},
        ))
    else:
        total_missing += 1
        warnings.append("rate_differential: missing yield or fed funds data")
        submetrics.append(_build_submetric("rate_differential", None, None))

    sub_weights = {
        "yield_curve_spread": 0.45,
        "ten_year_level": 0.30,
        "rate_differential": 0.25,
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
# PILLAR 2 — DOLLAR & COMMODITY (20%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_dollar_commodity(data: dict[str, Any]) -> dict[str, Any]:
    """Score the dollar & commodity pillar.

    Submetrics:
      usd_level     — strong dollar = headwind for equities (inverse)
      oil_level     — moderate oil = healthy, extremes = warning (ambiguity zone $45-$85)
      gold_level    — rising gold = safe-haven demand = bearish signal
      copper_level  — rising copper = growth optimism = bullish

    Weights (second-pass adjusted — oil reduced due to inherent ambiguity):
      usd_level     35%
      oil_level     15%  (was 25% — oil is inherently ambiguous without context)
      gold_level    20%
      copper_level  30%  (was 25% — copper is a more reliable growth proxy)
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    usd = _safe_float(data.get("usd_index"))
    oil = _safe_float(data.get("oil_wti"))
    gold = _safe_float(data.get("gold_price"))
    copper = _safe_float(data.get("copper_price"))

    raw_inputs = {
        "usd_index": usd, "oil_wti": oil,
        "gold_price": gold, "copper_price": copper,
    }

    # ── usd_level ────────────────────────────────────────────────
    # Strong dollar (high DXY) = headwind for multinationals = bearish signal
    # Weak dollar (low DXY) = tailwind = bullish
    # DXY typical range: 90-115. Score inversely.
    # Formula: score = interpolate(usd, 115, 90, 15, 90) — inverse mapping
    if usd is not None:
        usd_score = _interpolate(usd, 115, 90, 15, 90)
        submetrics.append(_build_submetric(
            "usd_level", usd, usd_score,
        ))
    else:
        total_missing += 1
        warnings.append("usd_level: missing USD index data")
        submetrics.append(_build_submetric("usd_level", None, None))

    # ── oil_level ────────────────────────────────────────────────
    # Oil is the most ambiguous cross-asset signal:
    #   - Low oil could mean supply glut (equity neutral) OR demand destruction (bearish)
    #   - High oil could mean growth (bullish backdrop) OR cost inflation (bearish)
    # AMBIGUITY ZONE: $45-$85 → score 50-55 (neutral), do not force direction.
    # Only extremes (<$30 or >$100) produce strongly directional scores.
    # oil_classification tracks the interpretation applied.
    if oil is not None:
        if oil < 30:
            oil_score = _interpolate(oil, 10, 30, 25, 45)
            oil_classification = "demand_destruction"
        elif oil < 45:
            oil_score = _interpolate(oil, 30, 45, 45, 50)
            oil_classification = "supply_concern"
        elif oil <= 85:
            # Ambiguity zone — score near neutral (50-55)
            oil_score = _interpolate(oil, 45, 85, 50, 55)
            oil_classification = "ambiguous"
        elif oil <= 100:
            oil_score = _interpolate(oil, 85, 100, 55, 40)
            oil_classification = "cost_pressure"
        else:
            oil_score = _interpolate(oil, 100, 130, 40, 20)
            oil_classification = "cost_pressure"
        oil_details: dict[str, Any] = {"oil_classification": oil_classification}
        if oil_classification == "ambiguous":
            warnings.append(
                "oil_level: price in ambiguous zone ($45-$85); "
                "direction not interpretable without trend context"
            )
            oil_details["ambiguous"] = True
        submetrics.append(_build_submetric(
            "oil_level", oil, oil_score, details=oil_details,
        ))
    else:
        total_missing += 1
        warnings.append("oil_level: missing oil WTI price")
        submetrics.append(_build_submetric("oil_level", None, None))

    # ── gold_level ───────────────────────────────────────────────
    # Gold as safe-haven proxy. High gold = fear = equity bearish (inverse)
    # Gold typical range: $1500-$2500+
    # Higher gold → lower score (inverse safe-haven signal)
    # Formula: score = interpolate(gold, 2500, 1500, 25, 85) — inverse
    if gold is not None:
        gold_score = _interpolate(gold, 2500, 1500, 25, 85)
        submetrics.append(_build_submetric("gold_level", gold, gold_score))
    else:
        total_missing += 1
        warnings.append("gold_level: missing gold price")
        submetrics.append(_build_submetric("gold_level", None, None))

    # ── copper_level ─────────────────────────────────────────────
    # Copper as growth proxy ("Dr. Copper")
    # Rising copper = global growth = equity bullish
    # Copper typical range: $3000-$10000 USD/metric ton
    # CAUTION: source is FRED PCOPPUSDM (MONTHLY) — see SIGNAL_PROVENANCE
    # Formula: score = interpolate(copper, 4000, 10000, 25, 90)
    if copper is not None:
        copper_score = _interpolate(copper, 4000, 10000, 25, 90)
        submetrics.append(_build_submetric("copper_level", copper, copper_score))
    else:
        total_missing += 1
        warnings.append("copper_level: missing copper price")
        submetrics.append(_build_submetric("copper_level", None, None))

    sub_weights = {
        "usd_level": 0.35,
        "oil_level": 0.15,
        "gold_level": 0.20,
        "copper_level": 0.30,
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
# PILLAR 3 — CREDIT & RISK APPETITE (25%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_credit_risk_appetite(data: dict[str, Any]) -> dict[str, Any]:
    """Score the credit & risk appetite pillar.

    VIX PRIMARY HOME: VIX is scored here as its primary pillar.
    It is NOT scored independently in Pillar 4 (removed in second pass).
    Pillar 5 references VIX as a graded coherence cross-check only.

    Submetrics:
      ig_spread_level  — tight IG = healthy credit = bullish
      hy_spread_level  — tight HY = risk appetite = bullish
      vix_level        — low VIX = calm = bullish (moderate levels best)
      hy_ig_ratio      — HY/IG spread ratio — wide = stress in lower credit

    Weights (second-pass adjusted — VIX reduced from 25% to 20%):
      ig_spread_level  30%  (was 25%)
      hy_spread_level  35%
      vix_level        20%  (was 25% — reduced to limit VIX aggregate weight)
      hy_ig_ratio      15%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    ig_spread = _safe_float(data.get("ig_spread"))
    hy_spread = _safe_float(data.get("hy_spread"))
    vix = _safe_float(data.get("vix"))

    raw_inputs = {
        "ig_spread": ig_spread, "hy_spread": hy_spread, "vix": vix,
    }

    # ── ig_spread_level ──────────────────────────────────────────
    # IG OAS typical range: 0.5% - 3.0%. Lower = healthier credit.
    # Formula: score = interpolate(ig, 3.0, 0.5, 15, 92) — inverse
    if ig_spread is not None:
        ig_score = _interpolate(ig_spread, 3.0, 0.5, 15, 92)
        submetrics.append(_build_submetric("ig_spread_level", ig_spread, ig_score))
    else:
        total_missing += 1
        warnings.append("ig_spread_level: missing IG spread data")
        submetrics.append(_build_submetric("ig_spread_level", None, None))

    # ── hy_spread_level ──────────────────────────────────────────
    # HY OAS typical range: 2.5% - 10.0%. Lower = more risk appetite.
    # Formula: score = interpolate(hy, 10.0, 2.5, 10, 92) — inverse
    if hy_spread is not None:
        hy_score = _interpolate(hy_spread, 10.0, 2.5, 10, 92)
        submetrics.append(_build_submetric("hy_spread_level", hy_spread, hy_score))
    else:
        total_missing += 1
        warnings.append("hy_spread_level: missing HY spread data")
        submetrics.append(_build_submetric("hy_spread_level", None, None))

    # ── vix_level ────────────────────────────────────────────────
    # VIX typical range: 10-40. Lower = calmer = equity bullish
    # Very low (<12) = complacency, slightly less bullish
    # Sweet spot: 12-18 → high score
    # Formula: bell-curve centered around 15
    if vix is not None:
        if vix <= 15:
            # 8→75, 12→88, 15→90
            vix_score = _interpolate(vix, 8, 15, 75, 90)
        else:
            # 15→90, 20→65, 25→45, 30→30, 40→15
            vix_score = _interpolate(vix, 15, 40, 90, 15)
        submetrics.append(_build_submetric("vix_level", vix, vix_score))
    else:
        total_missing += 1
        warnings.append("vix_level: missing VIX data")
        submetrics.append(_build_submetric("vix_level", None, None))

    # ── hy_ig_ratio ──────────────────────────────────────────────
    # HY/IG spread ratio: lower = healthier credit differentiation
    # Typical range: 2.5-6.0. Below 3.5 = healthy, above 5.0 = stress
    # Formula: score = interpolate(ratio, 6.0, 2.5, 15, 88) — inverse
    if ig_spread is not None and hy_spread is not None and ig_spread > 0:
        ratio = hy_spread / ig_spread
        ratio_score = _interpolate(ratio, 6.0, 2.5, 15, 88)
        submetrics.append(_build_submetric(
            "hy_ig_ratio", ratio, ratio_score,
            details={"hy_spread": hy_spread, "ig_spread": ig_spread},
        ))
    else:
        total_missing += 1
        warnings.append("hy_ig_ratio: missing spread data for ratio")
        submetrics.append(_build_submetric("hy_ig_ratio", None, None))

    sub_weights = {
        "ig_spread_level": 0.30,
        "hy_spread_level": 0.35,
        "vix_level": 0.20,
        "hy_ig_ratio": 0.15,
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
# PILLAR 4 — DEFENSIVE VS GROWTH ALIGNMENT (15%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_defensive_vs_growth(data: dict[str, Any]) -> dict[str, Any]:
    """Score whether safe-haven vs risk assets are aligned with equity direction.

    Second-pass refactored: removed vix_credit_alignment (which double-counted
    VIX with Pillar 3). Now uses only gold/yield and copper/gold ratios — both
    are VIX-independent measures of safe-haven vs growth positioning.

    Submetrics:
      gold_yield_divergence  — gold rising + yields falling = defensive flight
      copper_gold_ratio      — high copper/gold = growth optimism

    Weights:
      gold_yield_divergence  45%
      copper_gold_ratio      55%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    gold = _safe_float(data.get("gold_price"))
    ten_year = _safe_float(data.get("ten_year_yield"))
    copper = _safe_float(data.get("copper_price"))

    raw_inputs = {
        "gold_price": gold, "ten_year_yield": ten_year,
        "copper_price": copper,
    }

    # ── gold_yield_divergence ────────────────────────────────────
    # If gold is high AND yields are low → defensive flight → bearish
    # If gold is moderate AND yields are moderate → normal → bullish
    # Composite: lower gold + higher yields = growth optimism = higher score
    # Formula: score based on ten_year_yield / (gold / 1000)
    # Typical: 10Y=4.0, gold=2000 → ratio = 4.0/2.0 = 2.0
    if gold is not None and ten_year is not None and gold > 0:
        gold_k = gold / 1000.0
        ratio = ten_year / max(gold_k, 0.1)
        # Ratio range: 0.5 (defensive) to 4.0 (growth)
        # Formula: score = interpolate(ratio, 0.5, 4.0, 20, 88)
        gy_score = _interpolate(ratio, 0.5, 4.0, 20, 88)
        submetrics.append(_build_submetric(
            "gold_yield_divergence", ratio, gy_score,
            details={"gold": gold, "ten_year": ten_year, "gold_k": round(gold_k, 2)},
        ))
    else:
        total_missing += 1
        warnings.append("gold_yield_divergence: missing gold or yield data")
        submetrics.append(_build_submetric("gold_yield_divergence", None, None))

    # ── copper_gold_ratio ────────────────────────────────────────
    # High copper relative to gold = growth optimism = equity bullish
    # Low copper/gold = flight to safety = bearish
    # Copper in USD/metric ton, Gold in USD/oz
    # Normalize: (copper / gold) * scaling
    # Typical: copper=8000, gold=2000 → ratio = 4.0
    # Formula: score = interpolate(ratio, 1.5, 6.0, 20, 88)
    if copper is not None and gold is not None and gold > 0:
        cg_ratio = copper / gold
        cg_score = _interpolate(cg_ratio, 1.5, 6.0, 20, 88)
        submetrics.append(_build_submetric(
            "copper_gold_ratio", cg_ratio, cg_score,
            details={"copper": copper, "gold": gold},
        ))
    else:
        total_missing += 1
        warnings.append("copper_gold_ratio: missing copper or gold data")
        submetrics.append(_build_submetric("copper_gold_ratio", None, None))

    sub_weights = {
        "gold_yield_divergence": 0.45,
        "copper_gold_ratio": 0.55,
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
# PILLAR 5 — MACRO COHERENCE (15%)
# ═══════════════════════════════════════════════════════════════════════

def _compute_macro_coherence(data: dict[str, Any]) -> dict[str, Any]:
    """Meta-pillar: how internally consistent are cross-asset signals?

    Second-pass refactored: replaced binary True/False signals with GRADED
    TERNARY scoring. Each signal produces +1 (confirming), 0 (neutral/ambiguous),
    or -1 (contradicting) instead of forcing a binary direction through crude
    cutoffs. This prevents mild mixed signals from being treated the same as
    outright contradictions.

    Neutral bands per signal (values within these ranges score 0):
      vix:         16-22  (neither calm nor panicked)
      yield_curve: -0.20 to +0.10  (flat, not clearly inverted or steep)
      ig_credit:   1.0-1.8  (typical range)
      hy_credit:   3.5-5.5  (typical range)
      usd:         98-107  (middle of typical range)
      copper:      6500-8000  (moderate growth)
      gold:        1900-2300  (moderate level)

    Submetrics:
      risk_on_count        — weighted fraction of confirming signals
      signal_agreement     — coherence magnitude (how aligned are grades)
      contradiction_count  — number of strongly opposing signal pairs

    Weights:
      risk_on_count       35%
      signal_agreement    40%
      contradiction_count 25%
    """
    submetrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_inputs: dict[str, Any] = {}
    total_missing = 0

    vix = _safe_float(data.get("vix"))
    yield_spread = _safe_float(data.get("yield_curve_spread"))
    ig_spread = _safe_float(data.get("ig_spread"))
    hy_spread = _safe_float(data.get("hy_spread"))
    usd = _safe_float(data.get("usd_index"))
    oil = _safe_float(data.get("oil_wti"))
    gold = _safe_float(data.get("gold_price"))
    copper = _safe_float(data.get("copper_price"))

    raw_inputs = {
        "vix": vix, "yield_curve_spread": yield_spread,
        "ig_spread": ig_spread, "hy_spread": hy_spread,
        "usd_index": usd, "oil_wti": oil,
        "gold_price": gold, "copper_price": copper,
    }

    # ── Build graded ternary signal direction for each metric ─────
    # +1 = confirming risk-on / equity bullish
    #  0 = neutral / ambiguous (within normal band)
    # -1 = contradicting / risk-off signal
    signal_grades: dict[str, float] = {}

    if vix is not None:
        if vix < 16:
            signal_grades["vix"] = 1.0      # Calm → risk-on
        elif vix <= 22:
            signal_grades["vix"] = 0.0      # Neutral band
        else:
            signal_grades["vix"] = -1.0     # Elevated → risk-off

    if yield_spread is not None:
        if yield_spread > 0.10:
            signal_grades["yield_curve"] = 1.0     # Positive spread → growth
        elif yield_spread >= -0.20:
            signal_grades["yield_curve"] = 0.0     # Flat → ambiguous
        else:
            signal_grades["yield_curve"] = -1.0    # Inverted → recession risk

    if ig_spread is not None:
        if ig_spread < 1.0:
            signal_grades["ig_credit"] = 1.0       # Very tight → healthy
        elif ig_spread <= 1.8:
            signal_grades["ig_credit"] = 0.0       # Typical range → neutral
        else:
            signal_grades["ig_credit"] = -1.0      # Wide → stress

    if hy_spread is not None:
        if hy_spread < 3.5:
            signal_grades["hy_credit"] = 1.0       # Tight → risk appetite
        elif hy_spread <= 5.5:
            signal_grades["hy_credit"] = 0.0       # Typical → neutral
        else:
            signal_grades["hy_credit"] = -1.0      # Wide → risk-off

    if usd is not None:
        if usd < 98:
            signal_grades["usd"] = 1.0             # Weak dollar → tailwind
        elif usd <= 107:
            signal_grades["usd"] = 0.0             # Middle range → neutral
        else:
            signal_grades["usd"] = -1.0            # Strong dollar → headwind

    if copper is not None:
        if copper > 8000:
            signal_grades["copper"] = 1.0           # Strong → growth
        elif copper >= 6500:
            signal_grades["copper"] = 0.0           # Moderate → neutral
        else:
            signal_grades["copper"] = -1.0          # Weak → contraction signal

    if gold is not None:
        if gold < 1900:
            signal_grades["gold"] = 1.0             # Low gold → low fear
        elif gold <= 2300:
            signal_grades["gold"] = 0.0             # Moderate → neutral
        else:
            signal_grades["gold"] = -1.0            # High gold → safe-haven demand

    # Oil is excluded from coherence signals due to inherent ambiguity
    # (see oil interpretation rationale in module docstring)

    total_signals = len(signal_grades)

    # ── risk_on_count ────────────────────────────────────────────
    # Weighted fraction of confirming (+1) signals vs total.
    # Neutral (0) signals reduce the denominator — they don't confirm or deny.
    if total_signals >= 3:
        confirming = sum(1 for v in signal_grades.values() if v > 0)
        contradicting_n = sum(1 for v in signal_grades.values() if v < 0)
        neutral = sum(1 for v in signal_grades.values() if v == 0)
        # Effective denominator excludes neutral signals
        effective_total = confirming + contradicting_n
        if effective_total > 0:
            risk_on_pct = confirming / effective_total
        else:
            # All signals neutral — report as 0.5 (truly mixed/ambiguous)
            risk_on_pct = 0.5
        ro_score = _interpolate(risk_on_pct, 0.0, 1.0, 10, 95)
        submetrics.append(_build_submetric(
            "risk_on_count", risk_on_pct, ro_score,
            observations=total_signals,
            details={
                "confirming": confirming, "contradicting": contradicting_n,
                "neutral": neutral, "total": total_signals,
                "signal_grades": signal_grades,
            },
        ))
    else:
        total_missing += 1
        warnings.append("risk_on_count: insufficient signals available")
        submetrics.append(_build_submetric("risk_on_count", None, None))

    # ── signal_agreement ─────────────────────────────────────────
    # How aligned are non-neutral signals? High agreement (most pointing
    # the same direction) scores higher. Neutral signals are excluded.
    if total_signals >= 3:
        directional = [v for v in signal_grades.values() if v != 0]
        if len(directional) >= 2:
            pos_count = sum(1 for v in directional if v > 0)
            neg_count = sum(1 for v in directional if v < 0)
            max_direction = max(pos_count, neg_count)
            agreement = max_direction / len(directional)
        elif len(directional) == 1:
            agreement = 1.0  # Single directional signal = perfect self-agreement
        else:
            agreement = 0.5  # All neutral = report as ambiguous
        agree_score = _interpolate(agreement, 0.5, 1.0, 20, 95)
        submetrics.append(_build_submetric(
            "signal_agreement", agreement, agree_score,
            observations=total_signals,
        ))
    else:
        total_missing += 1
        warnings.append("signal_agreement: insufficient signals")
        submetrics.append(_build_submetric("signal_agreement", None, None))

    # ── contradiction_count ──────────────────────────────────────
    # Specific contradictions: pairs of signals pointing opposite directions.
    # Only counted when BOTH signals are directional (not neutral).
    contradictions = 0
    if total_signals >= 3:
        # Contradiction 1: VIX calm (+1) but HY credit stressed (-1)
        if signal_grades.get("vix", 0) > 0 and signal_grades.get("hy_credit", 0) < 0:
            contradictions += 1
        # Contradiction 2: Copper strong (+1) but yield curve inverted (-1)
        if signal_grades.get("copper", 0) > 0 and signal_grades.get("yield_curve", 0) < 0:
            contradictions += 1
        # Contradiction 3: Dollar weak/bullish (+1) but gold high/fearful (-1)
        if signal_grades.get("usd", 0) > 0 and signal_grades.get("gold", 0) < 0:
            contradictions += 1

        max_possible = 3
        contradiction_pct = contradictions / max_possible
        # Lower contradictions → higher score
        contra_score = _interpolate(contradiction_pct, 1.0, 0.0, 20, 90)
        submetrics.append(_build_submetric(
            "contradiction_count", float(contradictions), contra_score,
            observations=max_possible,
            details={"contradictions": contradictions},
        ))
    else:
        total_missing += 1
        warnings.append("contradiction_count: insufficient data")
        submetrics.append(_build_submetric("contradiction_count", None, None))

    sub_weights = {
        "risk_on_count": 0.35,
        "signal_agreement": 0.40,
        "contradiction_count": 0.25,
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
# CONFIDENCE + EXPLANATION BUILDERS
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

    # Penalty for missing pillars
    valid_pillars = [p for p in pillars.values() if p.get("score") is not None]
    missing_pillar_count = len(pillars) - len(valid_pillars)
    if missing_pillar_count > 0:
        penalty = missing_pillar_count * 15
        confidence -= penalty
        penalties.append(f"Missing {missing_pillar_count} pillar(s) (-{penalty})")

    # Penalty for degraded submetrics
    total_missing_subs = sum(p.get("missing_count", 0) for p in pillars.values())
    if total_missing_subs > 0:
        sub_penalty = min(total_missing_subs * 3, 25)
        confidence -= sub_penalty
        penalties.append(f"{total_missing_subs} missing submetric(s) (-{sub_penalty})")

    # Penalty for cross-pillar disagreement
    valid_scores = [p["score"] for p in valid_pillars]
    if len(valid_scores) >= 2:
        score_range = max(valid_scores) - min(valid_scores)
        if score_range > 40:
            disagree_penalty = min((score_range - 40) * 0.5, 15)
            confidence -= disagree_penalty
            penalties.append(f"Cross-pillar range {score_range:.0f} (-{disagree_penalty:.1f})")

    # Penalty for stale FRED sources (especially copper = monthly)
    if source_meta:
        fred_copper_date = source_meta.get("fred_copper_date")
        if fred_copper_date:
            stale_note = (
                "Copper (PCOPPUSDM) is a monthly series — may be "
                "up to 30 days stale (-3)"
            )
            confidence -= 3
            penalties.append(stale_note)

    return _clamp(round(confidence, 1), 0, 100), penalties


def _build_composite_explanation(
    composite: float,
    label: str,
    pillars: dict[str, dict[str, Any]],
    confidence: float,
) -> dict[str, Any]:
    """Build structured explanation for UI rendering."""
    confirming: list[str] = []
    contradicting: list[str] = []
    mixed: list[str] = []

    for pname, pdata in pillars.items():
        score = pdata.get("score")
        if score is None:
            continue
        readable = pname.replace("_", " ").title()
        if score >= 65:
            confirming.append(f"{readable} confirms equities ({score:.0f}/100)")
        elif score >= 45:
            mixed.append(f"{readable} is sending mixed signals ({score:.0f}/100)")
        else:
            contradicting.append(f"{readable} contradicts equities ({score:.0f}/100)")

    summary_parts = [f"Cross-asset confirmation is {label.lower()} (composite {composite:.0f}/100)."]
    if confirming:
        summary_parts.append(f"Confirming: {confirming[0].split(' confirms')[0].lower()}.")
    if contradicting:
        summary_parts.append(f"Contradicting: {contradicting[0].split(' contradicts')[0].lower()}.")

    if composite >= 70:
        takeaway = (
            "Cross-asset signals broadly confirm the equity story. "
            "Rates, credit, and commodities are aligned with risk-on positioning. "
            "Full-confidence setups are supported."
        )
    elif composite >= 55:
        takeaway = (
            "Cross-asset signals partially confirm equities. Some markets support "
            "the bull case but others are neutral or cautionary. "
            "Favor risk-defined strategies and monitor contradictory signals."
        )
    elif composite >= 45:
        takeaway = (
            "Cross-asset signals are mixed. The equity story lacks broad confirmation. "
            "Reduce position sizing, favor income strategies, and watch for deterioration."
        )
    elif composite >= 30:
        takeaway = (
            "Cross-asset signals partially contradict the equity story. "
            "Credit, commodities, or rates are sending warning signals. "
            "Defensive positioning recommended — hedges and smaller size."
        )
    else:
        takeaway = (
            "Cross-asset signals broadly contradict equities. Multiple markets are "
            "flashing risk-off. Avoid new directional exposure, favor cash and hedges."
        )

    if confidence < 60:
        takeaway += " (Note: confidence is low due to incomplete data — interpret cautiously.)"

    return {
        "summary": " ".join(summary_parts),
        "confirming_signals": confirming,
        "contradicting_signals": contradicting,
        "mixed_signals": mixed,
        "trader_takeaway": takeaway,
    }


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENGINE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def compute_cross_asset_scores(
    rates_data: dict[str, Any],
    dollar_commodity_data: dict[str, Any],
    credit_data: dict[str, Any],
    defensive_growth_data: dict[str, Any],
    coherence_data: dict[str, Any],
    source_meta: dict[str, Any],
) -> dict[str, Any]:
    """Compute the Cross-Asset / Macro Confirmation engine result.

    Parameters
    ----------
    rates_data : dict — inputs for Pillar 1 (yields, curve spread)
    dollar_commodity_data : dict — inputs for Pillar 2 (USD, oil, gold, copper)
    credit_data : dict — inputs for Pillar 3 (IG/HY spreads, VIX)
    defensive_growth_data : dict — inputs for Pillar 4 (gold/yield, copper/gold)
    coherence_data : dict — inputs for Pillar 5 (all signals for cross-check)
    source_meta : dict — data freshness metadata

    Returns
    -------
    dict — full engine result with score, label, pillars, diagnostics, etc.
    """
    as_of = datetime.now(timezone.utc).isoformat()

    # ── Compute each pillar (hardened with per-pillar try/except) ─
    pillars: dict[str, dict[str, Any]] = {}
    pillar_funcs = {
        "rates_yield_curve": (rates_data, _compute_rates_yield_curve),
        "dollar_commodity": (dollar_commodity_data, _compute_dollar_commodity),
        "credit_risk_appetite": (credit_data, _compute_credit_risk_appetite),
        "defensive_vs_growth": (defensive_growth_data, _compute_defensive_vs_growth),
        "macro_coherence": (coherence_data, _compute_macro_coherence),
    }

    for pname, (pdata, pfunc) in pillar_funcs.items():
        try:
            pillars[pname] = pfunc(pdata)
        except Exception as exc:
            logger.error("event=cross_asset_pillar_error pillar=%s error=%s", pname, exc, exc_info=True)
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
        composite = 0.0
        logger.warning("event=cross_asset_composite_failed reason=no_valid_pillars")

    full_label, short_label = _label_from_score(composite)
    confidence, confidence_penalties = _compute_confidence(pillars, source_meta)
    sig_quality = _signal_quality(confidence)
    explanation = _build_composite_explanation(composite, full_label, pillars, confidence)

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
    }

    raw_inputs = {
        "rates": pillars["rates_yield_curve"].get("raw_inputs", {}),
        "dollar_commodity": pillars["dollar_commodity"].get("raw_inputs", {}),
        "credit": pillars["credit_risk_appetite"].get("raw_inputs", {}),
        "defensive_growth": pillars["defensive_vs_growth"].get("raw_inputs", {}),
        "coherence": pillars["macro_coherence"].get("raw_inputs", {}),
    }

    result = {
        "engine": "cross_asset_macro",
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
        "confirming_signals": explanation["confirming_signals"],
        "contradicting_signals": explanation["contradicting_signals"],
        "mixed_signals": explanation["mixed_signals"],
        "trader_takeaway": explanation["trader_takeaway"],
        "warnings": all_warnings,
        "missing_inputs": all_missing,
        "diagnostics": diagnostics,
        "raw_inputs": raw_inputs,
    }

    logger.info(
        "event=cross_asset_engine_computed score=%.2f label=%s confidence=%.1f "
        "signal_quality=%s pillars=%s warnings=%d missing=%d",
        composite, full_label, confidence, sig_quality,
        {k: round(v, 1) if v is not None else None for k, v in pillar_scores.items()},
        len(all_warnings), len(all_missing),
    )

    return result
