"""Proof-of-functionality script for Conflict Detector v1.

Scenario 1 — Aligned:   All bullish engines, long stock candidate, bullish model
Scenario 2 — Mixed:     Bull/bear engine split, degraded modules, short premium
                         candidate in cautionary backdrop, bullish model vs bearish market
"""
import json
import sys
sys.path.insert(0, ".")

from app.services.conflict_detector import detect_conflicts
from app.utils.time_horizon import resolve_engine_horizon


# ── Scenario 1: Aligned / Low-conflict ───────────────────────────────

def _aligned_scenario():
    engines = [
        "breadth_participation", "volatility_options", "cross_asset_macro",
        "flows_positioning", "liquidity_financial_conditions", "news_sentiment",
    ]
    market = {}
    for eng in engines:
        h = resolve_engine_horizon(eng)
        market[eng] = {
            "normalized": {
                "engine_key": eng, "score": 72, "label": "Bullish",
                "short_label": "Bullish", "confidence": 85.0,
                "signal_quality": "high", "time_horizon": h,
                "summary": f"{eng} is bullish", "trader_takeaway": "Supportive",
                "bull_factors": ["strong breadth", "positive momentum"],
                "bear_factors": [],
                "risks": [], "regime_tags": ["bullish"],
                "data_quality": {"confidence_score": 85, "signal_quality": "high",
                                 "missing_inputs_count": 0, "warning_count": 0},
                "warnings": [],
                "source_status": {"errors": {}, "proxy_count": 0, "direct_count": 5},
            },
            "source": "normalized",
        }

    candidates = [{
        "normalized": {
            "candidate_id": "cand_SPY", "symbol": "SPY",
            "scanner_key": "stock_momentum_breakout",
            "strategy_family": "stock", "direction": "long",
            "setup_quality": 82, "confidence": 0.85,
            "time_horizon": "swing",
            "data_quality": {"source": "tradier", "missing_fields": []},
        },
    }]

    models = {
        "trade_decision": {
            "normalized": {
                "status": "success", "analysis_type": "trade_decision",
                "summary": "Bullish outlook with supportive market structure.",
                "confidence": 0.85, "time_horizon": "short_term",
                "actions": ["Consider bullish positioning"],
                "metadata": {"label": "Bullish", "score": 78.0},
                "warnings": [],
            },
            "source": "normalized",
        },
    }

    return {
        "market_context": market,
        "candidate_context": {"candidates": candidates, "count": 1},
        "model_context": {"analyses": models, "count": 1},
        "quality_summary": {
            "overall_quality": "good", "average_confidence": 85.0,
            "module_count": 6, "degraded_count": 0,
            "modules": {e: {"confidence": 85, "data_quality_status": "good",
                            "signal_quality": "high", "source": "normalized"}
                        for e in engines},
        },
        "freshness_summary": {
            "overall_freshness": "live", "module_count": 6,
            "modules": {e: {"freshness_status": "live", "last_update": None}
                        for e in engines},
        },
        "horizon_summary": {
            "market_horizons": {e: resolve_engine_horizon(e) for e in engines},
            "candidate_horizons": ["swing"],
            "model_horizons": {"trade_decision": "short_term"},
            "distinct_horizons": ["intraday", "short_term", "swing", "medium_term"],
            "shortest": "intraday", "longest": "medium_term",
        },
    }


# ── Scenario 2: Mixed / Multi-conflict ──────────────────────────────

