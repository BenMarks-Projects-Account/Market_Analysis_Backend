"""
BenTrade — Stock Strategy Model Analysis Prompt Library

Provides:
  1) STOCK_STRATEGY_SYSTEM_PROMPT — shared system prompt for all stock strategies
  2) build_stock_strategy_user_prompt(strategy_id, candidate) — per-strategy user prompt builder

Each prompt builder extracts the relevant engine metrics and thesis from the
candidate trade shape and structures them for the LLM.

Output contract demanded from the model:
  {
    recommendation: "BUY" | "PASS",
    score: int (0-100),
    confidence: int (0-100),
    summary: string,
    key_drivers: [{ factor, impact, evidence }],
    risk_review: { primary_risks: [], volatility_risk, timing_risk, data_quality_flag },
    engine_vs_model: { engine_score, model_score, agreement, notes: [] },
    data_quality: { provider, warnings: [] },
    timestamp: string
  }
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

# ── Shared System Prompt ───────────────────────────────────────────────────

STOCK_STRATEGY_SYSTEM_PROMPT = """\
You are a short-term stock risk advisor and trading assistant.

CONTEXT:
- Horizon: 1–30 trading days.
- Strategies: long-only equity positions.
- Recommendations: BUY or PASS (never SELL / SHORT / HOLD / WAIT).
- Focus: probability-weighted edge, risk, and timing.

RULES:
1. Never give financial guarantees or definitive predictions.
2. Use ONLY the provided metrics and engine data — do NOT hallucinate
   external news, fundamentals, earnings, or sector data.
3. Your "score" is YOUR independent 0–100 assessment, separate from the engine score.
4. You MUST compare your score to the engine score and explain any disagreement.

CRITICAL FORMATTING RULES:
- Return ONLY raw JSON. No markdown code fences, no backticks, no commentary.
- Start your response with { and end with }. Nothing before or after.
- Every key must be a double-quoted string. Every string value must be double-quoted.
- Do NOT use trailing commas after the last item in arrays or objects.
- All numbers must be numeric literals (not strings). Use null for unknown values.
- If any field is unknown, output null (for scalars) or empty arrays [], but KEEP the key.

OUTPUT JSON SCHEMA (return this exact structure):
{
  "recommendation": "BUY" or "PASS",
  "score": <int 0-100>,
  "confidence": <int 0-100>,
  "summary": "<1-2 sentence thesis>",
  "key_drivers": [
    {"factor": "<name>", "impact": "positive" or "negative" or "neutral", "evidence": "<short detail>"}
  ],
  "risk_review": {
    "primary_risks": ["<risk 1>", "<risk 2>"],
    "volatility_risk": "low" or "medium" or "high",
    "timing_risk": "low" or "medium" or "high",
    "data_quality_flag": null
  },
  "engine_vs_model": {
    "engine_score": <number from input>,
    "model_score": <your score>,
    "agreement": "agree" or "disagree" or "mixed",
    "notes": ["<reasoning about agreement/disagreement>"]
  },
  "data_quality": {
    "provider": "<data source from input>",
    "warnings": []
  }
}

