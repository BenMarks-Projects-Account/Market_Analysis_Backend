"""Proof script for Model-vs-Engine Disagreement Tracking v1.

Demonstrates two scenarios:
  1. Populated disagreement history — mixed outcomes, multiple categories
  2. Sparse / partial data — few records, missing snapshots

Run from backend/:
  python -m scripts.proof_disagreement_tracking
"""

import json

from app.services.disagreement_tracking import (
    build_tracking_report,
    validate_tracking_report,
)


def _fb(
    *,
    decision="approve",
    conviction="high",
    policy_decision="allow",
    severity="none",
    market_alignment="aligned",
    overall_bias="neutral",
    regime_label="neutral",
    strategy="iron_condor",
    spread_type="put_credit_spread",
    event_risk="low",
    event_risk_state="low",
    max_severity="none",
    confidence=0.75,
    realized_pnl=None,
    status="closed",
):
    rec = {
        "feedback_version": "1.0",
        "feedback_id": "proof-id",
        "recorded_at": "2025-01-15T12:00:00Z",
        "status": status,
        "trade_action": "taken",
        "response_snapshot": {
            "decision": decision,
            "conviction": conviction,
            "market_alignment": market_alignment,
            "policy_alignment": "clear",
            "event_risk": event_risk,
            "size_guidance": "normal",
        },
        "policy_snapshot": {
            "policy_decision": policy_decision,
            "severity": severity,
            "failed_check_names": [],
        },
        "market_snapshot": {
            "overall_bias": overall_bias,
            "regime_label": regime_label,
            "confidence": confidence,
        },
        "candidate_snapshot": {
            "strategy": strategy,
            "spread_type": spread_type,
        },
        "event_snapshot": {"event_risk_state": event_risk_state},
        "conflict_snapshot": {"max_severity": max_severity},
        "outcome_snapshot": {},
    }
    if realized_pnl is not None:
        rec["outcome_snapshot"]["realized_pnl"] = realized_pnl
    return rec


def scenario_populated():
    """Scenario 1: Populated history with disagreements and outcomes."""
    records = [
        # 1) Model overrides policy block — loss (bearish regime)
        _fb(decision="approve", policy_decision="block",
            realized_pnl=-120.0, regime_label="bearish",
            strategy="iron_condor"),
        # 2) Model overrides policy restrict — win (neutral)
        _fb(decision="approve", policy_decision="restrict",
            realized_pnl=80.0, regime_label="neutral",
            strategy="put_credit_spread"),
        # 3) Aligned — win (neutral)
        _fb(realized_pnl=60.0, regime_label="neutral",
            strategy="iron_condor"),
        # 4) Model approves in risk_off — loss
        _fb(decision="approve", overall_bias="bearish",
            regime_label="bearish", realized_pnl=-90.0,
            strategy="iron_condor"),
        # 5) High conviction + high conflict — win despite caution
        _fb(conviction="high", max_severity="high",
            realized_pnl=45.0, regime_label="neutral"),
        # 6) Model overrides — win
        _fb(decision="cautious_approve", policy_decision="block",
            realized_pnl=55.0, regime_label="neutral"),
        # 7) Approve + elevated event risk — pending (no outcome)
        _fb(decision="approve", event_risk="elevated",
            event_risk_state="elevated"),
        # 8) Aligned — win
        _fb(realized_pnl=40.0, strategy="butterfly"),
    ]
    return build_tracking_report(records)


def scenario_sparse():
    """Scenario 2: Sparse / partial data."""
    records = [
        _fb(decision="approve", policy_decision="block",
            realized_pnl=-50.0),
        # minimal record — partial snapshots
        {
            "feedback_version": "1.0",
            "status": "closed",
            "trade_action": "taken",
            "response_snapshot": {"decision": "approve"},
        },
    ]
    return build_tracking_report(records)


def main():
    print("=" * 70)
    print("  PROOF: Model-vs-Engine Disagreement Tracking v1")
    print("=" * 70)

    # Scenario 1
    print("\n── Scenario 1: Populated History ──\n")
    report1 = scenario_populated()
    ok1, errors1 = validate_tracking_report(report1)
    print(json.dumps(report1, indent=2, default=str))
    print(f"\nValidation: {'PASS' if ok1 else 'FAIL'}")
    if errors1:
        print(f"  Errors: {errors1}")
    print(f"  Status:                {report1['status']}")
    print(f"  Total records:         {report1['sample_size']['total_records']}")
    print(f"  With disagreement:     {report1['sample_size']['records_with_disagreement']}")
    print(f"  Rate:                  {report1['disagreement_rates']['disagreement_rate']}")
    print(f"  Disagreement records:  {len(report1['disagreement_records'])}")
    print(f"  Categories found:      {list(report1['disagreement_summary'].keys())}")
    print(f"  Warning flags:         {report1['warning_flags']}")
    print(f"  Diagnostics:           {len(report1['weighting_diagnostics'])} entries")

    # Scenario 2
    print("\n── Scenario 2: Sparse Data ──\n")
    report2 = scenario_sparse()
    ok2, errors2 = validate_tracking_report(report2)
    print(json.dumps(report2, indent=2, default=str))
    print(f"\nValidation: {'PASS' if ok2 else 'FAIL'}")
    if errors2:
        print(f"  Errors: {errors2}")
    print(f"  Status:                {report2['status']}")
    print(f"  Total records:         {report2['sample_size']['total_records']}")
    print(f"  Warning flags:         {report2['warning_flags']}")

    # Final
    print("\n" + "=" * 70)
    assert ok1 and ok2, "Validation failed!"
    print("  ALL SCENARIOS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()
