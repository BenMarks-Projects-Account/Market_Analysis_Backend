#!/usr/bin/env python3
"""Dump all 6 Market Intelligence engine prompts with realistic sample data.

Produces:
  scripts/sample_mi_prompts.txt  — human-readable
  scripts/sample_mi_prompts.json — machine-readable

Each engine produces two messages:
  1. system — the full system prompt (role + scoring guide + JSON schema)
  2. user   — the raw evidence payload (what the LLM actually sees)

Raw evidence uses REAL pillar_scores from the latest market state artifact
and realistic mock raw_inputs matching each engine's 5-pillar structure.
News sentiment uses mock headlines + real macro snapshot.

Source: BenTrade/backend/common/model_analysis.py
"""

import json
import textwrap
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_TXT = SCRIPT_DIR / "sample_mi_prompts.txt"
OUT_JSON = SCRIPT_DIR / "sample_mi_prompts.json"

# ── Real macro snapshot from latest market state (2026-03-19) ──────────
REAL_MACRO_CONTEXT = {
    "vix": 25.09,
    "us_10y_yield": 4.20,
    "us_2y_yield": 3.68,
    "fed_funds_rate": 3.64,
    "oil_wti": 93.39,
    "usd_index": 120.55,
    "yield_curve_spread": 0.52,
}

# ════════════════════════════════════════════════════════════════════════
# 1. NEWS SENTIMENT
# ════════════════════════════════════════════════════════════════════════

NEWS_SYSTEM_PROMPT = (
    "You are the BenTrade Market News Analyst. Analyze the supplied news, sentiment, "
    "macro, and market context and return ONLY valid JSON matching the required schema.\n\n"
    "Your task is to produce an institutional-style market news brief for traders. Focus on:\n"
    "- the dominant headline clusters\n"
    "- the narratives driving risk appetite\n"
    "- what is bullish, bearish, and conflicting\n"
    "- why the final score, label, and confidence are justified\n\n"
    "Rules:\n"
    "- Return JSON only\n"
    "- No markdown\n"
    "- No prose outside JSON\n"
    "- No chain-of-thought\n"
    "- No hidden reasoning\n"
    "- No <think> tags\n"
    "- No filler language\n"
    "- Summarize clusters of news, not random isolated stories\n"
    "- Keep score, label, confidence, and explanations internally consistent\n\n"
    "The summary MUST explicitly answer:\n"
    "- what happened\n"
    "- why markets care\n"
    "- what pushed risk up\n"
    "- what pushed risk down\n"
    "- what the trader should do with the information\n\n"
    "Scoring guide:\n"
    "- 0-20 = strongly bearish / risk-off\n"
    "- 21-40 = bearish\n"
    "- 41-59 = mixed / conflicted\n"
    "- 60-79 = constructive / mildly bullish\n"
    "- 80-100 = strongly bullish / risk-on\n\n"
    "If evidence conflicts:\n"
    "- use MIXED or NEUTRAL when appropriate\n"
    "- lower confidence\n"
    "- explicitly include offsetting factors and uncertainty flags\n\n"
    "Required JSON schema (return EXACTLY this shape):\n"
    "{\n"
    '  "label": "BULLISH | BEARISH | MIXED | NEUTRAL | RISK-OFF | RISK-ON",\n'
    '  "score": <float 0-100>,\n'
    '  "confidence": <float 0-1>,\n'
    '  "tone": "<string>",\n'
    '  "summary": "<2-4 sentence executive market brief>",\n'
    '  "headline_drivers": [\n'
    '    {"theme": "<short theme title>", "impact": "bullish|bearish|mixed|neutral", '
    '"strength": <1-5>, "explanation": "<why this theme matters>"}\n'
    "  ],\n"
    '  "major_headlines": [\n'
    '    {"headline": "<cleaned headline>", '
    '"category": "macro|geopolitics|rates|commodities|earnings|sector|policy|sentiment", '
    '"market_impact": "bullish|bearish|mixed|neutral", '
    '"why_it_matters": "<1-2 sentence explanation>"}\n'
    "  ],\n"
    '  "score_drivers": {\n'
    '    "bullish_factors": ["<specific factor>"],\n'
    '    "bearish_factors": ["<specific factor>"],\n'
    '    "offsetting_factors": ["<specific balancing/conflicting factor>"]\n'
    "  },\n"
    '  "market_implications": {\n'
    '    "equities": "<brief interpretation>",\n'
    '    "volatility": "<brief interpretation>",\n'
    '    "rates": "<brief interpretation>",\n'
    '    "energy_or_commodities": "<brief interpretation>",\n'
    '    "sector_rotation": "<brief interpretation>"\n'
    "  },\n"
    '  "uncertainty_flags": ["<uncertainty or conflict in the signal>"],\n'
    '  "trader_takeaway": "<2-4 sentence practical trader takeaway>"\n'
    "}\n\n"
    "Do not include any keys beyond this schema."
)

