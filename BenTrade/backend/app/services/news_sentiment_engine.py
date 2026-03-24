"""Deterministic News & Sentiment Engine.

Produces a composite 0-100 score from six weighted components, each computed
from raw provider data (headlines, macro context).  No LLM calls — purely
rule-based so results are reproducible and explainable.

Components and weights (total = 100):
  headline_sentiment  30  – aggregate keyword sentiment across headlines
  negative_pressure   20  – ratio of bearish headlines in recent window
  narrative_severity  15  – weighted severity of detected narrative themes
  source_agreement    10  – cross-source sentiment alignment
  macro_stress        15  – FRED-derived macro stress (VIX, yield curve, etc.)
  recency_pressure    10  – time-decay weighted sentiment of most recent items

Composite formula:
  score = sum(component_score * weight) / sum(weights)
  Each component_score is normalized to 0-100 (higher = more bullish/calm).

Regime label mapping (from composite score):
  >= 65  →  Risk-On
  40-64  →  Neutral
  25-39  →  Mixed
  < 25   →  Risk-Off / High Stress (if macro stress is "high")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ── Component weights ───────────────────────────────────────────────
_WEIGHTS: dict[str, float] = {
    "headline_sentiment": 30.0,
    "negative_pressure": 20.0,
    "narrative_severity": 15.0,
    "source_agreement": 10.0,
    "macro_stress": 15.0,
    "recency_pressure": 10.0,
}

# ── Sentiment keywords (mirrors news_sentiment_service) ────────────
_BULLISH_WORDS = frozenset([
    "surge", "rally", "gain", "soar", "bull", "upbeat", "optimism", "optimistic",
    "growth", "strong", "boost", "recovery", "positive", "beat", "outperform",
    "upgrade", "record high", "breakout", "rebound", "expansion",
])
_BEARISH_WORDS = frozenset([
    "crash", "plunge", "drop", "fall", "bear", "fear", "recession", "downturn",
    "decline", "loss", "risk", "warning", "downgrade", "sell-off", "selloff",
    "weak", "cut", "layoff", "default", "crisis", "contraction", "slump",
    "tumble", "collapse", "concern", "uncertainty", "volatile",
])

# Categories considered high-severity for narrative scoring
_HIGH_SEVERITY_CATEGORIES = frozenset(["geopolitical", "fed", "macro"])
_MEDIUM_SEVERITY_CATEGORIES = frozenset(["commodities", "shipping"])


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _score_text_sentiment(text: str) -> float:
    """Keyword sentiment: -1.0 (bearish) to +1.0 (bullish)."""
    text_lower = text.lower()
    words = set(text_lower.split())
    bull = len(words & _BULLISH_WORDS)
    bear = len(words & _BEARISH_WORDS)
    for phrase in _BULLISH_WORDS:
        if " " in phrase and phrase in text_lower:
            bull += 1
    for phrase in _BEARISH_WORDS:
        if " " in phrase and phrase in text_lower:
            bear += 1
    total = bull + bear
    if total == 0:
        return 0.0
    return (bull - bear) / total


def compute_engine_scores(
    items: list[dict[str, Any]],
    macro_context: dict[str, Any],
) -> dict[str, Any]:
    """Compute the deterministic engine result from raw provider data.

    Parameters
    ----------
    items : list[dict]
        Normalized news items (dicts with headline, summary, source,
        published_at, category, sentiment_score, sentiment_label, etc.).
    macro_context : dict
        FRED macro context with vix, us_10y_yield, us_2y_yield,
        yield_curve_spread, stress_level, etc.

    Returns
    -------
    dict with:
      - score (float 0-100, composite)
      - regime_label (str)
      - components (dict of component name → {score, signals, inputs})
      - weights (dict)
      - as_of (ISO timestamp)
    """
    components: dict[str, dict[str, Any]] = {}

    # ── 1. Headline Sentiment (0-100, 50 = neutral) ─────────────
    components["headline_sentiment"] = _compute_headline_sentiment(items)

    # ── 2. Negative Pressure (0-100, 100 = no bearish pressure) ─
    components["negative_pressure"] = _compute_negative_pressure(items)

    # ── 3. Narrative Severity (0-100, 100 = benign narratives) ──
    components["narrative_severity"] = _compute_narrative_severity(items)

    # ── 4. Source Agreement (0-100, 100 = all sources agree) ────
    components["source_agreement"] = _compute_source_agreement(items)

    # ── 5. Macro Stress (0-100, 100 = low stress) ──────────────
    components["macro_stress"] = _compute_macro_stress(macro_context)

    # ── 6. Recency Pressure (0-100, 50 = neutral recent flow) ──
    components["recency_pressure"] = _compute_recency_pressure(items)

    # ── Weighted composite ──────────────────────────────────────
    total_weight = 0.0
    weighted_sum = 0.0
    for name, weight in _WEIGHTS.items():
        comp = components.get(name)
        if comp and comp.get("score") is not None:
            weighted_sum += comp["score"] * weight
            total_weight += weight

    composite = _bounded(weighted_sum / total_weight, 0.0, 100.0) if total_weight > 0 else 50.0
    data_status = "ok" if total_weight > 0 else "no_data"

    # ── Regime label ────────────────────────────────────────────
    stress_level = (macro_context.get("stress_level") or "unknown").lower()
    if data_status == "no_data":
        regime_label = "Neutral / No Data"
    else:
        regime_label = _regime_from_score(composite, stress_level)

    # ── Confidence ──────────────────────────────────────────────
    headline_count = len(items) if items else 0
    source_count = len({h.get("source", "unknown") for h in items}) if items else 0
    defaulted_count = sum(
        1 for c in components.values()
        if c and c.get("score") == 50.0
    )
    confidence, confidence_penalties = _compute_confidence(
        headline_count=headline_count,
        source_count=source_count,
        defaulted_component_count=defaulted_count,
        total_components=len(_WEIGHTS),
        macro_context_available=bool(macro_context),
    )

    result = {
        "score": round(composite, 2),
        "regime_label": regime_label,
        "components": components,
        "weights": _WEIGHTS,
        "confidence": round(confidence, 1),
        "confidence_score": round(confidence, 1),
        "confidence_penalties": confidence_penalties,
        "explanation": build_engine_explanation(composite, regime_label, components, _WEIGHTS),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "data_status": data_status,
    }

    logger.info(
        "event=news_engine_computed score=%.2f regime=%s confidence=%.1f components=%s",
        composite,
        regime_label,
        confidence,
        {k: round(v.get("score", 0), 1) for k, v in components.items()},
    )

    return result


def _regime_from_score(score: float, stress_level: str) -> str:
    """Map composite score to regime label.

    Input fields:
      score: composite 0-100
      stress_level: from macro context (low/moderate/elevated/high)
    """
    if score < 25:
        return "High Stress" if stress_level == "high" else "Risk-Off"
    if score < 40:
        return "Mixed"
    if score < 65:
        return "Neutral"
    return "Risk-On"


# ── Component implementations ──────────────────────────────────────

def _compute_headline_sentiment(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate keyword sentiment across all headlines.

    Converts mean sentiment (-1..1) to 0-100 scale: -1 → 0, 0 → 50, +1 → 100.
    """
    if not items:
        return {"score": 50.0, "signals": ["No headlines available"], "inputs": {"count": 0}}

    scores = []
    for item in items:
        s = item.get("sentiment_score")
        if s is not None:
            scores.append(float(s))
        else:
            text = f"{item.get('headline', '')} {item.get('summary', '')}"
            scores.append(_score_text_sentiment(text))

    mean_sent = sum(scores) / len(scores)
    # Map -1..1 → 0..100
    component_score = _bounded((mean_sent + 1.0) * 50.0, 0.0, 100.0)

    signals = []
    if mean_sent > 0.15:
        signals.append(f"Headline tone is bullish (avg {mean_sent:.3f})")
    elif mean_sent < -0.15:
        signals.append(f"Headline tone is bearish (avg {mean_sent:.3f})")
    else:
        signals.append(f"Headline tone is neutral (avg {mean_sent:.3f})")
    signals.append(f"Based on {len(scores)} headlines")

    return {
        "score": round(component_score, 2),
        "signals": signals,
        "inputs": {"count": len(scores), "mean_sentiment": round(mean_sent, 4)},
    }


