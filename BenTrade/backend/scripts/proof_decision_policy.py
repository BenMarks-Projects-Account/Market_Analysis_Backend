#!/usr/bin/env python
"""
Proof script – Decision Policy Framework v1
=============================================

Demonstrates three scenarios:
  1. Clean eligible candidate → allow, size_guidance=normal
  2. Concentrated + conflicted → restrict, size_guidance=minimal
  3. Insufficient data → insufficient_data, size_guidance=none

Run:
    cd BenTrade/backend
    python scripts/proof_decision_policy.py
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.decision_policy import evaluate_policy


def _print(label: str, result: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  SCENARIO: {label}")
    print(f"{'='*60}")
    print(f"  decision:  {result['policy_decision']}")
    print(f"  severity:  {result['decision_severity']}")
    print(f"  guidance:  {result['size_guidance']}")
    print(f"  status:    {result['status']}")
    print(f"  checks:    {len(result['triggered_checks'])}")
    print(f"  blocking:  {len(result['blocking_checks'])}")
    print(f"  restrict:  {len(result['restrictive_checks'])}")
    print(f"  caution:   {len(result['caution_checks'])}")
    print(f"  flags:     {result['eligibility_flags']}")
    print(f"  warnings:  {result['warning_flags']}")
    print(f"  summary:   {result['summary']}")
    if result["triggered_checks"]:
        print(f"\n  Triggered checks:")
        for c in result["triggered_checks"]:
            print(f"    [{c['severity']:>8}] {c['check_code']} → {c['recommended_effect']}")
    print()


# ── Scenario 1: Clean eligible candidate ────────────────────────
candidate_clean = {
    "symbol": "SPY",
    "scanner_key": "put_credit_spread",
    "setup_type": "put_credit_spread",
    "strategy_family": "options",
    "direction": "short",
    "time_horizon": "short_term",
    "confidence": 0.85,
    "entry_context": {"dte": 30},
    "risk_definition": {
        "type": "defined_risk_spread",
        "max_loss_per_contract": 3.80,
        "pop": 0.72,
    },
    "reward_profile": {
        "type": "defined_reward_spread",
        "expected_value_per_contract": 0.35,
        "return_on_risk": 0.316,
    },
    "data_quality": {"metrics_ready": True, "missing_fields": [], "warning_count": 0},
    "risk_flags": [],
}

market_clean = {
    "status": "ok",
    "market_state": "neutral",
    "support_state": "supportive",
    "stability_state": "orderly",
    "confidence": 0.78,
    "metadata": {"conflict_severity": "none", "overall_quality": "good"},
}

conflicts_clean = {
    "status": "clean",
    "conflict_count": 0,
    "conflict_severity": "none",
    "conflict_flags": [],
}

portfolio_clean = {
    "status": "ok",
    "directional_exposure": {"bias": "neutral", "bullish_count": 2, "bearish_count": 1, "neutral_count": 1},
    "underlying_concentration": {
        "top_symbols": [
            {"symbol": "QQQ", "share": 0.25, "risk": 500},
            {"symbol": "IWM", "share": 0.25, "risk": 500},
        ],
        "concentrated": False,
        "hhi": 0.25,
    },
    "strategy_concentration": {
        "top_strategies": [{"strategy": "put_credit_spread", "count": 2, "share": 0.50}],
        "concentrated": False,
    },
    "expiration_concentration": {
        "buckets": {"22-45D": {"count": 2, "risk": 1000, "share": 0.40}},
        "concentrated": False,
    },
    "capital_at_risk": {"total_risk": 2000, "utilization_pct": 0.10},
    "correlation_exposure": {"clusters": {}, "concentrated": False},
    "greeks_exposure": {"coverage": "full"},
    "risk_flags": [],
    "warning_flags": [],
}

r1 = evaluate_policy(
    candidate=candidate_clean,
    market=market_clean,
    conflicts=conflicts_clean,
    portfolio=portfolio_clean,
)
_print("Clean eligible candidate", r1)
assert r1["policy_decision"] == "allow", f"Expected allow, got {r1['policy_decision']}"
assert r1["size_guidance"] == "normal", f"Expected normal, got {r1['size_guidance']}"
print("  ✓ Scenario 1 PASSED")

# ── Scenario 2: Concentrated + conflicted → restrict ────────────
portfolio_bad = {
    "status": "ok",
    "directional_exposure": {"bias": "bullish", "bullish_count": 8, "bearish_count": 0, "neutral_count": 0},
    "underlying_concentration": {
        "top_symbols": [{"symbol": "SPY", "share": 0.55, "risk": 4000}],
        "concentrated": True,
        "hhi": 0.60,
        "total_symbols": 2,
    },
    "strategy_concentration": {"top_strategies": [], "concentrated": False},
    "expiration_concentration": {"buckets": {}, "concentrated": False},
    "capital_at_risk": {"total_risk": 7000, "utilization_pct": 0.50},
    "correlation_exposure": {
        "clusters": {"sp500": {"count": 3, "risk": 3000, "share": 0.80, "symbols": ["SPY", "SPX"]}},
        "concentrated": True,
    },
    "greeks_exposure": {"coverage": "full"},
    "risk_flags": ["heavy_bullish_lean"],
    "warning_flags": [],
}

r2 = evaluate_policy(
    candidate=candidate_clean,
    market={"status": "ok", "market_state": "risk_off", "support_state": "fragile",
            "stability_state": "unstable", "confidence": 0.30,
            "metadata": {"conflict_severity": "high"}},
    conflicts={"status": "conflicts_detected", "conflict_count": 4,
               "conflict_severity": "high", "conflict_flags": ["market_label_split"]},
    portfolio=portfolio_bad,
)
_print("Concentrated + conflicted", r2)
assert r2["policy_decision"] in ("restrict", "block"), f"Expected restrict/block, got {r2['policy_decision']}"
assert r2["size_guidance"] in ("minimal", "none"), f"Expected minimal/none, got {r2['size_guidance']}"
print("  ✓ Scenario 2 PASSED")

# ── Scenario 3: Insufficient data ───────────────────────────────
r3 = evaluate_policy(candidate=None)
_print("Insufficient data (no candidate)", r3)
assert r3["policy_decision"] == "insufficient_data"
assert r3["size_guidance"] == "none"
print("  ✓ Scenario 3 PASSED")

print("\n" + "="*60)
print("  ALL 3 SCENARIOS PASSED")
print("="*60)