NEWS_USER_DATA = {
    "headlines": [
        {"source": "Reuters", "headline": "Fed holds rates steady, signals patience on next move", "category": "macro", "published_at": "2026-03-19T02:30:00Z", "symbols": ["SPY", "QQQ"]},
        {"source": "Bloomberg", "headline": "US 10-year yield steady at 4.20% as Treasury supply digested smoothly", "category": "rates", "published_at": "2026-03-19T01:15:00Z", "symbols": []},
        {"source": "CNBC", "headline": "Oil surges past $93 on OPEC+ production cut extension", "category": "commodities", "published_at": "2026-03-18T22:00:00Z", "symbols": ["XLE", "USO"]},
        {"source": "Reuters", "headline": "China PMI contracts for second month, raising global demand fears", "category": "macro", "published_at": "2026-03-18T21:00:00Z", "symbols": ["FXI", "EEM"]},
        {"source": "MarketWatch", "headline": "NVIDIA reports in-line Q4, guidance disappoints on export controls", "category": "earnings", "published_at": "2026-03-18T20:30:00Z", "symbols": ["NVDA", "SMH"]},
        {"source": "Bloomberg", "headline": "Dollar index hits 120.5, highest since 2001 — weighs on multinationals", "category": "macro", "published_at": "2026-03-18T19:45:00Z", "symbols": ["UUP", "EFA"]},
        {"source": "Financial Times", "headline": "European banks rally on ECB pivot hopes after soft inflation print", "category": "sector", "published_at": "2026-03-18T18:00:00Z", "symbols": ["EUFN"]},
        {"source": "Reuters", "headline": "US jobless claims tick higher to 225K, still historically low", "category": "macro", "published_at": "2026-03-18T14:30:00Z", "symbols": ["SPY"]},
        {"source": "CNBC", "headline": "Retail sales beat expectations, consumer spending remains resilient", "category": "macro", "published_at": "2026-03-18T14:00:00Z", "symbols": ["XRT", "XLY"]},
        {"source": "Bloomberg", "headline": "Credit spreads tighten as IG OAS hits 92bps — risk appetite intact", "category": "rates", "published_at": "2026-03-18T12:00:00Z", "symbols": ["LQD", "HYG"]},
        {"source": "MarketWatch", "headline": "VIX spikes to 25 on tariff uncertainty, options premiums elevated", "category": "sentiment", "published_at": "2026-03-18T16:00:00Z", "symbols": ["VIX", "UVXY"]},
        {"source": "Reuters", "headline": "White House signals new tariff review on semiconductor imports", "category": "geopolitics", "published_at": "2026-03-18T15:00:00Z", "symbols": ["SMH", "INTC", "TSM"]},
    ],
    "headline_count": 12,
    "macro_snapshot": REAL_MACRO_CONTEXT,
}


# ════════════════════════════════════════════════════════════════════════
# 2. BREADTH & PARTICIPATION
# ════════════════════════════════════════════════════════════════════════

BREADTH_SYSTEM_PROMPT = (
    "You are the BenTrade Breadth & Participation Analyst. Analyze the supplied "
    "market breadth data and return ONLY valid JSON matching the required schema.\n\n"
    "Your task is to produce an institutional-style market breadth assessment for "
    "options traders. Focus on:\n"
    "- Whether the rally/sell-off is broad or narrow\n"
    "- Whether participation is expanding or contracting\n"
    "- What the advance/decline, volume, trend, and leadership data says about conviction\n"
    "- How breadth conditions affect risk for income-style options strategies\n"
    "- Whether breadth supports or undermines the current price trend\n\n"
    "Rules:\n"
    "- Return JSON only\n"
    "- No markdown\n"
    "- No prose outside JSON\n"
    "- No chain-of-thought or <think> tags\n"
    "- Keep score, label, confidence, and explanations internally consistent\n\n"
    "The summary MUST explicitly answer:\n"
    "- Is the market rally/decline broadly supported or driven by a few names?\n"
    "- Are trend and volume confirming or diverging?\n"
    "- What does leadership quality tell us about sustainability?\n"
    "- What should a risk-defined options trader do with this information?\n\n"
    "Scoring guide:\n"
    "- 0-20 = extremely narrow / deteriorating breadth\n"
    "- 21-40 = weak / selective participation\n"
    "- 41-59 = mixed / transitional breadth\n"
    "- 60-79 = constructive / broadening participation\n"
    "- 80-100 = strong / robust broad rally\n\n"
    "Label options: BROAD_RALLY | NARROW_RALLY | DETERIORATING | WEAK | "
    "RECOVERING | MIXED | STRONG\n\n"
    "Required JSON schema (return EXACTLY this shape):\n"
    "{\n"
    '  "label": "BROAD_RALLY | NARROW_RALLY | DETERIORATING | WEAK | RECOVERING | MIXED | STRONG",\n'
    '  "score": <float 0-100>,\n'
    '  "confidence": <float 0-1>,\n'
    '  "summary": "<2-4 sentence executive breadth brief>",\n'
    '  "pillar_analysis": {\n'
    '    "participation": "<interpretation of A/D data, pct advancing, new highs/lows>",\n'
    '    "trend": "<interpretation of MA breadth — pct above 200/50/20 DMA>",\n'
    '    "volume": "<interpretation of up/down volume balance>",\n'
    '    "leadership": "<interpretation of EW vs CW, outperformance, sector dispersion>",\n'
    '    "stability": "<interpretation of breadth consistency and mean reversion risk>"\n'
    "  },\n"
    '  "breadth_drivers": {\n'
    '    "constructive_factors": ["<factor supporting breadth>"],\n'
    '    "warning_factors": ["<factor weakening breadth>"],\n'
    '    "conflicting_factors": ["<signal conflict or divergence>"]\n'
    "  },\n"
    '  "market_implications": {\n'
    '    "directional_bias": "<bullish/bearish/neutral lean from breadth>",\n'
    '    "position_sizing": "<recommendation on sizing given breadth>",\n'
    '    "strategy_recommendation": "<which options strategies breadth supports>",\n'
    '    "risk_level": "<low/moderate/elevated/high>",\n'
    '    "sector_tilt": "<sectors breadth favors or warns against>"\n'
    "  },\n"
    '  "uncertainty_flags": ["<data gap, divergence, or low-confidence area>"],\n'
    '  "trader_takeaway": "<2-4 sentence practical trader takeaway for options income strategies>"\n'
    "}\n\n"
    "Do not include any keys beyond this schema."
)

