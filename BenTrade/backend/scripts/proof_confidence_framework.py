"""Proof script: Confidence Framework v1.

Demonstrates two scenarios:
1. Clean / high-confidence — all systems healthy
2. Degraded / low-confidence — stale, missing, conflicted data

DEPRECATED: This script imports from trade_decision_orchestrator which has been
quarantined as part of the workflow pivot (Prompt 0). Do not use as a foundation
for new workflow builds.
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.confidence_framework import (
    build_confidence_assessment,
    build_uncertainty_summary,
    make_impact,
    quick_assess,
)
from app.services.trade_decision_orchestrator import build_decision_packet
from app.services.decision_response_contract import build_decision_response


def _pp(label: str, obj: dict) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")
    print(json.dumps(obj, indent=2, default=str))


def scenario_1_clean():
    """Scenario 1: Everything healthy — high confidence expected."""
    print("\n" + "#" * 70)
    print("  SCENARIO 1 — Clean / High Confidence")
    print("#" * 70)

    # Direct framework call
    assessment = build_confidence_assessment(
        raw_confidence=0.92,
        quality_status="good",
        freshness_status="live",
        conflict_severity="none",
        coverage_level="full",
        source="proof_script_clean",
    )
    _pp("Framework Assessment (direct)", assessment)

    # Quick assess shorthand
    quick = quick_assess(0.90, quality="acceptable", freshness="recent", source="quick")
    _pp("Quick Assess (shorthand)", {
        "adjusted_score": quick["adjusted_score"],
        "confidence_label": quick["confidence_label"],
        "uncertainty_level": quick["uncertainty_level"],
    })

    # Via orchestrator (all subsystems present)
    pkt = build_decision_packet(
        candidate={"symbol": "SPY", "strategy": "iron_condor", "confidence": 0.88},
        market={"overall_bias": "neutral", "confidence": 0.85},
        conflicts={"conflicts": [], "conflict_severity": "none"},
        portfolio={"exposure": {"spy": 2}},
        policy={"checks": [], "policy_evaluation": {"status": "clear"}},
        events={"events": [], "event_risk_state": "quiet"},
        model_context={"summary": "Neutral outlook"},
    )
    _pp("Orchestrator quality_overview.confidence_assessment",
         pkt["quality_overview"]["confidence_assessment"])
    _pp("Orchestrator quality_overview.uncertainty_summary",
         pkt["quality_overview"]["uncertainty_summary"])

    # Via decision response
    resp = build_decision_response(
        decision="approve",
        conviction="high",
        market_alignment="aligned",
        portfolio_fit="good",
        policy_alignment="clear",
        event_risk="low",
    )
    _pp("Response confidence_assessment", resp["confidence_assessment"])


def scenario_2_degraded():
    """Scenario 2: Multiple degradations — low confidence expected."""
    print("\n" + "#" * 70)
    print("  SCENARIO 2 — Degraded / Low Confidence")
    print("#" * 70)

    # Direct framework call with multiple issues
    assessment = build_confidence_assessment(
        raw_confidence=0.85,
        quality_status="poor",
        freshness_status="very_stale",
        conflict_severity="high",
        coverage_level="minimal",
        extra_impacts=[
            make_impact("fallback", 0.10, "using Yahoo proxy for options data"),
            make_impact("proxy", 0.05, "VIX data from fallback provider"),
        ],
        source="proof_script_degraded",
        context={"symbol": "QQQ", "reason": "stress test"},
    )
    _pp("Framework Assessment (degraded)", assessment)

    summary = build_uncertainty_summary(assessment)
    _pp("Uncertainty Summary (compact)", summary)

    # Via orchestrator (mostly missing subsystems)
    pkt = build_decision_packet(
        candidate={"symbol": "QQQ", "strategy": "put_credit_spread"},
    )
    _pp("Orchestrator (partial) confidence_assessment",
         pkt["quality_overview"]["confidence_assessment"])

    # Via decision response with bad signals
    resp = build_decision_response(
        decision="reject",
        conviction="low",
        market_alignment="misaligned",
        portfolio_fit="poor",
        policy_alignment="restricted",
        event_risk="high",
        warning_flags=["stale_data", "conflicting_signals", "low_liquidity",
                       "earnings_tomorrow", "high_vix"],
    )
    _pp("Response confidence_assessment (degraded)", resp["confidence_assessment"])


if __name__ == "__main__":
    scenario_1_clean()
    scenario_2_degraded()
    print("\n✅ Proof script completed successfully.")
