"""Proof script for Signal Attribution and Regime Calibration v1.

Demonstrates three scenarios:
  1. Populated dataset — mixed outcomes across regimes/strategies
  2. Sparse dataset — few records, low-sample warnings
  3. Empty / insufficient dataset — no P&L data

Run:  python -m scripts.proof_signal_attribution
"""

from __future__ import annotations

import json
import sys

from app.services.signal_attribution import (
    build_calibration_report,
    classify_outcome,
    validate_calibration_report,
)

_SEP = "=" * 72


def _make_record(
    *,
    realized_pnl=None,
    strategy="iron_condor",
    spread_type="put_credit_spread",
    regime_label="neutral",
    overall_bias="neutral",
    volatility_label="normal",
    trend_label="sideways",
    macro_label="stable",
    signal_quality="good",
    policy_decision="allow",
    failed_check_names=None,
    has_conflicts=False,
    max_severity="none",
    event_risk_state="low",
    conviction="moderate",
    decision="approve",
    trade_action="taken",
    status="closed",
):
    rec = {
        "feedback_version": "1.0",
        "feedback_id": "proof-id",
        "recorded_at": "2025-01-01T00:00:00Z",
        "status": status,
        "trade_action": trade_action,
        "candidate_snapshot": {
            "strategy": strategy,
            "spread_type": spread_type,
            "symbol": "SPY260320P00510000",
            "underlying": "SPY",
        },
        "market_snapshot": {
            "regime_label": regime_label,
            "overall_bias": overall_bias,
            "volatility_label": volatility_label,
            "trend_label": trend_label,
            "macro_label": macro_label,
            "signal_quality": signal_quality,
            "confidence": 0.8,
        },
        "policy_snapshot": {
            "policy_decision": policy_decision,
            "failed_check_names": failed_check_names or [],
        },
        "conflict_snapshot": {
            "has_conflicts": has_conflicts,
            "max_severity": max_severity,
        },
        "event_snapshot": {
            "event_risk_state": event_risk_state,
        },
        "response_snapshot": {
            "conviction": conviction,
            "decision": decision,
        },
        "outcome_snapshot": {},
        "review_notes": [],
        "warning_flags": [],
        "evidence": {},
        "metadata": {},
    }
    if realized_pnl is not None:
        rec["outcome_snapshot"]["realized_pnl"] = realized_pnl
    return rec


def _print_section(title: str, data):
    print(f"\n{title}")
    print("-" * len(title))
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, default=str))
    else:
        print(data)


# ─────────────────────────────────────────────────────────────────────
#  Scenario 1: Populated dataset
# ─────────────────────────────────────────────────────────────────────

def scenario_populated():
    print(f"\n{_SEP}")
    print("SCENARIO 1: Populated dataset (12 records, mixed outcomes)")
    print(_SEP)

    records = [
        # Neutral regime — wins
        _make_record(realized_pnl=80.0, regime_label="neutral",
                     strategy="iron_condor", conviction="high"),
        _make_record(realized_pnl=65.0, regime_label="neutral",
                     strategy="iron_condor", conviction="moderate"),
        _make_record(realized_pnl=45.0, regime_label="neutral",
                     strategy="put_credit_spread", conviction="moderate"),
        # Neutral regime — losses
        _make_record(realized_pnl=-25.0, regime_label="neutral",
                     strategy="iron_condor", conviction="low"),
        _make_record(realized_pnl=-40.0, regime_label="neutral",
                     strategy="put_credit_spread", conviction="low",
                     has_conflicts=True, max_severity="warning"),
        # Bullish regime
        _make_record(realized_pnl=120.0, regime_label="bullish",
                     overall_bias="bullish", volatility_label="elevated",
                     strategy="put_credit_spread", conviction="high"),
        _make_record(realized_pnl=90.0, regime_label="bullish",
                     overall_bias="bullish", strategy="put_credit_spread"),
        # Bearish regime — losses
        _make_record(realized_pnl=-200.0, regime_label="bearish",
                     overall_bias="bearish", volatility_label="high",
                     event_risk_state="elevated", conviction="low",
                     policy_decision="warn",
                     failed_check_names=["max_loss_exceeded"]),
        _make_record(realized_pnl=-80.0, regime_label="bearish",
                     overall_bias="bearish", strategy="iron_condor",
                     conviction="none", decision="reject"),
        # Skipped trade
        _make_record(realized_pnl=None, trade_action="skipped",
                     status="recorded", conviction="none", decision="reject"),
        # Policy reject that was taken anyway → big loss
        _make_record(realized_pnl=-150.0, policy_decision="reject",
                     failed_check_names=["portfolio_delta", "sector_concentration"],
                     conviction="low"),
        # Event risk — still won
        _make_record(realized_pnl=55.0, event_risk_state="elevated",
                     has_conflicts=True, max_severity="minor"),
    ]

    report = build_calibration_report(records)
    ok, errors = validate_calibration_report(report)

    print(f"\nValidation: {'PASS' if ok else 'FAIL'}")
    if errors:
        print(f"  Errors: {errors}")
    _print_section("Status", report["status"])
    _print_section("Sample Size", report["sample_size"])
    _print_section("Summary", report["summary"])
    _print_section("Warning Flags", report["warning_flags"])
    _print_section("Regime Calibration", report["regime_calibration"])
    _print_section("Strategy Attribution", report["strategy_attribution"])
    _print_section("Policy Attribution", report["policy_attribution"])
    _print_section("Conviction Attribution", report["conviction_attribution"])
    _print_section("Event Attribution", report["event_attribution"])
    _print_section("Conflict Attribution", report["conflict_attribution"])

    # Spot-check: outcome classification
    print("\nOutcome classifications:")
    for i, rec in enumerate(records):
        c = classify_outcome(rec)
        pnl = (rec.get("outcome_snapshot") or {}).get("realized_pnl", "N/A")
        print(f"  Record {i+1}: pnl={pnl}, classification={c}")

    assert ok
    assert report["status"] == "sufficient"
    print("\n✓ Scenario 1 PASSED")