BREADTH_USER_DATA = {
    "raw_inputs": {
        "participation": {
            "advance_decline_ratio": 0.62,
            "pct_advancing": 31.2,
            "pct_declining": 50.3,
            "new_highs": 18,
            "new_lows": 142,
            "net_new_highs": -124,
            "advance_decline_line_5d_slope": -0.38,
        },
        "trend": {
            "pct_above_200dma": 28.5,
            "pct_above_50dma": 32.1,
            "pct_above_20dma": 19.4,
            "short_term_trend_score": 32,
            "intermediate_trend_score": 19,
            "long_term_trend_score": 0,
        },
        "volume": {
            "up_down_volume_ratio": 0.41,
            "pct_above_avg_volume": 22.5,
            "volume_thrust_indicator": -0.35,
        },
        "leadership": {
            "equal_weight_vs_cap_weight_spread": -3.8,
            "pct_stocks_outperforming_index": 24.6,
            "sector_dispersion": 0.72,
            "top_5_concentration_pct": 38.1,
        },
        "stability": {
            "breadth_consistency_5d": 0.42,
            "breadth_mean_reversion_zscore": -1.1,
            "breadth_volatility_5d": 0.28,
        },
    },
    "pillar_scores": {
        "participation_breadth": 26.97,
        "trend_breadth": 17.32,
        "volume_breadth": 7.62,
        "leadership_quality": 27.07,
        "participation_stability": 40.83,
    },
    "pillar_weights": {
        "participation_breadth": 0.25,
        "trend_breadth": 0.25,
        "volume_breadth": 0.20,
        "leadership_quality": 0.20,
        "participation_stability": 0.10,
    },
    "universe": {
        "total_stocks": 503,
        "coverage_pct": 99.6,
        "data_source": "derived_from_sp500",
    },
    "warnings": [],
    "missing_inputs": [],
}


# ════════════════════════════════════════════════════════════════════════
# 3. VOLATILITY & OPTIONS STRUCTURE
# ════════════════════════════════════════════════════════════════════════

VOL_SYSTEM_PROMPT = (
    "You are the BenTrade Volatility & Options Structure Analyst. Analyze the "
    "supplied volatility data and return ONLY valid JSON matching the required schema.\n\n"
    "Your task is to produce an institutional-style volatility assessment for "
    "options income traders. Focus on:\n"
    "- What the VIX level, trend, and regime tell us about market fear\n"
    "- Whether term structure (contango/backwardation) supports premium selling\n"
    "- What IV vs realized vol says about option pricing (overpriced = sell, cheap = buy)\n"
    "- How skew and tail risk signals affect strategy selection\n"
    "- Which specific strategies are best suited to current conditions\n\n"
    "Rules:\n"
    "- Return JSON only\n"
    "- No markdown\n"
    "- No prose outside JSON\n"
    "- No chain-of-thought or <think> tags\n"
    "- Keep score, label, confidence, and explanations internally consistent\n\n"
    "Scoring guide (higher = more favorable for premium selling):\n"
    "- 0-20 = extreme vol stress / crisis\n"
    "- 21-40 = elevated risk / defensive\n"
    "- 41-59 = mixed / transitional\n"
    "- 60-79 = constructive for selling\n"
    "- 80-100 = strongly favorable for premium selling\n\n"
    "Label options: PREMIUM_SELLING_FAVORED | CONSTRUCTIVE | MIXED | FRAGILE | "
    "RISK_ELEVATED | VOL_STRESS | DEFENSIVE\n\n"
    "Required JSON schema (return EXACTLY this shape):\n"
    "{\n"
    '  "label": "PREMIUM_SELLING_FAVORED | CONSTRUCTIVE | MIXED | FRAGILE | '
    'RISK_ELEVATED | VOL_STRESS | DEFENSIVE",\n'
    '  "score": <float 0-100>,\n'
    '  "confidence": <float 0-1>,\n'
    '  "summary": "<2-4 sentence executive volatility brief>",\n'
    '  "pillar_analysis": {\n'
    '    "volatility_regime": "<VIX level, trend, IV rank interpretation>",\n'
    '    "volatility_structure": "<term structure shape, IV vs RV assessment>",\n'
    '    "tail_risk_skew": "<skew and tail risk assessment>",\n'
    '    "positioning_options_posture": "<put/call ratios, option richness>",\n'
    '    "strategy_suitability": "<which strategies current conditions favor>"\n'
    "  },\n"
    '  "vol_drivers": {\n'
    '    "favorable_factors": ["<factor supporting premium selling>"],\n'
    '    "warning_factors": ["<factor creating risk>"],\n'
    '    "conflicting_factors": ["<signal conflict or divergence>"]\n'
    "  },\n"
    '  "strategy_implications": {\n'
    '    "premium_selling": "<iron condors, credit spreads assessment>",\n'
    '    "directional": "<debit spreads, long straddles assessment>",\n'
    '    "vol_structure": "<calendars, diagonals assessment>",\n'
    '    "hedging": "<protective puts, collars assessment>",\n'
    '    "position_sizing": "<sizing recommendation given vol conditions>",\n'
    '    "risk_level": "<low/moderate/elevated/high>"\n'
    "  },\n"
    '  "uncertainty_flags": ["<data gap, divergence, or low-confidence area>"],\n'
    '  "trader_takeaway": "<2-4 sentence practical takeaway for options income traders>"\n'
    "}\n\n"
    "Do not include any keys beyond this schema."
)

