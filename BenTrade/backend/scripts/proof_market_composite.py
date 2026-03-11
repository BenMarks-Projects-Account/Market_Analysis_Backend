"""
Proof script – Market Composite Summary v1
===========================================

Exercises build_market_composite() with several realistic scenarios
and prints the output for visual inspection. Not a test suite — use
``pytest tests/test_market_composite.py`` for automated validation.

Usage:
    cd BenTrade/backend
    python scripts/proof_market_composite.py
"""

import json
import sys
import os

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.market_composite import build_market_composite


def _bull_engine(key, score=75.0, label="Bullish", horizon="short_term"):
    return {
        "normalized": {
            "engine_key": key, "score": score,
            "label": label, "short_label": label,
            "confidence": 85.0, "signal_quality": "high",
            "time_horizon": horizon,
            "bull_factors": ["strong breadth", "positive flows"],
            "bear_factors": [],
        },
    }


def _bear_engine(key, score=25.0, label="Cautionary", horizon="short_term"):
    return {
        "normalized": {
            "engine_key": key, "score": score,
            "label": label, "short_label": label,
            "confidence": 80.0, "signal_quality": "high",
            "time_horizon": horizon,
            "bull_factors": [],
            "bear_factors": ["weak momentum", "rising vol"],
        },
    }


def _neutral_engine(key, score=50.0, horizon="short_term"):
    return {
        "normalized": {
            "engine_key": key, "score": score,
            "label": "Mixed", "short_label": "Mixed",
            "confidence": 65.0, "signal_quality": "medium",
            "time_horizon": horizon,
            "bull_factors": ["some positive"],
            "bear_factors": ["some negative"],
        },
    }


def _assembled(market_ctx, quality_overall="good", freshness_overall="recent",
               shortest="short_term", longest="short_term"):
    return {
        "context_version": "1.0",
        "assembled_at": "2025-06-14T12:00:00+00:00",
        "assembly_status": "complete",
        "assembly_warnings": [],
        "included_modules": list(market_ctx.keys()),
        "missing_modules": [],
        "degraded_modules": [],
        "market_context": market_ctx,
        "candidate_context": {"candidates": [], "count": 0, "scanners": [], "families": []},
        "model_context": {"analyses": {}, "count": 0},
        "quality_summary": {
            "overall_quality": quality_overall,
            "average_confidence": 80.0,
            "module_count": len(market_ctx),
            "degraded_count": 0,
            "modules": {},
        },
        "freshness_summary": {
            "overall_freshness": freshness_overall,
            "module_count": len(market_ctx),
            "modules": {},
        },
        "horizon_summary": {
            "market_horizons": {},
            "candidate_horizons": [],
            "model_horizons": {},
            "distinct_horizons": [shortest] if shortest == longest else [shortest, longest],
            "shortest": shortest,
            "longest": longest,
        },
        "metadata": {},
    }


def _conflict_report(count=0, severity="none"):
    return {
        "status": "conflicts_detected" if count else "clean",
        "detected_at": "2025-06-14T12:00:00+00:00",
        "conflict_count": count,
        "conflict_severity": severity,
        "conflict_summary": f"{count} conflicts at {severity} severity",
        "conflict_flags": [],
        "market_conflicts": [],
        "candidate_conflicts": [],
        "model_conflicts": [],
        "time_horizon_conflicts": [],
        "quality_conflicts": [],
        "metadata": {
            "detector_version": "1.0",
            "engines_inspected": 6,
            "candidates_inspected": 0,
            "models_inspected": 0,
            "degraded_inputs": 0,
        },
    }


def _print_result(title, result):
    print(f"\n{'═' * 70}")
    print(f"  {title}")
    print(f"{'═' * 70}")
    # Print key dimensions
    print(f"  status:          {result['status']}")
    print(f"  market_state:    {result['market_state']}")
    print(f"  support_state:   {result['support_state']}")
    print(f"  stability_state: {result['stability_state']}")
    print(f"  confidence:      {result['confidence']}")
    print(f"  summary:         {result['summary']}")
    print(f"  horizon_span:    {result['metadata']['horizon_span']}")
    # Adjustments
    adj = result["adjustments"]
    if adj["conflict_adjustment"]:
        ca = adj["conflict_adjustment"]
        print(f"  conflict adj:    severity={ca['conflict_severity']}, penalty={ca['confidence_penalty']}")
    if adj["quality_adjustment"]:
        qa = adj["quality_adjustment"]
        print(f"  quality adj:     q_penalty={qa['quality_penalty']}, f_penalty={qa['freshness_penalty']}")
    if adj["horizon_adjustment"]:
        ha = adj["horizon_adjustment"]
        print(f"  horizon adj:     span={ha['span']}, penalty={ha['confidence_penalty']}")
    print()


