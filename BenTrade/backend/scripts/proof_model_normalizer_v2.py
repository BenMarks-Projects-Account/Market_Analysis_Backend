"""
Proof script — Step 3: Shared Model-Analysis Response Normalization Layer
=========================================================================

Demonstrates that all 7 Market Picture analysis types (6 services + regime route)
produce the same normalized contract shape through the shared normalizer.

Shows: success, degraded (plaintext fallback), and error paths for each type.

Usage:
    cd BenTrade/backend
    python scripts/proof_model_normalizer_v2.py
"""

from __future__ import annotations

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.model_analysis_contract import (
    ANALYSIS_METADATA,
    normalize_model_analysis_response,
    wrap_service_model_response,
    parse_raw_model_text,
)

DIVIDER = "═" * 72


def _print_contract(label: str, contract: dict, indent: int = 2) -> None:
    """Pretty-print a normalized contract with label."""
    print(f"\n  {label}")
    print(f"  {'-' * len(label)}")
    subset = {
        "status": contract["status"],
        "analysis_type": contract["analysis_type"],
        "analysis_name": contract["analysis_name"],
        "category": contract["category"],
        "summary": (contract["summary"] or "")[:80],
        "confidence": contract["confidence"],
        "response_format": contract["response_format"],
        "parse_strategy": contract["parse_strategy"],
        "error_type": contract["error_type"],
        "duration_ms": contract["duration_ms"],
        "time_horizon": contract["time_horizon"],
    }
    for k, v in subset.items():
        print(f"    {k}: {v}")


def demo_service_wrapping() -> None:
    """Demonstrate wrap_service_model_response for all 6 Market Picture services."""
    print(f"\n{DIVIDER}")
    print("1. SERVICE WRAPPING — wrap_service_model_response()")
    print(DIVIDER)

    services = [
        ("breadth_participation", {
            "label": "BULLISH", "score": 72.5, "confidence": 0.85,
            "summary": "Broad participation confirms uptrend.",
            "uncertainty_flags": ["Volume divergence"],
            "key_risks": [],
            "trader_takeaway": "Stay long.",
            "_trace": {"method": "direct"},
        }),
        ("cross_asset_macro", {
            "label": "NEUTRAL", "score": 52,  "confidence": 0.68,
            "summary": "Cross-asset signals mixed; equities outperform.",
            "uncertainty_flags": ["Bond-equity correlation shifting"],
            "key_risks": ["Rate shock"],
            "trader_takeaway": "Balanced exposure.",
            "_trace": {"method": "direct"},
        }),
        ("flows_positioning", {
            "label": "BEARISH", "score": 38, "confidence": 0.71,
            "summary": "Institutional outflows dominate.",
            "uncertainty_flags": ["Dark pool elevated"],
            "key_risks": ["Selling pressure"],
            "trader_takeaway": "Reduce longs.",
            "_trace": {"method": "strip_fences"},
        }),
        ("liquidity_conditions", {
            "label": "CAUTIOUS", "score": 45, "confidence": 0.62,
            "summary": "Liquidity tightening; spreads widening.",
            "key_risks": ["Flash crash risk"],
            "trader_takeaway": "Use limit orders.",
            "_trace": {"method": "extract_block"},
        }),
        ("volatility_options", {
            "label": "ELEVATED", "score": 65, "confidence": 0.77,
            "summary": "VIX term structure inverted.",
            "key_risks": ["Gamma squeeze"],
            "trader_takeaway": "Sell premium cautiously.",
            "_trace": {"method": "repaired"},
        }),
        ("news_sentiment", {
            "label": "BEARISH", "score": 35, "confidence": 0.72,
            "summary": "Negative headline pressure from rate fears.",
            "uncertainty_flags": ["Conflicting earnings"],
            "key_risks": ["Rate shock"],
            "trader_takeaway": "Reduce risk.",
            "_trace": {"method": "strip_fences"},
        }),
    ]

    for analysis_type, model_result in services:
        outcome = {"model_analysis": model_result}
        wrapped = wrap_service_model_response(
            analysis_type, outcome,
            requested_at="2025-07-15T10:00:00+00:00",
            duration_ms=2500,
        )
        norm = wrapped["normalized"]
        _print_contract(f"SUCCESS — {analysis_type}", norm)
        assert "normalized" in wrapped
        assert wrapped["model_analysis"] is model_result  # backward compat

    print(f"\n  ✓ All 6 services produce normalized key on success path")


def demo_regime_route() -> None:
    """Demonstrate normalize_model_analysis_response for regime route."""
    print(f"\n{DIVIDER}")
    print("2. REGIME ROUTE — normalize_model_analysis_response()")
    print(DIVIDER)

    model_output = {
        "risk_regime_label": "RISK_ON",
        "trend_label": "UPTREND",
        "vol_regime_label": "LOW_VOL",
        "confidence": 0.80,
        "key_drivers": ["Strong breadth", "Low VIX"],
        "summary": "Bull regime with low volatility.",
        "key_risks": ["Complacency risk"],
        "trader_takeaway": "Favor directional longs.",
        "_trace": {"method": "direct"},
    }

    normalized = normalize_model_analysis_response(
        "regime",
        model_result=model_output,
        requested_at="2025-07-15T10:00:00+00:00",
        duration_ms=4200,
    )
    _print_contract("SUCCESS — regime (route-level)", normalized)

    # Simulate full route response shape
    response = {
        "ok": True,
        "analysis": model_output,
        "engine_summary": {"risk_regime_label": "RISK_ON"},
        "model_summary": {"risk_regime_label": "RISK_ON"},
        "comparison": {"deltas": [], "disagreement_count": 0},
        "regime_comparison_trace": {},
        "normalized": normalized,
    }
    assert response["ok"]
    assert response["normalized"]["analysis_type"] == "regime"
    print(f"\n  ✓ Regime route produces normalized key")