VOL_USER_DATA = {
    "raw_inputs": {
        "regime": {
            "vix_current": 25.09,
            "vix_previous_close": 22.37,
            "vix_5d_avg": 21.8,
            "vix_20d_avg": 19.6,
            "vix_percentile_1y": 78,
            "iv_rank_30d": 72,
            "iv_percentile_1y": 68,
        },
        "structure": {
            "vix_term_structure_slope": -0.08,
            "vix_9d_vs_30d_ratio": 1.04,
            "iv_30d": 24.5,
            "rv_20d": 18.2,
            "iv_rv_spread": 6.3,
        },
        "skew": {
            "put_call_skew_25d": 5.8,
            "skew_change_5d": 1.2,
            "cboe_skew": None,
        },
        "positioning": {
            "put_call_ratio_equity": 0.82,
            "put_call_ratio_index": 1.35,
            "total_put_volume": 12400000,
            "total_call_volume": 9200000,
        },
        "strategy": {
            "iron_condor_suitability": 58,
            "credit_spread_suitability": 62,
            "calendar_suitability": 71,
            "debit_spread_suitability": 45,
        },
    },
    "pillar_scores": {
        "volatility_regime": 46.71,
        "volatility_structure": 63.79,
        "tail_risk_skew": 71.70,
        "positioning_options_posture": 65.79,
        "strategy_suitability": 55.63,
    },
    "pillar_weights": {
        "volatility_regime": 0.25,
        "volatility_structure": 0.25,
        "tail_risk_skew": 0.20,
        "positioning_options_posture": 0.15,
        "strategy_suitability": 0.15,
    },
    "strategy_scores": {
        "iron_condor": 58,
        "credit_spread": 62,
        "calendar": 71,
        "debit_spread": 45,
    },
    "warnings": ["cboe_skew data unavailable"],
    "missing_inputs": ["cboe_skew"],
}


# ════════════════════════════════════════════════════════════════════════
# 4. CROSS-ASSET & MACRO CONFIRMATION
# ════════════════════════════════════════════════════════════════════════

CROSS_ASSET_SYSTEM_PROMPT = (
    "You are the BenTrade Cross-Asset & Macro Confirmation Analyst. Analyze the supplied "
    "cross-asset macro data and return ONLY valid JSON matching the required schema.\n\n"
    "Your job: Determine whether non-equity markets (rates, credit, commodities, currencies) "
    "are confirming or contradicting the equity story.\n\n"
    "REQUIRED JSON SCHEMA:\n"
    "{\n"
    '  "label": "STRONG CONFIRMATION|CONFIRMING|PARTIAL CONFIRMATION|MIXED SIGNALS|PARTIAL CONTRADICTION|STRONG CONTRADICTION",\n'
    '  "score": <number 0-100>,\n'
    '  "confidence": <number 0.0-1.0>,\n'
    '  "summary": "<2-3 sentence macro assessment>",\n'
    '  "pillar_analysis": {\n'
    '    "rates_yield_curve": "<interpretation>",\n'
    '    "dollar_commodity": "<interpretation>",\n'
    '    "credit_risk_appetite": "<interpretation>",\n'
    '    "defensive_vs_growth": "<interpretation>",\n'
    '    "macro_coherence": "<interpretation>"\n'
    "  },\n"
    '  "macro_drivers": {\n'
    '    "confirming_factors": ["<factor1>", ...],\n'
    '    "contradicting_factors": ["<factor1>", ...],\n'
    '    "ambiguous_factors": ["<factor1>", ...]\n'
    "  },\n"
    '  "trading_implications": {\n'
    '    "directional_bias": "<bullish|neutral|bearish>",\n'
    '    "position_sizing": "<full|reduced|minimal>",\n'
    '    "strategy_recommendation": "<specific guidance>",\n'
    '    "risk_level": "<low|moderate|elevated|high>",\n'
    '    "hedging_guidance": "<specific guidance>"\n'
    "  },\n"
    '  "uncertainty_flags": ["<flag1>", ...],\n'
    '  "trader_takeaway": "<one actionable paragraph>"\n'
    "}\n\n"
    "SCORING GUIDE:\n"
    "  85-100 = Strong Confirmation — most cross-asset signals confirm equities\n"
    "  70-84  = Confirming — clear majority confirms\n"
    "  55-69  = Partial Confirmation — more confirm than contradict\n"
    "  45-54  = Mixed Signals — roughly split\n"
    "  30-44  = Partial Contradiction — more contradict than confirm\n"
    "  0-29   = Strong Contradiction — most signals contradict equities\n\n"
    "IMPORTANT: Base your analysis on the RAW DATA provided. Do not invent data points.\n\n"
    "DATA SOURCE AWARENESS (proxy honesty):\n"
    "  - Copper price (FRED PCOPPUSDM) is a MONTHLY series. It may be up to 30 days stale.\n"
    "    Do not treat it as confirming/contradicting real-time moves.\n"
    "  - Gold (FRED GOLDAMGBD228NLBM) has a ~1 business day delay.\n"
    "  - Credit spreads (IG OAS, HY OAS) have a 1-2 business day delay.\n"
    "  - USD index is a trade-weighted broad index proxy, not DXY.\n"
    "  - Oil (WTI) is inherently ambiguous: declining oil can mean supply glut (neutral)\n"
    "    OR demand destruction (bearish). If oil is between $45\u2013$85, treat it as ambiguous\n"
    "    rather than forcing a directional interpretation.\n"
    "  - If data is missing or stale, reflect that as LOWER confidence, not as a zero value."
)

