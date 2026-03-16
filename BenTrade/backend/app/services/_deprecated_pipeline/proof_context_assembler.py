"""Proof-of-functionality script for Context Assembler v1 + Time Horizon."""
import json
import sys
sys.path.insert(0, ".")
from app.services.context_assembler import assemble_context
from app.utils.time_horizon import resolve_engine_horizon

# === PROOF 1: Complete Assembly (all 6 modules, 2 candidates, 1 model) ===
ENGINES = [
    "breadth_participation", "volatility_options", "cross_asset_macro",
    "flows_positioning", "liquidity_financial_conditions", "news_sentiment",
]
market = {}
for eng in ENGINES:
    market[eng] = {
        "engine_result": {"score": 72, "label": "bullish", "summary": f"{eng} analysis"},
        "normalized": {
            "engine_name": eng, "score": 72, "label": "bullish",
            "summary": f"{eng} analysis", "confidence": 0.85,
            "data_quality_status": "good", "normalization_version": "1.0",
            "normalized_at": "2025-01-15T10:00:00Z", "input_hash": "abc123",
            "time_horizon": resolve_engine_horizon(eng),
        },
        "dashboard_metadata": {
            "engine_name": eng, "score": 72, "label": "bullish",
            "data_quality_status": "good", "freshness_status": "live",
            "confidence": 0.85, "computed_at": "2025-01-15T10:00:00Z",
        },
    }

candidates = [
    {"normalized": {"symbol": "SPY", "scanner_key": "put_credit_spread", "family": "options",
                    "score": 85, "label": "strong", "normalization_version": "1.0",
                    "time_horizon": "days_to_expiry"}},
    {"normalized": {"symbol": "AAPL", "scanner_key": "stock_momentum", "family": "stock",
                    "score": 70, "label": "moderate", "normalization_version": "1.0",
                    "time_horizon": "swing"}},
]

model_payloads = {
    "trade_decision": {
        "normalized": {"analysis_type": "trade_decision", "status": "success",
                       "response_format": "structured", "normalization_version": "1.0",
                       "time_horizon": "short_term"},
    },
}

result = assemble_context(market_payloads=market, candidates=candidates, model_payloads=model_payloads)
print("=== PROOF 1: COMPLETE ASSEMBLY ===")
proof1 = {k: result[k] for k in ["context_version", "assembly_status", "assembly_warnings",
    "included_modules", "missing_modules", "degraded_modules"]}
print(json.dumps(proof1, indent=2))
print(f"  market_context engines: {list(result['market_context'].keys())}")
print(f"  candidate count: {result['candidate_context']['count']}")
print(f"  model count: {len(result['model_context']['analyses'])}")
print(f"  quality: {result['quality_summary']}")
print(f"  freshness: {result['freshness_summary']}")
print(f"  horizon_summary: {json.dumps(result['horizon_summary'], indent=4)}")

# === PROOF 2: Degraded Assembly ===
market2 = {}
for eng in ["breadth_participation", "volatility_options"]:
    market2[eng] = {
        "engine_result": {"score": 60, "label": "neutral"},
        "normalized": {"engine_name": eng, "score": 60, "label": "neutral",
                       "confidence": 0.7, "data_quality_status": "good",
                       "normalization_version": "1.0", "normalized_at": "2025-01-15T10:00:00Z",
                       "time_horizon": resolve_engine_horizon(eng)},
        "dashboard_metadata": {"engine_name": eng, "data_quality_status": "good",
                               "freshness_status": "recent", "confidence": 0.7},
    }
for eng in ["cross_asset_macro", "flows_positioning"]:
    market2[eng] = {
        "engine_result": {"score": 45, "label": "bearish"},
    }

candidates2 = [{"symbol": "QQQ", "score": 55, "label": "weak"}]  # legacy
model2 = {"outlook": {"response": "some text", "analysis_type": "outlook"}}  # legacy

result2 = assemble_context(market_payloads=market2, candidates=candidates2, model_payloads=model2)
print()
print("=== PROOF 2: DEGRADED ASSEMBLY ===")
proof2 = {k: result2[k] for k in ["context_version", "assembly_status", "assembly_warnings",
    "included_modules", "missing_modules", "degraded_modules"]}
print(json.dumps(proof2, indent=2))
print(f"  market_context engines: {list(result2['market_context'].keys())}")
print(f"  candidate count: {result2['candidate_context']['count']}")
print(f"  fallback markers: {[c.get('_fallback') for c in result2['candidate_context']['candidates']]}")
print(f"  model count: {len(result2['model_context']['analyses'])}")
print(f"  quality: {result2['quality_summary']}")
print(f"  freshness: {result2['freshness_summary']}")
print(f"  horizon_summary: {json.dumps(result2['horizon_summary'], indent=4)}")