# ─────────────────────────────────────────────────────────────────────
#  Scenario 2: Sparse dataset
# ─────────────────────────────────────────────────────────────────────

def scenario_sparse():
    print(f"\n{_SEP}")
    print("SCENARIO 2: Sparse dataset (2 records with P&L)")
    print(_SEP)

    records = [
        _make_record(realized_pnl=50.0, regime_label="neutral"),
        _make_record(realized_pnl=-30.0, regime_label="bearish",
                     overall_bias="bearish"),
    ]

    report = build_calibration_report(records)
    ok, errors = validate_calibration_report(report)

    print(f"\nValidation: {'PASS' if ok else 'FAIL'}")
    _print_section("Status", report["status"])
    _print_section("Sample Size", report["sample_size"])
    _print_section("Summary", report["summary"])
    _print_section("Warning Flags", report["warning_flags"])
    _print_section("Regime Calibration", report["regime_calibration"])

    assert ok
    assert report["status"] == "sparse"
    assert "sparse_pnl_data" in report["warning_flags"]
    # All groups should have low_sample_warning
    for grp in report["regime_calibration"]:
        assert grp["low_sample_warning"] is True
    print("\n✓ Scenario 2 PASSED")


# ─────────────────────────────────────────────────────────────────────
#  Scenario 3: Insufficient / empty
# ─────────────────────────────────────────────────────────────────────

def scenario_insufficient():
    print(f"\n{_SEP}")
    print("SCENARIO 3: Insufficient dataset (no P&L data)")
    print(_SEP)

    # Records exist but none have realized_pnl
    records = [
        _make_record(realized_pnl=None, status="recorded"),
        _make_record(realized_pnl=None, status="recorded"),
    ]

    report = build_calibration_report(records)
    ok, errors = validate_calibration_report(report)

    print(f"\nValidation: {'PASS' if ok else 'FAIL'}")
    _print_section("Status", report["status"])
    _print_section("Sample Size", report["sample_size"])
    _print_section("Summary", report["summary"])
    _print_section("Warning Flags", report["warning_flags"])

    assert ok
    assert report["status"] == "insufficient"
    assert "no_pnl_data_available" in report["warning_flags"]
    assert report["sample_size"]["with_pnl"] == 0

    # Also test fully empty
    empty_report = build_calibration_report([])
    ok2, errors2 = validate_calibration_report(empty_report)
    assert ok2
    assert empty_report["status"] == "insufficient"
    print("\n✓ Scenario 3 PASSED")


# ─────────────────────────────────────────────────────────────────────

def main():
    print("Signal Attribution & Regime Calibration v1 — Proof Script")
    print("=" * 60)

    scenario_populated()
    scenario_sparse()
    scenario_insufficient()

    print(f"\n{_SEP}")
    print("ALL SCENARIOS PASSED")
    print(_SEP)


if __name__ == "__main__":
    main()