CROSS_ASSET_USER_DATA = {
    "raw_inputs": {
        "rates": {
            "us_10y_yield": 4.20,
            "us_2y_yield": 3.68,
            "yield_curve_spread": 0.52,
            "fed_funds_rate": 3.64,
            "yield_curve_shape": "normal",
            "rate_trend_direction": "easing",
        },
        "dollar_commodity": {
            "usd_index": 120.55,
            "usd_change_5d_pct": 0.8,
            "oil_wti": 93.39,
            "gold_price": 2980.50,
            "copper_price": 4.12,
            "copper_freshness": "monthly_delayed",
        },
        "credit": {
            "ig_oas": 0.92,
            "hy_oas": 3.22,
            "ig_oas_change_5d": -0.03,
            "hy_oas_change_5d": -0.08,
            "credit_trend": "tightening",
        },
        "defensive_growth": {
            "utilities_vs_tech_ratio_5d": 1.02,
            "staples_vs_discretionary_ratio_5d": 0.98,
        },
        "coherence": {
            "cross_asset_agreement_pct": 64,
            "signal_conflicts": ["strong_dollar_vs_tight_credit", "oil_surge_ambiguous"],
            "macro_regime_consistency": 0.68,
        },
    },
    "pillar_scores": {
        "rates_yield_curve": 63.45,
        "credit_risk_appetite": 75.18,
        "dollar_commodity": 47.54,
        "defensive_vs_growth": 50.0,
        "macro_coherence": 63.83,
    },
    "pillar_weights": {
        "rates_yield_curve": 0.25,
        "credit_risk_appetite": 0.25,
        "dollar_commodity": 0.20,
        "defensive_vs_growth": 0.15,
        "macro_coherence": 0.15,
    },
    "warnings": [],
    "missing_inputs": [],
}


# ════════════════════════════════════════════════════════════════════════
# 5. FLOWS & POSITIONING
# ════════════════════════════════════════════════════════════════════════

FLOWS_SYSTEM_PROMPT = (
    "You are the BenTrade Flows & Positioning Analyst. Analyze the supplied "
    "positioning and flow data and return ONLY valid JSON matching the required schema.\n\n"
    "Your job: Determine whether current positioning and flows support continuation, "
    "indicate crowding, create squeeze risk, or signal reversal potential.\n\n"
    "REQUIRED JSON SCHEMA:\n"
    "{\n"
    '  "label": "STRONGLY SUPPORTIVE|SUPPORTIVE|MIXED|FRAGILE|REVERSAL RISK|UNSTABLE",\n'
    '  "score": <number 0-100>,\n'
    '  "confidence": <number 0.0-1.0>,\n'
    '  "summary": "<2-3 sentence flows & positioning assessment>",\n'
    '  "pillar_analysis": {\n'
    '    "positioning_pressure": "<interpretation>",\n'
    '    "crowding_stretch": "<interpretation>",\n'
    '    "squeeze_unwind_risk": "<interpretation>",\n'
    '    "flow_direction_persistence": "<interpretation>",\n'
    '    "positioning_stability": "<interpretation>"\n'
    "  },\n"
    '  "flow_drivers": {\n'
    '    "supportive_factors": ["<factor1>", ...],\n'
    '    "risk_factors": ["<factor1>", ...],\n'
    '    "ambiguous_factors": ["<factor1>", ...]\n'
    "  },\n"
    '  "trading_implications": {\n'
    '    "continuation_support": "<strong|moderate|weak|none>",\n'
    '    "reversal_risk": "<low|moderate|elevated|high>",\n'
    '    "position_sizing": "<full|reduced|minimal>",\n'
    '    "strategy_recommendation": "<specific guidance>",\n'
    '    "squeeze_guidance": "<specific guidance>"\n'
    "  },\n"
    '  "uncertainty_flags": ["<flag1>", ...],\n'
    '  "trader_takeaway": "<one actionable paragraph>"\n'
    "}\n\n"
    "SCORING GUIDE:\n"
    "  85-100 = Strongly Supportive Flows — positioning/flows support continuation\n"
    "  70-84  = Supportive Positioning — healthy positioning with room to run\n"
    "  55-69  = Mixed but Tradable — some concerns but tradable\n"
    "  45-54  = Fragile / Crowded — elevated fragility, reduce exposure\n"
    "  30-44  = Reversal Risk Elevated — significant risk of positioning unwind\n"
    "  0-29   = Unstable / Unwind Risk — extreme positioning risk, defensive posture\n\n"
    "IMPORTANT: Base your analysis on the RAW DATA provided. Do not invent data points.\n\n"
    "DATA SOURCE AWARENESS (proxy honesty):\n"
    "  - Phase 1 uses PROXY data derived from VIX and market context, NOT direct\n"
    "    institutional flow feeds, CFTC COT data, or true dealer gamma reports.\n"
    "  - Put/call ratio is a VIX-derived PROXY, not exchange-reported.\n"
    "  - Futures positioning, short interest, systematic allocation, and retail\n"
    "    sentiment are ALL PROXY ESTIMATES from VIX regime heuristics.\n"
    "  - Flow direction, persistence, and follow-through are derived proxies.\n"
    "  - This significantly limits the precision of any positioning assessment.\n"
    "  - Reflect proxy limitations as LOWER confidence, not as confident assessments.\n"
    "  - If data is missing, reflect that as lower confidence and note it explicitly.\n"
    "  - NEVER claim precision that the proxy data cannot support."
)

