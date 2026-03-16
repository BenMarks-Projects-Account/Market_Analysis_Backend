"""Proof script: Post-Trade Feedback Loop v1.

Demonstrates three scenarios:
1. Taken trade: full decision context → execution → outcome → close
2. Skipped trade: decision context only, no execution or outcome
3. Exited trade: partial context → outcome snapshot

Each record is validated and printed.

DEPRECATED: This script imports from trade_decision_orchestrator which has been
quarantined as part of the workflow pivot (Prompt 0). Do not use as a foundation
for new workflow builds.
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.feedback_loop import (
    build_feedback_record,
    close_feedback_record,
    update_feedback_execution,
    update_feedback_outcome,
    validate_feedback_record,
)
from app.services.trade_decision_orchestrator import build_decision_packet
from app.services.decision_response_contract import build_decision_response


def _pp(label: str, obj: dict) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")
    print(json.dumps(obj, indent=2, default=str))


def scenario_1_taken_trade():
    """Full lifecycle: packet → response → execution → outcome → close."""
    print("\n" + "#" * 70)
    print("  SCENARIO 1 — Taken Trade (full lifecycle)")
    print("#" * 70)

    # Build decision context
    pkt = build_decision_packet(
        candidate={
            "symbol": "SPY260320P00510000",
            "underlying": "SPY",
            "spread_type": "put_credit_spread",
            "expiration": "2026-03-20",
            "dte": 10,
            "short_strike": 510,
            "long_strike": 505,
            "net_credit": 1.25,
            "width": 5.0,
            "max_profit_per_share": 1.25,
            "return_on_risk": 0.333,
            "confidence": 0.85,
        },
        market={"overall_bias": "neutral", "composite_score": 62},
        conflicts={"has_conflicts": False, "conflict_count": 0},
        portfolio={"total_positions": 3, "total_delta": -12},
        policy={"policy_decision": "pass", "checks_passed": 8, "total_checks": 8},
        events={"event_risk_state": "quiet", "total_events": 1},
    )

    resp = build_decision_response(
        decision="approve",
        conviction="high",
        market_alignment="aligned",
        portfolio_fit="good",
        policy_alignment="clear",
        event_risk="low",
        summary="High-prob SPY put credit spread.",
        reasons_for=["strong support", "low IV"],
        key_risks=["overnight gap"],
    )

    # Step 1: Record decision
    record = build_feedback_record(
        trade_action="taken",
        decision_packet=pkt,
        decision_response=resp,
        source="proof_script",
    )
    ok, errs = validate_feedback_record(record)
    print(f"\n  Step 1 — Recorded:  valid={ok}, status={record['status']}")
    assert ok, errs

    # Step 2: Add execution
    record = update_feedback_execution(record, {
        "broker_order_id": "ORD-55555",
        "order_status": "FILLED",
        "fill_price": 1.18,
        "fill_quantity": 5,
        "fill_timestamp": "2026-03-10T14:30:00Z",
        "mode": "paper",
    })
    ok, errs = validate_feedback_record(record)
    print(f"  Step 2 — Execution: valid={ok}, status={record['status']}")

    # Step 3: Add outcome and close
    record = close_feedback_record(record, outcome_snapshot={
        "realized_pnl": 425.0,
        "exit_reason": "profit_target",
        "hold_duration_days": 8,
        "close_timestamp": "2026-03-18T15:55:00Z",
    }, review_notes=["Solid entry at support. Exited at 80% max profit."])
    ok, errs = validate_feedback_record(record)
    print(f"  Step 3 — Closed:    valid={ok}, status={record['status']}")

    _pp("Final Taken Trade Record (trimmed)", {
        "feedback_id": record["feedback_id"],
        "status": record["status"],
        "trade_action": record["trade_action"],
        "candidate_snapshot": record["candidate_snapshot"],
        "response_snapshot": {
            k: record["response_snapshot"][k]
            for k in ["decision", "conviction", "market_alignment", "summary"]
            if k in (record.get("response_snapshot") or {})
        },
        "execution_snapshot": record["execution_snapshot"],
        "outcome_snapshot": record["outcome_snapshot"],
        "review_notes": record["review_notes"],
        "warning_flags": record["warning_flags"],
    })


def scenario_2_skipped_trade():
    """Skipped trade: decision context only, no execution or outcome."""
    print("\n" + "#" * 70)
    print("  SCENARIO 2 — Skipped Trade (not-taken proof)")
    print("#" * 70)

    resp = build_decision_response(
        decision="watchlist",
        conviction="moderate",
        market_alignment="neutral",
        event_risk="elevated",
        summary="QQQ iron condor — watchlist due to upcoming earnings.",
        reasons_against=["earnings in 2 days", "elevated VIX"],
    )

    record = build_feedback_record(
        trade_action="skipped",
        candidate_snapshot={
            "symbol": "QQQ260320IC",
            "underlying": "QQQ",
            "spread_type": "iron_condor",
            "expiration": "2026-03-20",
            "dte": 10,
            "net_credit": 2.10,
            "confidence": 0.62,
        },
        decision_response=resp,
        market_snapshot={"overall_bias": "bearish", "composite_score": 38},
        review_notes=["Skipped — too close to NVDA earnings window."],
        source="proof_script",
    )

    ok, errs = validate_feedback_record(record)
    print(f"\n  Skipped record valid={ok}, status={record['status']}")
    assert ok, errs

    _pp("Skipped Trade Record (trimmed)", {
        "feedback_id": record["feedback_id"],
        "status": record["status"],
        "trade_action": record["trade_action"],
        "candidate_snapshot": record["candidate_snapshot"],
        "response_snapshot": {
            k: record["response_snapshot"][k]
            for k in ["decision", "conviction", "summary"]
            if k in (record.get("response_snapshot") or {})
        },
        "execution_snapshot": record["execution_snapshot"],
        "outcome_snapshot": record["outcome_snapshot"],
        "review_notes": record["review_notes"],
        "warning_flags": record["warning_flags"],
    })


def scenario_3_exited_trade():
    """Exited trade: partial context → outcome → close."""
    print("\n" + "#" * 70)
    print("  SCENARIO 3 — Exited Trade (with outcome)")
    print("#" * 70)

    record = build_feedback_record(
        trade_action="exited",
        candidate_snapshot={
            "symbol": "IWM260327P00200000",
            "underlying": "IWM",
            "spread_type": "put_credit_spread",
            "short_strike": 200,
            "long_strike": 195,
            "net_credit": 0.95,
            "confidence": 0.70,
        },
        execution_snapshot={
            "broker_order_id": "ORD-77777",
            "fill_price": 0.90,
            "fill_quantity": 10,
            "mode": "paper",
        },
        outcome_snapshot={
            "realized_pnl": -1250.0,
            "exit_reason": "stop_loss",
            "hold_duration_days": 5,
            "close_timestamp": "2026-03-15T14:00:00Z",
            "notes": "IWM breached support after FOMC surprise.",
        },
        review_notes=["Loss trade — review entry timing vs FOMC."],
        source="proof_script",
    )

    ok, errs = validate_feedback_record(record)
    print(f"\n  Exited record valid={ok}, status={record['status']}")
    assert ok, errs

    _pp("Exited Trade Record (trimmed)", {
        "feedback_id": record["feedback_id"],
        "status": record["status"],
        "trade_action": record["trade_action"],
        "candidate_snapshot": record["candidate_snapshot"],
        "execution_snapshot": record["execution_snapshot"],
        "outcome_snapshot": record["outcome_snapshot"],
        "review_notes": record["review_notes"],
        "warning_flags": record["warning_flags"],
    })


if __name__ == "__main__":
    scenario_1_taken_trade()
    scenario_2_skipped_trade()
    scenario_3_exited_trade()
    print("\n✅ All proof scenarios completed successfully.")
