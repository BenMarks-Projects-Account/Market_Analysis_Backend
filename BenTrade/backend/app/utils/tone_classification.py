"""
Shared Tone Classification Helpers
====================================

Single source of truth for classifying engine labels and scores into
directional tones (bullish / bearish / neutral / unknown).

Used by:
- ``app.services.conflict_detector``  — conflict detection layer
- ``app.services.market_composite``   — market composite synthesis

Tone vocabulary
---------------
- **bullish**  — label or score indicates upward / risk-on lean
- **bearish**  — label or score indicates downward / risk-off lean
- **neutral**  — label or score indicates no directional bias
- **unknown**  — insufficient data to classify

Keyword sets
------------
Matched case-insensitively against engine ``label`` / ``short_label``.
Compound keywords (e.g. ``risk_off``, ``strongly_favored``) are checked
first as whole strings, then individual tokens are checked.
"""

from __future__ import annotations

from typing import Any

# ── Keyword sets ─────────────────────────────────────────────────────
# Labels we treat as "bullish-leaning" vs "bearish-leaning".
# Matched case-insensitively against the first word of engine short_label
# or the label field itself.

BULLISH_KEYWORDS: frozenset[str] = frozenset({
    "bullish", "favored", "strongly_favored", "supportive",
    "positive", "strong", "expansion", "broadening",
})

BEARISH_KEYWORDS: frozenset[str] = frozenset({
    "bearish", "cautious", "cautionary", "unfavorable",
    "negative", "weak", "contraction", "narrowing",
    "risk_off", "stress", "elevated_risk",
})

NEUTRAL_KEYWORDS: frozenset[str] = frozenset({
    "neutral", "mixed", "moderate", "unclear",
})


# ── Classification helpers ───────────────────────────────────────────

def classify_label(label: str | None) -> str:
    """Classify a label/short_label string as bullish/bearish/neutral/unknown.

    Inputs:
    - ``label`` — raw label string from engine normalized output.

    Formula:
    1. Normalize: lowercase, replace ``-`` and spaces with ``_``.
    2. Check full compound string against keyword sets.
    3. Tokenize on ``_`` and check individual tokens.
    4. Priority: BULLISH > BEARISH > NEUTRAL > unknown.
    """
    if not label:
        return "unknown"
    lower = label.lower().replace("-", "_").replace(" ", "_")
    # Check compound keywords first (e.g. "risk_off", "strongly_favored")
    if lower in BULLISH_KEYWORDS or lower in BEARISH_KEYWORDS or lower in NEUTRAL_KEYWORDS:
        if lower in BULLISH_KEYWORDS:
            return "bullish"
        if lower in BEARISH_KEYWORDS:
            return "bearish"
        return "neutral"
    tokens = {t.strip() for t in lower.split("_") if t}
    if tokens & BULLISH_KEYWORDS:
        return "bullish"
    if tokens & BEARISH_KEYWORDS:
        return "bearish"
    if tokens & NEUTRAL_KEYWORDS:
        return "neutral"
    return "unknown"


def classify_score(score: float | None) -> str:
    """Classify a 0-100 score as bullish/bearish/neutral/unknown.

    Inputs:
    - ``score`` — numeric score from engine normalized output (0–100).

    Thresholds:
    - >= 65 → bullish
    - <= 35 → bearish
    - 36–64 → neutral
    - None → unknown
    """
    if score is None:
        return "unknown"
    if score >= 65:
        return "bullish"
    if score <= 35:
        return "bearish"
    return "neutral"


def engine_tone(norm: dict[str, Any]) -> str:
    """Derive the dominant tone of an engine from its label and score.

    Inputs:
    - ``norm`` — engine normalized output dict.

    Priority: label classification first; score is tiebreaker when
    label is neutral/unknown.

    Returns "bullish", "bearish", "neutral", or "unknown".
    """
    label_class = classify_label(
        norm.get("short_label") or norm.get("label", ""),
    )
    if label_class in ("bullish", "bearish"):
        return label_class
    score_class = classify_score(norm.get("score"))
    if score_class in ("bullish", "bearish"):
        return score_class
    if label_class == "neutral" or score_class == "neutral":
        return "neutral"
    return "unknown"