FLOWS_USER_DATA = {
    "raw_inputs": {
        "positioning": {
            "put_call_ratio_proxy": 0.92,
            "vix_regime_label": "elevated",
            "futures_positioning_proxy": 48,
            "short_interest_proxy": 55,
        },
        "crowding": {
            "crowding_score": 38,
            "systematic_allocation_proxy": 52,
            "retail_sentiment_proxy": 45,
            "concentration_risk": 62,
            "herding_indicator": 0.41,
        },
        "squeeze": {
            "short_squeeze_risk_proxy": 35,
            "gamma_imbalance_proxy": -12,
            "vix_mean_reversion_zscore": 1.8,
            "options_expiry_gamma_risk": 42,
        },
        "flow": {
            "flow_direction_proxy": -0.15,
            "flow_persistence_5d": 0.32,
            "volume_follow_through": 0.48,
            "institutional_proxy_signal": "neutral",
            "etf_flow_direction": "outflow",
        },
        "stability": {
            "positioning_stability_5d": 0.65,
            "flow_volatility_5d": 0.28,
            "regime_transition_risk": 0.35,
            "cross_signal_agreement": 0.58,
            "reversal_probability_proxy": 0.22,
        },
    },
    "pillar_scores": {
        "positioning_pressure": 63.98,
        "crowding_stretch": 71.54,
        "squeeze_unwind_risk": 70.69,
        "flow_direction_persistence": 42.34,
        "positioning_stability": 70.69,
    },
    "pillar_weights": {
        "positioning_pressure": 0.25,
        "crowding_stretch": 0.20,
        "squeeze_unwind_risk": 0.20,
        "flow_direction_persistence": 0.20,
        "positioning_stability": 0.15,
    },
    "warnings": [],
    "missing_inputs": [],
}


# ════════════════════════════════════════════════════════════════════════
# 6. LIQUIDITY & FINANCIAL CONDITIONS
# ════════════════════════════════════════════════════════════════════════

LIQUIDITY_SYSTEM_PROMPT = (
    "You are the BenTrade Liquidity & Financial Conditions Analyst. Analyze the supplied "
    "rates, credit, dollar, and conditions data and return ONLY valid JSON matching the "
    "required schema.\n\n"
    "Your job: Determine whether current liquidity conditions and financial market "
    "plumbing are supportive, neutral, or restrictive for risk assets.\n\n"
    "REQUIRED JSON SCHEMA:\n"
    "{\n"
    '  "label": "STRONGLY SUPPORTIVE|SUPPORTIVE|MIXED|TIGHTENING|RESTRICTIVE|STRESS",\n'
    '  "score": <number 0-100>,\n'
    '  "confidence": <number 0.0-1.0>,\n'
    '  "tone": "<supportive|cautious|neutral|concerned|alarmed>",\n'
    '  "summary": "<2-3 sentence liquidity conditions assessment>",\n'
    '  "pillar_interpretation": {\n'
    '    "rates_policy_pressure": "<interpretation>",\n'
    '    "financial_conditions_tightness": "<interpretation>",\n'
    '    "credit_funding_stress": "<interpretation>",\n'
    '    "dollar_global_liquidity": "<interpretation>",\n'
    '    "liquidity_stability_fragility": "<interpretation>"\n'
    "  },\n"
    '  "liquidity_drivers": {\n'
    '    "supportive_factors": ["<factor1>", ...],\n'
    '    "restrictive_factors": ["<factor1>", ...],\n'
    '    "latent_stress_signals": ["<factor1>", ...]\n'
    "  },\n"
    '  "score_drivers": {\n'
    '    "primary_driver": "<what is driving the score most>",\n'
    '    "secondary_drivers": ["<driver1>", ...]\n'
    "  },\n"
    '  "market_implications": {\n'
    '    "risk_asset_outlook": "<supportive|neutral|headwind|hostile>",\n'
    '    "credit_conditions": "<easy|neutral|tightening|stressed>",\n'
    '    "funding_assessment": "<stable|manageable|strained|stressed>",\n'
    '    "position_sizing": "<full|reduced|minimal|defensive>",\n'
    '    "strategy_recommendation": "<specific guidance>"\n'
    "  },\n"
    '  "uncertainty_flags": ["<flag1>", ...],\n'
    '  "trader_takeaway": "<one actionable paragraph>"\n'
    "}\n\n"
    "SCORING GUIDE:\n"
    "  85-100 = Liquidity Strongly Supportive — conditions ideal for risk\n"
    "  70-84  = Supportive Conditions — favorable rates/credit/funding\n"
    "  55-69  = Mixed but Manageable — some tightening but tradable\n"
    "  45-54  = Neutral / Tightening — headwinds emerging, caution warranted\n"
    "  30-44  = Restrictive Conditions — active tightening, reduce exposure\n"
    "  0-29   = Liquidity Stress — hostile conditions, defensive posture\n\n"
    "IMPORTANT: Base your analysis on the RAW DATA provided. Do not invent data points.\n\n"
    "DATA SOURCE AWARENESS:\n"
    "  - Rate data (2Y, 10Y, Fed Funds, yield curve) is DIRECT from FRED.\n"
    "  - Credit spreads (IG, HY OAS) are DIRECT from FRED when available.\n"
    "  - USD index is DIRECT from FRED.\n"
    "  - VIX is from Tradier/Finnhub/FRED waterfall.\n"
    "  - Financial conditions index is a PROXY composite, NOT a true FCI.\n"
    "  - Funding stress is a PROXY from VIX + fed funds heuristic.\n"
    "  - If data is missing, reflect that as lower confidence.\n"
    "  - NEVER claim precision that the data cannot support."
)

