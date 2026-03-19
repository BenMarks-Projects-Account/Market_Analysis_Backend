"""
Dump every model prompt in BenTrade populated with realistic sample data.

Prompts covered:
  1. Stock Strategy — Pullback Swing        (stock_strategy_prompts.py)
  2. Stock Strategy — Momentum Breakout     (stock_strategy_prompts.py)
  3. Stock Strategy — Mean Reversion        (stock_strategy_prompts.py)
  4. Stock Strategy — Volatility Expansion  (stock_strategy_prompts.py)
  5. TMC Final Decision                     (tmc_final_decision_prompts.py)
  6. Active Trade Position Review           (routes_active_trades.py)
  7. Decision Prompt Payload                (decision_prompt_payload.py)

Usage:
    cd BenTrade/backend
    python scripts/dump_all_prompts.py
    python scripts/dump_all_prompts.py --out sample_prompts.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

# ensure backend root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.stock_strategy_prompts import (
    STOCK_STRATEGY_SYSTEM_PROMPT,
    build_stock_strategy_user_prompt,
)
from common.tmc_final_decision_prompts import (
    TMC_FINAL_DECISION_SYSTEM_PROMPT,
    build_tmc_final_decision_prompt,
)

# ── Real market picture context from latest MI artifact (Mar 19 2026) ──
MARKET_PICTURE_CONTEXT = {
    "breadth_participation": {
        "score": 22.09,
        "label": "Deteriorating",
        "confidence": 95.0,
        "summary": "Breadth is deteriorating (composite 22/100). Weaknesses: participation breadth.",
        "trader_takeaway": "Internal structure is deteriorating. Broad participation is absent. Defensive posture recommended — consider hedging, reducing exposure, or focusing on highest-conviction setups only.",
        "bull_factors": [],
        "bear_factors": ["Participation Breadth is weak (27/100)", "Trend Breadth is weak (17/100)"],
        "risks": ["[trend_breadth] trend_momentum_long: unavailable", "[volume_breadth] accumulation_distribution_bias: not yet implemented"],
        "regime_tags": ["deteriorating"],
    },
    "volatility_options": {
        "score": 60.18,
        "label": "Mixed but Tradable",
        "confidence": 95.0,
        "summary": "Volatility regime is mixed but tradable (composite 60/100). Strengths: volatility structure. Concerns: volatility regime fragility.",
        "trader_takeaway": "Mixed but tradable conditions. Premium selling is viable but use smaller position sizes and tighter risk management. Watch for vol expansion.",
        "bull_factors": ["Volatility Structure is constructive (64/100)", "Tail Risk Skew is favorable (72/100)"],
        "bear_factors": ["Volatility Regime is fragile (47/100)"],
        "risks": ["[tail_risk_skew] cboe_skew: CBOE SKEW index unavailable", "missing_data: 1 inputs missing (-5.0)"],
        "regime_tags": ["mixed_but_tradable"],
    },
    "cross_asset_macro": {
        "score": 61.24,
        "label": "Partial Confirmation",
        "confidence": 92.0,
        "summary": "Cross-asset confirmation is partial confirmation (composite 61/100). Confirming: credit risk appetite.",
        "trader_takeaway": "Cross-asset signals partially confirm equities. Some markets support the bull case but others are neutral or cautionary. Favor quality setups.",
        "bull_factors": ["Credit Risk Appetite confirms equities (75/100)"],
        "bear_factors": [],
        "risks": ["Copper (PCOPPUSDM) is 77 days stale"],
        "regime_tags": ["partial_confirmation"],
    },
    "flows_positioning": {
        "score": 63.51,
        "label": "Mixed but Tradable",
        "confidence": 70.0,
        "summary": "Flows & positioning composite is mixed but tradable (64/100). Supportive: crowding / stretch. Risk area: flow direction.",
        "trader_takeaway": "Flows are mixed but tradable. Some positioning metrics are supportive while others show early signs of crowding or stress. Stay selective.",
        "bull_factors": ["Crowding / Stretch is supportive (72/100)", "Squeeze / Unwind Risk is supportive (71/100)"],
        "bear_factors": ["Flow Direction & Persistence signals risk (42/100)"],
        "risks": ["Heavy proxy reliance (11 proxy sources) (-8)", "No direct institutional flow data — proxy only (-5)"],
        "regime_tags": ["mixed_but_tradable"],
    },
    "liquidity_financial_conditions": {
        "score": 51.95,
        "label": "Neutral / Tightening",
        "confidence": 84.3,
        "summary": "Liquidity & conditions composite is neutral / tightening (52/100). Supportive: credit & funding stress. Pressure from: dollar / global liquidity.",
        "trader_takeaway": "Conditions are tightening. Rate pressure, credit widening, or dollar strength may be creating headwinds. Reduce position sizing and be more selective.",
        "bull_factors": ["Credit & Funding Stress is supportive (75/100)"],
        "bear_factors": ["Dollar / Global Liquidity signals stress (17/100)"],
        "risks": ["Funding stress is a PROXY (VIX + rate heuristic), not direct measurement", "High cross-pillar disagreement (58pp range) — liquidity picture is fractured"],
        "regime_tags": ["neutral_tightening"],
    },
    "news_sentiment": {
        "score": 62.61,
        "label": "Neutral",
        "confidence": 0,
        "summary": "The engine sees a broadly neutral market backdrop (score: 62.6, regime: Neutral). The strongest positive contributor is negative pressure / risk load, and the largest drag is Macro Stress.",
        "trader_takeaway": "The engine leans mildly constructive. Standard premium-selling is reasonable but keep position sizes moderate. The signal quality is limited.",
        "bull_factors": ["negative_pressure: negative_pressure", "narrative_severity: narrative_severity"],
        "bear_factors": ["macro_stress: macro_stress"],
        "risks": [],
        "regime_tags": ["neutral"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
#  Sample candidates for each strategy
# ═══════════════════════════════════════════════════════════════════════════

PULLBACK_SWING_CANDIDATE = {
    "symbol": "AAPL",
    "price": 178.42,
    "as_of": "2026-03-18T15:30:00+00:00",
    "scanner_key": "stock_pullback_swing",
    "strategy_id": "stock_pullback_swing",
    "direction": "long",
    "composite_score": 82,
    "setup_quality": 82,
    "confidence": 0.75,
    "trend_state": "uptrend",
    "thesis": [
        "Price pulled back 3.2% from 20-day high into SMA-20 support",
        "RSI reset from 68 to 48 — healthy momentum reset",
        "Volume declining on pullback — selling exhaustion pattern",
        "Price holding above rising SMA-50 trend line",
    ],
    "score_breakdown": {
        "trend_score": 22,
        "momentum_score": 20,
        "volatility_score": 18,
        "pullback_score": 15,
        "catalyst_score": 7,
    },
    "data_source": {"history": "tradier", "confidence": 0.95},
    "metrics": {
        "price": 178.42,
        "rsi14": 48.3,
        "rsi2": 22.1,
        "rsi_change_5d": -19.7,
        "sma20": 179.85,
        "sma50": 174.60,
        "sma200": 168.22,
        "slope_20": 0.42,
        "slope_50": 0.28,
        "dist_sma20": -0.80,
        "dist_sma50": 2.19,
        "distance_to_sma20": -0.80,
        "distance_to_sma50": 2.19,
        "roc_10": -1.8,
        "roc_20": 3.2,
        "return_1d": -0.45,
        "return_2d": -1.12,
        "return_5d": -2.85,
        "return_20d": 3.2,
        "atr_pct": 1.65,
        "atr_ratio_10": 1.12,
        "bb_width_20": 0.058,
        "realized_vol_20": 0.22,
        "range_20_pct": 8.4,
        "avg_vol_20": 62500000,
        "avg_dollar_vol_20": 11150000000,
        "today_vol": 48200000,
        "today_vol_vs_avg": 0.77,
        "volume_ratio": 0.77,
        "high_20": 184.31,
        "high_55": 186.50,
        "high_252": 199.62,
        "pullback_from_20d_high": -3.19,
        "pullback_from_50d_high": -4.33,
        "pct_from_52w_high": -10.62,
        "drawdown_20": -3.19,
        "composite_score": 82,
        "trend_state": "uptrend",
    },
    "market_regime": "neutral",
    "risk_environment": "moderate",
    "vix": 19.8,
    "regime_tags": ["breadth_deteriorating", "vol_mixed", "flows_supportive"],
    "support_state": "near_support",
    "supporting_signals": [
        "Price at SMA-20 support zone",
        "RSI reset from overbought — healthy pullback",
        "Volume declining on pullback — no panic selling",
        "Positive earnings estimate revisions last 30d",
    ],
    "risk_flags": [
        "Breadth deteriorating across market (22/100)",
        "VIX elevated at 19.8 — positioning caution",
        "Near-term macro uncertainty (tariffs / FOMC)",
    ],
    "entry_context": "Pullback into SMA-20 support in confirmed uptrend with momentum reset",
    "market_picture_context": MARKET_PICTURE_CONTEXT,
}

MOMENTUM_BREAKOUT_CANDIDATE = {
    "symbol": "NVDA",
    "price": 142.88,
    "as_of": "2026-03-18T15:30:00+00:00",
    "scanner_key": "stock_momentum_breakout",
    "strategy_id": "stock_momentum_breakout",
    "direction": "long",
    "composite_score": 74,
    "setup_quality": 74,
    "confidence": 0.68,
    "thesis": [
        "Breaking through 20-day high on 2.1x volume surge",
        "RSI at 62 — strong but not overbought",
        "Price gapped above SMA-20 with bullish follow-through",
        "Tight 20-day range compression resolved upward",
    ],
    "score_breakdown": {
        "trend_score": 20,
        "momentum_score": 22,
        "volatility_score": 12,
        "breakout_score": 20,
    },
    "data_source": {"history": "tradier", "confidence": 0.95},
    "breakout_state": "breaking_out",
    "metrics": {
        "price": 142.88,
        "rsi14": 62.4,
        "roc_10": 5.2,
        "roc_20": 8.8,
        "gap_pct": 1.4,
        "sma20": 136.50,
        "sma50": 131.20,
        "sma200": 122.80,
        "dist_sma20": 4.67,
        "dist_sma50": 8.90,
        "slope_20": 0.65,
        "slope_50": 0.48,
        "trend_state": "uptrend",
        "high_20": 141.90,
        "high_55": 143.50,
        "high_252": 152.89,
        "breakout_proximity_55": -0.43,
        "breakout_proximity_20": 0.69,
        "pct_from_52w_high": -6.55,
        "vol_spike_ratio": 2.1,
        "today_vol": 95800000,
        "avg_dollar_vol_20": 6520000000,
        "range_20_pct": 5.2,
        "range_55_pct": 14.8,
        "compression_score": 72,
        "atr_pct": 2.35,
        "return_1d": 2.8,
        "return_5d": 4.1,
        "return_20d": 8.8,
    },
    "market_picture_context": MARKET_PICTURE_CONTEXT,
}

MEAN_REVERSION_CANDIDATE = {
    "symbol": "META",
    "price": 485.20,
    "as_of": "2026-03-18T15:30:00+00:00",
    "scanner_key": "stock_mean_reversion",
    "strategy_id": "stock_mean_reversion",
    "direction": "long",
    "composite_score": 71,
    "setup_quality": 71,
    "confidence": 0.62,
    "reversion_state": "oversold_stabilizing",
    "thesis": [
        "RSI-14 at 24 — deeply oversold after 5-day selloff",
        "Z-score at -2.3 standard deviations below 20-day mean",
        "Bounce hint detected: 2 consecutive up-closes after selloff",
        "Price 5.8% below SMA-20 — strong snapback room",
    ],
    "score_breakdown": {
        "trend_score": 8,
        "momentum_score": 22,
        "volatility_score": 18,
        "reversion_score": 23,
    },
    "data_source": {"history": "tradier", "confidence": 0.92},
    "metrics": {
        "price": 485.20,
        "rsi14": 24.1,
        "rsi2": 8.5,
        "zscore_20": -2.31,
        "dist_sma20": -5.82,
        "dist_sma50": -8.40,
        "drawdown_20": -9.1,
        "drawdown_55": -12.6,
        "return_1d": 0.85,
        "return_2d": 1.20,
        "return_5d": -7.80,
        "slope_sma20": -0.18,
        "downtrend_pressure": 0.6,
        "bounce_hint": True,
        "atr_pct": 2.85,
        "realized_vol_20": 0.38,
        "range_10_pct": 11.2,
        "avg_vol_20": 18500000,
        "avg_dollar_vol_20": 8975000000,
        "vol_spike_ratio": 1.8,
        "sma20": 515.10,
        "sma50": 529.70,
    },
    "market_picture_context": MARKET_PICTURE_CONTEXT,
}

VOLATILITY_EXPANSION_CANDIDATE = {
    "symbol": "AMZN",
    "price": 198.45,
    "as_of": "2026-03-18T15:30:00+00:00",
    "scanner_key": "stock_volatility_expansion",
    "strategy_id": "stock_volatility_expansion",
    "direction": "long",
    "composite_score": 68,
    "setup_quality": 68,
    "confidence": 0.60,
    "expansion_state": "expanding",
    "thesis": [
        "ATR ratio 1.55 — vol expanding from prior compression",
        "Bollinger width expanding from 180-day low",
        "1.6x volume surge on expansion day — institutional participation",
        "Price above SMA-20 with bullish directional bias confirmed",
    ],
    "score_breakdown": {
        "trend_score": 16,
        "momentum_score": 14,
        "volatility_score": 22,
        "expansion_score": 16,
    },
    "data_source": {"history": "tradier", "confidence": 0.94},
    "metrics": {
        "price": 198.45,
        "rsi14": 56.8,
        "return_1d": 1.9,
        "return_2d": 2.4,
        "return_5d": 0.6,
        "sma20": 195.30,
        "sma50": 190.80,
        "sma200": 182.50,
        "atr_ratio_10": 1.55,
        "rv_ratio": 1.38,
        "range_ratio": 1.42,
        "atr_pct": 2.10,
        "prior_atr_pct": 1.35,
        "bb_width_20": 0.072,
        "bb_width_prev": 0.038,
        "bb_width_rising": True,
        "bb_width_percentile_180": 15,
        "range_20_pct": 9.8,
        "prior_range_20_pct": 5.2,
        "bullish_bias": True,
        "close_vs_sma20": "above",
        "close_vs_sma50": "above",
        "vol_spike_ratio": 1.6,
        "today_vol": 72400000,
        "avg_dollar_vol_20": 9280000000,
        "gap_pct": 0.8,
    },
    "market_picture_context": MARKET_PICTURE_CONTEXT,
}

# ═══════════════════════════════════════════════════════════════════════════
#  Active Trade Position Review (hardcoded prompt format from routes_active_trades.py)
# ═══════════════════════════════════════════════════════════════════════════

_MODEL_ANALYSIS_SCHEMA = """{
  "headline": "<short attention-grabbing headline>",
  "stance": "HOLD|REDUCE|EXIT|ADD|WATCH",
  "confidence": 0-100,
  "thesis_status": "INTACT|WEAKENING|BROKEN",
  "summary": "<2-3 sentence executive summary>",
  "key_risks": ["<risk 1>", "<risk 2>"],
  "key_supports": ["<support 1>", "<support 2>"],
  "technical_state": {
    "price_vs_sma20": "ABOVE|BELOW|NEAR",
    "price_vs_sma50": "ABOVE|BELOW|NEAR",
    "trend_assessment": "<1 sentence>",
    "drawdown_assessment": "<1 sentence>"
  },
  "action_plan": {
    "primary_action": "<what to do>",
    "urgency": "LOW|MEDIUM|HIGH",
    "next_step": "<concrete next step>",
    "risk_trigger": "<what would force an exit>",
    "upside_trigger": "<what would justify adding>"
  },
  "memo": {
    "thesis_check": "<is original thesis intact?>",
    "what_changed": "<what moved since entry?>",
    "decision": "<clear recommendation>"
  }
}"""

_ACTIVE_TRADE_SYSTEM_MSG = (
    "You are a senior portfolio and risk analyst writing a structured "
    "active-position review for a trading desk UI.\n\n"
    "RULES — follow these exactly:\n"
    "1. Use ONLY the provided position and market context data.\n"
    "2. Do NOT invent catalysts, fundamentals, or news.\n"
    "3. Do NOT output chain-of-thought, reasoning tags, <think> tags, "
    "markdown fences, or any text outside the JSON.\n"
    "4. Return a single valid JSON object using the schema below.\n"
    "5. Be concise, specific, and decision-oriented. "
    "Reference actual price levels, P&L numbers, and indicator values.\n"
    "6. Avoid filler, generic advice, and educational explanations.\n"
    "7. Keep risk/action language specific to the provided data.\n"
    "8. confidence is an integer 0-100, NOT a float.\n"
    "9. stance must be exactly one of: HOLD, REDUCE, EXIT, ADD, WATCH\n"
    "10. thesis_status must be exactly: INTACT, WEAKENING, or BROKEN\n"
    "11. urgency must be exactly: LOW, MEDIUM, or HIGH\n\n"
    "Required JSON schema:\n" + _MODEL_ANALYSIS_SCHEMA
)

_ACTIVE_TRADE_USER_MSG = """Evaluate this active position using ONLY the data below.