def demo_degraded_paths() -> None:
    """Demonstrate degraded (plaintext fallback) for all types."""
    print(f"\n{DIVIDER}")
    print("3. DEGRADED PATHS — plaintext fallback")
    print(DIVIDER)

    all_types = [
        "breadth_participation", "cross_asset_macro", "flows_positioning",
        "liquidity_conditions", "volatility_options", "news_sentiment", "regime",
    ]

    for analysis_type in all_types:
        fallback = {
            "summary": f"The {analysis_type} analysis returned prose instead of JSON...",
            "_plaintext_fallback": True,
        }
        outcome = {"model_analysis": fallback}
        wrapped = wrap_service_model_response(analysis_type, outcome)
        norm = wrapped["normalized"]
        assert norm["status"] == "degraded"
        assert norm["response_format"] == "plaintext"
        _print_contract(f"DEGRADED — {analysis_type}", norm)

    print(f"\n  ✓ All 7 types produce degraded status on plaintext fallback")


def demo_error_paths() -> None:
    """Demonstrate error paths for all types."""
    print(f"\n{DIVIDER}")
    print("4. ERROR PATHS — model failures")
    print(DIVIDER)

    error_scenarios = [
        ("timeout", "Request timed out after 180s"),
        ("unreachable", "Connection refused"),
        ("empty_response", "Model returned empty response"),
        ("rate_limited", "Too many requests (429)"),
    ]

    for error_kind, error_msg in error_scenarios:
        outcome = {
            "model_analysis": None,
            "error": {"kind": error_kind, "message": error_msg},
        }
        wrapped = wrap_service_model_response("cross_asset_macro", outcome)
        norm = wrapped["normalized"]
        assert norm["status"] == "error"
        assert norm["error_type"] == error_kind
        _print_contract(f"ERROR — {error_kind}", norm)

    print(f"\n  ✓ All error types produce correct error contract")


def demo_parse_raw_model_text() -> None:
    """Demonstrate parse_raw_model_text for raw LLM output."""
    print(f"\n{DIVIDER}")
    print("5. PARSE RAW MODEL TEXT — standalone parser")
    print(DIVIDER)

    # Valid JSON
    result = parse_raw_model_text(
        '{"summary": "Direct JSON parse", "confidence": 0.9}',
        "cross_asset_macro",
    )
    assert result["status"] == "success"
    _print_contract("Direct JSON", result)

    # Fenced JSON
    result = parse_raw_model_text(
        '```json\n{"summary": "Fenced JSON"}\n```',
        "flows_positioning",
    )
    assert result["status"] == "success"
    _print_contract("Fenced JSON", result)

    # Think tags + JSON
    result = parse_raw_model_text(
        '<think>Reasoning...</think>{"summary": "After think tags"}',
        "liquidity_conditions",
    )
    assert result["status"] == "success"
    _print_contract("Think tags stripped", result)

    # Plaintext
    result = parse_raw_model_text(
        "This is a long enough plain text analysis of market conditions that triggers the fallback path.",
        "volatility_options",
    )
    assert result["status"] == "degraded"
    _print_contract("Plaintext fallback", result)

    # None/empty
    result = parse_raw_model_text(None, "regime")
    assert result["status"] == "error"
    _print_contract("Null input", result)

    print(f"\n  ✓ parse_raw_model_text handles all input shapes")


def demo_contract_consistency() -> None:
    """Verify all types produce the exact same field set."""
    print(f"\n{DIVIDER}")
    print("6. CONTRACT CONSISTENCY — same fields across all types")
    print(DIVIDER)

    expected_fields = {
        "status", "analysis_type", "analysis_name", "category",
        "model_source", "requested_at", "completed_at", "duration_ms",
        "raw_content", "normalized_text", "structured_payload",
        "summary", "key_points", "risks", "actions", "confidence",
        "warnings", "error_type", "error_message",
        "parse_strategy", "response_format", "time_horizon", "metadata",
    }

    all_types = list(ANALYSIS_METADATA.keys())
    for atype in all_types:
        result = normalize_model_analysis_response(
            atype, model_result={"summary": "test", "confidence": 0.5},
        )
        actual = set(result.keys())
        assert actual == expected_fields, f"{atype}: {actual ^ expected_fields}"
        print(f"  ✓ {atype:30s} → {len(actual)} fields (matches contract)")

    print(f"\n  ✓ All {len(all_types)} analysis types produce identical field sets")


def main() -> None:
    print("=" * 72)
    print("PROOF — Model Analysis Response Normalization Layer (Step 3)")
    print("=" * 72)

    demo_service_wrapping()
    demo_regime_route()
    demo_degraded_paths()
    demo_error_paths()
    demo_parse_raw_model_text()
    demo_contract_consistency()

    print(f"\n{'=' * 72}")
    print("ALL PROOFS PASSED ✓")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    main()