LIQUIDITY_USER_DATA = {
    "raw_inputs": {
        "rates": {
            "us_2y_yield": 3.68,
            "us_10y_yield": 4.20,
            "fed_funds_rate": 3.64,
            "yield_curve_spread": 0.52,
            "rate_trend": "easing",
            "front_end_pressure": "restrictive",
        },
        "conditions": {
            "fci_proxy_score": 57,
            "vix_current": 25.09,
            "credit_rate_supportiveness": 68,
            "broad_tightness_score": 64,
        },
        "credit": {
            "ig_oas": 0.92,
            "hy_oas": 3.22,
            "credit_stress_composite": 71,
            "funding_stress_proxy": 44,
            "breakage_risk_score": 90,
        },
        "dollar": {
            "usd_index": 120.55,
            "dollar_liquidity_pressure": 22,
            "dollar_impact_assessment": "significant_headwind",
        },
        "stability": {
            "cross_pillar_range_pp": 58,
            "conditions_stability_score": 64,
            "fragility_assessment": 55,
            "sudden_stress_risk": 58,
            "support_stress_ratio": "3:1",
        },
    },
    "pillar_scores": {
        "rates_policy_pressure": 51.56,
        "financial_conditions_tightness": 58.10,
        "credit_funding_stress": 75.28,
        "dollar_global_liquidity": 16.88,
        "liquidity_stability_fragility": 46.30,
    },
    "pillar_weights": {
        "rates_policy_pressure": 0.25,
        "financial_conditions_tightness": 0.25,
        "credit_funding_stress": 0.20,
        "dollar_global_liquidity": 0.15,
        "liquidity_stability_fragility": 0.15,
    },
    "warnings": [],
    "missing_inputs": [],
}


# ════════════════════════════════════════════════════════════════════════
# ENGINE REGISTRY
# ════════════════════════════════════════════════════════════════════════

ENGINE_PROMPTS = [
    {
        "engine_key": "news_sentiment",
        "engine_name": "News Sentiment",
        "task_type": "news_sentiment",
        "system_prompt": NEWS_SYSTEM_PROMPT,
        "user_data": NEWS_USER_DATA,
        "model_params": {"max_tokens": 2500, "temperature": 0.0},
        "excluded_fields": [
            "sentiment_score", "sentiment_label", "regime_label",
            "overall_score", "headline_pressure_24h", "headline_pressure_72h",
            "top_narratives", "divergence", "stress_level",
        ],
        "notes": "News engine is unique: takes items+macro_context, not engine_result. "
                 "Headlines capped at 40. Macro snapshot includes VIX/yields/oil/USD/spread.",
    },
    {
        "engine_key": "breadth_participation",
        "engine_name": "Breadth & Participation",
        "task_type": "breadth_participation",
        "system_prompt": BREADTH_SYSTEM_PROMPT,
        "user_data": BREADTH_USER_DATA,
        "model_params": {"max_tokens": 2500, "temperature": 0.0},
        "excluded_fields": [
            "score", "label", "short_label", "summary", "trader_takeaway",
            "positive_contributors", "negative_contributors",
            "conflicting_signals", "confidence_score", "signal_quality",
        ],
        "notes": "5-pillar structure: participation, trend, volume, leadership, stability. "
                 "Includes universe coverage stats and pillar weights.",
    },
    {
        "engine_key": "volatility_options",
        "engine_name": "Volatility & Options Structure",
        "task_type": "volatility_options",
        "system_prompt": VOL_SYSTEM_PROMPT,
        "user_data": VOL_USER_DATA,
        "model_params": {"max_tokens": 2500, "temperature": 0.0},
        "excluded_fields": [
            "score", "label", "short_label", "summary", "trader_takeaway",
            "positive_contributors", "negative_contributors",
            "conflicting_signals", "confidence_score", "signal_quality",
        ],
        "notes": "5-pillar structure: regime, structure, skew, positioning, strategy. "
                 "Also includes strategy_scores for individual strategy suitability.",
    },
    {
        "engine_key": "cross_asset_macro",
        "engine_name": "Cross-Asset & Macro Confirmation",
        "task_type": "cross_asset_macro",
        "system_prompt": CROSS_ASSET_SYSTEM_PROMPT,
        "user_data": CROSS_ASSET_USER_DATA,
        "model_params": {"max_tokens": 2500, "temperature": 0.0},
        "excluded_fields": [
            "score", "label", "short_label", "summary", "trader_takeaway",
            "confirming_signals", "contradicting_signals", "mixed_signals",
            "confidence_score", "signal_quality",
        ],
        "notes": "5-pillar structure: rates, dollar_commodity, credit, defensive_growth, coherence. "
                 "DATA SOURCE AWARENESS block warns about monthly copper, delayed gold/credit, "
                 "ambiguous oil interpretation.",
    },
    {
        "engine_key": "flows_positioning",
        "engine_name": "Flows & Positioning",
        "task_type": "flows_positioning",
        "system_prompt": FLOWS_SYSTEM_PROMPT,
        "user_data": FLOWS_USER_DATA,
        "model_params": {"max_tokens": 2500, "temperature": 0.0},
        "excluded_fields": [
            "score", "label", "short_label", "summary", "trader_takeaway",
            "positive_contributors", "negative_contributors",
            "conflicting_signals", "confidence_score", "signal_quality",
            "strategy_bias",
        ],
        "notes": "5-pillar structure: positioning, crowding, squeeze, flow, stability. "
                 "ALL data is PROXY ESTIMATES from VIX regime heuristics — not direct feeds. "
                 "DATA SOURCE AWARENESS block is critical to prompt behavior.",
    },
    {
        "engine_key": "liquidity_financial_conditions",
        "engine_name": "Liquidity & Financial Conditions",
        "task_type": "liquidity_conditions",
        "system_prompt": LIQUIDITY_SYSTEM_PROMPT,
        "user_data": LIQUIDITY_USER_DATA,
        "model_params": {"max_tokens": 2500, "temperature": 0.0},
        "excluded_fields": [
            "score", "label", "short_label", "summary", "trader_takeaway",
            "positive_contributors", "negative_contributors",
            "conflicting_signals", "confidence_score", "signal_quality",
            "support_vs_stress",
        ],
        "notes": "5-pillar structure: rates, conditions, credit, dollar, stability. "
                 "Mix of DIRECT (FRED rates/credit/USD) and PROXY (FCI, funding stress) data. "
                 "Dollar at 120.55 = significant headwind — unique to this engine.",
    },
]