REMEMBER: Raw JSON only. No prose. No fences. Start with { end with }.
"""


# ── Per-Strategy Prompt Builders ───────────────────────────────────────────

def build_stock_strategy_user_prompt(strategy_id: str, candidate: dict[str, Any]) -> str:
    """Dispatch to the correct per-strategy prompt builder.

    Args:
        strategy_id: e.g. 'stock_pullback_swing'
        candidate: full candidate dict from the scanner (includes metrics, thesis, scores).
                   If ``candidate["market_picture_context"]`` is present, it is
                   appended to the prompt payload so the LLM can assess the trade
                   in the context of the full market environment.

    Returns:
        JSON-serialized user prompt string
    """
    builders = {
        "stock_pullback_swing": _build_pullback_swing_prompt,
        "stock_momentum_breakout": _build_momentum_breakout_prompt,
        "stock_mean_reversion": _build_mean_reversion_prompt,
        "stock_volatility_expansion": _build_volatility_expansion_prompt,
    }

    builder = builders.get(strategy_id)
    if builder is None:
        raise ValueError(f"Unknown stock strategy_id for prompt building: {strategy_id}")

    prompt_str = builder(candidate)

    # Augment with Market Picture context if available.
    market_picture = candidate.get("market_picture_context")
    if market_picture:
        prompt_dict = json.loads(prompt_str)
        prompt_dict["market_picture"] = {
            "description": (
                "Current market environment from BenTrade's 6 intelligence modules. "
                "Use this to contextualize your BUY/PASS decision against broader "
                "market conditions."
            ),
            "engines": market_picture,
        }
        prompt_str = json.dumps(prompt_dict, ensure_ascii=False, indent=None)

    return prompt_str


def _safe_get(d: dict, *keys: str, default: Any = None) -> Any:
    """Safely get nested dict values."""
    current = d
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k, default)
        else:
            return default
    return current


def _extract_common_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    """Extract fields shared across all stock strategies."""
    metrics = candidate.get("metrics") or {}
    return {
        "symbol": candidate.get("symbol", ""),
        "price": candidate.get("price"),
        "as_of": candidate.get("as_of", ""),
        "composite_score": candidate.get("composite_score"),
        "score_breakdown": candidate.get("score_breakdown", {}),
        "thesis": candidate.get("thesis", []),
        "data_source": _safe_get(candidate, "data_source", "history", default="unknown"),
        "data_confidence": _safe_get(candidate, "data_source", "confidence", default=None),
    }


# ─────────────────────────────────────────────────────────────────────────
#  A) Pullback Swing
# ─────────────────────────────────────────────────────────────────────────

def _build_pullback_swing_prompt(candidate: dict[str, Any]) -> str:
    """Build user prompt for Pullback Swing strategy.

    Goal: Buy pullbacks in an uptrend, expecting continuation upward.
    """
    common = _extract_common_fields(candidate)
    metrics = candidate.get("metrics") or {}

    payload = {
        "strategy": "stock_pullback_swing",
        "strategy_description": (
            "Pullback Swing: buys healthy pullbacks in confirmed uptrends, "
            "expecting price to resume its upward trajectory. Short-term hold (3-15 days). "
            "Ideal setup: strong trend, modest pullback (-1% to -6% from 20D high), "
            "RSI reset to 40-60, near SMA-20 support."
        ),
        "symbol": common["symbol"],
        "price": common["price"],
        "as_of": common["as_of"],
        "engine": {
            "composite_score": common["composite_score"],
            "score_breakdown": common["score_breakdown"],
            "thesis": common["thesis"],
        },
        "trend_metrics": {
            "trend_state": candidate.get("trend_state") or metrics.get("trend_state"),
            "sma20": metrics.get("sma20"),
            "sma50": metrics.get("sma50"),
            "sma200": metrics.get("sma200"),
            "slope_20": metrics.get("slope_20"),
            "slope_50": metrics.get("slope_50"),
        },
        "pullback_metrics": {
            "pullback_from_20d_high": metrics.get("pullback_from_20d_high"),
            "pullback_from_50d_high": metrics.get("pullback_from_50d_high"),
            "distance_to_sma20": metrics.get("distance_to_sma20"),
            "distance_to_sma50": metrics.get("distance_to_sma50"),
        },
        "momentum_reset": {
            "rsi14": metrics.get("rsi14"),
            "rsi_change_5d": metrics.get("rsi_change_5d"),
        },
        "returns": {
            "return_1d": metrics.get("return_1d"),
            "return_5d": metrics.get("return_5d"),
            "return_20d": metrics.get("return_20d"),
        },
        "liquidity": {
            "avg_vol_20": metrics.get("avg_vol_20"),
            "avg_dollar_vol_20": metrics.get("avg_dollar_vol_20"),
            "today_vol_vs_avg": metrics.get("today_vol_vs_avg"),
        },
        "analysis_questions": [
            "Is this a healthy pullback in a genuine uptrend, or an early breakdown?",
            "Is entry timing good now, or should the trader wait for further pullback or confirmation?",
            "What is the biggest failure mode for this setup?"
        ],
    }

    return json.dumps(payload, ensure_ascii=False, indent=None)


# ─────────────────────────────────────────────────────────────────────────
#  B) Momentum Breakout
# ─────────────────────────────────────────────────────────────────────────

def _build_momentum_breakout_prompt(candidate: dict[str, Any]) -> str:
    """Build user prompt for Momentum Breakout strategy.

    Goal: Enter near breakout through resistance with volume confirmation.
    """
    common = _extract_common_fields(candidate)
    metrics = candidate.get("metrics") or {}

    payload = {
        "strategy": "stock_momentum_breakout",
        "strategy_description": (
            "Momentum Breakout: enters near or just above a breakout through "
            "multi-week resistance (20- or 55-day high), with volume confirmation. "
            "Short-term hold (5-20 days). Ideal setup: tight base / compression, "
            "volume spike on breakout day, RSI 50-70, not too extended from MAs."
        ),
        "symbol": common["symbol"],
        "price": common["price"],
        "as_of": common["as_of"],
        "engine": {
            "composite_score": common["composite_score"],
            "score_breakdown": common["score_breakdown"],
            "thesis": common["thesis"],
        },
        "breakout_metrics": {
            "breakout_state": candidate.get("breakout_state") or metrics.get("breakout_state"),
            "high_20": metrics.get("high_20"),
            "high_55": metrics.get("high_55"),
            "high_252": metrics.get("high_252"),
            "breakout_proximity_55": metrics.get("breakout_proximity_55"),
            "breakout_proximity_20": metrics.get("breakout_proximity_20"),
            "pct_from_52w_high": metrics.get("pct_from_52w_high"),
        },
        "volume_metrics": {
            "vol_spike_ratio": metrics.get("vol_spike_ratio"),
            "avg_dollar_vol_20": metrics.get("avg_dollar_vol_20"),
            "today_vol": metrics.get("today_vol"),
        },
        "compression_metrics": {
            "range_20_pct": metrics.get("range_20_pct"),
            "range_55_pct": metrics.get("range_55_pct"),
            "compression_score": metrics.get("compression_score"),
            "atr_pct": metrics.get("atr_pct"),
        },
        "momentum_metrics": {
            "rsi14": metrics.get("rsi14"),
            "roc_10": metrics.get("roc_10"),
            "roc_20": metrics.get("roc_20"),
            "gap_pct": metrics.get("gap_pct"),
        },
        "trend_metrics": {
            "trend_state": metrics.get("trend_state"),
            "sma20": metrics.get("sma20"),
            "sma50": metrics.get("sma50"),
            "dist_sma20": metrics.get("dist_sma20"),
            "dist_sma50": metrics.get("dist_sma50"),
            "slope_20": metrics.get("slope_20"),
            "slope_50": metrics.get("slope_50"),
        },
        "analysis_questions": [
            "Is this breakout real and sustainable, or likely a fakeout?",
            "Is the stock too extended from its base or moving averages?",
            "Does the volume profile support the breakout conviction?"
        ],
    }

    return json.dumps(payload, ensure_ascii=False, indent=None)


# ─────────────────────────────────────────────────────────────────────────
#  C) Mean Reversion
# ─────────────────────────────────────────────────────────────────────────

def _build_mean_reversion_prompt(candidate: dict[str, Any]) -> str:
    """Build user prompt for Mean Reversion strategy.

    Goal: Oversold bounce with stabilization signal; short holding period.
    """
    common = _extract_common_fields(candidate)
    metrics = candidate.get("metrics") or {}

    payload = {
        "strategy": "stock_mean_reversion",
        "strategy_description": (
            "Mean Reversion: buys oversold stocks showing stabilization signals, "
            "expecting a snapback toward the mean (SMA-20 or SMA-50). "
            "Very short hold (1-7 days). Ideal setup: RSI < 30 or Z-score < -2, "
            "bounce hint (2 consecutive up-days after selloff), atr_pct < 5%."
        ),
        "symbol": common["symbol"],
        "price": common["price"],
        "as_of": common["as_of"],
        "engine": {
            "composite_score": common["composite_score"],
            "score_breakdown": common["score_breakdown"],
            "thesis": common["thesis"],
        },
        "oversold_metrics": {
            "reversion_state": candidate.get("reversion_state") or metrics.get("reversion_state"),
            "rsi14": metrics.get("rsi14"),
            "rsi2": metrics.get("rsi2"),
            "zscore_20": metrics.get("zscore_20"),
        },
        "distance_metrics": {
            "dist_sma20": metrics.get("dist_sma20"),
            "dist_sma50": metrics.get("dist_sma50"),
            "drawdown_20": metrics.get("drawdown_20"),
            "drawdown_55": metrics.get("drawdown_55"),
        },
        "stabilization_metrics": {
            "bounce_hint": metrics.get("bounce_hint"),
            "return_1d": metrics.get("return_1d"),
            "return_2d": metrics.get("return_2d"),
            "return_5d": metrics.get("return_5d"),
            "slope_sma20": metrics.get("slope_sma20"),
            "downtrend_pressure": metrics.get("downtrend_pressure"),
        },
        "volatility_metrics": {
            "atr_pct": metrics.get("atr_pct"),
            "realized_vol_20": metrics.get("realized_vol_20"),
            "range_10_pct": metrics.get("range_10_pct"),
        },
        "liquidity": {
            "avg_vol_20": metrics.get("avg_vol_20"),
            "avg_dollar_vol_20": metrics.get("avg_dollar_vol_20"),
            "vol_spike_ratio": metrics.get("vol_spike_ratio"),
        },
        "analysis_questions": [
            "Is this a bounce setup with genuine stabilization, or a falling knife?",
            "Is there enough snapback room to the mean (SMA-20/50)?",
            "Is the volatility manageable for a short-term mean-reversion hold?"
        ],
    }

    return json.dumps(payload, ensure_ascii=False, indent=None)


# ─────────────────────────────────────────────────────────────────────────
#  D) Volatility Expansion
# ─────────────────────────────────────────────────────────────────────────

def _build_volatility_expansion_prompt(candidate: dict[str, Any]) -> str:
    """Build user prompt for Volatility Expansion strategy.

    Goal: Enter on volatility expansion from a prior compression, with bullish bias.
    """
    common = _extract_common_fields(candidate)
    metrics = candidate.get("metrics") or {}

    payload = {
        "strategy": "stock_volatility_expansion",
        "strategy_description": (
            "Volatility Expansion: identifies stocks transitioning from volatility "
            "compression to expansion, with a bullish directional bias. "
            "Short-to-medium hold (5-20 days). Ideal setup: ATR ratio > 1.3, "
            "Bollinger width expanding, volume spike, close above SMA-20."
        ),
        "symbol": common["symbol"],
        "price": common["price"],
        "as_of": common["as_of"],
        "engine": {
            "composite_score": common["composite_score"],
            "score_breakdown": common["score_breakdown"],
            "thesis": common["thesis"],
        },
        "expansion_metrics": {
            "expansion_state": candidate.get("expansion_state") or metrics.get("expansion_state"),
            "atr_ratio_10": metrics.get("atr_ratio_10"),
            "rv_ratio": metrics.get("rv_ratio"),
            "range_ratio": metrics.get("range_ratio"),
            "atr_pct": metrics.get("atr_pct"),
            "prior_atr_pct": metrics.get("prior_atr_pct"),
        },
        "compression_history": {
            "bb_width_20": metrics.get("bb_width_20"),
            "bb_width_prev": metrics.get("bb_width_prev"),
            "bb_width_rising": metrics.get("bb_width_rising"),
            "bb_width_percentile_180": metrics.get("bb_width_percentile_180"),
            "range_20_pct": metrics.get("range_20_pct"),
            "prior_range_20_pct": metrics.get("prior_range_20_pct"),
        },
        "directional_bias": {
            "bullish_bias": metrics.get("bullish_bias"),
            "close_vs_sma20": metrics.get("close_vs_sma20"),
            "close_vs_sma50": metrics.get("close_vs_sma50"),
            "return_1d": metrics.get("return_1d"),
            "return_2d": metrics.get("return_2d"),
            "return_5d": metrics.get("return_5d"),
            "rsi14": metrics.get("rsi14"),
        },
        "volume_confirmation": {
            "vol_spike_ratio": metrics.get("vol_spike_ratio"),
            "avg_dollar_vol_20": metrics.get("avg_dollar_vol_20"),
            "today_vol": metrics.get("today_vol"),
        },
        "risk_metrics": {
            "gap_pct": metrics.get("gap_pct"),
            "sma20": metrics.get("sma20"),
            "sma50": metrics.get("sma50"),
            "sma200": metrics.get("sma200"),
        },
        "analysis_questions": [
            "Is the volatility expansion likely to continue, or will it mean-revert quickly?",
            "Is the direction favorable for a long-only position?",
            "What is the risk if the expansion fizzles or reverses?"
        ],
    }

    return json.dumps(payload, ensure_ascii=False, indent=None)