def _compute_negative_pressure(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Proportion of bearish headlines in last 24h. Lower bearish ratio = higher score.

    Score: (1 - bear_ratio) * 100.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()

    recent = [i for i in items if (i.get("published_at") or "") >= cutoff]
    if not recent:
        return {"score": 50.0, "signals": ["No 24h headlines available"], "inputs": {"count_24h": 0}}

    bearish_count = sum(1 for i in recent if i.get("sentiment_label") == "bearish")
    bear_ratio = bearish_count / len(recent)
    component_score = _bounded((1.0 - bear_ratio) * 100.0, 0.0, 100.0)

    signals = [f"{bearish_count}/{len(recent)} bearish headlines in 24h ({bear_ratio:.0%})"]
    if bear_ratio > 0.5:
        signals.append("Heavy bearish pressure")
    elif bear_ratio < 0.15:
        signals.append("Minimal bearish pressure")

    return {
        "score": round(component_score, 2),
        "signals": signals,
        "inputs": {
            "count_24h": len(recent),
            "bearish_count": bearish_count,
            "bear_ratio": round(bear_ratio, 4),
        },
    }


def _compute_narrative_severity(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Score based on how many headlines fall into high-severity categories.

    High-severity (geopolitical, fed, macro) = penalized if bearish.
    Score: 100 - severity_penalty.
    """
    if not items:
        return {"score": 75.0, "signals": ["No items for narrative scoring"], "inputs": {}}

    severity_penalty = 0.0
    high_count = 0
    medium_count = 0

    for item in items:
        cat = (item.get("category") or "").lower()
        label = (item.get("sentiment_label") or "").lower()

        if cat in _HIGH_SEVERITY_CATEGORIES:
            high_count += 1
            if label == "bearish":
                severity_penalty += 2.0
            elif label == "mixed":
                severity_penalty += 0.5
        elif cat in _MEDIUM_SEVERITY_CATEGORIES:
            medium_count += 1
            if label == "bearish":
                severity_penalty += 1.0

    # Normalize: cap penalty at 50 points
    severity_penalty = min(severity_penalty, 50.0)
    component_score = _bounded(100.0 - severity_penalty, 0.0, 100.0)

    signals = [f"{high_count} high-severity, {medium_count} medium-severity narratives"]
    if severity_penalty > 20:
        signals.append("Significant bearish activity in critical categories")
    elif severity_penalty < 5:
        signals.append("Critical categories mostly calm")

    return {
        "score": round(component_score, 2),
        "signals": signals,
        "inputs": {
            "high_severity_count": high_count,
            "medium_severity_count": medium_count,
            "penalty": round(severity_penalty, 2),
        },
    }


def _compute_source_agreement(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure inter-source sentiment alignment.

    If Finnhub and Polygon agree on direction, score is high.
    Score: 100 if perfectly aligned, 50 if independent, lower if divergent.
    """
    source_sentiments: dict[str, list[float]] = {}
    for item in items:
        src = item.get("source", "unknown")
        s = item.get("sentiment_score")
        if s is not None:
            source_sentiments.setdefault(src, []).append(float(s))

    sources = list(source_sentiments.keys())
    if len(sources) < 2:
        return {
            "score": 50.0,
            "signals": ["Single source — cannot assess agreement"],
            "inputs": {"sources": sources},
        }

    means = {src: sum(vals) / len(vals) for src, vals in source_sentiments.items()}
    mean_values = list(means.values())

    # Check if all sources have the same sign
    all_positive = all(m > 0.05 for m in mean_values)
    all_negative = all(m < -0.05 for m in mean_values)
    spread = max(mean_values) - min(mean_values)

    if all_positive or all_negative:
        # Sources agree on direction — score based on spread tightness
        component_score = _bounded(100.0 - spread * 50.0, 60.0, 100.0)
        direction = "bullish" if all_positive else "bearish"
        signals = [f"Sources agree on {direction} direction (spread {spread:.3f})"]
    else:
        # Sources disagree
        component_score = _bounded(50.0 - spread * 30.0, 10.0, 50.0)
        signals = [f"Sources disagree on direction (spread {spread:.3f})"]

    return {
        "score": round(component_score, 2),
        "signals": signals,
        "inputs": {
            "source_means": {k: round(v, 4) for k, v in means.items()},
            "spread": round(spread, 4),
        },
    }


def _compute_macro_stress(macro_context: dict[str, Any]) -> dict[str, Any]:
    """Convert FRED macro stress into a 0-100 score.

    Input fields:
      macro_context.stress_level: low/moderate/elevated/high
      macro_context.vix: VIX level
      macro_context.yield_curve_spread: 10y - 2y

    Score mapping: low → 90, moderate → 65, elevated → 35, high → 10.
    VIX and yield curve adjustments applied.
    """
    stress = (macro_context.get("stress_level") or "unknown").lower()
    vix = macro_context.get("vix")
    spread = macro_context.get("yield_curve_spread")

    base_scores = {"low": 90.0, "moderate": 65.0, "elevated": 35.0, "high": 10.0}
    base = base_scores.get(stress, 50.0)

    signals = [f"Macro stress: {stress}"]
    adjustments = 0.0

    if vix is not None:
        signals.append(f"VIX at {vix:.1f}")
        if vix < 16:
            adjustments += 5.0
        elif vix > 30:
            adjustments -= 10.0
        elif vix > 25:
            adjustments -= 5.0

    if spread is not None:
        signals.append(f"Yield curve spread: {spread:.3f}")
        if spread < 0:
            adjustments -= 8.0
            signals.append("Inverted yield curve — elevated recession risk")

    component_score = _bounded(base + adjustments, 0.0, 100.0)

    return {
        "score": round(component_score, 2),
        "signals": signals,
        "inputs": {
            "stress_level": stress,
            "vix": vix,
            "yield_curve_spread": spread,
        },
    }


def _compute_recency_pressure(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Time-decay weighted sentiment of most recent headlines.

    More recent items get exponentially higher weight. Captures the
    "what's happening right now" signal.
    Score: maps decayed average from -1..1 to 0..100.
    """
    now = datetime.now(timezone.utc)

    weighted_sum = 0.0
    weight_sum = 0.0

    for item in items:
        published = item.get("published_at", "")
        score_val = item.get("sentiment_score", 0.0)
        if score_val is None:
            score_val = 0.0

        try:
            pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pub_dt = now - timedelta(hours=48)  # old default

        age_hours = max((now - pub_dt).total_seconds() / 3600.0, 0.1)
        # Exponential decay: half-life = 6 hours
        decay_weight = 2.0 ** (-age_hours / 6.0)

        weighted_sum += float(score_val) * decay_weight
        weight_sum += decay_weight

    if weight_sum == 0:
        return {"score": 50.0, "signals": ["No items for recency scoring"], "inputs": {}}

    decayed_avg = weighted_sum / weight_sum
    component_score = _bounded((decayed_avg + 1.0) * 50.0, 0.0, 100.0)

    signals = [f"Recency-weighted sentiment: {decayed_avg:.3f}"]
    if decayed_avg > 0.15:
        signals.append("Recent flow is bullish-leaning")
    elif decayed_avg < -0.15:
        signals.append("Recent flow is bearish-leaning")
    else:
        signals.append("Recent flow is neutral")

    return {
        "score": round(component_score, 2),
        "signals": signals,
        "inputs": {"decayed_avg": round(decayed_avg, 4), "item_count": len(items)},
    }


# ── Confidence computation ─────────────────────────────────────────

def _compute_confidence(
    *,
    headline_count: int,
    source_count: int,
    defaulted_component_count: int,
    total_components: int,
    macro_context_available: bool,
) -> tuple[float, list[str]]:
    """Compute confidence score for news sentiment analysis.

    Penalizes for:
    - Low headline count (limited data)
    - Low source diversity (echo chamber risk)
    - High proportion of defaulted components (no real data)
    - Keyword-based proxy nature of headline sentiment
    - Missing macro context

    Returns:
        (confidence 0-100, penalty descriptions)
    """
    confidence = 100.0
    penalties: list[str] = []

    # --- Penalty: Low headline count ---
    if headline_count == 0:
        confidence -= 30
        penalties.append("no_headlines: -30")
    elif headline_count < 3:
        confidence -= 20
        penalties.append(f"very_few_headlines ({headline_count}): -20")
    elif headline_count < 5:
        confidence -= 10
        penalties.append(f"few_headlines ({headline_count}): -10")
    elif headline_count < 10:
        confidence -= 5
        penalties.append(f"moderate_headlines ({headline_count}): -5")

    # --- Penalty: Low source diversity ---
    if source_count <= 1:
        confidence -= 15
        penalties.append(f"single_source ({source_count}): -15")
    elif source_count < 3:
        confidence -= 8
        penalties.append(f"low_source_diversity ({source_count}): -8")

    # --- Penalty: Defaulted components (no real data) ---
    if defaulted_component_count >= total_components:
        confidence -= 40
        penalties.append(f"all_components_defaulted ({defaulted_component_count}/{total_components}): -40")
    elif defaulted_component_count >= 4:
        confidence -= 20
        penalties.append(f"most_components_defaulted ({defaulted_component_count}/{total_components}): -20")
    elif defaulted_component_count >= 2:
        confidence -= 10
        penalties.append(f"some_components_defaulted ({defaulted_component_count}/{total_components}): -10")

    # --- Penalty: Proxy nature of keyword sentiment ---
    confidence -= 8
    penalties.append("keyword_sentiment_proxy: -8")

    # --- Penalty: No macro context ---
    if not macro_context_available:
        confidence -= 5
        penalties.append("no_macro_context: -5")

    confidence = max(0.0, min(100.0, confidence))
    return confidence, penalties


# ── Display name and tooltip mappings ──────────────────────────────

_DISPLAY_NAMES: dict[str, str] = {
    "headline_sentiment": "Headline Strength",
    "negative_pressure": "Negative Pressure / Risk Load",
    "narrative_severity": "Narrative Strength",
    "source_agreement": "Source Agreement",
    "macro_stress": "Macro Stress",
    "recency_pressure": "Signal Freshness",
}

_TOOLTIPS: dict[str, str] = {
    "headline_sentiment": "How constructive or supportive the headline set is. Higher = more bullish tone across headlines.",
    "negative_pressure": "Intensity of adverse or downside news pressure. Higher = less bearish pressure (inverted: 100 means no bearish headlines).",
    "narrative_severity": "How coherent and persistent the dominant market stories are in risk categories. Higher = fewer high-severity bearish narratives.",
    "source_agreement": "How consistently major news sources point in the same direction. Higher = stronger cross-source consensus.",
    "macro_stress": "Degree of macro strain in the economic backdrop (VIX, yield curve, stress level). Higher = calmer macro environment.",
    "recency_pressure": "How recent and relevant the input signal set is. Higher = recent headlines skew bullish; lower = recent flow is bearish.",
}

# Components where a higher raw score means LESS market risk
# (inverted semantics — score is already normalized: 100 = good)
_INVERTED_COMPONENTS = {"negative_pressure", "narrative_severity", "macro_stress"}


def _interpret_component(name: str, score: float) -> str:
    """Generate a plain-English interpretation of a component score.

    For inverted components (negative_pressure, narrative_severity, macro_stress),
    explains that a higher score means less risk, not more.
    """
    if name == "headline_sentiment":
        if score >= 65:
            return "Headlines are predominantly constructive and growth-oriented."
        if score >= 40:
            return "Headlines are balanced with no strong directional lean."
        if score >= 25:
            return "Headlines tilt negative with notable caution signals."
        return "Headlines are heavily bearish with widespread risk language."

    if name == "negative_pressure":
        # Higher score = less bearish pressure (inverted)
        if score >= 80:
            return "Very few bearish headlines in the last 24 hours — minimal downside pressure."
        if score >= 60:
            return "Some bearish headlines present but not dominant."
        if score >= 40:
            return "Meaningful bearish pressure — roughly half of recent headlines are negative."
        return "Heavy bearish pressure — the majority of recent headlines signal risk or decline."

    if name == "narrative_severity":
        # Higher score = fewer severe narratives (inverted)
        if score >= 80:
            return "Critical categories (geopolitics, Fed, macro) are mostly calm."
        if score >= 60:
            return "Some activity in high-severity categories but not alarming."
        if score >= 40:
            return "Notable bearish narratives in critical categories are dragging this score down."
        return "Significant bearish activity in geopolitical, Fed, or macro categories."

    if name == "source_agreement":
        if score >= 70:
            return "Major news sources agree on market direction — high-conviction signal."
        if score >= 45:
            return "Sources are loosely aligned or sending independent signals."
        return "Sources are sending conflicting signals — low conviction in any single direction."

    if name == "macro_stress":
        # Higher score = lower stress (inverted)
        if score >= 75:
            return "Macro backdrop is calm — low VIX, healthy yield curve."
        if score >= 50:
            return "Moderate macro stress — some concern signals but no acute crisis."
        if score >= 30:
            return "Elevated macro stress — VIX high or yield curve inverting."
        return "Severe macro stress — high VIX, inverted curve, or acute strain."

    if name == "recency_pressure":
        if score >= 65:
            return "The most recent headlines lean bullish, supporting upward momentum."
        if score >= 40:
            return "Recent headlines are mixed — no strong recency signal."
        return "Recent headlines lean bearish, suggesting deteriorating near-term sentiment."

    return ""


def _contribution_label(score: float) -> str:
    """Classify a component's contribution to the composite."""
    if score >= 60:
        return "positive"
    if score <= 40:
        return "negative"
    return "neutral"


def build_engine_explanation(
    composite: float,
    regime_label: str,
    components: dict[str, dict[str, Any]],
    weights: dict[str, float],
) -> dict[str, Any]:
    """Build a structured explanation object from the engine computation.

    Input fields:
      composite: weighted composite score 0-100
      regime_label: regime string from _regime_from_score
      components: dict of component_name → {score, signals, inputs}
      weights: dict of component_name → weight

    Returns structured explanation matching the required schema.
    """
    # ── Map regime_label to new label set ───────────────────────
    label_map = {
        "Risk-On": "BULLISH",
        "Neutral": "NEUTRAL",
        "Mixed": "MIXED",
        "Risk-Off": "RISK-OFF",
        "High Stress": "RISK-OFF",
    }
    label = label_map.get(regime_label, "NEUTRAL")

    # ── Component analysis ──────────────────────────────────────
    component_analysis = []
    weighted_contributions: list[tuple[str, float, float]] = []  # (name, score, weighted)
    total_weight = sum(weights.values())

    for name in (
        "headline_sentiment", "negative_pressure", "narrative_severity",
        "source_agreement", "macro_stress", "recency_pressure",
    ):
        comp = components.get(name)
        if not comp:
            continue
        score = comp.get("score", 50.0)
        weight = weights.get(name, 0)
        weighted_val = (score * weight) / total_weight if total_weight else 0

        interpretation = _interpret_component(name, score)
        contribution = _contribution_label(score)
        details = comp.get("signals", [])

        component_analysis.append({
            "component": name,
            "display_name": _DISPLAY_NAMES.get(name, name),
            "score": round(score, 2),
            "weight": weight,
            "interpretation": interpretation,
            "contribution": contribution,
            "tooltip": _TOOLTIPS.get(name, ""),
            "details": details,
        })

        weighted_contributions.append((name, score, weighted_val))

    # ── Score logic: positive/negative/balancing contributors ───
    positive = []
    negative = []
    balancing = []

    for name, score, weighted_val in sorted(weighted_contributions, key=lambda x: -x[2]):
        display = _DISPLAY_NAMES.get(name, name)
        if score >= 60:
            positive.append(f"{display} at {score:.0f} (w:{weights.get(name, 0):.0f})")
        elif score <= 40:
            negative.append(f"{display} at {score:.0f} (w:{weights.get(name, 0):.0f})")
        else:
            balancing.append(f"{display} at {score:.0f} — neither helping nor hurting")

    # ── Signal quality ──────────────────────────────────────────
    scores = [c.get("score", 50) for c in components.values() if c]
    score_spread = max(scores) - min(scores) if scores else 0
    count_extreme = sum(1 for s in scores if s >= 75 or s <= 25)

    if score_spread < 20 and count_extreme == 0:
        sig_strength = "low"
        sig_explain = (
            "Components are clustered near neutral with no strong readings. "
            "The composite score reflects a lack of conviction in any direction."
        )
    elif count_extreme >= 3 or score_spread > 50:
        sig_strength = "high"
        sig_explain = (
            "Multiple components show strong readings with clear directional bias. "
            "The composite score carries high conviction."
        )
    else:
        sig_strength = "medium"
        sig_explain = (
            "Some components show meaningful readings while others are neutral. "
            "The composite score reflects a moderate-confidence assessment."
        )

    # ── Summary ─────────────────────────────────────────────────
    summary = _build_engine_summary(composite, regime_label, positive, negative, balancing)

    # ── Trader takeaway ─────────────────────────────────────────
    trader_takeaway = _build_trader_takeaway(composite, regime_label, positive, negative)

    return {
        "label": label,
        "composite_score": round(composite, 2),
        "summary": summary,
        "component_analysis": component_analysis,
        "score_logic": {
            "largest_positive_contributors": positive,
            "largest_negative_contributors": negative,
            "balancing_forces": balancing,
        },
        "signal_quality": {
            "strength": sig_strength,
            "explanation": sig_explain,
        },
        "trader_takeaway": trader_takeaway,
    }


def _build_engine_summary(
    composite: float,
    regime_label: str,
    positive: list[str],
    negative: list[str],
    balancing: list[str],
) -> str:
    """Build a 2-3 sentence plain-English summary of the engine state."""
    # Opening: describe the regime
    if composite >= 65:
        opening = f"The engine reads a constructive market environment (score: {composite:.1f}, regime: {regime_label})."
    elif composite >= 40:
        opening = f"The engine sees a broadly neutral market backdrop (score: {composite:.1f}, regime: {regime_label})."
    elif composite >= 25:
        opening = f"The engine detects mixed signals with notable caution flags (score: {composite:.1f}, regime: {regime_label})."
    else:
        opening = f"The engine flags a stressed market environment (score: {composite:.1f}, regime: {regime_label})."

    # Contributors
    parts = []
    if positive:
        top = positive[0].split(" at ")[0] if positive else ""
        parts.append(f"the strongest positive contributor is {top}")
    if negative:
        top_neg = negative[0].split(" at ")[0] if negative else ""
        parts.append(f"the largest drag is {top_neg}")
    if balancing and not negative and not positive:
        parts.append("most components are near neutral with no strong readings")

    contrib_sentence = ""
    if parts:
        contrib_sentence = " " + parts[0].capitalize()
        for p in parts[1:]:
            contrib_sentence += ", and " + p
        contrib_sentence += "."

    # Why not more extreme
    if balancing and (positive or negative):
        balance_note = f" The score is moderated by {len(balancing)} neutral-range component{'s' if len(balancing) != 1 else ''} that limit conviction."
    elif positive and negative:
        balance_note = " Positive and negative forces are partially offsetting, keeping the composite in a middle range."
    else:
        balance_note = ""

    return (opening + contrib_sentence + balance_note).strip()


def _build_trader_takeaway(
    composite: float,
    regime_label: str,
    positive: list[str],
    negative: list[str],
) -> str:
    """Build a 2-3 sentence practical takeaway for traders."""
    if composite >= 70:
        return (
            "The deterministic engine is firmly constructive. This backdrop supports "
            "premium-selling strategies with a bullish bias — favor bull put spreads and "
            "short strangles on index ETFs. Monitor for complacency signals if macro stress "
            "stays very low."
        )
    if composite >= 55:
        return (
            "The engine leans mildly constructive. Standard premium-selling is reasonable "
            "but keep position sizes moderate. The signal is not strong enough to justify "
            "aggressive directional bets."
        )
    if composite >= 40:
        return (
            "The engine reads neutral with no strong edge in either direction. Iron condors "
            "and balanced strangles are appropriate. Avoid directional bias and keep "
            "positions small until a clearer signal emerges."
        )
    if composite >= 25:
        return (
            "The engine shows mixed readings with notable headwinds. Widen your spreads, "
            "reduce notional exposure, and favor defensive positioning. Consider bear call "
            "spreads if negative contributors dominate."
        )
    return (
        "The engine flags significant market stress. This is not the environment for "
        "aggressive premium selling. Reduce exposure, hedge existing positions, and wait "
        "for conditions to stabilize before re-engaging."
    )