POSITION SNAPSHOT
  Symbol: MSFT
  Strategy: single
  Direction: Long
  Quantity: 50
  Avg Entry Price: $412.35
  Current Price: $398.20
  Cost Basis (total): $20617.50
  Market Value: $19910.00
  Unrealized P&L: $-707.50 (-3.4%)
  Day Change: -$2.85

MARKET CONTEXT
  Regime: Neutral (score: 48/100)
  SMA 20-day: $405.60  — Price vs SMA20: BELOW (-1.8%)
  SMA 50-day: $410.25  — Price vs SMA50: BELOW (-2.9%)
  RSI 14-day: 38.2

Produce a concise position review memo as JSON. Be decisive."""

# ═══════════════════════════════════════════════════════════════════════════
#  Decision Prompt Payload (from decision_prompt_payload.py)
# ═══════════════════════════════════════════════════════════════════════════

def _build_decision_payload_sample():
    """Build a sample decision prompt payload using the real builder."""
    try:
        from app.services.decision_prompt_payload import build_prompt_payload
        return build_prompt_payload(
            candidate={
                "symbol": "AAPL",
                "price": 178.42,
                "strategy": "stock_pullback_swing",
                "composite_score": 82,
                "thesis": ["Pullback into SMA-20 support"],
            },
            market={
                "regime_label": "Neutral",
                "regime_score": 48,
                "vix": 19.8,
                "sma20": 179.85,
                "rsi14": 48.3,
            },
        )
    except Exception as e:
        return {"_error": f"Could not build decision payload: {e}"}


# ═══════════════════════════════════════════════════════════════════════════
#  Main — generate all
# ═══════════════════════════════════════════════════════════════════════════

SEPARATOR = "=" * 100
SUB_SEP = "-" * 100


def _fmt_messages(system_prompt: str, user_prompt_str: str, model_params: dict):
    """Pretty-print a messages array."""
    lines = []
    lines.append(f"  Model parameters: {json.dumps(model_params)}")
    lines.append("")
    lines.append(SUB_SEP)
    lines.append("  MESSAGE 1: SYSTEM PROMPT")
    lines.append(SUB_SEP)
    lines.append(system_prompt)
    lines.append("")
    lines.append(SUB_SEP)
    lines.append("  MESSAGE 2: USER PROMPT")
    lines.append(SUB_SEP)
    try:
        parsed = json.loads(user_prompt_str)
        lines.append(json.dumps(parsed, indent=2, ensure_ascii=False))
    except (json.JSONDecodeError, TypeError):
        lines.append(user_prompt_str)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Dump all BenTrade model prompts with sample data")
    parser.add_argument("--out", help="Write JSON output to file instead of stdout")
    args = parser.parse_args()

    all_prompts = []
    output_lines = []

    # ── 1. Stock Strategy: Pullback Swing ──
    user_prompt = build_stock_strategy_user_prompt("stock_pullback_swing", PULLBACK_SWING_CANDIDATE)
    prompt_entry = {
        "prompt_id": "stock_pullback_swing",
        "prompt_name": "Stock Strategy — Pullback Swing",
        "source_file": "common/stock_strategy_prompts.py",
        "model_params": {"temperature": 0.2, "max_tokens": 1500},
        "system_prompt": STOCK_STRATEGY_SYSTEM_PROMPT,
        "user_prompt": json.loads(user_prompt),
    }
    all_prompts.append(prompt_entry)
    output_lines.append(SEPARATOR)
    output_lines.append("  PROMPT 1: STOCK STRATEGY — PULLBACK SWING")
    output_lines.append(f"  Source: common/stock_strategy_prompts.py")
    output_lines.append(SEPARATOR)
    output_lines.append(_fmt_messages(STOCK_STRATEGY_SYSTEM_PROMPT, user_prompt,
                                       {"temperature": 0.2, "max_tokens": 1500}))
    output_lines.append("\n")

    # ── 2. Stock Strategy: Momentum Breakout ──
    user_prompt = build_stock_strategy_user_prompt("stock_momentum_breakout", MOMENTUM_BREAKOUT_CANDIDATE)
    prompt_entry = {
        "prompt_id": "stock_momentum_breakout",
        "prompt_name": "Stock Strategy — Momentum Breakout",
        "source_file": "common/stock_strategy_prompts.py",
        "model_params": {"temperature": 0.2, "max_tokens": 1500},
        "system_prompt": STOCK_STRATEGY_SYSTEM_PROMPT,
        "user_prompt": json.loads(user_prompt),
    }
    all_prompts.append(prompt_entry)
    output_lines.append(SEPARATOR)
    output_lines.append("  PROMPT 2: STOCK STRATEGY — MOMENTUM BREAKOUT")
    output_lines.append(f"  Source: common/stock_strategy_prompts.py")
    output_lines.append(SEPARATOR)
    output_lines.append(_fmt_messages(STOCK_STRATEGY_SYSTEM_PROMPT, user_prompt,
                                       {"temperature": 0.2, "max_tokens": 1500}))
    output_lines.append("\n")

    # ── 3. Stock Strategy: Mean Reversion ──
    user_prompt = build_stock_strategy_user_prompt("stock_mean_reversion", MEAN_REVERSION_CANDIDATE)
    prompt_entry = {
        "prompt_id": "stock_mean_reversion",
        "prompt_name": "Stock Strategy — Mean Reversion",
        "source_file": "common/stock_strategy_prompts.py",
        "model_params": {"temperature": 0.2, "max_tokens": 1500},
        "system_prompt": STOCK_STRATEGY_SYSTEM_PROMPT,
        "user_prompt": json.loads(user_prompt),
    }
    all_prompts.append(prompt_entry)
    output_lines.append(SEPARATOR)
    output_lines.append("  PROMPT 3: STOCK STRATEGY — MEAN REVERSION")
    output_lines.append(f"  Source: common/stock_strategy_prompts.py")
    output_lines.append(SEPARATOR)
    output_lines.append(_fmt_messages(STOCK_STRATEGY_SYSTEM_PROMPT, user_prompt,
                                       {"temperature": 0.2, "max_tokens": 1500}))
    output_lines.append("\n")

    # ── 4. Stock Strategy: Volatility Expansion ──
    user_prompt = build_stock_strategy_user_prompt("stock_volatility_expansion", VOLATILITY_EXPANSION_CANDIDATE)
    prompt_entry = {
        "prompt_id": "stock_volatility_expansion",
        "prompt_name": "Stock Strategy — Volatility Expansion",
        "source_file": "common/stock_strategy_prompts.py",
        "model_params": {"temperature": 0.2, "max_tokens": 1500},
        "system_prompt": STOCK_STRATEGY_SYSTEM_PROMPT,
        "user_prompt": json.loads(user_prompt),
    }
    all_prompts.append(prompt_entry)
    output_lines.append(SEPARATOR)
    output_lines.append("  PROMPT 4: STOCK STRATEGY — VOLATILITY EXPANSION")
    output_lines.append(f"  Source: common/stock_strategy_prompts.py")
    output_lines.append(SEPARATOR)
    output_lines.append(_fmt_messages(STOCK_STRATEGY_SYSTEM_PROMPT, user_prompt,
                                       {"temperature": 0.2, "max_tokens": 1500}))
    output_lines.append("\n")

    # ── 5. TMC Final Decision ──
    tmc_candidate = dict(PULLBACK_SWING_CANDIDATE)
    tmc_candidate.pop("market_picture_context", None)
    user_prompt = build_tmc_final_decision_prompt(tmc_candidate, MARKET_PICTURE_CONTEXT, "stock_pullback_swing")
    prompt_entry = {
        "prompt_id": "tmc_final_decision",
        "prompt_name": "TMC Final Trade Decision",
        "source_file": "common/tmc_final_decision_prompts.py",
        "model_params": {"temperature": 0.0, "max_tokens": 3000},
        "system_prompt": TMC_FINAL_DECISION_SYSTEM_PROMPT,
        "user_prompt": json.loads(user_prompt),
    }
    all_prompts.append(prompt_entry)
    output_lines.append(SEPARATOR)
    output_lines.append("  PROMPT 5: TMC FINAL TRADE DECISION")
    output_lines.append(f"  Source: common/tmc_final_decision_prompts.py")
    output_lines.append(SEPARATOR)
    output_lines.append(_fmt_messages(TMC_FINAL_DECISION_SYSTEM_PROMPT, user_prompt,
                                       {"temperature": 0.0, "max_tokens": 3000}))
    output_lines.append("\n")

    # ── 6. Active Trade Position Review ──
    prompt_entry = {
        "prompt_id": "active_trade_review",
        "prompt_name": "Active Trade Position Review",
        "source_file": "app/api/routes_active_trades.py",
        "model_params": {"temperature": 0.2, "max_tokens": 900},
        "system_prompt": _ACTIVE_TRADE_SYSTEM_MSG,
        "user_prompt": _ACTIVE_TRADE_USER_MSG,
    }
    all_prompts.append(prompt_entry)
    output_lines.append(SEPARATOR)
    output_lines.append("  PROMPT 6: ACTIVE TRADE POSITION REVIEW")
    output_lines.append(f"  Source: app/api/routes_active_trades.py")
    output_lines.append(SEPARATOR)
    output_lines.append(_fmt_messages(_ACTIVE_TRADE_SYSTEM_MSG, _ACTIVE_TRADE_USER_MSG,
                                       {"temperature": 0.2, "max_tokens": 900}))
    output_lines.append("\n")

    # ── 7. Decision Prompt Payload ──
    decision_payload = _build_decision_payload_sample()
    prompt_entry = {
        "prompt_id": "decision_prompt_payload",
        "prompt_name": "Decision Prompt Payload (Orchestrator → Model)",
        "source_file": "app/services/decision_prompt_payload.py",
        "model_params": {"note": "This is a packaging layer — not sent directly to LLM. It structures data for downstream decision calls."},
        "payload": decision_payload,
    }
    all_prompts.append(prompt_entry)
    output_lines.append(SEPARATOR)
    output_lines.append("  PROMPT 7: DECISION PROMPT PAYLOAD (Orchestrator packaging)")
    output_lines.append(f"  Source: app/services/decision_prompt_payload.py")
    output_lines.append(SEPARATOR)
    output_lines.append("  Note: This is a packaging/compression layer, not a direct LLM prompt.")
    output_lines.append("  It structures subsystem data into a stable payload for downstream decisions.")
    output_lines.append("")
    output_lines.append(json.dumps(decision_payload, indent=2, ensure_ascii=False, default=str))
    output_lines.append("\n")

    # ── Output ──
    full_text = "\n".join(output_lines)

    if args.out:
        # Write JSON version
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(all_prompts, f, indent=2, ensure_ascii=False, default=str)
        print(f"Wrote {len(all_prompts)} prompts to {args.out}")
        # Also write readable version
        txt_path = args.out.replace(".json", ".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(full_text)
        print(f"Wrote readable version to {txt_path}")
    else:
        print(full_text)


if __name__ == "__main__":
    main()