# ════════════════════════════════════════════════════════════════════════
# OUTPUT GENERATION
# ════════════════════════════════════════════════════════════════════════

def build_txt(engines: list[dict]) -> str:
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("BENTRADE — MARKET INTELLIGENCE ENGINE PROMPTS (SAMPLE)")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("Source: BenTrade/backend/common/model_analysis.py")
    lines.append("")
    lines.append("These are the 6 LLM prompts that GENERATE the Market Picture scores.")
    lines.append("Each engine receives RAW evidence only (no derived scores/labels).")
    lines.append("All engines: temperature=0.0, max_tokens=2500, stream=False.")
    lines.append("")
    lines.append("Pillar scores are REAL from latest market state (2026-03-19).")
    lines.append("Raw inputs are realistic mock data matching each engine's schema.")
    lines.append("=" * 80)
    lines.append("")

    for i, eng in enumerate(engines, 1):
        lines.append("#" * 80)
        lines.append(f"# PROMPT {i}: {eng['engine_name'].upper()}")
        lines.append(f"# Engine key: {eng['engine_key']}")
        lines.append(f"# Task type: {eng['task_type']}")
        lines.append(f"# Model params: {json.dumps(eng['model_params'])}")
        lines.append(f"# Excluded fields (anti-anchoring): {eng['excluded_fields']}")
        lines.append(f"# Notes: {eng['notes']}")
        lines.append("#" * 80)
        lines.append("")

        lines.append("-" * 40)
        lines.append("SYSTEM PROMPT:")
        lines.append("-" * 40)
        lines.append(eng["system_prompt"])
        lines.append("")

        lines.append("-" * 40)
        lines.append("USER DATA (raw evidence payload):")
        lines.append("-" * 40)
        lines.append(json.dumps(eng["user_data"], indent=2, ensure_ascii=False))
        lines.append("")

        lines.append("-" * 40)
        lines.append("FULL PAYLOAD (as sent to model):")
        lines.append("-" * 40)
        payload = {
            "messages": [
                {"role": "system", "content": eng["system_prompt"]},
                {"role": "user", "content": json.dumps(eng["user_data"], ensure_ascii=False)},
            ],
            "max_tokens": eng["model_params"]["max_tokens"],
            "temperature": eng["model_params"]["temperature"],
            "stream": False,
        }
        lines.append(json.dumps(payload, indent=2, ensure_ascii=False))
        lines.append("")
        lines.append("")

    lines.append("=" * 80)
    lines.append("END OF MARKET INTELLIGENCE ENGINE PROMPTS")
    lines.append("=" * 80)
    return "\n".join(lines)


def build_json(engines: list[dict]) -> list[dict]:
    result = []
    for eng in engines:
        entry = {
            "engine_key": eng["engine_key"],
            "engine_name": eng["engine_name"],
            "task_type": eng["task_type"],
            "model_params": eng["model_params"],
            "excluded_fields": eng["excluded_fields"],
            "notes": eng["notes"],
            "system_prompt": eng["system_prompt"],
            "user_data": eng["user_data"],
            "full_payload": {
                "messages": [
                    {"role": "system", "content": eng["system_prompt"]},
                    {"role": "user", "content": json.dumps(eng["user_data"], ensure_ascii=False)},
                ],
                "max_tokens": eng["model_params"]["max_tokens"],
                "temperature": eng["model_params"]["temperature"],
                "stream": False,
            },
        }
        result.append(entry)
    return result


def main():
    # Text output
    txt = build_txt(ENGINE_PROMPTS)
    OUT_TXT.write_text(txt, encoding="utf-8")
    print(f"Wrote {OUT_TXT} ({len(txt):,} chars, {txt.count(chr(10))+1} lines)")

    # JSON output
    data = build_json(ENGINE_PROMPTS)
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    OUT_JSON.write_text(json_str, encoding="utf-8")
    print(f"Wrote {OUT_JSON} ({len(json_str):,} chars)")

    # Summary
    print(f"\n{'='*60}")
    print("ENGINES DUMPED:")
    for i, eng in enumerate(ENGINE_PROMPTS, 1):
        sys_len = len(eng["system_prompt"])
        user_len = len(json.dumps(eng["user_data"]))
        print(f"  {i}. {eng['engine_name']:<40} sys={sys_len:>5} chars  user={user_len:>5} chars")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