def _mixed_scenario():
    market = {
        "breadth_participation": {
            "normalized": {
                "engine_key": "breadth_participation", "score": 75,
                "label": "Bullish", "short_label": "Bullish",
                "confidence": 85.0, "signal_quality": "high",
                "time_horizon": "short_term",
                "summary": "Breadth is bullish", "trader_takeaway": "OK",
                "bull_factors": ["strong breadth", "positive momentum", "broadening advances"],
                "bear_factors": [],
                "risks": [], "regime_tags": ["bullish"],
                "data_quality": {"confidence_score": 85, "signal_quality": "high",
                                 "missing_inputs_count": 0, "warning_count": 0},
                "warnings": [],
                "source_status": {"errors": {}, "proxy_count": 0, "direct_count": 5},
            },
            "source": "normalized",
        },
        "volatility_options": {
            "normalized": {
                "engine_key": "volatility_options", "score": 28,
                "label": "Cautionary", "short_label": "Bearish",
                "confidence": 80.0, "signal_quality": "high",
                "time_horizon": "short_term",
                "summary": "Elevated volatility with bearish structure",
                "trader_takeaway": "Reduce premium selling",
                "bull_factors": [],
                "bear_factors": ["elevated VIX", "steep term structure", "negative skew"],
                "risks": ["volatility spike risk"],
                "regime_tags": ["bearish"],
                "data_quality": {"confidence_score": 80, "signal_quality": "high",
                                 "missing_inputs_count": 0, "warning_count": 1},
                "warnings": [],
                "source_status": {"errors": {}, "proxy_count": 0, "direct_count": 5},
            },
            "source": "normalized",
        },
        "cross_asset_macro": {
            "normalized": {
                "engine_key": "cross_asset_macro", "score": 30,
                "label": "Cautionary", "short_label": "Bearish",
                "confidence": 78.0, "signal_quality": "high",
                "time_horizon": "short_term",
                "summary": "Cross-asset divergences signal caution",
                "trader_takeaway": "Risk-off positioning",
                "bull_factors": [],
                "bear_factors": ["bond-equity divergence", "credit widening", "USD strength"],
                "risks": ["macro risk"],
                "regime_tags": ["bearish", "risk_off"],
                "data_quality": {"confidence_score": 78, "signal_quality": "high",
                                 "missing_inputs_count": 0, "warning_count": 0},
                "warnings": [],
                "source_status": {"errors": {}, "proxy_count": 0, "direct_count": 4},
            },
            "source": "normalized",
        },
        "flows_positioning": {
            "normalized": {
                "engine_key": "flows_positioning", "score": 50,
                "label": "Neutral", "short_label": "Neutral",
                "confidence": 0, "signal_quality": "low",
                "time_horizon": "short_term",
                "summary": "Fallback data",
                "trader_takeaway": "", "bull_factors": [], "bear_factors": [],
                "risks": [], "regime_tags": [],
                "data_quality": {"confidence_score": 0, "signal_quality": "low",
                                 "missing_inputs_count": 5, "warning_count": 1},
                "warnings": ["No normalized contract available"],
                "_fallback": True,
            },
            "source": "fallback",
        },
    }

    candidates = [{
        "normalized": {
            "candidate_id": "cand_SPY_pcs", "symbol": "SPY",
            "scanner_key": "put_credit_spread",
            "strategy_family": "options", "direction": "short",
            "setup_quality": 75, "confidence": 0.80,
            "time_horizon": "days_to_expiry",
            "data_quality": {"metrics_ready": True, "missing_fields": [], "warning_count": 0},
            "candidate_metrics": {"pop": 0.72, "expected_value": 15.0},
        },
    }]

    models = {
        "trade_decision": {
            "normalized": {
                "status": "success", "analysis_type": "trade_decision",
                "summary": "Bullish outlook with supportive upside momentum.",
                "confidence": 0.82, "time_horizon": "short_term",
                "actions": ["Consider bullish positioning", "Keep positive exposure"],
                "metadata": {"label": "Bullish", "score": 76.0},
                "warnings": [],
            },
            "source": "normalized",
        },
    }

    return {
        "market_context": market,
        "candidate_context": {"candidates": candidates, "count": 1},
        "model_context": {"analyses": models, "count": 1},
        "quality_summary": {
            "overall_quality": "degraded", "average_confidence": 55.0,
            "module_count": 4, "degraded_count": 1,
            "modules": {
                "breadth_participation": {"confidence": 85, "data_quality_status": "good",
                                          "signal_quality": "high", "source": "normalized"},
                "volatility_options": {"confidence": 80, "data_quality_status": "good",
                                       "signal_quality": "high", "source": "normalized"},
                "cross_asset_macro": {"confidence": 78, "data_quality_status": "good",
                                      "signal_quality": "high", "source": "normalized"},
                "flows_positioning": {"confidence": 0, "data_quality_status": "unknown",
                                      "signal_quality": "low", "source": "fallback"},
            },
        },
        "freshness_summary": {
            "overall_freshness": "stale", "module_count": 4,
            "modules": {
                "breadth_participation": {"freshness_status": "live", "last_update": None},
                "volatility_options": {"freshness_status": "recent", "last_update": None},
                "cross_asset_macro": {"freshness_status": "stale", "last_update": None},
                "flows_positioning": {"freshness_status": "unknown", "last_update": None},
            },
        },
        "horizon_summary": {
            "market_horizons": {
                "breadth_participation": "short_term",
                "volatility_options": "short_term",
                "cross_asset_macro": "short_term",
                "flows_positioning": "short_term",
            },
            "candidate_horizons": ["days_to_expiry"],
            "model_horizons": {"trade_decision": "short_term"},
            "distinct_horizons": ["short_term", "days_to_expiry"],
            "shortest": "short_term", "longest": "short_term",
        },
    }


# ══════════════════════════════════════════════════════════════════════
# Run proofs
# ══════════════════════════════════════════════════════════════════════

print("=" * 72)
print("PROOF 1: ALIGNED / LOW-CONFLICT SCENARIO")
print("=" * 72)
result1 = detect_conflicts(_aligned_scenario())
print(json.dumps(result1, indent=2, default=str))

print()
print("=" * 72)
print("PROOF 2: MIXED / MULTI-CONFLICT SCENARIO")
print("=" * 72)
result2 = detect_conflicts(_mixed_scenario())
print(json.dumps(result2, indent=2, default=str))
