"""Regime-strategy alignment classification.

Maps (market_regime × strategy_id) → alignment level so pipelines can
flag candidates that conflict with the current regime.  Phase 1 = flag
only, no rejection.

Alignment levels
----------------
- ``aligned``    — strategy fits the regime well.
- ``neutral``    — no strong signal either way.
- ``misaligned`` — strategy works against the regime direction.
- ``unknown``    — regime not available; cannot classify.
"""

from __future__ import annotations

from typing import Any

# ── Alignment mapping ────────────────────────────────────────────────
# regime_key → {alignment_level: [strategy_ids]}

_REGIME_ALIGNMENT: dict[str, dict[str, list[str]]] = {
    "risk_off": {
        "aligned": ["put_debit", "call_debit"],
        "neutral": [
            "iron_condor", "butterfly_debit", "iron_butterfly",
            "calendar_call_spread", "calendar_put_spread",
            "diagonal_call_spread", "diagonal_put_spread",
            "stock_mean_reversion",
        ],
        "misaligned": [
            "put_credit_spread", "call_credit_spread",
            "stock_pullback_swing", "stock_momentum_breakout",
            "stock_volatility_expansion",
        ],
    },
    "risk_off_caution": {
        "aligned": ["put_debit", "iron_condor", "butterfly_debit"],
        "neutral": [
            "call_debit", "iron_butterfly",
            "calendar_call_spread", "calendar_put_spread",
            "stock_mean_reversion",
        ],
        "misaligned": [
            "put_credit_spread", "call_credit_spread",
            "stock_momentum_breakout",
        ],
    },
    "neutral": {
        "aligned": [
            "iron_condor", "butterfly_debit", "iron_butterfly",
            "calendar_call_spread", "calendar_put_spread",
            "diagonal_call_spread", "diagonal_put_spread",
            "stock_pullback_swing", "stock_mean_reversion",
        ],
        "neutral": [
            "put_credit_spread", "call_credit_spread",
            "put_debit", "call_debit",
            "stock_momentum_breakout", "stock_volatility_expansion",
        ],
        "misaligned": [],
    },
    "risk_on_cautious": {
        "aligned": [
            "put_credit_spread", "call_debit",
            "stock_pullback_swing", "stock_momentum_breakout",
        ],
        "neutral": [
            "iron_condor", "call_credit_spread",
            "calendar_call_spread", "stock_volatility_expansion",
            "stock_mean_reversion",
        ],
        "misaligned": ["put_debit"],
    },
    "risk_on": {
        "aligned": [
            "put_credit_spread", "call_credit_spread", "call_debit",
            "stock_pullback_swing", "stock_momentum_breakout",
            "stock_volatility_expansion",
        ],
        "neutral": [
            "iron_condor", "iron_butterfly",
            "calendar_call_spread", "diagonal_call_spread",
        ],
        "misaligned": ["put_debit"],
    },
}

# Warning messages for misaligned strategies
_MISALIGNMENT_WARNINGS: dict[str, str] = {
    "risk_off": (
        "Premium-selling and bullish strategies are misaligned with "
        "Risk-Off conditions. Consider protective or neutral strategies."
    ),
    "risk_off_caution": (
        "Aggressive premium-selling and momentum strategies carry "
        "elevated risk in cautious risk-off conditions."
    ),
    "risk_on": (
        "Bearish/protective strategies are misaligned with Risk-On "
        "conditions. Consider bullish or neutral strategies."
    ),
    "risk_on_cautious": (
        "Bearish strategies are misaligned with cautious risk-on conditions."
    ),
}

# Aliases for consumer_summary.market_state values
_REGIME_KEY_MAP: dict[str, str] = {
    "bullish": "risk_on",
    "strong_uptrend": "risk_on",
    "risk_on": "risk_on",
    "bearish": "risk_off",
    "correction": "risk_off",
    "risk_off": "risk_off",
    "neutral": "neutral",
    "risk_on_cautious": "risk_on_cautious",
    "cautious_bullish": "risk_on_cautious",
    "risk_off_caution": "risk_off_caution",
    "cautious_bearish": "risk_off_caution",
}


def classify_regime_alignment(
    market_regime: str | None,
    strategy_id: str,
) -> dict[str, Any]:
    """Classify how well a strategy aligns with the current market regime.

    Parameters
    ----------
    market_regime : str | None
        Regime label from consumer_summary.market_state.
    strategy_id : str
        Canonical strategy ID (e.g. ``"put_credit_spread"``).

    Returns
    -------
    dict with ``regime_alignment`` ("aligned"|"neutral"|"misaligned"|"unknown")
    and ``regime_warning`` (str | None).
    """
    if not market_regime:
        return {"regime_alignment": "unknown", "regime_warning": None}

    regime_key = str(market_regime).lower().replace(" ", "_").replace("-", "_")
    regime_key = _REGIME_KEY_MAP.get(regime_key, "neutral")

    alignment_map = _REGIME_ALIGNMENT.get(regime_key, _REGIME_ALIGNMENT["neutral"])

    for level in ("aligned", "neutral", "misaligned"):
        if strategy_id in alignment_map.get(level, []):
            warning = (
                _MISALIGNMENT_WARNINGS.get(regime_key) if level == "misaligned"
                else None
            )
            return {"regime_alignment": level, "regime_warning": warning}

    # Strategy not in any list — default to neutral
    return {"regime_alignment": "neutral", "regime_warning": None}