def main():
    print("Market Composite Summary v1 — Proof Script")
    print("=" * 70)

    # ── Scenario 1: All bullish, perfect data ────────────────────
    asm = _assembled({
        "breadth_participation": _bull_engine("breadth_participation"),
        "volatility_options": _bull_engine("volatility_options"),
        "cross_asset_macro": _bull_engine("cross_asset_macro"),
        "flows_positioning": _bull_engine("flows_positioning"),
        "liquidity_financial_conditions": _bull_engine("liquidity_financial_conditions", horizon="medium_term"),
        "news_sentiment": _bull_engine("news_sentiment", horizon="intraday"),
    })
    result = build_market_composite(asm)
    _print_result("Scenario 1: All Bullish / Good Quality / No Conflicts", result)
    assert result["market_state"] == "risk_on"
    assert result["support_state"] == "supportive"
    assert result["stability_state"] == "orderly"
    assert result["confidence"] == 1.0

    # ── Scenario 2: All bearish, perfect data ────────────────────
    asm = _assembled({
        "breadth_participation": _bear_engine("breadth_participation"),
        "volatility_options": _bear_engine("volatility_options"),
        "cross_asset_macro": _bear_engine("cross_asset_macro"),
        "flows_positioning": _bear_engine("flows_positioning"),
        "liquidity_financial_conditions": _bear_engine("liquidity_financial_conditions"),
        "news_sentiment": _bear_engine("news_sentiment"),
    })
    result = build_market_composite(asm)
    _print_result("Scenario 2: All Bearish / Good Quality / No Conflicts", result)
    assert result["market_state"] == "risk_off"
    assert result["support_state"] == "supportive"
    assert result["confidence"] == 1.0

    # ── Scenario 3: Mixed with moderate conflicts ────────────────
    asm = _assembled({
        "breadth_participation": _bull_engine("breadth_participation"),
        "volatility_options": _bull_engine("volatility_options"),
        "cross_asset_macro": _bull_engine("cross_asset_macro"),
        "flows_positioning": _bear_engine("flows_positioning"),
        "liquidity_financial_conditions": _bear_engine("liquidity_financial_conditions"),
        "news_sentiment": _neutral_engine("news_sentiment"),
    })
    cr = _conflict_report(count=2, severity="moderate")
    result = build_market_composite(asm, conflict_report=cr)
    _print_result("Scenario 3: Mixed Engines / Moderate Conflicts", result)
    assert result["market_state"] == "risk_on"
    assert result["stability_state"] == "noisy"
    assert result["confidence"] < 0.7

    # ── Scenario 4: Degraded quality + stale + high conflicts ────
    asm = _assembled(
        {
            "breadth_participation": _bull_engine("breadth_participation"),
            "volatility_options": _bear_engine("volatility_options"),
            "cross_asset_macro": _neutral_engine("cross_asset_macro"),
        },
        quality_overall="poor",
        freshness_overall="very_stale",
        shortest="intraday",
        longest="long_term",
    )
    cr = _conflict_report(count=5, severity="high")
    result = build_market_composite(asm, conflict_report=cr)
    _print_result("Scenario 4: Worst Case — Poor Quality / Stale / High Conflicts", result)
    assert result["status"] == "degraded"
    assert result["support_state"] == "fragile"
    assert result["stability_state"] == "unstable"
    assert result["confidence"] <= 0.05

    # ── Scenario 5: Lean bullish with wide horizon span ──────────
    asm = _assembled(
        {
            "breadth_participation": _bull_engine("breadth_participation"),
            "volatility_options": _bull_engine("volatility_options"),
            "news_sentiment": _bull_engine("news_sentiment", horizon="intraday"),
            "liquidity_financial_conditions": _bull_engine("liquidity_financial_conditions", horizon="medium_term"),
        },
        shortest="intraday",
        longest="medium_term",
    )
    result = build_market_composite(asm)
    _print_result("Scenario 5: Bullish Lean / Wide Horizon Span", result)
    assert result["market_state"] == "risk_on"
    assert result["adjustments"]["horizon_adjustment"] is not None

    # ── Scenario 6: Empty (no engines) ───────────────────────────
    result = build_market_composite({})
    _print_result("Scenario 6: Empty Context", result)
    assert result["status"] == "insufficient_data"
    assert result["confidence"] == 0.0

    print("\n✓ All 6 proof scenarios passed!\n")


if __name__ == "__main__":
    main()
